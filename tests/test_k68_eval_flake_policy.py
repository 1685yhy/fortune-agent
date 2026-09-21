# -*- coding: utf-8 -*-
"""k68 抖动策略测试：有界重试上界 / 第一原则 / 上报 / 信号可 grep。

本文件把「上一批踩过的坑」钉成回归测试。验收 A/B/C/D 四条各有**确定性**覆盖
（不依赖 LLM，秒级）+ 真 LLM 覆盖（`RUN_EVAL_K68_FLAKE_PROOF=1` 触发，见下）：

| 条 | 命题 | 覆盖 |
|---|---|---|
| A | 真回归仍会红（重试没把真失败洗白） | `A0`（确定性）+ `A1`（真 LLM，真跑两轮） |
| B | `executed=0` 不重试、直接红 | `B0*`（确定性，真实 `l1_eval` 指标）+ `B1`（真实 `run_eval` 的「隔离库复制失败 → 全跳过」形态） |
| C | 抖动被吸收但**被上报** | `C0`（确定性）+ `C1`（真 LLM：第 1 轮失败、第 2 轮通过） |
| D | 抖动率信号可 grep | `test_signal_line_is_greppable_and_persisted` |

**第一原则（本批的命门）**：重试只能用于「评测真的跑起来了、只是阈值差一点」。
**决不允许重试**：`executed == 0` / 有 skip / 基础设施失败 / 结构性断言失败 /
主链异常 —— k61 当初打红那条的形式**正是 `executed=0` 全跳过**；无差别重试会把
这种真故障吞掉，我们就再也发现不了 k61 那类破坏。

真 LLM 覆盖为何默认 skip：A1/C1 是「真实两跑」证明，跑一次 ~1-2min，且 C1 依赖
第 2 轮真跑达标（真实 LLM 抖动）——**放进默认门禁会把本批刚消掉的抖动又装回去**。
故按仓库既有约定（`RUN_EVAL_L1_FULL_SMOKE` / `RUN_EVAL_E6_GATE_RED` 同款）用环境
变量触发：``RUN_EVAL_K68_FLAKE_PROOF=1 pytest tests/test_k68_eval_flake_policy.py -s``
"""
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_EVAL_DIR = _REPO / "scripts" / "eval_agent"
_TESTS_DIR = Path(__file__).resolve().parent
for _p in (_TESTS_DIR, _REPO, _EVAL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402

import eval_flake_retry  # noqa: E402
import l1_eval  # noqa: E402

#: 真 LLM 证明用的轻量任务（chat 域 no_tool，实测 ~10s/条，主链真跑）
_PROOF_TASKS = ("T070", "T071")


# ================================================================
# 造数：用**真实** l1_eval.aggregate_metrics 产出指标（不是手写 dict）
# ================================================================

def _result(**kw):
    base = {"id": "TX", "skipped": False, "no_tool": False,
            "tool_select_ok": False, "params_ok": False, "ok": False,
            "actual_calls": [], "exception": None}
    base.update(kw)
    return base


def _metrics_all_skipped(n=7):
    """k61 打红那条的形态：全部 skip ⇒ executed=0（隔离库复制失败/键缺失）。"""
    return l1_eval.aggregate_metrics([
        _result(id=f"T{i:03d}", skipped=True,
                skip_reason="隔离库复制失败: FileNotFoundError: fortune.db")
        for i in range(1, n + 1)])


def _metrics_threshold_miss(passed=6, failed=1):
    """真实抖动形态：确实全部执行、只是阈值差一点（工具选择 6/7 = 85.7% < 90%）。"""
    res = [_result(id=f"P{i:03d}", tool_select_ok=True, params_ok=True, ok=True)
           for i in range(passed)]
    res += [_result(id=f"F{i:03d}", actual_calls=[("hehun", {})])
            for i in range(failed)]
    return l1_eval.aggregate_metrics(res)


def _metrics_ok(n=7):
    return l1_eval.aggregate_metrics([
        _result(id=f"T{i:03d}", tool_select_ok=True, params_ok=True, ok=True)
        for i in range(1, n + 1)])


def _metrics_partial_skip():
    res = [_result(id="T1", tool_select_ok=True, params_ok=True, ok=True),
           _result(id="T2"),
           _result(id="T3", skipped=True, skip_reason="种子注入失败")]
    return l1_eval.aggregate_metrics(res)


def _metrics_with_exception():
    res = [_result(id="T1", tool_select_ok=True, params_ok=True, ok=True),
           _result(id="T2", exception="TimeoutError: 任务超时（>300s）")]
    return l1_eval.aggregate_metrics(res)


def _summary(m):
    return (f"工具选择 {m['tool_selection_accuracy']:.1%} "
            f"({m['tool_selection_passed']}/{m['executed']}) | "
            f"跳过={m['skipped']}")


def _check_of(m):
    return eval_flake_retry.make_check(
        ok=l1_eval.thresholds_met(m), summary=_summary(m), metrics=m)


@pytest.fixture(autouse=True)
def _isolate_signal_log(tmp_path, monkeypatch):
    """信号落盘重定向到 tmp（绝不写仓库 logs/）。"""
    monkeypatch.setenv("EVAL_FLAKE_LOG", str(tmp_path / "eval_flake_signal.log"))


def _signal_lines(tmp_path):
    f = tmp_path / "eval_flake_signal.log"
    if not f.exists():
        return []
    return [ln for ln in f.read_text(encoding="utf-8").splitlines()
            if eval_flake_retry.SIGNAL_PREFIX in ln]


# ================================================================
# 第一原则判据（execution_integrity）——各形态矩阵
# ================================================================

def test_integrity_matrix_real_metrics():
    """「确实跑起来了」的判据（真实 aggregate_metrics 产出）。"""
    ok, why = eval_flake_retry.execution_integrity(_metrics_ok())
    assert ok is True and "executed=7" in why
    # 阈值不达标但确实执行了 ⇒ 唯一允许重试的形态
    ok, why = eval_flake_retry.execution_integrity(_metrics_threshold_miss())
    assert ok is True
    # k61 形态：全跳过 ⇒ executed=0
    ok, why = eval_flake_retry.execution_integrity(_metrics_all_skipped())
    assert ok is False and "executed=0" in why
    # 部分跳过 ⇒ 不完整执行
    ok, why = eval_flake_retry.execution_integrity(_metrics_partial_skip())
    assert ok is False and "被跳过 1 条" in why and "T3" in why
    # 有异常（主链/基础设施）⇒ 不是「阈值差一点」
    ok, why = eval_flake_retry.execution_integrity(_metrics_with_exception())
    assert ok is False and "exceptions" in why
    # 字段缺失 ⇒ fail-closed（信息不足时宁可红，不可重试）
    ok, why = eval_flake_retry.execution_integrity({"foo": 1})
    assert ok is False and "executed" in why


def test_integrity_l3_style_keys_judged_and_judge_error():
    """L3 口径（judged / judge_error）也走同一判据。"""
    ok, _ = eval_flake_retry.execution_integrity(
        {"judged": 2, "total": 2, "skipped": [], "judge_error": []},
        executed_key="judged", error_keys=("judge_error",))
    assert ok is True
    ok, why = eval_flake_retry.execution_integrity(
        {"judged": 1, "total": 2, "skipped": ["T005"], "judge_error": ["T001"]},
        executed_key="judged", error_keys=("judge_error",))
    assert ok is False and "T001" in why


# ================================================================
# A（确定性）：真回归 ⇒ 两次都红；上界 = 2 次尝试
# ================================================================

def test_A0_real_regression_reds_and_never_exceeds_two_attempts(
        capsys, tmp_path):
    """评测**确实完整执行**但阈值不达标（真回归形态）⇒ 重试 1 次仍红 ⇒ 报错含两次对比。"""
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        return _metrics_threshold_miss()

    with pytest.raises(AssertionError) as ei:
        eval_flake_retry.run_with_bounded_retry("A0", _run, _check_of)

    # 上界：总尝试恰为 2（= 1 次重试），**绝不 3 次**
    assert calls == [1, 2]
    msg = str(ei.value)
    assert "两次结果对比" in msg                    # 报错带两次结果
    assert msg.count("工具选择 85.7%") == 2         # 两次的指标都在
    assert "真回归" in msg
    out = capsys.readouterr().out
    assert "两次都失败" in out and "真红" in out
    assert "outcome=FAIL" in out and "retried=yes" in out
    assert "absorbed=no" in out
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1 and "retried=yes absorbed=no" in lines[0]
    assert "integrity=full" in lines[0]             # 确实跑起来了（不是「没跑」）


# ================================================================
# B（确定性 + 真实 run_eval）：评测没跑起来 ⇒ 不重试、直接红
# ================================================================

def test_B0_executed_zero_never_retries(capsys, tmp_path):
    """k61 的形态：全跳过 ⇒ `executed=0` ⇒ **一次都不重试**，直接红。"""
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        return _metrics_all_skipped()

    with pytest.raises(AssertionError) as ei:
        eval_flake_retry.run_with_bounded_retry("B0", _run, _check_of)

    assert calls == [1]                              # 关键：没有第 2 次
    msg = str(ei.value)
    assert "不允许重试" in msg and "第一原则" in msg
    assert "executed=0" in msg
    out = capsys.readouterr().out
    assert "不允许重试" in out and "executed=0" in out
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1
    assert "retried=no" in lines[0] and "outcome=FAIL" in lines[0]
    assert "integrity=incomplete" in lines[0]


def test_B0b_partial_skip_never_retries():
    """部分跳过（种子注入失败）⇒ 同样不重试。"""
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        return _metrics_partial_skip()

    with pytest.raises(AssertionError):
        eval_flake_retry.run_with_bounded_retry("B0b", _run, _check_of)
    assert calls == [1]


def test_B0c_exception_never_retries_and_propagates():
    """本轮抛异常（结构性断言/基础设施）⇒ 一律不重试、原样上抛（绝不吞）。"""
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        raise RuntimeError("隔离库复制失败: FileNotFoundError")

    with pytest.raises(RuntimeError, match="隔离库复制失败"):
        eval_flake_retry.run_with_bounded_retry("B0c", _run, _check_of)
    assert calls == [1]


def test_B0d_skip_semantics_preserved():
    """`pytest.skip` 不被策略吞掉/改判（缺 key 的既有约定语义不变）。"""
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        pytest.skip("缺少 ZHIPU_API_KEY（glm-4-flash 路由）")

    with pytest.raises(pytest.skip.Exception):
        eval_flake_retry.run_with_bounded_retry("B0d", _run, _check_of)
    assert calls == [1]


class _FakeSettings:
    """伪运行期：把「源库」指到一个不存在的路径 ⇒ 复刻 DB 快照缺失。"""

    def __init__(self, db_path):
        self.db_path = db_path


def _fake_runtime(tmp_path):
    return {"settings": _FakeSettings("/nonexistent/k68-no-such.db"),
            "tmp_root": tmp_path, "build_handler": None}


def test_B1_real_run_eval_all_skipped_never_retries(
        tmp_path, monkeypatch):
    """**真实 `run_eval` 的「隔离库复制失败 → 全跳过」形态**（零 LLM：任务在
    种子阶段就跳过了）。

    走的是 `test_eval_l1._run_smoke` 的**真实接线**（真实 `_load_tasks`、
    真实策略、真实断言），只把「key/库就绪」与运行期引导换成伪件 —— 复刻
    k61 打红那条的形式，验证：**只有 1 次尝试（不重试）+ 直接红**。
    """
    import test_eval_l1 as l1_file

    monkeypatch.setattr(l1_file, "_llm_keys_ready", lambda: True)
    monkeypatch.setattr(l1_file, "_real_db_ready", lambda: True)
    monkeypatch.setattr(l1_eval, "_init_runtime",
                        lambda model_route="glm": _fake_runtime(tmp_path))
    counted = []
    real_run_eval = l1_eval.run_eval

    def _counting(*a, **kw):
        counted.append(1)
        return real_run_eval(*a, **kw)

    monkeypatch.setattr(l1_eval, "run_eval", _counting)

    with pytest.raises(AssertionError) as ei:
        l1_file._run_smoke(tmp_path, ["T070", "T071"], "k68-B1")

    assert len(counted) == 1, "评测没跑起来（executed=0）⇒ 决不允许重试"
    assert "不允许重试" in str(ei.value) and "executed=0" in str(ei.value)
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1 and "retried=no" in lines[0]
    assert "integrity=incomplete" in lines[0]


def test_B2_pass_with_incomplete_run_is_reported_as_incomplete(
        capsys, tmp_path):
    """门禁不是阈值门禁的层（如 L2 只断言「跳过须有原因」）即使判「通过」，
    信号行也必须写明 `integrity=incomplete` —— 不许伪装成「真跑过」。"""
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        return _metrics_all_skipped()

    out_payload = eval_flake_retry.run_with_bounded_retry(
        "B2", _run,
        lambda m: eval_flake_retry.make_check(
            ok=True, summary=_summary(m), metrics=m))
    assert calls == [1] and out_payload["executed"] == 0
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1
    assert "outcome=PASS" in lines[0] and "integrity=incomplete" in lines[0]
    assert "本轮未完整执行" in lines[0]


# ================================================================
# C（确定性）：抖动被吸收但**必须上报**
# ================================================================

def test_C0_flake_absorbed_is_green_and_reported(capsys, tmp_path):
    """第 1 次失败、第 2 次通过 ⇒ 绿，但两次结果都打印 + 标明「抖动、重试后通过」。"""
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        return _metrics_threshold_miss() if attempt_no == 1 else _metrics_ok()

    payload = eval_flake_retry.run_with_bounded_retry("C0", _run, _check_of)

    assert calls == [1, 2]
    assert payload["executed"] == 7 and payload["tool_selection_passed"] == 7
    out = capsys.readouterr().out
    assert "抖动、重试后通过" in out
    assert "本次为第 2 次尝试" in out
    assert "第 1 次为何失败" in out
    assert "工具选择 85.7% (6/7)" in out      # 第 1 次的结果被打印（不许吞）
    assert "工具选择 100.0% (7/7)" in out      # 第 2 次的结果也打印
    assert "两次结果" in out
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1
    assert "retried=yes" in lines[0] and "absorbed=yes" in lines[0]
    assert "outcome=PASS" in lines[0] and "integrity=full" in lines[0]
    assert "抖动、重试后通过" in lines[0]


def test_C0b_clean_pass_reports_retried_no(capsys, tmp_path):
    """第 1 次就过 ⇒ 1 次尝试，信号 `retried=no`（抖动率分母）。"""
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        return _metrics_ok()

    eval_flake_retry.run_with_bounded_retry("C0b", _run, _check_of)
    assert calls == [1]
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1 and "retried=no" in lines[0] \
        and "outcome=PASS integrity=full" in lines[0]


# ================================================================
# D：抖动率信号可被 grep / 可被机器解析
# ================================================================

def test_signal_line_is_greppable_and_persisted(capsys, tmp_path):
    """每次跑都留一行固定前缀信号（落盘）；字段可机器解析（抖动率可统计）。"""
    log = tmp_path / "eval_flake_signal.log"

    # 跑次 1：干净通过
    eval_flake_retry.run_with_bounded_retry(
        "D-clean", lambda n: _metrics_ok(), _check_of)
    # 跑次 2：抖动吸收
    eval_flake_retry.run_with_bounded_retry(
        "D-flake",
        lambda n: _metrics_threshold_miss() if n == 1 else _metrics_ok(),
        _check_of)

    lines = _signal_lines(tmp_path)
    assert len(lines) == 2, lines                      # 每次跑一行，过红都留
    parsed = []
    for ln in lines:
        fields = dict(f.split("=", 1)
                      for f in ln.split(eval_flake_retry.SIGNAL_PREFIX)[1]
                      .strip().split(" ")[:6])
        parsed.append(fields)
    assert parsed[0]["label"] == "D-clean"
    assert parsed[0]["retried"] == "no" and parsed[0]["outcome"] == "PASS"
    assert parsed[1]["label"] == "D-flake"
    assert parsed[1]["retried"] == "yes" and parsed[1]["absorbed"] == "yes"
    # 抖动率 = retried=yes 的行数 / 总行数 = 1/2
    assert sum(1 for f in parsed if f["retried"] == "yes") == 1
    assert log.read_text(encoding="utf-8").count("[EVAL-FLAKE]") == 2


# ================================================================
# k73-M1（k68 复审 Minor）：第一原则判据**不得有 fail-open 分支**
# ================================================================

def _check_plain(m):
    """不做阈值判定的记录器（本组只考察「本轮是否允许重试」这一条）。"""
    return eval_flake_retry.make_check(ok=False, summary=str(m), metrics=m)


#: (说明, metrics, 缺失的键)
M1_CASES = [
    ("缺 total（复审构造的原始形态：半执行、无 total）",
     {"executed": 3, "skipped": []}, "total"),
    ("缺 executed", {"total": 3, "skipped": []}, "executed"),
    ("空 metrics（键全缺）", {}, "executed"),
]


def test_M1_missing_keys_are_fail_closed_not_retryable(capsys, tmp_path):
    """k73-M1：缺 `total` / 缺 `executed` / metrics 为空 ⇒ **一律按「没跑」处理**：

    - `execution_integrity` 判 False（信息不足宁可红）；
    - 执行器 **1 次尝试即红**（`calls == [1]`），**一次都不重试**。

    改前实测（复审构造 `{"executed": 3, "skipped": []}`）：完整性判据整条被跳过
    （`executed is not None and total is not None and executed != total`）⇒ 被判
    「可重试」⇒ `calls == [1, 2]` —— 正是「**评测没跑完却被当成抖动重试**」。
    """
    for label, metrics, missing in M1_CASES:
        ok, why = eval_flake_retry.execution_integrity(metrics)
        assert ok is False, f"{label}: 必须 fail-closed，实际 ok={ok}"
        assert missing in why, f"{label}: 原因须点名缺哪个键，实际 {why!r}"

    for label, metrics, missing in M1_CASES:
        calls = []

        def _run(attempt_no, _m=metrics, _c=calls):
            _c.append(attempt_no)
            return dict(_m)

        with pytest.raises(AssertionError) as ei:
            eval_flake_retry.run_with_bounded_retry(
                f"M1-{missing}", _run, _check_plain)
        assert calls == [1], f"{label}: 缺键 ⇒ 一次都不重试，实际 {calls}"
        assert "不允许重试" in str(ei.value), str(ei.value)
        assert "第一原则" in str(ei.value)
        assert missing in str(ei.value)

    out = capsys.readouterr().out
    assert "不允许重试" in out


def test_M1_contrast_total_present_is_still_retryable(tmp_path):
    """对照（证明上一条不是「一律判红」）：**同形态但 total 齐全** ⇒ 仍允许重试。

    「完整执行、只是阈值差一点」两次 ⇒ `calls == [1, 2]` 后真红 —— 第一原则要
    保护的抖动重试路径没被 k73-M1 的收紧误伤（这是本组最关键的反向守卫）。
    """
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        return {"executed": 3, "total": 3, "skipped": []}

    with pytest.raises(AssertionError) as ei:
        eval_flake_retry.run_with_bounded_retry("M1-ok", _run, _check_plain)
    assert calls == [1, 2], "total 齐全 ⇒ 允许 1 次重试"
    assert "真回归" in str(ei.value)
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1 and "integrity=full" in lines[0]


def test_M1_all_four_layers_metrics_carry_total():
    """k73-M1 的**输入契约**：L1/L2/L3/L4 的 metrics 必须都带 `total`。

    为什么必须钉住：k73-M1 之后「缺 total」= 「没跑」（fail-closed）⇒ 若某层的
    metrics 悄悄不再产出 `total`，该层会被**误判为未完整执行**（信号
    `integrity=incomplete`；L1 还会被禁掉重试）。故用**四层真实的**指标产出函数
    （不是手写 dict）确认契约成立 —— 这是 fail-closed 不放宽的前提条件。
    """
    import judge as judge_mod
    import l2_eval
    import l4_eval

    l1m = l1_eval.aggregate_metrics(
        [_result(id="T1", tool_select_ok=True, params_ok=True, ok=True)])
    l2m = l2_eval.aggregate_metrics(
        [{"id": "T1", "skipped": False, "ok": True, "exception": None}])
    l4m = l4_eval.aggregate_metrics(
        [{"id": "T1", "skipped": False, "first_ok": True, "passed": True,
          "attempts": [], "skip_reason": None}])
    jdims = {d: {"score": 8.0} for d in judge_mod.DIMS}
    l3m = judge_mod.group_metrics(
        [{"id": "T1", "skipped": False, "judge_error": None, "dims": jdims,
          "overall": 8.0, "category": "chat", "severity": "P0"}])

    for name, m, ok_key in (("l1", l1m, "executed"), ("l2", l2m, "executed"),
                            ("l3", l3m, "judged"), ("l4", l4m, "executed")):
        assert "total" in m, f"{name} metrics 必须带 total（fail-closed 输入契约）"
        assert m[ok_key] == m["total"], f"{name}: 单条全通过时 {ok_key} 应等于 total"
        kwargs = ({"executed_key": "judged", "error_keys": ("judge_error",)}
                  if name == "l3" else {})
        ok, why = eval_flake_retry.execution_integrity(m, **kwargs)
        assert ok is True, f"{name} 真实指标必须判「完整执行」，实际 {why!r}"


# ================================================================
# k73-M2（k68 复审 Minor）：上界由**构造**强制 + 文案随实际次数
# ================================================================

def test_M2_max_attempts_hard_cap_enforced_by_construction(capsys, tmp_path):
    """k73-M2：上界 = 控制方拍板的 2（最多 1 次重试），越界**构造性**拒绝。

    复审实测改前：显式传 `max_attempts=5` 会**真跑 5 次尝试**（输出
    `attempts=5/5`）—— 上界当时只是文档约定，不阻止调用方调大。
    本测试同时把「拍板值 = 2」钉成常量断言，防止日后被悄悄调大。
    """
    assert eval_flake_retry.MAX_ATTEMPTS_HARD_CAP == 2, \
        "控制方拍板的上界 = 2 次尝试（最多 1 次重试）；要改须先由控制方拍板"
    assert eval_flake_retry.MAX_ATTEMPTS <= eval_flake_retry.MAX_ATTEMPTS_HARD_CAP, \
        "默认上界不得超过硬上界"

    for bad in (-1, 0, 3, 5, 100):
        calls = []

        def _run(attempt_no, _c=calls):
            _c.append(attempt_no)
            return _metrics_ok()

        with pytest.raises(ValueError) as ei:
            eval_flake_retry.run_with_bounded_retry(
                f"M2-cap{bad}", _run, _check_of, max_attempts=bad)
        assert calls == [], f"max_attempts={bad} 越界 ⇒ 一次都不许跑，实际 {calls}"
        assert "max_attempts" in str(ei.value) and "上界" in str(ei.value)

    # 合法边界仍可用：1（不许重试）与 2（默认上界）
    for good in (1, 2):
        assert eval_flake_retry.run_with_bounded_retry(
            f"M2-cap-ok{good}", lambda n: _metrics_ok(), _check_of,
            max_attempts=good)["executed"] == 7
    capsys.readouterr()


def test_M2_wording_follows_actual_attempt_count(capsys, tmp_path):
    """k73-M2：次数文案随**实际尝试数**变 —— 不许硬编码「两次」。

    用合法的 `max_attempts=1` 造出「只跑 1 次就真红」：改前文案会谎报
    「两次都失败」（复审实测：5 次时照写「两次」）。
    默认上界 2 时的「两次…」措辞由 A0/C0 的既有断言继续锁住。
    """
    calls = []

    def _run(attempt_no):
        calls.append(attempt_no)
        return _metrics_threshold_miss()

    with pytest.raises(AssertionError) as ei:
        eval_flake_retry.run_with_bounded_retry(
            "M2-wording", _run, _check_of, max_attempts=1)
    assert calls == [1]
    out = capsys.readouterr().out
    assert "一次都失败" in out, out
    assert "两次" not in out, f"只跑了 1 次尝试，文案不得出现「两次」：{out}"
    msg = str(ei.value)
    assert "一次结果对比" in msg, msg
    assert "两次" not in msg, msg
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1
    assert "attempts=1/1" in lines[0] and "一次都失败" in lines[0]
    # 取词函数单测：本批把「>2 次」的路径走成**构造不可达**（硬上界挡住），
    # 故该路径只能在此单测兜底 —— 保证上界若被控制方日后调整，文案仍跟着变。
    assert eval_flake_retry._cn_count(2) == "两"
    assert eval_flake_retry._cn_count(5) == "五"
    assert eval_flake_retry._cn_count(11) == "11"


# ================================================================
# A1 / C1（真 LLM，`RUN_EVAL_K68_FLAKE_PROOF=1` 触发）：真实评测上的两跑证明
# ================================================================

def _proof_gate():
    """真 LLM 证明的公共门控（缺 key/库/开关即 skip —— 仓库既有约定同款）。"""
    if os.environ.get("RUN_EVAL_K68_FLAKE_PROOF") != "1":
        pytest.skip("真 LLM 抖动证明由 RUN_EVAL_K68_FLAKE_PROOF=1 触发"
                    "（真实两跑：A1 真回归必红 / C1 第一次失败第二次通过）")
    import test_eval_l1 as l1_file
    if not l1_file._llm_keys_ready():
        pytest.skip("缺少 ZHIPU_API_KEY（glm-4-flash 路由）")
    if not l1_file._real_db_ready():
        pytest.skip("真实库不存在: %s" % l1_file._DEFAULT_DB)
    return l1_file


def test_A1_real_regression_still_reds(tmp_path, monkeypatch, capsys):
    """A（真 LLM）：把比对层人为改到不可能满足 ⇒ **评测真跑**（LLM 真调用、任务
    真执行、指标真算、落盘真写）但阈值确实达不到 ⇒ **两次都失败 ⇒ 红**。

    证明：**重试没有把真失败洗白**（这是本批最该守住的边界）。
    """
    l1_file = _proof_gate()
    real_compare = l1_eval.compare_expected
    state = {"attempt": 0, "compare_calls": 0}
    real_run_eval = l1_eval.run_eval

    def _counting_run_eval(*a, **kw):
        state["attempt"] += 1
        return real_run_eval(*a, **kw)

    def _always_fail(expected, actual, allow_tools=None):
        state["compare_calls"] += 1
        real_compare(expected, actual, allow_tools=allow_tools)
        return (False, False,
                "[k68-A1 人为构造] 期望工具序列被改到不可能满足（等价真回归）")

    monkeypatch.setattr(l1_eval, "run_eval", _counting_run_eval)
    monkeypatch.setattr(l1_eval, "compare_expected", _always_fail)

    with pytest.raises(AssertionError) as ei:
        l1_file._run_smoke(tmp_path, list(_PROOF_TASKS), "k68-A1")

    assert state["attempt"] == 2, "上界 = 2 次尝试（1 次重试）"
    assert state["compare_calls"] == 2 * len(_PROOF_TASKS), "两次尝试都真跑了任务"
    msg = str(ei.value)
    assert "两次结果对比" in msg and "真回归" in msg
    out = capsys.readouterr().out
    # 留证：完整转写落盘（capsys 会吞掉通过用例的 stdout，落盘才留得下原始输出）
    (tmp_path / "k68-A1-transcript.txt").write_text(out, encoding="utf-8")
    assert "两次都失败" in out and "真红" in out
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1
    assert "retried=yes absorbed=no" in lines[0]
    assert "integrity=full" in lines[0], "两次都确实完整执行（不是「没跑」）"


def test_C1_real_first_fail_second_pass_is_reported(tmp_path, monkeypatch,
                                                   capsys):
    """C（真 LLM）：第 1 次尝试人为构造失败（真跑但达不到阈值）、第 2 次真跑干净
    ⇒ **绿**，且两次结果都被打印、明确标出「抖动、重试后通过」。

    说明（如实）：第 1 次的失败是**人为构造**的（自然抖动的实测口径 ~25% 由控制
    方留档），第 2 次是**真实 LLM 跑**——本测试证明的是**重试链路 + 上报链路在
    真实评测上可用**，不冒充「今天自然抖了一次」。

    k73-M3（观测强度如实标注，勿当实测引用）：上面的 **~25% 是粗估** —— 观测
    支撑只有控制方上一批「**4 次里红过 1 次**」，样本 4 次、误差很大；由此外推的
    「连续两次失败 ≈ p² ≈ 6%」**公式正确但精度高于观测**。引用任何抖动率/残留
    假红率之前，先看 `logs/eval_flake_signal.log` 的 `retried=*` 统计把 p 估准。
    """
    l1_file = _proof_gate()
    real_compare = l1_eval.compare_expected
    state = {"attempt": 0, "compare_calls": 0}
    real_run_eval = l1_eval.run_eval

    def _counting_run_eval(*a, **kw):
        state["attempt"] += 1
        return real_run_eval(*a, **kw)

    def _first_attempt_fails(expected, actual, allow_tools=None):
        state["compare_calls"] += 1
        if state["attempt"] == 1:
            return (False, False,
                    "[k68-C1 人为构造] 第 1 次尝试：期望被改到不可能满足")
        return real_compare(expected, actual, allow_tools=allow_tools)

    monkeypatch.setattr(l1_eval, "run_eval", _counting_run_eval)
    monkeypatch.setattr(l1_eval, "compare_expected", _first_attempt_fails)

    m = l1_file._run_smoke(tmp_path, list(_PROOF_TASKS), "k68-C1")

    assert state["attempt"] == 2 and l1_eval.thresholds_met(m)
    assert state["compare_calls"] == 2 * len(_PROOF_TASKS)
    out = capsys.readouterr().out
    # 留证：完整转写落盘（capsys 会吞掉通过用例的 stdout，落盘才留得下原始输出）
    (tmp_path / "k68-C1-transcript.txt").write_text(out, encoding="utf-8")
    assert "抖动、重试后通过" in out
    assert "本次为第 2 次尝试" in out and "第 1 次为何失败" in out
    assert out.count("[smoke k68-C1] 第 1 次尝试：") == 1   # 两次结果都打印
    assert out.count("[smoke k68-C1] 第 2 次尝试：") == 1
    assert "工具选择 0.0% (0/2)" in out                     # 第 1 次（人为构造）的指标
    assert "工具选择 100.0% (2/2)" in out                   # 第 2 次（真跑）的指标
    lines = _signal_lines(tmp_path)
    assert len(lines) == 1
    assert "retried=yes absorbed=yes" in lines[0]
    assert "outcome=PASS" in lines[0] and "integrity=full" in lines[0]
    # 两次尝试各自的落盘产物都在（可复核原始数据）
    for name in ("l1-smoke", "l1-smoke-retry2"):
        for fname in ("meta.json", "results.json", "report.md"):
            assert (tmp_path / name / fname).exists(), (name, fname)
    results = json.loads((tmp_path / "l1-smoke-retry2" / "results.json")
                         .read_text(encoding="utf-8"))
    assert all(r["skipped"] is False for r in results)   # 第 2 次确实真跑
