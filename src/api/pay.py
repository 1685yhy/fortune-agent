"""支付 / 订单 / 会员 API（A2 缺口：前端 5 个 404 接口 → 已接通）.

前端契约（miniprogram/utils/api.js）：
    POST /api/pay/create                {product_id}      → {payment, orderId}
    POST /api/pay/subscribe             {plan_id}         → {payment, orderId, membership?}
    GET  /api/user/member                                → {isMember, expireDate, benefits}
    GET  /api/user/orders                                → {orders: [...]}
    GET  /api/user/purchase/{product_id}                 → {purchased: bool}

支付策略（可配置）：
    WECHAT_PAY_ENABLED=false（默认，开发环境）：mock 直接成功——订单创建即置为 paid，
      返回 {payment: {mock: true, success: true}}；
    WECHAT_PAY_ENABLED=true（生产）：订单创建为 pending，返回微信支付参数占位
      （requires_config=true，需配置商户号/证书后实现统一下单与回调）。

安全：所有端点挂 require_user，user_id 一律取 JWT sub；订单/会员查询只允许
访问自己（WHERE user_id = uid），无法越权读取他人数据。
"""
import logging
import os
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.security.auth import require_user
from src.config import is_experience_mode

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pay"])

# 全局引用，由 main.py 在 lifespan 中设置
_member_dao = None


def setup(member_dao):
    """在应用启动时设置 MemberDAO 引用。"""
    global _member_dao
    _member_dao = member_dao


# ── 产品 / 套餐定义（与前端 miniprogram/utils/payment.js PRODUCTS 对齐）──

PRODUCTS = {
    "love_compatibility": {"name": "感情合盘分析", "amount": 9.9, "type": "single"},
    "deep_report": {"name": "深度解读报告", "amount": 19.9, "type": "single"},
    "full_analysis": {"name": "全盘分析", "amount": 29.9, "type": "single"},
    "detailed_fortune": {"name": "详细每日运势", "amount": 0.0, "type": "single"},
}

# 会员套餐：前端 plan_id → 内部套餐（member_dao.PLANS 的 key）+ 定价
SUBSCRIBE_PLANS = {
    "monthly": {"name": "月度会员", "amount": 68.0, "internal_plan": "pro", "period_days": 30},
    "first_month": {"name": "首月会员", "amount": 38.0, "internal_plan": "pro", "period_days": 30},
}


