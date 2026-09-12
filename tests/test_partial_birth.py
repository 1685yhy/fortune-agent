# -*- coding: utf-8 -*-
"""Task F2：渐进式出生信息累积——年龄→年份推算 + 部分信息回显 + 缺什么要什么。

生产用户反馈（PM 真机）：对话中分步提供出生信息（"我今年50岁了" →
"我1976年生的，现在50岁了" → "帮我排1976年出生的盘，出生时间10点以后"），
AI 每次都要求完整「出生年月日时+出生地+性别」，不利用已给信息。

本文件逐条落地 brief「测试要求」：
1. 年龄→年份（current_year=2026 显式传，与 PM 场景一致）
2. 中文数字年转换（含两位启发：27-99→19xx，00-26→20xx）
3. 部分信息提取（只含命中的键，无任何命中 → {}）
4. 会话历史累积合并（旧→新→当前，最新者胜；missing 固定顺序）
5. 渐进引导文案（known 非空 → 回显 + 只问缺失项、零 LLM；known 空 → 原文案）
6. 齐全自动排盘（_handle_bazi 部分信息齐全 → 直接 _do_bazi_analysis）
7. _extract_bazi_info 行为不变回归

红线回归：_extract_bazi_info 语义不动；无档案基线读取；累积不落库。
"""
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from unittest.mock import Mock, patch  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402
from src.bot.handler import _cn_year_to_int, _extract_age  # noqa: E402


def make_handler(**kw) -> MessageHandler:
    """object.__new__ 装配 _handle_bazi/_free_chat 所需 Mock 属性（跑真实方法体）。"""
    h = object.__new__(MessageHandler)
    h.engine = Mock()
    h.llm = Mock()
    h.llm.analyze.return_value = Mock(response="分析")
    h.dao = Mock()
    h.dao.get_user_bazi.return_value = None
    h.retriever = Mock()
    h.memory = None
    h.memory_system = None
    h._downgraded = {}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._emit_stream_event = Mock()
    h._consume_pregen_instant = Mock(return_value=None)
    h._gen_instant_reply = Mock(return_value="")
    h._do_bazi_analysis = Mock(return_value="分析结果")
    h._save_bazi_records = Mock()
    h._get_personalized_context = Mock(return_value="")
    h._route_by_scenario = Mock(return_value=None)
    h._gen_chart_title = Mock(return_value="测试标题")
    h._gen_followup_questions = Mock(return_value="")
    h._add_feedback_prompt = Mock(side_effect=lambda s: s)
    h._alloc_citations = Mock(return_value=1)
    h._append_citations = Mock()
    h._extract_user_context = Mock(return_value="")
    for k, v in kw.items():
        setattr(h, k, v)
    return h


# ------------------------------------------------------------- 1. 年龄→年份


def test_age_this_year_50():
    """'我今年50岁了' → 2026-50=1976（与 PM 场景一致）"""
    h = make_handler()
    assert h._extract_partial_birth("我今年50岁了", current_year=2026)["year"] == 1976


def test_age_xusui_50():
    """'我虚岁50了' → 虚岁 +1 → 1977"""
    h = make_handler()
    assert h._extract_partial_birth("我虚岁50了", current_year=2026)["year"] == 1977


def test_age_now_50():
    """'现在50'（无"岁"字，前缀词引导）→ 1976"""
    h = make_handler()
    assert h._extract_partial_birth("现在50", current_year=2026)["year"] == 1976


def test_age_this_year_is_which_year_no_false_positive():
    """'今年是几几年' → 不误命中（正则不把'今年'当年龄引导）"""
    h = make_handler()
    assert "year" not in h._extract_partial_birth("今年是几几年", current_year=2026)


def test_age_out_of_range_and_time_guard():
    """超界年龄（200 岁）不取；'现在3点了'是时间不是年龄"""
    h = make_handler()
    assert "year" not in h._extract_partial_birth("我今年200岁了", current_year=2026)
    assert "year" not in h._extract_partial_birth("现在已经3点了", current_year=2026)


