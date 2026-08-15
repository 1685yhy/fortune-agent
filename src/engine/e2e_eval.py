"""e2e 跑分器：全链路(排盘→推演→举证→综合)逐条断言"全"与"稳"，不判预测对错。"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.case_loader import Case
from src.engine.deduction import deduce
from src.engine.report import compose_report


@dataclass
class E2EReport:
    passed: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)


def _run_one(case: Case, llm, use_real_retriever: bool) -> str | None:
    """返回 None=通过；否则返回失败原因。"""
    try:
        if case.pills:
            chain = deduce(case.pills, engine_result=None, question="")
        else:
            b = case.expected.get("birth")
            from src.engines.bazi import BaziEngine  # 只读复用
            result = BaziEngine().calculate(
                b["year"], b["month"], b["day"], b["hour"], b["minute"],
                b.get("city") or "", b.get("gender") or "男")
            chain = deduce(result.bazi, engine_result=result, question=case.prose or "")
    except Exception as exc:
        return f"排盘/推演异常: {exc}"

    rules = [s.rule for s in chain.steps]
    if len(chain.steps) < 5:
        return f"推演步骤不足: {len(chain.steps)}"
    if not any(r.startswith("geju.") for r in rules):
        return "缺格局步骤"
    if not any(r.startswith("shishen.") for r in rules):
        return "缺十神步骤"
    if not any(r.startswith("qiongtong_table") for r in rules):
        return "缺调候用神步骤"
    duanyu_steps = [s for s in chain.steps if s.rule.startswith("断语要点")]
    if not duanyu_steps:
        return "缺断语要点步骤"
    if not duanyu_steps[-1].output:
        return "断语要点为空"

    try:
        report = compose_report(chain, case.prose or case.id, llm=llm,
                                evidences=None if use_real_retriever else [])
    except Exception as exc:
        return f"综合层异常: {exc}"
    if not report.analysis:
        return "综合输出为空"
    return None


def run_e2e(cases: list[Case], llm, use_real_retriever: bool = False) -> E2EReport:
    report = E2EReport()
    for case in cases:
        err = _run_one(case, llm, use_real_retriever)
        if err:
            report.failed += 1
            report.details.append(f"{case.id}: {err}")
        else:
            report.passed += 1
    return report
