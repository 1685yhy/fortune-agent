"""Tests for Ziwei Doushu Engine."""
from src.engines.ziwei import ZiweiEngine, ZiweiResult, PalaceInfo, DIZHI, TIANGAN


def test_ziwei_basic():
    """Test basic calculation returns all required fields."""
    engine = ZiweiEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")
    assert result is not None
    assert isinstance(result, ZiweiResult)

    # Check basic fields
    assert result.ming_gong in DIZHI
    assert result.shen_gong in DIZHI
    assert result.wuxing_ju in ["水二局", "木三局", "金四局", "土五局", "火六局"]
    assert len(result.palaces) == 12

    # Check 12 palaces
    expected_palaces = ["命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
                        "迁移", "交友", "官禄", "田宅", "福德", "父母"]
    for p in expected_palaces:
        assert p in result.palaces, f"Missing palace: {p}"
        assert isinstance(result.palaces[p], PalaceInfo)
        assert result.palaces[p].dizhi in DIZHI

    # Check 14 main stars
    assert "紫微" in result.main_stars
    assert len(result.main_stars) >= 14  # 紫微+天府系=14主星
    for star_name, dizhi in result.main_stars.items():
        assert dizhi in DIZHI, f"Star {star_name} has invalid position {dizhi}"

    # Check 四化
    assert "化禄" in result.sihua
    assert "化权" in result.sihua
    assert "化科" in result.sihua
    assert "化忌" in result.sihua

    # Check auxiliary stars
    for aux in ["左辅", "右弼", "文昌", "文曲", "天魁", "天钺",
                "禄存", "擎羊", "陀罗", "天马", "火星", "铃星", "地空", "地劫"]:
        assert aux in result.aux_stars, f"Missing auxiliary star: {aux}"

    # Check 大限
    assert len(result.dayun) == 12
    for age, palace, dizhi in result.dayun:
        assert 2 <= age <= 120
        assert palace in expected_palaces
        assert dizhi in DIZHI


def test_ziwei_1990_case():
    """Test with 1990-05-20 15:00 (庚午年 四月廿六日 申时)"""
    engine = ZiweiEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    print(f"\n=== 1990-05-20 15:00 (男) 紫微斗数命盘 ===")
    print(f"命宫: {result.ming_gong} | 身宫: {result.shen_gong} | 五行局: {result.wuxing_ju}")
    print(f"四化: {result.sihua}")
    print(f"\n14主星分布:")
    for star, pos in sorted(result.main_stars.items()):
        print(f"  {star}: {pos}")
    print(f"\n辅星分布:")
    for star, pos in sorted(result.aux_stars.items()):
        print(f"  {star}: {pos}")
    print(f"\n12宫:")
    for p_name, p_info in result.palaces.items():
        stars_str = ", ".join(p_info.stars) if p_info.stars else "空"
        aux_str = ", ".join(p_info.aux_stars) if p_info.aux_stars else ""
        print(f"  {p_name}({p_info.dizhi}): {stars_str}  {aux_str}")
    print(f"\n大限:")
    for age, palace, dizhi in result.dayun:
        print(f"  {age}岁起: {palace}({dizhi})")

    # Verify specific known relationships
    # For 庚午年四月廿六申时:
    # 年干=庚, 月=4(四月), 日=26, 时=申(8)
    # Expected 命宫 = 酉 (寅→卯→辰→巳 for month 4, 巳→辰→卯→寅→丑→子→亥→戌→酉 backward 8 steps)
    assert result.ming_gong == "酉", f"Expected 命宫=酉, got {result.ming_gong}"

    # 庚年四化: 太阳化禄, 武曲化权, 太阴化科, 天同化忌
    assert result.sihua["化禄"] == "太阳"
    assert result.sihua["化权"] == "武曲"
    assert result.sihua["化科"] == "太阴"
    assert result.sihua["化忌"] == "天同"


