"""AI-powered mood detection for adaptive personality responses.

Sprint 7: Uses DeepSeek Flash (fast, cheap) to detect user emotional state
from their message and selects the best personality mode automatically.
No hardcoded keyword matching -- the LLM handles all emotion classification.

k42 修复（用户可见静默错判：难受的用户被给到「毒舌闺蜜」人设）：
- 根因：本模块直连原生 /v1/chat/completions + max_tokens=100；该端点下
  deepseek-flash 是推理模型，reasoning_content 先占满 100 token 预算 →
  JSON 答案被截断（finish_reason=length）→ 解析失败 → 静默回退 sassy。
  实测 10 条样例 6 条命中（全部是「难受」类输入）。
- 改法：改走统一 LLM 层 src/llm/client.py（k33/A11 同款最小接线）——
  Anthropic 兼容端点 + thinking disabled，预算只计正文，截断根因消失；
  口径与主聊天链一致（deepseek-flash → [1m] 变体）。
- 兜底安全化：解析失败/调用异常一律回退 :data:`SAFE_FALLBACK_MOOD`（温柔），
  不再回退 sassy（深夜情绪陪伴场景下毒舌是反向体验）。
- 失败可见：失败记 warning（内容长度 / 疑似截断 / 异常类型），
  不含用户隐私原文，亦不回显模型返回的原始文本。
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Dict

from src.utils.text_clean import strip_emoji

logger = logging.getLogger(__name__)

# 安全兜底人设（k42）：产品是深夜情绪陪伴场景，判定失败时「温柔/中性」对
# 正在难受的用户无害；毒舌（sassy）会让难受的人更难受，不得作为默认。
# 置信度/情绪标签沿用修复前的兜底值（0.5 / 中性），仅人设从 sassy 改 gentle。
SAFE_FALLBACK_MOOD = "gentle"
_SAFE_FALLBACK_CONFIDENCE = 0.5
_SAFE_FALLBACK_EMOTION = "中性"

# JSON 答案的 token 预算：k42 前为 100，在原生推理端点下被 reasoning 吃光
# （实测 reasoning_content 250-405 字符 / finish_reason=length）。改走统一层
# （thinking disabled，预算只计正文）后，完整 JSON 仅约 30-40 token；
# 200 留足余量（含中文情绪标签波动），且实测 10/10 稳定出完整 JSON。
MAX_TOKENS = 200

_VALID_MOODS = ("sassy", "analyst", "gentle")


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


def _safe_fallback() -> MoodResult:
    """判定失败时的安全兜底（温柔），见 :data:`SAFE_FALLBACK_MOOD`。"""
    return MoodResult(mood=SAFE_FALLBACK_MOOD,
                      confidence=_SAFE_FALLBACK_CONFIDENCE,
                      emotion_label=_SAFE_FALLBACK_EMOTION)


def _looks_truncated(content: str) -> bool:
    """粗判响应是否被 max_tokens 截断（花括号未闭合即截断形态）。"""
    c = content or ""
    return c.count("{") > c.count("}")


class MoodDetector:
    """AI-powered mood detection for adaptive personality.

    Uses DeepSeek Flash (same fast model used for free chat) to classify
    user emotional state. The detection prompt is intentionally short
    (under 50 words) for speed and cost.

    k42：调用走统一 LLM 层（src/llm/client.py），端点为 Anthropic 兼容
    + thinking disabled —— 与主聊天链同口径，避免推理模型吃光 max_tokens。

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

    def __init__(self, api_key: str, model: str = "deepseek-flash"):
        self.api_key = api_key
        self.model = model
        self._cache: Dict[str, MoodResult] = {}

    def detect(self, user_message: str) -> MoodResult:
        """Detect mood from user message.

        Args:
            user_message: The user's input text.

        Returns:
            MoodResult with detected mood, confidence score, and emotion label.
            On failure, defaults to the safe mood (gentle) with 0.5 confidence.
        """
        msg_hash = hashlib.md5(user_message.encode()).hexdigest()
        if msg_hash in self._cache:
            return self._cache[msg_hash]

        try:
            content = self._call_llm(user_message)
            result = self._parse_response(content)
        except Exception as e:
            # k42 失败可见：不再静默。只记错误类型/摘要，不含用户原文
            # （模型异常文本可能回显用户输入，故同时截断长度）。
            logger.warning(
                "情绪人设判定失败（LLM 调用异常 %s: %s），回退安全人设 %s",
                type(e).__name__, str(e)[:120], SAFE_FALLBACK_MOOD)
            result = _safe_fallback()

        self._cache[msg_hash] = result
        return result

    def _call_llm(self, user_message: str) -> str:
        """调用统一 LLM 层取原始文本（失败抛异常，由 detect 兜底）。

        - 函数内 import：调用期解析模块属性，评测/装配对
          ``src.llm.client.deepseek_anthropic_completion`` 的 patch 才生效
          （k33/A11 同款接线；顶层 from-import 会在导入期绑定函数对象）。
        - key 面零变化：仍只用构造时传入的 ``self.api_key``，不新增任何
          env 读取（多认一个变量就可能在别的部署上凭空开启外呼）。
        - 判定参数与修复前逐项同值：prompt 逐字不变、用户消息截断 500 字符、
          temperature=0.1、timeout=30.0；仅 token 预算按 JSON 答案需要上调，
          且端点换统一层（这正是截断根因的修法）。
        """
        from src.llm.client import deepseek_anthropic_completion
        messages = [
            {"role": "system", "content": self.DETECTION_PROMPT},
            {"role": "user", "content": user_message[:500]},
        ]
        content = deepseek_anthropic_completion(
            self.api_key, messages, model=self.model,
            max_tokens=MAX_TOKENS, temperature=0.1, timeout=30.0,
        )
        # 统一层已 strip_emoji；此处幂等兜底（mock/别路注入可能带 emoji）
        return strip_emoji(content or "").strip()

    def _parse_response(self, content: str) -> MoodResult:
        """Parse JSON from LLM response; 任何失败 → 安全兜底 + warning。

        k42：失败面（截断/非 JSON/非法 mood）此前静默且回退毒舌 ——
        现在一律回退温柔并记 warning（长度 + 疑似截断标志，不含原文）。
        """
        json_match = re.search(r'\{.+?\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                mood = data.get("mood")
                if mood not in _VALID_MOODS:
                    logger.warning(
                        "情绪人设解析失败（mood 值非法 %r），回退安全人设 %s",
                        str(mood)[:20], SAFE_FALLBACK_MOOD)
                    return _safe_fallback()
                confidence = float(data.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, confidence))
                emotion = str(data.get("emotion", "中性"))[:20]
                return MoodResult(mood=mood, confidence=confidence, emotion_label=emotion)
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning(
                    "情绪人设解析失败（JSON 异常 %s，内容长度=%d，疑似截断=%s），"
                    "回退安全人设 %s",
                    type(e).__name__, len(content), _looks_truncated(content),
                    SAFE_FALLBACK_MOOD)
                return _safe_fallback()
        logger.warning(
            "情绪人设解析失败（未找到 JSON，内容长度=%d，疑似截断=%s），回退安全人设 %s",
            len(content), _looks_truncated(content), SAFE_FALLBACK_MOOD)
        return _safe_fallback()

    def clear_cache(self):
        """Clear detection cache for a new conversation turn."""
        self._cache.clear()
