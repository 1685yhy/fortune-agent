"""Fortune Agent - FastAPI 主入口."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Depends, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .config import load_settings
from .engines.bazi import BaziEngine
from .engines.ziwei import ZiweiEngine
from .engines.liuyao import LiuyaoEngine
from .engines.fengshui import FengshuiEngine
from .engines.mianxiang import MianxiangEngine
from .engines.zeri import ZeriEngine
from .engines.dream import DreamEngine
from .engines.hehun import HehunEngine
from .engines.qimen import QimenEngine
from .engines.xingming import XingmingEngine
from .rag.embedder import Embedder
from .rag.retriever import Retriever
from .rag.collection_manager import CollectionManager
from .llm.client import FortuneLLM
from .bot.handler import MessageHandler
from .bot.formatter import split_long_message
from .storage.dao import UserDAO
from .storage.member_dao import MemberDAO
from .storage.session_dao import SessionDAO

# Security imports
from .security.ratelimit import RateLimiter, RateLimitMiddleware
from .security.auth import AuthHandler
from .security.sanitizer import InputSanitizer
from .security.encryption import DataEncryptor
from .security.privacy import PrivacyManager, PIPL_DISCLAIMER
from .security.audit import AuditLogger
from .security.router import router as security_router, init_security_router

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
qimen_engine = None
xingming_engine = None
embedder = None
retriever = None
dao = None
handler = None
llm = None
member_dao = None
session_dao = None
_push_task = None  # 后台推送任务

# Security globals
security_rate_limiter = None
security_auth = None
security_sanitizer = None
security_encryptor = None
security_audit = None


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
    global mianxiang_engine, zeri_engine, dream_engine, hehun_engine, qimen_engine, xingming_engine, embedder, retriever, dao, llm, handler
    global _push_task, member_dao, session_dao
    global security_rate_limiter, security_auth, security_sanitizer, security_encryptor, security_audit

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    settings = load_settings()

    # ── Init Security Components ─────────────────────────────
    security_rate_limiter = RateLimiter()
    security_auth = AuthHandler()
    security_sanitizer = InputSanitizer()
    security_encryptor = DataEncryptor()
    security_audit = AuditLogger()

    # Set shared auth handler so FastAPI dependencies use consistent JWT key
    from .security.auth import set_auth_handler as _set_auth
    _set_auth(security_auth)

    db_path = str(settings.db_path)
    privacy_manager = PrivacyManager(db_path, security_encryptor)

    # Init security router with all components
    init_security_router(
        db_path=db_path,
        auth_handler=security_auth,
        privacy_manager=privacy_manager,
        audit_logger=security_audit,
        sanitizer=security_sanitizer,
        encryptor=security_encryptor,
    )

    logger.info("Security system: rate_limiter=✓ auth=✓ sanitizer=✓ encryption=✓ audit=✓ privacy=✓")

    # ── Init Engines ─────────────────────────────────────────
    engine = BaziEngine()
    ziwei_engine = ZiweiEngine()
    liuyao_engine = LiuyaoEngine()
    fengshui_engine = FengshuiEngine()
    mianxiang_engine = MianxiangEngine()
    zeri_engine = ZeriEngine()
    dream_engine = DreamEngine()
    hehun_engine = HehunEngine()
    xingming_engine = XingmingEngine()
    qimen_engine = QimenEngine()
    # 初始化 Embedder（确定性加载）
    embedder = Embedder(model_name=settings.embedding_model)
    if not embedder.load():
        logger.error(
            "FATAL: Cannot load embedding model '%s'. "
            "Service will start but RAG queries will fail.",
            settings.embedding_model,
        )
    else:
        logger.info(
            "Embedder loaded: %s (dim=%d)",
            settings.embedding_model, embedder.dimension,
        )

    # 验证 embedding 维度与配置一致
    if embedder.dimension != settings.embedding_dimension:
        logger.error(
            "FATAL: Embedding dimension mismatch! "
            "model=%d, config=%d. Update config or model.",
            embedder.dimension, settings.embedding_dimension,
        )

    # 初始化集合管理器，验证集合
    cm = CollectionManager(
        str(settings.vectordb_dir),
        settings.embedding_collection,
        settings.embedding_dimension,
    )
    validation = cm.validate()
    if not validation.exists:
        logger.warning(
            "Collection '%s' not found. RAG queries will fall back to "
            "keyword search until index is built. "
            "Run: python scripts/rebuild_index_v2.py",
            settings.embedding_collection,
        )
    elif not validation.valid:
        logger.error(
            "Collection validation FAILED: %s. "
            "RAG queries may not work correctly.",
            validation.errors,
        )
    else:
        logger.info(
            "Collection '%s' validated: %d docs",
            settings.embedding_collection, validation.doc_count,
        )

    retriever = Retriever(str(settings.vectordb_dir), embedder)
    # 设置 retriever 使用新集合
    retriever._collection_name = settings.embedding_collection
    dao = UserDAO(str(settings.db_path))
    member_dao = MemberDAO(str(settings.db_path))
    session_dao = SessionDAO(str(settings.db_path))
    llm = FortuneLLM(api_key=settings.claude_api_key, model="deepseek-v4-flash", deep_model="deepseek-v4-pro", provider="deepseek")

    # ── Init Narrative Service ──────────────────────────────────
    from .services.narrative import NarrativeService
    narrative_svc = NarrativeService(llm)
    logger.info("Narrative Service: ✅")

    handler = MessageHandler(
        engine, ziwei_engine, liuyao_engine, fengshui_engine,
        mianxiang_engine, zeri_engine, retriever, llm, dao, dream_engine=dream_engine,
        hehun_engine=hehun_engine,
        qimen_engine=qimen_engine,
        xingming_engine=xingming_engine,
        session_dao=session_dao,
    )

    # Phase 3: Setup calendar API with DAO and handler reference
    from .api.calendar import setup as setup_calendar
    setup_calendar(dao, handler)

    # Phase 4: Setup compatibility API with LLM reference
    from .api.compatibility import setup as setup_compatibility
    setup_compatibility(llm)

    # Task 1: Setup hehun API with hehun_engine + bazi_engine
    from .api.hehun import setup as setup_hehun
    setup_hehun(hehun_engine, engine, narrative_svc)

    # Task 3: Setup xingming API with xingming_engine
    from .api.xingming import setup as setup_xingming
    setup_xingming(xingming_engine, narrative_svc)

    # Task 2: Setup qimen API with qimen_engine, retriever, llm
    from .api.qimen import setup as setup_qimen
    setup_qimen(qimen_engine, retriever, llm, narrative_svc)

    # Task 4: Setup hourly fortune API
    from .api.hourly import setup as setup_hourly
    setup_hourly(dao, narrative_svc)

    # Task 5: Setup advisor API
    from .api.advisor import setup as setup_advisor
    setup_advisor(dao, llm, narrative_svc)

    # Task 6: Setup xuetang API
    from .api.xuetang import setup as setup_xuetang
    setup_xuetang(dao, retriever, narrative_svc)

    # Phase 5: Setup user API
    setup_user(dao, session_dao, security_auth)

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

from .api.pricing import router as pricing_router
from .api.scenarios import router as scenarios_router
from .api.calendar import router as calendar_router
from .api.visual_report import router as visual_report_router
from .api.compatibility import router as compatibility_router
from .api.share import router as share_router

# Security API router
app.include_router(security_router)

# Rate limiting middleware (registered after all routers)
app.add_middleware(RateLimitMiddleware, limiter=security_rate_limiter)

# ── Disclaimer Middleware ────────────────────────────────────
# Adds PIPL disclaimer header to all API responses
from starlette.middleware.base import BaseHTTPMiddleware

DISCLAIMER_HEADER = "Entertainment purposes only. Personal data protected per PIPL."

class _DisclaimerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/") and "text/html" not in response.headers.get("content-type", ""):
            response.headers["X-Disclaimer"] = DISCLAIMER_HEADER
        return response

app.add_middleware(_DisclaimerMiddleware)

# Pricing API
app.include_router(pricing_router)

# Phase 2: Scenario API
app.include_router(scenarios_router, prefix="/api")

# Phase 3: Calendar API
app.include_router(calendar_router)

# Phase 4: Social Virality
app.include_router(visual_report_router)     # /api/report, /report, /api/report/generate
app.include_router(compatibility_router)     # /api/compatibility, /compatibility
app.include_router(share_router)             # /api/share, /share

# Phase 5: User API
from .api.user import router as user_router, setup as setup_user
app.include_router(user_router)              # /api/user/*

# Task 1: Hehun matching API
from .api.hehun import router as hehun_router
app.include_router(hehun_router)             # /api/hehun

# Task 3: Xingming API
from .api.xingming import router as xingming_router
app.include_router(xingming_router)          # /api/xingming

# Task 2: Qimen API
from .api.qimen import router as qimen_router
app.include_router(qimen_router)             # /api/qimen

# Task 4: Hourly Fortune API
from .api.hourly import router as hourly_router
app.include_router(hourly_router)            # /api/hourly-fortune

# Task 5: Advisor API
from .api.advisor import router as advisor_router
app.include_router(advisor_router)           # /api/advisor

# Task 6: Xuetang API
from .api.xuetang import router as xuetang_router
app.include_router(xuetang_router)           # /api/xuetang

# Reports list endpoint (mini program compatibility)
@app.get("/api/reports")
async def list_reports(user_id: str = "", page: int = 1, limit: int = 20):
    """获取用户报告列表"""
    global dao
    if not user_id:
        return {"reports": [], "total": 0}
    consultations = dao.get_user_consultations(user_id, limit=1000) if dao else []
    total = len(consultations)
    start = (page - 1) * limit
    page_items = consultations[start:start + limit]
    reports = []
    for c in page_items:
        reports.append({
            "id": str(c["id"]),
            "title": c.get("question", "命理咨询")[:30],
            "preview": c.get("analysis_preview", ""),
            "intent": c.get("intent", ""),
            "created_at": c.get("created_at", ""),
        })
    return {"reports": reports, "total": total}

@app.get("/api/reports/{report_id}")
async def get_report_detail(report_id: str):
    """获取单份报告详情（兼容小程序）"""
    # 委托给 /api/report/{reading_id}
    return {"report": {"id": report_id, "note": "Report detail endpoint — integrate with visual_report"}}

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
    disclaimer: str = PIPL_DISCLAIMER  # PIPL 免责声明


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request = None) -> ChatResponse:
    """聊天接口 - 包含会员配额检查和输入过滤"""
    if handler is None or member_dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # ── 输入安全检测 ───────────────────────────────────────
    if security_sanitizer and req.message:
        cleaned, is_attack, attack_type = security_sanitizer.clean_and_check(req.message)
        if is_attack:
            ip = ""
            if request:
                forwarded = request.headers.get("X-Forwarded-For", "")
                ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "")
            if security_audit:
                security_audit.attack_detected(attack_type, req.user_id, ip, req.message[:80])
            return ChatResponse(
                reply="⚠️ 输入包含不安全内容，已拦截。请使用正常语言描述您的问题。",
                membership=member_dao.get_membership(req.user_id) if member_dao else None,
            )
        req.message = cleaned

    if security_sanitizer and req.voice_text:
        cleaned, is_attack, attack_type = security_sanitizer.clean_and_check(req.voice_text)
        if not is_attack:
            req.voice_text = cleaned

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


# ──────────────────────────────────────────
# Privacy / Data Rights Endpoints
# ──────────────────────────────────────────

@app.get("/api/user/export/{user_id}")
async def user_data_export(user_id: str, request: Request):
    """Export all user data (PIPL Art. 45 data portability)."""
    from .security.router import init_security_router as _init_sec
    pm = PrivacyManager(str(load_settings().db_path), DataEncryptor())
    audit = AuditLogger()
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    ua = request.headers.get("User-Agent", "")
    audit.data_export(user_id, ip, ua)
    data = pm.export_user_data(user_id)
    if data is None:
        raise HTTPException(status_code=404, detail="用户不存在或无数据")
    return {"status": "ok", "data": data, "disclaimer": PIPL_DISCLAIMER}


@app.delete("/api/user/data/{user_id}")
async def user_data_deletion(user_id: str, request: Request, confirm: bool = Query(True)):
    """Delete all user data (PIPL Art. 47 right to be forgotten)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="请确认删除操作")
    pm = PrivacyManager(str(load_settings().db_path), DataEncryptor())
    audit = AuditLogger()
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    audit.data_deletion(user_id, ip)
    deleted = pm.delete_user_data(user_id)
    logger.warning("Data deletion completed for user %s", user_id)
    return {
        "status": "ok",
        "message": "所有个人数据已删除（不可恢复）",
        "records_deleted": deleted,
        "disclaimer": PIPL_DISCLAIMER,
    }


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


