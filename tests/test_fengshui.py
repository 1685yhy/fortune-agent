"""Tests for Fengshui Engine - 玄空飞星 + 八宅派."""
from src.engines.fengshui import FengshuiEngine, FengshuiResult


def test_fengshui_basic():
    """基础测试: 坐北朝南, 默认八运"""
    engine = FengshuiEngine()
    result = engine.analyze(direction="子", year_built=2024)
    assert isinstance(result, FengshuiResult)
    assert result.period == 9  # 2024-2043 = 九运
    assert result.house_gua == "坎宅"
    assert len(result.flying_stars) == 9  # 九宫
    # 检查九宫都有运星/山星/向星
    for palace, stars in result.flying_stars.items():
        assert "运星" in stars
        assert "山星" in stars
        assert "向星" in stars
        assert 1 <= stars["运星"] <= 9
        assert 1 <= stars["山星"] <= 9
        assert 1 <= stars["向星"] <= 9


def test_fengshui_period():
    """测试各个时期的元运推算"""
    engine = FengshuiEngine()
    assert engine._get_period(1864) == 1
    assert engine._get_period(1900) == 2
    assert engine._get_period(1944) == 5
    assert engine._get_period(1985) == 7
    assert engine._get_period(2023) == 8
    assert engine._get_period(2024) == 9
    assert engine._get_period(2043) == 9


def test_fengshui_flying_stars_chart():
    """测试运盘生成: 九运(9)入中顺飞"""
    engine = FengshuiEngine()
    chart = engine._build_period_chart(9)
    assert chart["中"] == 9
    assert chart["乾"] == 1  # 9+1=10→1
    assert chart["兑"] == 2
    assert chart["艮"] == 3
    assert chart["离"] == 4
    assert chart["坎"] == 5
    assert chart["坤"] == 6
    assert chart["震"] == 7
    assert chart["巽"] == 8

    # 一运: 1入中
    chart1 = engine._build_period_chart(1)
    assert chart1["中"] == 1
    assert chart1["乾"] == 2


def test_fengshui_eight_mansions():
    """测试八宅派吉凶方位"""
    engine = FengshuiEngine()
    # 坎宅 (坐北)
    result = engine.analyze(direction="子")
    eight = result.eight_mansions

    # 坎宅: 坎五天生延绝祸六
    # 坎=N=伏位, 艮=NE=五鬼, 震=E=天医, 巽=SE=生气
    # 离=S=延年, 坤=SW=绝命, 兑=W=祸害, 乾=NW=六煞
    assert eight.get("伏位") == "北"
    assert eight.get("五鬼") == "东北"
    assert eight.get("天医") == "东"
    assert eight.get("生气") == "东南"
    assert eight.get("延年") == "南"
    assert eight.get("绝命") == "西南"
    assert eight.get("祸害") == "西"
    assert eight.get("六煞") == "西北"


def test_fengshui_eight_mansions_qian():
    """测试乾宅 (西北)"""
    engine = FengshuiEngine()
    result = engine.analyze(direction="乾")
    eight = result.eight_mansions
    assert result.house_gua == "乾宅"
    # 乾: 乾六天五祸绝延生
    # 乾=NW=伏位, 坎=N=六煞, 艮=NE=天医, 震=E=五鬼
    # 巽=SE=祸害, 离=S=绝命, 坤=SW=延年, 兑=W=生气
    assert eight.get("伏位") == "西北"
    assert eight.get("六煞") == "北"
    assert eight.get("天医") == "东北"
    assert eight.get("五鬼") == "东"
    assert eight.get("祸害") == "东南"
    assert eight.get("绝命") == "南"
    assert eight.get("延年") == "西南"
    assert eight.get("生气") == "西"


def test_person_gua_male():
    """测试男性命卦"""
    engine = FengshuiEngine()
    # 1990年男: 1+9+9+0=19, 1+9=10, 1+0=1, 11-1=10, 10%9=1→坎
    assert engine._calculate_person_gua(1990, "男") == "坎"
    # 2000年男: 2+0+0+0=2, 9-2=7→兑
    assert engine._calculate_person_gua(2000, "男") == "兑"
    # 1985年男: 1+9+8+5=23, 2+3=5, 11-5=6→乾
    assert engine._calculate_person_gua(1985, "男") == "乾"


def test_person_gua_female():
    """测试女性命卦"""
    engine = FengshuiEngine()
    # 1990年女: 1+9+9+0=19, 1+9=10, 1+0=1, 4+1=5→艮
    assert engine._calculate_person_gua(1990, "女") == "艮"
    # 2000年女: 2+0+0+0=2, 6+2=8→艮
    assert engine._calculate_person_gua(2000, "女") == "艮"
    # 1985年女: 1+9+8+5=23, 2+3=5, 4+5=9→离
    assert engine._calculate_person_gua(1985, "女") == "离"


def test_person_gua_with_analyze():
    """测试命卦集成到analyze"""
    engine = FengshuiEngine()
    result = engine.analyze(direction="子", birth_year=1990, gender="男")
    assert result.person_gua == "坎"


def test_mountain_resolution():
    """测试方向解析"""
    engine = FengshuiEngine()
    # 24山名
    assert engine._resolve_mountain("子") == "子"
    assert engine._resolve_mountain("午") == "午"
    assert engine._resolve_mountain("乾") == "乾"
    # 八卦方向
    assert engine._resolve_mountain("坎") == "子"  # 坎→子山
    assert engine._resolve_mountain("离") == "午"  # 离→午山
    # 中文方向
    assert engine._resolve_mountain("北") == "子"
    assert engine._resolve_mountain("南") == "午"
    assert engine._resolve_mountain("东") == "卯"
    assert engine._resolve_mountain("西") == "酉"


