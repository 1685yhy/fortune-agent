from src.engine.rules.shensha import shensha_of

def test_shensha_known():
    # 丙午日：羊刃在午(丙→午) 命中；禄神在巳(丙→巳, 辛巳) 命中；桃花在卯(日支午→寅午戌, 丁卯) 命中
    assert "羊刃" in shensha_of(["庚午", "辛巳", "丙午", "丁卯"])
    # 甲子年(申子辰→桃花酉)：四柱无酉 → 不命中
    assert "桃花" not in shensha_of(["甲子", "丙寅", "戊辰", "庚午"])
    # 申子辰见辰：辰为华盖
    assert "华盖" in shensha_of(["甲申", "丙子", "戊辰", "庚午"])
    # 甲日 文昌在巳：巳在月支
    assert "文昌" in shensha_of(["庚午", "辛巳", "甲午", "丁卯"])
    # 亥子丑三会：年支子 → 孤辰在寅，寅在月支 → 命中
    assert "孤辰" in shensha_of(["甲子", "丙寅", "戊午", "庚申"])
