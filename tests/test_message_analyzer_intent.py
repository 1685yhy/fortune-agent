# -*- coding: utf-8 -*-
"""MessageAnalyzer 意图路由测试（fast path 陷阱修复 + career 意图）。

覆盖（修复任务 A1/A2）：
- fast path 收紧：含生日 + 意图词（公司/适合/配/像谁…）不再直接判 bazi，
  必须走 AI 分类（可返回 career 等）
- 纯生日陈述 → fast path 直接返回 bazi（不触发 LLM 调用）
- career 意图进入 valid 集合：LLM 返回 career 时正确解析
"""
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.engines.message_analyzer import MessageAnalyzer  # noqa: E402


@pytest.fixture
def analyzer():
    return MessageAnalyzer(api_key="test-key", model="test-model")


def _mock_completion(intent, monkeypatch):
    """mock LLM 响应：返回指定 intent 的 JSON（json.dumps 双引号），并计数调用次数。

    B1 附带修复（B1-12）：改用 monkeypatch.setattr 自动还原——原实现直接给两个
    模块属性赋值且永不还原，假函数泄漏到本文件之后的所有测试：test_bot.py 在本
    文件之后运行时，其 process() 内 _analyze_message 的每个消息都会被残留的
    hehun 假响应劫持成合婚引导卡片（基线可复现 4 失败，见 task-B1-report）。
    """
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
    # analyzer_mod 侧：analyze() 内是函数级局部 import（每次从 llm.client 实时读
    # 属性），message_analyzer 模块本身无此属性——raising=False 兼容两种形态，
    # 保留原"双模块都盖"意图且同样自动还原
    monkeypatch.setattr(analyzer_mod, "deepseek_anthropic_completion", fake,
                        raising=False)
    return calls


def test_pure_birth_date_fast_path_bazi(analyzer, monkeypatch):
    """纯生日陈述：fast path 直接返回 bazi，不触发 LLM 调用。"""
    calls = _mock_completion("free_chat", monkeypatch)  # 若误走 LLM 会得到 free_chat
    result = analyzer.analyze("1990年5月20日 下午3点 北京 男")
    assert result.intent == "bazi"
    assert calls["n"] == 0  # fast path 无 LLM 调用


def test_birth_plus_company_question_goes_to_ai(analyzer, monkeypatch):
    """含生日 + 公司适配问题：不再 fast path 掐成 bazi，走 AI 分类返回 career。"""
    calls = _mock_completion("career", monkeypatch)
    result = analyzer.analyze("我的八字1990年5月20日生的，跟哪个互联网公司最配")
    assert result.intent == "career"
    assert calls["n"] == 1  # 确实走了 AI 分类


def test_birth_plus_career_question_career(analyzer, monkeypatch):
    """含生日 + 适合什么工作：AI 分类为 career。"""
    calls = _mock_completion("career", monkeypatch)
    result = analyzer.analyze("1990年5月20日出生，适合做什么工作")
    assert result.intent == "career"
    assert calls["n"] == 1


def test_birth_plus_similar_hint_goes_to_ai(analyzer, monkeypatch):
    """含生日 + 像谁/相似词：也走 AI 分类（不再被 fast path 掐成纯排盘 bazi）。"""
    calls = _mock_completion("bazi", monkeypatch)
    result = analyzer.analyze("1990年5月20日，我像谁")
    assert calls["n"] == 1
    assert result.intent == "bazi"


def test_parse_response_career_valid():
    """career 在 valid 意图集合中：LLM 返回 career 能正确解析。"""
    a = MessageAnalyzer(api_key="", model="")
    r = a._parse_response(
        '{"needs_soothe": false, "soothe_text": "", "emotion": "neutral", '
        '"intent": "career", "is_sharing": false}'
    )
    assert r.intent == "career"


def test_parse_response_phase1_fields():
    """阶段 1 理解升级（方案 v5）：secondary_needs/facts/missing_info/needs_search 解析。"""
    a = MessageAnalyzer(api_key="", model="")
    r = a._parse_response(
        '{"needs_soothe": false, "soothe_text": "", "emotion": "anxious", '
        '"intent": "career", "is_sharing": false, '
        '"secondary_needs": ["bazi", "comfort"], '
        '"facts": {"gender": "女", "subject": "self", "employer": "字节跳动"}, '
        '"missing_info": ["birth_time", "company_name"], "needs_search": true}'
    )
    assert r.intent == "career"
    assert r.secondary_needs == ["bazi", "comfort"]
    assert r.facts == {"gender": "女", "subject": "self", "employer": "字节跳动"}
    assert r.missing_info == ["birth_time", "company_name"]
    assert r.needs_search is True


def test_parse_response_phase1_fallback():
    """阶段 1 解析失败降级：旧格式 JSON 缺新字段时走默认值，不崩。"""
    a = MessageAnalyzer(api_key="", model="")
    r = a._parse_response(
        '{"needs_soothe": false, "soothe_text": "", "emotion": "neutral", '
        '"intent": "dream", "is_sharing": false}'
    )
    assert r.intent == "dream"
    assert r.secondary_needs == []
    assert r.facts == {}
    assert r.missing_info == []
    assert r.needs_search is False


