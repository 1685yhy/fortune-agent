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
    "数字吉凶": "num_omen",
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


# ══════════════════════════════════════════════════════════════════════
# k33/A3：GLM 降级链格式漂移适配（**纯加法**：既有各层一行未动，本层只在
# 它们零命中时兜底；剥离层复用同一 span 计算，解析到即剥掉，载荷不漏）
#
# 实录形态（批次 2 B3 smoke + k33 glm-4-flash 探针实测，2026-09-12）：
#   1) 【web_search】\n{"query": …}                全角方括号包裹工具名
#   2) web_search\n```json\n{"query": …}\n```      markdown 代码围栏包裹 JSON
#   3) 【web_search】\n```json\n{…}\n```            两者叠加
#   4) 稍等一下。web_search\n{"关键词": …}          散文前缀与工具名同行粘连
#   5) 搜索\n"2026年新能源…"                        参数为裸字符串（无大括号）
# 保守边界（防纯文本误判）：
#   - 载荷必须是 JSON 对象（允许一层嵌套）或裸引号串，且紧跟名字之后
#     （中间只允许空白/换行/```json 围栏）；
#   - 散文粘连形态（名字带前缀）**只认 JSON 对象载荷**，不认裸引号；
#   - 名字须解析为注册工具（后缀匹配取最长：散文前缀与工具名粘连时，
#     工具名一定在结尾处，如 "稍等一下。web_search" → web_search）。
# ══════════════════════════════════════════════════════════════════════

# 名字候选 + 可选围栏 + 载荷（对象优先，其次裸引号串）
_GLM_WRAP_JSON_OBJ = r'\{(?:[^{}]|\{[^{}]*\})*\}'
_GLM_WRAP_JSON_STR = r'"[^"\n]{1,120}"'
_GLM_WRAP_CALL_RE = re.compile(
    r'(?P<name>【\s*[^\s【】]{1,32}\s*】|[^\s<>{}\[\]:：]{1,64})'   # 【名】 或 裸名（可带前缀）
    r'[ \t]*[:：]?[ \t]*\n?[ \t]*'
    r'(?:```[ \t]*(?:json|JSON)?[ \t]*\n?[ \t]*)?'                # 可选 ```json 围栏
    r'(?P<payload>' + _GLM_WRAP_JSON_OBJ + r'|' + _GLM_WRAP_JSON_STR + r')'
    r'[ \t]*\n?[ \t]*(?:```)?',                                     # 可选围栏闭合
)
_GLM_WRAP_OPEN_RE = re.compile(r'^【\s*([^\s【】]{1,32})\s*】$')


def _resolve_glm_wrapped_name(raw: str):
    """漂移形态的名字归一 → (注册表中文名, 是否「干净名」, 名字后缀起始偏移)。

    - 干净名 = 【工具名】包裹形态，或裸名直接命中注册表（含 cap_id/同义词）
      —— 载荷为裸引号串时只认干净名（防误伤）；偏移 0（整段都是工具名）；
    - 散文前缀粘连（"稍等一下。web_search"）→ 取结尾处最长的已注册别名；
      偏移 = 别名在候选串内的起点（剥离时只从别名处切，散文前缀留给用户）；
    - 未命中 → ("", False, 0)（不执行、不剥离——与既有各层同策略）。
    """
    cand = (raw or "").strip()
    if not cand:
        return "", False, 0
    m = _GLM_WRAP_OPEN_RE.match(cand)
    if m:  # 【工具名】
        inner = m.group(1).strip()
        for c in (inner, _TOOL_SYNONYMS.get(inner, inner),
                  TOOL_NAME_BY_ID.get(inner, inner)):
            if c in TOOL_REGISTRY:
                return c, True, 0
        return "", False, 0
    # 裸名：直接命中 → 干净
    for c in (cand, _TOOL_SYNONYMS.get(cand, cand), TOOL_NAME_BY_ID.get(cand, cand)):
        if c in TOOL_REGISTRY:
            return c, True, 0
    # 散文前缀粘连：工具名一定在结尾处 → 后缀最长匹配
    aliases = sorted(
        list(TOOL_REGISTRY) + list(TOOL_NAME_BY_ID) + list(_TOOL_SYNONYMS),
        key=len, reverse=True)
    for a in aliases:
        if a and cand.endswith(a):
            norm = _TOOL_SYNONYMS.get(a, a)
            norm = TOOL_NAME_BY_ID.get(norm, norm)
            if norm in TOOL_REGISTRY:
                return norm, False, (len(cand) - len(a)) + (len(raw) - len(raw.lstrip()))
    return "", False, 0


def _glm_wrapped_spans(text: str):
    """扫出漂移形态工具调用块 → [(start, end, name, payload), ...]（解析/剥离同源）。

    start 已按名字偏移校正：散文前缀（"稍等一下。"）不随块一起剥掉，
    只切工具名与载荷（防吞用户可见正文）。
    """
    out = []
    if not text:
        return out
    for m in _GLM_WRAP_CALL_RE.finditer(text):
        name, clean, off = _resolve_glm_wrapped_name(m.group("name"))
        if not name:
            continue  # 未知工具：不执行（剥离层同样跳过，纯文本零改动）
        payload = m.group("payload")
        # 防误伤：散文前缀粘连名（clean=False）只认 JSON 对象载荷——
        # 裸引号串太容易撞正文（"……搜索\n"关键词"" 这类）。
        if not clean and not payload.startswith("{"):
            continue
        out.append((m.start("name") + off, m.end(), name, payload))
    return out


