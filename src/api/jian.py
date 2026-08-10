"""晨笺订阅 API:偏好读写(开关+时间自选)+服务号绑定。全挂 require_user。"""
import logging, re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from src.storage.jian_dao import JianPrefDAO
from src.security.auth import require_user  # 与现有 API 相同鉴权

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jian", tags=["jian"])

_pref_dao: Optional[JianPrefDAO] = None

def _dao() -> JianPrefDAO:
    global _pref_dao
    if _pref_dao is None:
        from src.storage.dao import get_conn
        _pref_dao = JianPrefDAO(get_conn())
    return _pref_dao

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

@router.put("/bind")
def bind(body: BindBody, uid: str = Depends(require_user)):
    _dao().upsert_pref(uid, {"bound_status": "bound", "mp_openid": body.mp_openid})
    return {"bound": True}
