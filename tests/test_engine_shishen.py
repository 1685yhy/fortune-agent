from src.engine.rules.shishen import shishen_of, detect_combos

def test_shishen_of_known():
    assert shishen_of("甲", "甲") == "比肩"
    assert shishen_of("甲", "乙") == "劫财"
    assert shishen_of("甲", "癸") == "正印"
    assert shishen_of("甲", "壬") == "偏印"
    assert shishen_of("甲", "丙") == "食神"
    assert shishen_of("甲", "丁") == "伤官"
    assert shishen_of("甲", "辛") == "正官"
    assert shishen_of("甲", "庚") == "七杀"
    assert shishen_of("甲", "己") == "正财"
    assert shishen_of("甲", "戊") == "偏财"

def test_detect_combos_known():
    # 伤官见官：日干甲，时干丁(伤官)，月干辛(正官)
    assert "伤官见官" in detect_combos(["庚午", "辛巳", "甲午", "丁卯"])
    # 官杀混杂：甲日 月干辛(正官) 时干庚(七杀)
    assert "官杀混杂" in detect_combos(["庚午", "辛巳", "甲午", "庚寅"])
    # 比劫夺财：甲日 年干甲 时干乙(劫财) 月干己(正财)
    assert "比劫夺财" in detect_combos(["甲子", "己巳", "甲午", "乙卯"])
    # 正常命例无命中（庚日：癸伤官/辛劫财/庚比肩/壬食神，无官杀无偏印）
    assert detect_combos(["癸丑", "辛巳", "庚申", "壬午"]) == []
