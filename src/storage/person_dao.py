"""多人档案 DAO（P2）— 一个用户下多个命主（自己/家人/朋友）。

- persons 表：user_id / name / relation / is_default + birth_enc（AES 加密 JSON）。
- 出生信息（性别/年月日时分/历法/城市）整行 JSON 加密落库（沿用 users.bazi_info
  的加密模式）；name/relation 明文（列表展示与归属过滤用）。
- 归属校验：所有按 person_id 的读写都带 user_id 条件（防越权操作他人档案）。
- 兼容迁移：users.bazi_info（旧单档案）→ 首次访问时自动迁移为默认 person。
"""
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from typing import Optional, Dict, List

from .models import init_db, connect as db_connect
from .dao import _encrypt_text, _decrypt_or_plain, _is_ciphertext

logger = logging.getLogger(__name__)

# 出生信息在 persons 表内的加密键（birth_enc 解出的 JSON 字段名）
BIRTH_KEYS = ("gender", "birth_year", "birth_month", "birth_day",
              "birth_hour", "birth_minute", "calendar", "city",
              "solar_time")

RELATION_VALUES = ("自己", "父母", "伴侣", "子女", "朋友", "其他")

# ────────────────────────────────────────────────────────────────────
# ④-4 档案出生信息一致性守卫（k19 立，k48 升级为"先问后写"）。
# 背景：21:44 事故前置根因 A = 默认命主 persons 曾整 2.5 周存错年份
# （1995 实为 1999），来源是 QA/对话期某次带错年份的写入。k19 在
# 「默认命主行被改写出生年且与既有年份差 > 2」时只打告警日志并留审计
# 痕迹（YEAR_SHIFT_REJECT=False 保守版），「明示纠正句式」（对话上下文含
# 不是/其实/更正 等）视为用户主动纠正 → 不告警。
#
# k48（2026-09-15，产品拍板"先问后写"）：升级为**拒绝写入**是唯一收口点。
# 背景 = 用户实机反馈「档案被职场文本污染 → 同一会话两张盘」：offer 文本里的
# 「各缴纳4.5%」被当 4 月 5 日写进档案（根因在提取器，见 handler k48 语境闸门），
# 而写档案这一步**从不询问**。口径（用户已批准）：
#   - 高置信出生语境（报年龄 / 我是X年X月X日生的）→ 上游照旧直接写；
#   - 与既有档案**冲突**（年/月/日/城市不一致）且非明示纠正、非表单
#     → 本守卫拒绝写入（调用方得到原样返回 + warning），
#       上游 handler 在同一判定下回一句确认（_gen_birth_conflict_ask），
#       用户确认后才写 —— **不再静默改档案**；
#   - 明示纠正/表单哨兵永远豁免（不误伤真纠正）。
# 判定实现唯一：`birth_conflict_fields`（本模块）——storage 拒绝与 handler
# 询问同源，绝不新增第二套判定（k48 brief 同族收口要求）。
# YEAR_SHIFT_REJECT=False 可一键退回 k19 的"仅告警"旧行为（应急开关）。
# ────────────────────────────────────────────────────────────────────
YEAR_SHIFT_MAX_GAP = 2
YEAR_SHIFT_REJECT = True

# 明示纠正句式（对话路径判定；子串命中即视为纠正声明，宁可漏报不可误伤）
_CORRECTION_MARKERS = (
    "不是", "不对", "错了", "弄错", "记错", "说错", "填错", "写错",
    "更正", "纠正", "其实是", "实际上", "应该是", "应为",
    "重新说", "改一下", "修改", "准确", "确认一下",
)

# 表单/显式编辑哨兵（api/user.py 传参）：用户亲手提交表单 = 明示动作，
# 与「明示纠正句式」同权豁免（对话解析误写才是 1995 事故的真正来源）。
FORM_EXPLICIT_CTX = "[表单显式提交]"


def is_correction_text(text: str) -> bool:
    """上下文文本是否表示明示纠正/显式提交（子串命中；空文本 → False）。

    对话路径传用户消息原文（含「我其实是1999年…不是1995」等句式命中）；
    api/user.py 表单路径传 FORM_EXPLICIT_CTX 哨兵（用户亲手编辑=显式）。
    """
    t = str(text or "")
    if not t:
        return False
    if t == FORM_EXPLICIT_CTX:
        return True
    return any(m in t for m in _CORRECTION_MARKERS)


# k48-r2 I-1②：完整生辰陈述（**年 + 月 + 日齐全** 且带出生语境词）。
# 口径来源 = 产品 brief 原文「高置信出生语境（出生/生于/我是X年X月X日生的/
# 报年龄）→ 仍**直接写**（保留"说一次就记住"的体验）」。用户把完整生辰一口气
# 说全 = 高置信明示声明，与"明示纠正句式"同权：**月/日/城市**冲突不再询问；
# 但**年份**仍由 k19 阈值（YEAR_SHIFT_MAX_GAP）守护——21:44 事故正是 4 年
# 跳变，完整陈述不解除年份保护（保守边界，见 k48-r2 报告）。
_COMPLETE_STATEMENT_DATE_RE = re.compile(
    r'(?:\d{4}|[〇零一二三四五六七八九]{2,4})\s*年'
    r'[^，。！？；;、\n]{0,8}?\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*[日号]?')
_COMPLETE_STATEMENT_CTX_RE = re.compile(
    r'出生|生于|生的|日生|月生|生日|生辰|农历|阴历|公历|阳历|生人|命主')


