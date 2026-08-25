"""E2-1 对话消息卡片化——服务端卡片标记生成（纯函数单测）。

覆盖 Task E2-1 brief §测试要求：
- detect_card_type：5 类场景各至少 1 用例（排盘工具→paipan / 择吉→zeri /
  分析意图→yunshi / 直读→data / 普通闲聊→None）
- 误包防护：含"财运"关键词的闲聊（"我财运不错"）无工具/意图记录不得判
  yunshi（判定依赖记录而非关键词）
- wrap_card：标题转义（引号/换行）、正文含 [/card] 字样不破坏结构、
  尾部引导语拆分、防重复包装
"""
import pytest

from src.bot.card_mark import (
    CARD_TYPES,
    detect_card_type,
    split_tail,
    wrap_card,
)


# ── detect_card_type：5 类场景 ──────────────────────────────────

def test_detect_tool_paipan():
    """执行过排盘工具（ToolResult 记录）→ paipan"""
    assert detect_card_type("这是排盘结果…", tool_calls=[{"type": "排盘", "hit": True}]) == "paipan"
    # 兼容纯字符串记录 / ToolResult 风格对象
    assert detect_card_type("x", tool_calls=["排盘"]) == "paipan"
    assert detect_card_type("x", tool_calls=[type("R", (), {"name": "排盘"})()]) == "paipan"


def test_detect_tool_zeri():
    """执行过择吉（择日）工具 → zeri"""
    assert detect_card_type("这是择日结果…", tool_calls=[{"type": "择日", "hit": True}]) == "zeri"


def test_detect_tool_failed_not_card():
    """失败的排盘/择日调用（hit=False，如「排盘」工具暂不可用）→ 不得判卡

    工具执行失败（_run_tool_loop 无条件记录 hit=False）后的降级文案
    （"「排盘」工具暂不可用，请直接与用户聊天。"）绝不能包成命盘卡片——
    宁可漏包不可误包。
    """
    assert detect_card_type(
        "「排盘」工具暂不可用，请直接与用户聊天。",
        tool_calls=[{"type": "排盘", "hit": False}]) is None
    assert detect_card_type(
        "「择日」工具暂不可用，请直接与用户聊天。",
        tool_calls=[{"type": "择日", "hit": False}]) is None
    # 失败排盘 + 成功择日混跑 → 只算成功调用（zeri）
    assert detect_card_type("正文…", tool_calls=[
        {"type": "排盘", "hit": False}, {"type": "择日", "hit": True}]) == "zeri"
    # 兼容性：dict 记录无 hit 键 / 纯字符串记录 → 维持原行为（视为已执行）
    assert detect_card_type("x", tool_calls=[{"type": "排盘"}]) == "paipan"
    assert detect_card_type("x", tool_calls=["排盘"]) == "paipan"


def test_detect_scenario_yunshi():
    """分析意图场景（career/wealth/love/health）→ yunshi"""
    for s in ("career", "wealth", "love", "health"):
        assert detect_card_type("你的分析正文…", scenario=s) == "yunshi", s


def test_detect_direct_read_data():
    """存量直读（档案/解梦/历史/签/晨笺…）→ data"""
    assert detect_card_type("你的档案（命主：小明）：…", direct_read=True) == "data"


def test_detect_knowledge_signature():
    """D9 无档案知识兜底签名文案 → knowledge"""
    reply = ("关于「财运」的通用命理常识：命理讲「财为养命之源」…\n\n"
             "好的，想帮你看看八字～请告诉我：出生年月日…")
    assert detect_card_type(reply) == "knowledge"
    assert detect_card_type("关于八字命理的基本常识：八字由出生年月日时…") == "knowledge"


def test_detect_plain_chat_none():
    """普通闲聊（无任何记录）→ None，不包卡片"""
    assert detect_card_type("你好呀，今天天气不错", tool_calls=[]) is None
    assert detect_card_type("") is None
    assert detect_card_type(None) is None


# ── 误包防护 ────────────────────────────────────────────────────

