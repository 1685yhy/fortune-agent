"""Fortune Agent - FastAPI 主入口."""
import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Depends, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .config import is_experience_mode, load_settings
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
from .bot.handler import MessageHandler, is_question  # is_question: v1.2 建议卡片触发判定
from .bot.tool_calls import strip_tool_calls  # 兜底：回复出口强制清理 TOOL 标签残留
from .bot.formatter import split_long_message
from .storage.dao import UserDAO
from .storage.member_dao import MemberDAO
from .storage.session_dao import SessionDAO

# Step 4: Performance optimization imports
from .utils.cache import (
    ResponseCache, set_cache, get_cache,
    TTL_PRECOMPUTE_DAILY, TTL_DAILY_FORTUNE, TTL_HOURLY_FORTUNE,
    TTL_XUETANG_TOPICS, TTL_XUETANG_LESSON,
)

# Security imports
from .security.ratelimit import RateLimiter, RateLimitMiddleware
from .security.auth import AuthHandler, require_user, require_chat_user, ensure_owner, require_admin

# 日志配置（/api/health/detail 与 lifespan 共用）
from .logging_config import resolve_log_dir, resolve_log_level
from .security.sanitizer import InputSanitizer
from .security.encryption import DataEncryptor
from .security.privacy import PrivacyManager, PIPL_DISCLAIMER
from .security.audit import AuditLogger
from .security.router import router as security_router, init_security_router
from .validators.response_checker import ResponseValidator

# 单请求超时（秒）：超时返回 503 而不是挂死（审计 §5-P1 服务卡死事故）
REQUEST_TIMEOUT_SECONDS = 120.0

logger = logging.getLogger(__name__)

# 进程启动时间（/api/health/detail 展示 uptime 用）
_APP_START_TIME = time.time()

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
_precompute_task = None  # Step 4: 每日预计算任务
_jian_precompute_task = None  # Task 6: 晨笺每日内容预生成任务
_night_lamp_task = None  # Task 4: 灯语 22:30 预生成任务
_night_cleanup_task = None  # Task 5: 倾诉临时消息 24h 硬清理任务
_zeri_reminder_task = None  # Task 5(择吉日): 提醒调度任务(档1 前1天21:00 / 档2 当天7:30)

# Security globals
security_rate_limiter = None
security_auth = None
security_sanitizer = None
security_encryptor = None
security_audit = None

# Accuracy validator
_validator = ResponseValidator()


async def _daily_precompute_worker():
    """Step 4: Background task that precomputes daily content.

    Every hour, computes:
    - Daily calendar (黄历) for anonymous users (generic daily fortune)
    - Weather/season-based fortune tips
    Stores results in cache with 'date:' prefix for instant serving.
    """
    global settings

    # 天干五行映射 (inline to avoid circular import)
    STEM_WUXING = {
        "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
        "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
    }

    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            date_str = now.strftime("%Y-%m-%d")
            cache = get_cache()

            # Precompute generic daily calendar (anonymous / no-bazi fallback)
            cache_key = f"date:generic_daily:{date_str}"
            if cache.get(cache_key) is None:
                try:
                    from .engines.calendar import LuckyCalendar
                    cal = LuckyCalendar("")
                    from datetime import date as dt_date
                    # Generate basic day stem/branch for the generic calendar
                    day_stem, day_branch = cal._day_stem_branch(date_str)
                    day_ganzhi = f"{day_stem}{day_branch}"
                    day_wuxing = STEM_WUXING.get(day_stem, "")

                    generic_daily = {
                        "date": date_str,
                        "day_ganzhi": day_ganzhi,
                        "day_wuxing": day_wuxing,
                        "suitable": ["保持好心情", "与朋友交流", "适度运动"],
                        "unsuitable": ["冲动决策", "过度消费", "熬夜"],
                        "personal_advice": "今日宜保持平和心态，顺势而为。",
                        "mood_reminder": "保持好心情是最好的开运方式。",
                    }
                    cache.set(cache_key, generic_daily, ttl_seconds=TTL_PRECOMPUTE_DAILY)
                    logger.info("Precomputed generic daily calendar: %s", date_str)
                except Exception as e:
                    logger.warning("Failed to precompute daily calendar: %s", e)

            # Precompute season/weather-based fortune tip
            season_key = f"date:season_tip:{date_str}"
            if cache.get(season_key) is None:
                try:
                    month = now.month
                    if 3 <= month <= 5:
                        season = "春"
                        tip = "春属木，宜舒展身心，多接触自然绿色，助旺肝气。"
                    elif 6 <= month <= 8:
                        season = "夏"
                        tip = "夏属火，宜静心养神，午间小憩，避开酷热时段外出。"
                    elif 9 <= month <= 11:
                        season = "秋"
                        tip = "秋属金，宜收敛内省，注意呼吸系统保养，早睡早起。"
                    else:
                        season = "冬"
                        tip = "冬属水，宜温补养藏，注意保暖，晚睡早起待日光。"
                    season_tip = {
                        "season": season,
                        "month": month,
                        "tip": tip,
                        "date": date_str,
                    }
                    cache.set(season_key, season_tip, ttl_seconds=TTL_PRECOMPUTE_DAILY)
                    logger.info("Precomputed season tip: %s %s月", season, month)
                except Exception as e:
                    logger.warning("Failed to precompute season tip: %s", e)

        except Exception as e:
            logger.error("Daily precompute worker error: %s", e)

        await asyncio.sleep(3600)  # Run every hour


async def _daily_jian_precompute():
    """Task 6: 每小时检查:当日晨笺内容未生成则预生成(金句+宜忌+通用私语)。"""
    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            date_str = now.strftime("%Y-%m-%d")
            _precompute_jian_for(date_str)
        except Exception as e:
            logger.error("晨笺预生成异常: %s", e)
        await asyncio.sleep(3600)


def _precompute_jian_for(date_str: str) -> dict:
    """晨笺每日内容预生成:干支 + 宜忌 + 古籍金句 + 通用私语。

    - 缓存键 date:jian:{date_str},TTL 26 小时(覆盖跨日发送窗口);
    - 宜忌优先复用 date:generic_daily:{date_str} 预计算缓存的当日宜忌列表
      (与匿名黄历一致,见 _daily_precompute_worker);
    - LuckyCalendar 无 daily_suitable/daily_unsuitable 方法,回退固定模板列表;
    - 轻私语为发送时按用户维度生成(见 _send_jian_batch),此处只存通用兜底句。
    """
    cache = get_cache()
    key = f"date:jian:{date_str}"
    hit = cache.get(key)
    if hit:
        return hit
    from src.engines.calendar import LuckyCalendar
    cal = LuckyCalendar("")
    day_stem, day_branch = cal._day_stem_branch(date_str)
    day_ganzhi = f"{day_stem}{day_branch}"
    from src.engines.jian_quote import generate_daily_quote
    quote = generate_daily_quote(date_str, day_ganzhi) or {}
    generic = cache.get(f"date:generic_daily:{date_str}") or {}
    suitable = (
        generic.get("suitable")
        or (cal.daily_suitable(date_str) if hasattr(cal, "daily_suitable") else None)
        or ["出行", "洽谈", "早起"]
    )
    unsuitable = (
        generic.get("unsuitable")
        or (cal.daily_unsuitable(date_str) if hasattr(cal, "daily_unsuitable") else None)
        or ["借贷", "熬夜"]
    )
    content = {
        "date": date_str, "day_ganzhi": day_ganzhi,
        "suitable": suitable,
        "unsuitable": unsuitable,
        "quote": quote.get("quote", ""), "book": quote.get("book", ""),
        "generic_line": "今日诸事,宜缓不宜急。",
    }
    cache.set(key, content, ttl_seconds=3600 * 26)
    return content


async def _night_temp_cleanup():
    """Task 5: 每小时清理过期的临时倾诉消息(24h 硬清理兜底)。"""
    while True:
        try:
            from src.storage.session_dao import SessionDAO
            sdao = SessionDAO(str(load_settings().db_path))
            removed = sdao.cleanup_temp()
            if removed:
                logger.info("倾诉临时消息清理: %s 条", removed)
        except Exception as e:
            logger.warning("临时消息清理异常: %s", e)
        await asyncio.sleep(3600)


