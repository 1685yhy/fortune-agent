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


def test_query_member_pay_verb_not_hijacked(tmp_path):
    """防回归（复审缺口）：裸购买动词/问法——'我想买会员''成为会员'
    '办个会员''开会员''会员卡怎么办'等自然支付句式，handler 会员精确词
    分支（整句等值）不覆盖，守卫词表补裸动词兜底——有会员记录时
    direct_query 仍返回 None（走支付引导全流程，不被 _q_会员 档位 dump 劫持）。"""
    rq = _rq_with_member(tmp_path)
    for pay_sent in ("我想买会员", "成为会员", "办个会员", "开会员",
                     "会员卡怎么办"):
        assert rq.direct_query("u1", pay_sent) is None, pay_sent


def test_query_member_pay_verb_ask_family(tmp_path):
    """防回归（复审缺口·顺带审计）：'怎么买/怎么开/怎么办/怎么续' 问法
    家族与口语祈使（续/弄/搞）——'怎么买'原表已有，'怎么开/怎么办/怎么续'
    由 开/办/续 子串覆盖，'弄个会员''搞个会员''会员卡怎么弄' 由 弄/搞
    覆盖；均不劫持。"""
    rq = _rq_with_member(tmp_path)
    for pay_sent in ("怎么开会员", "怎么续会员", "续会员", "弄个会员",
                     "搞个会员", "怎么弄会员"):
        assert rq.direct_query("u1", pay_sent) is None, pay_sent


def test_query_member_bought_account_query_degraded_not_hijacked(tmp_path):
    """记录已买想查的账务查询（'我买了会员怎么查'）命中'买'→ 返回 None
    降级 LLM 处理——可接受（降级不劫持，不吞支付引导）；反向豁免
    （'买了/已经买'放行直读）会重新打开祈使式劫持面，刻意不做，
    本用例锁定此取舍。"""
    rq = _rq_with_member(tmp_path)
    assert rq.direct_query("u1", "我买了会员怎么查") is None


def test_query_member_pay_penetration_matrix_final_seal(tmp_path):
    """防回归（终审二次封口·穿透矩阵）：两字词拦不住单字——'充个会员'/
    '怎么充会员'（'充值'含'充'但'充个/怎么充'不落两字词）→ 档位 dump 劫持
    实锤；同族'领个会员'/'会员卡怎么领'同理。补 '充/领' 及支付动词全集
    （冲/收费/花钱/要钱/缴费/缴/交/订/申请/兑换/购/付/延期/激活/会员费/
    会员卡）后，支付意图穿透矩阵全锁 None（有会员记录也不直读，走支付
    引导全流程）——覆盖：单字穿透（充个/怎么充/冲个/领个/会员卡怎么领/
    缴个/订个/购个）、前几轮已锁句（买/成为/办/开/怎么充值/多少钱/弄/搞/
    续）、候选动词句（收费吗/多少钱一年/要钱吗/怎么交/兑换/申请/订阅/付/
    激活/延期）。"""
    rq = _rq_with_member(tmp_path)
    for pay_sent in (
            # 单字穿透（本次封口核心）
            "充个会员", "怎么充会员", "冲个会员", "领个会员",
            "会员卡怎么领", "怎么领会员", "缴个会员费", "怎么订会员",
            "怎么兑换会员", "购个会员",
            # 前几轮已锁句（回归锁定）
            "我想买会员", "成为会员", "办个会员", "开会员",
            "会员卡怎么办", "怎么充值会员", "充值会员多少钱", "会员多少钱",
            "额度用完了怎么办", "弄个会员", "搞个会员", "怎么续会员",
            # 本轮候选动词句（评估后补词）
            "会员收费吗", "会员多少钱一年", "会员要钱吗", "怎么交会员费",
            "申请开通会员", "会员怎么订阅", "怎么付会员", "怎么激活会员",
            "会员续期延期怎么弄", "会员卡怎么办理",
    ):
        assert rq.direct_query("u1", pay_sent) is None, pay_sent


def test_query_member_pure_query_still_direct_final_seal(tmp_path):
    """防回归（终审二次封口·纯查询仍直读）：'我的会员是什么'/'我花了多少
    钱'/'我的会员等级是啥'不落任何新增支付词（'等级'未触新词，无需评估
    豁免）——扩词后纯账户查询仍直读档位秒回，扩词不误伤查询面。"""
    rq = _rq_with_member(tmp_path)
    out = rq.direct_query("u1", "我的会员是什么")
    assert out and "会员档位" in out and "额度 2/" in out
    out = rq.direct_query("u1", "我花了多少钱")
    assert out and "会员档位" in out
    out = rq.direct_query("u1", "我的会员等级是啥")
    assert out and "会员档位" in out


# ──────────────────────────────── F1 出生信息直读增强 ────────────────────────────────

