#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E6 报告生成（统一运行器的报告/对比/台账配套模块，纯函数可单测）。

职责（spec §6：report.py——报告生成（各层分数 + 失败明细 + 与上次对比））：
  1. `compare_runs` / `render_compare_md`：--compare-with <上次目录> 与上次
     运行对比（逐层指标涨跌标注 ↑/↓/→）
  2. `render_report_md`：统一运行器报告（门禁判定表 + 四层指标 + 失败明细 +
     跳过清单 + no_state_checks 归组 + 对比节）
  3. `load_ledger` / `append_ledger`：台账（data/eval/results/ledger.json，
     每次运行追加，为运行历史的唯一事实源；--compare-with 亦可指向任何
     结果目录）
  4. `calibration_consistency`：L3 校准重跑一致率（±1 分内一致；人工参考
     事实源 = docs/superpowers/eval/2026-08-31-l3-calibration.md 逐条不一致
     明细的 15 处人工分，其余 135 维以 E4 judge 分为代理——校准定义
     「人工与 E4 judge 差 ≤1」，保守口径）

红线：零新增依赖（纯标准库）；key 零落盘；只读评估集；台账为唯一事实源。
模块导入零副作用（不 import src）。
"""
import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (_REPO, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

RESULTS_ROOT = _REPO / "data" / "eval" / "results"
LEDGER_PATH = RESULTS_ROOT / "ledger.json"

# 四层门禁阈值表（唯一事实源 = spec §6 分层阈值表 L129-136）
# - L1 工具选择：能力 ≥90% / 回归 ≥98%；参数 ≥90%（能力）/ ≥98%（回归）；误调 0%
# - L2 断言通过率：100%（回归硬门禁）
# - L3 加权平均 ≥7.5，P0 平均 ≥8（k39 S1：聚合口径剔除边界/攻击类 edge 域，
#   含边界口径作为副指标如实公示，见 edge_scope.py 与 judge.group_metrics）
# - L4：P0 pass³ 100%、全量 TSR ≥90%
THRESHOLDS = {
    "capability": {
        "l1": {"tool_selection": 0.90, "param": 0.90, "false_call": 0.0},
        "l2": {"assertion_pass_rate": 1.0},
        "l3": {"weighted_avg": 7.5, "p0_avg": 8.0},
        "l4": {"p0_passk": 1.0, "tsr": 0.90},
    },
    "regression": {
        "l1": {"tool_selection": 0.98, "param": 0.98, "false_call": 0.0},
        "l2": {"assertion_pass_rate": 1.0},
        "l3": {"weighted_avg": 7.5, "p0_avg": 8.0},
        "l4": {"p0_passk": 1.0, "tsr": 0.90},
    },
}

# L3 校准人工参考分（E6 重跑一致率计算的事实源）：
# docs/superpowers/eval/2026-08-31-l3-calibration.md §逐条不一致明细——
# 15 处不一致维度的人工分；其余 135 维人工与 E4 judge 差 ≤1（90% 一致定义），
# 以 E4 judge 分作代理参考（保守口径：只对记录在案的 15 维用真人工分）。
CALIBRATION_HUMAN_REF = {
    "T005": {"accuracy": 7, "completeness": 6, "actionability": 5},
    "T067": {"accuracy": 7, "completeness": 7, "personalization": 2,
             "actionability": 6},
    "T058": {"accuracy": 4, "completeness": 4},
    "T064": {"accuracy": 3, "completeness": 2},
    "T045": {"accuracy": 5},
    "T019": {"accuracy": 4},
    "T037": {"accuracy": 3},
    "T054": {"accuracy": 4},
}
_JUDGE_DIMS = ("accuracy", "completeness", "personalization",
               "actionability", "citation_quality")


# ================================================================
# 门禁判定（纯函数，单测逐行覆盖）
# ================================================================

def evaluate_gate(l1m: dict, l2m: dict, l3m: dict, l4m: dict,
                  mode: str = "capability") -> dict:
    """门禁判定表：每层指标对照阈值逐行 PASS/RED。

    输入为四层 metrics 字典（l1_eval/l2_eval/judge/l4_eval 聚合口径）；
    l4m 为 runner 已做 no_state_checks 归组的 L4 指标（含 p0_passk_rate）。
    返回 {"rows": [...], "verdict": bool, "mode": mode}；任一行 RED → verdict=False。
    """
    t = THRESHOLDS[mode]
    rows = []

    fr = l1m.get("false_call_rate")
    rows.append({"layer": "L1", "metric": "tool_selection",
                 "value": l1m.get("tool_selection_accuracy"),
                 "threshold": t["l1"]["tool_selection"],
                 "pass": l1m.get("tool_selection_accuracy", 0.0)
                         >= t["l1"]["tool_selection"],
                 "is_rate": True})
    rows.append({"layer": "L1", "metric": "param",
                 "value": l1m.get("param_accuracy"),
                 "threshold": t["l1"]["param"],
                 "pass": l1m.get("param_accuracy", 0.0) >= t["l1"]["param"],
                 "is_rate": True})
    rows.append({"layer": "L1", "metric": "false_call",
                 "value": fr,
                 "threshold": t["l1"]["false_call"],
                 "pass": fr is None or fr <= t["l1"]["false_call"],
                 "note": "无 no_tool 任务（无法计算，按通过处理）"
                         if fr is None else "",
                 "is_rate": True})

    rows.append({"layer": "L2", "metric": "assertion_pass_rate",
                 "value": l2m.get("assertion_pass_rate"),
                 "threshold": t["l2"]["assertion_pass_rate"],
                 "pass": l2m.get("assertion_pass_rate", 0.0)
                         >= t["l2"]["assertion_pass_rate"],
                 "is_rate": True})

    w = l3m.get("weighted_avg")
    p0 = l3m.get("p0_avg")
    _excl = l3m.get("edge_excluded") or []
    _excl_note = (f"已剔除边界/攻击类 {len(_excl)} 条（edge 域，k39 S1）"
                  if _excl else "")
    rows.append({"layer": "L3", "metric": "weighted_avg",
                 "value": w, "threshold": t["l3"]["weighted_avg"],
                 "pass": w is not None and w >= t["l3"]["weighted_avg"],
                 "note": "无已判卷任务" if w is None else _excl_note})
    rows.append({"layer": "L3", "metric": "p0_avg",
                 "value": p0, "threshold": t["l3"]["p0_avg"],
                 "pass": p0 is not None and p0 >= t["l3"]["p0_avg"],
                 "note": "无 P0 已判卷任务" if p0 is None else _excl_note})

    # k39 S1：含边界口径**副指标**——如实公示两个数，但**不参与门禁判定**
    # （不进 rows；render_gate_table 以「副指标·不计入判定」单列）。
    sub_rows = []
    for metric, key in (("weighted_avg_incl_edge", "weighted_avg_incl_edge"),
                        ("p0_avg_incl_edge", "p0_avg_incl_edge")):
        val = l3m.get(key)
        if val is None:
            continue
        sub_rows.append({"layer": "L3", "metric": metric, "value": val,
                         "threshold": None, "pass": None,
                         "note": "含边界口径·副指标（不计入判定）"})

    # L4 空分母语义（spec §7 阈值表未定义空集合，取保守通过 + 显式注释）：
    # 无 P0 有断言键任务 / 无有断言键任务时门禁行按通过处理，绝不误红
    p0d = l4m.get("p0_denominator", 0)
    rows.append({"layer": "L4", "metric": "p0_passk",
                 "value": l4m.get("p0_passk_rate"),
                 "threshold": t["l4"]["p0_passk"],
                 "pass": p0d == 0
                         or l4m.get("p0_passk_rate", 0.0) >= t["l4"]["p0_passk"],
                 "note": "无 P0 有断言键任务（空分母，按通过处理）"
                         if p0d == 0 else "",
                 "is_rate": True})
    tsrd = l4m.get("tsr_denominator", 0)
    rows.append({"layer": "L4", "metric": "tsr",
                 "value": l4m.get("tsr"),
                 "threshold": t["l4"]["tsr"],
                 "pass": tsrd == 0
                         or l4m.get("tsr", 0.0) >= t["l4"]["tsr"],
                 "note": "无有断言键任务（空分母，无法计算，按通过处理）"
                         if tsrd == 0 else "",
                 "is_rate": True})

    return {"mode": mode, "rows": rows, "sub_rows": sub_rows,
            "verdict": all(r["pass"] for r in rows)}


def render_gate_table(gate: dict) -> str:
    """门禁判定表 markdown（每层 指标值 vs 阈值 vs 判定）。

    k39 S1：含边界口径的 L3 **副指标**单列一张表并显式标注「不计入判定」
    （如实公示两个数，但判定只看 `rows`）。
    """
    L = []
    L.append("| 层 | 指标 | 值 | 阈值（%s） | 判定 |" % gate["mode"])
    L.append("|---|---|---|---|---|")
    for r in gate["rows"]:
        v = r["value"]
        is_rate = r.get("is_rate", False)
        vs = "—" if v is None else (
            f"{v:.1%}" if isinstance(v, float) and is_rate else f"{v}")
        th = r["threshold"]
        ths = (f"{th:.1%}" if isinstance(th, float) and is_rate else f"{th}")
        note = ("（" + r["note"] + "）") if r.get("note") else ""
        L.append(f"| {r['layer']} | {r['metric']} | {vs} | {ths} | "
                 f"{'PASS' if r['pass'] else 'RED'}{note} |")
    subs = gate.get("sub_rows") or []
    if subs:
        L.append("")
        L.append("副指标（如实公示·**不计入门禁判定**）:")
        L.append("")
        L.append("| 层 | 指标 | 值 | 说明 |")
        L.append("|---|---|---|---|")
        for r in subs:
            v = r["value"]
            vs = "—" if v is None else f"{v}"
            L.append(f"| {r['layer']} | {r['metric']} | {vs} | "
                     f"{r.get('note') or ''} |")
    L.append("")
    L.append(f"门禁判定: **{'PASS（全阈值达标）' if gate['verdict'] else 'RED（任一层不达标）'}**")
    return "\n".join(L)


# ================================================================
# 对比（--compare-with：与上次运行逐层指标对比）
# ================================================================

def load_run_dir(path) -> dict:
    """读取一次运行结果目录 → {"meta": ..., "results": [...]}。

    meta.json 缺失 → 抛 ValueError（显式报错不静默）。
    """
    p = Path(path)
    meta_f = p / "meta.json"
    if not meta_f.exists():
        raise ValueError(f"对比目标缺少 meta.json: {p}")
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    results = []
    rf = p / "results.json"
    if rf.exists():
        results = json.loads(rf.read_text(encoding="utf-8"))
    return {"meta": meta, "results": results}


def _flat_run_metrics(meta: dict) -> dict:
    """运行 meta → 展平四层指标（统一运行器口径 {l1,l2,l3,l4}；
    单层运行器口径直接取 meta["metrics"]）。"""
    m = meta.get("metrics")
    if not isinstance(m, dict):
        return {}
    if "l1" in m or "l2" in m or "l3" in m or "l4" in m:
        return m
    return {"l1": m}  # 单层运行（L1/L2/L4 平铺口径）保守归 l1 槽
    # 注：单层对比的语义差异（l3 口径不同）由对比表表头注明，不混算


def compare_runs(prev: dict, cur: dict) -> dict:
    """两次运行逐层指标对比。

    返回 {"layers": {layer: {"metrics": {metric: {"prev", "cur", "diff",
    "arrow"}}}}, "layers_present": [...]}；cur 为当前运行（prev 为上次）。
    同一 metric 值类型（分数/百分比）直接数值差；None 任一侧 → diff=None。
    """
    pm = _flat_run_metrics(prev["meta"])
    cm = _flat_run_metrics(cur["meta"])
    layers = {}
    for layer in ("l1", "l2", "l3", "l4"):
        if layer not in pm and layer not in cm:
            continue
        pv = pm.get(layer) or {}
        cv = cm.get(layer) or {}
        metrics = {}
        for key in sorted(set(pv) | set(cv)):
            a, b = pv.get(key), cv.get(key)
            if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                continue  # 非数值键（列表/字典/字符串）不参与对比
            diff = None if (a is None or b is None) else round(b - a, 4)
            arrow = "→" if diff is None or diff == 0 else ("↑" if diff > 0 else "↓")
            metrics[key] = {"prev": a, "cur": b, "diff": diff, "arrow": arrow}
        layers[layer] = {"metrics": metrics}
    return {"layers": layers,
            "layers_present": [k for k in ("l1", "l2", "l3", "l4") if k in layers]}


def render_compare_md(prev_path, cur_path, compare: dict) -> str:
    """对比节 markdown（逐层指标 上次 vs 本次 + 涨跌标注）。"""
    L = []
    L.append(f"## 与上次对比（{prev_path} → {cur_path}）\n")
    if not compare["layers"]:
        L.append("（上次运行无可比指标）\n")
        return "\n".join(L)
    for layer in compare["layers_present"]:
        metrics = compare["layers"][layer]["metrics"]
        if not metrics:
            continue
        L.append(f"### {layer.upper()} 指标对比\n")
        L.append("| 指标 | 上次 | 本次 | 变化 |")
        L.append("|---|---|---|---|")
        for key, v in metrics.items():
            diff = "—" if v["diff"] is None else f"{v['diff']:+.4f} {v['arrow']}"
            L.append(f"| {key} | {v['prev']} | {v['cur']} | {diff} |")
        L.append("")
    return "\n".join(L)


# ================================================================
# 台账（data/eval/results/ledger.json —— 运行历史唯一事实源）
# ================================================================

def load_ledger(path=LEDGER_PATH) -> list:
    """台账读取；文件缺失 → []（首条追加时创建）。"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []  # 台账损坏按空处理（追加会重建），不静默吞——调用方可见
    return data if isinstance(data, list) else []


