# -*- coding: utf-8 -*-
"""对话数据复用体系 Task 2：profile 同源契约 + 登录响应补 bazi 档案 + 老用户迁移守护。

覆盖三件事：
1. 同源契约：GET /api/user/profile 的生日字段（事实源 = persons 默认命主
   default_person_bazi_info）与四柱字段（来源 = users.bazi_info get_user_bazi）
   由同一保存路径写入（/api/user/bazi 与对话 _sync_person_profile 同时写两处），
   天然一致。Task 1 合入后契约已成立 → 本文件以回归守护 + 注释说明。
2. 登录响应补 bazi：POST /api/user/login 返回体补 bazi 字段（默认命主出生信息
   dict 或 null），供前端 globalData 使用。
3. 老用户迁移守护：persons 无命主但 users.bazi_info 有档案 → get_default_person
   auto_migrate 自动补建「我/自己」命主（机制在 person_dao.py，本文件守护）。

注意：PersonDAO/UserDAO 一律用 tmp_path 真实 SQLite 文件（:memory: 每连接是新库）。
"""
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# 必须在导入 src.main（app）之前设置：AuthHandler/JWTHandler 从环境读密钥
os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.api import user as user_api  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402

# 旧版单档案（users.bazi_info 兼容迁移源；字段与 default_person_bazi_info 完全同名）
LEGACY_BAZI = {
    "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
    "gender": "男", "calendar": "solar", "city": "北京",
}

_BIRTH_FIELDS = ("year", "month", "day", "hour", "minute", "gender", "calendar", "city")


@pytest.fixture(autouse=True)
def _reset_dev_login_state(monkeypatch):
    """每次测试重置 dev openid 模块级缓存（DEV_OPENID 钉死在登录前生效），
    并清空微信配置（防 code="dev_code" 测试误触真实网络调用）。"""
    monkeypatch.setattr(user_api, "_dev_openid", None)
    monkeypatch.setenv("WECHAT_APP_ID", "")
    monkeypatch.setenv("WECHAT_APP_SECRET", "")


def _api(tmp_path: Path, name: str):
    """装配 DAO + 认证（仿 tests/test_paipan_api.py），返回 (client, udao, db)。"""
    db = str(tmp_path / f"{name}.db")
    udao = UserDAO(db)
    user_api.setup(udao, None, AuthHandler())
    set_auth_handler(AuthHandler())
    return TestClient(app), udao, db


def _headers(uid: str) -> dict:
    return {"Authorization": f"Bearer {JWTHandler('test-secret-key-32-bytes-long!!').create_token(uid)}"}


# ───────────────────────── 老用户迁移守护（机制在 person_dao.py） ─────────────────────────

def test_legacy_archive_auto_migrates_to_default_person(tmp_path):
    """persons 无命主、users.bazi_info 有档案 → get_default_person 自动补建「我/自己」"""
    db = str(tmp_path / "migrate.db")
    UserDAO(db).save_user_bazi("u_legacy", dict(LEGACY_BAZI))
    pdao = PersonDAO(db)
    assert pdao.count_persons("u_legacy") == 0
    p = pdao.get_default_person("u_legacy")  # auto_migrate=True（默认）
    assert p is not None
    assert p["name"] == "我" and p["relation"] == "自己"
    assert p["is_default"] == 1
    assert p["birth_year"] == 1990 and p["birth_month"] == 5 and p["birth_day"] == 20
    assert p["birth_hour"] == 15
    assert p["birth_minute"] is None  # 0 → None（P2 _birth_dict 归一约定：0 视为缺省）
    assert p["gender"] == "男" and p["calendar"] == "solar" and p["city"] == "北京"
    # 幂等：不重复建
    p2 = pdao.get_default_person("u_legacy")
    assert p2["id"] == p["id"]
    assert pdao.count_persons("u_legacy") == 1


def test_no_archive_does_not_create_empty_person(tmp_path):
    """无旧档案 → 不建空命主"""
    db = str(tmp_path / "no_migrate.db")
    UserDAO(db)  # 建表
    pdao = PersonDAO(db)
    assert pdao.get_default_person("u_new") is None
    assert pdao.count_persons("u_new") == 0


def test_archive_without_year_does_not_migrate(tmp_path):
    """旧档案无出生年 → 不迁移（返回 None，不建空档案）"""
    db = str(tmp_path / "no_year.db")
    UserDAO(db).save_user_bazi("u_ny", {"gender": "女", "city": "上海"})
    pdao = PersonDAO(db)
    assert pdao.get_default_person("u_ny") is None
    assert pdao.count_persons("u_ny") == 0


# ───────────────────────── 同源契约（DAO 层） ─────────────────────────

def test_default_person_bazi_info_same_fields_as_legacy(tmp_path):
    """default_person_bazi_info 字段与旧 users.bazi_info 完全同名同值（同源契约）

    注意 persons 侧既有归一约定（P2 _birth_dict：数值 0/None 归一为 None 并省略
    输出），故 minute=0 不输出——与旧字段语义兼容（缺省即 0 分）。

    k30：投影新增 `solar_time`（档案级真太阳时开关，缺省开=1；见
    tests/test_k30_solar_projection.py）——纯增量键，旧消费方逐键读取零变化。
    """
    db = str(tmp_path / "same_fields.db")
    UserDAO(db).save_user_bazi("u1", dict(LEGACY_BAZI))
    info = PersonDAO(db).default_person_bazi_info("u1")
    expected = {k: v for k, v in LEGACY_BAZI.items() if v}  # minute=0 → 省略
    expected["solar_time"] = 1  # k30：旧行无该键 → 读口径缺省开
    assert info == expected
    assert "minute" not in info  # 0 分按归一约定省略（不输出 None 键）


