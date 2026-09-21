"""数据访问对象."""
import json
import logging
import re
import sqlite3
from typing import Optional, Dict
from datetime import datetime

from .models import init_db, connect as db_connect

logger = logging.getLogger(__name__)

# 敏感字段加密（AES-256-GCM，密钥来自 ENCRYPTION_KEY 环境变量）
_encryptor = None

# ── 轻量表（jian_prefs 等）共享连接工厂 ─────────────────────────────
# 测试可把 _DB_PATH 指向临时库（见 scripts/test_jian_api.py）；
# 未设置时回落到 load_settings().db_path（生产路径）。
_DB_PATH: Optional[str] = None

# 用户资料懒迁移列（Task1 登录增强：手机号加密 + 昵称）
_PROFILE_COLUMNS = (("phone_enc", "TEXT"), ("nickname", "TEXT"))


def get_conn() -> sqlite3.Connection:
    """打开 sqlite3 连接（轻量 DAO 复用）。

    - check_same_thread=False：允许 TestClient 等跨线程复用同一连接；
    - 测试注入：设置本模块 _DB_PATH 后返回指向临时库的连接。
    """
    path = _DB_PATH
    if not path:
        from ..config import load_settings
        path = str(load_settings().db_path)
    return sqlite3.connect(path, check_same_thread=False)


def _get_encryptor():
    """惰性初始化 DataEncryptor（读取 ENCRYPTION_KEY；未配置时自动降级 dev 密钥并告警）。"""
    global _encryptor
    if _encryptor is None:
        from src.security.encryption import DataEncryptor
        _encryptor = DataEncryptor()
    return _encryptor


def _is_ciphertext(text: str) -> bool:
    """判断是否已是密文（格式: version:base64）。明文 JSON 以 { 开头，不是密文。"""
    return bool(text) and ":" in text and not text.lstrip().startswith("{") and not text.lstrip().startswith("[")


def _decrypt_or_plain(text: Optional[str]) -> Optional[str]:
    """尝试解密；解密失败按明文返回（兼容旧数据）。"""
    if not text:
        return text
    if not _is_ciphertext(text):
        return text  # 旧数据：明文
    try:
        decrypted = _get_encryptor().decrypt(text)
        if decrypted is not None:
            return decrypted
    except Exception:
        pass
    return text  # 解密失败 → 按明文兼容处理


def _encrypt_text(text: Optional[str]) -> Optional[str]:
    """加密文本；空值原样返回。"""
    if not text:
        return text
    return _get_encryptor().encrypt(text)


# ══════════════════════════════════════════════════════════════════════
# k76：账号数据清除（**唯一实现**，两条链路共用）
# ══════════════════════════════════════════════════════════════════════
# 调用方：`UserDAO.cleanup_cancelled_accounts()`（注销 90 天归档清理，启动时跑）
# 与 `PrivacyManager.delete_user_data()`（PIPL 删除权，DELETE /user/{id}/data）。
# 改前两条链路各写各的删除清单 ⇒ 覆盖不一致（k72-A3）。现在只有这一份。
#
# 顺序（有依赖，不可随意调换）：
#   ① 先**采集**该用户被引用的上传文件名 —— 引用它们的行马上要被删掉，
#      删完就再也找不到"哪些图属于他"了；
#   ② 删 DB 行（清单见 models.ACCOUNT_PURGE_TABLES）；
#   ③ 删文件（头像/上传图/L3 画像/本人报告/对应分享图 PNG）。
# payments / midas_orders **不删**（依法留存，见 models.ACCOUNT_RETAIN_TABLES）。

#: 上传文件名在引用里的形态（与 main.py 的 `_CHAT_UPLOAD_REF_RE` 同口径，
#: 但**不 import main**——storage 层不能反向依赖应用层；两处正则都锚定同一个
#: 上传 URL 路径形态，测试里有一致性断言）。
_UPLOAD_REF_RE = re.compile(r"/api/chat/uploads/([A-Za-z0-9._-]{1,128})")

#: 采集上传引用要扫的 (表, 列)——只扫"可能承载用户消息内容"的列。
_UPLOAD_REF_COLUMNS = (
    ("sessions", ("content", "tool_calls")),
    ("session_summaries", ("summary", "memories")),
    ("favorites", ("summary",)),
    ("consultations", ("question", "analysis", "chart_data")),
    ("share_entries", ("content",)),
)


