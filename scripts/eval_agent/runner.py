#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E6 统一运行器（E 系列收官批核心）：一条命令完成 读评估集 → 校验 → 四层打分
→ 门禁判定 → 落盘 → 台账追加。

用法：
  python3 scripts/eval_agent/runner.py [--mode quick|full] [--model free|prod]
      [--pass-k K] [--tasks T001,... | --category fortune | --all]
      [--compare-with <上次运行目录>] [--out <目录>]
      [--thresholds capability|regression] [--no-ledger] [--keep-tmp]
  python3 scripts/eval_agent/runner.py --calibration-rerun [--calibration-from <E4 校准目录>]

模式：
  quick（默认）：no_tool 全部 + edge 域 + P0 抽样 10 条 + 链路 7 条
                 （l4_eval.CHAIN_TASKS）——同 E2/E5 冒烟契约组合；
                 链路 P0 保持 k=3（任务字段），抽样 P0 k=1（smoke override）
  full：全部 100 条 × pass_k（P0=3，其余=1；--pass-k 统一覆盖）

核心机制——同轮采集（spec §6）：每任务每次尝试只跑 1 次主链会话，同一轮同时取
  - L1 拦截序列（interceptor + l1_eval.compare_expected，顺序敏感比对）
  - L2 回复断言（l2_eval：reply_checks 四键 + 跨轮互异 + *_unchanged 后置断言）
  - L4 库状态（state_verifier：跑前快照 → 跑后逐键断言）
  - L3 判卷（judge：对首次尝试的完整回复跑免费 glm-4-flash 五维判卷，TSR 口径）
pass^k（P0=3 全过才算过，spec §5.4）逐尝试独立临时库副本（重跑隔离）。

门禁（阈值表唯一事实源 = report.THRESHOLDS，spec §7 分层阈值表 L129-136）：
  quick → capability 档（L1 工具/参数 ≥90%、误调 0%；L2 断言 100%；L3 加权 ≥7.5、
  P0 平均 ≥8；L4 P0 pass³ 100%、TSR ≥90%）；full → regression 档（L1 ≥98%）。
  任一层 RED = exit 1。

退出码：0 = 全过（含门禁判定）/ 1 = 有任务失败或门禁红（红就是红如实报）/
        2 = 评估集校验失败 / 3 = 运行器错误（并发冲突拒绝、--model prod 拒绝、
        key 缺失、--compare-with 目标非法、未预期异常）。

红线（违反即返工）：
- 任何路径不初始化生产模型：--model 仅 free（默认）启用，prod 分支显式报错
  拒绝（报错文案「prod 未启用（红线：评测跑批一律免费模型）」），不初始化
  FortuneLLM/provider=deepseek（grep 自证：本文件零生产模型名/零 provider）
- 隔离库：每任务每尝试复制真实库 → 临时库（l1_eval.seed_db_copy），生产库
  零写入；USER_MEMORY_DIR/CHARTS_DIR 重定向临时目录（data/memory/ 零触碰）
- key 零落盘：ZHIPU_API_KEY 只读环境变量（仓库 .env 由 src.config 载入）
- 互斥排队纪律（E5 review 血泪教训，程序化）：启动时 pgrep 检测并发
  pytest/评测进程（排除自身与祖先链）+ pidfile 锁（liveness 检查）→
  存在则 exit 3 拒绝启动；冒烟/全量基线/全量 pytest 全部串行排队，
  报告如实记录每个跑的时间窗口
- 台账（report.ledger.json，data/eval/results/ledger.json）每次运行追加，
  为运行历史唯一事实源；--no-ledger 仅供门禁红验证等临时运行（不污染台账）
- data/eval/agent_tasks.jsonl 只读（唯一事实源）；data/memory/.json、
  src/engine/out/comparison_runs.jsonl 零改动

模块导入零副作用：src 导入全部经 l1_eval._init_runtime() 懒执行；
judge.py 顶部导入的 src.eval.scorer 为纯常量模块（零副作用，judge 自证）。
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

