"""微信虚拟支付（米大师）API — 道具直购 short_series_goods。

前端契约（miniprogram/utils/api.js → payment.js）：
    POST /api/pay/virtual/create   {product_id}   → {signData, paySig, signature, mode, outTradeNo, ...}
    POST /api/pay/virtual/notify                  ← 米大师发货通知回调（无鉴权但验签）
    GET  /api/pay/virtual/status?outTradeNo=      → {status, outTradeNo, ...}（前端支付后轮询）

启用条件（.env 控制，全部满足才走虚拟支付）：
    WECHAT_PAY_ENABLED=true   （现有 mock 支付保留为 false 路径，pay.py 行为不变）
    MIDAS_OFFER_ID=<米大师 offerId>，MIDAS_APP_KEY=<米大师 AppKey>
    MIDAS_ENV=0（现网）/ 1（沙箱）

签名算法（微信官方，2026-08 现状）：
    paySig     = hex(hmac_sha256(appKey, "requestVirtualPayment" + "&" + signData字符串))
    signature  = hex(hmac_sha256(session_key, signData字符串))        # 用户态签名
    signData 字段固定 8 项：offerId / buyQuantity / env / currencyType(CNY) /
        productId / goodsPrice(分) / outTradeNo(8-32字符唯一) / attach。
        mode 是 wx.requestVirtualPayment 的顶层参数（short_series_goods=道具直购），
        不在 signData 内（多传字段会导致 -15005 签名错误）。
    回调验签：X-WeChat-Signature = hex(hmac_sha256(appKey, 原始请求体))（兼容 "sha256=" 前缀）；
        无该请求头时兼容 body 内 pay_event_sig = hex(hmac_sha256(appKey, Event+"&"+payload))。

安全：
    create/status 挂 require_user，user_id 一律取 JWT sub，status 仅本人可查（owner 校验）；
    notify 无鉴权但必须验签通过才发货；outTradeNo 全局唯一（主键约束+重试）；
    session_key 加密落库（users.session_key_enc，AES-256-GCM，见 user.py 同款加密）。
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from src.api.pay import PRODUCTS, SUBSCRIBE_PLANS
from src.security.auth import require_user
from src.storage.models import connect as db_connect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pay-virtual"])

# 全局引用，由 main.py 在 lifespan 中设置
_member_dao = None
_db_path = None


def setup(member_dao):
    """在应用启动时设置 MemberDAO 引用，并确保 midas_orders 表存在。"""
    global _member_dao, _db_path
    _member_dao = member_dao
    if member_dao is not None:
        _db_path = member_dao.db_path
        _ensure_midas_orders_table()
    logger.info("虚拟支付（米大师）就绪: enabled=%s", virtual_pay_enabled())


# ── 配置（.env）──────────────────────────────────────────────────

def midas_offer_id() -> str:
    return os.getenv("MIDAS_OFFER_ID", "").strip()


def midas_app_key() -> str:
    return os.getenv("MIDAS_APP_KEY", "").strip()


def midas_env() -> int:
    """0=现网，1=沙箱（沙箱用于测试，需米大师后台开通沙箱）。"""
    try:
        return int(os.getenv("MIDAS_ENV", "0"))
    except (TypeError, ValueError):
        return 0


def wechat_pay_enabled() -> bool:
    """WECHAT_PAY_ENABLED env：false 时保持现有 mock 支付路径（pay.py）。"""
    return os.getenv("WECHAT_PAY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def virtual_pay_enabled() -> bool:
    """虚拟支付是否启用：WECHAT_PAY_ENABLED=true 且 MIDAS_OFFER_ID/MIDAS_APP_KEY 配齐。"""
    return wechat_pay_enabled() and bool(midas_offer_id()) and bool(midas_app_key())


# ── 签名（HMAC-SHA256 → hex 小写）───────────────────────────────

def _hmac_sha256_hex(key: str, message: str) -> str:
    return hmac.new(
        key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def build_pay_sig(app_key: str, sign_data_str: str) -> str:
    """支付签名：hex(hmac_sha256(appKey, 'requestVirtualPayment' + '&' + signData))。"""
    return _hmac_sha256_hex(app_key, f"requestVirtualPayment&{sign_data_str}")


def build_user_sig(session_key: str, sign_data_str: str) -> str:
    """用户态签名：hex(hmac_sha256(session_key, signData))。"""
    return _hmac_sha256_hex(session_key, sign_data_str)


def build_sign_data(offer_id: str, product_id: str, goods_price_cents: int,
                    out_trade_no: str, attach_str: str, env: int) -> str:
    """构造 signData JSON 字符串（字段固定 8 项，勿增删——多传字段报 -15005）。

    返回的字符串即参与 paySig/signature 签名的原串，前端必须原样透传。
    """
    sign_data = {
        "offerId": offer_id,
        "buyQuantity": 1,
        "env": int(env),
        "currencyType": "CNY",
        "productId": product_id,
        "goodsPrice": int(goods_price_cents),
        "outTradeNo": out_trade_no,
        "attach": attach_str,
    }
    return json.dumps(sign_data, ensure_ascii=True, separators=(",", ":"))


# ── midas_orders 表（独立自管理，不影响现有 payments/memberships）──

_MIDAS_ORDERS_SQL = """
CREATE TABLE IF NOT EXISTS midas_orders (
    out_trade_no TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payment_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    kind TEXT NOT NULL,                -- 'product' | 'subscription'
    plan TEXT,                         -- 订阅内部套餐（subscription 时非空）
    amount_cents INTEGER NOT NULL,
    env INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',     -- pending/paid/cancelled
    attach TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_midas_orders_user ON midas_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_midas_orders_status ON midas_orders(status);
"""


def _ensure_midas_orders_table():
    if not _db_path:
        return
    try:
        conn = db_connect(_db_path, timeout=10)
        conn.executescript(_MIDAS_ORDERS_SQL)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("midas_orders 表初始化失败: %s", e)


def _connect():
    return db_connect(_db_path, timeout=10)


# ── outTradeNo 生成（8-32 字符，仅 [0-9a-zA-Z_-|*@]，不能下划线开头）──

_OUT_TRADE_NO_CHARSET_OK = re.compile(r"^(?!_)[0-9a-zA-Z_\-|*@]{8,32}$")  # 8-32字符，不能下划线开头


def _generate_out_trade_no(user_id: str) -> str:
    """YL + 13位毫秒时间戳 + 8位随机hex = 23 字符，唯一性由主键约束兜底。"""
    return f"YL{int(time.time() * 1000)}{secrets.token_hex(4)}"


def _reserve_order(out_trade_no: str, user_id: str, payment_id: int, product_id: str,
                   kind: str, plan, amount_cents: int, env: int, attach_str: str) -> bool:
    """落库 midas_orders；outTradeNo 已存在返回 False（主键冲突）。"""
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO midas_orders
               (out_trade_no, user_id, payment_id, product_id, kind, plan, amount_cents, env, attach)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (out_trade_no, user_id, payment_id, product_id, kind, plan, amount_cents, env, attach_str),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def _get_order(out_trade_no: str):
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT out_trade_no, user_id, payment_id, product_id, kind, plan,
                      amount_cents, env, status, attach, created_at
               FROM midas_orders WHERE out_trade_no = ?""",
            (out_trade_no,),
        ).fetchone()
        if not row:
            return None
        return {
            "out_trade_no": row[0], "user_id": row[1], "payment_id": row[2],
            "product_id": row[3], "kind": row[4], "plan": row[5],
            "amount_cents": row[6], "env": row[7], "status": row[8],
            "attach": row[9], "created_at": row[10],
        }
    finally:
        conn.close()


