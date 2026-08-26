# src/engine/build_report.py
"""对比报告生成：comparison_runs.jsonl → comparison_report.md（人读、不判准）。"""
from __future__ import annotations

import json
from pathlib import Path


def _case_section(run: dict) -> str:
    lines = [f"## {run['id']}（{run['source']}）", "", f"**问题**: {run.get('question', '')}", ""]
    b = run.get("baseline") or {}
    b_note = run.get("baseline_note", "")
    lines.append("### 检索式输出（基线）")
    lines.append("")
    if b.get("analysis"):
        lines.append(b["analysis"])
        lines.append("")
        lines.append(f"- 查询词: `{b.get('query', '')}`；引用 {b.get('refs_n', 0)} 条；tokens {b.get('tokens', 0)}")
    else:
        lines.append(f"（{b_note}）")
    lines.append("")
    e = run["engine"]
    lines.append("### 引擎式输出（推演链+证据+综合）")
    lines.append("")
    if e.get("analysis"):
        lines.append(e["analysis"])
        lines.append("")
        lines.append(f"- 引用 {e.get('citations_n', 0)} 条；tokens {e.get('tokens', 0)}；{e.get('note', '')}")
    else:
        lines.append(f"（{e.get('note', '')}）")
    lines.append("")
    lines.append("### 引擎推演链（可回放）")
    lines.append("")
    lines.append("```")
    lines.append(e.get("chain_text", "（无）"))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def build_report(runs_path: str) -> str:
    runs = [json.loads(l) for l in Path(runs_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    lines = [
        "# 易理推理内核 对比报告：检索式 vs 引擎式",
        "",
        "> 同一案例、同一 LLM，两条管线的输出并排呈现。**本报告不做「准」的判分**",
        "> （命理断语无标准答案，预测准确率不可验证）；观察维度：推理过程、依据出处、具体度、稳定性、覆盖面。",
        "",
        "## 汇总",
        "",
        "| 案例 | 检索式 | 引擎式 | 引擎推演链 |",
        "|---|---|---|---|",
    ]
    for r in runs:
        b = r.get("baseline") or {}
        e = r["engine"]
        lines.append(f"| {r['id']} | {len(b.get('analysis', ''))}字/{b.get('refs_n', 0)}引用 | "
                     f"{len(e.get('analysis', ''))}字/{e.get('citations_n', 0)}引用 | "
                     f"{len(e.get('chain_text', ''))}字/{e.get('chain_text', '').count('第')}步 |")
    lines.append("")
    for r in runs:
        lines.append(_case_section(r))
    lines += [
        "## 已知边界（诚实边界）",
        "",
        "- 时区语义：BaziEngine 的 city 参数未参与计算（海外命例按北京时排盘）",
        "- 出处元数据：检索 chunk 的 source 字段在库中存在缺口（部分显示为未知）",
        "- pills-only 案例（滴天髓命例）无公历出生信息，检索式基线不适用",
        "- LLM 冒烟按 DEEPSEEK_API_KEY / .env 配置门控，无 key 时仅产出推演链与证据",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    md = build_report("src/engine/out/comparison_runs.jsonl")
    out = Path("src/engine/out/comparison_report.md")
    out.write_text(md, encoding="utf-8")
    print(f"对比报告已生成 -> {out}（{len(md)} 字符）")
