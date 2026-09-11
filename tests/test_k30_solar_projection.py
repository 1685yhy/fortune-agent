# -*- coding: utf-8 -*-
"""k30：真太阳时投影同族补齐 + 自愈状态快照加固（2026-09-11）。

背景（k28/k29 实现代理登记遗留 ③④）：
- 遗留④（真太阳时投影同族缺口）：k29 修了「合盘页丢 solarTime → 档案关了仍出
  修正盘」。同族排查发现另有两处「从 person/bazi_info 行构造 birth/bazi dict」
  的投影点丢开关：
    ① `PersonDAO.default_person_bazi_info()`——登录响应 `bazi` 与
       `/api/user/profile` 的 `bazi_info` 都吃它。丢键后前端 `bazi_info.solar_time`
       读不到真值（miniprogram/pages/bazi/bazi.js `_applyBazi`：缺键 → 默认开展示）
       → 用户关了开关，档案页仍显示「开」。
    ② `PersonDAO.migrate_legacy_bazi()`（建档路径）——users.bazi_info 旧单档案
       → 默认 person 时丢 solar_time。k11c/k25 镜像**会**把 solar_time 写进
       bazi_info（bazi_info_of_person payload 含该键），故 bazi_info 里显式 0
       经此迁移会**回弹默认开**（0 是有效值，不是「未提供」）。
- 遗留③（自愈状态并发加固）：`self_heal_events()` 的
  `{k: dict(v) for k, v in _SELF_HEAL_EVENTS.items()}` 在迭代**活视图**的同时
  逐项处理值；另一线程/重入路径 `_record_self_heal` 的 `setdefault` 新集合名
  落在两项之间 → `RuntimeError: dictionary changed size during iteration`。
  加固 = 迭代快照（`list(...)`），返回语义不变（仍是浅拷贝快照）。

本文件锁住（旧代码必失败）：
1. default_person_bazi_info 带 solar_time，且 **0 不被 `(None, "")` 折叠逻辑丢掉**；
2. 口径与既有单点同源（bazi_info_of_person / solar_time_on），不另立一套；
3. profile / login 两个消费面端到端带真值；
4. 老用户迁移保留显式 0，缺键仍默认开（向后兼容）；
5. 投影加键是纯增量（旧消费方读旧键零变化）；
6. self_heal_events() 在重入登记下不抛且仍是快照。

隔离：全部 tmp_path 真实 SQLite（UserDAO/PersonDAO 同库同生产形态），零网络
零 LLM；不写生产库与 data/。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在导入 src.main（app）之前设置：AuthHandler/JWTHandler 从环境读密钥
os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.person_dao import (  # noqa: E402
    PersonDAO, bazi_info_of_person, solar_time_on,
)
from src.api import user as user_api  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402


# ── 装配（仿 tests/test_profile_consistency.py） ──────────────────────

@pytest.fixture(autouse=True)
def _reset_dev_login_state(monkeypatch):
    monkeypatch.setattr(user_api, "_dev_openid", None)
    monkeypatch.setenv("WECHAT_APP_ID", "")
    monkeypatch.setenv("WECHAT_APP_SECRET", "")


def _api(tmp_path, name):
    db = str(tmp_path / f"{name}.db")
    udao = UserDAO(db)
    user_api.setup(udao, None, AuthHandler())
    set_auth_handler(AuthHandler())
    return TestClient(app), udao, db


def _headers(uid):
    return {"Authorization": "Bearer " + JWTHandler(
        "test-secret-key-32-bytes-long!!").create_token(uid)}


# 档案锚点（与 k11c/k29 同源锚点族；solar_time 单独给）
_BIRTH_ON = {
    "gender": "男", "birth_year": 1999, "birth_month": 5, "birth_day": 13,
    "birth_hour": 10, "birth_minute": 55, "calendar": "solar", "city": "长春",
}
# 旧版单档案（users.bazi_info 兼容迁移源；字段与 default_person_bazi_info 同名）
_LEGACY_BAZI = {
    "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 30,
    "gender": "男", "calendar": "solar", "city": "北京",
}
_BIRTH_KEYS_8 = ("year", "month", "day", "hour", "minute", "gender",
                 "calendar", "city")


# ================================================================
# 1. default_person_bazi_info：同族缺口①（登录 / profile 的投影源）
# ================================================================

def test_k30_default_person_bazi_info_carries_solar_time_on(tmp_path):
    """默认开（未设置开关）→ 投影必须带 solar_time=1（旧代码：整键丢失）。"""
    db = str(tmp_path / "k30_on.db")
    PersonDAO(db).create_person("u_on", name="我", relation="自己",
                                is_default=True, birth=dict(_BIRTH_ON))
    info = PersonDAO(db).default_person_bazi_info("u_on")
    assert "solar_time" in info, "投影必须携带档案级真太阳时开关"
    assert info["solar_time"] == 1


def test_k30_default_person_bazi_info_keeps_explicit_off_zero(tmp_path):
    """**0 是有效值**：显式关 → 投影必须是 0，绝不能被 `(None, "")` 折叠丢掉。

    丢 0 的后果 = 消费方（bazi.js `_applyBazi`）读不到键 → 默认开展示 →
    用户关了开关档案页仍显示开（k29 合盘页同族事故）。
    """
    db = str(tmp_path / "k30_off.db")
    PersonDAO(db).create_person(
        "u_off", name="我", relation="自己", is_default=True,
        birth={**_BIRTH_ON, "solar_time": 0})
    info = PersonDAO(db).default_person_bazi_info("u_off")
    assert "solar_time" in info, "0 是有效值，不得被 None/空 折叠逻辑丢弃"
    assert info["solar_time"] == 0
    # 整形态锁：8 birth 键 + 开关，值口径不变（0 分/空串仍按既有约定省略）
    assert info == {
        "year": 1999, "month": 5, "day": 13, "hour": 10, "minute": 55,
        "gender": "男", "calendar": "solar", "city": "长春", "solar_time": 0}


def test_k30_default_person_bazi_info_same_reading_as_single_point(tmp_path):
    """口径同源：投影的 solar_time 必须与既有单点（solar_time_on/
    bazi_info_of_person）逐值一致，不另立一套。"""
    db = str(tmp_path / "k30_src.db")
    pdao = PersonDAO(db)
    pdao.create_person("u_a", name="我", relation="自己", is_default=True,
                       birth={**_BIRTH_ON, "solar_time": 0})
    pdao.create_person("u_b", name="我", relation="自己", is_default=True,
                       birth=dict(_BIRTH_ON))
    for uid, expect in (("u_a", 0), ("u_b", 1)):
        person = pdao.get_default_person(uid)
        info = pdao.default_person_bazi_info(uid)
        assert info["solar_time"] == solar_time_on(person.get("solar_time"))
        assert info["solar_time"] == bazi_info_of_person(person)["solar_time"]


def test_k30_default_person_bazi_info_is_pure_additive(tmp_path):
    """向后兼容：新增键是纯增量 —— 旧消费方读的 8 个 birth 键逐值不变。"""
    db = str(tmp_path / "k30_compat.db")
    PersonDAO(db).create_person("u_c", name="我", relation="自己",
                                is_default=True, birth={**_BIRTH_ON,
                                                        "solar_time": 0})
    info = PersonDAO(db).default_person_bazi_info("u_c")
    # 旧投影形态（不含开关）逐键仍在，供旧前端/旧调用方零变化消费
    for k in _BIRTH_KEYS_8:
        assert k in info and info[k] == {
            "year": 1999, "month": 5, "day": 13, "hour": 10, "minute": 55,
            "gender": "男", "calendar": "solar", "city": "长春"}[k]


# ================================================================
# 2. 消费面端到端：/api/user/profile 与登录响应
# ================================================================

def test_k30_profile_bazi_info_carries_solar_time_off(tmp_path):
    """端到端：档案关 → GET /api/user/profile 的 bazi_info.solar_time == 0
    （前端 bazi.js `_applyBazi` 据此回显，旧代码缺键 → 回显「开」）。"""
    client, udao, db = _api(tmp_path, "k30_profile")
    uid = "wx_k30_profile"
    pdao = PersonDAO(db)
    pdao.create_person(uid, name="我", relation="自己", is_default=True,
                       birth={**_BIRTH_ON, "solar_time": 0})
    r = client.get("/api/user/profile", headers=_headers(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bazi_info"]["solar_time"] == 0, \
        "profile 投影丢开关 → 前端回显默认开（k29 合盘页同族）"
    assert body["bazi_info"]["year"] == 1999


def test_k30_login_bazi_carries_solar_time_off(tmp_path, monkeypatch):
    """端到端：登录响应 bazi（globalData 预填源）同样带真值 0。"""
    monkeypatch.setenv("DEV_OPENID", "k30_login_off")
    client, udao, db = _api(tmp_path, "k30_login")
    uid = "wx_k30_login_off"
    PersonDAO(db).create_person(uid, name="我", relation="自己",
                                is_default=True,
                                birth={**_BIRTH_ON, "solar_time": 0})
    r = client.post("/api/user/login", json={"code": "dev_code"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bazi"]["solar_time"] == 0
    assert body["bazi"]["year"] == 1999


# ================================================================
# 3. 建档路径 migrate_legacy_bazi：同族缺口②（0 回弹默认开）
# ================================================================

def test_k30_migrate_legacy_keeps_explicit_solar_time_off(tmp_path):
    """老用户迁移：② 源显式 0（k11c/k25 镜像会写进 bazi_info）→ 迁移出的
    默认 person 必须仍是关（旧代码：整键丢失 → 回弹默认开）。"""
    db = str(tmp_path / "k30_mig_off.db")
    udao = UserDAO(db)
    pdao = PersonDAO(db)
    udao.save_user_bazi("u_mig_off",
                        {**_LEGACY_BAZI, "solar_time": 0})
    person = pdao.get_default_person("u_mig_off")   # auto_migrate=True
    assert person is not None and person["name"] == "我"
    assert person["solar_time"] == 0, "迁移丢开关 → 档案回弹默认开"
    # 投影面同步可见
    assert pdao.default_person_bazi_info("u_mig_off")["solar_time"] == 0


def test_k30_migrate_legacy_without_key_still_defaults_on(tmp_path):
    """旧行无该键 → 迁移后仍默认开（向后兼容，零行为变化）。"""
    db = str(tmp_path / "k30_mig_legacy.db")
    udao = UserDAO(db)
    pdao = PersonDAO(db)
    udao.save_user_bazi("u_mig_old", dict(_LEGACY_BAZI))
    person = pdao.get_default_person("u_mig_old")
    assert person is not None
    assert person["solar_time"] == 1
    assert pdao.default_person_bazi_info("u_mig_old")["solar_time"] == 1


def test_k30_migrate_legacy_does_not_touch_bazi_info_source(tmp_path):
    """迁移是 ② 源 → person 的反向路径：不得回写 bazi_info（k25 ④-6(b) 语义）。"""
    db = str(tmp_path / "k30_mig_nowrite.db")
    udao = UserDAO(db)
    pdao = PersonDAO(db)
    udao.save_user_bazi("u_mig_nw", {**_LEGACY_BAZI, "solar_time": 0})
    before = udao.get_user_bazi("u_mig_nw")
    pdao.get_default_person("u_mig_nw")
    assert udao.get_user_bazi("u_mig_nw") == before


# ================================================================
# 4. self_heal_events() 快照加固（遗留③）
# ================================================================

class _ReentrantValue:
    """事件值替身：被 `dict(v)` 拷贝时重入登记一个新集合名。

    确定性复现「另一线程在迭代两项之间 setdefault 新集合名」的窗口 ——
    不依赖线程调度，可 100% 复现（旧实现迭代活视图 → RuntimeError）。
    """

    def __init__(self, store, data):
        self._store = store
        self._data = data

    def keys(self):
        self._store.setdefault(
            "__k30_reentrant__",
            {"count": 1, "last_reason": "reentrant", "healed_to": None,
             "last_ts": ""})
        return list(self._data.keys())

    def __getitem__(self, k):
        return self._data[k]


def test_k30_self_heal_events_survives_concurrent_registration(monkeypatch):
    """快照期间有新集合名登记 → 不得抛（旧代码：dictionary changed size）。

    用重入构造（值拷贝时登记新键）确定性复现并发窗口，不写线程竞态。
    """
    import src.rag.retriever as rt

    store = {}
    store["k30_base"] = _ReentrantValue(
        store, {"count": 3, "last_reason": "base", "healed_to": "fortune_v6",
                "last_ts": "2026-09-11T00:00:00"})
    monkeypatch.setattr(rt, "_SELF_HEAL_EVENTS", store)

    snap = rt.self_heal_events()          # 旧代码在此抛 RuntimeError
    # 快照语义不变：迭代开始后新登记的键不入本次快照（浅拷贝快照）
    assert set(snap) == {"k30_base"}
    assert snap["k30_base"]["count"] == 3
    assert isinstance(snap["k30_base"], dict)
    assert "__k30_reentrant__" in store   # 重入登记确实发生过（构造有效）


def test_k30_self_heal_events_snapshot_semantics_unchanged(monkeypatch):
    """返回语义不变：浅拷贝快照 —— 值是新 dict、改动快照不污染进程内状态、
    后续登记不进旧快照。"""
    import src.rag.retriever as rt

    store = {}
    monkeypatch.setattr(rt, "_SELF_HEAL_EVENTS", store)
    assert rt.self_heal_events() == {}

    rt._record_self_heal("k30_a", "reason-a", "fortune_v6")
    rt._record_self_heal("k30_a", "reason-b", None)
    snap1 = rt.self_heal_events()
    assert snap1["k30_a"]["count"] == 2
    assert snap1["k30_a"]["last_reason"] == "reason-b"
    assert snap1["k30_a"]["healed_to"] is None
    assert snap1["k30_a"]["last_ts"]

    # 值是新 dict（非引用）
    assert snap1["k30_a"] is not store["k30_a"]
    snap1["k30_a"]["count"] = 99
    rt._record_self_heal("k30_b", "reason-b", None)
    assert rt.self_heal_events()["k30_a"]["count"] == 2
    assert set(snap1) == {"k30_a"}, "旧快照不得出现迭代后新登记的键"


def test_k30_self_heal_events_returns_plain_dicts(monkeypatch):
    """返回容器与值都是普通 dict（运维巡检方可能做 JSON 序列化）。"""
    import src.rag.retriever as rt

    monkeypatch.setattr(rt, "_SELF_HEAL_EVENTS", {})
    rt._record_self_heal("k30_plain", "r", None)
    snap = rt.self_heal_events()
    assert type(snap) is dict
    assert type(snap["k30_plain"]) is dict
