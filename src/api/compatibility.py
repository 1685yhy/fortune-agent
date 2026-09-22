"""合盘功能 API — Phase 4 Social Virality.

Provides:
  POST /api/compatibility → match analysis between two people
  GET  /compatibility     → HTML form page

Uses BaziEngine to compute charts for both users, then calls LLM
to generate the compatibility analysis.
"""
import json
import logging
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.security.auth import require_user
from src.engines.bazi import BaziEngine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["compatibility"])

_engine = BaziEngine()
_llm_ref = None  # Set by main.py


def setup(llm=None):
    """Set LLM reference at startup."""
    global _llm_ref
    _llm_ref = llm


# ─── Models ────────────────────────────────────────────────────

class PersonInfo(BaseModel):
    year: int
    month: int
    day: int
    hour: int = 12
    minute: int = 0
    gender: str = "男"
    city: str = "北京"
    name: str = ""
    # k32（A10）REST 契约收口：与 hehun/paipan 的 BaziInput、union（同 import）
    # 同口径透传三个排盘开关——缺省值与 BaziEngine.calculate 缺省逐一对齐
    # （旧调用方不传 → 行为零变化；契约测试 test_k32_rest_solar_contract 锁三方一致）。
    daylightSaving: bool = False   # 夏令时：1986-1991 区间出生时刻减 1 小时再排盘
    lateChildHour: bool = False    # 早晚子时专业档：23:00-24:00 日柱按当天
    solarTime: bool = True         # 真太阳时（R2-4 产品口径默认开，可关）


class CompatibilityRequest(BaseModel):
    user1: PersonInfo
    user2: PersonInfo


# ─── Analysis Engine ───────────────────────────────────────────

_WUXING_RELATIONS = {
    ("金", "金"): ("比和", 85, "金金比和，刚毅相济，志同道合。"),
    ("木", "木"): ("比和", 80, "木木比和，生机勃勃，共同成长。"),
    ("水", "水"): ("比和", 80, "水水比和，相濡以沫，感情深厚。"),
    ("火", "火"): ("比和", 75, "火火比和，热情洋溢，活力四射。"),
    ("土", "土"): ("比和", 80, "土土比和，稳重踏实，相得益彰。"),
    ("金", "木"): ("相克", 50, "金木相克，各有主见，需要互相包容。"),
    ("木", "金"): ("相克", 50, "木金相克，性格差异较大，需磨合。"),
    ("金", "火"): ("相克", 45, "火金相克，热情与理智的碰撞。"),
    ("火", "金"): ("相克", 45, "火金相克，需要找到共同节奏。"),
    ("金", "水"): ("相生", 90, "金水相生，智慧与灵动的完美结合。"),
    ("水", "金"): ("相生", 90, "水金相生，相辅相成，极为默契。"),
    ("木", "火"): ("相生", 88, "木火相生，温暖而充满希望。"),
    ("火", "木"): ("相生", 88, "火木相生，热情似火，彼此成就。"),
    ("木", "土"): ("相克", 55, "木土相克，理想与现实的拉锯。"),
    ("土", "木"): ("相克", 55, "土木相克，需要理解彼此的节奏。"),
    ("水", "火"): ("相克", 48, "水火相克，冰与火的碰撞，充满挑战。"),
    ("火", "水"): ("相克", 48, "火水相克，需要极大的包容和理解。"),
    ("水", "土"): ("相克", 52, "水土相克，柔与刚的较量。"),
    ("土", "水"): ("相克", 52, "土水相克，需要找到平衡点。"),
    ("木", "水"): ("相生", 85, "木水相生，滋养与成长的良性循环。"),
    ("水", "木"): ("相生", 85, "水木相生，润物无声的默契。"),
    ("火", "土"): ("相生", 82, "火土相生，热情孕育成果。"),
    ("土", "火"): ("相生", 82, "土火相生，稳重与热情的融合。"),
    ("金", "土"): ("相生", 83, "金土相生，坚实可靠的基础。"),
    ("土", "金"): ("相生", 83, "土金相生，稳中求进的默契。"),
}


