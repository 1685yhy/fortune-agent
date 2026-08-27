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
# (?i) 覆盖大写完整对 <TOOL_CALLS>...</TOOL_CALLS>（Task 3 复查）
_TOOL_CALLS_BLOCK_RE = re.compile(r'(?i)<tool_calls>(.*?)</tool_calls>', re.S)
# 未闭合块兜底（截断场景）：剥到行尾，JSON 载荷（含用户查询参数）绝不泄漏
_TOOL_CALLS_LINE_RE = re.compile(r'(?i)<tool_calls>[^\n]*')

# 裸标签残留（开口/悬挂闭合符）：strip 时兜底清掉（单复数 + 大小写全覆盖）
_TOOL_RESIDUE_RE = re.compile(r'</?tool_calls?>', re.I)

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
    - 原生 tool_use 块（Task 3B）：params_obj 有值 + tool_use_id 携带块 id（回传 tool_result 用）
    """
    name: str
    params: str = ""
    params_obj: Optional[dict] = None
    tool_use_id: str = ""


@dataclass
class ToolResult:
    """一次工具执行的结果，将注入 LLM 对话。"""
    name: str
    ok: bool
    text: str
    needs_info: bool = False  # True = 参数不全，应由 LLM 自然追问


# 工具注册表：从 capability_registry 投影（唯一事实源，消费方签名不变）。
# name → {key, desc, requires}；key 保留历史值（bazi/search/web/dream/fengshui/zeri/records/hehun）。
# 批次 2 E1：新增工具须在此补 name→key 数据键（TOOL_REGISTRY 投影硬依赖，
# 与批次 1 Task 7 加「查记录」同款加法；主链解析/分派逻辑零改动）。
from src.bot.capability_registry import CAPABILITIES, TOOL_NAME_BY_ID  # noqa: E402

_TOOL_KEYS = {
    "排盘": "bazi", "检索": "search", "搜索": "web", "解梦": "dream",
    "风水": "fengshui", "择日": "zeri", "查记录": "records", "合婚": "hehun",
    "起名": "naming", "流月流年": "fortune_cycle", "择业": "career_dir",
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


def parse_native_tool_use_blocks(content: list) -> List[ToolCall]:
    """解析 Anthropic 协议原生 tool_use 块（content 列表）。

    name 接受英文工具名或中文名，统一归一为注册表中文名；
    若模型输出的工具名不在注册表 → 跳过不执行（程序确认原则）。
    input 为 str → 包装为 {"text": ...}（与 JSON 工单路径一致）；
    非 dict/非 str → 兜底 {}。
    """
    calls = []
    for b in content:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        name = str(b.get("name", "")).strip()
        name = TOOL_NAME_BY_ID.get(name, name)
        if name not in TOOL_REGISTRY:
            continue
        params = b.get("input") or {}
        if isinstance(params, str):
            params = {"text": params}
        elif not isinstance(params, dict):
            params = {}
        calls.append(ToolCall(name=name, params_obj=params,
                              tool_use_id=str(b.get("id", ""))))
    return calls


# ---- Task A1: GLM 免费模型裸格式（OpenAI 风格降级输出） ----
# glm-4-flash（glm_openai_completion 降级链）不输出 <tool_calls> JSON 工单，
# 而是 OpenAI 风格裸格式：`web_search\n{"query": "..."}`（工具名 + JSON 参数）。
# 冒烟已验证形态（Task 7）：`(web_search|搜索)\s*\n?\s*(\{.*?\})`，此处一般化为
# 任意工具名 + 平衡大括号 JSON（一层嵌套不截断）。只增不改：TOOL_CALL_RE /
# JSON 工单解析逻辑一行未动，纯加兜底分支。
_GLM_BARE_RE = re.compile(
    r'(?P<name>[^\s<>{}\[\]:：]+)'      # 工具名（英文 cap_id / 中文名）
    r'(?:\s*\n?\s*'                     # 与参数块间的空白/换行
    r'(\{(?:[^{}]|\{[^{}]*\})*\})'      # JSON 参数块（支持一层嵌套）
    r'|\s*\n?(?=\s*$))',                # 或裸名字独占文末（截断场景，无参数）
    re.S,
)
# 剥离层同形正则：仅要求 name + JSON 块（裸名是正文单词，不得剥离）
_GLM_BARE_STRIP_RE = re.compile(
    r'(?P<name>[^\s<>{}\[\]:：]+)'
    r'\s*\n?\s*'
    r'(\{(?:[^{}]|\{[^{}]*\})*\})',
    re.S,
)


def parse_glm_bare_format(text: str) -> List[ToolCall]:
    """GLM 裸格式适配（Task A1）：`工具名\n{JSON 参数}` → ToolCall。

    - 工具名英文 cap_id（web_search）或中文名（搜索），经 _TOOL_SYNONYMS /
      TOOL_NAME_BY_ID 归一化命中注册表（与 JSON 工单同路径）；未知工具跳过
    - JSON 参数：json.loads 得 params_obj（解析失败 → 空 dict，params 空串，
      与 JSON 工单行为一致）；裸名字独占文末（截断场景）→ params_obj={}
    - 句中工具名（后随正文）不触发：防纯文本误判
    """
    calls = []
    for m in _GLM_BARE_RE.finditer(text):
        name = m.group("name").strip()
        name = _TOOL_SYNONYMS.get(name, name)
        name = TOOL_NAME_BY_ID.get(name, name)  # 英文 cap_id → 中文名
        if name not in TOOL_REGISTRY:
            continue  # 未知工具：不执行（与 JSON 工单同策略）
        try:
            params = json.loads(m.group(2) or "")
        except json.JSONDecodeError:
            params = {}
        if not isinstance(params, dict):
            params = {}
        calls.append(ToolCall(name=name, params_obj=params))
    return calls


def _bare_strip_cb(m: re.Match) -> str:
    """裸格式剥离回调：仅剥注册工具调用块，纯文本中的 {JSON} 原样保留。"""
    name = m.group("name").strip()
    name = _TOOL_SYNONYMS.get(name, name)
    name = TOOL_NAME_BY_ID.get(name, name)
    if name in TOOL_REGISTRY:
        return ""  # 工具调用块整块剥除（含 JSON 载荷，防泄漏到可见文本）
    return m.group(0)


def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析回复中的工具调用。JSON 工单优先，正则文本标签兜底。

    - JSON 工单：<tool_calls>[{"tool": "web_search", "params": {"query": "..."}}]</tool_calls>
    - 文本标签（兼容期保留）：<tool_call>搜索: 关键词</tool_call> / TOOL: 关键词
    - GLM 裸格式（Task A1）：`工具名\n{JSON 参数}`（glm-4-flash 降级链）
    - 都没有 → 返回空列表，调用方静默降级
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
    if not calls:
        # Task A1: GLM 裸格式兜底（降级链无 <tool_calls> 包裹，最后一道）
        calls = parse_glm_bare_format(text)
    return calls


def strip_tool_calls(text: str) -> str:
    """去掉回复中的工具调用标记，保留其余文字（用户可见部分）。

    兜底四层：JSON 工单块 → 文本标签/截断残留 → GLM 裸格式 → 裸标签符。
    """
    if not text:
        return text
    s = _TOOL_CALLS_BLOCK_RE.sub("", text)
    s = _TOOL_CALLS_LINE_RE.sub("", s)
    s = TOOL_CALL_RE.sub("", s)
    # Task A1: GLM 裸格式（name+JSON 参数块）剥离——仅剥注册工具，纯文本 JSON 保留
    s = _GLM_BARE_STRIP_RE.sub(_bare_strip_cb, s)
    s = _TOOL_RESIDUE_RE.sub("", s)
    return s.strip()
