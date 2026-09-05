"""LLM 客户端 - 支持 Claude 和 DeepSeek."""
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import List, Union, Optional, Callable, AsyncIterator
import threading
import httpx

logger = logging.getLogger(__name__)

from .prompts import SYSTEM_PROMPT, CHAT_PROMPT, USER_CONTEXT_TEMPLATE
from src.engines.bazi import BaziResult
from src.rag.retriever import ChunkResult

# emoji 强收敛（v2026-08-17，PM 反馈回复 emoji 过多显 low）：
# 所有 LLM 输出在客户端统一后处理剔除 emoji——提示词兜底 + 此处硬兜底。
# strip_emoji 实现已下沉至 src/utils/text_clean.py（轻量零依赖），
# 供统一模型层与各引擎直调点共用。
from src.utils.text_clean import strip_emoji

# Bugfix: 原生 /v1/chat/completions 下 deepseek-v4-flash 是推理模型，
# reasoning_content 会占满 max_tokens 导致 content 为空（finish_reason=length，
# 日志 "Empty/short reply (0 chars)"）。统一改用 Anthropic 兼容端点并显式
# 关闭 thinking（与 src/engines/calendar.py 的修复方式一致），内容稳定返回。
ANTHROPIC_MESSAGES_URL = "https://api.deepseek.com/anthropic/v1/messages"

# ── GLM（智谱）— 对话降级链路（L5-1）：免费用户额度用尽后切 GLM-4-Flash ──
# OpenAI 兼容端点（已验证可调：ZHIPU_API_KEY 在 .env，配置见 src/config.py）。
GLM_COMPLETIONS_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_DEFAULT_MODEL = "glm-4-flash"

# 流式回调类型：stream_cb(event_type: str, payload: dict)
#   event_type: "chunk" → payload {"text": str}（正文增量）
# 由调用方（SSE 端点）负责跨线程投递；LLM 层只负责在生成线程里同步回调。
StreamCallback = Optional[Callable[[str, dict], None]]


def _anthropic_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }


def _anthropic_payload(messages: list, model: str, max_tokens: int,
                       temperature: float, stream: bool = False,
                       tools: Optional[list] = None,
                       tool_choice: Optional[dict] = None) -> dict:
    payload = {
        "model": _anthropic_model_name(model),
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "messages": messages,
        "stream": stream,
    }
    # Task 3B：原生 tool_use 透传（不传时行为与现状完全一致）
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return payload


async def deepseek_anthropic_completion_stream(
    api_key: str,
    messages: list,
    model: str = "deepseek-v4-flash",
    max_tokens: int = 1000,
    temperature: float = 0.7,
    timeout: float = 60.0,
) -> AsyncIterator[str]:
    """DeepSeek Anthropic 兼容端点的真实 SSE 流式调用（stream: true）。

    逐段 yield 文本增量（content_block_delta / text_delta）；上游错误抛异常。
    实测 deepseek-v4-flash[1m] 支持 Anthropic 原生流式（message_start →
    content_block_delta* → message_stop），故优先真实流式而非分句模拟。
    """
    payload = _anthropic_payload(messages, model, max_tokens, temperature, stream=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        async with client.stream(
            "POST", ANTHROPIC_MESSAGES_URL,
            headers=_anthropic_headers(api_key), json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "ignore")
                raise RuntimeError(
                    f"LLM stream HTTP {resp.status_code}: {body[:200]}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type")
                if etype == "content_block_delta":
                    delta = evt.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "") or ""
                        if text:
                            yield text
                elif etype == "message_stop":
                    break