def test_wealth_keyword_casual_chat_not_yunshi():
    """含"财运"关键词的闲聊（"我财运不错"）无工具/场景/直读记录 → 不得判 yunshi"""
    reply = "我财运不错，最近赚了点钱，就是有点累"
    assert detect_card_type(reply) is None
    assert detect_card_type(reply, tool_calls=None, scenario=None, direct_read=False) is None


def test_scenario_requires_record_not_keyword():
    """yunshi 判定依赖场景记录：仅回复文本含场景词（无记录）不得判 yunshi"""
    assert detect_card_type("关于财运的建议：多储蓄少投机") is None
    assert detect_card_type("事业方面要稳扎稳打", scenario=None) is None


# ── 判定优先级 ──────────────────────────────────────────────────

def test_detect_priority_tool_over_scenario():
    """优先级：工具记录（排盘）> 场景（yunshi）"""
    ctx = dict(tool_calls=[{"type": "排盘", "hit": True}], scenario="wealth")
    assert detect_card_type("正文…", **ctx) == "paipan"


def test_detect_engine_path_paipan():
    """意图路径引擎直跑：排盘（无 <tool_call> 记录）→ paipan"""
    assert detect_card_type("正文…", ran_paipan=True) == "paipan"


def test_detect_engine_path_zeri():
    """意图路径引擎直跑：择日 → zeri"""
    assert detect_card_type("正文…", ran_zeri=True) == "zeri"


def test_detect_engine_scenario_wins_over_ran_paipan():
    """场景问句（今年财运）回复以分析为主体 → yunshi 优先于 ran_paipan"""
    assert detect_card_type("正文…", ran_paipan=True, scenario="wealth") == "yunshi"


def test_detect_direct_read_wins_over_knowledge_signature():
    """优先级：直读（data）> 知识签名（knowledge）"""
    reply = "关于「财运」的通用命理常识…"
    assert detect_card_type(reply, direct_read=True) == "data"


# ── wrap_card：标记格式 / 标题转义 / 防破坏 ─────────────────────

def test_wrap_card_format():
    """契约格式：[card:类型 title="标题"]\n正文\n[/card]"""
    wrapped = wrap_card("🧧 **八字命盘**\n\n分析…", "paipan")
    assert wrapped == '[card:paipan title="我的命盘"]\n🧧 **八字命盘**\n\n分析…\n[/card]'


def test_wrap_card_title_escape():
    """标题含双引号/换行 → 转义，不破坏 title="..." 结构"""
    wrapped = wrap_card("正文", "paipan", title='我的"命"盘\n第二行')
    assert 'title="我的\\"命\\"盘\\n第二行"' in wrapped
    assert wrapped.startswith('[card:paipan title="')
    assert wrapped.endswith("[/card]")


def test_wrap_card_custom_title_win():
    """显式 title 覆盖类型默认标题"""
    wrapped = wrap_card("正文", "paipan", title="我的专属命盘")
    assert 'title="我的专属命盘"' in wrapped


def test_wrap_card_omit_title():
    """data/knowledge 无默认标题 → 省略 title 属性（端上回退默认）"""
    assert wrap_card("你的档案：…", "data") == "[card:data]\n你的档案：…\n[/card]"
    assert wrap_card("常识正文", "knowledge") == "[card:knowledge]\n常识正文\n[/card]"


def test_wrap_card_body_with_closing_marker_skips():
    """正文含 [/card]（或 [card:）字样时不得破坏结构 → 原样返回，宁可漏包"""
    reply = "正文里提到了 [/card] 这个词"
    assert wrap_card(reply, "paipan") == reply
    reply2 = "有人说 [card:paipan] 是格式"
    assert wrap_card(reply2, "paipan") == reply2


def test_wrap_card_already_marked_skips():
    """回复已含卡片标记（防重复包装/缓存回环）→ 原样返回"""
    marked = '[card:paipan title="我的命盘"]\n正文\n[/card]'
    assert wrap_card(marked, "paipan") == marked


def test_wrap_card_unknown_type_skips():
    """未知卡片类型 → 原样返回"""
    assert wrap_card("正文", "unknown_type") == "正文"


