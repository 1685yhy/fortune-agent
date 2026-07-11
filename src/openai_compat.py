"""OpenAI-compatible API wrapper for chatgpt-on-wechat integration.

CoW speaks OpenAI API format. This wraps our /api/chat endpoint.
"""

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


def create_openai_router(_handler=None):
    """Create OpenAI-compatible routes. Handler accessed via main module global."""

    @router.post("/chat/completions", response_model=ChatCompletionResponse)
    async def chat_completions(req: ChatCompletionRequest):
        # Access handler from main module's global
        import src.main as main_module
        handler = main_module.handler

        if handler is None:
            raise HTTPException(status_code=503, detail="Fortune Agent not ready")

        # Extract the last user message
        user_message = ""
        for msg in req.messages:
            if msg.role == "user":
                user_message = msg.content

        if not user_message:
            raise HTTPException(status_code=400, detail="No user message found")

        user_id = req.user or "cow_user"

        try:
            reply = handler.process(user_message, user_id)
        except Exception as e:
            reply = f"⚠️ 处理出错：{str(e)}"

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=req.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=reply),
                )
            ],
            usage=UsageInfo(),
        )

    @router.get("/models")
    async def list_models():
        return {
            "object": "list",
            "data": [
                {"id": "fortune-agent", "object": "model", "created": int(time.time()), "owned_by": "fortune"}
            ],
        }

    return router