def _mark_order_paid(out_trade_no: str) -> bool:
    """原子抢占：pending → paid（并发回调下只有一个赢家返回 True）。

    L5-2 修复（支付红线）：notify 发货前必须先原子抢占成功才允许发货——
    并发重复通知中 rowcount==0 的一方直接应答"已处理"，绝不重复发货。
    """
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE midas_orders SET status='paid', updated_at=datetime('now') WHERE out_trade_no=? AND status='pending'",
            (out_trade_no,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _revert_order_paid(out_trade_no: str) -> None:
    """发货失败回滚：paid → pending（微信重试时可重新发货，防订单丢失）。"""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE midas_orders SET status='pending', updated_at=datetime('now') WHERE out_trade_no=? AND status='paid'",
            (out_trade_no,),
        )
        conn.commit()
    finally:
        conn.close()


# ── session_key（由 /api/user/login 加密保存，此处解密读取）────────

def _get_session_key(user_id: str):
    """读取登录时保存的 session_key（用户态签名用）。未登录态返回 None。"""
    try:
        from src.api.user import get_user_session_key
        return get_user_session_key(user_id)
    except Exception as e:
        logger.error("session_key 读取失败 user=%s: %s", user_id, e)
        return None


# ── 商品 / 套餐 ──────────────────────────────────────────────────