async def _night_lamp_precompute():
    """Task 4: 每小时检查,22:30-23:30 窗口预生成当日灯语(订阅用户,限速)。

    终审修复:同步阻塞(L2 摘要读取+LLM 独白+TTS 合成,单用户数秒)移出事件循环,
    经 asyncio.to_thread 委托线程池执行,避免阻塞所有请求。
    """
    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            hm = now.strftime("%H:%M")
            if "22:30" <= hm <= "23:30":
                await asyncio.to_thread(
                    _prewarm_night_lamps, now.strftime("%Y-%m-%d"))
        except Exception as e:
            logger.error("灯语预生成异常: %s", e)
        await asyncio.sleep(3600)


def _prewarm_night_lamps(date_str: str, limit: int = 50) -> dict:
    """为已开启晚安推送的订阅用户预生成灯语(会员才合成语音,防 TTS 成本滥用)。

    终审修复:不再固定取 user_id 最小的一批——按 日期序数 % 分片数 轮转
    (OFFSET),每天覆盖不同用户批次,慢速用户(如永远排后面的大 id)也有机会被生成。
    """
    import math
    from datetime import date as _date
    from src.storage.dao import get_conn
    from src.storage.jian_dao import JianPrefDAO
    from src.storage.night_dao import NightPrefDAO
    from src.storage.lamp_dao import LampDAO
    from src.storage.session_dao import SessionDAO
    from src.storage.member_dao import MemberDAO
    from src.engines.night_soliloquy import build_soliloquy, synth_lamp_audio
    conn = get_conn()
    try:
        total_row = conn.execute(
            "SELECT COUNT(*) FROM jian_prefs"
            " WHERE night_enabled=1 AND bound_status='bound'").fetchone()
    except Exception:
        total_row = None
    total = (total_row[0] or 0) if total_row else 0
    if total == 0:
        return {"total": 0, "ok": 0, "skipped": 0, "failed": 0}
    try:
        doy = _date.fromisoformat(date_str).timetuple().tm_yday
    except ValueError:
        doy = 0
    chunks = max(1, math.ceil(total / limit))
    offset = (doy % chunks) * limit
    rows = conn.execute(
        "SELECT user_id FROM jian_prefs WHERE night_enabled=1 AND bound_status='bound' "
        "ORDER BY user_id LIMIT ? OFFSET ?",
        (limit, offset)).fetchall()
    stats = {"total": len(rows), "ok": 0, "skipped": 0, "failed": 0}
    ldao, pdao = LampDAO(conn), NightPrefDAO(conn)
    sdao = SessionDAO(str(load_settings().db_path))
    mdao = MemberDAO(str(load_settings().db_path))
    for (uid,) in rows:
        try:
            if ldao.get_lamp(uid, date_str):
                stats["skipped"] += 1
                continue
            prefs = pdao.get_pref(uid) or {}
            result = build_soliloquy(uid, date_str, sdao,
                                     whisper=bool(prefs.get("whisper_enabled", 1)))
            audio = ""
            if (mdao.get_membership(uid) or {}).get("plan", "free") != "free":
                audio = synth_lamp_audio(result["text"])
            ldao.upsert_lamp(uid, date_str, result["text"], audio)
            stats["ok"] += 1
        except Exception as e:
            logger.warning("灯语预生成失败 uid=%s: %s", uid, e)
            stats["failed"] += 1
    logger.info("灯语预生成完成(%s): %s", date_str, stats)
    return stats


def _send_jian_batch(dao, now_hm: str, kind: str = "jian") -> dict:
    """按偏好时间下发:晨笺(kind=jian)或晚安(kind=night)。

    thing1..thing4 全部截断到 20 字(微信模板消息 thing 字段上限)。
    失败静默(P1): 每次发送异常 bump_fail 计数,连续≥3 次将订阅标记为
    bound_status=invalid,list_enabled_at 只放行 'bound' 故自动停止推送;
    成功 reset_fail 清零。跳过(无 openid)不触碰计数。
    """
    from src.services.wechat_mp import send_template, mp_ready, _env
    stats = {"total": 0, "pushed": 0, "skipped": 0, "errors": 0}
    if not mp_ready():
        logger.debug("服务号未配置,%s 发送跳过", "晨笺" if kind == "jian" else "晚安")
        return stats
    uids = dao.list_enabled_at(now_hm, kind)
    stats["total"] = len(uids)
    tpl_key = "MP_JIAN_TEMPLATE_ID" if kind == "jian" else "MP_NIGHT_TEMPLATE_ID"
    tpl_id = _env(tpl_key)
    for uid in uids:
        try:
            pref = dao.get_pref(uid)
            openid = (pref or {}).get("mp_openid", "")
            if not openid:
                stats["skipped"] += 1
                continue
            date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            if kind == "jian":
                content = _precompute_jian_for(date_str)
                from src.engines.jian_private import generate_private_line
                line = generate_private_line(uid)
                data = {
                    "thing1": {"value": f"{content.get('day_ganzhi', '')}日"[:20]},
                    "thing2": {"value": (
                        f"宜{','.join(content.get('suitable', [])[:3])} "
                        f"忌{','.join(content.get('unsuitable', [])[:3])}"
                    )[:20]},
                    "thing3": {"value": (content.get("quote", "") or "")[:20]},
                    "thing4": {"value": line[:20]},
                }
                url = "pages/today/today"
            else:
                # 深夜版晚安(方案·灯下漫谈):深夜陪伴第一入口。
                # 落地页带 entry=night,前端以入口为准强进深夜模式(白天点开也生效);
                # thing 字段均 ≤20 字(微信模板消息上限);按钮文案由服务号模板配置,
                # 当前以 thing4 承诺文案 + 落地页入口承接「点一盏灯,说说话」。
                night_content = _precompute_jian_for(date_str)
                data = {
                    "thing1": {"value": "明灯 · 夜话"[:20]},
                    "thing2": {"value": "夜深了,灯还亮着"[:20]},
                    "thing3": {"value": (
                        f"明日宜{','.join(night_content.get('suitable', [])[:3])}"
                        f" 忌{','.join(night_content.get('unsuitable', [])[:3])}"
                    )[:20]},
                    "thing4": {"value": "今夜说的话,天亮就忘"[:20]},
                }
                url = "pages/chat/chat?entry=night"
            send_template(openid, tpl_id, data, url=f"https://yilichat.com/{url}")
            stats["pushed"] += 1
        except Exception as e:
            logger.warning("晨笺发送失败 uid=%s: %s", uid, e)
            stats["errors"] += 1
            dao.bump_fail(uid)
            after = dao.get_pref(uid) or {}
            if (after.get("fail_count") or 0) >= 3:
                dao.upsert_pref(uid, {"bound_status": "invalid"})
                logger.warning("晨笺连续失败≥3次 uid=%s: 订阅标记失效,停止推送", uid)
        else:
            dao.reset_fail(uid)
    logger.info("晨笺批次完成(%s %s): %s", kind, now_hm, stats)
    return stats


async def _daily_push_worker():
    """后台定时推送任务 - 每分钟检查一次是否到推送时间"""
    global settings, dao
    jdao = None  # Task 6: 晨笺偏好 DAO(惰性创建,复用连接)

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

            # Task 6: 晨笺/晚安按用户偏好时间精确匹配下发(与上方全局推送相互独立)
            if settings and settings.push_enabled:
                if jdao is None:
                    from src.storage.jian_dao import JianPrefDAO
                    from src.storage.dao import get_conn
                    jdao = JianPrefDAO(get_conn())
                _send_jian_batch(jdao, current_time, "jian")
                _send_jian_batch(jdao, current_time, "night")
        except Exception as e:
            logger.error(f"定时推送任务异常: {e}")

        await asyncio.sleep(60)  # 每分钟检查一次


# ── 择吉日提醒调度(Task 5,复用晨笺服务号通道)────────────────────────
ZERI_REMIND_D1_HM = (21, 0)   # 档1: 前1天晚 21:00(北京时间)
ZERI_REMIND_D0_HM = (7, 30)   # 档2: 当天早 07:30(北京时间)
# 择吉日提醒落地 URL —— 与晨笺 _send_jian_batch 同格式(https://yilichat.com/pages/<page>[?...]):
# 小程序页面在服务号模板消息里走 h5 兜底, 晨笺写 pages/today/today、晚安写
# pages/chat/chat?entry=night, 择吉日对应指向 zeri_plan 页(id 定位到具体计划)。
ZERI_REMIND_URL = "https://yilichat.com/pages/zeri_plan/zeri_plan?id={plan_id}"


