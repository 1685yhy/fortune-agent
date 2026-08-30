"""K4 权威对照测试：康熙笔画源 + 重复键清理 + 三才档级 2 例修正。

权威基准：中华起名网 zhonghuaqiming.com 逐字取证（2026-08-30），
报告 /tmp/compare_xingming.md（K4 背景报告），详见 .superpowers/sdd/task-K4-report.md。
"""
import json
import os
import re

from src.engines.xingming import (
    XingmingEngine,
    evaluate_sancai,
    get_stroke_count,
    get_wuge,
)

_DATA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "xingming_kangxi_strokes.json",
)

# ── 1. 21 字笔画逐字断言（报告 §笔画对照表全量，权威取证）──────────────
ANCHOR_STROKES = {
    "张": 11, "梓": 11, "墨": 15, "宇": 6, "轩": 10,
    "李": 7, "诗": 13, "涵": 12, "雨": 8, "桐": 10,
    "王": 4, "俊": 9, "杰": 12, "明": 8, "远": 17,
    "陈": 16, "欣": 8, "怡": 9, "刘": 15, "志": 7, "强": 12,
}


def test_21_anchor_chars_stroke_counts():
    """21 对比锚点字逐字断言（权威=康熙笔画）。"""
    for ch, expected in ANCHOR_STROKES.items():
        got = get_stroke_count(ch)
        assert got == expected, f"{ch}: 权威={expected}，我方={got}"


# ── 2. 简繁差异字校准组（康熙部首计画规则双端断言）──────────────────
def test_simplified_traditional_calibration():
    """为/為、龙/龍、张/張 简繁双端值一致（康熙口径）。"""
    assert get_stroke_count("为") == 12
    assert get_stroke_count("為") == 12
    assert get_stroke_count("龙") == 16
    assert get_stroke_count("龍") == 16
    assert get_stroke_count("张") == 11
    assert get_stroke_count("張") == 11


# ── 3. 部首特殊计画专项（氵=4、辶=7、忄=4、阝=8）────────────────────
def test_radical_special_counting_shuidian():
    """氵=4：涵(氵4+函8=12)、江(氵4+工3=7)、清(氵4+青8=12)。"""
    assert get_stroke_count("涵") == 12
    assert get_stroke_count("江") == 7
    assert get_stroke_count("清") == 12


def test_radical_special_counting_zou():
    """辶=7：远(袁10+辶7=17)、运(云4+辶7=11)。"""
    assert get_stroke_count("远") == 17
    assert get_stroke_count("运") == 11


def test_radical_special_counting_xin():
    """忄=4：怡(台5+忄4=9)、情(青8+忄4=12)。"""
    assert get_stroke_count("怡") == 9
    assert get_stroke_count("情") == 12


def test_radical_special_counting_er():
    """阝=8：陈(東8+阝8=16)、阳(陽17 权威口径)、陆(陸16)。"""
    assert get_stroke_count("陈") == 16
    assert get_stroke_count("阳") == 17
    assert get_stroke_count("陆") == 16


def test_numerals_by_value():
    """数字按数值计画（权威口径）：四=4、五=5、六=6、七=7、八=8、九=9、十=10。"""
    for ch, n in {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}.items():
        assert get_stroke_count(ch) == n, f"{ch}: 期望 {n}"


# ── 4. 五格复算：X1-X5 五案例（含比选名）与权威锚点断言 ──────────────
X_CASES = [
    # (姓, 名, 天格, 人格, 地格, 外格, 总格)
    ("X1",  "张", "梓墨", 12, 22, 26, 16, 37),
    ("X1b", "张", "宇轩", 12, 17, 16, 11, 27),
    ("X2",  "李", "诗涵", 8, 20, 25, 13, 32),
    ("X2b", "李", "雨桐", 8, 15, 18, 11, 25),
    ("X3",  "王", "俊杰", 5, 13, 21, 13, 25),
    ("X3b", "王", "明远", 5, 12, 25, 18, 29),
    ("X4",  "陈", "雨桐", 17, 24, 18, 11, 34),
    ("X4b", "陈", "欣怡", 17, 24, 17, 10, 33),
    ("X5",  "刘", "明远", 16, 23, 25, 18, 40),
    ("X5b", "刘", "志强", 16, 22, 19, 13, 34),
]


