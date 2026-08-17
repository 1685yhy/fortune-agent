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

from src.utils.text_clean import strip_emoji


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
    fortune4: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # 流日四运（今日详解页）：{"career": {"score": 7.2, "desc": "…"}, "wealth": …,
    #   "love": …, "health": …}；score 0-10 一位小数，desc 2-3 句解读


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
  "special_note": "如果是冲煞日/三合日/六合日，说明特殊之处；否则为空字符串",
  "fortune4": {{
    "career": {{"score": 7.2, "desc": "2-3句解读"}},
    "wealth": {{"score": 6.8, "desc": "2-3句解读"}},
    "love": {{"score": 5.5, "desc": "2-3句解读"}},
    "health": {{"score": 7.0, "desc": "2-3句解读"}}
  }}
}}

## 规则
1. 宜 ≥ 3 条，忌 ≥ 3 条
2. 每条必须包含 action + time + reason
3. 基于流日干支与用户日柱的生克关系来推理
4. 五行平衡：用户缺什么五行，宜对应补什么
5. personality 影响语气但不要出现在 JSON 中
6. fortune4 为「流日四运」：事业/财运/感情/健康四维，score 为 0-10 一位小数；
   desc 用现代中文、自然亲切，2-3 句，结合今日流日与命主五行关系给出具体建议
