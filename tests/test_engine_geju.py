from src.engine.rules.geju import determine_geju

def test_geju_month_branch_primary():
    # 甲日 酉月：酉本气辛(正官) 不透 → 正官格
    assert determine_geju(["庚午", "乙酉", "甲午", "丁卯"]) == "正官格"
    # 庚日 未月：未本气己(正印，阴土生阳金) 不透 → 正印格
    assert determine_geju(["辛丑", "辛未", "庚辰", "甲申"]) == "正印格"
    # 丙日 巳月：巳本气丙(比肩) → 建禄格
    assert determine_geju(["丙申", "癸巳", "丙午", "甲午"]) == "建禄格"

def test_geju_tou_gan_priority():
    # 甲日 辰月：辰藏戊乙癸；戊(偏财)透年干 → 偏财格（透干优先于本气）
    assert determine_geju(["戊午", "丙辰", "甲午", "丁卯"]) == "偏财格"
    # 甲日 子月：子藏癸(正印)；癸透时干 → 正印格
    assert determine_geju(["庚午", "丙子", "甲午", "癸卯"]) == "正印格"
    # 滴天髓命例 辛未/乙未/庚辰/丁亥：未月中气丁(正官)透时干 → 正官格（透干优先）
    assert determine_geju(["辛未", "乙未", "庚辰", "丁亥"]) == "正官格"

def test_geju_invalid():
    import pytest
    with pytest.raises(ValueError):
        determine_geju(["甲子", "乙丑", "丙寅"])  # 非四柱