def _compute_match_score(user1_result, user2_result) -> dict:
    """Compute match score based on wuxing interaction of day masters."""
    dm1 = user1_result.day_master  # e.g., "乙木"
    dm2 = user2_result.day_master  # e.g., "庚金"

    wx1 = dm1[-1]  # 木
    wx2 = dm2[-1]  # 金

    # Look up wuxing relation
    key = (wx1, wx2)
    if key in _WUXING_RELATIONS:
        relation, base_score, relation_desc = _WUXING_RELATIONS[key]
    else:
        relation, base_score, relation_desc = "中性", 65, "五行关系中性，有发展空间。"

    # Adjust score based on wuxing balance
    wx_data1 = user1_result.wuxing
    wx_data2 = user2_result.wuxing

    # Check if one's strong element is the other's weak element → good complement
    sorted1 = sorted(wx_data1.items(), key=lambda x: -x[1])
    sorted2 = sorted(wx_data2.items(), key=lambda x: -x[1])
    strongest1 = sorted1[0][0]
    strongest2 = sorted2[0][0]
    weakest1 = sorted1[-1][0]
    weakest2 = sorted2[-1][0]

    complement_bonus = 0
    if strongest1 == weakest2 or strongest2 == weakest1:
        complement_bonus = 10

    # Check shensha / guiren
    shensha_bonus = 0
    if "天乙贵人" in user1_result.shensha and "天乙贵人" in user2_result.shensha:
        shensha_bonus = 5

    # Check day branch (夫妻宫) compatibility
    day_zhi1 = user1_result.bazi[2][1]
    day_zhi2 = user2_result.bazi[2][1]
    combo_bonus = 0
    # Six combinations (六合)
    liuhe = {"子": "丑", "丑": "子", "寅": "亥", "亥": "寅",
             "卯": "戌", "戌": "卯", "辰": "酉", "酉": "辰",
             "巳": "申", "申": "巳", "午": "未", "未": "午"}
    if day_zhi1 in liuhe and liuhe[day_zhi1] == day_zhi2:
        combo_bonus = 10

    final_score = min(100, max(0, base_score + complement_bonus + shensha_bonus + combo_bonus))

    return {
        "base_score": base_score,
        "wuxing_relation": relation,
        "relation_desc": relation_desc,
        "complement_bonus": complement_bonus,
        "shensha_bonus": shensha_bonus,
        "combo_bonus": combo_bonus,
        "final_score": final_score,
        "dm1_wx": wx1,
        "dm2_wx": wx2,
    }


def _generate_strengths(match_result: dict, user1_result, user2_result) -> list:
    """Generate 2-3 strength statements."""
    strengths = []
    wx1 = match_result["dm1_wx"]
    wx2 = match_result["dm2_wx"]

    if match_result["wuxing_relation"] == "相生":
        strengths.append(f"双方五行相生（{wx1}→{wx2}），能量互补，相互促进。")
    elif match_result["wuxing_relation"] == "比和":
        strengths.append(f"双方五行相同（{wx1}与{wx2}），志趣相投，容易产生共鸣。")
    else:
        strengths.append("双方虽有不同，但差异中蕴含着互相学习的机会。")

    if match_result["complement_bonus"] > 0:
        strengths.append("双方五行互补，一方的优势恰好弥补另一方的短板，相互成就。")

    if match_result["combo_bonus"] > 0:
        day_zhi1 = user1_result.bazi[2][1]
        day_zhi2 = user2_result.bazi[2][1]
        strengths.append(f"夫妻宫相合（{day_zhi1}{day_zhi2}六合），感情基础牢固，姻缘深厚。")

    if "天乙贵人" in user1_result.shensha and "天乙贵人" in user2_result.shensha:
        strengths.append("双方皆带天乙贵人，互为对方的贵人，能共同成长。")

    if not strengths:
        strengths.append("双方各有特点，有共同成长的空间。")

    return strengths[:3]


