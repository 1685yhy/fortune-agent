"""Task 6: 存量数据直读（RecordQuery）——问'我的档案/解梦/历史/签/灯语/择吉…'秒回。

简报原测试按实际 DAO 签名微调（语义不变）：
- qian_dao 类名为 QianDAO（非 QianSaveDAO），构造入参是连接（conn）；
- lamp_dao 写入方法为 upsert_lamp（非 save）；
- zeri_dao 写入方法为 upsert_plan（非 save_plan，且 plan_type 必填）；
- dao.save_consultation 的 intent 需以关键字显式传入（第 3 参是 chart_result）。
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot.record_query import RecordQuery


def _rq(tmp_path, **kw):
    from src.storage.chart_dao import ChartDAO
    from src.storage.dao import UserDAO
    from src.storage.person_dao import PersonDAO
    from src.storage.session_dao import SessionDAO
    db = str(tmp_path / "r.db")
    return RecordQuery(UserDAO(db), PersonDAO(db), SessionDAO(db),
                       ChartDAO(db), **kw)


def test_query_archive(tmp_path):
    rq = _rq(tmp_path)
    rq.dao.save_user_bazi("u1", {"year": 1990, "month": 5, "day": 20, "hour": 15,
                                 "minute": 0, "city": "北京", "gender": "男",
                                 "bazi": ["庚午", "辛巳", "甲申", "壬申"]})
    out = rq.direct_query("u1", "我的档案是什么")
    # 直读返回命主出生信息（person 档案输出格式为 年月日时·出生地·性别，
    # 不含四柱干支——断言改按出生信息校验，语义不变）
    assert out and "1990年" in out and "北京" in out


def test_query_dream(tmp_path):
    rq = _rq(tmp_path)
    rq.dao.save_consultation("u1", "梦见在水里游", None, "鱼水之象……",
                             intent="dream")
    out = rq.direct_query("u1", "我以前解过什么梦")
    assert out and "鱼水之象" in out


def test_query_history(tmp_path):
    rq = _rq(tmp_path)
    rq.session_dao.save_summary("u1", "用户关心财运，最近看房", ["关注置业"])
    out = rq.direct_query("u1", "我之前聊过什么")
    assert out and "财运" in out


def test_query_qian_lamp_zeri(tmp_path):
    rq = _rq(tmp_path)
    # 灵签（QianDAO 构造入参为连接；写入 save(user_id, no)）
    from src.storage.qian_dao import QianDAO
    q = QianDAO(sqlite3.connect(str(tmp_path / "q.db")))
    q.save("u1", 3)
    rq.qian_dao = q
    assert rq.direct_query("u1", "我摇过什么签") is not None
    # 灯语（LampDAO 写入 upsert_lamp(user_id, date, text)；直读按历史最新一条）
    from src.storage.lamp_dao import LampDAO
    lamp = LampDAO(sqlite3.connect(str(tmp_path / "l.db")))
    lamp.upsert_lamp("u1", "2026-08-23", "今日宜静心")
    rq.lamp_dao = lamp
    assert "宜静心" in rq.direct_query("u1", "我昨晚的灯语")
    # 择吉（ZeriDAO 写入 upsert_plan(user_id, scene, lucky_date, card, items, plan_type)）
    from src.storage.zeri_dao import ZeriDAO
    z = ZeriDAO(sqlite3.connect(str(tmp_path / "z.db")))
    z.upsert_plan("u1", "结婚", "2026-09-01", {"date": "2026-09-01"},
                  [{"item": "登记"}], "free")
    rq.zeri_dao = z
    assert rq.direct_query("u1", "我选的吉日") is not None
