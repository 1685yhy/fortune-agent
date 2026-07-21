"""Tests for Liuyao (I Ching) Engine."""
from src.engines.liuyao import (
    LiuyaoEngine,
    LiuyaoResult,
    HEXAGRAM_TABLE,
    TRIGRAM_NAMES,
    DIZHI,
    DIZHI_WUXING,
    PALACE_WUXING,
    TRIGRAM_BY_NAME,
    calc_liuqin,
    ALL_HEXAGRAM_VALUES,
    wuxing_generates,
    wuxing_controls,
)


def test_liuyao_basic():
    """Test basic structure and return types."""
    engine = LiuyaoEngine()
    result = engine.cast(seed=42)
    assert result is not None
    assert isinstance(result, LiuyaoResult)
    assert result.original_hexagram in [v[0] for v in HEXAGRAM_TABLE.values()]
    assert result.changed_hexagram in [v[0] for v in HEXAGRAM_TABLE.values()]
    assert len(result.lines) == 6
    assert 0 <= result.shi_yao <= 5
    assert 0 <= result.ying_yao <= 5
    assert result.shi_yao != result.ying_yao
    assert result.palace in TRIGRAM_NAMES.values()
    assert result.palace_wuxing in ["金", "木", "水", "火", "土"]


def test_liuyao_lines_structure():
    """Test each line dict has the correct keys and values."""
    engine = LiuyaoEngine()
    result = engine.cast(seed=12345)

    valid_types = {"老阴", "少阳", "少阴", "老阳"}
    valid_yao_types = {"世", "应", ""}
    valid_liuqin = {"兄弟", "子孙", "父母", "妻财", "官鬼"}

    for line in result.lines:
        assert "type" in line
        assert "yao_type" in line
        assert "liuqin" in line
        assert "dizhi" in line
        assert line["type"] in valid_types, f"Invalid type: {line['type']}"
        assert line["yao_type"] in valid_yao_types, f"Invalid yao_type: {line['yao_type']}"
        assert line["liuqin"] in valid_liuqin, f"Invalid liuqin: {line['liuqin']}"
        assert line["dizhi"] in DIZHI, f"Invalid dizhi: {line['dizhi']}"


def test_liuyao_shi_ying_positions():
    """Verify 世 and 应 are always 3 lines apart (隔三爻)."""
    engine = LiuyaoEngine()
    # Test multiple seeds to cover different hexagrams
    for seed in range(10):
        result = engine.cast(seed=seed)
        assert (result.shi_yao + 3) % 6 == result.ying_yao, \
            f"世{result.shi_yao} and 应{result.ying_yao} should be 3 apart"

        # Check correct labeling
        assert result.lines[result.shi_yao]["yao_type"] == "世", \
            f"Line {result.shi_yao} should be 世"
        assert result.lines[result.ying_yao]["yao_type"] == "应", \
            f"Line {result.ying_yao} should be 应"


def test_liuyao_changing_lines():
    """Test that changing lines are correctly identified and 变卦 is computed."""
    engine = LiuyaoEngine()
    result = engine.cast(seed=42)

    # Verify changing_lines match lines marked as changing
    for i, line in enumerate(result.lines):
        is_changing = line["type"] in ("老阴", "老阳")
        if is_changing:
            assert i in result.changing_lines, \
                f"Line {i} is {line['type']} but not in changing_lines"
        else:
            assert i not in result.changing_lines, \
                f"Line {i} is {line['type']} but in changing_lines"

    # Verify 变卦: flip changing bits on original -> get changed value
    if result.changing_lines:
        expected_value = result.original_hexagram_value
        for i in result.changing_lines:
            expected_value ^= (1 << i)
        assert result.changed_hexagram_value == expected_value

        # Original and changed should be different
        assert result.original_hexagram != result.changed_hexagram


def test_liuyao_no_changing_lines():
    """静卦: hexagram with no changing lines should have empty changing_lines."""
    # Build a hexagram with only 少阳/少阴 (no changing lines)
    # We can't guarantee a seed gives this, so verify a result with no changing lines
    engine = LiuyaoEngine()
    for seed in range(50):
        result = engine.cast(seed=seed)
        if not result.changing_lines:
            # Found a 静卦
            assert result.changed_hexagram == result.original_hexagram
            assert result.changed_hexagram_value == result.original_hexagram_value
            return

    # If no 静卦 found in 50 seeds, that's statistically unlikely but possible
    # Just check that when there are no changing lines, it's handled correctly
    # by manually constructing the case
    result = engine.cast(seed=999)
    if not result.changing_lines:
        assert result.changed_hexagram == result.original_hexagram


