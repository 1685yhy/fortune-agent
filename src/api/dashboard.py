"""E1: Personal fortune dashboard — aggregates user data into a unified view.

Pure data aggregation, no AI calls. <500ms response time.
Combines data from: preference_dao, session_dao, dao, feedback system.
"""
from typing import Optional, Dict, List
from datetime import datetime


def build_dashboard(user_id: str, handler) -> dict:
    """Build complete user dashboard from all available data sources.

    Returns a rich dashboard JSON with fallback values for new users.
    """
    dashboard = {
        "user_id": user_id,
        "generated_at": datetime.now().isoformat(),
        "profile": {},
        "recent_activity": [],
        "accuracy": {},
        "preferences": {},
        "calendar_today": {},
    }

    # 1. Profile (八字概览)
    if handler.dao:
        saved = handler.dao.get_user_bazi(user_id)
        if saved:
            dashboard["profile"] = {
                "has_bazi": True,
                "bazi": " ".join(saved.get("bazi", ["?"])[:4]),
                "day_master": saved.get("day_master", ""),
                "gender": saved.get("gender", ""),
                "birth_info": f"{saved.get('year','')}年{saved.get('month','')}月{saved.get('day','')}日",
            }
        else:
            dashboard["profile"] = {
                "has_bazi": False,
                "hint": "告诉我你的出生日期，如：1990年5月20日 下午3点 北京 男",
            }

    # 2. Recent activity (last 5 consultations)
    if handler.dao:
        try:
            conn = handler.dao._connect()
            rows = conn.execute(
                "SELECT intent, question, created_at FROM consultations "
                "WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
                (user_id,)
            ).fetchall()
            dashboard["recent_activity"] = [
                {"intent": r[0] or "chat", "question": (r[1] or "")[:80], "date": r[2]}
                for r in rows
            ]
            conn.close()
        except Exception:
            pass

    # 3. Accuracy stats
    if handler.preference_dao:
        acc = handler.preference_dao.get_accuracy_dashboard(user_id)
        dashboard["accuracy"] = acc
    else:
        dashboard["accuracy"] = {"total_feedback": 0, "accuracy_pct": None,
                                  "hint": "使用后对分析点 👍👎 即可看到准确率"}

    # 4. Learned preferences
    if handler.preference_dao:
        prefs = handler.preference_dao.get(user_id)
        dashboard["preferences"] = {
            "mature": prefs.is_mature,
            "preferred_style": prefs.preferred_style if prefs.is_mature else None,
            "preferred_topic": prefs.preferred_topic if prefs.is_mature else None,
            "style_breakdown": {
                "sassy": round(prefs.style_sassy * 100),
                "analyst": round(prefs.style_analyst * 100),
                "gentle": round(prefs.style_gentle * 100),
            },
            "topics": {
                "wealth": round(prefs.topic_wealth * 100),
                "love": round(prefs.topic_love * 100),
                "career": round(prefs.topic_career * 100),
                "health": round(prefs.topic_health * 100),
                "growth": round(prefs.topic_growth * 100),
            },
        }
    else:
        dashboard["preferences"] = {"mature": False, "hint": "使用越多，越懂你的偏好"}

    # 5. Today's calendar (summary only)
    if handler.dao:
        saved = handler.dao.get_user_bazi(user_id)
        if saved:
            try:
                api_key = getattr(handler.llm, 'api_key', '') if handler.llm else ''
                if api_key:
                    from src.engines.calendar import LuckyCalendar
                    cal = LuckyCalendar(api_key)
                    day = cal.daily(saved)
                    dashboard["calendar_today"] = {
                        "date": day.date,
                        "mood": day.overall_mood,
                        "yi_count": len(day.yi),
                        "ji_count": len(day.ji),
                        "lucky": f"{day.lucky_color} | {day.lucky_direction} | {day.lucky_number}",
                    }
            except Exception:
                dashboard["calendar_today"] = {"hint": "今日运势生成中..."}

    return dashboard
