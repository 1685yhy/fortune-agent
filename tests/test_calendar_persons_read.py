"""G3c：calendar 今日运势读取路径与 G1 对话路径同源（persons 优先）。

背景：G3b 把今日运势缓存键改为 calendar:today:{date}:{指纹}（指纹取自读取
到的八字档案）；calendar.py 原仍读 users.bazi_info 旧字段 → P2 persons-only
建档不写 bazi_info → 建档用户指纹 "none" → 永远命中通用缓存（用户感知
「我建档了运势还是大众版」）。

修复：抽取 src/storage/birth_profile.get_user_birth_profile 为唯一实现
（handler 对话路径 + calendar 今日运势共用），指纹与实际驱动个性化计算的
档案同源——persons 建档用户指纹非 "none"，命中个性化 key（与 G3b 用例衔接）。

覆盖：
- 读取顺序：persons 默认档案优先（bazi_info 陈旧/缺失均以 persons 为准）；
  bazi_info-only 用户兜底；persons 无出生数据 → 走 bazi_info/None；无数据 None；
  gender 中文契约（男/女，不产出 male/female）；
- 指纹同源：端点返回后，get_user_birth_profile 返回值算出的指纹确实命中
  缓存（键 = calendar:today:{date}:{指纹}）→ 指纹与读取路径同源；
  persons 建档用户指纹非 "none"（不落通用缓存）；
- 端点行为：persons-only 用户 → 个性化（非通用版）；persons 出生数据变化
  → 指纹变化 → 当日立即重算；bazi_info-only 用户行为不回退（G3b 基线保持）。
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
from src.storage.birth_profile import get_user_birth_profile  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.utils.cache import get_cache  # noqa: E402

DATE = "2026-08-29"

# 个性化路径的假 daily 调用记录（验证缓存命中：只调一次）
LLM_CALLS = []


def _fake_daily(self, user_bazi, date_str=None, preferences=""):
    LLM_CALLS.append(date_str)
    from src.engines.calendar import CalendarDay
    bazi = user_bazi.get("bazi") or []
    day_stem = bazi[2][0] if len(bazi) >= 3 and bazi[2] else "?"
    day_stem2, day_branch = self._day_stem_branch(date_str)
    return CalendarDay(
        date=date_str, day_stem=day_stem2, day_branch=day_branch,
        yi=[{"action": "宜稳健", "time": "辰时7-9点", "reason": "测试"}],
        ji=[{"action": "忌冒进", "time": "全天", "reason": "测试"}],
        lucky_color="青色", lucky_direction="东南", lucky_number="7",
        overall_mood="测试基调",
    )


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
    LLM_CALLS.clear()


@pytest.fixture(autouse=True)
def clean_cache():
    get_cache().clear()
    yield
    get_cache().clear()


def _token(user_id: str) -> dict:
    tok = JWTHandler(os.environ["JWT_SECRET_KEY"]).create_token(user_id)
    return {"Authorization": f"Bearer {tok}"}


def _real_db(tmp_path):
    """真实 UserDAO + PersonDAO（同库，与生产一致）。"""
    dao = UserDAO(str(tmp_path / "u.db"))
    pdao = PersonDAO(str(tmp_path / "u.db"))
    return dao, pdao


def _create_person(pdao, user_id, gender="女", birth_year=1999,
                   month=2, day=26, hour=7, minute=0, city="北京",
                   is_default=True):
    pdao.create_person(
        user_id, name="我", relation="自己", is_default=is_default,
        birth={"gender": gender, "birth_year": birth_year,
               "birth_month": month, "birth_day": day,
               "birth_hour": hour, "birth_minute": minute,
               "calendar": "solar", "city": city})


def _client(dao):
    """挂载 calendar 路由的独立 app（注入真实 DAO，无 handler → 无 LLM key）。"""
    cal_api.setup(dao, None)
    app = FastAPI()
    app.include_router(cal_api.router)
    return TestClient(app)


# ================================================================
# 读取顺序（共享函数，真实 DAO；G1 语义保持 + G3c 同源基线）
# ================================================================

class TestSharedReadOrder:
    def test_persons_first_when_bazi_info_stale(self, tmp_path):
        """persons 默认档案（女）优先于陈旧 bazi_info（男），并自愈回写。"""
        dao, pdao = _real_db(tmp_path)
        _create_person(pdao, "u1", gender="女")
        dao.save_user_bazi("u1", {
            "year": 1999, "month": 2, "day": 26, "hour": 7, "minute": 0,
            "city": "北京", "gender": "男",
            "bazi": ["己卯", "丙寅", "己酉", "丁卯"],
        })
        saved = get_user_birth_profile(dao, "u1")
        assert saved is not None
        assert saved["gender"] == "女"      # 中文契约，不产出 male/female
        assert saved["year"] == 1999
        # 自愈：bazi_info 被 persons 回写为女，且既有 bazi 键保留
        bazi = dao.get_user_bazi("u1")
        assert bazi["gender"] == "女"
        assert bazi["bazi"] == ["己卯", "丙寅", "己酉", "丁卯"]

    def test_persons_only_when_bazi_info_missing(self, tmp_path):
        """P2 persons-only：bazi_info 完全缺失 → persons 出生数据可用（G3c 主场景）。"""
        dao, pdao = _real_db(tmp_path)
        _create_person(pdao, "u2", gender="女", birth_year=1988, month=6,
                       day=15, hour=14, minute=0, city="成都")
        saved = get_user_birth_profile(dao, "u2")
        assert saved is not None
        assert saved["year"] == 1988 and saved["gender"] == "女"
        assert saved["city"] == "成都"
        # 自愈：bazi_info 补写（persons → bazi_info 单向打通）
        assert dao.get_user_bazi("u2")["year"] == 1988

    def test_bazi_info_fallback_when_no_persons(self, tmp_path):
        """无 persons → bazi_info 兜底（G3b 既有行为保持）。"""
        dao, _ = _real_db(tmp_path)
        dao.save_user_bazi("u3", {
            "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
            "city": "北京", "gender": "男",
            "bazi": ["庚午", "辛巳", "甲申", "壬申"],
        })
        saved = get_user_birth_profile(dao, "u3")
        assert saved is not None
        assert saved["year"] == 1990 and saved["gender"] == "男"
        assert saved.get("bazi") == ["庚午", "辛巳", "甲申", "壬申"]

    def test_persons_without_birth_data_falls_to_bazi_info(self, tmp_path):
        """persons 无出生数据 → 退回 bazi_info（G1 行为保持）。"""
        dao, pdao = _real_db(tmp_path)
        _create_person(pdao, "u4", birth_year=None)
        dao.save_user_bazi("u4", {"year": 1990, "month": 5, "day": 20,
                                  "hour": 15, "minute": 0,
                                  "city": "北京", "gender": "男"})
        saved = get_user_birth_profile(dao, "u4")
        assert saved is not None
        assert saved["year"] == 1990 and saved["gender"] == "男"
        # persons 无数据 → 不触发自愈（bazi_info 不被覆盖为空）
        assert dao.get_user_bazi("u4")["year"] == 1990

    def test_no_data_returns_none(self, tmp_path):
        """无 persons、无 bazi_info → None（通用版兜底，指纹 "none"）。"""
        dao, _ = _real_db(tmp_path)
        assert get_user_birth_profile(dao, "u-none") is None


# ================================================================
# 端点：指纹同源 + persons 建档用户命中个性化 key（G3b 衔接）
# ================================================================

class TestCalendarPersonsFingerprint:
    def test_persons_user_personalized_and_fingerprint_same_source(self, tmp_path):
        """persons-only 建档用户：指纹非 "none"，且指纹与读取路径同源
        （get_user_birth_profile 返回值算出的指纹确实命中缓存键）。"""
        dao, pdao = _real_db(tmp_path)
        _create_person(pdao, "u-c1", gender="女", birth_year=1992, month=8,
                       day=15, hour=10, minute=0, city="北京")
        client = _client(dao)
        headers = _token("u-c1")

        r = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        # 个性化路径（非通用版：不出现「请先设置八字」）
        assert "请先设置八字" not in body["personal_advice"]
        assert len(LLM_CALLS) == 1

        # 指纹同源：用读取函数返回值算指纹 → 命中端点写入的缓存键
        saved = get_user_birth_profile(dao, "u-c1")
        fp = cal_api._profile_fingerprint(saved)
        assert fp != "none"                     # 建档用户不再落通用键
        cached = get_cache().get(f"calendar:today:{DATE}:{fp}", "u-c1")
        assert cached is not None and cached == body

    def test_generic_then_persons_switches_key_immediately(self, tmp_path):
        """建档前通用版 → persons 建档后立即个性化（指纹分键，与 G3b 用例衔接）。"""
        dao, pdao = _real_db(tmp_path)
        client = _client(dao)
        headers = _token("u-c2")

        r1 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert "请先设置八字" in r1.json()["personal_advice"]  # 通用版

        _create_person(pdao, "u-c2", gender="女", birth_year=1992, month=8,
                       day=15, hour=10, minute=0, city="北京")
        r2 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert "请先设置八字" not in r2.json()["personal_advice"]  # 立即个性化
        assert len(LLM_CALLS) == 1
        assert get_cache().size >= 2            # 通用版 + 个性化版并存分键

    def test_persons_data_change_invalidates_same_day(self, tmp_path):
        """persons 出生数据变化 → 指纹变化 → 当日立即重算（旧缓存天然失效）。"""
        dao, pdao = _real_db(tmp_path)
        _create_person(pdao, "u-c3", gender="女", birth_year=1992, month=8,
                       day=15, hour=10, minute=0, city="北京")
        client = _client(dao)
        headers = _token("u-c3")

        r1 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert len(LLM_CALLS) == 1
        fp1 = cal_api._profile_fingerprint(get_user_birth_profile(dao, "u-c3"))

        # 改 persons 出生年份 → 指纹变化 → 重算
        pid = pdao.list_persons("u-c3")[0]["id"]
        pdao.update_person("u-c3", pid, birth={"gender": "女", "birth_year": 1988,
                                               "birth_month": 6, "birth_day": 15,
                                               "birth_hour": 10, "birth_minute": 0,
                                               "calendar": "solar", "city": "北京"})
        r2 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert r2.status_code == 200
        fp2 = cal_api._profile_fingerprint(get_user_birth_profile(dao, "u-c3"))
        assert fp1 != fp2
        assert len(LLM_CALLS) == 2              # 旧缓存被指纹失效，重新生成
        assert get_cache().size == 2

    def test_bazi_info_only_user_behavior_unchanged(self, tmp_path):
        """bazi_info-only 用户：读取/指纹/缓存行为与 G3b 基线一致（不回退）。"""
        dao, _ = _real_db(tmp_path)
        dao.save_user_bazi("u-c4", {
            "year": 1992, "month": 8, "day": 15, "hour": 10, "minute": 0,
            "gender": "男", "calendar": "solar", "city": "北京",
            "bazi": ["壬申", "戊申", "甲午", "己巳"],
        })
        client = _client(dao)
        headers = _token("u-c4")

        r1 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        r2 = client.get(f"/api/calendar/today?date={DATE}", headers=headers)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == r2.json()
        assert len(LLM_CALLS) == 1              # 同档案命中缓存
        assert get_cache().size == 1
        fp = cal_api._profile_fingerprint(get_user_birth_profile(dao, "u-c4"))
        assert fp != "none"
