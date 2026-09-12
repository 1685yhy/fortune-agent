#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k39 S1 测试：L3 门禁「边界/攻击类」剔除口径 + 双数公示 + 判卷校准脚本。

覆盖（brief S1 三条）：
1. 分类：边界/攻击类判据可判定（`category == "edge"`，唯一事实源
   `edge_scope`），被归类清单与评估集实际一致；
2. 改门禁：L3 `weighted_avg` / `p0_avg` 用**剔除后**口径判门禁，同时
   `weighted_avg_incl_edge` / `p0_avg_incl_edge` 如实公示含边界口径；
   **L1/L2 对边界任务一分不减**（视图不带 category，结构上无法剔除，
   且边界任务失败确实拉低 L1/L2 指标）；
3. 判卷校准脚本 `judge_calibrate.py` 纯函数 + `--dry-run` 端到端可手动跑。

红线：本文件只读 `data/eval/agent_tasks.jsonl`；零 LLM 调用（校准脚本测试
全部走 `--dry-run`）；不触碰生产库。
"""
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_EVAL_DIR = _REPO / "scripts" / "eval_agent"
for _p in (_REPO, _EVAL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402

import edge_scope  # noqa: E402
import judge  # noqa: E402
import runner  # noqa: E402
import report  # noqa: E402
import judge_calibrate  # noqa: E402


# ================================================================
# 1. 分类判据（可判定标记）
# ================================================================

def _load_tasks():
    p = judge.TASKS_PATH
    if not p.exists():
        pytest.skip("评估集不存在: %s" % p)
    return [json.loads(line) for line in
            p.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_edge_marker_is_category_based():
    """判据 = category 字段的 edge 域（唯一结构化标记，非人工挑选）。"""
    assert edge_scope.EDGE_CATEGORY == "edge"
    assert edge_scope.is_edge_category("edge") is True
    assert edge_scope.is_edge_category("chat") is False
    assert edge_scope.is_edge_category(None) is False
    assert edge_scope.is_edge_category("EDGE") is False  # 大小写敏感，不做模糊匹配
    # 任务定义 dict 与判卷记录 dict 同一判据
    assert edge_scope.is_edge_record({"category": "edge"}) is True
    assert edge_scope.is_edge_record({"category": "paipan"}) is False
    assert edge_scope.is_edge_record(None) is False
    assert edge_scope.is_edge_record({}) is False


def test_edge_classification_list_matches_documented_set():
    """被归类清单 = 评估集里 category=edge 的 20 条（T081–T100），逐个可核。"""
    tasks = _load_tasks()
    ids = edge_scope.edge_ids(tasks)
    assert len(ids) == 20, f"边界类应为 20 条，实际 {len(ids)}: {ids}"
    assert ids == [f"T{i:03d}" for i in range(81, 101)], ids
    # 每条的 category 字面就是 edge（判据自证）
    by_id = {t["id"]: t for t in tasks}
    assert all(by_id[i]["category"] == "edge" for i in ids)


def test_split_edge_keeps_order_and_does_not_exclude_chat_tasks():
    """分类不扩大：`chat` 域的无关问题（T074）**不**进边界清单。"""
    tasks = _load_tasks()
    main, edge = edge_scope.split_edge(tasks)
    assert len(main) + len(edge) == len(tasks) == 108
    main_ids = [t["id"] for t in main]
    assert "T074" in main_ids  # 无关问题在 chat 域，不属边界判据
    assert "T074" not in edge_scope.edge_ids(tasks)
    assert all(t["category"] != "edge" for t in main)


# ================================================================
# 2. L3 聚合：剔除边界 + 双数公示
# ================================================================

def _result(id_, severity, category, score, judge_error=False):
    return {"id": id_, "category": category, "severity": severity,
            "title": "t", "skipped": False, "skip_reason": "",
            "judge_error": judge_error, "judge_error_reason": "",
            "dims": {d: {"score": score, "judge_error": False,
                         "justification": ""} for d in judge.DIMS},
            "overall": score}


def test_group_metrics_excludes_edge_from_gate_but_publishes_both():
    """边界任务从门禁口径剔除；含边界口径仍如实输出（两个数都在）。"""
    results = [
        _result("T1", "P0", "paipan", 8.0),   # 非边界
        _result("T2", "P1", "fortune", 8.0),  # 非边界
        _result("T81", "P2", "edge", 0.0),    # 边界（空消息类）
        _result("T83", "P0", "edge", 2.0),    # 边界（攻击类）
    ]
    m = judge.group_metrics(results)
    # 门禁口径：只算非边界 → (8+8)/2
    assert m["weighted_avg"] == pytest.approx(8.0)
    assert m["p0_avg"] == pytest.approx(8.0) and m["p0_count"] == 1
    # 副指标：含边界 → (8+8+0+2)/4
    assert m["weighted_avg_incl_edge"] == pytest.approx(4.5)
    assert m["p0_avg_incl_edge"] == pytest.approx(5.0)
    assert m["p0_count_incl_edge"] == 2
    # 剔除清单如实留档
    assert m["judged_excl_edge"] == 2
    assert sorted(m["edge_excluded"]) == ["T81", "T83"]
    assert m["judged"] == 4


def test_group_metrics_no_edge_keeps_legacy_numbers_identical():
    """无边界任务时两个口径**必须相等**（不改变既有 run 的数字）。"""
    results = [_result("T1", "P0", "paipan", 8.0),
               _result("T2", "P1", "chat", 6.0)]
    m = judge.group_metrics(results)
    assert m["weighted_avg"] == m["weighted_avg_incl_edge"] == pytest.approx(7.0)
    assert m["p0_avg"] == m["p0_avg_incl_edge"] == pytest.approx(8.0)
    assert m["edge_excluded"] == []
    # 全量口径（历史可比）未受影响
    assert m["weighted_avg_incl_errors"] == pytest.approx(7.0)
    assert m["by_category"]["chat"] == pytest.approx(6.0)


def test_group_metrics_per_dimension_and_by_category_stay_full_scope():
    """五维/分域平均保持全量（含边界）口径——只动门禁两项，不扩大改动面。"""
    results = [_result("T1", "P1", "paipan", 8.0),
               _result("T81", "P2", "edge", 0.0)]
    m = judge.group_metrics(results)
    assert m["per_dimension"]["accuracy"] == pytest.approx(4.0)
    assert m["by_category"]["edge"] == pytest.approx(0.0)
    assert m["by_category"]["paipan"] == pytest.approx(8.0)


# ================================================================
# 3. 门禁判定：用剔除后口径判，含边界只作副指标
# ================================================================

def _gate_inputs(**over):
    l1 = {"tool_selection_accuracy": 0.99, "param_accuracy": 0.99,
          "false_call_rate": 0.0}
    l2 = {"assertion_pass_rate": 1.0}
    l3 = {"weighted_avg": 8.0, "p0_avg": 8.5}
    l4 = {"p0_passk_rate": 1.0, "p0_denominator": 5,
          "tsr": 0.95, "tsr_denominator": 5}
    for k in ("l1", "l2", "l3", "l4"):
        if k in over:
            locals()[k].update(over[k])
    return (l1, l2, l3, l4)


def test_gate_l3_uses_excl_edge_number():
    """门禁看「不含边界」数：不含边界 7.6 达标 → PASS（哪怕含边界只有 6.0）。"""
    l3 = {"weighted_avg": 7.6, "p0_avg": 8.2,
          "weighted_avg_incl_edge": 6.0, "p0_avg_incl_edge": 6.5,
          "edge_excluded": ["T083", "T086"]}
    g = report.evaluate_gate(*_gate_inputs(l3=l3), mode="regression")
    assert g["verdict"] is True
    row = next(r for r in g["rows"] if r["metric"] == "weighted_avg")
    assert row["value"] == pytest.approx(7.6)
    assert "剔除边界/攻击类 2 条" in row["note"]


def test_gate_sub_rows_carry_incl_edge_but_never_change_verdict():
    """副指标只公示、不倒灌门禁：不含边界 7.4 不达标 → RED（含边界再高也没用）。"""
    l3 = {"weighted_avg": 7.4, "p0_avg": 8.5,
          "weighted_avg_incl_edge": 9.9, "p0_avg_incl_edge": 9.9,
          "edge_excluded": ["T081"]}
    g = report.evaluate_gate(*_gate_inputs(l3=l3), mode="regression")
    assert g["verdict"] is False
    subs = {r["metric"]: r for r in g["sub_rows"]}
    assert subs["weighted_avg_incl_edge"]["value"] == pytest.approx(9.9)
    assert subs["p0_avg_incl_edge"]["value"] == pytest.approx(9.9)
    # 副指标不进 rows（不参与 all(rows.pass) 判定）
    assert "weighted_avg_incl_edge" not in {r["metric"] for r in g["rows"]}


def test_gate_table_renders_both_numbers_and_marks_sub_rows():
    l3 = {"weighted_avg": 7.6, "p0_avg": 8.2,
          "weighted_avg_incl_edge": 5.98, "p0_avg_incl_edge": 6.54,
          "edge_excluded": ["T083"]}
    md = report.render_gate_table(
        report.evaluate_gate(*_gate_inputs(l3=l3), mode="regression"))
    assert "7.6" in md          # 门禁口径
    assert "5.98" in md         # 副指标（含边界）
    assert "6.54" in md
    assert "不计入门禁判定" in md
    assert "PASS" in md and "门禁判定" in md


def test_gate_without_edge_keys_renders_no_sub_table():
    """老 run（无 incl_edge 键）不渲染副指标表——向后兼容。"""
    g = report.evaluate_gate(*_gate_inputs(), mode="capability")
    assert g["sub_rows"] == []
    assert "副指标" not in report.render_gate_table(g)


# ================================================================
# 4. L1 / L2 对边界任务**一分不减**（仍硬门禁）
# ================================================================

def _record(id_, category, *, l1_ok, l2_ok, no_tool=False, severity="P1"):
    return {
        "id": id_, "category": category, "severity": severity,
        "title": "t", "no_tool": no_tool, "skipped": False,
        "skip_reason": "", "has_state_checks": True,
        "attempts": [{
            "exception": None, "skipped": False,
            "l1": {"tool_select_ok": l1_ok, "params_ok": l1_ok, "ok": l1_ok,
                   "actual_calls": []},
            "l2": {"ok": l2_ok, "checks": [], "replies_preview": ["x"]},
            "l4": {"ok": True, "detail": ""},
        }],
        "l3": _result(id_, severity, category, 0.0),
    }


def test_l1_l2_views_carry_no_category_so_edge_cannot_be_excluded():
    """L1/L2 聚合视图结构上不带 category → 无法按类别剔除（一分不减）。"""
    recs = [_record("T001", "paipan", l1_ok=True, l2_ok=True),
            _record("T081", "edge", l1_ok=True, l2_ok=True)]
    captured = {}
    real_l1 = runner.l1_eval.aggregate_metrics
    real_l2 = runner.l2_eval.aggregate_metrics

    def spy_l1(view):
        captured["l1_keys"] = {k for r in view for k in r}
        return real_l1(view)

    def spy_l2(view):
        captured["l2_keys"] = {k for r in view for k in r}
        return real_l2(view)

    runner.l1_eval.aggregate_metrics = spy_l1
    runner.l2_eval.aggregate_metrics = spy_l2
    try:
        runner._aggregate_layers(recs)
    finally:
        runner.l1_eval.aggregate_metrics = real_l1
        runner.l2_eval.aggregate_metrics = real_l2
    assert "category" not in captured["l1_keys"]
    assert "category" not in captured["l2_keys"]
    assert "edge_scope" not in Path(runner.__file__).read_text(encoding="utf-8")


def test_edge_task_failure_still_drags_l1_and_l2_down():
    """边界任务失败照样进 L1/L2 分母（≠ L3 的剔除口径）。"""
    ok = _record("T001", "paipan", l1_ok=True, l2_ok=True)
    bad_edge = _record("T081", "edge", l1_ok=False, l2_ok=False)
    m = runner._aggregate_layers([ok, bad_edge])
    assert m["l1"]["tool_selection_accuracy"] == pytest.approx(0.5)
    assert m["l2"]["assertion_pass_rate"] == pytest.approx(0.5)
    assert "T081" in m["l1"]["failed"] and "T081" in m["l2"]["failed"]


# ================================================================
# 5. 校准脚本（纯函数 + --dry-run 端到端；零 LLM）
# ================================================================

def test_calibrate_sample_selection_deterministic_and_covers_domains():
    recs = [{"id": f"T{i:03d}", "category": c, "skipped": False,
             "l3": {"replies": ["r"]}}
            for i, c in enumerate(["paipan"] * 3 + ["edge"] * 2 + ["chat"] * 30,
                                  start=1)]
    ids = judge_calibrate.select_sample_ids(recs, sample=20)
    assert len(ids) == 20 and len(set(ids)) == 20
    assert ids == judge_calibrate.select_sample_ids(recs, sample=20)  # 确定性
    # 尽域覆盖：每个域的最小 id 都在
    assert {"T001", "T004", "T006"} <= set(ids)


def test_calibrate_delta_stats_known_vector():
    pairs = [(8.0, 9.0), (6.0, 6.0), (7.0, 5.0), (5.0, 5.5)]
    st = judge_calibrate.delta_stats(pairs)
    assert st["n"] == 4
    assert st["free_mean"] == pytest.approx(6.5)
    assert st["strong_mean"] == pytest.approx(6.375)
    assert st["delta_mean"] == pytest.approx(-0.125)
    # 总体标准差（population）：deltas=[+1.0, 0.0, -2.0, +0.5]
    assert st["delta_std"] == pytest.approx(1.1388, abs=1e-3)
    assert st["delta_min"] == -2.0 and st["delta_max"] == 1.0
    assert st["agree_rate_within_1"] == pytest.approx(0.75)  # -2.0 那一对不一致
    # 7.5 阈值：free/strong 各仅 8.0/9.0 一条达标，无翻转
    assert st["crossings"]["7.5"]["free_pass"] == 1
    assert st["crossings"]["7.5"]["strong_pass"] == 1
    assert st["crossings"]["7.5"]["flips"] == 0


def test_calibrate_threshold_equivalence_verdicts():
    """噪声 ≥ 阈值间距 → 不可区分；噪声远小于间距 → 可区分（弱）。"""
    noisy = judge_calibrate.delta_stats([(8.0, 8.6), (7.0, 6.4), (5.0, 5.6)])
    v = judge_calibrate.threshold_equivalence(noisy)
    assert v["gap"] == pytest.approx(0.5)
    assert v["verdict"] == "不可区分"
    tight = judge_calibrate.delta_stats([(8.0, 8.05), (7.0, 6.98),
                                         (5.0, 5.02), (6.0, 6.01)])
    v2 = judge_calibrate.threshold_equivalence(tight)
    assert v2["verdict"].startswith("可区分")


def test_calibrate_lattice_analysis_flags_values_between_thresholds():
    la = judge_calibrate.lattice_analysis([7.5, 7.5, 8.0, 6.0, 7.8])
    assert 7.8 in la["values_between_thresholds"]
    assert la["threshold_reachable"] == {"7.5": True, "8.0": True}
    assert la["grid_step"] == 0.05
    assert la["count_lt_low"] == 1 and la["count_ge_low"] == 4  # 6.0 / 7.5×2+7.8+8.0
    assert la["count_in_gap"] == 1 and la["count_ge_high"] == 1


def test_calibrate_empirical_equivalence_without_strong_model():
    """无强模型对照时的零成本经验判据：8.0 近似不可达 → 两阈值不可区分。"""
    # 100 条里 ≥8.0 只有 1 条（<5%）→ 8.0 是"近似不可达"线
    la = judge_calibrate.lattice_analysis([6.0] * 94 + [7.5] * 3 + [7.7, 7.8]
                                          + [8.15])
    eq = judge_calibrate.empirical_equivalence(la)
    assert eq["n"] == 100 and eq["count_ge_high"] == 1
    assert "近似不可达" in eq["verdict"]
    # 反向：高分侧有实际落点（≥8.0 占比 ≥5%）→ 至少取值空间上可分辨
    la2 = judge_calibrate.lattice_analysis([6.0] * 80 + [8.0] * 20)
    eq2 = judge_calibrate.empirical_equivalence(la2)
    assert eq2["verdict"] == "存在可分辨迹象"
    assert judge_calibrate.empirical_equivalence(
        judge_calibrate.lattice_analysis([]))["verdict"] == "无法判定"


def test_calibrate_cli_dry_run_on_real_run_writes_both_bases(tmp_path):
    """手工跑真实 run（只读副本）：dry-run 也给出零成本等价性判据与两个口径。"""
    real = _REPO / "data" / "eval" / "results" / "unified-20260912-162737"
    if not (real / "results.json").exists():
        pytest.skip("E6 run 只读副本不存在")
    out = tmp_path / "real"
    proc = subprocess.run(
        [sys.executable, str(_EVAL_DIR / "judge_calibrate.py"),
         "--run", str(real), "--out", str(out), "--dry-run"],
        capture_output=True, text=True, cwd=str(_REPO))
    assert proc.returncode == 0, proc.stderr
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["dry_run"] is True
    assert meta["lattice"]["n"] >= 100          # 整个 run 的分布
    assert meta["threshold_equivalence"]["basis"].startswith("零成本经验判据")
    assert meta["strong_model"] == "glm-4-plus"
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "免费判卷取值分布" in md and "阈值等价性" in md


def _synthetic_run_dir(tmp_path):
    """造一个最小 run 目录（results.json + meta.json）供 --dry-run 端到端。"""
    d = tmp_path / "run"
    d.mkdir()
    recs = []
    for i in range(25):
        rid = f"T{i + 1:03d}"
        recs.append({
            "id": rid, "category": "edge" if i % 5 == 0 else "paipan",
            "severity": "P0" if i % 3 == 0 else "P1", "title": "t",
            "skipped": False, "attempts": [{"l2": {"replies_preview": ["回复"]}}],
            "l3": {"overall": 4.0 + (i % 9) * 0.5,
                   "dims": {d2: {"score": 5.0} for d2 in judge.DIMS},
                   "judge_error": False, "replies": ["回复"]},
        })
    (d / "results.json").write_text(json.dumps(recs, ensure_ascii=False),
                                    encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"run_at": "x", "metrics": {}}),
                                 encoding="utf-8")
    return d


def test_calibrate_cli_dry_run_is_manually_runnable(tmp_path):
    """`--dry-run` 端到端可手动跑通（零 key 零 LLM），产物齐全。"""
    run_dir = _synthetic_run_dir(tmp_path)
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(_EVAL_DIR / "judge_calibrate.py"),
         "--run", str(run_dir), "--out", str(out), "--dry-run"],
        capture_output=True, text=True, cwd=str(_REPO))
    assert proc.returncode == 0, proc.stderr
    for name in ("meta.json", "calibration.json", "report.md"):
        assert (out / name).exists(), f"缺产物 {name}"
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["dry_run"] is True and len(meta["samples"]) >= 20
    assert meta["strong_model"] == judge_calibrate.STRONG_JUDGE_MODEL
    assert meta["free_model"] == judge.JUDGE_MODEL
    assert meta["lattice"]["observed_unique"] >= 2
    assert "阈值" in (out / "report.md").read_text(encoding="utf-8")


_BALANCE_ERR = '429 {"code":"1113","message":"余额不足或无可用资源包,请充值。"}'


def _stub_strong_judge(monkeypatch, fail_ids=None, score=7.0):
    """桩掉 `_call_strong`（零网络、零花费）。

    - `fail_ids is None` → 全部失败（模拟余额不足）；
    - `fail_ids` 集合 → 只让这些任务失败（按判卷 user 提示词里的【任务】id 匹配）；
    - 其余 → 返回合法五维 JSON（指定分），供"判卷成功"路径断言。
    """
    note = json.dumps({d: {"score": score, "justification": "桩判卷"}
                       for d in judge.DIMS}, ensure_ascii=False)

    def stub(api_key, messages, model, **kw):
        # 强模型绝不能走 judge._call_glm（其红线断言锁死 glm-4-flash）
        assert model == judge_calibrate.STRONG_JUDGE_MODEL, model
        if fail_ids is None:
            raise RuntimeError(_BALANCE_ERR)
        user_prompt = messages[-1]["content"]
        if any(tid in user_prompt for tid in fail_ids):
            raise RuntimeError(_BALANCE_ERR)
        return note

    monkeypatch.setattr(judge_calibrate, "_call_strong", stub)


def test_calibrate_all_judge_errors_is_invalid_and_exits_nonzero(tmp_path,
                                                                monkeypatch):
    """k39 S1 修正：全错误 → 明确报「校准无效」+ 非零退出 + **无任何 delta 统计**。

    改前缺陷：失败样本的 0 分兜底值被计入统计 → 假数字
    （strong_mean 0.0 / delta_mean -6.682 / agree_rate 0.0）。
    """
    run_dir = _synthetic_run_dir(tmp_path)
    out = tmp_path / "invalid"
    _stub_strong_judge(monkeypatch)          # 全部样本判卷失败
    rc = judge_calibrate.main(
        ["--run", str(run_dir), "--out", str(out), "--api-key", "fake-key"])
    assert rc == 4, "全部判卷失败必须非零退出（校准无效）"
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["invalid"] is True
    assert meta["error_rate"] == 1.0
    assert meta["stats"] is None, "校准无效时不得输出任何 delta 统计"
    assert meta["invalid_reason"] and "校准无效" in meta["invalid_reason"]
    assert len(meta["judge_errors"]) == len(meta["strong_attempted"]) >= 20
    md = (out / "report.md").read_text(encoding="utf-8")
    # 顶部显著提示：只在 error_rate == 0 时可用
    assert md.splitlines()[2].startswith("> ⚠️") and "error_rate == 0" in md
    assert "校准无效" in md
    # 无 delta 数字（改前的假数字一个都不许出现）
    assert "delta_mean" not in md and "strong_mean" not in md
    assert "agree_rate_within_1" not in md
    # 阈值跨越**表**（标题 + 表头）不得渲染（banner 的说明文字里含该词，故查标题）
    assert "### 阈值跨越" not in md
    assert "| 阈值 | free 达标" not in md
    assert "-6.682" not in md


def test_calibrate_partial_judge_errors_excluded_from_stats(tmp_path,
                                                            monkeypatch):
    """部分判卷失败 → 从分差统计/阈值跨越里剔除，error_rate 如实、退出码 3。"""
    run_dir = _synthetic_run_dir(tmp_path)
    out = tmp_path / "partial"
    ids = [f"T{i:03d}" for i in range(1, 26)]
    _stub_strong_judge(monkeypatch, fail_ids=set(ids[:5]))  # 前 5 条失败
    rc = judge_calibrate.main(
        ["--run", str(run_dir), "--out", str(out), "--api-key", "fake-key"])
    assert rc == 3
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["invalid"] is False
    n_att = len(meta["strong_attempted"])
    n_err = len(meta["judge_errors"])
    assert n_att >= 20 and n_err == 5
    # error_rate 如实 = 失败/尝试（不是"0.0"这类假数字）
    assert meta["error_rate"] == pytest.approx(n_err / n_att, abs=1e-4)
    assert meta["stats"] is not None
    # 统计样本数 = 尝试数 − 失败数（失败样本绝不进均值）
    assert meta["stats"]["n"] == n_att - n_err
    assert set(meta["stats"]["judge_errors"]) == set(meta["judge_errors"])
    # 失败样本的 0 分兜底值绝不参与均值：strong_mean 必须 = 桩分 7.0（而非被拉低）
    assert meta["stats"]["strong_mean"] == pytest.approx(7.0)
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "error_rate" in md and "判卷失败" in md
    # 失败样本在逐条表里显式标注，而不是显示 0 分
    for tid in meta["judge_errors"]:
        line = next(l for l in md.splitlines() if l.startswith(f"| {tid} |"))
        assert "判卷失败" in line and "| 0.0 |" not in line


def test_calibrate_clean_run_reports_zero_error_rate(tmp_path, monkeypatch):
    """对照：全部判卷成功 → error_rate == 0、退出码 0、报告可用。"""
    run_dir = _synthetic_run_dir(tmp_path)
    out = tmp_path / "clean"
    _stub_strong_judge(monkeypatch, fail_ids=set())  # 不注入任何失败
    rc = judge_calibrate.main(
        ["--run", str(run_dir), "--out", str(out), "--api-key", "fake-key"])
    assert rc == 0
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["error_rate"] == 0.0 and meta["invalid"] is False
    assert meta["stats"]["n"] == len(meta["strong_attempted"])


def test_calibrate_cli_refuses_to_run_without_key(tmp_path):
    """非 dry-run 且无 key → 明确退出码 2（不静默、不误跑付费调用）。"""
    import os
    run_dir = _synthetic_run_dir(tmp_path)
    out = tmp_path / "out2"
    env = {k: v for k, v in os.environ.items() if k != "ZHIPU_API_KEY"}
    proc = subprocess.run(
        [sys.executable, str(_EVAL_DIR / "judge_calibrate.py"),
         "--run", str(run_dir), "--out", str(out)],
        capture_output=True, text=True, cwd=str(_REPO), env=env)
    assert proc.returncode == 2
    assert "ZHIPU_API_KEY" in (proc.stderr + proc.stdout)
