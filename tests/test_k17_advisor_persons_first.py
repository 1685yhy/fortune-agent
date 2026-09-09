# -*- coding: utf-8 -*-
"""k17-1：/api/advisor REST 读取链 persons-first 收口（G1 残留）。

原实现 _dao.get_user_bazi() 只读 users.bazi_info → persons 建档新用户（无
bazi_info 行）误报「未设置八字信息」400（T089 同款问题的 advisor REST 面）；
且 lunar 档案当公历排、solar_time 不透传（k11c 口径缺口）。k17 改走
get_user_birth_profile 统一读取链（persons → bazi_info → chart_records，语义见
src/storage/birth_profile.py 与 handler._handle_advisor 同源），并补齐 lunar
单点转公历 + solar_time 透传。

本文件锚端点「消费契约」（读取链本身由 birth_profile 既有测试覆盖）：
engine/advisor 打桩 → 断言档案 → 引擎实参的转换语义。
"""
import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest  # noqa: E402

from src.storage.birth_profile import to_solar_date  # noqa: E402
from src.api.advisor import (AdvisorRequest, AdvisorResponse,  # noqa: E402
                             get_advisor, setup)


class _FakeDao:
    """端点所需的最小 dao（db_path 不会被用到——读取链被 monkeypatch 打桩）。"""

    def __init__(self):
        self.saved = []
        self.db_path = ":memory:"

    def save_consultation(self, uid, text, result, intent=None):
        self.saved.append((uid, text, result, intent))


class _EngineStub:
    """记录 calculate 实参并返回哨兵 result（真实引擎输出链路由其余引擎测试覆盖）。"""

    calls = []

    def __init__(self):
        pass

    def calculate(self, *args, **kwargs):
        _EngineStub.calls.append((args, kwargs))
        return "engine-result-sentinel"


class _AdvisorStub:
    """AdaptiveAdvisor.generate 打桩：断言收到的 result 即引擎哨兵，返回 canned。"""

    received = []

    def __init__(self):
        pass

    def generate(self, result, user_context=None, api_key=""):
        _AdvisorStub.received.append((result, user_context, api_key))
        return {"actions": [], "insight": "测试洞见", "daily_tip": "",
                "style_notes": ""}


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    setup(_FakeDao(), None)
    # 端点函数体内 `from ..storage.birth_profile import get_user_birth_profile`
    # 在调用时求值 → 打桩目标是存储模块属性（api.advisor 模块无该名字绑定）
    monkeypatch.setattr(
        "src.storage.birth_profile.get_user_birth_profile",
        lambda dao, uid: _PROFILE)
    monkeypatch.setattr("src.engines.bazi.BaziEngine", _EngineStub)
    monkeypatch.setattr("src.engines.advisor_v2.AdaptiveAdvisor", _AdvisorStub)
    _EngineStub.calls = []
    _AdvisorStub.received = []
    yield
    setup(None, None)


_PROFILE = {"year": 1999, "month": 5, "day": 13, "hour": 9, "minute": 0,
            "city": "长春", "gender": "男", "calendar": "solar",
            "solar_time": 1}


def _run(profile):
    import src.storage.birth_profile as bp
    bp.get_user_birth_profile = lambda dao, uid: profile
    return asyncio.run(get_advisor(AdvisorRequest(context="测试处境"),
                                   uid="u1"))


def test_persons_only_profile_reaches_engine_and_advisor():
    """persons 档案（无 bazi_info 行语义）不再 400：档案 → 引擎 → advisor 全链走通。"""
    resp = _run(_PROFILE)
    assert isinstance(resp, AdvisorResponse)
    assert resp.summary == "测试洞见"
    assert len(_EngineStub.calls) == 1
    args, kwargs = _EngineStub.calls[0]
    assert args[:7] == (1999, 5, 13, 9, 0, "长春", "男")
    assert kwargs.get("solar_time") is True
    # advisor 收到引擎输出对象（同一哨兵）；审计落库一次
    assert _AdvisorStub.received[0][0] == "engine-result-sentinel"


def test_audit_recorded_via_dao():
    _run(_PROFILE)
    from src.api import advisor as mod
    assert len(mod._dao.saved) == 1
    uid, text, result, intent = mod._dao.saved[0]
    assert uid == "u1" and intent == "advisor"
    assert "测试处境" in text and result == "engine-result-sentinel"


def test_lunar_profile_solarized_before_engine():
    """lunar 档案消费前单点转公历（引擎契约=公历输入）：入引擎 y/m/d = to_solar_date。"""
    prof = {"year": 1999, "month": 5, "day": 13, "hour": None, "minute": None,
            "city": "", "gender": None, "calendar": "lunar", "solar_time": 1}
    _run(prof)
    args, kwargs = _EngineStub.calls[0]
    exp = to_solar_date(prof)
    assert exp is not None
    assert (args[0], args[1], args[2]) == exp  # 已转公历（非原始农历 5-13）
    assert (args[0], args[1], args[2]) != (1999, 5, 13)
    # 缺省归一：hour/minute None → 0；city/gender 空 → ""/"unknown"（k9 同款口径）
    assert args[3] == 0 and args[4] == 0
    assert args[5] == "" and args[6] == "unknown"
    assert kwargs.get("solar_time") is True


def test_solar_time_off_passthrough():
    """k11c 档案开关 0（北京时间直排）→ 引擎 solar_time=False。"""
    prof = dict(_PROFILE, solar_time=0)
    _run(prof)
    args, kwargs = _EngineStub.calls[0]
    assert kwargs.get("solar_time") is False


def test_no_profile_raises_400():
    """档案缺失 → 400（原语义不变；persons 全缺 / 无排盘历史时仍引导建档）。"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        _run(None)
    assert ei.value.status_code == 400


def test_dao_unready_raises_503():
    from src.api import advisor as mod
    mod.setup(None, None)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(get_advisor(AdvisorRequest(context=""), uid="u1"))
    assert ei.value.status_code == 503


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
