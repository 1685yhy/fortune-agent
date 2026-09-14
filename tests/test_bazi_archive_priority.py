# -*- coding: utf-8 -*-
"""B3-1：对话排盘归属修复（用户问题 A）——档案命主优先 / 历史不污染 /
第三方明确信息可排第三方 / 歧义询问。

用户拍板规则（2026-08-29）：
1. 对话排盘默认用档案里的默认命主（自己的信息）；
2. 只有明确给出第三方详细生辰（如「帮我朋友排，他X年X月X日X时生」）才排第三方；
3. 实在分不清时，问一句确认（而非静默排错）。

红线保护：F2 渐进出生信息收集（commit 79834e6+0f19513，用户此前拍板功能）
体验不回退——无完整档案时「年龄→年份推算 / 部分回显 / 缺什么要什么 /
齐全自动排盘」仍工作，既有 tests/test_partial_birth.py 全绿即证明。
"""
import sys
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

CURRENT_YEAR = date.today().year

from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

ARCHIVE = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
           "city": "北京", "gender": "男"}
FRIEND_BIRTH = {"year": 1976, "month": 5, "day": 13, "hour": 10, "minute": 0,
                "city": "上海", "gender": "女"}


def make_handler(**kw) -> MessageHandler:
    """同 test_partial_birth.make_handler：object.__new__ 装配 _handle_bazi
    所需 Mock 属性（跑真实方法体）。默认：档案完整 + 历史为空。"""
    h = object.__new__(MessageHandler)
    h.engine = Mock()
    h.llm = Mock()
    h.dao = Mock()
    h.dao.get_user_bazi.return_value = None
    h.retriever = Mock()
    h.memory = None
    h.memory_system = None
    h._downgraded = {}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._try_reuse_chart = Mock(return_value="")
    h._extract_bazi_info = Mock(return_value=None)
    h._get_user_birth_profile = Mock(return_value=ARCHIVE)
    h._do_bazi_analysis = Mock(return_value="分析结果")
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    h._gen_reuse_acknowledgment = Mock(return_value="")
    h._quick_flash = Mock(return_value="")
    h._emit_stream_event = Mock()
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = []
    for k, v in kw.items():
        setattr(h, k, v)
    return h


# ── 规则1：档案命主优先（历史不污染）──────────────────────────────


def test_archive_default_when_partial_current_and_thirdparty_history():
    """档案完整 + 当前消息只有部分信息（10点以后）+ 历史含帮他人排盘信息 →
    排档案命主的盘；当前消息非身份键（时辰）叠加，历史完全不查。"""
    h = make_handler()
    h.session_dao.get_context_for_llm.return_value = [
        {"role": "user",
         "content": "帮我朋友排个盘，他1976年5月13日10点上海出生"},
    ]
    h._collect_partial_birth = Mock(return_value=(dict(FRIEND_BIRTH), []))
    out = h._handle_bazi("帮我排个盘，10点以后", "u1")
    # 档案优先 → 渐进收集（含历史）根本不进入
    h._collect_partial_birth.assert_not_called()
    h._do_bazi_analysis.assert_called_once()
    args = h._do_bazi_analysis.call_args[0]
    # 身份=档案（1990-05-20 北京 男），时辰=当前消息（10点）叠加
    assert args[:7] == (1990, 5, 20, 10, 0, "北京", "男")
    assert out == "分析结果"


def test_archive_default_plain_request_ignores_history():
    """『帮我排盘』（当前消息无出生信息）→ 档案命主盘；历史帮他人排的盘
    不进入（_collect_partial_birth 不被调用）。"""
    h = make_handler()
    h.session_dao.get_context_for_llm.return_value = [
        {"role": "user",
         "content": "帮我朋友排个盘，他1976年5月13日10点上海出生"},
    ]
    h._collect_partial_birth = Mock(return_value=(dict(FRIEND_BIRTH), []))
    h._handle_bazi("帮我排个盘", "u1")
    h._do_bazi_analysis.assert_called_once()
    assert h._do_bazi_analysis.call_args[0][:7] == (1990, 5, 20, 15, 0,
                                                    "北京", "男")
    h._collect_partial_birth.assert_not_called()


def test_archive_supplement_city_gender_merges():
    """档案缺城市/性别时，当前消息补充项叠加进档案基线（身份不变）。"""
    h = make_handler()
    h._get_user_birth_profile = Mock(return_value={
        "year": 1990, "month": 5, "day": 20, "hour": None, "minute": None,
        "city": "", "gender": ""})
    h._handle_bazi("帮我排个盘，下午3点 北京 男", "u1")
    args = h._do_bazi_analysis.call_args[0]
    assert args[:7] == (1990, 5, 20, 15, 0, "北京", "男")


# ── 规则2：明确第三方 + 出生信息 → 排第三方 ──────────────────────


