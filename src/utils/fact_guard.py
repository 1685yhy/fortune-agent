"""k11 事实纪律 · 输出后纯规则校验器（B 性别称谓 / C 神煞白名单 / D schema 回显）——零 LLM。

事故背景（2026-09-06 行动建议卡）：男命收到「醒醒吧姐妹」式女性口吻；正文/卡引用
排盘卡上看不到的神煞（显示截断 [:6] 而喂全集 → 观感编造；主链另有真幻觉「文昌贵人」
不在引擎全集）。G1 性别链只覆盖主链/运势卡，未到达 advisor 三消费点；prompt 修复之外
需要"输出后校验器"兜底（任何 LLM 环节重写后仍可能违规）。

职责（纯规则，无 LLM、无网络）：
- guard_gender_terms：男/未知命文本中的女性称谓词 → 去词 + 返回命中（女命不处理）
- guard_shensha_refs：文本中出现的引擎神煞词典词（shensha.SHENSHA_LUCK 键集，59 型
  全集单一事实源）不在本盘 allow（引擎算出全集）→ 去词 + 返回命中
- guard_schema_echo（k63-r2 / D）：文本形态是"给模型看的 schema/规格说明" → **整段置空**
  （降级档 GLM 会把 prompt 里的 JSON schema 示例原样回显进字段，用户可见即露馅）
- scrub_turn：组合入口（性别 + 神煞 allow），供 handler/advisor/chat_stream 接线
  —— **语义与调用方保持不变**；D 是**独立函数**，不并入 scrub_turn（见下）

去词语义：命中词整体删除（规范允许的"去词/告警"档；不做整句改写）。防误伤：
allow 为空（无上下文/非命理轮）→ 神煞段跳过不 scrub。

为什么 D 与 scrub_turn **分开**（不扩 scrub_turn 的判据）：
1. scrub_turn 是 k11-B **称谓/神煞**防线，被 handler/advisor/chat_stream **多处**消费；
   在其中加"schema 判据"会让所有消费方**隐式**获得一个与它们无关的删段行为
   （主链正文若出现合法 `[{` 片段也会被连带整段置空），风险面与收益面不对等；
2. D 的判据是**形态学**的（JSON 键/占位符/元指令），与 B/C 的**词表**判据正交，
   混在一个返回值里无法区分"命中了什么"，排障与告警语义会糊掉；
3. 消费面不同：D 目前只接 **advisor 建议卡**（唯一挂了 GLM 降级链且字段来自 schema
   驱动的 prompt 的链）。分开后主链/流式链一点不受影响。
"""
import logging
import re
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# B：女性向称谓/闺蜜口吻词（男命/未知性别文本命中即去词+告警；仅女命放行）。
# 词表刻意保守（显式女性指向），避免误伤男性向文本（如"兄弟"不在此列）。
FEMALE_ADDRESS_TERMS: Tuple[str, ...] = (
    "姐妹", "闺蜜", "亲爱的", "姑娘", "小仙女", "小姐姐", "集美", "姐们儿",
)

# C：引擎神煞词典缓存（SHENSHA_LUCK 键集 = 59 型全集单一事实源；惰性加载防环）
_SHENSHA_LEXICON: Optional[Tuple[str, ...]] = None


def gender_label(gender) -> str:
    """性别归一：男/male → 男；女/female → 女；其余（unknown/空/None）→ unknown。"""
    _g = str(gender or "").strip().lower()
    if _g in ("男", "male", "m"):
        return "男"
    if _g in ("女", "female", "f"):
        return "女"
    return "unknown"


def shensha_lexicon() -> Tuple[str, ...]:
    """引擎神煞全集词典（词长降序，先长后短匹配防子串误拆）。"""
    global _SHENSHA_LEXICON
    if _SHENSHA_LEXICON is None:
        try:
            from src.engines.shensha import SHENSHA_LUCK
            _SHENSHA_LEXICON = tuple(
                sorted({str(k) for k in (SHENSHA_LUCK or {})},
                       key=len, reverse=True))
        except Exception:
            logger.warning("fact_guard: 神煞词典加载失败（降级为空词典）", exc_info=True)
            _SHENSHA_LEXICON = ()
    return _SHENSHA_LEXICON


