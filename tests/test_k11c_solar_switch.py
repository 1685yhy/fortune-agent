# -*- coding: utf-8 -*-
"""k11c 档案级真太阳时开关（2026-09-08，用户 2026-09-06 拍板：默认开 + 档案开关）。

背景：R2-4 定稿真太阳时默认开（引擎层）；R2-1 concern「solarTime 未随盘落库，
重排丢开关信息」——本批把开关落到 persons 档案（birth_enc 密文内字段，缺省
=默认开兼容，零迁移），读取链 get_user_birth_profile 出参带 solar_time，引擎
消费链（排盘/运势/日历）随档案开关排盘，缓存指纹掺 solar_time（开/关切换 →
calendar:today 与对话缓存键换键当日立即重算）。

golden：1999-05-13 10:55 长春 男（问真锚点日，日柱乙丑）
  开（solar_time=True）= 修正 11:20 午时 → 时柱壬午
  关（solar_time=False）= 北京时间直排 巳时 → 时柱辛巳
"""
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.engines.bazi import BaziEngine  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.person_dao import PersonDAO, solar_time_on  # noqa: E402
from src.storage.birth_profile import (  # noqa: E402
    get_user_birth_profile, profile_fingerprint)

ENGINE = BaziEngine()

# golden 档案：1999-05-13 10:55 长春 男（问真锚点日；开=壬午午时 / 关=辛巳巳时）
GOLDEN = dict(year=1999, month=5, day=13, hour=10, minute=55,
              city="长春", gender="男", calendar="solar")
GOLDEN_BAZI_ON = ["己卯", "己巳", "乙丑", "壬午"]   # 午时
GOLDEN_BAZI_OFF = ["己卯", "己巳", "乙丑", "辛巳"]  # 巳时


# ───────────────────────── ① golden：引擎逐字断言 ─────────────────────────

def test_golden_changchun_1055_solar_on_off():
    """10:55 长春男：开 → 修正 11:20 午时壬午；关 → 10:55 巳时辛巳。
    年月日柱（开/关）逐字一致，仅时柱随开关翻转（golden 对照）。"""
    on = ENGINE.calculate(GOLDEN["year"], GOLDEN["month"], GOLDEN["day"],
                          GOLDEN["hour"], GOLDEN["minute"], "长春", "男",
                          solar_time=True)
    off = ENGINE.calculate(GOLDEN["year"], GOLDEN["month"], GOLDEN["day"],
                           GOLDEN["hour"], GOLDEN["minute"], "长春", "男",
                           solar_time=False)
    assert on.corrected_time == "11:20"
    assert off.corrected_time == "10:55"        # 关 = 北京时间原样
    assert list(on.bazi) == GOLDEN_BAZI_ON       # 含壬午（午时）
    assert list(off.bazi) == GOLDEN_BAZI_OFF     # 含辛巳（巳时）
    # 年月日柱逐字一致（开关只影响时柱归柱/归日，不扰动年/月/日柱）
    assert on.bazi[:3] == off.bazi[:3] == GOLDEN_BAZI_ON[:3]
    # 默认不传参 = 开（R2-4 默认开口径不回归）
    default = ENGINE.calculate(GOLDEN["year"], GOLDEN["month"], GOLDEN["day"],
                               GOLDEN["hour"], GOLDEN["minute"], "长春", "男")
    assert list(default.bazi) == GOLDEN_BAZI_ON


def test_golden_with_province_full_city_path():
    """档案 city='吉林省长春市'（R2-6 行政区划全路径）→ 开关口径不变。"""
    on = ENGINE.calculate(1999, 5, 13, 10, 55, "吉林省长春市", "男",
                          solar_time=True)
    off = ENGINE.calculate(1999, 5, 13, 10, 55, "吉林省长春市", "男",
                           solar_time=False)
    assert list(on.bazi) == GOLDEN_BAZI_ON
    assert list(off.bazi) == GOLDEN_BAZI_OFF


# ───────────────────── 时钟边界：开关不改 23:xx/00:00 归日 ─────────────────────
# 口径（引擎既有注释）：晚子时/归日判定基于实际排盘时刻——开=真太阳时修正后、
# 关=北京时间原样；23:xx/00:00 的跨日归日逻辑不受开关影响（关时 23:40 长春仍按
# 晚子时归次日；开时 23:40 修正 00:05 亦归次日——两口径四柱逐字一致）。

