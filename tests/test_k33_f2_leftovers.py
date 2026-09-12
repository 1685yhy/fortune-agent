# -*- coding: utf-8 -*-
"""k33/A16：F2 渐进式出生信息累积三项遗留（审计行逐项）。

1. `_AGE_UNIT_GUARD` 补 小/号/刻——"现在3小时了"/"现在10号了"/"已经4刻了"
   是时间不是年龄（旧守卫漏这三字 → 误推成 3 岁/10 岁 → 年份错到 cy-3）；
2. 年份正则补 公历/阳历/公元 前缀（"我公历1976生的"无「年」字 → 旧 F2 永远
   缺年份，分步补全死循环问年份；与 _extract_bazi_info:5516 同口径）；
3. 时辰回显的「以后/之后/过后」锚定到时间片段——"我5月13日以后出生，10点"
   不得回显成"10点以后"（旧实现全串 search）。

运行：OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 -m pytest \
      tests/test_k33_f2_leftovers.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from src.bot.handler import (  # noqa: E402
    MessageHandler,
    _extract_age,
    _format_partial_echo,
)


@pytest.fixture
def h():
    """只跑纯提取方法：不装配引擎（_extract_partial_birth 只依赖类属性/模块常量）。"""
    return object.__new__(MessageHandler)


# ══════════════════════════════════════════════════════════════
# 1) 年龄单位守卫：小 / 号 / 刻
# ══════════════════════════════════════════════════════════════

class TestAgeUnitGuard:
    @pytest.mark.parametrize("msg", [
        "现在已经3小时了",          # 小（"3小时" 曾被当 3 岁）
        "现在10号了",              # 号（日期，被当 10 岁）
        "现在已经5号",             # 号 + 前缀
        "都4刻了",                 # 刻
        "今年30个小时没睡了",       # 小（"个" 既有守卫）
    ])
    def test_time_units_not_age(self, msg):
        assert _extract_age(msg) is None

    @pytest.mark.parametrize("msg,age", [
        ("今年50岁了", 50),
        ("我50岁", 50),
        ("现在50", 50),            # 前缀词引导，"岁" 可省
        ("已经满了18岁", 18),
    ])
    def test_real_age_still_detected(self, msg, age):
        assert _extract_age(msg) == age

    def test_partial_birth_year_not_polluted_by_hours(self, h):
        """端到端：'现在已经3小时了' 不再推出 year=cy-3。"""
        assert "year" not in h._extract_partial_birth("现在已经3小时了")

    def test_partial_birth_year_not_polluted_by_day_number(self, h):
        assert "year" not in h._extract_partial_birth("现在10号了")


# ══════════════════════════════════════════════════════════════
# 2) 年份正则：公历 / 阳历 / 公元
# ══════════════════════════════════════════════════════════════

class TestYearPrefix:
    @pytest.mark.parametrize("msg,year", [
        ("我公历1976生的", 1976),
        ("公历1976年3月", 1976),
        ("阳历1976年3月5日", 1976),
        ("公元1976年", 1976),
        ("1976年3月5日", 1976),          # 既有口径不变
        ("我一九七六年的", 1976),         # 中文数字年不受影响
    ])
    def test_year_parsed(self, h, msg, year):
        assert h._extract_partial_birth(msg).get("year") == year

    def test_age_year_fallback_unchanged(self, h):
        """年龄推算路径不回归（current_year 显式传入）。"""
        out = h._extract_partial_birth("我今年50岁了", current_year=2026)
        assert out["year"] == 1976
        assert out["_age_used"] == 50

    def test_out_of_range_prefix_year_ignored(self, h):
        assert "year" not in h._extract_partial_birth("公历1200年了")


# ══════════════════════════════════════════════════════════════
# 3) 时辰回显锚定时间片段
# ══════════════════════════════════════════════════════════════

class TestEchoTimeSuffixAnchor:
    def test_day_suffix_does_not_pollute_hour_echo(self):
        """「5月13日以后」不得污染「10点」回显（审计验收口径）。"""
        assert _format_partial_echo({"hour": 10}, "我5月13日以后出生，10点") == "10点"

    def test_day_suffix_after_hour_still_marks(self):
        assert _format_partial_echo({"hour": 10}, "10点以后") == "10点以后"

    @pytest.mark.parametrize("msg", [
        "10点之后", "10点过后", "10:00以后", "接近11点以后", "下午3点30分以后",
    ])
    def test_time_suffix_variants_kept(self, msg):
        assert _format_partial_echo({"hour": 10}, msg).endswith("以后")

    def test_hour_aria_with_other_date_no_pollution(self):
        assert _format_partial_echo(
            {"hour": 10}, "我1976年5月13日生的，出生时间10点左右") == "10点"

    def test_shi_chen_branch_unchanged(self):
        assert _format_partial_echo({"hour": 0}, "子时以后") == "子时"

    def test_echo_with_month_day_and_time(self):
        """月日 + 时辰同时命中：月日回显不受影响。"""
        assert _format_partial_echo(
            {"month": 5, "day": 13, "hour": 10}, "5月13日以后，10点") == "5月13日、10点"