def test_third_party_full_info_charts_third_party():
    """明确第三方 + 详细生辰 → 排第三方（档案不被使用）。"""
    h = make_handler()
    h._extract_bazi_info = Mock(return_value=(1976, 5, 13, 10, 0, "上海", "女"))
    h._handle_bazi("帮我朋友排个盘，他1976年5月13日10点上海出生", "u1")
    h._do_bazi_analysis.assert_called_once()
    assert h._do_bazi_analysis.call_args[0][:7] == (1976, 5, 13, 10, 0,
                                                    "上海", "女")


def test_third_party_partial_info_not_archive_chart():
    """明确第三方 + 部分信息 → 走第三方渐进收集（缺什么要什么），
    不静默排成档案命主的盘。"""
    h = make_handler()
    h._collect_partial_birth = Mock(return_value=(
        {"year": 1976}, ["出生月日", "出生时辰", "出生城市", "性别"]))
    out = h._handle_bazi("帮我朋友排个盘，他1976年生的", "u1")
    h._do_bazi_analysis.assert_not_called()
    h._collect_partial_birth.assert_called_once()
    pos, kw = h._collect_partial_birth.call_args
    assert pos == ("u1", None, "帮我朋友排个盘，他1976年生的")
    assert kw["third_party"] is True
    h._gen_info_collection_prompt.assert_called_once_with(
        "帮我朋友排个盘，他1976年生的", lite=False, known={"year": 1976},
        missing=["出生月日", "出生时辰", "出生城市", "性别"])
    assert out == "渐进引导"


def test_third_party_partial_complete_charts_third_party():
    """第三方部分信息同会话累积齐全 → 排第三方盘（不分男女档案干扰）。"""
    h = make_handler()
    h._collect_partial_birth = Mock(return_value=(dict(FRIEND_BIRTH), []))
    h._handle_bazi("帮我朋友排个盘，他10点生的", "u1")
    args = h._do_bazi_analysis.call_args[0]
    assert args[:7] == (1976, 5, 13, 10, 0, "上海", "女")


# ── 规则3：分不清时问一句（绝不静默排错）─────────────────────────


def test_ambiguity_conflicting_year_asks():
    """当前消息出生年份与档案命主不一致且无第三方指代 → 问一句确认；
    固定文案零 LLM；不排盘不引导。"""
    h = make_handler()
    out = h._handle_bazi("帮我排个盘，1976年", "u1")
    h._do_bazi_analysis.assert_not_called()
    h._gen_info_collection_prompt.assert_not_called()
    h._quick_flash.assert_not_called()
    assert "1976" in out
    assert "1990" in out
    assert "档案" in out


def test_ambiguity_self_marker_conflicting_year_asks():
    """即使带『我』，年份与档案冲突 → 仍问一句（不静默覆盖档案身份）。"""
    h = make_handler()
    out = h._handle_bazi("我1976年生的，帮我排个盘", "u1")
    h._do_bazi_analysis.assert_not_called()
    assert "1976" in out and "1990" in out and "档案" in out


def test_ambiguity_age_derived_year_conflict_asks():
    """年龄推算年份（50岁→1976）与档案冲突 → 同样询问（回显带推算说明）。"""
    h = make_handler()
    out = h._handle_bazi("我今年50岁了，帮我排个盘", "u1", )
    h._do_bazi_analysis.assert_not_called()
    assert "1976" in out and "1990" in out


def test_no_conflict_matching_year_charts_archive():
    """当前消息年份与档案一致 → 档案命主盘（不询问）。"""
    h = make_handler()
    h._handle_bazi("帮我排个盘，1990年", "u1")
    args = h._do_bazi_analysis.call_args[0]
    assert args[:7] == (1990, 5, 20, 15, 0, "北京", "男")


# ── 历史不污染：第三方标记消息不进本人累积 ────────────────────────


def test_collect_skips_third_party_history():
    """本人渐进收集：历史含他人排盘信息（最新-优先原本会赢）→ 被跳过，
    本人信息照常累积。"""
    h = make_handler()
    history = [
        {"role": "user", "content": "我1990年生的"},
        {"role": "user", "content": "5月20日"},
        {"role": "user", "content": "帮我朋友排个盘，他1976年5月13日10点出生"},
    ]
    known, missing = h._collect_partial_birth("u1", None, "帮我排个盘，10点",
                                              history=history)
    assert known["year"] == 1990  # 朋友 1976 被跳过（否则最新-优先为 1976）
    assert known["month"] == 5 and known["day"] == 20
    assert known["hour"] == 10  # 当前消息仍生效
    assert "出生年份" not in missing
    assert "出生月日" not in missing


