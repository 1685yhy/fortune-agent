"""AI-powered intent classification — replaces hardcoded INTENT_KEYWORDS.

P1 of AI-native polish. Uses a single DeepSeek Flash call to classify
user intent into one of 12 categories based on semantic understanding,
not keyword matching. Handles colloquial expressions like "看下命",
"最近运气咋样", "算一下" that keyword matching misses.
"""
import hashlib
import json
import re
from typing import Dict, Optional

import httpx


INTENT_CLASSIFY_PROMPT = """You are an intent classifier for a Chinese fortune-telling AI assistant (易理明灯).
Given a user message, classify it into EXACTLY ONE of these intents:

- "bazi" — 八字命理, fortune reading, birth chart, destiny, 算命, 排盘, 运势, life path
- "ziwei" — 紫微斗数, purple star astrology
- "liuyao" — 六爻, I-Ching divination, coin toss, 占卜, 起卦, 问卦
- "fengshui" — 风水, feng shui, home layout, direction analysis
- "zeri" — 择日, date selection, auspicious day, 搬家/结婚/开业选日子
- "mianxiang" — 面相, face reading, 手相, palm reading, 看相
- "qimen" — 奇门遁甲, strategic divination
- "xingming" — 姓名学, name analysis, 起名, 改名
- "hehun" — 合婚, relationship compatibility, 配对, 婚姻匹配
- "dream" — 解梦, dream interpretation, 梦见
- "advisor" — 用户需要建议/指导, general life advice (but NOT fortune-telling)
- "free_chat" — casual chat, greeting, emotional expression, general conversation, 闲聊

## Classification rules:
1. If the message contains a BIRTH DATE (year-month-day pattern), it's "bazi" regardless of other words.
2. If the message describes a DREAM (昨晚梦到, 梦见, 做梦, 梦里), it's "dream".
3. If the message is purely emotional (焦虑, 难过, 开心, 纠结) with NO fortune-telling request → "free_chat".
4. If the message mentions specific divination terms, use that intent.
5. "看下命", "算一下", "运气怎么样" → "bazi" (these are colloquial ways to ask for fortune reading).

Return ONLY JSON:
{"intent": "<the classified intent>", "confidence": 0.0-1.0}

IMPORTANT: Always return a valid intent. Default to "free_chat" if truly uncertain."""


class IntentClassifier:
    """AI-powered intent classifier — zero hardcoded keyword matching."""

    # Fast path: if message contains a birth date pattern, it's bazi — no AI call needed
    BIRTH_DATE_PATTERN = re.compile(r'\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}')

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash"):
        self.api_key = api_key
        self.model = model
        self._cache: Dict[str, str] = {}

    def classify(self, user_message: str) -> Optional[str]:
        """Classify user intent. Returns intent string or None for free_chat.

        Uses fast path (birth date regex) when possible, falls back to AI.
        None return means "no specific intent" → should use free_chat.
        """
        if not user_message:
            return None

        # Fast path: birth date → bazi immediately, no AI call
        if self.BIRTH_DATE_PATTERN.search(user_message):
            return "bazi"

        msg_hash = hashlib.md5(user_message.encode()).hexdigest()
        if msg_hash in self._cache:
            cached = self._cache[msg_hash]
            return None if cached == "free_chat" else cached

        if not self.api_key:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            messages = [
                {"role": "system", "content": INTENT_CLASSIFY_PROMPT},
                {"role": "user", "content": user_message[:500]},
            ]
            resp = httpx.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 100,
                    "temperature": 0.1,
                },
                timeout=30.0,
            )
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            intent = self._parse_response(content)
        except Exception:
            intent = None

        # Cache even failures as free_chat to avoid repeated failed calls
        cache_val = intent if intent else "free_chat"
        self._cache[msg_hash] = cache_val

        return intent

    def _parse_response(self, content: str) -> Optional[str]:
        """Parse JSON from LLM response."""
        json_match = re.search(r'\{.+?\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                intent = data.get("intent", "free_chat")
                # Validate against known intents
                valid = {"bazi", "ziwei", "liuyao", "fengshui", "zeri", "mianxiang",
                         "qimen", "xingming", "hehun", "dream", "advisor", "free_chat"}
                if intent in valid:
                    return None if intent == "free_chat" else intent
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return None

    def clear_cache(self):
        """Clear classification cache."""
        self._cache.clear()
