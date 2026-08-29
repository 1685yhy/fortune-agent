"""G3b H-7：今日运势缓存键加入八字档案指纹。

审计发现：缓存按 (user_id, date) 6h TTL，建档/改八字/改城市均不清缓存 →
建档前后命中同一缓存条目，当日运势最长 6 小时仍是旧版/通用版。
修复：缓存键 = f"calendar:today:{date}:{档案指纹}"（指纹 = 无八字 "none" /
有八字内容排序 JSON 的 md5），任何档案变化天然失效旧缓存；通用版与个性化版
分键不混用；仍按 user_id 作用域隔离（无跨用户碰撞）。

本测试覆盖：
- 建档前通用版 → 建档后立即个性化（不同 key，不覆盖）；
- 同档案重复请求命中缓存（LLM 只调一次）；
- 改城市（档案指纹变化）→ 当日立即重算；
- 不同用户互不串缓存；
- LLM 异常兜底缓存行为保持。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api import calendar as cal_api  # noqa: E402
from src.security.auth import set_auth_handler, AuthHandler, JWTHandler  # noqa: E402
from src.utils.cache import get_cache  # noqa: E402

DATE = "2026-08-29"

# 个性化路径的假 daily 调用记录（验证缓存命中：只调一次）
LLM_CALLS = []
RAISE_IN_DAILY = False

# 不同日主 → 不同假幸运色（用于区分重算）
_FAKE_COLOR = {"甲": "青色", "丙": "赤色", "壬": "玄色", "庚": "银色"}


def _fake_daily(self, user_bazi, date_str=None, preferences=""):
    if RAISE_IN_DAILY:
        raise RuntimeError("LLM boom (test)")
    LLM_CALLS.append(date_str)
    from src.engines.calendar import CalendarDay
    bazi = user_bazi.get("bazi") or []
    day_stem = bazi[2][0] if len(bazi) >= 3 and bazi[2] else "?"
    day_stem2, day_branch = self._day_stem_branch(date_str)
    return CalendarDay(
        date=date_str, day_stem=day_stem2, day_branch=day_branch,
        yi=[{"action": "宜稳健", "time": "辰时7-9点", "reason": "测试"}],
        ji=[{"action": "忌冒进", "time": "全天", "reason": "测试"}],
        lucky_color=_FAKE_COLOR.get(day_stem, "紫色"),
        lucky_direction="东南", lucky_number="7",
        overall_mood="测试基调",
    )


class FakeDao:
    def __init__(self, bazi=None):
        self.bazi = bazi

    def get_user_bazi(self, user_id):
        return self.bazi


PROFILE = {
    "year": 1992, "month": 8, "day": 15, "hour": 10, "minute": 0,
    "gender": "男", "calendar": "solar", "city": "北京",
    "bazi": ["壬申", "戊申", "甲午", "己巳"],  # 日主甲 → 青色
}


@pytest.fixture(autouse=True)
def auth():
    set_auth_handler(AuthHandler())
    yield
    set_auth_handler(None)


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """个性化路径的 LLM 调用替换为确定性假 daily（零网络）。"""
    LLM_CALLS.clear()
    monkeypatch.setattr("src.engines.calendar.LuckyCalendar.daily", _fake_daily)
    yield
    RAISE_IN_DAILY and LLM_CALLS.clear()


@pytest.fixture(autouse=True)
def clean_cache():
    get_cache().clear()
    yield
    get_cache().clear()


def _token(user_id: str) -> dict:
    tok = JWTHandler(os.environ["JWT_SECRET_KEY"]).create_token(user_id)
    return {"Authorization": f"Bearer {tok}"}


def _client(dao):
    """挂载 calendar 路由的独立 app（注入假 DAO，无 handler → 无 LLM key）。"""
    cal_api.setup(dao, None)
    app = FastAPI()
    app.include_router(cal_api.router)
    return TestClient(app)


class TestCacheFingerprint:
    def test_profile_creation_switches_to_personalized_immediately(self):
        """建档前通用版 → 建档后立即个性化（指纹分键，不命中旧通用版缓存）。"""
        dao = FakeDao(None)
        client = _client(dao)
        headers = _token("u-h7-1")
        r1 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert r1.status_code == 200
        assert "请先设置八字" in r1.json()["personal_advice"]  # 通用版

        dao.bazi = PROFILE  # 建档
        r2 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["lucky_color"] == "青色"          # 个性化（假 daily）
        assert r2.json()["personal_advice"] != r1.json()["personal_advice"]
        assert len(LLM_CALLS) == 1                          # 个性化路径只算一次
        assert get_cache().size >= 2                        # 通用版+个性化版并存

    def test_same_profile_hits_cache(self):
        """同档案重复请求：命中缓存，LLM 不重复调用。"""
        dao = FakeDao(dict(PROFILE))
        client = _client(dao)
        headers = _token("u-h7-2")
        r1 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        r2 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == r2.json()
        assert len(LLM_CALLS) == 1
        assert get_cache().size == 1

    def test_city_change_invalidates_same_day(self):
        """改城市（档案指纹变化）：当日立即重算，不返回 6h 旧缓存。"""
        dao = FakeDao(dict(PROFILE))
        client = _client(dao)
        headers = _token("u-h7-3")
        r1 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert len(LLM_CALLS) == 1

        changed = dict(PROFILE, city="上海")
        dao.bazi = changed
        r2 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert r2.status_code == 200
        assert len(LLM_CALLS) == 2      # 旧缓存被指纹失效，重新生成
        assert get_cache().size == 2

    def test_bazi_change_invalidates(self):
        """改八字（日主变化）：新结果立即生效。"""
        dao = FakeDao(dict(PROFILE))
        client = _client(dao)
        headers = _token("u-h7-4")
        r1 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert r1.json()["lucky_color"] == "青色"  # 日主甲

        dao.bazi = dict(PROFILE, bazi=["壬申", "戊申", "丙午", "己巳"])  # 日主丙
        r2 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert r2.json()["lucky_color"] == "赤色"  # 日主丙
        assert len(LLM_CALLS) == 2

    def test_different_users_isolated(self):
        """不同用户同档案：缓存按 user_id 隔离，无跨用户碰撞。"""
        dao = FakeDao(dict(PROFILE))
        client = _client(dao)
        r1 = client.get(f"/api/calendar/today?date={DATE}", headers=_token("u-h7-a"))
        r2 = client.get(f"/api/calendar/today?date={DATE}", headers=_token("u-h7-b"))
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == r2.json()
        assert len(LLM_CALLS) == 2          # 各算一次，不互借缓存
        assert get_cache().size == 2

    def test_llm_exception_fallback_still_cached(self):
        """LLM 异常：兜底通用版照常缓存（指纹键），第二次请求命中缓存。"""
        global RAISE_IN_DAILY
        dao = FakeDao(dict(PROFILE))
        client = _client(dao)
        headers = _token("u-h7-5")
        RAISE_IN_DAILY = True
        try:
            r1 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
            assert r1.status_code == 200
            assert "请先设置八字" in r1.json()["personal_advice"]  # 兜底通用版
            assert get_cache().size == 1
            r2 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
            assert r2.json() == r1.json()   # 命中缓存
        finally:
            RAISE_IN_DAILY = False
