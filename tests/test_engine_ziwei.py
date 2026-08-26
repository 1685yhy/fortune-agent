# tests/test_engine_ziwei.py
"""紫微规则库测试：纯查表函数手算断言 + 考卷全过（ZiweiEngine 真实输出交叉验证）。"""
import os

import pytest

from src.engine.case_loader import load_cases
from src.engine.rules.ziwei import (
    analyze,
    evaluate,
    main_stars_of,
    ming_gong_gan,
    ming_gong_of,
    palace_order,
    shen_gong_of,
    sihua_of,
    wuxing_ju_by_nayin,
    wuxing_ju_of,
)
from src.engines.ziwei import ZiweiEngine

CASE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "engine", "cases", "ziwei_cases.jsonl")


def test_sihua_hand():
    # 甲廉破武阳：甲年 禄=廉贞 权=破军 科=武曲 忌=太阳
    assert sihua_of("甲") == {"禄": "廉贞", "权": "破军", "科": "武曲", "忌": "太阳"}
    # 庚阳武阴同
    assert sihua_of("庚") == {"禄": "太阳", "权": "武曲", "科": "太阴", "忌": "天同"}
    # 癸破巨阴贪
    assert sihua_of("癸") == {"禄": "破军", "权": "巨门", "科": "太阴", "忌": "贪狼"}
    # 戊贪阴弼机
    assert sihua_of("戊")["禄"] == "贪狼"
    assert sihua_of("戊")["科"] == "右弼"
    # 十干四化忌皆非空（表完整）
    for gan in "甲乙丙丁戊己庚辛壬癸":
        assert sihua_of(gan)["忌"], gan
    # 非法年干 → 空表
    assert sihua_of("天") == {}


def test_wuxing_ju_by_nayin_hand():
    assert wuxing_ju_by_nayin("甲子") == "金四局"  # 海中金
    assert wuxing_ju_by_nayin("丙寅") == "火六局"  # 炉中火
    assert wuxing_ju_by_nayin("戊辰") == "木三局"  # 大林木
    assert wuxing_ju_by_nayin("乙酉") == "水二局"  # 泉中水
    assert wuxing_ju_by_nayin("庚午") == "土五局"  # 路旁土
    with pytest.raises(ValueError):
        wuxing_ju_by_nayin("甲丑")  # 非法干支


def test_wuxing_ju_of_hand():
    # 甲子年正月午时：命宫申，壬申剑锋金 → 金四局
    assert wuxing_ju_of("甲", 1, "午") == "金四局"
    # 庚午年四月申时：命宫酉，乙酉泉中水 → 水二局
    assert wuxing_ju_of("庚", 4, "申") == "水二局"
    # 癸酉年六月巳时：命宫寅，甲寅大溪水 → 水二局
    assert wuxing_ju_of("癸", 6, "巳") == "水二局"
    # 己巳年十月酉时：命宫寅，丙寅炉中火 → 火六局
    assert wuxing_ju_of("己", 10, "酉") == "火六局"
    # 乙亥年二月丑时：命宫寅，戊寅城头土 → 土五局
    assert wuxing_ju_of("乙", 2, "丑") == "土五局"


def test_ming_shen_gong_hand():
    assert ming_gong_of(4, "申") == "酉"  # 寅起正月顺至巳，逆数 8 位至酉
    assert shen_gong_of(4, "申") == "丑"  # 同起法，顺数 8 位至丑
    assert ming_gong_of(1, "午") == "申"  # 寅 - 6 → 申
    assert ming_gong_gan("甲", "申") == "壬"  # 甲年丙寅起顺数 6 位
    assert ming_gong_gan("庚", "酉") == "乙"  # 庚年戊寅起顺数 7 位


