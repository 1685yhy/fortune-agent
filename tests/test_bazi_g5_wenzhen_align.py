"""G5 排盘口径对齐问真（2026-08-30，task-G5-brief.md）四项修复测试：

① 起运「X岁X个月」实岁显示串（qiyun_sui_desc，qiyun_desc 保留不变）
② 夏令时开关 + 1986-1991 中国夏令时表（DST_TABLE，默认关，闭区间含边界）
③ 早晚子时专业档（late_child_hour，=问真 yzs=1，默认关，按真太阳时修正后时间判定）
④ 交运舍入边界（实为问真「不截断日历加法 + 问真表节锚定 + floor」口径，
   P3 陈静「寒露后12天」对齐；非舍入规则问题）

锚点来源：问真 API 实测 /tmp/wz/p*.json + 教研报告 /tmp/wenzhen_research.md §②/§④
（5 组虚拟案例 qiyunarr 与 qiyun_sui 实岁串逐字一致；交运 5/5 干支年/节名/天数对齐）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from datetime import datetime  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from lunar_python import Solar  # noqa: E402

from src.engines.bazi import BaziEngine, DST_TABLE  # noqa: E402
from src.api import paipan as paipan_api  # noqa: E402
from src.api.hehun import BaziInput  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402


ENGINE = BaziEngine()

# 问真 API 实测 5 组虚拟人物（qiyunarr 逐位一致 + 实岁串逐字一致 + 交运表述对齐）
ANCHORS = [
    # (姓名, 出生(年月日时分), 性别, 城市, 问真 qiyunarr, 问真实岁串, 问真交运表述, 四柱)
    ("王芳", (1993, 5, 8, 14, 30), "女", "西安",
     (9, 5, 22, 4, 26), "9岁5个月起运", "逢壬、丁年 寒露后21天 交大运",
     ["癸酉", "丁巳", "己丑", "辛未"]),
    ("李明", (1990, 1, 1, 23, 40), "男", "北京",
     (8, 6, 0, 0, 6), "8岁6个月起运", "逢戊、癸年 芒种后25天 交大运",
     ["己巳", "丙子", "丁卯", "庚子"]),
    ("陈静", (2000, 1, 1, 0, 15), "女", "成都",
     (1, 9, 19, 11, 24), "1岁9个月起运", "逢辛、丙年 寒露后12天 交大运",  # G5 修复后对齐
     ["己卯", "丙子", "戊午", "壬子"]),
    ("张伟", (1985, 6, 15, 8, 20), "男", "乌鲁木齐",
     (3, 0, 20, 20, 8), "3岁0个月起运", "逢戊、癸年 芒种后30天 交大运",
     ["乙丑", "壬午", "乙酉", "己卯"]),
    ("刘洋", (2016, 2, 4, 23, 30), "男", "深圳",
     (9, 10, 4, 6, 60), "9岁10个月起运", "逢乙、庚年 大雪后2天 交大运",  # 60 分不进位怪癖
     ["丙申", "庚寅", "丙辰", "己亥"]),
]


# ═══════════════════════ ① 起运实岁串 ═══════════════════════

@pytest.mark.parametrize("name,birth,gender,city,qy_detail,qy_sui,jy_text,bazi", ANCHORS,
                         ids=[a[0] for a in ANCHORS])
def test_qiyun_sui_desc_5_anchors(name, birth, gender, city, qy_detail, qy_sui, jy_text, bazi):
    """5 案例 qiyun_sui_desc 与问真「X岁X个月起运」逐字一致（含 60 分不进位、0 月案例）。"""
    r = ENGINE.calculate(*birth, city, gender)
    # 数据一致性：实岁串取 qiyun_detail 年/月位（60 分不进位不影响月位）
    assert r.qiyun_sui_desc == qy_sui == "%d岁%d个月起运" % (qy_detail[0], qy_detail[1])
    # qiyun_detail 逐位对齐问真 qiyunarr（前 5 位）
    assert list(r.qiyun_detail) == list(qy_detail)
    # ① 契约零破坏：qiyun_desc 保留原格式不变（无分、无实岁串）
    assert r.qiyun_desc == "出生后%d年%d月%d天%d时起运" % qy_detail[:4]


def test_qiyun_sui_desc_60min_no_carry_case():
    """60 分不进位怪癖案例（问真 qiyunarr [9,10,4,6,60,10]）：月位不受 60 分影响 → 9岁10个月。"""
    r = ENGINE.calculate(2016, 2, 4, 23, 30, "深圳", "男")
    assert list(r.qiyun_detail) == [9, 10, 4, 6, 60]
    assert r.qiyun_sui_desc == "9岁10个月起运"


def test_qiyun_sui_desc_zero_month_case():
    """0 个月案例（问真 qiyunarr [3,0,20,20,8,4]）→ 3岁0个月起运（0 月不可省略）。"""
    r = ENGINE.calculate(1985, 6, 15, 8, 20, "乌鲁木齐", "男")
    assert r.qiyun_sui_desc == "3岁0个月起运"


def test_qiyun_sui_desc_consistency_with_detail():
    """数据一致性铁律：任意生日，实岁串恒等于 qiyun_detail 年/月位格式化。"""
    for birth, city, gender in [((1999, 5, 13, 11, 25), "", "男"),
                                ((1990, 1, 1, 23, 40), "北京", "男"),
                                ((1987, 6, 1, 10, 0), "北京", "女")]:
        r = ENGINE.calculate(*birth, city, gender)
        assert r.qiyun_sui_desc == "%d岁%d个月起运" % (r.qiyun_detail[0], r.qiyun_detail[1])


# ═══════════════════════ ② 夏令时开关 ═══════════════════════

def test_dst_table_matches_public_history():
    """DST_TABLE 与公开史实一致（1986-1991，各年起止日均为星期日——与
    「4 月第 2 个星期日 02:00 ~ 9 月第 2 个星期日 02:00」规则自洽；1986 首年 5 月第 1 周日）。"""
    assert DST_TABLE == {
        1986: ((5, 4), (9, 14)),
        1987: ((4, 12), (9, 13)),
        1988: ((4, 10), (9, 11)),
        1989: ((4, 16), (9, 17)),
        1990: ((4, 15), (9, 16)),
        1991: ((4, 14), (9, 15)),
    }
    for year, ((sm, sd), (em, ed)) in DST_TABLE.items():
        assert datetime(year, sm, sd).weekday() == 6  # 起始日为该年周日
        assert datetime(year, em, ed).weekday() == 6  # 结束日为该年周日


def test_dst_default_off_zero_change():
    """默认关 = 现行为零变化：不传/传 False 输出逐位一致。"""
    base = ENGINE.calculate(1987, 6, 1, 10, 0, "北京", "男")
    off = ENGINE.calculate(1987, 6, 1, 10, 0, "北京", "男", daylight_saving=False)
    assert base.corrected_time == off.corrected_time == "09:47"
    assert list(base.bazi) == list(off.bazi)


def test_dst_in_window_minus_one_hour_before_correction():
    """表内生日开档：修正前原始时间减 1 小时（真太阳时修正后整 1 小时提前）。"""
    off = ENGINE.calculate(1987, 6, 1, 10, 0, "北京", "男")
    on = ENGINE.calculate(1987, 6, 1, 10, 0, "北京", "男", daylight_saving=True)
    assert off.corrected_time == "09:47"
    assert on.corrected_time == "08:47"  # 修正前 10:00→09:00，再走真太阳时


def test_dst_out_of_window_no_change():
    """表外生日开档 → 不变（非夏令时年份 1992 / 区间外日期 / 1986 首年起始日前）。"""
    for birth in [(1988, 3, 1, 10, 0), (1992, 6, 1, 10, 0), (1986, 5, 3, 10, 0),
                  (1986, 9, 15, 10, 0), (1991, 4, 13, 10, 0)]:
        off = ENGINE.calculate(*birth, "北京", "男")
        on = ENGINE.calculate(*birth, "北京", "男", daylight_saving=True)
        assert on.corrected_time == off.corrected_time
        assert list(on.bazi) == list(off.bazi)


def test_dst_boundaries_inclusive_0200():
    """边界闭区间含边界：起始日 02:00（含）、结束日 02:00（含）减 1 小时；01:59/02:01 不减。"""
    # 1987 起始 04-12 02:00：含边界 → 减 1 小时（01:00 → 修正后 00:44）
    at_start = ENGINE.calculate(1987, 4, 12, 2, 0, "北京", "男", daylight_saving=True)
    assert at_start.corrected_time == "00:44"
    # 01:59 → 不减（01:43，与关档一致）
    before = ENGINE.calculate(1987, 4, 12, 1, 59, "北京", "男", daylight_saving=True)
    assert before.corrected_time == "01:43"
    # 1987 结束 09-13 02:00：含边界 → 减 1 小时
    at_end = ENGINE.calculate(1987, 9, 13, 2, 0, "北京", "男", daylight_saving=True)
    assert at_end.corrected_time == "00:50"
    # 02:01 → 不减
    after = ENGINE.calculate(1987, 9, 13, 2, 1, "北京", "男", daylight_saving=True)
    assert after.corrected_time == "01:51"
    # 对照：同刻关档为未减时间
    assert ENGINE.calculate(1987, 4, 12, 2, 0, "北京", "男").corrected_time == "01:44"
    assert ENGINE.calculate(1987, 9, 13, 2, 0, "北京", "男").corrected_time == "01:50"


def test_dst_cross_midnight_rolls_to_previous_day():
    """减 1 小时跨日：00:30 开档 → 前一日 23:30 再修正（23:18），排盘走前日盘。"""
    on = ENGINE.calculate(1987, 6, 1, 0, 30, "北京", "男", daylight_saving=True)
    assert on.corrected_time == "23:18"


# ═══════════════════════ ③ 早晚子时专业档 ═══════════════════════

def test_late_child_hour_anchor_1990_0101_2300():
    """问真实测锚点：1990-01-01 23:00（无真太阳时修正）。
    关档 → 日柱丁卯（次日）；开档 → 日柱丙寅（当天）+ 时柱庚子（次日丁日五鼠遁）。"""
    off = ENGINE.calculate(1990, 1, 1, 23, 0, "", "男")
    on = ENGINE.calculate(1990, 1, 1, 23, 0, "", "男", late_child_hour=True)
    assert list(off.bazi) == ["己巳", "丙子", "丁卯", "庚子"]
    assert list(on.bazi) == ["己巳", "丙子", "丙寅", "庚子"]


def test_late_child_hour_default_off_is_existing_behavior():
    """默认关 = 现行为：23:40 北京（修正后 23:21）→ 日柱次日（问真 P2 李明 丁卯）。"""
    r = ENGINE.calculate(1990, 1, 1, 23, 40, "北京", "男")
    assert r.corrected_time == "23:21"
    assert list(r.bazi) == ["己巳", "丙子", "丁卯", "庚子"]
    on = ENGINE.calculate(1990, 1, 1, 23, 40, "北京", "男", late_child_hour=True)
    assert list(on.bazi) == ["己巳", "丙子", "丙寅", "庚子"]


def test_late_child_hour_non_late_hour_zero_effect():
    """非晚子时时刻开档零影响（00:00-22:59 全天各段逐位一致）。

    注：00:15 北京修正后为前日 23:5x（落入晚子时段）→ 档位本应生效，故不入本组
    （见 test_late_child_hour_true_solar_enter_window 的跨入交互覆盖）。"""
    for birth in [(1990, 1, 1, 12, 0), (1990, 1, 1, 0, 30), (1990, 1, 1, 22, 59),
                  (1985, 6, 15, 8, 20), (1990, 3, 5, 22, 40)]:
        off = ENGINE.calculate(*birth, "北京", "男")
        on = ENGINE.calculate(*birth, "北京", "男", late_child_hour=True)
        assert list(on.bazi) == list(off.bazi)
        assert on.corrected_time == off.corrected_time


def test_late_child_hour_true_solar_exit_window_p5():
    """真太阳时交互-退晚子时（问真 P5 刘洋）：23:30 深圳 → 修正 22:52 退出晚子时段，
    档位无关（双档逐位一致，含四柱/起运/交运）。"""
    off = ENGINE.calculate(2016, 2, 4, 23, 30, "深圳", "男")
    on = ENGINE.calculate(2016, 2, 4, 23, 30, "深圳", "男", late_child_hour=True)
    assert off.corrected_time == on.corrected_time == "22:52"
    assert list(on.bazi) == list(off.bazi) == ["丙申", "庚寅", "丙辰", "己亥"]
    assert list(on.qiyun_detail) == list(off.qiyun_detail) == [9, 10, 4, 6, 60]
    assert on.jiaoyun.get("page_text") == off.jiaoyun.get("page_text")


def test_late_child_hour_true_solar_enter_window():
    """真太阳时交互-跨入晚子时：22:52 长春 → 修正 23:01 落入晚子时段 → 档位生效。
    开档日柱 = 修正日当天（己巳，独立以 lunar-python 校验），关档 = 次日（庚午）；
    时柱双档均为庚午日五鼠遁丙子（与问真「时柱按次日日干」口径一致）。"""
    off = ENGINE.calculate(1990, 3, 5, 22, 52, "长春", "男")
    on = ENGINE.calculate(1990, 3, 5, 22, 52, "长春", "男", late_child_hour=True)
    assert off.corrected_time == on.corrected_time == "23:01"
    # 独立校验：修正日 1990-03-05 日柱 = 己巳，次日 1990-03-06 = 庚午（lunar-python 直查）
    assert Solar.fromYmdHms(1990, 3, 5, 12, 0, 0).getLunar().getEightChar().getDay() == "己巳"
    assert Solar.fromYmdHms(1990, 3, 6, 12, 0, 0).getLunar().getEightChar().getDay() == "庚午"
    assert list(on.bazi) == ["庚午", "戊寅", "己巳", "丙子"]   # 当天日柱（yzs=1 口径）
    assert list(off.bazi) == ["庚午", "戊寅", "庚午", "丙子"]  # 次日日柱（默认口径）


# ═══════════════════════ ④ 交运口径对齐 ═══════════════════════

@pytest.mark.parametrize("name,birth,gender,city,qy_detail,qy_sui,jy_text,bazi", ANCHORS,
                         ids=[a[0] for a in ANCHORS])
def test_jiaoyun_5_anchors_match_wenzhen(name, birth, gender, city, qy_detail, qy_sui, jy_text, bazi):
    """5 案例交运表述与问真逐字一致（干支年/节名/节后天数；P3 修复后 12 天对齐）。"""
    r = ENGINE.calculate(*birth, city, gender)
    assert r.jiaoyun["page_text"] == jy_text


def test_jiaoyun_p3_was_boundary_case():
    """P3 陈静（G5 差异原案例）：节后 12 天（问真）；修复后交运时刻 2001-10-21 10:31。
    根因=问真不截断日历加法（1999-12-31 23:07 + 1年9月 → 2001-10-01 而非 09-30），
    距寒露 12.9 天 → floor=12；非舍入规则差异。"""
    r = ENGINE.calculate(2000, 1, 1, 0, 15, "成都", "女")
    jy = r.jiaoyun
    assert jy["jie"] == "寒露" and jy["days_after_jie"] == 12
    assert jy["time"] == "2001-10-21 10:31"
    assert jy["year"] == 2001 and jy["year_ganzhi"] == "辛巳"  # 交运年立春界定（辛巳年）
    # 距节真实天数：2001-10-21 10:31 - 寒露（2001-10-08 21:24 左右）≈ 12.54 天 → floor 12
    assert r.jiaoyun["gan_pair"] == "辛、丙"


def test_jiaoyun_existing_anchor_27_days_still_aligned():
    """既有交运锚点零回归：1999-05-13 11:25 男 → 白露后27天（问真排盘页锚点）。"""
    r = ENGINE.calculate(1999, 5, 13, 11, 25, "", "男")
    assert r.jiaoyun["page_text"] == "逢辛、丙年 白露后27天 交大运"
    assert r.jiaoyun["time"] == "2001-10-05 11:25"


def test_jiaoyun_unclamped_calendar_add_direct():
    """不截断日历加法单测（_calendar_add_raw 与问真同口径）：
    12-31 + 9 月 → 10-01（截断口径得 09-30）；12-31 + 2 月 → 03-03（截断口径 02-28）。"""
    from src.engines.bazi import _calendar_add_raw
    from datetime import datetime as _dt
    assert _calendar_add_raw(_dt(1999, 12, 31, 23, 7), 1, 9, 19, 11, 24) == _dt(2001, 10, 21, 10, 31)
    assert _calendar_add_raw(_dt(1999, 12, 31, 12, 0), 0, 9, 0, 0, 0) == _dt(2000, 10, 1, 12, 0)
    assert _calendar_add_raw(_dt(1999, 12, 31, 12, 0), 0, 2, 0, 0, 0) == _dt(2000, 3, 2, 12, 0)  # 2000 闰年
    assert _calendar_add_raw(_dt(1998, 12, 31, 12, 0), 0, 2, 0, 0, 0) == _dt(1999, 3, 3, 12, 0)  # 平年：顺滚过 2/28


# ═══════════════════════ ⑤ 契约（零破坏） ═══════════════════════

def test_bazi_input_new_fields_default_off():
    """BaziInput 新字段默认 False（旧请求零影响）；显式传 True 可开。"""
    m = BaziInput(year=1999, month=5, day=13, hour=6, minute=25, gender="male", city="北京")
    assert m.daylightSaving is False and m.lateChildHour is False
    assert BaziInput().daylightSaving is False and BaziInput().lateChildHour is False


def _client():
    paipan_api.setup(BaziEngine())
    set_auth_handler(AuthHandler())
    return TestClient(__import__("src.main", fromlist=["app"]).app)


def _headers():
    from src.security.auth import JWTHandler
    return {"Authorization": "Bearer %s" % JWTHandler("test-secret-key-32-bytes-long!!").create_token("u_g5")}


def test_serialize_bazi_includes_qiyun_sui_desc():
    """serialize_bazi 输出含 qiyun_sui_desc（实岁串），与 qiyun_detail 年/月位一致。"""
    body = _client().post("/api/paipan", json={
        "birthYear": 1999, "birthMonth": 5, "birthDay": 13,
        "birthHour": 6, "minute": 25, "gender": "male", "city": "北京",
    }, headers=_headers()).json()
    assert body["qiyun_sui_desc"] == "2岁4个月起运"          # 闫海洋盘（北京真太阳时口径）
    assert body["qiyun_desc"] == "出生后2年4月21天2时起运"    # 原字段保留
    assert body["qiyun_sui_desc"] == "%d岁%d个月起运" % (body["qiyun_detail"][0], body["qiyun_detail"][1])


def test_paipan_old_request_identical_to_explicit_false():
    """旧请求（不传新字段）与显式 False 请求输出逐位一致（接口契约零破坏）。"""
    c = _client()
    old = c.post("/api/paipan", json={
        "birthYear": 1987, "birthMonth": 6, "birthDay": 1, "birthHour": 5, "minute": 0,
        "gender": "female", "city": "北京",
    }, headers=_headers()).json()
    new = c.post("/api/paipan", json={
        "birthYear": 1987, "birthMonth": 6, "birthDay": 1, "birthHour": 5, "minute": 0,
        "gender": "female", "city": "北京",
        "daylightSaving": False, "lateChildHour": False,
    }, headers=_headers()).json()
    assert old["bazi"] == new["bazi"] and old["qiyun_desc"] == new["qiyun_desc"]
    assert old["jiaoyun"] == new["jiaoyun"] and old["dayun"] == new["dayun"]
    assert old["qiyun_sui_desc"] == new["qiyun_sui_desc"]
    # 数据一致性：实岁串与 qiyun_detail 年/月位一致（该盘 = 1岁8个月起运）
    assert new["qiyun_sui_desc"] == "%d岁%d个月起运" % (new["qiyun_detail"][0], new["qiyun_detail"][1])
    assert new["qiyun_sui_desc"] == "1岁8个月起运"


def test_paipan_new_flags_take_effect():
    """新字段开启后生效（不崩且行为正确）：夏令时开 → 修正时间提前 1 小时；
    早晚子时开 → 晚子时当日柱。"""
    c = _client()
    # 夏令时：1987-06-01 09:00 北京（时辰序号 5 = 9 点）开档 → 不崩且修正提前 1 小时
    body = c.post("/api/paipan", json={
        "birthYear": 1987, "birthMonth": 6, "birthDay": 1, "birthHour": 5, "minute": 0,
        "gender": "male", "city": "北京", "daylightSaving": True,
    }, headers=_headers()).json()
    assert body["meta"]["solar_text"].endswith("09:00")
    assert body["bazi"][2] == "辛巳"  # 减 1 小时后仍同日（对照引擎层 08:47 盘）
    # 早子时：1990-01-01 23:00（生时 23 点，时钟小时）→ 当日柱丙寅
    body2 = c.post("/api/paipan", json={
        "birthYear": 1990, "birthMonth": 1, "birthDay": 1, "birthHour": 23, "minute": 0,
        "gender": "male", "city": "", "lateChildHour": True,
    }, headers=_headers()).json()
    assert body2["bazi"][2] == "丙寅" and body2["bazi"][3] == "庚子"
