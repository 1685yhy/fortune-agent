import json
from src.engine.coverage import write_coverage

def test_write_coverage(tmp_path):
    out = tmp_path / "coverage.json"
    data = write_coverage({"shishen": 2, "geju": 3}, {"tiandisui": 10}, out=str(out))
    assert data["rules"]["geju"] == 3
    assert data["cases"]["tiandisui"] == 10
    assert json.load(open(out, encoding="utf-8")) == data
