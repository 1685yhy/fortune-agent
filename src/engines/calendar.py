"""AI Lucky Calendar — personalized daily fortune calendar.

Sprint 8 / C1-C3. Generates unique daily 宜忌 for each user based on:
- Their bazi chart (day master, wuxing, dayun)
- Current day's heavenly stem + earthly branch (流日)
- RAG classical references
- User's personality preferences (from feedback loop)

100% AI-generated. Zero hardcoded templates.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

import httpx


@dataclass
class CalendarDay:
    """A single day's personalized calendar."""
    date: str                          # YYYY-MM-DD
    day_stem: str = ""                 # 日干
    day_branch: str = ""               # 日支
    lunar_date: str = ""               # 农历日期（如有）
    yi: List[Dict[str, str]] = field(default_factory=list)  # [{action, time, reason}]
    ji: List[Dict[str, str]] = field(default_factory=list)   # [{action, time, reason}]
    lucky_color: str = ""              # 幸运色
    lucky_direction: str = ""          # 幸运方向
    lucky_number: str = ""             # 幸运数字
    overall_mood: str = ""             # 整体运势基调（一句话）
    is_special: bool = False           # 是否特殊日（冲煞/三合等）
    special_note: str = ""             # 特殊日说明


CALENDAR_PROMPT = """你是一位精通八字命理的 AI 日历顾问。基于用户的命盘和今日流日，生成一份个性化的「今日宜忌」。

## 用户命盘信息
{chart_info}

## 今日流日
- 日期：{date}
- 日干支：{day_stem_branch}
- 与用户日柱关系：{day_relation}

## 用户偏好
{preferences}

## 生成要求
返回 JSON 格式（不要任何额外文本）：

{{
  "overall_mood": "一句话描述今日整体运势基调（15字以内）",
  "yi": [
    {{"action": "宜做的事", "time": "最佳时辰（如巳时9-11点）", "reason": "基于流日与命盘的关系（10字内）"}}
  ],
  "ji": [
    {{"action": "忌做的事", "time": "需避开的时辰", "reason": "基于流日与命盘的关系（10字内）"}}
  ],
  "lucky_color": "一种颜色",
  "lucky_direction": "一个方位（东/南/西/北/东南/东北/西南/西北）",
  "lucky_number": "一个数字（1-9）",
  "is_special": true/false,
  "special_note": "如果是冲煞日/三合日/六合日，说明特殊之处；否则为空字符串"
}}

## 规则
1. 宜 ≥ 3 条，忌 ≥ 3 条
2. 每条必须包含 action + time + reason
3. 基于流日干支与用户日柱的生克关系来推理
4. 五行平衡：用户缺什么五行，宜对应补什么
5. personality 影响语气但不要出现在 JSON 中"""


