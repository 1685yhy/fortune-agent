"""对话消息卡片化——服务端卡片标记生成（Task E2-1）。

契约（E2-2 端上依赖）：
    [card:类型 title="标题"]
    <markdown 正文>
    [/card]

- 类型枚举：paipan（排盘结果）/ yunshi（运势分析：事业/财运/感情/健康类）/
  zeri（择吉结果）/ data（收藏/档案/记录直读）/ knowledge（无档案知识兜底）
- title 可缺省（端上回退"易理明灯"或类型默认标题）
- 只包第一段结构化主体，末尾引导语/反馈语（"还想了解…"/"可回复「准」"/版本
  页脚）留在卡片外；正文是纯 markdown 字符串，不做二次转换

判定规则（brief §判定规则，按优先级，可单测的纯函数）：
1. 工具调用记录：本轮 <tool_call> 执行过「排盘」→ paipan、「择日」→ zeri
2. 意图/路由：本轮实际使用过 career/wealth/love/health 分析场景 → yunshi
   （依赖"实际路由记录"而非关键词扫描——含"财运"的闲聊无记录不误判）
3. 引擎直跑（意图路径）：_do_bazi_analysis 完成排盘 → paipan、
   _do_zeri_analysis 完成择日 → zeri（与规则 1 同质但无 <tool_call> 记录；
   scenario 优先于它——场景问句回复以分析为主体 → yunshi）
4. 存量直读（RecordQuery/重看盘直读）→ data
5. 无档案知识兜底（D9 路径确定性签名文案）→ knowledge
6. 都不命中 → None（普通对话保持原样）

判定原则：宁可漏包不可误包；拆不出尾部就整体包（宁整勿碎）。
"""
import re
from typing import Optional, Sequence, Tuple

# ── 卡片类型枚举（E2-2 端上依赖）────────────────────────────────
CARD_TYPES = ("paipan", "yunshi", "zeri", "data", "knowledge")

# ── 判定常量 ────────────────────────────────────────────────────
# 工具调用记录 → 卡片类型（tool_log["calls"] 的 "type" 字段，即 ToolResult.name）
_TOOL_CARD_TYPES = {
    "排盘": "paipan",
    "择日": "zeri",
}
# 场景路由 → 运势分析卡片（与 handler SCENARIO_KEYWORDS/MAP 的 category 口径一致；
# 仅事业/财运/感情/健康四类分析场景；property/compatibility 等不包——宁可漏包）
_YUNSHI_SCENARIOS = frozenset({"career", "wealth", "love", "health"})
# D9 无档案知识兜底的确定性签名（_BAZI_GENERAL_KNOWLEDGE 条目的固定开头）
_KNOWLEDGE_SIG_RE = re.compile(
    r'关于「[^」]{1,16}」的(?:通用命理常识|基本常识)|关于八字命理的基本常识')

# ── 尾部引导语/反馈语拆分 ──────────────────────────────────────
# 已知尾部模式（均为服务端代码拼接的固定结构，出现在回复末尾且可叠加）：
# 1) 反馈语："———\n这个分析对你有帮助吗？可回复「准」或「不准」告诉我"
#    / "———\n💬 这个分析对你有帮助吗？👍 有帮助  👎 不太准"
# 2) AI 下文引导："💬 还想了解：..."
# 3) 阅读版本页脚："---\n解读版本: vX | 生成时间: ...\n同一八字同一问题，结果始终一致"
_TAIL_START_RE = re.compile(
    r'\n\n(?:———\n|💬\s*还想了解|---\n(?:解读版本|阅读版本))')

# 卡片默认标题（title 可缺省，端上另有回退；data/knowledge 不设默认标题——
# 数据直读的类别（档案/解梦/签/晨笺…）与知识兜底无法统一命名，交给端上默认）
_DEFAULT_TITLES = {
    "paipan": "我的命盘",
    "yunshi": "运势分析",
    "zeri": "择吉结果",
}