def test_flying_star_complete():
    """完整测试: 玄空飞星九宫生成"""
    engine = FengshuiEngine()
    result = engine.analyze(direction="子", year_built=2024)
    fs = result.flying_stars

    # 运盘: 九运(9)入中顺飞
    # 中=9, 乾=1, 兑=2, 艮=3, 离=4, 坎=5, 坤=6, 震=7, 巽=8
    assert fs["中"]["运星"] == 9
    assert fs["乾"]["运星"] == 1
    assert fs["兑"]["运星"] == 2
    assert fs["震"]["运星"] == 7
    assert fs["巽"]["运星"] == 8

    # 山星: 坐山=子(坎), 运盘坎=5→入中
    # 子为阴→逆飞: 中=5, 巽=4, 震=3, 坤=2, 坎=1, 离=9, 艮=8, 兑=7, 乾=6
    assert fs["中"]["山星"] == 5
    assert fs["巽"]["山星"] == 4
    assert fs["震"]["山星"] == 3
    assert fs["坤"]["山星"] == 2
    assert fs["坎"]["山星"] == 1

    # 向星: 向山=午(离), 运盘离=4→入中
    # 午为阴→逆飞: 中=4, 巽=3, 震=2, 坤=1, 坎=9, 离=8, 艮=7, 兑=6, 乾=5
    assert fs["中"]["向星"] == 4
    assert fs["巽"]["向星"] == 3
    assert fs["震"]["向星"] == 2


def test_flying_star_yang_mountain():
    """测试阳山顺飞"""
    engine = FengshuiEngine()
    # 坐乾山(阳), 九运
    result = engine.analyze(direction="乾", year_built=2024)
    fs = result.flying_stars

    # 运盘: 中=9, 乾=1, ...
    # 山星: 坐山=乾(阳), 运盘乾=1→入中, 阳→顺飞
    # 中=1, 乾=2, 兑=3, 艮=4, 离=5, 坎=6, 坤=7, 震=8, 巽=9
    assert fs["中"]["山星"] == 1
    assert fs["乾"]["山星"] == 2
    assert fs["兑"]["山星"] == 3
    assert fs["巽"]["山星"] == 9


def test_eight_mansions_all_trigrams():
    """测试全部八卦的八宅吉凶"""
    engine = FengshuiEngine()

    # 坎宅
    r = engine.analyze(direction="子")
    assert r.house_gua == "坎宅"

    # 乾宅
    r = engine.analyze(direction="乾")
    assert r.house_gua == "乾宅"

    # 坤宅
    r = engine.analyze(direction="坤")
    assert r.house_gua == "坤宅"

    # 震宅
    r = engine.analyze(direction="卯")
    assert r.house_gua == "震宅"

    # 巽宅
    r = engine.analyze(direction="巽")
    assert r.house_gua == "巽宅"

    # 离宅
    r = engine.analyze(direction="午")
    assert r.house_gua == "离宅"

    # 艮宅
    r = engine.analyze(direction="艮")
    assert r.house_gua == "艮宅"

    # 兑宅
    r = engine.analyze(direction="酉")
    assert r.house_gua == "兑宅"


def test_known_case():
    """已知案例: 坐北朝南(子山午向) 九运"""
    engine = FengshuiEngine()
    result = engine.analyze(direction="子", year_built=2024, birth_year=1990, gender="男")
    print(f"\n=== 风水分析结果 ===")
    print(f"宅卦: {result.house_gua}")
    print(f"当前运: {result.period}运")
    print(f"命卦: {result.person_gua}")
    print(f"\n八宅吉凶:")
    for energy in ["生气", "天医", "延年", "伏位", "绝命", "五鬼", "六煞", "祸害"]:
        print(f"  {energy}: {result.eight_mansions.get(energy, '-')}")
    print(f"\n玄空飞星(九宫):")
    # 上排: 巽 离 坤
    for palace in ["巽", "离", "坤"]:
        s = result.flying_stars[palace]
        print(f"  {palace}: 运星{s['运星']} 山星{s['山星']} 向星{s['向星']}", end="")
    print()
    # 中排: 震 中 兑
    for palace in ["震", "中", "兑"]:
        s = result.flying_stars[palace]
        print(f"  {palace}: 运星{s['运星']} 山星{s['山星']} 向星{s['向星']}", end="")
    print()
    # 下排: 艮 坎 乾
    for palace in ["艮", "坎", "乾"]:
        s = result.flying_stars[palace]
        print(f"  {palace}: 运星{s['运星']} 山星{s['山星']} 向星{s['向星']}", end="")
    print()

    # 验证基本约束
    assert len(result.flying_stars) == 9
    assert result.period == 9
    assert result.person_gua == "坎"


def test_period_out_of_range():
    """测试超出范围的年份"""
    engine = FengshuiEngine()
    # 1900年以前
    p = engine._get_period(1800)
    assert 1 <= p <= 9
    # 2050年以后
    p = engine._get_period(2050)
    assert 1 <= p <= 9
    # 2100年
    p = engine._get_period(2100)
    assert 1 <= p <= 9
