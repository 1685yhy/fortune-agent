from src.engine.build_report import build_report


def test_report_contains_all_cases_and_sections():
    md = build_report("src/engine/out/comparison_runs.jsonl")
    assert "## " in md and md.count("## ") >= 5          # 每案例一节
    assert "检索式输出" in md and "引擎式输出" in md and "推演链" in md
    assert "诚实边界" in md or "不判准" in md
    assert "汇总" in md
