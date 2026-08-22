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
# 格式放宽（2026-08-17 真机修复·TOOL 标签残留）：
#   - 前缀兼容 <tool_call>（不区分大小写）与 TOOL:（冒号必需，防英文 "tool for…" 误伤）
#   - 标签内工具名不限列表（未知工具名也 strip 掉，不执行）；TOOL: 前缀任意名同理
#   - 分隔符兼容 [:：\s]（无冒号空格分隔也认）
#   - 无 </tool_call> 的残留（截断/换行/末尾）同样匹配：参数只吃到行尾/闭合标签，
#     绝不跨行吞掉后续正文
TOOL_CALL_RE = re.compile(
    r'(?:'
    r'(?i:<tool_call>)\s*(?P<name1>[^\s<>{}\[\]:：]+)'
    r'|(?i:TOOL)\s*[:：]\s*(?P<name2>[^\s<>{}\[\]:：]+)'
    r')'
    r'(?P<sep>\s*[:：]\s*|\s+)'
    r'(?P<params>[^<\n]*)(?i:</tool_call>)?',
)
# 裸标签残留（开口/悬挂闭合符）：strip 时兜底清掉
_TOOL_RESIDUE_RE = re.compile(r'</?tool_call>', re.I)

# 搜索类工具同义词 → 归一为注册表名「搜索」（LLM 偶尔写 联网/网络）
_TOOL_SYNONYMS = {
    "联网": "搜索",
    "网络": "搜索",
    "上网": "搜索",
    "网上": "搜索",
}

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
        "desc": "输入场景+时间范围，输出3个推荐吉日（宜忌/吉时/方位/一句理由）。"
               "场景支持：嫁娶/搬家/开业/晋升/出行/提车/签约；时间可写下个月、下周、具体日期",
        "requires": "场景（嫁娶/搬家/开业/晋升/出行/提车/签约）+ 时间范围（下个月/下周/具体日期）",
    },
}


def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析回复中的全部 <tool_call> 标签。

    解析失败（无标签/格式错误）返回空列表，调用方静默降级。
    放宽后的正则会匹配到未知工具名（标签内任意名）——只把注册表内
    可执行的工具返回（未知名会被 strip 掉但不执行，防无意义迭代）。
    """
    if not text:
        return []
    calls = []
    for m in TOOL_CALL_RE.finditer(text):
        name = (m.group("name1") or m.group("name2") or "").strip()
        name = _TOOL_SYNONYMS.get(name, name)
        params = (m.group("params") or "").strip()
        if name not in TOOL_REGISTRY:
            continue  # 未知工具：不执行（strip 路径仍会移除）
        calls.append(ToolCall(name=name, params=params))
    return calls


def strip_tool_calls(text: str) -> str:
    """去掉回复中的 <tool_call> 标签，保留其余文字（用户可见部分）。

    兜底两层：
      1) TOOL_CALL_RE 匹配完整调用/截断残留（<tool_call> / TOOL: 前缀均可）；
      2) 裸标签符（<tool_call> / </tool_call> 悬挂残留）单独清除。
    """
    if not text:
        return text
    s = TOOL_CALL_RE.sub("", text)
    s = _TOOL_RESIDUE_RE.sub("", s)
    return s.strip()
