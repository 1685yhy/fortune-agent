"""晨笺订阅 API:偏好读写(开关+时间自选)+服务号绑定。全挂 require_user。"""
import logging, re, threading
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from src.storage.jian_dao import JianPrefDAO
from src.security.auth import require_user  # 与现有 API 相同鉴权
from src.security.ratelimit import SlidingWindowCounter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jian", tags=["jian"])

_pref_dao: Optional[JianPrefDAO] = None

def _dao() -> JianPrefDAO:
    global _pref_dao
    if _pref_dao is None:
        from src.storage.dao import get_conn
        _pref_dao = JianPrefDAO(get_conn())
    return _pref_dao

# bind 端点按用户限流:每用户 5 次/分钟(缓解"信任调用方 openid"的滥用面)。
# 复用 src.security.ratelimit.SlidingWindowCounter(与全局 RateLimiter 同款实现);
# 全局 RateLimiter.check_user 是固定 100 次/小时 的整站上限,无按端点窗口参数,
# 故此处独立维护 per-user 计数器。锁保护 dict 与计数器的并发访问。
_BIND_LIMIT = (5, 60)  # (max_requests, window_seconds)
_bind_limiters = defaultdict(lambda: SlidingWindowCounter(*_BIND_LIMIT))
_bind_lock = threading.Lock()

def _bind_allowed(uid: str) -> tuple:
    """绑定限流判定。返回 (allowed, retry_after_seconds);超限返回 (False, n)。"""
    with _bind_lock:
        return _bind_limiters[uid].allow()

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

class PrefUpdate(BaseModel):
    jian_enabled: Optional[bool] = None
    jian_time: Optional[str] = None
    night_enabled: Optional[bool] = None
    night_time: Optional[str] = None

class BindBody(BaseModel):
    mp_openid: str

@router.get("/prefs")
def get_prefs(uid: str = Depends(require_user)):
    prefs = _dao().get_pref(uid) or {"user_id": uid, "jian_enabled": 0, "jian_time": "07:30",
                                     "night_enabled": 0, "night_time": "23:00", "bound_status": "unbound"}
    return {"prefs": prefs}

@router.put("/prefs")
def put_prefs(body: PrefUpdate, uid: str = Depends(require_user)):
    patch = {}
    for k in ("jian_time", "night_time"):
        v = getattr(body, k)
        if v is not None:
            if not _TIME_RE.match(v):
                raise HTTPException(status_code=400, detail=f"非法时间格式: {v}")
            patch[k] = v
    if body.jian_enabled is not None:
        patch["jian_enabled"] = 1 if body.jian_enabled else 0
    if body.night_enabled is not None:
        patch["night_enabled"] = 1 if body.night_enabled else 0
    _dao().upsert_pref(uid, patch)
    return {"prefs": _dao().get_pref(uid)}

# TODO(上线前): 绑定应走服务号 unionid 交换校验(当前信任调用方 openid,已限流缓解)
@router.put("/bind")
def bind(body: BindBody, uid: str = Depends(require_user)):
    allowed, retry_after = _bind_allowed(uid)
    if not allowed:
        raise HTTPException(status_code=429, detail="绑定过于频繁,请稍后再试",
                            headers={"Retry-After": str(retry_after)})
    _dao().upsert_pref(uid, {"bound_status": "bound", "mp_openid": body.mp_openid})
    return {"bound": True}

# 今日页晨笺卡:干支+宜忌+金句(消费 Task 6 预生成缓存)+按用户私语+固定小问。
# _precompute_jian_for 来自 src.main,模块内延迟导入避免循环 import(与预生成 worker 同函数)。
@router.get("/today")
def today_jian(uid: str = Depends(require_user)):
    from datetime import datetime, timezone, timedelta
    from src.main import _precompute_jian_for
    date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    content = _precompute_jian_for(date_str)
    from src.engines.jian_private import generate_private_line
    line = generate_private_line(uid)
    return {
        "date": content["date"], "day_ganzhi": content["day_ganzhi"],
        "suitable": content["suitable"], "unsuitable": content["unsuitable"],
        "quote": content.get("quote", ""), "book": content.get("book", ""),
        "private_line": line,
        "question": "今天最想做成的一件事是什么?",
    }