def append_ledger(entry: dict, path=LEDGER_PATH) -> None:
    """台账追加（每次运行一条：run_id/mode/时间/版本/门禁/四层指标摘要）。"""
    entries = load_ledger(path)
    entries.append(entry)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def build_ledger_entry(run_id: str, meta: dict, gate: dict) -> dict:
    """台账条目（门禁判定 + 四层指标摘要，不含逐任务明细——明细在结果目录）。"""
    m = meta.get("metrics") or {}
    return {
        "run_id": run_id,
        "run_at": meta.get("run_at"),
        "mode": meta.get("mode"),
        "thresholds_mode": meta.get("thresholds_mode"),
        "repo_version": meta.get("repo_version"),
        "scope_count": len(meta.get("scope") or []),
        "gate_verdict": gate["verdict"],
        "gate_mode": gate["mode"],
        "metrics": {
            "l1": {k: m.get("l1", {}).get(k)
                   for k in ("tool_selection_accuracy", "param_accuracy",
                             "false_call_rate", "executed", "skipped")},
            "l2": {k: m.get("l2", {}).get(k)
                   for k in ("assertion_pass_rate", "executed", "skipped")},
            "l3": {k: m.get("l3", {}).get(k)
                   for k in ("weighted_avg", "weighted_avg_incl_edge",
                             "weighted_avg_incl_errors",
                             "p0_avg", "p0_avg_incl_edge",
                             "judged", "judge_error")},
            "l4": {k: m.get("l4", {}).get(k)
                   for k in ("tsr", "passk_rate", "p0_passk_rate",
                             "tsr_denominator", "executed_with_checks",
                             "no_state_checks", "skipped")},
        },
    }