def _generate_warnings(match_result: dict, user1_result, user2_result) -> list:
    """Generate 1-2 warning statements."""
    warnings = []
    wx1 = match_result["dm1_wx"]
    wx2 = match_result["dm2_wx"]

    if match_result["wuxing_relation"] == "相克":
        warnings.append(f"五行相克（{wx1}{wx2}克），日常相处需要注意沟通方式，避免因性格差异产生摩擦。")

    wx_data1 = user1_result.wuxing
    wx_data2 = user2_result.wuxing

    # Check if both are same weakness
    sorted1 = sorted(wx_data1.items(), key=lambda x: -x[1])
    sorted2 = sorted(wx_data2.items(), key=lambda x: -x[1])
    weakest1 = sorted1[-1][0]
    weakest2 = sorted2[-1][0]
    if weakest1 == weakest2 and wx_data1.get(weakest1, 0) <= 1 and wx_data2.get(weakest2, 0) <= 1:
        warnings.append(f"双方{wx1}元素皆弱，在相关领域（对应{wx1}的方面）需要特别注意互相支持。")
    else:
        warnings.append("2026年流年丙午，火旺之年，注意情绪管理和沟通，避免因小事争执。")

    return warnings[:2]


def _generate_advice(match_result: dict) -> list:
    """Generate 2-3 advice items."""
    score = match_result["final_score"]
    advice = []

    advice.append("建议双方在财务上保持透明，共同规划未来1-3年的财务目标。")

    if score >= 80:
        advice.append("双方缘分深厚，建议在重要时间节点（如七夕、春节）共同做一次深度交流，巩固感情。")
    elif score >= 60:
        advice.append("建议培养共同爱好，增加相处时间，逐步建立更深的情感连接。")
    else:
        advice.append("建议多给对方空间，以朋友的方式建立信任，不要急于推进关系。")

    advice.append("在决策时多参考对方的意见，兼顾双方的需求，避免单方面做重大决定。")

    return advice[:3]


def _generate_llm_analysis(user1: PersonInfo, user2: PersonInfo,
                           user1_result, user2_result, match_result: dict) -> str:
    """Use LLM to generate a rich compatibility analysis if available."""
    global _llm_ref

    if not _llm_ref or not hasattr(_llm_ref, 'api_key') or not _llm_ref.api_key:
        # Fallback: generate summary from match data
        return _generate_summary(match_result, user1_result, user2_result)

    try:
        dm1 = user1_result.day_master
        dm2 = user2_result.day_master
        wx1 = match_result["dm1_wx"]
        wx2 = match_result["dm2_wx"]

        prompt = f"""你是一位专业的合婚命理分析师。请根据以下信息，为这对用户生成一份合婚分析报告。

## 用户1
姓名：{user1.name or '用户A'}
八字：{' '.join(user1_result.bazi)}
日主：{dm1}
五行分布：{user1_result.wuxing}
格局：{user1_result.geju}
用神：{user1_result.yongshen}
神煞：{'、'.join(user1_result.shensha) if user1_result.shensha else '无'}

## 用户2
姓名：{user2.name or '用户B'}
八字：{' '.join(user2_result.bazi)}
日主：{dm2}
五行分布：{user2_result.wuxing}
格局：{user2_result.geju}
用神：{user2_result.yongshen}
神煞：{'、'.join(user2_result.shensha) if user2_result.shensha else '无'}

## 五行关系：{wx1}与{wx2}为{match_result['wuxing_relation']}关系
## 综合匹配分：{match_result['final_score']}/100

请输出以下格式：
1. 总体评价（100-150字，概括两人的缘分和相处前景）
2. 优势分析（分3点，每点30-50字）
3. 注意事项（分2点，每点30-50字）
4. 改善建议（分2点，每点30-50字）

风格要求：专业但温暖，用现代语言，不要用文言文。"""

        result = _llm_ref._call_deepseek_model(
            prompt,
            _llm_ref.model,
            max_tokens=1000,
            timeout=30.0,
        )
        return result.response
    except Exception as e:
        logger.warning(f"LLM compatibility analysis failed: {e}")
        return _generate_summary(match_result, user1_result, user2_result)


