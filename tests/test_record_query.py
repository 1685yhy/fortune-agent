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


def _save_chart(chart_dao, user_id="u1"):
    chart_dao.save_chart(user_id, 1,
        {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
         "city": "北京", "gender": "男"},
        {"bazi": ["庚午", "辛巳", "甲申", "壬申"], "day_master": "甲",
         "dayun": [["0", "庚辰"]], "liunian": {"2026": "丙午"},
         "shensha": ["天乙贵人"], "geju": "正官格"})


def test_query_chart_scenario_question_not_hijacked(tmp_path):
    """防回归（审查缺陷）：'我的盘/上次的盘' 已从排盘直读关键词移除。

    有已存盘 + 场景问句（事业/财运）→ direct_query 返回 None，不把 chart dump
    直读文本回给用户。纯重看 → T5 直读、场景问句 → T5 排除走全流程的完整契约
    由 tests/test_chart_reuse.py test_reuse_excludes_scenario_question 覆盖。
    """
    rq = _rq(tmp_path)
    _save_chart(rq.chart_dao)
    assert rq.direct_query("u1", "我的盘适合什么事业") is None
    assert rq.direct_query("u1", "上次的盘看我的财运怎么样") is None


def test_query_chart_unique_phrases_still_direct(tmp_path):
    """防回归：排盘独有问法（排过/排的盘）不受关键词收窄误伤，仍直读秒回。"""
    rq = _rq(tmp_path)
    _save_chart(rq.chart_dao)
    out = rq.direct_query("u1", "我排过什么盘")
    assert out and "最近排过的盘" in out and "庚午" in out
    out = rq.direct_query("u1", "上次排的盘")
    assert out and "最近排过的盘" in out and "庚午" in out


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


def _rq_with_member(tmp_path, plan="basic"):
    """RecordQuery + 已开档位会员（u1 为 basic 会员，queries_used=2）。"""
    from src.storage.member_dao import MemberDAO
    m = MemberDAO(str(tmp_path / "m.db"))
    m.create_membership("u1", plan, queries_limit=999)
    conn = sqlite3.connect(str(tmp_path / "m.db"))
    conn.execute("UPDATE memberships SET queries_used = 2 WHERE user_id = 'u1'")
    conn.commit()
    conn.close()
    rq = _rq(tmp_path, member_dao=m)
    return rq


def test_query_member_pay_intent_not_hijacked(tmp_path):
    """防回归（终审缺陷）：'充值'/'额度' 是支付/引导意图词，已从会员
    直读关键词移除——支付引导全流程（handler 会员精确词分支 + LLM 意图
    路由）不得被 _q_会员 档位 dump 短路。有会员记录时也不直读。"""
    rq = _rq_with_member(tmp_path)
    assert rq.direct_query("u1", "怎么充值会员") is None
    assert rq.direct_query("u1", "充值会员多少钱") is None
    assert rq.direct_query("u1", "会员多少钱") is None  # 支付词守卫（多少钱）
    assert rq.direct_query("u1", "额度用完了怎么办") is None


def test_query_member_pure_query_still_direct(tmp_path):
    """防回归：'会员'/'我花了多少' 是纯账户信息查询词，仍直读档位信息。"""
    rq = _rq_with_member(tmp_path)
    out = rq.direct_query("u1", "我的会员是什么")
    assert out and "会员档位" in out and "额度 2/" in out
    out = rq.direct_query("u1", "我花了多少钱")
    assert out and "会员档位" in out