def test_midnight_boundary_2340_changchun_switch_independent():
    """23:40 长春（开=修正 00:05 次日 / 关=晚子时归次日）：日柱均次日，四柱
    开/关逐字一致（跨日归日不受开关影响）。"""
    on = ENGINE.calculate(1999, 5, 13, 23, 40, "长春", "男", solar_time=True)
    off = ENGINE.calculate(1999, 5, 13, 23, 40, "长春", "男", solar_time=False)
    assert on.corrected_time == "00:05"          # 开：修正跨日
    assert on.bazi[2] != "乙丑"                   # 日柱确已归次日
    assert list(on.bazi) == list(off.bazi)        # 开关不改变归日后的四柱


def test_early_midnight_0030_switch_independent():
    """00:30 长春：开=修正后仍在子时段（不跨时辰/日），关=原样——四柱一致。"""
    on = ENGINE.calculate(1999, 5, 13, 0, 30, "长春", "男", solar_time=True)
    off = ENGINE.calculate(1999, 5, 13, 0, 30, "长春", "男", solar_time=False)
    assert on.bazi[3][1] == "子"
    assert list(on.bazi) == list(off.bazi)


def test_birthday_day_boundary_not_affected_by_switch():
    """归日/月柱节令边界由排盘日期本身决定，开关只作用于经度+均时差修正：
    修正不跨日时，开/关 年/月柱与日柱恒一致（时柱可能按午/巳翻转的边界外）。"""
    for h, mi in ((9, 0), (11, 30), (13, 0)):    # 无跨日的整点样本
        on = ENGINE.calculate(1999, 5, 13, h, mi, "长春", "男", solar_time=True)
        off = ENGINE.calculate(1999, 5, 13, h, mi, "长春", "男",
                               solar_time=False)
        assert on.bazi[:3] == off.bazi[:3], (h, mi)


# ───────────────────────── ② 旧档案无字段 → 默认开 ─────────────────────────

def test_person_dao_legacy_row_defaults_on(tmp_path):
    """旧档案（birth_enc 无 solar_time）→ 读取默认开=1（兼容，无迁移重写）。"""
    db = str(tmp_path / "legacy.db")
    pdao = PersonDAO(db)
    p = pdao.create_person("u_legacy", "我", "自己", birth={
        "gender": "男", "birth_year": 1999, "birth_month": 5, "birth_day": 13,
        "birth_hour": 10, "birth_minute": 55, "calendar": "solar",
        "city": "长春"})  # 不带 solar_time = 旧建档路径
    assert p["solar_time"] == 1          # 缺省开
    got = pdao.get_person("u_legacy", p["id"])
    assert got["solar_time"] == 1
    lst = pdao.list_persons("u_legacy")
    assert lst[0]["solar_time"] == 1


def test_solar_time_on_reader_normalization():
    """读口径归一：None/缺失/非法/空 → 1（默认开）；0/'0'/False → 0。"""
    assert solar_time_on(None) == 1
    assert solar_time_on(1) == 1
    assert solar_time_on(0) == 0
    assert solar_time_on(False) == 0
    assert solar_time_on("0") == 0
    assert solar_time_on("1") == 1
    assert solar_time_on("") == 1
    assert solar_time_on("garbage") == 1


# ───────────────────────── 档案读写往返（DAO + 读取链） ─────────────────────────

def _seed_person(pdao, uid="u_rt", solar_time=0):
    return pdao.create_person(uid, "我", "自己", birth={
        "gender": "男", "birth_year": 1999, "birth_month": 5, "birth_day": 13,
        "birth_hour": 10, "birth_minute": 55, "calendar": "solar",
        "city": "长春", "solar_time": solar_time})


def test_person_dao_solar_time_roundtrip(tmp_path):
    """create 关(0) → 读 0；仅 solar_time 翻转（0↔1）往返；无关字段更新不吞开关。"""
    db = str(tmp_path / "rt.db")
    pdao = PersonDAO(db)
    p = _seed_person(pdao)
    assert p["solar_time"] == 0
    # 只提交开关字段 → 翻转成功（BIRTH_KEYS 含 solar_time，any() 触发合并）
    p2 = pdao.update_person("u_rt", p["id"], birth={"solar_time": 1})
    assert p2["solar_time"] == 1
    assert p2["birth_year"] == 1999           # 其余字段保留
    # 不带 solar_time 的普通更新 → 不覆盖开关（0 保留）
    pdao.update_person("u_rt", p["id"], birth={"solar_time": 0})
    p3 = pdao.update_person("u_rt", p["id"], birth={"city": "北京"})
    assert p3["solar_time"] == 0
    assert p3["city"] == "北京"


