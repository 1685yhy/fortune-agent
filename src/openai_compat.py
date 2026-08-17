"""OpenAI-compatible API wrapper for chatgpt-on-wechat integration."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
import time
import uuid

router = APIRouter(prefix="/v1", tags=["openai-compat"])

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "fortune-agent"
    messages: List[ChatMessage]
    stream: bool = False
    user: Optional[str] = None

class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"

class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: UsageInfo

# Legacy /completions request/response
class CompletionRequest(BaseModel):
    model: str = "fortune-agent"
    prompt: str = ""
    max_tokens: int = 2000
    temperature: float = 0.7
    stream: bool = False
    user: Optional[str] = None

class CompletionChoice(BaseModel):
    index: int = 0
    text: str
    finish_reason: str = "stop"

class CompletionResponse(BaseModel):
    id: str
    object: str = "text_completion"
    created: int
    model: str
    choices: List[CompletionChoice]
    usage: UsageInfo


def _require_api_key(request):
    """API 密钥校验（X-API-Key 头或 Authorization: Bearer <key>）。

    安全修复：/v1/* 是 LLM 成本接口，必须配置并使用 API 密钥，
    未配置密钥时一律拒绝（503），防止匿名刷 LLM 成本。
    """
    from fastapi import HTTPException
    from src.security.auth import get_auth_handler

    token = request.headers.get("X-API-Key", "")
    auth_header = request.headers.get("Authorization", "")
    if not token and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="缺少 API 密钥（X-API-Key）")

    auth = get_auth_handler()
    key_info = auth.validate_api_key(token)
    if not key_info:
        raise HTTPException(status_code=403, detail="无效的 API 密钥")

    return token


def create_openai_router(_handler=None):
    """Create OpenAI-compatible routes."""

    def _get_handler():
        import src.main as main_module
        return main_module.handler

    @router.post("/chat/completions", response_model=ChatCompletionResponse, dependencies=[Depends(_require_api_key)])
    async def chat_completions(req: ChatCompletionRequest):
        handler = _get_handler()
        if handler is None:
            raise HTTPException(status_code=503, detail="Fortune Agent not ready")

        # Build conversation context
        user_message = ""
        history = []
        for msg in req.messages:
            if msg.role == "user":
                user_message = msg.content
            history.append({"role": msg.role, "content": msg.content})

        if not user_message:
            raise HTTPException(status_code=400, detail="No user message found")

        try:
            # process() handles intent detection + routing internally via AI
            # Use provided user ID, or derive a stable ID from first message content
            # to keep multi-turn sessions coherent across anonymous API users
            uid = req.user
            if not uid:
                import hashlib
                uid = "api_" + hashlib.md5(user_message.encode()[:100]).hexdigest()[:12]
            reply = handler.process(user_message, uid)
            # 兜底：出口清理 TOOL 标签残留（与其他对话出口一致）
            from src.bot.tool_calls import strip_tool_calls
            reply = strip_tool_calls(reply) or reply
        except Exception as e:
            reply = f"⚠️ 处理出错：{str(e)}"

        token_count = max(len(reply) // 2, 1)
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=req.model,
            choices=[ChatCompletionChoice(
                index=0, message=ChatMessage(role="assistant", content=reply)
            )],
            usage=UsageInfo(completion_tokens=token_count, total_tokens=token_count),
        )

    @router.post("/completions", response_model=CompletionResponse, dependencies=[Depends(_require_api_key)])
    async def completions(req: CompletionRequest):
        handler = _get_handler()
        if handler is None:
            raise HTTPException(status_code=503, detail="Not ready")
        try:
            uid = req.user
            if not uid:
                import hashlib
                uid = "api_" + hashlib.md5(req.prompt.encode()[:100]).hexdigest()[:12]
            reply = handler.process(req.prompt, uid)
            # 兜底：出口清理 TOOL 标签残留（与其他对话出口一致）
            from src.bot.tool_calls import strip_tool_calls
            reply = strip_tool_calls(reply) or reply
        except Exception as e:
            reply = f"⚠️ {e}"
        token_count = max(len(reply) // 2, 1)  # approx tokens
        return CompletionResponse(
            id=f"cmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=req.model,
            choices=[CompletionChoice(text=reply)],
            usage=UsageInfo(completion_tokens=token_count, total_tokens=token_count),
        )

    @router.get("/models")
    async def list_models():
        return {
            "object": "list",
            "data": [{"id": "fortune-agent", "object": "model", "created": int(time.time()), "owned_by": "fortune"}],
        }

    return router
