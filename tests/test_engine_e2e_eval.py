import src.engine.e2e_eval as e2e_eval
from src.engine.case_loader import load_cases
from src.engine.deduction import deduce
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


def _pills_case():
    return next(c for c in load_cases("src/engine/cases/e2e_cases.jsonl") if c.pills)


def test_run_e2e_rejects_chain_missing_duanyu_step(monkeypatch):
    """终审 M-1：断语要点步骤缺失必须被门禁拦截（复制真实链但去掉末步，
    剩 5 步仍≥5 的静默盲区回归检测）。"""
    case = _pills_case()

    def fake_deduce(pills, engine_result=None, question=""):
        chain = deduce(pills, engine_result, question)
        chain.steps = chain.steps[:-1]  # 去掉末步"断语要点.compose"
        return chain

    monkeypatch.setattr(e2e_eval, "deduce", fake_deduce)
    err = e2e_eval._run_one(case, llm=FakeLLM(), use_real_retriever=False)
    assert err is not None
    assert "断语要点" in err


def test_run_e2e_rejects_empty_duanyu_output(monkeypatch):
    """终审 M-1：断语要点步骤存在但 output 为空同样必须被门禁拦截。"""
    case = _pills_case()

    def fake_deduce(pills, engine_result=None, question=""):
        chain = deduce(pills, engine_result, question)
        chain.steps[-1].output = ""  # 断语要点 output 置空
        return chain

    monkeypatch.setattr(e2e_eval, "deduce", fake_deduce)
    err = e2e_eval._run_one(case, llm=FakeLLM(), use_real_retriever=False)
    assert err is not None
    assert "断语要点" in err
