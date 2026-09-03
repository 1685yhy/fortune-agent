"""R2-5：农历生日直排修复（lunar 未转公历即排盘，起运差 5 年 P0）。

用户真机实锤：档案阴历 1999-03-28（= 阳历 1999-05-13）被当公历直排 →
引擎 7年4月起运（问真正确值 2年4月）。修复 = 数据一致性铁律落点：
storage 保留原始输入 + calendar 标记，消费点经单点 to_solar_date
转公历后进引擎；转换失败安全回落原值（logger.warning，不抛异常）。

本文件全部断言在修复前必失败（TDD）：to_solar_date 不存在 →
ImportError；calendar 键缺失 → 断言失败；_tool_bazi 引擎仍收 3/28 →
"7年4月" 断言失败；calendar 端点仍按阴历喂 daily → 断言失败。

运行：/home/a/fortune-agent/.venv/bin/python -m pytest tests/test_lunar_birth_solar_convert.py -q
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from unittest.mock import Mock  # noqa: E402

# ── 被修复模块（修复前 to_solar_date 不存在 → 收集期 ImportError = 先红） ──
from src.storage.birth_profile import (  # noqa: E402
    get_user_birth_profile,
    profile_fingerprint,
    to_solar_date,
)


# ================================================================
# 1) to_solar_date 单点转换（新增函数）
# ================================================================

class TestToSolarDate:
    def test_lunar_1999_3_28_converts_to_1999_5_13(self):
        """用户案例：阴历 1999-03-28 → 阳历 (1999, 5, 13)。"""
        assert to_solar_date(
            {"year": 1999, "month": 3, "day": 28, "calendar": "lunar"}
        ) == (1999, 5, 13)

    def test_solar_calendar_returns_none(self):
        """公历档案 → None（调用方直接原值，无需转换）。"""
        assert to_solar_date(
            {"year": 1999, "month": 3, "day": 28, "calendar": "solar"}
        ) is None

    def test_missing_calendar_returns_none(self):
        """无 calendar 键（旧档案）→ 缺省 solar 语义 → None 不抛。"""
        assert to_solar_date({"year": 1999, "month": 3, "day": 28}) is None

    def test_illegal_inputs_return_none_no_raise(self):
        """非法输入（不存在农历日/越界/类型错）→ None，绝不抛异常。"""
        cases = [
            {"year": 1999, "month": 13, "day": 1, "calendar": "lunar"},   # 无 13 月
            {"year": 1999, "month": 0, "day": 1, "calendar": "lunar"},    # 月 0
            {"year": 1999, "month": 3, "day": 0, "calendar": "lunar"},    # 日 0
            {"year": 1999, "month": 3, "day": 30, "calendar": "lunar"},   # 1999-03 仅 29 天（lunar-python 实测抛）
            {"year": 1899, "month": 3, "day": 28, "calendar": "lunar"},   # 前端 lunar.js 契约外（lunar-python 会转 → 必须挡）
            {"year": 2101, "month": 3, "day": 28, "calendar": "lunar"},   # 上限外
            {"year": "1999", "month": 3, "day": 28, "calendar": "lunar"},  # 非 int
        ]
        for prof in cases:
            assert to_solar_date(prof) is None, f"应安全回落 None: {prof}"

    def test_bounds_1900_2100_match_frontend_contract(self):
        """边界内 1900-01-01 / 2100-11-30（2100-12 仅 29 天，12-30 不存在）
        正常转换（与前端 lunar.js 范围一致）。"""
        assert to_solar_date(
            {"year": 1900, "month": 1, "day": 1, "calendar": "lunar"}
        ) is not None
        assert to_solar_date(
            {"year": 2100, "month": 11, "day": 30, "calendar": "lunar"}
        ) is not None


# ================================================================
# 2) get_user_birth_profile 三源 out 透传 calendar（不改 y/m/d 原值）
# ================================================================

def _real_db(tmp_path):
    from src.storage.dao import UserDAO
    from src.storage.person_dao import PersonDAO
    dao = UserDAO(str(tmp_path / "u.db"))
    pdao = PersonDAO(str(tmp_path / "u.db"))
    return dao, pdao


def _create_lunar_person(pdao, user_id):
    pdao.create_person(
        user_id, name="我", relation="自己", is_default=True,
        birth={"gender": "男", "birth_year": 1999, "birth_month": 3,
               "birth_day": 28, "birth_hour": 9, "birth_minute": None,
               "calendar": "lunar", "city": "吉林省长春市"})


class TestProfileCalendarPassthrough:
    def test_persons_source_carries_calendar_lunar_raw_dates_untouched(self, tmp_path):
        """persons 源：out 带 calendar='lunar'，year/month/day 原值 3/28 不被改。"""
        dao, pdao = _real_db(tmp_path)
        _create_lunar_person(pdao, "u-p1")
        out = get_user_birth_profile(dao, "u-p1")
        assert out["calendar"] == "lunar"
        assert (out["year"], out["month"], out["day"]) == (1999, 3, 28)

    def test_persons_source_solar_defaults_calendar_solar(self, tmp_path):
        """persons 源 solar 档案：calendar='solar'（显式）。"""
        dao, pdao = _real_db(tmp_path)
        pdao.create_person(
            "u-p2", name="我", relation="自己", is_default=True,
            birth={"gender": "女", "birth_year": 1990, "birth_month": 5,
                   "birth_day": 20, "birth_hour": 15, "birth_minute": 0,
                   "calendar": "solar", "city": "北京"})
        out = get_user_birth_profile(dao, "u-p2")
        assert out["calendar"] == "solar"
        assert (out["year"], out["month"], out["day"]) == (1990, 5, 20)

    def test_bazi_info_source_passthrough_calendar(self, tmp_path):
        """bazi_info 源：带 lunar 标记 → out 透传 calendar='lunar' + 原值。"""
        dao, _ = _real_db(tmp_path)
        dao.save_user_bazi("u-p3", {
            "year": 1999, "month": 3, "day": 28, "hour": 9, "minute": None,
            "city": "吉林省长春市", "gender": "男", "calendar": "lunar",
            "bazi": ["己卯", "己巳", "乙丑", "辛巳"],
        })
        out = get_user_birth_profile(dao, "u-p3")
        assert out["calendar"] == "lunar"
        assert (out["year"], out["month"], out["day"]) == (1999, 3, 28)

    def test_bazi_info_source_legacy_no_calendar_defaults_solar(self, tmp_path):
        """bazi_info 源旧行（无 calendar 键）→ out 缺省 'solar'，原值不改。"""
        dao, _ = _real_db(tmp_path)
        dao.save_user_bazi("u-p4", {
            "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
            "city": "北京", "gender": "男",
        })
        out = get_user_birth_profile(dao, "u-p4")
        assert out["calendar"] == "solar"
        assert (out["year"], out["month"], out["day"]) == (1990, 5, 20)

    def test_chart_source_passthrough_calendar(self, tmp_path):
        """chart_records 源：birth 带 lunar 标记 → out 透传 calendar='lunar'。"""
        dao, pdao = _real_db(tmp_path)
        from src.storage.chart_dao import ChartDAO
        cdao = ChartDAO(str(tmp_path / "c.db"))
        cdao.save_chart(
            "u-p5", None,
            {"year": 1999, "month": 3, "day": 28, "hour": 9, "minute": None,
             "city": "吉林省长春市", "gender": "男", "calendar": "lunar"},
            {"bazi": ["己卯", "己巳", "乙丑", "辛巳"]})
        out = get_user_birth_profile(dao, "u-p5", chart_dao=cdao)
        assert out is not None
        assert out["calendar"] == "lunar"
        assert (out["year"], out["month"], out["day"]) == (1999, 3, 28)

    def test_self_heal_syncs_calendar_marker_to_bazi_info(self, tmp_path):
        """自愈：persons lunar + 旧 bazi_info（无 calendar 键）→ 回写 calendar='lunar'
        （否则 bazi_info 源未来把阴历当公历，G1 单点双向打通含新键）。"""
        dao, pdao = _real_db(tmp_path)
        _create_lunar_person(pdao, "u-p6")
        dao.save_user_bazi("u-p6", {   # 模拟旧 bazi_info：无 calendar 键
            "year": 1999, "month": 3, "day": 28, "hour": 9, "minute": None,
            "city": "吉林省长春市", "gender": "男",
        })
        get_user_birth_profile(dao, "u-p6")
        assert dao.get_user_bazi("u-p6").get("calendar") == "lunar"

    def test_fingerprint_differs_when_calendar_key_added(self):
        """指纹含 calendar：同一档案 +calendar 键前后指纹不同 →
        转公历修复后 lunar 用户不再命中修复前（阴历当公历）生成的旧缓存键。"""
        prof = {"year": 1999, "month": 3, "day": 28, "hour": 9,
                "minute": None, "city": "吉林省长春市", "gender": "男"}
        old = dict(prof)
        new = dict(prof)
        new["calendar"] = "lunar"
        assert profile_fingerprint(new) != profile_fingerprint(old)


# ================================================================
# 3) handler._tool_bazi 档案兜底：lunar → 引擎收公历；solar 不回退
# ================================================================

class _RecordingEngine:
    """记录 calculate 实参并透传真实 BaziEngine 结果。"""

    def __init__(self):
        self.real = None  # 延迟装配避免收集期重复 import 顶层引擎
        self.calls = []
        self.results = []

    def calculate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        r = self.real.calculate(*args, **kwargs)
        self.results.append(r)
        return r


def _tool_handler(tmp_path, engine=None):
    """object.__new__ 手工装配 MessageHandler（真实 DAO + 真实引擎）。"""
    from src.bot.handler import MessageHandler
    from src.storage.dao import UserDAO
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.dao = UserDAO(str(tmp_path / "p.db"))
    h.memory_system = None
    h._analysis_facts = {}
    h._citations = {}
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    if engine is not None:
        h.engine = engine
    return h


class TestToolBaziLunarArchive:
    def test_engine_receives_converted_solar_and_qiyun_2y4m(self, tmp_path):
        """端到端：lunar 档案（1999-03-28）兜底排盘 → 引擎实收 (1999,5,13)，
        起运 2年4月（问真口径）；修复前收 (1999,3,28) → 7年4月（断言必失败）。"""
        from src.bot.handler import MessageHandler
        from src.engines.bazi import BaziEngine
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _tool_handler(tmp_path, engine=rec)
        dao = h.dao
        from src.storage.person_dao import PersonDAO
        pdao = PersonDAO(dao.db_path)
        _create_lunar_person(pdao, "u-b1")

        tr = h._tool_bazi("帮我排个盘", "u-b1")
        assert tr.ok, tr.text
        # 引擎实参 = 转换后公历（修复前 = (1999,3,28,...) → 断言失败）
        args = rec.calls[0][0]
        assert args[:3] == (1999, 5, 13), f"引擎应收公历 1999-5-13，实收 {args[:3]}"
        # 起运口径 = 问真（与直接按公历调用逐字一致），不再是 7年4月
        ref = BaziEngine().calculate(1999, 5, 13, 9, 0, "吉林省长春市", "男")
        assert rec.results[0].qiyun_desc == ref.qiyun_desc
        assert "2年4月" in rec.results[0].qiyun_desc
        assert "7年4月" not in rec.results[0].qiyun_desc

    def test_archive_raw_persisted_with_lunar_marker_not_clobbered(self, tmp_path):
        """保存回写三处（bazi_info/persons/chart_records）均保留原始 3/28 +
        calendar='lunar'——persons 原始输入事实源不被转成 5/13，否则下次
        档案读取需再转才知是阴历（自毁式修复）。"""
        from src.bot.handler import MessageHandler
        from src.engines.bazi import BaziEngine
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _tool_handler(tmp_path, engine=rec)
        dao = h.dao
        pdao = PersonDAO(dao.db_path)
        _create_lunar_person(pdao, "u-b2")

        tr = h._tool_bazi("帮我排个盘", "u-b2")
        assert tr.ok, tr.text
        # persons 原值 + 标记不被覆盖（修复前 _sync_person_profile 缺 calendar →
        # person_dao 全量替换默认 'solar' → 标记被抹掉 → 断言失败）
        p = pdao.get_default_person("u-b2")
        assert (p["birth_year"], p["birth_month"], p["birth_day"]) == (1999, 3, 28)
        assert p["calendar"] == "lunar"
        # bazi_info：原始值 + 标记（修复前只写 7 键，无 calendar → 断言失败）
        bazi = dao.get_user_bazi("u-b2")
        assert (bazi["year"], bazi["month"], bazi["day"]) == (1999, 3, 28)
        assert bazi.get("calendar") == "lunar"
        # chart_records：birth 原始值 + 标记（修复前 _persist_chart_result
        # 硬编码 "calendar": "solar" 且日期为转换后值 → 断言失败）
        chart = h.chart_dao.get_latest_chart("u-b2")
        assert chart is not None
        assert (chart["birth"]["year"], chart["birth"]["month"],
                chart["birth"]["day"]) == (1999, 3, 28)
        assert chart["birth"]["calendar"] == "lunar"
        # 引擎四柱 = 公历盘（5/13 → 乙丑日主），不是 3/28 盘（己卯日主）
        assert chart["bazi_json"]["bazi"] == ["己卯", "己巳", "乙丑", "辛巳"]

    def test_solar_profile_fallback_behavior_unchanged(self, tmp_path):
        """solar 档案兜底对照：引擎收原值、persist 原值——零行为回退。"""
        from src.bot.handler import MessageHandler
        from src.engines.bazi import BaziEngine
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _tool_handler(tmp_path, engine=rec)
        dao = h.dao
        pdao = PersonDAO(dao.db_path)
        pdao.create_person(
            "u-b3", name="我", relation="自己", is_default=True,
            birth={"gender": "男", "birth_year": 1990, "birth_month": 5,
                   "birth_day": 20, "birth_hour": 15, "birth_minute": 30,
                   "calendar": "solar", "city": "北京"})
        tr = h._tool_bazi("帮我排个盘", "u-b3")
        assert tr.ok, tr.text
        assert rec.calls[0][0][:3] == (1990, 5, 20)
        ref = BaziEngine().calculate(1990, 5, 20, 15, 30, "北京", "男")
        assert rec.results[0].qiyun_desc == ref.qiyun_desc
        p = pdao.get_default_person("u-b3")
        assert (p["birth_year"], p["birth_month"], p["birth_day"]) == (1990, 5, 20)
        assert p["calendar"] == "solar"

    def test_lunar_archive_display_text_uses_converted_solar_date(self, tmp_path):
        """_fmt_birth_text：lunar 档案 → 「1999年5月13日」（公历口径文本，
        下游 _extract_bazi_info 按公历解析正确）；solar 文本逐字节不变。"""
        from src.bot.handler import MessageHandler
        h = object.__new__(MessageHandler)
        lunar = {"year": 1999, "month": 3, "day": 28, "hour": 9,
                 "minute": None, "city": "吉林省长春市", "gender": "男",
                 "calendar": "lunar"}
        txt = h._fmt_birth_text(lunar)
        assert "1999年5月13日" in txt          # 修复前 = 1999年3月28日 → 失败
        assert "3月28日" not in txt
        assert "9时" in txt and "长春" in txt and txt.endswith("男")
        solar = {"year": 1990, "month": 5, "day": 20, "hour": 15,
                 "minute": 30, "city": "北京", "gender": "男",
                 "calendar": "solar"}
        assert h._fmt_birth_text(solar) == "1990年5月20日 15时30分 北京 男"
        legacy = {"year": 1990, "month": 5, "day": 20, "hour": 15,
                  "minute": 30, "city": "北京", "gender": "男"}   # 无 calendar 键
        assert h._fmt_birth_text(legacy) == "1990年5月20日 15时30分 北京 男"


# ================================================================
# 4) src/api/calendar.py：lunar 档案 → daily 收转换副本 + 旧缓存不命中
# ================================================================

DAILY_ARGS = []


def _spy_daily(self, user_bazi, date_str=None, preferences=""):
    """daily 间谍：记录入参（转换副本必须是 1999/5/13）。"""
    from src.engines.calendar import CalendarDay
    DAILY_ARGS.append(dict(user_bazi))
    day_stem, day_branch = self._day_stem_branch(date_str or "2026-09-03")
    return CalendarDay(
        date=date_str, day_stem=day_stem, day_branch=day_branch,
        yi=[{"action": "宜稳健", "time": "辰时7-9点", "reason": "测试"}],
        ji=[{"action": "忌冒进", "time": "全天", "reason": "测试"}],
        lucky_color="青色", lucky_direction="东南", lucky_number="7",
        overall_mood="测试基调",
    )


@pytest.fixture(autouse=True)
def _cal_env(monkeypatch):
    from src.api import calendar as cal_api
    from src.security.auth import set_auth_handler, AuthHandler
    from src.utils.cache import get_cache
    set_auth_handler(AuthHandler())
    DAILY_ARGS.clear()
    monkeypatch.setattr("src.engines.calendar.LuckyCalendar.daily", _spy_daily)
    get_cache().clear()
    yield
    get_cache().clear()
    set_auth_handler(None)


def _cal_client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import calendar as cal_api
    from src.storage.person_dao import PersonDAO
    dao, pdao = _real_db(tmp_path)
    cal_api.setup(dao, None)
    app = FastAPI()
    app.include_router(cal_api.router)
    return TestClient(app), dao, pdao


def _cal_headers(user_id):
    from src.security.auth import JWTHandler
    tok = JWTHandler(os.environ["JWT_SECRET_KEY"]).create_token(user_id)
    return {"Authorization": f"Bearer {tok}"}


DATE2 = "2026-09-03"


class TestCalendarLunarConverted:
    def test_daily_receives_converted_copy_not_raw(self, tmp_path):
        """lunar 档案（1999-03-28）：daily 实收转换副本 1999/5/13；
        档案原对象（含缓存指纹依据）不被 mutate（后读仍 3/28）。"""
        client, dao, pdao = _cal_client(tmp_path)
        _create_lunar_person(pdao, "u-c1")
        headers = _cal_headers("u-c1")
        r = client.get(f"/api/calendar/today?date={DATE2}", headers=headers)
        assert r.status_code == 200
        assert len(DAILY_ARGS) == 1, "daily 必须被调用（个性化路径）"
        got = DAILY_ARGS[0]
        assert (got["year"], got["month"], got["day"]) == (1999, 5, 13), \
            f"daily 应收转换副本 1999/5/13，实收 {got.get('year')}-{got.get('month')}-{got.get('day')}"
        # saved 原对象未 mutate：再读档案仍是原始阴历（指纹/缓存键语义稳定）
        again = get_user_birth_profile(dao, "u-c1")
        assert (again["year"], again["month"], again["day"]) == (1999, 3, 28)
        assert again["calendar"] == "lunar"

    def test_lunar_user_never_hits_pre_fix_wrong_cache(self, tmp_path):
        """缓存一致性红线：修复前按阴历当公历生成的旧缓存键（指纹无 calendar）
        绝不被命中 → daily 仍被调用；修复后新键第二次请求命中缓存。"""
        from src.api import calendar as cal_api
        from src.utils.cache import get_cache
        from src.storage.birth_profile import profile_fingerprint
        client, dao, pdao = _cal_client(tmp_path)
        _create_lunar_person(pdao, "u-c2")
        headers = _cal_headers("u-c2")
        # 预置「修复前」旧缓存：档案 out 无 calendar 键（旧指纹 → 旧键）
        old_profile = {"year": 1999, "month": 3, "day": 28, "hour": 9,
                       "minute": None, "city": "吉林省长春市", "gender": "男"}
        old_fp = hashlib.md5(
            json.dumps(old_profile, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        old_key = f"calendar:today:{DATE2}:{old_fp}"
        get_cache().set(old_key, {"stale": "阴历当公历的旧错缓存"}, "u-c2",
                        ttl_seconds=3600)

        r1 = client.get(f"/api/calendar/today?date={DATE2}", headers=headers)
        assert r1.status_code == 200
        # 修复前：新指纹 == 旧指纹 → 命中旧错缓存 → daily 零调用 → 断言失败
        assert len(DAILY_ARGS) == 1, "命中旧（修复前阴历当公历）缓存 = 回归"

        # 修复后新键（指纹含 calendar）第二次请求命中缓存，不重复调 daily
        r2 = client.get(f"/api/calendar/today?date={DATE2}", headers=headers)
        assert r2.status_code == 200
        assert len(DAILY_ARGS) == 1
        new_fp = profile_fingerprint(get_user_birth_profile(dao, "u-c2"))
        assert get_cache().get(f"calendar:today:{DATE2}:{new_fp}", "u-c2") is not None

    def test_solar_profile_daily_gets_raw_values(self, tmp_path):
        """solar 档案对照：daily 收原值（零行为回退）。"""
        client, _, pdao = _cal_client(tmp_path)
        pdao.create_person(
            "u-c3", name="我", relation="自己", is_default=True,
            birth={"gender": "女", "birth_year": 1990, "birth_month": 5,
                   "birth_day": 20, "birth_hour": 15, "birth_minute": 0,
                   "calendar": "solar", "city": "北京"})
        headers = _cal_headers("u-c3")
        r = client.get(f"/api/calendar/today?date={DATE2}", headers=headers)
        assert r.status_code == 200
        assert (DAILY_ARGS[0]["year"], DAILY_ARGS[0]["month"],
                DAILY_ARGS[0]["day"]) == (1990, 5, 20)