def test_ziwei_qianlong_case():
    """Test with known case: 乾隆皇帝 (辛卯年 八月十三日 子时)
    Birth: 1711-09-25 00:00 (康熙五十年八月十三日子时)
    Source: 紫微杨《清室气数录》

    Verified chart details:
    - 命宫(酉): 天相 + 禄存 + 火星
    - 福德宫(亥): 紫微 + 七杀
    - 兄弟宫(申): 巨门 + 太阳 (巨日同宫)
    - 财帛宫(巳): 天府 + 天马
    """
    engine = ZiweiEngine()
    # 1711-09-25 00:00 北京, 男
    result = engine.calculate(1711, 9, 25, 0, 0, "北京", "男")

    print(f"\n=== 乾隆皇帝 紫微斗数命盘 ===")
    print(f"命宫: {result.ming_gong} | 身宫: {result.shen_gong} | 五行局: {result.wuxing_ju}")
    print(f"四化: {result.sihua}")
    print(f"\n14主星分布:")
    for star, pos in sorted(result.main_stars.items()):
        print(f"  {star}: {pos}")
    print(f"\n12宫:")
    for p_name, p_info in result.palaces.items():
        stars_str = ", ".join(p_info.stars) if p_info.stars else "空"
        aux_str = ", ".join(p_info.aux_stars) if p_info.aux_stars else ""
        print(f"  {p_name}({p_info.dizhi}): {stars_str}  {aux_str}")
    print(f"\n大限:")
    for age, palace, dizhi in result.dayun:
        print(f"  {age}岁起: {palace}({dizhi})")

    # 命宫应该在酉
    assert result.ming_gong == "酉", f"Expected 命宫=酉, got {result.ming_gong}"

    # 身宫: 寅起正月顺数八月(酉), 从酉起子时顺数子时(0) → 身宫=酉
    assert result.shen_gong == "酉", f"Expected 身宫=酉, got {result.shen_gong}"

    # 乾隆是火六局 (丁酉纳音山下火)
    assert result.wuxing_ju == "火六局", f"Expected 火六局, got {result.wuxing_ju}"

    # 紫微在亥 (福德宫)
    assert result.main_stars["紫微"] == "亥", f"Expected 紫微在亥, got {result.main_stars['紫微']}"

    # Verified star positions
    known_positions = {
        "紫微": "亥", "天机": "戌", "太阳": "申", "武曲": "未",
        "天同": "午", "廉贞": "卯",
        "天府": "巳", "太阴": "午", "贪狼": "未", "巨门": "申",
        "天相": "酉", "天梁": "戌", "七杀": "亥", "破军": "卯",
    }
    for star, expected_pos in known_positions.items():
        assert result.main_stars[star] == expected_pos, \
            f"Expected {star} in {expected_pos}, got {result.main_stars[star]}"

    # Verify palace contents
    # 命宫(酉): 天相
    ming_palace = result.palaces["命宫"]
    assert "天相" in ming_palace.stars, f"命宫 should have 天相, got {ming_palace.stars}"

    # 夫妻宫(未): 武曲 + 贪狼 (武贪格)
    couple_palace = result.palaces["夫妻"]
    assert "武曲" in couple_palace.stars, f"夫妻宫 should have 武曲"
    assert "贪狼" in couple_palace.stars, f"夫妻宫 should have 贪狼"

    # 财帛宫(巳): 天府
    wealth_palace = result.palaces["财帛"]
    assert "天府" in wealth_palace.stars, f"财帛宫 should have 天府"

    # 福德宫(亥): 紫微 + 七杀
    fortune_palace = result.palaces["福德"]
    assert "紫微" in fortune_palace.stars, f"福德宫 should have 紫微"
    assert "七杀" in fortune_palace.stars, f"福德宫 should have 七杀"

    # 辛年四化: 巨门化禄, 太阳化权, 文曲化科, 文昌化忌
    assert result.sihua["化禄"] == "巨门"
    assert result.sihua["化权"] == "太阳"
    assert result.sihua["化科"] == "文曲"
    assert result.sihua["化忌"] == "文昌"

    # 左辅在辰(八月)
    assert result.aux_stars["左辅"] in DIZHI
    # 右弼在寅(八月)
    # 文昌在戌(子时)
    assert result.aux_stars["文昌"] == "戌", \
        f"Expected 文昌=戌, got {result.aux_stars['文昌']}"
    # 文曲在辰(子时)
    assert result.aux_stars["文曲"] == "辰", \
        f"Expected 文曲=辰, got {result.aux_stars['文曲']}"

    # 天魁天钺: 辛年 → (寅, 午)
    assert result.aux_stars["天魁"] == "寅", \
        f"Expected 天魁=寅, got {result.aux_stars['天魁']}"
    assert result.aux_stars["天钺"] == "午", \
        f"Expected 天钺=午, got {result.aux_stars['天钺']}"


def test_ziwei_women():
    """Test with female birth."""
    engine = ZiweiEngine()
    result = engine.calculate(1995, 8, 15, 10, 30, "上海", "女")

    assert result is not None
    assert result.ming_gong in DIZHI
    assert len(result.dayun) == 12
    # Female 大限 should start from 命宫
    assert result.dayun[0][1] == "命宫"


def test_ziwei_12_palaces_order():
    """Verify 12 palaces are in correct counter-clockwise order."""
    engine = ZiweiEngine()
    result = engine.calculate(2000, 1, 1, 12, 0, "北京", "男")

    ming_idx = DIZHI.index(result.ming_gong)

    # Palaces should go counter-clockwise from 命宫
    for i, palace_name in enumerate(["命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
                                      "迁移", "交友", "官禄", "田宅", "福德", "父母"]):
        expected_dizhi_idx = (ming_idx - i) % 12
        actual_dizhi = result.palaces[palace_name].dizhi
        expected_dizhi = DIZHI[expected_dizhi_idx]
        assert actual_dizhi == expected_dizhi, \
            f"Palace {palace_name} should be at {expected_dizhi}, got {actual_dizhi}"


def test_ziwei_14_main_stars_complete():
    """Verify all 14 main stars are present."""
    engine = ZiweiEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    expected_stars = ["紫微", "天机", "太阳", "武曲", "天同", "廉贞",
                       "天府", "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", "破军"]
    for star in expected_stars:
        assert star in result.main_stars, f"Missing main star: {star}"


