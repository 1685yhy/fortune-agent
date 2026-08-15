# tests/test_engine_eval.py
from src.engine.case_loader import Case
from src.engine.eval import run_unit

def fake_rule(pills):
    return {"geju": "正官格"}

def test_run_unit_pass_and_fail():
    cases = [
        Case(id="c1", source="t", source_lines="1", pills=["甲子", "乙丑", "丙寅", "丁卯"],
             expected={"geju": "正官格"}, quality="unit"),
        Case(id="c2", source="t", source_lines="2", pills=["甲子", "乙丑", "丙寅", "丁卯"],
             expected={"geju": "七杀格"}, quality="unit"),
        Case(id="c3", source="t", source_lines="3", pills=[], expected={}, quality="reference"),
    ]
    report = run_unit(cases, fake_rule)
    assert report.passed == 1
    assert report.failed == 1
    assert len(report.details) == 1
    assert report.details[0]["case_id"] == "c2"
    assert report.details[0]["expected"] == "七杀格"
    assert report.details[0]["actual"] == "正官格"
