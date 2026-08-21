"""流年/流月/流时生成与干支关系集成测试（L2-4，问真方式：排盘结果带出流年表）。

锚点：闫海洋盘 1999-05-13 11:25 北京 男 → 己卯 己巳 乙丑 壬午。
  - 流年表口径：年柱 + 岁差顺推（1999 己卯 → 2000 庚辰 → 2024 甲辰 → 2026 丙午）
  - 流月五虎遁：己年正月丙寅（甲己之年丙作首…，月支固定寅=正月）
  - 流时五鼠遁：乙日子时丙子（甲己还加甲、乙庚丙作初…，时辰支固定子=首）
  - 干支关系集成（rel_with 复用）：庚子 = 合日主+天克+地冲三例；丁未 = 地冲+天克两例

回归：tests/test_bazi_qz_full.py 200 案例另行跑（排盘主链路四柱/大运/神煞等不受影响）。
批1（流年详解）：liunian_full 每项另带 shensha（流年神煞）/ rel（与原局关系）/
dayun（所在大运及大运vs流年关系）——见 test_integration_liunian_full_detail。
"""
from src.engines.bazi import (BaziEngine, liunian_ganzhi, liunian_table,
                              liuyue, liushi)
from src.engines.shensha import shensha_of_dayun
from src.engines.ganzhi_rel import analyze_relations

ANCHOR_BAZI = ["己卯", "己巳", "乙丑", "壬午"]
ANCHOR_BIRTH = (1999, 5, 13, 11, 25, "北京", "男")
ENGINE = BaziEngine()


def _base(row):
    """liunian_full 行 → 基础四键（批1 起每项另带 shensha/rel/dayun 详解字段）。"""
    return {k: row[k] for k in ("year", "age", "ganzhi", "nayin")}


# ───────────────────────── 1. 流年表（年柱 + 岁差） ─────────────────────────

def test_liunian_ganzhi_anchor():
    """流年干支锚点：1999 己卯 → 2000 庚辰 / 2024 甲辰 / 2026 丙午。"""
    assert liunian_ganzhi(1999, "己卯", 2000) == "庚辰"
    assert liunian_ganzhi(1999, "己卯", 2024) == "甲辰"
    assert liunian_ganzhi(1999, "己卯", 2026) == "丙午"


def test_liunian_table_anchor():
    """流年表：出生年起 30 年，年柱+岁差顺推，含纳音与虚岁。"""
    table = liunian_table(1999, "己卯")
    assert len(table) == 30
    assert table[0] == {"year": 1999, "age": 1, "ganzhi": "己卯", "nayin": "城头土"}
    assert table[1] == {"year": 2000, "age": 2, "ganzhi": "庚辰", "nayin": "白蜡金"}
    assert table[27] == {"year": 2026, "age": 28, "ganzhi": "丙午", "nayin": "天河水"}
    assert table[29] == {"year": 2028, "age": 30, "ganzhi": "戊申", "nayin": "大驿土"}
    # 表内每项与 liunian_ganzhi 同源（口径自洽）
    for i, row in enumerate(table):
        assert row["ganzhi"] == liunian_ganzhi(1999, "己卯", 1999 + i)
        assert row["year"] == 1999 + i
        assert row["age"] == i + 1


def test_liunian_table_cycle():
    """六十甲子轮回：第 61 项（60 年整轮）回到出生年柱。"""
    table = liunian_table(1999, "己卯", years=61)
    assert len(table) == 61
    assert table[59]["ganzhi"] == "戊寅"  # 己卯 + 59（60 甲子末位，戊寅）
    assert table[59]["year"] == 2058
    assert table[60]["ganzhi"] == "己卯"  # 己卯 + 60 = 整轮回归
    assert table[60]["year"] == 2059
    assert liunian_ganzhi(1984, "甲子", 2043) == "癸亥"  # 甲子年 + 59 年 → 癸亥


# ───────────────────────── 2. 流月（五虎遁） ─────────────────────────