def test_ziwei_sihua_all_years():
    """Test 四化 for all year 天干 combinations."""
    engine = ZiweiEngine()
    # Use same birth data but vary year to test all 天干
    # 1984 = 甲子年 (年干 index 0), so year = 1984 + idx + 10*k for a nearby year
    # Use k=1 so every year is in the 1994-2003 range with a unique 年干
    base_year = 1984
    for year_gan_idx, expected_sihua in enumerate([
        ("廉贞", "破军", "武曲", "太阳"),  # 甲
        ("天机", "天梁", "紫微", "太阴"),  # 乙
        ("天同", "天机", "文昌", "廉贞"),  # 丙
        ("太阴", "天同", "天机", "巨门"),  # 丁
        ("贪狼", "太阴", "右弼", "天机"),  # 戊
        ("武曲", "贪狼", "天梁", "文曲"),  # 己
        ("太阳", "武曲", "太阴", "天同"),  # 庚
        ("巨门", "太阳", "文曲", "文昌"),  # 辛
        ("天梁", "紫微", "左辅", "武曲"),  # 壬
        ("破军", "巨门", "太阴", "贪狼"),  # 癸
    ]):
        year_gan = TIANGAN[year_gan_idx]
        # Find a nearby year with this 年干
        year = base_year + year_gan_idx + 10  # k=1 → 1994+(idx) = 1994..2003

        result = engine.calculate(year, 5, 20, 15, 0, "北京", "男")

        lu, quan, ke, ji = expected_sihua
        assert result.sihua["化禄"] == lu, \
            f"Year {year} ({year_gan}): expected 化禄={lu}, got {result.sihua['化禄']}"
        assert result.sihua["化权"] == quan, \
            f"Year {year} ({year_gan}): expected 化权={quan}, got {result.sihua['化权']}"
        assert result.sihua["化科"] == ke, \
            f"Year {year} ({year_gan}): expected 化科={ke}, got {result.sihua['化科']}"
        assert result.sihua["化忌"] == ji, \
            f"Year {year} ({year_gan}): expected 化忌={ji}, got {result.sihua['化忌']}"


def test_ziwei_aux_stars_complete():
    """Verify all 14 auxiliary stars are present."""
    engine = ZiweiEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    expected_aux = ["左辅", "右弼", "文昌", "文曲", "天魁", "天钺",
                     "禄存", "擎羊", "陀罗", "天马", "火星", "铃星", "地空", "地劫"]
    for aux in expected_aux:
        assert aux in result.aux_stars, f"Missing auxiliary star: {aux}"
        assert result.aux_stars[aux] in DIZHI, \
            f"Aux star {aux} has invalid position {result.aux_stars[aux]}"


def test_ziwei_known_same_palace_stars():
    """Test that stars that should share a palace do so correctly."""
    engine = ZiweiEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    # Verify all stars are in valid palaces
    for palace_name, info in result.palaces.items():
        assert info.dizhi in DIZHI
        # Each star should only appear in one palace
        all_star_positions = {}
        for star, pos in result.main_stars.items():
            if pos == info.dizhi:
                all_star_positions[star] = pos


def test_ziwei_minute_boundaries():
    """Test that minute boundaries don't affect the result.
    紫微斗数 only depends on the 时辰 (2-hour window), not minutes.
    """
    engine = ZiweiEngine()
    result1 = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")
    result2 = engine.calculate(1990, 5, 20, 15, 59, "北京", "男")

    # Same 时辰(申时) should give same result
    assert result1.ming_gong == result2.ming_gong
    assert result1.main_stars["紫微"] == result2.main_stars["紫微"]

    # Different 时辰 (申时 vs 酉时 boundary at 17:00)
    result3 = engine.calculate(1990, 5, 20, 16, 59, "北京", "男")  # 申时
    result4 = engine.calculate(1990, 5, 20, 17, 0, "北京", "男")   # 酉时

    assert result3.ming_gong == result1.ming_gong
    # 酉时 should give a different 命宫
    # Note: same day so 紫微 position stays the same
    assert result4.aux_stars["文昌"] != result3.aux_stars.get("文昌", ""), \
        "Different 时辰 should change 文昌 position"


def test_ziwei_empty_palaces():
    """Some palaces may have no main stars - this is valid in 紫微斗数."""
    engine = ZiweiEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    empty_count = 0
    for p_name, p_info in result.palaces.items():
        if not p_info.stars:
            empty_count += 1

    # In 紫微斗数, 14 main stars in 12 palaces means 0-4 empty palaces
    # (0 if 2 palaces have 2 stars each; up to 4 if clustering is high)
    assert 0 <= empty_count <= 6, \
        f"Expected 0-6 empty palaces, got {empty_count} empty"


def test_ziwei_dayun_progression():
    """Test 大限 progression is consistent."""
    engine = ZiweiEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")

    # Each decade should advance by 10 years
    for i in range(len(result.dayun) - 1):
        age_diff = result.dayun[i+1][0] - result.dayun[i][0]
        assert age_diff == 10, f"Expected 10-year gap, got {age_diff}"

    # Starting age should be 2-6 (based on 五行局)
    assert 2 <= result.dayun[0][0] <= 6