def test_age_last_hit_wins():
    """多条年龄命中取最后一条（最新者胜）"""
    h = make_handler()
    assert h._extract_partial_birth("我今年40岁，不，是50岁", current_year=2026)["year"] == 1976


def test_extract_age_helper():
    assert _extract_age("我今年50岁了") == 50
    assert _extract_age("现在50") == 50
    assert _extract_age("今年是几几年") is None
    assert _extract_age("都已经3点了") is None
    assert _extract_age("五十岁") is None  # 中文数字年龄不支持（范围控制）


# ------------------------------------------------------------- 2. 中文数字年


def test_cn_year_two_digit_76():
    """'排个七六年的盘' → 76 → 1976（27-99 → 19xx）"""
    h = make_handler()
    assert h._extract_partial_birth("排个七六年的盘")["year"] == 1976


def test_cn_year_four_digit():
    """'一九七六年出生' → 1976"""
    h = make_handler()
    assert h._extract_partial_birth("一九七六年出生")["year"] == 1976


def test_cn_year_lingliu():
    """'零六年' → 06 → 2006（00-26 → 20xx）"""
    h = make_handler()
    assert h._extract_partial_birth("零六年")["year"] == 2006


def test_cn_year_helpers():
    assert _cn_year_to_int("一九七六") == 1976
    assert _cn_year_to_int("七六") == 76
    assert _cn_year_to_int("零六") == 6
    assert _cn_year_to_int("〇五") == 5
    assert _cn_year_to_int("十二") is None  # 含十 → 不支持（个位'十'不出现）


# ------------------------------------------------------------- 3. 部分信息提取


def test_partial_year_only():
    """'我1976年生的' → {"year": 1976}（只含命中的键）"""
    h = make_handler()
    assert h._extract_partial_birth("我1976年生的") == {"year": 1976}


def test_partial_hour_only():
    """'出生时间10点以后' → {"hour": 10, "minute": 0}"""
    h = make_handler()
    assert h._extract_partial_birth("出生时间10点以后") == {"hour": 10, "minute": 0}


def test_partial_year_and_hour():
    """'帮我排1976年出生的盘，出生时间10点以后' → {"year":1976,"hour":10}"""
    h = make_handler()
    assert h._extract_partial_birth(
        "帮我排1976年出生的盘，出生时间10点以后") == {"year": 1976, "hour": 10, "minute": 0}


def test_partial_empty_msg():
    """无任何命中 → {}"""
    h = make_handler()
    assert h._extract_partial_birth("帮我看看我的财运") == {}
    assert h._extract_partial_birth("") == {}
    assert h._extract_partial_birth("你好，今天天气不错") == {}


def test_partial_month_day_cn_and_city_gender():
    """农历月日 + 最内层城市 + 独立性别"""
    h = make_handler()
    r = h._extract_partial_birth("三月初三，吉林省长春市榆树市出生，男")
    assert r["month"] == 3 and r["day"] == 3
    assert r["city"] == "榆树市"
    assert r["gender"] == "男"


def test_partial_gender_no_false_positive():
    """'渣男/美女'不误取性别；'性别男'可取"""
    h = make_handler()
    assert "gender" not in h._extract_partial_birth("我讨厌渣男")
    assert "gender" not in h._extract_partial_birth("我老婆是美女")
    assert h._extract_partial_birth("性别男")["gender"] == "男"


# ------------------------------------------------------------- 4. 累积合并


def test_collect_accumulates_history():
    """历史 '1976年' + '5月13日' + 当前 '帮我排个盘' → known 含年/月/日，
    missing 无'出生月日'"""
    h = make_handler()
    h.session_dao = Mock()
    history = [
        {"role": "user", "content": "我1976年生的"},
        {"role": "assistant", "content": "好的，还有其他信息吗"},
        {"role": "user", "content": "5月13日"},
    ]
    known, missing = h._collect_partial_birth("u1", "s1", "帮我排个盘",
                                              history=history)
    assert known["year"] == 1976
    assert known["month"] == 5
    assert known["day"] == 13
    assert "出生月日" not in missing
    assert missing == ["出生时辰", "出生城市", "性别"]


