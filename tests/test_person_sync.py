# -*- coding: utf-8 -*-
"""对话数据复用体系 Task 1：默认命主唯一事实源（年份不同更新而非新建"命主N"）。

覆盖：
- subject=self：默认命主出生信息以最新排盘为准（年份不同也更新，不新建）
- subject=other：帮他人排盘仍按关系/姓名新建（多人档案不受影响）
"""
import sys
from pathlib import Path

import pytest
from unittest.mock import Mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.bot.handler import MessageHandler  # noqa: E402


def _make_handler(db_path: str):
    """object.__new__ 跳过 __init__，手动装配。

    注意：_sync_person_profile 内部自建 PersonDAO(self.dao.db_path)
    （非 self.person_dao），故必须给 h.dao.db_path 指向真实 SQLite 文件
    （":memory:" 每次连接是新库，测试内建的档案不可见）。
    """
    h = object.__new__(MessageHandler)
    h._analysis_facts = {}
    h.memory_system = None
    h.dao = Mock()
    h.dao.db_path = db_path
    return h


def test_sync_person_updates_default_when_year_differs(tmp_path):
    """年份不同→更新默认命主，不新建'命主N'（单一事实源）"""
    from src.storage.person_dao import PersonDAO
    db = str(tmp_path / "person_sync.db")
    pdao = PersonDAO(db)
    pdao.create_person("u1", name="我", relation="自己", is_default=True,
                       birth={"gender": "男", "birth_year": 1990, "birth_month": 5,
                              "birth_day": 20, "birth_hour": 15, "birth_minute": 0,
                              "calendar": "solar", "city": "北京"})
    h = _make_handler(db)
    h._sync_person_profile("u1",
        {"year": 1991, "month": 6, "day": 21, "hour": 9, "minute": 0,
         "city": "上海", "gender": "女"},
        subject="self")
    persons = pdao.list_persons("u1")
    assert len(persons) == 1, "年份不同不得新建命主"
    assert persons[0]["birth_year"] == 1991
    assert persons[0]["is_default"] == 1


def test_sync_person_other_still_creates(tmp_path):
    """subject=other 帮他人排盘→仍新建命主（多人档案）"""
    from src.storage.person_dao import PersonDAO
    db = str(tmp_path / "person_sync_other.db")
    pdao = PersonDAO(db)
    pdao.create_person("u1", name="我", relation="自己", is_default=True,
                       birth={"gender": "男", "birth_year": 1990, "birth_month": 5,
                              "birth_day": 20, "birth_hour": 15, "birth_minute": 0,
                              "calendar": "solar", "city": "北京"})
    h = _make_handler(db)
    h._analysis_facts = {"u1": {"subject": "other"}}
    h._sync_person_profile("u1",
        {"year": 1991, "month": 6, "day": 21, "hour": 9, "minute": 0,
         "city": "上海", "gender": "女"},
        subject="other", facts={"name": "父亲", "relation": "父母"})
    assert len(pdao.list_persons("u1")) == 2