def _lookup_product(product_id: str):
    """支持道具直购商品（PRODUCTS）与会员订阅套餐（SUBSCRIBE_PLANS）。"""
    if product_id in PRODUCTS:
        return {"kind": "product", "name": PRODUCTS[product_id]["name"],
                "amount": PRODUCTS[product_id]["amount"], "plan": None,
                "period_days": None}
    if product_id in SUBSCRIBE_PLANS:
        return {"kind": "subscription", "name": SUBSCRIBE_PLANS[product_id]["name"],
                "amount": SUBSCRIBE_PLANS[product_id]["amount"],
                "plan": SUBSCRIBE_PLANS[product_id]["internal_plan"],
                "period_days": SUBSCRIBE_PLANS[product_id]["period_days"]}
    return None


# ── 请求模型 ────────────────────────────────────────────────────

class VirtualPayCreateRequest(BaseModel):
    product_id: str


# ── API 端点 ────────────────────────────────────────────────────

@router.post("/api/pay/virtual/create")
async def virtual_pay_create(req: VirtualPayCreateRequest, uid: str = Depends(require_user)):
    """创建虚拟支付订单，返回前端三要素（signData/paySig/signature）+ mode。

    前端 wx.requestVirtualPayment({signData, paySig, signature, mode}) 调起米大师支付。
    """
    global _member_dao
    if not virtual_pay_enabled():
        raise HTTPException(
            status_code=400,
            detail={"code": "virtual_pay_not_enabled",
                    "message": "虚拟支付未启用：请配置 MIDAS_OFFER_ID/MIDAS_APP_KEY 并设置 WECHAT_PAY_ENABLED=true"},
        )
    if _member_dao is None:
        raise HTTPException(status_code=503, detail={"code": "service_not_ready", "message": "支付服务未就绪"})

    product = _lookup_product(req.product_id)
    if not product:
        raise HTTPException(status_code=404, detail={"code": "product_not_found", "message": f"产品不存在: {req.product_id}"})

    # 用户态签名需要 session_key（登录时已加密保存）
    session_key = _get_session_key(uid)
    if not session_key:
        raise HTTPException(
            status_code=400,
            detail={"code": "session_key_missing",
                    "message": "支付登录态缺失，请重新登录后再试"},
        )

    # 落库 payments（pending）→ 生成唯一 outTradeNo → 落库 midas_orders
    amount_cents = int(round(product["amount"] * 100))
    payment_id = _member_dao.create_payment(uid, product["amount"], req.product_id, "midas")

    attach = json.dumps(
        {"kind": product["kind"], "id": req.product_id, "plan": product["plan"],
         "period_days": product["period_days"]},
        ensure_ascii=True, separators=(",", ":"),
    )
    env = midas_env()

    out_trade_no = None
    for _attempt in range(5):
        candidate = _generate_out_trade_no(uid)
        if _reserve_order(candidate, uid, payment_id, req.product_id, product["kind"],
                          product["plan"], amount_cents, env, attach):
            out_trade_no = candidate
            break
    if not out_trade_no:
        raise HTTPException(status_code=500, detail={"code": "order_create_failed", "message": "订单号生成失败，请重试"})

    # 三要素
    sign_data_str = build_sign_data(midas_offer_id(), req.product_id, amount_cents,
                                    out_trade_no, attach, env)
    pay_sig = build_pay_sig(midas_app_key(), sign_data_str)
    signature = build_user_sig(session_key, sign_data_str)

    logger.info("虚拟支付订单创建: user=%s product=%s outTradeNo=%s price=%d分 env=%d",
                uid, req.product_id, out_trade_no, amount_cents, env)

    return {
        "signData": sign_data_str,
        "paySig": pay_sig,
        "signature": signature,
        "mode": "short_series_goods",
        "env": env,
        "outTradeNo": out_trade_no,
        "productId": req.product_id,
        "goodsPrice": amount_cents,
        "offerId": midas_offer_id(),
        "status": "pending",
        "virtual": True,
    }


