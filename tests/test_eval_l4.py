"""L4 端到端执行验证（E5）测试：state_verifier 三键单测 + pass^k 逻辑单测 + 冒烟实跑。

- state_verifier：created（新增行=通过/无新增=失败）、unchanged（行数不变=通过/
  有增删=失败）、equals（行数等于=通过/不等于=失败）——sqlite 内存库构造种子表断言；
  表名映射（chat→sessions 物理表）、表不存在按 0、非法键显式失败、聚合 ok/detail
- pass^k：mock 执行器「k-1 次过 1 次败」→ 该任务不过；「k 次全过」→ 过；
  指标计入 pass^k 通过率；每次重跑独立临时库副本（隔离，零 LLM 代码路径实测）；
  pass_k 生效优先级（--pass-k > 冒烟覆盖 > 任务字段）
- 冒烟实跑（黑盒跑生产主链 + 真实临时库状态断言）：按仓库约定缺 LLM key 即
  skip；默认轻量切片（T013 链路 k=1 + T016 k=1）；完整 brief 范围（链路 7 条
  k=3 + P0 抽样 5 条 k=1）由 RUN_EVAL_L4_FULL_SMOKE=1 触发——L4 首测基线数字
  以 CLI 实跑为准写入报告（tests 内为可复现门禁）
- **k68 抖动策略**：冒烟走 `tests/eval_flake_retry.py` 的统一策略（有界重试
  ≤2 次尝试 + 必须如实上报）。本层门禁是运行完整性断言（**非阈值门禁**）⇒
  按第一原则失败一律**不可重试**（**断言与调用路径**逐字等价，**仅上报文案
  有变化**——新增策略的尝试行与 [EVAL-FLAKE] 信号行。k73-M4 更正：原措辞
  「行为与改前逐字等价」经复审裁定只**部分成立**）。

红线（本文件只读数据源）：data/eval/agent_tasks.jsonl 只读；src/bot/tool_calls.py
零改动；运行期隔离（临时库 + USER_MEMORY_DIR/CHARTS_DIR 重定向）由
l1_eval._init_runtime 兜底；L4 断言层零 LLM（纯查库，本批连免费模型都不用——
主链 LLM 仅执行任务本身）。
"""
import json
import os
import sqlite3
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
import l4_eval  # noqa: E402
import state_verifier  # noqa: E402

_DEFAULT_DB = "/mnt/d/fortune-data/userdata/fortune.db"


def _make_db(tmp_path: Path, name: str = "test.db") -> Path:
    """自建临时库（persons/sessions/favorites/chart_records 等业务表 DDL
    与 DAO 构造器同源，镜像 test_eval_l1._TEST_DDL）。"""
    db = tmp_path / name
    con = sqlite3.connect(db)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY, bazi_info TEXT, ziwei_info TEXT,
            created_at TEXT DEFAULT (datetime('now')))""")
        con.execute("""CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            name TEXT NOT NULL, relation TEXT DEFAULT '其他',
            is_default INTEGER DEFAULT 0, birth_enc TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')))""")
        con.execute("""CREATE TABLE IF NOT EXISTS sessions (
            user_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
            intent TEXT, session_id TEXT, id INTEGER PRIMARY KEY AUTOINCREMENT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            type TEXT NOT NULL, ref_id TEXT NOT NULL, summary TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, type, ref_id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS chart_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            person_id INTEGER, birth_enc TEXT, bazi_enc TEXT,
            created_at TEXT DEFAULT (datetime('now')))""")
        con.commit()
    finally:
        con.close()
    return db


# ================================================================
# state_verifier 三键断言单测（内存 sqlite 构造）
# ================================================================

_INSERT_COLUMNS = {
    "persons": ("user_id", "name"),
    "sessions": ("user_id", "role", "content"),
    "favorites": ("user_id", "type", "ref_id"),
    "chart_records": ("user_id",),
}


