# -*- coding: utf-8 -*-
"""k32 数据链欠账批：档案读路径 stale 比对纳 solar_time（A5）+ 表单 ② 源接缝（A8/A9）。

A5（`src/storage/birth_profile.py`）:读路径自愈 stale 比对的 `_keys` 无
`solar_time` → persons=0（档案关）而 ② 源=1 的分裂永不自愈（旧代码不触发回写）。
A8（`src/api/user.py` 表单路径）:k28 收敛 ② 源为镜像单写入点后，镜像静默失败
（返回 False）时 ② 源不再落库 → 两库分裂（本批：镜像未同步即降级直写）。
A9（同分支）:降级直写写的是**表单原样 dict**（gender 可能 male/female、
未传 solar_time 整键丢失）→ 必须写归一 payload。

隔离：tmp_path 真实 SQLite（UserDAO/PersonDAO 同库同生产形态）、TestClient 直连
（无网络/无 LLM）；不写生产库与 data/。
"""
import logging
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

import src.storage.person_dao as person_dao  # noqa: E402
from src.storage.birth_profile import get_user_birth_profile  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.person_dao import (  # noqa: E402
    PersonDAO, bazi_info_of_person, mirror_bazi_info_to_users,
)

PERSON_BIRTH = {"gender": "男", "birth_year": 1999, "birth_month": 5,
                "birth_day": 13, "birth_hour": 10, "birth_minute": 55,
                "calendar": "solar", "city": "长春"}


# ================================================================
# A5：solar_time 纳入读路径 stale 比对
# ================================================================

def test_a5_solar_time_split_triggers_self_heal(tmp_path, monkeypatch, caplog):
    """persons solar_time=0（档案关）而 ② 源=1 → 触发自愈回写（旧代码不触发）。

    构造：建档不走镜像（mirror=False，模拟 k25 前的历史分裂行）→ ② 源直写
    solar_time=1。读档案须发现分歧并回写 0。
    """
    db = str(tmp_path / "a5_split.db")
    udao, pdao = UserDAO(db), PersonDAO(db)
    pdao.create_person("u1", name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_BIRTH, solar_time=0), mirror=False)
    legacy = dict(bazi_info_of_person(pdao.get_default_person("u1")),
                  solar_time=1)
    udao.save_user_bazi("u1", legacy)
    assert udao.get_user_bazi("u1")["solar_time"] == 1  # 前置：② 源=开

    calls = {"n": 0}
    orig = person_dao.mirror_bazi_info_to_users

    def _spy(*a, **kw):
        calls["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(person_dao, "mirror_bazi_info_to_users", _spy)
    with caplog.at_level(logging.WARNING, logger="src.storage.birth_profile"):
        prof = get_user_birth_profile(udao, "u1")
    assert prof["solar_time"] == 0, "persons 档案关（权威）"
    assert calls["n"] == 1, "solar_time 分歧必须触发自愈回写（旧代码 0 次）"
    assert udao.get_user_bazi("u1")["solar_time"] == 0, "② 源已回写为关"
    assert any("自愈回写" in r.getMessage() for r in caplog.records)


def test_a5_missing_solar_time_key_default_on_no_write(tmp_path, monkeypatch):
    """② 源无 solar_time 键（旧行）而 persons 缺省开=1 → 等价，读路径零写。

    防误触发：缺键与显式 1 同口径（solar_time_on 单一实现），否则每次读都回写。
    """
    db = str(tmp_path / "a5_missing.db")
    udao, pdao = UserDAO(db), PersonDAO(db)
    pdao.create_person("u1", name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_BIRTH), mirror=False)
    legacy = bazi_info_of_person(pdao.get_default_person("u1"))
    legacy.pop("solar_time")  # 旧行形态：无该键
    udao.save_user_bazi("u1", legacy)

    calls = {"n": 0}
    orig = person_dao.mirror_bazi_info_to_users

    def _spy(*a, **kw):
        calls["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(person_dao, "mirror_bazi_info_to_users", _spy)
    prof = get_user_birth_profile(udao, "u1")
    assert prof["solar_time"] == 1
    assert calls["n"] == 0, "缺键 ≡ 默认开，不得误触发自愈"
    assert "solar_time" not in (udao.get_user_bazi("u1") or {})


def test_a5_converged_after_self_heal_zero_write(tmp_path, monkeypatch):
    """自愈收敛：回写后再读 → 零写（旧代码：每次都写 / 本批修后仅一次）。"""
    db = str(tmp_path / "a5_conv.db")
    udao, pdao = UserDAO(db), PersonDAO(db)
    pdao.create_person("u1", name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_BIRTH, solar_time=0), mirror=False)
    udao.save_user_bazi("u1", dict(
        bazi_info_of_person(pdao.get_default_person("u1")), solar_time=1))

    get_user_birth_profile(udao, "u1")  # 第一次：自愈回写

    calls = {"n": 0}
    orig = person_dao.mirror_bazi_info_to_users

    def _spy(*a, **kw):
        calls["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(person_dao, "mirror_bazi_info_to_users", _spy)
    assert get_user_birth_profile(udao, "u1")["solar_time"] == 0
    assert calls["n"] == 0, "两库已同源 → 读路径必须零写"


# ================================================================
# A8/A9：表单路径 ② 源接缝
# ================================================================

def _api(tmp_path, name):
    from fastapi.testclient import TestClient
    from src.main import app as main_app
    from src.api import user as user_api
    from src.security.auth import AuthHandler, set_auth_handler

    db = str(tmp_path / f"{name}.db")
    udao = UserDAO(db)
    user_api.setup(udao, None, AuthHandler())
    set_auth_handler(AuthHandler())
    return TestClient(main_app), udao, db


def _headers(uid: str) -> dict:
    from src.security.auth import JWTHandler
    tok = JWTHandler("test-secret-key-32-bytes-long!!").create_token(uid)
    return {"Authorization": f"Bearer {tok}"}


def _form(**over):
    f = {"birth_year": 1999, "birth_month": 5, "birth_day": 13,
         "birth_hour": 10, "birth_minute": 55,
         "gender": "male", "calendar": "solar", "city": "长春"}
    f.update(over)
    return f


def test_a8_mirror_silent_failure_still_persists(tmp_path, monkeypatch,
                                                 caplog):
    """镜像静默失败（漏斗内返回 False）→ ② 源仍落库（降级直写）。

    旧代码（k28 后）：② 源写入口只有镜像单点，镜像返回 False 无任何补偿 →
    ② 源停留在旧值，两库分裂直到下次读路径自愈。本例表单改城市（长春→北京）
    使 persons 新值 ≠ ② 源旧值，逼出「未同步」判定。
    """
    client, udao, db = _api(tmp_path, "k32_a8")
    uid = "u_a8"
    udao.save_user_bazi(uid, {})  # 登录建档：② 行存在（生产同序）
    pdao = PersonDAO(db)
    pdao.create_person(uid, name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_BIRTH, solar_time=0))  # 建档镜像 → ② 源=长春
    assert udao.get_user_bazi(uid)["city"] == "长春"

    # 只让**漏斗内**的镜像失败（person_dao 模块属性；api/user.py 自有引用不受影响）
    monkeypatch.setattr(person_dao, "mirror_bazi_info_to_users",
                        lambda *a, **kw: False)

    with caplog.at_level(logging.WARNING, logger="src.api.user"):
        r = client.post("/api/user/bazi", json=_form(city="北京"),
                        headers=_headers(uid))
    assert r.status_code == 200, r.text

    stored = udao.get_user_bazi(uid)
    assert stored and stored["city"] == "北京", \
        "镜像静默失败时 ② 源仍必须落库（旧代码停在长春）"
    assert stored.get("gender") == "男" and "solar_time" in stored
    assert any("降级" in rec.getMessage() or "镜像" in rec.getMessage()
               for rec in caplog.records), "降级路径必须留告警"