def test_liuyue_anchor():
    """己年（甲己之年丙作首）：正月丙寅 … 十二月丁丑。"""
    months = liuyue("己卯")
    assert len(months) == 12
    assert months == ["丙寅", "丁卯", "戊辰", "己巳", "庚午", "辛未",
                      "壬申", "癸酉", "甲戌", "乙亥", "丙子", "丁丑"]


def test_liuyue_all_years_first_month():
    """十干各年正月干首：甲己丙作首、乙庚戊为头、丙辛庚、丁壬壬、戊癸甲。"""
    firsts = {g: liuyue(g + "子")[0] for g in "甲乙丙丁戊己庚辛壬癸"}
    assert firsts == {"甲": "丙寅", "己": "丙寅", "乙": "戊寅", "庚": "戊寅",
                      "丙": "庚寅", "辛": "庚寅", "丁": "壬寅", "壬": "壬寅",
                      "戊": "甲寅", "癸": "甲寅"}


def test_liuyue_month_zhi_fixed():
    """月支固定：寅=正月 … 丑=十二月。"""
    assert [m[1] for m in liuyue("甲子")] == list("寅卯辰巳午未申酉戌亥子丑")


# ───────────────────────── 3. 流时（五鼠遁） ─────────────────────────

def test_liushi_anchor():
    """乙日（乙庚丙作初）：子时丙子 … 亥时丁亥。"""
    hours = liushi("乙丑")
    assert len(hours) == 12
    assert hours == ["丙子", "丁丑", "戊寅", "己卯", "庚辰", "辛巳",
                     "壬午", "癸未", "甲申", "乙酉", "丙戌", "丁亥"]


def test_liushi_all_days_first_hour():
    """十干各日子时干首：甲己还加甲、乙庚丙作初、丙辛从戊起、丁壬庚子居、戊癸壬子头。"""
    firsts = {g: liushi(g + "子")[0] for g in "甲乙丙丁戊己庚辛壬癸"}
    assert firsts == {"甲": "甲子", "己": "甲子", "乙": "丙子", "庚": "丙子",
                      "丙": "戊子", "辛": "戊子", "丁": "庚子", "壬": "庚子",
                      "戊": "壬子", "癸": "壬子"}


def test_liushi_hour_zhi_fixed():
    """时辰支固定：子=首 … 亥=末。"""
    assert [h[1] for h in liushi("甲子")] == list("子丑寅卯辰巳午未申酉戌亥")


# ───────────────────────── 4. BaziResult 集成 ─────────────────────────

def test_integration_liunian_full():
    """整盘集成：liunian_full = 出生年起 30 年流年表（基础四键与模块函数同源）。"""
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    assert r.bazi == ANCHOR_BAZI
    assert len(r.liunian_full) == 30
    base = liunian_table(1999, "己卯")
    for row, base_row in zip(r.liunian_full, base):
        assert _base(row) == base_row
    assert r.liunian_full[1]["ganzhi"] == "庚辰"
    assert r.liunian_full[27]["ganzhi"] == "丙午"


