# -*- coding: utf-8 -*-
"""批次 2 E6：工具 AI 决策通道（5 新工具对话可达性）——场景词门控 + 引擎兜底。

对标豆包/元宝「LLM 是调度员、工具是工具库」：
- 场景词命中 → MessageAnalyzer.analyze() 直接返回 intent=None +
  scene_hint=cap_id（0 LLM 确定性，与 D5 快路径门控同族；词表模块级常量
  TOOL_SCENE_WORDS，逐词误伤评估见 task-E6-report.md）
- process() intent=None 分支：工具链（_free_chat 工具清单注入 +
  _run_tool_loop）→ LLM 未输出工单时按 SCENE_DEFAULT_ENGINE 默认引擎兜底
- e2e：mock LLM 输出工具工单 → _run_tool_loop 执行 → 回复含工具结果

红线（逐字）：src/bot/tool_calls.py 主链零改动；executor/
_run_with_timeout/MAX_TOOL_ITERATIONS=2 零改动；鉴权/存储/计算层零改动。
"""
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import pytest  # noqa: E402

from src.engines.message_analyzer import (  # noqa: E402
    MessageAnalyzer, MessageAnalysis, TOOL_SCENE_WORDS, COMBINED_PROMPT,
)


@pytest.fixture
def analyzer():
    return MessageAnalyzer(api_key="test-key", model="test-model")


def _mock_completion(intent, monkeypatch):
    """mock LLM 响应并计数调用（与 test_message_analyzer_intent 同型，
    monkeypatch 自动还原，不泄漏到其他测试）。"""
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


# ============================================================
# A. 词表完整性 + 正例门控（0 LLM 确定性）
# ============================================================

NUM_OMEN_POS = [
    "这个手机号 13800138000 好不好",
    "我的手机号码 138 结尾 8888 怎么样",
    "看看这个车牌 京A88888 吉不吉利",
    "门牌号 404 好不好",
    "尾号 520 吉利吗",
    "号码吉凶 帮我看看 13911112222",
    "数字吉凶 168 寓意如何",
]
HEHUN_POS = [
    "我和她合不合",
    "想找人合婚",
    "我们八字合不合",
    "看看我们配不配",
    "我们俩般配吗",
    "生辰合不合怎么看",
]
NAMING_POS = [
    "给孩子起名",
    "想取名 姓李 男",
    "想改名",
    "宝宝叫什么好",
    "孩子叫啥好",
]
CYCLE_POS = [
    "今年流年怎么样",
    "这个月流月",
    "明年运势怎么样",
    "帮我看看逐月运势",
]
CAREER_POS = [
    "我适合做什么",
    "我适合什么行业",
    "职业方向怎么选",
    "想择业",
    "行业选择 帮我看看",
    "最近在找工作",
    "想换工作",
]


def test_tool_scene_words_table_shape():
    """词表为 5 键（对应 5 新工具 cap_id），全部为多字专属词。"""
    assert set(TOOL_SCENE_WORDS) == {"num_omen", "hehun", "naming",
                                     "fortune_cycle", "career_dir"}
    for cap_id, words in TOOL_SCENE_WORDS.items():
        assert words, cap_id
        assert all(len(w) >= 2 for w in words), cap_id  # 无单字泛词


def test_tool_scene_words_cross_cap_disjoint():
    """跨场景词无交集：任一消息最多命中一个 cap_id（门控确定性）。"""
    all_words = [w for words in TOOL_SCENE_WORDS.values() for w in words]
    assert len(all_words) == len(set(all_words)), "词表跨场景重复"


@pytest.mark.parametrize("cap_id,msgs", [
    ("num_omen", NUM_OMEN_POS),
    ("hehun", HEHUN_POS),
    ("naming", NAMING_POS),
    ("fortune_cycle", CYCLE_POS),
    ("career_dir", CAREER_POS),
], ids=["num_omen", "hehun", "naming", "fortune_cycle", "career_dir"])
def test_scene_gate_positive_each_cap(analyzer, monkeypatch, cap_id, msgs):
    """场景词正例：analyze() → intent=None + scene_hint=cap_id，0 LLM 调用。"""
    calls = _mock_completion("bazi", monkeypatch)  # 若误走 LLM 会得到 bazi
    for msg in msgs:
        result = analyzer.analyze(msg)
        assert result.intent is None, msg
        assert result.scene_hint == cap_id, msg
    assert calls["n"] == 0


