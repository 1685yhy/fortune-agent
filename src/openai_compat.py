"""OpenAI-compatible API wrapper for chatgpt-on-wechat integration."""
from fastapi import APIRouter, HTTPException
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


def create_openai_router(_handler=None):
    """Create OpenAI-compatible routes."""

    def _get_handler():
        import src.main as main_module
        return main_module.handler

    @router.post("/chat/completions", response_model=ChatCompletionResponse)
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
            reply = handler.process(user_message, req.user or "cow_user")
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

    @router.post("/completions", response_model=CompletionResponse)
    async def completions(req: CompletionRequest):
        handler = _get_handler()
        if handler is None:
            raise HTTPException(status_code=503, detail="Not ready")
        try:
            reply = handler.process(req.prompt, req.user or "cow_user")
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
