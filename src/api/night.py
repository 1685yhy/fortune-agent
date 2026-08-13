"""深夜陪伴 API(方案·灯下漫谈):偏好读写 + 深夜状态。全挂 require_user。
灯语(lamp/*)与倾诉单轮落库(remember)在 Task 4/6 追加到本模块。"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from src.storage.night_dao import NightPrefDAO
from src.storage.lamp_dao import LampDAO
from src.security.auth import require_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/night", tags=["night"])

_pref_dao: Optional[NightPrefDAO] = None
_lamp_dao: Optional[LampDAO] = None
_member_dao: Optional[object] = None  # 会员判定(语音版权益),由 main lifespan 注入

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


# ── 灯语库 API(方案·灯下漫谈):今日灯语 / 收藏 / 历史 ────────────────

def _ldao() -> LampDAO:
    global _lamp_dao
    if _lamp_dao is None:
        from src.storage.dao import get_conn
        _lamp_dao = LampDAO(get_conn())
    return _lamp_dao


def is_member(uid: str) -> bool:
    """会员判定(语音版权益):plan != free 即会员;未注入/异常一律按非会员处理。"""
    if _member_dao is None:
        return False
    try:
        m = _member_dao.get_membership(uid) or {}
        return (m.get("plan") or "free") != "free"
    except Exception:
        return False


@router.get("/lamp/today")
def lamp_today(uid: str = Depends(require_user)):
    """今日枕边灯语:生成后入库缓存,当日只生成一次;语音版仅会员(免费仅文字)。"""
    date_str = _bj_today()
    dao = _ldao()
    lamp = dao.get_lamp(uid, date_str)
    if not lamp:
        from src.storage.session_dao import SessionDAO
        from src.engines.night_soliloquy import build_soliloquy, synth_lamp_audio
        from src.config import load_settings
        sdao = SessionDAO(str(load_settings().db_path))
        prefs = _pdao().get_pref(uid) or {}
        result = build_soliloquy(uid, date_str, sdao,
                                 whisper=bool(prefs.get("whisper_enabled", 1)))
        audio = synth_lamp_audio(result["text"]) if is_member(uid) else ""
        dao.upsert_lamp(uid, date_str, result["text"], audio)
        lamp = dao.get_lamp(uid, date_str)
    resp = {"date": lamp["date"], "text": lamp["text"],
            "favorited": bool(lamp["favorite"])}
    if is_member(uid) and lamp.get("audio_url"):
        resp["audio_url"] = lamp["audio_url"]
    return {"lamp": resp}


class FavBody(BaseModel):
    date: str


@router.post("/lamp/favorite")
def lamp_favorite(body: FavBody, uid: str = Depends(require_user)):
    dao = _ldao()
    lamp = dao.get_lamp(uid, body.date)
    if not lamp:
        raise HTTPException(status_code=404, detail="该夜灯语不存在")
    fav = 0 if lamp["favorite"] else 1
    dao.set_favorite(uid, body.date, fav)
    return {"favorited": bool(fav)}


@router.get("/lamp/history")
def lamp_history(uid: str = Depends(require_user)):
    """灯语历史:免费仅近 3 夜且剥离语音(红线);会员全部。"""
    lamps = _ldao().list_history(uid, limit=100)
    member = is_member(uid)
    if not member:
        lamps = lamps[:3]
        for l in lamps:
            l.pop("audio_url", None)
    return {"lamps": lamps, "member": member}