def _zeri_reminder_eligible(member_dao, uid: str) -> bool:
    """提醒仅发会员(plan != free);体验模式也发(体验期全功能)。未注入/异常按非会员。"""
    if is_experience_mode():
        return True
    if member_dao is None:
        return False
    try:
        m = member_dao.get_membership(uid) or {}
        return (m.get("plan") or "free") != "free"
    except Exception:
        return False


def _zeri_reminder_batch(zdao, now, member_dao=None) -> dict:
    """择吉日提醒下发: 档1 前1天 21:00 / 档2 当天 7:30(now 为北京时间 datetime,可注入)。

    - 只发会员(get_membership plan != free);体验模式(is_experience_mode)也发;
    - openid 复用 jian_prefs.mp_openid(同一服务号,不重复存),无 openid/未绑定 → 跳过(静默);
    - 失败链(仿 _send_jian_batch): 每次异常 bump zeri_prefs.fail_count,连续≥3 次
      将 bound_status 置 invalid 停推;成功清零;
    - 每条计划最多 2 条消息,由 remind_sent_d1/d0 两个标记天然封顶,绝不多发;
    - 终审 Fix1: 设置页开关(zeri_prefs.reminder_enabled=0)整批跳过 —— 发送路径不再零读取;
      未建 prefs 行的用户不受影响(默认开启语义保持)。本批次自身的失败/成功计数 upsert
      显式带 reminder_enabled=1,避免把"从未碰过开关"的用户误打成关闭态(否则收过一条
      提醒后即被排除)。
    """
    from src.services.wechat_mp import send_template, mp_ready, _env
    stats = {"total": 0, "pushed": 0, "skipped": 0, "errors": 0}
    if not mp_ready():
        logger.debug("服务号未配置,择吉日提醒发送跳过(休眠态)")
        return stats
    tpl_id = _env("MP_ZERI_TEMPLATE_ID")
    if not tpl_id:
        logger.debug("MP_ZERI_TEMPLATE_ID 未配置,择吉日提醒发送跳过(休眠态)")
        return stats
    now_hm = now.hour * 60 + now.minute
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    # 候选档: 到点才查(时间点用常量,测试注入 now 控制)
    candidates = []
    if now_hm >= ZERI_REMIND_D1_HM[0] * 60 + ZERI_REMIND_D1_HM[1]:
        candidates.append(("d1", "remind_sent_d1", tomorrow))
    if now_hm >= ZERI_REMIND_D0_HM[0] * 60 + ZERI_REMIND_D0_HM[1]:
        candidates.append(("d0", "remind_sent_d0", today))
    for d1_or_d0, sent_col, lucky_date in candidates:
        rows = zdao.conn.execute(
            f"SELECT id FROM zeri_plans WHERE reminder_enabled=1 AND status='active'"
            f" AND {sent_col}=0 AND lucky_date=?"
            # Fix1: 设置页开关显式关闭(zeri_prefs.reminder_enabled=0)的用户整批跳过;
            #       NOT IN 保证未建 prefs 行的用户不受影响(默认开启语义保持)
            f" AND user_id NOT IN (SELECT user_id FROM zeri_prefs WHERE reminder_enabled=0)",
            (lucky_date,)).fetchall()
        stats["total"] += len(rows)
        for (pid,) in rows:
            # fix-later: 全部预读(get_plan_by_id/get_pref/jian_prefs SELECT)移入 per-plan try 内,
            # DB 读失败只记 errors 不中断整批, 且不误计 fail_count(瞬时读失败下轮重试即可)
            uid = None
            try:
                plan = zdao.get_plan_by_id(pid)
                if not plan:
                    continue
                uid = plan["user_id"]
                # 失败停推: zeri_prefs.bound_status='invalid'(仿晨笺 invalid 语义)
                zpref = zdao.get_pref(uid) or {}
                if zpref.get("bound_status") == "invalid":
                    stats["skipped"] += 1
                    continue
                # 会员判定(体验模式全功能放行)
                if not _zeri_reminder_eligible(member_dao, uid):
                    stats["skipped"] += 1
                    continue
                # openid 复用 jian_prefs(同一服务号);无 openid/未绑定 → 静默跳过
                jrow = zdao.conn.execute(
                    "SELECT mp_openid, bound_status FROM jian_prefs WHERE user_id=?",
                    (uid,)).fetchone()
                if not jrow or not (jrow[0] or "").strip() or jrow[1] != "bound":
                    stats["skipped"] += 1
                    continue
                openid = jrow[0]
                if d1_or_d0 == "d1":
                    scene = (plan.get("scene") or "").strip()
                    pending = sum(1 for it in (plan.get("items") or [])
                                  if not it.get("done"))
                    data = {
                        "thing1": {"value": (
                            f"您选定的{scene}吉日就在明天({plan['lucky_date'][5:]})")[:20]},
                        "thing2": {"value": f"清单还差 {pending} 项未完成"[:20]},
                    }
                else:
                    jishi = ((plan.get("card") or {}).get("jishi") or "").strip()
                    data = {
                        "thing1": {"value": f"今日吉时{jishi},宜此时开工"[:20]},
                        "thing2": {"value": "清单已就绪,祝诸事顺遂"[:20]},
                    }
                url = ZERI_REMIND_URL.format(plan_id=pid)
                send_template(openid, tpl_id, data, url=url)
                # fix-later: sent 标记写入用嵌套 try —— send 已成功, 标记写入失败仅日志,
                # 不 bump fail_count、不判发送失败(消息不重发, 标记失败仅日志提请人工核查);
                # 顺带紧邻重试一次, 覆盖瞬时写失败
                try:
                    zdao.set_remind_sent(uid, pid, d1_or_d0)
                except Exception:
                    try:
                        zdao.set_remind_sent(uid, pid, d1_or_d0)
                    except Exception as e:
                        logger.warning(
                            "择吉日提醒已发送但 sent 标记写入失败(仅日志,不计数;"
                            "请人工核查防重发) uid=%s plan=%s: %s", uid, pid, e)
                # 成功 reset fail_count(写失败仅日志, 不再落入外层 except 误计)
                try:
                    zdao.upsert_pref(uid, {"fail_count": 0, "reminder_enabled": 1})
                except Exception as e:
                    logger.warning(
                        "择吉日提醒已发送但 fail_count 清零失败(仅日志) uid=%s plan=%s: %s",
                        uid, pid, e)
                stats["pushed"] += 1
            except Exception as e:
                logger.warning("择吉日提醒发送失败 uid=%s plan=%s: %s", uid, pid, e)
                stats["errors"] += 1
                if uid is None:
                    # DB 预读失败: 无法计数也不应计数(瞬时读失败, 下轮重试), 仅日志
                    continue
                try:
                    zpref2 = zdao.get_pref(uid) or {}
                    fail = (zpref2.get("fail_count") or 0) + 1
                    # Fix1: 显式保留 reminder_enabled=1 —— 本条 upsert 可能新建 prefs 行,
                    # 默认值 reminder_enabled=0 会被批次查询当作"显式关闭"整批跳过
                    zdao.upsert_pref(uid, {"fail_count": fail, "reminder_enabled": 1})
                    if fail >= 3:
                        zdao.upsert_pref(uid, {"bound_status": "invalid", "reminder_enabled": 1})
                        logger.warning("择吉日提醒连续失败≥3次 uid=%s: 订阅标记失效,停止推送", uid)
                except Exception as e2:
                    logger.warning(
                        "择吉日提醒失败计数写入异常(仅日志,不阻断本批) plan=%s: %s", pid, e2)
    if stats["total"]:
        logger.info("择吉日提醒批次完成(%s %02d:%02d): %s",
                    today, now.hour, now.minute, stats)
    return stats