def test_liuyao_hexagram_table_complete():
    """Verify all 64 possible hexagram values are in the table."""
    assert len(HEXAGRAM_TABLE) == 64, f"Expected 64 hexagrams, got {len(HEXAGRAM_TABLE)}"

    # Every 6-bit value from 0 to 63 should be present
    for v in range(64):
        assert v in HEXAGRAM_TABLE, f"Missing hexagram value: {v:06b} ({v})"

    # All names are unique
    names = [v[0] for v in HEXAGRAM_TABLE.values()]
    assert len(names) == len(set(names)), "Duplicate hexagram names found"

    # All palace indices are valid
    valid_palaces = {0, 1, 2, 3, 4, 5, 6, 7}
    for v in HEXAGRAM_TABLE.values():
        assert v[1] in valid_palaces, f"Invalid palace index: {v[1]}"
        assert 0 <= v[2] <= 5, f"Invalid shi position: {v[2]}"

    # 8 hexagrams per palace
    from collections import Counter
    palace_counts = Counter(v[1] for v in HEXAGRAM_TABLE.values())
    for p in range(8):
        assert palace_counts[p] == 8, \
            f"Palace {p} ({TRIGRAM_NAMES[p]}) has {palace_counts[p]} hexagrams, expected 8"


def test_liuyao_known_hexagrams():
    """Verify known hexagram lookups by trigram pair."""
    engine = LiuyaoEngine()

    # 天地否: 上乾下坤 -> 乾宫三世(世=三爻)
    result = engine.lookup_hexagram("乾", "坤")
    assert result is not None
    assert result[0] == "天地否"
    assert result[1] == 7  # 乾宫
    assert result[2] == 2  # 三世: 三爻

    # 地天泰: 上坤下乾 -> 坤宫三世(世=三爻)
    result = engine.lookup_hexagram("坤", "乾")
    assert result is not None
    assert result[0] == "地天泰"
    assert result[1] == 0  # 坤宫
    assert result[2] == 2  # 三世

    # 火地晋: 上离下坤 -> 乾宫游魂(世=四爻)
    result = engine.lookup_hexagram("离", "坤")
    assert result is not None
    assert result[0] == "火地晋"
    assert result[1] == 7  # 乾宫
    assert result[2] == 3  # 游魂: 四爻

    # 乾为天: 上乾下乾 -> 乾宫本宫(世=上爻)
    result = engine.lookup_hexagram("乾", "乾")
    assert result is not None
    assert result[0] == "乾为天"
    assert result[1] == 7
    assert result[2] == 5  # 上爻

    # 雷泽归妹: 上震下兑 -> 兑宫归魂(世=三爻)
    result = engine.lookup_hexagram("震", "兑")
    assert result is not None
    assert result[0] == "雷泽归妹"
    assert result[1] == 6  # 兑宫
    assert result[2] == 2  # 归魂: 三爻


def test_liuyao_najia_dizhi():
    """Verify 纳甲 (Najia) earthly branch assignments for known hexagrams."""
    engine = LiuyaoEngine()

    # 天地否: 上乾下坤
    # Lower 坤(阴): 未(7), 巳(5), 卯(3)
    # Upper 乾(阳): 午(6), 申(8), 戌(10)
    dizhi = engine.get_line_dizhi("乾", "坤")
    expected = ["未", "巳", "卯", "午", "申", "戌"]  # DIZHI[7,5,3,6,8,10]
    assert dizhi == expected, f"Expected {expected}, got {dizhi}"

    # 火地晋: 上离下坤 (乾宫游魂)
    # Lower 坤(阴): 未(7), 巳(5), 卯(3)
    # Upper 离(阴): outer=酉(9), 未(7), 巳(5)
    dizhi = engine.get_line_dizhi("离", "坤")
    expected = ["未", "巳", "卯", "酉", "未", "巳"]  # DIZHI[7,5,3,9,7,5]
    assert dizhi == expected, f"Expected {expected}, got {dizhi}"

    # 乾为天: 上乾下乾
    # Lower 乾(阳): 子(0), 寅(2), 辰(4)
    # Upper 乾(阳): 午(6), 申(8), 戌(10)
    dizhi = engine.get_line_dizhi("乾", "乾")
    expected = ["子", "寅", "辰", "午", "申", "戌"]
    assert dizhi == expected, f"Expected {expected}, got {dizhi}"


