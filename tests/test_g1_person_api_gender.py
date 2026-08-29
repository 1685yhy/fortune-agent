# -*- coding: utf-8 -*-
"""Task G1（P0-A）：性别契约统一 —— persons API / 存储层归一。

- src/api/user.py:_person_birth：male→'男'、female→'女'、unknown/None/空→None
  （update 路径不覆盖既有值，行为保持）；persons API 仍接受 male/female 入参
  （归一兼容），输出 gender 归一为中文。
- src/storage/person_dao.py:_birth_dict：存储层兜底归一；_row_to_person 读
  出归一（历史 male/female 存量行 → 中文）。
"""
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import pytest  # noqa: E402


# ----------------------------------------------------------------
# src/api/user.py:_person_birth 归一
# ----------------------------------------------------------------

def test_person_birth_normalizes_english_to_chinese():
    from src.api.user import PersonRequest, _person_birth
    assert _person_birth(PersonRequest(gender="male"))["gender"] == "男"
    assert _person_birth(PersonRequest(gender="female"))["gender"] == "女"
    assert _person_birth(PersonRequest(gender="Male"))["gender"] == "男"


def test_person_birth_keeps_chinese():
    from src.api.user import PersonRequest, _person_birth
    assert _person_birth(PersonRequest(gender="男"))["gender"] == "男"
    assert _person_birth(PersonRequest(gender="女"))["gender"] == "女"


def test_person_birth_unknown_none_not_overwritten():
    """unknown/空 → None（update 路径不覆盖既有值，行为保持）。

    注：PersonRequest.gender 默认 "unknown"（pydantic 不允许 None），
    _person_birth 内 (req.gender or "") 对 None 仍防御安全。
    """
    from src.api.user import PersonRequest, _person_birth
    assert _person_birth(PersonRequest(gender="unknown"))["gender"] is None
    assert _person_birth(PersonRequest(gender=""))["gender"] is None
    assert _person_birth(PersonRequest(gender="None"))["gender"] is None
    # 防御路径：request 对象 gender=None 时等价于 unknown → None
    req = type("R", (), {
        "gender": None, "birth_year": 0, "birth_month": 0, "birth_day": 0,
        "birth_hour": 0, "birth_minute": 0, "calendar": "solar",
        "city": ""})()
    assert _person_birth(req)["gender"] is None


def test_person_birth_other_fields_untouched():
    from src.api.user import PersonRequest, _person_birth
    req = PersonRequest(gender="male", birth_year=1999, birth_month=2,
                        birth_day=26, birth_hour=7, city="北京",
                        calendar="solar")
    b = _person_birth(req)
    assert b["birth_year"] == 1999 and b["birth_month"] == 2
    assert b["birth_day"] == 26 and b["birth_hour"] == 7
    assert b["city"] == "北京" and b["calendar"] == "solar"


# ----------------------------------------------------------------
# src/storage/person_dao.py:_birth_dict 存储层兜底归一
# ----------------------------------------------------------------

def test_birth_dict_normalizes_gender():
    from src.storage.person_dao import _birth_dict
    assert _birth_dict(gender="male", birth_year=1999)["gender"] == "男"
    assert _birth_dict(gender="female")["gender"] == "女"
    assert _birth_dict(gender="男")["gender"] == "男"
    assert _birth_dict(gender="女")["gender"] == "女"
    assert _birth_dict(gender="unknown")["gender"] == "unknown"
    assert _birth_dict(gender=None)["gender"] == "unknown"
    assert _birth_dict(gender="")["gender"] == "unknown"


def test_person_dao_roundtrip_gender_chinese(tmp_path):
    """create → list 全程中文性别（写归一 + 读归一）。"""
    from src.storage.person_dao import PersonDAO
    db = str(tmp_path / "g1_persons.db")
    pdao = PersonDAO(db)
    p = pdao.create_person(
        "u1", name="我", relation="自己", is_default=True,
        birth={"gender": "female", "birth_year": 1999, "birth_month": 2,
               "birth_day": 26, "birth_hour": 7, "birth_minute": 0,
               "calendar": "solar", "city": "北京"})
    assert p["gender"] == "女"
    assert pdao.list_persons("u1")[0]["gender"] == "女"
    assert pdao.get_person("u1", p["id"])["gender"] == "女"


def test_row_read_normalizes_legacy_english(tmp_path):
    """历史存量行（birth_enc 内为 male/female）读出即归一为中文。"""
    from src.storage.person_dao import PersonDAO
    from src.storage.dao import _encrypt_text
    db = str(tmp_path / "legacy.db")
    pdao = PersonDAO(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO persons (user_id, name, relation, is_default, birth_enc) "
        "VALUES (?, ?, ?, 1, ?)",
        ("u1", "我", "自己",
         _encrypt_text(json.dumps({"gender": "female", "birth_year": 1999},
                                  ensure_ascii=False))))
    conn.commit()
    conn.close()
    p = pdao.list_persons("u1")[0]
    assert p["gender"] == "女"