def test_collect_latest_wins():
    """最新者胜：历史 '1976年' + 当前 '1990年' → year=1990"""
    h = make_handler()
    h.session_dao = Mock()
    history = [
        {"role": "user", "content": "我1976年生的"},
        {"role": "user", "content": "1990年"},
    ]
    known, _ = h._collect_partial_birth("u1", None, "帮我排个盘", history=history)
    assert known["year"] == 1990


def test_collect_from_session_dao():
    """history=None → session_dao.get_context_for_llm(history_limit=8,
    session_id) 取 user 消息"""
    h = make_handler()
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = [
        {"role": "user", "content": "我1976年生的"},
        {"role": "user", "content": "5月13日"},
    ]
    known, missing = h._collect_partial_birth("u1", "s1", "帮我排个盘")
    h.session_dao.get_context_for_llm.assert_called_once_with(
        "u1", history_limit=8, session_id="s1")
    assert known["year"] == 1976
    assert known["month"] == 5
    assert known["day"] == 13
    assert "出生月日" not in missing


def test_collect_empty_full_missing():
    """known 为空 → missing 为全量列表（缺 year → 首位'出生年份'）"""
    h = make_handler()
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = []
    known, missing = h._collect_partial_birth("u1", None, "今天天气不错")
    assert known == {}
    assert missing == ["出生年份", "出生月日", "出生时辰", "出生城市", "性别"]


def test_collect_age_then_stated_year_clears_note():
    """历史年龄推算 → 当前直接报年份：year 最新者胜且不带'按X岁推算'说明"""
    h = make_handler()
    h.session_dao = Mock()
    history = [{"role": "user", "content": "我今年50岁了"}]
    known, _ = h._collect_partial_birth("u1", None, "我1976年生的", history=history)
    assert known["year"] == 1976
    assert "_age_used" not in known


# ------------------------------------------------------------- 5. 渐进引导


def test_prompt_known_echoes_and_asks_missing():
    """known={"year":1976} → 回显'1976年' + 只问'出生月日'等缺失项；
    年龄推算带'（按50岁周岁推算）'说明"""
    h = make_handler()
    h._quick_flash = Mock(return_value="不应调用LLM")
    out = h._gen_info_collection_prompt(
        "我今年50岁了", known={"year": 1976, "_age_used": 50},
        missing=["出生月日", "出生时辰", "出生城市", "性别"])
    assert "1976年" in out
    assert "出生月日" in out
    assert "（按50岁周岁推算）" in out
    assert "再告诉我出生月日、出生时辰、出生城市、性别" in out
    h._quick_flash.assert_not_called()  # known 非空 → 固定模板零 LLM


def test_prompt_known_nonempty_lite_no_llm():
    """lite=True + known 非空 → 同样回显固定模板，不调 LLM（裁决 1）"""
    h = make_handler()
    h._quick_flash = Mock(return_value="不应调用LLM")
    out = h._gen_info_collection_prompt(
        "我1976年生的，出生时间10点以后", lite=True,
        known={"year": 1976, "hour": 10, "minute": 0},
        missing=["出生月日", "出生时辰", "出生城市", "性别"])
    assert "1976年" in out and "10点以后" in out
    h._quick_flash.assert_not_called()


def test_prompt_known_empty_keeps_original_llm():
    """known 空 → 与原文案一致（LLM 引导走 _quick_flash，prompt 一字不改）"""
    h = make_handler()
    h._quick_flash = Mock(return_value="LLM引导文案")
    out = h._gen_info_collection_prompt("随便问问")
    h._quick_flash.assert_called_once()
    prompt_arg = h._quick_flash.call_args[0][0]
    assert "请生成一段友善的引导" in prompt_arg  # 原 prompt 模板未变
    assert out == "LLM引导文案"