def collect_user_upload_names(conn, user_id: str, owner_tag: str = "") -> set:
    """该用户消息/记录里**被引用**的上传文件名集合（删行**之前**调用）。

    - 只扫 `_UPLOAD_REF_COLUMNS`；表/列不存在自动跳过（老库兼容）；
    - 值可能是密文（sessions.content 等）→ 统一 `_decrypt_or_plain` 后再匹配；
    - share_entries 无 user_id：按 `owner_tag` 命中该用户创建的分享行；
    - 任何异常 → 返回已收集到的部分（宁少删文件，不误删别人的图）。
    """
    names = set()
    for table, cols in _UPLOAD_REF_COLUMNS:
        key_col = "owner_tag" if table == "share_entries" else "user_id"
        key_val = owner_tag if key_col == "owner_tag" else user_id
        if not key_val:
            continue                      # 无归属标记 → 该表没有"属于他"的行
        try:
            existing = {r[1] for r in conn.execute(
                f"PRAGMA table_info({table})").fetchall()}
            if not existing:
                continue                  # 表不存在
            use_cols = [c for c in cols if c in existing]
            if not use_cols or key_col not in existing:
                continue
            rows = conn.execute(
                f"SELECT {', '.join(use_cols)} FROM {table} WHERE {key_col} = ?",
                (key_val,)).fetchall()
        except Exception as e:
            logger.warning("上传引用采集 %s 失败（跳过该表）: %s", table, e)
            continue
        for row in rows:
            for value in row:
                if not value:
                    continue
                text = value if isinstance(value, str) else str(value)
                try:
                    text = _decrypt_or_plain(text) or text
                except Exception:
                    pass
                # "." / ".." 也满足正则的字符集，但它们不是文件名（`dir/".."` 是父目录，
                # unlink 会失败甚至命中目录）——显式剔除，别把删除面交给巧合
                names.update(n for n in _UPLOAD_REF_RE.findall(text)
                             if n not in (".", ".."))
    return names


def _purge_dir_files(paths, label: str) -> int:
    """逐个删文件（不存在/失败都只是告警，不中断整体清除）。返回删除个数。"""
    import os as _os
    removed = 0
    for p in paths:
        try:
            _os.unlink(p)
            removed += 1
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.warning("注销清理 %s 文件失败 %s: %s", label, p, e)
    return removed


