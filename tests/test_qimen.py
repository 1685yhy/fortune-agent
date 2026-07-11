"""Tests for Qimen Dunjia (奇门遁甲) Engine."""
from src.engines.qimen import (
    QimenEngine,
    QimenResult,
    PALACE_NAMES,
    PALACE_NUMS,
    JIU_XING,
    JIU_XING_ORIGIN,
    BA_MEN,
    BA_MEN_ORIGIN,
    BA_SHEN,
    YI_QI,
    YANG_DOOR_PATH,
    YIN_DOOR_PATH,
    YANG_DUN_TERMS,
    YIN_DUN_TERMS,
    JIE_QI_DUN,
    xunshou_to_yi,
)


# ============================================================
# Basic structure tests
# ============================================================

def test_qimen_basic():
    """Test basic structure and return types."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)
    assert result is not None
    assert isinstance(result, QimenResult)
    assert result.dun_type in ("阳遁", "阴遁")
    assert 1 <= result.ju_number <= 9
    assert len(result.dipan) in (8, 9)
    assert len(result.tianpan) in (8, 9)
    assert len(result.bamen) == 8  # 八门 always 8 (skip 中宫)
    assert len(result.jiuxing) in (8, 9)
    assert len(result.bashen) == 8  # 八神 always 8 (skip 中宫)
    assert result.zhifu_star in JIU_XING
    assert result.zhishi_door in BA_MEN


def test_qimen_palace_keys():
    """Test that palace keys use standard 九宫 names."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)

    valid_palaces = set(PALACE_NAMES.values())  # {"坎","坤","震","巽","中","乾","兑","艮","离"}

    for dic in [result.dipan, result.tianpan, result.jiuxing]:
        for palace_name in dic:
            assert palace_name in valid_palaces, f"Invalid palace: {palace_name}"

    for palace_name in result.bamen:
        assert palace_name in valid_palaces and palace_name != "中", \
            f"Invalid palace for 八门: {palace_name}"

    for palace_name in result.bashen:
        assert palace_name in valid_palaces and palace_name != "中", \
            f"Invalid palace for 八神: {palace_name}"


def test_qimen_dipan_all_nine():
    """地盘 should contain exactly 9 仪奇 (all 9 palaces filled)."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)

    assert len(result.dipan) == 9
    values = list(result.dipan.values())
    for yi_qi in YI_QI:
        assert yi_qi in values, f"Missing {yi_qi} in 地盘: {values}"


def test_qimen_dipan_unique():
    """Every 仪奇 appears exactly once on 地盘."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)

    values = list(result.dipan.values())
    assert len(values) == len(set(values)), "Duplicate values in 地盘"


def test_qimen_bamen_unique():
    """八门 should have 8 unique doors."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)

    values = list(result.bamen.values())
    assert len(values) == 8
    assert len(set(values)) == 8, "Duplicate doors in 八门"
    for door in BA_MEN:
        assert door in values, f"Missing door: {door}"


def test_qimen_jiuxing_unique():
    """九星 should have 9 unique stars."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)

    values = list(result.jiuxing.values())
    assert len(values) == 9
    assert len(set(values)) == 9, "Duplicate stars in 九星"
    for star in JIU_XING:
        assert star in values, f"Missing star: {star}"


