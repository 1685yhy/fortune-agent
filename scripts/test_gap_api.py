#!/usr/bin/env python3
"""A 类缺口接口回归测试 — 6 个新接口 + 四术页契约 + 订单/会员归属.

覆盖（对应 FUNCTION_GAP_AUDIT.md A1/A2 + 四术页契约）：
  1. 无 token → 401（6 个新接口）
  2. 登录 → token（user_id = JWT sub）
  3. POST /api/love/compatibility：paid=false 摘要版（付费墙锁定）、
     paid=true 完整版（四大章节 + 排盘 charts）、myBirth/taBirth 简化模式
  4. POST /api/pay/create → {payment, orderId}，订单落库（/api/user/orders 可见，
     /api/user/purchase/{id} 为 purchased）
  5. POST /api/pay/subscribe → 开通会员；GET /api/user/member →
     {isMember, expireDate, benefits}
  6. 归属校验：他人订单不可见（直插 DB 伪造他人订单 + 隔离验证）
  7. 四术页契约：/api/hehun(person1/person2)、/api/qimen(date/time)、
     /api/xingming(givenName)、/api/xuetang(topics/lesson) 响应结构

用法:
    python scripts/test_gap_api.py [--base-url http://127.0.0.1:8767]

注意: 需在服务以**新代码**重启后运行（旧代码 6 个新接口为 404）。
"""
import argparse
import base64
import json
import os
import sqlite3
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8767"
TIMEOUT = 30.0

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))
    return cond


def token_sub(token: str) -> str:
    """从 JWT payload 解出 sub（user_id）。"""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("sub", "")
    except Exception:
        return ""


