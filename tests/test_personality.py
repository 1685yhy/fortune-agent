"""Tests for AI personality system — Sprint 1."""
import time
from unittest.mock import Mock

from src.llm.prompts import (
    PERSONALITY_PROMPTS,
    SYSTEM_PROMPT_SASSY,
    SYSTEM_PROMPT_ANALYST,
    SYSTEM_PROMPT_GENTLE,
    CHAT_PROMPT,
)
from src.bot.handler import MessageHandler


# ── Banned Words Tests ─────────────────────────────────────────────

def test_sassy_mode_no_old_style():
    """毒舌闺蜜模式不应使用老旧自称呼（如自称老夫/老朽/小友）"""
    # The old prompt had "输出风格" section with "自称老夫" - check it's replaced
    # The new prompts use "说话风格" or "人格设定" instead
    old_style_markers = [
        "- 自称\"老夫\"或\"老先生\"",
        "- 自称'老夫'或'老先生'",
        "对用户称呼\"小友\"或\"您\"",
        "对用户称呼'小友'或'您'",
    ]
    for marker in old_style_markers:
        assert marker not in SYSTEM_PROMPT_SASSY, \
            f"毒舌闺蜜模式包含老旧风格标记: {marker}"


def test_analyst_mode_no_old_style():
    """理性分析师模式不应使用老旧自称呼"""
    assert "自称\"老夫\"" not in SYSTEM_PROMPT_ANALYST
    assert "自称'老夫'" not in SYSTEM_PROMPT_ANALYST
    assert "自称老夫" not in SYSTEM_PROMPT_ANALYST
    # It should NOT have the old "对用户称呼'小友'或'您'" style
    assert "对用户称呼" not in SYSTEM_PROMPT_ANALYST or "小友" not in SYSTEM_PROMPT_ANALYST.split("对用户称呼")[1] if "对用户称呼" in SYSTEM_PROMPT_ANALYST else True


def test_gentle_mode_no_old_style():
    """温柔陪伴者模式不应使用老旧自称呼"""
    assert "自称老夫" not in SYSTEM_PROMPT_GENTLE


def test_chat_prompt_no_old_style():
    """聊天提示不应使用老旧自称呼"""
    # CHAT_PROMPT mentions 小友/老夫 as things NOT to use
    # Check it doesn't USE them in instructions
    assert "自称老夫" not in CHAT_PROMPT
    assert "自称'老夫'" not in CHAT_PROMPT


def test_all_prompts_present():
    """PERSONALITY_PROMPTS 字典包含所有三个模式"""
    assert "sassy" in PERSONALITY_PROMPTS
    assert "analyst" in PERSONALITY_PROMPTS
    assert "gentle" in PERSONALITY_PROMPTS


# ── Mode Distinction Tests ─────────────────────────────────────────

def test_analyst_uses_probability_language():
    """理性分析师模式应包含概率/数据相关的表述"""
    assert "概率" in SYSTEM_PROMPT_ANALYST or \
        "统计" in SYSTEM_PROMPT_ANALYST, \
        "理性分析师模式缺少概率或数据相关表述"
    assert "结构" in SYSTEM_PROMPT_ANALYST or \
        "分析" in SYSTEM_PROMPT_ANALYST, \
        "理性分析师模式缺少结构化表述"


def test_gentle_uses_empathy_language():
    """温柔陪伴者模式应包含共情相关的表述"""
    empathy_words = ["共情", "温暖", "接纳", "安全", "理解", "感受"]
    assert any(word in SYSTEM_PROMPT_GENTLE for word in empathy_words), \
        f"温柔陪伴者模式缺少共情表达, 需包含以下之一: {empathy_words}"


def test_sassy_uses_modern_language():
    """毒舌闺蜜模式应包含现代/直接的语言标记"""
    modern_words = ["闺蜜", "现代", "毒舌"]
    assert any(word in SYSTEM_PROMPT_SASSY for word in modern_words), \
        f"毒舌闺蜜模式缺少现代语言标记, 需包含以下之一: {modern_words}"


# ── Handler: Personality Mode Helpers ─────────────────────────────

def make_test_handler():
    """Helper: create a MessageHandler with all mocks for personality testing."""
    return MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=Mock(),
        llm=Mock(),
        dao=Mock(),
    )


def test_personality_default_is_auto_detect():
    """默认人格模式为None（自动检测）"""
    handler = make_test_handler()
    assert handler._get_personality_mode("new_user") is None


def test_personality_mode_set_and_get():
    """设置和获取性格模式"""
    handler = make_test_handler()
    handler._set_personality_mode("user1", "analyst")
    assert handler._get_personality_mode("user1") == "analyst"


def test_personality_invalid_mode_ignored():
    """设置无效性格模式时被忽略（None = 自动检测）"""
    handler = make_test_handler()
    handler._set_personality_mode("user1", "invalid_mode")
    assert handler._get_personality_mode("user1") is None


def test_personality_mode_persists_per_user():
    """不同用户可以有不同的性格模式"""
    handler = make_test_handler()
    handler._set_personality_mode("user_a", "analyst")
    handler._set_personality_mode("user_b", "gentle")
    assert handler._get_personality_mode("user_a") == "analyst"
    assert handler._get_personality_mode("user_b") == "gentle"


