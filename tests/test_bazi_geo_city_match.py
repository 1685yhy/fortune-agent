"""R2-6（Gap A）：真太阳时 geo 城市名匹配升级——行政区划前缀/后缀不再静默失效。

用户 2026-09-04 生产实锤：档案 city='吉林省长春市'（带省前缀全路径），
BaziEngine._city_longitude 旧实现只做「市/省」尾缀去除 → 查不到经纬度 →
_true_solar_time 静默跳过真太阳时修正 → 起运/交运与问真差 2 天。

修复：匹配策略逐级升级（_city_longitude 返回类型 Optional[float] 不变）：
1. 原样精确命中；2. 去「市」「省」尾缀；3. 剥行政区划前缀（省/自治区/
特别行政区）后再走 1+2；4. 最长后缀兜底（'吉林省长春市' 以 '长春' 结尾）；
5. 全部不中 → None + logger.warning 一行（本批「不静默」要求）。

本文件全部断言在修复前必失败（TDD）：'吉林省长春市' → None（断言 125.35
失败）；真实用户路径端到端 qiyun 与 city='长春' 不一致（断言失败）；
未知城市无 warning（caplog 断言失败）。

运行：/home/a/fortune-agent/.venv/bin/python -m pytest tests/test_bazi_geo_city_match.py -q
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.engines.bazi import BaziEngine, CITY_LONGLAT  # noqa: E402


# ================================================================
# 1) _city_longitude 匹配矩阵（修复前：'吉林省长春市' → None）
# ================================================================

class TestCityLongitudeMatch:
    def test_plain_city_exact_hit(self):
        """原样精确命中（现有行为不回归）。"""
        assert BaziEngine._city_longitude("长春") == 125.35

    def test_city_with_shi_suffix(self):
        """去「市」尾缀命中（现有行为不回归）。"""
        assert BaziEngine._city_longitude("长春市") == 125.35

    def test_province_prefixed_city_full_path(self):
        """生产实锤：'吉林省长春市' → 125.35（修复前 None → 断言失败）。"""
        assert BaziEngine._city_longitude("吉林省长春市") == 125.35

    def test_province_bare_city_no_shi(self):
        """'吉林长春'（省名+市名无尾缀）→ 125.35（最长后缀兜底）。"""
        assert BaziEngine._city_longitude("吉林长春") == 125.35

    def test_jilin_city_not_confused_with_changchun(self):
        """'吉林市' → 126.55（126.55 吉林 ≠ 125.35 长春，防混淆）。"""
        assert BaziEngine._city_longitude("吉林市") == 126.55

    def test_jilin_province_prefixed_jilin_city(self):
        """'吉林省吉林市' → 126.55（剥省前缀后精确命中吉林市）。"""
        assert BaziEngine._city_longitude("吉林省吉林市") == 126.55

    def test_municipality_beijing_with_shi(self):
        """直辖市 '北京市' → 北京经度（表键 '北京': 116.40）。"""
        assert "北京" in CITY_LONGLAT
        assert BaziEngine._city_longitude("北京市") == CITY_LONGLAT["北京"][0]

    def test_autonomous_region_prefixed_city(self):
        """自治区前缀：'广西壮族自治区南宁市' → 南宁经度。"""
        assert BaziEngine._city_longitude("广西壮族自治区南宁市") == CITY_LONGLAT["南宁"][0]

    def test_unknown_city_returns_none_with_warning(self, caplog):
        """未知城市 '火星' → None（行为兼容不抛异常）+ warning 一行（含原文）。"""
        with caplog.at_level(logging.WARNING, logger="src.engines.bazi"):
            assert BaziEngine._city_longitude("火星") is None
        assert any("火星" in r.message and "城市" in r.message
                   for r in caplog.records), \
            f"未知城市必须 warning（不静默），实际记录: {[r.message for r in caplog.records]}"

    def test_empty_city_silent_none_no_warning(self, caplog):
        """空 city → None 静默（不传 city = 不做修正的口径，非失效不告警）。"""
        with caplog.at_level(logging.WARNING, logger="src.engines.bazi"):
            assert BaziEngine._city_longitude("") is None
            assert BaziEngine._city_longitude(None) is None
        assert not any(r.levelno >= logging.WARNING for r in caplog.records), \
            f"空 city 不应告警: {[r.message for r in caplog.records]}"

    def test_nested_two_level_city_innermost_wins(self):
        """'吉林省长春市榆树市' 类嵌套 → 最内层城市：榆树（126.53）。"""
        assert BaziEngine._city_longitude("吉林省榆树市") == CITY_LONGLAT["榆树"][0]


# ================================================================
# 2) 真实用户路径端到端（生产档案字面量，勿自造顺手值）
# ================================================================

# 生产 persons 表真实档案：calendar=lunar, 1999-03-28, birth_hour=9,
# city='吉林省长春市', gender='男'（问真基准：起运 2年4月22天0时，交运白露后27天）
_Y, _M, _D = 1999, 5, 13  # 档案农历 1999-03-28 的公历等价（R2-5 契约）


class TestRealUserPath:
    def test_archive_province_prefixed_city_qiyun_equals_plain_city(self):
        """hour=9：'吉林省长春市' 的 qiyun_desc 必须 == 同参 city='长春'
        （档案省前缀不再导致真太阳时修正被静默跳过）。"""
        with_province = BaziEngine().calculate(
            _Y, _M, _D, 9, 0, "吉林省长春市", "男", solar_time=True)
        plain = BaziEngine().calculate(
            _Y, _M, _D, 9, 0, "长春", "男", solar_time=True)
        assert with_province.qiyun_desc == plain.qiyun_desc, (
            f"省前缀城市修正被跳过: {with_province.qiyun_desc!r} != {plain.qiyun_desc!r}")

    def test_archive_path_hour_11_north_star_exact(self):
        """hour=11 版本 == 北极星断言逐字（问真基准，与 city='长春' 逐字一致）：
        qiyun_desc == "出生后2年4月22天0时起运"；
        jiaoyun：gan_pair 辛、丙 / jie 白露 / 白露后27天。"""
        r = BaziEngine().calculate(
            _Y, _M, _D, 11, 0, "吉林省长春市", "男", solar_time=True)
        assert r.qiyun_desc == "出生后2年4月22天0时起运", r.qiyun_desc
        jy = r.jiaoyun
        assert jy["gan_pair"] == "辛、丙"
        assert jy["jie"] == "白露"
        assert jy["days_after_jie"] == 27
        assert "白露后27天" in jy["page_text"]

    def test_province_prefixed_and_plain_qiyun_hour_11_identical(self):
        """hour=11：省前缀版本与 '长春' 版 qiyun 逐字相等（互为印证）。"""
        a = BaziEngine().calculate(_Y, _M, _D, 11, 0, "吉林省长春市", "男",
                                   solar_time=True)
        b = BaziEngine().calculate(_Y, _M, _D, 11, 0, "长春", "男",
                                   solar_time=True)
        assert a.qiyun_desc == b.qiyun_desc
        assert a.bazi == b.bazi

    def test_solar_time_false_still_no_correction_unchanged(self):
        """solar_time=False（用户关闭真太阳时）行为零回退：
        修正本来就不做 → 省前缀与否无差异（对照修复前 bug 特征）。"""
        a = BaziEngine().calculate(_Y, _M, _D, 9, 0, "吉林省长春市", "男",
                                   solar_time=False)
        b = BaziEngine().calculate(_Y, _M, _D, 9, 0, "长春", "男",
                                   solar_time=False)
        assert a.qiyun_desc == b.qiyun_desc
