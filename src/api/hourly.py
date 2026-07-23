"""时辰运势 API — 十二时辰逐时吉凶分析.

提供 GET /api/hourly-fortune 接口，基于用户八字和指定日期，
返回当日12个时辰的逐时运势，包括吉凶等级、五行属性、适宜活动。
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from src.engines.hourly_fortune import get_hourly_fortune
from src.storage.dao import UserDAO

router = APIRouter(tags=["hourly"])

# 天干五行映射
STEM_WUXING = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}

# 天干 / 地支
STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# 全局引用，由 main.py 在 lifespan 中设置
_dao: Optional[UserDAO] = None


def setup(dao: UserDAO):
    """在应用启动时设置 DAO 引用。"""
    global _dao
    _dao = dao


def _day_stem_branch(date_str: str) -> tuple:
    """计算指定日期的干支.

    参考点：2026-01-01 = 乙巳日 (stem=1, branch=5).
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()
    ref = datetime(2026, 1, 1)
    days_diff = (dt - ref).days
    stem_idx = (1 + days_diff) % 10
    branch_idx = (5 + days_diff) % 12
    return STEMS[stem_idx], BRANCHES[branch_idx]


def _rating_label(rating: str) -> str:
    """Convert rating code to Chinese label."""
    labels = {
        "excellent": "大吉",
        "good": "吉",
        "fair": "平",
        "neutral": "平",
        "poor": "凶",
    }
    return labels.get(rating, rating)


@router.get("/api/hourly-fortune")
async def get_hourly_fortune_api(
    user_id: str = Query(..., description="用户 ID"),
    date: str = Query(None, description="日期 YYYY-MM-DD，默认今天"),
):
    """获取指定日期的十二时辰运势分析.

    基于用户八字和流日干支，逐时分析十二时辰的吉凶宜忌，
    并标注最佳和最差时段。

    Args:
        user_id: 用户 ID
        date: 日期 (YYYY-MM-DD)，可选，默认今天

    Returns:
        JSON 格式的十二时辰运势
    """
    global _dao
    if _dao is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Service not ready")

    # 获取日期
    if date is None:
        date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    # 计算当日干支
    day_stem, day_branch = _day_stem_branch(date)

    # 获取用户八字
    saved = _dao.get_user_bazi(user_id)
    if not saved:
        return {
            "status": "no_bazi",
            "message": "请先设置您的八字信息，才能生成时辰运势",
            "date": date,
            "day_ganzhi": f"{day_stem}{day_branch}",
            "slots": [],
        }

    # 提取用户日主
    bazi_list = saved.get("bazi", ["?"])
    user_day_stem = bazi_list[2] if len(bazi_list) >= 3 else "?"
    user_wx = STEM_WUXING.get(user_day_stem, "土")
    user_day_master = f"{user_wx}{user_day_stem}"

    # 获取十二时辰运势
    hourly = get_hourly_fortune(user_day_master, day_branch)

    # 构建返回数据
    slots = []
    for h in hourly:
        slots.append({
            "name": h["name"],
            "time": h["time"],
            "branch": h["branch"],
            "element": h["element"],
            "rating": h["rating"],
            "rating_label": _rating_label(h["rating"]),
            "reason": h["reason"],
            "activities": h["activities"],
        })

    # 最佳 / 最差时段
    best = [s for s in slots if s["rating"] == "excellent"]
    if not best:
        best = [s for s in slots if s["rating"] == "good"]
    worst = [s for s in slots if s["rating"] == "poor"]

    return {
        "status": "ok",
        "date": date,
        "day_ganzhi": f"{day_stem}{day_branch}",
        "day_stem": day_stem,
        "day_branch": day_branch,
        "user_day_master": user_day_master,
        "total_slots": len(slots),
        "slots": slots,
        "best_hours": [s["name"] for s in best[:3]],
        "worst_hours": [s["name"] for s in worst[:3]],
    }
