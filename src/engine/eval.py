# src/engine/eval.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.engine.case_loader import Case


@dataclass
class UnitReport:
    rule_name: str
    passed: int = 0
    failed: int = 0
    details: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + self.failed


def run_unit(cases: list[Case], rule_fn: Callable[[list[str]], dict]) -> UnitReport:
    """单元级跑分：只跑 quality=='unit' 且 expected 非空的 case；
    expected 为空的键跳过，不相比较。"""
    report = UnitReport(rule_name=getattr(rule_fn, "__name__", "rule"))
    for case in cases:
        if case.quality != "unit" or not case.expected:
            continue
        actual = rule_fn(case.pills) or {}
        for key, want in case.expected.items():
            if want in ("", None, []):
                continue
            got = actual.get(key, "")
            ok = got == want or (isinstance(want, list) and got == want)
            if ok:
                report.passed += 1
            else:
                report.failed += 1
                report.details.append({
                    "case_id": case.id,
                    "key": key,
                    "expected": want,
                    "actual": got,
                    "source": f"{case.source}:{case.source_lines}",
                })
    return report


def format_report(report: UnitReport) -> str:
    lines = [f"# 单元跑分: {report.rule_name}", f"通过 {report.passed}/{report.total}", ""]
    for d in report.details:
        lines.append(f"- FAIL {d['case_id']} ({d['source']}) {d['key']}: "
                     f"期望 {d['expected']} 实际 {d['actual']}")
    return "\n".join(lines)