def test_prompt_known_empty_lite_keeps_original():
    """lite=True + known 空 → 原 lite 固定文案一字不改"""
    h = make_handler()
    h._quick_flash = Mock(return_value="不应调用LLM")
    out = h._gen_info_collection_prompt("随便问问", lite=True)
    assert out == ("好的，想帮你看看八字～请告诉我：\n"
                   "📅 出生年月日（阳历/阴历）\n⏰ 几点几分\n"
                   "📍 出生城市\n👤 性别（男/女，这个很重要，影响大运方向）\n\n"
                   "💡 示例：1990年5月20日 下午3点 北京 男")
    h._quick_flash.assert_not_called()


def test_format_partial_echo_full():
    """回显格式示例：年（推算说明）+ 月日 + 时辰（保留'以后'）+ 城市 + 性别"""
    from src.bot.handler import _format_partial_echo
    echo = _format_partial_echo(
        {"year": 1976, "_age_used": 50, "month": 5, "day": 13,
         "hour": 10, "minute": 0, "city": "榆树市", "gender": "男"},
        "我今年50岁了，5月13日，10点以后，榆树市，男")
    assert echo == "出生于1976年（按50岁周岁推算）、5月13日、10点以后、榆树市、男"
    # 无"以后"字样 → 不带
    echo2 = _format_partial_echo({"hour": 10, "minute": 30}, "出生时间10点30分")
    assert echo2 == "10点30分"
    # 时辰字 → 回显时辰
    echo3 = _format_partial_echo({"hour": 23, "minute": 0}, "子时出生")
    assert echo3 == "子时"


# ------------------------------------------------------------- 6. 齐全自动排盘


def test_handle_bazi_partial_complete_direct_analysis():
    """_handle_bazi：当前消息含部分信息 + 历史累积年/月/日齐全 →
    直接调 _do_bazi_analysis（mock 断言被调、参数正确），不引导"""
    h = make_handler()
    h._try_reuse_chart = Mock(return_value="")
    h._extract_bazi_info = Mock(return_value=None)
    h._get_user_birth_profile = Mock(return_value=None)
    h._do_bazi_analysis = Mock(return_value="分析结果")
    # 年来自历史累积（collect 返回完整 known），当前消息只带月/日/时/城市/性别
    with patch.object(h, "_collect_partial_birth",
                      return_value=({"year": 1976, "month": 5, "day": 13,
                                     "hour": 10, "minute": 0, "city": "榆树市",
                                     "gender": "男"}, [])) as m_collect:
        out = h._handle_bazi("5月13日 10点 榆树市 男，帮我排个盘", "u1")
    m_collect.assert_called_once()
    h._do_bazi_analysis.assert_called_once()
    args = h._do_bazi_analysis.call_args[0]
    assert args[:7] == (1976, 5, 13, 10, 0, "榆树市", "男")
    assert args[7] == "5月13日 10点 榆树市 男，帮我排个盘"  # question 原样
    assert args[8] == "u1"
    assert out == "分析结果"


def test_handle_bazi_partial_complete_defaults_hour_gender():
    """年/月/日齐全但无时辰 → hour/minute 缺省 0、gender 缺省 unknown、
    city 缺省空串（沿用 _do_bazi_analysis 参数约定与 1384 语义）"""
    h = make_handler()
    h._try_reuse_chart = Mock(return_value="")
    h._extract_bazi_info = Mock(return_value=None)
    h._get_user_birth_profile = Mock(return_value=None)
    h._do_bazi_analysis = Mock(return_value="分析结果")
    with patch.object(h, "_collect_partial_birth",
                      return_value=({"year": 1976, "month": 5, "day": 13}, [])):
        h._handle_bazi("我1976年5月13日生的", "u1")
    args = h._do_bazi_analysis.call_args[0]
    assert args[:7] == (1976, 5, 13, 0, 0, "", "unknown")


def test_handle_bazi_partial_incomplete_progressive():
    """_handle_bazi：部分信息不全 → _gen_info_collection_prompt 带
    known/missing 渐进引导，不排盘"""
    h = make_handler()
    h._try_reuse_chart = Mock(return_value="")
    h._extract_bazi_info = Mock(return_value=None)
    h._get_user_birth_profile = Mock(return_value=None)
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    with patch.object(h, "_collect_partial_birth",
                      return_value=({"year": 1976},
                                    ["出生月日", "出生时辰", "出生城市", "性别"])):
        out = h._handle_bazi("我1976年生的", "u1")
    h._gen_info_collection_prompt.assert_called_once_with(
        "我1976年生的", lite=False,
        known={"year": 1976},
        missing=["出生月日", "出生时辰", "出生城市", "性别"])
    h._do_bazi_analysis.assert_not_called()
    assert out == "渐进引导"