def test_birth_date_pattern_covers_lunar_month_11_12():
    """D7 修复：快判 BIRTH_DATE_PATTERN 覆盖 十一月/十二月（中文数字两位数月）。

    修前月名交替是单字符类 [一…十冬腊正]，'十一月'取'十'后 [月/-] 匹配
    '一' 失败 → 整句 miss → 降级链路把完整出生信息误判为自由聊天。
    十月（单字符）不受影响；冬月/腊月同族保留。
    """
    p = MessageAnalyzer.BIRTH_DATE_PATTERN
    assert p.search("1999年十一月28"), "十一月+阿拉伯日"
    assert p.search("1999年十二月28"), "十二月+阿拉伯日"
    assert p.search("1999年十一月二十八"), "十一月+中文数字日"
    assert p.search("农历1999年十一月28出生"), "农历前缀+十一月"
    assert p.search("1999年十月28"), "十月不回归"
    assert p.search("1999年冬月28") and p.search("1999年腊月28"), "冬月/腊月不回归"
    assert not p.search("1999年十一月"), "无日不误判"  # 只有月没有日，非完整出生信息


def test_fast_path_bazi_with_lunar_month_11_12(analyzer, monkeypatch):
    """快判 fast path：农历十一月/十二月生日 → 直接判 bazi，不触发 LLM 调用。"""
    calls = _mock_completion("free_chat", monkeypatch)  # 若误走 LLM 会得到 free_chat
    for msg in ("1999年阴历十一月28", "1999年农历十二月28出生"):
        result = analyzer.analyze(msg)
        assert result.intent == "bazi", msg
    assert calls["n"] == 0


def test_downgrade_rule_analyze_lunar_month_11_12():
    """降级链路（_rule_analyze → _quick_intent，L5-2 零 LLM 快判）同覆盖十一月/十二月。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.bot.handler import MessageHandler
    h = object.__new__(MessageHandler)
    for msg in ("1999年十一月28", "1999年十二月28", "1999年阴历十一月28"):
        assert h._rule_analyze(msg).intent == "bazi", msg
    assert h._rule_analyze("1999年十一月").intent is None  # 无日不判 bazi


def test_intent_hint_pattern_coverage():
    """INTENT_HINT_PATTERN 覆盖任务要求的全部意图词。"""
    p = MessageAnalyzer.INTENT_HINT_PATTERN
    for kw in ["适合", "发展", "工作", "公司", "职业", "事业", "配", "像谁",
               "相似", "去哪", "怎么样", "好吗", "能", "会", "合不合", "合盘",
               "缘分", "契合"]:
        assert p.search(f"1990年5月20日{kw}"), f"缺少意图词: {kw}"


# ── Task 8 双人合盘 hehun 意图扩展（触发词 + 两人语义规则） ──

def test_birth_plus_hehun_words_goes_to_ai(analyzer, monkeypatch):
    """含生日 + 婚恋配对词（我和TA合不合）：不走 fast path 纯排盘，走 AI 分类返回 hehun。"""
    calls = _mock_completion("hehun", monkeypatch)
    result = analyzer.analyze("1990年5月20日 男，我和TA合不合")
    assert result.intent == "hehun"
    assert calls["n"] == 1  # 确实走了 AI 分类


def test_couple_match_question_hehun(analyzer, monkeypatch):
    """「我们俩配不配/合不合」类两人消息 → hehun（mock LLM 返回 hehun）。"""
    calls = _mock_completion("hehun", monkeypatch)
    result = analyzer.analyze("看看我们配不配")
    assert result.intent == "hehun"
    assert calls["n"] == 1


def test_hepan_word_hehun(analyzer, monkeypatch):
    """「合盘」触发词 → hehun。"""
    calls = _mock_completion("hehun", monkeypatch)
    result = analyzer.analyze("帮我合盘，我和她")
    assert result.intent == "hehun"
    assert calls["n"] == 1


def test_parse_response_hehun_valid():
    """hehun 在 valid 意图集合中：LLM 返回 hehun 能正确解析。"""
    a = MessageAnalyzer(api_key="", model="")
    r = a._parse_response(
        '{"needs_soothe": false, "soothe_text": "", "emotion": "neutral", '
        '"intent": "hehun", "is_sharing": false}'
    )
    assert r.intent == "hehun"


def test_hehun_trigger_words_in_prompts():
    """COMBINED_PROMPT 与 INTENT_CLASSIFY_PROMPT 均覆盖 Task 8 扩展的 hehun 触发词。"""
    from src.engines.message_analyzer import COMBINED_PROMPT
    from src.engines.intent_classifier import INTENT_CLASSIFY_PROMPT
    kws = ["双人合盘", "合盘", "八字合婚", "我和TA合不合", "看看我们配不配", "缘分契合"]
    for kw in kws:
        assert kw in COMBINED_PROMPT, f"COMBINED_PROMPT 缺 hehun 触发词: {kw}"
        assert kw in INTENT_CLASSIFY_PROMPT, f"INTENT_CLASSIFY_PROMPT 缺 hehun 触发词: {kw}"
    # 两人语义规则（我/我们 + 他/她/TA + 合/配/缘分 → hehun）
    assert "他/她/TA" in COMBINED_PROMPT
    assert "他/她/TA" in INTENT_CLASSIFY_PROMPT
