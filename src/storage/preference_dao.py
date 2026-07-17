"""User preference storage and learning — lightweight ML without dependencies.

F1-F2 of feedback calibration loop. Learns user preferences from 👍/👎 feedback
using exponential moving average (EMA). No deep learning, no GPU, no extra deps.
"""
import sqlite3
from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass
class UserPreferences:
    """Learned user preferences from feedback history."""
    user_id: str
    style_sassy: float = 0.33
    style_analyst: float = 0.33
    style_gentle: float = 0.34
    topic_wealth: float = 0.2
    topic_love: float = 0.2
    topic_career: float = 0.2
    topic_health: float = 0.2
    topic_growth: float = 0.2
    prefer_short: bool = False
    feedback_count: int = 0
    positive_count: int = 0
    last_style: str = ""
    last_topic: str = ""

    @property
    def preferred_style(self) -> str:
        """Return the highest-weighted style mode."""
        styles = {"sassy": self.style_sassy, "analyst": self.style_analyst, "gentle": self.style_gentle}
        return max(styles, key=styles.get)

    @property
    def preferred_topic(self) -> str:
        """Return the highest-weighted topic."""
        topics = {"wealth": self.topic_wealth, "love": self.topic_love,
                  "career": self.topic_career, "health": self.topic_health,
                  "growth": self.topic_growth}
        return max(topics, key=topics.get)

    @property
    def is_mature(self) -> bool:
        """Has enough feedback for reliable preferences (>= 3 feedbacks)."""
        return self.feedback_count >= 3

    @property
    def accuracy_pct(self) -> Optional[float]:
        """Positive feedback rate."""
        if self.feedback_count == 0:
            return None
        return round(self.positive_count / self.feedback_count * 100, 1)

    def to_prompt_hint(self) -> str:
        """Generate a brief prompt hint for LLM personalization.

        Only generates hints when preferences are mature (>= 3 feedbacks).
        Keeps it short to not bloat the prompt.
        """
        if not self.is_mature:
            return ""

        parts = []

        # Style hint
        style = self.preferred_style
        style_names = {"sassy": "毒舌直接", "analyst": "理性分析", "gentle": "温柔陪伴"}
        parts.append(f"用户偏好风格：{style_names.get(style, style)}")

        # Topic hint
        topic = self.preferred_topic
        topic_names = {"wealth": "财运", "love": "感情", "career": "事业",
                       "health": "健康", "growth": "个人成长"}
        parts.append(f"关注话题：{topic_names.get(topic, topic)}")

        # Length hint
        if self.prefer_short:
            parts.append("回复尽量简短精炼")

        # Accuracy context
        if self.accuracy_pct is not None:
            parts.append(f"历史好评率{self.accuracy_pct}%")

        return "【用户偏好】" + "，".join(parts) + "。"