def test_handle_bazi_no_partial_keeps_saved_profile():
    """当前消息无部分信息 → 走既有 saved 档案复用分支（3259-3271 原样保留）"""
    h = make_handler()
    h._try_reuse_chart = Mock(return_value="")
    h._extract_bazi_info = Mock(return_value=None)
    h._get_user_birth_profile = Mock(return_value={
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "男"})
    h._gen_reuse_acknowledgment = Mock(return_value="")
    h._do_bazi_analysis = Mock(return_value="分析结果")
    out = h._handle_bazi("帮我看看八字", "u1")
    h._do_bazi_analysis.assert_called_once()
    args = h._do_bazi_analysis.call_args[0]
    assert args[:7] == (1990, 5, 20, 15, 0, "北京", "男")
    assert out == "分析结果"


def test_handle_bazi_no_partial_no_saved_plain_prompt():
    """全无信息 → 原 _gen_info_collection_prompt 行为（known 缺省 None）"""
    h = make_handler()
    h._try_reuse_chart = Mock(return_value="")
    h._extract_bazi_info = Mock(return_value=None)
    h._get_user_birth_profile = Mock(return_value=None)
    h._gen_info_collection_prompt = Mock(return_value="原引导")
    out = h._handle_bazi("帮我看看八字", "u1")
    h._gen_info_collection_prompt.assert_called_once_with(
        "帮我看看八字", lite=False)
    assert out == "原引导"


# ------------------------------------------------------------- free_chat 路径


def test_free_chat_partial_year_progressive_prompt():
    """复现 2 路径：无档案 + 4 位年份消息 → free_chat 不返固定全格式，
    走渐进引导（缺什么要什么）"""
    h = make_handler()
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = []
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    out = h._free_chat("我1976年生的，现在50岁了", "u1", session_id="s1")
    h._gen_info_collection_prompt.assert_called_once()
    args, kw = h._gen_info_collection_prompt.call_args
    assert kw["known"]["year"] == 1976
    assert out == "渐进引导"


def test_free_chat_no_partial_keeps_fixed_format():
    """有年份但无可提取部分信息（年份超界）→ 保持原固定格式引导（行为不变）"""
    h = make_handler()
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = []
    h._gen_info_collection_prompt = Mock(return_value="不应走")
    out = h._free_chat("帮我看看1800年的历史", "u1", session_id="s1")
    h._gen_info_collection_prompt.assert_not_called()
    assert "看起来您可能在提供出生信息" in out


def test_free_chat_partial_hint_injected():
    """复现 1 路径：无档案 + 年龄消息 → LLM 提示词注入
    '目前已确认…只询问缺失项'（不要求完整格式、不问今年是几几年）"""
    from datetime import date
    h = make_handler()
    h.session_dao = None  # 无会话存储 → 单消息模式，当前消息即累积源
    h.llm.chat.return_value = Mock(response="好的，你出生于…")
    expected_year = date.today().year - 50  # 50岁 → 当前年-50（生产 2026 → 1976）
    out = h._free_chat("我今年50岁了", "u1")
    assert out == "好的，你出生于…"
    chat_arg = h.llm.chat.call_args[0][0]
    assert "【重要】用户正在分步提供出生信息" in chat_arg
    assert f"目前已确认：出生于{expected_year}年（按50岁周岁推算）" in chat_arg
    assert "只询问缺失项：出生月日、出生时辰、出生城市、性别" in chat_arg
    assert "不要问今年是哪一年" in chat_arg


