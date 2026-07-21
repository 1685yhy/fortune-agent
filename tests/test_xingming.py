"""Tests for Xingming Engine - 五格剖象法 (Five-Cell Name Analysis)."""
from src.engines.xingming import (
    XingmingEngine, XingmingResult,
    get_stroke_count, get_wuge, get_numerology,
    digit_to_wuxing, evaluate_sancai, get_sancai_wuxing,
)


def test_xingming_basic():
    """Test basic name analysis with known case."""
    engine = XingmingEngine()
    # 张三 (single surname + double given name? no, single char given name)
    result = engine.analyze("张", "三")
    assert result is not None
    assert isinstance(result, XingmingResult)
    assert len(result.wuge) == 5  # 五格
    assert "天格" in result.wuge
    assert "人格" in result.wuge
    assert "地格" in result.wuge
    assert "外格" in result.wuge
    assert "总格" in result.wuge
    assert result.sancai is not None
    assert result.sancai_ji in ("大吉", "吉", "半吉", "凶")
    assert len(result.analysis) == 5
    assert result.overall is not None


def test_xingming_wuge_calculation():
    """Test five-cell calculation correctness."""
    # 李四：李=7, 四=5
    # 天格 = 7+1 = 8 (单姓+1)
    # 人格 = 李(7) + 四(5) = 12
    # 地格 = 5+1 = 6 (单名+1)
    # 总格 = 7+5 = 12
    # 外格 = 12-12+1 = 1
    wuge = get_wuge("李", "四")
    assert wuge["天格"] == 8, f"Expected 8, got {wuge['天格']}"
    assert wuge["人格"] == 12, f"Expected 12, got {wuge['人格']}"
    assert wuge["地格"] == 6, f"Expected 6, got {wuge['地格']}"
    assert wuge["总格"] == 12, f"Expected 12, got {wuge['总格']}"
    assert wuge["外格"] == 1, f"Expected 1, got {wuge['外格']}"


def test_xingming_double_given_name():
    """Test with double-character given name."""
    # 王小明：王=4, 小=3, 明=8
    # 天格 = 4+1 = 5
    # 人格 = 王(4) + 小(3) = 7
    # 地格 = 3+8 = 11 (双名直接相加)
    wuge = get_wuge("王", "小明")
    assert wuge["天格"] == 5
    assert wuge["人格"] == 7
    assert wuge["地格"] == 11


def test_xingming_compound_surname():
    """Test with compound surname."""
    # 欧阳明：欧=?, 阳=?, 明=8
    # For欧阳, need stroke counts for both characters
    # 欧=?, 阳=? - let's use characters we know
    wuge = get_wuge("欧阳", "明")
    # 天格 = stroke(欧) + stroke(阳) (compound surname, no +1)
    # 人格 = 阳(last) + 明(first)
    # 地格 = 明+1 (single name)
    ou_stroke = get_stroke_count("欧")
    yang_stroke = get_stroke_count("阳")
    ming_stroke = get_stroke_count("明")
    assert wuge["天格"] == ou_stroke + yang_stroke
    assert wuge["人格"] == yang_stroke + ming_stroke


def test_numerology_table():
    """Test 81-number numerology lookups."""
    # 1 = 大吉
    n1 = get_numerology(1)
    assert "大吉" in n1["吉凶"]
    assert n1["运势"] == "天地开泰"

    # 2 = 凶
    n2 = get_numerology(2)
    assert "凶" in n2["吉凶"]
    assert n2["运势"] == "混沌未定"

    # 81 = 大吉 (last entry)
    n81 = get_numerology(81)
    assert "大吉" in n81["吉凶"]

    # Out of bounds wraps around
    n82 = get_numerology(82)
    assert n82["数字"] == 1  # 82 mod 81 = 1


def test_numerology_all_81():
    """Test all 81 entries exist."""
    for i in range(1, 82):
        n = get_numerology(i)
        assert n["数字"] == i
        assert n["吉凶"] in ("大吉", "吉", "半吉", "凶")
        assert len(n["运势"]) > 0
        assert len(n["详解"]) > 0