def is_complete_birth_statement(text: str) -> bool:
    """消息是否为**完整生辰陈述**（年+月+日齐 + 出生语境；k48-r2 I-1②）。

    与 `is_correction_text` 同族（本模块唯一的两类豁免判定）：命中即视为
    用户主动、高置信地声明自己的生辰 → 月/日/城市不询问（年份仍守 k19）。
    """
    t = str(text or "")
    if not t:
        return False
    if not _COMPLETE_STATEMENT_CTX_RE.search(t):
        return False
    return bool(_COMPLETE_STATEMENT_DATE_RE.search(t))


def year_shift_exceeds(old_year, new_year, max_gap: int = YEAR_SHIFT_MAX_GAP) -> bool:
    """年份差是否超过阈值（1995 vs 1999 = 4 > 2 → True 可拦截族）。"""
    try:
        a = int(old_year or 0)
        b = int(new_year or 0)
    except (TypeError, ValueError):
        return False
    if not a or not b:
        return False
    return abs(a - b) > max_gap


def birth_conflict_fields(existing: Optional[dict], new: Optional[dict],
                          ctx: str = "") -> List[str]:
    """**唯一**出生信息冲突判定（k19 年份守卫 + k48 先问后写同源实现）。

    返回冲突字段名列表（[] = 不冲突或豁免）。既有档案（默认命主行）与
    新值逐字段比对：

    - **year**：双方都有值且差值 > YEAR_SHIFT_MAX_GAP（=2，k19 既有阈值，
      容忍周岁/虚岁/年龄推算的 ±1~2 噪声）→ 冲突；
    - **month / day**：**既有侧**该字段有值、新值也有值且不同 → 冲突（对外
      统一报 "month_day"）；既有侧缺该字段 = F2 渐进累积"补全缺失项"，是正常
      流程不是冲突（用户实机污染形态是月日**一起**被当生辰，两边都有值）；
    - **city**：既有侧非空、新值非空且不同 → 冲突（空串/None = 未提供，不算）。

    豁免（返回 []）：ctx 命中「明示纠正句式」或表单哨兵（is_correction_text）
    ——用户主动纠正/亲手编辑表单永远直接写，不询问不拒绝（k19 同口径）。

    existing 可为 None（无档案/新增命主）→ 无冲突可比 → []。
    new 的键允许两种命名：year/month/day/city（对话侧）或
    birth_year/birth_month/birth_day/city（存储侧），两者同键名自动兼容。
    """
    if not isinstance(existing, dict) or not isinstance(new, dict):
        return []

    def _get(d, name):
        """取字段：兼容 person 行命名（birth_*）与档案命名（year/month/day）。

        类型不可信（非 dict 行/字段值为 Mock/容器等）→ 视为"该字段无值"（None），
        不参与比对——守卫只在**双方都拿到真实标量**时才判冲突，绝不因调用方
        传了异形对象而误报（Mock dao 的测试装配实测过这类误判）。
        """
        if not isinstance(d, dict):
            return None
        v = d[name] if name in d else d.get("birth_" + name)
        return v if isinstance(v, (str, int, float)) else None

    if is_correction_text(ctx):
        return []
    out: List[str] = []
    # 年份冲突恒查（完整陈述也不解除 21:44 族的大差年份保护——k19 阈值）
    if year_shift_exceeds(_get(existing, "year"), _get(new, "year")):
        out.append("year")
    # k48-r2 I-1②：完整生辰陈述（年+月+日齐+出生语境）= 高置信明示声明 →
    # 月日/城市不再询问（只有零散/含混的冲突才走确认问句）。
    if is_complete_birth_statement(ctx):
        return out
    for name in ("month", "day", "city"):
        old_v, new_v = _get(existing, name), _get(new, name)
        if name == "city":
            old_s = old_v.strip() if isinstance(old_v, str) else ""
            new_s = new_v.strip() if isinstance(new_v, str) else ""
            if old_s and new_s and old_s != new_s:
                out.append("city")
        else:
            try:
                old_n = int(old_v) if old_v not in (None, "") else None
                new_n = int(new_v) if new_v not in (None, "") else None
            except (TypeError, ValueError):
                continue
            if old_n is not None and new_n is not None and old_n != new_n:
                out.append(name)
    # month/day 任一不同即"月日不一致"（对外统一成一个冲突名，便于上游文案）
    if "month" in out or "day" in out:
        out = [f for f in out if f not in ("month", "day")] + ["month_day"]
    return out


def solar_time_on(raw) -> int:
    """solar_time 读口径 → 0/1：缺失/旧行/非法 → 1（默认开=产品口径 R2-4）。

    k11c（2026-09-08）：档案级真太阳时开关存 birth_enc 密文内（新列需迁移，
    密文内字段零迁移）；读路径在此统一归一——persons 存储层只可能写入
    int 0/1（_birth_dict），历史行无该键/None/非法值一律按默认开兼容。
    """
    if raw is None:
        return 1
    try:
        if str(raw).strip().lower() in ("0", "false", "off"):
            return 0
        return 1
    except Exception:
        return 1


def _normalize_gender(g) -> str:
    """性别单一中文契约（G1，2026-08-29）：male/female（大小写不敏感）→
    男/女；中文原样；其余（unknown/None/空/历史脏值）→ "unknown"。

    存储层兜底：写（_birth_dict）与读（_row_to_person）双向归一，保证
    persons API 输出 gender 恒为中文、引擎/前端契约不再出现 male/female。
    """
    v = str(g or "").strip().lower()
    if v in ("男", "male"):
        return "男"
    if v in ("女", "female"):
        return "女"
    return "unknown"


