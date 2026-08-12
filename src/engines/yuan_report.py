"""深度合盘报告管线（付费后生成）：前世今生 / 相处模式 / 矛盾点与化解 / 契合详情。

管线：
- 前世今生：纳音+日柱+天乙贵人 → 古籍 RAG（category='hehun'）→ 引用书名原文 → LLM 叙事（300-500 字）；
- 相处模式：合婚引擎三维明细 + 合盘引擎特征 → 体感描述（规则 + 特征口诀）；
- 矛盾点与化解：六冲/六害/相刑/纳音相克 规则逐条提取 → 每条配一条化解建议；
- 契合详情：双引擎分数明细直出（无 LLM），可对照复核。
质量红线：不出现「注定/必离婚/必分手/必成/化灾/改运/斩桃花」类绝对断言与承诺。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

TRUST_STATEMENT = "签文只作心意，不作断言 · 明灯拟"

_ABSOLUTE_TOKENS = ("注定", "必离婚", "必分手", "必成", "一定", "保证", "化灾", "改运", "斩桃花")

_RESOLVE_ADVICE = {
    "六冲": "六冲主聚少离多，宜把见面变成固定习惯，约定好的日子不轻易改，冲意自缓。",
    "六害": "六害多因小事生隙，宜大事说开、小事翻篇，不翻旧账、不积怨气。",
    "相刑": "相刑主言语摩擦，宜约定争吵后冷处理半小时，气头不过夜。",
    "纳音相克": "纳音相克主气场节奏不同，宜各留个人空间，再以共同爱好作缓冲。",
    "相克": "五行相克主节奏快慢不一，宜把差异当互补，先听后说、慢半拍回应。",
}

_GENERIC_RESOLVE = "命盘未见明显冲害，日常多沟通、多见面，感情自然稳步向前。"

_STYLE_MAP = [
    ("六合", "有六合之缘，天然亲近，多制造共同回忆即可保持热度。"),
    ("三合", "有三合之势，彼此默契度高，相处以自然为主，不必刻意。"),
    ("夫妻宫六合", "夫妻宫六合，感情根基深厚，遇事多商量即稳。"),
    ("双天乙贵人", "互为贵人，一方低谷时另一方总在关键处拉一把。"),
    ("五行互补", "五行互补，一方的强项恰好补足另一方的短板。"),
    ("六冲", "相处宜固定节奏，把见面变成习惯。"),
    ("六害", "小摩擦较多，宜大事说开、小事翻篇。"),
    ("相刑", "言语易起摩擦，约定冷处理时限会很有用。"),
    ("纳音相克", "气场节奏不同，各留空间反而更长久。"),
    ("相克", "节奏不一，把差异当互补、先听后说。"),
]


def _validate_text(text: str) -> bool:
    return not any(k in (text or "") for k in _ABSOLUTE_TOKENS)


def _retrieve_classics(retriever, bazi1, bazi2) -> list:
    """古籍 RAG 检索：纳音+日柱+天乙贵人 主题。返回 [{book, text}]（书名去重，最多 3 条）。"""
    if retriever is None:
        return []
    day1 = "".join(bazi1.bazi[2]) if len(bazi1.bazi) > 2 else ""
    day2 = "".join(bazi2.bazi[2]) if len(bazi2.bazi) > 2 else ""
    n1 = (bazi1.nayin or [""] * 4)[2] if getattr(bazi1, "nayin", None) else ""
    n2 = (bazi2.nayin or [""] * 4)[2] if getattr(bazi2, "nayin", None) else ""
    query = f"{n1} {n2} {day1} {day2} 姻缘 贵人 前世"
    try:
        hits = retriever.search(query, category="hehun", top_k=10)
    except Exception as e:
        logger.warning("合盘古籍检索失败: %s", e)
        return []
    refs, seen = [], set()
    for hit in hits or []:
        book = (getattr(hit, "source", "") or "").strip()
        text = (getattr(hit, "text", "") or "").strip().replace("\n", "")
        if not book or not text or book in seen:
            continue
        seen.add(book)
        refs.append({"book": book, "text": text})
        if len(refs) >= 3:
            break
    return refs


def _build_qianshi(union: dict, bazi1, bazi2, refs: list, llm) -> str:
    """前世今生：古籍原典引用 + LLM 叙事；无 LLM/越红线 → 规则叙事（引用原句）。"""
    day1 = "".join(bazi1.bazi[2]) if len(bazi1.bazi) > 2 else ""
    day2 = "".join(bazi2.bazi[2]) if len(bazi2.bazi) > 2 else ""
    quotes = "；".join(f"《{r['book']}》『{r['text'][:40]}』" for r in refs[:2])
    fallback = (
        f"你们的日柱{day1}与{day2}各自带着独立的命运轨迹，而纳音与贵人星把两条线牵到了一处。"
        + (f"古籍原典可作印证：{quotes}。" if quotes else "")
        + "缘分的来处不必深究，重要的是它把你们带到了彼此面前——这份相遇，本身就是一段前缘的续写。")
    if llm is None or not getattr(llm, "api_key", ""):
        return fallback
    try:
        r = llm._call_deepseek_model(
            "你是命理叙事师。请用 300-500 字写一段'前世今生'的叙事体姻缘解读，"
            f"必须引用以下古籍原典金句（书名+原文）：{quotes or '无可用引文，可略过'}。"
            f"背景：日柱{day1}与{day2}。要求：温暖不玄幻，不出现'注定/必离婚/必分手/化灾改运'等断言，"
            "不涉及姓名与具体事件。",
            llm.model, max_tokens=700, timeout=30)
        text = (r.response or "").strip()
        return text if _validate_text(text) else fallback
    except Exception as e:
        logger.warning("前世今生叙事失败(规则兜底): %s", e)
        return fallback


def _build_xiangchu(union: dict) -> str:
    """相处模式：三维明细 + 特征口诀（规则，无 LLM 依赖）。"""
    d = union["dimensions"]
    parts = [
        f"你们的相处，先从五行讲起：互补得分 {d['wuxing']['score']}/{d['wuxing']['max']}，"
        f"生肖关系{d['shengxiao']['relation']}，日柱关系{d['rizhu']['relation']}。",
    ]
    matched = [desc for kw, desc in _STYLE_MAP if any(kw in f for f in union["features"])]
    if matched:
        parts.append("相处要诀：" + "".join(dict.fromkeys(matched))[:200])
    else:
        parts.append("相处要诀：顺其自然，多见面、多分享日常。")
    return "".join(parts)


def _build_maodun(union: dict) -> str:
    """矛盾点与化解：规则提取负特征，逐条配化解建议。"""
    items = []
    for f in union["features"]:
        for kw, advice in _RESOLVE_ADVICE.items():
            if kw in f:
                items.append(f"· {f}：{advice}")
                break
    if not items:
        return _GENERIC_RESOLVE
    return "以下矛盾点可逐一化解：\n" + "\n".join(items)


def _build_qihe(union: dict) -> str:
    """契合详情：双引擎分数明细直出（无 LLM，可对照复核）。"""
    raw = union.get("raw", {})
    hw = raw.get("hehun_wuxing", {}) or {}
    hs = raw.get("hehun_shengxiao", {}) or {}
    hr = raw.get("hehun_rizhu", {}) or {}
    cp = raw.get("compat", {}) or {}
    return "\n".join([
        f"总分：{union['score']} 分（合婚引擎 ×0.6 + 合盘引擎 ×0.4），等级「{union['levelLabel']}」。",
        f"一、合婚引擎：五行互补 {union['dimensions']['wuxing']['score']}/40（{hw.get('complement_desc', '—')}）；"
        f"生肖 {union['dimensions']['shengxiao']['score']}/25（{hs.get('description', '—')}）；"
        f"日柱 {union['dimensions']['rizhu']['score']}/35（{hr.get('description', '—')}）。",
        f"二、合盘引擎：日主五行关系 {cp.get('wuxing_relation', '—')}（基准 {cp.get('base_score', '—')} 分）；"
        f"五行互补加成 +{cp.get('complement_bonus', 0)}；双天乙贵人加成 +{cp.get('shensha_bonus', 0)}；"
        f"夫妻宫六合加成 +{cp.get('combo_bonus', 0)}；综合 {cp.get('final_score', '—')} 分。",
        "以上明细均来自双方真实排盘的双引擎计算，可对照复核。",
    ])


def build_report(union: dict, bazi1, bazi2, retriever=None, llm=None) -> dict:
    """四章报告 + 引用 + 全文。每章独立兜底，单章失败不阻断其余章节。"""
    refs = _retrieve_classics(retriever, bazi1, bazi2)
    chapters = [
        {"title": "前世今生", "content": _build_qianshi(union, bazi1, bazi2, refs, llm)},
        {"title": "相处模式", "content": _build_xiangchu(union)},
        {"title": "矛盾点与化解", "content": _build_maodun(union)},
        {"title": "契合详情", "content": _build_qihe(union)},
    ]
    full_text = "\n\n".join(f"【{c['title']}】\n{c['content']}" for c in chapters)
    full_text += f"\n\n{TRUST_STATEMENT}"
    return {"chapters": chapters, "citations": refs, "full_text": full_text}
