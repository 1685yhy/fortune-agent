#!/usr/bin/env python3
"""鉴权回归测试 — 验证红线修复（无 token 401 / 登录闭环 / IDOR 403）。

用法:
    python scripts/test_auth.py [--base-url http://127.0.0.1:8767]

注意: 需在服务以**新代码**重启后运行（旧代码无鉴权，本测试会失败）。
"""
import argparse
import sys
import httpx

BASE = "http://127.0.0.1:8767"
TIMEOUT = 30.0

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    mark = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def main():
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE)
    args = parser.parse_args()
    BASE = args.base_url.rstrip("/")

    client = httpx.Client(timeout=TIMEOUT)

    print(f"=== 鉴权测试 → {BASE} ===\n")

    # ── 1. 无 token 访问业务接口 → 401 ──────────────────────────
    for path in ["/api/reports", "/api/user/profile", "/api/calendar/today",
                 "/api/membership/nobody", "/api/user/export/nobody",
                 "/api/push-settings/nobody"]:
        r = client.get(BASE + path)
        check(f"无 token GET {path} → 401", r.status_code == 401, f"got {r.status_code}")

    r = client.post(BASE + "/api/chat", json={"message": "你好"})
    check("无 token POST /api/chat → 401", r.status_code == 401, f"got {r.status_code}")

    r = client.get(BASE + "/api/admin/stats")
    check("无 admin key GET /api/admin/stats → 403", r.status_code == 403, f"got {r.status_code}")

    # ── 2. 登录闭环：稳定 user_id + token ────────────────────────
    r1 = client.post(BASE + "/api/user/login", json={"code": "dev_code"})
    check("登录1 → 200", r1.status_code == 200, f"got {r1.status_code} {r1.text[:200]}")
    if r1.status_code != 200:
        print("\n结论: 服务不可用或未用新代码重启（见脚本头注释）")
        sys.exit(1)
    data1 = r1.json()
    token_a = data1.get("token", "")
    uid_a = (data1.get("user") or {}).get("id", "")
    check("登录1 返回 token", bool(token_a))
    check("登录1 返回 user.id", bool(uid_a))

    r2 = client.post(BASE + "/api/user/login", json={"code": "dev_code"})
    data2 = r2.json()
    uid_a2 = (data2.get("user") or {}).get("id", "")
    check("同一用户重复登录 → 同一 user_id（登录闭环）", uid_a == uid_a2,
          f"{uid_a} vs {uid_a2}")

    headers_a = {"Authorization": f"Bearer {token_a}"}

    # ── 3. 带 token 访问 → 200 ───────────────────────────────────
    for path in ["/api/reports", "/api/user/profile", "/api/calendar/today"]:
        r = client.get(BASE + path, headers=headers_a)
        check(f"带 token GET {path} → 200", r.status_code == 200, f"got {r.status_code}")

    r = client.get(BASE + f"/api/user/export/{uid_a}", headers=headers_a)
    check(f"带 token 导出自己数据 → 200", r.status_code == 200, f"got {r.status_code}")

    # ── 4. IDOR：A 的 token 访问 B 的 user_id → 403 ──────────────
    victim = "wx_attacker_target_" + "x" * 8
    for path in [f"/api/user/export/{victim}",
                 f"/api/user/{victim}/history",
                 f"/api/user/{victim}/accuracy",
                 f"/api/membership/{victim}",
                 f"/api/push-settings/{victim}",
                 f"/api/dashboard/{victim}",
                 f"/api/share-card/{victim}"]:
        r = client.get(BASE + path, headers=headers_a)
        check(f"A 的 token 访问 B 的 {path} → 403", r.status_code == 403, f"got {r.status_code}")

    r = client.post(BASE + f"/api/membership/{victim}/upgrade?plan=basic", headers=headers_a)
    check(f"A 的 token 给 B 升级会员 → 403", r.status_code == 403, f"got {r.status_code}")

    r = client.delete(BASE + f"/api/user/data/{victim}", headers=headers_a)
    check(f"A 的 token 删除 B 数据 → 403", r.status_code == 403, f"got {r.status_code}")

    # ── 5. chat 冒用：body user_id 被忽略，记录挂在自己名下 ──────
    r = client.post(BASE + "/api/chat",
                    json={"message": "你好", "user_id": victim},
                    headers=headers_a)
    check("POST /api/chat 带 token → 200（冒用 user_id 被忽略）",
          r.status_code == 200, f"got {r.status_code} {r.text[:120]}")

    # ── 6. 伪造/篡改 token → 401 ─────────────────────────────────
    bad = token_a[:-2] + ("ab" if token_a[-2:] != "ab" else "cd")
    r = client.get(BASE + "/api/reports", headers={"Authorization": f"Bearer {bad}"})
    check("篡改 token → 401", r.status_code == 401, f"got {r.status_code}")

    print(f"\n=== 结果: {PASS} PASS / {FAIL} FAIL ===")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