async def _zeri_reminder_worker():
    """择吉日提醒 worker: 每分钟轮询(仿 _daily_push_worker)。

    档1 前1天晚 21:00 / 档2 当天早 7:30(北京时间);复用晨笺服务号通道,
    mp_ready() False 直接跳过(休眠态,同晨笺);push_enabled 关闭时与晨笺同步停。
    """
    global settings, member_dao
    zdao = None  # 择吉日 DAO(惰性创建,复用连接)

    while True:
        try:
            if settings and settings.push_enabled:
                if zdao is None:
                    from src.storage.zeri_dao import ZeriDAO
                    from src.storage.dao import get_conn
                    zdao = ZeriDAO(get_conn())
                now = datetime.now(timezone(timedelta(hours=8)))  # 北京时间
                _zeri_reminder_batch(zdao, now, member_dao)
        except Exception as e:
            logger.error("择吉日提醒任务异常: %s", e)

        await asyncio.sleep(60)  # 每分钟检查一次


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings, engine, ziwei_engine, liuyao_engine, fengshui_engine
    global mianxiang_engine, zeri_engine, dream_engine, hehun_engine, qimen_engine, xingming_engine, embedder, retriever, dao, llm, handler
    global _push_task, _precompute_task, member_dao, session_dao
    global _jian_precompute_task, _night_lamp_task, _night_cleanup_task, _zeri_reminder_task
    global security_rate_limiter, security_auth, security_sanitizer, security_encryptor, security_audit

    # 配置日志：logs/app.log 按天轮转（保留 14 天），级别取 LOG_LEVEL（默认 INFO）
    from .logging_config import setup_app_logging
    app_log_path = setup_app_logging()
    logger.info(
        "日志初始化: 级别=%s 文件=%s（按天轮转，保留 14 天）",
        resolve_log_level(), app_log_path,
    )

    settings = load_settings()

    # ── 启动配置健康检查（审计要求：密钥缺失必须 ERROR 而非 WARNING）──
    jwt_secret = os.getenv("JWT_SECRET_KEY", "").strip()
    if not jwt_secret:
        logger.error(
            "启动配置 FATAL: JWT_SECRET_KEY 未设置！JWT 使用本次进程随机密钥——"
            "重启后所有已登录用户 token 全部失效，多 worker 下各进程 token 互不认可。"
            "生产环境必须在 .env 中固定 JWT_SECRET_KEY（≥32 字节）。"
        )
    enc_key = os.getenv("ENCRYPTION_KEY", "").strip()
    admin_key = os.getenv("ADMIN_KEY", "").strip()
    logger.info(
        "启动配置: JWT_SECRET_KEY=%s ENCRYPTION_KEY=%s ADMIN_KEY=%s LOG_LEVEL=%s db=%s",
        "已固定" if jwt_secret else "未设置(随机密钥/ERROR)",
        "已配置" if enc_key else "未配置(dev 派生密钥)",
        "已配置" if admin_key else "未配置(管理员接口一律 403)",
        resolve_log_level(),
        settings.db_path,
    )

    # ── Step 4: Init Response Cache ────────────────────────────────────
    _response_cache = ResponseCache(max_size=1000)
    set_cache(_response_cache)
    logger.info("ResponseCache initialized: max_size=1000, default_ttl=3600s")

    # ── 数据库备份兜底（审计 §审计6 S1）──
    # 启动时检查：距上次备份超过 24 小时则在后台线程执行一次在线备份。
    def _startup_backup_check():
        try:
            from scripts.backup_db import ensure_recent_backup
            ensure_recent_backup(str(settings.db_path), max_age_hours=24)
        except Exception as e:
            logger.error("启动备份检查失败: %s", e)

    threading.Thread(target=_startup_backup_check, daemon=True).start()
    logger.info("启动备份检查已调度（后台线程）")

    # ── Init Security Components ───────────────────────────────────────
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

    # P2 账号注销（软删+90 天归档）：启动时清理已过保留期的注销账号
    try:
        _cancel_stats = dao.cleanup_cancelled_accounts()
        if _cancel_stats.get("removed_users"):
            logger.info("注销账号归档清理: %s", _cancel_stats)
    except Exception as e:
        logger.warning("注销账号归档清理失败: %s", e)
    llm = FortuneLLM(api_key=settings.claude_api_key, model="deepseek-v4-flash", deep_model="deepseek-v4-flash", provider="deepseek")

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

    # Phase: 双人合盘聚合 API（免费钩子 + 付费深度报告）
    from .api.union import setup as setup_union
    setup_union(hehun_engine, engine, llm=llm, retriever=retriever,
                member_dao=member_dao, dao=dao)

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

    # Phase 5b: Setup share API（分享卡 owner 校验用 DAO 引用）
    from .api.share import setup as setup_share
    setup_share(dao)

    # A 类缺口：支付 / 会员 / 订单（pay.py 需要 MemberDAO）
    from .api.pay import setup as setup_pay
    setup_pay(member_dao)

    # 微信虚拟支付（米大师）：signData/paySig/signature + 发货回调
    from .api.pay_midas import setup as setup_pay_midas
    setup_pay_midas(member_dao)

    # Task 4: 深夜陪伴 is_member 会员判定接线（同 union/jian 注入模式，生产用真实 MemberDAO）
    from .api import night as night_mod
    night_mod._member_dao = member_dao

    # 择吉日 API（zeri_dao + 会员判定 + handler 引擎引用; /api/zeri/*）
    from src.storage.zeri_dao import ZeriDAO
    from src.storage.dao import get_conn as _zeri_conn
    from .api.zeri import setup as setup_zeri
    setup_zeri(ZeriDAO(_zeri_conn()), member_dao, handler)

    # 抽灵签 API（签文库/摇签/收藏/历史; qian_saves 轻量表, 全接口 require_user）
    from src.storage.qian_dao import QianDAO
    from .api.qian import setup as setup_qian
    setup_qian(QianDAO(_zeri_conn()))

    # AI 取名 API（生成/深度报告/名笺收藏; ming_saves+ming_quota 轻量表, 全接口 require_user）
    # 免费 5 名(第4/5名只给分数) + 五维评分; 深度报告付费边界服务端强制(EXPERIENCE_MODE 全免费)
    from src.storage.ming_dao import MingDAO
    from .api.ming import setup as setup_ming
    setup_ming(llm=llm, retriever=retriever, member_dao=member_dao, dao=dao,
               bazi_engine=engine, ming_dao=MingDAO(_zeri_conn()))

    # 启动后台推送任务
    if settings.push_enabled:
        _push_task = asyncio.create_task(_daily_push_worker())
        logger.info(f"后台推送任务已启动 (目标时间: {settings.push_time})")
    else:
        logger.info("推送功能已禁用")

    # Step 4: Start daily precompute background task
    _precompute_task = asyncio.create_task(_daily_precompute_worker())
    logger.info("Daily precompute worker started (runs every 3600s)")

    # Task 6: Start daily jian (晨笺) precompute background task
    _jian_precompute_task = asyncio.create_task(_daily_jian_precompute())
    logger.info("晨笺预生成 worker 已启动 (每小时检查,缓存 date:jian:*)")

    # Task 4: 灯语 22:30 预生成 worker
    _night_lamp_task = asyncio.create_task(_night_lamp_precompute())
    logger.info("灯语预生成 worker 已启动 (22:30-23:30 预生成当日灯语)")

    # Task 5: 倾诉临时消息 24h 硬清理 worker（每小时）
    _night_cleanup_task = asyncio.create_task(_night_temp_cleanup())
    logger.info("倾诉临时消息清理 worker 已启动 (每小时)")

    # Task 5(择吉日): 提醒调度 worker（档1 前1天21:00 / 档2 当天7:30，复用晨笺服务号通道）
    _zeri_reminder_task = asyncio.create_task(_zeri_reminder_worker())
    logger.info("择吉日提醒 worker 已启动 (档1 前1天21:00 / 档2 当天7:30, 北京时区)")

    logger.info("服务启动完成: 易理明灯 fortune-agent（端口由启动命令指定，路由全量就绪）")

    yield
    # cleanup
    if _push_task and not _push_task.done():
        _push_task.cancel()
    if _precompute_task and not _precompute_task.done():
        _precompute_task.cancel()
    if _jian_precompute_task and not _jian_precompute_task.done():
        _jian_precompute_task.cancel()
    if _night_lamp_task and not _night_lamp_task.done():
        _night_lamp_task.cancel()
    if _night_cleanup_task and not _night_cleanup_task.done():
        _night_cleanup_task.cancel()
    if _zeri_reminder_task and not _zeri_reminder_task.done():
        _zeri_reminder_task.cancel()


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

