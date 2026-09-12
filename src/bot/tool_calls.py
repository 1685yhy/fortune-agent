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
#
# k33 审查 I1 收紧（2026-09-12，审查实跑反例见 tests/test_k33_glm_drift_format.py）：
# 本层只在「**明确漂移标记** + **可解析载荷** + **注册工具名** +
# **块起点**」四条同时成立时命中——旧版把普通叙述错判成工具调用并真实执行：
#   反例 a) 我的建议：先看排盘\n{"年": …}      → 旧版后缀匹配认成「排盘」
#   反例 b) 想查实时信息就用一下搜索\n{"q": …}  → 同理（真外呼 web_search）
#   反例 c) 搜索\n"2026年运势"                  → 裸引号串与正文「怎么用搜索」不可区分
#
# 四条允许形态（A/B/C 为 JSON 载荷，D 为真实生产形态）：
#   A) 【web_search】\n{"query": …}（可叠加 ```json 围栏）
#   B) web_search\n```json\n{"query": …}\n```（围栏本身即标记）
#   C) 稍等一下。web_search\n{"关键词": …}（名字前一个字符必须是句末符）
#   D) 择日\n搬家,2026年9月15日（裸名独占一行 + **末行**散文参数）
# 收紧要点：
#   - **块起点**：块（含粘连名整体）前只允许行首/串首/句末符（。！？!?；;…），
#     名字前是别的字（"先看排盘"/"就用一下搜索"）一律不认；
#   - A/B/C 的**载荷必须是能 json.loads 成 dict 的对象**；裸引号串取消
#     （`搜索\n"2026年运势"` 与正文"怎么用搜索"不可区分）；
#   - **未知工具名不执行也不剥离**（与既有各层同策略）。
#
# k33 复审（2026-09-12）：真实 glm-4-flash 冒烟（`test_live_glm_zeri_output_parses`）
# 实录 **5/5 采样均为形态 D**（`择日\n搬家,2026年9月15日` 系）；I1 一度把形态 D
# 整体取消 → 真实冒烟红、D3 zeri 场景继续失败。复审裁定：**生产正确性优先**——
# 形态 D 以「更硬的护栏」放行（见 `_GLM_PROSE_CALL_RE` 注释），
# 其中与生产形态逐字节同构的审查反例 `排盘\n姓名,1990年3月5日` **不得不放行**
# （两者结构不可区分；对照矩阵见 tests/test_k33_glm_drift_format.py:TestReleasedTradeoff）。
# ══════════════════════════════════════════════════════════════════════

_GLM_DRIFT_JSON_OBJ = r'\{(?:[^{}]|\{[^{}]*\})*\}'
_GLM_DRIFT_FENCE = r'```[ \t]*(?:json|JSON)?[ \t]*\r?\n'
# 名字必须落在「行首/串首/句末符之后」（块起点）；句末符集合含中英文标点
_GLM_DRIFT_BOUNDARY = "。！？!?；;…"

# 形态 A：全角方括号包裹工具名（【web_search】），可带 ```json 围栏
_GLM_DRIFT_BRACKET_RE = re.compile(
    r'(?P<name>【[ \t]*[^\s【】]{1,32}[ \t]*】)'
    r'[ \t]*[:：]?[ \t]*\r?\n?[ \t]*'
    r'(?:' + _GLM_DRIFT_FENCE + r'[ \t]*)?'
    r'(?P<payload>' + _GLM_DRIFT_JSON_OBJ + r')'
    r'[ \t]*\r?\n?[ \t]*(?:```)?',
)
# 形态 B：裸名独占一行 + ```json 围栏 + JSON（围栏本身即明确漂移标记）
_GLM_DRIFT_FENCE_RE = re.compile(
    r'(?P<name>[^\s<>{}\[\]:：]{1,64})'
    r'[ \t]*[:：]?[ \t]*\r?\n[ \t]*'
    r'```[ \t]*(?:json|JSON)?[ \t]*\r?\n[ \t]*'
    r'(?P<payload>' + _GLM_DRIFT_JSON_OBJ + r')'
    r'[ \t]*\r?\n?[ \t]*(?:```)?',
)
# 形态 C：裸名（可带句末符收尾的散文前缀粘连）+ JSON 对象（无围栏）
_GLM_DRIFT_GLUED_RE = re.compile(
    r'(?P<name>[^\s<>{}\[\]:：]{1,64})'
    r'[ \t]*[:：]?[ \t]*\r?\n?[ \t]*'
    r'(?P<payload>' + _GLM_DRIFT_JSON_OBJ + r')',
)
_GLM_DRIFT_OPEN_RE = re.compile(r'^【\s*([^\s【】]{1,32})\s*】$')