def guard_gender_terms(text: str, gender) -> Tuple[str, List[str]]:
    """B：称谓词校验——男/未知命出现女性称谓词 → 去词并返回命中列表；女命原样。

    :return: (cleaned_text, hits)
    """
    if not text:
        return text, []
    if gender_label(gender) == "女":
        return text, []
    hits = [t for t in FEMALE_ADDRESS_TERMS if t in text]
    if not hits:
        return text, []
    cleaned = text
    for t in hits:
        cleaned = cleaned.replace(t, "")
    logger.warning("fact_guard: 性别称谓过滤（去词 %s）", "、".join(hits))
    return cleaned, hits


def guard_shensha_refs(text: str, allow_names: Iterable[str],
                       lexicon: Optional[Iterable[str]] = None) -> Tuple[str, List[str]]:
    """C：神煞引用校验——文本中出现的引擎词典神煞词必须在 allow（本盘引擎全集）内。

    allow 为空 → 视为"无本盘白名单上下文"，跳过（防误伤自由对话/非命理轮）。
    命中白名单外神煞词 → 去词 + 返回命中（纯规则，去词/告警档）。
    """
    if not text:
        return text, []
    allow = {str(a) for a in (allow_names or ())}
    if not allow:
        return text, []
    lex = tuple(lexicon) if lexicon is not None else shensha_lexicon()
    if not lex:
        return text, []
    hits: List[str] = []
    cleaned = text
    for w in lex:
        if w in allow or w not in cleaned:
            continue
        cnt = cleaned.count(w)
        cleaned = cleaned.replace(w, "")
        hits.append(w)
        logger.warning("fact_guard: 神煞白名单外引用去词 %s×%d（本盘全集=%d 个）",
                       w, cnt, len(allow))
    return cleaned, hits


def scrub_turn(text: str, gender, shensha_allow: Optional[Iterable[str]] = None
               ) -> str:
    """组合出口：称谓 + 神煞去词（幂等，可对同文本多次调用）。"""
    if not text:
        return text
    cleaned, _h1 = guard_gender_terms(text, gender)
    cleaned, _h2 = guard_shensha_refs(cleaned, shensha_allow)
    return cleaned


