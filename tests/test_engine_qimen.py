# tests/test_engine_qimen.py
"""奇门规则库测试：八门/九星五行属性、三吉门、阴阳遁、八神序 + QimenEngine 交叉验证。"""
import re

import pytest

from src.engine.case_loader import load_cases
from src.engine.rules.qimen import (
    analyze, ba_shen_order, dun_style, evaluate, men_attribute,
    san_ji_men, star_attribute,
)
from src.engines.qimen import QimenEngine

# ---- 八门五行属性（烟波钓叟歌八门落宫：开乾金/休坎水/生艮土/伤震木/杜巽木/景离火/死坤土/惊兑金）----

def test_men_attribute_known():
    assert men_attribute("开") == "金"
    assert men_attribute("休") == "水"
    assert men_attribute("生") == "土"
    assert men_attribute("伤") == "木"
    assert men_attribute("杜") == "木"
    assert men_attribute("景") == "火"
    assert men_attribute("死") == "土"
    assert men_attribute("惊") == "金"

def test_star_attribute_known():
    # 九星落宫五行：蓬水/任土/冲木/辅木/英火/芮土/柱金/心金/禽土（禽寄坤）
    assert star_attribute("天蓬") == "水"
    assert star_attribute("天任") == "土"
    assert star_attribute("天冲") == "木"
    assert star_attribute("天辅") == "木"
    assert star_attribute("天英") == "火"
    assert star_attribute("天芮") == "土"
    assert star_attribute("天柱") == "金"
    assert star_attribute("天心") == "金"
    assert star_attribute("天禽") == "土"

def test_san_ji_men():
    # 三吉门：休、生、开
    assert san_ji_men("休") is True
    assert san_ji_men("生") is True
    assert san_ji_men("开") is True
    # 其余五门非吉门
    assert san_ji_men("惊") is False
    assert san_ji_men("死") is False
    assert san_ji_men("伤") is False
    assert san_ji_men("杜") is False
    assert san_ji_men("景") is False

def test_ba_shen_order():
    # 八神序：值符→螣蛇→太阴→六合→白虎→玄武→九地→九天
    assert ba_shen_order() == ["值符", "螣蛇", "太阴", "六合", "白虎", "玄武", "九地", "九天"]

def test_dun_style_by_solar_terms():
    # 2024-07-11 已过夏至(6/21)，处小暑 → 阴遁
    assert dun_style(2024, 7, 11, 13, 30) == "阴遁"
    # 2024-12-22 已过冬至(12/21) → 阳遁
    assert dun_style(2024, 12, 22, 10) == "阳遁"
    # 2025-01-08 小寒（冬至后、夏至前） → 阳遁
    assert dun_style(2025, 1, 8, 10) == "阳遁"
    # 2024-08-15 立秋（夏至后、冬至前） → 阴遁
    assert dun_style(2024, 8, 15, 10) == "阴遁"

def test_invalid_input_raises():
    with pytest.raises(ValueError):
        men_attribute("奇")
    with pytest.raises(ValueError):
        star_attribute("天干")
    with pytest.raises(ValueError):
        san_ji_men("中")

# ---- 与现有奇门引擎交叉验证（规则库为独立查表实现）----

def test_dun_style_cross_validate_engine():
    engine = QimenEngine()
    for y, m, d, h in [(2024, 7, 11, 13), (2024, 12, 22, 10),
                       (2025, 1, 8, 10), (2024, 8, 15, 10)]:
        r = engine.calculate(y, m, d, h)
        assert dun_style(y, m, d, h) == r.dun_type, f"{y}-{m}-{d} 遁局不一致"

def test_evaluate_on_engine_result():
    engine = QimenEngine()
    r = engine.calculate(2024, 7, 11, 13, 30)
    out = evaluate(r)
    assert out["遁局"] == "阴遁"
    assert out["值符星"] == r.zhifu_star
    assert out["值使门"] == r.zhishi_door
    assert out["八门属性"]["开"] == "金"
    assert out["八门属性"]["休"] == "水"
    assert out["八门属性"]["景"] == "火"
    assert out["九星属性"]["天蓬"] == "水"
    assert out["九星属性"]["天英"] == "火"
    assert out["九星属性"]["天禽"] == "土"
    assert out["三吉门"] == ["休", "生", "开"]
    assert out["八神序"] == ["值符", "螣蛇", "太阴", "六合", "白虎", "玄武", "九地", "九天"]

def test_evaluate_accepts_dict():
    # 规则库独立于排盘引擎：直接喂 dict 也应工作
    out = evaluate({"dun_type": "阳遁", "ju_number": 3,
                    "bamen": {"乾": "开"}, "jiuxing": {"乾": "天心"},
                    "zhifu_star": "天心", "zhishi_door": "开"})
    assert out["遁局"] == "阳遁"
    assert out["八门属性"] == {"开": "金"}
    assert out["九星属性"] == {"天心": "金"}
    assert out["三吉门"] == ["开"]

def test_analyze_returns_points():
    engine = QimenEngine()
    r = engine.calculate(2024, 7, 11, 13, 30)
    points = analyze(r)
    assert isinstance(points, list) and points
    assert any("遁局" in p for p in points)
    assert any("值符" in p for p in points)
    assert any("三吉门" in p for p in points)
    assert any("八神序" in p for p in points)

# ---- 考卷：全部 unit case 用真实排盘交叉验证（expected 为 evaluate 输出子集断言）----

_CHART_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})")

def _chart_for(case):
    """从 case.prose 提取起盘时间（如 2024-07-11 13:30），默认 2024-07-11 13:30。"""
    m = _CHART_RE.search(case.prose or "")
    if m:
        y, mo, d, h, mi = (int(g) for g in m.groups())
    else:
        y, mo, d, h, mi = 2024, 7, 11, 13, 30
    return QimenEngine().calculate(y, mo, d, h, mi)

def test_qimen_cases_loadable():
    cases = load_cases("src/engine/cases/qimen_cases.jsonl")
    assert len(cases) >= 8
    assert all(c.quality == "unit" for c in cases)
    assert all(c.expected for c in cases)

def test_qimen_cases_all_pass():
    """每 case 断言 evaluate(真实排盘) 满足 expected（dict 子集、list/str 全等）。"""
    cases = load_cases("src/engine/cases/qimen_cases.jsonl")
    for case in cases:
        out = evaluate(_chart_for(case))
        for key, want in case.expected.items():
            got = out.get(key, "")
            if isinstance(want, dict) and isinstance(got, dict):
                miss = {k: v for k, v in want.items() if got.get(k) != v}
                assert not miss, f"{case.id} {key} 不符: {miss}"
            else:
                assert got == want, f"{case.id} {key}: 期望 {want} 实际 {got}"