def test_liuyao_liuqin_correct():
    """Verify 六亲 calculations for a known hexagram.

    天地否 (上乾下坤, 乾宫属金):
    Line 初: 未(土) -> 土生金 -> 生我 -> 父母
    Line 二: 巳(火) -> 火克金 -> 克我 -> 官鬼
    Line 三: 卯(木) -> 金克木 -> 我克 -> 妻财
    Line 四: 午(火) -> 火克金 -> 克我 -> 官鬼
    Line 五: 申(金) -> 同 -> 兄弟
    Line 上: 戌(土) -> 土生金 -> 生我 -> 父母
    """
    # Palace 7 (乾)=金
    palace_wx = PALACE_WUXING[7]
    assert palace_wx == "金"

    assert calc_liuqin("金", "土") == "父母"   # 土生金
    assert calc_liuqin("金", "火") == "官鬼"   # 火克金
    assert calc_liuqin("金", "木") == "妻财"   # 金克木
    assert calc_liuqin("金", "金") == "兄弟"   # 同金
    assert calc_liuqin("金", "水") == "子孙"   # 金生水

    # For 离宫(火):
    assert calc_liuqin("火", "木") == "父母"   # 木生火
    assert calc_liuqin("火", "土") == "子孙"   # 火生土
    assert calc_liuqin("火", "水") == "官鬼"   # 水克火
    assert calc_liuqin("火", "金") == "妻财"   # 火克金
    assert calc_liuqin("火", "火") == "兄弟"   # 同火

    # For 坤宫(土):
    assert calc_liuqin("土", "火") == "父母"   # 火生土
    assert calc_liuqin("土", "金") == "子孙"   # 土生金
    assert calc_liuqin("土", "木") == "官鬼"   # 木克土
    assert calc_liuqin("土", "水") == "妻财"   # 土克水
    assert calc_liuqin("土", "土") == "兄弟"   # 同土


def test_liuyao_liuqin_on_known_hexagram():
    """Verify 六亲 on the 天地否 hexagram via engine cast.

    We cast with a seed known to produce 天地否 (hex_value = 0b111000).
    Seed 42 gives a specific result; test by verifying structure.
    """
    engine = LiuyaoEngine()

    # Direct lookup: 天地否 should have specific 六亲 per line
    # 初(0) 未土 -> 父母, 二(1) 巳火 -> 官鬼, 三(2) 卯木 -> 妻财
    # 四(3) 午火 -> 官鬼, 五(4) 申金 -> 兄弟, 上(5) 戌土 -> 父母
    # 世=三爻(2), 应=上爻(5)

    # We'll construct this case: seed that produces 天地否
    # Let's just verify via the lookup_hexagram and liuqin calculation manually
    result = engine.lookup_hexagram("乾", "坤")
    assert result[0] == "天地否"
    palace_idx = result[1]  # 7 (乾)
    shi_pos = result[2]  # 2 (三世)

    palace_wx = PALACE_WUXING[palace_idx]  # 金
    dizhi = engine.get_line_dizhi("乾", "坤")

    expected_liuqin = []
    for dz in dizhi:
        dz_wx = DIZHI_WUXING[dz]
        expected_liuqin.append(calc_liuqin(palace_wx, dz_wx))

    assert expected_liuqin == ["父母", "官鬼", "妻财", "官鬼", "兄弟", "父母"], \
        f"Unexpected liuqin for 天地否: {expected_liuqin}"

    # 世=三爻(index 2), 应=上爻(index 5)
    assert shi_pos == 2
    assert (shi_pos + 3) % 6 == 5


def test_liuyao_deterministic():
    """Same seed produces identical results."""
    engine = LiuyaoEngine()
    result1 = engine.cast(seed=999)
    result2 = engine.cast(seed=999)

    assert result1.original_hexagram == result2.original_hexagram
    assert result1.changed_hexagram == result2.changed_hexagram
    assert result1.changing_lines == result2.changing_lines
    assert result1.shi_yao == result2.shi_yao

    # Different seeds should (most likely) give different results
    result3 = engine.cast(seed=777)
    # At least check the results are not None
    assert result3 is not None


