from src.engine.select_cases import select_cases


def test_select_5_cases_with_expected_fields():
    cases = select_cases("src/engine/cases/e2e_cases.jsonl",
                         "src/engine/cases/tiandisui_cases.jsonl")
    assert len(cases) == 5
    mingli = [c for c in cases if c["source"] == "mingli_bench"]
    tds = [c for c in cases if c["source"] != "mingli_bench"]
    assert len(mingli) == 4 and len(tds) == 1
    for c in mingli:
        assert c["birth"]["year"] and c["question"]
    assert tds[0]["pills"] and len(tds[0]["pills"]) == 4

def test_select_is_deterministic():
    a = select_cases("src/engine/cases/e2e_cases.jsonl",
                     "src/engine/cases/tiandisui_cases.jsonl")
    b = select_cases("src/engine/cases/e2e_cases.jsonl",
                     "src/engine/cases/tiandisui_cases.jsonl")
    assert a == b
