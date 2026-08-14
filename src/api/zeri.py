"""择吉日 API:选日落库/清单勾选备注/历史/换一批/提醒订阅。全挂 require_user + 归属校验。

- 免费档: 历史近 3 条 + more_requires_member;换一批每日 3 次(zeri_quota);会员/体验模式不限
- plan_type 由服务端按会员档重算(客户端传值仅作兜底参考,防止乱标权益)
- 归属校验红线: 非本人访问他人计划一律 403(不存在 404)
- 绑定态复用 jian_prefs(同一服务号 openid 不重复存);提醒为主动同意制
"""
import logging
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.config import is_experience_mode
from src.security.auth import require_user
from src.storage.zeri_dao import ZeriDAO

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/zeri", tags=["zeri"])

# 免费档每日换一批上限(zeri_quota.refresh_count)
FREE_REFRESH_LIMIT = 3

_dao: Optional[ZeriDAO] = None
_member_dao = None    # MemberDAO 引用,由 lifespan 注入(测试注入 stub)
_handler = None       # 对话 handler 引用,提供 zeri_engine + 八字映射(测试注入 stub)


def setup(dao, member_dao=None, handler=None):
    """在主应用生命周期中注入依赖(仿 union.py setup 模式)。"""
    global _dao, _member_dao, _handler
    _dao = dao
    _member_dao = member_dao
    _handler = handler


def _zdao() -> ZeriDAO:
    global _dao
    if _dao is None:
        from src.storage.dao import get_conn
        _dao = ZeriDAO(get_conn())
    return _dao


def is_member(uid: str) -> bool:
    """会员判定:plan != free 即会员;未注入/异常一律按非会员处理(仿 night.py)。"""
    if _member_dao is None:
        return False
    try:
        m = _member_dao.get_membership(uid) or {}
        return (m.get("plan") or "free") != "free"
    except Exception:
        return False