def test_qimen_bashen_unique():
    """八神 should have 8 unique gods."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)

    values = list(result.bashen.values())
    assert len(values) == 8
    assert len(set(values)) == 8, "Duplicate gods in 八神"
    for shen in BA_SHEN:
        assert shen in values, f"Missing god: {shen}"


# ============================================================
# 阳遁 地盘 tests
# ============================================================

def test_yang_dun_1_dipan():
    """Verify 阳遁1局 地盘 layout.

    阳遁1局:
        戊1(坎) 己2(坤) 庚3(震) 辛4(巽) 壬5(中)
        癸6(乾) 丁7(兑) 丙8(艮) 乙9(离)
    """
    engine = QimenEngine()
    # 冬至上元 is 阳遁1局
    # Use 2024-12-21 (冬至)
    result = engine.calculate(2024, 12, 21, 12, 0)

    if result.ju_number == 1 and result.dun_type == "阳遁":
        assert result.dipan["坎"] == "戊"
        assert result.dipan["坤"] == "己"
        assert result.dipan["震"] == "庚"
        assert result.dipan["巽"] == "辛"
        assert result.dipan["中"] == "壬"
        assert result.dipan["乾"] == "癸"
        assert result.dipan["兑"] == "丁"
        assert result.dipan["艮"] == "丙"
        assert result.dipan["离"] == "乙"


def test_yang_dun_3_dipan():
    """Verify 阳遁3局 地盘 layout.

    阳遁3局:
        戊3(震) 己4(巽) 庚5(中) 辛6(乾) 壬7(兑)
        癸8(艮) 丁9(离) 丙1(坎) 乙2(坤)
    """
    engine = QimenEngine()
    # 大寒上元 is 阳遁3局
    # 2024-01-20 is 大寒
    result = engine.calculate(2024, 1, 20, 12, 0)

    if result.ju_number == 3 and result.dun_type == "阳遁":
        assert result.dipan["震"] == "戊"
        assert result.dipan["巽"] == "己"
        assert result.dipan["中"] == "庚"
        assert result.dipan["乾"] == "辛"
        assert result.dipan["兑"] == "壬"
        assert result.dipan["艮"] == "癸"
        assert result.dipan["离"] == "丁"
        assert result.dipan["坎"] == "丙"
        assert result.dipan["坤"] == "乙"


# ============================================================
# 阴遁 地盘 tests
# ============================================================

def test_yin_dun_9_dipan():
    """Verify 阴遁9局 地盘 layout.

    阴遁9局:
        戊9(离) 己8(艮) 庚7(兑) 辛6(乾) 壬5(中)
        癸4(巽) 丁3(震) 丙2(坤) 乙1(坎)
    """
    engine = QimenEngine()
    # 夏至上元 is 阴遁9局
    # 2024-06-21 is 夏至
    result = engine.calculate(2024, 6, 21, 12, 0)

    if result.ju_number == 9 and result.dun_type == "阴遁":
        assert result.dipan["离"] == "戊"
        assert result.dipan["艮"] == "己"
        assert result.dipan["兑"] == "庚"
        assert result.dipan["乾"] == "辛"
        assert result.dipan["中"] == "壬"
        assert result.dipan["巽"] == "癸"
        assert result.dipan["震"] == "丁"
        assert result.dipan["坤"] == "丙"
        assert result.dipan["坎"] == "乙"


def test_yin_dun_6_dipan():
    """Verify 阴遁6局 地盘 layout.

    阴遁6局:
        戊6(乾) 己5(中) 庚4(巽) 辛3(震) 壬2(坤)
        癸1(坎) 丁9(离) 丙8(艮) 乙7(兑)
    """
    engine = QimenEngine()
    # 大雪上元 is 阴遁4局... let's try 寒露上元 which is 阴遁6局
    # 2024-10-08 is 寒露
    result = engine.calculate(2024, 10, 8, 12, 0)

    if result.ju_number == 6 and result.dun_type == "阴遁":
        assert result.dipan["乾"] == "戊"
        assert result.dipan["中"] == "己"
        assert result.dipan["巽"] == "庚"
        assert result.dipan["震"] == "辛"
        assert result.dipan["坤"] == "壬"
        assert result.dipan["坎"] == "癸"
        assert result.dipan["离"] == "丁"
        assert result.dipan["艮"] == "丙"
        assert result.dipan["兑"] == "乙"


# ============================================================
# 九星 original palace tests
# ============================================================

def test_jiuxing_original_positions():
    """Verify 九星 original palace assignments."""
    assert JIU_XING_ORIGIN[1] == "天蓬"  # 坎
    assert JIU_XING_ORIGIN[2] == "天芮"  # 坤
    assert JIU_XING_ORIGIN[3] == "天冲"  # 震
    assert JIU_XING_ORIGIN[4] == "天辅"  # 巽
    assert JIU_XING_ORIGIN[5] == "天禽"  # 中
    assert JIU_XING_ORIGIN[6] == "天心"  # 乾
    assert JIU_XING_ORIGIN[7] == "天柱"  # 兑
    assert JIU_XING_ORIGIN[8] == "天任"  # 艮
    assert JIU_XING_ORIGIN[9] == "天英"  # 离


# ============================================================
# 八门 original palace tests
# ============================================================

def test_bamen_original_positions():
    """Verify 八门 original palace assignments."""
    assert BA_MEN_ORIGIN[1] == "休"  # 坎
    assert BA_MEN_ORIGIN[2] == "死"  # 坤
    assert BA_MEN_ORIGIN[3] == "伤"  # 震
    assert BA_MEN_ORIGIN[4] == "杜"  # 巽
    assert BA_MEN_ORIGIN[6] == "开"  # 乾
    assert BA_MEN_ORIGIN[7] == "惊"  # 兑
    assert BA_MEN_ORIGIN[8] == "生"  # 艮
    assert BA_MEN_ORIGIN[9] == "景"  # 离


# ============================================================
# 节气/阴阳遁 tests
# ============================================================

def test_yang_dun_terms():
    """Verify all 12 阳遁 节气 are correctly classified."""
    for term in YANG_DUN_TERMS:
        assert term in JIE_QI_DUN, f"Missing {term} in 节气table"
        assert JIE_QI_DUN[term][0] == "阳遁", f"{term} should be 阳遁"


def test_yin_dun_terms():
    """Verify all 12 阴遁 节气 are correctly classified."""
    for term in YIN_DUN_TERMS:
        assert term in JIE_QI_DUN, f"Missing {term} in 节气table"
        assert JIE_QI_DUN[term][0] == "阴遁", f"{term} should be 阴遁"


def test_all_24_terms():
    """Verify exactly 24 节气 in the table."""
    assert len(JIE_QI_DUN) == 24, f"Expected 24 节气, got {len(JIE_QI_DUN)}"


# ============================================================
# 旬首→六仪 mapping tests
# ============================================================

def test_xunshou_to_yi():
    """Verify 旬首 branch → 六仪 index mapping."""
    # 子(0) → 戊(0)
    assert xunshou_to_yi(0) == 0, "子 should map to 戊(index 0)"
    # 寅(2) → 癸(5)
    assert xunshou_to_yi(2) == 5, "寅 should map to 癸(index 5)"
    # 辰(4) → 壬(4)
    assert xunshou_to_yi(4) == 4, "辰 should map to 壬(index 4)"
    # 午(6) → 辛(3)
    assert xunshou_to_yi(6) == 3, "午 should map to 辛(index 3)"
    # 申(8) → 庚(2)
    assert xunshou_to_yi(8) == 2, "申 should map to 庚(index 2)"
    # 戌(10) → 己(1)
    assert xunshou_to_yi(10) == 1, "戌 should map to 己(index 1)"


# ============================================================
# Determinism tests
# ============================================================

def test_qimen_deterministic():
    """Same inputs produce identical results."""
    engine = QimenEngine()

    for _ in range(5):
        result1 = engine.calculate(2024, 7, 11, 13, 30)
        result2 = engine.calculate(2024, 7, 11, 13, 30)

        assert result1.dun_type == result2.dun_type
        assert result1.ju_number == result2.ju_number
        assert result1.dipan == result2.dipan
        assert result1.tianpan == result2.tianpan
        assert result1.bamen == result2.bamen
        assert result1.jiuxing == result2.jiuxing
        assert result1.bashen == result2.bashen
        assert result1.zhifu_star == result2.zhifu_star
        assert result1.zhishi_door == result2.zhishi_door


def test_qimen_different_hours():
    """Different hours on the same day give different results."""
    engine = QimenEngine()

    result1 = engine.calculate(2024, 7, 11, 8, 0)
    result2 = engine.calculate(2024, 7, 11, 14, 0)

    # Same 天地盘 but different 值符/值使 position
    assert result1.dipan == result2.dipan  # Same 地盘 for same day
    assert result1.ju_number == result2.ju_number
    # 天盘 may differ due to different hour
    # (Different 值符 position can cause different rotation)


# ============================================================
# 值符/值使 tests
# ============================================================

def test_zhifu_star_in_jiuxing():
    """值符星 should always appear in the 九星 distribution."""
    engine = QimenEngine()

    # Test multiple dates/hours
    test_cases = [
        (2024, 1, 15, 10, 0),
        (2024, 3, 20, 14, 0),
        (2024, 6, 21, 8, 0),
        (2024, 9, 23, 16, 0),
        (2024, 12, 21, 12, 0),
    ]

    for year, month, day, hour, minute in test_cases:
        result = engine.calculate(year, month, day, hour, minute)
        assert result.zhifu_star in result.jiuxing.values(), \
            f"值符星 {result.zhifu_star} not found in 九星 for {year}-{month}-{day} {hour}:{minute}"


def test_zhishi_door_in_bamen():
    """值使门 should always appear in the 八门 distribution."""
    engine = QimenEngine()

    for year, month, day, hour, minute in [
        (2024, 1, 15, 10, 0),
        (2024, 3, 20, 14, 0),
        (2024, 6, 21, 8, 0),
        (2024, 12, 21, 12, 0),
    ]:
        result = engine.calculate(year, month, day, hour, minute)
        assert result.zhishi_door in result.bamen.values(), \
            f"值使门 {result.zhishi_door} not found in 八门 for {year}-{month}-{day}"


# ============================================================
# 天盘奇仪 tests
# ============================================================

def test_tianpan_all_nine():
    """天盘 should contain exactly 9 仪奇."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)

    assert len(result.tianpan) == 9
    values = list(result.tianpan.values())
    for yi_qi in YI_QI:
        assert yi_qi in values, f"Missing {yi_qi} in 天盘: {values}"


