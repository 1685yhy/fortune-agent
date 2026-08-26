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


# ---------- 阶段3 Task 7：多体系 e2e 考卷（紫微/六爻/奇门/六壬） ----------

PHASE3_CASES = "src/engine/cases/e2e_phase3_cases.jsonl"
SYSTEMS = {"ziwei", "liuyao", "qimen", "liuren"}


def test_phase3_cases_load_with_system_and_complete_inputs():
    """多体系考卷可加载：system 路由字段 + 公历生日完整输入；六爻 seed 固定、question 必填。"""
    cases = load_cases(PHASE3_CASES)
    assert len(cases) == 4
    by_system = {c.expected["system"]: c for c in cases}
    assert set(by_system) == SYSTEMS, f"必须恰好覆盖四体系: {sorted(by_system)}"
    for case in by_system.values():
        b = case.expected["birth"]
        assert isinstance(b["year"], int) and 1 <= b["month"] <= 12
        assert 1 <= b["day"] <= 31 and 0 <= b["hour"] <= 23 and 0 <= b["minute"] <= 59
    ly = by_system["liuyao"]
    assert isinstance(ly.expected["seed"], int), "六爻 seed 必须固定为整数"
    assert ly.expected["question"].strip(), "六爻 question 必填"


def test_run_e2e_phase3_routes_by_system(monkeypatch):
    """按考卷 system 路由：四条用例必须各自调 deduce(system=...)（紫微/六爻/奇门/六壬）。"""
    cases = load_cases(PHASE3_CASES)
    called = []

    def spy_deduce(pills, engine_result=None, question="", system="bazi"):
        called.append(system)
        return deduce(pills, engine_result=engine_result, question=question, system=system)

    monkeypatch.setattr(e2e_eval, "deduce", spy_deduce)
    report = run_e2e(cases, llm=FakeLLM(), use_real_retriever=False)
    assert report.failed == 0, f"e2e 失败: {report.details[:3]}"
    assert report.passed == len(cases)
    assert sorted(called) == ["liuren", "liuyao", "qimen", "ziwei"], \
        f"必须按考卷 system 路由, 实际调用: {sorted(called)}"


def test_run_e2e_phase3_each_system_chain_prefixes():
    """断言参数化：四条用例链非空且各自含本体系步骤前缀，末步为本体系断语要点。"""
    for case in load_cases(PHASE3_CASES):
        system = case.expected["system"]
        chain = e2e_eval._build_chain(case, system)
        rules = [s.rule for s in chain.steps]
        assert chain.steps, f"{case.id}: 链非空"
        assert any(r.startswith(f"{system}.") for r in rules), \
            f"{case.id}: 缺 {system}. 前缀步骤"
        assert rules[-1] == f"{system}.断语要点.compose"


def test_run_e2e_phase3_liuyao_seed_fixed():
    """六爻 seed 固定可复现：seed=42 → 雷火丰（与 Task 6 单测交叉验证）。"""
    case = next(c for c in load_cases(PHASE3_CASES) if c.expected["system"] == "liuyao")
    chain = e2e_eval._build_chain(case, "liuyao")
    assert "雷火丰" in chain.steps[0].output
    assert "固定 seed" in chain.steps[0].fact


def test_check_chain_rejects_wrong_system_prefix():
    """断言参数化拦截：链缺该体系前缀（如六壬用例跑出八字链）必须报缺前缀步骤。"""
    bazi_chain = deduce(["庚午", "辛巳", "乙酉", "甲申"], question="")  # 八字链
    err = e2e_eval._check_chain(bazi_chain, "liuren")
    assert err is not None and "liuren." in err
