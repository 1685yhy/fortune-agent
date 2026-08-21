"""万年历 API — GET /api/wannianli（月视图）+ GET /api/wannianli/day（日详情）。

问真"吉真万年历"同款确定性功能（0 LLM）：lunar-python 历法 + 建除/黄黑道宜忌规则
（引擎见 src/engines/wannianli.py，规则来源注释见该文件头部）。
全接口 require_user 鉴权；结果与用户无关且历法数据永不变 → 全局缓存 24h。
"""
import calendar as _cal
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.engines.wannianli import WannianliEngine, MIN_YEAR, MAX_YEAR
from src.security.auth import require_user
from src.utils.cache import get_cache, TTL_WANNIANLI

router = APIRouter(tags=["wannianli"])

_engine = WannianliEngine()
BJT = timezone(timedelta(hours=8))


def _validate_date(date_str: str) -> tuple:
    """解析校验 YYYY-MM-DD，返回 (year, month, day)；非法抛 400。"""
    try:
        year, month, day = (int(x) for x in date_str.split("-"))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="日期格式须为 YYYY-MM-DD")
    if not (MIN_YEAR <= year <= MAX_YEAR):
        raise HTTPException(
            status_code=400,
            detail=f"年份须在 {MIN_YEAR}-{MAX_YEAR} 之间",
        )
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="月份须在 1-12 之间")
    if not (1 <= day <= _cal.monthrange(year, month)[1]):
        raise HTTPException(status_code=400, detail=f"日期不存在: {date_str}")
    return year, month, day


@router.get("/api/wannianli")
async def get_month_view(
    year: Optional[int] = Query(None, description="公历年份（默认今年，北京时间）"),
    month: Optional[int] = Query(None, description="公历月份 1-12（默认当月）"),
    _uid: str = Depends(require_user),
):
    """万年历月视图：当月每日 公历/农历/干支日/节气/宜忌简表/黄黑道/建除。

    确定性纯规则（0 LLM）；响应缓存 24h（历法数据不可变）。
    """
    now = datetime.now(BJT)
    y = year if year is not None else now.year
    m = month if month is not None else now.month

    if not (MIN_YEAR <= y <= MAX_YEAR):
        raise HTTPException(
            status_code=400,
            detail=f"年份须在 {MIN_YEAR}-{MAX_YEAR} 之间",
        )
    if not (1 <= m <= 12):
        raise HTTPException(status_code=400, detail="月份须在 1-12 之间")

    cache = get_cache()
    key = f"wannianli:month:{y}-{m:02d}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    try:
        data = _engine.month_view(y, m)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cache.set(key, data, ttl_seconds=TTL_WANNIANLI)
    return data


@router.get("/api/wannianli/day")
async def get_day_detail(
    date: str = Query(..., description="公历日期 YYYY-MM-DD"),
    _uid: str = Depends(require_user),
):
    """万年历单日详情：干支/纳音/宜忌/吉神凶煞/冲煞/值神/建除/方位/旬空。

    确定性纯规则（0 LLM）；响应缓存 24h。
    """
    year, month, day = _validate_date(date)

    cache = get_cache()
    key = f"wannianli:day:{date}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    try:
        data = _engine.day_detail(year, month, day)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cache.set(key, data, ttl_seconds=TTL_WANNIANLI)
    return data
