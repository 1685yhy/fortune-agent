"""E6 统一运行器测试（runner.py + report.py + judge.py 修正 + validate 放宽 + DDL 对齐）。

覆盖（task-E6-brief 测试要求）：
1. runner CLI 单测：参数解析/退出码语义（mock 各层返回 → 门禁 PASS/RED 判定）
2. 门禁判定单测：每层阈值表逐行（值略低于阈值=RED，达标=PASS）；任一层 RED=exit 1
3. report 对比单测：--compare-with 两目录分数对比 + 涨跌标注（↑/↓/→）
4. rubric 修正单测：judge prompt 含四类锚点文字（引导/错域/核查/稳定）
5. validate_tasks state_checks 数值断言放宽（_equals int，与 state_verifier 对齐）
6. qian_saves.kind DDL 对齐（E6 残留 #4）：旧 schema 库经生产 QianDAO 迁移后
   种子成功（T051/T053/T057 不再依赖 judge.py shim）
7. 互斥排队纪律程序化：pidfile 锁冲突 → exit 3；陈旧锁覆盖
8. 冒烟实跑（keys + 真实库存在时）：--tasks 轻量实跑 + 门禁红验证
   （RUN_EVAL_E6_GATE_RED=1：T017 破坏 canonical_tool_name → RED → 还原 → PASS）
9. 校准重跑一致率：30 条样本重判（RUN_EVAL_E6_FULL_CALIBRATION=1 全量；
   默认 3 条快速子集），±1 分内一致率如实报

红线（本文件零生产写入）：data/eval/agent_tasks.jsonl 只读；--no-ledger 贯穿
（不污染台账唯一事实源）；运行期隔离（临时库 + USER_MEMORY_DIR/CHARTS_DIR
重定向）由 l1_eval 兜底；单测不触碰真实 ledger.json（monkeypatch 掉 append）。
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_EVAL_DIR = _REPO / "scripts" / "eval_agent"
for _p in (_REPO, _EVAL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402

import l1_eval  # noqa: E402
import l2_eval  # noqa: E402
import l4_eval  # noqa: E402
import judge  # noqa: E402
import state_verifier  # noqa: E402
import validate_tasks  # noqa: E402
import interceptor  # noqa: E402
import runner  # noqa: E402
import report  # noqa: E402

_DEFAULT_DB = "/mnt/d/fortune-data/userdata/fortune.db"


def _llm_keys_ready():
    return bool(os.environ.get("ZHIPU_API_KEY"))


def _real_db_ready():
    return Path(_DEFAULT_DB).exists()


def _load_tasks():
    p = l1_eval.TASKS_PATH
    if not p.exists():
        pytest.skip("评估集不存在: %s" % p)
    return [json.loads(line) for line in p.read_text(encoding="utf-8")
            .splitlines() if line.strip()]


def _env_snapshot():
    return {k: os.environ.get(k) for k in
            ("USER_MEMORY_DIR", "CHARTS_DIR", "EXPERIENCE_MODE",
             "EVAL_RUNNER_SKIP_MUTEX")}


def _restore_env(snap):
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _mutex_skip(monkeypatch):
    """单测统一跳过互斥检测（真实 CLI 运行的互斥由 runner 自己守）。"""
    monkeypatch.setenv("EVAL_RUNNER_SKIP_MUTEX", "1")


# ================================================================
# 门禁判定单测（report.evaluate_gate，阈值表唯一事实源逐行）
# ================================================================

def _gate_inputs(**over):
    l1 = {"tool_selection_accuracy": 0.95, "param_accuracy": 0.95,
          "false_call_rate": 0.0}
    l2 = {"assertion_pass_rate": 1.0}
    l3 = {"weighted_avg": 8.0, "p0_avg": 8.5}
    l4 = {"p0_passk_rate": 1.0, "p0_denominator": 5,
          "tsr": 0.95, "tsr_denominator": 5}
    for k in ("l1", "l2", "l3", "l4"):
        if k in over:
            locals()[k].update(over[k])
    return (l1, l2, l3, l4)


def _gate_row(g, metric):
    return next(r for r in g["rows"] if r["metric"] == metric)


def test_gate_l1_tool_selection_boundary():
    """L1 工具选择：0.89→RED（capability 阈值 0.90），0.90→PASS。"""
    b = _gate_inputs(l1={"tool_selection_accuracy": 0.89})
    g = report.evaluate_gate(*b, mode="capability")
    assert not _gate_row(g, "tool_selection")["pass"]
    b = _gate_inputs(l1={"tool_selection_accuracy": 0.90})
    g = report.evaluate_gate(*b, mode="capability")
    assert _gate_row(g, "tool_selection")["pass"]
    assert g["verdict"]


def test_gate_l1_param_and_false_call():
    """L1 参数 0.89→RED/0.90→PASS；误调 0.01→RED/0.0→PASS。"""
    b = _gate_inputs(l1={"param_accuracy": 0.89})
    g = report.evaluate_gate(*b, mode="capability")
    assert not _gate_row(g, "param")["pass"]
    b = _gate_inputs(l1={"param_accuracy": 0.90, "false_call_rate": 0.01})
    g = report.evaluate_gate(*b, mode="capability")
    assert _gate_row(g, "param")["pass"]
    assert not _gate_row(g, "false_call")["pass"]


def test_gate_l1_false_call_none_note():
    """无 no_tool 任务 → false_call_rate=None → 按通过处理 + 显式注释。"""
    b = _gate_inputs(l1={"false_call_rate": None})
    g = report.evaluate_gate(*b, mode="capability")
    row = _gate_row(g, "false_call")
    assert row["pass"] and row.get("note")


def test_gate_l2_assertion_boundary():
    """L2 断言通过率 99.9%→RED/100%→PASS（回归硬门禁）。"""
    b = _gate_inputs(l2={"assertion_pass_rate": 0.999})
    g = report.evaluate_gate(*b, mode="capability")
    assert not _gate_row(g, "assertion_pass_rate")["pass"]
    b = _gate_inputs(l2={"assertion_pass_rate": 1.0})
    g = report.evaluate_gate(*b, mode="capability")
    assert _gate_row(g, "assertion_pass_rate")["pass"]


def test_gate_l3_boundaries():
    """L3 加权 7.49→RED/7.5→PASS；P0 平均 7.99→RED/8.0→PASS。"""
    b = _gate_inputs(l3={"weighted_avg": 7.49, "p0_avg": 8.5})
    g = report.evaluate_gate(*b, mode="capability")
    assert not _gate_row(g, "weighted_avg")["pass"]
    b = _gate_inputs(l3={"weighted_avg": 7.5, "p0_avg": 7.99})
    g = report.evaluate_gate(*b, mode="capability")
    assert _gate_row(g, "weighted_avg")["pass"]
    assert not _gate_row(g, "p0_avg")["pass"]
    b = _gate_inputs(l3={"weighted_avg": 7.5, "p0_avg": 8.0})
    g = report.evaluate_gate(*b, mode="capability")
    assert g["verdict"]


def test_gate_l3_no_judged_note():
    """无已判卷任务 → weighted/p0=None → RED + 显式注释。"""
    b = _gate_inputs(l3={"weighted_avg": None, "p0_avg": None})
    g = report.evaluate_gate(*b, mode="capability")
    assert not g["verdict"]
    assert _gate_row(g, "weighted_avg").get("note")


def test_gate_l4_boundaries():
    """L4 P0 pass³ 99%→RED/100%→PASS；TSR 89%→RED/90%→PASS。"""
    b = _gate_inputs(l4={"p0_passk_rate": 0.99, "p0_denominator": 5})
    g = report.evaluate_gate(*b, mode="capability")
    assert not _gate_row(g, "p0_passk")["pass"]
    b = _gate_inputs(l4={"p0_passk_rate": 1.0, "tsr": 0.89})
    g = report.evaluate_gate(*b, mode="capability")
    assert not _gate_row(g, "tsr")["pass"]
    b = _gate_inputs(l4={"p0_passk_rate": 1.0, "tsr": 0.90})
    g = report.evaluate_gate(*b, mode="capability")
    assert g["verdict"]


def test_gate_l4_no_p0_note():
    """无 P0 有断言键任务（分母 0）→ p0_passk 按通过处理 + 注释。"""
    b = _gate_inputs(l4={"p0_passk_rate": 0.0, "p0_denominator": 0})
    g = report.evaluate_gate(*b, mode="capability")
    row = _gate_row(g, "p0_passk")
    assert row["pass"] and row.get("note")
    assert g["verdict"]


def test_gate_l4_no_tsr_denominator_note():
    """无有断言键任务（tsr 分母 0）→ tsr 按通过处理 + 注释（如 --tasks T005）。"""
    b = _gate_inputs(l4={"tsr": 0.0, "tsr_denominator": 0})
    g = report.evaluate_gate(*b, mode="capability")
    row = _gate_row(g, "tsr")
    assert row["pass"] and row.get("note")
    assert g["verdict"]


def test_gate_any_layer_red_verdict_false():
    """任一层 RED → verdict=False（exit 1 依据）。"""
    cases = [
        dict(l1={"tool_selection_accuracy": 0.89}),
        dict(l2={"assertion_pass_rate": 0.5}),
        dict(l3={"weighted_avg": 5.0}),
        dict(l4={"tsr": 0.5}),
    ]
    for over in cases:
        g = report.evaluate_gate(*_gate_inputs(**over), mode="capability")
        assert not g["verdict"], over


def test_gate_capability_vs_regression():
    """同一指标 0.95：capability（0.90）PASS / regression（0.98）RED。"""
    b = _gate_inputs(l1={"tool_selection_accuracy": 0.95,
                         "param_accuracy": 0.95})
    g_cap = report.evaluate_gate(*b, mode="capability")
    g_reg = report.evaluate_gate(*b, mode="regression")
    assert g_cap["verdict"]
    assert not g_reg["verdict"]


def test_gate_table_render_contains_rows():
    """门禁判定表渲染：含 指标值 vs 阈值 vs 判定 表头与 PASS/RED。"""
    g = report.evaluate_gate(*_gate_inputs(), mode="capability")
    md = report.render_gate_table(g)
    assert "| 层 | 指标 | 值 | 阈值" in md
    assert "PASS" in md and "门禁判定" in md


def test_gate_table_render_is_rate_formatting():
    """渲染语义：比率指标（0-1）显示百分比，L3 分制指标（0-10）显示原值。

    回归项：L3 weighted_avg=5.136 曾误渲染为 513.6%，阈值 7.5 误渲染为 750.0%。
    """
    g = report.evaluate_gate(*_gate_inputs(
        l3={"weighted_avg": 5.136, "p0_avg": 5.921},
        l1={"tool_selection_accuracy": 0.86}),
        mode="regression")
    md = report.render_gate_table(g)
    rows = {r["metric"]: r for r in g["rows"]}
    # L3 分制：原值渲染，绝不带 %
    assert "5.136" in md and "513.6%" not in md
    assert "7.5" in md and "750.0%" not in md
    assert rows["weighted_avg"].get("is_rate", False) is False
    # 比率指标：百分比渲染
    assert "86.0%" in md
    assert rows["tool_selection"]["is_rate"] is True
    assert rows["tsr"]["is_rate"] is True


# ================================================================
# report 对比单测（--compare-with 语义）
# ================================================================

def _fake_run_dir(tmp_path, name, metrics, scope=None):
    d = tmp_path / name
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({
        "run_at": "2026-08-31 00:00:00 +0800", "mode": "quick",
        "thresholds_mode": "capability", "repo_version": "test",
        "scope": scope or ["T001"],
        "metrics": metrics,
    }, ensure_ascii=False), encoding="utf-8")
    (d / "results.json").write_text("[]", encoding="utf-8")
    return str(d)


def test_compare_runs_arrows(tmp_path):
    """逐层指标对比：涨↑ / 跌↓ / 平→。"""
    prev = _fake_run_dir(tmp_path, "prev", {
        "l1": {"tool_selection_accuracy": 0.90},
        "l3": {"weighted_avg": 5.0, "p0_avg": 6.0},
    })
    cur = _fake_run_dir(tmp_path, "cur", {
        "l1": {"tool_selection_accuracy": 0.95},
        "l3": {"weighted_avg": 4.8, "p0_avg": 6.0},
    })
    c = report.compare_runs(report.load_run_dir(prev),
                            report.load_run_dir(cur))
    assert c["layers"]["l1"]["metrics"]["tool_selection_accuracy"]["arrow"] == "↑"
    assert c["layers"]["l3"]["metrics"]["weighted_avg"]["arrow"] == "↓"
    assert c["layers"]["l3"]["metrics"]["p0_avg"]["arrow"] == "→"
    md = report.render_compare_md(prev, cur, c)
    assert "↑" in md and "↓" in md and "→" in md
    assert "与上次对比" in md


def test_compare_runs_layer_absent(tmp_path):
    """上次缺 L4 → 对比只含双方共有的层，不崩。"""
    prev = _fake_run_dir(tmp_path, "prev", {"l1": {"x": 1.0}})
    cur = _fake_run_dir(tmp_path, "cur", {"l1": {"x": 1.0},
                                          "l4": {"tsr": 0.9}})
    c = report.compare_runs(report.load_run_dir(prev),
                            report.load_run_dir(cur))
    assert "l4" in c["layers"]
    assert "l2" not in c["layers"]


def test_load_run_dir_requires_meta(tmp_path):
    d = tmp_path / "nometa"
    d.mkdir()
    with pytest.raises(ValueError):
        report.load_run_dir(str(d))


def test_ledger_roundtrip(tmp_path):
    """台账追加/读取往返（唯一事实源格式）。"""
    f = tmp_path / "ledger.json"
    report.append_ledger({"run_id": "a", "gate_verdict": False}, path=f)
    report.append_ledger({"run_id": "b", "gate_verdict": True}, path=f)
    entries = report.load_ledger(f)
    assert [e["run_id"] for e in entries] == ["a", "b"]
    assert report.load_ledger(tmp_path / "missing.json") == []


# ================================================================
# rubric 修正单测（judge.py CALIBRATION_FIX_ANCHORS 四类落地）
# ================================================================

def test_judge_prompt_contains_calibration_fix_anchors():
    """判卷 system prompt 含四类修正锚点（引导/错域/核查/稳定）。"""
    p = judge.build_judge_system_prompt()
    for kw in ("校准修正锚点", "引导方向错误", "内容错域", "空壳回复",
               "逐柱核查", "分差 ≤1"):
        assert kw in p, kw


def test_calibration_fix_anchors_four_classes():
    """四类锚点逐条存在（校准报告 §rubric 修正建议四类）。"""
    a = judge.CALIBRATION_FIX_ANCHORS
    for kw in ("1. 引导类", "2. 内容错域/空壳", "3. 事实核查",
               "4. 跳档稳定", "accuracy ≤4", "accuracy/completeness ≤3"):
        assert kw in a, kw
    assert "calibration_fix_anchors" in judge.JUDGE_SYSTEM_PROMPT


def test_calibration_consistency_unit():
    """一致率口径：±1 内一致；直接人工维/代理维分开统计。"""
    # T005 人工对照 3 维（accuracy 7/completeness 6/actionability 5）；
    # 其余 2 维用 e4 分作代理参考（缺维按 0 分计并标注）
    new = [{"id": "T005", "dims": {
        "accuracy": {"score": 7}, "completeness": {"score": 6},
        "personalization": {"score": 5}, "actionability": {"score": 5},
        "citation_quality": {"score": 5}}}]
    e4 = [{"id": "T005", "dims": {
        "citation_quality": {"score": 5}, "personalization": {"score": 5}}}]
    cons = report.calibration_consistency(new, e4)
    assert cons["rate"] == 1.0
    assert cons["direct"]["n"] == 3  # T005 人工分 3 维
    assert cons["proxy"]["n"] == 2
    # 差 2 分 → 不一致（accuracy 7→9）
    new[0]["dims"]["accuracy"]["score"] = 9
    cons = report.calibration_consistency(new, e4)
    assert cons["consistent"] == 4 and cons["rate"] == pytest.approx(0.8)
    assert cons["direct"]["consistent"] == 2
    # 缺维代理参考：e4 无 personalization → 参考 0 分（保守标注）
    e4[0]["dims"].pop("personalization")
    cons = report.calibration_consistency(new, e4)
    assert cons["proxy"]["consistent"] == 1
    per = next(p for p in cons["per_dim"] if p["dim"] == "personalization")
    assert not per["consistent"] and per["ref"] == 0.0


# ================================================================
# validate_tasks state_checks 数值断言放宽（E6 残留 #3，与 state_verifier 对齐）
# ================================================================

def _task_with_state_checks(sc):
    t = {"id": "T000", "title": "x", "category": "edge", "severity": "P1",
         "pass_k": 1, "source": "manual-x", "no_tool": True,
         "expected_tools": [], "turns": [{"role": "user", "text": "hi"}],
         "reply_checks": {"contains": [], "neg_checks": ["{", "undefined",
                                                         "NaN", "null"],
                          "regex": [], "min_len": 0}}
    if sc is not None:
        t["state_checks"] = sc
    return t


def test_validate_equals_int_accepted():
    """_equals 数值断言（int 行数）合法——校验器与 state_verifier._equals 对齐。"""
    t = _task_with_state_checks({"chart_records_equals": 2})
    out = []
    validate_tasks.check_task(t, out)
    assert not any("state_checks" in e for e in out), out


def test_validate_created_int_rejected():
    """created/unchanged 仍必须 bool（数值断言只属于 _equals）。"""
    t = _task_with_state_checks({"persons_created": 2})
    out = []
    validate_tasks.check_task(t, out)
    assert any("值必须为 bool" in e for e in out), out
    t = _task_with_state_checks({"persons_created": True,
                                 "chart_records_equals": 3})
    out = []
    validate_tasks.check_task(t, out)
    assert not any("state_checks" in e for e in out), out


def test_state_verifier_equals_int_contract(tmp_path):
    """state_verifier _equals int：行数精确相等断言（与校验器放宽同一契约）。"""
    db = tmp_path / "v.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE favorites (id INTEGER PRIMARY KEY, "
                "user_id TEXT, type TEXT, ref_id TEXT)")
    con.execute("INSERT INTO favorites (user_id,type,ref_id) VALUES "
                "('u','chat','1'),('u','chat','2')")
    con.commit()
    before = state_verifier.snapshot_counts(str(db))
    r = state_verifier.run_state_checks(str(db), before,
                                        {"favorites_equals": 2})
    assert r["ok"]
    r = state_verifier.run_state_checks(str(db), before,
                                        {"favorites_equals": 3})
    assert not r["ok"] and "期望 3 行" in r["detail"]
    con.close()


def test_validate_eval_set_still_green():
    """全量评估集仍全绿（放宽不破坏既有契约）。"""
    tasks = _load_tasks()
    out = []
    for t in tasks:
        validate_tasks.check_task(t, out)
    assert not out, out[:5]
    cerrs, stats = validate_tasks.coverage_errors(tasks)
    assert not cerrs
    assert stats["total"] == 103  # 100 基线 + k11-F T101-T103


# ================================================================
# qian_saves.kind DDL 对齐（E6 残留 #4：种子前触发生产 QianDAO 迁移）
# ================================================================

def test_seed_qian_saves_old_schema_migrates(tmp_path):
    """旧 schema qian_saves（无 kind 列）→ seed_task_setup 经生产 QianDAO
    迁移（_migrate_kind 重建，kind='original'）后种子成功——
    T051/T053/T057 不再依赖 judge.py shim（与生产迁移语义逐字一致）。"""
    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE qian_saves (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " user_id TEXT NOT NULL, no INTEGER NOT NULL, drawn_at REAL)")
    con.execute("INSERT INTO qian_saves (user_id, no) VALUES ('old', 1)")
    con.commit()
    con.close()
    err = l1_eval.seed_task_setup(str(db), "eval_T051",
                                  {"qian_saves": [{"no": 7}]})
    assert err is None, err
    con = sqlite3.connect(str(db))
    cols = [r[1] for r in con.execute("PRAGMA table_info(qian_saves)")]
    assert "kind" in cols
    rows = con.execute("SELECT user_id, no, kind FROM qian_saves "
                       "ORDER BY no").fetchall()
    assert rows == [("old", 1, "original"), ("eval_T051", 7, "original")]
    con.close()


def test_seed_qian_saves_new_schema_idempotent(tmp_path):
    """新 schema（kind 已存在）→ QianDAO 构造幂等（不迁移），种子仍成功。"""
    db = tmp_path / "new.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE qian_saves (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " user_id TEXT NOT NULL, no INTEGER NOT NULL,"
                " kind TEXT NOT NULL DEFAULT 'original', drawn_at REAL,"
                " UNIQUE(user_id, no, kind))")
    con.commit()
    con.close()
    err = l1_eval.seed_task_setup(str(db), "eval_T053",
                                  {"qian_saves": [{"no": 5}]})
    assert err is None, err
    con = sqlite3.connect(str(db))
    row = con.execute("SELECT no, kind FROM qian_saves").fetchone()
    assert row == (5, "original")
    con.close()


# ================================================================
# runner CLI 单测（mock 各层返回 → 退出码/门禁语义）
# ================================================================

def _mock_attempt(task, l1_ok=True, l2_ok=True, l4_ok=True, score=9.0):
    ok = l1_ok and l2_ok and l4_ok
    dims = {d: {"score": score, "judge_error": False, "justification": "mock"}
            for d in judge.DIMS}
    return {
        "index": 0, "ok": ok, "skipped": False, "skip_reason": "",
        "exception": None, "elapsed": 1.0,
        "replies": ["mock 回复（含所需关键内容）"],
        "l1": {"tool_select_ok": l1_ok, "params_ok": l1_ok, "ok": l1_ok,
               "actual_calls": [], "actual_by_turn": [], "detail": "" if l1_ok
               else "期望工具序列 ['fortune_cycle']，实际 []（sabotage）"},
        "l2": {"ok": l2_ok,
               "checks": [{"name": "contains", "expect": "x", "ok": l2_ok,
                           "detail": "" if l2_ok else "未含关键内容"}],
               "replies_preview": ["mock"]},
        "l4": {"ok": l4_ok, "checks": [],
               "detail": "persons: 0→1✓" if task.get("state_checks") else "（无断言键）"},
        "dims": dims,
    }


def _mock_record(task, k=1, source="mock", **kw):
    a = _mock_attempt(task, **kw)
    return {
        "id": task["id"], "category": task["category"],
        "severity": task["severity"], "title": task["title"],
        "turns": len(task["turns"]), "no_tool": bool(task.get("no_tool")),
        "pass_k_field": task.get("pass_k"), "pass_k": k, "pass_k_source": source,
        "state_checks": task.get("state_checks", {}),
        "has_state_checks": bool(task.get("state_checks")),
        "skipped": False, "skip_reason": "",
        "attempts": [a], "elapsed": 1.0,
        "first_ok": a["ok"], "passed": a["ok"], "passed_all_layers": a["ok"],
        "l3": {"id": task["id"], "category": task["category"],
               "severity": task["severity"], "title": task["title"],
               "skipped": False, "skip_reason": "", "dims": a["dims"],
               "overall": 9.0, "parse_level": 1, "judge_error": False,
               "judge_error_reason": "", "turns": task.get("turns") or [],
               "replies": ["mock"]},
    }


@pytest.fixture
def mock_run(monkeypatch, tmp_path):
    """统一 mock：运行时 stub + 层执行 stub + 台账 recorder（零真实写入）。"""
    _mutex_skip(monkeypatch)
    monkeypatch.setenv("ZHIPU_API_KEY", "test-key")
    stub_runtime = {"tmp_root": str(tmp_path / "runtime"),
                    "settings": type("S", (), {"db_path": str(_DEFAULT_DB)})()}
    Path(stub_runtime["tmp_root"]).mkdir(exist_ok=True)
    monkeypatch.setattr(l1_eval, "_init_runtime", lambda route: stub_runtime)
    records = {}

    def fake_run_task(task, R, k, source, api_key):
        records[task["id"]] = (k, source)
        return _mock_record(task, k=k, source=source)

    monkeypatch.setattr(runner, "_run_task_with_passk", fake_run_task)
    ledger_calls = []
    monkeypatch.setattr(report, "append_ledger",
                        lambda entry, path=None: ledger_calls.append(entry))
    return {"ledger_calls": ledger_calls, "records": records}


def test_cli_gate_pass_exit_0(tmp_path, mock_run, capsys):
    """mock 全过 → 门禁 PASS → exit 0 + 台账追加（append 已被 fixture 吸收）。"""
    out = tmp_path / "out"
    code = runner.main(["--tasks", "T001", "--pass-k", "1",
                        "--out", str(out)])
    assert code == 0, capsys.readouterr().out
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["gate"]["verdict"] is True
    assert meta["metrics"]["l1"]["tool_selection_accuracy"] == 1.0
    assert meta["metrics"]["l4"]["p0_passk_rate"] == 1.0
    assert meta["metrics"]["l3"]["weighted_avg"] >= 7.5
    assert meta["thresholds_mode"] == "capability"  # quick 默认档
    assert len(mock_run["ledger_calls"]) == 1
    assert mock_run["ledger_calls"][0]["gate_verdict"] is True
    assert mock_run["records"]["T001"][0] == 1  # --pass-k 覆盖


def test_cli_gate_red_exit_1(tmp_path, monkeypatch, capsys):
    """mock L1 失败 → 门禁 RED → exit 1（红就是红如实报）。"""
    _mutex_skip(monkeypatch)
    monkeypatch.setenv("ZHIPU_API_KEY", "test-key")
    stub_runtime = {"tmp_root": str(tmp_path / "runtime"),
                    "settings": type("S", (), {"db_path": "x"})()}
    Path(stub_runtime["tmp_root"]).mkdir(exist_ok=True)
    monkeypatch.setattr(l1_eval, "_init_runtime", lambda route: stub_runtime)
    monkeypatch.setattr(runner, "_run_task_with_passk",
                        lambda task, R, k, source, api_key: _mock_record(
                            task, k=k, source=source, l1_ok=False))
    monkeypatch.setattr(report, "append_ledger", lambda entry, path=None: None)
    out = tmp_path / "out"
    code = runner.main(["--tasks", "T001", "--pass-k", "1",
                        "--out", str(out), "--no-ledger"])
    assert code == 1, capsys.readouterr().out
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["gate"]["verdict"] is False
    assert meta["metrics"]["l1"]["tool_selection_accuracy"] == 0.0
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "RED" in md


def test_cli_model_prod_rejected(tmp_path, monkeypatch, capsys):
    """--model prod 显式拒绝：exit 3 + 红线文案，零生产模型初始化。"""
    _mutex_skip(monkeypatch)
    code = runner.main(["--model", "prod"])
    err = capsys.readouterr().err
    assert code == 3
    assert "prod 未启用（红线：评测跑批一律免费模型）" in err


def test_cli_pass_k_negative_exit_3(monkeypatch):
    _mutex_skip(monkeypatch)
    assert runner.main(["--pass-k", "-1"]) == 3


def test_cli_unknown_task_exit_2(tmp_path, monkeypatch, capsys):
    """未知任务 id → 评估集前置失败 → exit 2。"""
    _mutex_skip(monkeypatch)
    code = runner.main(["--tasks", "T999"])
    assert code == 2
    assert "未知任务" in capsys.readouterr().err


def test_cli_validation_failure_exit_2(tmp_path, monkeypatch):
    """评估集校验失败 → exit 2。"""
    _mutex_skip(monkeypatch)

    def fail_validate():
        raise SystemExit(2)

    monkeypatch.setattr(runner, "_validate_eval_set", fail_validate)
    assert runner.main(["--tasks", "T001"]) == 2


def test_cli_compare_with_invalid_exit_3(tmp_path, monkeypatch, capsys):
    """--compare-with 目标非法（无 meta.json）→ exit 3（运行器错误）。"""
    _mutex_skip(monkeypatch)
    d = tmp_path / "bad"
    d.mkdir()
    code = runner.main(["--tasks", "T001", "--compare-with", str(d)])
    assert code == 3
    assert "目标非法" in capsys.readouterr().err


def test_cli_full_mode_thresholds_regression(tmp_path, mock_run, capsys):
    """full 模式 → regression 档（L1 ≥98%）。"""
    out = tmp_path / "out"
    code = runner.main(["--mode", "full", "--tasks", "T001", "--pass-k", "1",
                        "--out", str(out), "--no-ledger"])
    assert code == 0, capsys.readouterr().out
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["thresholds_mode"] == "regression"


def test_l4_no_state_checks_grouping(tmp_path, monkeypatch):
    """L4 无断言键任务归组（E6 残留处理）：不进 TSR 分母，单列。"""
    _mutex_skip(monkeypatch)
    monkeypatch.setenv("ZHIPU_API_KEY", "test-key")
    stub_runtime = {"tmp_root": str(tmp_path / "runtime"),
                    "settings": type("S", (), {"db_path": "x"})()}
    Path(stub_runtime["tmp_root"]).mkdir(exist_ok=True)
    monkeypatch.setattr(l1_eval, "_init_runtime", lambda route: stub_runtime)
    tasks = {t["id"]: t for t in _load_tasks()}
    monkeypatch.setattr(runner, "_run_task_with_passk",
                        lambda task, R, k, source, api_key: _mock_record(
                            task, k=k, source=source))
    monkeypatch.setattr(report, "append_ledger", lambda entry, path=None: None)
    out = tmp_path / "out"
    # T005 无 state_checks；T001 有 → TSR 分母 = 1
    code = runner.main(["--tasks", "T001,T005", "--pass-k", "1",
                        "--out", str(out), "--no-ledger"])
    assert code in (0, 1)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    l4m = meta["metrics"]["l4"]
    assert l4m["tsr_denominator"] == 1  # 只算有断言键
    assert "T005" in l4m["no_state_checks"]
    assert l4m["tsr"] == 1.0


# ================================================================
# 互斥排队纪律（程序化）：pidfile 锁
# ================================================================

class _PgrepNoMatch:
    returncode = 1
    stdout = ""
    stderr = ""


def test_mutex_guard_pidfile_conflict(monkeypatch, tmp_path):
    """活进程 pidfile → 并发冲突（拒绝启动依据）。"""
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *a, **k: _PgrepNoMatch())
    monkeypatch.setattr(runner, "_PIDFILE", tmp_path / ".runner.pid")
    runner._PIDFILE.write_text(str(os.getpid()))  # 本进程存活 = 并发
    assert runner.mutex_guard()


def test_mutex_guard_stale_pidfile_overwritten(monkeypatch, tmp_path):
    """死进程 pidfile（陈旧锁）→ 覆盖获取，无冲突。"""
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *a, **k: _PgrepNoMatch())
    monkeypatch.setattr(runner, "_PIDFILE", tmp_path / ".runner.pid")
    runner._PIDFILE.write_text("999999999")  # 大概率死进程
    try:
        assert runner.mutex_guard() == ""
        assert runner._PIDFILE.read_text().strip() == str(os.getpid())
    finally:
        runner.mutex_release()
    assert not runner._PIDFILE.exists()


def test_ancestor_pids_contains_self():
    assert os.getpid() in runner._ancestor_pids()


# ================================================================
# 任务范围契约（quick/full 组合）
# ================================================================

def test_select_scope_quick_composition():
    """quick = no_tool 全部 + edge 域 + P0 抽样 10 + 链路 7（40 条）；
    链路 k=3（任务字段），抽样 P0 k=1（smoke override）。"""
    tasks = _load_tasks()
    args = type("A", (), {"tasks": "", "category": "", "all": False})()
    ids, smoke = runner._select_scope(tasks, "quick", args)
    assert len(ids) == 40
    chain = set(l4_eval.CHAIN_TASKS)
    assert chain <= set(ids)
    for x in l4_eval.CHAIN_TASKS:
        t = next(t for t in tasks if t["id"] == x)
        k, s = l4_eval.effective_pass_k(t, 0, smoke)
        assert k == 3 and s == "task-field"  # 链路保持 k=3
    for tid in smoke:
        t = next(t for t in tasks if t["id"] == tid)
        k, s = l4_eval.effective_pass_k(t, 0, smoke)
        assert k == 1 and s == "smoke-override" and t["severity"] == "P0"
        assert tid not in chain


def test_select_scope_full_and_explicit():
    tasks = _load_tasks()
    args = type("A", (), {"tasks": "", "category": "", "all": False})()
    ids, smoke = runner._select_scope(tasks, "full", args)
    assert len(ids) == 103 and smoke == {}  # 100 基线 + k11-F T101-T103
    args = type("A", (), {"tasks": "T001,T017", "category": "", "all": False})()
    ids, smoke = runner._select_scope(tasks, "full", args)
    assert ids == ["T001", "T017"] and smoke == {}


# ================================================================
# 冒烟实跑（keys + 真实库门控；--no-ledger 贯穿，不污染台账）
# ================================================================

def _run_cli_smoke(tmp_path, argv_extra):
    """跑一次 runner CLI（真实运行），返回 (exit_code, out_dir)。"""
    out = tmp_path / "out"
    code = runner.main(["--tasks", "T001", "--pass-k", "1",
                        "--out", str(out), "--no-ledger"] + argv_extra)
    return code, out


def test_smoke_unified_real_t001(tmp_path, monkeypatch):
    """真实轻量冒烟：T001 实跑四层（keys + 真实库存在时）。

    当前真实基线：L2/L3 红是预期（体系价值），本测试只断言运行完整落盘 +
    退出码语义（0=全过 / 1=有失败或门禁红），数字如实留档不美化。"""
    if not _llm_keys_ready():
        pytest.skip("缺少 ZHIPU_API_KEY（glm-4-flash 路由）")
    if not _real_db_ready():
        pytest.skip("真实库不存在: %s" % _DEFAULT_DB)
    _mutex_skip(monkeypatch)
    snap = _env_snapshot()
    try:
        code, out = _run_cli_smoke(tmp_path, [])
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
        assert (out / "results.json").exists()
        assert (out / "report.md").exists()
        assert code in (0, 1)  # 真实门禁判定：红就是红如实报
        mm = meta["metrics"]
        print(f"\n[smoke T001] L1 {mm['l1']['tool_selection_accuracy']:.1%} | "
              f"L2 {mm['l2']['assertion_pass_rate']:.1%} | "
              f"L3 加权 {mm['l3']['weighted_avg']} | "
              f"L4 TSR {mm['l4']['tsr']:.1%} | "
              f"门禁 {'PASS' if meta['gate']['verdict'] else 'RED'}")
    finally:
        _restore_env(snap)
        l1_eval._close_runtime()


def test_gate_red_sabotage_then_restore(tmp_path, monkeypatch):
    """门禁红验证（验收 #5，RUN_EVAL_E6_GATE_RED=1 触发）：T017 真实两跑。

    破坏 canonical_tool_name（工具名归一改错，等效「把某工具注册名改错」）→
    L1 RED + exit 1；还原 → L1 PASS。双向证据 = L1 行翻转 + 退出码。
    （L3 红与否如实报，不操控；断言只针对 L1 行与退出码语义）"""
    if os.environ.get("RUN_EVAL_E6_GATE_RED") != "1":
        pytest.skip("门禁红验证由 RUN_EVAL_E6_GATE_RED=1 触发（真实 LLM 两跑）")
    if not _llm_keys_ready():
        pytest.skip("缺少 ZHIPU_API_KEY")
    if not _real_db_ready():
        pytest.skip("真实库不存在: %s" % _DEFAULT_DB)
    _mutex_skip(monkeypatch)
    snap = _env_snapshot()
    orig = interceptor.canonical_tool_name
    try:
        # 破坏：工具名归一改错（等效「把某工具注册名改错」）→ L1 必红
        interceptor.canonical_tool_name = lambda n: "sabotaged_" + n
        out1 = tmp_path / "red"
        code1 = runner.main(["--tasks", "T017", "--pass-k", "1",
                             "--out", str(out1), "--no-ledger",
                             "--thresholds", "capability"])
        # 还原后重跑
        interceptor.canonical_tool_name = orig
        out2 = tmp_path / "pass"
        code2 = runner.main(["--tasks", "T017", "--pass-k", "1",
                             "--out", str(out2), "--no-ledger",
                             "--thresholds", "capability"])
    finally:
        interceptor.canonical_tool_name = orig
        _restore_env(snap)
        l1_eval._close_runtime()

    def _l1_row(out_dir):
        meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        g = report.evaluate_gate(meta["metrics"]["l1"],
                                 meta["metrics"]["l2"],
                                 meta["metrics"]["l3"],
                                 meta["metrics"]["l4"],
                                 meta["thresholds_mode"])
        return next(r for r in g["rows"] if r["metric"] == "tool_selection")

    row1, row2 = _l1_row(out1), _l1_row(out2)
    assert code1 == 1 and row1["pass"] is False, "破坏后必须 RED + exit 1"
    assert code2 in (0, 1) and row2["pass"] is True, "还原后 L1 必须 PASS"
    print(f"\n[gate-red] 破坏: L1 {row1['value']} RED exit={code1} | "
          f"还原: L1 {row2['value']} PASS exit={code2}")


def test_calibration_rerun_consistency(tmp_path, monkeypatch):
    """修正 rubric 后重判校准存储样本一致率（±1 分内）。

    默认重判 3 条有记录在案人工分的任务（T005 3 维/T067 4 维/T058 2 维
    = 9 处直接人工对照维）；RUN_EVAL_E6_FULL_CALIBRATION=1 触发全部 30 条
    （走 cmd_calibration_rerun 完整路径）。数字如实报。"""
    if not _llm_keys_ready():
        pytest.skip("缺少 ZHIPU_API_KEY")
    samples_f = runner.DEFAULT_CALIBRATION_DIR / "calibration_samples.json"
    if not samples_f.exists():
        pytest.skip("校准样本不存在: %s" % samples_f)
    tasks = {t["id"]: t for t in _load_tasks()}

    def _rerun(sample_ids):
        samples = [s for s in json.loads(
            samples_f.read_text(encoding="utf-8")) if s["id"] in sample_ids]
        new = []
        for s in samples:
            j = judge.judge_task(tasks[s["id"]],
                                 [(r or "") for r in (s.get("replies") or [])],
                                 os.environ["ZHIPU_API_KEY"])
            new.append(j)
        e4 = [{"id": s["id"], "dims": s.get("dims") or {}} for s in samples]
        return report.calibration_consistency(new, e4)

    if os.environ.get("RUN_EVAL_E6_FULL_CALIBRATION") == "1":
        # 走完整路径：直接调 cmd（30 条全部重判）
        args = type("A", (), {"calibration_from": str(runner.DEFAULT_CALIBRATION_DIR),
                              "out": str(tmp_path / "full")})()
        code = runner.cmd_calibration_rerun(args)
        assert code == 0
        cons = json.loads((tmp_path / "full" / "consistency.json")
                          .read_text(encoding="utf-8"))
        assert cons["total_dims"] == 150
        print(f"\n[calibration] 全量 30 条重判一致率 {cons['rate']:.1%} "
              f"（人工对照 {cons['direct']['rate']:.1%}）")
        return

    cons = _rerun({"T005", "T067", "T058"})
    print(f"\n[calibration] 3 条重判一致率 {cons['rate']:.1%} "
          f"({cons['consistent']}/{cons['total_dims']} 维，"
          f"人工对照 {cons['direct']['rate']:.1%} "
          f"{cons['direct']['consistent']}/{cons['direct']['n']})")
    assert cons["direct"]["n"] == 9
    assert cons["rate"] >= 0.5  # 兜底 sanity（真实 LLM 数字如实，不做强断言）


def test_cli_help_has_expected_flags(capsys):
    """CLI 面：brief 契约参数全在（--mode/--model/--pass-k/--tasks/--category/
    --all/--compare-with/--out）。"""
    with pytest.raises(SystemExit):
        runner.main(["--help"])
    out = capsys.readouterr().out
    for flag in ("--mode", "--model", "--pass-k", "--tasks", "--category",
                 "--all", "--compare-with", "--out", "--calibration-rerun"):
        assert flag in out, flag