_REPO = Path(__file__).resolve().parent.parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (_REPO, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import l1_eval  # noqa: E402  — 骨架复用：隔离库/种子/主链装配/拦截/指标
import l2_eval  # noqa: E402  — L2 断言纯函数 + snapshot_unchanged
import l4_eval  # noqa: E402  — effective_pass_k / CHAIN_TASKS / L4 指标
import judge  # noqa: E402   — L3 判卷（免费 glm-4-flash）
import state_verifier  # noqa: E402  — L4 状态断言执行器
import report  # noqa: E402  — 门禁判定表 / 对比 / 台账 / 统一报告渲染
from interceptor import ToolCallRecorder  # noqa: E402

TASKS_PATH = l1_eval.TASKS_PATH
VALIDATOR = l1_eval.VALIDATOR
RESULTS_ROOT = l1_eval.RESULTS_ROOT
TASK_TIMEOUT = l1_eval.TASK_TIMEOUT
P0_SAMPLE_QUICK = 10  # quick 冒烟 P0 抽样条数（E2/E5 契约）
DEFAULT_CALIBRATION_DIR = RESULTS_ROOT / "l3-20260831-185821"

# ================================================================
# 互斥排队纪律（E5 review 立，本批程序化：启动检测 → 冲突 exit 3）
# ================================================================

def _read_ppid(pid: int):
    """/proc/<pid>/stat 取父进程 pid（字段 4）；失败 → 0（不可探，终止上溯）。"""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            rest = f.read().rsplit(")", 1)[1].split()
        return int(rest[1])  # 状态之后第 2 个字段 = ppid
    except Exception:  # noqa: BLE001
        return 0


def _ancestor_pids(max_depth: int = 12) -> set:
    """本进程祖先链 pid 集合（自身 + 逐级父进程，最多 max_depth 级）。"""
    out = set()
    pid = os.getpid()
    for _ in range(max_depth):
        out.add(pid)
        ppid = _read_ppid(pid)
        if ppid in (0, 1, pid):
            break
        pid = ppid
    return out


_PIDFILE = RESULTS_ROOT / ".runner.pid"
_MUTEX_PATTERNS = [
    "pytest", "verify_qa_scenarios", "eval_agent/runner",
    "eval_agent/l1_eval", "eval_agent/l2_eval", "eval_agent/l3_eval",
    "eval_agent/l4_eval", "eval_agent/judge.py",
]


def mutex_guard() -> str:
    """并发检测。返回空串 = 干净并已持锁；非空 = 冲突描述（调用方 exit 3）。

    两类检测：
    1. pgrep -f 扫描并发 pytest/评测进程（排除自身进程与祖先链——本进程
       可能由 pytest 子进程方式启动，祖先不算并发）
    2. pidfile 锁（liveness 检查：进程活着 = 并发；死亡/坏值 = 陈旧锁覆盖）
    环境变量 EVAL_RUNNER_SKIP_MUTEX=1 可跳过（仅限单测，meta 会如实标注）。
    """
    if os.environ.get("EVAL_RUNNER_SKIP_MUTEX") == "1":
        return "SKIPPED（EVAL_RUNNER_SKIP_MUTEX=1，仅限单测使用）"
    conflicts = []
    ancestors = _ancestor_pids()
    proc = None
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "|".join(_MUTEX_PATTERNS)],
            capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        proc = None  # 无 pgrep → pidfile 锁兜底
    if proc is not None and proc.returncode == 0:
        for line in proc.stdout.splitlines():
            try:
                pid = int(line.strip())
            except ValueError:
                continue
            if pid == os.getpid() or pid in ancestors:
                continue  # 自身/祖先不算并发
            conflicts.append(f"pgrep 命中并发进程 pid={pid}")
    if _PIDFILE.exists():
        try:
            old = int(_PIDFILE.read_text().strip())
        except ValueError:
            old = None
        if old:
            try:
                os.kill(old, 0)
                conflicts.append(f"pidfile 锁占用 pid={old}")
            except ProcessLookupError:
                pass  # 陈旧锁（进程已死）→ 覆盖
            except PermissionError:
                conflicts.append(f"pidfile 锁占用 pid={old}（存在但不可探）")
    if conflicts:
        return "；".join(sorted(set(conflicts)))
    _PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    _PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    return ""


def mutex_release():
    """释放 pidfile 锁（仅当锁属本进程）。"""
    try:
        if _PIDFILE.exists():
            cur = int(_PIDFILE.read_text().strip() or "0")
            if cur == os.getpid():
                _PIDFILE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 — 释放失败不掩盖主流程结果
        pass


# ================================================================
# 任务范围与 pass_k 覆盖（quick/full 契约组合）
# ================================================================