# 形态 D（真实生产形态，复审回归修复）：裸工具名独占一行 + **末行**散文参数：
#   择日
#   搬家,2026年9月15日
# 护栏（把误判面压到最小；「注册工具名 + 参数可解析 + 块在消息末尾 + 只认一个块」
# 四条替代「形态取消」）：
#   - 名字行**行首锚定**（消息开头或空行之后）且**精确命中注册表**——不做后缀匹配
#     （"我的建议：先看排盘"这种同行粘连一律不认）、不认【】包裹（A/B 才用括号）；
#   - **参数行必须是消息最后一行**（其后只允许空白）——正文后续还有内容 → 不认
#     （`搜索\n关键词: 黄金价格\n以上。` 因此被拦）；
#   - 结构上**天然唯一**（只有一行能是"最后一行"）→ 不存在"多块吞正文"；
#   - 参数行 ≤60 字、含可执行字符（字母/数字/汉字，排除标点噪声）、
#     无句末标点（。！？!?；;）、不以 `{ " [` 开头（JSON/引号残块不当散文参数）。
_GLM_PROSE_CALL_RE = re.compile(
    r'(?:^|\n[ \t]*\n)[ \t]*'
    r'(?P<name>[^\s<>{}\[\]:：,，、【】]{1,32})'
    r'[ \t]*[:：]?[ \t]*\n'
    r'(?P<params>[^\n]{1,60}?)'
    r'[ \t]*(?:\n[ \t]*)*\Z',
)
_GLM_PROSE_BAD_PUNCT = "。！？!?；;"
_GLM_PROSE_PARAM_CHAR_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")
# 注册工具别名全集（后缀最长匹配用；含英文 cap_id 与同义词）
_GLM_DRIFT_ALIASES = None


def _drift_aliases() -> list:
    global _GLM_DRIFT_ALIASES
    if _GLM_DRIFT_ALIASES is None:
        _GLM_DRIFT_ALIASES = sorted(
            list(TOOL_REGISTRY) + list(TOOL_NAME_BY_ID) + list(_TOOL_SYNONYMS),
            key=len, reverse=True)
    return _GLM_DRIFT_ALIASES


def _normalize_drift_name(name: str) -> str:
    """别名 → 注册表中文名（同义词/cap_id 归一）；未注册 → ""。"""
    if not name:
        return ""
    for c in (name, _TOOL_SYNONYMS.get(name, name), TOOL_NAME_BY_ID.get(name, name)):
        if c in TOOL_REGISTRY:
            return c
    return ""


def _resolve_glm_drift_name(raw: str):
    """漂移形态名字归一 → (注册表中文名, 剥离偏移)；未命中 → ("", 0)。

    - 【工具名】包裹 → 偏移 0（整段都是名字）；
    - 裸名精确命中注册表（含 cap_id/同义词）→ 偏移 0；
    - 散文前缀粘连（"稍等一下。web_search"）→ 名字结尾处后缀最长匹配，
      且**别名前一个字符必须是句末符**（。！？!?；;…）——"先看排盘"的"看"、
      "就用一下搜索"的"下"不是句末符 → 不认（审查 I1 反例 a/b 的直接判据）；
      偏移 = 别名起点（剥离只切名字+载荷，散文前缀留给用户可见文本）；
    - 其余 → ("", 0)：不执行、不剥离（与既有各层同策略）。
    """
    cand = (raw or "").strip()
    if not cand:
        return "", 0
    m = _GLM_DRIFT_OPEN_RE.match(cand)
    if m:  # 【工具名】
        return _normalize_drift_name(m.group(1).strip()), 0
    norm = _normalize_drift_name(cand)
    if norm:
        return norm, 0
    for a in _drift_aliases():
        if not a or not cand.endswith(a):
            continue
        # 别名前一个字符必须是句末符（防"先看排盘"这类叙述被当成工具名）
        prev = cand[len(cand) - len(a) - 1] if len(cand) > len(a) else ""
        if prev not in _GLM_DRIFT_BOUNDARY:
            continue
        norm = _normalize_drift_name(a)
        if norm:
            return norm, (len(cand) - len(a)) + (len(raw) - len(raw.lstrip()))
    return "", 0


def _glm_drift_block_start_ok(text: str, start: int) -> bool:
    """块起点判定：块前（跳过行内空白）只允许串首 / 换行 / 句末符。

    防「叙述里的工具名」被误判：`我的建议：先看排盘\\n{…}` 的块前字符是
    "看"（非句末符）→ 不认；`稍等一下。web_search\\n{…}` 的块前是 "。" → 认。
    """
    head = text[:start].rstrip(" \t")
    if not head:
        return True
    return head[-1] in ("\n",) or head[-1] in _GLM_DRIFT_BOUNDARY