def test_scene_gate_precedes_birth_fastpath(analyzer, monkeypatch):
    """门控先于 BIRTH_DATE_PATTERN 快路径：含完整日期+场景词不被掐成 bazi
    （D4 同族缺陷防护：『1990年5月20日 想给孩子起名』修前判 bazi 排盘）。"""
    calls = _mock_completion("bazi", monkeypatch)
    for msg in ("1990年5月20日 想给孩子起名",
                "2026年9月15日 合婚 帮我们看看",
                "1995年3月12日 下午2点 男，我适合什么行业"):
        result = analyzer.analyze(msg)
        assert result.intent is None, msg
        assert result.scene_hint is not None, msg
    assert calls["n"] == 0


def test_pure_birth_statement_still_fast_path_bazi(analyzer, monkeypatch):
    """无场景词的纯生日陈述仍走 bazi 快路径（0 LLM 回归保护）。"""
    calls = _mock_completion("free_chat", monkeypatch)
    for msg in ("1990年5月20日 下午3点 北京 男",
                "1999年阴历十一月28出生"):
        result = analyzer.analyze(msg)
        assert result.intent == "bazi", msg
        assert result.scene_hint is None, msg
    assert calls["n"] == 0


def test_combined_prompt_tool_scene_guidance():
    """COMBINED_PROMPT 含 E6 工具场景分类指导（规则门控未命中的变体表述
    由 LLM 分到 free_chat → 工具链）。"""
    assert "## 6. 工具场景分类指导" in COMBINED_PROMPT
    for kw in ("手机号", "合婚", "起名", "流年", "择业"):
        assert kw in COMBINED_PROMPT, f"COMBINED_PROMPT 缺场景指导词: {kw}"


# ============================================================
# B. 反例不误伤（泛词/闲聊/其他 intent 行为不变）
# ============================================================

def test_no_false_positive_generic_words_and_chitchat(analyzer, monkeypatch):
    """不含工具场景词的闲聊/泛词 → 行为不变（走 LLM 分类，mock 返回 free_chat）。

    防误伤要点：『名字/号码/运势』等泛词单用不收录（只收多字专属词）；
    『电话号码是多少』不命中（词表只有 手机号/手机号码 无 电话号码）。
    """
    calls = _mock_completion("free_chat", monkeypatch)
    for msg in ("我觉得这名字挺好听的",     # 名字（泛词，非起名场景词）
                "电话号码是多少",           # 号码（泛词，非手机号/尾号）
                "今天天气不错",
                "我的运势如何",             # 运势（泛词，非明年运势/逐月运势）
                "帮我看下面相"):
        result = analyzer.analyze(msg)
        assert result.intent is None, msg      # mock LLM → free_chat
        assert result.scene_hint is None, msg  # 未被场景门控劫持
    assert calls["n"] == 5


def test_no_false_positive_lookalike_words(analyzer, monkeypatch):
    """形近词不误伤：改名 vs 改签、起名 vs 起个名字（变体走 LLM）、
    合盘 vs 合婚（合盘仍走 LLM 分类返回 hehun）、配 vs 配不配。"""
    calls = _mock_completion("free_chat", monkeypatch)
    for msg in ("帮我改签机票",           # 改签 不命中 改名
                "帮我起个名字",           # 起个名字 不命中 起名（变体走 LLM）
                "帮我合盘，我和她"):      # 合盘 不命中 合婚词表（LLM→hehun）
        result = analyzer.analyze(msg)
        assert result.scene_hint is None, msg
    # 3 条全部走了 LLM 分类（未被规则门控截断）
    assert calls["n"] == 3


def test_no_false_positive_pure_birth_with_zeri_words(analyzer, monkeypatch):
    """D5 择日词回归：含日期+择日词仍走 LLM 分类（zeri），E6 门控不得劫持。"""
    calls = _mock_completion("zeri", monkeypatch)
    result = analyzer.analyze("2026年9月15日搬家 帮我选个日子")
    assert result.intent == "zeri"
    assert result.scene_hint is None
    assert calls["n"] == 1


# ============================================================
# C. process() 兜底：工具链无工单 → 默认引擎（质量下限护栏）
# ============================================================