# Step 4: Cache-Control headers middleware
# Sets Cache-Control on GET endpoints based on URL patterns
CACHE_CONTROL_RULES = [
    ("/api/calendar/today", "public, max-age=600"),
    ("/api/calendar/daily", "public, max-age=600"),
    ("/api/hourly-fortune", "public, max-age=600"),
    ("/api/xuetang/topics", "public, max-age=86400"),
    ("/api/xuetang/lesson", "public, max-age=3600"),
]

class _CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.method == "GET":
            for prefix, cache_control in CACHE_CONTROL_RULES:
                if request.url.path.startswith(prefix):
                    response.headers["Cache-Control"] = cache_control
                    break
        return response

app.add_middleware(_CacheControlMiddleware)


# ── 请求超时看门狗 ────────────────────────────────────────────
# 单请求超过 REQUEST_TIMEOUT_SECONDS 直接返回 503，避免请求永久挂起拖死服务。
# 注意：配合将同步重活（LLM 调用等）移入线程池使用，事件循环保持空闲，
# asyncio.wait_for 才能真正生效（审计 §5-P1：8767 全端口卡死事故的修复）。
class _TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path == "/api/chat/stream":
            # v8 阶段 3（过程体验）：SSE 流式端点不套 120s 单请求超时（长回复），
            # 由流内看门狗（chunk 间隔超时 → error 事件）负责兜底。
            return await call_next(request)
        try:
            return await asyncio.wait_for(call_next(request), timeout=REQUEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.error("Request timeout (>=%.0fs): %s %s", REQUEST_TIMEOUT_SECONDS, request.method, request.url.path)
            return JSONResponse(
                status_code=503,
                content={"detail": "请求处理超时，请稍后重试", "code": "request_timeout"},
            )


# 注册在最后 = 最外层，包裹所有其它中间件
app.add_middleware(_TimeoutMiddleware)

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

# 分享卡 PNG 静态服务（ShareCardGenerator 产物，目录存在时挂载；
# CHARTS_DIR 未配置或目录不存在则跳过，分享接口降级返回结构化 card 数据）
_CHARTS_DIR = Path(os.environ.get("CHARTS_DIR", "/opt/fortune-data/charts"))
if _CHARTS_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/share-cards", StaticFiles(directory=str(_CHARTS_DIR)), name="share-cards")
    logger.info("分享卡静态目录已挂载: %s → /share-cards", _CHARTS_DIR)

# Phase 5: User API
from .api.user import router as user_router, setup as setup_user
app.include_router(user_router)              # /api/user/*

# A 类缺口补齐（前端 api.js 调用但原 404）：
from .api.love import router as love_router   # POST /api/love/compatibility
from .api.pay import router as pay_router     # /api/pay/* + /api/user/member|orders|purchase
from .api.pay_midas import router as pay_midas_router  # 微信虚拟支付（米大师）
app.include_router(love_router)
app.include_router(pay_router)
app.include_router(pay_midas_router)

# Task 1: Hehun matching API
from .api.hehun import router as hehun_router
app.include_router(hehun_router)             # /api/hehun

# 双人合盘聚合 API
from .api.union import router as union_router
app.include_router(union_router)             # POST /api/union

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

# 晨笺订阅 API（偏好开关/时间自选 + 服务号绑定，全接口 require_user 鉴权）
from .api.jian import router as jian_router
app.include_router(jian_router)              # /api/jian/prefs|bind

# 深夜陪伴 API（偏好读写 + 深夜状态，全接口 require_user 鉴权）
from .api.night import router as night_router
app.include_router(night_router)             # /api/night/prefs|status

# 择吉日 API（选日/清单/历史/换一批/订阅，全接口 require_user + 归属校验）
from .api.zeri import router as zeri_router
app.include_router(zeri_router)              # /api/zeri/*

# 抽灵签 API（签文库/摇签/收藏/历史，全接口 require_user）
from .api.qian import router as qian_router
app.include_router(qian_router)              # /api/qian/*

# AI 取名 API（生成/深度报告/名笺收藏/额度，全接口 require_user）
from .api.ming import router as ming_router
app.include_router(ming_router)              # /api/ming/*

# ──────────────────────────────────────────
# Reports list endpoint (mini program compatibility)
# ──────────────────────────────────────────
# Bugfix: 原实现只认 user_id 查询参数（前端未传时恒为空），详情为占位实现。
# 现在从数据库 consultations 表读取真实咨询记录，按 scenario 归类生成报告，
# 并支持 Authorization Bearer JWT / user_id 查询参数两种鉴权方式。

SCENARIO_INTENTS = {
    "bazi": ("bazi", "八字命理"),
    "ziwei": ("ziwei", "紫微斗数"),
    "liuyao": ("liuyao", "六爻占卜"),
    "fengshui": ("fengshui", "风水堪舆"),
    "mianxiang": ("mianxiang", "面相手相"),
    "zeri": ("zeri", "择日吉时"),
    "hehun": ("hehun", "合婚配对"),
    "qimen": ("qimen", "奇门遁甲"),
    "xingming": ("xingming", "姓名分析"),
    "dream": ("dream", "梦境解读"),
    "advisor": ("advisor", "人生建议"),
    "calendar": ("calendar", "每日运势"),
    "hourly": ("hourly", "时辰运势"),
    "xuetang": ("xuetang", "命理学堂"),
}
# 问题关键词 → 业务场景（前端图标仅覆盖 career/love/wealth/health）
SCENARIO_KEYWORDS = [
    ("事业", "career", "事业"), ("工作", "career", "事业"), ("升职", "career", "事业"),
    ("跳槽", "career", "事业"), ("创业", "career", "事业"), ("面试", "career", "事业"),
    ("财运", "wealth", "财运"), ("投资", "wealth", "财运"), ("赚钱", "wealth", "财运"),
    ("破财", "wealth", "财运"), ("债务", "wealth", "财运"), ("生意", "wealth", "财运"),
    ("感情", "love", "感情"), ("婚姻", "love", "感情"), ("桃花", "love", "感情"),
    ("恋爱", "love", "感情"), ("正缘", "love", "感情"), ("对象", "love", "感情"),
    ("结婚", "love", "感情"), ("分手", "love", "感情"), ("复合", "love", "感情"),
    ("健康", "health", "健康"), ("病", "health", "健康"), ("身体", "health", "健康"),
    ("失眠", "health", "健康"), ("体检", "health", "健康"),
]


def _classify_scenario(question: str, intent: str) -> tuple:
    """返回 (scenario_id, scenario_label)：关键词优先，其次按 intent 映射。"""
    q = question or ""
    for kw, sid, label in SCENARIO_KEYWORDS:
        if kw in q:
            return sid, label
    intent_info = SCENARIO_INTENTS.get(intent or "")
    if intent_info:
        return intent_info
    return "bazi", "八字命理"


def _report_score(report_id: int) -> int:
    """确定性伪随机评分（60-95），同一报告每次稳定。"""
    return 60 + (report_id * 7) % 36


def _report_tags(scenario: str, scenario_label: str, intent: str) -> list:
    tags = [scenario_label]
    intent_info = SCENARIO_INTENTS.get(intent or "")
    if intent_info and intent_info[1] != scenario_label:
        tags.append(intent_info[1])
    return tags[:3]


def _build_report_item(c: dict) -> dict:
    """咨询记录 → 报告列表项 {id, date, scenario, scenarioLabel, summary, score, tags?, note?}"""
    report_id = c["id"]
    question = (c.get("question") or "").strip()
    scenario, scenario_label = _classify_scenario(question, c.get("intent", ""))
    summary = question[:60] if question else f"{scenario_label}解读"
    if len(question) > 60:
        summary += "…"
    item = {
        "id": str(report_id),
        "date": (c.get("created_at") or "")[:10],
        "scenario": scenario,
        "scenarioLabel": scenario_label,
        "summary": summary,
        "score": _report_score(report_id),
        "tags": _report_tags(scenario, scenario_label, c.get("intent", "")),
    }
    if c.get("feedback"):
        item["note"] = "已反馈" + ("👍" if c["feedback"] == "positive" else "👎")
    return item


def _derive_report_content(c: dict, item: dict) -> str:
    """分析正文为空时，由咨询记录派生出合理解读文本。"""
    question = (c.get("question") or "").strip()
    scenario_label = item["scenarioLabel"]
    topic = question[:50] if question else "综合命盘走势"
    lines = [f"【咨询问题】\n{question or '命理咨询'}"]
    lines.append(
        f"\n【{scenario_label}解读】\n基于您的提问与命盘信息综合推演（{topic}）。"
        f"整体来看，{scenario_label}相关的趋势清晰可辨，宜顺势而为、稳中求进；"
        f"重要决策前可再做一次针对性咨询，以获取更细致的推演。"
    )
    lines.append(
        "\n【行动建议】\n1. 保持理性，命理仅供决策参考；\n"
        "2. 重大事项建议结合现实情况综合判断；\n"
        "3. 可继续追问细节，获得更深入解读。"
    )
    lines.append("\n—\n以上内容由 AI 生成，仅供娱乐参考。")
    return "\n".join(lines)


_BASE_REPORT_ID = "base"  # 基础命书（用户已设置八字但尚无任何咨询报告时生成）


def _build_base_report_item(bazi_info: dict) -> dict:
    """基础命书列表项：用户设置八字后无任何咨询时兜底生成首卷。

    最小合理版：不落库、不生成文件，仅在列表/详情接口派生。
    """
    bazi = bazi_info.get("bazi", []) if isinstance(bazi_info, dict) else []
    day_master = ""
    if len(bazi) >= 3:
        day_master = bazi[2][0]  # 日柱天干
    summary = "您的个人命书（基础版）"
    if day_master:
        summary += f" · 日主{day_master}"
    return {
        "id": _BASE_REPORT_ID,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "scenario": "bazi",
        "scenarioLabel": "八字命理",
        "summary": summary,
        "score": 68,
        "tags": ["八字命理", "基础命书"],
        "note": "已保存八字，尚未咨询；此为基础命书，咨询后生成专属解读",
    }


def _derive_base_report_content(bazi_info: dict) -> str:
    """基础命书正文：基于已保存的八字信息派生的通用解读。"""
    year = bazi_info.get("year", "")
    month = bazi_info.get("month", "")
    day = bazi_info.get("day", "")
    hour = bazi_info.get("hour", "")
    gender = bazi_info.get("gender", "")
    calendar = bazi_info.get("calendar", "solar")
    bazi = bazi_info.get("bazi", []) if isinstance(bazi_info.get("bazi"), list) else []

    gender_cn = {"male": "男", "female": "女"}.get(gender, gender or "未知")
    birth_desc = f"{year}年{month}月{day}日{hour}时" if year else "未填写完整出生时间"
    cal_cn = "农历" if calendar == "lunar" else "阳历"
    bazi_str = " ".join(bazi) if bazi else "（尚未排盘）"

    lines = [
        "【基础命书】",
        f"命主信息：{cal_cn} {birth_desc}，性别 {gender_cn}",
    ]
    if bazi:
        lines.append(f"命盘八字：{bazi_str}")
        dm = bazi[2][0] if len(bazi) > 2 else ""
        if dm:
            lines.append(f"日主为「{dm}」，为全局旺衰与喜忌判断之基准。")
    else:
        lines.append("完整排盘需在对话中发起一次八字咨询（或在我的页面重新提交八字），"
                     "生成后本卷将自动升级为专属命书。")
    lines.append(
        "\n【命理建议】\n1. 命理提供参考视角，人生走向仍由自身选择与努力决定；\n"
        "2. 可进入对话发起「八字 / 紫微 / 六爻」等专项咨询，获得更细致的解读；\n"
        "3. 重大决策请结合现实情况综合判断。"
    )
    lines.append("\n—\n以上内容由 AI 生成，仅供娱乐参考。")
    return "\n".join(lines)


@app.get("/api/reports")
async def list_reports(page: int = 1, limit: int = 20, uid: str = Depends(require_user)):
    """获取用户报告列表（按咨询记录归类生成，分页）。

    安全修复（审计 E13）：JWT 必填；user_id 一律取 token sub。
    已设置八字但无任何咨询报告 → 列表兜底返回「基础命书」首卷（id=base）。
    """
    global dao
    if dao is None:
        return {"reports": [], "total": 0}
    consultations = dao.get_user_consultations(uid, limit=1000)
    reports = [_build_report_item(c) for c in consultations]
    if not reports:
        bazi_info = dao.get_user_bazi(uid)
        if bazi_info:
            reports = [_build_base_report_item(bazi_info)]
    total = len(reports)
    start = (page - 1) * limit
    page_items = reports[start:start + limit]
    return {"reports": page_items, "total": total}


@app.get("/api/reports/{report_id}")
async def get_report_detail(report_id: str, uid: str = Depends(require_user)):
    """获取单份报告详情：{report: {..., fullContent, luckyColor, luckyDirection, luckyNumber}}。

    安全修复：校验报告归属当前用户（防跨用户读报告）。
    特殊 id=base：基础命书（用户已设置八字但无咨询报告时由 /api/reports 兜底返回）。
    """
    global dao
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # 基础命书：不落库，从已保存八字派生
    if report_id == _BASE_REPORT_ID:
        bazi_info = dao.get_user_bazi(uid)
        if not bazi_info:
            raise HTTPException(status_code=404, detail="报告不存在")
        item = _build_base_report_item(bazi_info)
        item.update({
            "fullContent": _derive_base_report_content(bazi_info),
            "luckyColor": "金色",
            "luckyDirection": "东",
            "luckyNumber": "8",
        })
        return {"report": item}

    try:
        rid = int(report_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="报告不存在")
    c = dao.get_consultation(rid)
    if c is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    ensure_owner(c.get("user_id", ""), uid)
    item = _build_report_item(c)
    full_content = (c.get("analysis") or "").strip()
    if not full_content:
        full_content = _derive_report_content(c, item)
    colors = ["金色", "白色", "红色", "蓝色", "绿色"]
    directions = ["东", "南", "西", "北", "东南", "东北", "西南", "西北"]
    item.update({
        "fullContent": full_content,
        "luckyColor": colors[rid % len(colors)],
        "luckyDirection": directions[rid % len(directions)],
        "luckyNumber": str((rid % 9) + 1),
    })
    return {"report": item}

# Models
class ChatRequest(BaseModel):
    message: str = ""
    user_id: str = "default_user"  # 已废弃：安全修复后一律以 JWT sub 为准，此字段被忽略
    message_type: str = "text"  # "text", "voice", "image"
    image_url: str = ""  # 图片链接（message_type=image 时）
    voice_text: str = ""  # 语音转文字结果（message_type=voice 时）
    deep_night: bool = False  # Task 5: 深夜倾诉模式(默认临时不记录+深夜语气层)
    session_id: str = ""  # 会话隔离：新开对话 → 新 session_id（AI 上下文只取本会话）


class ChatResponse(BaseModel):
    reply: str
    parts: Optional[list] = None  # 拆分后的多条消息
    membership: Optional[dict] = None  # 用户会员信息
    consultation_id: Optional[int] = None  # Sprint 4: 反馈用咨询ID
    disclaimer: str = PIPL_DISCLAIMER  # PIPL 免责声明
    citations: Optional[list] = None  # 阶段 5：本轮引用来源 [{index,type,title,text,url?}]
    suggestions: Optional[list] = None  # v1.2 建议卡片：回复后的推荐追问（提问时生成，失败为空）


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None  # 音色（可选，默认由 TTS 服务决定）


@app.post("/api/tts")
async def api_tts(req: TTSRequest, uid: str = Depends(require_user)):
    """文字转语音 — 转发到 8768 TTS 服务并返回可播放的完整 audio_url。

    Bugfix: 8767 原无此路由（前端 404）。8768 的 POST /tts 返回
    {audio_url: "/audio/xxx.mp3", duration_ms}，其 /audio 为 StaticFiles 静态
    服务；这里把相对路径改写为完整 http://127.0.0.1:8768 前缀，开发环境
    小程序可直接播放。
    安全修复：必须登录（防刷 TTS 成本）。
    """
    import httpx
    if not (req.text or "").strip():
        raise HTTPException(status_code=400, detail="text 不能为空")
    try:
        payload = {"text": req.text}
        if req.voice:
            payload["voice"] = req.voice
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post("http://127.0.0.1:8768/tts", json=payload)
        if r.status_code != 200:
            logger.warning("TTS upstream error: %s %s", r.status_code, r.text[:200])
            raise HTTPException(status_code=502, detail=f"TTS 服务错误（{r.status_code}）")
        data = r.json()
        audio_url = data.get("audio_url", "")
        if audio_url.startswith("/"):
            # 相对路径 → 完整 URL（8768 静态挂载 /audio）
            audio_url = f"http://127.0.0.1:8768{audio_url}"
        return {
            "audio_url": audio_url,
            "duration_ms": data.get("duration_ms", 0),
        }
    except httpx.HTTPError as e:
        logger.warning("TTS upstream unavailable: %s", e)
        raise HTTPException(status_code=502, detail="TTS 服务不可用，请稍后重试")


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request = None, auth: dict = Depends(require_chat_user)) -> ChatResponse:
    """聊天接口 - 包含会员配额检查和输入过滤。

    安全修复（审计 E11）：
    - 小程序用户：user_id 一律取 JWT sub，body 里的 user_id 被忽略
      （防冒用他人身份消耗配额/写入他人名下）；
    - 外部机器人（chatgpt-on-wechat）：FORTUNE_API_KEY 认证后可自带 user_id。
    """
    if handler is None or member_dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # user_id 权威来源：JWT sub；仅 API 密钥（机器人）通道允许使用 body 的 user_id
    if auth.get("method") == "api_key":
        req.user_id = (req.user_id or "").strip() or "api_user"
    else:
        req.user_id = auth.get("user_id", "")

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

    # 配额检查（体验模式不限次）
    if not is_experience_mode() and not member_dao.check_quota(req.user_id):
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
        loop = asyncio.get_event_loop()
        if req.message_type == "voice":
            reply = await loop.run_in_executor(
                None, handler._handle_voice, req.voice_text
            )
        elif req.message_type == "image":
            reply = await loop.run_in_executor(
                None, handler._handle_image, req.image_url, req.message
            )
        else:
            # 同步 LLM 调用放线程池：事件循环不阻塞，请求超时中间件才可生效
            # 会话隔离：session_id 透传（新开对话 → 全新上下文；空/非法 → 旧行为）
            from src.api.chat_stream import normalize_session_id
            reply = await loop.run_in_executor(
                None, lambda: handler.process(
                    req.message, req.user_id, deep_night=req.deep_night,
                    session_id=normalize_session_id(req.session_id)))
        # 兜底：回复出口强制清理 TOOL 标签残留（格式变体/未知工具名/未闭合标签
        # 统一在返回前端前 strip 一次，双保险——handler 工具循环已清，这里再兜底）
        reply = strip_tool_calls(reply) or reply
        # 阶段 5：本轮引用来源（校验后），随响应返回给前端渲染角标
        citations = handler.pop_citations(req.user_id) or None

        # v1.2 建议卡片：用户提问时轻量生成 2-3 个追问（失败/超时 → 无建议，
        # 走线程池不阻塞事件循环；6s 内返回）
        suggestions: list = []
        question = (req.voice_text or "").strip() or (req.message or "").strip()
        if question and is_question(question) and reply and not reply.startswith("⚠️"):
            try:
                suggestions = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, handler.gen_suggestions, req.user_id, question, reply),
                    timeout=6.0,
                ) or []
            except Exception:
                suggestions = []

        # ── 准确率验证 ───────────────────────────────────────────
        if reply and len(reply) > 10:
            val_result = _validator.validate(reply, engine_data_used=True)
            if not val_result["passed"]:
                logger.warning(f"Accuracy issue in response: {val_result['violations']}")
            if not val_result["has_citation"] and len(reply) > 300:
                reply += "\n\n---\n📖 以上分析仅供参考，命理之说，信则有，不信则无。"

        # 成功响应后扣减配额（体验模式不扣）
        if not is_experience_mode():
            member_dao.use_quota(req.user_id)

        parts = split_long_message(reply)
        membership = member_dao.get_membership(req.user_id)

        # Sprint 4: 获取最近一次咨询ID用于反馈
        # Bugfix: last_consultation_id 是进程内最近一次保存的 ID（未保存过时为 0，
        # 且可能属于其他用户），改为按当前用户从数据库查询真实 ID；确实没保存过则
        # 返回 None（前端 `res.consultation_id || null` 可正确识别为无反馈条）。
        consultation_id = dao.get_last_consultation_id(req.user_id) if dao else None

        return ChatResponse(
            reply=reply, parts=parts, membership=membership,
            consultation_id=consultation_id,
            citations=citations,
            suggestions=suggestions,
        )
    except Exception as e:
        import traceback
        logger.error(f"处理请求失败: {traceback.format_exc()}")
        membership = member_dao.get_membership(req.user_id)
        return ChatResponse(
            reply=f"⚠️ 处理出错：{str(e)}\n请稍后重试。",
            membership=membership,
        )


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, request: Request = None, auth: dict = Depends(require_chat_user)):
    """v8 阶段 3（过程体验）：SSE 流式对话（start → thinking/tool* → chunk* → done）。

    - 与 /api/chat 共用核心 handler 逻辑（安全检测/配额/意图路由/工具循环/记忆/落库），
      仅把"一次性返回"改为"逐事件推送"（真实 LLM 流式 + 保底分句模拟）；
    - 不受 120s 单请求超时中间件限制（长回复），由流内看门狗兜底；
    - 客户端断开（停止生成）→ 生成器被关闭，executor 线程自然跑完（配额/历史不丢）。
    """
    if handler is None or member_dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    from .api.chat_stream import ChatStreamer, sse_format

    async def _sse_wrap():
        async for evt in ChatStreamer(
            handler=handler, member_dao=member_dao, dao=dao,
            sanitizer=security_sanitizer, auditor=security_audit,
            validator=_validator,
        ).events(req, request, auth):
            yield sse_format(evt)

    return StreamingResponse(
        _sse_wrap(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/health")
async def health():
    """轻量健康检查：不触发任何重活（不查 DB、不调 LLM），看门狗专用。"""
    return {
        "status": "ok",
        "service": "fortune-agent",
        "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
    }


@app.get("/api/health/detail")
async def health_detail(uid_admin: bool = Depends(require_admin)):
    """运维健康详情（管理员）: DB 连接状态 / 今日错误日志条数 / 最近备份 / 队列深度。

    鉴权：require_admin（ADMIN_KEY 未配置时返回 403，不允许空 key 放行）。
    轻量实现：不调 LLM、不加载模型，仅做一次 SQLite 连接与日志文件扫描。
    """
    import sqlite3

    if settings is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # DB 连接状态 + journal_mode
    db_status = {"ok": False, "journal_mode": None, "error": None}
    try:
        conn = sqlite3.connect(str(settings.db_path), timeout=5)
        db_status["ok"] = conn.execute("SELECT 1").fetchone()[0] == 1
        db_status["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
    except Exception as e:
        db_status["error"] = str(e)[:200]

    # 今日错误/告警条数（app.log 按天轮转，行首带日期前缀）
    errors_today = 0
    warnings_today = 0
    today_prefix = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    log_file = resolve_log_dir() / "app.log"
    try:
        for line in log_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.startswith(today_prefix):
                continue
            if "[ERROR]" in line:
                errors_today += 1
            elif "[WARNING]" in line:
                warnings_today += 1
    except OSError:
        pass

    # 最近备份时间（scripts/backup_db.py 的备份目录）
    last_backup = None
    try:
        from scripts.backup_db import latest_backup_time
        t = latest_backup_time()
        if t:
            last_backup = datetime.fromtimestamp(
                t, tz=timezone(timedelta(hours=8))
            ).isoformat(timespec="seconds")
    except Exception:
        pass

    return {
        "status": "ok",
        "service": "fortune-agent",
        "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "uptime_seconds": int(time.time() - _APP_START_TIME),
        "log": {
            "level": resolve_log_level(),
            "file": str(log_file),
            "errors_today": errors_today,
            "warnings_today": warnings_today,
        },
        "db": {"path": str(settings.db_path), **db_status},
        "last_backup": last_backup,
        "queue_depth": 0,
        "note": "单 worker 无外部任务队列，queue_depth 恒为 0",
    }


@app.get("/api/stats")
async def stats(uid: str = Depends(require_user)):
    if dao is None:
        return {"error": "Not ready"}
    return dao.get_user_stats()


# ──────────────────────────────────────────
# Privacy / Data Rights Endpoints
# ──────────────────────────────────────────

@app.get("/api/user/export/{user_id}")
async def user_data_export(user_id: str, request: Request, uid: str = Depends(require_user)):
    """Export all user data (PIPL Art. 45 data portability).

    安全修复（审计 E5）：必须登录 + owner 校验（token sub == path user_id）。
    """
    ensure_owner(user_id, uid)
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
async def user_data_deletion(user_id: str, request: Request, uid: str = Depends(require_user), confirm: bool = Query(True)):
    """Delete all user data (PIPL Art. 47 right to be forgotten).

    安全修复（审计 E6）：必须登录 + owner 校验（token sub == path user_id）。
    """
    ensure_owner(user_id, uid)
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
async def push_daily(dry_run: bool = Query(False, description="仅测试，不写入日志"), authorization: str = Header("")):
    """手动触发每日运势推送（管理员专用：Authorization: Bearer <ADMIN_KEY>）"""
    global settings, dao
    if not _verify_admin(authorization):
        raise HTTPException(status_code=403, detail="Forbidden: invalid admin key")
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
async def push_weekly(dry_run: bool = Query(False, description="仅测试，不写入日志"), authorization: str = Header("")):
    """手动触发每周运势总结推送（管理员专用）"""
    global settings, dao
    if not _verify_admin(authorization):
        raise HTTPException(status_code=403, detail="Forbidden: invalid admin key")
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
async def get_user_history(user_id: str, uid: str = Depends(require_user)):
    """获取用户最近咨询历史。

    安全修复（审计 E7）：必须登录 + owner 校验。
    """
    ensure_owner(user_id, uid)
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    consultations = dao.get_user_consultations(user_id, limit=20)
    return {
        "user_id": user_id,
        "consultations": consultations,
        "total": len(consultations),
    }


@app.get("/api/user/{user_id}/accuracy")
async def get_user_accuracy(user_id: str, uid: str = Depends(require_user)):
    """获取用户准确率仪表盘 — 合并 consultations 表和 preference 学习数据。

    安全修复（审计 E7）：必须登录 + owner 校验。
    """
    ensure_owner(user_id, uid)
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
    uid: str = Depends(require_user),
):
    """提交预测反馈 (👍/👎)。

    安全修复（审计 E12）：校验咨询记录归属当前用户，防对他人咨询刷反馈。
    """
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    c = dao.get_consultation(consultation_id)
    if c is None:
        raise HTTPException(status_code=404, detail="咨询记录不存在")
    ensure_owner(c.get("user_id", ""), uid)
    success = dao.save_feedback(consultation_id, feedback)
    if not success:
        raise HTTPException(status_code=400, detail="反馈值无效，请使用 positive 或 negative")
    return {"status": "ok", "consultation_id": consultation_id, "feedback": feedback}


class CalendarRequest(BaseModel):
    user_id: str
    date: Optional[str] = None  # YYYY-MM-DD, default today


@app.post("/api/calendar/daily")
async def get_daily_calendar(req: CalendarRequest, uid: str = Depends(require_user)):
    """AI 每日幸运日历 — 基于用户八字个性化生成。

    安全修复：user_id 一律取 JWT sub（body 的 user_id 被忽略）。
    """
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    user_id = uid

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
        loop = asyncio.get_event_loop()
        day = await loop.run_in_executor(
            None, lambda: cal.daily(saved, req.date, preferences=preferences)
        )
        return {"status": "ok", "calendar": _calendar_day_to_dict(day)}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200],
                "calendar": _generic_calendar(req.date)}


