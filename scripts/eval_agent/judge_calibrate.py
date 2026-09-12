#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k39 S1 · L3 判卷校准：用**更强的判卷模型**重判既有 run 的回复，对比分数差。

背景（E6 归因 §3.2 + L3 校准文档）：L3 门禁现用**免费 glm-4-flash** 判卷，
阈值 7.5（全量加权）/ 8.0（P0）。判卷模型的偏差有多大、这两个阈值在免费
判卷下是否可区分，之前只有 30 条样本的**人工 vs judge** 一致率（90%，见
docs/superpowers/eval/2026-08-31-l3-calibration.md），缺**强模型 vs 免费模型**
同输入的直接对照。本脚本补这一格。

用法（**手动运行**，不进任何自动流水线）：

  # 只看结构分析（零 LLM 调用、零成本、无需 key）
  python3 scripts/eval_agent/judge_calibrate.py --run <run_dir> --dry-run

  # 真跑：强模型重判（需要 key，可能产生费用）
  python3 scripts/eval_agent/judge_calibrate.py --run <run_dir> \
      --judge-model glm-4-plus --api-key "$ZHIPU_API_KEY"

  # 指定样本 / 加大样本量
  python3 scripts/eval_agent/judge_calibrate.py --run <run_dir> \
      --tasks T001,T019,T083 --sample 30

输入：`<run_dir>` = 统一运行器的结果目录（含 `results.json`；`meta.json`
可选）。脚本**只读**该目录，绝不写回、绝不改台账、绝不重启服务。

回复来源（两个都已被落盘瘦身，脚本显式记录用了哪个，结论里标注该口径）：
  1. `record.l3.replies`            —— 判卷输入截断到 200 字（runner.py:361）
  2. `record.attempts[0].l2.replies_preview` —— 截断到 2000 字
脚本取**较长**的一个（更接近原判卷输入），并逐条记录 `replies_source`。

输出（`--out`，默认 data/eval/results/judge-calibration-<时间戳>/）：
  - `calibration.json`：逐条 free/strong 五维分、总分、差、阈值跨越；
  - `meta.json`：样本来源、模型、口径、汇总统计（方差/一致率/等价性判定）；
  - `report.md`：人读摘要。

退出码：0 = 跑完；2 = 前置失败（run 目录/样本数/key 缺失）；
        3 = 判卷失败条数 > 0（judge_error，结果仍落盘）。

