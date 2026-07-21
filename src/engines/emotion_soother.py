"""AI-powered emotion detection + soothing — replaces hardcoded keyword matching.

P0 of AI-native polish. Uses a single DeepSeek Flash call to:
1. Detect if user needs emotional support
2. Generate a warm, contextual 1-2 sentence acknowledgment

No hardcoded keywords, no template responses. Every soothe is unique to the user's message.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import httpx


@dataclass
class SootherResult:
    """Result of emotion detection + soothe generation."""
    needs_soothe: bool
    emotion_label: Optional[str]  # e.g. "焦虑", "悲伤", "愤怒", "心碎"
    soothe_text: str              # AI-generated acknowledgment, empty if no distress


DETECT_AND_SOOTHE_PROMPT = """You are an emotional support detector for a modern Chinese fortune-telling AI assistant.

Analyze the user's message. Determine:
1. Do they need emotional soothing BEFORE any fortune-telling analysis? (true/false)
   - YES if: expressing distress, sadness, anxiety, anger, heartbreak, confusion, overwhelm
   - NO if: neutral question, providing birth info, casual greeting, factual inquiry
2. If YES, write a BRIEF (1-2 sentences max), warm, modern Chinese acknowledgment.
   - Be specific to their situation, not generic
   - Use natural modern Chinese, NO classical/文言文
   - NO "小友", "老夫", "老朽", "本道"
3. If NO, soothe_text should be empty string "".

Return ONLY JSON:
{"needs_soothe": true/false, "emotion": "简短中文情绪标签", "soothe_text": "你的安抚文本"}

IMPORTANT: If the user is just asking a factual question or providing birth info, needs_soothe MUST be false."""


class EmotionSoother:
    """AI-powered emotion soother — zero hardcoded keywords.

    Uses the SAME MoodDetector pattern (single Flash call) but with a
    prompt optimized for detection + generation in one pass.
    """

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash"):
        self.api_key = api_key
        self.model = model
        self._cache: Dict[str, SootherResult] = {}

    def detect_and_soothe(self, user_message: str) -> Tuple[str, Optional[str]]:
        """Detect emotion and generate soothe text.

        Returns (soothe_text, emotion_label) — same interface as handler._soothe().
        Returns ("", None) if no emotional support needed.
        """
        if not user_message or not self.api_key:
            return "", None

        msg_hash = hashlib.md5(user_message.encode()).hexdigest()
        if msg_hash in self._cache:
            cached = self._cache[msg_hash]
            return (cached.soothe_text, cached.emotion_label)

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            messages = [
                {"role": "system", "content": DETECT_AND_SOOTHE_PROMPT},
                {"role": "user", "content": user_message[:500]},
            ]
            resp = httpx.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 200,
                    "temperature": 0.7,
                },
                timeout=30.0,
            )
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            result = self._parse_response(content)
        except Exception:
            result = SootherResult(needs_soothe=False, emotion_label=None, soothe_text="")

        self._cache[msg_hash] = result
        if result.needs_soothe:
            return (result.soothe_text, result.emotion_label)
        return ("", None)

    def _parse_response(self, content: str) -> SootherResult:
        """Parse JSON from LLM response, with fallback."""
        json_match = re.search(r'\{.+?\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                needs = bool(data.get("needs_soothe", False))
                emotion = str(data.get("emotion", ""))[:20] if data.get("emotion") else None
                soothe = str(data.get("soothe_text", ""))[:300] if data.get("soothe_text") else ""
                if needs and not soothe:
                    needs = False  # can't soothe without text
                return SootherResult(needs_soothe=needs, emotion_label=emotion, soothe_text=soothe)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return SootherResult(needs_soothe=False, emotion_label=None, soothe_text="")

    def clear_cache(self):
        """Clear cache for a new conversation turn."""
        self._cache.clear()
