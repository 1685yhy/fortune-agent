# -*- coding: utf-8 -*-
"""k28 审查遗留 Minor 收口（2026-09-11）。

来源：`.superpowers/sdd/task-k25-review.md`（M-1/M-3/M-5/M-6）、
`.superpowers/sdd/task-k26-review.md`（m-6）、
`.superpowers/sdd/task-k23k24-review.md`（M-6）。

本文件锁住四条「修」的行为（旧代码必失败）：
1. mirror_bazi_info_to_users 行缺失分支：单次关闭连接 + 返回 False
   （旧：else 分支与 finally 双 close）；
2. /api/user/bazi 表单路径 ② 源单一写入点：写完后 users.bazi_info 必须等于
   persons 权威镜像 payload，且本接口不得再调 save_user_bazi
   （旧：镜像被表单原样 dict 覆盖 → gender 未归一 / solar_time 丢键 / 读路径再自愈）；
3. delete_person 提升默认命主后的读调用包守卫：读失败只告警、不抛
   （旧：异常直接抛给调用方）；
4. 空集合自愈可观测：首次 warning 保留，重复自愈降级 info + 落可查询状态
   （旧：进程内后续完全无痕）。

隔离：tmp_path 真实 SQLite（UserDAO/PersonDAO 同库同生产形态）、retriever 用
假 `_raw_count`（零 chroma/零网络/零 LLM）；不写生产库与 data/。
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
from src.rag.retriever import Retriever, self_heal_events  # noqa: E402
from src.storage.birth_profile import get_user_birth_profile  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.person_dao import (  # noqa: E402
    PersonDAO, bazi_info_of_person, mirror_bazi_info_to_users,
)

PERSON_BIRTH = {"gender": "男", "birth_year": 1999, "birth_month": 5,
                "birth_day": 13, "birth_hour": 10, "birth_minute": 55,
                "calendar": "solar", "city": "长春"}


# ================================================================
# 1. mirror_bazi_info_to_users：行缺失分支单次关闭连接
# ================================================================

class _ConnSpy:
    """sqlite3 连接代理：计 close() 次数 + 其余属性透传。"""

    def __init__(self, real):
        self._real = real
        self.closes = 0

    def close(self):
        self.closes += 1
        return self._real.close()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _patch_connect(monkeypatch, holder):
    real_connect = person_dao.db_connect

    def _connect(path):
        c = _ConnSpy(real_connect(path))
        holder["conn"] = c
        return c

    monkeypatch.setattr(person_dao, "db_connect", _connect)


def test_k28_mirror_missing_row_returns_false_single_close(tmp_path, monkeypatch):
    """行缺失 + create_if_missing=False → no-op：返回 False，且连接只关一次
    （旧代码 else 分支 close 后 finally 再 close = 2 次）。"""
    db = str(tmp_path / "k28_mirror.db")
    UserDAO(db)  # 建表（镜像函数自身不建表）
    holder = {}
    _patch_connect(monkeypatch, holder)

    assert mirror_bazi_info_to_users(db, "nobody", {"year": 1999}) is False
    conn = holder["conn"]
    assert conn.closes == 1, f"连接被关闭 {conn.closes} 次（应恰好 1 次）"
    # 真关闭（不是只计数）：已关闭连接再操作必抛 ProgrammingError
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    # 对照：行缺失 + create_if_missing=True 仍首建（语义不变）
    holder2 = {}
    _patch_connect(monkeypatch, holder2)
    assert mirror_bazi_info_to_users(db, "u_new", {"year": 1999},
                                     create_if_missing=True) is True
    assert holder2["conn"].closes == 1
    assert UserDAO(db).get_user_bazi("u_new")["year"] == 1999


# ================================================================
# 2. /api/user/bazi 表单路径：② 源单一写入点 + 权威 payload
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


def test_k28_form_api_single_canonical_write(tmp_path, monkeypatch):
    """表单保存：② 源只由镜像漏斗写一次，且 payload = persons 权威镜像。

    旧行为（k25 审查 M-6）：镜像后紧跟 `save_user_bazi(表单原样 dict)` →
    gender='male' 未归一、未传 solar_time 时整键丢失 → 与 persons 分裂，
    下一次读路径自愈再写一次（收敛后读零写被打破）。
    """
    client, udao, db = _api(tmp_path, "k28_form")
    uid = "u_k28_form"
    udao.save_user_bazi(uid, {})  # 登录建档（users 行存在，生产同序）
    pdao = PersonDAO(db)
    pdao.create_person(uid, name="我", relation="自己", is_default=True,
                       birth=dict(PERSON_BIRTH, solar_time=0))

    calls = {"save": 0}
    orig_save = udao.save_user_bazi

    def _spy_save(*a, **kw):
        calls["save"] += 1
        return orig_save(*a, **kw)

    monkeypatch.setattr(udao, "save_user_bazi", _spy_save)

    # 表单：性别传引擎外形态 male、且不传 solar_time（旧调用方形态）
    r = client.post("/api/user/bazi", json={
        "birth_year": 1999, "birth_month": 5, "birth_day": 13,
        "birth_hour": 10, "birth_minute": 55,
        "gender": "male", "calendar": "solar", "city": "长春",
    }, headers=_headers(uid))
    assert r.status_code == 200, r.text
    assert calls["save"] == 0, "表单路径不得再直写 ② 源（单一写入点）"

    default = pdao.get_default_person(uid)
    assert default["gender"] == "男" and default["solar_time"] == 0
    stored = udao.get_user_bazi(uid)
    assert stored.get("solar_time") == 0, "② 源不得丢 solar_time 键（档案=关）"
    assert stored == bazi_info_of_person(default), \
        "② 源必须等于 persons 权威镜像 payload（逐键）"

    # 收敛后读路径零写（旧代码：gender male≠男 → stale → 自愈 1 次）
    mirror_calls = {"n": 0}
    orig_mirror = person_dao.mirror_bazi_info_to_users

    def _spy_mirror(*a, **kw):
        mirror_calls["n"] += 1
        return orig_mirror(*a, **kw)

    monkeypatch.setattr(person_dao, "mirror_bazi_info_to_users", _spy_mirror)
    prof = get_user_birth_profile(udao, uid)
    assert prof["gender"] == "男" and prof["solar_time"] == 0
    assert mirror_calls["n"] == 0, "两库已同源 → 读路径自愈不得再写"


# ================================================================
# 3. delete_person：提升默认后的读调用包守卫
# ================================================================

def test_k28_delete_person_promote_read_failure_guarded(tmp_path, monkeypatch,
                                                        caplog):
    """删除默认命主后读「新默认」失败 → 只告警：delete_person 仍返回 True
    且 ② 源不被清空（旧代码：异常直接抛给调用方）。"""
    db = str(tmp_path / "k28_delete.db")
    udao = UserDAO(db)
    pdao = PersonDAO(db)
    udao.save_user_bazi("u1", {})  # 登录建档 → ② 行存在
    a = pdao.create_person("u1", name="我", relation="自己", is_default=True,
                           birth=dict(PERSON_BIRTH))
    pdao.create_person("u1", name="妈", relation="父母",
                       birth={"gender": "女", "birth_year": 1970,
                              "birth_month": 1, "birth_day": 1,
                              "calendar": "solar", "city": "长春"})
    before = udao.get_user_bazi("u1")
    assert before and before["year"] == 1999

    def _boom(self, *a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(PersonDAO, "get_default_person", _boom)
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        assert pdao.delete_person("u1", a["id"]) is True
    assert any("k28" in rec.getMessage() for rec in caplog.records), \
        "读失败必须留下告警"
    assert udao.get_user_bazi("u1") == before, "读失败不得清空/改写 ② 源"


# ================================================================
# 4. 空集合自愈可观测（首次 warning 保留，重复降级 info + 可查询状态）
# ================================================================

def test_k28_self_heal_events_observable(tmp_path, monkeypatch, caplog):
    """同一集合名在进程内二次自愈：warning 只有首次，重复走 info 留痕，
    且 self_heal_events() 累计可查（旧代码：后续完全无痕）。"""
    import src.rag.retriever as rt

    # 事件表是进程级状态：本用例用独立集合名 + 独立登记表（防跨用例串扰）
    monkeypatch.setattr(rt, "_SELF_HEAL_EVENTS", {})
    monkeypatch.setattr(rt.Retriever, "_raw_count",
                        lambda self, name: 0 if name == "k28_empty_coll" else 7)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="src.rag.retriever"):
        r1 = Retriever(str(tmp_path), None, collection_name="k28_empty_coll")
        r1._ensure_non_empty_collection()
        assert r1.collection_name == rt.BOOKS_COLLECTION
        warn1 = [rec for rec in caplog.records if rec.levelno >= logging.WARNING]
        assert len(warn1) == 1, "首次自愈必须 warning"

        r2 = Retriever(str(tmp_path), None, collection_name="k28_empty_coll")
        r2._ensure_non_empty_collection()
        assert r2.collection_name == rt.BOOKS_COLLECTION
        warn2 = [rec for rec in caplog.records if rec.levelno >= logging.WARNING]
        assert len(warn2) == 1, "重复自愈不得刷 warning"
        info = [rec for rec in caplog.records
                if rec.levelno == logging.INFO
                and "第 2 次" in rec.getMessage()]
        assert len(info) == 1, "重复自愈必须 info 留痕（可观测）"

    evt = self_heal_events()["k28_empty_coll"]
    assert evt["count"] == 2 and evt["healed_to"] == rt.BOOKS_COLLECTION
    assert evt["last_ts"] and evt["last_reason"]
    # 快照是拷贝（调用方改动不污染进程内状态）
    self_heal_events()["k28_empty_coll"]["count"] = 99
    assert self_heal_events()["k28_empty_coll"]["count"] == 2


def test_k28_self_heal_failure_recorded(tmp_path, monkeypatch, caplog):
    """自愈失败（权威库也空）同样登记事件（healed_to=None），warning 照旧。"""
    import src.rag.retriever as rt

    monkeypatch.setattr(rt, "_SELF_HEAL_EVENTS", {})
    monkeypatch.setattr(rt.Retriever, "_raw_count", lambda self, name: 0)
    with caplog.at_level(logging.WARNING, logger="src.rag.retriever"):
        r = Retriever(str(tmp_path), None, collection_name="fortune_v6")
        r._ensure_non_empty_collection()
    evt = self_heal_events()["fortune_v6"]
    assert evt["healed_to"] is None and evt["count"] >= 1
    assert any("自愈失败" in rec.getMessage() for rec in caplog.records)