def _select_scope(tasks: list, mode: str, args) -> tuple:
    """返回 (ids, smoke_override)。

    quick：no_tool 全部 + edge 域 + P0 抽样 10 条 + 链路 7 条
           （同 E2/E5 冒烟契约）；抽样 P0（非链路）k=1 覆盖（链路保持
           任务字段 k=3——P0 可靠性已由链路 k=3 覆盖，E5 覆盖语义）
    full / 显式范围（--tasks/--category/--all）：无覆盖（任务字段 + --pass-k）
    """
    if args.tasks or args.category or args.all:
        ids = l1_eval.select_task_ids(tasks, args)
        return ids, {}
    if mode == "full":
        return [t["id"] for t in tasks], {}
    ids = [t["id"] for t in tasks if t.get("no_tool")]
    ids += [t["id"] for t in tasks
            if t["category"] == "edge" and t["id"] not in ids]
    p0_pool = [t["id"] for t in tasks
               if t["severity"] == "P0" and t["id"] not in ids]
    sample = p0_pool[:P0_SAMPLE_QUICK]
    ids += sample
    known = {t["id"] for t in tasks}
    ids += [x for x in l4_eval.CHAIN_TASKS if x in known and x not in ids]
    chain = set(l4_eval.CHAIN_TASKS)
    smoke_override = {x: 1 for x in sample if x not in chain}
    return ids, smoke_override


# ================================================================
# 同轮采集（核心机制）：每次尝试 1 次主链会话 → L1+L2+L4 同轮取
# ================================================================