@app.post("/api/calendar/week")
async def get_week_calendar(req: CalendarRequest, uid: str = Depends(require_user)):
    """AI 7天日历预览。

    安全修复：user_id 一律取 JWT sub（body 的 user_id 被忽略）。
    """
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    user_id = uid
    saved = handler.dao.get_user_bazi(user_id) if handler.dao else None
    if not saved:
        return {"status": "no_bazi", "message": "请先设置八字信息"}

    preferences = handler._get_preference_hint(user_id) if hasattr(handler, '_get_preference_hint') else ""
    api_key = getattr(handler.llm, 'api_key', '') if handler.llm else ''

    try:
        from .engines.calendar import LuckyCalendar
        cal = LuckyCalendar(api_key)
        loop = asyncio.get_event_loop()
        days = await loop.run_in_executor(None, lambda: cal.week(saved, preferences=preferences))
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
    uid: str = Depends(require_user),
    user_id: str = Form("anonymous"),
):
    """CV 精确面相分析 — 上传自拍照片，返回精确测量 + 古籍解读。

    安全修复：必须登录；user_id 一律取 JWT sub。
    """
    user_id = uid
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
    uid: str = Depends(require_user),
    user_id: str = Form("anonymous"),
):
    """CV 手相分析 — 上传手掌照片，检测掌纹并分析。

    安全修复：必须登录；user_id 一律取 JWT sub。
    """
    user_id = uid
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
async def get_dashboard(user_id: str, uid: str = Depends(require_user)):
    """E1: 个人命理仪表盘 — 数据聚合视图。

    安全修复（审计 E10）：必须登录 + owner 校验。
    """
    ensure_owner(user_id, uid)
    if handler is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    try:
        from .api.dashboard import build_dashboard
        return build_dashboard(user_id, handler)
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


