"""LLM 客户端 - 支持 Claude 和 DeepSeek."""
from dataclasses import dataclass
from typing import List, Optional, Union
import threading
import httpx
import json

from .prompts import PERSONALITY_PROMPTS, CHAT_PROMPT, USER_CONTEXT_TEMPLATE
from src.engines.bazi import BaziResult
from src.engines.mood_detector import MoodDetector, MoodResult
from src.rag.retriever import ChunkResult


@dataclass
class AnalysisResult:
    response: str
    tokens_used: int
    model: str


class FortuneLLM:
    """算命助手 LLM 封装 - 双模型：Flash(快聊) + Pro(深度分析)

    Sprint 7: personality_mode=None enables automatic mood detection.
    Server hardening: shared httpx client + Pro call semaphore.
    """

    # Limit concurrent Pro model calls to prevent server overload
    _pro_semaphore = threading.Semaphore(3)

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash", provider: str = "deepseek",
                 deep_model: str = "deepseek-v4-pro"):
        self.api_key = api_key
        self.model = model          # 快速模型 (日常聊天)
        self.deep_model = deep_model  # 深度模型 (命理分析)
        self.provider = provider
        # Sprint 7: AI mood detector (reuses same Flash model for speed)
        self.mood_detector = MoodDetector(api_key=api_key, model=model)
        # Server hardening: shared httpx client with connection pooling
        self._client = httpx.Client(
            timeout=httpx.Timeout(120.0, connect=15.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    def _resolve_mood(self, user_message: str, personality_mode: Optional[str]) -> str:
        """Auto-detect mood if personality_mode is None, else return override."""
        if personality_mode is not None:
            return personality_mode
        mood_result = self.mood_detector.detect(user_message)
        return mood_result.mood

    def chat(self, user_message: str, personality_mode: Optional[str] = None) -> AnalysisResult:
        """自由对话 - 用快速模型（V4 Flash），轻量人设提示。

        Args:
            user_message: User's input text.
            personality_mode: One of "sassy", "analyst", "gentle",
                             or None to auto-detect from message.
        """
        resolved_mode = self._resolve_mood(user_message, personality_mode)
        return self._call_deepseek_model(user_message, self.model, max_tokens=300,
                                         custom_prompt=CHAT_PROMPT)

    def chat_conversation(self, history: list, personality_mode: Optional[str] = None) -> str:
        """多轮对话 - 带完整上下文的自然聊天。

        Args:
            history: List of message dicts with "role" and "content".
            personality_mode: One of "sassy", "analyst", "gentle",
                             or None to auto-detect from last user message.
        """
        # Auto-detect mood from last user message if no override
        if personality_mode is None:
            last_user_msg = next(
                (m["content"] for m in reversed(history) if m["role"] == "user"),
                "",
            )
            mood_result = self.mood_detector.detect(last_user_msg) if last_user_msg else MoodResult("sassy", 0.5, "中性")
            personality_mode = mood_result.mood

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        messages = [{"role": "system", "content": CHAT_PROMPT}]
        messages.extend(history)
        resp = self._client.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers,
            json={"model": self.model, "messages": messages, "max_tokens": 500, "temperature": 0.8},
        )
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def analyze(
        self,
        chart_data: Union[BaziResult, str],
        references: List[ChunkResult],
        user_question: str,
        personality_mode: Optional[str] = None,
    ) -> AnalysisResult:
        """命理分析 - 用深度模型（V4 Pro），带 Flash 超时回退。

        If Pro model times out (>90s) or fails, automatically falls back
        to Flash for a faster (but less deep) response instead of crashing.
        """
        if isinstance(chart_data, str):
            chart_str = chart_data
        else:
            chart_str = self._format_chart(chart_data)
        refs_str = self._format_references(references)

        user_message = USER_CONTEXT_TEMPLATE.format(
            chart_data=chart_str,
            references=refs_str,
            question=user_question,
        )
        resolved_mode = self._resolve_mood(user_question, personality_mode)
        system_prompt = PERSONALITY_PROMPTS.get(resolved_mode, PERSONALITY_PROMPTS["sassy"])

        # Try Pro model first, fall back to Flash on timeout
        try:
            return self._call_deepseek_model(
                user_message, self.deep_model, max_tokens=2000,
                custom_prompt=system_prompt,
                timeout=90.0,  # Pro timeout: 90s
            )
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Pro model timeout/failure, falling back to Flash for faster response")
            try:
                # Flash fallback with analysis-specific prompt
                fallback_prompt = (
                    f"{system_prompt}\n\n"
                    "【注意】深度模型暂时繁忙，请用你的知识给出一份精简但完整的命理分析。"
                    "包含：八字排盘确认、日主分析、五行解读、大运趋势。300-500字。"
                )
                result = self._call_deepseek_model(
                    user_message, self.model, max_tokens=800,
                    custom_prompt=fallback_prompt,
                    timeout=30.0,
                )
                result.model = f"{self.model} (Pro fallback)"
                return result
            except Exception:
                return AnalysisResult(
                    response="命理分析引擎暂时繁忙，请稍后重试。或发送「今日运势」查看您的每日宜忌 🙏",
                    tokens_used=0, model="fallback")

    def _call_deepseek_model(self, user_message: str, model: str, max_tokens: int = 500,
                             use_system_prompt: bool = True,
                             custom_prompt: str = None) -> AnalysisResult:
        """调用 DeepSeek API — 使用共享连接池和并发控制"""
        is_pro = (model == self.deep_model)

        # Pro model: limit concurrency to prevent server overload
        if is_pro:
            acquired = self._pro_semaphore.acquire(timeout=120)
            if not acquired:
                return AnalysisResult(
                    response="服务繁忙，请稍后再试。当前排队人数较多，建议1分钟后重试 🙏",
                    tokens_used=0, model=model)

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            messages = []
            if custom_prompt:
                messages.append({"role": "system", "content": custom_prompt})
            elif use_system_prompt:
                prompt = PERSONALITY_PROMPTS.get("sassy", "")
                messages.append({"role": "system", "content": prompt})
            messages.append({"role": "user", "content": user_message})
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7,
            }
            resp = self._client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers, json=payload,
            )
            data = resp.json()
            if "error" in data:
                return AnalysisResult(
                    response=f"AI 服务暂时不可用，请稍后再试 🙏",
                    tokens_used=0, model=model)
            return AnalysisResult(
                response=data["choices"][0]["message"]["content"],
                tokens_used=data.get("usage", {}).get("total_tokens", 0),
                model=model,
            )
        except Exception:
            return AnalysisResult(
                response="AI 服务暂时不可用，请稍后重试 🙏",
                tokens_used=0, model=model)
        finally:
            if is_pro:
                self._pro_semaphore.release()

    def _format_chart(self, r: BaziResult) -> str:
        return f"""八字：{' '.join(r.bazi)}
日主：{r.day_master}
五行：{r.wuxing}
十神：{' '.join(r.shishen)}
格局：{r.geju}
用神：{r.yongshen}
大运：{' → '.join(f'{age}岁{ganzhi}' for age, ganzhi in r.dayun[:5])}
神煞：{'、'.join(r.shensha) if r.shensha else '无'}"""

    def _format_references(self, refs: List[ChunkResult]) -> str:
        lines = []
        for i, ref in enumerate(refs[:15], 1):
            lines.append(f"{i}. 【{ref.source}】\"{ref.text[:300]}...\" (相关度: {ref.score:.2f})")
        if not lines:
            return "（未找到直接相关古籍记载）"
        return "\n".join(lines)