def purge_account_files(user_id: str, upload_names=None, memory_dir=None,
                        avatar_dir=None, uploads_dir=None, reports_dir=None,
                        charts_dir=None) -> dict:
    """删除该用户的**文件类**落点，返回 `{类别: 删除个数}`。

    覆盖（与 `models.ACCOUNT_PURGE_FILE_KINDS` 一一对应）：
      - avatar     : `data/avatars/{user_id}.jpg`（`AVATAR_DIR` 可覆盖）
      - uploads    : `upload_names` 里的文件（调用方在删行前用
                     `collect_user_upload_names()` 采好；**只删被引用的**，
                     绝不整目录清空——同目录下有别人的图）
      - memory     : `data/memory/{user_id}.json`（L3 画像，走 UserMemory 自带接口）
      - reports    : `data/reports/*.json` 中归属为该用户的（`owner_enc` 解密命中）
      - share_cards: 上述报告对应的分享图 PNG（`CHARTS_DIR/share_{reading_id}.png`）
    """
    import os as _os
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent.parent
    stats = {"avatar": 0, "uploads": 0, "memory": 0, "reports": 0, "share_cards": 0}

    # ① 头像：文件名就是 user_id（与 api/user.py 的 `{safe_id}.jpg` 同口径）
    av_dir = _Path(avatar_dir) if avatar_dir else _Path(
        _os.environ.get("AVATAR_DIR", "").strip() or (root / "data" / "avatars"))
    stats["avatar"] = _purge_dir_files([av_dir / f"{user_id}.jpg"], "头像")

    # ② 上传图：只删被该用户消息引用过的那些
    if upload_names:
        if uploads_dir:
            up_dir = _Path(uploads_dir)
        else:
            from ..config import load_settings
            up_dir = _Path(_os.environ.get("FORTUNE_UPLOADS_DIR", "").strip()
                           or (load_settings().data_dir / "uploads" / "chat"))
        stats["uploads"] = _purge_dir_files(
            [up_dir / n for n in sorted(upload_names)], "上传图")

    # ③ L3 画像文件
    try:
        from src.memory.user_memory import UserMemory
        um = UserMemory(base_dir=memory_dir) if memory_dir else UserMemory()
        stats["memory"] = 1 if um.clear_all(user_id) else 0
    except Exception as e:
        logger.warning("注销清理画像文件 %s 失败: %s", user_id, e)

    # ④⑤ 报告文件 + 对应分享图：归属命中才删（老报告无归属字段 → 不动，
    #     它们不随注销删除这件事会如实写进文案清单，不假装删掉了）
    rep_dir = _Path(reports_dir) if reports_dir else (root / "data" / "reports")
    ch_dir = _Path(charts_dir) if charts_dir else _Path(
        _os.environ.get("CHARTS_DIR", "/opt/fortune-data/charts"))
    try:
        from src.security.encryption import DataEncryptor
        enc = DataEncryptor()
        for f in sorted(rep_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            owner_enc = data.get("owner_enc") or ""
            if not owner_enc:
                continue
            try:
                owner = enc.decrypt(owner_enc) or ""
            except Exception:
                continue
            if owner != user_id:
                continue
            reading_id = data.get("reading_id") or f.stem
            stats["reports"] += _purge_dir_files([f], "报告")
            stats["share_cards"] += _purge_dir_files(
                [ch_dir / f"share_{reading_id}.png"], "分享图")
    except Exception as e:
        logger.warning("注销清理报告文件 %s 失败: %s", user_id, e)

    return stats


def purge_account_data(conn, user_id: str, memory_dir=None,
                       purge_files: bool = True, **file_dirs) -> dict:
    """**账号数据清除的唯一实现**：删该用户的全部 DB 行 + 文件。

    返回 `{"deleted_rows": n, "tables": {表: 行数}, "files": {类别: 个数},
           "retained": {表: 依据}}`。

    - 归属列取自 `models.ACCOUNT_PURGE_TABLES`（含 share_entries 的 `owner_tag`）；
    - 每次删除独立 try：某张表失败只告警，不中断其余表（**尽力删干净**）；
    - `payments` / `midas_orders` 明确**保留**（依法留存），结果里带依据说明；
    - `purge_files=False` 供只想删行的调用方（如单测）。
    """
    from .models import (ACCOUNT_PURGE_TABLES, ACCOUNT_RETAIN_TABLES)
    from .share_dao import owner_tag_for

    owner_tag = owner_tag_for(user_id)
    tables: Dict[str, int] = {}

    # ① 删行之前先采集上传图引用（删完就找不到归属了）
    upload_names = set()
    if purge_files:
        try:
            upload_names = collect_user_upload_names(conn, user_id, owner_tag)
        except Exception as e:
            logger.warning("注销清理：上传引用采集失败 %s: %s", user_id, e)

    # ② 删 DB 行（清单单一事实源）
    for table, key_col in ACCOUNT_PURGE_TABLES:
        key_val = owner_tag if key_col == "owner_tag" else user_id
        if key_col == "owner_tag" and not key_val:
            tables[table] = 0      # 无归属标记 → 本表无该用户的行（不误删全表）
            continue
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE {key_col} = ?", (key_val,))
            tables[table] = cur.rowcount or 0
        except Exception as e:
            logger.warning("注销清理 %s.%s 失败: %s", table, user_id, e)
            tables[table] = 0

    # ③ users 行最后删（否则 status='cancelled' 的判定行先没了）
    try:
        cur = conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        tables["users"] = cur.rowcount or 0
    except Exception as e:
        logger.warning("注销清理 users.%s 失败: %s", user_id, e)
        tables["users"] = 0

    # ④ 文件
    files = {}
    if purge_files:
        try:
            # file_dirs：avatar_dir / uploads_dir / reports_dir / charts_dir 透传
            # （部署自定义目录或测试注入用；缺省各自回落到生产默认目录）
            files = purge_account_files(user_id, upload_names=upload_names,
                                        memory_dir=memory_dir, **file_dirs)
        except Exception as e:
            logger.warning("注销清理文件 %s 失败: %s", user_id, e)

    deleted_rows = sum(tables.values())
    retained = {t: why for t, _col, why in ACCOUNT_RETAIN_TABLES}
    logger.info("账号数据清除完成 %s：删 %d 行 / 文件 %s；依法留存 %s",
                user_id, deleted_rows, files, sorted(retained))
    return {"deleted_rows": deleted_rows, "tables": tables,
            "files": files, "retained": retained}


class UserDAO:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._last_consultation_id: int = 0
        init_db(db_path)

    def _connect(self):
        return db_connect(self.db_path)

    @property
    def last_consultation_id(self) -> int:
        return self._last_consultation_id

    def get_user_bazi(self, user_id: str) -> Optional[Dict]:
        """获取用户已保存的八字（密文自动解密；旧明文数据读时迁移加密）。"""
        conn = self._connect()
        row = conn.execute(
            "SELECT bazi_info FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            raw = row[0]
            # 旧数据明文：解析后写回加密（懒迁移，不改变 consultation_count）
            if not _is_ciphertext(raw):
                try:
                    data = json.loads(raw)
                except (ValueError, TypeError):
                    return None
                self._migrate_bazi_encrypted(user_id, _encrypt_text(raw))
                return data
            plaintext = _decrypt_or_plain(raw)
            try:
                return json.loads(plaintext)
            except (ValueError, TypeError):
                return None
        return None

    def _migrate_bazi_encrypted(self, user_id: str, encrypted_json: str):
        """把明文八字原地迁移为密文（仅写 bazi_info，不动 consultation_count）。"""
        try:
            conn = self._connect()
            conn.execute(
                "UPDATE users SET bazi_info=? WHERE user_id=?",
                (encrypted_json, user_id),
            )
            conn.commit()
            conn.close()
            logger.info("已迁移用户 %s 的八字为密文存储", user_id)
        except Exception as e:
            logger.warning("八字迁移加密失败 %s: %s", user_id, e)

    # ── bazi 键一致性守卫（k8，2026-09-05 单一漏斗防线）─────────────────
    # 四柱只属于 chart_records；users.bazi_info 同时含 birth 键与 bazi 键的
    # 写入口（W2/W3 排盘落库等）在写前用确定性排盘引擎复算比对——
    # 不一致（如 21:44 事故的「1995 出生 + 他人 2026-08-18 盘四柱」畸形
    # 组装）→ 丢弃 bazi 键，绝不并存矛盾字段。复算为纯规则零 LLM（实测
    # 单次 ~26ms）；calendar=lunar 时先经 to_solar_date 转公历再复算
    # （与 R2-5/R2-6 写路径口径一致：落库保留原始农历 y/m/d+标记）。
    # 无法验证（复算异常/bazi 形状不可比）→ fail-open 保留 + warning。
    # 不得改动 tool_calls.py 主链——守卫只挂在 dao 写漏斗。
    _guard_engine = None

    def _guard_bazi_pillars(self, user_id: str, bazi_info: dict) -> dict:
        try:
            info = dict(bazi_info)
            birth_keys = ("year", "month", "day")
            if not all(info.get(k) not in (None, "") for k in birth_keys):
                return info  # 无完整出生键 → 无法复算（缺省形态写不校验）
            stored = info.get("bazi")
            if not stored:
                return info  # 无 bazi 键 → 守卫不介入
            if isinstance(stored, str):
                stored = stored.split()
            if not isinstance(stored, (list, tuple)) or len(stored) != 4:
                return info  # 形状不可比 → fail-open 保留
            # lunar → 引擎契约=公历输入：先单点转公历（与写路径同口径）
            y, m, d = info["year"], info["month"], info["day"]
            if str(info.get("calendar") or "solar") == "lunar":
                from src.storage.birth_profile import to_solar_date
                sol = to_solar_date(info)
                if sol is None:
                    return info  # 转换失败（写路径同样回落原值）→ 不误判
                y, m, d = sol
            hour = int(info.get("hour") or 0)
            minute = int(info.get("minute") or 0)
            city = str(info.get("city") or "")
            gender = info.get("gender") or "unknown"
            if self._guard_engine is None:
                from src.engines.bazi import BaziEngine
                self._guard_engine = BaziEngine()
            try:
                expected = list(self._guard_engine.calculate(
                    int(y), int(m), int(d), hour, minute, city, gender).bazi)
            except Exception as e:
                logger.warning(
                    "bazi 一致性守卫复算失败（fail-open 保留）user=%s birth="
                    "%s-%s-%s %s时：%s", user_id, y, m, d, hour, str(e)[:120])
                return info
            actual = [str(s).strip() for s in stored]
            if actual == expected:
                return info
            logger.warning(
                "bazi 一致性守卫：birth 键与 bazi 四柱矛盾 → 丢弃 bazi 键 "
                "user=%s birth=%s-%s-%s %s:%s %s %s（待写 %s vs 引擎复算 %s）",
                user_id, info.get("year"), info.get("month"), info.get("day"),
                hour, minute, city or "未知城市",
                str(info.get("gender") or ""),
                "/".join(actual), "/".join(expected))
            info.pop("bazi", None)
            return info
        except Exception as e:
            logger.warning("bazi 一致性守卫异常（fail-open 保留）user=%s: %s",
                           user_id, str(e)[:160])
            return bazi_info

    def save_user_bazi(self, user_id: str, bazi_info: dict):
        """保存或更新用户八字信息（加密后落库）。

        k8：写前经 _guard_bazi_pillars 校验（birth 键与 bazi 四柱一致性）。
        """
        bazi_info = self._guard_bazi_pillars(user_id, bazi_info)
        conn = self._connect()
        existing = conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        bazi_json = _encrypt_text(json.dumps(bazi_info, ensure_ascii=False))
        now = datetime.now().isoformat()

        if existing:
            conn.execute(
                "UPDATE users SET bazi_info=?, updated_at=?, consultation_count=consultation_count+1 WHERE user_id=?",
                (bazi_json, now, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO users (user_id, bazi_info, created_at, updated_at, consultation_count) VALUES (?,?,?,?,1)",
                (user_id, bazi_json, now, now),
            )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------
    # 手机号绑定 + 昵称（Task1 登录增强：phone_enc AES 加密落库 / nickname 明文）
    # ------------------------------------------------------------

    def ensure_profile_columns(self):
        """users 表懒迁移：增加 phone_enc/nickname 列（幂等，PRAGMA 检查缺列才 ALTER）。"""
        conn = self._connect()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        for col, decl in _PROFILE_COLUMNS:
            if col not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
        conn.commit()
        conn.close()

    def save_user_phone(self, user_id: str, phone: str) -> bool:
        """绑定/换绑手机号（AES-256-GCM 加密落库）。返回是否覆盖了既有绑定。"""
        self.ensure_profile_columns()
        conn = self._connect()
        existing = conn.execute(
            "SELECT phone_enc FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        replaced = bool(existing and existing[0])
        now = datetime.now().isoformat()
        enc = _encrypt_text(phone)
        if existing:
            conn.execute(
                "UPDATE users SET phone_enc=?, updated_at=? WHERE user_id=?",
                (enc, now, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO users (user_id, phone_enc, created_at, updated_at) VALUES (?,?,?,?)",
                (user_id, enc, now, now),
            )
        conn.commit()
        conn.close()
        return replaced

    def get_user_phone(self, user_id: str) -> Optional[str]:
        """读取用户手机号（自动解密）。未绑定返回 None。"""
        self.ensure_profile_columns()
        conn = self._connect()
        row = conn.execute(
            "SELECT phone_enc FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        return _decrypt_or_plain(row[0])

    def set_user_nickname(self, user_id: str, nickname: str):
        """保存昵称（覆盖）。"""
        self.ensure_profile_columns()
        conn = self._connect()
        now = datetime.now().isoformat()
        existing = conn.execute(
            "SELECT user_id FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET nickname=?, updated_at=? WHERE user_id=?",
                (nickname, now, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO users (user_id, nickname, created_at, updated_at) VALUES (?,?,?,?)",
                (user_id, nickname, now, now),
            )
        conn.commit()
        conn.close()

    def get_user_nickname(self, user_id: str) -> str:
        """读取昵称（未设置返回空串）。"""
        self.ensure_profile_columns()
        conn = self._connect()
        row = conn.execute(
            "SELECT nickname FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        return (row[0] if row and row[0] else "") or ""

    def get_user_status(self, user_id: str) -> str:
        """用户账号状态（active/cancelled）。无记录/无列时默认 active。"""
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT status FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            conn.close()
        except Exception:
            return "active"
        if not row or not row[0]:
            return "active"
        return row[0] if row[0] in ("active", "cancelled") else "active"

    def cancel_user(self, user_id: str) -> bool:
        """软删用户：status=cancelled + cancelled_at=now。

        业务数据走既有口径：**保留 90 天**（期满由
        `cleanup_cancelled_accounts()` 物理清除，覆盖范围 =
        `models.ACCOUNT_PURGE_TABLES`）。

        **k76 唯一例外**：该账号创建的**匿名分享链接**在注销时**立即删除**
        （控制方 2026-09-21 拍板："注销时一并删除用户的分享记录"）。
        理由：分享链接是**对外的公开可访问面**（任何拿到链接的人都能打开），
        与"保留 90 天可恢复"的账号内数据不是一回事 —— 注销后继续公开 90 天，
        等于用户已经撤回同意却仍在持续对外提供内容。分享行按 HMAC 伪名
        `owner_tag` 定位（本表不存 user_id，见 share_dao 红线）。
        删除失败**不阻断注销**（注销是用户的强诉求，不能被附属清理拖失败）。

        users 行不存在（如仅建过命主档案）时补建注销行，保证登录拦截生效。
        """
        conn = self._connect()
        now = datetime.now().isoformat()
        # k76：先删该账号的分享链接（与昵称等账号数据无关，只删公开面）
        try:
            from .share_dao import ShareDAO, owner_tag_for
            tag = owner_tag_for(user_id)
            if tag:
                removed = ShareDAO(conn).delete_by_owner(tag)
                if removed:
                    logger.info("注销时删除分享记录: %s 条（user=%s）", removed, user_id)
        except Exception as e:
            logger.warning("注销时删除分享记录失败（不阻断注销）: %s", e)
        cursor = conn.execute(
            "UPDATE users SET status='cancelled', cancelled_at=?, updated_at=? "
            "WHERE user_id=? AND status != 'cancelled'",
            (now, now, user_id),
        )
        if cursor.rowcount == 0:
            exists = conn.execute(
                "SELECT user_id FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO users (user_id, status, cancelled_at, created_at, updated_at) "
                    "VALUES (?, 'cancelled', ?, ?, ?)",
                    (user_id, now, now, now),
                )
        conn.commit()
        conn.close()
        logger.info("账号已注销（软删）: %s", user_id)
        return True

    def cleanup_cancelled_accounts(self, retention_days: int = 90,
                                   memory_dir: Optional[str] = None) -> dict:
        """清理已注销满保留期的用户（启动时调用一次）。

        删除范围 = `src/storage/models.py::ACCOUNT_PURGE_TABLES`（**单一事实源**，
        k76 起不再在本方法里内联表名清单——内联清单与 privacy.py 各写一份必然漂移，
        正是 k72-A3「注销删除覆盖不全」的成因）。

        retention_days: 保留天数（默认 90），cancelled_at < now-90d 才清除。
        memory_dir: 画像文件目录（默认 UserMemory 默认目录；测试可注入临时目录）
        """
        from datetime import timedelta
        try:
            cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
            conn = self._connect()
            rows = conn.execute(
                """SELECT user_id FROM users
                   WHERE status='cancelled' AND cancelled_at IS NOT NULL
                     AND cancelled_at < ?""",
                (cutoff,),
            ).fetchall()
            removed, total_deleted = 0, 0
            for (user_id,) in rows:
                stats = purge_account_data(conn, user_id, memory_dir=memory_dir)
                total_deleted += stats.get("deleted_rows", 0)
                removed += 1
            conn.commit()
            conn.close()
            if removed:
                logger.info("注销账号 90 天归档清理完成: %d 个用户（%d 行）",
                            removed, total_deleted)
            return {"removed_users": removed, "deleted_rows": total_deleted}
        except Exception as e:
            logger.error("注销账号清理失败: %s", e)
            return {"removed_users": 0, "deleted_rows": 0, "error": str(e)}

    def save_consultation(self, user_id: str, question: str, chart_result=None, analysis: str = "", intent: str = "bazi"):
        """保存咨询记录（question / chart_data / analysis 加密落库）"""
        conn = self._connect()

        if chart_result is not None and hasattr(chart_result, 'bazi'):
            # BaziResult handling — preserve backward compatibility
            chart_json = json.dumps({
                "bazi": chart_result.bazi,
                "day_master": chart_result.day_master,
                "wuxing": chart_result.wuxing,
                "shishen": chart_result.shishen,
                "geju": chart_result.geju,
                "yongshen": chart_result.yongshen,
            }, ensure_ascii=False)
        elif isinstance(chart_result, dict):
            chart_json = json.dumps(chart_result, ensure_ascii=False)
        elif chart_result is not None:
            chart_json = json.dumps({"type": type(chart_result).__name__}, ensure_ascii=False)
        else:
            chart_json = ""

        question_enc = _encrypt_text(question)
        chart_enc = _encrypt_text(chart_json)
        analysis_enc = _encrypt_text(analysis)

        cursor = conn.execute(
            "INSERT INTO consultations (user_id, question, intent, chart_data, analysis) VALUES (?,?,?,?,?)",
            (user_id, question_enc, intent, chart_enc, analysis_enc),
        )
        consultation_id = cursor.lastrowid
        self._last_consultation_id = consultation_id
        conn.commit()
        conn.close()
        return consultation_id

    def get_user_stats(self) -> dict:
        """获取用户统计"""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_cons = conn.execute("SELECT COUNT(*) FROM consultations").fetchone()[0]
        conn.close()
        return {"total_users": total, "total_consultations": total_cons}

    def get_all_users_with_bazi(self) -> list:
        """查询所有保存了八字信息的用户（bazi_info 自动解密）"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT user_id, bazi_info, push_enabled, push_time FROM users WHERE bazi_info IS NOT NULL"
        ).fetchall()
        conn.close()
        users = []
        for row in rows:
            bazi_raw = row[1]
            bazi_data = None
            if bazi_raw:
                try:
                    bazi_data = json.loads(_decrypt_or_plain(bazi_raw))
                except (ValueError, TypeError):
                    bazi_data = None
            users.append({
                "user_id": row[0],
                "bazi_info": bazi_data,
                "push_enabled": bool(row[2]),
                "push_time": row[3] or "08:00",
            })
        return users

    def get_user_push_settings(self, user_id: str) -> dict:
        """查询用户推送设置"""
        conn = self._connect()
        row = conn.execute(
            "SELECT push_enabled, push_time FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()
        if row:
            return {"push_enabled": bool(row[0]), "push_time": row[1] or "08:00"}
        return {"push_enabled": False, "push_time": "08:00"}

    def set_push_enabled(self, user_id: str, enabled: bool):
        """设置用户是否开启推送"""
        conn = self._connect()
        conn.execute(
            "UPDATE users SET push_enabled=?, updated_at=? WHERE user_id=?",
            (1 if enabled else 0, datetime.now().isoformat(), user_id),
        )
        conn.commit()
        conn.close()

    def set_push_time(self, user_id: str, push_time: str):
        """设置用户推送时间"""
        conn = self._connect()
        conn.execute(
            "UPDATE users SET push_time=?, updated_at=? WHERE user_id=?",
            (push_time, datetime.now().isoformat(), user_id),
        )
        conn.commit()
        conn.close()

    def get_user_consultations(self, user_id: str, intent: Optional[str] = None,
                               limit: int = 20) -> list:
        """获取用户最近咨询历史（question/analysis 自动解密）。

        Task 6 扩展：intent 过滤（如 intent="dream" 只看解梦记录）——
        向后兼容：intent=None（缺省）不过滤，行为与旧版完全一致。
        """
        conn = self._connect()
        sql = (
            """SELECT id, question, intent, analysis, feedback, created_at
               FROM consultations
               WHERE user_id = ?"""
        )
        params: list = [user_id]
        if intent is not None:
            sql += " AND intent = ?"
            params.append(intent)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "question": _decrypt_or_plain(r[1]),
                "intent": r[2],
                "analysis_preview": (_decrypt_or_plain(r[3]) or "")[:100],
                "feedback": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    def get_user_hehun_records(self, user_id: str, limit: int = 50) -> list:
        """获取用户最近合盘记录（intent='hehun'，只返回脱敏摘要 chart_data）。

        隐私红线：只回 chart_data（得分/等级/关系/三维/缘语/缘笺，均不含生辰、
        时辰、出生地）；非 yuan_union 类型的记录跳过。chart_data 自动解密。
        """
        conn = self._connect()
        rows = conn.execute(
            """SELECT id, chart_data, created_at
               FROM consultations
               WHERE user_id = ? AND intent = 'hehun'
               ORDER BY created_at DESC, id DESC
               LIMIT ?""",
            (user_id, int(limit)),
        ).fetchall()
        conn.close()
        records = []
        for r in rows:
            chart = None
            raw = _decrypt_or_plain(r[1])
            if raw:
                try:
                    chart = json.loads(raw)
                except (ValueError, TypeError):
                    chart = None
            if not chart or chart.get("type") != "yuan_union":
                continue
            records.append({
                "id": r[0],
                "chart": chart,
                "created_at": r[2],
            })
        return records

    def get_last_consultation_id(self, user_id: str) -> Optional[int]:
        """获取用户最近一次咨询的 ID（无则返回 None）。

        Bugfix: /api/chat 反馈条需要真实咨询 ID。last_consultation_id 只是
        进程内最后一次保存的 ID（可能是 0 或属于其他用户），按 user_id 从
        数据库查才是准确的。
        """
        conn = self._connect()
        row = conn.execute(
            "SELECT id FROM consultations WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.close()
        return row[0] if row else None

    def get_consultation(self, consultation_id: int) -> Optional[Dict]:
        """按 ID 查询单条咨询完整记录（用于报告详情；敏感字段自动解密）。"""
        conn = self._connect()
        row = conn.execute(
            """SELECT id, user_id, question, intent, chart_data, analysis, feedback, created_at
               FROM consultations WHERE id = ?""",
            (consultation_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "user_id": row[1],
            "question": _decrypt_or_plain(row[2]),
            "intent": row[3],
            "chart_data": _decrypt_or_plain(row[4]),
            "analysis": _decrypt_or_plain(row[5]),
            "feedback": row[6],
            "created_at": row[7],
        }

    def get_user_accuracy(self, user_id: str) -> dict:
        """计算用户历史预测准确率"""
        conn = self._connect()
        rows = conn.execute(
            """SELECT id, feedback FROM consultations
               WHERE user_id = ? AND feedback IS NOT NULL AND feedback != ''""",
            (user_id,),
        ).fetchall()
        conn.close()

        total = len(rows)
        positive = sum(1 for r in rows if r[1] == "positive")
        negative = sum(1 for r in rows if r[1] == "negative")

        accuracy_pct = round((positive / total) * 100, 1) if total > 0 else None

        return {
            "user_id": user_id,
            "total_feedback": total,
            "positive": positive,
            "negative": negative,
            "accuracy_pct": accuracy_pct,
        }

    def save_feedback(self, consultation_id: int, feedback: str) -> bool:
        """保存用户反馈（positive/negative）"""
        if feedback not in ("positive", "negative"):
            return False
        conn = self._connect()
        conn.execute(
            "UPDATE consultations SET feedback = ? WHERE id = ?",
            (feedback, consultation_id),
        )
        conn.commit()
        conn.close()
        return True

    def get_total_predictions(self) -> dict:
        """获取全局预测统计"""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM consultations").fetchone()[0]
        with_feedback = conn.execute(
            "SELECT COUNT(*) FROM consultations WHERE feedback IS NOT NULL AND feedback != ''"
        ).fetchone()[0]
        conn.close()

        verified_pct = round((with_feedback / total) * 100, 1) if total > 0 else 0
        return {
            "total_predictions": total,
            "verified_count": with_feedback,
            "verified_pct": verified_pct,
        }

    def log_push(self, user_id: str, push_date: str, message: str, success: bool = True, error: str = ""):
        """记录推送日志"""
        conn = self._connect()
        conn.execute(
            "INSERT INTO push_log (user_id, push_date, message, success, error) VALUES (?,?,?,?,?)",
            (user_id, push_date, message, 1 if success else 0, error),
        )
        conn.commit()
        conn.close()

    def get_push_log(self, user_id: str, push_date: str) -> list:
        """获取指定日期的推送记录"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, message, success, error, created_at FROM push_log WHERE user_id=? AND push_date=?",
            (user_id, push_date),
        ).fetchall()
        conn.close()
        return [
            {"id": r[0], "message": r[1], "success": bool(r[2]), "error": r[3], "created_at": r[4]}
            for r in rows
        ]
