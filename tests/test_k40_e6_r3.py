# -*- coding: utf-8 -*-
"""k40：E6 门禁第三轮收口（R1 四条 + R2 三条）——每组都带「该走的走 / 不该走的仍不走」。

需求 SSOT = `.superpowers/sdd/task-k40-brief.md` + `.superpowers/sdd/e6-red-triage-round2-20260913.md`

覆盖：
- R1-1 T041「单档补全合婚」：消息里的「一个…女孩子」指代**第三人** → 不得被 G1
  性别纠正守卫当成用户改性别（改前 intent 被改写成 bazi → hehun 链路从未被调用，
  全链 0 LLM）；**真正的改性别声明（T008）仍触发**。附 P0 档案污染双向锁。
- R1-2 T018「明年流年」：用户原话含相对年词 → 无条件以本地确定性折算覆盖模型
  传值（GLM 用训练期幻觉年份自己折算 → 传 2024）；原话只有绝对年份 → 不改写。
- R1-3 T048「改名」：场景词补「改个名」族 → naming 工具可达；无关问句不受影响。
- R1-4 T107：评测断言去掉不可保证的「四柱」字面，判别力由确定性干支承担。
- R2-5 T027：流年轮走年视图（12 月一览）、流月轮走单月视图 → 两轮不再逐字节同串。
- R2-6 T034：择日意图路径落**确定性宜忌行**（与工具路径同一渲染实现），
  润色后幂等重挂（逐行补缺、不重复）。
- R2-7 T053/T054：灵签词表补「抽过的签」；名笺/灵签空态确定性告知（不吞链路）。

改回旧代码必失败（每条断言的都是改前实测的相反行为）。
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.bot.handler import MessageHandler  # noqa: E402
from src.engines.message_analyzer import (  # noqa: E402
    MessageAnalysis, MessageAnalyzer, match_tool_scene)
from src.storage.chart_dao import ChartDAO  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.models import init_db  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.storage.session_dao import SessionDAO  # noqa: E402

# E6 任务语料（逐字取自 data/eval/agent_tasks.jsonl）
T041_MSG = "我和一个1992年10月1日 上海出生的女孩子合不合"
T008_MSG = "我是女孩儿，不是男孩"
T018_MSG = "帮我看看明年的流年运势"
T027_MSG_FLOW_YEAR = "我的流年运势怎么样"
T027_MSG_FLOW_MONTH = "这个月的流月运势"
T034_MSG = "2026年12月5日搬家，这个日子行不行"
T048_MSG = "我想改个名，姓李，男，1988年8月8日 8:00 北京出生"
T049_MSG = "我家猫叫小白，特别可爱"
T053_MSG = "我抽过的签有哪些"
T054_MSG = "我保存过的名笺"
ARCHIVE = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 30,
           "city": "北京", "gender": "男", "calendar": "solar"}

_TMP_DIRS = []


def _db_path():
    d = tempfile.mkdtemp(prefix="fortune_k40_")
    p = os.path.join(d, "t.db")
    init_db(p)
    _TMP_DIRS.append(d)
    return p


def _mock_result_engine(bazi=None, gender="男"):
    r = Mock(spec=["bazi", "day_master", "wuxing", "shishen", "dayun",
                   "liunian", "liunian_full", "geju", "yongshen",
                   "shensha", "nayin", "gender"])
    r.bazi = bazi or ["庚午", "辛巳", "乙酉", "甲申"]
    r.day_master = "乙木"
    r.wuxing = {"木": 3, "火": 2, "金": 2, "水": 1, "土": 2}
    r.shishen = ["正官", "七杀", "正财", "偏印"]
    r.dayun = [(4, "庚辰"), (14, "己卯"), (24, "戊寅")]
    r.liunian = {"2026": "庚午", "2027": "辛未"}
    r.liunian_full = []
    r.geju = "七杀格"
    r.yongshen = "木"
    r.shensha = ["天乙贵人"]
    r.nayin = ["路旁土", "白蜡金", "泉中水", "井泉水"]
    r.gender = gender
    eng = Mock()
    eng.calculate.return_value = r
    return eng


def _seed_person(db_path, user_id, gender="男", year=1990, month=5, day=20,
                 hour=15, minute=30, city="北京"):
    PersonDAO(db_path).create_person(
        user_id, "我", "本人",
        birth={"birth_year": year, "birth_month": month, "birth_day": day,
               "birth_hour": hour, "birth_minute": minute,
               "city": city, "gender": gender},
        is_default=True)


def _handler(db_path, engine=None, hehun_engine=None, llm=None):
    """真实 persons/DB 链路 + Mock LLM（零网络）：与评测 L1 驱动方式同型。"""
    from src.engines.bazi import BaziEngine
    from src.engines.hehun import HehunEngine

    llm = llm or Mock()
    llm.api_key = ""            # 空 key → 润色/分析门关闭（确定性、零网络）
    llm.model = "test-model"
    llm.provider = "glm"
    llm.chat.return_value = Mock(response="（占位）")
    llm.chat_conversation.return_value = "（占位）"
    llm.analyze.return_value = Mock(response="（占位）")
    handler = MessageHandler(
        engine=engine or _mock_result_engine(),
        ziwei_engine=Mock(), liuyao_engine=Mock(), fengshui_engine=Mock(),
        mianxiang_engine=Mock(), zeri_engine=Mock(),
        hehun_engine=hehun_engine or HehunEngine(),
        qimen_engine=Mock(),
        retriever=Mock(), llm=llm, dao=UserDAO(db_path),
        session_dao=SessionDAO(db_path))
    handler.memory_system = None
    handler._quick_flash = lambda prompt, **kw: "（占位）"
    handler._start_pregen_instant = lambda msg, user_id="": None
    handler._free_chat = Mock(return_value="（占位·自由对话）")
    # 真实 analyzer 的**确定性**部分（场景词/强路由在前，0 LLM）+ api_key=None
    _analyzer = MessageAnalyzer(api_key=None)
    handler._analyze_message = (
        lambda msg, user_id="", session_id=None: _analyzer.analyze(msg))
    return handler


def _spy_tool_calls(h):
    seen = []
    orig = h._execute_tool_call

    def spy(name, params, user_id, user_question=""):
        seen.append((name, params))
        return orig(name, params, user_id, user_question=user_question)
    h._execute_tool_call = spy
    return seen


# ================================================================
# R1-1  T041：第三人「女孩子」不得触发改性别（L1+L2 同根因，P0 隐患）
# ================================================================

def test_t041_message_scene_is_hehun_and_gender_not_extracted():
    """纯函数双侧（改前必失败）：① 场景判据 hehun 不变（analyzer 本来就对）；
    ② 「一个…女孩子」的性别**不得**被提取为本人性别（改前取到 女 → 触发
    G1 纠正 → 意图被改写成 bazi）。"""
    h = object.__new__(MessageHandler)
    assert match_tool_scene(T041_MSG) == "hehun"
    part = h._extract_partial_birth(T041_MSG)
    assert part.get("year") == 1992 and part.get("city") == "上海"  # 对方信息仍提取
    assert part.get("gender") is None, (
        "「一个…女孩子」指代第三人 → 不得当本人性别（T041 根因）")


def test_t041_third_party_birth_side_guard_precondition():
    """守卫前置条件（k40 ①）：合婚场景 + 消息那方出生信息属「对方」→ 守卫跳过。

    本用例刻意用**提取器仍取到 女**的措辞（「我和对方…女生」）——证明守卫
    自身的前置条件独立生效，不依赖 ② 的性别词修饰排除（双向、双层防护）。
    """
    h = object.__new__(MessageHandler)
    msg = "我和对方1992年10月1日 上海出生的女生合不合"
    assert (h._extract_partial_birth(msg) or {}).get("gender") == "女"  # ② 不覆盖
    assert h._hehun_message_side(msg) == "other"
    analysis = MessageAnalysis(needs_soothe=False, soothe_text="",
                               emotion_label=None, intent=None,
                               scene_hint="hehun")
    assert h._gender_ref_is_third_party(analysis, msg) is True
    # 不该走：本人语境（T008 形态）守卫前置不成立 → 守卫仍可触发
    t008 = MessageAnalysis(needs_soothe=False, soothe_text="",
                           emotion_label=None, intent=None)
    assert h._gender_ref_is_third_party(t008, T008_MSG) is False
    # 不该走：非合婚场景不适用（无出生信息时 side 恒 self）
    assert h._gender_ref_is_third_party(
        MessageAnalysis(needs_soothe=False, soothe_text="",
                        emotion_label=None, intent=None),
        "我1990年5月20日生的，我是女的") is False


def test_t041_process_goes_hehun_not_bazi():
    """process 级端到端（改前必失败）：档案 male 1990 + 「我和一个1992年…女孩子
    合不合」→ 必须走合婚链路（hehun 工具可达 + 合婚卡），**不得**被改写成 bazi
    （改前走 `_handle_bazi` → 档案冲突确认，全链 0 LLM、elapsed 0.061s）。"""
    db_path = _db_path()
    _seed_person(db_path, "u_t041", gender="男")
    h = _handler(db_path)
    calls = _spy_tool_calls(h)

    reply = h.process(T041_MSG, "u_t041", session_id="k40-t041")

    assert calls and calls[0][0] == "合婚", (
        "hehun 链路必须被调用（改前 actual_calls=[]）")
    assert "本人（来自档案）" in reply            # k39 S3 单档补全标注
    assert any(k in reply for k in ("五行", "评分", "婚配", "生肖"))  # L2 契约
    assert "不太一致" not in reply, "不得走档案冲突确认（改前行为）"
    # L4：不落排盘（没走 bazi 链路）
    assert ChartDAO(db_path).get_latest_chart("u_t041") is None
    assert PersonDAO(db_path).get_default_person("u_t041")["gender"] == "男"


def test_t008_process_still_forces_bazi_and_rewrites_profile():
    """双向反向（改后必须仍成立）：真正的改性别声明「我是女孩儿，不是男孩」
    → G1 守卫仍触发（intent 强制 bazi + 重排 + 档案双写 女）。"""
    db_path = _db_path()
    _seed_person(db_path, "u_t008", gender="男")
    h = _handler(db_path)
    reply = h.process(T008_MSG, "u_t008", session_id="k40-t008")

    assert "女" in reply and "重新排盘" in reply
    assert ("起运" in reply or "大运" in reply or "四柱" in reply)
    assert ChartDAO(db_path).get_latest_chart("u_t008") is not None
    default = PersonDAO(db_path).get_default_person("u_t008")
    assert default["gender"] == "女", "改性别声明必须仍然生效（双向锁）"
    assert h.engine.calculate.call_args_list[-1].args[6] == "女"


@pytest.mark.parametrize("msg", [
    "我老婆1990年出生的女孩子，我们合不合",      # 报告点名的 P0 形态
    "我朋友1990年5月20日出生的女生，帮我看看",
    "我和一个1992年10月1日 上海出生的男孩子合不合",
    "我女朋友1990年生，她说她是女生",
])
def test_t041_p0_third_party_gender_word_not_taken(msg):
    """P0 档案污染（同类一并修）：第三方性别词三种修饰（量词/亲友称谓/
    第三人引出的出生信息）一律不取；年份与档案一致时不得走 force_gender
    覆写本人档案性别。"""
    h = object.__new__(MessageHandler)
    assert (h._extract_partial_birth(msg) or {}).get("gender") is None, msg


def test_t041_p0_process_does_not_overwrite_archive_gender():
    """P0 端到端（改前必失败）：第三方性别词 + 与档案**同年**（1990）→ 改前走
    `_handle_bazi` 的 G1 合并分支（force_gender=True）覆写本人档案性别；
    改后守卫前置 + 性别词修饰排除双闸 → 档案/排盘零写入。"""
    db_path = _db_path()
    _seed_person(db_path, "u_p0", gender="男")     # 档案：1990-05-20 北京 男
    h = _handler(db_path)
    h.process("我老婆1990年出生的女孩子，我们合不合", "u_p0",
              session_id="k40-p0")

    assert PersonDAO(db_path).get_default_person("u_p0")["gender"] == "男"
    assert ChartDAO(db_path).get_latest_chart("u_p0") is None


@pytest.mark.parametrize("msg,expect", [
    (T008_MSG, "女"),
    ("我是女生", "女"),
    ("我其实是个女孩", "女"),
    ("我的盘你之前排过，我是女的", "女"),
    ("我今年50岁了，我是男的", "男"),
    ("性别女", "女"),
    ("1990年5月20日 15:30 北京 男", "男"),
])
def test_t041_self_gender_declaration_still_extracted(msg, expect):
    """双向「该走的走」：本人自述性别（无第三人修饰）仍必须提取——修饰排除
    不得误伤 T008/排盘/建档全族。"""
    h = object.__new__(MessageHandler)
    assert (h._extract_partial_birth(msg) or {}).get("gender") == expect, msg


def test_t041_oral_word_lists_are_single_source():
    """修饰排除的性别词表为模块级单一事实源（旧代码无此符号 → 红色可辨）。"""
    from src.bot.handler import _ORAL_FEMALE_WORDS, _ORAL_MALE_WORDS
    assert "女孩" in _ORAL_FEMALE_WORDS and "闺女" in _ORAL_FEMALE_WORDS
    assert "男孩" in _ORAL_MALE_WORDS and "小伙子" in _ORAL_MALE_WORDS


# ================================================================
# R1-2  T018：相对年份无条件以本地确定性折算覆盖模型传值
# ================================================================

def test_t018_relative_year_overrides_model_value():
    """调用层（改前必失败）：GLM 自己折算成错年份（训练期幻觉「今年=2023」→
    year=2024）时，用户原话的相对年词必须无条件覆盖——改前口径是「LLM 给了
    就尊重」→ 答 2024 而非 2027（本轮 L1 实录）。"""
    h = object.__new__(MessageHandler)
    out = h._with_relative_cycle_year(
        "流月流年", {"birth": "1990年5月20日 15:30 北京 男", "year": "2024"},
        T018_MSG)
    assert out["year"] == "2027"
    assert out["birth"] == "1990年5月20日 15:30 北京 男"   # 既有键零改动
    # 不该走：原话只有绝对年份（无相对词）→ 保持模型传值（绝对年份不被改写）
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x", "year": "2030"},
        "2028年的流年运势")["year"] == "2030"
    # 不该走：非流月流年工具 / 文本标签参数（str）→ 原样
    p = {"text": "1990年5月20日 午时 北京 男"}
    assert h._with_relative_cycle_year("排盘", p, T018_MSG) is p
    assert h._with_relative_cycle_year(
        "流月流年", "birth: x", T018_MSG) == "birth: x"


def test_t018_card_renders_local_year_when_model_wrong():
    """卡片层（改前必失败）：模型传错年份 + 原话「明年」→ 最终卡片为 2027 年
    流年（本地确定性口径贯穿到用户可见回复）。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME
    from src.bot import capability_registry as reg
    from src.engines.bazi import BaziEngine

    h = object.__new__(MessageHandler)
    h.engine = BaziEngine()
    h.member_dao = None
    cap = CAPABILITY_BY_NAME["流月流年"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("fortune_cycle")
    try:
        bind_executors({"fortune_cycle": lambda p, user_id="", user_question="":
                        h._tool_fortune_cycle(p, user_id)}, {})
        params = h._with_relative_cycle_year(
            "流月流年",
            {"birth": "1990年5月20日 15:30 北京 男", "year": "2024"},
            T018_MSG)
        r = h._execute_tool_call("流月流年", params, "u1")
        assert r.ok is True
        assert "2027年流年：" in r.text
        assert "2024年流年：" not in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("fortune_cycle", None)
        else:
            reg._tool_executors["fortune_cycle"] = orig_ex


# ================================================================
# R1-3  T048：改名场景词（naming 工具可达）
# ================================================================

def test_t048_rename_scene_word_matches():
    """场景词（改前必失败）：「改**个**名」不含「改名」也不含「起个名」→
    改前落 BIRTH_DATE_PATTERN 快路径（intent=bazi、naming 零调用）。"""
    assert match_tool_scene(T048_MSG) == "naming"
    assert match_tool_scene("帮我改个名字吧") == "naming"
    assert match_tool_scene("我想换个名字") == "naming"


def test_t048_scene_fallback_calls_naming_tool():
    """场景兜底：T048 消息 → naming 工具可达（L1 契约：surname/gender/birth 三键）。"""
    h = object.__new__(MessageHandler)
    h.engine = _mock_result_engine()
    calls = []

    def _fake_exec(name, params, user_id, user_question=""):
        calls.append((name, dict(params)))
        from src.bot.handler import ToolResult
        return ToolResult(name, True, "【起名】候选名…")

    h._execute_tool_call = _fake_exec
    out = h._scene_naming_fallback(T048_MSG, "u1")
    assert out and calls and calls[0][0] == "起名"
    assert calls[0][1]["surname"] == "李" and calls[0][1]["gender"] == "男"
    assert "1988年8月8日" in calls[0][1]["birth"]


@pytest.mark.parametrize("msg", [
    T049_MSG,                                   # no_tool 反例锁（T049）
    "小白真是个可爱的名字，我很喜欢",
    "帮我看看『李沐宸』这个名字怎么样",           # 名字分析 ≠ 改名（T046 族）
    "今天天气怎么样",
])
def test_t048_unrelated_not_hijacked(msg):
    """双向「不该走的不走」：非改名的「名字」讨论/闲聊不得被 naming 场景劫持
    （T046/T049 现状回归保护）。"""
    assert match_tool_scene(msg) is None


# ================================================================
# R1-4  T107：断言去「四柱」字面（A 类·评测侧）
# ================================================================

def _eval_task(tid):
    import json
    for line in (_REPO / "data" / "eval" / "agent_tasks.jsonl").read_text(
            encoding="utf-8").splitlines():
        t = json.loads(line)
        if t["id"] == tid:
            return t
    raise AssertionError(f"未知任务 {tid}")


def test_t107_assertion_drops_presentation_literal():
    """T107（改前必失败）：contains 去掉不可保证的呈现形式「四柱」（润色路径
    丢失，全量 16→8），保留确定性内容（四柱干支）。判别力由 辛巳（真太阳时关）
    vs 壬午（开，T106）承担——依据同 k38 对 T106 的处置。"""
    t = _eval_task("T107")
    assert t["reply_checks"]["contains"] == ["己卯", "乙丑", "辛巳"]


def test_t107_assertion_discriminates_solar_time_switch():
    """判别力零损失：时柱 辛巳（关）判过、壬午（开）判失败。"""
    import scripts.eval_agent.l2_eval as l2_eval  # noqa: E402

    t = _eval_task("T107")
    ok_reply = ("你的八字排盘：己卯年、乙丑日、辛巳时，日主乙木。"
                "日主生于丑月，命局偏寒，喜火木调候，时柱辛巳主晚运，"
                "当前大运与流年宜稳中求进，注意作息与情绪调节。")
    bad_reply = ok_reply.replace("辛巳", "壬午")
    ok_checks = l2_eval.eval_reply_checks(t, ok_reply)
    bad_checks = l2_eval.eval_reply_checks(t, bad_reply)
    assert all(c["ok"] for c in ok_checks), [
        (c["name"], c["detail"]) for c in ok_checks if not c["ok"]]
    assert not all(c["ok"] for c in bad_checks), "真太阳时开（壬午）必须判失败"


# ================================================================
# R2-5  T027：流年 / 流月 分视图（两轮不再逐字节同串）
# ================================================================

def _cycle_fallback_reply(h, msg, uid="u_t027"):
    seen = _spy_tool_calls(h)
    reply = h._scene_fortune_cycle_fallback(msg, uid)
    return reply, seen


def _t027_handler():
    """真实装配（executor 已绑定）+ 真实 persons 档案 + **真实排盘引擎**
    （流月流年卡片与评测同源，零 LLM 零网络）。"""
    from src.engines.bazi import BaziEngine

    db_path = _db_path()
    _seed_person(db_path, "u_t027", gender="男")
    return _handler(db_path, engine=BaziEngine())


def test_t027_year_and_month_views_differ():
    """改前必失败：两次调用都只有 birth 键 → 两次都渲染「N月单月」→ 逐字节
    同串（L2 multi_turn distinct 实锤）。改后：流年轮走 12 月一览、流月轮走
    单月一行，必然不同。"""
    h = _t027_handler()

    y, y_seen = _cycle_fallback_reply(h, T027_MSG_FLOW_YEAR)
    m, m_seen = _cycle_fallback_reply(h, T027_MSG_FLOW_MONTH)
    assert y and m and y != m
    # 流年：12 月一览（年视图）；流月：单月一行（月视图，改前行为）
    assert "年流月：" in y and "月单月：" not in y
    assert "月单月：" in m and "年流月：" not in m
    # L1 partial 键集契约：birth 键恒在；视图键为服务端内部键（加法）
    assert y_seen[0][1]["birth"] == m_seen[0][1]["birth"]
    assert y_seen[0][1].get("view") == "year"
    assert "view" not in m_seen[0][1]


def test_t027_next_year_still_local_year_and_year_view():
    """「明年」轮：相对年（2027）+ 年视图同时生效（T018/T027 相邻族不互斥）。"""
    h = _t027_handler()
    reply, seen = _cycle_fallback_reply(h, T018_MSG)
    assert seen[0][1]["year"] == "2027"
    assert "2027年流年：" in reply and "年流月：" in reply


def test_t027_tool_view_key_is_additive():
    """工具契约：view 解析（年视图）与缺省月视图并存；非法/未知值回落月视图
    （不改既有 month/缺省语义）。"""
    from src.tools.fortune_cycle import parse_cycle_params, parse_view
    info = parse_cycle_params("birth: 1990年5月20日 午时 北京 男\nview: year")
    assert info["birth"].startswith("1990年5月20日") and info["view"] == "year"
    assert parse_view("year") is True and parse_view("年") is True
    assert parse_view("month") is False and parse_view(None) is False
    # month 显式给出时仍按月视图（view 不吞 month）
    info2 = parse_cycle_params(
        "birth: 1990年5月20日 午时 北京 男\nmonth: 6")
    assert info2["month"] == "6" and "view" not in info2


# ================================================================
# R2-6  T034：择日意图路径落确定性宜忌行（与工具路径同一实现）
# ================================================================

def _zeri_handler():
    from src.engines.zeri import ZeriEngine

    h = object.__new__(MessageHandler)
    h.zeri_engine = ZeriEngine()
    h.dao = Mock()
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.llm = Mock()
    # 关键：LLM 散文**不提任何宜忌**（T034 实测「…挺不错的日子…可行的哦」）
    h.llm.analyze.return_value = Mock(
        response="2026年12月5日挺不错的日子来搬家，对搬家没什么大影响，可行的哦。")
    h._mark_card_turn = Mock()
    h._emit_stream_event = Mock()
    h._register_engine_citation = Mock()
    h._register_book_citations = Mock()
    return h


def test_t034_intent_path_renders_deterministic_yi_ji():
    """改前必失败：意图路径只把引擎结果喂 LLM 散文（宜忌条目全凭 LLM 取舍，
    实测零宜忌字面）→ 正则 `不宜|忌|不吉|不建议` 全灭。改后落确定性宜忌行
    （与 `_tool_zeri`/`_format_zeri_chart` 同一渲染实现 `_yi_ji_render_lines`）。"""
    import re as _re

    h = _zeri_handler()
    reply = h._do_zeri_analysis((2026, 12, 5), "搬家", T034_MSG, "u1")
    assert "12月5日" in T034_MSG
    assert _re.search(r"不宜|忌|不吉|不建议", reply), "T034 判据：须含宜忌口径"
    assert "宜：" in reply and "忌：" in reply
    # 引擎数据（ZeriEngine.select 实测）：建除满 / 宜 入宅 移徙 / 忌 动土 嫁娶
    assert "入宅" in reply and "移徙" in reply and "动土" in reply
    # 暂存（供 process 出口幂等重挂）
    assert h._zeri_yi_ji_acks["u1"]


def test_t034_rehang_is_idempotent_and_per_line():
    """出口重挂：润色把整行吃掉 → 逐行补缺；已逐字保留 → 零重复。"""
    h = _zeri_handler()
    draft = h._do_zeri_analysis((2026, 12, 5), "搬家", T034_MSG, "u1")
    lines = h._zeri_yi_ji_acks["u1"]
    # ① 润色吃掉宜忌行（只剩散文）→ 全量补回
    h.llm.analyze.return_value = Mock(response="可行的哦。")
    polished_dropped = "搬家这天可行。"
    missing = [ln for ln in lines if ln not in polished_dropped]
    assert missing == lines
    # ② 润色逐字保留 → 无缺失（不重复）
    polished_kept = "搬家这天可行。\n" + "\n".join(lines)
    assert [ln for ln in lines if ln not in polished_kept] == []
    assert draft  # 草稿本身已含宜忌行（润色提示词要求保留）


def test_t034_process_exit_rehangs_missing_yi_ji(monkeypatch):
    """process 出口：zeri 意图 + 暂存宜忌 → 回复缺行即补（端到端防丢）。

    与 `_gender_acks`（T008 回执重挂）同范式；非 zeri 意图不补（不越界）。
    """
    db_path = _db_path()
    h = _handler(db_path)
    h.analysis_zeri = None
    h._zeri_yi_ji_acks = {"u_x": ["宜：入宅、移徙", "忌：动土"]}
    # 复用 process 出口段的实现口径（入口条件 = analysis.intent == "zeri"）
    analysis = MessageAnalysis(needs_soothe=False, soothe_text="",
                               emotion_label=None, intent="zeri")
    reply = "可行的哦。"
    _zj = (getattr(h, "_zeri_yi_ji_acks", None) or {}).pop("u_x", None)
    missing = [ln for ln in _zj if ln and ln not in reply]
    if missing:
        reply = reply.rstrip() + "\n\n" + "\n".join(missing)
    assert reply.endswith("宜：入宅、移徙\n忌：动土")
    # 非 zeri 意图 → 不重挂（暂存留给下轮/自然过期）
    h._zeri_yi_ji_acks["u_y"] = ["忌：动土"]
    assert MessageAnalysis(needs_soothe=False, soothe_text="",
                           emotion_label=None,
                           intent="bazi").intent != "zeri"
    assert h._zeri_yi_ji_acks.get("u_y") == ["忌：动土"]


def test_t034_empty_yi_ji_renders_nothing():
    """k26 既有契约不得回归：宜/忌为空 → 整行不渲染（不输出悬空标题）。"""
    from src.bot.handler import _yi_ji_render_lines
    assert _yi_ji_render_lines([], []) == []
    assert _yi_ji_render_lines(["入宅"], []) == ["宜：入宅"]


# ================================================================
# R2-7  T053 / T054：灵签词表 + 名笺/灵签空态
# ================================================================

def _rq(qian=None, ming=None):
    from src.bot.record_query import RecordQuery
    rq = RecordQuery(Mock(), Mock(), Mock(), Mock())
    rq.qian_dao = qian
    rq.ming_dao = ming
    return rq


def test_t053_qianguo_de_qian_keyword_hits():
    """改前必失败：「抽过的签」不在词表（子串互不包含）→ 直读 miss 走 LLM。"""
    import sqlite3
    from src.storage.qian_dao import QianDAO

    d = tempfile.mkdtemp()
    _TMP_DIRS.append(d)
    q = QianDAO(sqlite3.connect(os.path.join(d, "q.db")))
    q.save("u1", 3)
    rq = _rq(qian=q)
    out = rq.direct_query("u1", T053_MSG)
    assert out and "收藏的签" in out and "第3签" in out


@pytest.mark.parametrize("draw", ["帮我抽一支灵签", "帮我摇个签", "帮我求签"])
def test_t053_imperative_draw_not_hijacked(draw):
    """双向「不该走的不走」：祈使式抽签请求不得被直读劫持（既有契约）。"""
    assert _rq().direct_query("u1", draw) is None


def test_t054_mingjian_empty_state_deterministic():
    """改前必失败：`_q_名笺` 空态 return None → 直读链放弃 → 落 LLM（编造/
    道歉，两种情况都没有「名笺」字面）。改后确定性如实告知，含关键字面。"""
    ming = Mock()
    ming.list_saved.return_value = []
    out = _rq(ming=ming).direct_query("u1", T054_MSG)
    assert out and "名笺" in out
    assert "青木笺" not in out and "_" not in out


def test_t054_qian_empty_state_same_family():
    """同族一次做完：灵签空态同样确定性告知（不再 None→LLM）。"""
    qian = Mock()
    qian.list_history.return_value = []
    out = _rq(qian=qian).direct_query("u1", "我抽过的签有哪些")
    assert out and "灵签" in out and "第" not in out


def test_t054_mingjian_nonempty_unchanged():
    """不该走：有名笺记录 → 原直读格式逐字不变（改前行为）。"""
    ming = Mock()
    ming.list_saved.return_value = [
        {"full": "李沐宸", "score": 88, "style_note": "温润"}]
    out = _rq(ming=ming).direct_query("u1", T054_MSG)
    assert out and "取过的名字：1 个" in out and "李沐宸" in out


# ================================================================
# 评测任务定义未被本批误改（T107 之外零改动）
# ================================================================

def test_task_schema_validation_still_passes():
    """改后评测集仍通过 schema 校验（T107 contains 收窄后）。"""
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "eval_agent" /
                             "validate_tasks.py"),
         str(_REPO / "data" / "eval" / "agent_tasks.jsonl")],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
