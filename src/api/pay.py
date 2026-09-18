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

k53 部署门禁（生产禁用 mock 支付）：
    `mock_pay_blocked()` = `is_production()` 且 真实支付未配置（WECHAT_PAY_ENABLED +
    MIDAS_OFFER_ID + MIDAS_APP_KEY 任一缺失）→ `/api/pay/create`、`/api/pay/subscribe`、
    `/api/pay/virtual/create` 一律 **503 {code: pay_unavailable}**，**不落单、不置 paid、
    不开通权益**（修复「分文未收但权益已发」的收入漏损）。非生产（dev/体验态）行为不变。

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
from src.config import is_experience_mode, is_production

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pay"])

# 全局引用，由 main.py 在 lifespan 中设置
_member_dao = None


def setup(member_dao):
    """在应用启动时设置 MemberDAO 引用。"""
    global _member_dao
    _member_dao = member_dao
    # k53 部署门禁可见性：启动即打印守卫状态，运维在启动日志即可确认，
    # 避免「生产未配真实支付却仍在 mock 发权益」无人知晓。
    if mock_pay_blocked():
        logger.warning("支付守卫已生效(k53)：生产 + 真实支付未配置 → /api/pay/create|subscribe|"
                       "virtual/create 一律 503，不再置 paid/开会员（缺失配置: %s）",
                       ", ".join(missing_real_pay_config()) or "WECHAT_PAY_ENABLED")
    elif is_production():
        logger.info("支付守卫(k53)：生产 + 真实支付已配置 → 走真实（虚拟）支付通道")
    else:
        logger.info("支付守卫(k53)：非生产（dev/体验态）→ mock 支付可用，行为与 k53 前一致")


# ── 产品 / 套餐定义（与前端 miniprogram/utils/payment.js PRODUCTS 对齐）──

PRODUCTS = {
    "love_compatibility": {"name": "感情合盘分析", "amount": 9.9, "type": "single"},
    "deep_report": {"name": "深度解读报告", "amount": 19.9, "type": "single"},
    "ming_report": {"name": "AI取名名笺深度报告（宝宝版）", "amount": 19.9, "type": "single"},
    "ming_report_pro": {"name": "成人改名深度报告（含现名诊断与改名对比）", "amount": 29.9, "type": "single"},
    "full_analysis": {"name": "全盘分析", "amount": 29.9, "type": "single"},
    "detailed_fortune": {"name": "详细每日运势", "amount": 0.0, "type": "single"},
}

# 会员套餐（L5-2）：前端 plan_id → 内部套餐（member_dao.PLANS 的 key）+ 定价
# 档位设计：基础会员三档（月/季/年，plan='basic'）+ 高级会员一档（月，plan='pro'，
# 含论财/论事业/论健康等专项论断，见 zhuanxiang 高级会员门控）。
# period_days 参与到期时间计算（confirm_payment 续费延长，见 member_dao）。
SUBSCRIBE_PLANS = {
    "monthly": {"name": "基础会员·月", "amount": 19.9, "internal_plan": "basic", "period_days": 30},
    "quarterly": {"name": "基础会员·季", "amount": 49.9, "internal_plan": "basic", "period_days": 90},
    "yearly": {"name": "基础会员·年", "amount": 168.0, "internal_plan": "basic", "period_days": 365},
    "pro_monthly": {"name": "高级会员·月", "amount": 39.9, "internal_plan": "pro", "period_days": 30},
}


def wechat_pay_enabled() -> bool:
    """WECHAT_PAY_ENABLED env：false 时 mock 直接成功，true 时走微信支付流程。"""
    return os.getenv("WECHAT_PAY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def missing_real_pay_config() -> list:
    """真实支付缺失的配置项名（**仅用于服务端日志/启动自检**，绝不回给客户端）。"""
    missing = []
    if not wechat_pay_enabled():
        missing.append("WECHAT_PAY_ENABLED")
    if not os.getenv("MIDAS_OFFER_ID", "").strip():
        missing.append("MIDAS_OFFER_ID")
    if not os.getenv("MIDAS_APP_KEY", "").strip():
        missing.append("MIDAS_APP_KEY")
    return missing


def real_pay_configured() -> bool:
    """真实（微信虚拟/米大师）支付是否配置齐：WECHAT_PAY_ENABLED=true + offerId + AppKey。

    单一事实源：`pay_midas.virtual_pay_enabled()` 复用本函数（同一判定，禁止两处漂移）。
    """
    return not missing_real_pay_config()


def mock_pay_blocked() -> bool:
    """生产部署且真实支付未配置 → **禁用 mock 支付通道**（k53 部署门禁）。

    背景（k52 审查实测的既有漏损）：mock 模式下 `/api/pay/create` 订单**直接置 paid**、
    `/api/pay/subscribe` **直接开通会员**——分文未收但权益已发（收入漏损 + 与审核
    材料描述不符）；而客户端在 release 下只看到「支付失败」，用户与运营都不易察觉。

    fail-closed：生产只认「已配置的真实支付」，配置缺失一律拒绝服务（503），
    绝不静默降级成「免费发货」。非生产（dev/体验态）不受影响，mock 照旧。
    """
    return is_production() and not real_pay_configured()


# 生产守卫的 503 契约（三端点统一；客户端按 detail.code 分流）
PAY_UNAVAILABLE_CODE = "pay_unavailable"
PAY_UNAVAILABLE_MESSAGE = "支付暂不可用，请稍后再试"


def raise_pay_unavailable(endpoint: str, uid: str) -> None:
    """生产守卫拒绝服务：503 + 统一错误码。缺失配置只进服务端日志（不向客户端泄露）。"""
    logger.error("支付端点已在生产环境拒绝服务(k53): endpoint=%s user=%s reason=real_pay_not_configured missing=%s",
                 endpoint, uid, ",".join(missing_real_pay_config()))
    raise HTTPException(
        status_code=503,
        detail={"code": PAY_UNAVAILABLE_CODE, "message": PAY_UNAVAILABLE_MESSAGE},
    )


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
    k53 部署门禁：生产 + 真实支付未配置 → 503（在建单之前拦截，不落单、不置 paid）。
    """
    global _member_dao
    if mock_pay_blocked():
        raise_pay_unavailable("/api/pay/create", uid)
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
    """订阅会员套餐（mock 支付后开通 memberships 表）。

    k53 部署门禁：生产 + 真实支付未配置 → 503（在开通会员之前拦截）。
    """
    global _member_dao
    if mock_pay_blocked():
        raise_pay_unavailable("/api/pay/subscribe", uid)
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
        _member_dao.confirm_payment(payment_id, uid, plan["internal_plan"],
                                     period_days=plan["period_days"])
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