def test_birth_profile_read_chain_includes_solar_time(tmp_path):
    """读取链 get_user_birth_profile 出参带 solar_time：
    persons 权威 0/1 透传；bazi_info-only 旧用户（无键）→ 默认开。"""
    db = str(tmp_path / "chain.db")
    udao = UserDAO(db)
    pdao = PersonDAO(db)
    # ① persons 关
    _seed_person(pdao, uid="u_a", solar_time=0)
    prof = get_user_birth_profile(udao, "u_a")
    assert prof is not None and prof["solar_time"] == 0
    assert prof["year"] == 1999
    # ② bazi_info-only（无 persons）→ 默认开
    udao.save_user_bazi("u_b", {"year": 1990, "month": 5, "day": 20,
                                "hour": 15, "minute": 0, "gender": "男",
                                "calendar": "solar", "city": "北京"})
    prof_b = get_user_birth_profile(udao, "u_b")
    assert prof_b is not None and prof_b["solar_time"] == 1


def test_profile_fingerprint_changes_with_solar_time():
    """开/关切换 → 档案指纹变化（calendar:today / 对话缓存键同源换键）。"""
    base = {"year": 1999, "month": 5, "day": 13, "hour": 10, "minute": 55,
            "city": "长春", "gender": "男", "calendar": "solar",
            "solar_time": 1}
    off = dict(base, solar_time=0)
    assert profile_fingerprint(base) != profile_fingerprint(off)
    # 同开关值指纹稳定（缓存命中不抖动）
    assert profile_fingerprint(dict(base)) == profile_fingerprint(base)


# ───────────────────────── ③ H-7 同口径：calendar:today 换键 ─────────────────────────

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api import calendar as cal_api  # noqa: E402
from src.security.auth import (  # noqa: E402
    AuthHandler, JWTHandler, set_auth_handler)
from src.utils.cache import get_cache  # noqa: E402

_DATE = "2026-09-08"
_LLM_CALLS = []
_FAKE_COLOR = {"甲": "青色", "乙": "玄色"}


def _fake_daily(self, user_bazi, date_str=None, preferences=""):
    _LLM_CALLS.append(date_str)
    from src.engines.calendar import CalendarDay
    bazi = user_bazi.get("bazi") or []
    day_stem = bazi[2][0] if len(bazi) >= 3 and bazi[2] else "?"
    day_stem2, day_branch = self._day_stem_branch(date_str)
    return CalendarDay(
        date=date_str, day_stem=day_stem2, day_branch=day_branch,
        yi=[{"action": "宜稳健", "time": "辰时7-9点", "reason": "测试"}],
        ji=[{"action": "忌冒进", "time": "全天", "reason": "测试"}],
        lucky_color=_FAKE_COLOR.get(day_stem, "紫色"),
        lucky_direction="东南", lucky_number="7", overall_mood="测试基调")


class _FakeDao:
    def __init__(self, bazi=None):
        self.bazi = bazi

    def get_user_bazi(self, user_id):
        return self.bazi


@pytest.fixture(autouse=True)
def _k11c_auth():
    set_auth_handler(AuthHandler())
    yield
    set_auth_handler(None)


@pytest.fixture(autouse=True)
def _k11c_no_llm(monkeypatch):
    _LLM_CALLS.clear()
    monkeypatch.setattr("src.engines.calendar.LuckyCalendar.daily", _fake_daily)
    yield
    _LLM_CALLS.clear()


@pytest.fixture(autouse=True)
def _k11c_clean_cache():
    get_cache().clear()
    yield
    get_cache().clear()


def _token(user_id: str) -> dict:
    tok = JWTHandler(os.environ["JWT_SECRET_KEY"]).create_token(user_id)
    return {"Authorization": f"Bearer {tok}"}


def _client(dao):
    cal_api.setup(dao, None)
    app = FastAPI()
    app.include_router(cal_api.router)
    return TestClient(app)


