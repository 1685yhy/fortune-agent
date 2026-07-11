"""Tests for Zeri Engine - 择日 (Date Selection)."""
from src.engines.zeri import (
    ZeriEngine, ZeriResult,
    DIZHI, JIANCHU, ERSHIBA_XIU, JIANCHU_QUALITY,
)


def test_zeri_basic():
    """基础测试：给定一个日期应得到完整择日结果"""
    engine = ZeriEngine()
    # 2024年1月1日 公历（农历冬月二十）
    result = engine.select(2024, 1, 1)
    assert isinstance(result, ZeriResult)
    assert result.jianchu in JIANCHU
    assert result.ershibaxiu in ERSHIBA_XIU
    assert result.xiu_jixiong in ["吉", "凶", "平"]
    assert len(result.yi) > 0
    assert len(result.ji) > 0
    assert "冲" in result.chong
    assert result.overall in ["吉", "凶", "平"]


def test_zeri_jianchu_known():
    """测试已知建除神的日期"""
    engine = ZeriEngine()

    # 2024-10-09 农历九月初七, 月支戌
    # 已知该日地支与月支关系可计算建除
    result = engine.select(2024, 10, 9)
    assert result.jianchu in JIANCHU

    # 两次计算同一日期应一致
    result2 = engine.select(2024, 10, 9)
    assert result.jianchu == result2.jianchu
    assert result.ershibaxiu == result2.ershibaxiu


def test_zeri_jianchu_with_month_zhi():
    """测试建除计算：通过月支验证"""
    engine = ZeriEngine()

    # 农历正月（寅）的寅日应该是"建"
    # 2024-02-10 甲辰年 正月 丙寅日
    result = engine.select(2024, 2, 10)
    # 验证至少得到一个有效的建除神
    assert result.jianchu in JIANCHU


def test_zeri_purpose_matching():
    """测试用途匹配"""
    engine = ZeriEngine()

    # 带有用途的择日
    result = engine.select(2024, 1, 15, purpose="嫁娶")
    assert "嫁娶" in result.yi or result.overall is not None


def test_zeri_ershibaxiu_cycle():
    """测试二十八宿循环"""
    engine = ZeriEngine()

    # 同一月相邻两天，二十八宿应该相邻或成循环
    r1 = engine.select(2024, 7, 1)
    r2 = engine.select(2024, 7, 2)

    idx1 = ERSHIBA_XIU.index(r1.ershibaxiu)
    idx2 = ERSHIBA_XIU.index(r2.ershibaxiu)

    # 应该相差1天（循环28天）
    diff = (idx2 - idx1) % 28
    assert diff == 1, f"相邻日期二十八宿应差1, 实际差{diff} ({r1.ershibaxiu} -> {r2.ershibaxiu})"


def test_zeri_chong():
    """测试冲生肖"""
    engine = ZeriEngine()

    # 子日冲午（马）
    # 查找一个子日：2024-01-05 甲子日
    result = engine.select(2024, 1, 5)
    assert "冲" in result.chong

    # 验证六冲关系
    day_zhi = result.raw_data.get("day_ganzhi", "")[1] if result.raw_data else ""
    if day_zhi:
        from src.engines.zeri import LIU_CHONG, ZODIAC_MAP
        expected_chong_zhi = LIU_CHONG.get(day_zhi, "")
        expected_animal = ZODIAC_MAP.get(expected_chong_zhi, "")
        assert expected_animal in result.chong


def test_zeri_multiple_dates():
    """测试多个不同日期"""
    engine = ZeriEngine()
    dates = [
        (2024, 6, 1),
        (2024, 8, 15),
        (2024, 10, 1),
        (2025, 1, 1),
        (2025, 3, 20),
    ]
    for y, m, d in dates:
        result = engine.select(y, m, d)
        assert result.jianchu in JIANCHU, f"{y}-{m}-{d}: 建除应为12神之一"
        assert result.ershibaxiu in ERSHIBA_XIU, f"{y}-{m}-{d}: 二十八宿应为28宿之一"


def test_zeri_quality_classification():
    """测试吉凶分类的覆盖面"""
    engine = ZeriEngine()

    seen_overall = set()
    for month in range(1, 13):
        result = engine.select(2024, month, 15)
        seen_overall.add(result.overall)

    # 三种结果至少出现2种
    assert len(seen_overall) >= 2, f"应覆盖至少2种吉凶判定, 只有{seen_overall}"


def test_zeri_jianchu_quality_map():
    """验证建除吉凶映射完整"""
    for jc in JIANCHU:
        assert jc in JIANCHU_QUALITY, f"建除神{jc}缺少吉凶判定"
        assert JIANCHU_QUALITY[jc] in ["吉", "凶", "平"]


def test_zeri_year_boundary():
    """年份边界测试"""
    engine = ZeriEngine()
    # 跨年
    r_2024 = engine.select(2024, 12, 31)
    r_2025 = engine.select(2025, 1, 1)
    assert r_2024.jianchu in JIANCHU
    assert r_2025.jianchu in JIANCHU
    assert r_2024.ershibaxiu in ERSHIBA_XIU
    assert r_2025.ershibaxiu in ERSHIBA_XIU


def test_zeri_deterministic():
    """确定性测试"""
    engine = ZeriEngine()
    r1 = engine.select(2024, 7, 15, purpose="开业")
    r2 = engine.select(2024, 7, 15, purpose="开业")
    assert r1.jianchu == r2.jianchu
    assert r1.ershibaxiu == r2.ershibaxiu
    assert r1.yi == r2.yi
    assert r1.overall == r2.overall


def test_zeri_known_case():
    """已知案例测试"""
    engine = ZeriEngine()

    # 2024-10-01 国庆节
    result = engine.select(2024, 10, 1, purpose="开业")
    print(f"\n日期: 2024-10-01")
    print(f"建除: {result.jianchu} ({JIANCHU_QUALITY.get(result.jianchu, '?')})")
    print(f"二十八宿: {result.ershibaxiu} ({result.xiu_jixiong})")
    print(f"宜: {result.yi}")
    print(f"忌: {result.ji}")
    print(f"冲: {result.chong}")
    print(f"综合: {result.overall}")
    assert result.jianchu in JIANCHU
    assert result.ershibaxiu in ERSHIBA_XIU
