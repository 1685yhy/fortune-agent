"""感情合盘 API — POST /api/love/compatibility（A1 缺口：前端 404 → 已接通）.

前端契约（miniprogram/utils/api.js getLoveCompatibility，love 页调用）：
    POST /api/love/compatibility
    body: {birthYear1, birthMonth1, birthDay1, birthHour1, gender1,
           birthYear2, birthMonth2, birthDay2, birthHour2, gender2, paid}
    （love 页还有简化输入：{myBirth, taBirth}，同样支持）

实现：复用 compatibility.py 的规则化合盘分析（日主五行生克 + 夫妻宫六合 +
天乙贵人 + 五行互补 + 纳音），双方命盘全部由 BaziEngine 真实排盘得出——
禁止伪随机假结果（前端 love 页原失败后静默生成假结果，本接口接通后显示真实数据）。

- paid=false：摘要版（综合分 + 等级 + 摘要 + 优点/注意/建议）
- paid=true ：完整版（性格/缘分/婚姻/子女/建议五大章节 + 付费墙标记）
"""
import logging
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.security.auth import require_user
from src.config import is_experience_mode
from src.engines.bazi import BaziEngine
from .birth_contract import normalize_gender, normalize_hour, parse_birth_str
from .compatibility import (
    PersonInfo,
    _compute_match_score,
    _generate_strengths,
    _generate_warnings,
    _generate_advice,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["love"])

_engine = BaziEngine()

# 前端 love.js SCORE_LEVELS 等级划分（与页面展示一致）
SCORE_LEVELS = [
    {"min": 90, "label": "天作之合", "sublabel": "Perfect Match"},
    {"min": 80, "label": "情投意合", "sublabel": "Soul Connection"},
    {"min": 70, "label": "相得益彰", "sublabel": "Complementary"},
    {"min": 60, "label": "和而不同", "sublabel": "Harmony in Diversity"},
    {"min": 0, "label": "细水长流", "sublabel": "Gentle Flow"},
]

_WX_PERSONALITY = {
    "金": "刚毅果决，讲原则重信义，处事雷厉风行",
    "木": "仁厚向上，富同情心，喜欢不断成长进取",
    "水": "聪慧灵动，善于变通，直觉敏锐感知力强",
    "火": "热情明快，行动力强，待人真诚充满感染力",
    "土": "稳重诚信，踏实可靠，包容心强值得信赖",
}

# 每个等级固定配两句经典诗文，按双方日主确定性选取（非随机）
_QUOTES_BY_LEVEL = {
    "天作之合": [
        "金风玉露一相逢，便胜却人间无数。——秦观",
        "在天愿作比翼鸟，在地愿为连理枝。——白居易",
    ],
    "情投意合": [
        "身无彩凤双飞翼，心有灵犀一点通。——李商隐",
        "愿得一心人，白首不相离。——卓文君",
    ],
    "相得益彰": [
        "结发为夫妻，恩爱两不疑。——苏武",
        "相知无远近，万里尚为邻。——张九龄",
    ],
    "和而不同": [
        "两情若是久长时，又岂在朝朝暮暮。——秦观",
        "君子和而不同。——《论语》",
    ],
    "细水长流": [
        "执子之手，与子偕老。——《诗经》",
        "此情可待成追忆，只是当时已惘然。——李商隐",
    ],
}

# 付费墙：完整版章节名（摘要版仅返回锁定提示）
_PAID_SECTION_NAMES = {
    "personality": "性格分析",
    "fate": "缘分解析",
    "marriage": "婚姻前景",
    "children": "子女缘分析",
}

# 前端 payment.js 中该产品的定价
LOVE_PRODUCT_PRICE = 9.9


# ── 请求模型 ────────────────────────────────────────────────────

