"""OpenAI-compatible API wrapper for chatgpt-on-wechat integration.

k39 S5 身份契约（本模块唯一事实源，改前→改后见 .superpowers/sdd/task-k39-report.md）：
  1. **用户标识必须由调用方显式传**：请求体 `user` 字段（OpenAI 兼容协议的
     标准字段，两个请求模型上早已声明；CoW 侧同一命名见
     `scripts/cow_multi_user.patch` 的 `user=session.session_id`）。
  2. **未传 = 无状态问答**：不建档、不写任何用户数据、不共享任何身份，
     直接返回确定性的契约说明（见 `STATELESS_NOTICE`）。
  3. **md5 派生身份已废弃**：改前按「当轮最后一条消息」取 md5 前 12 位拼成
     `api_<hash>` 当 uid —— 同一句开场白的不同用户共用一个身份（串档面）。
  4. `/v1/*` 的 API 密钥鉴权**不放宽**（`_require_api_key`，两个 POST 路由）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
import time
import uuid

logger = logging.getLogger(__name__)

# 显式用户标识的最长长度（防御：超长标识不落库、不撑爆日志）
USER_ID_MAX_LEN = 128

# 未传 user 时的无状态应答文案（确定性、零 LLM、零落库）
STATELESS_NOTICE = (
    "本接口需要显式提供用户标识（请求体 `user` 字段）才能建立个人档案、"
    "记忆与个性化测算。本次调用未提供 `user`，已按无状态方式处理："
    "不建档、不写入任何用户数据、不与其他调用方共享身份。"
    "请补传 `user` 后重试。"
)

router = APIRouter(prefix="/v1", tags=["openai-compat"])

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "fortune-agent"
    messages: List[ChatMessage]
    stream: bool = False
    # k39 S5：**必须显式传**的用户标识；缺失 → 无状态应答（不建档、零写入）
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
    # k39 S5：同 ChatCompletionRequest.user（必须显式传；缺失 → 无状态应答）
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


def _require_api_key(request: Request):
    """API 密钥校验（X-API-Key 头或 Authorization: Bearer <key>）。

    安全修复：/v1/* 是 LLM 成本接口，必须配置并使用 API 密钥，
    未配置密钥时一律拒绝（503），防止匿名刷 LLM 成本。

    k39 S5 勘察修正：此前本函数签名写的是**未注解**的 `request`，FastAPI
    0.115 会把它当成**必填查询参数** `?request=`，于是 `/v1/*` 两个 POST
    在进依赖前就一律 422（`{"loc":["query","request"],"msg":"Field required"}`）
    ——本通道实际不可达，鉴权根本没执行过。补上 `: Request` 注解后 FastAPI
    才注入真实 Request 对象。这是**收紧**而非放宽：改前 422（谁都用不了），
    改后无 key → 401 / 错 key → 403 / 合规 key → 正常。
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


def normalize_user(user) -> str:
    """调用方显式用户标识归一（k39 S5）。

    空白 / None → `""`（= 未传 → 无状态应答）。**不做任何派生**：改前的
    「按当轮最后一条消息 md5 派生 uid」已废弃（同开场白的不同用户会共用
    一个身份 → 串档）。超长标识截断到 `USER_ID_MAX_LEN`（防御性）。
    """
    uid = (user or "").strip()
    return uid[:USER_ID_MAX_LEN]


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

        # k39 S5：身份只认调用方显式传入的 `user`（不再 md5 派生）。
        # 未传 → **无状态应答**：不进 process()、零落库、零跨用户共享。
        uid = normalize_user(req.user)
        if not uid:
            logger.warning("openai_compat: 未携带 user 标识 → 无状态应答（不建档/零写入）")
            reply = STATELESS_NOTICE
        else:
            try:
                # process() handles intent detection + routing internally via AI
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
        # k39 S5：同 /chat/completions —— 显式 user 才进个性化链，否则无状态应答
        uid = normalize_user(req.user)
        if not uid:
            logger.warning("openai_compat(/completions): 未携带 user 标识 → 无状态应答（不建档/零写入）")
            reply = STATELESS_NOTICE
        else:
            try:
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
