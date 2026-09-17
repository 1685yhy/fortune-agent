"""缘语管线：等级+特征+关系标签 → 模板库确定性选取 → 可选 LLM 润色 → 红线校验 → 模板兜底。

免费档红线：
- 2 秒内返回（LLM 润色超时 2s，失败即用模板原句，绝不编造）；
- 不点破具体事件、不涉对方姓名、不含绝对断言（必成/必散/注定/一定/保证/离婚/分手）；
- 主句 ≤24 字（可晒体），不以付费为要挟话术。
"""
import hashlib
import logging

logger = logging.getLogger(__name__)

_QUOTES_BY_LEVEL = {
    "天作之合": ["金玉相逢，良缘可期", "双星交辉，缘分天成", "天时地利，恰逢其人"],
    "情投意合": ["心意相通，如沐春风", "彼此滋养，越处越顺", "一见如故，久处不厌"],
    "相得益彰": ["一刚一柔，互补成章", "差异生趣，相映生辉", "你补我短，我成你长"],
    "和而不同": ["各有天地，仍可同行", "风格不同，心却有约", "慢火细炖，感情愈醇"],
    "细水长流": ["细水长流，历久弥新", "来日方长，温柔以待", "静水流深，情在久处"],
}

_RELATION_SUFFIX = {
    "暗恋": "缘分已起，静待花开",
    "恋人": "此缘正浓，宜温柔相待",
    "夫妻": "共度余生，细水长流",
    "朋友": "相逢是缘，同行是福",
}

_NEGATIVE_MARK = ("六冲", "六害", "相刑", "相克")
_REDLINE_TOKENS = ("必成", "必散", "注定", "一定", "保证", "离婚", "分手", "必离")
_MAX_MAIN_LEN = 24


def _validate(text: str) -> bool:
    """红线校验：非空、≤24 字、无绝对断言、无脏文本。"""
    t = (text or "").strip()
    if not t or len(t) > _MAX_MAIN_LEN:
        return False
    if any(k in t for k in _REDLINE_TOKENS):
        return False
    if "http" in t or "{" in t or "}" in t:
        return False
    return True


def _pick_template(level: str, features: list, relation: str) -> str:
    """按 特征+关系+等级 哈希确定性选取（同一对情侣结果稳定，非随机）。"""
    candidates = _QUOTES_BY_LEVEL.get(level, _QUOTES_BY_LEVEL["细水长流"])
    seed = hashlib.md5(("|".join(sorted(features)) + "|" + relation + "|" + level).encode()).hexdigest()
    return candidates[int(seed, 16) % len(candidates)]


def _pick_cliffhanger(features: list) -> str:
    """悬念半句（免费档付费墙钩子）：负特征 → 调和钩子；否则默契钩子。"""
    if any(k in f for f in features for k in _NEGATIVE_MARK):
        return "只是暗处尚有一克，需一份调和的智慧……"
    return "还有一份暗藏的默契，等着你们亲手揭开……"


def generate_yuan_quote(level_label: str, features: list, relation: str = "",
                        cliffhanger: bool = False, polish_fn=None) -> dict:
    """生成一句话缘语。

    - main: 主句（≤24 字）；polish_fn 返回合法文本时采用润色版，异常/非法 → 模板原句；
    - suffix: 关系口吻后缀（无关系标签为空串）；
    - cliffhanger: 悬念半句（免费档展示，点「展开」见付费墙）；
    - full: 完整拼接文本。
    """
    main = _pick_template(level_label, features or [], relation)
    if polish_fn is not None:
        try:
            polished = polish_fn(main)
            if isinstance(polished, str) and _validate(polished):
                main = polished
        except Exception as e:  # LLM 失败 → 模板兜底（宁缺毋滥）
            logger.warning("缘语润色失败(模板兜底): %s", e)
    suffix = _RELATION_SUFFIX.get(relation, "")
    tail = _pick_cliffhanger(features or []) if cliffhanger else ""
    full = main
    if suffix:
        full += "，" + suffix
    full += "。"
    if tail:
        full += tail
    return {"main": main, "suffix": suffix, "cliffhanger": tail, "full": full}
