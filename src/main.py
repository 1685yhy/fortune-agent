from pathlib import Path
"""Fortune Agent - FastAPI 主入口."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .config import load_settings
from .engines.bazi import BaziEngine
from .engines.ziwei import ZiweiEngine
from .engines.liuyao import LiuyaoEngine
from .engines.fengshui import FengshuiEngine
from .engines.mianxiang import MianxiangEngine
from .engines.zeri import ZeriEngine
from .engines.dream import DreamEngine
from .rag.embedder import Embedder
from .rag.retriever import Retriever
from .llm.client import FortuneLLM
from .bot.handler import MessageHandler
from .bot.formatter import split_long_message
from .storage.dao import UserDAO
from .storage.member_dao import MemberDAO
from .storage.session_dao import SessionDAO

logger = logging.getLogger(__name__)

# 全局实例
settings = None
engine = None
ziwei_engine = None
liuyao_engine = None
fengshui_engine = None
mianxiang_engine = None
zeri_engine = None
dream_engine = None
embedder = None
retriever = None
dao = None
handler = None
llm = None
member_dao = None
session_dao = None
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
    global mianxiang_engine, zeri_engine, dream_engine, embedder, retriever, dao, llm, handler
    global _push_task, member_dao, session_dao

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
    dream_engine = DreamEngine()
    embedder = Embedder(model_name=settings.embedding_model)
    retriever = Retriever(str(settings.vectordb_dir), embedder)
    dao = UserDAO(str(settings.db_path))
    member_dao = MemberDAO(str(settings.db_path))
    session_dao = SessionDAO(str(settings.db_path))
    llm = FortuneLLM(api_key=settings.claude_api_key, model="deepseek-v4-flash", deep_model="deepseek-v4-pro", provider="deepseek")
    handler = MessageHandler(
        engine, ziwei_engine, liuyao_engine, fengshui_engine,
        mianxiang_engine, zeri_engine, retriever, llm, dao, dream_engine=dream_engine,
        session_dao=session_dao,
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
    membership: Optional[dict] = None  # 用户会员信息
    consultation_id: Optional[int] = None  # Sprint 4: 反馈用咨询ID


@app.post("/api/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    """聊天接口 - 包含会员配额检查"""
    if handler is None or member_dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # 配额检查
    if not member_dao.check_quota(req.user_id):
        membership = member_dao.get_membership(req.user_id)
        plan = membership.get("plan", "free")
        return ChatResponse(
            reply=f"⚠️ 今日查询次数已用尽。\n"
                  f"当前计划：{membership.get('plan_label', '免费版')}\n"
                  f"已用次数：{membership.get('queries_used', 0)}\n"
                  f"上限：{membership.get('queries_limit', 3)}\n\n"
                  f"💡 升级会员可获得更多查询次数："
                  f"基础版¥19.9/月(50次)，专业版¥39.9/月(150次)",
            membership=membership,
        )

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

        # 成功响应后扣减配额
        member_dao.use_quota(req.user_id)

        parts = split_long_message(reply)
        membership = member_dao.get_membership(req.user_id)

        # Sprint 4: 获取最近一次咨询ID用于反馈
        consultation_id = dao.last_consultation_id if dao else None

        return ChatResponse(
            reply=reply, parts=parts, membership=membership,
            consultation_id=consultation_id,
        )
    except Exception as e:
        import traceback
        logger.error(f"处理请求失败: {traceback.format_exc()}")
        membership = member_dao.get_membership(req.user_id)
        return ChatResponse(
            reply=f"⚠️ 处理出错：{str(e)}\n请稍后重试。",
            membership=membership,
        )


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


@app.post("/api/push-weekly")
async def push_weekly(dry_run: bool = Query(False, description="仅测试，不写入日志")):
    """手动触发每周运势总结推送"""
    global settings, dao
    if settings is None or dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        from scripts.daily_push import get_today_ganzhi, run_weekly_push_batch

        today = get_today_ganzhi()
        stats = run_weekly_push_batch(dao, today, dry_run=dry_run)

        return {
            "status": "ok",
            "date": today["date"],
            "day_ganzhi": today["day_ganzhi"],
            "stats": stats,
        }
    except Exception as e:
        logger.exception("周报推送异常")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────
# Sprint 4: History & Accuracy Dashboard
# ──────────────────────────────────────────

@app.get("/api/user/{user_id}/history")
async def get_user_history(user_id: str):
    """获取用户最近咨询历史"""
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    consultations = dao.get_user_consultations(user_id, limit=20)
    return {
        "user_id": user_id,
        "consultations": consultations,
        "total": len(consultations),
    }


@app.get("/api/user/{user_id}/accuracy")
async def get_user_accuracy(user_id: str):
    """获取用户准确率仪表盘 — 合并 consultations 表和 preference 学习数据"""
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Use the new preference dashboard if available
    if handler.preference_dao:
        return handler.preference_dao.get_accuracy_dashboard(user_id)

    # Fallback to old consultations-based accuracy
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return dao.get_user_accuracy(user_id)


@app.post("/api/feedback/{consultation_id}")
async def submit_feedback(
    consultation_id: int,
    feedback: str = Query(..., description="positive or negative"),
):
    """提交预测反馈 (👍/👎)"""
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    success = dao.save_feedback(consultation_id, feedback)
    if not success:
        raise HTTPException(status_code=400, detail="反馈值无效，请使用 positive 或 negative")
    return {"status": "ok", "consultation_id": consultation_id, "feedback": feedback}


@app.get("/api/stats/predictions")
async def get_prediction_stats():
    """获取全局预测统计"""
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return dao.get_total_predictions()


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


# ──────────────────────────────────────────
# Membership/Payment API
# ──────────────────────────────────────────

def _verify_admin(authorization: str = Header("")) -> bool:
    """Verify admin key from Authorization header."""
    expected = getattr(settings, "admin_key", "") or ""
    if not expected:
        return True  # no key configured = allow
    return authorization == f"Bearer {expected}"


@app.get("/api/membership/{user_id}")
async def get_membership(user_id: str):
    """获取用户会员信息"""
    if member_dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return member_dao.get_membership(user_id)


@app.post("/api/membership/{user_id}/upgrade")
async def upgrade_membership(user_id: str, plan: str = Query(..., description="free/basic/pro/annual")):
    """升级会员计划 (模拟支付)"""
    if member_dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    from .storage.member_dao import PLANS
    if plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"无效计划: {plan}。可选: {', '.join(PLANS.keys())}")

    plan_info = PLANS[plan]
    if plan == "free":
        # Reset to free
        member_dao.create_membership(user_id, "free")
        return {"status": "ok", "message": "已切换回免费版", "plan": plan}

    # Simulate payment
    payment_id = member_dao.create_payment(
        user_id=user_id,
        amount=plan_info["price"],
        plan=plan,
        payment_method="模拟支付",
    )
    # Auto-confirm for simulation
    member_dao.confirm_payment(payment_id, user_id, plan)

    membership = member_dao.get_membership(user_id)
    return {
        "status": "ok",
        "message": f"已升级至 {plan_info['label']}！",
        "payment_id": payment_id,
        "amount": plan_info["price"],
        "membership": membership,
    }


@app.get("/api/admin/stats")
async def admin_stats(authorization: str = Header("")):
    """管理员统计 - 需要 admin_key"""
    if not _verify_admin(authorization):
        raise HTTPException(status_code=403, detail="Forbidden: invalid admin key")
    if member_dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return member_dao.get_stats()


@app.get("/api/admin/active-members")
async def admin_active_members(authorization: str = Header("")):
    """列出所有活跃付费会员 - 需要 admin_key"""
    if not _verify_admin(authorization):
        raise HTTPException(status_code=403, detail="Forbidden: invalid admin key")
    if member_dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return {"members": member_dao.list_active_members()}


@app.get("/membership")
async def membership_page():
    """会员价格页面"""
    html = Path(__file__).parent / "membership.html"
    if html.exists():
        return HTMLResponse(html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>会员页面未找到</h1>", status_code=404)


@app.get("/")
async def home():
    from pathlib import Path
    html = Path(__file__).parent / "index.html"
    return HTMLResponse(html.read_text(encoding="utf-8"))

def run():
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8765, reload=True)


if __name__ == "__main__":
    run()