def _rq_with_person(tmp_path, birth=None, sub="p"):
    """建档（users.bazi_info → 迁移为默认 person）并返回 RecordQuery。

    birth 缺省：1999年5月13日 10:55 出生地榆树 男（与 PM 反馈同型数据）。
    sub 隔离不同建档场景的 db（同 tmp_path 复用会命中已建 person）。
    """
    rq = _rq(tmp_path / sub)
    if birth is None:
        birth = {"year": 1999, "month": 5, "day": 13, "hour": 10,
                 "minute": 55, "city": "榆树", "gender": "男"}
    rq.dao.save_user_bazi("u1", birth)
    return rq


def test_archive_birth_phrase_direct_read(tmp_path):
    """PM 真机反馈原句：'我的出生年月日是啥' → 直读档案（含分钟+出生地+性别）。

    直读命中即短路（0 LLM 0 引擎），回答 公历+时分，不强行展示干支。
    """
    rq = _rq_with_person(tmp_path)
    out = rq.direct_query("u1", "我的出生年月日是啥")
    assert out and "你的档案" in out
    assert "1999年5月13日10:55" in out   # 分钟并入时分（birth_minute 字段）
    assert "出生地榆树" in out and "男" in out


def test_archive_birth_variant_matrix(tmp_path):
    """变体矩阵：什么时候出生/哪年出生/出生日期/生日 各至少 1 例命中直读。"""
    rq = _rq_with_person(tmp_path)
    for q in ("我是什么时候出生的", "我是哪年出生的", "我的出生日期是什么",
              "我的生日是哪天", "我是哪天出生的", "我何时出生的"):
        out = rq.direct_query("u1", q)
        assert out and "1999年5月13日10:55" in out, f"miss: {q}"


def test_archive_lunar_birth_question(tmp_path):
    """农历/阴历问法：'我农历生日是哪天' → 追加农历日期。

    1999-05-13（公历）→ 农历三月廿八（lunar-python 转换，与排盘引擎同库）。
    """
    rq = _rq_with_person(tmp_path)
    out = rq.direct_query("u1", "我农历生日是哪天")
    assert out and "农历三月廿八" in out
    out = rq.direct_query("u1", "我阴历生日是哪天")
    assert out and "农历三月廿八" in out


def test_archive_hour_only_and_no_time(tmp_path):
    """时分取舍：仅小时 → 10时；无小时无分钟 → 不带时/分（信息不丢不造假）。"""
    rq = _rq_with_person(tmp_path, sub="h",
                         birth={"year": 1990, "month": 5, "day": 20, "hour": 15,
                                "city": "北京", "gender": "男"})
    out = rq.direct_query("u1", "我的出生日期")
    assert out and "1990年5月20日15时" in out
    rq2 = _rq_with_person(tmp_path, sub="n",
                          birth={"year": 1990, "month": 5, "day": 20,
                                 "city": "北京", "gender": "男"})
    out2 = rq2.direct_query("u1", "我的出生日期")
    assert "1990年5月20日" in out2 and "时" not in out2 and "分" not in out2


def test_archive_stored_lunar_labeled(tmp_path):
    """农历建档（calendar=lunar）→ 主日期标注'农历'（存的就是农历，不转换）。"""
    rq = _rq_with_person(tmp_path, sub="l",
                         birth={"year": 1999, "month": 5, "day": 13, "hour": 10,
                                "minute": 55, "calendar": "lunar", "gender": "女"})
    out = rq.direct_query("u1", "我的出生年月日")
    assert out and "农历1999年5月13日10:55" in out and "女" in out


def test_archive_no_person_clear_answer(tmp_path):
    """无档案 → 明确答'还没有你的档案'（查不到就是查不到，不绕弯不 LLM 兜底）。"""
    rq = _rq(tmp_path)
    out = rq.direct_query("u1", "我的出生年月日是啥")
    assert out and "还没有你的档案" in out and "帮你建档" in out


def test_archive_action_intent_not_hijacked(tmp_path):
    """防误伤（F1 守卫）：排盘/更正/建档 等动作意图即使含生日关键词也不直读。

    命中动作词即跳过档案直读返回 None（走全流程）——宁漏勿误：直读漏了只是
    慢，误读劫持才是答非所问（PM 反馈的正是劫持/绕弯）。
    """
    rq = _rq_with_person(tmp_path)
    for act in ("我生日是1999年5月13日，帮我排盘",   # 简报例 1：排盘
                "更正我的生日",                       # 简报例 2：更正
                "帮我重新排一下我的生辰八字",          # 重排 + 生辰关键词
                "改下我的出生日期",                    # 改生日单字穿透
                "帮我算我的生辰八字"):                 # 算（排盘请求穿透）
        assert rq.direct_query("u1", act) is None, act


def test_archive_greeting_not_hijacked(tmp_path):
    """防误伤：'生日'关键词的词面碰撞——生日快乐/生日蛋糕 是问候/名词，
    不落档案直读（否则问候也会被档位 dump 答非所问）。"""
    rq = _rq_with_person(tmp_path)
    assert rq.direct_query("u1", "生日快乐") is None
    assert rq.direct_query("u1", "我想订个生日蛋糕") is None