def _generate_summary(match_result: dict, user1_result, user2_result) -> str:
    """Generate a readable summary from match data."""
    score = match_result["final_score"]
    wx1 = match_result["dm1_wx"]
    wx2 = match_result["dm2_wx"]
    relation = match_result["wuxing_relation"]

    if score >= 85:
        level = "天作之合"
        desc = "两人五行相合，气场相融，是难得的好缘分。"
    elif score >= 70:
        level = "上等姻缘"
        desc = "两人非常般配，虽有细微差异，但整体非常和谐。"
    elif score >= 55:
        level = "中等缘分"
        desc = "两人有不错的缘分基础，需要用心经营。"
    else:
        level = "待磨合"
        desc = "两人差异较大，需要更多的包容和理解。"

    return f"""## 总体评价：{level}（{score}分）

{desc}

日主{wx1}与{wx2}为{relation}关系，{match_result['relation_desc']}

## 相处建议
双方应当珍惜这段缘分，在相处中多沟通、多理解。{"五行互补性强" if match_result['complement_bonus'] > 0 else "需要找到共同的兴趣和话题"}，{"夫妻宫相合说明姻缘深厚" if match_result['combo_bonus'] > 0 else "日常相处中注意互相尊重"}。"""


def run_compatibility_analysis(req: CompatibilityRequest) -> dict:
    """Full compatibility analysis pipeline."""
    # Compute bazi for both users
    # k32（A10）：三个排盘开关按人透传（与 hehun/paipan/union 同序同义；
    # 此前 REST 侧丢弃 → 前端/调用方传关闭也恒按缺省开排，与主链口径分裂）。
    result1 = _engine.calculate(
        year=req.user1.year, month=req.user1.month, day=req.user1.day,
        hour=req.user1.hour, minute=req.user1.minute,
        city=req.user1.city, gender=req.user1.gender,
        daylight_saving=req.user1.daylightSaving,
        late_child_hour=req.user1.lateChildHour,
        solar_time=req.user1.solarTime,
    )
    result2 = _engine.calculate(
        year=req.user2.year, month=req.user2.month, day=req.user2.day,
        hour=req.user2.hour, minute=req.user2.minute,
        city=req.user2.city, gender=req.user2.gender,
        daylight_saving=req.user2.daylightSaving,
        late_child_hour=req.user2.lateChildHour,
        solar_time=req.user2.solarTime,
    )

    # Compute match score
    match_result = _compute_match_score(result1, result2)

    # Generate strengths, warnings, advice
    strengths = _generate_strengths(match_result, result1, result2)
    warnings = _generate_warnings(match_result, result1, result2)
    advice = _generate_advice(match_result)

    # Generate LLM analysis (or fallback summary)
    summary = _generate_llm_analysis(
        req.user1, req.user2, result1, result2, match_result,
    )

    return {
        "match_score": match_result["final_score"],
        "summary": summary,
        "strengths": strengths,
        "warnings": warnings,
        "advice": advice,
        "shareable": True,
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
    }


# ─── API Endpoints ─────────────────────────────────────────────

@router.post("/api/compatibility")
async def compatibility_analysis(req: CompatibilityRequest, uid: str = Depends(require_user)):
    """Run compatibility analysis between two people.

    安全修复：必须登录（双方生辰信息为敏感数据）。
    """
    try:
        return run_compatibility_analysis(req)
    except Exception as e:
        logger.exception("合盘分析失败")
        raise HTTPException(status_code=500, detail=f"合盘分析失败: {str(e)}")