def test_free_chat_emotion_no_hint():
    """情绪支持路径不注入（emotion_label 非空）"""
    h = make_handler()
    h.session_dao = None
    h.llm.chat.return_value = Mock(response="先陪陪你")
    out = h._free_chat("我今年50岁了，心里很难受", "u1", emotion_label="sadness")
    assert out == "先陪陪你"
    chat_arg = h.llm.chat.call_args[0][0]
    assert "分步提供出生信息" not in chat_arg


# ------------------------------------------------------------- 7. 行为不变回归


def test_extract_bazi_info_unchanged():
    """原'1990年5月20日 15:30 北京 男'提取结果与改造前一致"""
    h = make_handler()
    r = h._extract_bazi_info("1990年5月20日 15:30 北京 男")
    assert r == (1990, 5, 20, 15, 30, "北京", "男")


def test_extract_bazi_info_incomplete_still_none():
    """部分信息（只有年份/只有月日）→ _extract_bazi_info 仍返回 None（语义不变）"""
    h = make_handler()
    assert h._extract_bazi_info("我1976年生的") is None
    assert h._extract_bazi_info("5月13日出生") is None


# ------------------------------------------------------- 农历无效日/闰月回退
# D7 固化（批次 2 B3-21；reviewer Important 2026-08-27 修正口径）：
#   ① 合法日（含 30 天月，如 2024 二月三十——该月实有 30 天，二月三十=
#      solar 2024-04-08）→ lunar-python 正常换算（正确换算，非"顺延"）
#   ② 真无效日（小月三十，如 2024 三月三十——三月仅 29 天）→ 库抛异常
#      （"only 29 days in lunar year..."）→ 本块回退分支按近似阳历继续
#      （month=abs(month)，不放弃解析——即"回退近似"口径）
#   ③ 不存在的闰月（如 2000 闰三月）→ 抛异常 → 按平月近似（abs(month)）
#   ④ 有效闰月（2023 闰二月）→ 正常转阳历（对照组）
# 四类都不得让排盘放弃（宁可近似也不丢用户出生信息）。


def test_extract_lunar_30day_month_normal_converts():
    """合法 30 天月（2024 农历二月实有 30 天，二月三十=2024-04-08）→
    正常换算 2024-04-08（正确换算，非"顺延"）。"""
    h = make_handler()
    r = h._extract_bazi_info("农历2024年二月三十出生")
    assert r is not None, "合法日不得放弃解析"
    assert r[0:3] == (2024, 4, 8), f"应正常换算 2024-04-08，实际 {r[0:3]}"
    assert r[3:5] == (0, 0)  # 无时辰


def test_extract_lunar_invalid_day_approx_fallback():
    """真无效日（2024 农历三月仅 29 天，三月三十不存在）→ 库抛异常 →
    回退分支按近似阳历继续 (2024, 3, 30)，不放弃解析。"""
    h = make_handler()
    r = h._extract_bazi_info("农历2024年三月三十出生")
    assert r is not None, "真无效日不得放弃解析"
    assert r[0:3] == (2024, 3, 30), f"近似回退应 (2024, 3, 30)，实际 {r[0:3]}"
    assert r[3:5] == (0, 0)  # 无时辰


def test_extract_lunar_nonexistent_leap_month_flat_approx():
    """不存在闰月（2000 无闰三月）→ 平月近似 abs(month)=3，继续转阳历。"""
    h = make_handler()
    r = h._extract_bazi_info("农历2000年闰三月28出生")
    assert r is not None, "闰月不存在不得放弃解析"
    assert r[0:3] == (2000, 5, 2), f"平月近似 2000-03-28 转阳历应 2000-05-02，实际 {r[0:3]}"


def test_extract_lunar_valid_leap_month_converts():
    """对照组：有效闰月（2023 闰二月）→ 正常转阳历 2023-04-05。"""
    h = make_handler()
    r = h._extract_bazi_info("农历2023年闰二月15出生")
    assert r is not None
    assert r[0:3] == (2023, 4, 5)


# --------------------------------------------- 8. k38-RI4b 生辰时间谓词