def test_collect_keeps_self_history():
    """本人历史（无第三方指代）照常累积——F2 渐进收集不回退。"""
    h = make_handler()
    history = [
        {"role": "user", "content": "我今年50岁了"},
        {"role": "user", "content": "5月13日"},
    ]
    known, missing = h._collect_partial_birth("u1", None, "10点以后",
                                              history=history)
    assert known["year"] == CURRENT_YEAR - 50
    assert known["month"] == 5 and known["day"] == 13
    assert known["hour"] == 10


def test_collect_third_party_mode_keeps_other_history():
    """third_party=True（明确第三方排盘）→ 对方分步信息照常累积。"""
    h = make_handler()
    history = [{"role": "user", "content": "他1976年5月13日生的"}]
    known, missing = h._collect_partial_birth("u1", None, "帮我朋友排个盘，10点",
                                              history=history, third_party=True)
    assert known["year"] == 1976 and known["month"] == 5 and known["day"] == 13
    assert known["hour"] == 10
    assert "出生年份" not in missing


# ── 归属判定辅助 ──────────────────────────────────────────────────


def test_third_party_marker_detection():
    h = make_handler()
    assert h._is_third_party_birth_request("帮我朋友排个盘，他1976年生") is True
    assert h._is_third_party_birth_request("帮我老婆排个盘，1976年生") is True
    assert h._is_third_party_birth_request("帮我女儿排个盘，她2001年生") is True
    assert h._is_third_party_birth_request("帮我排个盘，1976年") is False
    assert h._is_third_party_birth_request("我1976年生的，帮我排个盘") is False


# ── B3-1-fix：第三方判定收紧（他/她/妈/爸 裸词误判修复）──────────


def test_third_party_detection_no_false_positive_other():
    """「其他我记不清了」——"其他"含"他"但非指代 → 不判第三方（纯误伤）。"""
    h = make_handler()
    assert h._is_third_party_birth_request("其他我记不清了") is False
    assert h._is_third_party_birth_request(
        "帮我排个盘，1976年，其他我记不清了") is False
    assert h._is_third_party_birth_request("帮我排个盘，1976年，其它情况忘了") is False


def test_third_party_detection_no_false_positive_mom_said():
    """「我妈说我是1976年生的」——"妈"是信息出处不是排盘对象 → 不判第三方；
    该场景是用户陈述自己的生日（与档案冲突 → 规则3问一句，不静默排错）。"""
    h = make_handler()
    assert h._is_third_party_birth_request(
        "我妈说我是1976年生的，帮我排个盘") is False
    h._collect_partial_birth = Mock(return_value=(
        {"year": 1976}, ["出生月日", "出生时辰", "出生城市", "性别"]))
    out = h._handle_bazi("我妈说我是1976年生的，帮我排个盘", "u1")
    h._do_bazi_analysis.assert_not_called()
    h._collect_partial_birth.assert_not_called()  # 本人陈述 → 档案路径，不渐进收集
    assert "1976" in out and "1990" in out and "档案" in out


def test_third_party_detection_mom_paipan_struct():
    """「给我妈排个盘，她1976年5月13日生」——帮/给+亲属+排盘结构 →
    判第三方（真实的给妈妈排盘）：走第三方收集并排妈妈的盘，
    绝不静默排成档案命主（1990）的盘。"""
    h = make_handler()
    assert h._is_third_party_birth_request(
        "给我妈排个盘，她1976年5月13日生") is True
    h._collect_partial_birth = Mock(return_value=(dict(FRIEND_BIRTH), []))
    out = h._handle_bazi("给我妈排个盘，她1976年5月13日生", "u1")
    h._do_bazi_analysis.assert_called_once()
    pos, kw = h._collect_partial_birth.call_args
    assert kw["third_party"] is True
    assert h._do_bazi_analysis.call_args[0][:7] == (1976, 5, 13, 10, 0,
                                                    "上海", "女")
    assert out == "分析结果"


def test_third_party_detection_friend_paipan_plain():
    """「帮我朋友排盘」——无出生信息但结构明确 → 判第三方（不裸词也不漏判）。"""
    h = make_handler()
    assert h._is_third_party_birth_request("帮我朋友排盘") is True
    assert h._is_third_party_birth_request("帮我老婆排盘") is True
    assert h._is_third_party_birth_request("帮她看八字，1976年生") is True


def test_third_party_detection_pronoun_birth():
    """「他1976年5月13日生」——他/她+出生信息 → 判第三方（历史过滤场景，
    无排盘结构也命中）。"""
    h = make_handler()
    assert h._is_third_party_birth_request("他1976年5月13日生的") is True
    assert h._is_third_party_birth_request("她2001年生") is True
    assert h._is_third_party_birth_request("他今年50岁了") is True
    assert h._is_third_party_birth_request("我1976年生的") is False
    assert h._is_third_party_birth_request("其他年份记不清了") is False