def _seed_counts(db: Path, before: dict, after: dict):
    """按 before/after 表→行数构造库（已存在表按真实必填列插入；新表按
    v 列自建）。返回 (before_counts, db)。"""
    con = sqlite3.connect(db)
    try:
        for table in set(before) | set(after):
            cols = _INSERT_COLUMNS.get(table)
            if cols is None:
                con.execute(f"CREATE TABLE IF NOT EXISTS \"{table}\" (v INTEGER)")
        con.commit()
    finally:
        con.close()

    def _insert_rows(table, n):
        cols = _INSERT_COLUMNS.get(table)
        con = sqlite3.connect(db)
        try:
            if cols is None:
                con.execute("DELETE FROM \"%s\"" % table)
                for _ in range(n):
                    con.execute(f"INSERT INTO \"{table}\" (v) VALUES (0)")
            else:
                con.execute("DELETE FROM \"%s\"" % table)
                placeholders = ",".join("?" for _ in cols)
                for i in range(n):  # 逐行唯一值（favorites UNIQUE(user_id,type,ref_id)）
                    vals = [f"u{i}" if c == "user_id" else f"{c}_{i}"
                            for c in cols]
                    con.execute(
                        f"INSERT INTO \"{table}\" ({','.join(cols)})"
                        f" VALUES ({placeholders})", vals)
            con.commit()
        finally:
            con.close()

    for table, n in before.items():
        _insert_rows(table, n)
    before_counts = {t: n for t, n in before.items()}
    for table, n in after.items():
        _insert_rows(table, n)
    return before_counts


def test_created_pass(tmp_path):
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"persons": 0}, {"persons": 1})
    out = state_verifier.run_state_checks(str(db), before, {"persons_created": True})
    assert out["ok"] is True
    c = out["checks"][0]
    assert c["table"] == "persons" and c["before"] == 0 and c["after"] == 1


def test_created_no_new_rows_fails(tmp_path):
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"persons": 1}, {"persons": 1})
    out = state_verifier.run_state_checks(str(db), before, {"persons_created": True})
    assert out["ok"] is False
    c = out["checks"][0]
    assert c["pass"] is False
    assert "期望增加" in c["reason"] and "1 行 → 跑后 1 行" in c["reason"]


def test_created_lazy_table_missing_before_counts_zero(tmp_path):
    # 懒建表场景：跑前表不存在（before_counts 无键/键=0）→ 跑后建表新增 = 通过
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"persons": 0}, {"chart_records": 1})
    out = state_verifier.run_state_checks(str(db), before, {"chart_records_created": True})
    assert out["ok"] is True
    c = out["checks"][0]
    assert c["before"] == 0 and c["after"] == 1 and c["table"] == "chart_records"


def test_unchanged_same_count_passes(tmp_path):
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"favorites": 3}, {"favorites": 3})
    out = state_verifier.run_state_checks(str(db), before, {"favorites_unchanged": True})
    assert out["ok"] is True


def test_unchanged_rows_added_fails(tmp_path):
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"favorites": 3}, {"favorites": 4})
    out = state_verifier.run_state_checks(str(db), before, {"favorites_unchanged": True})
    assert out["ok"] is False
    c = out["checks"][0]
    assert c["pass"] is False and "期望不变" in c["reason"]


def test_equals_exact_count_passes(tmp_path):
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"persons": 0}, {"persons": 2})
    out = state_verifier.run_state_checks(str(db), before, {"persons_equals": 2})
    assert out["ok"] is True
    assert out["checks"][0]["expected"] == 2


def test_equals_wrong_count_fails(tmp_path):
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"persons": 0}, {"persons": 2})
    out = state_verifier.run_state_checks(str(db), before, {"persons_equals": 3})
    assert out["ok"] is False
    c = out["checks"][0]
    assert c["pass"] is False and "实际 2 行，期望 3 行" in c["reason"]


def test_equals_bool_degenerate_semantics(tmp_path):
    # 校验器对 state_checks 值强制 bool（评估集 0 条用 _equals）：
    # bool True = 等于跑前快照 / False = 等于 0（退化语义，E6 放宽时废弃）
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"persons": 2}, {"persons": 2})
    assert state_verifier.run_state_checks(str(db), before,
                                           {"persons_equals": True})["ok"] is True
    db2 = _make_db(tmp_path, "b.db")
    before2 = _seed_counts(db2, {"persons": 0}, {"persons": 0})
    assert state_verifier.run_state_checks(str(db2), before2,
                                           {"persons_equals": False})["ok"] is True


