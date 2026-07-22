"""用户管理 API — 登录、资料、八字、订阅、反馈。

提供小程序所需的所有 /api/user/* 端点。
"""

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.storage.dao import UserDAO
from src.storage.session_dao import SessionDAO

router = APIRouter(tags=["user"])

# 全局引用，由 main.py 在 lifespan 中设置
_dao: Optional[UserDAO] = None
_session_dao: Optional[SessionDAO] = None
_auth_handler = None


def setup(dao: UserDAO, session_dao: Optional[SessionDAO] = None, auth_handler=None):
    """在应用启动时设置 DAO 和认证引用。"""
    global _dao, _session_dao, _auth_handler
    _dao = dao
    _session_dao = session_dao
    _auth_handler = auth_handler


# ── 请求模型 ────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    code: str  # wx.login 获取的临时 code


class BaziRequest(BaseModel):
    birth_year: int = 0
    birth_month: int = 0
    birth_day: int = 0
    birth_hour: int = 0
    birth_minute: int = 0
    gender: str = "unknown"
    calendar: str = "solar"  # "solar" or "lunar"
    city: str = ""


class SubscriptionRequest(BaseModel):
    daily_push: bool = False


class FeedbackRequest(BaseModel):
    text: str = ""
    is_anonymous: bool = False


# ── API 端点 ─────────────────────────────────────────────────────

@router.post("/api/user/login")
async def user_login(req: LoginRequest):
    """微信登录或开发模式登录。

    生产环境：用 code 调微信接口换取 openid。
    开发环境：直接返回 dev token（不校验 code）。
    """
    global _dao, _auth_handler

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    token = ""

    # 尝试真正的微信登录
    if req.code and req.code != "dev_code" and len(req.code) > 10:
        try:
            openid = await _wechat_code_to_openid(req.code)
            if openid:
                user_id = f"wx_{openid}"
        except Exception:
            pass  # 降级到 dev 模式

    # 生成 JWT token
    if _auth_handler:
        token = _auth_handler.create_user_token(user_id)
    else:
        token = f"dev_token_{user_id}"

    # 确保用户记录存在
    if _dao:
        try:
            _dao.save_user_bazi(user_id, {})  # 创建空记录
        except Exception:
            pass

    return {
        "token": token,
        "user": {
            "id": user_id,
            "has_bazi": False,
            "is_new": True,
        },
    }


@router.get("/api/user/profile")
async def user_profile(user_id: str = ""):
    """获取用户资料，包括八字信息、偏好设置、会员状态。"""
    global _dao

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    bazi_info = None
    has_bazi = False
    push_settings = {"daily_push": False, "push_time": "08:00"}
    stats = {"total_consultations": 0}

    if _dao:
        bazi_info = _dao.get_user_bazi(user_id)
        has_bazi = bool(bazi_info and bazi_info.get("bazi"))
        push_settings = {
            "daily_push": _dao.get_user_push_settings(user_id).get("push_enabled", False),
            "push_time": _dao.get_user_push_settings(user_id).get("push_time", "08:00"),
        }
        consultations = _dao.get_user_consultations(user_id, limit=1000)
        stats["total_consultations"] = len(consultations)

    # 生成八字标签
    bazi_label = ""
    if bazi_info and bazi_info.get("bazi"):
        bazi_list = bazi_info["bazi"]
        if len(bazi_list) >= 8:
            bazi_label = f"{bazi_list[0]}{bazi_list[1]} {bazi_list[2]}{bazi_list[3]} {bazi_list[4]}{bazi_list[5]} {bazi_list[6]}{bazi_list[7]}"

    return {
        "id": user_id,
        "has_bazi": has_bazi,
        "bazi_info": bazi_info,
        "bazi_label": bazi_label,
        "push_settings": push_settings,
        "stats": stats,
        "member_plan": "free",
        "queries_remaining": 50,
    }


@router.post("/api/user/bazi")
async def user_update_bazi(req: BaziRequest, user_id: str = ""):
    """保存或更新用户八字信息。"""
    global _dao

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    bazi_info = {
        "year": req.birth_year,
        "month": req.birth_month,
        "day": req.birth_day,
        "hour": req.birth_hour,
        "minute": req.birth_minute,
        "gender": req.gender,
        "calendar": req.calendar,
        "city": req.city,
    }

    if _dao:
        _dao.save_user_bazi(user_id, bazi_info)

    return {"success": True, "message": "八字信息已保存"}


@router.post("/api/user/subscription")
async def user_subscription(req: SubscriptionRequest, user_id: str = ""):
    """更新每日推送订阅设置。"""
    global _dao

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    if _dao:
        _dao.set_push_enabled(user_id, req.daily_push)

    return {"success": True, "daily_push": req.daily_push}


@router.post("/api/user/feedback")
async def user_feedback(req: FeedbackRequest, user_id: str = ""):
    """提交用户反馈。"""
    global _dao

    feedback_text = req.text.strip()
    if not feedback_text:
        raise HTTPException(status_code=400, detail="反馈内容不能为空")

    # 存储反馈（保存在最近的 consultation 或单独存储）
    if _dao:
        try:
            # 查找最近一次咨询
            consultations = _dao.get_user_consultations(user_id, limit=1)
            if consultations:
                _dao.save_feedback(consultations[0]["id"], "positive" if "好" in feedback_text else "negative")
        except Exception:
            pass

    # 使用匿名用户 ID 如果要求
    display_user = "anonymous" if req.is_anonymous else user_id

    return {
        "success": True,
        "message": "感谢你的反馈！我们会认真对待每一条建议。",
        "submitted_as": display_user,
    }


# ── 辅助函数 ─────────────────────────────────────────────────────

async def _wechat_code_to_openid(code: str) -> Optional[str]:
    """用微信 code 换取 openid。

    生产环境需要配置 AppID 和 AppSecret。
    当前返回 None 使用 dev 模式。
    """
    # TODO: 配置真实的微信 AppID/AppSecret
    # import httpx
    # resp = await httpx.AsyncClient().get(
    #     "https://api.weixin.qq.com/sns/jscode2session",
    #     params={"appid": APP_ID, "secret": APP_SECRET, "js_code": code, "grant_type": "authorization_code"},
    # )
    # data = resp.json()
    # return data.get("openid")
    return None