红线：data/eval/agent_tasks.jsonl 只读；key 只读环境变量/参数，零落盘；
不调用生产模型（判卷模型由 `--judge-model` 显式指定，默认强模型），
主链零调用（本脚本只判卷，不跑主链）。
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (_REPO, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import judge  # noqa: E402  — 复用判卷 prompt/解析/加权（唯一事实源）
import report  # noqa: E402
import edge_scope  # noqa: E402  — k39 S1 边界判据（本脚本同口径）

TASKS_PATH = judge.TASKS_PATH
RESULTS_ROOT = judge.RESULTS_ROOT

# 强判卷模型默认值：glm-4-plus（与主链同档的旗舰档，强于免费 glm-4-flash）。
# 换模型只改这里/--judge-model，不改判卷 prompt（保证只比"模型"这一个变量）。
STRONG_JUDGE_MODEL = "glm-4-plus"
# 门禁阈值（与 report.THRESHOLDS 对齐，只读不改）
GATE_THRESHOLDS = (7.5, 8.0)
MIN_SAMPLES = 20  # brief 硬要求：至少抽样 20 条


# ================================================================
# 纯函数（单测直接 import，零网络零副作用）
# ================================================================

def select_sample_ids(records: list, sample: int = MIN_SAMPLES) -> list:
    """确定性抽样：先按域各取最小 id 一条（尽域覆盖），余量按 id 升序补齐。

    只从「有回复可重判」的记录里选（skipped / 无回复的没有判卷输入）。
    与 judge.select_calibration_ids 同精神（P0/P1/P2 分层版），此处按
    **域**分层以覆盖所有类别（含 edge——校准要能看到边界任务的判卷差异）。
    """
    pool = [r for r in records
            if not r.get("skipped") and _pick_replies(r)[0]]
    by_cat = {}
    for r in pool:
        by_cat.setdefault(r.get("category"), []).append(r.get("id"))
    picked = [min(v) for _, v in sorted(by_cat.items())]
    rest = sorted(r.get("id") for r in pool if r.get("id") not in picked)
    ids = picked + rest
    return ids[:max(int(sample), 1)]


def _pick_replies(record: dict) -> tuple:
    """记录 → (回复列表, 来源标签)。取较长的一路（更接近原判卷输入）。"""
    l3_replies = [r or "" for r in ((record.get("l3") or {}).get("replies") or [])]
    attempts = record.get("attempts") or []
    prev = [r or "" for r in (((attempts[0] if attempts else {})
                               .get("l2") or {}).get("replies_preview") or [])]
    l3_len = sum(len(r) for r in l3_replies)
    prev_len = sum(len(r) for r in prev)
    if prev and prev_len > l3_len:
        return prev, "attempts[0].l2.replies_preview(2000字截断)"
    if l3_replies:
        return l3_replies, "l3.replies(200字截断)"
    return [], "无"


def delta_stats(pairs: list) -> dict:
    """[(free, strong)] → 差值统计（均值/中位/标准差/一致率/阈值跨越）。

    一致率口径沿用 L3 校准文档的 ±1 分（同量纲加权总分）。
    """
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs:
        return {"n": 0}
    deltas = [round(b - a, 4) for a, b in pairs]
    return {
        "n": len(pairs),
        "free_mean": round(statistics.fmean([a for a, _ in pairs]), 4),
        "strong_mean": round(statistics.fmean([b for _, b in pairs]), 4),
        "delta_mean": round(statistics.fmean(deltas), 4),
        "delta_median": round(statistics.median(deltas), 4),
        "delta_std": round(statistics.pstdev(deltas), 4) if len(deltas) > 1 else 0.0,
        "delta_min": min(deltas),
        "delta_max": max(deltas),
        "agree_within_1": sum(1 for d in deltas if abs(d) <= 1.0),
        "agree_rate_within_1": round(
            sum(1 for d in deltas if abs(d) <= 1.0) / len(deltas), 4),
        # 阈值跨越：free 侧不达标而 strong 侧达标（或反向）
        "crossings": {str(t): _crossings(pairs, t) for t in GATE_THRESHOLDS},
    }


def _crossings(pairs: list, thr: float) -> dict:
    """阈值跨越计数：free/strong 两侧分别达标数与"翻转"数（口径敏感度）。"""
    free_pass = sum(1 for a, _ in pairs if a >= thr)
    strong_pass = sum(1 for _, b in pairs if b >= thr)
    flips = sum(1 for a, b in pairs if (a >= thr) != (b >= thr))
    return {"free_pass": free_pass, "strong_pass": strong_pass,
            "flips": flips, "n": len(pairs)}


def threshold_equivalence(stats: dict, thresholds=GATE_THRESHOLDS) -> dict:
    """7.5 / 8.0 在免费判卷下是否可区分（判卷噪声 vs 阈值间距）。

    判据（可判定）：阈值间距 = 8.0 - 7.5 = 0.5。若模型间差的**标准差**（或
    平均绝对偏差）不小于间距，则同一份回复的分数抖动即可跨越两个阈值——
    两个阈值在统计上**不可区分**（判 red/green 取决于判卷噪声而非作品质量）。
    """
    gap = round(thresholds[1] - thresholds[0], 4)
    if not stats or not stats.get("n"):
        return {"gap": gap, "verdict": "无法判定", "reason": "无样本"}
    std = float(stats.get("delta_std") or 0.0)
    mean_abs = (round(sum(abs(v) for v in stats.get("_abs_deltas", []))
                      / len(stats["_abs_deltas"]), 4)
                if stats.get("_abs_deltas") else None)
    if std >= gap:
        verdict, reason = "不可区分", (
            f"模型间差的标准差 {std} ≥ 阈值间距 {gap}：同一份回复的分数抖动"
            f"即可跨过 7.5 与 8.0，两个阈值在免费判卷下不可区分")
    elif mean_abs is not None and mean_abs >= gap:
        verdict, reason = "不可区分", (
            f"平均绝对偏差 {mean_abs} ≥ 阈值间距 {gap}：两个阈值在免费判卷下"
            f"不可区分")
    else:
        verdict, reason = "可区分（弱）", (
            f"模型间差的标准差 {std} < 阈值间距 {gap}，但样本量 {stats['n']} 条"
            f"——按 L3 校准口径（±1 一致）仍属同一判卷档，结论为弱")
    return {"gap": gap, "delta_std": std, "mean_abs_delta": mean_abs,
            "verdict": verdict, "reason": reason}


def lattice_analysis(scores: list, thresholds=GATE_THRESHOLDS) -> dict:
    """免费判卷的实际取值分布分析（**零成本**，回答阈值是否可区分）。

    五维分限整数/半整数（judge.py rubric）+ 权重 (0.30/0.25/0.20/0.15/0.10)
    → 加权总分落在 0.05 的格点上。本函数给出：实际观测到的取值集合、
    阈值可达性、严格落在两阈值之间的观测值，以及按阈值分桶的计数。
    """
    nums = [s for s in scores if isinstance(s, (int, float))]
    vals = sorted({round(s, 4) for s in nums})
    lo, hi = thresholds
    n = len(nums)
    return {
        "n": n,
        "observed_values": vals,
        "observed_unique": len(vals),
        "min": vals[0] if vals else None,
        "max": vals[-1] if vals else None,
        "threshold_reachable": {str(t): any(abs(v - t) < 1e-9 for v in vals)
                                for t in thresholds},
        # 严格落在 (7.5, 8.0) 的观测值：存在即说明格点分辨率足以区分两阈值
        "values_between_thresholds": [v for v in vals if lo < v < hi],
        "count_ge_low": sum(1 for s in nums if s >= lo),
        "count_ge_high": sum(1 for s in nums if s >= hi),
        "count_in_gap": sum(1 for s in nums if lo < s < hi),
        "count_lt_low": sum(1 for s in nums if s < lo),
        "grid_step": 0.05,
        "note": "半整数五维分 × 权重 → 0.05 格点；7.5=150 格、8.0=160 格，"
                "两阈值本身均可达（不是同一格）",
    }


def empirical_equivalence(lattice: dict, thresholds=GATE_THRESHOLDS) -> dict:
    """**零成本**经验判据：免费判卷实际产出里两阈值是否可区分。

    判据（可判定，只用免费判卷的既有输出）：
      - 高分侧观测数：`count_ge_high`（≥8.0）与 `count_in_gap`（7.5~8.0 之间）
        都 < 2 → 免费判卷在实践中几乎不产出 8.0 以上的分，也不在 0.5 间隔里
        落点 → 7.5 与 8.0 不是两个**可分辨**的档位（8.0 近似"不可达门槛"）。
      - 同时报告高分侧实际观测数，便于人读判据强度。
    没有强模型对照时，这是唯一可零成本给出的结论，故显式标注为经验判据。
    """
    lo, hi = thresholds
    if not lattice.get("n"):
        return {"gap": round(hi - lo, 4), "verdict": "无法判定", "reason": "无样本"}
    gap = round(hi - lo, 4)
    n = lattice["n"]
    ge_high = lattice.get("count_ge_high", 0)
    ge_low = lattice.get("count_ge_low", 0)
    in_gap = lattice.get("count_in_gap", 0)
    hi_rate = ge_high / n
    base = (f"免费判卷 {n} 条：≥{lo} 仅 {ge_low} 条（{ge_low / n:.1%}），"
            f"其中 ({lo}, {hi}) 区间 {in_gap} 条、≥{hi} 仅 {ge_high} 条"
            f"（{hi_rate:.1%}，最高分 {lattice.get('max')}）")
    if hi_rate < 0.05:
        verdict = f"不可区分（{hi} 在实践中近似不可达）"
        reason = (base + f"——0.5 的阈值间距不是两个都在使用的档位：{lo} 只是"
                  f"勉强有落点，{hi} 几乎无落点。此时「P0 平均 ≥8」的判定"
                  f"实际由极少数样本+判卷噪声决定，与 ≥7.5 不是可分辨的两档。"
                  f"（强模型对照未跑，此项为零成本经验判据）")
    else:
        verdict = "存在可分辨迹象"
        reason = (base + "——高分侧有实际落点，两阈值至少在取值空间上可分辨；"
                  "噪声是否吞掉 0.5 间距仍需强模型对照")
    return {"gap": gap, "n": n, "count_ge_low": ge_low,
            "count_ge_high": ge_high, "count_in_gap": in_gap,
            "count_lt_low": lattice.get("count_lt_low"),
            "verdict": verdict, "reason": reason}


# ================================================================
# 强模型重判（唯一有网络/费用的路径）
# ================================================================

class _StrongJudge:
    """临时把 judge.call_judge_model 换成强模型（复用判卷 prompt/解析/兜底）。

    只换模型名这一件事：prompt、temperature、max_tokens、三级 JSON 兜底、
    加权汇总全部沿用 judge.py 的唯一事实源——保证对照的变量只有"模型"。
    """

    def __init__(self, model: str, max_tokens: int = judge.JUDGE_MAX_TOKENS,
                 temperature: float = judge.JUDGE_TEMPERATURE,
                 timeout: float = 120.0):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self._orig = None

    def __enter__(self):
        self._orig = judge.call_judge_model

        def _call(system_prompt, user_prompt, api_key):
            return judge._call_glm(
                api_key,
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user_prompt}],
                model=self.model, max_tokens=self.max_tokens,
                temperature=self.temperature, timeout=self.timeout)

        judge.call_judge_model = _call
        return self

    def __exit__(self, *exc):
        judge.call_judge_model = self._orig
        return False


