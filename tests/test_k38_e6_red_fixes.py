# -*- coding: utf-8 -*-
"""k38：E6 门禁红灯修复批（产品侧）——每组都带「该走的走 / 不该走的仍不走」双向用例。

覆盖（需求 SSOT = .superpowers/sdd/e6-red-triage-20260912.md「修复动作」列）：
- T018 相对年份折算（明年 → year=2027；唯一真回归）
- T029/T033/T034/T035/T036/T037 择日强路由扩词 + 放开无日期锚（6 条同根）
- T104 实体 QA 优先于场景词（career_dir「换工作」劫持）
- T046 姓名分析确定性强路由（≠ 起名/改名 naming 工具链）
- T056 签文释义直读（签库单一事实源，0 LLM 0 编造）
- T076 单人婚姻询问 → advisor（双人合盘仍走 hehun）

双向纪律：每条「该走」用例旁必有「不该走」用例（防误伤是主要风险）。
改回旧代码必失败（强路由断言 intent/scene_hint + LLM 调用次数 0）。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.engines import message_analyzer as ma  # noqa: E402
from src.engines.message_analyzer import MessageAnalyzer  # noqa: E402
from src.tools import fortune_cycle as cycle  # noqa: E402

# k38 评测实锤的 6 条择日请求（逐字取自 data/eval/agent_tasks.jsonl）
ZERI_TASKS = [
    ("T029", "2026年9月20日开业 帮我挑个时间"),
    ("T033", "下个月搬家 帮我选个日子"),
    ("T034", "2026年12月5日搬家，这个日子行不行"),
    ("T035", "2026年10月搬家，帮我挑几个好日子"),
    ("T036", "2026年10月5日提车，帮我看看这个日子"),
    ("T037", "2026年11月1日升职庆功，帮我选个好日子"),
]


@pytest.fixture
def analyzer():
    return MessageAnalyzer(api_key="test-key", model="test-model")


def _mock_completion(intent, monkeypatch):
    """mock LLM 意图分类（返回指定 intent），并计数调用次数（防误走 LLM）。"""
    import json

    import src.llm.client as llm_client_mod
    import src.engines.message_analyzer as analyzer_mod

    calls = {"n": 0}
    payload = {
        "needs_soothe": False, "soothe_text": "",
        "emotion": "neutral", "intent": intent, "is_sharing": False,
    }

    def fake(api_key, messages, **kw):
        calls["n"] += 1
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(llm_client_mod, "deepseek_anthropic_completion", fake)
    monkeypatch.setattr(analyzer_mod, "deepseek_anthropic_completion", fake,
                        raising=False)
    return calls


def _bare_handler():
    """无装配 MessageHandler（object.__new__，只测纯规则方法）。"""
    from src.bot.handler import MessageHandler
    return object.__new__(MessageHandler)


# ================================================================
# 一、T018 相对年份折算（唯一真回归）
# ================================================================

def test_t018_relative_year_from_text_all_forms():
    """相对年份词 → 四位年份：明年=+1 / 后年=+2 / 去年=-1 / 前年=-2 / 今年=当年。"""
    now = datetime(2026, 9, 12)
    assert cycle.relative_year_from_text("帮我看看明年的流年运势", now) == 2027
    assert cycle.relative_year_from_text("后年运势", now) == 2028
    assert cycle.relative_year_from_text("大后年运势", now) == 2029
    assert cycle.relative_year_from_text("去年的流年", now) == 2025
    assert cycle.relative_year_from_text("前年运势", now) == 2024
    assert cycle.relative_year_from_text("今年的流年运势", now) == 2026
    # 不该走：无相对词 → None（不猜年份，保持缺省=今年语义）
    assert cycle.relative_year_from_text("我的流年运势怎么样", now) is None
    assert cycle.relative_year_from_text("", now) is None
    assert cycle.relative_year_from_text(None, now) is None


def test_t018_parse_target_year_accepts_relative_words():
    """参数层：year 写成相对词不再误报「目标年份没看懂」；四位整数口径不变。"""
    now = datetime(2026, 9, 12)
    assert cycle.parse_target_year("明年", now) == 2027
    assert cycle.parse_target_year("今年", now) == 2026
    # 既有四位年份口径零回归
    assert cycle.parse_target_year("2027") == 2027
    assert cycle.parse_target_year(1990) == 1990
    assert cycle.parse_target_year("1899") is None
    assert cycle.parse_target_year("abc") is None
    # 不该走：无相对词的垃圾串仍点名 year（不静默纠正）
    assert cycle.parse_target_year("随便哪年") is None


def test_t018_tool_loop_injects_relative_year_into_params():
    """调用层：LLM 只传 birth（缺 year 键）时，从用户原话折算并补进调用参数
    ——L1 partial=键集契约（缺 year 键即 FAIL）与用户可见回复（答 2026 而非
    2027）双修。"""
    h = _bare_handler()
    out = h._with_relative_cycle_year(
        "流月流年", {"birth": "1990年5月20日 15:30 北京 男"},
        "帮我看看明年的流年运势")
    assert out["year"] == "2027"
    assert out["birth"] == "1990年5月20日 15:30 北京 男"  # 既有键零改动
    # 不该走 ①：LLM 自己给了 year → 尊重，不覆盖
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x", "year": "2030"}, "明年的流年")["year"] == "2030"
    # 不该走 ②：非流月流年工具 → 原样（其余工具无 year 语义）
    p = {"text": "1990年5月20日 午时 北京 男"}
    assert h._with_relative_cycle_year("排盘", p, "明年的流年") is p
    # 不该走 ③：消息无相对年份词 → 原样（不猜）
    assert h._with_relative_cycle_year("流月流年", {"birth": "x"},
                                        "我的流年运势") == {"birth": "x"}
    # 不该走 ④：文本标签参数（str）→ 原样（结构化键注入会吞掉出生信息）
    assert h._with_relative_cycle_year("流月流年", "birth: x", "明年的流年") == "birth: x"


def test_t018_cycle_card_renders_target_year():
    """卡片层：目标年 2027 → 卡片首行「2027年流年」（用户可见口径）。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME
    from src.bot import capability_registry as reg
    h = _bare_handler()
    from src.engines.bazi import BaziEngine
    h.engine = BaziEngine()
    h.member_dao = None
    cap = CAPABILITY_BY_NAME["流月流年"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("fortune_cycle")
    try:
        bind_executors({"fortune_cycle": lambda p, user_id="", user_question="":
                        h._tool_fortune_cycle(p, user_id)}, {})
        r = h._execute_tool_call(
            "流月流年",
            {"birth": "1990年5月20日 15:30 北京 男", "year": "2027"}, "u1")
        assert r.ok is True
        assert "2027年流年：" in r.text      # 目标年上卡（T018 用户可见判据）
        assert "2026年流年：" not in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("fortune_cycle", None)
        else:
            reg._tool_executors["fortune_cycle"] = orig_ex


def test_t018_scene_fallback_path_uses_same_source():
    """场景兜底路径（档案 → 工具）同样折算：T018 实测调用形态即
    {"birth": …} 无 year 键（同一事实源，不另立词表）。"""
    h = _bare_handler()
    seen = {}

    class _FakeEngine:
        def calculate(self, *a, **kw):
            raise AssertionError("不应真排盘")

    h.engine = _FakeEngine()
    h.member_dao = None
    h._get_user_birth_profile = lambda uid: {
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 30,
        "city": "北京", "gender": "male"}

    def _fake_exec(name, params, user_id, user_question=""):
        seen["name"] = name
        seen["params"] = params
        from src.bot.handler import ToolResult
        return ToolResult(name, True, "卡片")

    h._execute_tool_call = _fake_exec
    out = h._scene_fortune_cycle_fallback("帮我看看明年的流年运势", "u1")
    assert out == "卡片"
    assert seen["name"] == "流月流年"
    assert seen["params"]["year"] == "2027"


# ================================================================
# 二、择日强路由扩词 + 放开无日期锚（T029/T033/T034/T035/T036/T037）
# ================================================================

@pytest.mark.parametrize("tid,text", ZERI_TASKS)
def test_zeri_six_tasks_force_route_to_zeri(analyzer, monkeypatch, tid, text):
    """6 条择日请求 0 LLM 确定性强路由 intent=zeri（改前落 LLM 分类 → calendar
    →「请先设置八字」建档引导兜底，L1 零调用 + L2 关键日期/宜忌全灭）。"""
    calls = _mock_completion("calendar", monkeypatch)  # 旧链会拿到 calendar
    result = analyzer.analyze(text)
    assert result.intent == "zeri", tid
    assert result.scene_hint is None
    assert calls["n"] == 0, f"{tid}: 强路由不得走 LLM"


def test_zeri_green_tasks_still_route(analyzer, monkeypatch):
    """不该走（回归保护）：现有 3 条绿的择日任务仍强路由 zeri。"""
    calls = _mock_completion("calendar", monkeypatch)
    for text in ("下个月结婚 帮我选个吉日",           # T030
                 "这周末想出门旅游，帮我选个出行吉日",  # T031
                 "下个月开工，帮我选个开业吉日",       # T032
                 "2026年9月15日搬家 帮我选个日子"):    # T028/T100
        assert analyzer.analyze(text).intent == "zeri", text
    assert calls["n"] == 0


@pytest.mark.parametrize("text", [
    "什么时候才能过上好日子",          # 「好日子」但无场景词无日期锚 → 不劫持
    "2026年5月20日他对我好不好",      # 泛问词 + 日期锚但无场景词 → 不劫持
    "下个月结婚好不好",               # 泛问词 + 场景词但无强意图词 → 不劫持
    "我什么时候能结婚",               # 只有场景词 → 不劫持
    "帮我看看今天的运势",             # 无关问句
    "你好呀，今天心情不错",           # 无关寒暄
])
def test_zeri_route_does_not_hijack_unrelated(analyzer, monkeypatch, text):
    """不该走：泛问词/场景词单用或无关问句一律不落择日（新码不得扩大触发面）。"""
    _mock_completion("free_chat", monkeypatch)
    assert analyzer.analyze(text).intent != "zeri", text


def test_zeri_unrelated_questions_still_go_to_llm(analyzer, monkeypatch):
    """不该走（补充）：未命中任何确定性强路由的普通问句仍走 LLM 分类
    （强路由不是「见词就拦」）。"""
    calls = _mock_completion("free_chat", monkeypatch)
    assert analyzer.analyze("你好呀，今天心情不错").intent is None
    assert calls["n"] == 1


def test_zeri_no_date_anchor_returns_deterministic_guide():
    """T033 目标行为：无日期锚的择日请求 → `_handle_zeri` 无日期分支确定性
    返回「请告诉我您想查询的日期和用途」（0 LLM 0 引擎）。"""
    h = _bare_handler()
    h.member_dao = None
    reply = h._handle_zeri("下个月搬家 帮我选个日子", "u1")
    assert "请告诉我您想查询的日期和用途" in reply


def test_t035_month_scope_multi_day_recommendation():
    """T035：明确年月锚（2026年10月）+ 场景 + 多日诉求 → 引擎 Top3 多日吉日卡
    （0 LLM，0 工具调用；改前意图路径缺此分支 → 落建档引导/日期引导文案）。

    与 T033 的分工锁：只有相对窗口词（下个月）时仍走确定性日期引导文案，
    不硬跑引擎（D5 负例契约）。"""
    import re as _re
    from src.engines.zeri import ZeriEngine
    h = _bare_handler()
    h.zeri_engine = ZeriEngine()
    h.member_dao = None
    h._card_turn = {}
    out = h._handle_zeri("2026年10月搬家，帮我挑几个好日子", "u1")
    assert "10月" in out, out[:120]
    assert _re.search(r"宜|吉日", out)
    assert "请告诉我您想查询的日期和用途" not in out
    # 多日推荐（≥2 个候选日期，judge_hint：不得只给单日）
    assert len(_re.findall(r"\d+月\d+日", out)) >= 2
    # 不该走：相对窗口词无年月锚 → 仍是确定性日期引导（T033 回归保护）
    guide = h._handle_zeri("下个月搬家 帮我选个日子", "u1")
    assert "请告诉我您想查询的日期和用途" in guide
    # 不该走：完整日期仍走单日分析（不受月范围分支截走）
    assert h._YEAR_MONTH_ANCHOR_RE.search("2026年10月5日提车") is None


def test_zeri_paipan_requests_still_fast_path_bazi(analyzer, monkeypatch):
    """不该走（回归保护）：含日期 + 排盘类词仍 0 LLM 判 bazi，择日扩词不误伤。"""
    calls = _mock_completion("free_chat", monkeypatch)
    for msg in ("1990年5月20日 男 排盘", "1990年5月20日 男 八字",
                "1990年5月20日 下午3点 北京 男"):
        assert analyzer.analyze(msg).intent == "bazi", msg
    assert calls["n"] == 0


# ================================================================
# 三、T104 实体 QA 优先于场景词（career_dir「换工作」劫持）
# ================================================================

T104_MSG = "易宝支付这家公司靠不靠谱？我正考虑换工作过去，我的盘你之前排过。"


def test_t104_entity_qa_bypasses_career_scene(analyzer, monkeypatch):
    """该走：命名实体 + 强实体问词（易宝支付 + 靠不靠谱）→ 不被 career_dir
    场景词（换工作）劫持成纯工具卡，落 LLM 意图 + needs_search 引导
    （与 T105 腾讯同路径：真实联网检索 + 来源痕迹）。"""
    assert ma.match_tool_scene(T104_MSG) == "career_dir"   # 场景词确实命中
    assert ma._entity_qa_beats_scene(T104_MSG, "career_dir") is True
    calls = _mock_completion("free_chat", monkeypatch)
    result = analyzer.analyze(T104_MSG)
    assert result.scene_hint is None      # 改前：scene_hint == "career_dir"
    assert calls["n"] == 1                # 走 LLM 意图（needs_search 通道）
    # 联网触发判定仍是真信号（实体层放行，与 handler._web_search_allowed 同源）
    from src.rag.search_trigger import decide_search
    dec = decide_search(T104_MSG)
    assert dec.should_search is True and dec.reason == "entity"
    assert dec.entity == "易宝支付"


def test_t104_pure_career_request_still_scene_routes(analyzer, monkeypatch):
    """不该走：真·择业请求（无实体 / 实体无问词）仍 0 LLM 直达 career_dir。"""
    calls = _mock_completion("free_chat", monkeypatch)
    # ① 纯择业意图：无命名实体
    r1 = analyzer.analyze("我想换工作，帮我看看适合做什么行业")
    assert r1.scene_hint == "career_dir" and r1.intent is None
    # ② 含实体但无实体问词（纯陈述/咨询，非实体 QA）→ 不夺路由
    r2 = analyzer.analyze("我打算换工作去腾讯")
    assert r2.scene_hint == "career_dir" and r2.intent is None
    assert calls["n"] == 0


def test_t104_guard_only_applies_to_career_dir():
    """不该走：守卫只对 career_dir 生效，其余场景词表零影响（防面扩大）。"""
    msg = "易宝支付这家公司靠不靠谱，帮我起个名"
    assert ma.match_tool_scene(msg) == "naming"
    assert ma._entity_qa_beats_scene(msg, "naming") is False
    assert ma._entity_qa_beats_scene(msg, "hehun") is False


# ================================================================
# 四、T046 姓名分析确定性强路由
# ================================================================

T046_MSG = "帮我看看『李沐宸』这个名字怎么样"


def test_t046_name_analysis_routes_xingming(analyzer, monkeypatch):
    """该走：姓名语境 + 评价问词 → 0 LLM 强路由 intent=xingming
    （改前 LLM 判 advisor → 建档引导死胡同，产品 _handle_xingming 够不着）。"""
    calls = _mock_completion("advisor", monkeypatch)
    result = analyzer.analyze(T046_MSG)
    assert result.intent == "xingming"
    assert calls["n"] == 0


def test_t046_handler_analyzes_quoted_name():
    """该走：`_handle_xingming` 从『』中抽名 → 引擎五格 + 卡片关键词齐全。"""
    h = _bare_handler()
    seen = {}

    class _FakeXm:
        def analyze(self, surname, given, gender):
            seen["name"] = (surname, given, gender)

            class _R:
                wuge = {"天格": 8, "人格": 16, "地格": 17, "外格": 9, "总格": 24}
                sancai = "金土"
                sancai_ji = "吉"
                stroke_counts = {"李": 7, "沐": 7, "宸": 10}
                wuxing = {"金": 1, "木": 1, "水": 1}
                overall = "配置中上"
                analysis = {"人格": {"数字": "16", "吉凶": "吉",
                                     "运势": "厚德载物", "详解": "数理吉祥"}}
            return _R()

    class _FakeLLM:
        def analyze(self, chart, refs, question):
            class _A:
                response = "五格三才分析：笔画与数理如下，五行偏木。"
            return _A()

    class _FakeRetriever:
        def search(self, *a, **kw):
            return []

    h.xingming_engine = _FakeXm()
    h.llm = _FakeLLM()
    h.retriever = _FakeRetriever()
    reply = h._handle_xingming(T046_MSG, "u1")
    assert seen["name"][:2] == ("李", "沐宸")     # 引号内姓名抽取正确
    assert "五格" in reply and "笔画" in reply     # 契约关键词齐备


@pytest.mark.parametrize("text", [
    "帮我看看名字笔画",        # 只有泛词「名字」+「看看」，无姓名槽位 → 不夺路由
    "姓名学是什么",            # 只有泛词 → 不夺路由
    "帮我改签机票",            # 「改签」不命中 改名（对照 T048 场景词表）
])
def test_t046_name_word_without_slot_not_hijacked(analyzer, monkeypatch, text):
    """不该走（防误伤实锤）：泛词「名字/姓名」+ 评价词但**没有姓名槽位**
    （引号姓名 or 「名字+评价词」相邻）→ 不路由 xingming。改前宽松口径会把
    「帮我看看名字笔画」路由到 `_handle_xingming`，姓名抽取退化成取「帮我」
    两个字当名字做五格分析（比自由问答更糟）。"""
    calls = _mock_completion("free_chat", monkeypatch)
    assert analyzer.analyze(text).intent != "xingming", text
    assert calls["n"] == 1


def test_t046_naming_requests_not_hijacked(analyzer, monkeypatch):
    """不该走：起名/改名动作族仍走 naming 工具场景链（要候选名，不是五格分析）。"""
    calls = _mock_completion("free_chat", monkeypatch)
    for text in ("给孩子起个名，姓张，男孩，2019年3月15日 午时 北京出生",  # T045
                 "帮我起个名字，姓王，女孩",                            # T047
                 "帮我起个名，姓刘，女孩，2020年6月1日 10:00 上海出生",  # T050
                 "宝宝叫个什么名字好"):
        r = analyzer.analyze(text)
        assert r.intent != "xingming", text
        assert r.scene_hint == "naming", text
    assert calls["n"] == 0


# ================================================================
# 五、T056 签文释义直读（签库单一事实源）
# ================================================================

def test_t056_guandi_sign3_from_library():
    """该走：关帝灵签第3签 → 签库（QIAN_KINDS["guandi"]）逐字原文直读，
    0 LLM 0 编造；回复含「签」且不含编造签诗（风雷益）/错号（第42签）。"""
    h = _bare_handler()
    out = h._answer_qian_meaning("关帝灵签第三签是什么意思")
    assert out is not None
    assert "签" in out and len(out) >= 10
    from src.api.qian import QIAN_KINDS
    card = next(c for c in QIAN_KINDS["guandi"] if c["no"] == 3)
    for line in card["poem"]:
        assert line in out            # 签诗逐字取自签库
    assert card["jx"] in out          # 等第取自签库
    assert "风雷益" not in out and "第42签" not in out


def test_t056_other_kinds_and_original():
    """该走：观音/玄武山/未写签种但写「灵签」→ 各自签种库（同号异 kind 不串）。"""
    h = _bare_handler()
    from src.api.qian import QIAN_KINDS
    out_g = h._answer_qian_meaning("观音灵签第七签讲什么")
    g_card = next(c for c in QIAN_KINDS["guanyin"] if c["no"] == 7)
    assert g_card["poem"][0] in out_g
    out_x = h._answer_qian_meaning("玄武山佛祖灵签第9签")
    x_card = next(c for c in QIAN_KINDS["xuanwushan"] if c["no"] == 9)
    assert x_card["poem"][0] in out_x
    out_o = h._answer_qian_meaning("灵签第三签是什么意思")
    o_card = next(c for c in QIAN_KINDS["original"] if c["no"] == 3)
    assert o_card["poem"][0] in out_o


@pytest.mark.parametrize("text", [
    "第三签是什么意思",              # 签种不明 → 不路由（宁可自由问答不猜签种）
    "关帝灵签第九百九十九签",        # 签号超出该签种库 → 不路由
    "帮我抽一支灵签",                # 祈使式抽签（非释义）→ 不路由
    "今天运势怎么样",                # 无关问句
    "关帝第3签",                     # 无「灵签/签文/解签/签诗/签意/签号」语境词
])
def test_t056_not_routed_cases(text):
    """不该走：签种/签号/语境任一不明确一律不直读（不猜=不给错签诗）。"""
    h = _bare_handler()
    assert h._answer_qian_meaning(text) is None, text


def test_t056_no_llm_dependency():
    """该走：直读不依赖 LLM/引擎（装配缺失也必回）——被移除 llm 属性仍工作。"""
    h = _bare_handler()
    h.llm = None
    out = h._answer_qian_meaning("关帝灵签第三签是什么意思")
    assert out and "签诗" in out


# ================================================================
# 六、T076 单人婚姻询问 → advisor（双人合盘仍 hehun）
# ================================================================

def test_t076_single_marriage_routes_advisor(analyzer, monkeypatch):
    """该走：单人婚姻询问 0 LLM 强路由 advisor（改前 LLM 判 hehun →
    「给我双方生辰」死胡同）。"""
    calls = _mock_completion("hehun", monkeypatch)
    result = analyzer.analyze("帮我看看我的婚姻状况")
    assert result.intent == "advisor"
    assert calls["n"] == 0


def test_t076_advisor_no_profile_asks_birth():
    """该走：advisor 无档案分支给出建档引导（T076 断言：出生 + 年月日/性别）。"""
    h = _bare_handler()
    h._get_user_birth_profile = lambda uid: None
    reply = h._handle_advisor("帮我看看我的婚姻状况", "u1")
    assert "出生" in reply
    import re
    assert re.search(r"年月日|时辰|性别", reply)
    assert len(reply) >= 15


def test_t076_couple_match_still_hehun(analyzer, monkeypatch):
    """不该走（回归保护）：双人合盘语义仍走 hehun 场景链，不被单人路由挡掉。"""
    calls = _mock_completion("free_chat", monkeypatch)
    for text in ("我和TA合不合", "看看我们配不配",
                 "男1990年5月20日 15:30 北京，和女1992年10月1日 上海，我们合不合"):
        r = analyzer.analyze(text)
        assert r.scene_hint == "hehun", text
    assert calls["n"] == 0


@pytest.mark.parametrize("text", [
    "帮我看看我的婚姻运势",   # T022（当前绿）：带运势锚 → 不夺既有路由
    "我的姻缘运程如何",
    "今年我的姻缘怎么样",
])
def test_t076_timeline_marriage_not_forced(analyzer, monkeypatch, text):
    """不该走：带运势/流年时间锚的单人婚姻问句属运势域（T022 现状回归保护），
    不强制 advisor —— 交既有路径处理。"""
    calls = _mock_completion("free_chat", monkeypatch)
    r = analyzer.analyze(text)
    assert r.intent is None and r.scene_hint is None
    assert calls["n"] == 1


def test_t076_handler_guard_hehun_to_advisor():
    """该走（handler 侧兜底）：LLM 仍判 hehun 时，单人婚姻问句就地改判 advisor。"""
    from src.engines.message_analyzer import MessageAnalysis
    h = _bare_handler()

    def _a(intent):
        return MessageAnalysis(needs_soothe=False, soothe_text="",
                               emotion_label=None, intent=intent)

    a1 = _a("hehun")
    h._redirect_single_marriage_hehun(a1, "帮我看看我的婚姻状况")
    assert a1.intent == "advisor"
    # 不该走 ①：双人语境 → 保持 hehun
    a2 = _a("hehun")
    h._redirect_single_marriage_hehun(a2, "我和TA合不合")
    assert a2.intent == "hehun"
    # 不该走 ②：非 hehun 意图不动
    a3 = _a("bazi")
    h._redirect_single_marriage_hehun(a3, "帮我看看我的婚姻状况")
    assert a3.intent == "bazi"
    # 不该走 ③：带运势锚 → 不动
    a4 = _a("hehun")
    h._redirect_single_marriage_hehun(a4, "帮我看看我的婚姻运势")
    assert a4.intent == "hehun"
    # 不该走 ④：非婚姻文本 → 不动
    a5 = _a("hehun")
    h._redirect_single_marriage_hehun(a5, "帮我合婚")
    assert a5.intent == "hehun"


# ================================================================
# 七、评测侧口径修正（改前断言 → 改后断言 → 依据）
# ================================================================

_EVAL_DIR = PROJECT_DIR / "scripts" / "eval_agent"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

import l2_eval  # noqa: E402


def _eval_task(tid, checks):
    import json as _json
    for line in (PROJECT_DIR / "data" / "eval" / "agent_tasks.jsonl"
                 ).read_text(encoding="utf-8").splitlines():
        t = _json.loads(line)
        if t["id"] == tid:
            return t
    raise AssertionError(f"未知任务 {tid}")


def _derived_ok(checks, name):
    return next(c for c in checks if c["name"] == name)["ok"]


def test_t102_age_claim_accepts_adverb_and_particle():
    """T102：改前断言（产品实际答对却判「未提及 27/28 岁」）→ 改后断言接受
    「虚岁已经28岁了」形态。

    改前：`_AGE_CLAIM_RES` 6 条 pattern 全部要求 虚岁/周岁 与数字相邻（无副词夹层），
    「今年是2026年，你虚岁已经28岁了」整句漏判 → derived.age_claim
    require_mention 判「未出现 27/28 岁表述」= FAIL（产品输出实际正确）。
    改后：补「单位 + 副词 + N岁（可带语气收尾）」一条 pattern。
    依据：归因报告 §C 类 T102 行（断言实现缺口，P0 1 行修复）。
    """
    facts = {"age_zhousui": 27, "age_xusui": 28}
    spec = {"type": "age_claim", "params": {"require_mention": True}}
    reply = "今年是2026年，你虚岁已经28岁了。目前你正走在丙寅大运上。"
    assert _derived_ok(l2_eval.eval_derived_checks(
        {"reply_checks": {"derived": [spec]}, "setup": {"persons": [{}]}},
        reply, facts), "derived.age_claim") is True
    # 不说年龄 → 仍判「未提及」（判别力不得丢失）
    assert _derived_ok(l2_eval.eval_derived_checks(
        {"reply_checks": {"derived": [spec]}, "setup": {"persons": [{}]}},
        "目前你正走在丙寅大运上。", facts), "derived.age_claim") is False


def test_t102_new_pattern_does_not_treat_dayun_span_as_claim():
    """T102 防误伤：补的 pattern 要求副词**必须**出现 → 大运段端点句
    （「虚岁23岁到32岁」「从虚岁33岁起」）不得被认成当前年龄声明。"""
    facts = {"age_zhousui": 27, "age_xusui": 28}
    spec = {"type": "age_claim"}
    for reply in ("当前大运为丙寅，虚岁23岁到32岁这段走丙寅。",
                  "从虚岁33岁起换入乙丑大运。"):
        assert _derived_ok(l2_eval.eval_derived_checks(
            {"reply_checks": {"derived": [spec]}, "setup": {"persons": [{}]}},
            reply, facts), "derived.age_claim") is True, reply


@pytest.mark.parametrize("tid,reply,expect", [
    # T018：L2 语义断言补 2027（用户问「明年」，回复须给目标年 2027 而非当年）——
    # 与 L1 的 year 键契约双保险；改前只有 regex 流年|运势（答 2026 也能过）
    ("T018", "2027年流年运势：丁未年，日主乙木，正财透出，"
             "整体宜稳中求进，注意上半年口舌是非。", True),
    ("T018", "2026年流年运势：丙午年，日主乙木，正财透出，"
             "整体宜稳中求进，注意上半年口舌是非。", False),
    # T011：第三方盘按「天干/地支」两行渲染 → 丙 与 辰 分离（裸子串/丙\s+辰
    # 都永远不可能命中，改前断言结构上不可通过）
    ("T011", "天干  丙     癸     乙     辛\n地支  辰     巳     丑     巳\n"
             "📅 大运：8岁庚辰 → 18岁己卯", True),
    ("T011", "天干  庚     辛     乙     甲\n地支  午     巳     酉     申\n"
             "📅 大运：8岁庚辰 → 18岁己卯", False),   # 本人盘（错误覆盖）→ 判失败
    # T016：产品用「这周」；断言须容忍同义口语（min_len 30）
    ("T016", "嘿，这周运势来啦！2026年9月12日，你的运势有点起起伏伏，"
             "本周宜稳中求进，忌冲动决策、忌大额消费。", True),
    ("T016", "嘿，今天来啦！2026年9月12日，你的运程有点起起伏伏，"
             "宜稳中求进，忌冲动决策、忌大额消费，注意休息调养身心。", False),
    # T044：产品用「生辰」；须容错 + 仍要求指向双方（min_len 15）
    ("T044", "想看看你们合不合？给我双方生辰即可直接测算；或进入「双人合盘」页", True),
    ("T044", "请提供你的出生年月日时、出生地、性别", False),   # 单人建档引导 → 判失败
    # T106：k11c 金标「时柱壬午」为核心判别；「📜 四柱」非确定性契约（润色丢失）
    # （min_len 60）
    ("T106", "你的八字排盘是这样的：己卯年、己巳月、乙丑日、壬午时。"
             "日主乙木，生于巳月，整体命局偏温和，喜水木调候，时柱壬午主晚运，"
             "宜稳中求进。", True),
    ("T106", "你的八字排盘是这样的：己卯年、己巳月、乙丑日、辛巳时。"
             "日主乙木，生于巳月，整体命局偏温和，喜水木调候，时柱辛巳主晚运，"
             "宜稳中求进。",
     False),  # solar_time 关档 → 判失败
    # T101（min_len 80）/T108（min_len 60）：字面干支删除后，域标记仍须在
    ("T101", "【择业方位】适合行业：金融、能源、国企平台；吉利方位：东方、东南。"
             "当前大运为丙寅，正印透出，适合在稳定平台积累资历，"
             "行业选择上宜守正不宜冒进，避免短期频繁跳槽带来的损耗。", True),
    ("T101", "【择业方位】适合行业：金融、能源、国企平台；吉利方位：东方、东南。"
             "命局印星为用，适合在稳定平台积累资历，"
             "行业选择上宜守正不宜冒进，避免短期频繁跳槽带来的损耗与意气用事。",
     False),
    ("T108", "在易宝支付上班是背景信息，这里按你的本地信息来看：今年流年运势平稳，"
             "宜稳中求进，把手上项目做扎实，忌大幅变动，注意作息与情绪调节，"
             "秋冬两季可多留意长辈与健康。", True),
    ("T108", "在易宝支付上班是背景信息，这里按你的本地信息来看：今年整体平稳，"
             "宜稳中求进，把手上项目做扎实，忌大幅变动，注意作息与情绪调节，"
             "秋冬两季可多留意长辈与健康。", False),
])
def test_eval_side_assertion_rewrites(tid, reply, expect):
    """评测侧 9 条口径修正的逐条行为锁（改前断言 → 改后断言 → 依据）：

    - T011 丙辰 裸子串（结构不可通过）→ 双 lookahead（容忍卡片分行 + 保留
      大运/四柱/流年卡面标记；负例「本人盘」仍判失败）
    - T016 contains["本周"] → regex(本周|这周) AND (运势|运程)
    - T044 contains["出生"] → regex(出生|生辰) AND (双方|双人合盘|两人|你俩|八字)
    - T106 contains 去「四柱」（润色/卡面重建路径丢失，非确定性契约）
    - T101/T108 contains 去「丙寅」字面（T101 由 derived 五检承担一致性；
      T108 负例判据是「零检索」）
    依据：归因报告 §1 各任务行 + §5「评测侧可直接做」。
    """
    task = _eval_task(tid, None)
    checks = l2_eval.eval_reply_checks(task, reply)
    assert all(c["ok"] for c in checks) is expect, (
        tid, reply, [(c["name"], c["ok"], c["detail"]) for c in checks
                     if not c["ok"]])
    # 追加：负例断言（T108 零检索痕迹）仍有效
    if tid == "T108":
        bad = l2_eval.eval_reply_checks(
            task, reply + "\n（信息来源：以上为网络公开搜索结果）")
        assert any(c["name"] == "neg_checks" and not c["ok"] for c in bad)


def test_t028_t038_dual_channel_contract():
    """T028/T038：择日族双通道契约——expected_tools=[] 时声明 allow_tools 的
    工具调用视为等价通过；未声明工具 / 未声明通道仍判失败（不放宽判别力）。"""
    import l1_eval
    t28 = _eval_task("T028", None)
    t38 = _eval_task("T038", None)
    assert t28["allow_tools"] == ["zeri"] and t38["allow_tools"] == ["zeri"]
    # 该走：引擎意图路径（零调用）与工具路径（zeri 单调用）都通过
    assert l1_eval.compare_expected([], [], t28["allow_tools"]) == (True, True, "")
    ok, pok, detail = l1_eval.compare_expected(
        [], [("zeri", {"text": "搬家,2026年9月15日"})], t28["allow_tools"])
    assert ok is True and pok is True and "双通道" in detail
    # 不该走 ①：未声明的工具仍判失败
    ok, _, detail = l1_eval.compare_expected(
        [], [("bazi_chart", {})], t28["allow_tools"])
    assert ok is False and "实际 1 次" in detail
    # 不该走 ②：多个调用里混入未声明工具 → 失败
    ok, _, _ = l1_eval.compare_expected(
        [], [("zeri", {}), ("web_search", {})], t28["allow_tools"])
    assert ok is False
    # 不该走 ③：未声明 allow_tools 的任务（no_tool 桶）语义零变化
    ok, _, _ = l1_eval.compare_expected([], [("zeri", {})], [])
    assert ok is False
    ok, _, _ = l1_eval.compare_expected([], [("zeri", {})])
    assert ok is False


def test_task_schema_validation_still_passes():
    """改后评测集仍通过 schema 校验（allow_tools 键为契约内可选键；regex 可编译）。"""
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(_EVAL_DIR / "validate_tasks.py"),
         str(PROJECT_DIR / "data" / "eval" / "agent_tasks.jsonl")],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
