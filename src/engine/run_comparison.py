# src/engine/run_comparison.py
"""对比跑批：同一案例双管线（检索式基线 vs 引擎式）逐条运行并存档。"""
from __future__ import annotations

import json
from pathlib import Path


class _FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        return type("AR", (), {"response": "（测试解读）", "tokens_used": 5, "model": "fake"})()


def _get_llm(use_real: bool):
    if not use_real:
        return _FakeLLM()
    from src.config import load_settings
    key = load_settings().claude_api_key
    if not key:
        return None
    from src.llm.client import FortuneLLM
    return FortuneLLM(api_key=key, model="deepseek-flash",
                      deep_model="deepseek-v4-pro", provider="deepseek")


def _run_engine(case: dict, llm) -> dict:
    from src.engine.deduction import deduce
    from src.engine.evidence import EvidenceProvider
    from src.engine.report import compose_report
    question = case.get("question", "")
    pills = case.get("pills")
    chain = None
    if pills:
        chain = deduce(pills, engine_result=None, question=question)
    else:
        b = case["birth"]
        from src.engines.bazi import BaziEngine
        result = BaziEngine().calculate(b["year"], b["month"], b["day"], b["hour"],
                                        b["minute"], b.get("city") or "", b.get("gender") or "男")
        chain = deduce(result.bazi, engine_result=result, question=question)
    out = {"chain_text": chain.to_text(), "citations_n": 0, "analysis": "", "tokens": 0, "model": ""}
    if llm is None:
        return {**out, "note": "LLM不可用(无api_key)，仅产出推演链与证据"}
    try:
        evidences = EvidenceProvider().gather(chain, question=question)
        report = compose_report(chain, question, llm=llm, evidences=evidences)
        out.update({"citations_n": len(report.citations), "analysis": report.analysis,
                    "tokens": report.tokens_used, "model": report.model,
                    "note": "OK" if evidences else "检索无命中(如实)"})
    except Exception as exc:
        out["note"] = f"综合层降级: {exc}"
    return out


def run_comparison(cases: list[dict], use_real_llm: bool = True) -> list[dict]:
    from src.engine.baseline import BaselinePipeline
    llm = _get_llm(use_real_llm)
    baseline = BaselinePipeline(llm=llm) if llm else None
    runs = []
    for case in cases:
        run = {"id": case["id"], "source": case["source"], "question": case.get("question", "")}
        if llm is None:
            run["baseline"] = {"analysis": "", "refs_n": 0, "tokens": 0, "model": "", "query": ""}
            run["baseline_note"] = "LLM不可用(无api_key)"
        elif "birth" not in case:
            # pills-only 案例（如滴天髓古籍命例）无公历出生信息，检索式基线无从排盘——如实降级
            run["baseline"] = {}
            run["baseline_note"] = "基线需要公历出生信息，pills-only 案例不适用"
        else:
            try:
                br = baseline.run(case["birth"], case.get("question", ""))
                run["baseline"] = {"analysis": br.analysis, "refs_n": len(br.refs),
                                   "tokens": br.tokens_used, "model": br.model, "query": br.query}
                run["baseline_note"] = "OK"
            except Exception as exc:
                run["baseline"] = {}
                run["baseline_note"] = f"基线降级: {exc}"
        run["engine"] = _run_engine(case, llm)
        run["note"] = run.get("baseline_note", "") + " / " + run["engine"].get("note", "")
        runs.append(run)
    Path("src/engine/out").mkdir(parents=True, exist_ok=True)
    with open("src/engine/out/comparison_runs.jsonl", "w", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return runs


if __name__ == "__main__":
    cases = json.load(open("src/engine/cases/comparison_cases.json", encoding="utf-8"))
    runs = run_comparison(cases, use_real_llm=True)
    for r in runs:
        print(r["id"], r["note"], "基线", len(r["baseline"].get("analysis", "")),
              "引擎", len(r["engine"].get("analysis", "")))