def _make_light_handler(**kw):
    """__new__ 轻量装配 process()（test_fastpath 同型）：llm api_key="" →
    工具循环空转，引擎兜底路径可精确观测。"""
    from unittest.mock import Mock

    from src.bot.handler import MessageHandler
    from src.utils.cache import ResponseCache

    mock_llm = Mock()
    mock_llm.api_key = ""
    mock_llm.model = "deepseek-v4-flash"
    mock_llm.chat_conversation.return_value = "🔮 精简回复"
    mock_llm.chat.return_value = Mock(response="🔮 精简回复")
    mock_dao = Mock()
    mock_dao.db_path = ""
    mock_dao.get_user_bazi.return_value = None
    mock_session = Mock()
    mock_session.get_context_for_llm.return_value = []
    mock_session.add_message.return_value = None

    h = MessageHandler.__new__(MessageHandler)
    h.llm = mock_llm
    h.dao = mock_dao
    h.session_dao = mock_session
    h.engine = Mock()
    h.hehun_engine = Mock()
    h.xingming_engine = Mock()
    h.member_dao = None
    h.preference_dao = None
    h.chart_dao = None
    h.record_query = None
    h.memory = None
    h.memory_system = None
    h.compactor = None  # _maybe_compact 守卫（session_dao 为真值 Mock，须显式置 None）
    h.cache = ResponseCache(max_size=50)
    h._tool_logs = {}
    h._citations = {}
    h._card_turn = {}
    h._downgraded = {}
    h._deep_night = {}
    h._analysis_facts = {}
    h._pregen_instant = {}
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def _patch_scene_analysis(handler, scene_hint):
    handler._analyze_message = (
        lambda msg, user_id="", session_id=None: MessageAnalysis(
            needs_soothe=False, soothe_text="", emotion_label=None,
            intent=None, scene_hint=scene_hint))


def test_scene_fallback_default_engine_when_no_tool_call():
    """兜底正例：hehun 场景 + LLM 无工单 → 回退 _handle_hehun 引擎产出。

    （"我们合不合"无双方生辰 → 引擎处理器真实产出引导追问文案，
    而非工具链的占位回复——证明兜底替换发生。）"""
    h = _make_light_handler()
    _patch_scene_analysis(h, "hehun")
    reply = h.process("我们合不合", "u1")
    assert "合不合" in reply or "生辰" in reply
    assert reply != "🔮 精简回复"


def test_scene_fallback_skipped_when_tool_calls_exist():
    """工具链已执行（tool_log 有 calls）→ 不兜底，工具链回复原样返回。"""
    from unittest.mock import Mock

    h = _make_light_handler()
    _patch_scene_analysis(h, "hehun")
    h._handle_hehun = Mock(side_effect=AssertionError("不应走引擎兜底"))

    def fake_tool_loop(msg, user_id, reply, stream_cb=None, analysis=None,
                       session_id=None):
        h._tool_logs[user_id] = {
            "calls": [{"type": "合婚", "params": "{}", "hit": True}],
            "retrieval_hit": "unused",
        }
        return "根据合婚结果：你们很般配。"

    h._run_tool_loop = fake_tool_loop
    reply = h.process("我们合不合", "u1")
    assert reply == "根据合婚结果：你们很般配。"


def test_num_omen_scene_no_engine_fallback():
    """num_omen 无引擎（映射表外）：LLM 自由回复即为兜底，回复保持原样。"""
    h = _make_light_handler()
    _patch_scene_analysis(h, "num_omen")
    reply = h.process("这个手机号 13800138000 好不好", "u1")
    assert reply == "🔮 精简回复"


def test_scene_fallback_disabled_in_downgrade():
    """降级链路不做引擎兜底（L5-2 成本纪律）：lite 回复原样返回。

    防御纵深：_rule_analyze 本不产出 scene_hint，这里强制注入 scene_hint
    验证 process() 侧还有第二道门（not downgraded）。"""
    from unittest.mock import Mock

    h = _make_light_handler()
    h._rule_analyze = lambda msg: MessageAnalysis(
        needs_soothe=False, soothe_text="", emotion_label=None,
        intent=None, scene_hint="hehun")
    h._handle_hehun = Mock(side_effect=AssertionError("降级不得走引擎"))
    reply = h.process("我们合不合", "u1", downgraded=True)
    assert reply == "🔮 精简回复"


def test_scene_fallback_fail_open_on_handler_exception():
    """兜底处理器抛异常 → 保留 _free_chat 原文（fail-open，绝不冒泡）。"""
    from unittest.mock import Mock

    h = _make_light_handler()
    _patch_scene_analysis(h, "hehun")
    h._handle_hehun = Mock(side_effect=RuntimeError("engine down"))
    reply = h.process("我们合不合", "u1")
    assert reply == "🔮 精简回复"


# ------------------------------------------------------------- C2. partial_hint 与场景消息互斥
# Important-1 修复（批次 2 E6）：含生日片段的工具场景消息不得注入「分步提供
# 出生信息」partial_hint——否则与工具链调度指令并存，LLM 行为不可预测
# （实测复现：『1990年5月20日 想给孩子起名』known={year,month,day}、
# 『1995年3月12日 下午2点 男，我适合什么行业』known 5 键）。


