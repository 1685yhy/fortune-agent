# -*- coding: utf-8 -*-
"""Task A4：意图分级路由（轻量路由器，预算感知）。

三档评分（_score_intent_complexity）：
- simple：问候/感谢/再见/日期时间 → 0 LLM 直通快通道（_route_simple_intent）
- normal：生日陈述/单意图问题/自由聊天 → 现状主链（pregen + 意图分析 + 路由）
- complex：长文（>80 字）/ 多意图叠加（≥2 场景类别且 >40 字）→ 现状主链
  （模型池不足，更强模型选择文档化为未来挂载点，见 handler.py 模型矩阵注释）

红线：不引入新模型/新 key——"简单意图不经过 AI 慢推理"即本任务预算感知
核心价值（调研结论：轻模型不能承担工具路由决策，故不做轻模型承担任务）。
"""
import sys
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402


def make_score_handler() -> MessageHandler:
    """object.__new__ 装配评分/路由单测（纯正则 + 类属性，无依赖）。"""
    return object.__new__(MessageHandler)


def make_process_handler() -> MessageHandler:
    """真实 __init__ 装配 process() 全链路（llm 全 Mock，api_key 空 → 规则快判）。"""
    mock_llm = Mock()
    mock_llm.api_key = ""
    mock_llm.model = "deepseek-flash"
    mock_llm.chat_conversation.return_value = "🔮 精简回复"
    mock_llm.chat.return_value = Mock(response="🔮 精简回复")
    mock_dao = Mock()
    mock_dao.get_user_bazi.return_value = None
    mock_dao.db_path = ""  # 无 db → chart_dao/record_query/preference_dao 均 None
    mock_session = Mock()
    mock_session.get_context_for_llm.return_value = []
    mock_session.add_message.return_value = None
    h = MessageHandler(
        engine=Mock(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        retriever=Mock(), llm=mock_llm, dao=mock_dao, session_dao=mock_session,
    )
    h.memory_system = None  # 不碰真实记忆库（_get_welcome_back 等均已 None 守卫）
    return h


# ───────────────────────────── 评分：SIMPLE 档各子类样例 ─────────────────────────────


def test_score_simple_greeting_variants():
    h = make_score_handler()
    for msg in ("你好", "您好", "你好呀", "哈喽", "嗨", "hello", "Hi!",
                "早上好", "中午好", "下午好", "晚上好", "早安", "你好呀！"):
        assert h._score_intent_complexity(msg) == "simple", msg


def test_score_simple_thanks():
    h = make_score_handler()
    for msg in ("谢谢", "谢谢啦", "谢谢你", "谢谢了", "感谢", "多谢", "辛苦了"):
        assert h._score_intent_complexity(msg) == "simple", msg


def test_score_simple_bye():
    h = make_score_handler()
    for msg in ("再见", "拜拜", "晚安", "再见啦"):
        assert h._score_intent_complexity(msg) == "simple", msg


def test_score_simple_datetime():
    h = make_score_handler()
    for msg in ("今天几号", "几月几号", "现在几点", "几点了", "几点钟",
                "现在几点呢", "请问现在几点了", "现在时间", "什么时间",
                "今天星期几", "星期几", "今天周几", "周几"):
        assert h._score_intent_complexity(msg) == "simple", msg


# ───────────────────────────── 评分：NORMAL / COMPLEX 档 ─────────────────────────────


def test_score_normal_birth_statement():
    """纯生日陈述是排盘意图（走引擎主链），不属简单档。"""
    h = make_score_handler()
    assert h._score_intent_complexity("1990年5月20日 下午3点 北京 男") == "normal"


def test_score_normal_intent_hint():
    """生日+意图提示词（工作/适合…）→ normal（走 AI 意图分类）。"""
    h = make_score_handler()
    assert h._score_intent_complexity("1990年5月20日适合什么工作") == "normal"


def test_score_normal_greeting_prefix_plus_intent():
    """问候前缀+真意图不得被简单档掐掉（守卫：场景词/命理词/意图提示词）。"""
    h = make_score_handler()
    for msg in ("你好，帮我算八字", "你好，今天财运怎么样",
                "您好，我想看看我的紫微盘", "谢谢，帮我分析一下"):
        assert h._score_intent_complexity(msg) == "normal", msg


def test_score_normal_casual_chat():
    """中性闲聊/语气词 → normal（走自由对话主链）。"""
    h = make_score_handler()
    for msg in ("今天天气不错", "随便聊聊", "在吗", "嗯嗯", "哈哈"):
        assert h._score_intent_complexity(msg) == "normal", msg


def test_score_complex_long_text():
    """>80 字长文（倾诉/多要求）→ complex。"""
    h = make_score_handler()
    msg = "最近工作压力很大，和同事关系也有些紧张，领导总是给我安排额外任务，"
    msg += "回到家还要照顾孩子，感觉特别疲惫，不知道自己这样坚持下去到底值不值得，"
    msg += "想请您帮我看看我是不是应该换个工作方向，或者调整一下心态"
    assert len(msg) > 80
    assert h._score_intent_complexity(msg) == "complex"


def test_score_complex_multi_intent():
    """>40 字且命中 ≥2 个场景类别（财运+感情+工作+健康）→ complex。"""
    h = make_score_handler()
    msg = "我今年财运不错，但感情上有些波折，想问问工作上的发展应该怎么规划，"
    msg += "还有健康方面要注意什么"
    assert len(msg) > 40
    assert h._score_intent_complexity(msg) == "complex"


def test_score_empty_msg_normal():
    """空消息/纯空白 → normal（交原有流程处理，不被路由吞掉）。"""
    h = make_score_handler()
    assert h._score_intent_complexity("") == "normal"
    assert h._score_intent_complexity(" ") == "normal"


def test_score_fail_open_on_exception(monkeypatch):
    """评分异常 fail-open → normal（宁多走一步不误掐）。"""
    h = make_score_handler()
    monkeypatch.setattr(MessageHandler, "SIMPLE_GREETING_RE", None)  # 触发 .match 异常
    assert h._score_intent_complexity("你好") == "normal"


# ───────────────────────────── 路由：选择断言（_route_simple_intent） ─────────────────────────────


def test_route_greeting_returns_default_text():
    h = make_score_handler()
    out = h._route_simple_intent("你好", "u1")
    assert out and "易理明灯" in out


def test_route_greeting_welcome_back_personalized():
    """有记忆的回头客 → 个性化欢迎回来（0 LLM，复用记忆模板）。"""
    h = make_score_handler()
    h.memory_system = Mock()
    h.memory_system.has_memory.return_value = True
    h.memory_system.get_greeting.return_value = "早上好，最近怎么样"
    assert h._route_simple_intent("你好", "u1") == "欢迎回来！早上好，最近怎么样"


def test_route_greeting_fail_open_on_memory_error():
    """记忆层异常 → 回落默认文案（fail-open，不冒泡）。"""
    h = make_score_handler()
    h.memory_system = Mock()
    h.memory_system.has_memory.side_effect = RuntimeError("memory db locked")
    out = h._route_simple_intent("你好", "u1")
    assert out and "易理明灯" in out


def test_route_datetime_contains_today():
    h = make_score_handler()
    out = h._route_simple_intent("今天几号", "u1")
    assert str(date.today().year) in out
    assert "星期" in out


def test_route_non_simple_returns_none():
    """normal/complex 档不产生简单回复（走主链）。"""
    h = make_score_handler()
    assert h._route_simple_intent("帮我算八字", "u1") is None
    assert h._route_simple_intent("1990年5月20日 北京 男", "u1") is None
    assert h._route_simple_intent("你好，今天财运怎么样", "u1") is None
    long_msg = "最近工作压力很大，和同事关系也有些紧张，领导总是给我安排额外任务，"
    long_msg += "回到家还要照顾孩子，感觉特别疲惫，不知道自己这样坚持下去到底值不值得"
    assert h._route_simple_intent(long_msg, "u1") is None


def test_route_fail_open_on_exception(monkeypatch):
    """路由器内部异常 → None 走主链（绝不因路由错误丢回复）。"""
    h = make_score_handler()
    monkeypatch.setattr(h, "_score_intent_complexity",
                        Mock(side_effect=RuntimeError("router boom")))
    assert h._route_simple_intent("你好", "u1") is None


# ───────────────────────────── 集成：process() 路由决策 ─────────────────────────────


def test_process_greeting_zero_llm(monkeypatch):
    """SIMPLE 档命中：不经过 AI 慢推理（意图分析/回复生成均不调），不写历史。"""
    h = make_process_handler()
    calls = []
    monkeypatch.setattr(h, "_analyze_message",
                        Mock(side_effect=lambda *a, **kw: calls.append(1) or None))
    reply = h.process("你好", "u1")
    assert reply and "易理明灯" in reply
    assert calls == []  # 核心价值实证：意图分析 LLM 未调用
    h.llm.chat.assert_not_called()
    h.llm.chat_conversation.assert_not_called()  # 回复生成 LLM 也未调用
    h.session_dao.add_message.assert_not_called()  # T10 快路径不写历史


def test_process_datetime_zero_llm(monkeypatch):
    """日期时间查询 SIMPLE 档：确定性回答，0 LLM。"""
    h = make_process_handler()
    calls = []
    monkeypatch.setattr(h, "_analyze_message",
                        Mock(side_effect=lambda *a, **kw: calls.append(1) or None))
    reply = h.process("现在几点", "u2")
    assert "今天" in reply and "星期" in reply
    assert calls == []
    h.llm.chat_conversation.assert_not_called()


def test_process_greeting_deep_night_ok():
    """deep_night 倾诉用户问候同样直通（快通道与深夜语气层无关，无副作用）。"""
    h = make_process_handler()
    reply = h.process("你好", "u3", deep_night=True)
    assert reply and "易理明灯" in reply


def test_process_normal_birth_full_pipeline(monkeypatch):
    """NORMAL 档不受影响：生日陈述照常走主链（规则快判 bazi → handler 分发）。"""
    from src.bot.handler import MessageHandler as MH
    monkeypatch.setattr(MH, "_handle_bazi", Mock(return_value="测试回复"))
    h = make_process_handler()
    reply = h.process("1990年5月20日 北京 男", "u4")
    assert "测试回复" in reply
    MH._handle_bazi.assert_called_once()


def test_process_greeting_prefix_intent_not_shortcircuited():
    """问候前缀+真意图（你好，帮我算八字）不得被简单档掐掉 → 走主链 free_chat。"""
    h = make_process_handler()
    reply = h.process("你好，帮我算八字", "u5")
    assert reply == "🔮 精简回复"  # 主链自由对话（mock LLM）实证
    # 空会话历史下 free_chat 走 llm.chat 单消息分支（chat_conversation 需历史）
    h.llm.chat.assert_called_once()


def test_process_downgraded_greeting_zero_llm(monkeypatch):
    """降级用户问候同样直通（比 lite 精简对话更便宜，预算感知全覆盖）。"""
    h = make_process_handler()
    calls = []
    monkeypatch.setattr(h, "_analyze_message",
                        Mock(side_effect=lambda *a, **kw: calls.append(1) or None))
    reply = h.process("你好", "u6", downgraded=True)
    assert reply and "易理明灯" in reply
    assert calls == []
    h.llm.chat_conversation.assert_not_called()
