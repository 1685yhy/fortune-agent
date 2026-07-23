"""姓名学 API — 五格剖象 + 三才分析.

提供 RESTful POST /api/xingming 接口，接收姓名和性别，返回五格数理、
三才配置、81数理解读及可选八字匹配分析。
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..services.narrative import NarrativeService

router = APIRouter(tags=["xingming"])

# ── Pydantic 模型 ─────────────────────────────────────────────────


class XingmingRequest(BaseModel):
    """姓名学请求"""
    surname: str
    given_name: str
    gender: str = "男"


class XingmingResponse(BaseModel):
    """姓名学分析结果"""
    wuge: dict
    sancai: str
    sancai_ji: str
    stroke_counts: dict
    analysis: dict
    overall: str
    wuxing: dict
    bazi_match: Optional[dict] = None
    narrative: str = ""


# ── 全局依赖注入 ──────────────────────────────────────────────────

_xingming_engine = None
_narrative: Optional[NarrativeService] = None


def setup(xingming_engine, narrative: NarrativeService = None) -> None:
    """在主应用生命周期中注入 XingmingEngine 实例。"""
    global _xingming_engine, _narrative
    _xingming_engine = xingming_engine
    _narrative = narrative


# ── API 端点 ──────────────────────────────────────────────────────


@router.post("/api/xingming", response_model=XingmingResponse)
async def analyze_xingming(req: XingmingRequest):
    """姓名分析 — 返回五格、三才、81数理及综合评判。"""
    if _xingming_engine is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="姓名学引擎暂不可用")

    result = _xingming_engine.analyze(
        surname=req.surname,
        given_name=req.given_name,
        gender=req.gender,
    )

    # LLM narrative
    narrative_text = ""
    if _narrative:
        try:
            result_dict = {
                "surname": req.surname,
                "given_name": req.given_name,
                "gender": req.gender,
                "wuge": result.wuge,
                "sancai": result.sancai,
                "sancai_ji": result.sancai_ji,
                "stroke_counts": result.stroke_counts,
                "analysis": result.analysis,
                "overall": result.overall,
                "wuxing": result.wuxing,
            }
            narrative_text = _narrative.xingming(result_dict)
        except Exception:
            pass

    return XingmingResponse(
        wuge=result.wuge,
        sancai=result.sancai,
        sancai_ji=result.sancai_ji,
        stroke_counts=result.stroke_counts,
        analysis=result.analysis,
        overall=result.overall,
        wuxing=result.wuxing,
        bazi_match=None,
        narrative=narrative_text,
    )