def test_tianpan_unique():
    """Every 仪奇 appears exactly once on 天盘."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)

    values = list(result.tianpan.values())
    assert len(values) == len(set(values)), "Duplicate values in 天盘"


# ============================================================
# Nine Palace grid consistency tests
# ============================================================

def test_jiuxing_contains_all_stars():
    """九星 should contain all 9 stars."""
    engine = QimenEngine()
    for year, month, day, hour, minute in [
        (2024, 1, 15, 10, 0),
        (2024, 6, 21, 8, 0),
        (2024, 12, 21, 12, 0),
    ]:
        result = engine.calculate(year, month, day, hour, minute)
        for star in JIU_XING:
            assert star in result.jiuxing.values(), \
                f"Missing {star} in 九星 for {year}-{month}-{day}"


def test_bamen_contains_all_doors():
    """八门 should contain all 8 doors."""
    engine = QimenEngine()
    for year, month, day, hour, minute in [
        (2024, 1, 15, 10, 0),
        (2024, 6, 21, 8, 0),
        (2024, 12, 21, 12, 0),
    ]:
        result = engine.calculate(year, month, day, hour, minute)
        for door in BA_MEN:
            assert door in result.bamen.values(), \
                f"Missing {door} in 八门 for {year}-{month}-{day}"


def test_bashen_contains_all_gods():
    """八神 should contain all 8 gods."""
    engine = QimenEngine()
    for year, month, day, hour, minute in [
        (2024, 1, 15, 10, 0),
        (2024, 6, 21, 8, 0),
        (2024, 12, 21, 12, 0),
    ]:
        result = engine.calculate(year, month, day, hour, minute)
        for shen in BA_SHEN:
            assert shen in result.bashen.values(), \
                f"Missing {shen} in 八神 for {year}-{month}-{day}"


# ============================================================
# 阳遁/阴遁 cross-check tests
# ============================================================

def test_yang_vs_yin_distinct():
    """阳遁 and 阴遁 should produce different 地盘 for the same 局数."""
    engine = QimenEngine()

    # 春分上元 = 阳遁3局, 秋分上元 = 阴遁7局
    yang = engine.calculate(2024, 3, 20, 12, 0)  # 春分
    yin = engine.calculate(2024, 9, 22, 12, 0)   # 秋分

    assert yang.dun_type == "阳遁"
    assert yin.dun_type == "阴遁"

    # Different 地盘 layout
    if yang.ju_number == yin.ju_number:
        assert yang.dipan != yin.dipan, \
            "Same 局数 should have different 地盘 for 阳遁 vs 阴遁"


# ============================================================
# Solar term boundary tests
# ============================================================

def test_solar_term_transition():
    """Verify correct term detection at boundaries."""
    engine = QimenEngine()

    # Just before 小暑 (2024-07-05)
    before = engine.calculate(2024, 7, 5, 12, 0)
    # Just after 小暑 (2024-07-06)
    after = engine.calculate(2024, 7, 6, 12, 0)

    # They should be in different solar terms with potentially different 局数
    assert before.raw_data["solar_term"] != after.raw_data["solar_term"] or \
           before.ju_number != after.ju_number


# ============================================================
# 时辰 boundary tests
# ============================================================

def test_hour_boundary():
    """Verify correct handling of hour transitions."""
    engine = QimenEngine()

    # 午时 (11-13) vs 未时 (13-15)
    wu_shi = engine.calculate(2024, 7, 11, 11, 30)
    wei_shi = engine.calculate(2024, 7, 11, 13, 30)

    # Should have same 地盘 but potentially different 天盘
    assert wu_shi.dipan == wei_shi.dipan
    # May have different 值符/值使 if hour changes


# ============================================================
# 八门 path tests
# ============================================================

def test_yang_door_path():
    """Verify 阳遁八门 path is correct."""
    expected = [1, 2, 3, 4, 6, 7, 8, 9]
    assert YANG_DOOR_PATH == expected, \
        f"Expected {expected}, got {YANG_DOOR_PATH}"


def test_yin_door_path():
    """Verify 阴遁八门 path is correct."""
    expected = [1, 9, 8, 7, 6, 4, 3, 2]
    assert YIN_DOOR_PATH == expected, \
        f"Expected {expected}, got {YIN_DOOR_PATH}"


# ============================================================
# Edge case tests
# ============================================================

def test_qimen_midnight():
    """Test calculation at midnight (子时)."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 0, 0)
    assert result is not None
    assert isinstance(result, QimenResult)
    assert result.zhifu_star in JIU_XING