def test_table_alias_chat_to_sessions(tmp_path):
    # 业务表名 chat → 物理表 sessions（主链对话历史落库，SessionDAO 事实）
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"sessions": 0}, {"sessions": 2})
    out = state_verifier.run_state_checks(str(db), before, {"chat_created": True})
    assert out["ok"] is True
    assert out["checks"][0]["table"] == "sessions"
    # 物理表 sessions 不存在时按 0（created 失败如实报）
    db2 = _make_db(tmp_path, "c.db")
    before2 = _seed_counts(db2, {"persons": 0}, {"persons": 1})
    out2 = state_verifier.run_state_checks(str(db2), before2, {"chat_created": True})
    assert out2["ok"] is False
    assert out2["checks"][0]["table"] == "sessions" and out2["checks"][0]["after"] == 0


def test_invalid_key_reported_explicitly(tmp_path):
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"persons": 0}, {"persons": 1})
    out = state_verifier.run_state_checks(str(db), before, {"persons_bogus": True})
    assert out["ok"] is False
    assert "非法断言键" in out["checks"][0]["reason"]


def test_mixed_checks_aggregate(tmp_path):
    db = _make_db(tmp_path)
    before = _seed_counts(db, {"persons": 0, "favorites": 2},
                          {"persons": 1, "favorites": 3})
    out = state_verifier.run_state_checks(str(db), before, {
        "persons_created": True,       # 0→1 过
        "favorites_unchanged": True,   # 2→3 不过
    })
    assert out["ok"] is False
    assert [c["pass"] for c in out["checks"]] == [True, False]
    assert "persons: 0→1✓" in out["detail"] and "✗" in out["detail"]


def test_snapshot_counts_all_tables(tmp_path):
    db = _make_db(tmp_path)
    _seed_counts(db, {"persons": 1, "favorites": 2}, {})
    snap = state_verifier.snapshot_counts(str(db))
    assert snap["persons"] == 1 and snap["favorites"] == 2
    assert "sqlite_sequence" not in snap  # 内部表剔除


# ================================================================
# pass^k 逻辑单测（mock 执行器）
# ================================================================

def _task(**kw):
    base = {"id": "T013", "title": "链路：建档→排盘→财运→收藏查询",
            "category": "paipan", "severity": "P0", "pass_k": 3,
            "state_checks": {"persons_created": True}, "turns": [{"role": "user",
            "text": "x"}]}
    base.update(kw)
    return base


def _ok_attempt(i):
    return {"index": i, "ok": True, "skipped": False, "exception": None,
            "state": {"ok": True, "detail": ""}, "elapsed": 1.0,
            "replies": ["r"], "tool_calls": []}


def _fail_attempt(i):
    a = _ok_attempt(i)
    a["ok"] = False
    a["state"] = {"ok": False, "detail": "persons: 0→0✗", "checks": [
        {"key": "persons_created", "table": "persons", "before": 0,
         "after": 0, "expected": True, "pass": False,
         "reason": "persons: 跑前 0 行 → 跑后 0 行（期望增加）"}]}
    return a


def test_passk_k_minus_1_pass_1_fail_task_not_passed():
    calls = []

    def fake_attempt(task, R, route, i):
        calls.append(i)
        return _fail_attempt(i) if i == 1 else _ok_attempt(i)

    r = l4_eval.run_task_with_passk(_task(), {}, "glm", 3, attempt_fn=fake_attempt)
    assert len(calls) == 3            # k 次全跑（1 败也要跑完——记录完整失败面）
    assert r["passed"] is False       # 任意一次失败 = 该任务不过
    assert r["first_ok"] is True      # 首轮过了（TSR 与 pass^k 分开计）
    assert r["skipped"] is False


def test_passk_all_pass_task_passed():
    calls = []

    def fake_attempt(task, R, route, i):
        calls.append(i)
        return _ok_attempt(i)

    r = l4_eval.run_task_with_passk(_task(), {}, "glm", 3, attempt_fn=fake_attempt)
    assert calls == [0, 1, 2]         # k=3 恰好调用 3 次
    assert r["passed"] is True and r["first_ok"] is True


