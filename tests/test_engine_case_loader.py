# tests/test_engine_case_loader.py
import json
import pytest
from src.engine.case_loader import load_cases

def test_load_cases_valid():
    cases = load_cases("src/engine/cases/tiandisui_cases.jsonl")
    assert len(cases) >= 10
    for c in cases:
        assert c.id and c.source and c.source_lines and c.pills
        assert len(c.pills) == 4 and all(len(p) == 2 for p in c.pills)
        assert c.quality in ("unit", "reference", "rejected")

def test_load_cases_rejects_malformed(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(str(bad))

def test_tiandisui_recovered_case():
    """回归：6字干支残行（如原文L6375"壬辰乙未丙申"）不得截断真实段——
    L6375 附近真实命例 丙申甲午丙寅壬辰 必须恢复为 reference；
    顺排/逆排补足干支不得冒充命例（曾致 tds_0429/tds_0444 假条目）。"""
    cases = load_cases("src/engine/cases/tiandisui_cases.jsonl")
    recovered = [c for c in cases if c.pills == ["丙申", "甲午", "丙寅", "壬辰"]]
    assert len(recovered) == 1
    assert recovered[0].quality == "reference"
    fakes = [c for c in cases if c.pills in (["丁酉", "戊戌", "己亥", "庚子"],
                                             ["己酉", "戊申", "丁未", "丙午"])]
    assert fakes == []
