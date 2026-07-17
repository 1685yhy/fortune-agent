"""会话持久化 - SQLite 存储聊天历史."""
import sqlite3
from typing import Optional, List, Dict

from .models import init_db

MAX_MESSAGES_PER_USER = 100  # 自动清理：每个用户最多保留 100 条


class SessionDAO:
    """会话数据访问层，负责聊天消息的持久化与查询。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def add_message(self, user_id: str, role: str, content: str, intent: Optional[str] = None):
        """保存一条聊天消息。

        Args:
            user_id: 用户标识
            role: 'user' 或 'assistant'
            content: 消息正文
            intent: 命理意图（bazi/ziwei/liuyao 等），自由对话时为 None
        """
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO sessions (user_id, role, content, intent) VALUES (?, ?, ?, ?)",
                (user_id, role, content, intent),
            )
            conn.commit()
        finally:
            conn.close()
        self._cleanup(user_id)

    def _cleanup(self, user_id: str):
        """删除超出保留上限的旧消息，每个用户最多保留 MAX_MESSAGES_PER_USER 条。"""
        conn = self._connect()
        try:
            # 删除超出上限的最旧记录
            conn.execute(
                """DELETE FROM sessions
                   WHERE user_id = ?
                     AND id NOT IN (
                         SELECT id FROM sessions
                         WHERE user_id = ?
                         ORDER BY created_at DESC, id DESC
                         LIMIT ?
                     )""",
                (user_id, user_id, MAX_MESSAGES_PER_USER),
            )
            conn.commit()
        finally:
            conn.close()

    def get_history(self, user_id: str, limit: int = 20) -> List[Dict]:
        """获取指定用户的最近 N 条消息。

        Returns:
            list of dicts: [{id, user_id, role, content, intent, created_at}, ...]
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT id, user_id, role, content, intent, created_at
                   FROM sessions
                   WHERE user_id = ?
                   ORDER BY created_at DESC, id DESC
                   LIMIT ?""",
                (user_id, limit),
            ).fetchall()
            # 按时间正序返回（旧→新）
            rows.reverse()
            return [
                {
                    "id": r[0],
                    "user_id": r[1],
                    "role": r[2],
                    "content": r[3],
                    "intent": r[4],
                    "created_at": r[5],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_context_for_llm(self, user_id: str, history_limit: int = 15) -> List[Dict]:
        """获取可用于 LLM API 的历史消息列表。

        Args:
            user_id: 用户标识
            history_limit: 最多返回多少条消息（默认 15，控制 token 用量）

        Returns:
            list of dicts: [{"role": "user"/"assistant", "content": "..."}, ...]
        """
        history = self.get_history(user_id, limit=history_limit)
        return [{"role": h["role"], "content": h["content"]} for h in history]

    def clear_history(self, user_id: str):
        """清除指定用户的所有会话消息。"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
