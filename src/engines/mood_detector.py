"""AI-powered mood detection for adaptive personality responses.

Sprint 7: Uses DeepSeek Flash (fast, cheap) to detect user emotional state
from their message and selects the best personality mode automatically.
No hardcoded keyword matching -- the LLM handles all emotion classification.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict

import httpx


@dataclass
class MoodResult:
    """Result of mood detection.

    Attributes:
        mood: One of "sassy", "analyst", "gentle".
        confidence: Float 0.0 to 1.0 indicating detection certainty.
        emotion_label: Human-readable emotion label in Chinese (e.g. "焦虑", "开心").
    """
    mood: str
    confidence: float
    emotion_label: str


class MoodDetector:
    """AI-powered mood detection for adaptive personality.

    Uses DeepSeek Flash (same fast model used for free chat) to classify
    user emotional state. The detection prompt is intentionally short
    (under 50 words) for speed and cost.

    Results are cached per unique message to avoid redundant API calls
    within the same conversation turn.
    """

    # Open-ended prompt: let the LLM decide based on its understanding, not rules.
    DETECTION_PROMPT = (
        'You have 3 response styles:\n'
        '1. sassy — sharp, witty, like a close friend who tells hard truths with humor\n'
        '2. analyst — professional, precise, data-driven, like a trusted advisor\n'
        '3. gentle — warm, empathetic, validating, like a therapist who truly listens\n\n'
        'Given the user\'s message, which style would make them feel most understood? '
        'Return ONLY JSON: {"mood":"<pick one>","confidence":0.0-1.0,"emotion":"<their apparent emotion>"}'
    )

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash"):
        self.api_key = api_key
        self.model = model
        self._cache: Dict[str, MoodResult] = {}

    def detect(self, user_message: str) -> MoodResult:
        """Detect mood from user message.

        Args:
            user_message: The user's input text.

        Returns:
            MoodResult with detected mood, confidence score, and emotion label.
            On failure, defaults to sassy with 0.5 confidence.
        """
        msg_hash = hashlib.md5(user_message.encode()).hexdigest()
        if msg_hash in self._cache:
            return self._cache[msg_hash]

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            messages = [
                {"role": "system", "content": self.DETECTION_PROMPT},
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
            result = self._parse_response(content)
        except Exception:
            result = MoodResult(mood="sassy", confidence=0.5, emotion_label="中性")

        self._cache[msg_hash] = result
        return result

    def _parse_response(self, content: str) -> MoodResult:
        """Parse JSON from LLM response, with fallback to default."""
        json_match = re.search(r'\{.+?\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                mood = data.get("mood", "sassy")
                if mood not in ("sassy", "analyst", "gentle"):
                    mood = "sassy"
                confidence = float(data.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, confidence))
                emotion = str(data.get("emotion", "中性"))[:20]
                return MoodResult(mood=mood, confidence=confidence, emotion_label=emotion)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return MoodResult(mood="sassy", confidence=0.5, emotion_label="中性")

    def clear_cache(self):
        """Clear detection cache for a new conversation turn."""
        self._cache.clear()
