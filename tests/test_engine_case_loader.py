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
