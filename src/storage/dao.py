"""数据访问对象."""
import json
import logging
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

    def save_user_bazi(self, user_id: str, bazi_info: dict):
        """保存或更新用户八字信息（加密后落库）"""
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
        """软删用户：status=cancelled + cancelled_at=now（不删除任何数据）。

        users 行不存在（如仅建过命主档案）时补建注销行，保证登录拦截生效。
        90 天后由 cleanup_cancelled_accounts() 物理清除。
        """
        conn = self._connect()
        now = datetime.now().isoformat()
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

        删除：users 行 + persons + consultations + sessions + session_summaries
              + memberships + payments + push_log + 画像文件（data/memory/*.json）
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
                for table in ("consultations", "sessions", "session_summaries",
                              "memberships", "payments", "push_log", "persons"):
                    try:
                        c = conn.execute(f"DELETE FROM {table} WHERE user_id = ?",
                                         (user_id,))
                        total_deleted += c.rowcount
                    except Exception as e:
                        logger.warning("注销清理 %s.%s 失败: %s",
                                       table, user_id, e)
                try:
                    c = conn.execute("DELETE FROM users WHERE user_id = ?",
                                     (user_id,))
                    total_deleted += c.rowcount
                except Exception as e:
                    logger.warning("注销清理 users.%s 失败: %s", user_id, e)
                # 画像文件（data/memory/{user_id}.json）
                try:
                    from src.memory.user_memory import UserMemory
                    if memory_dir:
                        UserMemory(base_dir=memory_dir).clear_all(user_id)
                    else:
                        UserMemory().clear_all(user_id)
                except Exception as e:
                    logger.warning("注销清理画像文件 %s 失败: %s", user_id, e)
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
