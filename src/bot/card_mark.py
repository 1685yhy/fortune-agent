"""对话消息卡片化——服务端卡片标记生成（Task E2-1）。

契约（E2-2 端上依赖）：
    [card:类型 title="标题"]
    <markdown 正文>
    [/card]

- 类型枚举：paipan（排盘结果）/ yunshi（运势分析：事业/财运/感情/健康类）/
  zeri（择吉结果）/ data（收藏/档案/记录直读）/ knowledge（无档案知识兜底）/
  ziwei（紫微斗数）/ liuyao（六爻占卜）/ fengshui（风水分析）/ mianxiang（面相分析）/
  qimen（奇门遁甲）/ dream（解梦结果）——后 6 类为批次 2 P1 引擎结果卡片
- title 可缺省（端上回退"易理明灯"或类型默认标题）
- 只包第一段结构化主体，末尾引导语/反馈语（"还想了解…"/"可回复「准」"/版本
  页脚）留在卡片外；正文是纯 markdown 字符串，不做二次转换

判定规则（brief §判定规则，按优先级，可单测的纯函数）：
1. 工具调用记录：本轮 <tool_call> 实际执行过且成功（hit=True）的「排盘」→
   paipan、「择日」→ zeri；hit=False 的失败调用不计（"「排盘」工具暂不可用"
   等错误文案绝不能判卡）
2. 意图/路由：本轮实际使用过 career/wealth/love/health 分析场景 → yunshi
   （依赖"实际路由记录"而非关键词扫描——含"财运"的闲聊无记录不误判）
3. 引擎直跑（意图路径）：_do_bazi_analysis 完成排盘 → paipan、
   _do_zeri_analysis 完成择日 → zeri（与规则 1 同质但无 <tool_call> 记录；
   scenario 优先于它——场景问句回复以分析为主体 → yunshi）；批次 2 P1：
   紫微/六爻/风水/面相/奇门/解梦引擎完成计算 → 对应类型（标记仅在引擎
   成功返回后写入，异常/未注入无标记 → 漏包）
4. 存量直读（RecordQuery/重看盘直读）→ data
5. 无档案知识兜底（D9 路径确定性签名文案）→ knowledge
6. 都不命中 → None（普通对话保持原样）

判定原则：宁可漏包不可误包；拆不出尾部就整体包（宁整勿碎）。
错误/失败文案（⚠️/暂不可用/引擎执行失败）一律不包装（wrap_card 防御层）。
"""
import re
from typing import Optional, Sequence, Tuple

# ── 卡片类型枚举（E2-2 端上依赖）────────────────────────────────
# 基础 5 类 + 批次 2 P1（Task C1）：6 类引擎（紫微/六爻/风水/面相/奇门/解梦）
CARD_TYPES = ("paipan", "yunshi", "zeri", "data", "knowledge",
              "ziwei", "liuyao", "fengshui", "mianxiang", "qimen", "dream")

# ── 判定常量 ────────────────────────────────────────────────────
# 工具调用记录 → 卡片类型（tool_log["calls"] 的 "type" 字段，即 ToolResult.name）
_TOOL_CARD_TYPES = {
    "排盘": "paipan",
    "择日": "zeri",
    "解梦": "dream",
    "风水": "fengshui",
}
# 场景路由 → 运势分析卡片（与 handler SCENARIO_KEYWORDS/MAP 的 category 口径一致；
# 仅事业/财运/感情/健康四类分析场景；property/compatibility 等不包——宁可漏包）
_YUNSHI_SCENARIOS = frozenset({"career", "wealth", "love", "health"})
# D9 无档案知识兜底的确定性签名（_BAZI_GENERAL_KNOWLEDGE 条目的固定开头）
_KNOWLEDGE_SIG_RE = re.compile(
    r'关于「[^」]{1,16}」的(?:通用命理常识|基本常识)|关于八字命理的基本常识')
# 错误/失败文案签名（服务端降级与工具失败回复的确定性片段）——出现任一即不
# 包装卡片（宁可漏包不可误包）："⚠️ 服务暂时不可用：…"（handler 流程异常降级）、
# "「排盘/解梦/风水/择日/查记录」工具暂不可用…"（工具注册失败）、
# "XX引擎执行失败：…"（引擎抛异常）等均由此覆盖
_ERROR_SIGS = ("⚠️", "暂不可用", "引擎执行失败")

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
    # 批次 2 P1（Task C1）：6 类引擎默认标题（端上 CARD_DEFAULT_TITLES 同步）
    "ziwei": "紫微命盘",
    "liuyao": "六爻卦象",
    "fengshui": "风水分析",
    "mianxiang": "面相分析",
    "qimen": "奇门遁甲局",
    "dream": "解梦结果",
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
    ran_ziwei: bool = False,
    ran_liuyao: bool = False,
    ran_fengshui: bool = False,
    ran_mianxiang: bool = False,
    ran_qimen: bool = False,
    ran_dream: bool = False,
) -> Optional[str]:
    """判定回复应包的卡片类型（按优先级），都不命中返回 None。

    输入 = 回复文本 + 本次上下文（纯函数，可单测）。tool_calls 为
    tool_log["calls"]（元素 {"type": ToolResult.name, ...}，也兼容纯字符串）。

    普通闲聊（如"我财运不错"）没有任何工具/场景/直读记录 → 返回 None，
    不会被误判（判定依赖记录而非关键词）。
    """
    if not reply:
        return None
    # 1. 工具调用记录（<tool_call> 实际执行过的工具；hit=False 的失败调用不计——
    #    工具"暂不可用/引擎执行失败"等错误文案绝不能包成命盘/择日卡片）
    names = set()
    for c in (tool_calls or ()):
        if isinstance(c, dict):
            if c.get("hit") is False:
                continue  # 执行失败（hit=False）→ 不视为本轮实际使用
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
    # 3. 引擎直跑（意图路径排盘/择日，无 <tool_call> 记录；批次 2 P1 扩展：
    #    6 类引擎——标记仅在引擎真实完成计算后写入，失败/未注入无标记 → 漏包）
    if ran_zeri:
        return "zeri"
    if ran_paipan:
        return "paipan"
    if ran_ziwei:
        return "ziwei"
    if ran_liuyao:
        return "liuyao"
    if ran_fengshui:
        return "fengshui"
    if ran_mianxiang:
        return "mianxiang"
    if ran_qimen:
        return "qimen"
    if ran_dream:
        return "dream"
    # 4. 存量直读
    if direct_read:
        return "data"
    # 5. 无档案知识兜底（确定性签名文案；LLM 合成变体无签名 → 漏包）
    if _KNOWLEDGE_SIG_RE.search(reply):
        return "knowledge"
    return None


