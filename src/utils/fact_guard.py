"""k11 事实纪律 · 输出后纯规则校验器（B 性别称谓 / C 神煞白名单）——零 LLM。

事故背景（2026-09-06 行动建议卡）：男命收到「醒醒吧姐妹」式女性口吻；正文/卡引用
排盘卡上看不到的神煞（显示截断 [:6] 而喂全集 → 观感编造；主链另有真幻觉「文昌贵人」
不在引擎全集）。G1 性别链只覆盖主链/运势卡，未到达 advisor 三消费点；prompt 修复之外
需要"输出后校验器"兜底（任何 LLM 环节重写后仍可能违规）。

职责（纯规则，无 LLM、无网络）：
- guard_gender_terms：男/未知命文本中的女性称谓词 → 去词 + 返回命中（女命不处理）
- guard_shensha_refs：文本中出现的引擎神煞词典词（shensha.SHENSHA_LUCK 键集，59 型
  全集单一事实源）不在本盘 allow（引擎算出全集）→ 去词 + 返回命中
- scrub_turn：组合入口（性别 + 神煞 allow），供 handler/advisor/chat_stream 接线

去词语义：命中词整体删除（规范允许的"去词/告警"档；不做整句改写）。防误伤：
allow 为空（无上下文/非命理轮）→ 神煞段跳过不 scrub。
"""
import logging
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
