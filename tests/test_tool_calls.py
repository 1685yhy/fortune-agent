"""批次 2 Task A1：GLM 免费模型裸格式工具输出适配。

glm-4-flash（glm_openai_completion 降级链）输出 OpenAI 风格裸格式：
`web_search\n{"query": "..."}`——工具名 + JSON 参数，无 <tool_calls> 包裹，
现有 JSON 工单解析器认不出（Task 7 实测证据）。

本文件覆盖新增的裸格式适配分支（只增不改：TOOL_CALL_RE / JSON 工单解析逻辑
一行未动，纯加兜底）：
- parse_glm_bare_format：并列新增的裸格式解析函数
- parse_tool_calls：主链兜底接入（JSON 工单 → 文本标签 → 裸格式）
- strip_tool_calls：可见文本剥离层（裸格式载荷不泄漏到用户/下一轮 LLM）
"""
import sys

sys.path.insert(0, "src")

from src.bot.tool_calls import (  # noqa: E402
    ToolCall,
    parse_glm_bare_format,
    parse_tool_calls,
    strip_tool_calls,
)


# ---- parse_glm_bare_format：裸格式解析 ----

def test_glm_bare_english_name():
    """裸格式英文 cap_id：web_search → 归一化「搜索」，JSON 参数解析为 params_obj。"""
    calls = parse_glm_bare_format('web_search\n{"query": "2026年教育政策"}')
    assert calls == [ToolCall(name="搜索", params_obj={"query": "2026年教育政策"})]
    assert calls[0].params == ""


def test_glm_bare_chinese_name():
    """裸格式中文名变体：搜索 → 直接命中注册表。"""
    calls = parse_glm_bare_format('搜索\n{"query": "今日运势"}')
    assert calls == [ToolCall(name="搜索", params_obj={"query": "今日运势"})]


def test_glm_bare_synonym_name():
    """同义词变体（联网）→ 归一为「搜索」（与文本标签路径一致）。"""
    calls = parse_glm_bare_format('联网\n{"query": "今日天气"}')
    assert calls == [ToolCall(name="搜索", params_obj={"query": "今日天气"})]


def test_glm_bare_name_no_params():
    """裸名字无参数（独占文末，截断场景）→ params 空串、params_obj={}（与 JSON 工单一致）。"""
    calls = parse_glm_bare_format("需要实时信息\n搜索")
    assert calls == [ToolCall(name="搜索", params_obj={})]


def test_glm_bare_invalid_json_params_empty():
    """JSON 参数解析失败 → params_obj={}（不误执行，校验层兜底）。"""
    calls = parse_glm_bare_format("搜索\n{不是JSON}")
    assert calls == [ToolCall(name="搜索", params_obj={})]


def test_glm_bare_nested_json_params():
    """嵌套 JSON 参数：平衡大括号完整捕获（不截断在一层）。"""
    calls = parse_glm_bare_format(
        'web_search\n{"query": "2026", "filters": {"region": "北京"}}')
    assert calls[0].params_obj == {
        "query": "2026", "filters": {"region": "北京"}}


def test_glm_bare_multiple_calls_order():
    """同回复多个裸格式调用：顺序保持。"""
    calls = parse_glm_bare_format(
        'web_search\n{"query": "北京天气"}\nbazi_chart\n{"text": "1990年5月20日 北京 男"}')
    assert [c.name for c in calls] == ["搜索", "排盘"]
    assert calls[0].params_obj == {"query": "北京天气"}
    assert calls[1].params_obj == {"text": "1990年5月20日 北京 男"}


def test_glm_bare_unknown_tool_skipped():
    """未知工具名 → 跳过不执行（与 JSON 工单同策略）。"""
    assert parse_glm_bare_format('no_such_tool\n{"q": "x"}') == []


def test_glm_bare_pure_text_no_false_positive():
    """纯文本回答 → 空列表（不误判）。"""
    assert parse_glm_bare_format("今天天气不错") == []
    assert parse_glm_bare_format(
        "好的，我根据命理知识为您分析：2026年事业运势总体向好，建议稳中求进。") == []


def test_glm_bare_mid_sentence_name_not_fired():
    """句中工具名（后随正文而非 JSON/文末）→ 不触发（防误判）。"""
    assert parse_glm_bare_format("建议您搜索 一下官方信息") == []
    assert parse_glm_bare_format("请在 web_search 里查一下") == []
    assert parse_glm_bare_format("搜索\n正文还有内容") == []


# ---- parse_tool_calls：主链接入回归 ----

def test_parse_tool_calls_glm_bare_english():
    """主链兜底：纯裸格式回复 → 解析出搜索工单（params_obj 供执行层校验）。"""
    calls = parse_tool_calls('好的，我来查。\nweb_search\n{"query": "2026年教育政策"}')
    assert [c.name for c in calls] == ["搜索"]
    assert calls[0].params_obj == {"query": "2026年教育政策"}
    assert calls[0].params == ""


def test_parse_tool_calls_glm_bare_chinese():
    """主链兜底：中文名裸格式 → 同样命中。"""
    calls = parse_tool_calls('搜索\n{"query": "今日运势"}')
    assert calls == [ToolCall(name="搜索", params_obj={"query": "今日运势"})]


def test_parse_tool_calls_json_workorder_unaffected():
    """回归：正常 JSON 工单 → 行为不变（JSON 优先，不受裸分支影响）。"""
    calls = parse_tool_calls(
        '<tool_calls>[{"tool": "web_search", "params": {"query": "北京天气"}}]'
        '</tool_calls>')
    assert [c.name for c in calls] == ["搜索"]
    assert calls[0].params_obj == {"query": "北京天气"}


def test_parse_tool_calls_text_tag_unaffected():
    """回归：文本标签调用 → 行为不变（params 原始字符串，params_obj None）。"""
    calls = parse_tool_calls("<tool_call>搜索: 北京天气</tool_call>")
    assert calls == [ToolCall(name="搜索", params="北京天气")]


def test_parse_tool_calls_pure_text_empty():
    """回归：纯文本回答 → 空列表（不误判）。"""
    assert parse_tool_calls("今天天气不错") == []


# ---- strip_tool_calls：可见文本剥离回归（载荷不泄漏） ----

def test_strip_glm_bare_removed():
    """裸格式调用块（含 JSON 载荷）→ 从可见文本剥离，正文保留。"""
    s = strip_tool_calls('好的。\nweb_search\n{"query": "1990年出生信息"}')
    assert s == "好的。"


def test_strip_glm_bare_chinese_removed():
    """中文名裸格式块同样剥离。"""
    s = strip_tool_calls('搜索 {"query": "北京天气"}以下是正文')
    assert s == "以下是正文"


def test_strip_glm_bare_pure_text_json_preserved():
    """纯文本中的 {JSON}（非注册工具名）→ 原样保留（防误删正文）。"""
    s = strip_tool_calls('他说 {"abc": 1}，需要确认')
    assert s == '他说 {"abc": 1}，需要确认'