def _run_attempt(task: dict, R: dict, attempt_idx: int) -> dict:
    """单次完整尝试：隔离库复制 → 种子 → 跑前快照 → 逐轮喂主链 →
    同轮取 L1 拦截 + L2 回复断言 + L4 库状态断言。返回 attempt 记录。

    - 复制/种子失败 → skipped（整任务跳过，同 E2 契约）
    - 异常/超时 → 三层全失败 + exception 标注（不是崩溃）
    - 每次尝试独立临时目录（pass^k 重跑隔离）
    """
    tid = task["id"]
    user_id = f"eval_{tid}"
    tdir = Path(tempfile.mkdtemp(prefix=f"{tid}-a{attempt_idx}-",
                                 dir=str(R["tmp_root"])))
    attempt = {
        "index": attempt_idx, "ok": False, "skipped": False,
        "skip_reason": "", "exception": None, "elapsed": 0.0,
        "replies": [],  # 完整回复（L3 判卷输入，落盘前截断瘦身）
        "l1": {"tool_select_ok": False, "params_ok": False, "ok": False,
               "actual_calls": [], "actual_by_turn": [], "detail": ""},
        "l2": {"ok": False, "checks": [], "replies_preview": []},
        "l4": {"ok": False, "checks": [], "detail": ""},
    }
    try:
        db_path = l1_eval.seed_db_copy(R["settings"].db_path, tdir)
    except Exception as e:  # noqa: BLE001
        attempt["skipped"] = True
        attempt["skip_reason"] = f"隔离库复制失败: {type(e).__name__}: {str(e)[:120]}"
        return attempt
    seed_err = l1_eval.seed_task_setup(str(db_path), user_id,
                                       task.get("setup") or {})
    if seed_err is not None:
        attempt["skipped"] = True
        attempt["skip_reason"] = f"种子注入失败: {seed_err}"
        return attempt

    state_checks = task.get("state_checks") or {}
    l2_before = l2_eval.snapshot_unchanged(str(db_path), state_checks)
    l4_before = state_verifier.snapshot_counts(str(db_path))

    recorder = ToolCallRecorder()
    t0 = time.monotonic()
    replies = []
    try:
        with recorder:
            recorder.install()  # 默认目标 MessageHandler（context 兜底恢复）
            handler = R["build_handler"](db_path, "glm")

            def _run_chain():
                rps = []
                for turn in task["turns"]:
                    recorder.begin_turn()
                    text = (turn.get("text") or "").strip()
                    rps.append(handler.process(text, user_id,
                                               session_id=f"eval-{tid}") or "")
                return rps

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                replies = ex.submit(_run_chain).result(timeout=TASK_TIMEOUT)
    except concurrent.futures.TimeoutError:
        attempt["exception"] = f"任务超时（>{TASK_TIMEOUT}s）"
    except Exception as e:  # noqa: BLE001 — 主链异常也要落报告
        attempt["exception"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        attempt["elapsed"] = time.monotonic() - t0
    attempt["replies"] = replies

    # L1：拦截序列 → 顺序敏感比对（E2 契约）
    calls = recorder.flat_calls()
    tool_ok, params_ok, detail = l1_eval.compare_expected(
        task["expected_tools"], calls)
    if attempt["exception"]:
        tool_ok, params_ok = False, False
        detail = (detail + "；" if detail else "") + f"异常: {attempt['exception']}"
    attempt["l1"].update(tool_select_ok=tool_ok, params_ok=params_ok,
                         ok=tool_ok and params_ok, detail=detail,
                         actual_calls=calls,
                         actual_by_turn=recorder.turns())

    # L2：回复内容断言（E3 契约：四键 + 跨轮 + *_unchanged 后置）
    checks = []
    if attempt["exception"]:
        checks.append({"name": "无异常", "expect": "", "ok": False,
                       "detail": f"异常: {attempt['exception']}"})
    else:
        full = "\n".join(replies)
        checks += l2_eval.eval_reply_checks(task, full)
        checks += l2_eval.eval_multi_turn(task, replies)
        if l2_before:
            l2_after = l2_eval.snapshot_unchanged(str(db_path), state_checks)
            checks += l2_eval.eval_state_unchanged(state_checks,
                                                   l2_before, l2_after)
    attempt["l2"]["checks"] = checks
    attempt["l2"]["ok"] = all(c["ok"] for c in checks)
    attempt["l2"]["replies_preview"] = [(r or "")[:2000] for r in replies]

    # L4：库状态断言（E5 契约：跑前快照 → 跑后逐键断言）
    attempt["l4"] = state_verifier.run_state_checks(
        str(db_path), l4_before, state_checks)

    attempt["ok"] = (attempt["exception"] is None and attempt["l1"]["ok"]
                     and attempt["l2"]["ok"] and attempt["l4"]["ok"])
    return attempt


def _run_task_with_passk(task: dict, R: dict, k: int, source: str,
                         api_key: str) -> dict:
    """按 pass^k 重跑单任务（四层同轮）。k 次全部通过才算该任务过。"""
    t0 = time.monotonic()
    attempts = []
    for i in range(k):
        a = _run_attempt(task, R, i)
        attempts.append(a)
        if a["skipped"]:
            break
        mark = "✓" if a["ok"] else "✗"
        print(f"    [尝试 {i + 1}/{k} {mark}] {task['id']} "
              f"{a['elapsed']:.1f}s | {a['l4']['detail'][:140]}", flush=True)
    rec = {
        "id": task["id"], "category": task["category"],
        "severity": task["severity"], "title": task["title"],
        "turns": len(task["turns"]), "no_tool": bool(task.get("no_tool")),
        "pass_k_field": task.get("pass_k"), "pass_k": k,
        "pass_k_source": source,
        "state_checks": task.get("state_checks", {}),
        "has_state_checks": bool(task.get("state_checks")),
        "skipped": attempts[0]["skipped"], "skip_reason": "",
        "attempts": attempts, "elapsed": time.monotonic() - t0,
        "first_ok": False, "passed": False, "passed_all_layers": False,
        "l3": None,
    }
    if rec["skipped"]:
        rec["skip_reason"] = attempts[0]["skip_reason"]
        rec["l3"] = {"id": task["id"], "category": task["category"],
                     "severity": task["severity"], "title": task["title"],
                     "skipped": True, "skip_reason": rec["skip_reason"],
                     "dims": {}, "overall": 0.0, "parse_level": 0,
                     "judge_error": False, "judge_error_reason": "",
                     "turns": task.get("turns") or [], "replies": []}
        return rec
    rec["first_ok"] = attempts[0]["ok"]
    rec["passed"] = all(a["ok"] for a in attempts)
    rec["passed_all_layers"] = rec["passed"]
    # L3：对首次尝试的完整回复判卷（TSR 口径，免费 glm-4-flash）
    j = judge.judge_task(task, attempts[0]["replies"], api_key)
    j["replies"] = [(r or "")[:200] for r in j["replies"]]  # 落盘瘦身
    rec["l3"] = j
    for a in attempts:  # 落盘瘦身：完整回复只用于判卷
        a["replies"] = [(r or "")[:1000] for r in a["replies"]]
    return rec


# ================================================================
# 四层聚合（复用各层既有纯函数，不复制逻辑）
# ================================================================

def _aggregate_layers(results: list) -> dict:
    """统一任务记录 → 四层指标（l1/l2/l3/l4 各层口径与单层运行器一致）。

    - L1/L2：首尝试口径（TSR 语义，同 E2/E3 单次运行）
    - L4：TSR/pass^k 只算「有断言键」任务（E6 残留处理：T005 空 state_checks
      归组为 no_state_checks，不进分母，单独列出）
    - L3：判卷结果经 judge.group_metrics（judge_error 按 0 分计两种口径）
    """
    # L1 视图（首尝试）
    l1_view = []
    for r in results:
        a0 = r["attempts"][0] if r["attempts"] else None
        l1_view.append({
            "id": r["id"], "skipped": r["skipped"],
            "no_tool": r["no_tool"],
            "tool_select_ok": bool(a0 and a0["l1"]["tool_select_ok"]),
            "params_ok": bool(a0 and a0["l1"]["params_ok"]),
            "ok": bool(a0 and a0["l1"]["ok"]),
            "actual_calls": (a0["l1"]["actual_calls"] if a0 else []),
            "exception": (a0["exception"] if a0 else None),
        })
    l1m = l1_eval.aggregate_metrics(l1_view)

    # L2 视图（首尝试）
    l2_view = []
    for r in results:
        a0 = r["attempts"][0] if r["attempts"] else None
        l2_view.append({
            "id": r["id"], "skipped": r["skipped"],
            "ok": bool(a0 and a0["l2"]["ok"]),
            "checks": (a0["l2"]["checks"] if a0 else []),
            "exception": (a0["exception"] if a0 else None),
            "replies": (a0["l2"]["replies_preview"] if a0 else []),
        })
    l2m = l2_eval.aggregate_metrics(l2_view)

    # L3 判卷（judge.group_metrics）
    l3m = judge.group_metrics([r["l3"] for r in results])

    # L4（有断言键子集 + no_state_checks 归组）
    with_checks = [r for r in results
                   if not r["skipped"] and r["has_state_checks"]]
    l4_view = [{
        "id": r["id"], "skipped": False, "skip_reason": "",
        "category": r["category"], "severity": r["severity"],
        "first_ok": r["attempts"][0]["l4"]["ok"],
        "passed": all(a["l4"]["ok"] for a in r["attempts"]),
        "attempts": r["attempts"],
    } for r in with_checks]
    l4m = l4_eval.aggregate_metrics(l4_view)
    l4m["executed_with_checks"] = l4m["executed"]
    l4m["no_state_checks"] = [r["id"] for r in results
                              if not r["skipped"] and not r["has_state_checks"]]
    p0m = l4_eval.aggregate_metrics([v for v in l4_view
                                     if v["severity"] == "P0"])
    l4m["p0_passk_rate"] = p0m["passk_rate"]
    l4m["p0_passk_passed"] = p0m["passk_passed"]
    l4m["p0_denominator"] = p0m["executed"]
    l4m["skipped"] = [r["id"] for r in results if r["skipped"]]
    l4m["skipped_reasons"] = {r["id"]: r["skip_reason"]
                              for r in results if r["skipped"]}
    return {"l1": l1m, "l2": l2m, "l3": l3m, "l4": l4m}


# ================================================================
# 主流程
# ================================================================

def _validate_eval_set() -> list:
    """评估集校验（唯一事实源，只读）。失败 → raise SystemExit(2)。"""
    if not TASKS_PATH.exists():
        print(f"FATAL: 评估集不存在: {TASKS_PATH}", file=sys.stderr)
        raise SystemExit(2)
    proc = subprocess.run([sys.executable, str(VALIDATOR), str(TASKS_PATH)],
                          capture_output=True, text=True, timeout=120)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        print(f"FATAL: 评估集校验失败（validate_tasks 退出码 {proc.returncode}）",
              file=sys.stderr)
        raise SystemExit(2)
    return [json.loads(line) for line in
            TASKS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_unified(tasks: list, mode: str, thresholds_mode: str,
                out_dir: Path, smoke_override: dict, cli_k: int,
                compare_with: str, no_ledger: bool,
                keep_tmp: bool, mutex_note: str,
                compare_ctx: dict = None) -> int:
    """统一运行主流程：逐任务四层同轮 → 聚合 → 门禁 → 落盘 → 台账。

    compare_ctx：{prev_path, cur_path, compare}——运行完成后补全当前 meta
    对比并追加到 report.md。返回退出码（0=全过 / 1=有失败或门禁红）。
    """
    t_start = time.monotonic()
    t_clock = time.strftime("%Y-%m-%d %H:%M:%S %z")
    R = l1_eval._init_runtime("glm")
    if not keep_tmp:
        l1_eval.atexit_register_cleanup(R["tmp_root"])
    api_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("需要环境变量 ZHIPU_API_KEY（免费 glm-4-flash 判卷/主链）")

    print(f"统一运行器开始（{len(tasks)} 任务，mode={mode}，"
          f"thresholds={thresholds_mode}）: {t_clock}", flush=True)
    print(f"任务范围: {[t['id'] for t in tasks]}", flush=True)
    results = []
    for task in tasks:
        k, source = l4_eval.effective_pass_k(task, cli_k, smoke_override)
        print(f"[{task['id']}] {task['title']}（k={k}，{source}）", flush=True)
        rec = _run_task_with_passk(task, R, k, source, api_key)
        results.append(rec)
        if rec["skipped"]:
            print(f"[SKIP] {rec['id']} {rec['title']} —— {rec['skip_reason']}",
                  flush=True)
        else:
            status = "PASS" if rec["passed"] else "FAIL"
            jnote = ""
            if rec["l3"].get("judge_error"):
                jnote = f" | JUDGE_ERR: {rec['l3']['judge_error_reason'][:80]}"
            print(f"[{status}] {rec['id']} {rec['category']} "
                  f"{rec['severity']} {rec['title']} "
                  f"(k={k}，{rec['elapsed']:.1f}s，L3 加权 "
                  f"{rec['l3'].get('overall')}){jnote}", flush=True)

    metrics = _aggregate_layers(results)
    gate = report.evaluate_gate(metrics["l1"], metrics["l2"],
                                metrics["l3"], metrics["l4"],
                                thresholds_mode)
    duration = round(time.monotonic() - t_start, 1)
    meta = {
        "layer": "UNIFIED",
        "run_at": t_clock,
        "duration_s": duration,
        "mode": mode,
        "thresholds_mode": thresholds_mode,
        "judge_model": judge.JUDGE_MODEL,
        "chain_model_route": "free（glm-4-flash，--model prod 显式拒绝）",
        "repo_version": l1_eval._git_head(),
        "scope": [t["id"] for t in tasks],
        "eval_set": "data/eval/agent_tasks.jsonl (E1, 唯一事实源，只读)",
        "pass_k_table": {r["id"]: {"field": r["pass_k_field"],
                                   "effective": r["pass_k"],
                                   "source": r["pass_k_source"]}
                         for r in results},
        "metrics": metrics,
        "gate": {"mode": gate["mode"], "verdict": gate["verdict"]},
        "mutex": mutex_note,
        "isolation": "每任务每尝试复制真实库→临时库 + USER_MEMORY_DIR/"
                     "CHARTS_DIR 重定向（生产库/data/memory 零写入）",
        "same_round": "每任务 1 次主链会话同轮取 L1 拦截 + L2 回复断言 + "
                      "L4 库状态；L3 对首次尝试完整回复判卷（TSR 口径）",
        "ledger": str(report.LEDGER_PATH),
        "compare_with": compare_with or None,
        "no_ledger": no_ledger,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(
        report.render_report_md(meta, gate, results,
                                compare_ctx if compare_with else None),
        encoding="utf-8")

    # --compare-with：当前运行 meta 已知后补全对比，追加到报告
    if compare_ctx is not None and compare_with:
        cur = report.load_run_dir(str(out_dir))
        compare_ctx["cur_path"] = str(out_dir)
        compare_ctx["compare"] = report.compare_runs(
            report.load_run_dir(compare_with), cur)
        (out_dir / "report.md").write_text(
            (out_dir / "report.md").read_text(encoding="utf-8") + "\n"
            + report.render_compare_md(compare_ctx["prev_path"],
                                       compare_ctx["cur_path"],
                                       compare_ctx["compare"]) + "\n",
            encoding="utf-8")
        print(f"对比已追加: {compare_with} → {out_dir}", flush=True)
    if not no_ledger:
        report.append_ledger(report.build_ledger_entry(out_dir.name, meta, gate))
    print(f"\n结果已写入: {out_dir}（耗时 {duration}s）", flush=True)
    print("\n" + report.render_gate_table(gate), flush=True)
    return 0 if gate["verdict"] else 1


def cmd_calibration_rerun(args) -> int:
    """30 条校准样本重判（rubric 四类修正后，E6 校准落地）。

    方法论（隔离 rubric 效应）：对 E4 校准存储样本（calibration_samples.json
    中的真实回复）用修正后 rubric 重新判卷——主链/回复不变，只换判卷标准，
    一致率变化即 rubric 修正的直接效果；若重跑主链会混入模型非确定性。
    一致率口径（report.calibration_consistency）：|新 judge 分 − 参考分| ≤ 1；
    校准报告记录在案的 15 处不一致维用人工分（CALIBRATION_HUMAN_REF），
    其余 135 维用 E4 judge 分作代理参考（保守口径，校准定义 90% 一致即由此来）。
    """
    src_dir = Path(args.calibration_from or DEFAULT_CALIBRATION_DIR)
    samples_f = src_dir / "calibration_samples.json"
    if not samples_f.exists():
        print(f"FATAL: 校准样本不存在: {samples_f}（--calibration-from 指定）",
              file=sys.stderr)
        return 2
    samples = json.loads(samples_f.read_text(encoding="utf-8"))
    if len(samples) < 30:
        print(f"FATAL: 校准样本 {len(samples)} 条 < 30（事实源异常）",
              file=sys.stderr)
        return 2
    api_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not api_key:
        print("FATAL: 需要环境变量 ZHIPU_API_KEY（免费 glm-4-flash 判卷）",
              file=sys.stderr)
        return 2
    tasks = [json.loads(line) for line in
             TASKS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {t["id"]: t for t in tasks}

    print(f"校准重判开始（{len(samples)} 条，rubric 四类修正后，"
          f"来源 {src_dir}）: {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
          flush=True)
    new_results = []
    for s in samples:
        task = by_id.get(s["id"])
        if task is None:
            print(f"[WARN] 样本 {s['id']} 不在评估集，跳过", flush=True)
            continue
        replies = [(r or "") for r in (s.get("replies") or [])]
        j = judge.judge_task(task, replies, api_key)
        new_results.append(j)
        mark = "REJUDGED" if not j["judge_error"] else "JUDGE_ERR"
        print(f"[{mark}] {j['id']} {j['category']} {j['severity']} "
              f"{j['title']} 加权 {j['overall']} 解析L{j['parse_level']}",
              flush=True)

    e4_results = [{"id": s["id"], "dims": s.get("dims") or {}} for s in samples]
    cons = report.calibration_consistency(new_results, e4_results)
    out_dir = (Path(args.out) if args.out
               else RESULTS_ROOT / f"calibration-rerun-"
                                    f"{time.strftime('%Y%m%d-%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "layer": "L3-CALIBRATION-RERUN",
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "judge_model": judge.JUDGE_MODEL,
        "source_samples": str(samples_f),
        "rubric_fix": "judge.py CALIBRATION_FIX_ANCHORS 四类修正（E6）",
        "method": "重判 E4 存储样本回复（隔离 rubric 效应）；一致率 = "
                  "|新 judge − 参考| ≤ 1；15 处记录在案维用人工分，其余用 "
                  "E4 judge 分代理",
        "repo_version": l1_eval._git_head(),
        "consistency": {k: cons[k] for k in
                        ("total_dims", "consistent", "rate",
                         "direct", "proxy")},
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps(new_results, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / "consistency.json").write_text(
        json.dumps(cons, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(
        report.render_calibration_md(cons), encoding="utf-8")
    print(f"\n校准重判完成: {out_dir}", flush=True)
    print(report.render_calibration_md(cons), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="E6 统一运行器：读评估集 → 校验 → 四层打分 → 门禁 → "
                    "落盘 → 台账（一条命令）")
    ap.add_argument("--mode", default="quick", choices=["quick", "full"],
                    help="quick=冒烟子集（默认，E2/E5 契约组合）；full=全量 100 条 × pass_k")
    ap.add_argument("--model", default="free",
                    help="free=免费 glm-4-flash（唯一启用）；prod 显式拒绝")
    ap.add_argument("--pass-k", type=int, default=0,
                    help="统一覆盖重跑次数（默认按任务字段，P0=3；quick 抽样覆盖 k=1）")
    ap.add_argument("--tasks", default="",
                    help="只跑指定任务 id（逗号分隔，如 T001,T013）")
    ap.add_argument("--category", default="",
                    help="只跑指定域（paipan/fortune/zeri/hehun/xingming/"
                         "qian/liuyao/ziwei/chat/edge）")
    ap.add_argument("--all", action="store_true", help="跑全量 100 条")
    ap.add_argument("--compare-with", default="",
                    help="上次运行结果目录（报告附逐层指标对比，涨跌标注）")
    ap.add_argument("--out", default="",
                    help="结果目录（默认 data/eval/results/unified-<时间戳>/）")
    ap.add_argument("--thresholds", default="",
                    choices=["", "capability", "regression"],
                    help="门禁档位（默认：quick→capability，full→regression）")
    ap.add_argument("--no-ledger", action="store_true",
                    help="不追加台账（仅供门禁红验证等临时运行，不污染唯一事实源）")
    ap.add_argument("--keep-tmp", action="store_true",
                    help="保留临时隔离目录（默认退出清理）")
    ap.add_argument("--calibration-rerun", action="store_true",
                    help="30 条校准样本重判（rubric 四类修正后一致率）")
    ap.add_argument("--calibration-from", default="",
                    help="E4 校准目录（含 calibration_samples.json；默认 "
                         "l3-20260831-185821）")
    args = ap.parse_args(argv)

    if args.calibration_rerun:
        return cmd_calibration_rerun(args)
    if args.pass_k < 0:
        print("FATAL: --pass-k 必须为非负整数", file=sys.stderr)
        return 3

    # 1) 互斥排队纪律（E5 review 立，程序化）：并发 pytest/评测 → exit 3
    mutex_note = mutex_guard()
    if mutex_note and not mutex_note.startswith("SKIPPED"):
        print(f"FATAL: 并发冲突，拒绝启动——{mutex_note}\n"
              f"      互斥排队纪律（E5 review）：评估跑期间不得有并发 "
              f"pytest/评测进程，串行排队先跑完评估再跑 pytest。",
              file=sys.stderr)
        return 3
    try:
        return _main_after_guard(ap, args, mutex_note)
    finally:
        mutex_release()


def _main_after_guard(ap, args, mutex_note: str) -> int:
    """互斥检查通过后的主流程（退出码 2/3/1/0）。"""
    # 2) --model prod 显式拒绝（红线：评测跑批一律免费模型，零生产模型初始化）
    if args.model != "free":
        print("FATAL: prod 未启用（红线：评测跑批一律免费模型）——"
              "本运行器只初始化免费 glm-4-flash 路由（l1_eval._init_runtime"
              "(glm)），任何路径不初始化 FortuneLLM/provider=deepseek。",
              file=sys.stderr)
        return 3

    # 3) 评估集校验（唯一事实源，只读；失败退出码 2）
    try:
        tasks = _validate_eval_set()
    except SystemExit as e:
        return int(e.code or 2)

    # 4) --compare-with 前置校验（非法目标 → exit 3，不在跑完后才报）
    compare_ctx = None
    if args.compare_with:
        try:
            report.load_run_dir(args.compare_with)
        except ValueError as e:
            print(f"FATAL: --compare-with 目标非法: {e}", file=sys.stderr)
            return 3
        compare_ctx = {"prev_path": args.compare_with,
                       "cur_path": "<当前运行>", "compare": None}

    # 5) 任务范围与 pass_k 覆盖
    try:
        ids, smoke_override = _select_scope(tasks, args.mode, args)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    selected = [t for t in tasks if t["id"] in ids]
    if not selected:
        print("FATAL: 无任务可跑", file=sys.stderr)
        return 2

    thresholds_mode = args.thresholds or (
        "capability" if args.mode == "quick" else "regression")
    out_dir = (Path(args.out) if args.out
               else RESULTS_ROOT / f"unified-{time.strftime('%Y%m%d-%H%M%S')}")

    # 6) 统一运行（同轮采集 → 聚合 → 门禁 → 落盘 → 对比追加）
    try:
        run_unified(selected, args.mode, thresholds_mode, out_dir,
                    smoke_override, args.pass_k, args.compare_with,
                    args.no_ledger, args.keep_tmp, mutex_note, compare_ctx)
    except RuntimeError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: 运行器错误: {type(e).__name__}: {str(e)[:300]}",
              file=sys.stderr)
        return 3

    m = (out_dir / "meta.json")
    if m.exists():
        meta = json.loads(m.read_text(encoding="utf-8"))
        mm = meta["metrics"]
        l1m, l2m, l3m, l4m = mm["l1"], mm["l2"], mm["l3"], mm["l4"]
        print(f"\n四层指标（{meta['mode']}）:")
        print(f"  L1 工具选择 {l1m['tool_selection_accuracy']:.1%} "
              f"({l1m['tool_selection_passed']}/{l1m['executed']}) | "
              f"参数 {l1m['param_accuracy']:.1%} | "
              f"误调 {l1m['false_call_count']}/{l1m['no_tool_count']}")
        print(f"  L2 断言通过率 {l2m['assertion_pass_rate']:.1%} "
              f"({l2m['passed']}/{l2m['executed']})")
        print(f"  L3 加权平均 {l3m['weighted_avg']}（P0 {l3m['p0_avg']}，"
              f"已判卷 {l3m['judged']}）")
        print(f"  L4 TSR {l4m['tsr']:.1%} ({l4m['tsr_passed']}/"
              f"{l4m['tsr_denominator']}) | pass^k {l4m['passk_rate']:.1%} | "
              f"P0 pass³ {l4m['p0_passk_rate']:.1%} | 无断言键归组 "
              f"{len(l4m['no_state_checks'])} 条")
        if l4m["skipped"]:
            print(f"  跳过 {len(l4m['skipped'])} 条: {l4m['skipped']}")
        print("门禁判定:", "PASS（全阈值达标）" if meta["gate"]["verdict"]
              else "RED（红就是红如实报——门禁红是体系价值，R1 修复后转绿）")
        return 0 if meta["gate"]["verdict"] else 1
    print("FATAL: 结果未落盘", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