def test_wrap_card_skips_error_copy():
    """错误/失败文案（⚠️ / 暂不可用 / 引擎执行失败）→ 不包装，原样返回

    覆盖流程异常降级（"⚠️ 服务暂时不可用…"）与工具失败（"「排盘」工具暂不可用"、
    "排盘引擎执行失败"）两类误包面——宁可漏包不可误包。
    """
    degrade = "⚠️ 服务暂时不可用：Object of type Mock is not JSON serializable\n\n请稍后再试或换一种命理方式。"
    assert wrap_card(degrade, "paipan") == degrade
    tool_down = "「排盘」工具暂不可用，请直接与用户聊天。"
    assert wrap_card(tool_down, "paipan") == tool_down
    engine_fail = "排盘引擎执行失败：connect timeout"
    assert wrap_card(engine_fail, "paipan") == engine_fail
    zeri_fail = "择日引擎执行失败：internal error"
    assert wrap_card(zeri_fail, "zeri") == zeri_fail


# ── 尾部引导语拆分 ──────────────────────────────────────────────

def test_wrap_card_tail_feedback_outside():
    """反馈语（可回复「准」/👍👎）拆到卡片外"""
    body = "🧧 **八字命盘**\n\n分析正文…"
    fb = "———\n这个分析对你有帮助吗？可回复「准」或「不准」告诉我"
    wrapped = wrap_card(f"{body}\n\n{fb}", "paipan")
    assert wrapped == f'[card:paipan title="我的命盘"]\n{body}\n[/card]\n\n{fb}'


def test_wrap_card_tail_stacked_outside():
    """叠加尾部（下文引导 + 反馈语 + 版本页脚）全部拆到卡片外"""
    body = "分析正文…"
    followup = "💬 还想了解：要不要看看下个月的整体运势？"
    fb = "———\n💬 这个分析对你有帮助吗？👍 有帮助  👎 不太准"
    footer = "---\n解读版本: v5.0.0 | 生成时间: 2026-08-25T10:00:00+08:00\n同一八字同一问题，结果始终一致"
    reply = f"{body}\n\n{followup}\n\n{fb}\n\n{footer}"
    wrapped = wrap_card(reply, "yunshi")
    assert wrapped.startswith('[card:yunshi title="运势分析"]\n' + body)
    assert "\n[/card]\n\n" in wrapped
    assert "还想了解" in wrapped and "👍" in wrapped and "解读版本" in wrapped
    # 卡片内不含任何尾部内容
    inner = wrapped.split("[/card]")[0]
    assert "还想了解" not in inner and "———" not in inner and "解读版本" not in inner


def test_wrap_card_no_tail_whole_wrap():
    """拆不出已知尾部 → 整体包（宁整勿碎）"""
    reply = "🧧 **八字命盘**\n\n年柱：庚午 月柱：辛巳\n\n日主：乙木"
    wrapped = wrap_card(reply, "paipan")
    assert wrapped.endswith("[/card]")
    assert "日主：乙木" in wrapped


def test_split_tail_units():
    """split_tail：各类尾部模式拆分 + 无尾部不拆"""
    assert split_tail("正文") == ("正文", "")
    assert split_tail("正文\n\n———\n反馈") == ("正文", "———\n反馈")
    assert split_tail("正文\n\n💬 还想了解：继续吗") == ("正文", "💬 还想了解：继续吗")
    assert split_tail("正文\n\n---\n解读版本: v1") == ("正文", "---\n解读版本: v1")
    # 全是尾部 → 整体包（不拆空）
    assert split_tail("\n\n———\n反馈") == ("\n\n———\n反馈", "")


def test_wrap_card_empty_skips():
    """空回复原样返回"""
    assert wrap_card("", "paipan") == ""
    assert wrap_card(None, "paipan") is None


# ── 类型枚举契约（E2-2 端上依赖）───────────────────────────────

def test_card_types_enum():
    """类型枚举契约：paipan/yunshi/zeri/data/knowledge"""
    assert set(CARD_TYPES) == {"paipan", "yunshi", "zeri", "data", "knowledge"}