# ────────────────────────────────────────────────────────────────────
# k25 ④-6 自愈收口（2026-09-10）：persons → users.bazi_info 镜像单点实现。
# 评估依据：docs/superpowers/plans/2026-09-10-k19-profile-migration.md §4。
# 背景：users.bazi_info 是「默认档案」的 ② 兼容源，历史上只有两条写路径——
# ① 读路径自愈（birth_profile.get_user_birth_profile，走 UserDAO.save_user_bazi
# → 每次回写 bump consultation_count+1 + 过写守卫复算）；② k11c 开关翻转镜像
# （本文件 update_person 内联，直写 SQL 绕开副作用）。
# 本批把①改为与②同款的直写镜像，并把收敛时机从「读时」前移到「写时」
# （默认行/建档 → 立即镜像），读路径自愈降为低频兜底（仅历史分裂与外部
# 直写场景）。对外行为不变：镜像 payload 与读路径 out = bazi_info_of_person
# 同构，两库一致后读路径零写。
# ────────────────────────────────────────────────────────────────────

def bazi_info_of_person(person: Optional[dict]) -> dict:
    """person 行（_row_to_person 形态）→ users.bazi_info birth 镜像 dict。

    读路径（birth_profile.get_user_birth_profile 的 persons 源 out）与
    写侧镜像（默认行/建档）共用本实现——单一事实源铁律：三处（读返回、
    读路径自愈回写、写侧镜像漏斗）payload 由构造保证同构，两库一致后
    自愈判定必然为「非 stale」→ 读路径零写。

    键集 = 8 birth 键 + k11c solar_time（与读路径 out 完全一致）：
    hour/minute 沿用 persons 存储层 0→None 折叠、city 空串、gender 缺省
    unknown、calendar 缺省 solar、solar_time 缺省开=1。
    """
    p = person or {}
    return {
        "year": p.get("birth_year"),
        "month": p.get("birth_month"),
        "day": p.get("birth_day"),
        "hour": p.get("birth_hour"),
        "minute": p.get("birth_minute"),
        "city": p.get("city") or "",
        "gender": p.get("gender") or "unknown",
        "calendar": p.get("calendar") or "solar",
        "solar_time": solar_time_on(p.get("solar_time")),
    }