def test_a8_missing_source_row_created(tmp_path):
    """② 源行缺失（镜像 create_if_missing=False 静默 no-op）→ 表单保存后仍落库。

    k28 接缝根因场景：新用户/② 行被清 → 写侧镜像 no-op 且无第二次写 →
    ② 源缺失（旧代码必失败）。
    """
    client, udao, db = _api(tmp_path, "k32_a8_missing")
    uid = "u_a8_missing"
    assert udao.get_user_bazi(uid) in (None, {}), "前置：② 源缺失"
    pdao = PersonDAO(db)
    pdao.create_person(uid, name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_BIRTH, solar_time=0),
                       mirror=False)  # 不让建档镜像顺带写 ② 源

    r = client.post("/api/user/bazi", json=_form(), headers=_headers(uid))
    assert r.status_code == 200, r.text
    stored = udao.get_user_bazi(uid)
    assert stored and stored.get("year") == 1999, "② 源缺失时表单保存必须补齐"
    assert stored["gender"] == "男" and stored["solar_time"] == 0, \
        "payload 与 persons 权威镜像同构"


def test_a9_fallback_write_normalized(tmp_path, monkeypatch, caplog):
    """persons 链路抛异常 → 降级直写值已归一（gender 中文、含 solar_time）。

    旧代码写表单原样 dict：gender='male' 未归一、未传 solar_time 时整键丢失。
    """
    client, udao, db = _api(tmp_path, "k32_a9")
    uid = "u_a9"
    udao.save_user_bazi(uid, {})
    pdao = PersonDAO(db)
    pdao.create_person(uid, name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_BIRTH, solar_time=0))

    def _boom(self, *a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(PersonDAO, "update_person", _boom)
    with caplog.at_level(logging.WARNING, logger="src.api.user"):
        r = client.post("/api/user/bazi", json=_form(), headers=_headers(uid))
    assert r.status_code == 200, r.text

    stored = udao.get_user_bazi(uid)
    assert stored, "降级兜底不得静默丢档案"
    assert stored["gender"] == "男", "gender 必须走中文契约（旧代码写 'male'）"
    assert "solar_time" in stored, "solar_time 键必须完整（旧代码丢键）"
    assert stored["solar_time"] == 0, "取 persons 权威行的既有开关值"
    assert stored["year"] == 1999 and stored["city"] == "长春"
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


def test_a9_fallback_no_person_normalized_form(tmp_path, monkeypatch):
    """persons 完全不可用（无 person 行 + 写抛异常）→ 回退表单归一 payload。

    gender male→男、solar_time 恒含（未传 → 读口径默认开=1）。
    """
    client, udao, db = _api(tmp_path, "k32_a9_nop")
    uid = "u_a9_nop"
    udao.save_user_bazi(uid, {})

    def _boom(*a, **kw):
        raise RuntimeError("persons table unavailable")

    monkeypatch.setattr(PersonDAO, "get_default_person", _boom)
    monkeypatch.setattr(PersonDAO, "create_person", _boom)
    r = client.post("/api/user/bazi", json=_form(), headers=_headers(uid))
    assert r.status_code == 200, r.text
    stored = udao.get_user_bazi(uid)
    assert stored and stored["year"] == 1999
    assert stored["gender"] == "男", "表单 male → 中文契约"
    assert stored.get("solar_time") == 1, "未传 → 缺省开（读口径）"
