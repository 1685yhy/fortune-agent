"""命运日历 API — 每日运势日历。

提供 GET /api/calendar/today 接口，基于用户八字返回个性化每日运势。
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from src.engines.calendar import LuckyCalendar
from src.storage.dao import UserDAO

router = APIRouter(tags=["calendar"])

# 天干五行映射
STEM_WUXING = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}

# 全局引用，由 main.py 在 lifespan 中设置
_dao: Optional[UserDAO] = None
_handler_ref = None  # Will be set by main.py to provide LLM access


def setup(dao: UserDAO, handler=None):
    """在应用启动时设置 DAO 引用。"""
    global _dao, _handler_ref
    _dao = dao
    _handler_ref = handler


@router.get("/api/calendar/today")
async def get_today_calendar(
    user_id: str = Query(..., description="用户 ID"),
):
    """获取用户今日专属运势日历。

    基于用户八字和今日流日，生成个性化运势分析。
    包含日干支、五行属性、宜忌事项、个人建议和情绪提醒。

    Args:
        user_id: 用户 ID

    Returns:
        JSON 格式的今日运势日历
    """
    global _dao, _handler_ref

    # Try to get user's bazi from DAO
    saved = _dao.get_user_bazi(user_id) if _dao else None

    if not saved:
        return _generate_generic_calendar()

    # Get API key from handler's LLM
    api_key = ""
    personality = "sassy"
    preferences = ""
    if _handler_ref:
        if hasattr(_handler_ref, 'llm') and _handler_ref.llm:
            api_key = getattr(_handler_ref.llm, 'api_key', '')
        if hasattr(_handler_ref, '_get_personality_mode'):
            personality = _handler_ref._get_personality_mode(user_id) or "sassy"
        if hasattr(_handler_ref, '_get_preference_hint'):
            pref_hint = _handler_ref._get_preference_hint(user_id)
            if pref_hint:
                preferences = pref_hint

    try:
        cal = LuckyCalendar(api_key) if api_key else LuckyCalendar("")
        day = cal.daily(saved, date_str=None, personality=personality, preferences=preferences)
    except Exception:
        return _generate_generic_calendar()

    # Build the response
    day_ganzhi = f"{day.day_stem}{day.day_branch}" if day.day_stem else ""
    day_wuxing = STEM_WUXING.get(day.day_stem, "")

    # Extract action names from yi/ji for the simplified suitable/unsuitable lists
    suitable = [item.get("action", "") for item in day.yi if item.get("action")]
    unsuitable = [item.get("action", "") for item in day.ji if item.get("action")]

    # Generate personal_advice and mood_reminder
    user_day_stem = ""
    bazi_list = saved.get("bazi", [])
    if len(bazi_list) >= 3:
        user_day_stem = bazi_list[2] if bazi_list[2] else ""

    personal_advice, mood_reminder = _generate_advice(
        day_stem=day.day_stem,
        day_branch=day.day_branch,
        day_wuxing=day_wuxing,
        user_day_stem=user_day_stem,
        overall_mood=day.overall_mood,
    )

    return {
        "date": day.date,
        "day_ganzhi": day_ganzhi,
        "day_wuxing": day_wuxing,
        "suitable": suitable[:5],  # Max 5 items
        "unsuitable": unsuitable[:5],
        "personal_advice": personal_advice,
        "mood_reminder": mood_reminder,
    }


def _generate_advice(
    day_stem: str,
    day_branch: str,
    day_wuxing: str,
    user_day_stem: str,
    overall_mood: str,
) -> tuple:
    """生成个人建议和情绪提醒。

    Returns:
        (personal_advice, mood_reminder) 元组
    """
    # Wuxing of user's day master
    user_wuxing = STEM_WUXING.get(user_day_stem, "")

    # Relationship-based advice
    if day_wuxing and user_wuxing:
        generates = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
        controls = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

        if day_wuxing == user_wuxing:
            advice = f"今日{day_wuxing}旺，与你五行相同。能量共振，适合巩固已有成果，但也要注意不要过度自我。"
            mood = "今天精力充沛但可能固执，适合午间小憩放松。"
        elif generates.get(user_wuxing) == day_wuxing:
            advice = f"今日{day_wuxing}旺，生助你的{user_wuxing}。能量流入，适合推进重要事项。"
            mood = "今天状态不错，适合主动出击，但也要注意劳逸结合。"
        elif generates.get(day_wuxing) == user_wuxing:
            advice = f"你的{user_wuxing}生今日{day_wuxing}，能量有所消耗。注意保存精力，不要过度付出。"
            mood = "今天可能感觉有些疲惫，建议适当休息，避免熬夜。"
        elif controls.get(user_wuxing) == day_wuxing:
            advice = f"今日{day_wuxing}克制你的{user_wuxing}，外在压力较大。宜守不宜攻，保持耐心。"
            mood = "今天容易被外界影响情绪，建议给自己留出独处时间。"
        elif controls.get(day_wuxing) == user_wuxing:
            advice = f"你能克制今日的{day_wuxing}，掌控力强。适合解决棘手问题，发挥领导力。"
            mood = "今天思维清晰，效率较高，适合做重要决策。"
        else:
            advice = f"今日{day_wuxing}当值。顺势而为，关注自身感受。"
            mood = "保持平和心态，适当运动有助于提升能量。"
    else:
        advice = "今日保持平和心态，顺势而为。"
        mood = "适当休息，保持好心情。"

    # Override with AI-generated mood if available
    if overall_mood:
        # Use overall_mood to enrich advice
        pass

    return advice, mood


def _generate_generic_calendar() -> dict:
    """为用户提供通用日历（无八字信息时）。"""
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y-%m-%d")

    # Basic ganzhi for the day
    from src.engines.calendar import LuckyCalendar
    try:
        cal = LuckyCalendar("")
        day_stem, day_branch = cal._day_stem_branch(date_str)
        day_ganzhi = f"{day_stem}{day_branch}"
        day_wuxing = STEM_WUXING.get(day_stem, "")
    except Exception:
        day_ganzhi = ""
        day_wuxing = ""

    return {
        "date": date_str,
        "day_ganzhi": day_ganzhi,
        "day_wuxing": day_wuxing,
        "suitable": ["保持好心情", "与朋友交流", "适度运动"],
        "unsuitable": ["冲动决策", "过度消费", "熬夜"],
        "personal_advice": "请先设置八字信息，获取个性化日历。当前为通用运势参考。",
        "mood_reminder": "保持好心情是最好的开运方式。",
    }