def _glm_wrapped_spans(text: str):
    """扫出漂移形态工具调用块 → [(start, end, name, params_obj), ...]（解析/剥离同源）。

    start 已按名字偏移校正：散文前缀（"稍等一下。"）不随块一起剥掉，
    只切工具名与载荷（防吞用户可见正文）。三道形态共用同一判定：
    注册工具名 + 块起点 + 可解析 JSON 对象载荷。
    """
    out = []
    if not text:
        return out
    for rx in (_GLM_DRIFT_BRACKET_RE, _GLM_DRIFT_FENCE_RE, _GLM_DRIFT_GLUED_RE):
        for m in rx.finditer(text):
            name, off = _resolve_glm_drift_name(m.group("name"))
            if not name:
                continue  # 未知工具：不执行（剥离层同样跳过，纯文本零改动）
            if not _glm_drift_block_start_ok(text, m.start("name") + off):
                continue  # 名字前不是行首/句末 → 叙述文本，不认（审查 I1）
            try:
                payload = json.loads(m.group("payload"))
            except json.JSONDecodeError:
                continue  # 载荷不可解析（截断/残块）→ 不认（参数可解析性）
            if not isinstance(payload, dict):
                continue
            out.append((m.start("name") + off, m.end(), name, payload))
    # 三道形态可能在同一位置重复命中（如 【名】+围栏 同时满足 A/B）→ 去重取最长
    kept = []
    for span in sorted(out, key=lambda x: (x[0], -x[1])):
        if any(span[0] < k[1] and k[0] < span[1] for k in kept):
            continue
        kept.append(span)
    return kept


def _glm_prose_call_spans(text: str):
    """形态 D（真实生产形态）→ [(start, end, name, {"text": params}), ...]。

    四条硬护栏（复审裁定：以护栏替代"形态取消"，保生产正确性）：
    名字精确命中注册表 + 参数可解析（非空/≤60 字/含可执行字符/无句末标点/
    非 JSON 引号残块）+ 块在**消息末尾** + 结构唯一（最多一个块）。
    """
    out = []
    if not text:
        return out
    m = _GLM_PROSE_CALL_RE.search(text)
    if not m:
        return out
    name = _normalize_drift_name(m.group("name").strip())
    if not name:
        return out  # 未知工具名：不执行、不剥离
    params = m.group("params").strip()
    if not params or len(params) > 60:
        return out
    if params[0] in '{"[':
        return out  # JSON/引号载荷残块 → 不当散文参数执行
    if any(ch in params for ch in _GLM_PROSE_BAD_PUNCT):
        return out  # 带句末标点 = 散文句，不是参数
    if not _GLM_PROSE_PARAM_CHAR_RE.search(params):
        return out  # 纯标点噪声 → 不可解析
    out.append((m.start("name"), m.end(), name, {"text": params}))
    return out


def parse_glm_wrapped_format(text: str) -> List[ToolCall]:
    """GLM 漂移格式适配（k33/A3，末道兜底）：见上方形态说明。

    - 形态 A/B/C（JSON 载荷）：要求「明确漂移标记 + 注册工具名 + 块起点 +
      可解析 JSON 对象载荷」四条同时成立（审查 I1）；
    - 形态 D（真实生产形态，复审回归修复）：仅在前三形态零命中时按
      `_glm_prose_call_spans` 的硬护栏判定。
    """
    calls = [ToolCall(name=name, params_obj=params)
             for _s, _e, name, params in _glm_wrapped_spans(text)]
    if calls:
        return calls
    return [ToolCall(name=name, params_obj=params)
            for _s, _e, name, params in _glm_prose_call_spans(text)]


def strip_glm_wrapped_format(text: str) -> str:
    """剥离漂移形态工具调用块（与 parse 同一 span 计算；未命中名 → 原文保留）。"""
    spans = _glm_wrapped_spans(text)
    if not spans:
        spans = _glm_prose_call_spans(text)
    if not spans:
        return text
    parts, last = [], 0
    for start, end, _name, _params in spans:
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
      句末符后散文粘连名（JSON 对象载荷）+ 形态 D「裸名独占行 + 末行散文参数」
      （真实生产形态，带硬护栏；既有各层零命中时才跑）。
      k33 审查 I1 收紧后复审再修正：裸引号串/散文粘连叙述名仍一律不认
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
        # k33/A3: GLM 格式漂移兜底（【】包裹 / 围栏 / 句末符后粘连名 + JSON 载荷）
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