# ── 回调验签 ────────────────────────────────────────────────────

def _verify_callback_signature(raw_body: bytes, headers: dict, app_key: str) -> bool:
    """验签：优先请求头 X-WeChat-Signature，其次兼容 body 内 pay_event_sig。"""
    if not raw_body or not app_key:
        return False

    header_sig = (headers.get("X-WeChat-Signature") or "").strip()
    if header_sig:
        if header_sig.startswith("sha256="):
            header_sig = header_sig[len("sha256="):]
        expected = _hmac_sha256_hex(app_key, raw_body.decode("utf-8", "ignore"))
        if secrets.compare_digest(header_sig, expected):
            return True
        logger.warning("虚拟支付回调验签失败（X-WeChat-Signature 不匹配）")
        return False

    # 兼容兜底：body 内 pay_event_sig = hex(hmac_sha256(appKey, Event + "&" + payload))
    try:
        text = raw_body.decode("utf-8")
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return False
    sig = (data.get("pay_event_sig") or "").strip()
    if not sig:
        logger.warning("虚拟支付回调缺少签名（无 X-WeChat-Signature 也无 pay_event_sig）")
        return False
    event = data.get("Event") or ""
    # 去掉 pay_event_sig 字段后重建 payload 字符串（保留原始序列化；
    # 字段在末尾时其前导逗号一并处理，避免 `,}` 破坏 JSON）
    payload_str = re.sub(r'"pay_event_sig"\s*:\s*"[^"]*"', "", text).replace(",}", "}")
    expected = _hmac_sha256_hex(app_key, f"{event}&{payload_str}")
    return secrets.compare_digest(sig, expected)


def _deliver_goods(order: dict) -> bool:
    """按 attach 发货：product → 订单置 paid；subscription → 开通会员。幂等。

    L5-2：会员商品按 attach 携带的 period_days 开通（季度 90/年度 365 等档期
    不丢失），到期时间按续费延长计算（member_dao._compute_expiry）。
    """
    global _member_dao
    if _member_dao is None:
        return False
    try:
        if order["kind"] == "subscription" and order["plan"]:
            period_days = None
            try:
                attach = json.loads(order["attach"] or "{}")
                period_days = attach.get("period_days")
            except (ValueError, AttributeError):
                pass
            _member_dao.confirm_payment(order["payment_id"], order["user_id"],
                                        order["plan"], period_days=period_days)
        else:
            _member_dao.mark_payment_paid(order["payment_id"], order["user_id"])
        return True
    except Exception as e:
        logger.error("虚拟支付发货失败 order=%s: %s", order["out_trade_no"], e)
        return False


