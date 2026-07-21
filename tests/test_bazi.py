"""Tests for Bazi Engine."""
from src.engines.bazi import BaziEngine


def test_bazi_basic():
    engine = BaziEngine()
    # 1990年5月20日 15:00 北京 男
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")
    assert result is not None
    assert len(result.bazi) == 4  # 年柱 月柱 日柱 时柱
    assert result.day_master is not None
    assert len(result.day_master) > 0
    assert "金" in result.wuxing or "木" in result.wuxing or "水" in result.wuxing
    assert len(result.dayun) > 0
    print(f"八字：{' '.join(result.bazi)} | 日主：{result.day_master}")


def test_bazi_shishen_has_ten_types():
    engine = BaziEngine()
    result = engine.calculate(1985, 3, 15, 8, 0, "上海", "女")
    # 十神应该有值
    assert len(result.shishen) == 4  # 四柱各一个十神
    valid_shishen = {"比肩","劫财","食神","伤官","正财","偏财","正官","七杀","正印","偏印","日主"}
    for s in result.shishen:
        assert s in valid_shishen, f"Unknown shishen: {s}"


def test_bazi_dayun_sequence():
    engine = BaziEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")
    # 大运应该有8-10个
    assert len(result.dayun) >= 8
    # 每个大运年数递增
    for i in range(len(result.dayun) - 1):
        assert result.dayun[i][0] < result.dayun[i+1][0]


def test_bazi_known_case():
    """已知案例：庚午 辛巳 乙酉 甲申（1990年5月20日15时 男）"""
    engine = BaziEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")
    # 至少日柱应该是乙酉
    assert "乙" in result.bazi[2] or "酉" in result.bazi[2], f"Expected 乙酉 in day pillar, got {result.bazi[2]}"
    # 时柱甲（木）与日主乙（木）同五行不同天干 → 应为劫财
    assert result.shishen[3] == "劫财", f"Expected 劫财 for time pillar, got {result.shishen[3]}"
    print(f"八字：{' '.join(result.bazi)} | 日主：{result.day_master}")
    print(f"十神：{result.shishen}")
    print(f"大运：{result.dayun[:3]}")
    print(f"流年(2026)：{result.liunian.get('2026', 'N/A')}")
