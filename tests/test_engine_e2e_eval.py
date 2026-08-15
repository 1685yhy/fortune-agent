from src.engine.case_loader import load_cases
from src.engine.e2e_eval import run_e2e


class FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        return type("AR", (), {"response": "解读", "tokens_used": 10, "model": "fake"})()


def test_run_e2e_all_cases_pass():
    cases = load_cases("src/engine/cases/e2e_cases.jsonl")
    assert len(cases) == 25
    report = run_e2e(cases, llm=FakeLLM(), use_real_retriever=False)
    assert report.failed == 0, f"e2e 失败: {report.details[:3]}"
    assert report.passed == len(cases)


def test_e2e_cases_have_valid_inputs():
    cases = load_cases("src/engine/cases/e2e_cases.jsonl")
    for c in cases:
        if c.pills:
            assert len(c.pills) == 4
        else:
            b = c.expected.get("birth")
            assert b and b["year"] and 1 <= b["month"] <= 12