def test_passk_k1_single_attempt():
    calls = []

    def fake_attempt(task, R, route, i):
        calls.append(i)
        return _ok_attempt(i)

    r = l4_eval.run_task_with_passk(_task(pass_k=1), {}, "glm", 1,
                                    attempt_fn=fake_attempt)
    assert calls == [0] and r["passed"] is True


def test_passk_seed_failure_skips_task_with_reason():
    def fake_attempt(task, R, route, i):
        return {"index": i, "ok": False, "skipped": True,
                "skip_reason": "种子注入失败: OperationalError: table "
                               "qian_saves has no column named kind",
                "state": {"ok": False, "detail": ""}}

    r = l4_eval.run_task_with_passk(_task(), {}, "glm", 3,
                                    attempt_fn=fake_attempt)
    assert r["skipped"] is True and r["passed"] is False
    assert len(r["attempts"]) == 1  # 首轮失败即短路，不再重跑
    assert "kind" in r["skip_reason"]


def test_aggregate_metrics_tsr_and_passk():
    results = [
        {"id": "T1", "skipped": False, "first_ok": True, "passed": True,
         "attempts": [_ok_attempt(0)]},
        {"id": "T2", "skipped": False, "first_ok": True, "passed": False,
         "attempts": [_ok_attempt(0), _fail_attempt(1), _ok_attempt(2)]},
        {"id": "T3", "skipped": False, "first_ok": False, "passed": False,
         "attempts": [_fail_attempt(0)]},
        {"id": "T4", "skipped": True, "skip_reason": "种子失败",
         "first_ok": False, "passed": False, "attempts": []},
    ]
    m = l4_eval.aggregate_metrics(results)
    assert m["executed"] == 3                  # 跳过不进分母
    assert m["tsr"] == 2 / 3                   # T1/T2 首轮过
    assert m["passk_rate"] == 1 / 3            # 仅 T1 k 次全过
    assert m["skipped"] == ["T4"]
    assert m["failed"] == ["T2", "T3"]
    assert m["exceptions"] == []


def test_aggregate_metrics_exception_listed():
    a = _ok_attempt(0)
    a["exception"] = "TimeoutError: 任务超时"
    a["ok"] = False
    results = [{"id": "T1", "skipped": False, "first_ok": False,
                "passed": False, "attempts": [a]}]
    m = l4_eval.aggregate_metrics(results)
    assert m["exceptions"] == ["T1"] and m["failed"] == ["T1"]


def test_effective_pass_k_priority():
    t3 = _task(id="T013", pass_k=3)   # 链路 P0 字段值 3（不在冒烟覆盖表）
    t1 = _task(id="T016", pass_k=1)   # 抽样任务字段值 1
    t001 = _task(id="T001", pass_k=3)  # 抽样任务字段值 3（在冒烟覆盖表内）
    # 任务字段默认生效
    assert l4_eval.effective_pass_k(t3) == (3, "task-field")
    assert l4_eval.effective_pass_k(t1) == (1, "task-field")
    # 冒烟覆盖（抽样 5 条统一 1）优先于字段；链路任务不受覆盖
    assert l4_eval.effective_pass_k(t001, 0, l4_eval.SMOKE_PASS_K_OVERRIDE) \
        == (1, "smoke-override")
    assert l4_eval.effective_pass_k(t3, 0, l4_eval.SMOKE_PASS_K_OVERRIDE) \
        == (3, "task-field")
    # --pass-k 最高优先
    assert l4_eval.effective_pass_k(t3, 3, l4_eval.SMOKE_PASS_K_OVERRIDE) \
        == (3, "cli-override")


