"""微信虚拟支付（米大师）接口测试 — scripts/test_virtual_pay.py

覆盖（与任务验证要求对应）：
1. 无 token 创建订单 → 401
2. 带 token 创建订单 → 200，三要素齐全；paySig 用 appKey 复算一致；signature 用 session_key 复算一致
3. 重复 outTradeNo 拒绝
4. notify 验签逻辑（构造签名验证：X-WeChat-Signature 正确→发货成功；篡改→拒绝；
   pay_event_sig 兜底验签；重复通知幂等）
5. 登录链路：dev 登录 → session_key 加密落库（可解密回读）→ 创建订单可用
6. status 查询：owner 可见；他人 403
7. WECHAT_PAY_ENABLED=false → 400 virtual_pay_not_enabled（mock 保留路径）

运行：cd /mnt/e/fortune-agent && .venv/bin/python scripts/test_virtual_pay.py
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path

# ── 环境（必须先于任何 src 导入设置，避免 .env 覆盖）──
os.environ["WECHAT_APP_ID"] = ""
os.environ["WECHAT_APP_SECRET"] = ""
os.environ["DEV_OPENID"] = "test_dev_user"
os.environ["MIDAS_OFFER_ID"] = "offer_test_001"
os.environ["MIDAS_APP_KEY"] = "test_midas_app_key_0123456789abcdef"
os.environ["MIDAS_ENV"] = "1"
os.environ["WECHAT_PAY_ENABLED"] = "true"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest import mock

from src.api import pay_midas
from src.api import user as user_api
from src.security.auth import AuthHandler, set_auth_handler
from src.storage.dao import UserDAO
from src.storage.member_dao import MemberDAO

APP_KEY = os.environ["MIDAS_APP_KEY"]
SESSION_KEY = "test_session_key_for_user_sig"

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def hmac_sha256_hex(key, msg):
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def main():
    global PASS, FAIL
    tmp = tempfile.mkdtemp(prefix="midas_test_")
    db = os.path.join(tmp, "test.db")

    member_dao = MemberDAO(db)
    dao = UserDAO(db)
    user_api.setup(dao, None, None)  # _dao 就绪（session_key 读写依赖）
    pay_midas.setup(member_dao)      # midas_orders 表

    auth = AuthHandler()
    set_auth_handler(auth)
    user_api.setup(dao, None, auth)  # _auth_handler 就绪（dev 登录签发可验证 token）

    app = FastAPI(title="midas-test")
    app.include_router(user_api.router)
    app.include_router(pay_midas.router)
    client = TestClient(app)

    print("=" * 62)
    print("测试 1：登录链路 — dev 登录 → session_key 加密落库")
    print("=" * 62)
    r = client.post("/api/user/login", json={"code": "dev_code"})
    check("dev 登录 200", r.status_code == 200, str(r.status_code))
    login_data = r.json()
    token = login_data.get("token", "")
    check("登录返回 token", bool(token))
    uid = login_data["user"]["id"]
    check("user_id 为 wx_dev_user_test", uid == "wx_test_dev_user", uid)
    stored_key = user_api.get_user_session_key(uid)
    check("session_key 已存储且可解密回读", stored_key is not None and len(stored_key) == 64, str(stored_key))
    # 密文落库验证：列值应为 ciphertext（含 ":"），非明文
    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT session_key_enc FROM users WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    check("session_key 密文落库（AES-256-GCM）", bool(row) and ":" in (row[0] or ""), str(row))
    check("session_key 与 dev 确定性派生一致",
          stored_key == hashlib.sha256(f"dev_session_key:{os.environ['DEV_OPENID']}".encode()).hexdigest())

    headers = {"Authorization": f"Bearer {token}"}
    other_uid = "wx_other_user"
    other_token = auth.create_user_token(other_uid, openid="other_openid")
    other_headers = {"Authorization": f"Bearer {other_token}"}

    print("=" * 62)
    print("测试 2：无 token 创建订单 → 401")
    print("=" * 62)
    r = client.post("/api/pay/virtual/create", json={"product_id": "love_compatibility"})
    check("无 token 401", r.status_code == 401, str(r.status_code))

    print("=" * 62)
    print("测试 3：创建订单 → 三要素齐全 + 签名复算一致")
    print("=" * 62)
    r = client.post("/api/pay/virtual/create", json={"product_id": "love_compatibility"}, headers=headers)
    check("创建订单 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    data = r.json()
    for field in ("signData", "paySig", "signature", "mode", "outTradeNo", "productId", "goodsPrice", "offerId"):
        check(f"返回字段 {field}", field in data and data[field] not in (None, ""), str(data.get(field)))

    sign_data_str = data["signData"]
    out_trade_no = data["outTradeNo"]
    check("mode=short_series_goods（道具直购）", data["mode"] == "short_series_goods", data["mode"])
    check("goodsPrice=990（9.9元→分）", data["goodsPrice"] == 990, str(data["goodsPrice"]))
    check("offerId 来自配置", data["offerId"] == "offer_test_001")
    check("env=1（沙箱）", data["env"] == 1)
    check("outTradeNo 8-32 字符", 8 <= len(out_trade_no) <= 32, str(len(out_trade_no)))

    # signData 内容校验（固定 8 字段，无多余字段）
    sd = json.loads(sign_data_str)
    check("signData 字段齐全", all(k in sd for k in
          ("offerId", "buyQuantity", "env", "currencyType", "productId", "goodsPrice", "outTradeNo", "attach")))
    check("signData 无多余字段（mode 在顶层）", set(sd.keys()) ==
          {"offerId", "buyQuantity", "env", "currencyType", "productId", "goodsPrice", "outTradeNo", "attach"})
    check("signData.currencyType=CNY", sd["currencyType"] == "CNY")
    check("signData.buyQuantity=1", sd["buyQuantity"] == 1)
    check("signData.productId 正确", sd["productId"] == "love_compatibility")
    check("signData.attach 透传 kind/id", sd["attach"] and "product" in sd["attach"] and "love_compatibility" in sd["attach"])

    # paySig 复算：hex(hmac_sha256(appKey, "requestVirtualPayment&" + signData))
    expect_pay_sig = hmac_sha256_hex(APP_KEY, f"requestVirtualPayment&{sign_data_str}")
    check("paySig 用 appKey 复算一致", data["paySig"] == expect_pay_sig,
          f"\n    got={data['paySig']}\n    exp={expect_pay_sig}")

    # signature 复算：hex(hmac_sha256(session_key, signData))
    expect_user_sig = hmac_sha256_hex(stored_key, sign_data_str)
    check("signature 用 session_key 复算一致", data["signature"] == expect_user_sig,
          f"\n    got={data['signature']}\n    exp={expect_user_sig}")

    print("=" * 62)
    print("测试 4：重复 outTradeNo 拒绝")
    print("=" * 62)
    fixed_no = "YLfixed00000000000000"
    with mock.patch("src.api.pay_midas._generate_out_trade_no", return_value=fixed_no):
        r1 = client.post("/api/pay/virtual/create", json={"product_id": "deep_report"}, headers=headers)
        r2 = client.post("/api/pay/virtual/create", json={"product_id": "deep_report"}, headers=headers)
    check("第一次创建成功", r1.status_code == 200, str(r1.status_code))
    check("重复 outTradeNo 被拒绝", r2.status_code == 500 and r2.json().get("detail", {}).get("code") == "order_create_failed",
          f"{r2.status_code} {r2.text[:200]}")

    print("=" * 62)
    print("测试 5：notify 验签与发货")
    print("=" * 62)

    def notify_body(o_no):
        return {
            "ToUserName": "gh_abc123",
            "FromUserName": "fake_openid_from_wechat",
            "CreateTime": 1723000000,
            "MsgType": "event",
            "Event": "xpay_goods_deliver_notify",
            "OpenId": "test_openid",
            "OutTradeNo": o_no,
            "Env": 1,
        }

    # 5.1 篡改签名 → 拒绝
    raw = json.dumps(notify_body(out_trade_no), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    bad_sig = hmac_sha256_hex(APP_KEY, raw.decode("utf-8") + "tampered")
    r = client.post("/api/pay/virtual/notify", content=raw,
                    headers={"X-WeChat-Signature": bad_sig, "Content-Type": "application/json"})
    check("篡改签名 → errcode=-1 拒绝发货", r.status_code == 200 and r.json().get("errcode") == -1,
          f"{r.status_code} {r.text[:200]}")

    # 5.2 正确签名 → 发货成功
    good_sig = hmac_sha256_hex(APP_KEY, raw.decode("utf-8"))
    r = client.post("/api/pay/virtual/notify", content=raw,
                    headers={"X-WeChat-Signature": good_sig, "Content-Type": "application/json"})
    check("正确签名 → errcode=0", r.status_code == 200 and r.json().get("errcode") == 0, f"{r.status_code} {r.text[:200]}")
    payments = member_dao.get_user_payments(uid)
    love_payment = next((p for p in payments if p["plan"] == "love_compatibility"), None)
    check("payments 已置 paid", love_payment and love_payment["status"] == "paid",
          str(love_payment if love_payment else payments))
    check("payment_method=midas", love_payment and love_payment["payment_method"] == "midas",
          str(love_payment if love_payment else payments))

    # 5.3 重复通知幂等
    r = client.post("/api/pay/virtual/notify", content=raw,
                    headers={"X-WeChat-Signature": good_sig, "Content-Type": "application/json"})
    check("重复通知幂等 → errcode=0", r.status_code == 200 and r.json().get("errcode") == 0,
          f"{r.status_code} {r.text[:200]}")

    # 5.4 sha256= 前缀兼容
    r = client.post("/api/pay/virtual/notify", content=raw,
                    headers={"X-WeChat-Signature": f"sha256={good_sig}", "Content-Type": "application/json"})
    check("X-WeChat-Signature 带 sha256= 前缀兼容", r.status_code == 200 and r.json().get("errcode") == 0,
          f"{r.status_code} {r.text[:200]}")

    # 5.5 pay_event_sig 兜底验签
    body_pes = notify_body(out_trade_no)
    body_pes["pay_event_sig"] = "placeholder"
    payload_str = json.dumps({k: v for k, v in body_pes.items() if k != "pay_event_sig"},
                             ensure_ascii=False, separators=(",", ":"))
    body_pes["pay_event_sig"] = hmac_sha256_hex(APP_KEY, f"xpay_goods_deliver_notify&{payload_str}")
    raw_pes = json.dumps(body_pes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    r = client.post("/api/pay/virtual/notify", content=raw_pes, headers={"Content-Type": "application/json"})
    check("pay_event_sig 兜底验签通过", r.status_code == 200 and r.json().get("errcode") == 0,
          f"{r.status_code} {r.text[:200]}")

    # 5.6 无任何签名 → 拒绝
    r = client.post("/api/pay/virtual/notify", content=raw, headers={"Content-Type": "application/json"})
    check("无签名 → 拒绝", r.status_code == 200 and r.json().get("errcode") == -1, f"{r.status_code} {r.text[:200]}")

    print("=" * 62)
    print("测试 6：订阅套餐（monthly）→ 发货激活会员")
    print("=" * 62)
    r = client.post("/api/pay/virtual/create", json={"product_id": "monthly"}, headers=headers)
    check("订阅创建 200（goodsPrice=6800）", r.status_code == 200 and r.json().get("goodsPrice") == 6800,
          f"{r.status_code} {r.text[:200]}")
    sub_no = r.json()["outTradeNo"]
    sub_raw = json.dumps(notify_body(sub_no), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sub_sig = hmac_sha256_hex(APP_KEY, sub_raw.decode("utf-8"))
    r = client.post("/api/pay/virtual/notify", content=sub_raw,
                    headers={"X-WeChat-Signature": sub_sig, "Content-Type": "application/json"})
    check("订阅回调 errcode=0", r.status_code == 200 and r.json().get("errcode") == 0, f"{r.text[:200]}")
    membership = member_dao.get_membership(uid)
    check("会员已开通（pro 专业版）", membership.get("plan") == "pro", str(membership.get("plan")))

    print("=" * 62)
    print("测试 7：status 查询（owner 校验）")
    print("=" * 62)
    r = client.get(f"/api/pay/virtual/status?outTradeNo={out_trade_no}", headers=headers)
    check("owner 查询 200 且 status=paid", r.status_code == 200 and r.json().get("status") == "paid",
          f"{r.status_code} {r.text[:200]}")
    r = client.get(f"/api/pay/virtual/status?outTradeNo={out_trade_no}", headers=other_headers)
    check("他人查询 → 403", r.status_code == 403, str(r.status_code))
    r = client.get("/api/pay/virtual/status?outTradeNo=YLnonexist000000000000")
    check("未登录查询 → 401", r.status_code == 401, str(r.status_code))
    r = client.get("/api/pay/virtual/status?outTradeNo=__bad__", headers=headers)
    check("非法订单号（过短）→ 422 拒绝", r.status_code == 422, str(r.status_code))
    r = client.get("/api/pay/virtual/status?outTradeNo=_bad_order_123456789", headers=headers)
    check("非法订单号（下划线开头）→ 400", r.status_code == 400, str(r.status_code))

    print("=" * 62)
    print("测试 8：WECHAT_PAY_ENABLED=false → 保留 mock 路径")
    print("=" * 62)
    with mock.patch.dict(os.environ, {"WECHAT_PAY_ENABLED": "false"}):
        r = client.post("/api/pay/virtual/create", json={"product_id": "love_compatibility"}, headers=headers)
    check("未启用 → 400 virtual_pay_not_enabled",
          r.status_code == 400 and r.json().get("detail", {}).get("code") == "virtual_pay_not_enabled",
          f"{r.status_code} {r.text[:200]}")

    print("=" * 62)
    print("测试 9：未知商品 / session_key 缺失")
    print("=" * 62)
    r = client.post("/api/pay/virtual/create", json={"product_id": "no_such_product"}, headers=headers)
    check("未知商品 → 404", r.status_code == 404, str(r.status_code))
    # 新用户直接创建订单（未登录过、无 session_key）
    r = client.post("/api/pay/virtual/create", json={"product_id": "love_compatibility"}, headers=other_headers)
    check("无 session_key → 400 session_key_missing",
          r.status_code == 400 and r.json().get("detail", {}).get("code") == "session_key_missing",
          f"{r.status_code} {r.text[:200]}")

    print("=" * 62)
    print(f"结果：通过 {PASS} 项，失败 {FAIL} 项")
    print("=" * 62)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