def wechat_pay_enabled() -> bool:
    """WECHAT_PAY_ENABLED env：false 时 mock 直接成功，true 时走微信支付流程。"""
    return os.getenv("WECHAT_PAY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _build_payment_params(order_id: str, amount: float, description: str) -> dict:
    """构造支付参数。

    - mock 模式：直接标记成功（payment.js DEV_MODE 下 wx.requestPayment 失败
      会走演示成功分支，订单已在后端落库置为 paid）；
    - 微信模式：返回统一下单参数占位（生产需配置 WECHAT_MCH_ID 等商户信息后
      实现真实 prepay_id 与 paySign）。
    """
    if wechat_pay_enabled():
        return {
            "mock": False,
            "appId": os.getenv("WECHAT_APP_ID", ""),
            "timeStamp": str(int(time.time())),
            "nonceStr": secrets.token_hex(16),
            "package": f"prepay_id=PLACEHOLDER_{order_id}",
            "signType": "RSA",
            "paySign": "PLACEHOLDER_SIGN",
            "requires_config": True,
            "message": "微信支付商户配置未完成，请配置 WECHAT_MCH_ID/WECHAT_MCH_KEY 后启用",
        }
    return {
        "mock": True,
        "success": True,
        "mode": "mock",
        "message": "开发环境模拟支付成功（WECHAT_PAY_ENABLED=false）",
    }


# ── 请求模型 ────────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    product_id: str


class SubscribeRequest(BaseModel):
    plan_id: str  # 'monthly' | 'first_month'


# ── API 端点 ────────────────────────────────────────────────────

@router.post("/api/pay/create")
async def pay_create(req: CreateOrderRequest, uid: str = Depends(require_user)):
    """创建订单。

    只操作自己的数据：user_id 一律取 JWT sub。
    """
    global _member_dao
    if _member_dao is None:
        raise HTTPException(status_code=503, detail="支付服务未就绪")

    product = PRODUCTS.get(req.product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"产品不存在: {req.product_id}")

    payment_id = _member_dao.create_payment(
        uid, product["amount"], req.product_id, "mock" if not wechat_pay_enabled() else "wechat",
    )
    order_id = str(payment_id)

    # mock 模式：订单直接支付成功
    if not wechat_pay_enabled():
        _member_dao.mark_payment_paid(payment_id, uid)
        logger.info("支付建单(模拟): user=%s product=%s amount=%s payment_id=%s 状态=paid(mock)",
                    uid, req.product_id, product["amount"], payment_id)
    else:
        logger.info("支付建单: user=%s product=%s amount=%s payment_id=%s 状态=pending(微信配置占位)",
                    uid, req.product_id, product["amount"], payment_id)

    return {
        "payment": _build_payment_params(order_id, product["amount"], product["name"]),
        "orderId": order_id,
        "product_id": req.product_id,
        "amount": product["amount"],
    }


@router.post("/api/pay/subscribe")
async def pay_subscribe(req: SubscribeRequest, uid: str = Depends(require_user)):
    """订阅会员套餐（mock 支付后开通 memberships 表）。"""
    global _member_dao
    if _member_dao is None:
        raise HTTPException(status_code=503, detail="支付服务未就绪")

    plan = SUBSCRIBE_PLANS.get(req.plan_id)
    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"套餐不存在: {req.plan_id}（可用: {', '.join(SUBSCRIBE_PLANS.keys())}）",
        )

    payment_id = _member_dao.create_payment(
        uid, plan["amount"], req.plan_id, "mock" if not wechat_pay_enabled() else "wechat",
    )
    order_id = str(payment_id)

    membership = None
    if not wechat_pay_enabled():
        _member_dao.confirm_payment(payment_id, uid, plan["internal_plan"])
        membership = _member_dao.get_membership(uid)
        logger.info("订阅支付建单(模拟): user=%s plan=%s amount=%s payment_id=%s 已开通会员=%s",
                    uid, req.plan_id, plan["amount"], payment_id, plan["internal_plan"])
    else:
        logger.info("订阅支付建单: user=%s plan=%s amount=%s payment_id=%s 状态=pending(微信配置占位)",
                    uid, req.plan_id, plan["amount"], payment_id)

    return {
        "payment": _build_payment_params(order_id, plan["amount"], plan["name"]),
        "orderId": order_id,
        "plan_id": req.plan_id,
        "membership": membership,
    }


@router.get("/api/user/member")
async def user_member(uid: str = Depends(require_user)):
    """获取当前用户会员信息（只读自己的数据）。"""
    global _member_dao
    if _member_dao is None:
        raise HTTPException(status_code=503, detail="会员服务未就绪")

    membership = _member_dao.get_membership(uid)
    plan = membership.get("plan", "free")
    exp_mode = is_experience_mode()
    # 体验模式：一律按会员展示（解锁前端会员权益展示）
    is_member = (plan != "free") or exp_mode

    return {
        "isMember": is_member,
        "plan": plan,
        "planLabel": membership.get("plan_label", "免费版"),
        "expireDate": membership.get("expires_at"),
        "benefits": membership.get("features", []),
        "queriesRemaining": membership.get("queries_remaining"),
        "queriesUsed": membership.get("queries_used", 0),
        "queriesLimit": membership.get("queries_limit"),
        "autoRenew": membership.get("auto_renew", False),
        "experienceMode": exp_mode,
    }


@router.get("/api/user/orders")
async def user_orders(uid: str = Depends(require_user)):
    """获取当前用户订单列表（从 payments 表，只读自己的数据）。"""
    global _member_dao
    if _member_dao is None:
        raise HTTPException(status_code=503, detail="订单服务未就绪")

    rows = _member_dao.get_user_payments(uid, limit=50)
    orders = []
    for r in rows:
        orders.append({
            "id": r["id"],
            "orderId": str(r["id"]),
            "product_id": r["plan"],
            "productName": (PRODUCTS.get(r["plan"]) or SUBSCRIBE_PLANS.get(r["plan"]) or {}).get("name", r["plan"]),
            "amount": r["amount"],
            "status": r["status"],
            "payment_method": r["payment_method"],
            "created_at": r["created_at"],
        })
    return {"orders": orders}


@router.get("/api/user/purchase/{product_id}")
async def user_purchase(product_id: str, uid: str = Depends(require_user)):
    """检查当前用户是否已购买某产品（只读自己的数据）。"""
    global _member_dao
    if _member_dao is None:
        raise HTTPException(status_code=503, detail="订单服务未就绪")

    purchased = _member_dao.get_user_purchase(uid, product_id) is not None
    return {"purchased": purchased, "product_id": product_id}