def mirror_bazi_info_to_users(db_path: str, user_id: str, payload: dict,
                              create_if_missing: bool = False) -> bool:
    """persons → users.bazi_info 直写镜像（k25 ④-6 单点；k11c F3 镜像同款）。

    - 直接 SQL 读写密文行，**不经 UserDAO.save_user_bazi**：绕开
      consultation_count+1 副作用与 _guard_bazi_pillars 复算（镜像 payload
      恒为 birth 形态无 bazi 四柱键，守卫对无 bazi 键写入本就不介入）。
    - 行存在 → UPDATE 全量重建（k8 语义：旧行 bazi 四柱键等非 birth 键一律
      丢弃——四柱只属于 chart_records）。
    - 行缺失 → create_if_missing=True 才 INSERT（读路径自愈首建契约）；
      consultation_count 不写入 → 落库 DEFAULT 0（读不是咨询，绝不 +1）；
      False 则 no-op（写侧镜像只镜像既有 ② 源，不代建档）。
    - 失败仅告警返回 False，绝不抛（调用方主流程不受阻）。
    """
    try:
        conn = db_connect(db_path)
        try:
            now = datetime.now().isoformat()
            enc = _encrypt_text(json.dumps(dict(payload or {}),
                                           ensure_ascii=False))
            row = conn.execute("SELECT user_id FROM users WHERE user_id = ?",
                               (user_id,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE users SET bazi_info=?, updated_at=? "
                    "WHERE user_id=?", (enc, now, user_id))
            elif create_if_missing:
                conn.execute(
                    "INSERT INTO users (user_id, bazi_info, created_at, "
                    "updated_at) VALUES (?,?,?,?)",
                    (user_id, enc, now, now))
            else:
                # k28（k25 审查 M-1）：行缺失且不代建档 = no-op。连接关闭统一由
                # 外层 finally 承担（此处不再 close，去掉同一连接的双重关闭）。
                return False
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.warning("k25 bazi_info 镜像失败 user=%s: %s",
                       user_id, str(e)[:160])
        return False


def _birth_dict(**kw) -> dict:
    """构造出生信息字典（只保留合法键；birth_year 等为 0/None 时置 None）。

    G1：gender 写归一（male/female → 男/女）；其余保持原语义
    （unknown/None/空 → "unknown"，沿用既有存储形态）。
    """
    out = {}
    for k in BIRTH_KEYS:
        v = kw.get(k)
        if k == "calendar":
            # 历法为字符串（solar/lunar），缺省 solar；必须 continue，
            # 否则落入下方 int() 归一分支把 "solar" 变成 None → 历法恒丢失
            out[k] = v or "solar"
            continue
        if k == "city":
            out[k] = str(v or "")
            continue
        if k == "gender":
            out[k] = _normalize_gender(v or "unknown")
            continue
        if k == "solar_time":
            # k11c：档案级真太阳时开关。None/空/非法 → 省略（不落库）——
            # 读路径 solar_time_on 对缺失/旧行默认开=1，与「未提供=默认开」
            # 语义一致；写侧绝不让 None 覆盖既有 0（update 合并时视为未提供）。
            if v is None or v == "":
                continue
            try:
                out[k] = 1 if int(v) else 0
            except (TypeError, ValueError):
                pass  # 非法值省略，读路径默认开兜底
            continue
        try:
            n = int(v or 0)
        except (TypeError, ValueError):
            n = 0
        out[k] = n if n else None
    return out


class PersonDAO:
    """多人档案数据访问对象。

    只读构造（k35/A7）：`PersonDAO.readonly(db)` 跳过 `init_db` 的副作用
    （`PRAGMA journal_mode=WAL` 持久切换 / 建缺表 / `_migrate_db` ALTER 加列），
    且查询走 `mode=ro` 只读连接——供迁移脚本 dry-run 等「承诺零写入」的
    只读场景使用（默认构造路径行为不变）。

    只读连接的**有证边界**（k35-复审 Important-3，实测）：SQLite 读 WAL 库时
    会自行建出 `-shm`（32KB）与 0 字节 `-wal`（`immutable=1` 可避免但会读到
    忽略 `-wal` 的陈旧快照，不采用）；对库文件与 schema 本身仍是零写入。
    打开失败（如 WAL 库 + 目录不可写）**显式抛出**，不吞、不降级为可写连接。
    """

    def __init__(self, db_path: str, readonly: bool = False):
        self.db_path = db_path
        self._readonly = bool(readonly)
        if not self._readonly:
            init_db(db_path)

    @classmethod
    def readonly(cls, db_path: str) -> "PersonDAO":
        """只读实例（k35/A7）：不建表/不 ALTER/不置 WAL，查询走 mode=ro。"""
        return cls(db_path, readonly=True)

    def _connect(self):
        if self._readonly:
            from urllib.parse import quote
            uri = "file:%s?mode=ro" % quote(os.path.abspath(self.db_path))
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            try:
                conn.execute("PRAGMA busy_timeout=10000")  # 连接级，无写入
                # 惰性打开探针（复审 Important-2 同源修法）：mode=ro 的真实失败
                # （WAL 库目录不可写等）在首条语句才抛；此处显式触发并重抛，
                # 避免错误在后续查询处才以裸 traceback 冒出。
                conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            except sqlite3.Error:
                conn.close()
                raise
            return conn
        return db_connect(self.db_path)

    # ------------------------------------------------------------
    # 行 ↔ dict 转换
    # ------------------------------------------------------------

    @staticmethod
    def _row_to_person(row) -> dict:
        """数据库行 → 对外 dict（birth_enc 自动解密）。

        行结构: (id, user_id, name, relation, is_default, birth_enc,
                  created_at, updated_at)
        """
        birth = {}
        raw = row[5] if len(row) > 5 else None
        if raw:
            try:
                birth = json.loads(_decrypt_or_plain(raw) or "{}")
            except (ValueError, TypeError):
                birth = {}
        return {
            "id": row[0],
            "user_id": row[1],
            "name": row[2] or "",
            "relation": row[3] or "其他",
            "is_default": bool(row[4]),
            "gender": _normalize_gender(birth.get("gender")),
            "birth_year": birth.get("birth_year"),
            "birth_month": birth.get("birth_month"),
            "birth_day": birth.get("birth_day"),
            "birth_hour": birth.get("birth_hour"),
            "birth_minute": birth.get("birth_minute"),
            "calendar": birth.get("calendar", "solar"),
            "city": birth.get("city", ""),
            # k11c：真太阳时开关（档案级）；密文内缺失/旧行 → 1=默认开
            "solar_time": solar_time_on(birth.get("solar_time")),
            "created_at": row[6] if len(row) > 6 else "",
            "updated_at": row[7] if len(row) > 7 else "",
        }

    # ------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------

    def list_persons(self, user_id: str) -> List[dict]:
        """列出用户全部命主（默认在前，其余按创建时间）。"""
        conn = self._connect()
        rows = conn.execute(
            """SELECT id, user_id, name, relation, is_default, birth_enc,
                      created_at, updated_at
               FROM persons WHERE user_id = ?
               ORDER BY is_default DESC, created_at ASC, id ASC""",
            (user_id,),
        ).fetchall()
        conn.close()
        return [self._row_to_person(r) for r in rows]

    def get_person(self, user_id: str, person_id) -> Optional[dict]:
        """按 ID 取命主（归属校验：只能取自己的）。"""
        try:
            person_id = int(person_id)
        except (TypeError, ValueError):
            return None
        conn = self._connect()
        row = conn.execute(
            """SELECT id, user_id, name, relation, is_default, birth_enc,
                      created_at, updated_at
               FROM persons WHERE id = ? AND user_id = ?""",
            (person_id, user_id),
        ).fetchone()
        conn.close()
        return self._row_to_person(row) if row else None

    def get_default_person(self, user_id: str, auto_migrate: bool = True) -> Optional[dict]:
        """获取用户默认命主。

        - 有 is_default=1 → 直接返回
        - 无默认但有其他 person → 把最早的提升为默认（自愈）
        - 无任何 person 且 auto_migrate → 尝试把 users.bazi_info 旧单档案
          迁移为默认 person（兼容迁移，首次访问触发）
        """
        conn = self._connect()
        row = conn.execute(
            """SELECT id, user_id, name, relation, is_default, birth_enc,
                      created_at, updated_at
               FROM persons WHERE user_id = ? AND is_default = 1
               ORDER BY id ASC LIMIT 1""",
            (user_id,),
        ).fetchone()
        if row:
            conn.close()
            return self._row_to_person(row)
        # 无默认：有其他人 → 最早者提升为默认
        row = conn.execute(
            """SELECT id, user_id, name, relation, is_default, birth_enc,
                      created_at, updated_at
               FROM persons WHERE user_id = ?
               ORDER BY id ASC LIMIT 1""",
            (user_id,),
        ).fetchone()
        if row:
            conn.execute("UPDATE persons SET is_default=1, updated_at=? WHERE id=?",
                         (datetime.now().isoformat(), row[0]))
            conn.commit()
            conn.close()
            return self._row_to_person(row)
        conn.close()
        if auto_migrate:
            return self.migrate_legacy_bazi(user_id)
        return None

    def find_person_by_birth(self, user_id: str, birth: dict) -> Optional[dict]:
        """按出生信息找同生日 person（subject=other 建档时复用）。

        匹配：年/月/日一致（+性别若两者都有）。出生信息解密后比对。
        """
        target = _birth_dict(**birth)
        if not target.get("birth_year"):
            return None
        for p in self.list_persons(user_id):
            if p.get("birth_year") != target.get("birth_year"):
                continue
            # 月/日：双方都有值才比较（缺失视为可接受，宽松复用）
            if target.get("birth_month") is not None and p.get("birth_month") is not None \
                    and p.get("birth_month") != target.get("birth_month"):
                continue
            if target.get("birth_day") is not None and p.get("birth_day") is not None \
                    and p.get("birth_day") != target.get("birth_day"):
                continue
            tg = target.get("gender")
            if tg and tg != "unknown" and p.get("gender") and p.get("gender") != "unknown":
                if p.get("gender") != tg:
                    continue
            return p
        return None

    def count_persons(self, user_id: str) -> int:
        """用户的命主总数。"""
        conn = self._connect()
        n = conn.execute("SELECT COUNT(*) FROM persons WHERE user_id = ?",
                         (user_id,)).fetchone()[0]
        conn.close()
        return n

    # ------------------------------------------------------------
    # 写操作
    # ------------------------------------------------------------

    def _year_shift_guard(self, user_id: str, existing: Optional[dict],
                          new_birth, birth_ctx: str = "") -> List[str]:
        """④-4 出生信息一致性守卫（k19 立，k48 升级"先问后写"）。

        返回**冲突字段名列表**（[] = 不冲突/不适用）并打告警日志；调用方按
        场景决定处置（storage 只报冲突，不替调用方决定"整条拒绝"还是"逐字段
        保留既有值"）：

        - 仅默认命主行（非默认=家人/朋友档案，生日本就不同，不设限）；
        - 冲突判定复用**唯一实现** `birth_conflict_fields`（年差 > 2 / 月日不一致
          / 城市不一致，明示纠正与表单哨兵豁免）——storage 与 handler 询问同源，
          避免两套判定漂移。

        参数 new_birth 兼容两种形态：单个 birth_year（k19 旧调用）或
        {"year"/"birth_year", "month"/"birth_month", ...} 整份出生 dict（k48）。
        处置口径见 create_person（整条拒绝）/ update_person（冲突字段保留既有值）。
        """
        if not existing or not existing.get("is_default"):
            return []
        if isinstance(new_birth, dict):
            new_b = new_birth
        else:
            new_b = {"birth_year": new_birth}
        conflict = birth_conflict_fields(existing, new_b, ctx=birth_ctx)
        if not conflict:
            return []
        # k48-r2（守卫日志 PII）：**绝不记录写入上下文原文**——用户消息里可能
        # 夹带住址/公司/薪资等隐私（对话原文属 PII，日志会外流到文件/采集）。
        # 只留可审计的判定要素：冲突字段、前后出生值、上下文长度与豁免类别。
        logger.warning(
            "④-4 档案守卫拒绝：默认命主出生信息改写 %s → %s（冲突字段 %s）"
            "user=%s person=%s ctx_len=%s ctx_kind=%s",
            {k: existing.get(k) for k in
             ("birth_year", "birth_month", "birth_day", "city")},
            {k: new_b.get(k) for k in ("birth_year", "birth_month", "birth_day",
                                       "city", "year", "month", "day")},
            ",".join(conflict), user_id,
            existing.get("name") or existing.get("id"),
            len(str(birth_ctx or "")),
            ("correction" if is_correction_text(birth_ctx) else
             ("complete_statement" if is_complete_birth_statement(birth_ctx)
              else "plain")))
        return conflict

    # 冲突字段名 → 需保留既有值的存储键（update_person 逐字段保留用）
    _CONFLICT_TO_KEYS = {
        "year": ("birth_year",),
        "month_day": ("birth_month", "birth_day"),
        "city": ("city",),
    }

    def create_person(self, user_id: str, name: str, relation: str = "其他",
                      birth: Optional[dict] = None,
                      is_default: Optional[bool] = None,
                      birth_ctx: str = "",
                      mirror: bool = True) -> Optional[dict]:
        """创建命主（首个自动 is_default=True；is_default=True 时清旧默认）。

        birth_ctx（k19 ④-4）：写入上下文（对话原文/表单哨兵），年份守卫用。
        mirror（k25 ④-6(b)）：默认行建档后是否立即镜像 users.bazi_info（② 源）。
        默认 True = 用户建档（API/对话）语义：写时收敛，读路径自愈降为兜底。
        **兼容迁移 migrate_legacy_bazi 必须传 False**——迁移是 ② 源 → person
        的反向路径，镜像回写会原地重写（丢旧行 bazi 四柱键、solar_time 归一）
        并破坏 k19 迁移脚本 dry-run 的「只分类不改进数据」语义。
        """
        name = (name or "").strip()[:32] or "未命名"
        if relation not in RELATION_VALUES:
            relation = "其他"
        conn = self._connect()
        now = datetime.now().isoformat()
        if is_default is None:
            is_default = self.count_persons(user_id) == 0
        if is_default:
            # ④-4：新建默认前先取既有默认行（若存在）供年份守卫比对——
            # 仅「顶替既有默认命主」场景有可比对象（无默认=正常建档放行）
            prev_default = conn.execute(
                "SELECT id, user_id, name, relation, is_default, birth_enc, "
                "created_at, updated_at FROM persons "
                "WHERE user_id = ? AND is_default = 1 ORDER BY id ASC LIMIT 1",
                (user_id,)).fetchone()
            prev_row = self._row_to_person(prev_default) if prev_default else None
            # k48：与既有默认命主出生信息冲突（且非明示纠正）→ **不顶替**
            # （整条拒绝：替换默认命主=整份档案换人，不能只保留部分字段）。
            # YEAR_SHIFT_REJECT=False 可退回 k19 的"仅告警、照建"旧行为。
            if (self._year_shift_guard(user_id, prev_row, birth,
                                       birth_ctx=birth_ctx)
                    and YEAR_SHIFT_REJECT):
                conn.close()
                return prev_row or None
            conn.execute("UPDATE persons SET is_default=0 WHERE user_id=?",
                         (user_id,))
        birth_json = _encrypt_text(json.dumps(
            _birth_dict(**(birth or {})), ensure_ascii=False))
        cursor = conn.execute(
            """INSERT INTO persons (user_id, name, relation, is_default, birth_enc,
                                    created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, name, relation, 1 if is_default else 0,
             birth_json, now, now),
        )
        conn.commit()
        pid = cursor.lastrowid
        conn.close()
        created = self.get_person(user_id, pid)
        # k25 ④-6(b)：建档即镜像（默认行 + 有出生年）——收敛时机从「读时」
        # 前移到「写时」，读路径自愈降为低频兜底。只镜像既有 ② 源行
        # （create_if_missing=False，不代建档）；失败仅告警不阻塞建档。
        # mirror=False（兼容迁移反向路径）跳过。
        if (mirror and created and is_default and created.get("birth_year")):
            mirror_bazi_info_to_users(
                self.db_path, user_id, bazi_info_of_person(created))
        return created

    def update_person(self, user_id: str, person_id, name: str = None,
                      relation: str = None, birth: Optional[dict] = None,
                      is_default: Optional[bool] = None,
                      birth_ctx: str = "") -> Optional[dict]:
        """更新命主（归属校验：只能改自己的）。None 字段不更新。

        birth_ctx（k19 ④-4）：写入上下文（对话原文/表单哨兵），年份守卫用；
        未传 = 旧调用方（守卫按「无纠正声明」处理，默认仍只告警不拒绝）。
        """
        existing = self.get_person(user_id, person_id)
        if existing is None:
            return None
        conn = self._connect()
        now = datetime.now().isoformat()
        sets, args = [], []
        if name is not None:
            sets.append("name=?")
            args.append((name or "").strip()[:32] or "未命名")
        if relation is not None:
            rel = relation if relation in RELATION_VALUES else "其他"
            sets.append("relation=?")
            args.append(rel)
        if is_default is not None:
            if is_default:
                conn.execute("UPDATE persons SET is_default=0 WHERE user_id=?",
                             (user_id,))
            sets.append("is_default=?")
            args.append(1 if is_default else 0)
        # k11c r1（F3）：开关翻转标记——本次更新是否真实改动了 solar_time
        # （merged 恒含 0/1；仅显式携带且与既有不同才置位，普通更新零镜像）
        _solar_flipped = False
        # k25 ④-6(b)：本次是否写入出生数据（镜像漏斗触发条件之一）
        _birth_written = False
        # k25 ④-6(b)：本次是否显式改动了默认标记（提升为默认 → ② 源换人镜像）
        # k28（k25 审查 M-3，保留+注释）：`is_default=` 形参目前**无生产调用方**
        # ——全 src/ grep 仅本函数声明；4 个调用点（api/user.py:723/987、
        # handler.py:3807/3818）均不传，生产「提升为默认」走 set_default（k26
        # 已接漏斗）。本分支保留为防御面（测试覆盖 update_person(is_default=)
        # 语义），若将来生产提升改走本函数无需再补漏斗。
        _default_set = is_default is not None
        if birth:
            new_birth = _birth_dict(**birth)
            # 占位符不覆盖既有值 —— 与 save_bazi_info 的 gender 保护约定一致
            # （未知/缺省视为未提供，而非显式"清空"）：
            #   gender: unknown/None/空 视为未提供；city: 空串视为未提供
            for _k in ("gender", "city"):
                if new_birth.get(_k) in ("unknown", "None", ""):
                    new_birth[_k] = None
            if any(new_birth.get(k) is not None for k in BIRTH_KEYS):
                merged = {}
                for k in BIRTH_KEYS:
                    nv = new_birth.get(k)
                    merged[k] = nv if nv is not None else existing.get(k)
                # ④-4 档案守卫（k19 立，k48 升级"先问后写"）：与既有档案冲突的
                # 出生字段**保留既有值**（不静默改写 = 用户实机两张盘的直接
                # 成因），其余字段照写。逐字段（而非整条 return）是必须的——
                # G1 性别纠正实测：整条回退会连带吞掉用户真纠正的性别双写
                # （tests/test_g1_gender_contract.py 锁）。
                for _f in self._year_shift_guard(user_id, existing, merged,
                                                 birth_ctx=birth_ctx):
                    for _k in self._CONFLICT_TO_KEYS.get(_f, ()):
                        merged[_k] = existing.get(_k)
                if merged.get("solar_time") != existing.get("solar_time"):
                    _solar_flipped = True
                _birth_written = True
                sets.append("birth_enc=?")
                args.append(_encrypt_text(json.dumps(merged, ensure_ascii=False)))
        if not sets:
            conn.close()
            return existing
        sets.append("updated_at=?")
        args.append(now)
        args += [person_id, user_id]
        conn.execute(
            f"UPDATE persons SET {', '.join(sets)} WHERE id=? AND user_id=?",
            tuple(args),
        )
        conn.commit()
        conn.close()
        updated = self.get_person(user_id, person_id)
        # k25 ④-6(b)：默认命主写侧镜像漏斗——出生数据被改写 / 本行被提升为
        # 默认时立即镜像 users.bazi_info（收敛时机从「读时」前移到「写时」，
        # 读路径自愈降为低频兜底）。只镜像既有 ② 源行（create_if_missing=
        # False，不代建档）；非默认行绝不写 ② 源（bazi_info 恒为默认档案
        # 镜像，不能被他人的出生数据覆盖）。
        # k25 自审补口（与 create_person 同条件）：必须「默认行 **且有出生年**」
        # ——无出生年的默认行（如仅填了真太阳时开关/仅改默认标记）镜像会写出
        # 全 None birth payload，覆盖 ② 源既有档案（k11c 原块以 _mdata.get("year")
        # 保护过同一场景，本漏斗不得放宽）；此情形交由下方 k11c 块按旧语义处理
        # （仅行存在且含出生年才单键镜像 solar_time），行为语义与 k11c 一致。
        _mirrored = False
        if (updated and updated.get("is_default")
                and updated.get("birth_year")
                and (_birth_written or _default_set)):
            _mirrored = mirror_bazi_info_to_users(
                self.db_path, user_id, bazi_info_of_person(updated))
        if _solar_flipped and not _mirrored:
            # k11c r1（F3 镜像）：开关翻转时同步镜像 users.bazi_info（② 源），
            # 防默认命主删除后 ② 源回弹默认开。直接 SQL 读写密文行（不经
            # UserDAO.save_user_bazi——避开 consultation_count+1 副作用）；
            # 仅行存在且含出生年才镜像，失败仅告警不阻塞。
            # k25 ④-6：默认行已由上方镜像漏斗全量覆盖（含 solar_time）→ 本块
            # 退为非默认行的单键镜像路径，行为语义不变。
            try:
                _mconn = self._connect()
                try:
                    _mrow = _mconn.execute(
                        "SELECT bazi_info FROM users WHERE user_id = ?",
                        (user_id,)).fetchone()
                    if _mrow and _mrow[0]:
                        try:
                            _mdata = json.loads(
                                _decrypt_or_plain(_mrow[0]) or "{}")
                        except (ValueError, TypeError):
                            _mdata = {}
                        if isinstance(_mdata, dict) and _mdata.get("year"):
                            _mdata["solar_time"] = merged.get("solar_time")
                            _mconn.execute(
                                "UPDATE users SET bazi_info=?, updated_at=? "
                                "WHERE user_id=?",
                                (_encrypt_text(json.dumps(
                                    _mdata, ensure_ascii=False)),
                                 datetime.now().isoformat(), user_id))
                            _mconn.commit()
                finally:
                    _mconn.close()
            except Exception as e:
                logger.warning("k11c 开关镜像 bazi_info 失败 user=%s: %s",
                               user_id, str(e)[:160])
        return updated

    def delete_person(self, user_id: str, person_id) -> bool:
        """删除命主（归属校验：只能删自己的）。

        删除的是默认命主时：剩余最早者提升为默认（若无剩余则保留 users.bazi_info
        旧档案，下次访问自动重建默认 person）。

        k26（k25 审查 I-1 同类收口）：删除默认命主 = 默认身份换人 → 被提升者的
        出生数据必须同步镜像到 ② 源（users.bazi_info）。本路径此前直接用裸 SQL
        提升默认、未接写侧镜像漏斗 → 下一次读路径自愈前，直读 ② 源的消费点会
        拿到**已删除命主**的档案（与 set_default 同源缺陷，故同批收口）。

        语义与 k25 漏斗完全一致（复用单点，不手写 payload dict）：
        - 无出生年不镜像（不得以空 payload 清空既有 ② 源；删光命主的场景本就
          保留旧 ② 源，下次访问据此自动重建默认 person）；
        - 失败仅告警不抛（mirror_bazi_info_to_users 内部兜底返回 False）。
        删非默认命主时默认身份未变 → 不镜像（零多余写）。
        """
        existing = self.get_person(user_id, person_id)
        if existing is None:
            return False
        conn = self._connect()
        conn.execute("DELETE FROM persons WHERE id=? AND user_id=?",
                     (existing["id"], user_id))
        if existing["is_default"]:
            conn.execute(
                "UPDATE persons SET is_default=1, updated_at=? "
                "WHERE user_id=? ORDER BY id ASC LIMIT 1",
                (datetime.now().isoformat(), user_id),
            )
        conn.commit()
        conn.close()
        if existing["is_default"]:
            # 提升后的新默认命主。auto_migrate=False：本路径绝不代建档
            # （无剩余命主 → None → 不镜像，保留既有 ② 源）。
            # k28（k26 审查 m-6）：读调用包异常守卫——与同类读（归属校验
            # get_person 之外的下游读）对齐：读取失败只告警，不回退删除结果
            # （persons 侧删除已是终态，② 源交给读路径自愈兜底），绝不把
            # 异常抛给调用方。
            try:
                promoted = self.get_default_person(user_id, auto_migrate=False)
            except Exception as e:
                promoted = None
                logger.warning("k28 删除默认命主后读取新默认失败 user=%s: %s",
                               user_id, str(e)[:160])
            if promoted and promoted.get("birth_year"):
                mirror_bazi_info_to_users(
                    self.db_path, user_id, bazi_info_of_person(promoted))
        return True

    def set_default(self, user_id: str, person_id) -> bool:
        """设默认（事务：清旧默认 → 置新默认）。归属校验。

        k26（k25 审查 I-1 收口）：置默认成功后把**新默认命主**的出生数据镜像到
        ② 源（users.bazi_info）——「提升为默认」是生产入口（api/user.py 的
        POST /api/persons/{id}/default），k25 只把 update_person 默认行/建档接了
        写侧漏斗，此路径漏接 → 在下一次读路径自愈前，直读 ② 源的消费点会读到
        旧默认档案（默认人员与出生数据混用的窗口）。

        语义与 k25 漏斗一致（单点复用，不手写 payload dict）：
        - 无出生年不镜像（不得以空 payload 清空既有 ② 源）；
        - 失败仅告警不抛（mirror_bazi_info_to_users 内部兜底并返回 False，
          主流程与返回值不受影响，与 update_person 走同一条路径）。
        """
        existing = self.get_person(user_id, person_id)
        if existing is None:
            return False
        conn = self._connect()
        conn.execute("UPDATE persons SET is_default=0 WHERE user_id=?", (user_id,))
        conn.execute(
            "UPDATE persons SET is_default=1, updated_at=? WHERE id=? AND user_id=?",
            (datetime.now().isoformat(), existing["id"], user_id),
        )
        conn.commit()
        conn.close()
        # k26：写侧镜像漏斗（与 update_person 默认行同条件、同 payload 实现）
        updated = self.get_person(user_id, person_id)
        if updated and updated.get("birth_year"):
            mirror_bazi_info_to_users(
                self.db_path, user_id, bazi_info_of_person(updated))
        return True

    # ------------------------------------------------------------
    # 兼容迁移（users.bazi_info 单档案 → 默认 person）
    # ------------------------------------------------------------

    def migrate_legacy_bazi(self, user_id: str) -> Optional[dict]:
        """把 users.bazi_info 旧单档案迁移为默认 person（首次访问触发）。

        - 用户表有 bazi_info（含出生年）→ 建 name="我" relation="自己" 的默认 person
        - 无 bazi_info / 无出生年 → 返回 None（不建空档案）
        """
        conn = self._connect()
        row = conn.execute("SELECT bazi_info FROM users WHERE user_id = ?",
                           (user_id,)).fetchone()
        if not row or not row[0]:
            conn.close()
            return None
        raw = row[0]
        try:
            if _is_ciphertext(raw):
                data = json.loads(_decrypt_or_plain(raw) or "{}")
            else:
                data = json.loads(raw)
        except (ValueError, TypeError):
            conn.close()
            return None
        conn.close()
        if not isinstance(data, dict) or not data.get("year"):
            return None
        birth = {
            "gender": data.get("gender", "unknown"),
            "birth_year": data.get("year"),
            "birth_month": data.get("month"),
            "birth_day": data.get("day"),
            "birth_hour": data.get("hour"),
            "birth_minute": data.get("minute"),
            "calendar": data.get("calendar", "solar"),
            "city": data.get("city", ""),
        }
        # k30（真太阳时投影同族补齐）：② 源显式携带开关时随行迁移——k11c/k25
        # 镜像写入的 bazi_info payload 含 solar_time（bazi_info_of_person 出参），
        # 旧代码整键丢失 → 迁移出的默认 person 回弹默认「开」（0 是有效值）。
        # 归一委托既有单点 solar_time_on（读路径/写侧同口径，不另立一套）；
        # 源无该键 → 不落键（_birth_dict 省略，读路径默认开，旧行零行为变化）。
        if "solar_time" in data:
            birth["solar_time"] = solar_time_on(data.get("solar_time"))
        return self.create_person(
            user_id, name="我", relation="自己", is_default=True,
            # k25 ④-6(b)：反向路径不镜像（② 源 → person，无需回写；且回写会
            # 破坏 k19 迁移脚本 dry-run 的只分类语义与旧行 bazi 键保留判定）
            mirror=False,
            birth=birth,
        )

    def default_person_bazi_info(self, user_id: str) -> Optional[dict]:
        """默认命主的八字信息 dict（/api/user/profile 兼容返回用）。

        字段名与旧 users.bazi_info 一致（year/month/day/hour/minute/gender/calendar/city
        + k11c solar_time）；前端旧代码不破（新增键为**纯增量**，旧消费方逐键
        读取零变化）。无档案时触发迁移；仍无 → None。

        k30（真太阳时投影同族补齐）：投影体复用既有单点 bazi_info_of_person
        （读路径 out / 写侧镜像 payload 同一实现，口径由构造保证同源）。此前
        本方法自建 dict 且**漏了 solar_time**——而登录响应 bazi（api/user.py）
        与 /api/user/profile 的 bazi_info 都吃它 → 前端 bazi.js `_applyBazi`
        读不到真值 → 档案关了开关仍回显「开」（k29 合盘页丢 solarTime 同族）。
        """
        p = self.get_default_person(user_id)
        if p is None:
            return None
        bazi_info = bazi_info_of_person(p)
        # 与旧字段完全一致（值为 None/空串的键不输出，避免前端 bazi.get("bazi")
        # 判空逻辑混淆）。k30：**逐键判 None/空串，绝不按真值过滤**——
        # solar_time=0（显式关）是有效值，被真值过滤折叠掉就会回弹默认「开」。
        return {k: v for k, v in bazi_info.items()
                if v is not None and v != ""}