def _freeze_today(monkeypatch, y=2026, m=9, d=12):
    """冻结「今天」基准（k38 复审实测基准日 2026-09-12）：未来日期用例不随
    真实时钟腐化。谓词取时间基准的模块名 `date`，monkeypatch 该名字即可。"""
    from datetime import date as _date
    import src.bot.handler as _handler_mod
    import src.engines.message_analyzer as _ma_mod

    class _Frozen(_date):
        @classmethod
        def today(cls):
            return cls(y, m, d)
    monkeypatch.setattr(_handler_mod, "date", _Frozen)
    monkeypatch.setattr(_ma_mod, "date", _Frozen)


def test_ri4b_future_date_not_extracted_as_birth(monkeypatch):
    """R-I-4b（复审实测·档案污染）：完整日期全在未来（婚期/预产期/行程 等非
    生辰语境）→ 不得当生辰提取。

    改前实测：「2026年10月1日结婚，帮我看看我的婚姻」→ {'year': 2026,
    'month': 10, 'day': 1, '_md_lunar': False} → 进排盘/建档路径（婚期被写成
    出生档案；F2 累积 `_collect_partial_birth`、`_redirect_single_marriage`
    是同一条边的两个消费点）。
    """
    _freeze_today(monkeypatch)
    h = make_handler()
    for text in ("2026年10月1日结婚，帮我看看我的婚姻",
                 "2026年10月1日我要结婚了，我的婚姻怎么样",
                 "2026年12月5日结婚，帮我挑个日子",
                 "2027年5月20日出生"):          # 未来年份同理
        assert h._extract_partial_birth(text) == {}, text
    # 不该走（反向·不得误杀）：真生辰/部分生辰/混合句 提取不变
    assert h._extract_partial_birth(
        "1990年5月20日 15:30 北京 男，我的婚姻怎么样")["year"] == 1990
    assert h._extract_partial_birth("我1990年生的")["year"] == 1990
    assert h._extract_partial_birth("我今年50岁了")["year"] == 1976
    mixed = h._extract_partial_birth("我1990年5月20日出生，2026年10月1日结婚")
    assert (mixed.get("year"), mixed.get("month"), mixed.get("day")) == (1990, 5, 20)


def test_ri4b_future_date_no_chart_no_archive(monkeypatch):
    """R-I-4b：未来日期（婚期）→ `_handle_bazi` 不排盘/不落档。

    `_extract_bazi_info` 会给未来日期补默认时辰/城市（hour=0、city=北京）凑出
    完整盘，故在该消费点用同一谓词作废结果：改前实测
    `_do_bazi_analysis(2026, 10, 1, 0, 0, '北京', …)` 落库（婚期当生辰）。
    """
    _freeze_today(monkeypatch)
    h = make_handler()
    h._try_reuse_chart = Mock(return_value="")
    h._get_user_birth_profile = Mock(return_value=None)
    h._is_third_party_birth_request = Mock(return_value=False)
    # 前提（真实提取器行为）：完整未来日期确实能凑出"完整盘"
    assert h._extract_bazi_info("2026年10月1日 15:30 北京 男") is not None
    for text in ("2026年10月1日结婚，帮我看看我的婚姻",
                 "2026年10月1日 15:30 北京 男"):
        h._do_bazi_analysis.reset_mock()
        h._save_bazi_records.reset_mock()
        h._handle_bazi(text, "u1")
        h._do_bazi_analysis.assert_not_called()
        h._save_bazi_records.assert_not_called()


def test_ri4b_real_birth_still_charts_and_archives(monkeypatch):
    """反向（不得误杀）：真生辰照旧排盘/落档（F2 路径 `_feed_birth` →
    `_do_bazi_analysis` → `_save_bazi_records`）。"""
    _freeze_today(monkeypatch)
    h = make_handler()
    h._try_reuse_chart = Mock(return_value="")
    h._get_user_birth_profile = Mock(return_value=None)
    h._is_third_party_birth_request = Mock(return_value=False)
    h._handle_bazi("1990年5月20日 15:30 北京 男，我的婚姻怎么样", "u1")
    h._do_bazi_analysis.assert_called_once()
    assert h._do_bazi_analysis.call_args[0][:7] == \
        (1990, 5, 20, 15, 30, "北京", "男")