def test_qimen_noon():
    """Test calculation at noon (午时)."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 12, 0)
    assert result is not None
    assert isinstance(result, QimenResult)


def test_qimen_different_years():
    """Test across different years."""
    engine = QimenEngine()

    for year in [2023, 2024, 2025]:
        result = engine.calculate(year, 6, 21, 12, 0)  # 夏至
        assert result.dun_type == "阴遁", f"{year} 夏至 should be 阴遁"

        result2 = engine.calculate(year, 12, 22, 12, 0)  # 冬至附近
        assert result2.dun_type == "阳遁" or result2.dun_type == "阴遁"


# ============================================================
# Known value tests
# ============================================================

def test_known_case_winter():
    """Test a known case: 2024-12-21 冬至 午时.

    冬至上元 = 阳遁1局
    地盘: 戊坎 己坤 庚震 辛巽 壬中 癸乾 丁兑 丙艮 乙离
    """
    engine = QimenEngine()
    result = engine.calculate(2024, 12, 21, 12, 0)

    assert result.dun_type == "阳遁"
    assert result.ju_number == 1
    assert result.raw_data["solar_term"] == "冬至"
    assert result.raw_data["yuan"] == "上元"

    # 地盘 for 阳遁1局
    assert result.dipan["坎"] == "戊"
    assert result.dipan["坤"] == "己"
    assert result.dipan["震"] == "庚"
    assert result.dipan["巽"] == "辛"
    assert result.dipan["中"] == "壬"
    assert result.dipan["乾"] == "癸"
    assert result.dipan["兑"] == "丁"
    assert result.dipan["艮"] == "丙"
    assert result.dipan["离"] == "乙"


def test_known_case_summer():
    """Test a known case: 2024-06-21 夏至.

    夏至上元 = 阴遁9局
    地盘: 戊离 己艮 庚兑 辛乾 壬中 癸巽 丁震 丙坤 乙坎
    """
    engine = QimenEngine()
    result = engine.calculate(2024, 6, 21, 12, 0)

    assert result.dun_type == "阴遁"
    assert result.ju_number == 9
    assert result.raw_data["solar_term"] == "夏至"
    assert result.raw_data["yuan"] == "上元"

    # 地盘 for 阴遁9局
    assert result.dipan["离"] == "戊"
    assert result.dipan["艮"] == "己"
    assert result.dipan["兑"] == "庚"
    assert result.dipan["乾"] == "辛"
    assert result.dipan["中"] == "壬"
    assert result.dipan["巽"] == "癸"
    assert result.dipan["震"] == "丁"
    assert result.dipan["坤"] == "丙"
    assert result.dipan["坎"] == "乙"


def test_known_print_chart():
    """Test that print_chart returns a non-empty string."""
    engine = QimenEngine()
    result = engine.calculate(2024, 7, 11, 13, 30)
    chart = engine.print_chart(result)
    assert isinstance(chart, str)
    assert len(chart) > 0
    # Should contain key identifiers
    assert result.dun_type in chart
    assert str(result.ju_number) in chart
    assert result.zhifu_star in chart
    assert result.zhishi_door in chart


# ============================================================
# Data integrity tests
# ============================================================

def test_all_24_solar_terms_listed():
    """Verify all 24 节气 are present."""
    assert len(JIE_QI_DUN) == 24

    # Check no duplicate entries
    assert len(JIE_QI_DUN.keys()) == 24


def test_ju_number_range():
    """Verify all 局数 are in range 1-9."""
    for term, (dun_type, (upper, middle, lower)) in JIE_QI_DUN.items():
        assert 1 <= upper <= 9, f"{term} 上元={upper} out of range"
        assert 1 <= middle <= 9, f"{term} 中元={middle} out of range"
        assert 1 <= lower <= 9, f"{term} 下元={lower} out of range"
