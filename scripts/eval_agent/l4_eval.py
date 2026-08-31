#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L4 最小 CLI（E5）：复用 E2 骨架 → 逐轮喂生产主链 → 跑完查临时库状态断言 → pass^k 重跑 → 报告落盘。

测什么（spec §5.4，tau-bench 模式）：完整业务链路能否真正完成 + 结果是否真正
落库——只查库不读回复文字，抓「回复看着对但数据没落库」假成功。本批只建
state_verifier.py + l4_eval.py 最小 CLI，统一 runner 组装归 E6。

用法：
  python3 scripts/eval_agent/l4_eval.py                      # 默认冒烟子集
  python3 scripts/eval_agent/l4_eval.py --tasks T001,T013    # 指定任务
  python3 scripts/eval_agent/l4_eval.py --category fortune   # 指定域
  python3 scripts/eval_agent/l4_eval.py --all                # 全量 100 条
  python3 scripts/eval_agent/l4_eval.py --pass-k 3           # 统一覆盖重跑次数

退出码：0 = 全部执行任务通过（本批不设阈值门禁——L4 门禁在 E6 接线）
        1 = 有任务失败 / 2 = 评估集校验失败或前置失败。

默认冒烟子集（brief 契约）：链路 7 条 T013/T014/T026/T027/T038/T057/T090
pass_k=3 实跑 + P0 非链路抽样 5 条 T001/T002/T003/T005/T016 pass_k=1
（冒烟覆盖语义，任务自身字段 T001/T002/T003 为 3——见 SMOKE_PASS_K_OVERRIDE）。

红线（同 E2）：src/bot/tool_calls.py 零改动（拦截在测试侧 interceptor.py）；
隔离库方案（复制真实库 → 每任务每轮重跑独立临时库，原始库零写入）；
USER_MEMORY_DIR/CHARTS_DIR 重定向临时目录，data/memory/ 零触碰；
key 只读环境变量（ZHIPU_API_KEY），零落盘；零新增依赖；零 LLM 调用（L4 是
确定性层——主链 LLM 路由仅执行任务本身，断言层纯查库）；data/eval/
agent_tasks.jsonl 只读（唯一事实源）；结果落 data/eval/results/。

模块导入零副作用：src 导入/环境变量/模型路由 patch 全部在
l1_eval._init_runtime()/main() 内懒执行——便于 tests/test_eval_l4.py 直接
import 断言/指标/选择纯函数。
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import l1_eval  # noqa: E402  —— E2 骨架复用（_init_runtime/seed_db_copy/
#                                    seed_task_setup/atexit/TASKS_PATH 等）
import state_verifier  # noqa: E402 —— L4 状态断言执行器（本批核心）
from interceptor import ToolCallRecorder  # noqa: E402

TASKS_PATH = l1_eval.TASKS_PATH
VALIDATOR = l1_eval.VALIDATOR
RESULTS_ROOT = l1_eval.RESULTS_ROOT
TASK_TIMEOUT = l1_eval.TASK_TIMEOUT

# 默认冒烟子集（brief 契约：链路 7 条全量 + P0 非链路抽样 5 条）
CHAIN_TASKS = ["T013", "T014", "T026", "T027", "T038", "T057", "T090"]
SMOKE_SAMPLE = ["T001", "T002", "T003", "T005", "T016"]
# 冒烟覆盖语义：抽样 5 条统一 pass_k=1（其字段值 T001/T002/T003 为 3——
# P0 可靠性已由链路 7 条 k=3 覆盖；--pass-k 可整体覆盖）
SMOKE_PASS_K_OVERRIDE = {tid: 1 for tid in SMOKE_SAMPLE}


# ================================================================
# pass_k 生效解析（纯函数，供单测）
# ================================================================

def effective_pass_k(task: dict, cli_k: int = 0,
                     smoke_override: dict = None) -> tuple:
    """任务生效重跑次数 + 来源。

    优先级：--pass-k K（cli-override）> 冒烟子集覆盖（smoke-override）>
    任务自身 pass_k 字段（task-field，spec §5.4 #4：P0=3，其余=1）。
    """
    if cli_k:
        return int(cli_k), "cli-override"
    if smoke_override and task.get("id") in smoke_override:
        return int(smoke_override[task["id"]]), "smoke-override"
    return int(task.get("pass_k") or 1), "task-field"


# ================================================================
# 单次尝试执行（每次尝试独立临时库副本——重跑隔离）
# ================================================================

