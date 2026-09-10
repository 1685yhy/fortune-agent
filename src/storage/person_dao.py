"""多人档案 DAO（P2）— 一个用户下多个命主（自己/家人/朋友）。

- persons 表：user_id / name / relation / is_default + birth_enc（AES 加密 JSON）。
- 出生信息（性别/年月日时分/历法/城市）整行 JSON 加密落库（沿用 users.bazi_info
  的加密模式）；name/relation 明文（列表展示与归属过滤用）。
- 归属校验：所有按 person_id 的读写都带 user_id 条件（防越权操作他人档案）。
- 兼容迁移：users.bazi_info（旧单档案）→ 首次访问时自动迁移为默认 person。
"""
import json
import logging
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
# ④-4 档案年份合理性守卫（k19，2026-09-10，保守版——默认仅告警）。
# 背景：21:44 事故前置根因 A = 默认命主 persons 曾整 2.5 周存错年份
# （1995 实为 1999），来源是 QA/对话期某次带错年份的写入。本守卫在
# 「默认命主行被改写出生年且与既有年份差 > 2」时默认只打告警日志
# （绝不拒绝——防误伤用户真纠正），并留下可审计痕迹；「明示纠正句式」
# （对话上下文含 不是/其实/更正 等）视为用户主动纠正 → 不告警。
# YEAR_SHIFT_REJECT = True 为预留参数位：未来产品拍板后可开「拒绝写入」，
# 语义 = 非明示纠正的大差年份写入被拦下（调用方得到原样返回+warning）。
# 明示纠正/表单显式提交（FORM_EXPLICIT_CTX 哨兵 ctx）永远豁免，拒绝模式
# 不误伤真纠正。
# ────────────────────────────────────────────────────────────────────
YEAR_SHIFT_MAX_GAP = 2
YEAR_SHIFT_REJECT = False

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
                conn.close()
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
    """多人档案数据访问对象。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)

    def _connect(self):
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
                          new_birth_year, birth_ctx: str = "") -> bool:
        """④-4 年份守卫（保守版，仅默认命主行）：返回 True = 写入被拒绝。

        - existing 为默认命主且带既有 birth_year、新 year 与之差 > 2，
          且上下文非明示纠正（is_correction_text）→ 触发；默认仅告警
          （YEAR_SHIFT_REJECT=False）；预留参数位开拒绝时返回 True。
        - 正常建档（无既有年份）/ 差 ≤ 2 / 明示纠正 / 表单显式提交 → 放行。
        """
        if not existing or not existing.get("is_default"):
            return False
        if not year_shift_exceeds(existing.get("birth_year"), new_birth_year):
            return False
        if is_correction_text(birth_ctx):
            # 明示纠正/表单显式 → 豁免（拒绝模式同样豁免，防误伤真纠正）
            return False
        gap = abs(int(existing.get("birth_year") or 0) - int(new_birth_year or 0))
        reject = bool(YEAR_SHIFT_REJECT)
        logger.warning(
            "④-4 年份守卫%s：默认命主出生年改写 %s → %s（差 %s 年 > %s）"
            "user=%s person=%s ctx=%s",
            "拒绝" if reject else "告警", existing.get("birth_year"),
            new_birth_year, gap, YEAR_SHIFT_MAX_GAP, user_id,
            existing.get("name") or existing.get("id"), (birth_ctx or "")[:80])
        return reject

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
            if self._year_shift_guard(user_id, prev_row,
                                      (birth or {}).get("birth_year"),
                                      birth_ctx=birth_ctx):
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
                # ④-4 年份守卫：默认命主行出生年大差改写 → 默认仅告警日志
                # （YEAR_SHIFT_REJECT=True 时拒绝整次写入并返回原行）
                if self._year_shift_guard(user_id, existing,
                                          merged.get("birth_year"),
                                          birth_ctx=birth_ctx):
                    conn.close()
                    return existing
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
            promoted = self.get_default_person(user_id, auto_migrate=False)
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
        return self.create_person(
            user_id, name="我", relation="自己", is_default=True,
            # k25 ④-6(b)：反向路径不镜像（② 源 → person，无需回写；且回写会
            # 破坏 k19 迁移脚本 dry-run 的只分类语义与旧行 bazi 键保留判定）
            mirror=False,
            birth={
                "gender": data.get("gender", "unknown"),
                "birth_year": data.get("year"),
                "birth_month": data.get("month"),
                "birth_day": data.get("day"),
                "birth_hour": data.get("hour"),
                "birth_minute": data.get("minute"),
                "calendar": data.get("calendar", "solar"),
                "city": data.get("city", ""),
            },
        )

    def default_person_bazi_info(self, user_id: str) -> Optional[dict]:
        """默认命主的八字信息 dict（/api/user/profile 兼容返回用）。

        字段名与旧 users.bazi_info 一致（year/month/day/hour/minute/gender/calendar/city），
        前端旧代码不破。无档案时触发迁移；仍无 → None。
        """
        p = self.get_default_person(user_id)
        if p is None:
            return None
        bazi_info = {
            "year": p.get("birth_year"),
            "month": p.get("birth_month"),
            "day": p.get("birth_day"),
            "hour": p.get("birth_hour"),
            "minute": p.get("birth_minute"),
            "gender": p.get("gender"),
            "calendar": p.get("calendar"),
            "city": p.get("city"),
        }
        # 与旧字段完全一致（值为 None 的键不输出，避免前端 bazi.get("bazi") 判空逻辑混淆）
        return {k: v for k, v in bazi_info.items() if v not in (None, "")}
