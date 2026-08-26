"""AI 原生对话系统 — <tool_call> 标签解析与工具注册表（方案 3.2 / 3.3）。

LLM 在回复中以自然语言输出工具需求，例如：

    <tool_call>排盘: 1990年5月20日 午时 北京 男</tool_call>

系统层解析标签 → 调用对应引擎 → 结果以 system 消息注入对话 → LLM 基于结果继续生成。

- 防循环：最多 MAX_TOOL_ITERATIONS 次工具迭代
- 解析失败：静默降级（直接返回原文）
- 工具调用失败：错误信息注入 system 提示，不影响对话
"""
import json
import re
from dataclasses import dataclass
from typing import List, Optional

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
# 结构化工单块：<tool_calls>[{"tool": "...", "params": {...}}]</tool_calls>
_TOOL_CALLS_BLOCK_RE = re.compile(r'<tool_calls>(.*?)</tool_calls>', re.S)

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
    """一条解析出的工具调用。

    - 结构化工单（JSON 工单块）：params_obj 有值，params 为空串，执行前需校验+序列化
    - 文本标签（正则兜底）：params 有值，params_obj 为 None（旧行为，不校验直接执行）
    """
    name: str
    params: str = ""
    params_obj: Optional[dict] = None


@dataclass
class ToolResult:
    """一次工具执行的结果，将注入 LLM 对话。"""
    name: str
    ok: bool
    text: str
    needs_info: bool = False  # True = 参数不全，应由 LLM 自然追问


# 工具注册表：从 capability_registry 投影（唯一事实源，消费方签名不变）。
# name → {key, desc, requires}；key 保留历史值（bazi/search/web/dream/fengshui/zeri/records）。
from src.bot.capability_registry import CAPABILITIES, TOOL_NAME_BY_ID  # noqa: E402

_TOOL_KEYS = {
    "排盘": "bazi", "检索": "search", "搜索": "web", "解梦": "dream",
    "风水": "fengshui", "择日": "zeri", "查记录": "records",
}
TOOL_REGISTRY = {
    c.name: {"key": _TOOL_KEYS[c.name], "desc": c.description,
             "requires": c.requires}
    for c in CAPABILITIES if c.cap_type == "tool"
}


def _parse_json_workorder(text: str) -> List[ToolCall]:
    """解析 JSON 工单块（JSON 优先，失败返回 [] 由正则兜底）。

    tool 字段接受英文 cap_id（如 "web_search"）或中文名（如 "搜索"），
    统一归一为注册表中文名（与文本标签路径的 ToolCall.name 一致）。
    """
    m = _TOOL_CALLS_BLOCK_RE.search(text)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    calls = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool", "")).strip()
        name = _TOOL_SYNONYMS.get(name, name)
        name = TOOL_NAME_BY_ID.get(name, name)  # 英文 cap_id → 中文名
        if name not in TOOL_REGISTRY:
            continue  # 未知工具：不执行（strip 路径仍会移除）
        params = item.get("params", {})
        if isinstance(params, str):
            params = {"text": params}
        elif not isinstance(params, dict):
            params = {}
        calls.append(ToolCall(name=name, params_obj=params))
    return calls


def serialize_params(params: dict) -> str:
    """结构化工单参数 → 执行器文本桥接（执行器全部吃自然语言文本）。

    单键直接取值；多键 "k: v" 换行拼接（通用兜底，够用即可）。
    """
    if not params:
        return ""
    if len(params) == 1:
        v = next(iter(params.values()))
        if isinstance(v, (str, int, float)):
            return str(v).strip()
    return "\n".join(f"{k}: {v}" for k, v in params.items())


def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析回复中的工具调用。JSON 工单优先，正则文本标签兜底。

    - JSON 工单：<tool_calls>[{"tool": "web_search", "params": {"query": "..."}}]</tool_calls>
    - 文本标签（兼容期保留）：<tool_call>搜索: 关键词</tool_call> / TOOL: 关键词
    - 两者都失败/都没有 → 返回空列表，调用方静默降级
    """
    if not text:
        return []
    calls = _parse_json_workorder(text)
    if calls:
        return calls
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
    """去掉回复中的工具调用标记，保留其余文字（用户可见部分）。

    兜底三层：JSON 工单块 → 文本标签/截断残留 → 裸标签符。
    """
    if not text:
        return text
    s = _TOOL_CALLS_BLOCK_RE.sub("", text)
    s = TOOL_CALL_RE.sub("", s)
    s = _TOOL_RESIDUE_RE.sub("", s)
    return s.strip()