class LoveCompatibilityRequest(BaseModel):
    birthYear1: Optional[int] = None
    birthMonth1: Optional[int] = None
    birthDay1: Optional[int] = None
    birthHour1: Optional[int] = None
    gender1: Optional[Union[int, str]] = None
    birthYear2: Optional[int] = None
    birthMonth2: Optional[int] = None
    birthDay2: Optional[int] = None
    birthHour2: Optional[int] = None
    gender2: Optional[Union[int, str]] = None
    paid: bool = False
    # 简化输入（love 页 v6 简单模式：'1992-08-15'）
    myBirth: Optional[str] = None
    taBirth: Optional[str] = None


# ── 分析章节生成（全部由双方真实命盘规则化生成）──────────────

def _get_level(score: int) -> dict:
    for level in SCORE_LEVELS:
        if score >= level["min"]:
            return level
    return SCORE_LEVELS[-1]


def _build_personality(match: dict, r1, r2) -> str:
    dm1, dm2 = r1.day_master, r2.day_master
    wx1, wx2 = match["dm1_wx"], match["dm2_wx"]
    p1 = _WX_PERSONALITY.get(wx1, "性格鲜明")
    p2 = _WX_PERSONALITY.get(wx2, "性格鲜明")
    relation = match["wuxing_relation"]
    if relation == "相生":
        rel_desc = (f"双方日主{wx1}{wx2}相生，一方的付出恰好滋养另一方的成长，"
                    f"性格天然互补，相处时容易形成默契的配合。")
    elif relation == "比和":
        rel_desc = (f"双方日主同为{wx1}（{wx2}）五行，性格特质相近、志趣相投，"
                    f"容易产生强烈共鸣，但也要留意相似带来的平淡。")
    else:
        rel_desc = (f"双方日主{wx1}{wx2}相克，性格差异明显，一动一静、一快一慢，"
                    f"需要刻意练习沟通与包容，磨合之后反而互补出彩。")
    fit = "十分契合" if match["complement_bonus"] > 0 else "有清晰的互补空间"
    return (f"TA 的日主为{dm1}，气性属「{wx1}」：{p1}；对方的日主为{dm2}，"
            f"属「{wx2}」：{p2}。{rel_desc}这对组合在性格层面{fit}，"
            f"彼此的差异正是关系中最值得经营的部分。")


def _build_fate(match: dict, r1, r2) -> str:
    score = match["final_score"]
    level = _get_level(score)["label"]
    parts = [f"从双方命盘综合来看，这段缘分的契合度评分为 {score} 分，属「{level}」之列。"]
    if match["combo_bonus"] > 0:
        parts.append(
            f"双方日支（夫妻宫）{r1.bazi[2][1]}{r2.bazi[2][1]}构成六合，为姻缘中的吉象，"
            f"感情基础深厚，容易一见如故。")
    else:
        parts.append(
            f"双方日支（夫妻宫）{r1.bazi[2][1]}与{r2.bazi[2][1]}无合冲，"
            f"缘分属于细水长流型，感情在相处中逐步加深。")
    if "天乙贵人" in r1.shensha and "天乙贵人" in r2.shensha:
        parts.append(
            "更难得的是双方命盘皆带天乙贵人，互为对方生命中的贵人，能相互扶持、共同成长。")
    if match["complement_bonus"] > 0:
        parts.append(
            "五行层面一方的强项恰好补足另一方的弱项，这种互补结构意味着两人在一起"
            "能产生「1+1>2」的良性循环。")
    return "".join(parts)