def _escape_title(title: str) -> str:
    """标题属性转义：反斜杠/双引号/换行 → 转义序列，保证 title="..." 结构不被破坏。"""
    return (str(title)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "")
            .strip())


def detect_card_type(
    reply: str,
    *,
    tool_calls: Optional[Sequence] = None,
    scenario: Optional[str] = None,
    direct_read: bool = False,
    ran_paipan: bool = False,
    ran_zeri: bool = False,
) -> Optional[str]:
    """判定回复应包的卡片类型（按优先级），都不命中返回 None。

    输入 = 回复文本 + 本次上下文（纯函数，可单测）。tool_calls 为
    tool_log["calls"]（元素 {"type": ToolResult.name, ...}，也兼容纯字符串）。

    普通闲聊（如"我财运不错"）没有任何工具/场景/直读记录 → 返回 None，
    不会被误判（判定依赖记录而非关键词）。
    """
    if not reply:
        return None
    # 1. 工具调用记录（<tool_call> 实际执行过的工具）
    names = set()
    for c in (tool_calls or ()):
        if isinstance(c, dict):
            t = c.get("type")
        elif isinstance(c, str):
            t = c
        else:
            t = getattr(c, "name", "")
        if t:
            names.add(str(t))
    for tool_name, card_type in _TOOL_CARD_TYPES.items():
        if tool_name in names:
            return card_type
    # 2. 场景路由（本次实际使用，非关键词扫描）
    if scenario in _YUNSHI_SCENARIOS:
        return "yunshi"
    # 3. 引擎直跑（意图路径排盘/择日，无 <tool_call> 记录）
    if ran_zeri:
        return "zeri"
    if ran_paipan:
        return "paipan"
    # 4. 存量直读
    if direct_read:
        return "data"
    # 5. 无档案知识兜底（确定性签名文案；LLM 合成变体无签名 → 漏包）
    if _KNOWLEDGE_SIG_RE.search(reply):
        return "knowledge"
    return None


def split_tail(reply: str) -> Tuple[str, str]:
    """按回复尾部已知模式拆分 (正文主干, 尾部引导语/反馈语)。

    尾部模式（还想了解 / 可回复「准」/ 👍👎 / 版本页脚）由服务端代码拼接在
    回复末尾、可能叠加（下文引导 → 反馈语 → 版本页脚）；按首个已知模式
    拆分即得到完整尾部区。正文偶发同形文字时内容整体保留（移至卡片下方
    展示），不丢数据；拆不出/拆后无正文 → 整体包（宁整勿碎）。
    """
    if not reply:
        return reply, ""
    m = _TAIL_START_RE.search(reply)
    if not m:
        return reply, ""
    body = reply[: m.start()].rstrip()
    if not body:
        return reply, ""
    return body, reply[m.start():].strip("\n")


def wrap_card(reply: str, card_type: str, title: str = "") -> str:
    """给回复包上卡片标记（E2-2 端上解析格式）。

    - 正文不转换：就是原回复 markdown；尾部引导语/反馈语拆到卡片外
    - title 缺省时按类型默认标题（data/knowledge 无默认则省略属性，端上回退）
    - 正文含 [card: / [/card] 字样（防结构破坏）或回复已含卡片标记
      （防重复包装/缓存回环）→ 原样返回，宁可漏包
    - 未知卡片类型 → 原样返回
    """
    if not reply or card_type not in CARD_TYPES:
        return reply
    if "[card:" in reply or "[/card]" in reply:
        return reply
    body, tail = split_tail(reply)
    _title = (title or _DEFAULT_TITLES.get(card_type, "")).strip()
    attr = f' title="{_escape_title(_title)}"' if _title else ""
    card = f"[card:{card_type}{attr}]\n{body}\n[/card]"
    if tail:
        card = card + "\n\n" + tail
    return card