class PreferenceDAO:
    """Data access for user preferences with built-in learning."""

    # EMA decay factor: new signal weight = 0.3, old weight = 0.7
    ALPHA = 0.3

    # Topic keywords for auto-detection
    TOPIC_KEYWORDS = {
        "wealth": ["财", "钱", "投资", "收入", "工资", "赚", "生意", "股票", "基金"],
        "love": ["感情", "恋爱", "分手", "桃花", "婚姻", "男朋友", "女朋友", "老公", "老婆", "喜欢", "爱"],
        "career": ["工作", "事业", "跳槽", "老板", "同事", "升职", "offer", "面试", "职业"],
        "health": ["健康", "身体", "病", "累", "睡眠", "失眠", "压力", "焦虑"],
        "growth": ["学习", "成长", "进步", "改变", "突破", "发展", "未来", "方向"],
    }

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, user_id: str) -> UserPreferences:
        """Get preferences for a user, with defaults if new."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()

        if row is None:
            return UserPreferences(user_id=user_id)

        return UserPreferences(
            user_id=row["user_id"],
            style_sassy=row["style_sassy"],
            style_analyst=row["style_analyst"],
            style_gentle=row["style_gentle"],
            topic_wealth=row["topic_wealth"],
            topic_love=row["topic_love"],
            topic_career=row["topic_career"],
            topic_health=row["topic_health"],
            topic_growth=row["topic_growth"],
            prefer_short=bool(row["prefer_short"]),
            feedback_count=row["feedback_count"],
            positive_count=row["positive_count"],
            last_style=row["last_style"] or "",
            last_topic=row["last_topic"] or "",
        )

    def learn(self, user_id: str, is_positive: bool,
              style: str = "", topic: str = "", response_len: int = 0) -> UserPreferences:
        """Learn from a single feedback event using EMA.

        Args:
            user_id: User ID
            is_positive: True for 👍, False for 👎
            style: Current personality mode (sassy/analyst/gentle)
            topic: Detected topic (wealth/love/career/health/growth)
            response_len: Length of response in characters
        """
        prefs = self.get(user_id)
        alpha = self.ALPHA
        beta = 1.0 - alpha  # 0.7 — weight of old value

        # Update style weights
        if style == "sassy":
            prefs.style_sassy = beta * prefs.style_sassy + alpha * (1.0 if is_positive else 0.0)
        elif style == "analyst":
            prefs.style_analyst = beta * prefs.style_analyst + alpha * (1.0 if is_positive else 0.0)
        elif style == "gentle":
            prefs.style_gentle = beta * prefs.style_gentle + alpha * (1.0 if is_positive else 0.0)

        # Normalize style weights to sum to 1.0
        total_style = prefs.style_sassy + prefs.style_analyst + prefs.style_gentle
        if total_style > 0:
            prefs.style_sassy /= total_style
            prefs.style_analyst /= total_style
            prefs.style_gentle /= total_style

        # Update topic weights
        topic_map = {
            "wealth": "topic_wealth", "love": "topic_love", "career": "topic_career",
            "health": "topic_health", "growth": "topic_growth",
        }
        if topic in topic_map:
            for t, attr in topic_map.items():
                current = getattr(prefs, attr)
                if t == topic:
                    setattr(prefs, attr, beta * current + alpha * (1.0 if is_positive else 0.0))
                # Slight decay for non-matching topics
                else:
                    setattr(prefs, attr, beta * current + alpha * 0.5)

            # Normalize topic weights
            total_topic = (prefs.topic_wealth + prefs.topic_love + prefs.topic_career +
                          prefs.topic_health + prefs.topic_growth)
            if total_topic > 0:
                for attr in topic_map.values():
                    setattr(prefs, attr, getattr(prefs, attr) / total_topic)

        # Update short preference
        if response_len > 0:
            likes_short = is_positive and response_len < 300
            prefs.prefer_short = bool(beta * prefs.prefer_short + alpha * (1.0 if likes_short else 0.0) > 0.5)

        # Update counters
        prefs.feedback_count += 1
        if is_positive:
            prefs.positive_count += 1
        prefs.last_style = style
        prefs.last_topic = topic

        # Persist
        self._upsert(prefs)
        return prefs

    def detect_topic(self, message: str) -> str:
        """Detect the primary topic from a user message."""
        scores = {}
        for topic, keywords in self.TOPIC_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message)
            if score > 0:
                scores[topic] = score
        if not scores:
            return ""
        return max(scores, key=scores.get)

    def _upsert(self, prefs: UserPreferences):
        """Insert or update preferences."""
        conn = self._connect()
        conn.execute("""
            INSERT INTO user_preferences
                (user_id, style_sassy, style_analyst, style_gentle,
                 topic_wealth, topic_love, topic_career, topic_health, topic_growth,
                 prefer_short, feedback_count, positive_count,
                 last_style, last_topic, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                style_sassy=excluded.style_sassy,
                style_analyst=excluded.style_analyst,
                style_gentle=excluded.style_gentle,
                topic_wealth=excluded.topic_wealth,
                topic_love=excluded.topic_love,
                topic_career=excluded.topic_career,
                topic_health=excluded.topic_health,
                topic_growth=excluded.topic_growth,
                prefer_short=excluded.prefer_short,
                feedback_count=excluded.feedback_count,
                positive_count=excluded.positive_count,
                last_style=excluded.last_style,
                last_topic=excluded.last_topic,
                updated_at=datetime('now')
        """, (
            prefs.user_id,
            prefs.style_sassy, prefs.style_analyst, prefs.style_gentle,
            prefs.topic_wealth, prefs.topic_love, prefs.topic_career,
            prefs.topic_health, prefs.topic_growth,
            int(prefs.prefer_short), prefs.feedback_count, prefs.positive_count,
            prefs.last_style, prefs.last_topic,
        ))
        conn.commit()
        conn.close()

    def get_accuracy_dashboard(self, user_id: str) -> dict:
        """Generate accuracy dashboard data for API."""
        prefs = self.get(user_id)

        conn = self._connect()
        # Topic-level accuracy from consultations (using LIKE for SQLite compatibility)
        topic_stats = {}
        try:
            for topic, keywords in self.TOPIC_KEYWORDS.items():
                like_clauses = " OR ".join([f"(question LIKE ? OR analysis LIKE ?)" for _ in keywords])
                params = [user_id]
                for kw in keywords:
                    params.extend([f"%{kw}%", f"%{kw}%"])
                row = conn.execute(
                    f"""SELECT COUNT(*) as total,
                       SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) as positive
                       FROM consultations
                       WHERE user_id = ? AND feedback IS NOT NULL AND feedback != ''
                       AND ({like_clauses})""",
                    params
                ).fetchone()
                if row and row["total"] > 0:
                    topic_stats[topic] = {
                        "total": row["total"],
                        "positive": row["positive"] or 0,
                        "accuracy": round((row["positive"] or 0) / row["total"] * 100, 1)
                    }
        except Exception:
            pass  # Topic stats are best-effort

        # Recent trend (last 4 weeks) — best-effort, may be empty
        trend = []
        try:
            for week_offset in range(3, -1, -1):
                row = conn.execute(
                    """SELECT COUNT(*) as total,
                       SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) as positive
                       FROM consultations
                       WHERE user_id = ? AND feedback IS NOT NULL AND feedback != ''
                       AND created_at >= datetime('now', ? || ' days')
                       AND created_at < datetime('now', ? || ' days')""",
                    (user_id, f"-{(week_offset+1)*7}", f"-{week_offset*7}")
                ).fetchone()
                if row and row["total"] > 0:
                    trend.append({
                        "week_offset": week_offset,
                        "total": row["total"],
                        "accuracy": round((row["positive"] or 0) / row["total"] * 100, 1)
                    })
        except Exception:
            pass

        conn.close()

        return {
            "user_id": user_id,
            "total_feedback": prefs.feedback_count,
            "accuracy_pct": prefs.accuracy_pct,
            "preferred_style": prefs.preferred_style if prefs.is_mature else None,
            "preferred_topic": prefs.preferred_topic if prefs.is_mature else None,
            "preferences_mature": prefs.is_mature,
            "topic_accuracy": topic_stats,
            "weekly_trend": trend,
        }