def test_x_cases_wuge_match_authority():
    """X1-X5（含比选名）五格数理与权威逐格一致。"""
    for case_id, xing, ming, tg, rg, dg, wg, zg in X_CASES:
        wuge = get_wuge(xing, ming)
        assert wuge == {"天格": tg, "人格": rg, "地格": dg, "外格": wg, "总格": zg}, (
            f"{case_id} {xing}{ming}: 期望 天{tg}人{rg}地{dg}外{wg}总{zg}，"
            f"实际 天{wuge['天格']}人{wuge['人格']}地{wuge['地格']}"
            f"外{wuge['外格']}总{wuge['总格']}"
        )


def test_x_cases_analyze_real_run():
    """真实用户路径：X1-X5 走 XingmingEngine.analyze，五格与权威逐项对。"""
    engine = XingmingEngine()
    for case_id, xing, ming, tg, rg, dg, wg, zg in X_CASES:
        result = engine.analyze(xing, ming)
        assert result.wuge == {"天格": tg, "人格": rg, "地格": dg, "外格": wg, "总格": zg}, (
            f"{case_id} {xing}{ming} analyze 五格不一致"
        )


def test_sancai_tier_fixes():
    """三才档级 2 例修正：木金土=凶（权威），土火木=吉（权威）。"""
    # 直接函数级断言
    assert evaluate_sancai("木", "金", "土") == "凶"
    assert evaluate_sancai("土", "火", "木") == "吉"
    # X1b 张宇轩（天12木/人17金/地16土 → 木金土）真实路径
    r1 = XingmingEngine().analyze("张", "宇轩")
    assert r1.sancai == "木金土"
    assert r1.sancai_ji == "凶"
    # X3 王俊杰（天5土/人13火/地21木 → 土火木）真实路径
    r2 = XingmingEngine().analyze("王", "俊杰")
    assert r2.sancai == "土火木"
    assert r2.sancai_ji == "吉"


# ── 5. 重复键清理：数据文件无重复键（程序化检查）────────────────────
def test_stroke_data_no_duplicate_keys():
    """笔画数据文件（data/xingming_kangxi_strokes.json）无重复键。"""
    assert os.path.exists(_DATA_FILE), f"数据文件缺失: {_DATA_FILE}"
    seen = []

    def _hook(pairs):
        for k, v in pairs:
            assert k not in seen, f"重复键: {k}"
            seen.append(k)
        return dict(pairs)

    with open(_DATA_FILE, encoding="utf-8") as f:
        table = json.load(f, object_pairs_hook=_hook)
    assert table, "笔画表为空"
    assert all(isinstance(v, int) and v > 0 for v in table.values()), "存在非正笔画值"
    # 覆盖 21 锚点 + 简繁校准组 + 部首特殊字
    covered = set(ANCHOR_STROKES) | {"为", "為", "龙", "龍", "張",
                                     "涵", "江", "清", "远", "运", "怡", "情", "陈", "阳", "陆"}
    missing = covered - set(table)
    assert not missing, f"数据文件缺少字: {missing}"


def test_table_source_consistency():
    """数据文件与模块加载的 STROKE_TABLE 一致（单一事实源）。"""
    with open(_DATA_FILE, encoding="utf-8") as f:
        table = json.load(f)
    from src.engines.xingming import STROKE_TABLE
    assert STROKE_TABLE == table, "模块加载表与数据文件不一致"


# ── 6. 生僻字/表外字降级（失败路径）─────────────────────────────────
def test_unknown_char_fallback():
    """表外字/生僻字返回 0，不崩溃（调用方按 0 画降级）。"""
    assert get_stroke_count("😀") == 0
    assert get_stroke_count("畾") == 0 or get_stroke_count("畾") > 0  # 有值或无值均可
    assert get_stroke_count("") == 0
