# tests/test_engine_liuyao.py
"""六爻规则库测试：六亲/世应/纳甲/动变（阶段3 Task 4，TDD）。

口径：
- 六亲：以日干五行为"我"（生我父母/克我官鬼/我克妻财/同我兄弟/我生子孙）
- 世应：八宫六十四卦表（本宫世六爻、一~五世、游魂四、归魂三；应爻隔三位）
- 纳甲：火珠林纳甲（乾金甲子外壬午……）
- 动变：老阴/老阳动，阴阳互变
现有 LiuyaoEngine（src/engines/liuyao.py）只读复用做全量交叉验证。
"""

from src.engine.case_loader import load_cases
from src.engine.rules.liuyao import (
    liuqin_of,
    shiying_positions,
    najia_dizhi,
    najia_ganzhi,
    bian_hexagram,
    analyze,
    evaluate,
)
from src.engines import liuyao as liuyao_engine
from src.engines.liuyao import LiuyaoEngine


# ---------- 六亲（以日干五行为我） ----------

def test_liuqin_of_all_five_relations():
    # 日干甲（木）为"我"：
    assert liuqin_of("甲", "子") == "父母"  # 子水：水生木 → 生我者父母
    assert liuqin_of("甲", "申") == "官鬼"  # 申金：金克木 → 克我者官鬼
    assert liuqin_of("甲", "辰") == "妻财"  # 辰土：木克土 → 我克者妻财
    assert liuqin_of("甲", "寅") == "兄弟"  # 寅木：同我
    assert liuqin_of("甲", "午") == "子孙"  # 午火：木生火 → 我生者子孙

def test_liuqin_of_accepts_wuxing_or_gan():
    # 直接给五行字
    assert liuqin_of("甲", "金") == "官鬼"
    # 给天干（庚金）
    assert liuqin_of("甲", "庚") == "官鬼"
    # 丙（火）日：丑土 火生土 → 子孙
    assert liuqin_of("丙", "丑") == "子孙"

def test_liuqin_of_invalid():
    import pytest
    with pytest.raises(ValueError):
        liuqin_of("甲", "X")


# ---------- 世应（八宫卦表） ----------

def test_shiying_ba_chun_liu_shi():
    # 八纯卦：世在第六爻（上爻），应隔三位在三爻
    assert shiying_positions("乾为天") == (5, 2)
    assert shiying_positions("坤为地") == (5, 2)

def test_shiying_gong_order():
    # 乾宫八宫卦序：本宫六世 → 一~五世 → 游魂四世 → 归魂三世
    assert shiying_positions("天风姤") == (0, 3)    # 一世卦 世在初爻
    assert shiying_positions("天山遁") == (1, 4)    # 二世卦 世在二爻
    assert shiying_positions("天地否") == (2, 5)    # 三世卦 世在三爻
    assert shiying_positions("风地观") == (3, 0)    # 四世卦 世在四爻
    assert shiying_positions("山地剥") == (4, 1)    # 五世卦 世在五爻
    assert shiying_positions("火地晋") == (3, 0)    # 游魂卦 世在四爻
    assert shiying_positions("火天大有") == (2, 5)  # 归魂卦 世在三爻
    # 雷火丰 = 坎宫五世卦（引擎 seed=42 实卦）
    assert shiying_positions("雷火丰") == (4, 1)

def test_shiying_cross_validate_all_64():
    # 全 64 卦与引擎八宫卦表逐卦交叉验证
    for value, (name, palace, shi) in liuyao_engine.HEXAGRAM_TABLE.items():
        assert shiying_positions(name) == (shi, (shi + 3) % 6), name


# ---------- 纳甲 ----------

def test_najia_standard_table():
    assert najia_dizhi("乾", "乾") == ["子", "寅", "辰", "午", "申", "戌"]  # 乾金甲子外壬午
    assert najia_dizhi("坎", "坎") == ["寅", "辰", "午", "申", "戌", "子"]  # 坎水戊寅外戊申
    assert najia_dizhi("艮", "艮") == ["辰", "午", "申", "戌", "子", "寅"]  # 艮土丙辰外丙戌
    assert najia_dizhi("震", "震") == ["子", "寅", "辰", "午", "申", "戌"]  # 震木庚子外庚午
    assert najia_dizhi("巽", "巽") == ["丑", "亥", "酉", "未", "巳", "卯"]  # 巽木辛丑外辛未（阴卦逆行）
    assert najia_dizhi("离", "离") == ["卯", "丑", "亥", "酉", "未", "巳"]  # 离火己卯外己酉
    assert najia_dizhi("坤", "坤") == ["未", "巳", "卯", "丑", "亥", "酉"]  # 坤土乙未外癸丑
    assert najia_dizhi("兑", "兑") == ["巳", "卯", "丑", "亥", "酉", "未"]  # 兑金丁巳外丁亥

def test_najia_mixed_trigrams():
    # 雷火丰 = 震上离下：内卦离（卯丑亥）、外卦震（午申戌）
    assert najia_dizhi("震", "离") == ["卯", "丑", "亥", "午", "申", "戌"]