# ============================================================
# D：schema/结构文本泄漏（k63-r2）——呈现层兜底，零 LLM，纯规则
# ============================================================
# 事故形态（2026-09-20 实测，非推测）：advisor_v2._call_llm 挂 **GLM-4-Flash 降级链**
# （src/llm/client.py glm_openai_completion，ZHIPU_API_KEY 存在时优先），小模型会把
# **prompt 里给模型看的 JSON schema 示例**原样当字段值回显 → 用户直接看到
# `你问的是[领域]，…格式：…如果实在没有特别信息，输出空字符串。` 这类**对模型说的话**。
# 生产档（deepseek）未观察到该形态。
#
# 判据按**形态**（不是匹配某段 schema 原文 —— 那种补丁换个 prompt 就失效）：
#   强信号：命中 1 条即判泄漏（自然中文用户文案几乎不可能出现的结构/元语言形态）
#   弱信号：命中 ≥2 条才判泄漏（"格式：/示例："等元语言 + 英文字段名裸词，单条易误伤）
# 正反例清单见 tests/test_k63_schema_echo_guard.py（**中文引号/全角括号/`{}` 合法表达/
# `[1]` 引用编号/“30字以内”日常建议**均不得命中）。
#
# ⚠️ 精度优先于召回（控制方裁决 · k62k63 集成修复）：D 只被 `advisor_v2` 两处消费
# （`:146` / `:150`），且 `advice` 命中会**整条建议摘除** ⇒ **误杀代价 = 用户少一条
# 建议**（5 条全摘 → 退回通用兜底）。因此宁可漏判（用户看到一段模板说明）也不许
# 误杀（用户丢掉真建议）：**加信号必须同时给出"误杀未上升"的实测**，否则不加。
# 已知代价（阈值设计的固有代价，已用用例钉住，不隐藏）：`时间窗口` + 任一"必须X"
# 两条弱信号同时出现在**正常建议**里 → 会被整段置空
# （examples: "2026年的关键时间窗口在立秋之后，签合同必须包含违约条款。"）。
# 召回面（k62k63 集成修复补齐，此前 8/18 漏判 → 现 18/18）：prompt schema 里 4 个
# 领域用的是短规格（`最佳时间窗口，包含具体日期范围` 出现 4 次 /
# `具体的行动建议，结合…的个性化分析` 等），每条只带 1 条弱信号时会被放行 →
# 补入下面三条"规格从句"弱信号（带逐条形态论证与实测数字）。
# 残留缺口（如实登记，已用测试钉住）：**非逐字**的规格回显仍不命中 ——
#   ① 只回显半句（`包含具体日期范围`，丢了前面的"最佳时间窗口，"）；
#   ② 同义改写（"包含"→"含"：`最佳时间窗口，含具体日期范围`）；
#   ③ 只回显 advice 规格的后半句（`结合八字五行的个性化分析`，丢了 spec 头）——
#      它只有 1 条弱信号，被阈值（≥2）**故意**放行。
# 刻意不追：这三条都是"像规格的自造文案"，追下去要把 `时间窗口`/`具体的行动建议`
# 升成强信号或堆词表，而 D 的误杀代价是**用户少一条真建议** ⇒ 精度优先于召回。
# 复现口径与数字见 tests/test_k63_schema_echo_guard.py::RESIDUAL_GAPS 与集成修复报告 §③。
SCHEMA_ECHO_STRONG: Tuple[str, ...] = (
    r'"[A-Za-z_][A-Za-z0-9_]{2,}"\s*:',          # "advice": / "serendipity":（JSON 键）
    r'\[\s*\{',                                    # [{（JSON 数组开头）
    r'\}\s*\]',                                    # }]（JSON 数组结尾）
    r'\[[一-龥]{1,6}\]',                   # [领域]（中文占位符；[1]/[n] 不算）
    r'(?i)(?:high|medium|low)\s*/\s*(?:high|medium|low)',  # 取值域枚举
    r'(?:只|仅)?输出\s*JSON',                        # 元输出指令
    r'不要\s*markdown', r'输出空字符串', r'不输出(?:其他|其它|任何)文字',
    # 规格形态：值**以**「（N字以内）」结尾 —— 用户向文案不会用括号约束作者长度
    r'[（(]\s*\d+\s*字以内\s*[)）]\s*$',
)
SCHEMA_ECHO_WEAK: Tuple[str, ...] = (
    r'格式[：:]', r'示例[：:]', r'取值[：:]', r'请按以下(?:格式|JSON|要求)',
    r'\d+\s*(?:[-~—至]\s*\d+\s*)?字以内',
    r'JSON\s*(?:对象|格式)',
    # 元指令形态（对模型下的要求，不是对用户说的话）
    r'\d+\s*[-~—]\s*\d+\s*句话', r'不超过\s*\d+\s*字',
    r'必须(?:包含|结合|基于|覆盖|符合|给出|使用|遵守)',
    r'(?<!例)如[’‘\'"]', r'时间窗口',
    # 英文字段名裸词（schema 的 key；中文正文里出现即是回显特征）
    r'(?i)\b(?:serendipity|daily_tip|style_notes|actions|confidence|category|'
    r'timing|advice|concrete_steps|success_metric|celebrity_match|insight)\b',
    # ── 规格从句（k62k63 集成修复 · 召回补齐；每条都是**形态**，非抄某段原文）──
    # 事故面（审查 I-1 实测）：prompt schema 块里 4 个领域用的是**短规格** ——
    # `"timing": "最佳时间窗口，包含具体日期范围"`（在 prompt 里出现 **4 次**）与
    # `"advice": "具体的行动建议，结合八字五行的个性化分析"` 等 4 条；它们此前
    # 各只带 1 条弱信号 → 被阈值放行 ⇒ **真·原文回显漏判**。下面三条补上第二条
    # 弱信号。为什么不是"给某个例子打的补丁"：三条各自对应一类**写法差异**
    # （规格在描述"值该长什么样"，用户向文案在给出内容），且都已被 55 条自造正常
    # 文案（含 12 条对抗性"最像规格"写法）+ 229 条真实 LLM 字段（归档 137 + 本次
    # 自造 92）实测**零新增误杀**（改前/改后对照见
    # tests/test_k63_schema_echo_guard.py 的正/反例与集成修复报告 §③）：
    # ① 规格尾：`，包含具体<X>`（**不带「的/了」**）且**收在串尾** —— 无主语的
    #    祈使式名词短语收尾是规格写法；用户向文案不会这么写（写了"的"就不算，
    #    句中还有下文（逗号/句号）也不算 ⇒ 不误伤"…，包含具体的责任人"这类正常句）。
    r'[，,]\s*(?:必须|需要|需|应)?(?:包含|涵盖)具体(?![的了的这那哪])[^。！？；，,\n]{0,8}\s*[。！？]?\s*$',
    # ② schema 里 5 条 advice spec 全部是「具体的行动建议，结合…」这一搭配；
    #    用户向文案即使出现"具体的行动建议"，后面接的是冒号/具体事由，不会紧跟"结合"。
    r'具体的行动建议[，,]\s*结合',
    # ③ `结合…的<个性化|长远>(分析|建议…)` 是**名词化的"分析物描述"**（描述分析该
    #    长什么样，而不是给出分析）；"结合…的"偏正结构在用户向文案里极少见
    #    （那里写"结合你的情况…"，不带"的个性化/长远"）。
    r'结合[^。；！？，,\n]{0,12}的(?:个性化|长远)(?:发展)?(?:健康)?(?:分析|建议|方案)',
)
_SCHEMA_ECHO_STRONG_RE = tuple(re.compile(p) for p in SCHEMA_ECHO_STRONG)
_SCHEMA_ECHO_WEAK_RE = tuple(re.compile(p) for p in SCHEMA_ECHO_WEAK)