def _run_stream_feed_callback(
    api_key: str,
    messages: list,
    model: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
    stream_cb: StreamCallback,
) -> str:
    """在调用方（线程池）线程内跑 async 流式，增量实时喂给 stream_cb，返回完整文本。

    线程内自建事件循环跑流式生成器（同步管道无法 await）；
    流式失败抛异常，由调用方按原有重试/降级策略处理。
    """
    buf: list = []

    def _feed(text: str):
        # emoji 强收敛：流式增量也逐段剔除（残缺代理对由 _LONE_SURROGATE_RE 兜底）
        text = strip_emoji(text)
        if not text:
            return
        buf.append(text)
        if stream_cb:
            try:
                stream_cb("chunk", {"text": text})
            except Exception:
                pass  # 回调失败（如连接已断开）不影响生成

    async def _collect():
        async for delta in deepseek_anthropic_completion_stream(
            api_key, messages, model=model, max_tokens=max_tokens,
            temperature=temperature, timeout=timeout,
        ):
            _feed(delta)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_collect())
    finally:
        try:
            # k7d：message_stop break 后 async 生成器 aclose 由 call_soon 调度，
            # run_until_complete 返回后立即 close → aclose task 未跑完 →
            # 「Task was destroyed but it is pending! async_generator_athrow」
            # 噪音（实测每次流式正常收尾必现，与内容中断无因果）。让事件循环
            # 再转一圈收尾 aclose 后再关，日志归零。
            loop.run_until_complete(asyncio.sleep(0.01))
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
    return "".join(buf)


def deepseek_anthropic_completion(
    api_key: str,
    messages: list,
    model: str = "deepseek-v4-flash",
    max_tokens: int = 1000,
    temperature: float = 0.7,
    timeout: float = 60.0,
    client: Optional[httpx.Client] = None,
    stream_cb: StreamCallback = None,
    tools: Optional[list] = None,
    tool_choice: Optional[dict] = None,
) -> str:
    """调用 DeepSeek Anthropic 兼容端点（thinking disabled），返回文本内容。

    响应为空 / 解析失败 / 上游错误时抛异常，由调用方决定重试或降级。
    stream_cb 提供时走真实流式：增量实时回调（"chunk" 事件），返回完整文本；
    注意：流式分支（_run_stream_feed_callback）不透传 tools/tool_choice，
    传入会被静默丢弃——需要解析 tool_use 块时请改用 deepseek_anthropic_messages。
    tools/tool_choice（Task 3B）：透传原生 tool_use 参数；不传时行为与现状完全一致。
    """
    if stream_cb is not None:
        return _run_stream_feed_callback(
            api_key, messages, model, max_tokens, temperature, timeout, stream_cb)

    payload = _anthropic_payload(messages, model, max_tokens, temperature,
                                 tools=tools, tool_choice=tool_choice)
    headers = _anthropic_headers(api_key)
    if client is not None:
        resp = client.post(ANTHROPIC_MESSAGES_URL, headers=headers, json=payload)
    else:
        resp = httpx.post(ANTHROPIC_MESSAGES_URL, headers=headers, json=payload, timeout=timeout)
    data = resp.json()
    if "error" in data or data.get("type") == "error":
        raise RuntimeError(str(data.get("error") or data)[:200])
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "").strip()
            break
    if not text:
        raise ValueError(f"Empty content from LLM (stop_reason={data.get('stop_reason')})")
    return strip_emoji(text)


def deepseek_anthropic_messages(
    api_key: str,
    messages: list,
    model: str = "deepseek-v4-flash",
    max_tokens: int = 1000,
    temperature: float = 0.7,
    timeout: float = 60.0,
    client: Optional[httpx.Client] = None,
    tools: Optional[list] = None,
    tool_choice: Optional[dict] = None,
) -> dict:
    """调用 DeepSeek Anthropic 兼容端点，返回完整响应 dict（不抽取文本）。

    Task 3B：供原生 tool_use 循环使用（需读取 content 中的 tool_use 块与
    stop_reason）。复用同一 payload/headers 构造（tools 透传）。
    错误响应抛异常，由调用方决定重试或降级。
    """
    payload = _anthropic_payload(messages, model, max_tokens, temperature,
                                 tools=tools, tool_choice=tool_choice)
    headers = _anthropic_headers(api_key)
    if client is not None:
        resp = client.post(ANTHROPIC_MESSAGES_URL, headers=headers, json=payload)
    else:
        resp = httpx.post(ANTHROPIC_MESSAGES_URL, headers=headers, json=payload,
                          timeout=timeout)
    data = resp.json()
    if "error" in data or data.get("type") == "error":
        raise RuntimeError(str(data.get("error") or data)[:200])
    return data


