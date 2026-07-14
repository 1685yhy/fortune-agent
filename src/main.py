"""Fortune Agent - FastAPI 主入口."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

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

logger = logging.getLogger(__name__)

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
_push_task = None  # 后台推送任务


async def _daily_push_worker():
    """后台定时推送任务 - 每分钟检查一次是否到推送时间"""
    global settings, dao

    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=8)))  # 北京时间
            current_time = now.strftime("%H:%M")
            target_time = settings.push_time if settings else "08:00"

            if settings and settings.push_enabled and current_time == target_time:
                logger.info(f"触发定时推送 (北京时间 {current_time})")
                from scripts.daily_push import get_today_ganzhi, run_push_batch

                today = get_today_ganzhi()
                stats = run_push_batch(dao, today, dry_run=False)
                logger.info(
                    f"定时推送完成: 总{stats['total']}, "
                    f"成功{stats['pushed']}, 跳过{stats['skipped']}, 失败{stats['errors']}"
                )

                # 推送完成后等待60秒避免重复触发
                await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"定时推送任务异常: {e}")

        await asyncio.sleep(60)  # 每分钟检查一次


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings, engine, ziwei_engine, liuyao_engine, fengshui_engine
    global mianxiang_engine, zeri_engine, embedder, retriever, dao, llm, handler
    global _push_task

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

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
    llm = FortuneLLM(api_key=settings.claude_api_key, model="deepseek-v4-flash", deep_model="deepseek-v4-pro", provider="deepseek")
    handler = MessageHandler(
        engine, ziwei_engine, liuyao_engine, fengshui_engine,
        mianxiang_engine, zeri_engine, retriever, llm, dao,
    )

    # 启动后台推送任务
    if settings.push_enabled:
        _push_task = asyncio.create_task(_daily_push_worker())
        logger.info(f"后台推送任务已启动 (目标时间: {settings.push_time})")
    else:
        logger.info("推送功能已禁用")

    yield
    # cleanup
    if _push_task and not _push_task.done():
        _push_task.cancel()


app = FastAPI(title="Fortune Agent", version="0.1.0", lifespan=lifespan)

# OpenAI-compatible API for chatgpt-on-wechat
# Router accesses handler via global, set during lifespan
from .openai_compat import create_openai_router as _create_oai_router
app.include_router(_create_oai_router(None))

# Models
class ChatRequest(BaseModel):
    message: str = ""
    user_id: str = "default_user"
    message_type: str = "text"  # "text", "voice", "image"
    image_url: str = ""  # 图片链接（message_type=image 时）
    voice_text: str = ""  # 语音转文字结果（message_type=voice 时）


class ChatResponse(BaseModel):
    reply: str
    parts: Optional[list] = None  # 拆分后的多条消息


@app.post("/api/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    """聊天接口"""
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        if req.message_type == "voice":
            reply = handler._handle_voice(voice_text=req.voice_text)
        elif req.message_type == "image":
            reply = handler._handle_image(
                image_url=req.image_url,
                user_text=req.message,
            )
        else:
            reply = handler.process(req.message, req.user_id)

        parts = split_long_message(reply)
        return ChatResponse(reply=reply, parts=parts)
    except Exception as e:
        import traceback
        logger.error(f"处理请求失败: {traceback.format_exc()}")
        return ChatResponse(reply=f"⚠️ 处理出错：{str(e)}\n请稍后重试。")


@app.get("/api/health")
async def health():
    return {"status": "ok", "users": dao.get_user_stats() if dao else {}}


@app.get("/api/stats")
async def stats():
    if dao is None:
        return {"error": "Not ready"}
    return dao.get_user_stats()


@app.post("/api/push-daily")
async def push_daily(dry_run: bool = Query(False, description="仅测试，不写入日志")):
    """手动触发每日运势推送"""
    global settings, dao
    if settings is None or dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        from scripts.daily_push import get_today_ganzhi, run_push_batch

        today = get_today_ganzhi()
        stats = run_push_batch(dao, today, dry_run=dry_run)

        return {
            "status": "ok",
            "date": today["date"],
            "day_ganzhi": today["day_ganzhi"],
            "stats": stats,
        }
    except Exception as e:
        logger.exception("推送异常")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/push-settings/{user_id}")
async def get_push_settings(user_id: str):
    """获取用户推送设置"""
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return dao.get_user_push_settings(user_id)


@app.post("/api/push-settings/{user_id}")
async def update_push_settings(
    user_id: str,
    push_enabled: Optional[bool] = Query(None),
    push_time: Optional[str] = Query(None),
):
    """更新用户推送设置"""
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    if push_enabled is not None:
        dao.set_push_enabled(user_id, push_enabled)
    if push_time is not None:
        dao.set_push_time(user_id, push_time)

    return {"status": "ok", "user_id": user_id}



@app.get("/")
async def home():
    from fastapi.responses import HTMLResponse
    from pathlib import Path
    html = Path(__file__).parent / "index.html"
    return HTMLResponse(html.read_text(encoding="utf-8"))

def run():
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8765, reload=True)


if __name__ == "__main__":
    run()
