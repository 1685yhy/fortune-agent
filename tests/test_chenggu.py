"""Tests for 称骨算命引擎（问真数据基准，L2-1）。

锚点案例全部由问真 chunk2 原始 hn 函数（node 直接执行压缩代码）跑出，
非人工推算；歌诀锚点与「网传袁天罡称骨歌」对照。
"""
import re
from pathlib import Path

import pytest

from src.engines.bazi import BaziEngine
from src.engines.chenggu import (
    DIZHI,
    TIANGAN,
    _load_tables,
    _month_index,
    _split_tips,
    _year_index,
    bone_weight_text,
    chenggu_bone,
    chenggu_detail,
    chenggu_verse,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "chenggu"


# ---------------------------------------------------------------- 锚点案例
# 由问真 v13.9 chunk2 hn() 原始代码跑出（月/日已换算为农历 1 基写法）：
ANCHOR_CASES = [
    # (年干支, 农历月, 农历日, 时支, 两, 钱, 文本, 算式)
    ("甲子", 1, 1, "子", 3, 9, "三两九钱", "1.2+0.6+0.5+1.6"),
    ("甲子", 5, 19, "子", 3, 8, "三两八钱", "1.2+0.5+0.5+1.6"),
    ("癸卯", 4, 19, "申", 3, 4, "三两四钱", "1.2+0.9+0.5+0.8"),
    ("戊午", 9, 26, "午", 6, 5, "六两五钱", "1.9+1.8+1.8+1.0"),
    ("辛酉", 12, 30, "亥", 3, 3, "三两三钱", "1.6+0.5+0.6+0.6"),
    ("甲戌", 10, 15, "酉", 4, 2, "四两二钱", "1.5+0.8+1.0+0.9"),
]


@pytest.mark.parametrize("gz,m,d,hz,liang,qian,text,expr", ANCHOR_CASES)
def test_chenggu_anchor_cases(gz, m, d, hz, liang, qian, text, expr):
    """问真 hn() 原始代码跑出的 6 组锚点案例。"""
    got_liang, got_qian, jieci = chenggu_bone(gz, m, d, hz)
    assert (got_liang, got_qian) == (liang, qian), f"{gz}年{m}月{d}日{hz}时 != {expr}"
    assert bone_weight_text(got_liang, got_qian) == text
    assert len(jieci) > 20


def test_chenggu_input_forms_equivalent():
    """时干支/时支/时支+时、月份整数/字符串写法应等价。"""
    base = chenggu_bone("庚午", 4, 26, "申")
    assert chenggu_bone("庚午", 4, 26, "申时") == base
    assert chenggu_bone("庚午", 4, 26, "甲申") == base
    assert chenggu_bone("庚午", "四月", 26, "申") == base
    assert chenggu_bone("庚午", "4月", 26, "申") == base
    assert _month_index("闰四月") == _month_index(4) == 3


def test_chenggu_leap_month_same_weight():
    """闰月与平月同重（问真 G.c 口径：去「闰」字后查表）。"""
    assert chenggu_bone("癸卯", "闰二月", 5, "辰") == chenggu_bone("癸卯", 2, 5, "辰")
    assert chenggu_bone("甲子", "闰五月", 10, "午") == chenggu_bone("甲子", 5, 10, "午")


# ---------------------------------------------------------------- 表完整性
def test_year_table_60_jiazi_complete():
    """年骨重表 60 项：60 甲子全覆盖、与标准袁天罡称骨歌年表一致。"""
    tables = _load_tables()
    fn = tables["fn"]
    assert len(fn) == 60
    # 60 甲子序：甲子=0 … 癸亥=59
    ganzhi_seq = [f"{TIANGAN[i % 10]}{DIZHI[i % 12]}" for i in range(60)]
    assert _year_index("甲子") == 0 and _year_index("癸亥") == 59
    # 年干支 → 序数 与 60 甲子序严格一一对应（每项恰好命中一次）
    hits = [_year_index(gz) for gz in ganzhi_seq]
    assert sorted(hits) == list(range(60))
    # 全部骨重在 0.5-1.9 两范围、0.1 两粒度
    assert all(0.5 <= v <= 1.9 and abs(v * 10 - round(v * 10)) < 1e-9 for v in fn)
    # 与网传袁天罡称骨歌公开年表抽查（问真 fn 与公开表逐项一致，已用 node 全表核对）
    known = {
        "甲子": 1.2, "乙丑": 0.9, "丙寅": 0.6, "丁卯": 0.7, "戊辰": 1.2,
        "己巳": 0.5, "庚午": 0.9, "辛未": 0.8, "壬申": 0.7, "癸酉": 0.8,
        "甲戌": 1.5, "丙子": 1.6, "己卯": 1.9, "戊午": 1.9, "癸亥": 0.7,
    }
    for gz, w in known.items():
        assert fn[_year_index(gz)] == w, f"{gz}年骨重应为 {w}"


def test_month_day_hour_tables_complete():
    """月 12 / 日 30 / 时 12 表完整性 + 公开表锚点。"""
    tables = _load_tables()
    assert len(tables["cn"]) == 12
    assert len(tables["ln"]) == 30
    assert len(tables["un"]) == 12
    # 公开表锚点：正月0.6 / 腊月0.5；初一0.5 / 三十0.6；子时1.6 / 亥时0.6
    assert tables["cn"][0] == 0.6 and tables["cn"][11] == 0.5
    assert tables["ln"][0] == 0.5 and tables["ln"][29] == 0.6
    assert tables["un"][0] == 1.6 and tables["un"][11] == 0.6
    # 所有合法月/日/时输入都能算（不抛异常）
    for m in range(1, 13):
        for d in (1, 15, 30):
            for hz in DIZHI:
                liang, qian, jieci = chenggu_bone("甲子", m, d, hz)
                assert 2 <= liang <= 7 and 0 <= qian <= 9


# ---------------------------------------------------------------- 歌诀完整性
# 2.1-7.1 共 51 个可能骨重值（最小 2两1钱、最大 7两1钱：2.1-2.9、3.0-6.9、7.0-7.1）
ALL_WEIGHTS = [
    (x, y) for x in range(2, 8) for y in range(10)
    if not ((x == 2 and y == 0) or (x == 7 and y > 1))
]


def test_verses_all_weights_covered():
    """2.1-7.1 全部 51 个可能骨重值男女歌诀齐备，且非空、可拆两句（问真 tips 口径）。"""
    tables = _load_tables()
    male, female = tables["verses"]["male"], tables["verses"]["female"]
    assert len(male) == 52      # 男 52 条（含 1 条不可达异文，问真原样）
    assert len(female) == 51    # 女 51 条
    for liang, qian in ALL_WEIGHTS:
        for gender in ("男", "女"):
            v = chenggu_verse(liang, qian, gender)
            assert len(v) >= 20, f"{liang}两{qian}钱 {gender}歌诀为空/过短"
            tips = _split_tips(v)
            assert len(tips) == 2 and all(tips), f"{liang}两{qian}钱 {gender} 拆句失败"
            assert len(re.findall(r"[一-龥]+[，？]", v)) >= 2


def test_verses_anchor_texts():
    """歌诀锚点：与网传袁天罡称骨歌一致（问真 _n 原文字符串）。"""
    assert chenggu_verse(2, 1, "男").startswith("短命非业谓大空，平生灾难事重重")
    assert chenggu_verse(2, 1, "女").startswith("生身此命运不通，乌云盖月黑朦胧")
    assert chenggu_verse(4, 4, "男").startswith("万事由天莫苦求，须知福禄命里收")
    assert chenggu_verse(4, 4, "女").startswith("夜梦金银醒来空，立志谋业运不通")
    assert chenggu_verse(7, 1, "男").startswith("此命生成大不同，公侯卿相在其中")
    assert chenggu_verse(7, 1, "女").startswith("此命推来宏运交，不须再愁苦劳难")


def test_verses_male_extra_variant_preserved():
    """男命第 52 条（7.1 异文）保留但不可达：最大骨重 7.1 只到索引 50。"""
    tables = _load_tables()
    assert tables["verses"]["male"][51].startswith("此格世界罕有生")
    assert chenggu_verse(7, 1, "男") == tables["verses"]["male"][50]


# ---------------------------------------------------------------- 集成：BaziResult.chenggu
def test_bazi_integration_1990_case():
    """1990-05-20 15:00 男：庚午/四月/廿六/申 → 四两四钱（问真锚点案例 庚午辛巳乙酉甲申）。"""
    engine = BaziEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "", "男")
    assert result.bazi[0] == "庚午"
    assert result.chenggu["weight_text"] == "四两四钱"
    assert (result.chenggu["liang"], result.chenggu["qian"]) == (4, 4)
    assert result.chenggu["jieci"] == chenggu_verse(4, 4, "男")
    assert result.chenggu["jieci"].startswith("万事由天莫苦求")


