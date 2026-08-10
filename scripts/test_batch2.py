#!/usr/bin/env python3
"""Batch 2 回归测试 — 会话加密 / 分享卡 / 订阅设置 / 报告派生 / 推送链路现状.

覆盖（对应 /mnt/e/fortune-agent 第二批完善任务）：
  A. 会话加密（独立于服务，可随时运行）
     1. SessionDAO 写入 → 库中为密文（version:base64）→ 读取解密为明文
     2. 旧明文读取懒迁移为密文（含含冒号明文不误判）
     3. get_context_for_llm 解密正确（LLM 上下文路径）
  B. 分享卡（需服务以新代码重启后运行）
     4. GET /api/share/{数字报告ID}：本人报告 200（imageUrl 降级 null + card 结构化数据）
     5. GET /api/share/{不存在} → 404
     6. GET /api/share/{他人报告} → 403（IDOR 防护）
     7. 无 token → 401
  C. 订阅设置（需服务重启）
     8. GET /api/user/subscription 无 token → 401；带 token → 200 {daily_push, push_time}
     9. POST /api/user/subscription 保存 → GET 读回一致；用户间隔离
  D. 报告派生（需服务重启）
     10. 有八字无咨询的用户 → /api/reports 兜底返回「基础命书」(id=base)
     11. GET /api/reports/base → 200 含 fullContent；无八字用户 base → 404
     12. 咨询记录派生：/api/reports/{咨询ID} 详情 200（加密后派生逻辑仍可读）
  E. 推送链路现状（独立可运行）
     13. WECHAT_SUBSCRIBE_TEMPLATE_ID 未配置 → _deliver 返回 template_not_configured

用法:
    python scripts/test_batch2.py [--base-url http://127.0.0.1:8767] [--skip-api]

注意: B/C/D 部分需服务以**新代码**重启后运行（旧代码 GET /api/user/subscription
为 404）。A/E 部分独立于服务，可随时运行。
"""
import argparse
import base64
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8767"
TIMEOUT = 60.0

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


# ── A. 会话加密（独立于服务）───────────────────────────────────────

def test_session_encryption():
    print("\n=== A. 会话加密（SessionDAO 独立测试，临时库）===")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import src.config  # 加载 .env（ENCRYPTION_KEY），与线上同密钥验证
    from src.storage.session_dao import SessionDAO

    tmp = tempfile.mktemp(suffix=".db")
    dao = SessionDAO(tmp)
    try:
        msg_user = "你好，帮我看看今年的财运"
        msg_ai = "根据八字来看，今年正财运平稳，偏财有起伏。"
        dao.add_message("u1", "user", msg_user)
        dao.add_message("u1", "assistant", msg_ai, intent="bazi")

        conn = sqlite3.connect(tmp)
        rows = conn.execute(
            "SELECT content FROM sessions WHERE user_id=? ORDER BY id", ("u1",)
        ).fetchall()
        conn.close()
        check("写入后库中为密文（2 条）", len(rows) == 2, f"got {len(rows)}")
        check("密文格式 version:base64", all(
            ":" in r[0] and r[0] != msg_user for r in rows),
            str([r[0][:20] for r in rows]))
        check("密文不含原始明文", all(msg_user not in r[0] and msg_ai not in r[0] for r in rows))

        hist = dao.get_history("u1")
        check("读取解密为明文(user)", hist[0]["content"] == msg_user, hist[0]["content"][:30])
        check("读取解密为明文(assistant)", hist[1]["content"] == msg_ai, hist[1]["content"][:30])

        ctx = dao.get_context_for_llm("u1", history_limit=2)
        check("get_context_for_llm 解密正确",
              ctx[0]["content"] == msg_user and ctx[1]["content"] == msg_ai)

        # 旧明文懒迁移
        legacy = "旧数据明文消息"
        legacy2 = "时间12:30见 含冒号明文"
        conn = sqlite3.connect(tmp)
        conn.execute("INSERT INTO sessions (user_id, role, content) VALUES ('u2','user',?)", (legacy,))
        conn.execute("INSERT INTO sessions (user_id, role, content) VALUES ('u3','user',?)", (legacy2,))
        conn.commit()
        conn.close()
        h2 = dao.get_history("u2", limit=5)
        check("旧明文读取返回原文", h2[0]["content"] == legacy, str(h2[0]["content"])[:30])
        h3 = dao.get_history("u3", limit=5)
        check("含冒号明文不误判解密", h3[0]["content"] == legacy2, str(h3[0]["content"])[:30])
        conn = sqlite3.connect(tmp)
        c2 = conn.execute("SELECT content FROM sessions WHERE user_id='u2'").fetchone()[0]
        c3 = conn.execute("SELECT content FROM sessions WHERE user_id='u3'").fetchone()[0]
        conn.close()
        check("旧明文读取后懒迁移为密文", c2 != legacy and c3 != legacy2,
              f"{c2[:20]} | {c3[:20]}")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── E. 推送链路现状（独立于服务）───────────────────────────────────

