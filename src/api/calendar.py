"""命运日历 API — 每日运势日历。

提供 GET /api/calendar/today 接口，基于用户八字返回个性化每日运势。
"""

import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.engines.calendar import LuckyCalendar
from src.security.auth import require_user
from src.storage.dao import UserDAO
from src.utils.cache import get_cache, TTL_CALENDAR_TODAY

router = APIRouter(tags=["calendar"])

# 天干五行映射
STEM_WUXING = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}

# 五行 → 幸运色/数字/方向（规则派生，不调 LLM）
WX_COLOR = {"木": "绿色", "火": "红色", "土": "黄色", "金": "金色", "水": "蓝色"}
WX_NUMBER = {"木": "3", "火": "2", "土": "5", "金": "4", "水": "6"}
WX_DIR = {"木": "东", "火": "南", "土": "西南", "金": "西", "水": "北"}

# 十二时辰（地支五行）
SHICHEN = [
    ("子时", "23-01"), ("丑时", "01-03"), ("寅时", "03-05"), ("卯时", "05-07"),
    ("辰时", "07-09"), ("巳时", "09-11"), ("午时", "11-13"), ("未时", "13-15"),
    ("申时", "15-17"), ("酉时", "17-19"), ("戌时", "19-21"), ("亥时", "21-23"),
]
BRANCH_WX = {
    "子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
    "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水",
}


def _score_to_stars(score: int) -> int:
    """score(55-95) → 1-5 星，与前端 Math.round(score/20) 一致（76 → 4 星）。"""
    return max(1, min(5, int(score / 20 + 0.5)))


def _generate_hourly(day_stem: str) -> list:
    """按日干五行与时辰地支五行的生克关系生成 12 条时辰运势（纯规则，不调 LLM）。"""
    day_wx = STEM_WUXING.get(day_stem, "")
    if not day_wx:
        return [
            {"time": f"{name} {hours}", "desc": "时辰平稳，按部就班，宜处理常规事务。"}
            for name, hours in SHICHEN
        ]
    generates = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    controls = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

    result = []
    for name, hours in SHICHEN:
        bw = BRANCH_WX.get(name[0], "")
        if bw == day_wx:
            desc = f"{day_wx}气同频，能量最旺，适合推进核心事务、做重要决定。"
        elif generates.get(bw) == day_wx:
            desc = f"{bw}生{day_wx}，外界滋养，适合学习充电、听取建议、接受帮助。"
        elif generates.get(day_wx) == bw:
            desc = f"{day_wx}生{bw}，属付出时段，适合分享、协作、创意表达。"
        elif controls.get(bw) == day_wx:
            desc = f"{bw}克{day_wx}，压力稍大，宜守不宜攻，注意劳逸结合。"
        elif controls.get(day_wx) == bw:
            desc = f"{day_wx}制{bw}，掌控力强，适合谈判、决策、解决棘手问题。"
        else:
            desc = "时辰平稳，按部就班，宜处理常规事务。"
        result.append({"time": f"{name} {hours}", "desc": desc})
    return result

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
    uid: str = Depends(require_user),
    date: str = Query(None, description="日期 YYYY-MM-DD（可选，默认今天）"),
):
    """获取用户今日专属运势日历。

    基于用户八字和今日流日，生成个性化运势分析。
    包含日干支、五行属性、宜忌事项、个人建议和情绪提醒。

    安全修复（红线）：user_id 一律取 JWT sub；客户端传的 user_id 查询参数
    被忽略（FastAPI 未声明的查询参数自动忽略），无 token 一律 401。

    Bugfix:
    - 按 (user_id, date) 缓存 LLM 结果 6 小时（TTL_CALENDAR_TODAY），
      避免每次请求都调用 DeepSeek（原来每次 20 秒+）。
    - 响应补全前端契约：stars（=score 换算 1-5 星）、lucky_color、
      lucky_number、lucky_direction、hourly（12 时辰运势，纯规则生成）。
    - 同步 LLM 调用移入线程池，避免阻塞事件循环导致服务卡死。

    Args:
        date: 日期（默认今天，北京时间）

    Returns:
        JSON 格式的今日运势日历
    """
    global _dao, _handler_ref

    user_id = uid
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = date or now.strftime("%Y-%m-%d")
    cache_scope = user_id or ""

    # ── 缓存命中直接返回（避免每次调 LLM）──
    cache = get_cache()
    cache_key = f"calendar:today:{date_str}"
    cached = cache.get(cache_key, cache_scope)
    if cached is not None:
        return cached

    # Try to get user's bazi from DAO
    saved = _dao.get_user_bazi(user_id) if (_dao and user_id) else None

    if not saved:
        result = _generate_generic_calendar(date_str)
        cache.set(cache_key, result, cache_scope, ttl_seconds=TTL_CALENDAR_TODAY)
        return result

    # Get API key from handler's LLM
    api_key = ""
    preferences = ""
    if _handler_ref:
        if hasattr(_handler_ref, 'llm') and _handler_ref.llm:
            api_key = getattr(_handler_ref.llm, 'api_key', '')
        if hasattr(_handler_ref, '_get_preference_hint'):
            pref_hint = _handler_ref._get_preference_hint(user_id)
            if pref_hint:
                preferences = pref_hint

    try:
        cal = LuckyCalendar(api_key) if api_key else LuckyCalendar("")
        # 同步 LLM 调用放线程池：事件循环保持空闲，请求超时中间件才能生效
        loop = asyncio.get_event_loop()
        day = await loop.run_in_executor(
            None, lambda: cal.daily(saved, date_str=date_str, preferences=preferences)
        )
    except Exception:
        result = _generate_generic_calendar(date_str)
        cache.set(cache_key, result, cache_scope, ttl_seconds=TTL_CALENDAR_TODAY)
        return result

    # Build the response
    day_ganzhi = f"{day.day_stem}{day.day_branch}" if day.day_stem else ""
    day_wuxing = STEM_WUXING.get(day.day_stem, "")

    # Extract action names from yi/ji for the simplified suitable/unsuitable lists
    suitable = [item.get("action", "") for item in day.yi if item.get("action")]
    unsuitable = [item.get("action", "") for item in day.ji if item.get("action")]
    if not suitable:
        suitable = ["静心思考", "与人交流", "整理规划"]
    if not unsuitable:
        unsuitable = ["冲动决策", "过度消费", "熬夜"]

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

    # Calculate score based on day wuxing + personal relationship
    wx_scores = {"木": 72, "火": 78, "土": 65, "金": 70, "水": 75}
    day_wx = day_wuxing or "土"
    score = min(95, max(55, wx_scores.get(day_wx, 70) + ((now.day % 11) - 5)))

    result = {
        "date": day.date,
        "lunar_date": getattr(day, 'lunar_date', ''),
        "day_ganzhi": day_ganzhi,
        "day_wuxing": day_wuxing,
        "score": score,
        "stars": _score_to_stars(score),
        "suitable": suitable[:5],  # Max 5 items
        "unsuitable": unsuitable[:5],
        "personal_advice": personal_advice,
        "mood_reminder": mood_reminder,
        "overall_mood": day.overall_mood,
        "lucky_color": day.lucky_color or WX_COLOR.get(day_wx, "蓝色"),
        "lucky_number": str(day.lucky_number) if day.lucky_number else WX_NUMBER.get(day_wx, "6"),
        "lucky_direction": day.lucky_direction or WX_DIR.get(day_wx, "东"),
        "hourly": _generate_hourly(day.day_stem),
    }
    cache.set(cache_key, result, cache_scope, ttl_seconds=TTL_CALENDAR_TODAY)
    return result


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