def test_calendar_today_key_flips_on_solar_toggle():
    """开/关切换 → calendar:today 换键（同 H-7 断言模式）：当日立即重算不
    命中 6h 旧缓存（指纹掺 solar_time 的实现同源 profile_fingerprint）。"""
    profile = {
        "year": 1999, "month": 5, "day": 13, "hour": 10, "minute": 55,
        "gender": "男", "calendar": "solar", "city": "长春",
        "solar_time": 1,
        "bazi": ["己卯", "己巳", "乙丑", "壬午"],  # 日主乙 → 玄色
    }
    dao = _FakeDao(dict(profile))
    client = _client(dao)
    headers = _token("u-k11c-cache")
    r1 = client.get(f"/api/calendar/today?date={_DATE}", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["lucky_color"] == "玄色"
    assert len(_LLM_CALLS) == 1

    dao.bazi = dict(profile, solar_time=0)       # 档案切关 → 指纹变化
    r2 = client.get(f"/api/calendar/today?date={_DATE}", headers=headers)
    assert r2.status_code == 200
    assert len(_LLM_CALLS) == 2                  # 未命中旧缓存，重算一次
    assert get_cache().size == 2                 # 新旧键并存（互不覆盖）

    # 同键状态重复请求命中缓存（零新增 LLM 调用）
    r3 = client.get(f"/api/calendar/today?date={_DATE}", headers=headers)
    assert r3.json() == r2.json()
    assert len(_LLM_CALLS) == 2


# ───────────────────────── ④ API 存取往返 ─────────────────────────

from src.main import app as main_app  # noqa: E402
from src.api import user as user_api  # noqa: E402


def _api(tmp_path, name):
    db = str(tmp_path / f"{name}.db")
    udao = UserDAO(db)
    user_api.setup(udao, None, AuthHandler())
    set_auth_handler(AuthHandler())
    return TestClient(main_app), udao, db


def _api_headers(uid: str) -> dict:
    return {"Authorization": f"Bearer {JWTHandler('test-secret-key-32-bytes-long!!').create_token(uid)}"}


def test_persons_api_solar_time_roundtrip(tmp_path):
    """/api/persons POST/PUT：solar_time 0/1 存取往返 + 列表透传。"""
    client, udao, db = _api(tmp_path, "k11c_api")
    headers = _api_headers("u_k11c_api")
    r = client.post("/api/persons", json={
        "name": "小晚", "relation": "自己", "gender": "男",
        "birth_year": 1999, "birth_month": 5, "birth_day": 13,
        "birth_hour": 10, "birth_minute": 55,
        "calendar": "solar", "city": "长春", "solar_time": 0,
    }, headers=headers)
    assert r.status_code == 200, r.text
    pid = r.json()["person"]["id"]
    assert r.json()["person"]["solar_time"] == 0
    # 列表透传
    lst = client.get("/api/persons", headers=headers).json()["persons"]
    assert lst[0]["solar_time"] == 0
    # PUT 翻转 0 → 1（其余字段保留）
    r2 = client.put(f"/api/persons/{pid}", json={
        "name": "小晚", "gender": "男", "solar_time": 1,
    }, headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["person"]["solar_time"] == 1
    assert r2.json()["person"]["birth_year"] == 1999
    # 旧请求（不带 solar_time）→ 不覆盖（保持 1）
    r3 = client.put(f"/api/persons/{pid}", json={
        "name": "小晚", "gender": "男",
    }, headers=headers)
    assert r3.json()["person"]["solar_time"] == 1
    # 切 0 后未传 solar_time 的普通保存 → 仍保持 0（r1 F1：不静默写回 1）
    r4 = client.put(f"/api/persons/{pid}", json={
        "name": "小晚", "gender": "男", "solar_time": 0,
    }, headers=headers)
    assert r4.json()["person"]["solar_time"] == 0
    r5 = client.put(f"/api/persons/{pid}", json={
        "name": "小晚", "gender": "男",
    }, headers=headers)
    assert r5.json()["person"]["solar_time"] == 0


def test_persons_api_create_without_solar_time_defaults_on(tmp_path):
    """创建不带 solar_time（旧调用方/前端未改动路径）→ 默认开=1（r1 F1 兜底）。"""
    client, udao, db = _api(tmp_path, "k11c_api_default")
    headers = _api_headers("u_k11c_def")
    r = client.post("/api/persons", json={
        "name": "小晚", "relation": "自己", "gender": "男",
        "birth_year": 1999, "birth_month": 5, "birth_day": 13,
        "birth_hour": 10, "birth_minute": 55,
        "calendar": "solar", "city": "长春",
    }, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["person"]["solar_time"] == 1


def test_user_bazi_api_solar_time_roundtrip(tmp_path):
    """/api/user/bazi（本人档案）solar_time 落默认命主 + bazi_info；未传不覆盖。"""
    client, udao, db = _api(tmp_path, "k11c_bazi")
    headers = _api_headers("u_k11c_bazi")
    base = {
        "birth_year": 1999, "birth_month": 5, "birth_day": 13,
        "birth_hour": 10, "birth_minute": 55,
        "gender": "男", "calendar": "solar", "city": "长春",
    }
    r = client.post("/api/user/bazi", json=dict(base, solar_time=0),
                    headers=headers)
    assert r.status_code == 200, r.text
    pdao = PersonDAO(db)
    default = pdao.get_default_person("u_k11c_bazi")
    assert default["solar_time"] == 0
    # 旧调用方不传 solar_time → 保留 0（不重置默认开）
    r2 = client.post("/api/user/bazi", json=dict(base, birth_minute=0),
                     headers=headers)
    assert r2.status_code == 200, r2.text
    assert pdao.get_default_person("u_k11c_bazi")["solar_time"] == 0
    # 显式翻回 1
    client.post("/api/user/bazi", json=dict(base, solar_time=1), headers=headers)
    assert pdao.get_default_person("u_k11c_bazi")["solar_time"] == 1
    # profile 出参（读取链）带 solar_time
    prof = client.get("/api/user/profile", headers=headers).json()
    assert prof["bazi_info"]["year"] == 1999


# ───────────────────────── k11c r1 审查修复：F2/F3 ─────────────────────────
# F2（对齐）：_tool_bazi 文本直排路径随档案开关（本人同年 → 与 _handle_bazi
# parsed 直排同口径）；F3（镜像）：persons PUT 开关翻转 → users.bazi_info 同步镜像。

class _RecordingEngine:
    """记录 calculate 实参并透传真实 BaziEngine 结果（r1 F2 用）。"""

    def __init__(self):
        self.real = BaziEngine()
        self.calls = []

    def calculate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.real.calculate(*args, **kwargs)


def _tool_handler(tmp_path, uid="u_f2", db_path=None):
    """object.__new__ 装配（R2-6 同款）：真实 DAO + 记录引擎（0 LLM）。

    db_path：persons/users 同库路径（与测试种子同一文件）；缺省自建。
    """
    from src.bot.handler import MessageHandler
    from src.storage.dao import UserDAO
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.dao = UserDAO(db_path or str(tmp_path / "t.db"))
    h.chart_dao = ChartDAO(str(tmp_path / "tc.db"))
    h.memory_system = None
    h.memory = None
    h._analysis_facts = {}
    h._citations = {}
    h._downgraded = {}
    h._deep_night = {}
    h._gender_acks = {}
    h.session_dao = None
    h.llm = None
    rec = _RecordingEngine()
    h.engine = rec
    return h, rec


def _seed_f2_person(pdao, uid, solar_time):
    return pdao.create_person(uid, name="我", relation="自己",
                              is_default=True, birth={
                                  "gender": "男", "birth_year": 1999,
                                  "birth_month": 5, "birth_day": 13,
                                  "birth_hour": 10, "birth_minute": 55,
                                  "calendar": "solar", "city": "长春",
                                  "solar_time": solar_time})


def test_tool_bazi_text_path_follows_archive_solar(tmp_path):
    """r1 F2：_tool_bazi 文本直排（本人同年）随档案开关——档案关(0) → 引擎
    solar_time=False（辛巳）；档案开(1)/无档案/消息年份不同 → 默认 True（壬午）。"""
    from src.storage.person_dao import PersonDAO
    db = str(tmp_path / "f2.db")
    udao = UserDAO(db)
    pdao = PersonDAO(db)

    def _run(uid, msg, solar=None):
        if solar is not None:
            _seed_f2_person(pdao, uid, solar)
        h, rec = _tool_handler(tmp_path, uid, db_path=db)
        tr = h._tool_bazi(msg, uid)
        assert tr and tr.ok, tr.text if tr else "tool 未执行"
        assert rec.calls, "应调用引擎"
        return rec.calls[0][1]

    # 档案关(0) + 同年自我文本 → solar_time=False
    kw1 = _run("u_f2_off", "1999年5月13日 10:55 长春 男", solar=0)
    assert kw1["solar_time"] is False
    # 档案开(1) + 同年自我文本 → True
    kw2 = _run("u_f2_on", "1999年5月13日 10:55 长春 男", solar=1)
    assert kw2["solar_time"] is True
    # 无档案（新用户）→ 引擎默认开
    kw3 = _run("u_f2_new", "1999年5月13日 10:55 长春 男")
    assert kw3["solar_time"] is True
    # 消息年份与档案不同（第三方/他人盘）→ 默认开（不随本人档案）
    kw4 = _run("u_f2_other", "1998年5月13日 10:55 长春 男", solar=0)
    assert kw4["solar_time"] is True


def test_person_dao_solar_flip_mirrors_bazi_info(tmp_path):
    """r1 F3：persons 开关翻转 → users.bazi_info 同步镜像（② 源防回弹默认开）；
    行无出生年 → 跳过不崩。

    k25 ④-6(b) 语义同步：默认行出生数据改写（含仅城市）改为「写侧镜像漏斗」
    立即全量收敛 bazi_info（收敛时机从「读时」前移到「写时」）——旧契约
    「非开关更新零镜像」随 k25 作废（读路径自愈本就覆盖同款改写，只是晚一次
    读）；镜像 payload = 8 birth 键 + solar_time，与读路径自愈同构。不涉出生
    数据的更新（仅名字/关系）仍零镜像。
    """
    db = str(tmp_path / "mirror.db")
    udao = UserDAO(db)
    pdao = PersonDAO(db)
    udao.save_user_bazi("u_m", {"year": 1999, "month": 5, "day": 13,
                                "hour": 10, "minute": 55, "gender": "男",
                                "calendar": "solar", "city": "长春"})
    p = pdao.create_person("u_m", "我", "自己", birth={
        "gender": "男", "birth_year": 1999, "birth_month": 5, "birth_day": 13,
        "birth_hour": 10, "birth_minute": 55, "calendar": "solar",
        "city": "长春", "solar_time": 1})
    # k25 ④-6(b)：建档即镜像（默认行 + 出生年）——bazi_info 立即全量收敛，
    # solar_time 落地为档案生效值 1（旧契约「建档不镜像、无 solar_time 键」
    # 随 k25 作废）
    assert udao.get_user_bazi("u_m")["solar_time"] == 1
    # k25 ④-6(b)：默认行出生数据改写（仅城市）→ 写侧镜像漏斗立即全量收敛
    # （8 birth 键 + solar_time=档案生效值 1，与读路径自愈 payload 同构）
    pdao.update_person("u_m", p["id"], birth={"city": "北京"})
    assert udao.get_user_bazi("u_m")["city"] == "北京"
    assert udao.get_user_bazi("u_m")["solar_time"] == 1
    # 不涉出生数据的更新（仅名字）→ 零镜像（bazi_info 原样）
    _before_name_upd = udao.get_user_bazi("u_m")
    pdao.update_person("u_m", p["id"], name="新名字")
    assert udao.get_user_bazi("u_m") == _before_name_upd
    # 显式切关 → persons 0 且 bazi_info 镜像 0
    p2 = pdao.update_person("u_m", p["id"], birth={"solar_time": 0})
    assert p2["solar_time"] == 0
    assert udao.get_user_bazi("u_m")["solar_time"] == 0
    # 切回 1 → bazi_info 镜像 1（bazi_info 更新为最新，不残留旧 0）
    pdao.update_person("u_m", p["id"], birth={"solar_time": 1})
    assert udao.get_user_bazi("u_m")["solar_time"] == 1
    # 无 bazi_info 行（另用户）→ 镜像跳过不崩
    pdao.create_person("u_none", "我", "自己", birth={
        "gender": "男", "birth_year": 1999, "birth_month": 5, "birth_day": 13,
        "birth_hour": 10, "birth_minute": 55, "calendar": "solar",
        "city": "长春", "solar_time": 1})
    pdao.update_person("u_none", pdao.list_persons("u_none")[0]["id"],
                       birth={"solar_time": 0})
    assert udao.get_user_bazi("u_none") is None