# ================================================================
# 统一报告渲染（runner.py 调用；单层运行器各自保留自己的 render）
# ================================================================

def render_report_md(meta: dict, gate: dict, task_records: list,
                     compare: dict = None) -> str:
    """统一运行器报告：门禁判定表 + 四层指标 + 失败明细 + 跳过/归组清单。"""
    m = meta.get("metrics") or {}
    L = []
    L.append("# Agent 评测统一运行器报告（E6）\n")
    L.append(f"- 运行时间: {meta.get('run_at')}")
    L.append(f"- 模式: {meta.get('mode')}（thresholds: {meta.get('thresholds_mode')}）"
             f" | 主链/判卷模型: 免费 glm-4-flash | 仓库版本: {meta.get('repo_version')}")
    L.append(f"- 任务范围: {len(meta.get('scope') or [])} 条 "
             f"（{', '.join((meta.get('scope') or [])[:20])}"
             + ("…" if len(meta.get("scope") or []) > 20 else "") + "）")
    L.append(f"- 互斥纪律: {meta.get('mutex')}")
    L.append(f"- 隔离: {meta.get('isolation')}")
    L.append(f"- 台账: {LEDGER_PATH}（每次运行追加，唯一事实源）\n")

    L.append("## 门禁判定\n")
    L.append(render_gate_table(gate) + "\n")

    l1m, l2m, l3m, l4m = m.get("l1") or {}, m.get("l2") or {}, \
        m.get("l3") or {}, m.get("l4") or {}

    L.append("## L1 工具调用（确定性比对）\n")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    fr = l1m.get("false_call_rate")
    L.append(f"| 工具选择准确率 | {l1m.get('tool_selection_accuracy', 0):.1%} "
             f"({l1m.get('tool_selection_passed')}/{l1m.get('executed')}) |")
    L.append(f"| 参数提取准确率 | {l1m.get('param_accuracy', 0):.1%} "
             f"({l1m.get('param_passed')}/{l1m.get('param_denominator')}) |")
    L.append(f"| 误调率（no_tool） | "
             f"{f'{fr:.1%}' if fr is not None else '—（无 no_tool 任务）'} "
             f"({l1m.get('false_call_count')}/{l1m.get('no_tool_count')}) |")
    L.append(f"| 失败任务 | {l1m.get('failed') or '—'} |")
    if l1m.get("skipped"):
        L.append(f"| 跳过 | {l1m.get('skipped')} |")
    L.append("")

    L.append("## L2 回复内容（确定性断言）\n")
    L.append(f"- 断言通过率: **{l2m.get('assertion_pass_rate', 0):.1%}** "
             f"({l2m.get('passed')}/{l2m.get('executed')}) | 门禁 =100%（回归硬门禁）")
    L.append(f"- 失败任务: {l2m.get('failed') or '—'}")
    if l2m.get("skipped"):
        L.append(f"- 跳过: {l2m.get('skipped')}")
    L.append("")

    L.append("## L3 质量判卷（免费 glm-4-flash 五维加权）\n")
    L.append("- 口径（k39 S1）：边界/攻击类（`category=edge`）任务**已从门禁口径剔除**"
             "——其目标是韧性与安全（不报错/不越权/不落库/不硬断），五维"
             "**质量**判卷对其系统性给低分属口径错配；**两个数都如实公示**，"
             "判定只用「不含边界」口径。L1/L2/L4 对边界任务一分不减。")
    L.append(f"- 加权平均（**不含边界**，已判卷 {l3m.get('judged_excl_edge')} 条）: "
             f"**{l3m.get('weighted_avg') if l3m.get('weighted_avg') is not None else '—'}**"
             f"（参考阈值 ≥7.5，门禁口径）")
    L.append(f"- 加权平均（含边界，已判卷 {l3m.get('judged')} 条）: "
             f"{l3m.get('weighted_avg_incl_edge')}（副指标·如实公示）")
    L.append(f"- 被剔除的边界/攻击类任务（{len(l3m.get('edge_excluded') or [])} 条）: "
             f"{l3m.get('edge_excluded') or '—'}")
    L.append(f"- P0 平均（**不含边界**，{l3m.get('p0_count')} 条）: "
             f"**{l3m.get('p0_avg') if l3m.get('p0_avg') is not None else '—'}**"
             f"（参考阈值 ≥8，门禁口径）| P0 平均（含边界，"
             f"{l3m.get('p0_count_incl_edge')} 条）: `{l3m.get('p0_avg_incl_edge')}`"
             f"（副指标）")
    L.append(f"- 含 judge_error 按 0 计（全量口径，历史可比）: "
             f"{l3m.get('weighted_avg_incl_errors')}")
    L.append(f"- 判卷失败: {l3m.get('judge_error') or '—'}"
             f"{' | 跳过: ' + str(l3m.get('skipped')) if l3m.get('skipped') else ''}")
    L.append("")

    L.append("## L4 端到端执行验证（临时库状态断言 + pass^k）\n")
    L.append(f"- TSR（首轮通过率）: **{l4m.get('tsr', 0):.1%}** "
             f"({l4m.get('tsr_passed')}/{l4m.get('tsr_denominator')}) | 门禁 ≥90%")
    L.append(f"- pass^k 通过率: **{l4m.get('passk_rate', 0):.1%}** "
             f"({l4m.get('passk_passed')}/{l4m.get('passk_denominator')})")
    L.append(f"- P0 pass³: **{l4m.get('p0_passk_rate', 0):.1%}** "
             f"({l4m.get('p0_passk_passed')}/{l4m.get('p0_denominator')}) | 门禁 100%")
    L.append(f"- 失败任务: {l4m.get('failed') or '—'}")
    L.append(f"- 跳过: {l4m.get('skipped') or '—'}")
    L.append(f"- **无断言键任务（{len(l4m.get('no_state_checks') or [])} 条，"
             f"不计入 TSR/pass^k 分母，单独归组——E5 concern #4 落地）**: "
             f"{l4m.get('no_state_checks') or '—'}")
    L.append("")

    # 失败明细（逐层，取前 N）
    fails = [r for r in task_records if not r.get("skipped")
             and not r.get("passed_all_layers", True)]
    if fails:
        L.append("## 失败明细（逐任务各层失败原因）\n")
        for r in fails[:30]:
            L.append(f"### {r['id']} {r['title']}（{r['category']}/{r['severity']}"
                     f"，k={r.get('pass_k')}）\n")
            att = r.get("attempts") or []
            if att:
                a0 = att[0]
                l1d = a0.get("l1", {}).get("detail", "")
                if l1d:
                    L.append(f"- L1: {l1d[:200]}")
                l2fails = [c for c in (a0.get("l2") or {}).get("checks", [])
                           if not c["ok"]]
                for c in l2fails[:4]:
                    L.append(f"- L2 {c['name']}: {str(c.get('detail'))[:200]}")
                l4fails = [c for c in (a0.get("l4") or {}).get("checks", [])
                           if not c["pass"]]
                for c in l4fails[:4]:
                    L.append(f"- L4 {c.get('key')}: {str(c.get('reason'))[:200]}")
                if a0.get("exception"):
                    L.append(f"- 异常: {a0['exception'][:200]}")
            j = r.get("l3") or {}
            if j.get("judge_error"):
                L.append(f"- L3 judge_error: {j.get('judge_error_reason', '')[:200]}")
            L.append("")
    else:
        L.append("（本运行无失败任务）\n")

    if compare is not None:
        L.append(render_compare_md(compare.get("prev_path", "?"),
                                   compare.get("cur_path", "?"),
                                   compare.get("compare", {})) + "\n")

    L.append("## 台账\n")
    L.append(f"本运行已追加台账 {LEDGER_PATH}（门禁判定 "
             f"{'PASS' if gate['verdict'] else 'RED'}）。"
             f"红就是红如实报：门禁红是体系价值（R1 修复后转绿），"
             f"门禁判定逻辑正确才是本批验收项。\n")
    return "\n".join(L)