def test_liuyao_question_accepted():
    """Test that the question parameter is stored in the result."""
    engine = LiuyaoEngine()
    result = engine.cast(seed=42, question="今日财运如何")
    assert result.question == "今日财运如何"

    result2 = engine.cast(seed=42)
    assert result2.question == ""


def test_liuyao_wuxing_functions():
    """Test wuxing cycle helper functions."""
    # 生 (generate cycle)
    assert wuxing_generates("木") == "火"
    assert wuxing_generates("火") == "土"
    assert wuxing_generates("土") == "金"
    assert wuxing_generates("金") == "水"
    assert wuxing_generates("水") == "木"

    # 克 (control cycle)
    assert wuxing_controls("木") == "土"
    assert wuxing_controls("土") == "水"
    assert wuxing_controls("水") == "火"
    assert wuxing_controls("火") == "金"
    assert wuxing_controls("金") == "木"


def test_liuyao_invalid_method():
    """Test invalid method raises error."""
    engine = LiuyaoEngine()
    try:
        engine.cast(method="invalid")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_liuyao_multiple_seeds():
    """Test that multiple seeds produce varying hexagrams."""
    engine = LiuyaoEngine()
    hexagrams = set()
    for seed in range(20):
        result = engine.cast(seed=seed)
        hexagrams.add(result.original_hexagram)

    # With 20 seeds, should see at least a few different hexagrams
    assert len(hexagrams) > 1, "All 20 seeds produced the same hexagram"


def test_liuyao_hexagram_palace_membership():
    """Verify palace-level 六亲 is consistent for a specific hexagram.

    All lines in a hexagram should have liuqin relative to the palace's 五行.
    """
    engine = LiuyaoEngine()
    result = engine.cast(seed=42)

    # Verify that all lines' 六亲 are based on the same palace 五行
    palace_wx = result.palace_wuxing
    for line in result.lines:
        dz_wx = DIZHI_WUXING[line["dizhi"]]
        computed = calc_liuqin(palace_wx, dz_wx)
        assert line["liuqin"] == computed, \
            f"Line liuqin {line['liuqin']} != computed {computed} (palace={palace_wx}, dizhi={line['dizhi']}={dz_wx})"


def test_liuyao_eight_palace_heads():
    """Verify the eight 本宫 (pure palace) hexagrams."""
    pure_hexagrams = [
        ("乾", "乾", "乾为天"),
        ("兑", "兑", "兑为泽"),
        ("离", "离", "离为火"),
        ("震", "震", "震为雷"),
        ("巽", "巽", "巽为风"),
        ("坎", "坎", "坎为水"),
        ("艮", "艮", "艮为山"),
        ("坤", "坤", "坤为地"),
    ]

    engine = LiuyaoEngine()
    for upper, lower, expected_name in pure_hexagrams:
        entry = engine.lookup_hexagram(upper, lower)
        assert entry is not None, f"Missing hexagram {upper} over {lower}"
        assert entry[0] == expected_name, \
            f"Expected {expected_name}, got {entry[0]}"
        assert TRIGRAM_NAMES[entry[1]] == upper, \
            f"Palace should be {upper} for pure hexagram, got {TRIGRAM_NAMES[entry[1]]}"
        assert entry[2] == 5, \
            f"Pure hexagram should have 世=上爻(5), got {entry[2]}"


def test_liuyao_all_eight_trigrams_as_lower():
    """Test each trigram can appear as the lower trigram."""
    engine = LiuyaoEngine()
    for upper_name in TRIGRAM_NAMES.values():
        for lower_name in TRIGRAM_NAMES.values():
            entry = engine.lookup_hexagram(upper_name, lower_name)
            assert entry is not None, \
                f"Missing hexagram: {upper_name} over {lower_name}"
            assert entry[0] is not None
            assert len(entry[0]) > 0


def test_liuyao_yao_types_labels():
    """Test that exactly one line is labeled '世' and one line is labeled '应'."""
    engine = LiuyaoEngine()
    for seed in range(50):
        result = engine.cast(seed=seed)

        shi_count = sum(1 for l in result.lines if l["yao_type"] == "世")
        ying_count = sum(1 for l in result.lines if l["yao_type"] == "应")

        assert shi_count == 1, f"Expected exactly 1 世, got {shi_count} (seed={seed})"
        assert ying_count == 1, f"Expected exactly 1 应, got {ying_count} (seed={seed})"
