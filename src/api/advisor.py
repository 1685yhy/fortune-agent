"""AI 建议 API — 基于八字命盘 + 用户处境生成个性化建议.

提供 POST /api/advisor 接口，接收用户 ID 和处境描述，
返回名人匹配 + 分领域行动建议。
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["advisor"])

# ── Pydantic 模型 ─────────────────────────────────────────────────


class AdvisorRequest(BaseModel):
    """AI 建议请求"""
    user_id: str
    context: str = ""  # 用户当前处境描述
    domains: List[str] = []  # 关注的领域，如 ["事业", "财运"]


class CelebrityMatch(BaseModel):
    """名人匹配信息"""
    name: str = ""
    similarity: int = 0
    bio: str = ""
    reason: str = ""


class AdviceItem(BaseModel):
    """单条建议"""
    suggestion: str = ""
    reasoning: str = ""
    timing: str = ""


class DomainAdvice(BaseModel):
    """领域建议"""
    career: List[AdviceItem] = []
    wealth: List[AdviceItem] = []
    love: List[AdviceItem] = []
    health: List[AdviceItem] = []
    growth: List[AdviceItem] = []


class AdvisorResponse(BaseModel):
    """AI 建议响应"""
    celebrity_matches: List[CelebrityMatch] = []
    advice: DomainAdvice = DomainAdvice()
    summary: str = ""


# ── 全局依赖注入 ──────────────────────────────────────────────────

_dao = None
_llm = None


def setup(dao, llm):
    """在主应用生命周期中注入 DAO 和 LLM 实例。"""
    global _dao, _llm
    _dao = dao
    _llm = llm


# ── API 端点 ─────────────────────────────────────────────────────


def _get_api_key() -> str:
    """从 LLM 实例中提取 API key。"""
    if _llm is None:
        return ""
    return getattr(_llm, 'api_key', '')


@router.post("/api/advisor", response_model=AdvisorResponse)
async def get_advisor(req: AdvisorRequest):
    """生成 AI 个性化建议。

    基于用户的八字命盘 + 当前处境，由 LLM 动态生成
    分领域行动建议，并匹配历史相似名人提供参考。
    """
    if _dao is None:
        raise HTTPException(status_code=503, detail="Advisor service not ready")

    # 1. 获取用户八字
    saved = _dao.get_user_bazi(req.user_id)
    if not saved:
        raise HTTPException(
            status_code=400,
            detail="用户未设置八字信息，请先通过聊天接口设置出生信息",
        )

    # 2. 排盘
    from src.engines.bazi import BaziEngine
    engine = BaziEngine()
    try:
        result = engine.calculate(
            saved["year"], saved["month"], saved["day"],
            saved["hour"], saved["minute"], saved["city"],
            saved["gender"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"排盘失败：{str(e)[:200]}")

    # 3. 调用 AdaptiveAdvisor
    from src.engines.advisor_v2 import AdaptiveAdvisor
    api_key = _get_api_key()
    advisor = AdaptiveAdvisor()
    try:
        advice_data = advisor.generate(
            result, user_context=req.context, api_key=api_key,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"建议生成失败：{str(e)[:200]}")

    # 4. 构建响应
    # 4a. 名人匹配
    celeb_matches = []
    celeb = advice_data.get("celebrity_match", {})
    if celeb and celeb.get("name"):
        celeb_matches.append(CelebrityMatch(
            name=celeb["name"],
            similarity=celeb.get("similarity", 0),
            bio=f"格局: {celeb.get('geju', '')}",
            reason=celeb.get("insight", ""),
        ))

    # 4b. 领域建议 — 按域过滤 & 转换
    domain_map = {
        "事业": "career",
        "财运": "wealth",
        "感情": "love",
        "健康": "health",
        "个人成长": "growth",
    }
    domain_advice = {v: [] for v in domain_map.values()}
    actions = advice_data.get("actions", [])

    for action in actions:
        cat = action.get("category", "")
        eng_cat = domain_map.get(cat)
        if not eng_cat:
            continue
        # 过滤用户指定的领域（如果传了 domains 参数）
        if req.domains and cat not in req.domains:
            continue
        domain_advice[eng_cat].append(AdviceItem(
            suggestion=action.get("advice", ""),
            reasoning=f"confidence: {action.get('confidence', 'medium')}",
            timing=action.get("timing", ""),
        ))

    # 4c. 摘要
    summary_parts = []
    insight = advice_data.get("insight", "")
    if insight:
        summary_parts.append(insight)
    daily_tip = advice_data.get("daily_tip", "")
    if daily_tip:
        summary_parts.append(f"今日贴士：{daily_tip}")
    style_notes = advice_data.get("style_notes", "")
    if style_notes:
        summary_parts.append(style_notes)
    summary = "\n".join(summary_parts)

    # 5. 记录审计
    if _dao:
        try:
            _dao.save_consultation(
                req.user_id,
                f"AI建议: {req.context[:100]}",
                result,
                intent="advisor",
            )
        except Exception:
            pass

    return AdvisorResponse(
        celebrity_matches=celeb_matches,
        advice=DomainAdvice(**domain_advice),
        summary=summary,
    )