7. 回复中不使用任何 emoji 表情符号（所有字段均不得包含 emoji）"""


# 天干五行（流日四运规则兜底用）
wuxing_map = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}


def derive_fortune4(
    day_wuxing: str,
    user_day_stem: str = "",
    score: float = 70.0,
    overall_mood: str = "",
) -> Dict[str, Dict[str, Any]]:
    """流日四运规则兜底（确定性，不调 LLM）。

    基于当日分数（55-95 换算 5.5-9.5）与日干五行做维度偏移，并结合
    命主日干与流日的生克关系微调；desc 用模板句（按分数档与五行）。

    Returns:
        {"career": {"score": 7.2, "desc": "…"}, "wealth": …, "love": …, "health": …}
    """

    day_wx = day_wuxing or "土"
    base = max(2.0, min(9.9, (score or 70) / 10.0))

    # 五行 → 四维偏移（当日五行气质对四运的影响）
    wx_offset = {
        "木": {"career": 0.3, "wealth": 0.1, "love": 0.2, "health": 0.2},
        "火": {"career": 0.4, "wealth": 0.2, "love": 0.3, "health": -0.1},
        "土": {"career": 0.1, "wealth": 0.4, "love": -0.2, "health": 0.3},
        "金": {"career": 0.2, "wealth": 0.5, "love": -0.1, "health": -0.2},
        "水": {"career": -0.1, "wealth": 0.1, "love": 0.4, "health": 0.1},
    }.get(day_wx, {"career": 0.0, "wealth": 0.0, "love": 0.0, "health": 0.0})

    # 命主与流日关系微调（同 _stem_branch_relation 的五组）
    rel_offset = {"career": 0.0, "wealth": 0.0, "love": 0.0, "health": 0.0}
    user_wx = wuxing_map.get(user_day_stem, "")
    if day_wx and user_wx:
        generates = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
        controls = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}
        if day_wx == user_wx:
            rel_offset = {"career": 0.1, "wealth": 0.1, "love": 0.1, "health": 0.1}
        elif generates.get(day_wx) == user_wx:   # 时生日：外界滋养你
            rel_offset = {"career": 0.1, "wealth": 0.1, "love": 0.2, "health": 0.3}
        elif generates.get(user_wx) == day_wx:   # 日生时：付出时段
            rel_offset = {"career": -0.2, "wealth": -0.2, "love": 0.0, "health": -0.2}
        elif controls.get(day_wx) == user_wx:    # 时克日：外界压力
            rel_offset = {"career": -0.2, "wealth": -0.1, "love": -0.4, "health": -0.2}
        elif controls.get(user_wx) == day_wx:    # 日克时：掌控强
            rel_offset = {"career": 0.3, "wealth": 0.2, "love": 0.0, "health": 0.1}

    def clamp(v: float) -> float:
        return round(max(2.0, min(9.9, v)), 1)

    career = clamp(base + wx_offset["career"] + rel_offset["career"])
    wealth = clamp(base + wx_offset["wealth"] + rel_offset["wealth"])
    love = clamp(base + wx_offset["love"] + rel_offset["love"])
    health = clamp(base + wx_offset["health"] + rel_offset["health"])

    def band(v: float) -> str:
        return "high" if v >= 7.5 else ("mid" if v >= 6.0 else "low")

    # 四维解读模板（按分数档 × 当日五行），现代中文 2-3 句
    career_desc = {
        "high": f"今日{day_wx}气助力事业，方案与合作最易敲定点头。宜主动开口、果断出手，不宜观望等待。",
        "mid": f"今日{day_wx}气平稳，事业按部就班即可推进。有想法先成文，重要决定放到状态最好的时段再做。",
        "low": f"今日{day_wx}气偏弱，事业上宜守不宜攻。把手头的事做扎实，别急着开新战线。",
    }
    wealth_desc = {
        "high": "财星得位，正财可进，偏财勿贪。收入稳稳落袋，大额支出缓一缓再定。",
        "mid": "财气平平，守好正财即可。消费量入为出，投资多看少动，钱包才稳。",
        "low": "财气偏弱，今日不宜投资与借贷。守住钱包，大额开支改日再议。",
    }
    love_desc = {
        "high": "感情运温暖，适合约会与谈心。主动一点，关系更进一步。",
        "mid": "感情运平稳，寻常相处即是福。有话好好说，别急着下结论。",
        "low": "今日情绪偏低沉，说话容易急。重要的话留到傍晚再说，那时语气也软了。",
    }
    health_desc = {
        "high": "精神头足，宜早起活动筋骨。午间小憩充电，全天状态在线。",
        "mid": "精力尚可，注意劳逸结合。夜间早睡，明日精神更旺。",
        "low": "今日易感疲惫，宜多休息、少熬夜。子时前入睡，养足精神。",
    }

    def with_mood(desc: str) -> str:
        if overall_mood and overall_mood not in desc:
            return f"{desc}{overall_mood}"
        return desc

    return {
        "career": {"score": career, "desc": with_mood(career_desc[band(career)])},
        "wealth": {"score": wealth, "desc": with_mood(wealth_desc[band(wealth)])},
        "love": {"score": love, "desc": with_mood(love_desc[band(love)])},
        "health": {"score": health, "desc": with_mood(health_desc[band(health)])},
    }


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
              preferences: str = "") -> CalendarDay:
        """Generate personalized calendar for a single day.

        Args:
            user_bazi: Dict with bazi info (bazi list, day_master, wuxing, dayun, etc.)
            date_str: Date in YYYY-MM-DD format (defaults to today).
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

        prompt = CALENDAR_PROMPT.format(
            chart_info=chart_info,
            date=date_str,
            day_stem_branch=f"{day_stem}{day_branch}",
            day_relation=day_relation,
            preferences=f"风格要求：用现代中文、自然亲切的语气。\n{preferences}" if preferences else "风格要求：用现代中文、自然亲切的语气。",
        )

        try:
            # Bugfix（原实现每次 20s+ 且 content 为空）：
            # - 走原生 /v1/chat/completions 时 deepseek-v4-flash 是推理模型，
            #   max_tokens 需同时容纳 reasoning_content 与 content：1500 时推理
            #   占满配额 → content 为空（finish_reason=length）；即使加大到 8000
            #   也要 30s+，逼近前端 30s 超时。
            # - 改为与主聊天一致的 Anthropic 兼容端点 + deepseek-v4-flash[1m]，
            #   并显式 thinking: {"type": "disabled"} 关闭推理：实测约 4.5s 返回
            #   完整 JSON（不关闭时完整 prompt 的 thinking 会占满 2000 tokens）。
            resp = httpx.post(
                "https://api.deepseek.com/anthropic/v1/messages",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": "deepseek-v4-flash[1m]",
                    "max_tokens": 2000,
                    "thinking": {"type": "disabled"},
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60.0,
            )
            resp_data = resp.json()
            if "error" in resp_data:
                import logging
                logging.getLogger(__name__).warning(f"Calendar API error: {resp_data['error']}")
                raise ValueError(str(resp_data['error']))
            # Anthropic 格式：content 为 blocks 数组，取 text block
            content = ""
            for block in resp_data.get("content", []):
                if block.get("type") == "text":
                    content = strip_emoji(block.get("text", "").strip())
                    break
            if not content:
                # 无 text block / content 为空，必须降级到 fallback
                import logging
                logging.getLogger(__name__).warning(
                    f"Calendar API returned empty content (stop_reason={resp_data.get('stop_reason')}) — using fallback"
                )
                raise ValueError("Empty content from LLM")
            data = self._parse_json(content)
            if not data:
                import logging
                logging.getLogger(__name__).warning(f"Failed to parse calendar JSON from: {content[:300]}")
                raise ValueError("Unparseable calendar JSON")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Calendar generation failed: {e}")
            data = self._fallback_calendar(user_bazi, date_str, day_stem, day_branch)

        # 流日四运：LLM 返回缺失/格式不合法时用规则兜底（确定性，不调 LLM）
        fortune4 = self._sanitize_fortune4(data.get("fortune4"))
        if fortune4 is None:
            day_wx = wuxing_map.get(day_stem, "")
            fortune4 = derive_fortune4(day_wx, user_day_stem, 70, "")

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
            fortune4=fortune4,
        )

    def _sanitize_fortune4(self, raw) -> Optional[Dict[str, Dict[str, Any]]]:
        """校验 LLM 返回的 fortune4：四维齐全、score 合法、desc 非空；不合法返回 None。"""
        if not isinstance(raw, dict):
            return None
        keys = ("career", "wealth", "love", "health")
        out = {}
        for k in keys:
            item = raw.get(k)
            if not isinstance(item, dict):
                return None
            try:
                score = float(item.get("score"))
            except (TypeError, ValueError):
                return None
            if not (0 <= score <= 10):
                return None
            desc = str(item.get("desc", "")).strip()
            if not desc:
                return None
            out[k] = {"score": round(score, 1), "desc": desc}
        return out

    def week(self, user_bazi: dict, preferences: str = "") -> List[CalendarDay]:
        """Generate 7-day calendar preview (today + 6 days)."""
        today = datetime.now()
        days = []
        for i in range(7):
            date_str = (today + timedelta(days=i)).strftime("%Y-%m-%d")
            day = self.daily(user_bazi, date_str, preferences)
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
        """Minimal fallback when AI is unavailable.

        保证 yi/ji 永不为空数组，并按当日干支五行派生幸运色/数字/方向/基调，
        使 fallback 内容也随日期变化（确定性规则，不调 LLM）。
        """
        wuxing = {
            "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
            "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
        }
        day_wx = wuxing.get(day_stem, "土")
        user_bazi_list = user_bazi.get("bazi", ["?"])
        user_day_stem = user_bazi_list[2] if len(user_bazi_list) >= 3 else ""
        wx_color = {"木": "绿色", "火": "红色", "土": "黄色", "金": "金色", "水": "蓝色"}
        wx_number = {"木": "3", "火": "2", "土": "5", "金": "4", "水": "6"}
        wx_dir = {"木": "东", "火": "南", "土": "西南", "金": "西", "水": "北"}
        mood = {
            "木": "今日木气当旺，生机勃勃，宜舒展身心、顺势而为。",
            "火": "今日火气当旺，热情充沛，宜把握关键时机、主动进取。",
            "土": "今日土气当旺，沉稳笃定，宜踏实推进事务、稳中求进。",
            "金": "今日金气当旺，决断力强，宜果敢处理要事、收敛锋芒。",
            "水": "今日水气当旺，思维通透，宜沟通交流协作、顺势而为。",
        }
        return {
            "overall_mood": mood.get(day_wx, "保持平和心态，顺势而为"),
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
            "lucky_color": wx_color.get(day_wx, "蓝色"),
            "lucky_direction": wx_dir.get(day_wx, "东"),
            "lucky_number": wx_number.get(day_wx, "6"),
            "is_special": False,
            "special_note": "",
            "fortune4": derive_fortune4(day_wx, user_day_stem, 70),
        }