class CalendarRequest(BaseModel):
    user_id: str
    date: Optional[str] = None  # YYYY-MM-DD, default today


@app.post("/api/calendar/daily")
async def get_daily_calendar(req: CalendarRequest):
    """AI 每日幸运日历 — 基于用户八字个性化生成"""
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    user_id = req.user_id

    # Get user's bazi
    saved = handler.dao.get_user_bazi(user_id) if handler.dao else None
    if not saved:
        return {
            "status": "no_bazi",
            "message": "请先设置您的八字信息，才能生成专属日历哦～",
            "calendar": _generic_calendar(req.date),
        }

    # Get user preferences
    preferences = handler._get_preference_hint(user_id) if hasattr(handler, '_get_preference_hint') else ""

    # Get API key
    api_key = getattr(handler.llm, 'api_key', '') if handler.llm else ''

    try:
        from .engines.calendar import LuckyCalendar
        cal = LuckyCalendar(api_key)
        day = cal.daily(saved, req.date, preferences=preferences)
        return {"status": "ok", "calendar": _calendar_day_to_dict(day)}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200],
                "calendar": _generic_calendar(req.date)}


@app.post("/api/calendar/week")
async def get_week_calendar(req: CalendarRequest):
    """AI 7天日历预览"""
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    user_id = req.user_id
    saved = handler.dao.get_user_bazi(user_id) if handler.dao else None
    if not saved:
        return {"status": "no_bazi", "message": "请先设置八字信息"}

    preferences = handler._get_preference_hint(user_id) if hasattr(handler, '_get_preference_hint') else ""
    api_key = getattr(handler.llm, 'api_key', '') if handler.llm else ''

    try:
        from .engines.calendar import LuckyCalendar
        cal = LuckyCalendar(api_key)
        days = cal.week(saved, preferences=preferences)
        return {"status": "ok", "calendar": [_calendar_day_to_dict(d) for d in days]}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