@app.get("/api/share-card/{user_id}")
async def get_share_card(user_id: str, style: str = "dark", uid: str = Depends(require_user)):
    """E2: 生成可分享的运势卡片数据。

    安全修复（审计 E10）：必须登录 + owner 校验。
    """
    ensure_owner(user_id, uid)
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
                from src.llm.client import deepseek_anthropic_completion
                prompt = f"用户八字{bazi_str}，日主{dm}。生成一句15字以内的命理金句，适合发朋友圈。风格：{'毒舌犀利' if style=='dark' else '温暖治愈' if style=='warm' else '简约大气'}。直接返回句子。"
                quote = deepseek_anthropic_completion(
                    api_key,
                    [{"role": "user", "content": prompt}],
                    model="deepseek-v4-flash",
                    max_tokens=200,
                    temperature=0.9,
                    timeout=20.0,
                )
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
async def get_prediction_stats(uid: str = Depends(require_user)):
    """获取全局预测统计。"""
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return dao.get_total_predictions()


@app.get("/api/push-settings/{user_id}")
async def get_push_settings(user_id: str, uid: str = Depends(require_user)):
    """获取用户推送设置。

    安全修复（审计 E9）：必须登录 + owner 校验。
    """
    ensure_owner(user_id, uid)
    if dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return dao.get_user_push_settings(user_id)