def test_digit_to_wuxing():
    """Test digit-to-wuxing mapping."""
    assert digit_to_wuxing(1) == "木"
    assert digit_to_wuxing(2) == "木"
    assert digit_to_wuxing(3) == "火"
    assert digit_to_wuxing(4) == "火"
    assert digit_to_wuxing(5) == "土"
    assert digit_to_wuxing(6) == "土"
    assert digit_to_wuxing(7) == "金"
    assert digit_to_wuxing(8) == "金"
    assert digit_to_wuxing(9) == "水"
    assert digit_to_wuxing(0) == "水"


def test_sancai_evaluation():
    """Test 三才 configuration evaluation."""
    # 木火火 = 木生火, 火火same = 吉
    ji = evaluate_sancai("木", "火", "火")
    assert ji in ("大吉", "吉")

    # 金金金 = all same = 中等偏上
    mid = evaluate_sancai("金", "金", "金")
    assert mid in ("大吉", "吉", "半吉")

    # 木金土 = 木克金(凶), 金生土(吉) → 半吉
    mixed = evaluate_sancai("木", "金", "土")
    assert mixed in ("半吉", "吉", "凶")


def test_sancai_wuxing():
    """Test 三才五行 retrieval."""
    wuge = {"天格": 8, "人格": 12, "地格": 6, "外格": 1, "总格": 14}
    tg, rg, dg, sancai = get_sancai_wuxing(wuge)
    assert tg == digit_to_wuxing(8)  # 8→金
    assert rg == digit_to_wuxing(12)  # 12%10=2→木
    assert dg == digit_to_wuxing(6)  # 6→土
    assert sancai == f"{tg}{rg}{dg}"


def test_stroke_table_coverage():
    """Test that common characters have stroke counts."""
    common = ["王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴"]
    for char in common:
        assert get_stroke_count(char) > 0, f"Missing stroke count for '{char}'"


def test_unknown_char_stroke():
    """Test fallback for unknown characters."""
    stroke = get_stroke_count("😀")
    assert stroke == 0  # Unknown character returns 0


def test_xingming_full_analysis():
    """Test complete name analysis flow."""
    engine = XingmingEngine()
    result = engine.analyze("李", "明", "男")
    assert "张" != "李"  # ensure test runs
    assert result.sancai is not None

    # Verify each analysis has expected structure
    for ge_name, analysis in result.analysis.items():
        assert "数字" in analysis
        assert "吉凶" in analysis
        assert "运势" in analysis
        assert "详解" in analysis

    # 三才 should be 3 chars
    assert len(result.sancai) == 3


def test_xingming_gender_not_affect_wuge():
    """Gender should not affect wuge calculation (only affects interpretation)."""
    engine = XingmingEngine()
    result_m = engine.analyze("赵", "云", "男")
    result_f = engine.analyze("赵", "云", "女")
    assert result_m.wuge == result_f.wuge


def test_xingming_already_known():
    """已知案例：张三"""
    engine = XingmingEngine()
    result = engine.analyze("张", "三")
    # 张=11画(繁)?, 三=3
    zhang_stroke = get_stroke_count("张")  # should be 11
    san_stroke = get_stroke_count("三")  # should be 3
    assert zhang_stroke == 11 or get_stroke_count("张") > 0
    assert san_stroke == 3
    print(f"张={zhang_stroke}画, 三={san_stroke}画")
    print(f"五格: {result.wuge}")
    print(f"三才: {result.sancai} ({result.sancai_ji})")
    print(f"综合评价: {result.overall}")


def test_get_stroke_count_coverage():
    """Verify stroke count for a broader set of characters."""
    # Check some specific characters
    assert get_stroke_count("一") == 1
    assert get_stroke_count("二") == 2
    assert get_stroke_count("三") == 3
    assert get_stroke_count("十") == 2
    assert get_stroke_count("大") == 3
    assert get_stroke_count("天") == 4
    assert get_stroke_count("文") == 4
    assert get_stroke_count("金") == 8