def _calendar_day_to_dict(day) -> dict:
    """Convert CalendarDay to JSON-serializable dict."""
    return {
        "date": day.date,
        "day_stem": day.day_stem,
        "day_branch": day.day_branch,
        "yi": day.yi,
        "ji": day.ji,
        "lucky_color": day.lucky_color,
        "lucky_direction": day.lucky_direction,
        "lucky_number": day.lucky_number,
        "overall_mood": day.overall_mood,
        "is_special": day.is_special,
        "special_note": day.special_note,
    }


def _generic_calendar(date_str: str = None) -> dict:
    """Generic calendar for users without bazi."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return {
        "date": date_str,
        "overall_mood": "保持平和心态，顺势而为",
        "yi": [{"action": "保持好心情", "time": "全天", "reason": "心态决定运势"}],
        "ji": [{"action": "冲动决策", "time": "全天", "reason": "冷静再行动"}],
        "lucky_color": "蓝色",
        "lucky_direction": "东",
        "lucky_number": "6",
        "is_special": False,
        "special_note": "",
    }


# ============================================================
# Face Reading API
# ============================================================

from fastapi import UploadFile, File, Form


@app.post("/api/face-reading")
async def face_reading(
    image: UploadFile = File(...),
    user_id: str = Form("anonymous"),
):
    """CV 精确面相分析 — 上传自拍照片，返回精确测量 + 古籍解读"""
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        import tempfile, os
        # Save uploaded file temporarily
        suffix = os.path.splitext(image.filename or "photo.jpg")[1] or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await image.read()
            tmp.write(content)
            tmp_path = tmp.name

        # Run face analysis
        from .engines.face_reader import FaceReader, generate_report

        reader = FaceReader()
        metrics = reader.analyze(tmp_path)

        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        if metrics is None:
            return {
                "status": "no_face",
                "message": "未检测到人脸，请上传一张正面自拍照片（光线充足、面部清晰）📷",
            }

        api_key = getattr(handler.llm, 'api_key', '') if handler.llm else ''
        report = generate_report(metrics, retriever=handler.retriever if hasattr(handler, 'retriever') else None,
                                 api_key=api_key)

        return {
            "status": "ok",
            "measurements": {
                "face_shape": {"type": metrics.face_shape, "confidence": metrics.face_shape_conf},
                "eye_type": {"type": metrics.eye_type, "confidence": metrics.eye_type_conf},
                "eyebrow_type": {"type": metrics.eyebrow_type, "confidence": metrics.eyebrow_type_conf},
                "nose_type": {"type": metrics.nose_type, "confidence": metrics.nose_type_conf},
                "mouth_type": {"type": metrics.mouth_type, "confidence": metrics.mouth_type_conf},
                "skin_tone": {"type": metrics.skin_tone, "confidence": metrics.skin_tone_confidence},
                "three_sections": {"upper": metrics.upper_ratio, "middle": metrics.middle_ratio, "lower": metrics.lower_ratio},
                "face_dimensions_mm": {"width": round(metrics.face_width, 1), "height": round(metrics.face_height, 1)},
                "moles": [{"region": m["region"], "size_px": m["size_px"]} for m in metrics.moles],
                "best_features": metrics.best_features,
                "improvement_areas": metrics.improvement_areas,
            },
            "report": report,
        }

    except Exception as e:
        return {"status": "error", "message": f"分析失败：{str(e)[:200]}"}


@app.post("/api/palm-reading")
async def palm_reading(
    image: UploadFile = File(...),
    user_id: str = Form("anonymous"),
):
    """CV 手相分析 — 上传手掌照片，检测掌纹并分析"""
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    try:
        import tempfile, os
        suffix = os.path.splitext(image.filename or "hand.jpg")[1] or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await image.read()
            tmp.write(content)
            tmp_path = tmp.name
        from .engines.palm_reader import PalmReader, generate_palm_report
        reader = PalmReader()
        metrics = reader.analyze(tmp_path)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        if metrics is None:
            return {"status": "no_hand", "message": "未检测到手掌，请上传一张光线充足的手掌照片 ✋"}
        api_key = getattr(handler.llm, 'api_key', '') if handler.llm else ''
        report = generate_palm_report(metrics, retriever=handler.retriever, api_key=api_key)
        return {
            "status": "ok",
            "palm_shape": metrics.palm_shape,
            "palm_color": metrics.palm_color,
            "finger_type": metrics.finger_type,
            "life_line": metrics.life_line,
            "wisdom_line": metrics.wisdom_line,
            "feeling_line": metrics.feeling_line,
            "fate_line": metrics.fate_line,
            "special_patterns": metrics.special_patterns,
            "report": report,
        }
    except Exception as e:
        return {"status": "error", "message": f"分析失败：{str(e)[:200]}"}


@app.get("/api/dashboard/{user_id}")
async def get_dashboard(user_id: str):
    """E1: 个人命理仪表盘 — 数据聚合视图"""
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    try:
        from .api.dashboard import build_dashboard
        return build_dashboard(user_id, handler)
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


@app.get("/api/share-card/{user_id}")
async def get_share_card(user_id: str, style: str = "dark"):
    """E2: 生成可分享的运势卡片数据"""
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    try:
        saved = handler.dao.get_user_bazi(user_id) if handler.dao else None
        if not saved:
            return {"status": "no_bazi", "message": "请先设置八字"}

        api_key = getattr(handler.llm, 'api_key', '') if handler.llm else ''
        bazi_str = " ".join(saved.get("bazi", ["?"])[:4])
        dm = saved.get("day_master", "?")

        # AI generates a personalized golden quote
        quote = ""
        if api_key:
            try:
                import httpx
                prompt = f"用户八字{bazi_str}，日主{dm}。生成一句15字以内的命理金句，适合发朋友圈。风格：{'毒舌犀利' if style=='dark' else '温暖治愈' if style=='warm' else '简约大气'}。直接返回句子。"
                resp = httpx.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 60, "temperature": 0.9}, timeout=15.0)
                quote = resp.json()["choices"][0]["message"]["content"].strip()
            except Exception:
                quote = f"命里有时终须有，命里无时莫强求 ✨"

        return {
            "status": "ok",
            "style": style,
            "bazi": bazi_str,
            "day_master": dm,
            "quote": quote,
            "share_text": f"🔮 我的八字：{bazi_str} | 日主：{dm}\n\n{quote}\n\n—— 来自「易理明灯」AI命理助手",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


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


@app.get("/pricing")
async def pricing_page():
    """透明定价页面"""
    html = Path(__file__).parent / "static" / "pricing.html"
    if html.exists():
        return HTMLResponse(html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>定价页面未找到</h1>", status_code=404)


@app.get("/scenarios")
async def scenarios_page():
    """Phase 2: Scenario picker page"""
    html = Path(__file__).parent / "static" / "scenarios.html"
    if html.exists():
        return HTMLResponse(html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>场景页面未找到</h1>", status_code=404)


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