def test_palace_order_hand():
    order = palace_order("酉")
    assert order["命宫"] == "酉"
    assert order["兄弟"] == "申"
    assert order["夫妻"] == "未"
    assert order["官禄"] == "丑"
    assert order["田宅"] == "子"
    assert order["父母"] == "戌"
    # 命宫在寅：官禄在午、父母在卯
    order2 = palace_order("寅")
    assert order2["官禄"] == "午"
    assert order2["父母"] == "卯"
    with pytest.raises(ValueError):
        palace_order("甲")


def test_main_stars_hand():
    # 紫微在寅：天府在辰（寅申线对称），破军同宫于寅
    assert main_stars_of("寅") == {
        "紫微": "寅", "天机": "丑", "太阳": "亥", "武曲": "戌", "天同": "酉", "廉贞": "午",
        "天府": "辰", "太阴": "巳", "贪狼": "午", "巨门": "未", "天相": "申", "天梁": "酉",
        "七杀": "戌", "破军": "寅",
    }
    # 紫微在卯：天府在卯，破军丑
    assert main_stars_of("卯") == {
        "紫微": "卯", "天机": "寅", "太阳": "子", "武曲": "亥", "天同": "戌", "廉贞": "未",
        "天府": "卯", "太阴": "辰", "贪狼": "巳", "巨门": "午", "天相": "未", "天梁": "申",
        "七杀": "酉", "破军": "丑",
    }
    # 紫微在巳：天府在丑，破军亥
    assert main_stars_of("巳")["天府"] == "丑"
    assert main_stars_of("巳")["破军"] == "亥"
    with pytest.raises(ValueError):
        main_stars_of("甲")


def test_analyze_evaluate_cross_validate_engine():
    """规则库 evaluate/analyze 与 ZiweiEngine 真实输出交叉验证。"""
    engine = ZiweiEngine()
    for birth in [
        (1990, 5, 20, 15, 0, "北京", "男"),
        (1984, 2, 2, 12, 0, "北京", "男"),
        (2008, 8, 8, 20, 0, "北京", "女"),
        (1989, 11, 12, 18, 0, "北京", "女"),
    ]:
        r = engine.calculate(*birth)
        ev = evaluate(r)
        assert ev["五行局"] == r.wuxing_ju
        assert ev["四化禄"] == r.sihua["化禄"]
        assert ev["四化忌"] == r.sihua["化忌"]
        assert ev["命宫"] == r.ming_gong
        assert ev["身宫"] == r.shen_gong
        assert ev["紫微"] == r.main_stars["紫微"]
        assert ev["破军"] == r.main_stars["破军"]
        assert ev["父母"] == r.palaces["父母"].dizhi
        # analyze 要点非空且首条为五行局
        pts = analyze(r)
        assert len(pts) >= 6
        assert pts[0] == f"五行局：{r.wuxing_ju}"
        assert "化禄" in pts[2]


def test_cases_all_pass():
    """考卷全过：每条 case 用 ZiweiEngine 真实输出排盘 → evaluate → 逐键断言。"""
    cases = load_cases(CASE_PATH)
    unit = [c for c in cases if c.quality == "unit"]
    assert len(unit) >= 8, f"考卷不足 8 条: {len(unit)}"
    # 结构：四化 3 / 五行局 2 / 主星分布 2 / 宫序 1
    assert sum(1 for c in unit if "四化禄" in c.expected) >= 3
    assert sum(1 for c in unit if "五行局" in c.expected) >= 2
    assert sum(1 for c in unit if "紫微" in c.expected) >= 2
    assert sum(1 for c in unit if "兄弟" in c.expected) >= 1

    engine = ZiweiEngine()
    checked = 0
    for case in unit:
        assert "birth" in case.expected, f"{case.id} 缺 birth 输入"
        result = engine.calculate(**case.expected["birth"])
        actual = evaluate(result)
        for key, want in case.expected.items():
            if key == "birth" or want in ("", None, []):
                continue
            assert actual.get(key) == want, (
                f"{case.id} {key}: 期望 {want} 实际 {actual.get(key)}")
            checked += 1
    assert checked >= 20, f"断言键过少: {checked}"