def test_collect_skips_pronoun_birth_history():
    """历史裸「他1976年…」消息（无排盘结构）同样被过滤——不污染本人累积。"""
    h = make_handler()
    history = [
        {"role": "user", "content": "我1990年生的"},
        {"role": "user", "content": "他1976年5月13日10点出生"},
    ]
    known, missing = h._collect_partial_birth("u1", None, "10点以后",
                                              history=history)
    assert known["year"] == 1990  # 他1976 被跳过（否则最新-优先为 1976）
    assert known["hour"] == 10
    assert "出生年份" not in missing


# ── B3-1-fix：parsed 完整信息路径规则3补全（冲突问一句）────────────


def test_parsed_conflicting_year_asks():
    """parsed 完整信息路径：完整生辰与档案命主年份冲突且无第三方指代 →
    问一句确认（复用 _gen_birth_conflict_ask 文案），不排盘不落库。"""
    h = make_handler()
    h._extract_bazi_info = Mock(
        return_value=(1976, 5, 13, 0, 0, "北京", "unknown"))
    out = h._handle_bazi("帮我排个盘，1976年5月13日", "u1")
    h._do_bazi_analysis.assert_not_called()
    h._gen_info_collection_prompt.assert_not_called()
    assert "1976" in out and "1990" in out and "档案" in out


def test_parsed_conflicting_year_mom_said_asks():
    """parsed 完整信息 + 「我妈说…」（妈是信息出处）→ 仍视为本人陈述冲突 →
    问一句，不静默排盘。"""
    h = make_handler()
    h._extract_bazi_info = Mock(
        return_value=(1976, 5, 13, 0, 0, "北京", "unknown"))
    out = h._handle_bazi("我妈说我是1976年5月13日生的，帮我排个盘", "u1")
    h._do_bazi_analysis.assert_not_called()
    assert "1976" in out and "1990" in out and "档案" in out


def test_parsed_matching_year_charts_normally():
    """parsed 完整信息与档案一致 → 正常排盘（规则1不破坏）。

    k48 输入微调：原用例消息是「1990年5月13日」而档案是 1990-05-20——k48
    把冲突判定从"仅年份"扩为 年/月/日/城市（先问后写），月日不一致现在会走
    确认问句，故此处改为与档案**完全一致**的生辰以保持本用例原意（年份一致
    → 用消息值正常排盘）；月日冲突的新行为另由
    test_k48_pollution_guard_ask.py::TestAskBeforeWrite 锁定。"""
    h = make_handler()
    h._extract_bazi_info = Mock(return_value=(1990, 5, 20, 10, 0, "北京", "男"))
    out = h._handle_bazi("帮我排个盘，1990年5月20日10点", "u1")
    h._do_bazi_analysis.assert_called_once()
    assert h._do_bazi_analysis.call_args[0][:7] == (1990, 5, 20, 10, 0,
                                                    "北京", "男")


def test_parsed_no_archive_charts_normally():
    """parsed 完整信息 + 无档案 → 正常排盘（无冲突可查）。"""
    h = make_handler()
    h._extract_bazi_info = Mock(return_value=(1976, 5, 13, 10, 0, "上海", "女"))
    h._get_user_birth_profile = Mock(return_value=None)
    out = h._handle_bazi("1976年5月13日10点上海女，帮我排个盘", "u1")
    h._do_bazi_analysis.assert_called_once()
    assert h._do_bazi_analysis.call_args[0][:7] == (1976, 5, 13, 10, 0,
                                                    "上海", "女")


# ── F2 保护：无完整档案时渐进收集照常 ─────────────────────────────


def test_no_archive_partial_progressive_unchanged():
    """无完整档案 + 当前消息部分信息 → 渐进收集（缺什么要什么）。"""
    h = make_handler()
    h._get_user_birth_profile = Mock(return_value=None)
    h._collect_partial_birth = Mock(return_value=(
        {"year": 1976}, ["出生月日", "出生时辰", "出生城市", "性别"]))
    out = h._handle_bazi("我1976年生的", "u1")
    h._do_bazi_analysis.assert_not_called()
    h._gen_info_collection_prompt.assert_called_once_with(
        "我1976年生的", lite=False, known={"year": 1976},
        missing=["出生月日", "出生时辰", "出生城市", "性别"])
    assert out == "渐进引导"


def test_no_archive_partial_complete_auto_chart():
    """无完整档案 + 当前消息部分信息累积齐全 → 直接排盘（齐全自动排盘）。"""
    h = make_handler()
    h._get_user_birth_profile = Mock(return_value=None)
    h._collect_partial_birth = Mock(return_value=(dict(FRIEND_BIRTH), []))
    h._handle_bazi("5月13日 10点 上海 女，帮我排个盘", "u1")
    args = h._do_bazi_analysis.call_args[0]
    assert args[:7] == (1976, 5, 13, 10, 0, "上海", "女")
