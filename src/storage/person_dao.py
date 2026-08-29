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
              "birth_hour", "birth_minute", "calendar", "city")

RELATION_VALUES = ("自己", "父母", "伴侣", "子女", "朋友", "其他")


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

    def create_person(self, user_id: str, name: str, relation: str = "其他",
                      birth: Optional[dict] = None,
                      is_default: Optional[bool] = None) -> Optional[dict]:
        """创建命主（首个自动 is_default=True；is_default=True 时清旧默认）。"""
        name = (name or "").strip()[:32] or "未命名"
        if relation not in RELATION_VALUES:
            relation = "其他"
        birth_json = _encrypt_text(json.dumps(
            _birth_dict(**(birth or {})), ensure_ascii=False))
        conn = self._connect()
        now = datetime.now().isoformat()
        if is_default is None:
            is_default = self.count_persons(user_id) == 0
        if is_default:
            conn.execute("UPDATE persons SET is_default=0 WHERE user_id=?",
                         (user_id,))
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
        return self.get_person(user_id, pid)

    def update_person(self, user_id: str, person_id, name: str = None,
                      relation: str = None, birth: Optional[dict] = None,
                      is_default: Optional[bool] = None) -> Optional[dict]:
        """更新命主（归属校验：只能改自己的）。None 字段不更新。"""
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
        return self.get_person(user_id, person_id)

    def delete_person(self, user_id: str, person_id) -> bool:
        """删除命主（归属校验：只能删自己的）。

        删除的是默认命主时：剩余最早者提升为默认（若无剩余则保留 users.bazi_info
        旧档案，下次访问自动重建默认 person）。
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
        return True

    def set_default(self, user_id: str, person_id) -> bool:
        """设默认（事务：清旧默认 → 置新默认）。归属校验。"""
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