def test_integration_liunian_full_detail():
    """批1 流年详解：liunian_full 30 年每项带 流年神煞/与原局关系/所在大运（含大运vs流年关系）。

    锚点 1999-05-13 北京 男（己卯 己巳 乙丑 壬午）：
    - 2020 庚子（虚岁 22）：流年神煞 7 种（shensha_of_dayun 问真口径同源）；
      rel 含「庚合日主乙」；所在大运 = 虚岁定位步（sui 13 丁卯），大运vs流年关系含 合
    - 起运前年份（1999 虚岁 1 < 3 岁起运）：dayun = {sui:0, ganzhi:'', rel:[]}
    """
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    for row in r.liunian_full:
        assert set(row) == {"year", "age", "ganzhi", "nayin",
                            "shensha", "rel", "dayun"}
        assert set(row["dayun"]) == {"sui", "ganzhi", "rel"}
        # 流年神煞与 shensha_of_dayun 直接调用同源（问真 dyshensha 同款口径）
        assert row["shensha"] == shensha_of_dayun(
            row["ganzhi"], r.bazi[0], r.bazi[1], r.bazi[2], "男")
        # 与原局关系 = rel_with 同源
        assert row["rel"] == r.rel_with(row["ganzhi"])
        # 所在大运 rel = analyze_relations 直接调用同源（空步除外）
        if row["dayun"]["ganzhi"]:
            assert row["dayun"]["rel"] == [
                {"type": it.type, "desc": it.desc}
                for it in analyze_relations(row["dayun"]["ganzhi"],
                                            row["ganzhi"], r.bazi)]

    # 2020 庚子（虚岁 22，大运步 sui 13 丁卯）
    row20 = next(x for x in r.liunian_full if x["year"] == 2020)
    assert row20["ganzhi"] == "庚子" and row20["age"] == 22
    assert row20["shensha"] == ["天乙贵人", "太极贵人", "桃花", "红鸾",
                                "披麻", "月德贵人", "德秀贵人"]
    assert any(x["type"] == "合" and x["desc"] == "庚合日主乙"
               for x in row20["rel"])
    assert {"between": "日柱", "type": "天克", "desc": "天干庚克乙"} in row20["rel"]
    step = next((s, g) for s, g in r.dayun if s <= 22 <= s + 9)
    assert row20["dayun"]["sui"] == step[0] == 13
    assert row20["dayun"]["ganzhi"] == step[1] == "丁卯"
    assert any(x["type"] == "合" for x in row20["dayun"]["rel"])
    assert any(x["type"] == "天克" for x in row20["dayun"]["rel"])

    # 起运前年份（虚岁 < 起运岁数 3）：无大运步
    row0 = r.liunian_full[0]
    assert row0["year"] == 1999 and row0["age"] == 1
    assert row0["dayun"] == {"sui": 0, "ganzhi": "", "rel": []}


def test_integration_liuyue():
    """整盘集成：liuyue = 当前流年（真年，立春界定）12 流月。"""
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    ly = r.liuyue
    assert len(ly["months"]) == 12
    assert ly["ganzhi"] == liunian_ganzhi(1999, "己卯", ly["year"])
    assert ly["months"] == liuyue(ly["ganzhi"])


def test_integration_liushi():
    """整盘集成：liushi = 今日 12 流时（日柱 = 当日干支，五鼠遁配时）。"""
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    ls = r.liushi
    assert len(ls["hours"]) == 12
    assert ls["hours"] == liushi(ls["day_pillar"])


def test_integration_liunian_rel():
    """整盘集成：liunian_rel = 当前流年 vs 原局各柱（复用 rel_with）。"""
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    lr = r.liunian_rel
    assert lr["ganzhi"] == liunian_ganzhi(1999, "己卯", lr["year"])
    assert lr["rel"] == r.rel_with(lr["ganzhi"])
    assert isinstance(lr["rel"], list)


def test_integration_dayun_rel():
    """整盘集成：dayun_rel = 各步大运 vs 原局摘要（与 dayun 逐项对应）。"""
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    assert len(r.dayun) == 12
    assert len(r.dayun_rel) == len(r.dayun)
    for step, (sui, gz) in zip(r.dayun_rel, r.dayun):
        assert step["sui"] == sui and step["ganzhi"] == gz
        assert step["rel"] == r.rel_with(gz)


# ───────────────────────── 5. 干支关系集成锚点（rel_with 2 例） ─────────────────────────

