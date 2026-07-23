"""合婚配对 API — 五行互补 + 生肖 + 日柱分析.

提供 RESTful POST /api/hehun 接口，接收双方八字信息，返回合婚分析结果。
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["hehun"])

# ── Pydantic 模型 ─────────────────────────────────────────────────

class BaziInput(BaseModel):
    """单人生辰信息"""
    year: int
    month: int
    day: int
    hour: int = 0
    minute: int = 0
    city: str = "北京"
    gender: str = "男"


class HehunRequest(BaseModel):
    """合婚请求：双方生辰"""
    person_a: BaziInput
    person_b: BaziInput


class HehunResponse(BaseModel):
    """合婚匹配结果"""
    total_score: int
    wuxing: dict
    shengxiao: dict
    rizhu: dict
    advice: list[str]
    summary: str


# ── 全局依赖注入 ──────────────────────────────────────────────────

_hehun_engine = None
_bazi_engine = None


def setup(hehun_engine, bazi_engine):
    """在主应用生命周期中注入引擎实例。"""
    global _hehun_engine, _bazi_engine
    _hehun_engine = hehun_engine
    _bazi_engine = bazi_engine


# ── API 端点 ─────────────────────────────────────────────────────

@router.post("/api/hehun", response_model=HehunResponse)
async def hehun_match(req: HehunRequest):
    """合婚匹配计算。

    接收双方八字信息，引擎计算五行互补、生肖配对、日柱关系，
    返回综合评分和详细分析。
    """
    if _hehun_engine is None or _bazi_engine is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Hehun service not ready")

    a = req.person_a
    b = req.person_b

    # 排盘
    r1 = _bazi_engine.calculate(a.year, a.month, a.day, a.hour, a.minute, a.city, a.gender)
    r2 = _bazi_engine.calculate(b.year, b.month, b.day, b.hour, b.minute, b.city, b.gender)

    # 合婚匹配
    result = _hehun_engine.match(r1, r2)

    return HehunResponse(
        total_score=result.score,
        wuxing={
            "score": result.bazi_match.get("score", 0),
            "detail": result.bazi_match.get("complement_desc", ""),
            "complement": result.bazi_match.get("complement_details", []),
            "deficiency": "",
        },
        shengxiao={
            "type": result.shengxiao,
            "score": result.shengxiao_score,
            "relation": result.shengxiao_detail.get("relation", ""),
            "shengxiao_1": result.shengxiao_detail.get("shengxiao1", ""),
            "shengxiao_2": result.shengxiao_detail.get("shengxiao2", ""),
        },
        rizhu={
            "score": result.rizhu_score,
            "detail": result.rizhu,
            "rizhi_relation": result.rizhu_detail.get("ri_zhi_relation", ""),
            "rigan_relation": result.rizhu_detail.get("ri_gan_relation", ""),
        },
        advice=result.advice.split('\n'),
        summary=f"综合评分：{result.score}/100。{result.bazi_match.get('complement_desc', '')}。{result.shengxiao}。{result.rizhu}。",
    )
