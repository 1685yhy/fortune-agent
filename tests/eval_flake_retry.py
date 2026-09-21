# -*- coding: utf-8 -*-
r"""k68：LLM 评测抖动策略 —— **有界重试（上界 1 次）+ 必须如实上报**。

## 背景（为什么需要它）

k61 r11 把 eval 从「空转」（全部 skip ⇒ `executed=0`）修成「真跑」——**覆盖变好了，
代价是门禁里带进了真实 LLM 调用的固有随机性**。实测：合并态全量门禁里
`tests/test_eval_l1.py::test_smoke_l1_default_slice` 红过一次，连跑 3 次
全部满分 `工具选择 100.0% (7/7) | 参数 100.0% (7/7) | 误调率 0/5`
⇒ 该次红是**真实 LLM 的固有抖动**（`model_route="glm"`，四次一红 ≈ 25%
——**粗估数字，勿当实测引用**，见下「抖动率数字的强度」），
**不是产品缺陷**（见 `.superpowers/sdd/progress.md` 的定性与处置段）。

## 处置（控制方拍板：有界重试 + 必须如实上报）

- **不降阈值**：会掩盖真回归；
- **不标非阻断**：会丢失信号；
- **不放着不管**：会让门禁变 flaky，而 **flaky 的门禁最后一定会被无视 ——
  等于没有门禁**。

## 第一原则（本模块最重要的一条，做错了比不做更糟）

**重试只能用于「评测真的跑起来了、只是阈值差一点」的情况。
绝不允许重试掩盖「评测根本没跑」。**

- **允许重试**：本轮确实完整执行（`executed == total` 且 `skipped == []`
  且无异常），失败是**阈值未达标**；
- **决不允许重试**：`executed == 0` / 有 skip / 基础设施失败（隔离库复制失败、
  键缺失、DB 快照缺失等）/ 结构性断言失败 / 主链异常。

为什么：**k61 当初打红这条的形式正是 `executed=0` 全跳过**。如果无差别重试，
那种「评测根本没跑」的**真故障会被重试吞掉**，我们就再也发现不了 k61 那类破坏。
本判据在 `execution_integrity()` 里取「宁严勿宽」：字段缺失也按「没跑」处理
（fail-closed），异常一律不重试（`run_with_bounded_retry` 原样上抛）。

## 上界（k73-M2：由**构造**强制，不再只靠约定）

`MAX_ATTEMPTS = 2` ⇒ **总共最多 2 次尝试（= 最多 1 次重试，不许更多）**。

这是**控制方拍板的上界**（最多 1 次重试）。`run_with_bounded_retry` 用
`MAX_ATTEMPTS_HARD_CAP` 对入参做**硬校验**：`max_attempts` 越界直接 ValueError，
**连跑都不跑**（原实现只断言 `>= 1`，显式传 5 就会真跑 5 次并输出
`attempts=5/5` —— 上界当时只是约定）。改这个上限需要控制方重新拍板，写死在此
是为了**防止日后被悄悄调大**（调大 = 用更多重试掩盖抖动）。
所有「两次」这类**文案一律由实际次数推出**（`_cn_count`），不许硬编码 ——
否则上界一旦变化，报错文案会**谎报尝试次数**。

## 上报契约（不许悄悄吞）

| 情形 | 上报 |
|---|---|
| 第 1 次通过 | 1 次结果 + 信号行 `retried=no` |
| 重试后通过 | **各次结果都打印**（含「本次为第 2 次尝试 / 第 1 次为何失败」），明确标注 **「抖动、重试后通过」**，信号行 `retried=yes absorbed=yes` |
| 全部尝试都失败 | **真红**，报错信息里带**各次结果对比**，信号行 `retried=yes absorbed=no` |
| 失败且不允许重试 | **直接红**，报错信息注明「第一原则：不允许重试」+ 原因 + 本轮结果 |

（k73-M2：上表的「次数」一律由**实际尝试数**推出 —— 文案经 `_cn_count` 取词，
默认上界 2 时读作「两次…」，不写死。）

## 抖动率可观测（每次跑都留信号）

每次跑（过、红、跳过出异常）都打印一行固定前缀：

    [EVAL-FLAKE] label=<标签> attempts=<n>/<上界> outcome=PASS|FAIL|ERROR integrity=full|incomplete|n/a retried=yes|no absorbed=yes|no|n/a [note=...]

同一行（前缀加北京时间戳）**追加**写入 `logs/eval_flake_signal.log`
（路径可用 `EVAL_FLAKE_LOG` 覆盖）。为什么必须落盘：pytest 默认捕获 stdout，
**通过用例的 print 不进终端** —— 不落盘就等于「抖动被吸收了但没人看见」。
落盘是 best-effort：写失败绝不影响判定。

统计口径（举例）::

    # 本次是否用过重试（抖动吸收次数）:
    grep -c 'retried=yes absorbed=yes' logs/eval_flake_signal.log
    # 抖动率 = 吸收次数 / 总跑次:
    grep -h '\[EVAL-FLAKE\]' logs/eval_flake_signal.log | awk '{...}'

## 抖动率数字的强度（k73-M3：如实标注，勿把外推当实测）

上面用到的自然抖动率 `p ≈ 25%`，目前的观测支撑只有「**4 次里红过 1 次**」
——是**粗估**（样本仅 4 次，误差极大），**不是运行指标**。由此外推的
「连续两次失败 ≈ p² ≈ 6%」**公式正确，但精度高于观测**（拿 4 样本估出的 p
去平方）。⇒ 引用任何抖动率/残留假红率数字之前，先看
`logs/eval_flake_signal.log` 里 `retried=*` 的统计把 p 估准；样本攒够之前，
这些数字只当**量级提示**，不得当实测值引用。
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: 尝试次数上界：总尝试 ≤ 2 ⇒ 最多 1 次重试（**不许更多**）
MAX_ATTEMPTS = 2

#: 尝试次数**硬上界**（k73-M2）：控制方拍板「最多 1 次重试」⇒ `max_attempts`
#: 一律不许超过 2。由 `run_with_bounded_retry` 入参校验强制（**构造**而非约定；
#: 越界抛 ValueError，连跑都不跑）。要改它必须先由控制方重新拍板。
MAX_ATTEMPTS_HARD_CAP = 2

#: 抖动信号固定前缀（可被 grep；每次跑都打，过红都打）
SIGNAL_PREFIX = "[EVAL-FLAKE]"

#: 信号落盘默认路径（仓库 logs/ 已 gitignore）
DEFAULT_SIGNAL_LOG = (Path(__file__).resolve().parent.parent
                      / "logs" / "eval_flake_signal.log")


# ================================================================
# 第一原则的判据：本轮到底「跑起来没有」
# ================================================================

def execution_integrity(metrics: dict, *, executed_key: str = "executed",
                        skipped_key: str = "skipped",
                        error_keys=("exceptions",),
                        total_key: str = "total"):
    """评测是否「确实完整执行了」。返回 `(ok, reason)`。

    `ok is False` ⇒ **决不允许重试**（k61 那类「评测根本没跑」的全落这里）：

    - `executed == 0`（全跳过 / 无任务）——**k61 打红那条的形式**；
    - 有 skip（种子注入失败 / 隔离库复制失败 / 键缺失 / DB 快照缺失 …）；
    - `executed != total`（执行不完整）；
    - 有异常（`exceptions` / `judge_error` 之类的清单非空，基础设施或主链失败）。

    各层指标字段名不同（L1/L2/L4 = `executed`+`exceptions`，
    L3 = `judged`+`judge_error`），由调用方按层传参。
    **字段缺失按「没跑」处理（fail-closed）**：信息不足时宁可红，不可重试。

    k73-M1（k68 复审 Minor）：上面这条 fail-closed 承诺原先**代码没做到** ——
    完整性判据写作 `executed is not None and total is not None and executed !=
    total`，**缺 `total` 时整条判据被跳过**（fail-open）：复审构造
    `{"executed": 3, "skipped": []}`（半执行、无 total）会被判「可重试」。
    现在**缺键即判 problem**：信息不足 ⇒ 不放行。
    """
    problems = []
    executed = metrics.get(executed_key)
    total = metrics.get(total_key)
    skipped = metrics.get(skipped_key) or []
    if executed is None:
        problems.append(
            f"{executed_key} 缺失（信息不足 ⇒ 按「没跑」处理，fail-closed）")
    elif not executed:
        problems.append(f"{executed_key}={executed!r}（没有任何任务真的执行）")
    if skipped:
        problems.append(f"被跳过 {len(skipped)} 条: {skipped[:5]}")
    if total is None:
        # k73-M1：**缺 total 不得放行**（原实现此处跳过完整性判据 = fail-open；
        # 一个自称「宁严勿宽」的函数不许留这种分支）。
        problems.append(
            f"{total_key} 缺失（无法判定执行完整性 ⇒ 按「没跑」处理，fail-closed）")
    elif executed is not None and executed != total:
        problems.append(
            f"{executed_key}={executed} != {total_key}={total}（执行不完整）")
    for k in error_keys:
        errs = metrics.get(k) or []
        if errs:
            problems.append(f"{k}={errs[:5]}（基础设施/主链失败，非阈值问题）")
    if problems:
        return False, "；".join(problems)
    return True, f"{executed_key}={executed} 全部执行 / 无跳过 / 无异常"


def make_check(*, ok: bool, summary: str, metrics: dict, **integrity_kwargs):
    """装配「本轮尝试的判定记录」。

    - `ok=True`  → 本轮通过（不涉及重试）；`summary` 为一行结果摘要；
    - `ok=False` → 用 `execution_integrity` 判**是否允许重试**：
      「确实跑起来了但没达标」⇒ 允许；其余一律不允许（第一原则）。

    无论过红都记录 `integrity`（本轮是否确实完整执行）——**如实上报**：
    某些层的门禁不是阈值门禁（如 L2 只断言「跳过须有原因」），全跳过也可能
    判「通过」；此时信号行会带 `integrity=incomplete`，不会伪装成「真跑过」。
    """
    ran, why = execution_integrity(metrics, **integrity_kwargs)
    return {"ok": bool(ok), "summary": _one_line(summary),
            "retryable": (not ok) and bool(ran),
            "block_reason": "" if (ok or ran) else why,
            "integrity": bool(ran), "integrity_reason": why}


# ================================================================
# 执行器
# ================================================================

def run_with_bounded_retry(label: str, run_once, check,
                           max_attempts: int = MAX_ATTEMPTS):
    """有界重试执行器：总尝试 ≤ `max_attempts`（默认 2 = 最多 1 次重试）。

    - `run_once(attempt_no)`（1 基）→ payload：跑第 `attempt_no` 次。
      **抛异常 = 本轮失败且不可重试**（结构性断言失败 / pytest.skip /
      基础设施异常）—— 打信号行后**原样上抛**，绝不吞（第一原则）。
    - `check(payload)` → `make_check` 产出的记录。

    **上界（k73-M2）**：`max_attempts` 必须落在 `1..MAX_ATTEMPTS_HARD_CAP`
    （= 1..2，控制方拍板「最多 1 次重试」）。越界**直接 ValueError 且一次都不跑**
    —— 上界是**构造**约束，不是文档约定（原实现只断言 `>= 1`：显式传 5 会真跑
    5 次、输出 `attempts=5/5`）。要调大必须先由控制方重新拍板。

    返回第 1 次通过的 payload；**所有尝试都不通过 ⇒ AssertionError（含各次结果
    对比，次数由实际尝试数推出，文案不写死「两次」）**；**不允许重试的失败 ⇒
    AssertionError（含第一原则说明 + 本轮结果）**。
    """
    if not (1 <= max_attempts <= MAX_ATTEMPTS_HARD_CAP):
        raise ValueError(
            f"[k68] max_attempts={max_attempts} 越界：只允许 1.."
            f"{MAX_ATTEMPTS_HARD_CAP}（控制方拍板的上界 = {MAX_ATTEMPTS_HARD_CAP} "
            f"次尝试 = 最多 {MAX_ATTEMPTS_HARD_CAP - 1} 次重试）。"
            "上界是构造约束（k73-M2），不许调大 —— 调大等于用更多重试掩盖抖动；"
            "确有需要请由控制方重新拍板并同步改 MAX_ATTEMPTS_HARD_CAP。")
    records = []                      # [(attempt_no, check_dict)]
    for attempt_no in range(1, max_attempts + 1):
        try:
            payload = run_once(attempt_no)
        except BaseException as exc:  # noqa: BLE001 —— Skip/断言/基础设施异常一律不重试
            _report_block_line(label, attempt_no, max_attempts, exc)
            _emit_signal(label, attempt_no, max_attempts, outcome="ERROR",
                         retried=False, absorbed="n/a",
                         note=f"本轮抛异常({type(exc).__name__})"
                              "——第一原则：不重试，原样上抛")
            raise
        rec = check(payload)
        records.append((attempt_no, rec))
        _report_attempt_line(label, attempt_no, max_attempts, rec)
        if rec["ok"]:
            if attempt_no == 1:
                _emit_signal(label, 1, max_attempts, outcome="PASS",
                             retried=False, absorbed="n/a", rec=rec)
            else:
                _report_flake_absorbed(label, records)
                _emit_signal(label, attempt_no, max_attempts, outcome="PASS",
                             retried=True, absorbed="yes", rec=rec,
                             note="抖动、重试后通过；"
                                  f"第 1 次失败原因: {records[0][1]['summary']}")
            return payload
        if not rec["retryable"]:
            _report_not_retryable(label, attempt_no, max_attempts, rec)
            _emit_signal(label, attempt_no, max_attempts, outcome="FAIL",
                         retried=False, absorbed="n/a", rec=rec,
                         note=f"不允许重试（第一原则）: {rec['block_reason']}")
            raise AssertionError(
                f"[k68] {label} 评测未达标，且**不允许重试**（第一原则：只有"
                f"「确实完整执行了、只是阈值差一点」才允许重试）："
                f"{rec['block_reason']}；本轮结果: {rec['summary']}")
        if attempt_no == max_attempts:
            n_cn = _cn_count(len(records))     # k73-M2：按**实际尝试数**取词
            _report_both_failed(label, records)
            _emit_signal(label, attempt_no, max_attempts, outcome="FAIL",
                         retried=True, absorbed="no", rec=rec,
                         note=f"{n_cn}次都失败 ⇒ 真红（非抖动）；"
                              f"{n_cn}次结果: {_compare(records)}")
            raise AssertionError(
                f"[k68] {label} 连续 {max_attempts} 次尝试均未达标（重试上界 "
                f"{max_attempts} = 最多 {max_attempts - 1} 次重试）——"
                f"这不是抖动，是真回归。{n_cn}次结果对比: {_compare(records)}")
        # 允许重试 → 继续下一轮
        print(f"[EVAL-FLAKE] {label} 第 {attempt_no}/{max_attempts} 次未达标"
              f"（已完整执行，属阈值问题）→ 重试第 {attempt_no + 1} 次"
              f"（上界 {max_attempts} 次尝试）", flush=True)
    raise AssertionError(  # pragma: no cover —— 循环内必然 return/raise
        f"[k68] {label} 不可达：max_attempts={max_attempts}")


# ================================================================
# 上报（打印 + 落盘）
# ================================================================

def _compare(records) -> str:
    return " || ".join(f"第 {n} 次: {c['summary']}" for n, c in records)


#: 尝试次数 → 中文量词（1→「一」，2→「两」…）
_CN_NUM = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五",
           6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}


def _cn_count(n: int) -> str:
    """尝试次数 → 文案量词（k73-M2）。

    复审实测：`max_attempts=5` 真跑 5 次时，`_report_both_failed` 仍硬写
    「两次都失败」——**谎报尝试次数**。凡涉及次数的文案一律经本函数取词，
    不许硬编码「两次」：上界一旦被控制方调整，文案必须跟着变。
    """
    return _CN_NUM.get(n, str(n))


def _report_attempt_line(label, attempt_no, max_attempts, rec):
    if rec["ok"]:
        status = "PASS"
        tail = ""
    elif rec["retryable"]:
        status = "FAIL（阈值未达标，已完整执行）"
        tail = ""
    else:
        status = "FAIL（不可重试）"
        tail = f" —— {rec['block_reason']}"
    print(f"[EVAL-FLAKE] {label} 第 {attempt_no}/{max_attempts} 次尝试："
          f"{status} —— {rec['summary']}{tail}", flush=True)


def _report_block_line(label, attempt_no, max_attempts, exc):
    """本轮以异常收场（结构性断言失败 / skip / 基础设施异常）——一律不重试。"""
    print(f"[EVAL-FLAKE] {label} 第 {attempt_no}/{max_attempts} 次尝试：ERROR —— "
          f"带异常收场（{type(exc).__name__}: {exc}）"
          "；第一原则：结构性/基础设施失败一律**不重试**，原样上抛", flush=True)


def _report_not_retryable(label, attempt_no, max_attempts, rec):
    print(f"[EVAL-FLAKE] {label} 第 {attempt_no}/{max_attempts} 次尝试：FAIL —— "
          f"**不允许重试**（第一原则：重试只能用于「评测真的跑起来了、只是阈值差"
          f"一点」）：{rec['block_reason']}", flush=True)


def _report_flake_absorbed(label, records):
    print(f"[EVAL-FLAKE] {label}：**抖动、重试后通过**"
          f"（本次为第 {len(records)} 次尝试；第 1 次为何失败 → "
          f"{records[0][1]['summary']}）", flush=True)
    print(f"[EVAL-FLAKE] {label} {_cn_count(len(records))}次结果: "
          f"{_compare(records)}", flush=True)


def _report_both_failed(label, records):
    n_cn = _cn_count(len(records))     # k73-M2：不写死「两次」
    print(f"[EVAL-FLAKE] {label}：{n_cn}次都失败 ⇒ **真红**（不是抖动）；"
          f"{n_cn}次结果对比: {_compare(records)}", flush=True)


def _emit_signal(label, attempt_no, max_attempts, *, outcome, retried, absorbed,
                 rec=None, note=""):
    integrity = "n/a" if rec is None else ("full" if rec["integrity"]
                                           else "incomplete")
    if rec is not None and not rec["integrity"] and not note:
        note = f"本轮未完整执行: {rec['integrity_reason']}"
    line = (f"{SIGNAL_PREFIX} label={label} attempts={attempt_no}/{max_attempts} "
            f"outcome={outcome} integrity={integrity} "
            f"retried={'yes' if retried else 'no'} absorbed={absorbed}"
            + (f" note={note}" if note else ""))
    print(line, flush=True)
    _append_signal_log(line)


def signal_log_path() -> Path:
    """信号落盘路径（`EVAL_FLAKE_LOG` 可覆盖；默认仓库 `logs/eval_flake_signal.log`）。"""
    override = os.environ.get("EVAL_FLAKE_LOG", "").strip()
    return Path(override) if override else DEFAULT_SIGNAL_LOG


def _append_signal_log(line: str) -> None:
    """best-effort 追加信号行（**写失败绝不影响判定**）。"""
    try:
        path = signal_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{_bj_now()} {line}\n")
    except Exception:  # noqa: BLE001 —— 只做观测，不许因它改变过/红
        pass


def _bj_now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _one_line(text: str) -> str:
    return " / ".join(str(text).splitlines()).strip()