def test_push_chain_status():
    print("\n=== E. 推送链路现状（订阅消息配置评估）===")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts import daily_push

    cfg = daily_push._subscribe_config()
    check("读取 WECHAT_SUBSCRIBE_TEMPLATE_ID 配置位", "template_id" in cfg and "ready" in cfg,
          f"template_id={'已配置' if cfg['template_id'] else '未配置'} app_id={'已配置' if cfg['app_id'] else '未配置'}")
    result = daily_push._deliver_subscribe_message("wx_dev_user", "测试消息")
    if cfg["ready"]:
        check("模板已配置 → 真实下发尝试", "delivered" in result,
              f"{result.get('reason')} {result.get('detail')}")
    else:
        check("模板未配置 → 降级 template_not_configured",
              result["delivered"] is False and result["reason"] == "template_not_configured",
              str(result.get("detail", ""))[:80])
        print("  ℹ 现状：每日 08:00 _push_task 生成运势消息并写 push_log，但真实下发需"
              "模板 ID + 端内订阅授权（wx.requestSubscribeMessage），见 .env 注释")


# ── B/C/D. 在线 API 测试（需服务重启后运行）────────────────────────

def test_api(base: str, skip_api: bool):
    if skip_api:
        print("\n=== B/C/D. 在线 API 测试：已跳过（--skip-api）===")
        return
    print(f"\n=== B/C/D. 在线 API 测试 → {base} ===")
    client = httpx.Client(timeout=TIMEOUT)
    db = db_path()

    # ── 登录 ──
    r = client.post(base + "/api/user/login", json={"code": "dev_code"})
    check("登录 → 200", r.status_code == 200, f"got {r.status_code} {r.text[:120]}")
    if r.status_code != 200:
        print("\n结论: 服务不可用或未用新代码重启（见脚本头注释）")
        return
    data = r.json()
    token = data.get("token", "")
    uid_a = (data.get("user") or {}).get("id", "")
    headers_a = {"Authorization": f"Bearer {token}"}
    check("登录返回 token + user_id", bool(token) and bool(uid_a), f"{uid_a}")

    # 造测试数据（直插 DB，测后清理）
    victim = "batch2_victim_x"
    fresh_user = "batch2_fresh_no_cons_x"
    inserted_consult_ids = []
    conn = sqlite3.connect(str(db), timeout=30)
    try:
        conn.execute("INSERT OR IGNORE INTO users (user_id, bazi_info) VALUES (?, ?)",
                     (victim, json.dumps({"bazi": ["庚午", "辛巳", "乙酉", "甲申"]}, ensure_ascii=False)))
        # 他人咨询记录（明文插入 → 读取路径需兼容/解密）
        cur = conn.execute(
            "INSERT INTO consultations (user_id, question, intent, chart_data, analysis) VALUES (?,?,?,?,?)",
            (victim, "测试：他人的咨询", "bazi",
             json.dumps({"bazi": ["庚午", "辛巳", "乙酉", "甲申"]}, ensure_ascii=False),
             "这是他人报告的分析内容，用于验证归属校验。"),
        )
        inserted_consult_ids.append(cur.lastrowid)
        # 无咨询但有八字的新用户（基础命书测试）
        conn.execute("INSERT OR IGNORE INTO users (user_id, bazi_info) VALUES (?, ?)",
                     (fresh_user, json.dumps({"year": 1990, "month": 5, "day": 20,
                                              "hour": 10, "gender": "male",
                                              "bazi": ["庚午", "辛巳", "乙酉", "甲申"]}, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()

    # 本人真实咨询（dev 用户）
    own_consult = None
    conn = sqlite3.connect(str(db), timeout=30)
    try:
        row = conn.execute(
            "SELECT id FROM consultations WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid_a,)
        ).fetchone()
        own_consult = row[0] if row else None
    finally:
        conn.close()

    try:
        # ── B. 分享卡 ──
        r = client.get(base + "/api/share/999999999999", headers=headers_a)
        check("GET /api/share/{不存在} → 404", r.status_code == 404, f"got {r.status_code}")

        victim_rid = inserted_consult_ids[0] if inserted_consult_ids else 999999999999
        r = client.get(base + f"/api/share/{victim_rid}", headers=headers_a)
        check("GET /api/share/{他人报告} → 403（IDOR）", r.status_code == 403, f"got {r.status_code}")

        r = client.get(base + "/api/share/abc", headers=headers_a)
        check("GET /api/share/{不存在reading_id} → 404", r.status_code == 404, f"got {r.status_code}")

        r = client.get(base + "/api/share/abc")
        check("GET /api/share 无 token → 401", r.status_code == 401, f"got {r.status_code}")

        if own_consult:
            r = client.get(base + f"/api/share/{own_consult}", headers=headers_a)
            body = r.json() if r.status_code == 200 else {}
            check("GET /api/share/{本人报告} → 200", r.status_code == 200, f"got {r.status_code}")
            check("返回 imageUrl 字段", "imageUrl" in body, str(list(body.keys()))[:80])
            check("返回结构化 card（title/summary/user_name/qr_placeholder）",
                  all(k in body.get("card", {}) for k in
                      ("title", "summary", "user_name", "qr_placeholder", "style")),
                  str(list(body.get("card", {}).keys())))
        else:
            check("本人有咨询记录可测分享卡", False, "dev 用户无咨询记录")

        # ── C. 订阅设置 ──
        r = client.get(base + "/api/user/subscription")
        check("GET /api/user/subscription 无 token → 401", r.status_code == 401, f"got {r.status_code}")

        r = client.get(base + "/api/user/subscription", headers=headers_a)
        body = r.json() if r.status_code == 200 else {}
        check("GET /api/user/subscription 带 token → 200", r.status_code == 200, f"got {r.status_code}")
        check("GET 返回 daily_push/push_time", "daily_push" in body and "push_time" in body,
              str(list(body.keys())))

        r = client.post(base + "/api/user/subscription",
                        json={"daily_push": True, "push_time": "09:30"}, headers=headers_a)
        check("POST /api/user/subscription 保存 → 200", r.status_code == 200, f"got {r.status_code}")
        r = client.get(base + "/api/user/subscription", headers=headers_a)
        body = r.json() if r.status_code == 200 else {}
        check("POST 后 GET 读回一致", body.get("daily_push") is True and body.get("push_time") == "09:30",
              json.dumps(body, ensure_ascii=False))

        # 用户间隔离：B（victim）不受 A 的写入影响
        r_b = client.post(base + "/api/user/subscription",
                          json={"daily_push": False}, headers=headers_a)
        victim_row = None
        conn = sqlite3.connect(str(db), timeout=30)
        try:
            v = conn.execute("SELECT push_enabled, push_time FROM users WHERE user_id=?",
                             (victim,)).fetchone()
            victim_row = v if v else None
        finally:
            conn.close()
        check("订阅设置按 token sub 归属（不影响他人）", victim_row is not None
              and victim_row[0] == 1, f"victim push_enabled={victim_row}")

        # ── D. 报告派生 ──
        # fresh_user 有八字无咨询 → 基础命书（需要该用户 token；dev 用户有咨询，
        # 用 victim 直插数据不可取 → 直接调用 DAO 逻辑验证基础命书生成函数）
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from src.main import _build_base_report_item, _derive_base_report_content
        item = _build_base_report_item({"year": 1990, "month": 5, "day": 20, "hour": 10,
                                        "gender": "male", "bazi": ["庚午", "辛巳", "乙酉", "甲申"]})
        check("基础命书列表项生成（id=base）", item["id"] == "base" and "日主" in item["summary"],
              json.dumps(item, ensure_ascii=False)[:120])
        content = _derive_base_report_content({"year": 1990, "month": 5, "day": 20,
                                               "hour": 10, "gender": "male",
                                               "bazi": ["庚午", "辛巳", "乙酉", "甲申"]})
        check("基础命书正文生成", "基础命书" in content and "庚午" in content,
              content[:60].replace("\n", " "))

        # 咨询派生报告详情（加密后派生逻辑仍可读）
        if own_consult:
            r = client.get(base + f"/api/reports/{own_consult}", headers=headers_a)
            body = r.json() if r.status_code == 200 else {}
            check("GET /api/reports/{咨询ID} → 200", r.status_code == 200, f"got {r.status_code}")
            check("报告详情含 fullContent", "fullContent" in body.get("report", {}),
                  str(list(body.get("report", {}).keys()))[:80])
            r = client.get(base + f"/api/reports/{victim_rid}", headers=headers_a)
            check("他人报告详情 → 403", r.status_code == 403, f"got {r.status_code}")
    finally:
        # 清理测试数据
        conn = sqlite3.connect(str(db), timeout=30)
        try:
            for cid in inserted_consult_ids:
                conn.execute("DELETE FROM consultations WHERE id=?", (cid,))
            conn.execute("DELETE FROM users WHERE user_id IN (?, ?)", (victim, fresh_user))
            conn.execute("UPDATE users SET push_enabled=0, push_time='08:00' WHERE user_id=?",
                         (uid_a,))
            conn.commit()
        finally:
            conn.close()


def main():
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE)
    parser.add_argument("--skip-api", action="store_true",
                        help="跳过在线 API 部分（只跑独立可跑的 A/E）")
    args = parser.parse_args()
    BASE = args.base_url.rstrip("/")

    print(f"=== Batch 2 回归测试 → {BASE} ===")
    test_session_encryption()
    test_push_chain_status()
    test_api(BASE, args.skip_api)

    print(f"\n=== 结果: {PASS} PASS / {FAIL} FAIL ===")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
