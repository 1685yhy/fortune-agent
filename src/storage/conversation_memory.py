"""Conversation memory — maintains a rolling summary of past interactions.

So the bot can say "上次我们聊到你的事业运..." instead of
"请提供出生日期" every time. Uses Flash for cheap summarization.
"""
import json
from typing import List, Dict, Optional


class ConversationMemory:
    """Lightweight conversation memory with rolling summaries.

    Stores up to 5 recent interaction summaries. Each summary captures
    what was discussed and key insights, so the LLM can reference them
    naturally in future conversations.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._cache: Dict[str, List[Dict]] = {}  # user_id -> list of summaries

    def add_interaction(self, user_id: str, user_msg: str, bot_reply: str,
                        intent: str = "", key_data: dict = None):
        """Record a significant interaction and update the rolling summary."""
        if user_id not in self._cache:
            self._cache[user_id] = []

        # Extract key info for the summary
        summary = {
            "intent": intent,
            "user_asked": user_msg[:200],
            "bot_did": intent or "conversation",
            "timestamp": "",
        }
        if key_data:
            summary.update(key_data)

        # Try to generate a smart one-line summary via Flash
        summary_text = self._summarize(user_msg, bot_reply, intent)
        if summary_text:
            summary["text"] = summary_text

        self._cache[user_id].append(summary)
        # Keep only last 5
        if len(self._cache[user_id]) > 5:
            self._cache[user_id] = self._cache[user_id][-5:]

    def get_context(self, user_id: str) -> str:
        """Get a natural-language context summary for LLM injection.

        Returns empty string if no history or API unavailable.
        """
        if user_id not in self._cache or not self._cache[user_id]:
            return ""

        memories = self._cache[user_id]
        if not memories:
            return ""

        lines = ["[以下是用户之前的对话记录，可以在回复中自然引用]"]
        for i, m in enumerate(memories[-3:], 1):  # last 3 interactions
            text = m.get("text", "")
            if text:
                lines.append(f"{i}. {text}")
            else:
                lines.append(f"{i}. 用户问了：{m.get('user_asked', '')[:80]}")

        return "\n".join(lines)

    def _summarize(self, user_msg: str, bot_reply: str, intent: str) -> str:
        """Generate a one-line summary of the interaction via Flash."""
        if not self.api_key:
            return ""

        try:
            import httpx
            prompt = (
                f"用户问：「{user_msg[:150]}」\n"
                f"系统回复了关于{intent or '对话'}的内容。\n"
                "请用15字以内的中文总结这次对话的核心内容。直接返回文本。"
            )
            resp = httpx.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 50, "temperature": 0.3},
                timeout=15.0,
            )
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return ""
