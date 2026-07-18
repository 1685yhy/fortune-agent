"""Hourly fortune analysis — 12 时辰 (two-hour periods) breakdown.

This expands the daily calendar into 12 time slots, showing which
hours are best for specific activities based on the user's bazi
and the current day's stem/branch combinations.

The core calculation is deterministic (based on 地支 relations).
No LLM needed for the base analysis — only for natural language output.
"""

# 十二时辰 (12 two-hour periods of the Chinese day)
HOURS = [
    ("子时", "23:00-01:00", "子"),
    ("丑时", "01:00-03:00", "丑"),
    ("寅时", "03:00-05:00", "寅"),
    ("卯时", "05:00-07:00", "卯"),
    ("辰时", "07:00-09:00", "辰"),
    ("巳时", "09:00-11:00", "巳"),
    ("午时", "11:00-13:00", "午"),
    ("未时", "13:00-15:00", "未"),
    ("申时", "15:00-17:00", "申"),
    ("酉时", "17:00-19:00", "酉"),
    ("戌时", "19:00-21:00", "戌"),
    ("亥时", "21:00-23:00", "亥"),
]

# 地支六合 (harmonious pairs)
HE_LIU = {"子":"丑", "丑":"子", "寅":"亥", "亥":"寅", "卯":"戌", "戌":"卯",
           "辰":"酉", "酉":"辰", "巳":"申", "申":"巳", "午":"未", "未":"午"}

# 地支六冲 (clashing pairs)
LIU_CHONG = {"子":"午", "午":"子", "丑":"未", "未":"丑", "寅":"申", "申":"寅",
              "卯":"酉", "酉":"卯", "辰":"戌", "戌":"辰", "巳":"亥", "亥":"巳"}

# 地支三合 (three-harmony groups)
SAN_HE = {
    "申子辰": "水", "亥卯未": "木", "寅午戌": "火", "巳酉丑": "金",
}

# 五行 (five elements) for each branch
BRANCH_WUXING = {
    "子":"水", "丑":"土", "寅":"木", "卯":"木", "辰":"土", "巳":"火",
    "午":"火", "未":"土", "申":"金", "酉":"金", "戌":"土", "亥":"水",
}

# Activity recommendations per element energy
ELEMENT_ACTIVITIES = {
    "水": ["冥想", "学习", "写作", "规划"],
    "木": ["运动", "创作", "社交", "开始新项目"],
    "火": ["演讲", "谈判", "展示", "决策"],
    "土": ["整理", "理财", "储蓄", "栽培"],
    "金": ["交易", "签约", "竞争", "裁决"],
}


def get_hourly_fortune(user_day_master: str, day_branch: str) -> list:
    """Generate 12-hour fortune analysis based on bazi principles.

    Args:
        user_day_master: User's day stem (e.g., "乙木", "丙火")
        day_branch: Current day's earthly branch (e.g., "子", "午")

    Returns:
        List of dicts with hour analysis for each of the 12 时辰
    """
    user_wx = ""
    for char in user_day_master:
        if char in "木火土金水":
            user_wx = char
            break

    if not user_wx:
        user_wx = "土"  # default

    results = []
    for name, time_range, branch in HOURS:
        hour_wx = BRANCH_WUXING.get(branch, "土")

        # Determine auspiciousness based on branch relations
        rating = "neutral"
        reason = ""

        # Check 六合 (harmony) — most auspicious
        if HE_LIU.get(branch) == day_branch:
            rating = "excellent"
            reason = f"{branch}与日支{day_branch}六合，大吉之时"
        # Check 六冲 (clash)
        elif LIU_CHONG.get(branch) == day_branch:
            rating = "poor"
            reason = f"{branch}与日支{day_branch}六冲，宜避"
        # Check if branch wx nourishes user's wx
        elif _wx_generates(hour_wx, user_wx):
            rating = "good"
            reason = f"{hour_wx}生{user_wx}，能量充沛"
        # Check if user's wx nourishes branch wx
        elif _wx_generates(user_wx, hour_wx):
            rating = "fair"
            reason = f"{user_wx}生{hour_wx}，付出型时段"

        # Get recommended activities
        activities = ELEMENT_ACTIVITIES.get(hour_wx, [])[:2]

        results.append({
            "name": name,
            "time": time_range,
            "branch": branch,
            "element": hour_wx,
            "rating": rating,
            "reason": reason,
            "activities": activities,
        })

    return results


def _wx_generates(parent: str, child: str) -> bool:
    """Check if element A generates element B in the 五行 cycle."""
    generation = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    return generation.get(parent) == child


def format_hourly_card(user_day_master: str, day_branch: str,
                       personality: str = "sassy") -> str:
    """Format the hourly fortune as a WeChat-friendly text card."""
    hourly = get_hourly_fortune(user_day_master, day_branch)

    emoji_map = {"excellent": "🌟", "good": "✅", "fair": "📌", "neutral": "💡", "poor": "⚠️"}
    lines = [f"⏰ 今日十二时辰运势 ({user_day_master} · 日支{day_branch})", ""]

    for h in hourly:
        e = emoji_map.get(h["rating"], "")
        activities = " · ".join(h["activities"]) if h["activities"] else "—"
        lines.append(f"{e} {h['name']} {h['time']} | {h['reason']} | 宜{activities}")

    # Best 3 hours
    best = [h for h in hourly if h["rating"] == "excellent"]
    if not best:
        best = [h for h in hourly if h["rating"] == "good"][:3]

    if best:
        lines.append(f"\n🍀 今日最佳时段：{'、'.join(h['name'] for h in best[:3])}")

    return "\n".join(lines)