def test_bazi_integration_gender_verse():
    """同盘女命：骨重相同，歌诀取女命版本。"""
    engine = BaziEngine()
    male = engine.calculate(1990, 5, 20, 15, 0, "", "男")
    female = engine.calculate(1990, 5, 20, 15, 0, "", "女")
    assert (male.chenggu["liang"], male.chenggu["qian"]) == \
           (female.chenggu["liang"], female.chenggu["qian"])
    assert female.chenggu["jieci"] == chenggu_verse(4, 4, "女")
    assert female.chenggu["jieci"].startswith("夜梦金银醒来空")


def test_bazi_integration_leap_month_and_whole_liang():
    """2023-04-15 10:00：农历闰二月廿五 → 按二月计骨（闰平同重），
    总骨重 1.2+0.7+1.5+1.6 = 5.0 → 整两文本「五两」。"""
    engine = BaziEngine()
    result = engine.calculate(2023, 4, 15, 10, 0, "", "男")
    assert result.chenggu["weight_text"] == "五两"
    assert (result.chenggu["liang"], result.chenggu["qian"]) == (5, 0)
    assert result.chenggu["jieci"] == chenggu_verse(5, 0, "男")


def test_bazi_integration_unknown_gender_defaults_male():
    """性别 unknown：称骨按男命歌诀（与排盘按男口径一致）。"""
    engine = BaziEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "", "unknown")
    assert result.chenggu["jieci"] == chenggu_verse(4, 4, "男")


def test_chenggu_detail_structure():
    """chenggu_detail 输出结构即 BaziResult.chenggu 结构。"""
    detail = chenggu_detail("庚午", 4, 26, "申", "男")
    assert set(detail.keys()) == {"weight_text", "liang", "qian", "jieci"}
    assert detail["weight_text"] == "四两四钱"


def test_data_files_are_fresh():
    """data/chenggu/ 资产与引擎读取一致（防止数据文件被误改）。"""
    tables = _load_tables()
    assert len(tables["fn"]) == 60 and len(tables["cn"]) == 12
    assert len(tables["ln"]) == 30 and len(tables["un"]) == 12
    assert (DATA_DIR / "chenggu_verses.json").exists()
