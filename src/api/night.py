"""深夜陪伴 API(方案·灯下漫谈):偏好读写 + 深夜状态。全挂 require_user。
灯语(lamp/*)与倾诉单轮落库(remember)在 Task 4/6 追加到本模块。"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from src.storage.night_dao import NightPrefDAO
from src.security.auth import require_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/night", tags=["night"])

_pref_dao: Optional[NightPrefDAO] = None

def _pdao() -> NightPrefDAO:
    global _pref_dao
    if _pref_dao is None:
        from src.storage.dao import get_conn
        _pref_dao = NightPrefDAO(get_conn())
    return _pref_dao

_PRESET_SET = {"early", "standard", "night"}
_TIMER_SET = {5, 10, 15, 30}

class NightPrefUpdate(BaseModel):
    preset: Optional[str] = None
    effect_enabled: Optional[bool] = None
    keep_enabled: Optional[bool] = None
    lamp_timer_min: Optional[int] = None
    whisper_enabled: Optional[bool] = None

def _bj_today() -> str:
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

@router.get("/prefs")
def get_prefs(uid: str = Depends(require_user)):
    prefs = _pdao().get_pref(uid) or {"user_id": uid, "preset": "standard",
                                      "effect_enabled": 1, "keep_enabled": 1,
                                      "lamp_timer_min": 15, "whisper_enabled": 1}
    return {"prefs": prefs}

@router.put("/prefs")
def put_prefs(body: NightPrefUpdate, uid: str = Depends(require_user)):
    patch = {}
    if body.preset is not None:
        if body.preset not in _PRESET_SET:
            raise HTTPException(status_code=400, detail=f"非法时段档位: {body.preset}")
        patch["preset"] = body.preset
    if body.lamp_timer_min is not None:
        if body.lamp_timer_min not in _TIMER_SET:
            raise HTTPException(status_code=400, detail="定时关闭仅支持 5/10/15/30 分钟")
        patch["lamp_timer_min"] = body.lamp_timer_min
    for k in ("effect_enabled", "keep_enabled", "whisper_enabled"):
        v = getattr(body, k)
        if v is not None:
            patch[k] = 1 if v else 0
    _pdao().upsert_pref(uid, patch)
    return {"prefs": _pdao().get_pref(uid)}

@router.get("/status")
def night_status(uid: str = Depends(require_user)):
    from src.engines.night_mode import is_night_mode, night_window
    prefs = _pdao().get_pref(uid) or {}
    preset = prefs.get("preset", "standard")
    start, end = night_window(preset)
    return {"night_mode": is_night_mode(preset=preset),
            "preset": preset,
            "window": f"{start:02d}:00-{end:02d}:00"}