def _owned_plan(uid: str, plan_id: int) -> dict:
    """归属校验红线:不存在 404;非本人 403。"""
    plan = _zdao().get_plan_by_id(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    if plan["user_id"] != uid:
        raise HTTPException(status_code=403, detail="无权访问他人计划")
    return plan


def _bj_today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _bound_status(uid: str) -> str:
    return _zdao().get_jian_bound_status(uid) or "unbound"


# ─────────────────────────── 请求模型 ───────────────────────────

class SelectBody(BaseModel):
    scene: str
    lucky_date: str                      # ISO YYYY-MM-DD
    card: dict                           # 吉日卡完整数据
    items: list = []                     # 办事清单 items
    plan_type: str = "free"              # 仅参考,服务端按会员档重算兜底


class ItemUpdateBody(BaseModel):
    idx: int
    done: Optional[bool] = None
    note: Optional[str] = None


class ReminderBody(BaseModel):
    enabled: bool


class PrefUpdate(BaseModel):
    reminder_enabled: Optional[bool] = None


class RefreshBody(BaseModel):
    scene: str
    start: str                           # ISO YYYY-MM-DD
    end: str                             # ISO YYYY-MM-DD
    exclude_dates: Optional[list] = None # 换一批去重: 已展示过的日期


# ─────────────────────────── 选日/清单/历史 ───────────────────────────

@router.post("/select")
def select_plan(body: SelectBody, uid: str = Depends(require_user)):
    """对话/前端选定吉日后落库。服务端按会员档重算 plan_type 兜底,返回 plan_id。"""
    scene = body.scene.strip()
    if not scene:
        raise HTTPException(status_code=400, detail="场景不能为空")
    try:
        datetime.strptime(body.lucky_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"非法日期格式: {body.lucky_date}")
    plan_type = "member" if is_member(uid) else "free"
    pid = _zdao().upsert_plan(uid, scene, body.lucky_date,
                              body.card, body.items, plan_type)
    return {"plan_id": pid, "plan_type": plan_type}


@router.get("/plans")
def list_plans(uid: str = Depends(require_user)):
    """历史列表: 免费近 3 条 + more_requires_member=true;会员全量。"""
    dao = _zdao()
    member = is_member(uid)
    if member:
        plans = dao.list_plans(uid)
        more = False
        limit = None
    else:
        limit = 3
        plans = dao.list_plans(uid, limit=limit)
        more = dao.count_plans(uid) > len(plans)
    return {"plans": plans, "more_requires_member": more, "limit": limit}


@router.get("/plans/{plan_id}")
def plan_detail(plan_id: int, uid: str = Depends(require_user)):
    """计划详情(卡完整数据 + 清单)。非本人 403。"""
    return {"plan": _owned_plan(uid, plan_id)}


@router.put("/plans/{plan_id}/item")
def update_item(plan_id: int, body: ItemUpdateBody, uid: str = Depends(require_user)):
    """清单项勾选/备注持久化。"""
    plan = _owned_plan(uid, plan_id)
    items = plan.get("items") or []
    if body.idx < 0 or body.idx >= len(items):
        raise HTTPException(status_code=400, detail=f"清单项索引越界: {body.idx}")
    if body.done is None and body.note is None:
        raise HTTPException(status_code=400, detail="done 与 note 至少提供其一")
    patch = {}
    if body.done is not None:
        patch["done"] = 1 if body.done else 0
    if body.note is not None:
        patch["note"] = body.note
    updated = _zdao().update_item(plan_id, body.idx, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    return {"items": updated}


@router.put("/plans/{plan_id}/reminder")
def set_reminder(plan_id: int, body: ReminderBody, uid: str = Depends(require_user)):
    """提醒订阅开关(主动同意制: enabled=true 即用户同意,落库排期)。"""
    _owned_plan(uid, plan_id)
    ok = _zdao().set_reminder(uid, plan_id, 1 if body.enabled else 0)
    if not ok:
        raise HTTPException(status_code=404, detail="计划不存在")
    return {"enabled": body.enabled, "plan_id": plan_id}


# ─────────────────────────── 换一批 ───────────────────────────

@router.post("/refresh")
def refresh_cards(body: RefreshBody, uid: str = Depends(require_user)):
    """换一批: 重新选 3 卡(zeri_quota 每日 3 次,会员/体验模式不限;超限 429)。

    额度判定先于引擎调用(超限不调引擎),引擎成功后才计数——引擎异常不占额度
    (与对话 handler 扣额度语义一致)。计数后再次校验 bump 返回的 allowed:并发
    下两个请求同时过预检时,串行 bump 的第二个以 429 拒绝(权威结果为准)。
    """
    dao = _zdao()
    unlimited = is_member(uid) or is_experience_mode()
    today = _bj_today()
    if not unlimited:
        if dao.get_refresh_count(uid, today) >= FREE_REFRESH_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"今天的换一批次数已用完({FREE_REFRESH_LIMIT} 次),明日再来或升级会员")
    if _handler is None or getattr(_handler, "zeri_engine", None) is None:
        raise HTTPException(status_code=503, detail="择日服务未就绪")
    user_bazi = None
    try:
        user_bazi = _handler._map_user_bazi_for_zeri(uid)
    except Exception:
        user_bazi = None
    try:
        res = _handler.zeri_engine.select_lucky_days(
            scene=body.scene.strip(), start_date=body.start, end_date=body.end,
            user_bazi=user_bazi, exclude_dates=body.exclude_dates or None,
            prefer_weekend=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("换一批引擎执行失败 uid=%s scene=%s", uid, body.scene)
        raise HTTPException(status_code=500, detail=f"择日引擎执行失败: {str(e)[:100]}")
    cards = [asdict(c) if hasattr(c, "__dataclass_fields__") else c
             for c in (res.get("cards") or [])]
    if not unlimited:
        allowed, count = dao.bump_refresh(uid, today, limit=FREE_REFRESH_LIMIT)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"今天的换一批次数已用完({FREE_REFRESH_LIMIT} 次),明日再来或升级会员")
        remaining = max(0, FREE_REFRESH_LIMIT - count)
    else:
        dao.bump_refresh(uid, today)  # 会员/体验模式: 计数仅作统计,不限
        remaining = None
    return {"cards": cards,
            "scanned": res.get("scanned", 0),
            "suggest_wider": bool(res.get("suggest_wider")),
            "reason": res.get("reason"),
            "refresh_remaining": remaining}


# ─────────────────────────── 偏好 ───────────────────────────

@router.get("/prefs")
def get_prefs(uid: str = Depends(require_user)):
    """提醒开关 + 绑定态(绑定态以 jian_prefs 为准,openid 不下发)。"""
    p = _zdao().get_pref(uid) or {}
    return {"prefs": {"user_id": uid,
                      "reminder_enabled": p.get("reminder_enabled", 0),
                      "bound_status": _bound_status(uid)}}


@router.put("/prefs")
def put_prefs(body: PrefUpdate, uid: str = Depends(require_user)):
    patch = {}
    if body.reminder_enabled is not None:
        patch["reminder_enabled"] = 1 if body.reminder_enabled else 0
    if not patch:
        raise HTTPException(status_code=400, detail="无有效字段")
    _zdao().upsert_pref(uid, patch)
    p = _zdao().get_pref(uid) or {}
    return {"prefs": {"user_id": uid,
                      "reminder_enabled": p.get("reminder_enabled", 0),
                      "bound_status": _bound_status(uid)}}