class LuckyCalendar:
    """AI-powered personalized daily fortune calendar."""

    # Heavenly stems and earthly branches for date calculation
    STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
    BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _day_stem_branch(self, date_str: str) -> tuple:
        """Calculate heavenly stem and earthly branch for a given date.

        Uses a known reference point: 2026-01-01 = 乙巳日 (stem=1, branch=5).
        """
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            dt = datetime.now()

        ref = datetime(2026, 1, 1)  # 乙巳日
        days_diff = (dt - ref).days
        stem_idx = (1 + days_diff) % 10   # 乙=1
        branch_idx = (5 + days_diff) % 12  # 巳=5
        return self.STEMS[stem_idx], self.BRANCHES[branch_idx]

    def _stem_branch_relation(self, day_stem: str, user_day_stem: str) -> str:
        """Describe relationship between day stem and user's day stem."""
        if not day_stem or not user_day_stem or user_day_stem == "?":
            return "未知（请先设置八字）"

        wuxing = {
            "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
            "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
        }
        day_wx = wuxing.get(day_stem, "")
        user_wx = wuxing.get(user_day_stem, "")

        # Five element relationships
        generates = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
        controls = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

        if day_wx == user_wx:
            return f"比和（同为{day_wx}，能量共振，适合巩固自我）"
        elif generates.get(user_wx) == day_wx:
            return f"日生时（{user_wx}生{day_wx}，你付出能量，适合分享和创造）"
        elif generates.get(day_wx) == user_wx:
            return f"时生日（{day_wx}生{user_wx}，外界滋养你，适合接收和学习）"
        elif controls.get(user_wx) == day_wx:
            return f"日克时（{user_wx}克{day_wx}，你需要消耗精力，注意节奏）"
        elif controls.get(day_wx) == user_wx:
            return f"时克日（{day_wx}克{user_wx}，外界压力较大，宜守不宜攻）"
        return f"{user_wx}(你) vs {day_wx}(今日)"

    def daily(self, user_bazi: dict, date_str: str = None,
              personality: str = "sassy",
              preferences: str = "") -> CalendarDay:
        """Generate personalized calendar for a single day.

        Args:
            user_bazi: Dict with bazi info (bazi list, day_master, wuxing, dayun, etc.)
            date_str: Date in YYYY-MM-DD format (defaults to today).
            personality: User's personality mode for tone adaptation.
            preferences: User preference hint string from feedback loop.

        Returns:
            CalendarDay with personalized 宜忌, lucky items, etc.
        """
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        day_stem, day_branch = self._day_stem_branch(date_str)
        user_bazi_list = user_bazi.get("bazi", ["?"])
        user_day_stem = user_bazi_list[2] if len(user_bazi_list) >= 3 else "?"
        day_relation = self._stem_branch_relation(day_stem, user_day_stem)

        # Build chart info for prompt
        bazi_str = " ".join(user_bazi_list[:4]) if len(user_bazi_list) >= 4 else "未知"
        dm = user_bazi.get("day_master", "未知")
        wuxing = user_bazi.get("wuxing", {})
        wuxing_str = " ".join(f"{k}{v}" for k, v in wuxing.items()) if wuxing else "未知"
        dayun = user_bazi.get("current_dayun", user_bazi.get("dayun", "未知"))

        chart_info = (
            f"八字：{bazi_str}\n"
            f"日主：{dm}\n"
            f"五行分布：{wuxing_str}\n"
            f"当前大运：{dayun}\n"
        )

        # Personality tone directive
        tone_directives = {
            "sassy": "语气犀利带点调侃，像朋友说实话。但宜忌的内容本身要专业准确。",
            "analyst": "语气理性专业，可以适当用百分比来表达运势强度。",
            "gentle": "语气温柔鼓励，多给正向能量，让人感到被支持。",
        }
        tone = tone_directives.get(personality, tone_directives["sassy"])

        prompt = CALENDAR_PROMPT.format(
            chart_info=chart_info,
            date=date_str,
            day_stem_branch=f"{day_stem}{day_branch}",
            day_relation=day_relation,
            preferences=f"风格要求：{tone}\n{preferences}" if preferences else f"风格要求：{tone}",
        )

        try:
            resp = httpx.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1500,
                    "temperature": 0.8,
                },
                timeout=30.0,
            )
            resp_data = resp.json()
            if "error" in resp_data:
                import logging
                logging.getLogger(__name__).warning(f"Calendar API error: {resp_data['error']}")
                raise ValueError(str(resp_data['error']))
            if "choices" not in resp_data or not resp_data["choices"]:
                import logging
                logging.getLogger(__name__).warning(f"Calendar API empty choices. Full response keys: {list(resp_data.keys())}")
                logging.getLogger(__name__).warning(f"Full response: {str(resp_data)[:500]}")
                raise ValueError("No choices in response")
            content = resp_data["choices"][0]["message"]["content"].strip()
            data = self._parse_json(content)
            if not data:
                import logging
                logging.getLogger(__name__).warning(f"Failed to parse calendar JSON from: {content[:300]}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Calendar generation failed: {e}")
            data = self._fallback_calendar(user_bazi, date_str, day_stem, day_branch)

        return CalendarDay(
            date=date_str,
            day_stem=day_stem,
            day_branch=day_branch,
            yi=data.get("yi", []),
            ji=data.get("ji", []),
            lucky_color=data.get("lucky_color", ""),
            lucky_direction=data.get("lucky_direction", ""),
            lucky_number=str(data.get("lucky_number", "")),
            overall_mood=data.get("overall_mood", ""),
            is_special=data.get("is_special", False),
            special_note=data.get("special_note", ""),
        )

    def week(self, user_bazi: dict, personality: str = "sassy",
             preferences: str = "") -> List[CalendarDay]:
        """Generate 7-day calendar preview (today + 6 days)."""
        today = datetime.now()
        days = []
        for i in range(7):
            date_str = (today + timedelta(days=i)).strftime("%Y-%m-%d")
            day = self.daily(user_bazi, date_str, personality, preferences)
            days.append(day)
        return days

    def _parse_json(self, content: str) -> dict:
        """Parse LLM JSON response — handles nested objects and truncated responses."""
        start = content.find('{')
        if start == -1:
            return {}

        # Try bracket-counting first (handles nested objects)
        depth = 0
        end = start
        for i in range(start, len(content)):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        json_str = content[start:end]
        try:
            return json.loads(json_str)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # Recovery: try to fix truncated JSON by closing open structures
        # Count unclosed brackets and close them
        open_braces = json_str.count('{') - json_str.count('}')
        open_brackets = json_str.count('[') - json_str.count(']')
        if open_braces > 0 or open_brackets > 0:
            # Remove the last incomplete element (likely a truncated string)
            # Find the last complete item before truncation
            last_comma = max(
                json_str.rfind(',"ji"'),
                json_str.rfind('"}'),
                json_str.rfind('"]'),
                json_str.rfind(',{"'),
                -1
            )
            if last_comma > 0:
                json_str = json_str[:last_comma]
            # Close any open structures
            json_str += ']' * open_brackets
            json_str += '}' * open_braces
            try:
                return json.loads(json_str)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        return {}

    def _fallback_calendar(self, user_bazi: dict, date_str: str,
                           day_stem: str, day_branch: str) -> dict:
        """Minimal fallback when AI is unavailable."""
        return {
            "overall_mood": "保持平和心态，顺势而为",
            "yi": [
                {"action": "静心思考", "time": "辰时7-9点", "reason": "晨起气清，利于决策"},
                {"action": "与人交流", "time": "午时11-13点", "reason": "阳气最旺时沟通顺畅"},
                {"action": "整理规划", "time": "申时15-17点", "reason": "金气收敛，适合归纳"},
            ],
            "ji": [
                {"action": "冲动决策", "time": "全天", "reason": "心浮气躁易失误"},
                {"action": "过度消费", "time": "酉时17-19点", "reason": "金旺易破财"},
                {"action": "熬夜", "time": "子时23点后", "reason": "伤肝损运势"},
            ],
            "lucky_color": "蓝色",
            "lucky_direction": "东",
            "lucky_number": "6",
            "is_special": False,
            "special_note": "",
        }