def test_run_attempt_isolated_fresh_db_per_attempt(tmp_path, monkeypatch):
    """真实 run_attempt 代码路径（stub handler，零 LLM）：
    pass^k 每次重跑独立临时库副本——attempt1 的写入不污染 attempt2 快照。"""
    src = tmp_path / "src.db"
    con = sqlite3.connect(src)
    try:
        con.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY)")
        con.execute("CREATE TABLE t (v INTEGER)")
        con.execute("INSERT INTO t VALUES (0)")
        con.commit()
    finally:
        con.close()

    recorded = []
    orig_copy = l4_eval.l1_eval.seed_db_copy

    def spy(src_db, tdir):
        p = orig_copy(src_db, tdir)
        recorded.append(str(p))
        return p

    monkeypatch.setattr(l4_eval.l1_eval, "seed_db_copy", spy)

    class _H:
        def process(self, text, user_id, session_id=""):
            con = sqlite3.connect(recorded[-1])
            try:
                con.execute("INSERT INTO t VALUES (1)")
                con.commit()
            finally:
                con.close()
            return "ok"

    R = {"tmp_root": tmp_path / "runtime", "settings": type("S", (), {
        "db_path": str(src)})(), "build_handler": lambda db, route: _H()}
    (R["tmp_root"]).mkdir(exist_ok=True)

    task = _task(state_checks={"t_created": True}, setup={})
    a1 = l4_eval.run_attempt(task, R, "glm", 0)
    a2 = l4_eval.run_attempt(task, R, "glm", 1)
    assert a1["ok"] is True and a2["ok"] is True
    assert a1["state"]["checks"][0]["before"] == 1 and \
        a1["state"]["checks"][0]["after"] == 2
    assert a2["state"]["checks"][0]["before"] == 1   # 独立副本：快照仍是种子基线
    assert len(recorded) == 2 and recorded[0] != recorded[1]  # 两个不同临时库


def test_select_task_ids_default_smoke_subset():
    tasks = [{"id": t} for t in ["T001", "T002", "T003", "T005", "T013",
                                 "T014", "T016", "T026", "T027", "T038",
                                 "T057", "T090", "T099"]]
    import argparse
    args = argparse.Namespace(tasks="", category="", all=False)
    ids = l4_eval.select_task_ids(tasks, args)
    # 链路 7 条全量 + P0 非链路抽样 5 条（T005/T016 字段 k=1）
    assert ids == ["T013", "T014", "T026", "T027", "T038", "T057", "T090",
                   "T001", "T002", "T003", "T005", "T016"]
    assert l4_eval.select_task_ids(
        tasks, argparse.Namespace(tasks="T001,T013", category="", all=False)
    ) == ["T001", "T013"]
    assert l4_eval.select_task_ids(
        tasks, argparse.Namespace(tasks="", category="", all=True)
    ) == [t["id"] for t in tasks]
    with pytest.raises(SystemExit):
        l4_eval.select_task_ids(
            tasks, argparse.Namespace(tasks="T999", category="", all=False))


# ================================================================
# 冒烟实跑（黑盒跑生产主链；LLM key / 真实库缺一即 skip——仓库约定）
# ================================================================

def _llm_keys_ready():
    return bool(os.environ.get("ZHIPU_API_KEY"))


def _real_db_ready():
    return Path(_DEFAULT_DB).exists()


def _load_tasks():
    p = l4_eval.TASKS_PATH
    if not p.exists():
        pytest.skip("评估集不存在: %s" % p)
    return [json.loads(line) for line in p.read_text(encoding="utf-8")
            .splitlines() if line.strip()]


def _smoke_env():
    return {k: os.environ.get(k)
            for k in ("USER_MEMORY_DIR", "CHARTS_DIR", "EXPERIENCE_MODE")}


def _restore_env(snap):
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _l4_summary(m):
    return (f"TSR {m['tsr']:.1%} ({m['tsr_passed']}/{m['tsr_denominator']}) | "
            f"pass^k {m['passk_rate']:.1%} "
            f"({m['passk_passed']}/{m['passk_denominator']}) | "
            f"失败={m['failed']} | 跳过={m['skipped']}")