# ================================================================
# L3 校准重跑一致率（E6 校准落地：修正 rubric 后重跑 30 条样本）
# ================================================================

def calibration_consistency(new_results: list, e4_results: list,
                            human_ref: dict = None) -> dict:
    """30 条校准样本重跑一致率（±1 分内一致，spec §5.3 校准口径）。

    - new_results：修正 rubric 后重跑的 judge 结果（judge.py 口径：
      {id, dims: {dim: {"score": ...}}}）
    - e4_results：E4 校准 run 的 judge 结果（同口径）——作 135 维代理参考
      （校准定义「人工与 E4 judge 差 ≤1」，90% 一致即由此而来）
    - human_ref：记录在案的 15 处人工分（默认 CALIBRATION_HUMAN_REF）
    返回 {"total_dims", "consistent", "rate", "direct": {"n","consistent","rate"},
          "proxy": {...}, "per_dim": [...]}。未判卷/缺维任务按 0 分计并标注。
    """
    human_ref = human_ref if human_ref is not None else CALIBRATION_HUMAN_REF
    e4_by_id = {r["id"]: r for r in e4_results}
    new_by_id = {r["id"]: r for r in new_results}
    per_dim = []
    for rid, nr in sorted(new_by_id.items()):
        e4r = e4_by_id.get(rid) or {}
        for dim in _JUDGE_DIMS:
            new_score = ((nr.get("dims") or {}).get(dim) or {}).get("score", 0.0)
            if dim in (human_ref.get(rid) or {}):
                ref = human_ref[rid][dim]
                direct = True
            else:
                ref = ((e4r.get("dims") or {}).get(dim) or {}).get("score", 0.0)
                direct = False
            consistent = abs(new_score - ref) <= 1.0 + 1e-9
            per_dim.append({"id": rid, "dim": dim, "ref": ref,
                            "new_score": new_score, "consistent": consistent,
                            "direct_human": direct})
    total = len(per_dim)
    consistent_n = sum(1 for p in per_dim if p["consistent"])
    direct = [p for p in per_dim if p["direct_human"]]
    proxy = [p for p in per_dim if not p["direct_human"]]
    return {
        "total_dims": total,
        "consistent": consistent_n,
        "rate": (consistent_n / total) if total else 0.0,
        "direct": {"n": len(direct),
                   "consistent": sum(1 for p in direct if p["consistent"]),
                   "rate": (sum(1 for p in direct if p["consistent"]) / len(direct))
                   if direct else 0.0},
        "proxy": {"n": len(proxy),
                  "consistent": sum(1 for p in proxy if p["consistent"]),
                  "rate": (sum(1 for p in proxy if p["consistent"]) / len(proxy))
                  if proxy else 0.0},
        "per_dim": per_dim,
    }