def run_attempt(task: dict, R: dict, model_route: str, attempt_idx: int) -> dict:
    """单次完整尝试：隔离库复制 → 种子注入 → 跑前快照 → 逐轮喂主链 →
    跑后临时库状态断言。返回 attempt 记录。

    - 复制/种子失败 → 返回 skipped 标记（run_task_with_passk 据此整任务跳过）
    - 断言失败/异常 → ok=False + 原因（不是崩溃）
    - 每次尝试独立临时目录（pass^k 重跑隔离，k 次互不污染）
    """
    tid = task["id"]
    user_id = f"eval_{tid}"
    tdir = Path(tempfile.mkdtemp(prefix=f"{tid}-a{attempt_idx}-",
                                 dir=str(R["tmp_root"])))
    attempt = {
        "index": attempt_idx, "ok": False, "skipped": False,
        "skip_reason": "", "exception": None, "elapsed": 0.0,
        "state": {"ok": False, "checks": [], "detail": ""},
        "replies": [], "tool_calls": [],
    }
    try:
        db_path = l1_eval.seed_db_copy(R["settings"].db_path, tdir)
    except Exception as e:  # noqa: BLE001
        attempt["skipped"] = True
        attempt["skip_reason"] = (f"隔离库复制失败: "
                                  f"{type(e).__name__}: {str(e)[:120]}")
        return attempt
    seed_err = l1_eval.seed_task_setup(str(db_path), user_id,
                                       task.get("setup") or {})
    if seed_err is not None:
        attempt["skipped"] = True
        attempt["skip_reason"] = f"种子注入失败: {seed_err}"
        return attempt

    # 跑前快照（种子注入后、主链运行前——created 的基线）
    before_counts = state_verifier.snapshot_counts(str(db_path))

    recorder = ToolCallRecorder()
    t0 = time.monotonic()
    try:
        with recorder:
            recorder.install()
            handler = R["build_handler"](db_path, model_route)

            def _run_chain():
                replies = []
                for i, turn in enumerate(task["turns"]):
                    recorder.begin_turn()
                    text = (turn.get("text") or "").strip()
                    replies.append(
                        handler.process(text, user_id,
                                        session_id=f"eval-{tid}") or "")
                return replies

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                replies = ex.submit(_run_chain).result(timeout=TASK_TIMEOUT)
            attempt["replies"] = [(r or "")[:200] for r in replies]
    except concurrent.futures.TimeoutError:
        attempt["exception"] = f"任务超时（>{TASK_TIMEOUT}s）"
    except Exception as e:  # noqa: BLE001 — 主链异常也要落报告
        attempt["exception"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        attempt["elapsed"] = time.monotonic() - t0

    attempt["tool_calls"] = recorder.turns()
    # 执行验证（核心）：跑后在临时库副本上查表断言（对照跑前快照）
    attempt["state"] = state_verifier.run_state_checks(
        str(db_path), before_counts, task.get("state_checks") or {})
    attempt["ok"] = (attempt["exception"] is None and attempt["state"]["ok"])
    return attempt


def run_task_with_passk(task: dict, R: dict, model_route: str, k: int,
                        attempt_fn=run_attempt) -> dict:
    """按 pass^k 重跑单任务。k 次全部通过才算该任务过（spec §5.4 #4）。

    - 每次重跑经 attempt_fn 用独立临时库副本（重跑隔离）
    - 首次尝试复制/种子失败 → 整任务跳过并标注（同 E2 契约）
    - 任意一次失败 → 该任务不过（pass^k 语义，单测覆盖）
    """
    tid = task["id"]
    result = {
        "id": tid, "category": task["category"], "severity": task["severity"],
        "title": task["title"], "turns": len(task["turns"]),
        "pass_k_field": task.get("pass_k"),
        "pass_k": k, "pass_k_source": "",
        "state_checks": task.get("state_checks", {}),
        "skipped": False, "skip_reason": "",
        "attempts": [], "passed": False, "first_ok": False, "elapsed": 0.0,
    }
    t0 = time.monotonic()
    attempts = []
    for i in range(k):
        a = attempt_fn(task, R, model_route, i)
        attempts.append(a)
        if i == 0 and a.get("skipped"):
            result["skipped"] = True
            result["skip_reason"] = a["skip_reason"]
            break
        if a.get("skipped"):  # 非首轮不应出现（首轮已短路）；防御性兜底
            result["skipped"] = True
            result["skip_reason"] = a["skip_reason"]
            break
        status = "✓" if a["ok"] else "✗"
        print(f"    [尝试 {i + 1}/{k} {status}] {tid} "
              f"{a['elapsed']:.1f}s | {a['state']['detail'][:160]}",
              flush=True)
    result["attempts"] = attempts
    result["elapsed"] = time.monotonic() - t0
    if not result["skipped"]:
        result["first_ok"] = attempts[0]["ok"]
        result["passed"] = all(a["ok"] for a in attempts)
    return result


# ================================================================
# 指标（TSR + pass^k，spec §5.4 指标）
# ================================================================

def aggregate_metrics(results: list) -> dict:
    """L4 指标（跳过条目不进任何分母，显式列在 skipped）：

    - TSR（首轮通过率）= 首次尝试通过任务数 / 已执行任务数
    - pass^k 通过率 = k 次全部通过的任务数 / 已执行任务数（可靠性）
    - failed = 执行过但未通过的任务；exceptions = 任一次尝试有异常的任务
    """
    executed = [r for r in results if not r["skipped"]]
    n = len(executed)
    tsr_ok = [r for r in executed if r["first_ok"]]
    passk_ok = [r for r in executed if r["passed"]]
    return {
        "total": len(results),
        "executed": n,
        "skipped": [r["id"] for r in results if r["skipped"]],
        "skipped_reasons": {r["id"]: r["skip_reason"]
                            for r in results if r["skipped"]},
        "tsr": (len(tsr_ok) / n) if n else 0.0,
        "tsr_passed": len(tsr_ok),
        "tsr_denominator": n,
        "passk_rate": (len(passk_ok) / n) if n else 0.0,
        "passk_passed": len(passk_ok),
        "passk_denominator": n,
        "failed": [r["id"] for r in executed if not r["passed"]],
        "exceptions": [r["id"] for r in executed
                       if any(a.get("exception") for a in r["attempts"])],
    }


# ================================================================
# 报告落盘（meta.json + results.json + report.md）
# ================================================================

def render_report_md(meta: dict, metrics: dict, results: list) -> str:
    L = []
    L.append("# L4 端到端执行验证报告（E5）\n")
    L.append(f"- 运行时间: {meta['run_at']}")
    L.append(f"- 模型路由: {meta['model']}（主链执行；L4 断言层零 LLM 纯查库）"
             f" | 仓库版本: {meta['repo_version']}")
    L.append(f"- 任务范围: {len(meta['scope'])} 条 "
             f"（{', '.join(meta['scope'][:20])}"
             + ("…" if len(meta["scope"]) > 20 else "") + "）")
    L.append(f"- 隔离: 每任务每轮重跑独立临时库副本 + "
             f"USER_MEMORY_DIR/CHARTS_DIR 重定向（生产库/生产 memory 零写入）")
    L.append(f"- 门禁: 本批不设阈值（L4 门禁在 E6 接线），数字即基线\n")

    L.append("## 指标（spec §5.4：L4 首测基线，不达标也是基线）\n")
    L.append("| 指标 | 值 | 说明 |")
    L.append("|---|---|---|")
    L.append(f"| TSR（首轮通过率） | {metrics['tsr']:.1%} "
             f"({metrics['tsr_passed']}/{metrics['tsr_denominator']}) "
             f"| 首次尝试状态断言全过 |")
    L.append(f"| pass^k 通过率 | {metrics['passk_rate']:.1%} "
             f"({metrics['passk_passed']}/{metrics['passk_denominator']}) "
             f"| k 次全部通过才算过（可靠性） |")
    L.append("")
    if metrics["skipped"]:
        L.append("## 跳过清单（显式标注，不进指标分母）\n")
        for tid, reason in metrics["skipped_reasons"].items():
            L.append(f"- {tid}: {reason}")
        L.append("")

    L.append("## pass_k 生效表\n")
    L.append("| id | 字段值 | 生效 k | 来源 |")
    L.append("|---|---|---|---|")
    for r in results:
        L.append(f"| {r['id']} | {r['pass_k_field']} | {r['pass_k']} "
                 f"| {r['pass_k_source']} |")
    L.append("")

    L.append("## 逐任务明细\n")
    L.append("| id | 域 | 级别 | k | 结果 | 首轮 | 状态断言（跑前→跑后） | 说明 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        if r["skipped"]:
            continue
        state_detail = (r["attempts"][0]["state"]["detail"]
                        if r["attempts"] else "—")
        note = ""
        fails = [a for a in r["attempts"] if not a["ok"]]
        if fails:
            a = fails[0]
            note = a["exception"] or "; ".join(
                c["reason"] for c in a["state"]["checks"] if not c["pass"]) \
                or "状态断言失败"
        L.append(f"| {r['id']} | {r['category']} | {r['severity']} "
                 f"| {r['pass_k']} | {'PASS' if r['passed'] else 'FAIL'} "
                 f"| {'✓' if r['first_ok'] else '✗'} | {state_detail[:80]} "
                 f"| {note[:120]} |")
    L.append("")

    fails = [r for r in results if not r["skipped"] and not r["passed"]]
    if fails:
        L.append("## 失败明细（期望表状态 vs 实际行数变化）\n")
        for r in fails:
            L.append(f"### {r['id']} {r['title']}（k={r['pass_k']}，"
                     f"{r['pass_k_source']}）\n")
            L.append(f"- state_checks 契约: "
                     f"{json.dumps(r['state_checks'], ensure_ascii=False)}")
            for a in r["attempts"]:
                st = a["state"]
                fail_keys = [c for c in st["checks"] if not c["pass"]]
                mark = "PASS" if a["ok"] else "FAIL"
                L.append(f"- 尝试 {a['index'] + 1}/{r['pass_k']} [{mark}] "
                         f"({a['elapsed']:.1f}s)")
                if fail_keys:
                    for c in fail_keys:
                        L.append(f"  - {c['key']}: {c['table']} "
                                 f"跑前 {c['before']} 行 → 跑后 {c['after']} 行"
                                 f"（期望 {c['expected']}）—— {c['reason']}")
                if a["exception"]:
                    L.append(f"  - 异常: {a['exception']}")
                for i, rep in enumerate(a["replies"]):
                    L.append(f"  - 轮 {i + 1} 回复摘录: {rep}")
            L.append("")
    return "\n".join(L)


def run_eval(tasks: list, out_dir: Path, model_route: str = "glm",
             cli_k: int = 0, smoke_override: dict = None,
             keep_tmp: bool = False) -> dict:
    """跑指定任务集（串行；链路单条可能 1-2min × k 次）并落盘报告。"""
    R = l1_eval._init_runtime(model_route)
    if not keep_tmp:
        l1_eval.atexit_register_cleanup(R["tmp_root"])
    results = []
    print(f"L4 运行开始（{len(tasks)} 任务，串行）: "
          f"{[t['id'] for t in tasks]}", flush=True)
    for task in tasks:
        k, source = effective_pass_k(task, cli_k, smoke_override)
        print(f"[{task['id']}] {task['title']}（k={k}，{source}）", flush=True)
        r = run_task_with_passk(task, R, model_route, k)
        r["pass_k_source"] = source
        results.append(r)
        if r["skipped"]:
            print(f"[SKIP] {r['id']} {r['title']} —— {r['skip_reason']}",
                  flush=True)
        else:
            print(f"[{'PASS' if r['passed'] else 'FAIL'}] {r['id']} "
                  f"{r['category']} {r['severity']} {r['title']} "
                  f"(k={k}，{r['elapsed']:.1f}s)", flush=True)
    metrics = aggregate_metrics(results)
    meta = {
        "layer": "L4",
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "model": model_route,
        "repo_version": l1_eval._git_head(),
        "scope": [t["id"] for t in tasks],
        "eval_set": "data/eval/agent_tasks.jsonl (E1, 唯一事实源，只读)",
        "pass_k_table": {r["id"]: {"field": r["pass_k_field"],
                                   "effective": r["pass_k"],
                                   "source": r["pass_k_source"]}
                         for r in results},
        "metrics": metrics,
        "thresholds": None,  # 本批不设阈值（L4 门禁在 E6 接线）
        "verifier": "scripts/eval_agent/state_verifier.py "
                    "（created/unchanged/equals 三键，临时库查表断言）",
        "isolation": "每任务每轮重跑独立临时库副本 + "
                     "USER_MEMORY_DIR/CHARTS_DIR 重定向",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(
        render_report_md(meta, metrics, results), encoding="utf-8")
    print(f"\n结果已写入: {out_dir}", flush=True)
    return {"meta": meta, "metrics": metrics, "results": results}


# ================================================================
# 任务选择与 CLI
# ================================================================

def select_task_ids(tasks: list, args) -> list:
    """任务选择：--tasks 指定 / --category 域 / --all 全量 / 默认冒烟子集
    （链路 7 条全量 + P0 非链路抽样 5 条，brief 契约）。"""
    if args.tasks:
        want = {x.strip() for x in args.tasks.split(",") if x.strip()}
        unknown = sorted(want - {t["id"] for t in tasks})
        if unknown:
            raise SystemExit(f"FATAL: 未知任务 id: {unknown}")
        return [t["id"] for t in tasks if t["id"] in want]
    if args.category:
        if args.category not in l1_eval.CATEGORIES:
            raise SystemExit(
                f"FATAL: 未知域: {args.category}"
                f"（合法: {','.join(l1_eval.CATEGORIES)}）")
        return [t["id"] for t in tasks if t["category"] == args.category]
    if args.all:
        return [t["id"] for t in tasks]
    ids = [t for t in CHAIN_TASKS if t in {x["id"] for x in tasks}]
    known_sample = [t for t in SMOKE_SAMPLE
                    if t in {x["id"] for x in tasks} and t not in ids]
    return ids + known_sample


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="L4 端到端执行验证（E5）：黑盒跑生产主链 → 临时库状态断言"
                    " → pass^k 重跑 → 报告")
    ap.add_argument("--tasks", default="",
                    help="只跑指定任务 id（逗号分隔，如 T001,T013）")
    ap.add_argument("--category", default="",
                    help="只跑指定域（paipan/fortune/zeri/hehun/xingming/"
                         "qian/liuyao/ziwei/chat/edge）")
    ap.add_argument("--all", action="store_true", help="跑全量 100 条")
    ap.add_argument("--pass-k", type=int, default=0,
                    help="统一覆盖重跑次数（默认按任务自身 pass_k 字段；"
                         "冒烟子集抽样任务按 1）")
    ap.add_argument("--out", default="",
                    help="结果目录（默认 data/eval/results/l4-<时间戳>/）")
    ap.add_argument("--keep-tmp", action="store_true",
                    help="保留临时隔离目录（默认退出清理）")
    args = ap.parse_args(argv)

    if args.pass_k < 0:
        print("FATAL: --pass-k 必须为非负整数", file=sys.stderr)
        return 2

    # 1) 评估集校验（唯一事实源，只读；失败退出码 2）
    if not TASKS_PATH.exists():
        print(f"FATAL: 评估集不存在: {TASKS_PATH}", file=sys.stderr)
        return 2
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(TASKS_PATH)],
        capture_output=True, text=True, timeout=120)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        print(f"FATAL: 评估集校验失败（validate_tasks 退出码 {proc.returncode}）",
              file=sys.stderr)
        return 2

    tasks = [json.loads(line) for line in TASKS_PATH.read_text(encoding="utf-8")
             .splitlines() if line.strip()]
    ids = select_task_ids(tasks, args)
    selected = [t for t in tasks if t["id"] in ids]
    if not selected:
        print("FATAL: 无任务可跑", file=sys.stderr)
        return 2

    # 冒烟覆盖仅作用于默认冒烟子集（--tasks/--category/--all 时不覆盖）
    smoke_override = (SMOKE_PASS_K_OVERRIDE if not (args.tasks or args.category
                                                    or args.all) else {})

    out_dir = (Path(args.out) if args.out
               else RESULTS_ROOT / f"l4-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        run_eval(selected, out_dir, model_route="glm", cli_k=args.pass_k,
                 smoke_override=smoke_override, keep_tmp=args.keep_tmp)
    except RuntimeError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: 运行器错误: {type(e).__name__}: {str(e)[:300]}",
              file=sys.stderr)
        return 2

    m = (out_dir / "meta.json")
    if m.exists():
        meta = json.loads(m.read_text(encoding="utf-8"))
        mm = meta["metrics"]
        print(f"\n指标: TSR {mm['tsr']:.1%} ({mm['tsr_passed']}/"
              f"{mm['tsr_denominator']}) | pass^k {mm['passk_rate']:.1%} "
              f"({mm['passk_passed']}/{mm['passk_denominator']})")
        if mm["skipped"]:
            print(f"跳过 {len(mm['skipped'])} 条: {mm['skipped']}")
        if mm["failed"]:
            print(f"失败 {len(mm['failed'])} 条: {mm['failed']}")
        print("本批无阈值门禁（E6 接线）——退出码语义: "
              "0=全部通过 / 1=有失败")
        return 0 if not mm["failed"] else 1
    print("FATAL: 结果未落盘", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