def _anthropic_model_name(model: str) -> str:
    """推理模型在 Anthropic 兼容端点上的模型名（1M 上下文变体，实测最快最稳）。"""
    if model == "deepseek-v4-flash":
        return "deepseek-v4-flash[1m]"
    return model


# ── GLM（智谱）OpenAI 兼容调用（对话降级链路，L5-1）───────────────────
# 与 DeepSeek Anthropic 兼容端点并列的第二种 provider：base_url/api_key/model
# 全参数化，_chat_lite 中按 key 配置选择。流式失败且已有内容流出时返回已流出
# 文本（不抛异常），避免调用方二次回退产生重复内容。

def _glm_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _glm_payload(messages: list, model: str, max_tokens: int,
                 temperature: float, stream: bool = False) -> dict:
    return {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }


async def glm_openai_completion_stream(
    api_key: str,
    messages: list,
    model: str = GLM_DEFAULT_MODEL,
    max_tokens: int = 400,
    temperature: float = 0.7,
    timeout: float = 45.0,
) -> AsyncIterator[str]:
    """GLM OpenAI 兼容端点的真实 SSE 流式调用（stream: true）。

    逐段 yield 文本增量（choices[0].delta.content）；上游错误抛异常。
    """
    payload = _glm_payload(messages, model, max_tokens, temperature, stream=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        async with client.stream(
            "POST", GLM_COMPLETIONS_URL,
            headers=_glm_headers(api_key), json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "ignore")
                raise RuntimeError(
                    f"GLM stream HTTP {resp.status_code}: {body[:200]}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue
                try:
                    delta = (evt.get("choices") or [{}])[0].get("delta") or {}
                    text = delta.get("content") or ""
                except (AttributeError, IndexError, TypeError):
                    continue
                if text:
                    yield text


def _run_glm_stream_feed_callback(
    api_key: str,
    messages: list,
    model: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
    stream_cb: StreamCallback,
) -> str:
    """在调用方（线程池）线程内跑 GLM async 流式，增量实时喂给 stream_cb。

    流式失败时：已有内容流出 → 以已流出文本作为结果返回（不回退重发，
    避免调用方降级回退产生重复内容）；尚未流出任何内容 → 抛异常由调用方回退。
    """
    buf: list = []

    def _feed(text: str):
        text = strip_emoji(text)
        if not text:
            return
        buf.append(text)
        if stream_cb:
            try:
                stream_cb("chunk", {"text": text})
            except Exception:
                pass

    async def _collect():
        async for delta in glm_openai_completion_stream(
            api_key, messages, model=model, max_tokens=max_tokens,
            temperature=temperature, timeout=timeout,
        ):
            _feed(delta)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_collect())
    except Exception:
        if buf:
            logger.warning("GLM 流式中途失败，返回已流出内容 %d chars: model=%s",
                           len("".join(buf)), model)
            return "".join(buf)
        raise
    finally:
        try:
            loop.close()
        except Exception:
            pass
    return "".join(buf)


def glm_openai_completion(
    api_key: str,
    messages: list,
    model: str = GLM_DEFAULT_MODEL,
    max_tokens: int = 400,
    temperature: float = 0.7,
    timeout: float = 45.0,
    client: Optional[httpx.Client] = None,
    stream_cb: StreamCallback = None,
) -> str:
    """调用 GLM OpenAI 兼容端点，返回文本内容。

    响应为空 / 解析失败 / 上游错误时抛异常，由调用方决定回退（DeepSeek）。
    stream_cb 提供时走真实流式：增量实时回调（"chunk" 事件），返回完整文本。
    """
    if stream_cb is not None:
        return _run_glm_stream_feed_callback(
            api_key, messages, model, max_tokens, temperature, timeout, stream_cb)

    payload = _glm_payload(messages, model, max_tokens, temperature)
    headers = _glm_headers(api_key)
    if client is not None:
        resp = client.post(GLM_COMPLETIONS_URL, headers=headers, json=payload)
    else:
        resp = httpx.post(GLM_COMPLETIONS_URL, headers=headers,
                          json=payload, timeout=timeout)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(str(data.get("error"))[:200])
    try:
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except (AttributeError, IndexError, TypeError):
        text = ""
    text = (text or "").strip()
    if not text:
        raise ValueError(f"Empty content from GLM (finish={data.get('finish_reason')})")
    return strip_emoji(text)


def _log_llm_failure(caller: str, model: str, exc: Exception, retry: bool = False):
    """统一记录 LLM 调用失败/超时（供调用方排查上游质量与超时配置）。

    区分超时（httpx.TimeoutException）与一般错误（认证/限流/网络等），
    重试后的最终失败以 ERROR 级别记录。
    """
    timeout = isinstance(exc, httpx.TimeoutException)
    kind = "LLM 超时" if timeout else "LLM 调用失败"
    level = logger.error if retry else logger.warning
    level("%s: caller=%s model=%s 错误类型=%s 详情=%s",
          kind, caller, model, type(exc).__name__, str(exc)[:200])


@dataclass
class AnalysisResult:
    response: str
    tokens_used: int
    model: str


class FortuneLLM:
    """算命助手 LLM 封装 - 双模型：Flash(快聊) + Pro(深度分析)
    Server hardening: shared httpx client + Pro call semaphore.
    """

    # Limit concurrent Pro model calls to prevent server overload
    _pro_semaphore = threading.Semaphore(3)

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash", provider: str = "deepseek",
                 deep_model: str = "deepseek-v4-flash", glm_api_key: str = ""):
        self.api_key = api_key
        self.model = model          # 快速模型 (日常聊天)
        self.deep_model = deep_model  # 深度模型 (命理分析)
        self.provider = provider
        # 降级链路（L5-1）：智谱 GLM key（未显式传入时回退环境变量 ZHIPU_API_KEY）
        self.glm_api_key = glm_api_key or os.environ.get("ZHIPU_API_KEY", "").strip()
        # Server hardening: shared httpx client with connection pooling
        self._client = httpx.Client(
            timeout=httpx.Timeout(120.0, connect=15.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    def chat(self, user_message: str, lite: bool = False,
             system_prompt: str = None) -> AnalysisResult:
        """自由对话 - 用快速模型（V4 Flash），轻量人设提示。

        system_prompt（B2-11）：非降级路径自定义系统提示——None 时兜底
        CHAT_PROMPT（_call_deepseek_model 默认）；主链无会话存储时 handler
        注入 [可用工具清单] + CHAT_PROMPT，与主链首轮同口径。
        lite=True（降级链路）：GLM-4-Flash + CHAT_PROMPT_LITE 精简回复，
        忽略 system_prompt。
        """
        if lite:
            return AnalysisResult(
                response=self._chat_lite(user_message=user_message, max_tokens=400),
                tokens_used=0, model=GLM_DEFAULT_MODEL)
        return self._call_deepseek_model(user_message, self.model, max_tokens=1000,
                                         system_prompt=system_prompt)

    def chat_conversation(self, history: list, stream_cb: StreamCallback = None,
                          lite: bool = False) -> str:
        """多轮对话 - 带完整上下文的自然聊天。

        Bugfix: 改走 Anthropic 兼容端点 + thinking disabled，避免推理占满
        max_tokens 导致空回复。

        stream_cb 提供时走真实流式（chunk 增量实时回调）；流式模式下
        不做内部自动重试（避免半截重发），失败抛异常由调用方兜底。

        lite=True（降级链路）：切 GLM-4-Flash（OpenAI 兼容端点）+ 精简
        prompt，短回复、不调工具；GLM 失败自动回退 DeepSeek 同精简 prompt。
        """
        messages = [{"role": "system", "content": CHAT_PROMPT}]
        messages.extend(history)
        if lite:
            return self._chat_lite(history=messages, max_tokens=400,
                                   stream_cb=stream_cb)
        try:
            return deepseek_anthropic_completion(
                self.api_key, messages, model=self.model,
                max_tokens=1000, temperature=0.8, timeout=60.0,
                client=self._client, stream_cb=stream_cb,
            )
        except Exception as e:
            _log_llm_failure("chat_conversation", self.model, e)
            if stream_cb is not None:
                raise
            try:
                # Retry once
                return deepseek_anthropic_completion(
                    self.api_key, messages, model=self.model,
                    max_tokens=1000, temperature=0.8, timeout=60.0,
                    client=self._client,
                )
            except Exception as e2:
                _log_llm_failure("chat_conversation", self.model, e2, retry=True)
                return ""

    def _chat_lite(self, user_message: str = None, history: Optional[list] = None,
                   max_tokens: int = 400, stream_cb: StreamCallback = None) -> str:
        """对话降级链路：GLM-4-Flash + CHAT_PROMPT_LITE 精简回复（L5-1）。

        - 优先 GLM（ZHIPU_API_KEY）：OpenAI 兼容端点，成本低；
        - GLM key 未配置 / 调用失败 → 回退 DeepSeek（同精简 prompt，
          内容仍短，只是模型不同）；
        - 全失败 → 返回礼貌降级文案（绝不抛出打断正常对话流）。
        """
        from .prompts import CHAT_PROMPT_LITE
        messages = [{"role": "system", "content": CHAT_PROMPT_LITE}]
        if history is not None:
            messages.extend(history)
        else:
            messages.append({"role": "user", "content": user_message or ""})

        if self.glm_api_key:
            try:
                return glm_openai_completion(
                    self.glm_api_key, messages, model=GLM_DEFAULT_MODEL,
                    max_tokens=max_tokens, temperature=0.7, timeout=45.0,
                    client=self._client, stream_cb=stream_cb,
                )
            except Exception as e:
                _log_llm_failure("chat_lite", GLM_DEFAULT_MODEL, e)
        try:
            return deepseek_anthropic_completion(
                self.api_key, messages, model=self.model,
                max_tokens=max_tokens, temperature=0.7, timeout=45.0,
                client=self._client, stream_cb=stream_cb,
            )
        except Exception as e:
            _log_llm_failure("chat_lite", self.model, e, retry=True)
            return "今日额度已用尽，这里先给你简要回复：如有更多问题，可明天再来，或升级会员畅聊。"

    def analyze(
        self,
        chart_data: Union[BaziResult, str],
        references: List[ChunkResult],
        user_question: str,
        use_pro: bool = False,
        extra_system_prompt: str = None,
        stream_cb: StreamCallback = None,
    ) -> AnalysisResult:
        """命理分析 - 默认用 Flash（可靠+快速），Pro 可选。"""
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
        system_prompt = SYSTEM_PROMPT
        if extra_system_prompt:
            system_prompt = system_prompt + "\n\n" + extra_system_prompt

        # Flash-first: reliable, fast, sufficient with RAG
        # (thinking disabled 后 max_tokens 只用于正文，4000 足够完整分析)
        if not use_pro:
            return self._call_deepseek_model(
                user_message, self.model, max_tokens=4000,
                system_prompt=system_prompt,
                timeout=60.0, stream_cb=stream_cb,
            )

        # Pro only if explicitly requested
        try:
            return self._call_deepseek_model(
                user_message, self.deep_model, max_tokens=4000,
                system_prompt=system_prompt,
                timeout=90.0, stream_cb=stream_cb,
            )
        except Exception as e:
            logger.warning("Pro 模型调用失败，降级 Flash: %s", e)
            return self._call_deepseek_model(
                user_message, self.model, max_tokens=4000,
                system_prompt=system_prompt,
                timeout=30.0, stream_cb=stream_cb,
            )

    def _call_deepseek_model(self, user_message: str, model: str, max_tokens: int = 300,
                             system_prompt: str = None,
                             timeout: float = 60.0,
                             stream_cb: StreamCallback = None) -> AnalysisResult:
        """调用 DeepSeek（Anthropic 兼容端点 + thinking disabled）— 共享连接池 + 并发控制

        Bugfix: 原原生 /v1/chat/completions 下推理模型的 reasoning_content 占满
        max_tokens 导致 content 为空；切换端点后 content 稳定非空。

        stream_cb 提供时走真实流式；流式模式下不做空/短回复自动重试
        （半截内容已发出，重试会产生重复），失败时按原降级文案返回。
        """
        is_pro = (model == self.deep_model)

        # Pro model: limit concurrency to prevent server overload
        if is_pro:
            acquired = self._pro_semaphore.acquire(timeout=120)
            if not acquired:
                logger.warning("LLM 排队超时: model=%s 并发占满（semaphore 120s 未获取）", model)
                return AnalysisResult(
                    response="服务繁忙，请稍后再试。当前排队人数较多，建议1分钟后重试。",
                    tokens_used=0, model=model)

        try:
            messages = [{"role": "system", "content": system_prompt or CHAT_PROMPT}]
            messages.append({"role": "user", "content": user_message})

            def _call() -> str:
                return deepseek_anthropic_completion(
                    self.api_key, messages, model=model,
                    max_tokens=max_tokens, temperature=0.7,
                    timeout=timeout, client=self._client,
                    stream_cb=stream_cb,
                )

            content = _call()
            # Retry once if reply is empty or severely truncated (仅非流式)
            if not content or len(content) < 15:
                if stream_cb is not None:
                    logger.warning("LLM 流式空/过短回复 (%s chars): model=%s", len(content), model)
                else:
                    logger.warning("LLM 空/过短回复 (%s chars), 重试一次: model=%s", len(content), model)
                    try:
                        content = _call()
                    except Exception as e:
                        _log_llm_failure("_call_deepseek_model", model, e, retry=True)
                        content = ""
            return AnalysisResult(
                response=content or "AI 服务暂时不可用，请稍后重试。",
                tokens_used=0,
                model=model,
            )
        except Exception as e:
            _log_llm_failure("_call_deepseek_model", model, e, retry=True)
            return AnalysisResult(
                response="AI 服务暂时不可用，请稍后重试。",
                tokens_used=0, model=model)
        finally:
            if is_pro:
                self._pro_semaphore.release()

    def _format_chart(self, r: BaziResult) -> str:
        gender_line = f"\n性别：{r.gender}" if r.gender else ""
        # R1-2（T007 实锤）：五行计数不得以 Python dict 字面量形态注入
        # （{'金': 3, …}）——LLM 会逐字回显成回复文本（评测 neg "{" 契约
        # 失败）。与 format_compact_card 同口径：无括号计数字符串。
        _wuxing = r.wuxing or {}
        _wuxing_str = " ".join(f"{k}{v}" for k, v in _wuxing.items()) or "无"
        return f"""八字：{' '.join(r.bazi)}
日主：{r.day_master}
五行：{_wuxing_str}
十神：{' '.join(r.shishen)}
格局：{r.geju}
用神：{r.yongshen}{gender_line}
大运：{' → '.join(f'{age}岁{ganzhi}' for age, ganzhi in r.dayun[:5])}
神煞：{'、'.join(r.shensha) if r.shensha else '无'}"""

    def _format_references(self, refs: List[ChunkResult]) -> str:
        lines = []
        for i, ref in enumerate(refs[:15], 1):
            if isinstance(ref, str):
                # 兼容调用方传入纯文本片段（如解梦引擎的 interpretations 列表）
                lines.append(f"{i}. \"{ref[:300]}\"")
            else:
                lines.append(f"{i}. 【{ref.source}】\"{ref.text[:300]}...\" (相关度: {ref.score:.2f})")
        if not lines:
            return "（未找到直接相关古籍记载）"
        return "\n".join(lines)