def _run_smoke(tmp_path, tasks, cli_k=0, smoke_override=None):
    """冒烟实跑（k68 抖动策略：**有界重试 ≤2 次尝试 + 必须如实上报**）。

    本层冒烟门禁是**运行完整性断言**（链路必须真跑、跳过不允许、失败须有状态
    明细，见 `test_smoke_l4_light`；本批不设阈值门禁，E6 接线）⇒ 按第一原则，
    这类失败**不属于**「跑起来了、只是阈值差一点」⇒ **一律不可重试**
    （异常原样上抛 + 留信号行）。**断言与调用路径**与改前逐字等价（语义等价
    且顺序不变），**仅上报文案有变化**（多了策略的尝试行 + [EVAL-FLAKE]
    信号行）。k73-M4：原措辞「行为与改前逐字等价」经复审裁定只**部分成立**。
    """
    if not _llm_keys_ready():
        pytest.skip("缺少 ZHIPU_API_KEY（glm-4-flash 路由）")
    if not _real_db_ready():
        pytest.skip("真实库不存在: %s" % _DEFAULT_DB)
    snap = _smoke_env()
    try:

        def _attempt(attempt_no):
            # 第 1 次沿用原落盘路径（不重试的常态路径产物位置不变）
            out_dir = tmp_path / ("l4-smoke" if attempt_no == 1
                                  else f"l4-smoke-retry{attempt_no}")
            out = l4_eval.run_eval(tasks, out_dir, model_route="glm",
                                   cli_k=cli_k, smoke_override=smoke_override,
                                   keep_tmp=True)
            m = out["metrics"]
            print(f"[smoke] 第 {attempt_no} 次尝试：{_l4_summary(m)}")
            if m["skipped"]:
                print(f"[smoke] 跳过: {m['skipped']} → {m['skipped_reasons']}")
            if m["failed"]:
                print(f"[smoke] 失败: {m['failed']}")
            for fname in ("meta.json", "results.json", "report.md"):
                assert (out_dir / fname).exists(), fname
            return out

        def _check(out):
            m = out["metrics"]
            return eval_flake_retry.make_check(
                ok=True, summary=_l4_summary(m), metrics=m)

        return eval_flake_retry.run_with_bounded_retry(
            "l4-smoke", _attempt, _check)
    finally:
        _restore_env(snap)
        l4_eval.l1_eval._close_runtime()


def test_smoke_l4_light(tmp_path):
    """默认轻量冒烟：链路 T013（k=1）+ 抽样 T016（k=1）——keys 存在时实跑
    真实主链 + 真实临时库状态断言（~2-3min）。完整 brief 范围
    （链路 7 条 k=3 + 抽样 5 条 k=1）由 RUN_EVAL_L4_FULL_SMOKE=1 触发。"""
    tasks = _load_tasks()
    by_id = {t["id"]: t for t in tasks}
    out = _run_smoke(tmp_path, [by_id["T013"], by_id["T016"]], cli_k=1)
    m = out["metrics"]
    # 冒烟自证：链路任务必须真实执行（断言层在临时库上查表）
    for r in out["results"]:
        assert r["skipped"] is False, f"{r['id']} 不应被跳过: {r['skip_reason']}"
        assert len(r["attempts"]) == 1
        # 每个失败条目必须有可解释原因（期望表状态 vs 实际行数）
        if not r["passed"]:
            a = r["attempts"][0]
            assert a["state"]["detail"], "失败必须带状态断言明细"
    print(f"[smoke-l4-light] TSR {m['tsr']:.1%} | pass^k {m['passk_rate']:.1%}")


def test_smoke_l4_full_brief_range(tmp_path):
    """完整 brief 冒烟范围：链路 7 条 pass_k=3 实跑 + P0 非链路抽样 5 条
    pass_k=1（L4 首测基线数字来源）。由 RUN_EVAL_L4_FULL_SMOKE=1 触发
    （默认不跑：全量 26 次任务运行，链路单条 1-2min × k=3，约 40min+）。"""
    if os.environ.get("RUN_EVAL_L4_FULL_SMOKE") != "1":
        pytest.skip("完整冒烟范围由 RUN_EVAL_L4_FULL_SMOKE=1 触发")
    tasks = _load_tasks()
    by_id = {t["id"]: t for t in tasks}
    selected = [by_id[t] for t in l4_eval.CHAIN_TASKS + l4_eval.SMOKE_SAMPLE]
    out = _run_smoke(tmp_path, selected, smoke_override=l4_eval.SMOKE_PASS_K_OVERRIDE)
    m = out["metrics"]
    print(f"\n[smoke-l4-full] TSR {m['tsr']:.1%} | pass^k {m['passk_rate']:.1%}"
          f" | 跳过 {m['skipped']} | 失败 {m['failed']}")
    # 基线数字如实留档（不达标也是基线——本批不设阈值门禁，E6 接线）
    for r in out["results"]:
        assert r["pass_k"] == (3 if r["id"] in l4_eval.CHAIN_TASKS else 1)