# 形态 6：散文参数（探针实录最常形态）——工具名独占一行 + 下一行自然语言参数：
#   择日
#   搬家,2026年9月15日
# 严格边界（防正文误判，宁可漏判不可误判）：
#   - 名字独占一行（行首，无散文前缀粘连），必须是注册工具（/【】包裹）；
#   - 该行必须是块起点（文本开头或前面是空行）——正文段落里"最后一行恰好是
#     工具名"不触发；
#   - 参数行紧跟其后（无空行）、单行、≤60 字、不含句子结束符（。！？；;）——
#     散文句必带标点，参数行是逗号分隔的短语。
_GLM_PROSE_CALL_RE = re.compile(
    r'(?:^|\n[ \t]*\n)[ \t]*'
    r'(?P<name>【[^\s【】]{1,32}】|[^\s<>{}\[\]:：,，、]{1,32})'
    r'[ \t]*[:：]?[ \t]*\n'
    r'(?P<params>[^\n]{1,60}?)'
    r'(?=\n|$)')
_GLM_PROSE_BAD_PUNCT = "。！？!?；;"


def _glm_prose_param_spans(text: str):
    """散文参数形态 → [(start, end, name, params), ...]（解析/剥离同源）。"""
    out = []
    if not text:
        return out
    for m in _GLM_PROSE_CALL_RE.finditer(text):
        name, clean, off = _resolve_glm_wrapped_name(m.group("name"))
        if not name or not clean:
            continue  # 未注册工具 / 粘连名 → 不触发（保守）
        params = m.group("params").strip()
        if not params:
            continue
        if any(ch in params for ch in _GLM_PROSE_BAD_PUNCT):
            continue  # 带句末标点 = 散文句，不是参数
        if params[0] in '{"[':
            continue  # JSON/引号载荷残块（截断）→ 不当散文参数执行（防误调）
        out.append((m.start("name") + off, m.end(), name, params))
    return out


def parse_glm_wrapped_format(text: str) -> List[ToolCall]:
    """GLM 漂移格式适配（k33/A3，末道兜底）：见上方形态说明。

    与既有 GLM 裸格式同策略：名字经 _TOOL_SYNONYMS / TOOL_NAME_BY_ID 归一后
    必须命中注册表（未知工具不执行）；JSON 解析失败 → params_obj={}（执行层
    按参数不全自然追问）；params 不合法类型（非 dict）→ {}。
    散文参数形态（形态 6）→ 参数原文进 {"text": …}（执行器全部吃自然语言文本）。
    """
    calls = []
    for _s, _e, name, payload in _glm_wrapped_spans(text):
        try:
            params = json.loads(payload)
        except json.JSONDecodeError:
            params = {}
        if isinstance(params, str):
            params = {"text": params}      # 裸引号串载荷
        elif not isinstance(params, dict):
            params = {}
        calls.append(ToolCall(name=name, params_obj=params))
    if calls:
        return calls
    # 形态 6：仅在前述（JSON 载荷）形态零命中时才试散文参数形态
    return [ToolCall(name=name, params_obj={"text": params})
            for _s, _e, name, params in _glm_prose_param_spans(text)]


def strip_glm_wrapped_format(text: str) -> str:
    """剥离漂移形态工具调用块（与 parse 同一 span 计算；未命中名 → 原文保留）。"""
    spans = _glm_wrapped_spans(text)
    if not spans:
        # 与解析层同源：JSON 载荷形态零命中时按散文参数形态剥（载荷不漏）
        spans = _glm_prose_param_spans(text)
    if not spans:
        return text
    parts, last = [], 0
    for start, end, _name, _payload in sorted(spans):
        if start < last:
            continue
        parts.append(text[last:start])
        last = end
    parts.append(text[last:])
    return "".join(parts)


def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析回复中的工具调用。JSON 工单优先，正则文本标签兜底。

    - JSON 工单：<tool_calls>[{"tool": "web_search", "params": {"query": "..."}}]</tool_calls>
    - 文本标签（兼容期保留）：<tool_call>搜索: 关键词</tool_call> / TOOL: 关键词
    - GLM 裸格式（Task A1）：`工具名\n{JSON 参数}`（glm-4-flash 降级链）
    - GLM 漂移格式（k33/A3，**新增末道**）：【工具名】包裹 / ```json 围栏 /
      散文前缀同行粘连 / 裸引号参数（既有各层零命中时才跑）
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
    if not calls:
        # k33/A3: GLM 格式漂移兜底（【】包裹 / 围栏 / 散文粘连 / 裸引号）
        calls = parse_glm_wrapped_format(text)
    return calls


def strip_tool_calls(text: str) -> str:
    """去掉回复中的工具调用标记，保留其余文字（用户可见部分）。

    兜底五层：JSON 工单块 → 文本标签/截断残留 → GLM 裸格式 → GLM 漂移格式
    （k33/A3）→ 裸标签符。
    """
    if not text:
        return text
    s = _TOOL_CALLS_BLOCK_RE.sub("", text)
    s = _TOOL_CALLS_LINE_RE.sub("", s)
    s = TOOL_CALL_RE.sub("", s)
    # Task A1: GLM 裸格式（name+JSON 参数块）剥离——仅剥注册工具，纯文本 JSON 保留
    s = _GLM_BARE_STRIP_RE.sub(_bare_strip_cb, s)
    # k33/A3: GLM 漂移格式剥离（同一 span 计算 → 解析到了必剥掉，载荷不漏）
    s = strip_glm_wrapped_format(s)
    s = _TOOL_RESIDUE_RE.sub("", s)
    return s.strip()