def _build_marriage(match: dict, r1, r2) -> str:
    dz1, dz2 = r1.bazi[2][1], r2.bazi[2][1]
    wx1, wx2 = match["dm1_wx"], match["dm2_wx"]
    parts = []
    if match["combo_bonus"] > 0:
        parts.append(f"日支（夫妻宫）{dz1}{dz2}六合，主婚姻和顺、夫妻感情融洽，家庭观念趋同。")
    else:
        parts.append(f"日支（夫妻宫）{dz1}与{dz2}无合局，婚姻需要更多经营与包容，"
                     f"日常沟通质量决定感情的走向。")
    relation = match["wuxing_relation"]
    if relation == "相生":
        parts.append(f"日主{wx1}{wx2}相生，婚姻中一方愿意付出、一方懂得感恩，"
                     f"是「你中有我、我中有你」的良性结构。")
    elif relation == "比和":
        parts.append(f"日主同属{wx1}五行，婚姻中双方势均力敌，平等尊重、共同进退是长久之道。")
    else:
        parts.append(f"日主{wx1}{wx2}相克，婚姻中易出现观点碰撞，关键在于把"
                     f"「谁对谁错」换成「怎样对我们都好」。")
    return "".join(parts)


def _build_children(match: dict, r1, r2) -> str:
    # 时柱为子女宫，时干十神（食神/伤官）为子女星
    kids_star1 = r1.shishen[3] if len(r1.shishen) > 3 else ""
    kids_star2 = r2.shishen[3] if len(r2.shishen) > 3 else ""
    t1, t2 = r1.bazi[3] if len(r1.bazi) > 3 else "", r2.bazi[3] if len(r2.bazi) > 3 else ""
    parts = [
        f"以时柱为子女宫：TA 的时柱为{t1}（{kids_star1 or '无'}），"
        f"对方的时柱为{t2}（{kids_star2 or '无'}）。",
    ]
    if kids_star1 in ("食神", "伤官") or kids_star2 in ("食神", "伤官"):
        parts.append("命带食神或伤官透于时柱，子女缘较好，与子女缘分深厚，教育上宜因材施教。")
    else:
        parts.append("时柱未见食伤透出，子女缘属平常，顺其自然即可，家庭重心可多放在伴侣关系经营上。")
    return "".join(parts)


def _build_summary(match: dict, r1, r2) -> str:
    score = match["final_score"]
    level = _get_level(score)
    level_desc = {
        "天作之合": "双方五行相合、气场相融，是难得的好缘分",
        "情投意合": "两人非常般配，虽有细微差异，但整体非常和谐",
        "相得益彰": "两人有不错的缘分基础，差异中蕴含彼此成就的潜力",
        "和而不同": "两人各有特点，尊重差异、用心经营是长久之道",
        "细水长流": "两人缘分中平，需要更多的耐心与包容去培育",
    }[level["label"]]
    return (f"综合评分 {score} 分，属「{level['label']}」。{level_desc}。"
            f"日主{match['dm1_wx']}与{match['dm2_wx']}为{match['wuxing_relation']}关系，"
            f"{match['relation_desc']}")


def _build_quote(match: dict, r1, r2) -> str:
    level = _get_level(match["final_score"])["label"]
    quotes = _QUOTES_BY_LEVEL[level]
    # 按双方日主确定性选取，保证同一对情侣结果稳定
    seed = sum(ord(c) for c in (r1.day_master + r2.day_master))
    return quotes[seed % len(quotes)]


# ── 分析主流程 ──────────────────────────────────────────────────

def _resolve_person(req: LoveCompatibilityRequest, which: int) -> PersonInfo:
    """从请求中解析单人生辰（支持显式字段与 myBirth/taBirth 字符串两种契约）。"""
    if which == 1:
        year, month, day = req.birthYear1, req.birthMonth1, req.birthDay1
        hour, gender = req.birthHour1, req.gender1
        birth_str = req.myBirth
    else:
        year, month, day = req.birthYear2, req.birthMonth2, req.birthDay2
        hour, gender = req.birthHour2, req.gender2
        birth_str = req.taBirth

    if year is None or month is None or day is None:
        parsed = parse_birth_str(birth_str)
        if parsed is None:
            raise HTTPException(
                status_code=400,
                detail=f"缺少第 {which} 方生辰：需提供 birthYear{which}/birthMonth{which}/birthDay{which} "
                       f"或格式为 YYYY-MM-DD 的 myBirth/taBirth",
            )
        year, month, day = parsed
        return PersonInfo(year=year, month=month, day=day, hour=12,
                          minute=0, gender="男", city="北京")

    return PersonInfo(
        year=int(year), month=int(month), day=int(day),
        hour=normalize_hour(hour), minute=0,
        gender=normalize_gender(gender), city="北京",
    )


