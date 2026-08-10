#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2 后端测试（方案 v5 · 2026-08-09）— 多人档案 / 账号注销 / 记忆升级 / 兼容迁移
============================================================================

  A 组（无网络 · 单元级）：
    A1  多人档案 persons CRUD（创建/列表/更新/删除/设默认/首个自动默认/归属校验）
    A2  兼容迁移：users.bazi_info 旧单档案 → 默认 person（首次访问自动迁移）
    A3  账号注销：软删 status/cancelled_at + 90 天清理（persons/sessions/consultations/画像）
    A4  L2 事实条目：去重（同 type+content 不重复）/ 加密落盘 / TTL / 删除 / 清空
    A5  演化链：时间线 append / cap 5 / ≥2 条注入"过往咨询演变"提示
    A6  API 契约（TestClient + 临时 DB + 假 token）：/api/persons*、/api/user/cancel、
        /api/memory*、/api/user/bazi 写默认命主、login 拦截注销账号 403

用法：
  .venv/bin/python3 scripts/test_p2.py
  退出码：0=全部通过；1=有失败
"""
import os
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.storage.dao import UserDAO
from src.storage.person_dao import PersonDAO
from src.memory.user_memory import UserMemory
from src.security.auth import AuthHandler

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail[:200]}" if detail and not cond else ""))
    (PASS if cond else FAIL).append(name)


def main():
    print("=== P2 后端测试（persons / 注销 / 记忆升级）===\n")

    tmp = tempfile.mkdtemp(prefix="p2_test_")
    db = os.path.join(tmp, "test.db")
    dao = UserDAO(db)
    pdao = PersonDAO(db)
    mem = UserMemory(base_dir=os.path.join(tmp, "memory"))

    # ── A1 persons CRUD ───────────────────────────────────────────
    print("── A1 多人档案 CRUD ──")
    p1 = pdao.create_person("u1", name="我", relation="自己",
                            birth={"gender": "男", "birth_year": 1990, "birth_month": 5,
                                   "birth_day": 20, "city": "北京"})
    check("A1.1 首个命主自动 is_default", p1["is_default"] and p1["name"] == "我",
          str(p1))
    check("A1.2 出生信息加密落库", "1990" not in dao._connect().execute(
        "SELECT birth_enc FROM persons WHERE id=?", (p1["id"],)).fetchone()[0])

    p2 = pdao.create_person("u1", name="女儿", relation="子女",
                            birth={"gender": "女", "birth_year": 2020})
    check("A1.3 第二个命主非默认", not p2["is_default"])
    check("A1.4 列表默认在前", pdao.list_persons("u1")[0]["id"] == p1["id"])

    up = pdao.update_person("u1", p2["id"], birth={"birth_year": 2021})
    check("A1.5 更新命主", up["birth_year"] == 2021 and up["name"] == "女儿")
    check("A1.6 归属校验（他人不可读/改/删）",
          pdao.get_person("other", p2["id"]) is None
          and pdao.update_person("other", p2["id"], name="x") is None
          and pdao.delete_person("other", p2["id"]) is False)

    check("A1.7 set_default 事务（唯一默认）",
          pdao.set_default("u1", p2["id"])
          and pdao.get_default_person("u1")["id"] == p2["id"]
          and sum(p["is_default"] for p in pdao.list_persons("u1")) == 1)

    check("A1.8 删除默认 → 剩余最早提升",
          pdao.delete_person("u1", p2["id"])
          and pdao.get_default_person("u1")["id"] == p1["id"])

    # ── A2 兼容迁移 ───────────────────────────────────────────────
    print("── A2 users.bazi_info → 默认 person 迁移 ──")
    dao.save_user_bazi("u2", {"year": 1988, "month": 8, "day": 8, "hour": 10,
                              "minute": 0, "gender": "女", "city": "上海"})
    migrated = pdao.get_default_person("u2")
    check("A2.1 首次访问自动迁移为默认 person",
          migrated and migrated["name"] == "我" and migrated["relation"] == "自己"
          and migrated["is_default"] and migrated["birth_year"] == 1988
          and migrated["gender"] == "女" and migrated["city"] == "上海", str(migrated))
    check("A2.2 迁移后仍可再建他人档案",
          pdao.create_person("u2", name="妈妈", relation="父母",
                             birth={"birth_year": 1960}) is not None)
    check("A2.3 无 bazi 用户不建空档案", pdao.get_default_person("u3") is None)

    # ── A3 账号注销 ───────────────────────────────────────────────
    print("── A3 账号注销（软删 + 90 天清理）──")
    dao.save_user_bazi("u4", {"year": 1990, "gender": "男"})
    pdao.create_person("u4", name="我", relation="自己", birth={"birth_year": 1990})
    dao.save_consultation("u4", "今年运势", {"bazi": []}, analysis="分析")
    mem.add_fact_entry("u4", "profile", "性别：男")
    check("A3.1 默认 active", dao.get_user_status("u4") == "active")
    check("A3.2 注销软删", dao.cancel_user("u4")
          and dao.get_user_status("u4") == "cancelled")
    check("A3.3 软删不删数据（90 天保留期内）",
          dao.get_user_consultations("u4", limit=5) != []
          and pdao.list_persons("u4") != []
          and mem.has_memory("u4"))
    stats = dao.cleanup_cancelled_accounts(retention_days=0,
                                           memory_dir=mem.base_dir)
    check("A3.4 90 天到期清理（users+persons+consultations+画像）",
          stats["removed_users"] == 1
          and dao.get_user_status("u4") == "active"   # 行已删
          and pdao.list_persons("u4") == []
          and dao.get_user_consultations("u4", limit=5) == []
          and not mem.has_memory("u4"), str(stats))
    check("A3.5 清理幂等（二次清理无用户）",
          dao.cleanup_cancelled_accounts(retention_days=0,
                                         memory_dir=mem.base_dir)["removed_users"] == 0)

    # ── A4 L2 事实条目 ────────────────────────────────────────────
    print("── A4 L2 事实条目（去重/加密/TTL）──")
    e1 = mem.add_fact_entry("u5", "profile", "性别：女", source_msg="消息1")
    e2 = mem.add_fact_entry("u5", "profile", "性别：女", source_msg="消息2")
    check("A4.1 同 type+content 去重（不重复加，updated_at 刷新）",
          len(mem.list_fact_entries("u5")) == 1 and e1["id"] == e2["id"]
          and e2["updated_at"] >= e1["updated_at"])
    mem.add_fact_entry("u5", "preference", "公司：字节跳动")
    mem.add_fact_entry("u5", "fact", "正在考虑跳槽")
    check("A4.2 不同类型共存", len(mem.list_fact_entries("u5")) == 3)
    raw = mem._load("u5")
    check("A4.3 加密落盘（明文不出现在文件）",
          "fact_entries_enc" in raw and "性别：女" not in raw["fact_entries_enc"]
          and "fact_entries" not in raw)
    mem.add_fact_entry("u5", "event", "旧事件", ttl_days=1)
    items = mem._load_enc_json("u5", "fact_entries_enc")
    for it in items:
        if it["content"] == "旧事件":
            it["created_at"] = "2020-01-01T00:00:00"
    mem._save_enc_json("u5", "fact_entries_enc", items)
    check("A4.4 TTL 过期剔除",
          not any(f["content"] == "旧事件" for f in mem.list_fact_entries("u5")))
    target = mem.list_fact_entries("u5")[0]
    check("A4.5 单条删除", mem.delete_fact_entry("u5", target["id"]) == 1)
    check("A4.6 清空全部（不动生辰/persons）",
          mem.clear_fact_entries("u5") == 2 and mem.list_fact_entries("u5") == [])

    # ── A5 演化链 ─────────────────────────────────────────────────
    print("── A5 演化链 ──")
    mem.add_evolution("u6", "career", quote="6月建议先稳定当前岗位")
    mem.add_evolution("u6", "career", quote="8月建议把握跳槽窗口")
    check("A5.1 时间线 append",
          mem.get_evolutions("u6")["career"]["count"] == 2)
    hint = mem.format_evolution_hint("u6", "career")
    check("A5.2 ≥2 条注入过往咨询演变提示", "过往咨询演变" in hint, hint[:80])
    check("A5.3 <2 条不注入", mem.format_evolution_hint("u6", "love") == "")
    for i in range(6):
        mem.add_evolution("u6", "career", quote=f"第{i}次")
    check("A5.4 cap 5 条（保留最近）",
          len(mem.get_evolution("u6", "career")) == 5
          and mem.get_evolution("u6", "career")[-1]["quote"] == "第5次")

    # ── A6 API 契约（TestClient）──────────────────────────────────
    print("── A6 API 契约 ──")
    from fastapi import FastAPI
    from src.api import user as user_api
    from src.api.user import router as user_router

    app = FastAPI()
    app.include_router(user_router)
    # 共享鉴权处理器：require_user 走 get_auth_handler()（模块级共享实例），
    # token 必须由同一实例签发才能通过校验
    from src.security.auth import set_auth_handler as _set_auth_handler
    _auth = AuthHandler()
    _set_auth_handler(_auth)
    user_api.setup(dao, None, _auth)

    def _token(uid: str) -> str:
        return _auth.create_user_token(uid, openid=uid)

    h = {"Authorization": f"Bearer {_token('api_user')}"}
    client = __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(app)

    # persons CRUD API
    r = client.get("/api/persons", headers=h)
    check("A6.1 GET /api/persons", r.status_code == 200
          and "persons" in r.json() and r.json()["count"] >= 0, r.text[:120])
    r = client.post("/api/persons", headers=h, json={
        "name": "老公", "relation": "伴侣", "gender": "男",
        "birth_year": 1989, "birth_month": 3, "birth_day": 15, "city": "深圳"})
    check("A6.2 POST /api/persons 创建", r.status_code == 200
          and r.json()["person"]["is_default"] and r.json()["person"]["name"] == "老公",
          r.text[:120])
    pid = r.json()["person"]["id"]
    r = client.put(f"/api/persons/{pid}", headers=h, json={
        "name": "老公", "relation": "伴侣", "gender": "男",
        "birth_year": 1989, "birth_month": 3, "birth_day": 16, "city": "广州"})
    check("A6.3 PUT /api/persons 更新", r.status_code == 200
          and r.json()["person"]["city"] == "广州")
    r = client.post(f"/api/persons/{pid}/default", headers=h)
    check("A6.4 POST /api/persons/{id}/default", r.status_code == 200)
    r = client.delete(f"/api/persons/{pid}", headers=h)
    check("A6.5 DELETE /api/persons/{id}", r.status_code == 200)
    r = client.delete(f"/api/persons/{pid}", headers=h)
    check("A6.6 重复删除/他人档案 → 404", r.status_code == 404)

    # 未登录 → 401
    r = client.get("/api/persons")
    check("A6.7 persons 无 token → 401", r.status_code == 401)

    # /api/user/bazi 写默认命主（兼容映射）
    r = client.post("/api/user/bazi", headers=h, json={
        "birth_year": 1992, "birth_month": 7, "birth_day": 1, "birth_hour": 0,
        "birth_minute": 0, "gender": "女", "calendar": "solar", "city": "杭州"})
    d = pdao.get_default_person("api_user")
    check("A6.8 /api/user/bazi 写默认命主", r.status_code == 200
          and d and d["birth_year"] == 1992 and d["gender"] == "女", str(d))
    r = client.get("/api/user/profile", headers=h)
    check("A6.9 profile.bazi_info 返回默认命主",
          r.json()["bazi_info"] and r.json()["bazi_info"].get("year") == 1992,
          str(r.json().get("bazi_info")))

    # 注销 API
    r = client.post("/api/user/cancel", headers=h, json={})
    check("A6.10 cancel 无二次确认 → 400", r.status_code == 400)
    r = client.post("/api/user/cancel", headers=h, json={"confirm": True})
    check("A6.11 cancel confirm=true → 200 软删",
          r.status_code == 200 and dao.get_user_status("api_user") == "cancelled")

    # 注销后登录拦截 403：先登录拿到 dev 用户 → 注销该用户 → 再次登录被拦
    r = client.post("/api/user/login", json={"code": "dev_code"})
    dev_uid = (r.json().get("user") or {}).get("id", "")
    check("A6.12a 首次登录成功（dev 用户）", r.status_code == 200 and bool(dev_uid),
          r.text[:120])
    dao.cancel_user(dev_uid)
    r = client.post("/api/user/login", json={"code": "dev_code"})
    check("A6.12b 注销账号登录 → 403",
          r.status_code == 403 and "90 天" in r.json().get("detail", ""),
          r.text[:120])

    # memory API（UserMemory 默认目录由 USER_MEMORY_DIR 指向测试临时目录，隔离生产数据）
    os.environ["USER_MEMORY_DIR"] = os.path.join(tmp, "memory")
    client2 = __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(app)
    h2 = {"Authorization": f"Bearer {_token('mem_user')}"}
    mem_user = UserMemory(base_dir=os.path.join(tmp, "memory"))
    mem_user.add_fact_entry("mem_user", "profile", "性别：男")
    mem_user.add_fact_entry("mem_user", "preference", "公司：腾讯")
    mem_user.add_evolution("mem_user", "career", quote="倾向稳定")
    r = client2.get("/api/memory", headers=h2)
    body = r.json()
    check("A6.13 GET /api/memory → facts+evolutions",
          r.status_code == 200 and len(body["facts"]) == 2
          and body["evolutions"] and body["evolutions"][0]["topic"] == "career",
          r.text[:150])
    fid = body["facts"][0]["id"]
    r = client2.delete(f"/api/memory/{fid}", headers=h2)
    check("A6.14 DELETE /api/memory/{fact_id}", r.status_code == 200
          and r.json()["removed"] == 1)
    r = client2.delete("/api/memory/facts", headers=h2)
    check("A6.15 DELETE /api/memory/facts 清空（不动演化链）",
          r.status_code == 200 and r.json()["removed"] == 1
          and len(mem_user.list_fact_entries("mem_user")) == 0
          and mem_user.get_evolutions("mem_user")["career"]["count"] == 1)
    r = client2.get("/api/memory", headers=h2)
    check("A6.16 清空后 facts=[] evolutions 保留",
          r.json()["facts"] == [] and len(r.json()["evolutions"]) == 1)
    r = client2.get("/api/memory")
    check("A6.17 memory 无 token → 401", r.status_code == 401)

    # ── 汇总 ──────────────────────────────────────────────────────
    print(f"\n=== 结果: {len(PASS)} 通过, {len(FAIL)} 失败 ===")
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print("全部通过 ✅")
    sys.exit(0)


if __name__ == "__main__":
    main()
