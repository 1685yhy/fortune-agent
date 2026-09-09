"""k16 引擎口径批测试（分支 k16-calendar-dst，BASE main=e004b41）。

覆盖（对应 plan docs/superpowers/plans/2026-09-10-k16-calendar-dst.md）：
A 夏令时 1986-1991 口径——问真默认忽略（不减 1 小时、无开关参数）实证 + 锁定：
  引擎默认口径与问真 getbasebz8 直查逐字一致（12 案例含起止边界日）；
  DST_TABLE 史实表锁定；可选开关 daylight_saving=True 的减 1 小时机制与
  API 实证的 −1h 命盘逐字一致（防止未来误加默认 DST 逻辑 = 偏离问真）。
B 换运切段口径（k11c 遗留）——current_stage 当前大运段统一为精确交运时刻切段
  （jiaoyun.years[].time，排盘卡/起运分解同源），不再按虚岁岁首（1 月 1 日）
  提前切段；起运前无当前段；无 time 字段兜底虚岁口径（k11 原行为锁定）。

问真证据（2026-09-10 直查 bzapi3.iwzbz.com/getbasebz8.php，
  params: d=YYYY-MM-DD HH:MM&s=sex&today=…&vip=0&yzs=0&pqf=0；
  probes xls=1/dst=1/dss=1 均与 baseline 同盘 → 无夏令时开关参数）：
  - 1987-07-15 07:30 男（1987 DST 04-12~09-13 窗内）→ 丁卯丁未乙丑庚辰 辰时
    （07:30 原样排；若减 1h 应为 06:30 的 己卯 卯时盘，已另查 06:30 → 丁卯丁未
    乙丑己卯 证实 API 能区分且未减）
  - 1988-08-08 08:30 女 → 戊辰庚申乙未庚辰 辰时（减 1h 也落辰时，不区分时辰，
    但同日 07:30 直查同为 辰时盘，日柱不变 → 未减日）
  - 1986-05-04 07:30 男（首年 DST 起始日）→ 丙寅壬辰戊申丙辰 辰时（未减）
  - 1986-05-03 07:30 女（DST 前一日）→ 丙寅壬辰丁未甲辰 辰时
  - 1987-09-13 01:30/03:30/07:30 女（DST 结束日）→ 丑/寅/辰时 原样（未减）
  - 1987-04-12 01:30/03:30 女（DST 起始日 02:00 前/后）→ 丑/寅时 原样（未减）
  - 1991-09-15 07:30 男（末年度 DST 结束日）→ 辛未丁酉戊子丙辰 辰时（未减）
  结论：问真 API 默认把夏令时期间出生时刻按钟面时间原样排盘（忽略夏令时），
  与本引擎 daylight_saving 默认关一致 → A 项 = 零引擎改动 + 锁定测试。

运行：cd /mnt/e/fortune-agent-deploy && OMP_NUM_THREADS=4 \
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k16_calendar_dst.py -q -p no:cacheprovider
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.engines.bazi import BaziEngine, current_stage_facts, DST_TABLE  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    return BaziEngine()


# ============================================================
# A：夏令时口径（问真默认忽略 = 引擎默认关，锁定防回归）
# ============================================================

# (y, m, d, h, mi, gender, 问真 API 直查四柱 2026-09-10)
WZ_RAW = [
    # DST 窗内（问真不减 1 小时，钟面时间原样排盘）
    (1987, 7, 15, 7, 30, "男", "丁卯丁未乙丑庚辰"),
    (1987, 7, 15, 6, 30, "男", "丁卯丁未乙丑己卯"),   # 07:30 减 1h 的对照盘（API 能区分）
    (1988, 8, 8, 8, 30, "女", "戊辰庚申乙未庚辰"),
    (1988, 8, 8, 7, 30, "女", "戊辰庚申乙未庚辰"),
    (1991, 9, 15, 7, 30, "男", "辛未丁酉戊子丙辰"),   # 末年 DST 结束日
    # DST 起始日 1987-04-12（02:00 起）前后
    (1987, 4, 12, 1, 30, "女", "丁卯甲辰辛卯己丑"),
    (1987, 4, 12, 3, 30, "女", "丁卯甲辰辛卯庚寅"),
    # DST 结束日 1987-09-13（02:00 回拨）前后
    (1987, 9, 13, 1, 30, "女", "丁卯己酉乙丑丁丑"),
    (1987, 9, 13, 3, 30, "女", "丁卯己酉乙丑戊寅"),
    (1987, 9, 13, 7, 30, "女", "丁卯己酉乙丑庚辰"),
    # 首年 1986 特例（05-04 起）与 DST 前一日
    (1986, 5, 4, 7, 30, "男", "丙寅壬辰戊申丙辰"),
    (1986, 5, 3, 7, 30, "女", "丙寅壬辰丁未甲辰"),
]


class TestDstWenzhenDefaultIgnore:
    """问真直查锁定：夏令时期间出生按钟面时间原样排盘 → 引擎默认（关）必须逐字一致。"""

    @pytest.mark.parametrize("y,m,d,h,mi,g,wz", WZ_RAW)
    def test_engine_default_matches_wenzhen_verbatim(self, engine, y, m, d, h, mi, g, wz):
        """solar_time=False（北京时间直排=问真 API 无城市口径）四柱与问真逐字一致。
        API 参数探针 xls=1/dst=1/dss=1 与 baseline 同盘 → 问真无夏令时开关参数。"""
        r = engine.calculate(y, m, d, h, mi, "北京", g, solar_time=False)
        assert "".join(r.bazi) == wz, (
            f"{y}-{m:02d}-{d:02d} {h:02d}:{mi:02d} {g}: 引擎默认盘 {''.join(r.bazi)} "
            f"≠ 问真直查 {wz}（问真默认不减 1 小时，引擎默认关=对齐）")

    def test_wenzhen_can_distinguish_minus1h(self, engine):
        """取证有效性：07:30 与 06:30 时辰不同（辰/卯），若问真减 1h 必出己卯盘——
        实测出庚辰盘 → 问真默认确实忽略夏令时。"""
        r7 = engine.calculate(1987, 7, 15, 7, 30, "北京", "男", solar_time=False)
        r6 = engine.calculate(1987, 7, 15, 6, 30, "北京", "男", solar_time=False)
        assert "".join(r7.bazi) != "".join(r6.bazi)
        assert r7.bazi[3] == "庚辰" and r6.bazi[3] == "己卯"

    def test_engine_default_off_equals_raw(self, engine):
        """默认 daylight_saving=False（G5 起，k16 实证对齐问真默认=不调整）：默认
        排盘即零夏令时调整——与显式 False 同盘；显式开才减 1h（防未来误把默认改调
        整而偏离问真：1987-07-15 07:30 开则会变 06:xx 卯时盘）。"""
        r_default = engine.calculate(1987, 7, 15, 7, 30, "北京", "男")
        r_off = engine.calculate(1987, 7, 15, 7, 30, "北京", "男",
                                 daylight_saving=False)
        assert r_default.bazi == r_off.bazi
        r_on = engine.calculate(1987, 7, 15, 7, 30, "北京", "男",
                                daylight_saving=True)
        assert r_on.bazi != r_off.bazi
        assert r_on.bazi[3][1] == "卯"  # 减 1h（含真太阳时修正后 06 时）跨入卯时


class TestDstSwitchMachinery:
    """可选开关 daylight_saving=True 的减 1 小时机制锁定（与问真 −1h 对照盘逐字一致）。"""

    def test_switch_mid_window_equals_minus1h_wenzhen(self, engine):
        """1987-07-15 07:30 开夏令时 → 减 1h = 06:30 盘 = 问真直查 06:30 己卯盘。"""
        r_on = engine.calculate(1987, 7, 15, 7, 30, "北京", "男",
                                solar_time=False, daylight_saving=True)
        assert "".join(r_on.bazi) == "丁卯丁未乙丑己卯"  # 问真 06:30 直查逐字
        assert r_on.bazi[3] == "己卯"

    def test_switch_start_day_after_0200(self, engine):
        """起始日 1987-04-12 03:30（02:00 起调后）开 → 减 1h = 02:30 丑时盘。"""
        r_on = engine.calculate(1987, 4, 12, 3, 30, "北京", "女",
                                solar_time=False, daylight_saving=True)
        r_raw = engine.calculate(1987, 4, 12, 2, 30, "北京", "女", solar_time=False)
        assert r_on.bazi == r_raw.bazi and r_on.bazi[3][1] == "丑"

    def test_switch_start_day_before_0200_noop(self, engine):
        """起始日 02:00 前（1987-04-12 01:30）开 → 未进夏令时区间，不减（问真直查
        同盘 丁卯甲辰辛卯己丑）。"""
        r_on = engine.calculate(1987, 4, 12, 1, 30, "北京", "女",
                                solar_time=False, daylight_saving=True)
        r_raw = engine.calculate(1987, 4, 12, 1, 30, "北京", "女", solar_time=False)
        assert r_on.bazi == r_raw.bazi
        assert r_on.bazi == ["丁卯", "甲辰", "辛卯", "己丑"]  # 问真直查 2026-09-10

    def test_switch_end_day_morning_adjust_after_0200_noop(self, engine):
        """结束日 1987-09-13（02:00 回拨）：01:30（回拨前仍夏令时）开 → 减 1h =
        00:30 盘；03:30 / 07:30（已回拨，标准时）开 → 不减。"""
        r_on_early = engine.calculate(1987, 9, 13, 1, 30, "北京", "女",
                                      solar_time=False, daylight_saving=True)
        r_minus1 = engine.calculate(1987, 9, 13, 0, 30, "北京", "女", solar_time=False)
        assert r_on_early.bazi == r_minus1.bazi
        for h in (3, 7):
            r_on = engine.calculate(1987, 9, 13, h, 30, "北京", "女",
                                    solar_time=False, daylight_saving=True)
            r_raw = engine.calculate(1987, 9, 13, h, 30, "北京", "女",
                                     solar_time=False)
            assert r_on.bazi == r_raw.bazi, f"{h}:30 已过 02:00 回拨不应再减"
        r_raw3 = engine.calculate(1987, 9, 13, 3, 30, "北京", "女", solar_time=False)
        assert r_raw3.bazi == ["丁卯", "己酉", "乙丑", "戊寅"]  # 问真直查 2026-09-10

    def test_switch_first_year_and_pre_dst_day(self, engine):
        """首年 1986-05-04 起调日 07:30 开 → 减 1h = 06:30 盘；1986-05-03（DST
        前一日）开 → 不减（问真直查同盘 丙寅壬辰丁未甲辰）。"""
        r_on = engine.calculate(1986, 5, 4, 7, 30, "北京", "男",
                                solar_time=False, daylight_saving=True)
        r_minus1 = engine.calculate(1986, 5, 4, 6, 30, "北京", "男", solar_time=False)
        assert r_on.bazi == r_minus1.bazi
        r_pre = engine.calculate(1986, 5, 3, 7, 30, "北京", "女",
                                 solar_time=False, daylight_saving=True)
        r_raw = engine.calculate(1986, 5, 3, 7, 30, "北京", "女", solar_time=False)
        assert r_pre.bazi == r_raw.bazi
        assert r_raw.bazi == ["丙寅", "壬辰", "丁未", "甲辰"]  # 问真直查 2026-09-10

    def test_switch_last_year_end_day(self, engine):
        """末年 1991-09-15（结束日）：01:30 开 → 减 1h = 00:30 盘；07:30 开
        （02:00 已回拨）→ 不减（问真直查 07:30 同盘 辛未丁酉戊子丙辰）。"""
        r_on_early = engine.calculate(1991, 9, 15, 1, 30, "北京", "男",
                                      solar_time=False, daylight_saving=True)
        r_minus1 = engine.calculate(1991, 9, 15, 0, 30, "北京", "男", solar_time=False)
        assert r_on_early.bazi == r_minus1.bazi
        r_on_late = engine.calculate(1991, 9, 15, 7, 30, "北京", "男",
                                     solar_time=False, daylight_saving=True)
        r_raw = engine.calculate(1991, 9, 15, 7, 30, "北京", "男", solar_time=False)
        assert r_on_late.bazi == r_raw.bazi == ["辛未", "丁酉", "戊子", "丙辰"]


class TestDstTableLocked:
    """史实表锁定：1986-1991 逐年起止（人民日报 1986-04-19 通知 + 澎湃逐年日期，
    起止均为星期日；1986 首年自 05-04 特例）。"""

    def test_table_years(self):
        assert DST_TABLE == {
            1986: ((5, 4), (9, 14)),
            1987: ((4, 12), (9, 13)),
            1988: ((4, 10), (9, 11)),
            1989: ((4, 16), (9, 17)),
            1990: ((4, 15), (9, 16)),
            1991: ((4, 14), (9, 15)),
        }

    def test_all_dates_are_sundays(self):
        for y, ((sm, sd), (em, ed)) in DST_TABLE.items():
            assert datetime(y, sm, sd).weekday() == 6, f"{y} 起始日非周日"
            assert datetime(y, em, ed).weekday() == 6, f"{y} 结束日非周日"

    def test_no_dst_outside_range(self):
        assert 1985 not in DST_TABLE and 1992 not in DST_TABLE


# ============================================================
# B：换运切段口径（k11c 遗留）——精确交运时刻切段
# ============================================================

# 基准命盘：1999-05-13 09:00 长春 男（k11 行 48 事故同款 golden）
# 问真/引擎精确交运：2001-09-25 09:25 起运（虚岁 3 戊辰），此后每逢 9-25 09:25 换运
# （2011→丁卯 13，2021→丙寅 23，2031→乙丑 33…），交运时刻与 排盘卡 jiaoyun.years
# 同源。虚岁 23 于 2021-01-01 即满 —— 旧口径（虚岁段选）2021 年元旦就错报丙寅；
# 统一口径后 2021-09-25 09:25 才交丙寅。


@pytest.fixture(scope="module")
def switch_golden(engine):
    """engine.calculate 实盘 golden：dayun/jiaoyun 取引擎同源数据（交运时刻 09:25）。"""
    r = engine.calculate(1999, 5, 13, 9, 0, "长春", "男")
    assert r.current_stage["dayun_ganzhi"] in ("丁卯", "丙寅")  # 2026 现实世界应丙寅
    return r


def _probe(golden, now):
    r = golden
    f = current_stage_facts(1999, 5, 13, r.dayun, r.jiaoyun, r.liunian_rel, now=now)
    return f


class TestExactLuckSwitchBoundary:
    def test_new_year_day_before_switch_stays_old_segment(self, switch_golden):
        """虚岁岁首（2021-01-01，虚岁 23 已满）早于交运日 → 仍在丁卯段（旧口径
        元旦即错报丙寅——本测试即锁定该修复）。"""
        f = _probe(switch_golden, datetime(2021, 1, 1, 0, 0))
        assert f["age_xusui"] == 23
        assert f["dayun_index"] == 1
        assert f["dayun_ganzhi"] == "丁卯"
        assert f["next_ganzhi"] == "丙寅" and f["next_year"] == 2021
        assert f["dayun_year_start"] == 2011 and f["dayun_year_end"] == 2020

    def test_minutes_around_exact_switch_moment(self, switch_golden):
        """交运当天出生时刻前后：09:24 → 丁卯；09:25（整点起）→ 丙寅；09:26 → 丙寅。"""
        t = datetime(2021, 9, 25, 9, 25)
        f_before = _probe(switch_golden, datetime(2021, 9, 25, 9, 24))
        assert f_before["dayun_ganzhi"] == "丁卯"
        f_at = _probe(switch_golden, t)
        assert f_at["dayun_index"] == 2 and f_at["dayun_ganzhi"] == "丙寅"
        f_after = _probe(switch_golden, datetime(2021, 9, 25, 9, 26))
        assert f_after["dayun_index"] == 2 and f_after["dayun_ganzhi"] == "丙寅"
        assert f_after["dayun_sui_start"] == 23 and f_after["dayun_sui_end"] == 32
        assert f_after["dayun_year_start"] == 2021 and f_after["dayun_year_end"] == 2030
        assert f_after["next_ganzhi"] == "乙丑" and f_after["next_sui"] == 33
        assert f_after["next_year"] == 2031

    def test_next_switch_year_jan1_still_current_segment(self, switch_golden):
        """2031-01-01（虚岁 33 满，交运 09-25 未到）→ 仍在丙寅段、下步乙丑。"""
        f = _probe(switch_golden, datetime(2031, 1, 1, 0, 0))
        assert f["age_xusui"] == 33
        assert f["dayun_ganzhi"] == "丙寅" and f["dayun_index"] == 2
        assert f["next_ganzhi"] == "乙丑" and f["next_year"] == 2031

    def test_before_first_luck_switch_no_segment(self, switch_golden):
        """起运前（2001-09-25 09:24，起运时刻前 1 分钟；虚岁 3 当年 1 月 1 日即满）
        → 无当前大运段（缺键契约；旧口径自 2001-01-01 即错报戊辰）。"""
        f = _probe(switch_golden, datetime(2001, 9, 25, 9, 24))
        assert f["age_xusui"] == 3
        assert "dayun_ganzhi" not in f and "dayun_index" not in f
        assert "next_ganzhi" not in f
        f_birthyear = _probe(switch_golden, datetime(1999, 12, 31, 12, 0))
        assert "dayun_ganzhi" not in f_birthyear  # 出生当年同样无段

    def test_after_first_luck_switch_first_segment(self, switch_golden):
        """起运时刻后 1 分钟（09:26）→ 戊辰（虚岁 3-12，2001-2010），下步丁卯。"""
        f = _probe(switch_golden, datetime(2001, 9, 25, 9, 26))
        assert f["dayun_index"] == 0 and f["dayun_ganzhi"] == "戊辰"
        assert f["dayun_year_start"] == 2001 and f["dayun_year_end"] == 2010
        assert f["next_ganzhi"] == "丁卯" and f["next_sui"] == 13

    def test_tail_beyond_jiaoyun_table(self, switch_golden):
        """交运年表末段（2081-09-25 09:26 = 虚岁 83 段交后）→ 庚申段（idx8）；
        表外（2095，虚岁 97）→ 虚岁口径兜底进 93 岁档（己未，表外近似注记）。"""
        f = _probe(switch_golden, datetime(2081, 9, 25, 9, 26))
        assert f["dayun_index"] == 8 and f["dayun_sui_start"] == 83
        assert f["dayun_year_start"] == 2081 and f["dayun_year_end"] == 2090
        f2 = _probe(switch_golden, datetime(2095, 6, 1, 0, 0))
        assert f2["age_xusui"] == 97
        assert f2["dayun_ganzhi"] == "己未"  # 93 岁档（表外，交运年表只覆盖 9 段）

    def test_fallback_virtual_age_without_time(self):
        """jiaoyun.years 无 time 字段（旧数据/手工构造）→ 虚岁段选兜底 = k11 原行为。"""
        dayun = [(3, "戊辰"), (13, "丁卯"), (23, "丙寅"), (33, "乙丑"), (43, "甲子")]
        f = current_stage_facts(
            1999, 5, 13, dayun,
            jiaoyun={"years": [{"sui": 3, "year": 2001}, {"sui": 13, "year": 2011},
                               {"sui": 23, "year": 2021}, {"sui": 33, "year": 2031}]},
            liunian_rel={"year": 2026, "ganzhi": "丙午"},
            now=datetime(2026, 9, 7, 12, 0))
        assert f["dayun_ganzhi"] == "丙寅"
        assert f["dayun_sui_start"] == 23 and f["dayun_year_start"] == 2021
        assert f["next_ganzhi"] == "乙丑" and f["next_year"] == 2031
        assert f["liunian_ganzhi"] == "丙午"

    def test_engine_attached_current_stage_consistent(self, switch_golden):
        """引擎挂载的 current_stage（现实 now）与精确切段同源：now ≥ 2021-09-25 后
        恒为丙寅段，起始/结束年份与 jiaoyun 年表一致。"""
        cs = switch_golden.current_stage
        assert cs["dayun_ganzhi"] == "丙寅" and cs["dayun_index"] == 2
        assert cs["dayun_year_start"] == 2021 and cs["dayun_year_end"] == 2030
        assert cs["next_ganzhi"] == "乙丑" and cs["next_year"] == 2031
        assert cs["birth_solar"][:5] == (1999, 5, 13, 9, 0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
