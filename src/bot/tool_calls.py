"""AI 原生对话系统 — <tool_call> 标签解析与工具注册表（方案 3.2 / 3.3）。

LLM 在回复中以自然语言输出工具需求，例如：

    <tool_call>排盘: 1990年5月20日 午时 北京 男</tool_call>

系统层解析标签 → 调用对应引擎 → 结果以 system 消息注入对话 → LLM 基于结果继续生成。

- 防循环：最多 MAX_TOOL_ITERATIONS 次工具迭代
- 解析失败：静默降级（直接返回原文）
- 工具调用失败：错误信息注入 system 提示，不影响对话
"""
import re
from dataclasses import dataclass
from typing import List

# 支持的工具体系（方案 3.3 工具表 + 阶段 5 网络检索）
TOOL_CALL_RE = re.compile(
    r'<tool_call>\s*(排盘|检索|解梦|风水|择日|搜索)\s*[:：]\s*(.*?)</tool_call>',
    re.S,
)

# 防循环：最多 2 次工具迭代
MAX_TOOL_ITERATIONS = 2

# 搜索工具不可用时的降级提示（C6）：执行失败不再静默返回空/假装查不到，
# 而是把"搜索不可用"以 system 提示注入下一轮 LLM，让它明确告知用户实时信息受限
SEARCH_UNAVAILABLE_HINT = (
    "你刚才想用的网络搜索（实时信息）暂不可用。请明确告知用户："
    "「实时信息暂不可用，以下按命理知识分析」。"
    "不要假装搜索成功，不要编造信息或出处，继续基于命理知识完成回答。"
)

# 古籍检索不可用/未命中时的降级提示（回答纪律 B4：检索不可用要明说，不静默、不装）
RETRIEVAL_UNAVAILABLE_HINT = (
    "本次古籍检索未命中或暂不可用。请明确告知用户："
    "「相关典籍暂未查到，以下按命理知识分析」。"
    "不要假装有依据，不要编造古籍出处，基于自己的命理知识诚实作答。"
)


@dataclass
class ToolCall:
    """一条解析出的工具调用。"""
    name: str
    params: str


@dataclass
class ToolResult:
    """一次工具执行的结果，将注入 LLM 对话。"""
    name: str
    ok: bool
    text: str
    needs_info: bool = False  # True = 参数不全，应由 LLM 自然追问


# 工具注册表：name → 描述。引擎不可用时由 handler 运行时标注 unavailable
# （执行时返回"暂不可用"错误注入，prompt 层面不宣传不可用工具）。
TOOL_REGISTRY = {
    "排盘": {
        "key": "bazi",
        "desc": "输入出生信息（年月日时、地点、性别）的自然语言描述，输出四柱十神大运流年",
        "requires": "出生年月日时、出生地点、性别",
    },
    "检索": {
        "key": "search",
        "desc": "输入搜索关键词，输出古籍原文 Top5。只引用与用户问题直接相关的内容，不相关忽略",
        "requires": "搜索关键词",
    },
    "搜索": {
        "key": "web",
        "desc": "输入关键词，输出网络搜索结果（带来源 URL，Top 3-5，补充最新/社会信息）",
        "requires": "搜索关键词",
    },
    "解梦": {
        "key": "dream",
        "desc": "输入梦境描述，输出象征分析+古籍匹配",
        "requires": "梦境描述",
    },
    "风水": {
        "key": "fengshui",
        "desc": "输入房屋坐向/布局描述，输出吉凶判断+化解建议",
        "requires": "房屋坐向（如：坐北朝南）",
    },
    "择日": {
        "key": "zeri",
        "desc": "输入事项+日期，输出推荐吉日",
        "requires": "具体日期（如 2026年8月15日）+ 用途",
    },
}


def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析回复中的全部 <tool_call> 标签。

    解析失败（无标签/格式错误）返回空列表，调用方静默降级。
    """
    if not text:
        return []
    calls = []
    for m in TOOL_CALL_RE.finditer(text):
        name = m.group(1).strip()
        params = m.group(2).strip()
        if not params:
            params = ""
        calls.append(ToolCall(name=name, params=params))
    return calls


def strip_tool_calls(text: str) -> str:
    """去掉回复中的 <tool_call> 标签，保留其余文字（用户可见部分）。"""
    if not text:
        return text
    return TOOL_CALL_RE.sub("", text).strip()