def strip_card_decor_for_llm(text: Optional[str]) -> Optional[str]:
    """按行剥离卡片渲染装饰（k7b），返回纯正文 —— LLM 上下文/输出清洗。

    背景（k7b 端到端实录实证）：会话历史里存在装配层生成的整卡定稿
    （[card:…]…[/card]+图行+页脚）后，LLM 会从历史仿写卡尾（自造卡头行+
    假图行（URL 幻觉抄历史旧图、可能丢📊）+假[/card]+假页脚），每次仿写
    又落库成为新的假样例 → 自增强。卡/图/页脚是渲染装饰，只由装配层
    （wrap_card/引擎直出保底）注入一次；LLM 永远只该看到和产出正文。

    规则（每种装饰在实录中均独立成行，按行级剥离，正文行全部保留）：
    - 卡头行：行首（可带缩进）`[card:…`
    - 卡闭合行：行首（可带缩进）`[/card]`
    - 图行：行内 📊 且含 `http(s)://…`；或行内「命盘图片：」且含 URL
      （覆盖 LLM 仿写丢📊 的空白前缀形态；不锚定行首）
    - 反馈页脚行：行内含「可回复」且「「准」」且「「不准」」
    - 分隔行：整行 `———…` 或 `---…`
    - 版本页脚（防御性，存量消息）：行含「解读版本：」

    契约：
    - 保正文逐行原样（只删装饰行）；删除后连续空行压缩（≥3 个 `\\n` → 2 个
      `\\n`，正文段落间正常最多 1 空行=2 换行）；
    - 首尾 strip；无装饰文本 → 返回原文（strip 后恒等）；
    - 误伤防护：正文行含「命盘」但无「图片：+URL」不动；含 📊 但无 http
      不动；`[card:` 只在整行行首才剥（正文行以文字开头不受影响）；
    - 空串/None 原样返回（防御）。
    - 只读清洗：只服务 LLM 上下文/LLM 输出，绝不回写库（库内定稿保持
      原样，前端历史渲染仍读原文含卡）。
    """
    if not text:
        return text
    lines = text.splitlines()
    kept = [ln for ln in lines if not _is_decor_line(ln)]
    if len(kept) == len(lines):
        return text.strip()  # 无装饰 → 恒等（strip 后）
    body = _BLANK_RUN_RE.sub("\n\n", "\n".join(kept))
    return body.strip()


# k7b 行级装饰判定（单行正则，见 strip_card_decor_for_llm docstring）
_URL_IN_LINE_RE = re.compile(r'https?://\S+')
_TEXT_IMG_LINE_RE = re.compile(r'命盘图片[：:][^\n]*https?://\S+')
_VERSION_FOOTER_RE = re.compile(r'解读版本[：:]')
_CARD_HEADER_LINE_RE = re.compile(r'^\s*\[card:')
_CARD_CLOSE_LINE_RE = re.compile(r'^\s*\[/card\]')
_DASH_SEP_LINE_RE = re.compile(r'^\s*---+$')
_EMDASH_SEP_LINE_RE = re.compile(r'^\s*———+\s*$')
_BLANK_RUN_RE = re.compile(r'\n{3,}')


def _is_decor_line(line: str) -> bool:
    """判定单行是否为卡渲染装饰（卡头/卡闭合/图行/反馈页脚/分隔/版本页脚）。"""
    if _CARD_HEADER_LINE_RE.match(line):
        return True
    if _CARD_CLOSE_LINE_RE.match(line):
        return True
    if ("📊" in line and _URL_IN_LINE_RE.search(line)) or \
            _TEXT_IMG_LINE_RE.search(line):
        return True
    if "可回复" in line and "「准」" in line and "「不准」" in line:
        return True
    if _DASH_SEP_LINE_RE.match(line) or _EMDASH_SEP_LINE_RE.match(line):
        return True
    if _VERSION_FOOTER_RE.search(line):
        return True
    return False


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
    - 错误/失败文案（"⚠️"、"暂不可用"、"引擎执行失败"签名）→ 原样返回，
      绝不包成卡片（宁可漏包不可误包）
    - 未知卡片类型 → 原样返回
    """
    if not reply or card_type not in CARD_TYPES:
        return reply
    if any(sig in reply for sig in _ERROR_SIGS):
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
