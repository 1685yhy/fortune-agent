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
    wuxing_score: int
    wuxing_detail: str
    shengxiao: dict
    rizhu: dict
    advice: str
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

    # 取生肖
    def _shengxiao_from_result(bazi_result) -> str:
        DZ = "子丑寅卯辰巳午未申酉戌亥"
        SX = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
        if bazi_result and bazi_result.bazi and len(bazi_result.bazi) > 0 and len(bazi_result.bazi[0]) > 1:
            zhi = bazi_result.bazi[0][1]
            if zhi in DZ:
                return SX[DZ.index(zhi)]
        return ""

    sx1 = _shengxiao_from_result(r1)
    sx2 = _shengxiao_from_result(r2)

    return HehunResponse(
        total_score=result.score,
        wuxing_score=result.wuxing_score,
        wuxing_detail=result.bazi_match.get("complement_desc", ""),
        shengxiao={
            "type": result.shengxiao,
            "score": result.shengxiao_score,
            "relation": result.shengxiao_detail.get("relation", ""),
            "shengxiao_1": sx1,
            "shengxiao_2": sx2,
        },
        rizhu={
            "score": result.rizhu_score,
            "detail": result.rizhu,
            "rizhi_relation": result.rizhu_detail.get("ri_zhi_relation", ""),
            "rigan_relation": result.rizhu_detail.get("ri_gan_relation", ""),
        },
        advice=result.advice,
        summary=f"综合评分：{result.score}/100。{result.bazi_match.get('complement_desc', '')}。{result.shengxiao}。{result.rizhu}。",
    )