def rejudge(task: dict, replies: list, api_key: str, model: str) -> dict:
    """强模型重判单条（judge.judge_task 原样复用，只换模型）。"""
    with _StrongJudge(model):
        return judge.judge_task(task, replies, api_key)


# ================================================================
# 主流程
# ================================================================

def _load_tasks() -> dict:
    if not TASKS_PATH.exists():
        return {}
    out = {}
    for line in TASKS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        t = json.loads(line)
        out[t["id"]] = t
    return out


def build_report_md(meta: dict, rows: list, stats: dict, lattice: dict,
                    equiv: dict) -> str:
    L = []
    L.append("# k39 S1 · L3 判卷校准（强模型 vs 免费判卷）\n")
    L.append(f"- 运行时间: {meta['run_at']}")
    L.append(f"- 输入 run: `{meta['run_dir']}`（只读）")
    L.append(f"- 判卷模型: 免费 `{meta['free_model']}` vs 强模型 "
             f"`{meta['strong_model']}`"
             + ("（**--dry-run：未调用强模型**）" if meta["dry_run"] else ""))
    L.append(f"- 样本: {len(rows)} 条（来源口径：{meta['replies_source']}）")
    L.append(f"- 回复落盘口径: {meta['replies_note']}\n")

    if not meta["dry_run"]:
        L.append("## 分差统计（strong − free）\n")
        L.append("| 指标 | 值 |")
        L.append("|---|---|")
        for k in ("n", "free_mean", "strong_mean", "delta_mean",
                  "delta_median", "delta_std", "delta_min", "delta_max",
                  "agree_rate_within_1"):
            L.append(f"| {k} | {stats.get(k)} |")
        L.append("")
        L.append("### 阈值跨越（7.5 / 8.0）\n")
        L.append("| 阈值 | free 达标 | strong 达标 | 翻转 | 样本 |")
        L.append("|---|---|---|---|---|")
        for t, c in (stats.get("crossings") or {}).items():
            L.append(f"| {t} | {c['free_pass']} | {c['strong_pass']} | "
                     f"{c['flips']} | {c['n']} |")
        L.append("")
    else:
        L.append("## 分差统计\n\n**--dry-run：未调用强模型，分差统计缺失**"
                 "（需 key；见 meta.blocked_reason）\n")

    L.append("## 阈值等价性（7.5 vs 8.0 在免费判卷下是否可区分）\n")
    L.append(f"- 判定: **{equiv.get('verdict')}**")
    L.append(f"- 判据来源: {equiv.get('basis')}")
    L.append(f"- 依据: {equiv.get('reason')}")
    L.append("")
    L.append("## 免费判卷取值分布（零成本分析，**整个 run**）\n")
    L.append(f"- 已判卷 {lattice['n']} 条，观测到 {lattice['observed_unique']} "
             f"个不同总分，范围 [{lattice['min']}, {lattice['max']}]，"
             f"格点步长 {lattice['grid_step']}")
    L.append(f"- 阈值可达性: {lattice['threshold_reachable']}")
    L.append(f"- 分桶: <7.5 = {lattice['count_lt_low']} 条 | ≥7.5 = "
             f"{lattice['count_ge_low']} 条 | 其中 (7.5, 8.0) = "
             f"{lattice['count_in_gap']} 条 | ≥8.0 = {lattice['count_ge_high']} 条")
    L.append(f"- 严格落在 (7.5, 8.0) 的观测值: "
             f"{lattice['values_between_thresholds'] or '无'}")
    L.append(f"- 说明: {lattice['note']}")
    L.append("")
    L.append("## 逐条明细\n")
    L.append("| 任务 | 域 | 严重度 | free | strong | 差 | 回复来源 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in rows:
        L.append(f"| {r['id']} | {r['category']} | {r['severity']} | "
                 f"{r['free_overall']} | {r.get('strong_overall', '—')} | "
                 f"{r.get('delta', '—')} | {r['replies_source']} |")
    L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="k39 S1 L3 判卷校准（强模型 vs 免费判卷；手动运行）")
    ap.add_argument("--run", required=True,
                    help="统一运行器结果目录（含 results.json；只读）")
    ap.add_argument("--tasks", default="",
                    help="指定任务 id，逗号分隔（默认按域确定性抽样）")
    ap.add_argument("--sample", type=int, default=MIN_SAMPLES,
                    help=f"抽样条数（默认 {MIN_SAMPLES}，brief 要求 ≥{MIN_SAMPLES}）")
    ap.add_argument("--judge-model", default=STRONG_JUDGE_MODEL,
                    help=f"强判卷模型（默认 {STRONG_JUDGE_MODEL}）")
    ap.add_argument("--api-key", default="",
                    help="判卷模型 key；优先级 --api-key > 环境变量 "
                         "CALIB_JUDGE_API_KEY > ZHIPU_API_KEY")
    ap.add_argument("--out", default="",
                    help="输出目录（默认 data/eval/results/"
                         "judge-calibration-<时间戳>/）")
    ap.add_argument("--dry-run", action="store_true",
                    help="零 LLM 调用：只做样本选择 + 格点/等价性结构分析")
    args = ap.parse_args(argv)

    try:
        run = report.load_run_dir(args.run)
    except ValueError as e:
        # 允许「只有 results.json 的只读副本」（控制方复制既有 run 的常见形态：
        # 不带 meta.json）。results.json 也缺才是真前置失败。
        rf = Path(args.run) / "results.json"
        if not rf.exists():
            print(f"FATAL: {e}；且 {rf} 不存在", file=sys.stderr)
            return 2
        print(f"[WARN] {e} —— 按「仅 results.json」口径继续")
        run = {"meta": {}, "results": json.loads(rf.read_text(encoding="utf-8"))}
    records = run["results"]
    if not records:
        print(f"FATAL: {args.run} 的 results.json 为空", file=sys.stderr)
        return 2

    tasks = _load_tasks()
    if args.tasks.strip():
        want = [t.strip() for t in args.tasks.split(",") if t.strip()]
    else:
        want = select_sample_ids(records, args.sample)
    if len(want) < MIN_SAMPLES and not args.tasks.strip():
        print(f"FATAL: 可选样本仅 {len(want)} 条 < {MIN_SAMPLES}", file=sys.stderr)
        return 2

    by_id = {r.get("id"): r for r in records}
    rows = []
    for tid in want:
        rec = by_id.get(tid)
        if rec is None:
            print(f"[WARN] {tid} 不在 run 结果里，跳过", file=sys.stderr)
            continue
        replies, src = _pick_replies(rec)
        if not replies:
            print(f"[WARN] {tid} 无可重判回复，跳过", file=sys.stderr)
            continue
        l3 = rec.get("l3") or {}
        rows.append({
            "id": tid, "category": rec.get("category"),
            "severity": rec.get("severity"), "title": rec.get("title"),
            "is_edge": edge_scope.is_edge_record(rec),
            "free_overall": l3.get("overall"),
            "free_dims": {d: (l3.get("dims") or {}).get(d, {}).get("score")
                          for d in judge.DIMS},
            "free_judge_error": bool(l3.get("judge_error")),
            "replies_source": src, "replies": replies,
        })
    if len(rows) < MIN_SAMPLES:
        print(f"FATAL: 有效样本 {len(rows)} 条 < {MIN_SAMPLES}", file=sys.stderr)
        return 2

    api_key = (args.api_key
               or os.environ.get("CALIB_JUDGE_API_KEY", "")
               or os.environ.get("ZHIPU_API_KEY", "")).strip()
    blocked_reason = ""
    if not args.dry_run:
        if not api_key:
            print("FATAL: 强模型判卷需要 key —— 请传 --api-key，或设置环境变量 "
                  "CALIB_JUDGE_API_KEY（首选，控制方注入）或 ZHIPU_API_KEY。"
                  "若只想做零成本结构分析（不调模型）请加 --dry-run。",
                  file=sys.stderr)
            return 2
        for r in rows:
            task = tasks.get(r["id"])
            if task is None:
                print(f"[WARN] {r['id']} 不在评估集，无法重判，跳过",
                      file=sys.stderr)
                r["strong_overall"] = None
                continue
            j = rejudge(task, r["replies"], api_key, args.judge_model)
            r["strong_overall"] = j["overall"]
            r["strong_dims"] = {d: (j.get("dims") or {}).get(d, {}).get("score")
                                for d in judge.DIMS}
            r["strong_judge_error"] = bool(j.get("judge_error"))
            r["delta"] = (None if (r["free_overall"] is None
                                   or j["overall"] is None)
                          else round(j["overall"] - r["free_overall"], 4))
            print(f"[{'DRY' if args.dry_run else 'REJUDGED'}] {r['id']} "
                  f"{r['category']}/{r['severity']} free={r['free_overall']} "
                  f"strong={j['overall']} Δ={r['delta']}", flush=True)
    else:
        blocked_reason = ("--dry-run：未调用强模型（需 key 且可能产生费用）；"
                          "分差/方差统计缺失，只输出结构分析")

    pairs = [(r["free_overall"], r.get("strong_overall")) for r in rows
             if r.get("strong_overall") is not None]
    stats = delta_stats(pairs) if pairs else {"n": 0}
    if pairs:
        stats["_abs_deltas"] = [abs(b - a) for a, b in pairs]
    # 格点/分布分析用**整个 run** 的已判卷记录（零成本，样本更大更有说服力）
    full_scores = [(r.get("l3") or {}).get("overall") for r in records
                   if not (r.get("l3") or {}).get("skipped")
                   and not (r.get("l3") or {}).get("judge_error")]
    lattice = lattice_analysis(full_scores)
    lattice_sample = lattice_analysis([r["free_overall"] for r in rows])
    # 有强模型对照 → 用它判等价性；没有（dry-run/未跑）→ 退到零成本经验判据
    equiv = (threshold_equivalence(stats) if pairs
             else empirical_equivalence(lattice))
    equiv["basis"] = ("强模型对照（strong − free 分差）" if pairs
                      else "零成本经验判据（仅用既有免费判卷输出；强模型未跑）")

    out_dir = (Path(args.out) if args.out else
               RESULTS_ROOT / f"judge-calibration-"
                              f"{time.strftime('%Y%m%d-%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "layer": "L3-JUDGE-CALIBRATION",
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "run_dir": str(args.run),
        "repo_version": _git_head(),
        "free_model": judge.JUDGE_MODEL,
        "strong_model": args.judge_model,
        "dry_run": bool(args.dry_run),
        "blocked_reason": blocked_reason,
        "samples": [r["id"] for r in rows],
        "edge_samples": [r["id"] for r in rows if r["is_edge"]],
        "replies_source": sorted({r["replies_source"] for r in rows}),
        "replies_note": "既有 run 的回复已落盘瘦身（l3.replies 200 字 / "
                        "attempts.replies_preview 2000 字），完整回复未落盘；"
                        "分差含截断效应——两模型吃同一份截断文本，"
                        "对照变量仍是「模型」",
        "stats": {k: v for k, v in stats.items() if k != "_abs_deltas"},
        "lattice": lattice,
        "lattice_sample": lattice_sample,
        "threshold_equivalence": equiv,
        "thresholds": list(GATE_THRESHOLDS),
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "calibration.json").write_text(
        json.dumps([{k: v for k, v in r.items() if k != "replies"}
                    for r in rows], ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / "report.md").write_text(
        build_report_md(meta, rows, stats, lattice, equiv),
        encoding="utf-8")
    print(f"\n输出目录: {out_dir}")
    print(f"阈值等价性: {equiv.get('verdict')} —— {equiv.get('reason')}")
    if args.dry_run:
        print(f"注意: {blocked_reason}")
    errs = sum(1 for r in rows if r.get("strong_judge_error"))
    return 3 if errs else 0


def _git_head() -> str:
    try:
        import l1_eval
        return l1_eval._git_head()
    except Exception:  # noqa: BLE001 — 报告字段，失败不阻断
        return ""


if __name__ == "__main__":
    sys.exit(main())