def test_scene_hint_suppresses_partial_hint_injection():
    """场景词命中 + 消息含生日片段 → _free_chat 不注入 partial_hint
    （复现实测：known 非空但 scene_hint 门控优先，工具链提示词保持纯净）。"""
    h = _make_light_handler()
    h.session_dao = None  # 单消息模式：partial 累积只读当前消息，不碰会话 DAO
    for msg, cap in (("1990年5月20日 想给孩子起名", "naming"),
                     ("1995年3月12日 下午2点 男，我适合什么行业", "career_dir")):
        h.llm.chat.reset_mock()
        reply = h._free_chat(msg, "u1", scene_hint=cap)
        assert reply == "🔮 精简回复"
        chat_arg = h.llm.chat.call_args[0][0]
        assert "分步提供出生信息" not in chat_arg, msg
        assert "只询问缺失项" not in chat_arg, msg


def test_no_scene_word_birthday_request_still_injects_partial_hint():
    """既有 F2 行为回归：无场景词的生日/年龄请求仍注入 partial_hint
    （渐进引导只被场景门控让路，自由对话路径不受影响）。"""
    from datetime import date

    h = _make_light_handler()
    h.session_dao = None
    expected_year = date.today().year - 50  # 50岁 → 当前年-50（生产 2026 → 1976）
    reply = h._free_chat("我今年50岁了", "u1")
    assert reply == "🔮 精简回复"
    chat_arg = h.llm.chat.call_args[0][0]
    assert "【重要】用户正在分步提供出生信息" in chat_arg
    assert f"目前已确认：出生于{expected_year}年（按50岁周岁推算）" in chat_arg
    assert "只询问缺失项" in chat_arg


# ============================================================
# D. e2e：场景消息 → 工具链 → 工单执行 → 回复含工具结果
# ============================================================

def test_e2e_scene_routing_tool_ticket_executed(monkeypatch):
    """e2e：场景消息（scene_hint=hehun）→ _free_chat 注入工单 →
    _run_tool_loop 真实执行合婚工具（真实双排盘）→ 回复含工具结果；
    tool_log 落库 → 出口不触发引擎兜底。"""
    from unittest.mock import Mock

    import src.llm.client as llm_client
    from src.bot.handler import MessageHandler
    from src.engines.bazi import BaziEngine
    from src.engines.hehun import HehunEngine

    mock_llm = Mock()
    mock_llm.api_key = "test-key"
    mock_llm.model = "deepseek-v4-flash"
    mock_llm.provider = "glm"  # 非 deepseek → JSON 工单路径
    mock_llm.chat_conversation.return_value = "（占位）"
    mock_dao = Mock()
    mock_dao.db_path = ""
    mock_dao.get_user_bazi.return_value = None
    mock_session = Mock()
    mock_session.get_context_for_llm.return_value = []
    mock_session.add_message.return_value = None

    h = MessageHandler(
        engine=BaziEngine(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        dream_engine=Mock(), hehun_engine=HehunEngine(),
        qimen_engine=Mock(), xingming_engine=Mock(),
        retriever=Mock(), llm=mock_llm, dao=mock_dao,
        session_dao=mock_session)
    h.memory_system = None  # 测试隔离：不写 data/memory 用户记忆文件

    h._analyze_message = (
        lambda msg, user_id="", session_id=None: MessageAnalysis(
            needs_soothe=False, soothe_text="", emotion_label=None,
            intent=None, scene_hint="hehun"))
    ticket = ('好的，我来为你们合婚。<tool_calls>[{"tool": "hehun", "params": '
              '{"birth_a": "1996年8月15日 巳时 上海 男", '
              '"birth_b": "1990年5月20日 午时 北京 女"}}]</tool_calls>')
    h._free_chat = Mock(return_value=ticket)

    seen = {}

    def fake_completion(api_key, messages, **kw):
        seen["msgs"] = messages
        return "合婚结果：你们是鼠马六冲配对，需多磨合。"

    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                        fake_completion)

    # tool_log 在 process() 出口被 _pop_tool_log 取走（pop 即删）——用 spy
    # 捕获落库值，证明走的是工具链而非引擎兜底路径
    orig_pop = h._pop_tool_log
    popped = {}

    def spy_pop(uid):
        v = orig_pop(uid)
        if v and v.get("calls"):
            popped["log"] = v
        return v

    h._pop_tool_log = spy_pop

    reply = h.process("看看我们合不合", "u1")

    # 回复含工具结果（工具真实执行 + 结果回传 LLM）
    assert "鼠马六冲" in reply
    # 工具执行结果确实注入到了 LLM 消息（真实合婚卡片内容）
    tail = seen["msgs"][-1]["content"]
    assert "生肖鼠与马" in tail and "六冲" in tail and "综合评分" in tail
    # tool_log 已落库（证明走的是工具链而非引擎兜底路径）
    calls = popped["log"]["calls"]
    assert calls and calls[0]["type"] == "合婚" and calls[0]["hit"] is True
