"""用户记忆系统 - 跨会话持久化用户记忆。

存储为 JSON 文件：data/memory/{user_id}.json

记忆字段：
- bazi_info: 八字信息（年、月、日、时、分、城市、性别）
- last_topic: 最近一次对话主题
- concerns: 用户关心的主题列表
- mood_history: 最近的情感状态记录
- consultation_count: 总咨询次数
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


class UserMemory:
    """Persistent user memory across sessions.

    每个用户的记忆存储为单独的 JSON 文件。
    支持记住、回忆、获取上下文和忘记功能。
    """

    _DEFAULT_FIELDS = {
        "bazi_info": None,
        "last_topic": "",
        "concerns": [],
        "mood_history": [],
        "consultation_count": 0,
    }

    def __init__(self, base_dir: Optional[str] = None):
        """初始化用户记忆系统。

        Args:
            base_dir: 记忆文件存储目录，默认 data/memory
        """
        if base_dir is None:
            base_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data", "memory",
            )
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    # ------------------------------------------------------------
    # 路径与 I/O
    # ------------------------------------------------------------

    def _path(self, user_id: str) -> str:
        """获取用户记忆文件的路径。"""
        # Sanitize user_id to prevent directory traversal
        safe_id = user_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        return os.path.join(self.base_dir, f"{safe_id}.json")

    def _load(self, user_id: str) -> Dict[str, Any]:
        """从磁盘加载用户记忆。"""
        path = self._path(user_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save(self, user_id: str, data: Dict[str, Any]) -> None:
        """将用户记忆写入磁盘。"""
        data["_updated_at"] = datetime.now().isoformat()
        path = self._path(user_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------
    # 核心 API
    # ------------------------------------------------------------

    def remember(self, user_id: str, key: str, value: Any) -> None:
        """记住一个键值对。

        Args:
            user_id: 用户 ID
            key: 键名
            value: 值（必须是 JSON 可序列化类型）
        """
        data = self._load(user_id)
        data[key] = value
        self._save(user_id, data)

    def recall(self, user_id: str, key: str) -> Optional[Any]:
        """回忆一个键的值。

        Args:
            user_id: 用户 ID
            key: 键名

        Returns:
            存储的值，如果键不存在返回 None
        """
        data = self._load(user_id)
        return data.get(key)

    def get_context(self, user_id: str) -> Dict[str, Any]:
        """获取用户的所有记忆。

        Args:
            user_id: 用户 ID

        Returns:
            包含所有记忆字段的字典
        """
        data = self._load(user_id)
        # Ensure default fields exist for convenient access
        for field, default in self._DEFAULT_FIELDS.items():
            if field not in data:
                data[field] = default
        return data

    def forget(self, user_id: str, key: str) -> None:
        """忘记一个键。

        Args:
            user_id: 用户 ID
            key: 要删除的键名
        """
        data = self._load(user_id)
        data.pop(key, None)
        self._save(user_id, data)

    # ------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------

    def has_memory(self, user_id: str) -> bool:
        """检查用户是否有记忆数据。"""
        return os.path.exists(self._path(user_id))

    def save_bazi_info(self, user_id: str, bazi_info: Dict[str, Any]) -> None:
        """保存用户的八字信息。

        Args:
            user_id: 用户 ID
            bazi_info: 包含 year, month, day, hour, minute, city, gender 等字段
        """
        data = self._load(user_id)
        data["bazi_info"] = bazi_info
        data["consultation_count"] = data.get("consultation_count", 0) + 1
        self._save(user_id, data)

    def add_mood_record(self, user_id: str, mood: str, topic: str = "") -> None:
        """添加一条情绪记录。

        Args:
            user_id: 用户 ID
            mood: 情绪标签（如 "anxiety", "sadness", "happy"）
            topic: 当前讨论主题
        """
        data = self._load(user_id)
        mood_history: List[dict] = data.get("mood_history", [])
        mood_history.append({
            "mood": mood,
            "topic": topic,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep only last 20 records
        if len(mood_history) > 20:
            mood_history = mood_history[-20:]
        data["mood_history"] = mood_history
        data["last_topic"] = topic or data.get("last_topic", "")
        self._save(user_id, data)

    def add_concern(self, user_id: str, concern: str) -> None:
        """添加一个用户关心的主题。

        Args:
            user_id: 用户 ID
            concern: 关心的话题（如 "事业", "感情", "健康"）
        """
        data = self._load(user_id)
        concerns: List[str] = data.get("concerns", [])
        if concern not in concerns:
            concerns.append(concern)
            # Keep only last 10 unique concerns
            if len(concerns) > 10:
                concerns = concerns[-10:]
        data["concerns"] = concerns
        self._save(user_id, data)

    def get_last_mood(self, user_id: str) -> Optional[str]:
        """获取用户最近一次情绪状态。

        Returns:
            最近的情绪标签，如果没有记录返回 None
        """
        data = self._load(user_id)
        mood_history = data.get("mood_history", [])
        if mood_history:
            return mood_history[-1].get("mood")
        return None

    def get_mood_summary(self, user_id: str) -> str:
        """生成情绪摘要。

        Returns:
            中文摘要字符串，如 "最近情绪波动较大" 或 "状态平稳"
        """
        data = self._load(user_id)
        mood_history = data.get("mood_history", [])
        if not mood_history:
            return ""

        # Count mood types
        mood_counts: Dict[str, int] = {}
        for record in mood_history[-10:]:  # Last 10 entries
            mood = record.get("mood", "")
            if mood:
                mood_counts[mood] = mood_counts.get(mood, 0) + 1

        if not mood_counts:
            return ""

        # Find dominant mood
        dominant = max(mood_counts, key=mood_counts.get)

        mood_labels = {
            "heartbreak": "感情困扰",
            "sadness": "低落",
            "anxiety": "焦虑",
            "confusion": "迷茫",
            "anger": "烦躁",
            "happy": "不错",
            "neutral": "平稳",
        }

        label = mood_labels.get(dominant, dominant)
        return f"最近情绪{label}"

    def get_greeting(self, user_id: str) -> str:
        """生成个性化的欢迎语。

        根据用户记忆生成合适的问候语。
        如果用户没有记忆，返回空字符串。

        Returns:
            个性化问候语，如 "上次聊到你的感情问题，最近好点了吗？"
        """
        data = self._load(user_id)
        if not data:
            return ""

        last_topic = data.get("last_topic", "")
        last_mood = self.get_last_mood(user_id)
        concerns = data.get("concerns", [])
        consultation_count = data.get("consultation_count", 0)

        if consultation_count == 0:
            return ""

        # Mood-aware greeting
        if last_mood in ("heartbreak", "sadness", "anxiety", "confusion", "anger"):
            return "最近感觉好点了吗？"

        # Topic-aware greeting
        if last_topic:
            topic_greetings = {
                "事业": "工作上有新进展吗？",
                "工作": "工作上有新进展吗？",
                "感情": "感情方面最近怎么样？",
                "健康": "身体好点了吗？",
                "财运": "财运方面有什么新变化吗？",
            }
            for keyword, greeting in topic_greetings.items():
                if keyword in last_topic:
                    return greeting

            # Generic topic reference
            return f"上次聊到你的{last_topic}，最近有变化吗？"

        # Last resort: generic greeting
        return "今天想聊点什么？"