def test_rel_anchor_2020_gengzi():
    """流年 2020 庚子（年柱+21）：合日主 + 天克（日柱）+ 地冲（时柱）三例集成。"""
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    assert liunian_ganzhi(1999, "己卯", 2020) == "庚子"
    rel = r.rel_with("庚子")
    # 庚合日主乙（单合；between 为 rel_with 首个产出柱位，不在此断言）
    assert any(x["type"] == "合" and x["desc"] == "庚合日主乙" for x in rel)
    # 庚金克乙木（日柱乙丑）
    assert {"between": "日柱", "type": "天克", "desc": "天干庚克乙"} in rel
    # 子午冲（时柱壬午）
    assert {"between": "时柱", "type": "地冲", "desc": "地支子冲午"} in rel


def test_rel_anchor_2027_dingwei():
    """流年 2027 丁未（年柱+28）：地冲（日柱丑未冲）+ 天克（时柱壬克丁）两例集成。"""
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    assert liunian_ganzhi(1999, "己卯", 2027) == "丁未"
    rel = r.rel_with("丁未")
    assert {"between": "日柱", "type": "地冲", "desc": "地支未冲丑"} in rel
    assert {"between": "时柱", "type": "天克", "desc": "天干壬克丁"} in rel


# ───────────────── 6. 流年基准年边界（干支年 vs 农历年，2026-08-20 修复） ─────────────────

def test_boundary_lichun_after_cny_before_table0():
    """[立春, 正月初一) 窗口：1999-02-10（立春 02-04 后、正月初一 02-16 前），
    农历年 = 1998 但年柱 = 己卯（1999 干支年）→ 流年表首项必须 {1999, 己卯}，
    不得按农历年基数错位成 {1998, 己卯}（旧口径 bug：整表错位一年）。"""
    r = ENGINE.calculate(1999, 2, 10, 12, 0, "北京", "男")
    assert r.bazi[0] == "己卯"  # 年柱已按立春换年（口径前提）
    assert _base(r.liunian_full[0]) == {"year": 1999, "age": 1,
                                        "ganzhi": "己卯", "nayin": "城头土"}
    assert _base(r.liunian_full[1]) == {"year": 2000, "age": 2,
                                        "ganzhi": "庚辰", "nayin": "白蜡金"}
    # 全表与干支年基数自洽
    for i, row in enumerate(r.liunian_full):
        assert row["ganzhi"] == liunian_ganzhi(1999, "己卯", 1999 + i)
        assert row["year"] == 1999 + i


def test_boundary_lichun_after_cny_before_current_2026():
    """同窗口盘（1999-02-10）2026 当前流年：rel/liuyue 干支 == 丙午
    （1999 干支年 + 27），不得按农历年基数（1998）错成 丁未（+28）。"""
    r = ENGINE.calculate(1999, 2, 10, 12, 0, "北京", "男")
    assert r.liunian_rel["year"] == 2026
    assert r.liunian_rel["ganzhi"] == "丙午"
    assert r.liuyue["year"] == 2026
    assert r.liuyue["ganzhi"] == "丙午"
    assert r.liuyue["months"] == liuyue("丙午")
    assert liunian_ganzhi(1999, "己卯", 2026) == "丙午"


def test_boundary_before_lichun_table0():
    """立春前：1999-01-20（早于立春 02-04）→ 干支年 1998、年柱 戊寅，
    流年表首项 {1998, 戊寅}（边界另一侧不受影响）。"""
    r = ENGINE.calculate(1999, 1, 20, 12, 0, "北京", "男")
    assert r.bazi[0] == "戊寅"
    assert _base(r.liunian_full[0]) == {"year": 1998, "age": 1,
                                        "ganzhi": "戊寅", "nayin": "城头土"}
    assert _base(r.liunian_full[1]) == {"year": 1999, "age": 2,
                                        "ganzhi": "己卯", "nayin": "城头土"}


def test_anchor_2026_liunian_rel_fixed():
    """2026 流年锚点（固定年断言）：1999-05-13 盘（干支年 = 1999）→
    当前流年干支 == 丙午（旧口径同值，防回归）。"""
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    assert r.liunian_rel["year"] == 2026
    assert r.liunian_rel["ganzhi"] == "丙午"
    assert r.liuyue["ganzhi"] == "丙午"