@app.post("/api/push-settings/{user_id}")
async def update_push_settings(
    user_id: str,
    uid: str = Depends(require_user),
    push_enabled: Optional[bool] = Query(None),
    push_time: Optional[str] = Query(None),
):
    """更新用户推送设置。

    安全修复（审计 E9）：必须登录 + owner 校验。
    """
    ensure_owner(user_id, uid)
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
    """Verify admin key from Authorization header.

    安全修复（审计 E16）：未配置 admin_key 时返回 False（拒绝），
    不再"空 key 放行"。
    """
    expected = getattr(settings, "admin_key", "") or ""
    if not expected:
        logger.warning("ADMIN_KEY 未配置，拒绝管理员请求")
        return False
    return authorization == f"Bearer {expected}"


@app.get("/api/membership/{user_id}")
async def get_membership(user_id: str, uid: str = Depends(require_user)):
    """获取用户会员信息。

    安全修复（审计 E8）：必须登录 + owner 校验。
    """
    ensure_owner(user_id, uid)
    if member_dao is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return member_dao.get_membership(user_id)


@app.post("/api/membership/{user_id}/upgrade")
async def upgrade_membership(user_id: str, plan: str = Query(..., description="free/basic/pro/annual"), uid: str = Depends(require_user)):
    """升级会员计划 (模拟支付)。

    安全修复（审计 E8）：必须登录 + owner 校验（防给别人改会员）。
    """
    ensure_owner(user_id, uid)
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
