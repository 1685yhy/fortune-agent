"""Combined message analyzer — detects emotion + intent in ONE Flash call.

Replaces two sequential AI calls (EmotionSoother + IntentClassifier) with
a single call that handles both. Cuts free-chat latency from ~9s to ~5s.

The combined prompt is carefully designed to maintain accuracy on both
tasks while halving API calls.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import httpx


@dataclass
class MessageAnalysis:
    """Combined result: emotion detection + intent classification."""
    needs_soothe: bool
    soothe_text: str
    emotion_label: Optional[str]
    intent: Optional[str]  # None = free_chat
    is_sharing: bool = False  # True = user is telling a personal story


COMBINED_PROMPT = """You are a message analyzer for a Chinese fortune-telling AI (易理明灯).
Analyze the user's message and return BOTH:

## 1. Emotional Analysis
- needs_soothe: true if the user is expressing distress/sadness/anxiety/anger/heartbreak/confusion and needs emotional acknowledgment BEFORE any fortune-telling analysis. false for neutral questions, birth info, casual greetings.
- If needs_soothe: write a BRIEF (1-2 sentence), warm, modern Chinese acknowledgment specific to their situation. NO classical/文言文. NO "小友""老夫".
- If NOT needs_soothe: soothe_text = ""

## 2. Intent Classification
Classify into EXACTLY ONE: bazi, ziwei, liuyao, fengshui, zeri, mianxiang, qimen, xingming, hehun, dream, calendar, free_chat

Rules:
- Birth date (year-month-day) = "bazi" regardless of other words
- Dream description (梦见/梦到/做梦) = "dream"
- Daily fortune / today's luck requests (今日运势/今天运气/今日宜忌/今天宜忌/今日运程/今日日历/今天适合) = "calendar"
- Pure emotional expression with NO fortune-telling request = "free_chat"
- Colloquial fortune-telling: "看下命""算一下""运气怎么样" = "bazi"

## 3. Sharing Detection (心事树洞)
- is_sharing: true ONLY if user is telling a personal story with emotional depth
- Sharing signals: messages with "我" + emotional words + personal situation (not a request)
- NOT sharing (must return false):
  * Knowledge questions: "告诉我X", "X是什么意思", "X代表什么", "有没有人X"
  * Service requests: "帮我看看X", "帮我算X", "请帮我看X", "想看X"
  * Urgent requests: "在线等", "急", "追分"
  * Direct fortune requests, birth date info, short greetings

Return ONLY JSON:
{"needs_soothe": bool, "soothe_text": "安抚文本或空", "emotion": "情绪标签", "intent": "意图分类", "is_sharing": bool}"""


class MessageAnalyzer:
    """Single-pass message analyzer: emotion + intent in one Flash call."""

    # Fast path: birth date pattern → bazi immediately
    BIRTH_DATE_PATTERN = re.compile(r'\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}')

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash"):
        self.api_key = api_key
        self.model = model
        self._cache: Dict[str, MessageAnalysis] = {}

    def analyze(self, user_message: str) -> MessageAnalysis:
        """Analyze message for emotion + intent in one pass.

        Returns MessageAnalysis with both results.
        Uses cache for repeated messages.
        """
        if not user_message:
            return MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent=None)

        # Fast path: birth date → bazi, skip AI call
        if self.BIRTH_DATE_PATTERN.search(user_message):
            return MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent="bazi")

        msg_hash = hashlib.md5(user_message.encode()).hexdigest()
        if msg_hash in self._cache:
            return self._cache[msg_hash]

        if not self.api_key:
            return MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent=None)

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            messages = [
                {"role": "system", "content": COMBINED_PROMPT},
                {"role": "user", "content": user_message[:500]},
            ]
            resp = httpx.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 250,
                    "temperature": 0.3,
                },
                timeout=30.0,
            )
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            result = self._parse_response(content)
        except Exception:
            result = MessageAnalysis(needs_soothe=False, soothe_text="",
                                     emotion_label=None, intent=None)

        self._cache[msg_hash] = result
        return result

    def _parse_response(self, content: str) -> MessageAnalysis:
        """Parse combined JSON response."""
        json_match = re.search(r'\{.+?\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                needs = bool(data.get("needs_soothe", False))
                soothe = str(data.get("soothe_text", ""))[:300]
                emotion = data.get("emotion", "")
                emotion = str(emotion)[:20] if emotion else None
                intent = data.get("intent", "free_chat")
                valid = {"bazi", "ziwei", "liuyao", "fengshui", "zeri",
                         "mianxiang", "qimen", "xingming", "hehun", "dream",
                         "calendar", "xuetang", "advisor", "free_chat"}
                if intent not in valid:
                    intent = "free_chat"
                if needs and not soothe:
                    needs = False
                is_sharing = bool(data.get("is_sharing", False))
                return MessageAnalysis(needs_soothe=needs, soothe_text=soothe,
                                       emotion_label=emotion,
                                       intent=None if intent == "free_chat" else intent,
                                       is_sharing=is_sharing)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return MessageAnalysis(needs_soothe=False, soothe_text="",
                               emotion_label=None, intent=None)

    def clear_cache(self):
        self._cache.clear()