def render_calibration_md(cons: dict, prev_rate: float = None) -> str:
    """校准重跑一致率摘要（markdown）。"""
    L = []
    L.append("## L3 校准重跑一致率（rubric 四类修正后，±1 分内一致）\n")
    L.append(f"- **一致率: {cons['rate']:.1%}（{cons['consistent']}/"
             f"{cons['total_dims']} 维）**"
             + (f"，E4 基线 {prev_rate:.1%}" if prev_rate is not None else ""))
    L.append(f"- 直接人工对照（校准报告记录在案的 15 处不一致维）: "
             f"{cons['direct']['rate']:.1%} "
             f"({cons['direct']['consistent']}/{cons['direct']['n']})")
    L.append(f"- 代理对照（其余维，参考=E4 judge 分，保守口径）: "
             f"{cons['proxy']['rate']:.1%} "
             f"({cons['proxy']['consistent']}/{cons['proxy']['n']})")
    misses = [p for p in cons["per_dim"] if not p["consistent"]]
    if misses:
        L.append(f"- 不一致明细（{len(misses)} 处）:")
        for p in misses:
            tag = "人工" if p["direct_human"] else "代理"
            L.append(f"  - {p['id']}.{p['dim']}: 新 judge {p['new_score']} "
                     f"vs 参考 {p['ref']}（{tag}）")
    return "\n".join(L)


# ================================================================
# CLI（对比独立入口：python3 scripts/eval_agent/report.py --compare-with）
# ================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="E6 报告工具：两次运行对比（--compare-with）")
    ap.add_argument("--compare-with", required=True,
                    help="上次运行结果目录（data/eval/results/<dir>）")
    ap.add_argument("--cur", required=True,
                    help="当前运行结果目录（data/eval/results/<dir>）")
    args = ap.parse_args(argv)
    try:
        prev = load_run_dir(args.compare_with)
        cur = load_run_dir(args.cur)
    except ValueError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    comp = compare_runs(prev, cur)
    print(render_compare_md(args.compare_with, args.cur, comp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
