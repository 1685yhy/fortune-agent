# -*- coding: utf-8 -*-
"""k32（A10）REST 侧排盘开关契约收口：solarTime / daylightSaving / lateChildHour。

三方一致性（单一事实源）：`api/compatibility.PersonInfo`、`api/hehun.BaziInput`
（union 复用）、`BaziEngine.calculate` 形参缺省值必须逐一对齐；且两个端点在
调用引擎时**实际透传**三个开关（旧代码丢弃 → 传 false 也恒按缺省开排）。

隔离：引擎为真实实例（纯计算，无网络），断言用 spy 捕获入参；无 LLM
（compatibility._llm_ref 置 None → 走模板 summary）。
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.engines.bazi import BaziEngine  # noqa: E402

_SWITCHES = (("daylightSaving", "daylight_saving"),
             ("lateChildHour", "late_child_hour"),
             ("solarTime", "solar_time"))


def test_a10_contract_defaults_three_way_aligned():
    """PersonInfo / BaziInput / BaziEngine.calculate 三方缺省逐一相等。"""
    from src.api.compatibility import PersonInfo
    from src.api.hehun import BaziInput

    eng = inspect.signature(BaziEngine.calculate).parameters
    for field, param in _SWITCHES:
        assert field in PersonInfo.model_fields, f"compatibility 契约缺 {field}"
        assert field in BaziInput.model_fields, f"hehun 契约缺 {field}"
        assert (PersonInfo.model_fields[field].default
                == BaziInput.model_fields[field].default
                == eng[param].default), field


def _person(**over):
    p = {"year": 1999, "month": 5, "day": 13, "hour": 10, "minute": 55,
         "gender": "男", "city": "北京", "name": "甲"}
    p.update(over)
    return p


def test_a10_compatibility_passes_switches(monkeypatch):
    """POST /api/compatibility 语义：两方开关按人透传引擎（旧代码丢弃）。"""
    from src.api import compatibility as compat

    monkeypatch.setattr(compat, "_llm_ref", None)
    real = compat._engine.calculate
    calls = []

    def _spy(*a, **kw):
        calls.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(compat._engine, "calculate", _spy)
    req = compat.CompatibilityRequest(
        user1=_person(solarTime=False, daylightSaving=True, lateChildHour=True),
        user2=_person(hour=12),
    )
    out = compat.run_compatibility_analysis(req)
    assert out["match_score"] > 0
    assert len(calls) == 2
    assert calls[0].get("solar_time") is False, "user1 关闭真太阳时须透传（旧代码缺参数）"
    assert calls[0].get("daylight_saving") is True
    assert calls[0].get("late_child_hour") is True
    assert calls[1].get("solar_time") is True, "user2 未传 → 缺省开"
    assert calls[1].get("daylight_saving") is False
    assert calls[1].get("late_child_hour") is False


def test_a10_compatibility_switch_changes_chart():
    """行为差异证据：solarTime 关/开在真太阳时修正下确实产生不同时柱。

    锚点（R2-4 校准用例）：2019-03-15 12:00 北京 → 开=午时（甲午），
    关=北京时间直排亦午时；改用 11:00（修正后落巳时/午时边界）区分。
    """
    from src.api import compatibility as compat

    req_on = compat.CompatibilityRequest(
        user1=_person(year=2019, month=3, day=15, hour=11, minute=0),
        user2=_person(year=2019, month=3, day=15, hour=11, minute=0))
    req_off = compat.CompatibilityRequest(
        user1=_person(year=2019, month=3, day=15, hour=11, minute=0,
                      solarTime=False),
        user2=_person(year=2019, month=3, day=15, hour=11, minute=0,
                      solarTime=False))
    on = compat.run_compatibility_analysis(req_on)["charts"]["user1"]["bazi"]
    off = compat.run_compatibility_analysis(req_off)["charts"]["user1"]["bazi"]
    assert len(on.split()) == 4 and len(off.split()) == 4
    assert on != off, "开关必须影响排盘结果（旧代码两方恒相同）"
    # 锚点（R1-3 校准族，本机引擎实测）：北京 2019-03-15 11:00 →
    # 开=真太阳时修正 -23 分落巳时（癸巳）；关=北京时间直排午时（甲午）。
    assert on.split()[3] == "癸巳" and off.split()[3] == "甲午"


def test_a10_union_passes_switches():
    """POST /api/union：三开关按方透传引擎（旧代码 daylightSaving/lateChildHour 丢弃）。"""
    from fastapi.testclient import TestClient
    from src.api import union as union_api
    from src.engines.hehun import HehunEngine
    from src.security.auth import AuthHandler, JWTHandler, set_auth_handler

    set_auth_handler(AuthHandler())
    be, he = BaziEngine(), HehunEngine()
    union_api.setup(he, be, None, None)
    real = be.calculate
    calls = []

    def _spy(*a, **kw):
        calls.append(kw)
        return real(*a, **kw)

    be.calculate = _spy
    try:
        from src.main import app
        client = TestClient(app)
        tok = JWTHandler("test-secret-key-32-bytes-long!!").create_token("u_a10")
        r = client.post("/api/union", json={
            "person_a": _person(solarTime=False, daylightSaving=True,
                                lateChildHour=True),
            "person_b": _person(hour=12),
        }, headers={"Authorization": f"Bearer {tok}"})
    finally:
        be.calculate = real
    assert r.status_code == 200, r.text
    assert len(calls) == 2
    assert calls[0].get("solar_time") is False
    assert calls[0].get("daylight_saving") is True
    assert calls[0].get("late_child_hour") is True
    assert calls[1].get("solar_time") is True