def _generate_generic_calendar(date_str: str = None) -> dict:
    """为用户提供通用日历（无八字信息时）。

    Bugfix: 与个性化路径保持一致，补全 stars / lucky_color / lucky_number /
    lucky_direction / hourly，保证前端契约完整。
    """
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = date_str or now.strftime("%Y-%m-%d")

    # Basic ganzhi for the day
    from src.engines.calendar import LuckyCalendar
    day_stem = ""
    day_ganzhi = ""
    day_wuxing = ""
    try:
        cal = LuckyCalendar("")
        day_stem, day_branch = cal._day_stem_branch(date_str)
        day_ganzhi = f"{day_stem}{day_branch}"
        day_wuxing = STEM_WUXING.get(day_stem, "")
    except Exception:
        pass

    # Calculate score based on day wuxing
    wx_scores = {"木": 72, "火": 78, "土": 65, "金": 70, "水": 75}
    day_wx = day_wuxing or "土"
    score = min(95, max(55, wx_scores.get(day_wx, 70) + ((now.day % 11) - 5)))

    return {
        "date": date_str,
        "lunar_date": "",
        "day_ganzhi": day_ganzhi,
        "day_wuxing": day_wuxing,
        "score": score,
        "stars": _score_to_stars(score),
        "suitable": ["保持好心情", "与朋友交流", "适度运动"],
        "unsuitable": ["冲动决策", "过度消费", "熬夜"],
        "personal_advice": "请先设置八字信息，获取个性化日历。当前为通用运势参考。",
        "mood_reminder": "保持好心情是最好的开运方式。",
        "overall_mood": "保持平和心态，顺势而为",
        "lucky_color": WX_COLOR.get(day_wx, "蓝色"),
        "lucky_number": WX_NUMBER.get(day_wx, "6"),
        "lucky_direction": WX_DIR.get(day_wx, "东"),
        "hourly": _generate_hourly(day_stem),
    }