def run_love_compatibility(req: LoveCompatibilityRequest) -> dict:
    """完整合盘分析流水线（纯规则，无 LLM：确定、快速、可离线）。"""
    p1 = _resolve_person(req, 1)
    p2 = _resolve_person(req, 2)

    result1 = _engine.calculate(p1.year, p1.month, p1.day, p1.hour, p1.minute,
                                p1.city, p1.gender)
    result2 = _engine.calculate(p2.year, p2.month, p2.day, p2.hour, p2.minute,
                                p2.city, p2.gender)

    match = _compute_match_score(result1, result2)
    score = match["final_score"]
    level = _get_level(score)

    strengths = _generate_strengths(match, result1, result2)
    warnings = _generate_warnings(match, result1, result2)
    advice = _generate_advice(match)
    summary = _build_summary(match, result1, result2)

    sections = {
        "personality": _build_personality(match, result1, result2),
        "fate": _build_fate(match, result1, result2),
        "marriage": _build_marriage(match, result1, result2),
        "children": _build_children(match, result1, result2),
    }
    quote = _build_quote(match, result1, result2)

    # 体验模式（EXPERIENCE_MODE=true）：paid=false 也强制按完整版（已付费）逻辑返回
    exp_mode = is_experience_mode()
    paid = req.paid or exp_mode

    if not paid:
        # 摘要版：真实分数/摘要/要点全给，四章详细分析锁定
        for key, name in _PAID_SECTION_NAMES.items():
            sections[key] = f"🔒 {name}为付费内容，解锁完整版合盘报告后可查看"

    return {
        "score": score,
        "match_score": score,
        "levelLabel": level["label"],
        "levelSublabel": level["sublabel"],
        "summary": summary,
        "personality": sections["personality"],
        "fate": sections["fate"],
        "marriage": sections["marriage"],
        "children": sections["children"],
        "advice": advice,
        "advice_analysis": "；".join(advice),
        "quote": quote,
        "strengths": strengths,
        "warnings": warnings,
        "charts": {
            "user1": {
                "bazi": " ".join(result1.bazi),
                "day_master": result1.day_master,
                "wuxing": result1.wuxing,
                "geju": result1.geju,
                "yongshen": result1.yongshen,
                "shensha": result1.shensha,
            },
            "user2": {
                "bazi": " ".join(result2.bazi),
                "day_master": result2.day_master,
                "wuxing": result2.wuxing,
                "geju": result2.geju,
                "yongshen": result2.yongshen,
                "shensha": result2.shensha,
            },
        },
        "paid": paid,
        "unlocked": paid,
        "experienceMode": exp_mode,
        "paywall": {
            "locked": not paid,
            "unlocked": paid,
            "product": "love_compatibility",
            "price": LOVE_PRODUCT_PRICE,
            "message": ("已解锁完整版合盘报告" if paid
                        else "付费解锁完整合盘报告（性格/缘分/婚姻/子女四大章节）"),
        },
        "shareable": True,
    }


# ── API 端点 ────────────────────────────────────────────────────

@router.post("/api/love/compatibility")
async def love_compatibility(req: LoveCompatibilityRequest, uid: str = Depends(require_user)):
    """感情合盘分析。

    安全修复：必须登录（双方生辰为敏感数据），user_id 取 JWT sub。
    """
    try:
        return run_love_compatibility(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("感情合盘分析失败")
        raise HTTPException(status_code=500, detail=f"合盘分析失败: {str(e)}")