# ───────────────────────── 同源契约（API 层） ─────────────────────────

def test_profile_birth_fields_match_users_bazi_info(tmp_path):
    """同源契约（API）：统一保存路径写入后，profile 生日字段（默认命主）与
    users.bazi_info（四柱来源）的出生字段完全一致（回归守护）"""
    client, udao, db = _api(tmp_path, "profile_api")
    uid = "wx_task2_prof"
    headers = _headers(uid)
    r = client.post("/api/user/bazi", json={
        "birth_year": 1990, "birth_month": 5, "birth_day": 20,
        "birth_hour": 15, "birth_minute": 30,
        "gender": "男", "calendar": "solar", "city": "北京",
    }, headers=headers)
    assert r.status_code == 200, r.text
    r2 = client.get("/api/user/profile", headers=headers)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    # 生日字段与 users.bazi_info（四柱字段来源）出生字段同值 → 同一保存路径保证同源
    # （minute 取 30 而非 0，避开 persons 侧 0→None 归一约定；该边界由 DAO 层测试覆盖）
    src = udao.get_user_bazi(uid)
    for k in _BIRTH_FIELDS:
        assert body["bazi_info"].get(k) == src.get(k), f"字段 {k} 不同源"
    # 且生日字段确实来自默认命主
    p = PersonDAO(db).get_default_person(uid)
    assert p is not None and p["name"] == "我" and p["birth_year"] == 1990


def test_profile_birth_fields_come_from_default_person(tmp_path):
    """同源契约（API）：生日字段事实源 = persons 默认命主（非 users.bazi_info）。
    人为制造两源差异时，profile 仍返回命主值。"""
    client, udao, db = _api(tmp_path, "profile_person")
    uid = "wx_task2_person"
    pdao = PersonDAO(db)
    pdao.create_person(uid, name="我", relation="自己", is_default=True, birth={
        "gender": "男", "birth_year": 1990, "birth_month": 5, "birth_day": 20,
        "birth_hour": 15, "birth_minute": 0, "calendar": "solar", "city": "北京"})
    udao.save_user_bazi(uid, {"year": 1980, "month": 1, "day": 1, "hour": 0,
                              "minute": 0, "gender": "男", "calendar": "solar",
                              "city": ""})
    r = client.get("/api/user/profile", headers=_headers(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bazi_info"]["year"] == 1990      # 以默认命主为准
    assert body["bazi_info"]["month"] == 5
    assert body["bazi_info"]["city"] == "北京"


# ───────────────────────── 登录响应补 bazi 档案 ─────────────────────────

def test_login_returns_bazi_for_legacy_user(tmp_path, monkeypatch):
    """登录响应补 bazi：老用户（有档案无命主）→ bazi=出生信息 dict，并触发迁移"""
    monkeypatch.setenv("DEV_OPENID", "task2_legacy")
    client, udao, db = _api(tmp_path, "login_legacy")
    uid = "wx_task2_legacy"
    udao.save_user_bazi(uid, dict(LEGACY_BAZI))
    r = client.post("/api/user/login", json={"code": "dev_code"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["id"] == uid
    assert body["user"]["is_new"] is False
    # minute=0 → None → 省略（P2 归一约定；与 default_person_bazi_info 同源契约一致）
    # k30：登录 bazi 同步带档案级真太阳时开关（此用户旧行无该键 → 缺省开=1）
    assert body["bazi"] == {**{k: v for k, v in LEGACY_BAZI.items() if v},
                            "solar_time": 1}
    assert "minute" not in body["bazi"]
    # 登录触发老用户迁移：persons 已补建「我/自己」
    p = PersonDAO(db).get_default_person(uid)
    assert p is not None and p["name"] == "我" and p["birth_year"] == 1990


def test_login_returns_bazi_null_for_new_user(tmp_path, monkeypatch):
    """登录响应补 bazi：新用户 → bazi=None"""
    monkeypatch.setenv("DEV_OPENID", "task2_newbie")
    client, _, _ = _api(tmp_path, "login_new")
    r = client.post("/api/user/login", json={"code": "dev_code"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["is_new"] is True
    assert body["bazi"] is None


def test_login_returns_bazi_for_user_with_person(tmp_path, monkeypatch):
    """登录响应补 bazi：已有默认命主 → bazi=命主出生信息 dict"""
    monkeypatch.setenv("DEV_OPENID", "task2_person")
    client, _, db = _api(tmp_path, "login_person")
    uid = "wx_task2_person"
    pdao = PersonDAO(db)
    pdao.create_person(uid, name="我", relation="自己", is_default=True, birth={
        "gender": "女", "birth_year": 1995, "birth_month": 8, "birth_day": 12,
        "birth_hour": 9, "birth_minute": 30, "calendar": "solar", "city": "广州"})
    r = client.post("/api/user/login", json={"code": "dev_code"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bazi"] is not None
    assert body["bazi"]["year"] == 1995 and body["bazi"]["gender"] == "女"
    assert body["bazi"]["city"] == "广州"