# ── Personality Switching Tests ───────────────────────────────────

def test_detect_switch_to_analyst():
    """检测切换到分析师模式"""
    handler = make_test_handler()
    assert handler._detect_personality_switch("用分析师模式") == "analyst"
    assert handler._detect_personality_switch("理性分析一下") == "analyst"


def test_detect_switch_to_gentle():
    """检测切换到温柔陪伴者模式"""
    handler = make_test_handler()
    assert handler._detect_personality_switch("温柔一点") == "gentle"
    assert handler._detect_personality_switch("换温柔模式") == "gentle"


def test_detect_switch_to_sassy():
    """检测切换到毒舌闺蜜模式"""
    handler = make_test_handler()
    assert handler._detect_personality_switch("换个风格") == "sassy"
    assert handler._detect_personality_switch("毒舌一点") == "sassy"


def test_detect_no_switch():
    """普通消息不应触发切换"""
    handler = make_test_handler()
    assert handler._detect_personality_switch("帮我看看八字") is None
    assert handler._detect_personality_switch("你好") is None


def test_personality_switch_via_process():
    """通过 process() 切换性格模式"""
    handler = make_test_handler()
    result = handler.process("用分析师模式", "test_user")
    assert handler._get_personality_mode("test_user") == "analyst"
    assert "分析师" in result or "模式" in result


def test_personality_switch_to_gentle():
    """通过 process() 温柔一点"""
    handler = make_test_handler()
    handler._set_personality_mode("test_user", "sassy")
    result = handler.process("温柔一点", "test_user")
    assert handler._get_personality_mode("test_user") == "gentle"
    assert "温柔" in result or "模式" in result


def test_personality_switch_to_sassy():
    """通过 process() 换个风格"""
    handler = make_test_handler()
    handler._set_personality_mode("test_user", "analyst")
    result = handler.process("换个风格", "test_user")
    assert handler._get_personality_mode("test_user") == "sassy"
    assert "闺蜜" in result or "模式" in result or "风格" in result


# ── Emotional Soothing Tests ──────────────────────────────────────

def test_soothe_for_neutral_message_empty():
    """中性消息不应触发安抚"""
    handler = make_test_handler()
    result = handler._soothe("你好")
    assert result == ""


def test_soothe_for_anxiety():
    """焦虑关键词应触发安抚"""
    handler = make_test_handler()
    result = handler._soothe("我最近特别焦虑, 工作压力太大了")
    assert result != ""
    assert "焦虑" in result or "压力" in result or "自然" in result


def test_soothe_for_confusion():
    """迷茫关键词应触发安抚"""
    handler = make_test_handler()
    result = handler._soothe("很迷茫不知道该怎么选")
    assert result != ""
    assert "选择" in result or "纠结" in result or "正常" in result


def test_soothe_for_sadness():
    """难过关键词应触发安抚"""
    handler = make_test_handler()
    result = handler._soothe("这段时间很难过, 一直在哭")
    assert result != ""
    assert "难过" in result or "理解" in result or "感情" in result


def test_soothe_for_anger():
    """生气关键词应触发安抚"""
    handler = make_test_handler()
    result = handler._soothe("我真的很生气, 受不了这样")
    assert result != ""
    assert "火大" in result or "深呼吸" in result or "化解" in result


def test_soothe_under_one_second():
    """情感安抚应在 1 秒内完成"""
    handler = make_test_handler()
    start = time.time()
    handler._soothe("我好纠结和焦虑, 不知道怎么办")
    elapsed = time.time() - start
    assert elapsed < 1.0, f"情感安抚耗时 {elapsed:.3f}s, 超过 1 秒"


def test_soothe_fires_before_analysis_in_process():
    """含情感关键词时, 安抚应出现在回复中"""
    handler = make_test_handler()
    handler.llm.chat = Mock(return_value=Mock(response="分析结果"))
    result = handler.process("我好纠结该不该跳槽", "user123")
    assert "纠结" in result or "选择" in result or "正常" in result


# ── Banned Words Filter Tests ─────────────────────────────────────

def test_filter_banned_words_removes_xiaoyou():
    """过滤掉'小友'"""
    handler = make_test_handler()
    result = handler._filter_banned_words("小友, 你的命盘显示...")
    assert "小友" not in result


def test_filter_banned_words_removes_laofu():
    """过滤掉'老夫'"""
    handler = make_test_handler()
    result = handler._filter_banned_words("让老夫为你分析一番")
    assert "老夫" not in result


def test_filter_banned_words_removes_laoxiu():
    """过滤掉'老朽'"""
    handler = make_test_handler()
    result = handler._filter_banned_words("老朽认为你的运势不错")
    assert "老朽" not in result


def test_filter_banned_words_clean_text_unchanged():
    """不含禁用词的消息不变"""
    handler = make_test_handler()
    text = "你的命盘显示今年运势不错"
    result = handler._filter_banned_words(text)
    assert result == text


def test_filter_banned_words_multiple():
    """多条禁用词同时过滤"""
    handler = make_test_handler()
    result = handler._filter_banned_words("小友, 让老夫为你分析")
    assert "小友" not in result
    assert "老夫" not in result
