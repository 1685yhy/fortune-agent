# -*- coding: utf-8 -*-
"""k29 合盘 REST 真太阳时透传（2026-09-11）。

缺口（k19 计划 §5 遗留④）：R2-4 档案级真太阳时开关已接入 paipan/hehun REST
（person.solarTime → 引擎 solar_time），但 **/api/union（合盘页唯一入口）** 的
两次 `calculate` 漏传该字段 → 引擎缺省恒开，前端关闭/档案关在合盘页无效。

本文件锁住（旧代码必失败）：
1. 显式 solarTime:false → 引擎收到 solar_time=False 且时柱按北京时间直排
   （golden：1999-05-13 10:55 长春男 开=壬午午时 / 关=辛巳巳时，k11c 同源锚点）；
2. 逐人透传（person1 关 / person2 开 同请求互不串味）；
3. 不传 solarTime → 服务端默认开（旧调用方零变化）。

隔离：TestClient + 记录型引擎（转调真实 BaziEngine），dao=None（免费档不落库）、
零 LLM（_llm_ref=None → 缘语模板兜底）、零网络；不写生产库与 data/。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from fastapi.testclient import TestClient  # noqa: E402

from src.api import union as union_api  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.engines.hehun import HehunEngine  # noqa: E402
from src.main import app  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402

# golden 锚点（tests/test_k11c_solar_switch.py 同源）：1999-05-13 10:55 长春 男
GOLDEN_ON = "壬午"    # 开=真太阳时修正 11:20 午时
GOLDEN_OFF = "辛巳"   # 关=北京时间直排 10:55 巳时


class _RecordingEngine:
    """记录 calculate 实参并透传真实 BaziEngine（结果可断言）。"""

    def __init__(self):
        self.real = BaziEngine()
        self.calls = []
        self.results = []

    def calculate(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        r = self.real.calculate(*args, **kwargs)
        self.results.append(r)
        return r


def _client():
    client = TestClient(app)
    token = JWTHandler("test-secret-key-32-bytes-long!!").create_token("u_k29")
    return client, {"Authorization": f"Bearer {token}"}


def _person1(**over):
    p = {"birthYear": 1999, "birthMonth": 5, "birthDay": 13,
         "birthHour": 10, "minute": 55, "birthClock": True,
         "gender": "male", "city": "长春"}
    p.update(over)
    return p


def _person2(**over):
    # 与 person1 同一生辰（性别不同不影响四柱）——同一请求内两人开关相反，
    # 时柱必须各随各的，证明逐人透传（非「一方开关带全请求」）
    p = _person1()
    p["gender"] = "female"
    p.update(over)
    return p


def test_union_passes_solar_time_per_person():
    """person1 solarTime=false / person2 true：逐人透传且各自影响时柱。"""
    rec = _RecordingEngine()
    union_api.setup(HehunEngine(), rec, dao=None, member_dao=None)
    set_auth_handler(AuthHandler())
    client, headers = _client()

    r = client.post("/api/union", json={
        "person1": _person1(solarTime=False),
        "person2": _person2(solarTime=True),
    }, headers=headers)
    assert r.status_code == 200, r.text
    assert len(rec.calls) == 2, "双方各排一次盘"

    assert rec.calls[0]["kwargs"].get("solar_time") is False, \
        "person1 显式关必须透传到引擎（旧代码漏传 → None → 缺省开）"
    assert rec.calls[1]["kwargs"].get("solar_time") is True, "person2 逐人透传"
    # 同生辰两人、开关相反 → 时柱各随各的（旧代码两人都走缺省开 → 均壬午）
    assert list(rec.results[0].bazi)[3] == GOLDEN_OFF, "关=北京时间直排（辛巳）"
    assert list(rec.results[1].bazi)[3] == GOLDEN_ON, "开=真太阳时修正（壬午）"
    # 免费档响应形态不回归
    for k in ("score", "levelLabel", "dimensions", "quoteParts"):
        assert k in r.json(), f"免费档响应缺 {k}"


def test_union_solar_time_defaults_on_when_absent():
    """旧调用方不传 solarTime → 服务端默认开（R2-4 产品口径，零行为变化）。"""
    rec = _RecordingEngine()
    union_api.setup(HehunEngine(), rec, dao=None, member_dao=None)
    set_auth_handler(AuthHandler())
    client, headers = _client()

    r = client.post("/api/union", json={
        "person1": _person1(),
        "person2": _person2(),
    }, headers=headers)
    assert r.status_code == 200, r.text
    assert rec.calls[0]["kwargs"].get("solar_time") is True
    assert list(rec.results[0].bazi)[3] == GOLDEN_ON