@router.post("/api/pay/virtual/notify")
async def virtual_pay_notify(request: Request):
    """米大师发货通知回调（xpay_goods_deliver_notify）——无鉴权但必须验签通过。

    验签：请求头 X-WeChat-Signature = hex(hmac_sha256(appKey, 原始请求体))。
    验签通过 → 原子抢占订单（pending→paid，仅一个并发赢家）→ 按 attach 发货
    → 返回 {"errcode": 0}。抢占失败（重复通知/并发）直接应答"已处理"（不发货）；
    发货失败回滚 pending，微信重试可重新发货。
    幂等：已发货订单重复通知直接返回成功。
    """
    raw_body = await request.body()
    app_key = midas_app_key()
    if not app_key:
        logger.error("虚拟支付回调收到但 MIDAS_APP_KEY 未配置，拒绝处理")
        return {"errcode": -1, "errmsg": "midas not configured"}

    if not _verify_callback_signature(raw_body, request.headers, app_key):
        logger.warning("虚拟支付回调验签失败，拒绝发货（body 前 200 字符: %s）",
                       raw_body[:200].decode("utf-8", "ignore"))
        return {"errcode": -1, "errmsg": "signature mismatch"}

    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        logger.error("虚拟支付回调 body 解析失败: %s", e)
        return {"errcode": -1, "errmsg": "bad body"}

    event = data.get("Event") or ""
    out_trade_no = data.get("OutTradeNo") or data.get("out_trade_no") or data.get("order_id") or ""
    logger.info("虚拟支付回调: event=%s outTradeNo=%s env=%s", event, out_trade_no, data.get("Env", ""))

    if event and event != "xpay_goods_deliver_notify":
        # 非发货事件（如 xpay_refund_notify）暂不处理，但应答成功避免微信重试风暴
        return {"errcode": 0, "errmsg": "ok, event ignored"}

    if not out_trade_no:
        return {"errcode": -1, "errmsg": "missing out_trade_no"}

    order = _get_order(out_trade_no)
    if order is None:
        # 未知订单（非本系统/历史数据）：记录后应答成功
        logger.warning("虚拟支付回调未知订单: %s（忽略）", out_trade_no)
        return {"errcode": 0, "errmsg": "ok, unknown order"}

    # L5-2 修复（支付红线·并发幂等）：发货前先原子抢占 pending→paid——
    # 并发重复通知只有一个赢家；rowcount==0（已被并发回调处理/已发货）
    # 直接应答"已处理"，绝不重复发货（原实现 read-then-act 可双倍发货）。
    if not _mark_order_paid(out_trade_no):
        logger.info("虚拟支付回调重复通知（原子抢占失败）: outTradeNo=%s", out_trade_no)
        return {"errcode": 0, "errmsg": "ok, already processed"}

    if not _deliver_goods(order):
        # 发货失败 → 回滚为 pending（微信会按 errcode!=0 重试，可重新发货，
        # 避免订单被永久跳过）；deliver 幂等，重试不产生副作用。
        _revert_order_paid(out_trade_no)
        return {"errcode": -1, "errmsg": "deliver failed"}

    logger.info("虚拟支付发货完成: outTradeNo=%s kind=%s product=%s user=%s",
                out_trade_no, order["kind"], order["product_id"], order["user_id"])
    return {"errcode": 0, "errmsg": "success"}


@router.get("/api/pay/virtual/status")
async def virtual_pay_status(out_trade_no: str = Query(..., alias="outTradeNo", min_length=8, max_length=32),
                             uid: str = Depends(require_user)):
    """查询订单状态（前端支付成功后轮询）。只允许查询本人订单（owner 校验）。"""
    if not out_trade_no or not _OUT_TRADE_NO_CHARSET_OK.match(out_trade_no):
        raise HTTPException(status_code=400, detail={"code": "bad_out_trade_no", "message": "订单号格式不正确"})

    order = _get_order(out_trade_no)
    if order is None:
        raise HTTPException(status_code=404, detail={"code": "order_not_found", "message": "订单不存在"})
    if order["user_id"] != uid:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "无权访问该订单"})

    return {
        "status": order["status"],
        "outTradeNo": order["out_trade_no"],
        "productId": order["product_id"],
        "kind": order["kind"],
        "amountCents": order["amount_cents"],
        "env": order["env"],
        "createdAt": order["created_at"],
    }
