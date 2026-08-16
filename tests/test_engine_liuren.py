# tests/test_engine_liuren.py
"""六壬规则库测试：九宗门判定/旬空落传落课/贵人顺逆/三传五行 + 考卷。

考卷全部为合成口径（birth 精确到时分，可完整复现排盘），
每条的 expected 均由人工按经典口诀手算核实（见 audit 字段）。
"""
import json

from src.engine.case_loader import load_cases
from src.engine.rules.liuren import (
    analyze,
    classify_zongmen,
    evaluate,
    guiren_direction,
    kongwang_in_sanchuan,
    kongwang_in_sipan,
    sanchuan_wuxing,
    zhi_wuxing,
)
from src.engines.liuren import LiurenEngine

CASES_PATH = "src/engine/cases/liuren_cases.jsonl"


def _parse_birth(birth: str) -> tuple:
    """"1990-08-16 14:30" -> (1990, 8, 16, 14, 30)."""
    date_part, time_part = birth.split(" ")
    y, m, d = (int(x) for x in date_part.split("-"))
    h, mi = (int(x) for x in time_part.split(":"))
    return y, m, d, h, mi


def _chart_of(birth: str) -> dict:
    return LiurenEngine().calculate(*_parse_birth(birth)).to_dict()


def _raw_cases():
    with open(CASES_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ============================================================
# 纯规则函数
# ============================================================

def test_zhi_wuxing():
    assert zhi_wuxing("子") == "水"
    assert zhi_wuxing("午") == "火"
    assert zhi_wuxing("寅") == "木"
    assert zhi_wuxing("酉") == "金"
    assert zhi_wuxing("辰") == "土"


def test_sanchuan_wuxing():
    # 1990-08-16 三传子亥戌 → 水水土
    assert sanchuan_wuxing(["子", "亥", "戌"]) == ["水", "水", "土"]
    assert sanchuan_wuxing(["午", "卯", "子"]) == ["火", "木", "水"]


def test_kongwang_in_sanchuan():
    # 子亥戌 vs 空寅卯：不落传；子申辰 vs 空子丑：子落传
    assert kongwang_in_sanchuan(["子", "亥", "戌"], ["寅", "卯"]) is False
    assert kongwang_in_sanchuan(["子", "申", "辰"], ["子", "丑"]) is True


def test_kongwang_in_sipan():
    # 四课上神[寅亥亥申] 空[戌亥] → 亥落课（去重）
    sipan = [
        {"位置": "第一课(干上)", "干支": "寅丙"},
        {"位置": "第二课", "干支": "亥寅"},
        {"位置": "第三课(支上)", "干支": "亥寅"},
        {"位置": "第四课", "干支": "申亥"},
    ]
    assert kongwang_in_sipan(sipan, ["戌", "亥"]) == ["亥"]
    # 上神[子亥子亥] 空[寅卯] → 无
    sipan2 = [
        {"位置": "第一课(干上)", "干支": "子癸"},
        {"位置": "第二课", "干支": "亥子"},
        {"位置": "第三课(支上)", "干支": "子丑"},
        {"位置": "第四课", "干支": "亥子"},
    ]
    assert kongwang_in_sipan(sipan2, ["寅", "卯"]) == []


def test_guiren_direction():
    # 贵人落地盘阳支(子寅辰午申戌)顺行，阴支逆行
    assert guiren_direction("午") == "顺行"
    assert guiren_direction("酉") == "逆行"
    assert guiren_direction("辰") == "顺行"
    assert guiren_direction("卯") == "逆行"


def test_classify_zongmen_known():
    # 1990-08-16: 唯一克为下贼上 → 贼克/重审课
    chart = _chart_of("1990-08-16 14:30")
    assert classify_zongmen(chart) == ("贼克", "重审课")
    # 2023-01-13 08:30 辛未日: 干上神=日支(实三课)无克无遥克 → 别责课
    chart2 = _chart_of("2023-01-13 08:30")
    assert classify_zongmen(chart2) == ("别责", "别责课")
    # 2023-01-01 00:30 己未日: 干支同宫无克 → 八专课
    chart3 = _chart_of("2023-01-01 00:30")
    assert classify_zongmen(chart3) == ("八专", "八专课")


def test_analyze_returns_points():
    chart = _chart_of("1990-08-16 14:30")
    points = analyze(chart)
    assert isinstance(points, list) and points
    assert all(isinstance(p, str) and p for p in points)
    # 确定性要点含宗门/旬空/贵人信息
    joined = " ".join(points)
    assert "重审课" in joined
    assert "旬空" in joined


def test_evaluate_shape():
    chart = _chart_of("1990-08-16 14:30")
    out = evaluate(chart)
    for key in ("要点", "三传", "宗门", "课名", "三传五行",
                "旬空", "空亡入传", "空亡落四课", "贵人", "贵人顺逆"):
        assert key in out, f"evaluate 缺字段 {key}"


# ============================================================
# 考卷（8+ 条 unit，合成口径，expected 手算核实）
# ============================================================

def test_cases_loader_valid():
    cases = load_cases(CASES_PATH)
    assert len(cases) >= 8
    for case in cases:
        assert case.quality in ("unit", "e2e")
        assert case.id and case.source and case.audit
        assert len(case.pills) == 4  # 排盘可复现性的交叉字段


def test_cases_all_pass():
    for raw in _raw_cases():
        chart = _chart_of(raw["birth"])
        out = evaluate(chart)
        for key, value in raw["expected"].items():
            assert key in out, f"{raw['id']} evaluate 缺 {key}"
            assert out[key] == value, \
                f"{raw['id']} {key}: 期望 {value} 实际 {out[key]}"


def test_classify_consistent_with_engine():
    """规则库九宗门判定与引擎三传宗门一致（独立判定层交叉验证）。"""
    for raw in _raw_cases():
        chart = _chart_of(raw["birth"])
        assert classify_zongmen(chart) == (
            chart["raw_data"]["宗门"], chart["raw_data"]["课名"],
        ), f"{raw['id']} 规则库宗门判定与引擎不一致"