def schema_echo_hits(text: str) -> List[str]:
    """命中明细（强/弱分开，供告警与测试取证）。"""
    if not text:
        return []
    hits = [f"strong:{rx.pattern}" for rx in _SCHEMA_ECHO_STRONG_RE if rx.search(text)]
    hits += [f"weak:{rx.pattern}" for rx in _SCHEMA_ECHO_WEAK_RE if rx.search(text)]
    return hits


def is_schema_echo(text: str) -> bool:
    """形态判定：强信号 ≥1 或 弱信号 ≥2（阈值化，防单条弱信号误杀）。"""
    hits = schema_echo_hits(text)
    n_strong = sum(1 for h in hits if h.startswith("strong:"))
    return n_strong >= 1 or (len(hits) - n_strong) >= 2


def guard_schema_echo(text: str) -> Tuple[str, List[str]]:
    """D：schema/规格说明回显 → **整段置空** + 返回命中。

    为什么是整段置空而不是"删掉命中片段"：回显物是**给模型看的模板说明**，
    片段删除只会留下语义残缺的半句（更像 bug）；字段本身可空
    （schema 明说 serendipity 可给空串），置空后由调用方走各自兜底。
    """
    if not text:
        return text, []
    if not is_schema_echo(text):
        return text, []
    hits = schema_echo_hits(text)
    logger.warning("fact_guard: schema 回显整段置空（%d 条信号：%s）",
                   len(hits), "、".join(hits[:4]))
    return "", hits


def scrub_schema_echo(text: str) -> str:
    """便捷出口（与 scrub_turn 同风格；**独立于** scrub_turn，不改其语义）。"""
    return guard_schema_echo(text)[0]