def db_path() -> Path:
    """与后端一致的数据文件路径（读 src.config，避免硬编码）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.config import load_settings
    return Path(load_settings().db_path)


def main():
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE)
    args = parser.parse_args()
    BASE = args.base_url.rstrip("/")

    client = httpx.Client(timeout=TIMEOUT)
    print(f"=== A 类缺口接口回归测试 → {BASE} ===\n")

    # ── 1. 无 token → 401 ────────────────────────────────────────
    no_token_requests = [
        ("POST", "/api/love/compatibility", {"birthYear1": 1992}),
        ("POST", "/api/pay/create", {"product_id": "love_compatibility"}),
        ("POST", "/api/pay/subscribe", {"plan_id": "monthly"}),
        ("GET", "/api/user/member", None),
        ("GET", "/api/user/orders", None),
        ("GET", "/api/user/purchase/love_compatibility", None),
    ]
    for method, path, body in no_token_requests:
        r = client.request(method, BASE + path, json=body)
        check(f"无 token {method} {path} → 401", r.status_code == 401, f"got {r.status_code}")

    # ── 2. 登录 ──────────────────────────────────────────────────
    r = client.post(BASE + "/api/user/login", json={"code": "dev_code"})
    check("登录 → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        print("\n结论: 服务不可用或未用新代码重启")
        sys.exit(1)
    login = r.json()
    token = login["token"]
    sub = token_sub(token)
    check("token sub == login user.id", sub == login["user"]["id"], f"sub={sub}")
    auth = {"Authorization": f"Bearer {token}"}
    print(f"   用户: {sub}  dev_mode={login.get('dev_mode')}\n")

    # ── 3. 感情合盘 ──────────────────────────────────────────────
    love_body = {
        "birthYear1": 1992, "birthMonth1": 8, "birthDay1": 15, "birthHour1": 0, "gender1": 1,
        "birthYear2": 1990, "birthMonth2": 5, "birthDay2": 20, "birthHour2": 11, "gender2": 0,
        "paid": False,
    }
    r = client.post(BASE + "/api/love/compatibility", json=love_body, headers=auth)
    check("合盘 paid=false → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        check("合盘 score 为 0-100 整数", isinstance(d.get("score"), int) and 0 <= d["score"] <= 100, str(d.get("score")))
        check("合盘 match_score == score", d.get("match_score") == d.get("score"))
        check("合盘 levelLabel 非空", bool(d.get("levelLabel")), str(d.get("levelLabel")))
        check("合盘 summary 非空（真实规则文案）", bool(d.get("summary")))
        check("合盘 strengths/warnings/advice 为列表", all(isinstance(d.get(k), list) for k in ("strengths", "warnings", "advice")))
        check("摘要版 paywall.locked=true", d.get("paywall", {}).get("locked") is True)
        check("摘要版 personality 为锁定提示", "付费" in (d.get("personality") or ""))
        check("摘要版 charts.user1.bazi 为真实排盘", bool(d.get("charts", {}).get("user1", {}).get("bazi")))
        free_bazi = d["charts"]["user1"]["bazi"]

    love_body["paid"] = True
    r = client.post(BASE + "/api/love/compatibility", json=love_body, headers=auth)
    check("合盘 paid=true → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        d = r.json()
        check("完整版 paywall.locked=false", d.get("paywall", {}).get("locked") is False)
        for key in ("personality", "fate", "marriage", "children"):
            check(f"完整版 {key} 为真实章节文本", bool(d.get(key)) and "付费内容" not in (d.get(key) or ""))
        check("完整版 quote 非空", bool(d.get("quote")))
        check("完整版 advice_analysis 非空", bool(d.get("advice_analysis")))
        check("完整版与摘要版排盘一致（同一对输入）", d["charts"]["user1"]["bazi"] == free_bazi)

    # 简化模式（love 页 v6: myBirth/taBirth 字符串）
    r = client.post(BASE + "/api/love/compatibility",
                    json={"myBirth": "1992-08-15", "taBirth": "1990-05-20", "paid": False},
                    headers=auth)
    check("合盘 myBirth/taBirth 模式 → 200 且含 match_score", r.status_code == 200 and "match_score" in r.json(), f"got {r.status_code}")

    # ── 4. 支付 / 订单 / 购买检查 ─────────────────────────────────
    r = client.post(BASE + "/api/pay/create", json={"product_id": "love_compatibility"}, headers=auth)
    check("POST /api/pay/create → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    order = None
    if r.status_code == 200:
        d = r.json()
        order_id = d.get("orderId")
        payment = d.get("payment")
        check("create 返回 orderId", bool(order_id), str(order_id))
        check("create 返回 payment 参数", isinstance(payment, dict) and "mock" in payment)
        order = {"orderId": order_id, "mock": payment.get("mock", False)}

    r = client.get(BASE + "/api/user/orders", headers=auth)
    check("GET /api/user/orders → 200", r.status_code == 200, f"got {r.status_code}")
    orders = []
    if r.status_code == 200:
        orders = r.json().get("orders", [])
        check("orders 为列表", isinstance(orders, list))
        if order:
            ids = [o.get("orderId") for o in orders]
            check("刚创建的订单出现在本人订单列表", order["orderId"] in ids, f"orders={ids[:5]}")
        else:
            check("订单列表非空", len(orders) > 0)

    if order and order["mock"]:
        r = client.get(BASE + "/api/user/purchase/love_compatibility", headers=auth)
        check("GET /api/user/purchase/{id} → 200", r.status_code == 200, f"got {r.status_code}")
        if r.status_code == 200:
            check("mock 支付后 purchased=true", r.json().get("purchased") is True, str(r.json()))

    r = client.post(BASE + "/api/pay/create", json={"product_id": "not_exist_product"}, headers=auth)
    check("create 未知产品 → 404", r.status_code == 404, f"got {r.status_code}")

    # ── 5. 会员订阅 ──────────────────────────────────────────────
    r = client.post(BASE + "/api/pay/subscribe", json={"plan_id": "monthly"}, headers=auth)
    check("POST /api/pay/subscribe → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        check("subscribe 返回 orderId", bool(d.get("orderId")))
        check("subscribe 返回 payment", isinstance(d.get("payment"), dict))

    r = client.get(BASE + "/api/user/member", headers=auth)
    check("GET /api/user/member → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        d = r.json()
        check("member 含 isMember 字段", "isMember" in d, str(d))
        check("member 含 expireDate 字段", "expireDate" in d)
        check("member 含 benefits 列表", isinstance(d.get("benefits"), list))
        if order and order["mock"]:
            check("mock 订阅后 isMember=true", d.get("isMember") is True, f"plan={d.get('plan')}")
            check("mock 订阅后 expireDate 非空", bool(d.get("expireDate")), str(d.get("expireDate")))
            check("mock 订阅后 benefits 非空", len(d.get("benefits", [])) > 0)

    r = client.post(BASE + "/api/pay/subscribe", json={"plan_id": "yearly"}, headers=auth)
    check("subscribe 未知套餐 → 404", r.status_code == 404, f"got {r.status_code}")

    # ── 6. 归属校验（他人订单不可见）──────────────────────────────
    _foreign_payment_id = None
    try:
        conn = sqlite3.connect(str(db_path()))
        foreign_user = "wx_other_user_gap_test"
        cur = conn.execute(
            "INSERT INTO payments (user_id, amount, plan, status, payment_method) VALUES (?, 999.9, 'love_compatibility', 'paid', 'test')",
            (foreign_user,),
        )
        _foreign_payment_id = cur.lastrowid
        conn.commit()
        conn.close()

        r = client.get(BASE + "/api/user/orders", headers=auth)
        order_ids = [o.get("orderId") for o in r.json().get("orders", [])]
        check("他人订单不出现在本人列表（归属隔离）",
              str(_foreign_payment_id) not in order_ids,
              f"foreign_id={_foreign_payment_id}")

        r = client.get(BASE + "/api/user/purchase/love_compatibility", headers=auth)
        # 他人 purchased 的同一产品不应因他人的订单变为 purchased（仅当本人未买时）
        # 本测试早前已 mock 购买，因此这里仅验证接口不越权返回他人订单明细
        check("purchase 接口正常（不抛 500）", r.status_code == 200, f"got {r.status_code}")
    finally:
        if _foreign_payment_id:
            conn = sqlite3.connect(str(db_path()))
            conn.execute("DELETE FROM payments WHERE id=?", (_foreign_payment_id,))
            conn.commit()
            conn.close()

    # ── 7. 四术页契约 ────────────────────────────────────────────
    # 7.1 合婚：小程序 person1/person2 + birthYear 契约
    hehun_body = {
        "person1": {"birthYear": 1992, "birthMonth": 8, "birthDay": 15, "birthHour": 0, "gender": "male", "city": "北京"},
        "person2": {"birthYear": 1990, "birthMonth": 5, "birthDay": 20, "birthHour": 11, "gender": "female", "city": "上海"},
    }
    r = client.post(BASE + "/api/hehun", json=hehun_body, headers=auth)
    check("hehun person1/person2 契约 → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        check("hehun 响应含 score（前端读 result.score）", isinstance(d.get("score"), int), str(d.get("score")))
        check("hehun score == total_score", d.get("score") == d.get("total_score"))
        check("hehun 含 wuxing/shengxiao/rizhu.score", all(d.get(k, {}).get("score") is not None for k in ("wuxing", "shengxiao", "rizhu")))
        check("hehun advice 为列表", isinstance(d.get("advice"), list))

    # 7.2 奇门：date/time 字符串契约
    qimen_body = {"date": "2026-08-07", "time": "09:30", "city": "北京", "question": "近期工作如何"}
    r = client.post(BASE + "/api/qimen", json=qimen_body, headers=auth)
    check("qimen date/time 契约 → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        check("qimen 响应含 palaces 数组（前端读 res.palaces）", isinstance(d.get("palaces"), list) and len(d["palaces"]) == 9,
              f"len={len(d.get('palaces', []))}")
        p0 = d["palaces"][0]
        check("qimen palace 含 bamen/bashen/jiuxing 字段", all(k in p0 for k in ("bamen", "bashen", "jiuxing", "tianpan", "dipan")))

    # 7.3 姓名：givenName/gender('male') 契约
    xingming_body = {"surname": "李", "givenName": "明远", "gender": "male"}
    r = client.post(BASE + "/api/xingming", json=xingming_body, headers=auth)
    check("xingming givenName 契约 → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        check("xingming 响应含 wuge/sancai", "wuge" in d and "sancai" in d)

    # 7.4 学堂：topics/lesson 契约
    r = client.get(BASE + "/api/xuetang/topics", headers=auth)
    check("xuetang topics → 200", r.status_code == 200, f"got {r.status_code}")
    topic_id = None
    if r.status_code == 200:
        d = r.json()
        topics = d.get("topics", [])
        check("xuetang topics 为数组（前端读 res.topics）", isinstance(topics, list) and len(topics) > 0, f"len={len(topics)}")
        if topics:
            t0 = topics[0]
            check("topic 含 id/name/description/lessonCount", all(k in t0 for k in ("id", "name", "description", "lessonCount")), str(t0))
            topic_id = t0["id"]
        check("xuetang 兼容 curriculum 结构", isinstance(d.get("curriculum"), list))

    if topic_id:
        r = client.get(BASE + f"/api/xuetang/lesson?topic={topic_id}", headers=auth)
        check("xuetang lesson → 200", r.status_code == 200, f"got {r.status_code} {r.text[:150]}")
        if r.status_code == 200:
            lesson = r.json().get("lesson")
            check("lesson 含 title/content（前端读 res.lesson）", lesson and lesson.get("title") and lesson.get("content"), str(lesson)[:100] if lesson else "no lesson")

    # ── 汇总 ─────────────────────────────────────────────────────
    print(f"\n=== 结果: {PASS} 通过 / {FAIL} 失败 ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