def test_najia_cross_validate_engine():
    # 与引擎 get_line_dizhi 全 8×8 卦交叉验证
    engine = LiuyaoEngine()
    trigrams = liuyao_engine.TRIGRAM_NAMES.values()
    for u in trigrams:
        for l in trigrams:
            assert najia_dizhi(u, l) == engine.get_line_dizhi(u, l), f"{u}{l}"

def test_najia_ganzhi():
    # 纳甲干支合并（口诀含天干：乾金甲子外壬午 / 坤土乙未外癸丑）
    assert najia_ganzhi("乾", "乾") == ["甲子", "甲寅", "甲辰", "壬午", "壬申", "壬戌"]
    assert najia_ganzhi("坤", "坤") == ["乙未", "乙巳", "乙卯", "癸丑", "癸亥", "癸酉"]
    assert najia_ganzhi("坎", "坎") == ["戊寅", "戊辰", "戊午", "戊申", "戊戌", "戊子"]


# ---------- 动变 ----------

def test_bian_hexagram_flip():
    # 雷火丰(37=0b100101) 二四五爻老阴动 → 阴阳互变 → 乾为天(63)
    assert bian_hexagram(37, [1, 3, 4]) == "乾为天"
    # 静卦：无动爻 → 变卦即本卦
    assert bian_hexagram(37, []) == "雷火丰"

def test_analyze_engine_result_cross_check():
    # 真实起卦（seed=42 固定可复现）与规则库交叉验证
    engine = LiuyaoEngine()
    r = engine.cast(method="random", question="测试", seed=42)
    assert r.original_hexagram == "雷火丰"
    assert r.changing_lines == [1, 3, 4]
    assert r.changed_hexagram == "乾为天"
    # 动变独立计算与引擎一致
    assert bian_hexagram(r.original_hexagram_value, r.changing_lines) == r.changed_hexagram
    # analyze：日干口径六亲（甲木日 世爻申金 克我→官鬼）
    points = analyze(r, day_gan="甲")
    assert "本卦：雷火丰" in points
    assert "世应：世在五爻应在二爻" in points
    assert "世爻六亲：申为官鬼（以日干甲木为我）" in points
    assert "动爻：3爻动（二爻、四爻、五爻）" in points
    assert "变卦：乾为天" in points
    # 未给日干：世爻六亲采排盘卦宫口径（坎水为体，申金生水→父母）并明示
    points2 = analyze(r)
    assert any("卦宫口径" in p for p in points2)
    assert "世应：世在五爻应在二爻" in points2

def test_evaluate_dict_chart():
    chart = {
        "original_hexagram": "雷火丰",
        "original_hexagram_value": 37,
        "shi_yao": 4,
        "ying_yao": 1,
        "changing_lines": [1, 3, 4],
        "lines": [
            {"dizhi": "卯", "type": "少阳"}, {"dizhi": "丑", "type": "老阴"},
            {"dizhi": "亥", "type": "少阳"}, {"dizhi": "午", "type": "老阴"},
            {"dizhi": "申", "type": "老阴"}, {"dizhi": "戌", "type": "少阳"},
        ],
    }
    out = evaluate(chart, day_gan="甲")
    assert out["本卦"] == "雷火丰"
    assert out["世应"] == "世在五爻应在二爻"
    assert out["纳甲"] == ["卯", "丑", "亥", "午", "申", "戌"]
    assert out["世爻六亲"] == "官鬼"
    assert out["动爻数"] == 3
    assert out["动爻"] == ["二爻", "四爻", "五爻"]
    assert out["变卦"] == "乾为天"
    assert isinstance(out["要点"], list) and len(out["要点"]) >= 5


# ---------- 考卷（liuyao_cases.jsonl） ----------

def test_liuyao_cases_file():
    cases = load_cases("src/engine/cases/liuyao_cases.jsonl")
    assert len(cases) >= 8
    assert all(c.quality == "unit" for c in cases)
    by_id = {c.id: c for c in cases}
    # 六亲 3 条
    assert liuqin_of("甲", "子") == by_id["ly_0001"].expected["liuqin_of"]
    assert liuqin_of("甲", "申") == by_id["ly_0002"].expected["liuqin_of"]
    assert liuqin_of("丙", "辰") == by_id["ly_0003"].expected["liuqin_of"]
    # 世应 2 条
    assert list(shiying_positions("乾为天")) == by_id["ly_0004"].expected["shiying_positions"]
    assert list(shiying_positions("天地否")) == by_id["ly_0005"].expected["shiying_positions"]
    # 纳甲 2 条
    assert najia_dizhi("乾", "乾") == by_id["ly_0006"].expected["najia_dizhi"]
    assert najia_dizhi("坎", "坎") == by_id["ly_0007"].expected["najia_dizhi"]
    # 动变 1 条
    assert bian_hexagram(37, [1, 3, 4]) == by_id["ly_0008"].expected["bian_hexagram"]
