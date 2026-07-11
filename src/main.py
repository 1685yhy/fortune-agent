"""Fortune Agent - FastAPI 主入口."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

from .config import load_settings
from .engines.bazi import BaziEngine
from .engines.ziwei import ZiweiEngine
from .engines.liuyao import LiuyaoEngine
from .engines.fengshui import FengshuiEngine
from .engines.mianxiang import MianxiangEngine
from .engines.zeri import ZeriEngine
from .rag.embedder import Embedder
from .rag.retriever import Retriever
from .llm.client import FortuneLLM
from .bot.handler import MessageHandler
from .bot.formatter import split_long_message
from .storage.dao import UserDAO

# 全局实例
settings = None
engine = None
ziwei_engine = None
liuyao_engine = None
fengshui_engine = None
mianxiang_engine = None
zeri_engine = None
embedder = None
retriever = None
dao = None
handler = None
llm = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings, engine, ziwei_engine, liuyao_engine, fengshui_engine
    global mianxiang_engine, zeri_engine, embedder, retriever, dao, llm, handler
    settings = load_settings()
    engine = BaziEngine()
    ziwei_engine = ZiweiEngine()
    liuyao_engine = LiuyaoEngine()
    fengshui_engine = FengshuiEngine()
    mianxiang_engine = MianxiangEngine()
    zeri_engine = ZeriEngine()
    embedder = Embedder(model_name=settings.embedding_model)
    retriever = Retriever(str(settings.vectordb_dir), embedder)
    dao = UserDAO(str(settings.db_path))
    llm = FortuneLLM(api_key=settings.claude_api_key, model=settings.claude_model)
    handler = MessageHandler(
        engine, ziwei_engine, liuyao_engine, fengshui_engine,
        mianxiang_engine, zeri_engine, retriever, llm, dao,
    )
    yield
    # cleanup


app = FastAPI(title="Fortune Agent", version="0.1.0", lifespan=lifespan)

# Models
class ChatRequest(BaseModel):
    message: str
    user_id: str = "default_user"


class ChatResponse(BaseModel):
    reply: str
    parts: Optional[list] = None  # 拆分后的多条消息


@app.post("/api/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    """聊天接口"""
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        reply = handler.process(req.message, req.user_id)
        parts = split_long_message(reply)
        return ChatResponse(reply=reply, parts=parts)
    except Exception as e:
        return ChatResponse(reply=f"⚠️ 处理出错：{str(e)}\n请稍后重试。")


@app.get("/api/health")
async def health():
    return {"status": "ok", "users": dao.get_user_stats() if dao else {}}


@app.get("/api/stats")
async def stats():
    if dao is None:
        return {"error": "Not ready"}
    return dao.get_user_stats()


def run():
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8765, reload=True)


if __name__ == "__main__":
    run()
