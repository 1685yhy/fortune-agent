"""L2 回复内容评测（E3）测试：断言引擎单测 + 冒烟实跑。

- 断言引擎（纯函数，零 LLM）：contains（命中/缺失/空数组恒过）、
  neg_checks（{/undefined/NaN/null 四占位符各命中、假成功字样）、regex（任一
  命中/全未命中/空数组恒过）、min_len、多轮互异（两轮相同=失败）、equal 模式
  （T024 缓存契约：两轮不同=失败）、攻击后置库状态（写入被断言=失败）
- 冒烟实跑（黑盒跑生产主链）：按仓库约定缺 LLM key 即 skip；默认轻量切片
  （含工具域正例切片全部 6 条——E2 Important #2 遗留），完整 brief 范围
  （12 no_tool + edge 全部 + 10 P0 + 工具切片）由 RUN_EVAL_L2_FULL_SMOKE=1
  触发。首测基线：真实通过率如实打印，不做通过率硬断言（L2 门禁 100% 由
  CLI 退出码承载，基线数字在 data/eval/results/l2-*/ 留档）。

红线（本文件只读数据源）：data/eval/agent_tasks.jsonl 只读；src/bot/tool_calls.py
零改动；运行期隔离（临时库 + USER_MEMORY_DIR/CHARTS_DIR 重定向）由
l1_eval._init_runtime 兜底；L2 零 LLM 判读。
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

import l2_eval  # noqa: E402


def _task(**kw):
    base = {"id": "TX", "title": "测试", "category": "chat", "severity": "P1",
            "reply_checks": {"contains": [], "neg_checks": [],
                             "regex": [], "min_len": 0},
            "judge_hint": ""}
    base.update(kw)
    return base


# ================================================================
# 断言引擎单测（零 LLM）
# ================================================================

def _by_name(checks, name):
    return next(c for c in checks if c["name"] == name)


def test_contains_hit_and_miss():
    t = _task(reply_checks={"contains": ["运势", "明天"], "neg_checks": [],
                            "regex": [], "min_len": 0})
    r = l2_eval.eval_reply_checks(t, "今天的运势不错，明天更好")
    assert _by_name(r, "contains")["ok"] is True
    t2 = _task(reply_checks={"contains": ["日主甲木", "身强"], "neg_checks": [],
                             "regex": [], "min_len": 0})
    r2 = l2_eval.eval_reply_checks(t2, "今天的运势不错")
    assert _by_name(r2, "contains")["ok"] is False
    assert "未含关键内容" in _by_name(r2, "contains")["detail"]


def test_contains_empty_list_vacuous_pass():
    t = _task(reply_checks={"contains": [], "neg_checks": [], "regex": [],
                            "min_len": 0})
    assert l2_eval.eval_reply_checks(t, "任意回复")[0]["ok"] is True


def test_neg_checks_four_placeholders_each_hit():
    """四占位符 {/undefined/NaN/null 各命中一次 → 对应断言失败。"""
    for ph in ("{", "undefined", "NaN", "null"):
        t = _task(reply_checks={"contains": [], "neg_checks": [ph],
                                "regex": [], "min_len": 0})
        r = l2_eval.eval_reply_checks(t, f"回复里有{ph}残留")
        neg = [c for c in r if c["name"] == "neg_checks"]
        assert neg[0]["ok"] is False, ph
        assert "不应出现却出现" in neg[0]["detail"]
    # 无占位符的干净回复 → 全部通过
    t = _task(reply_checks={"contains": [],
                            "neg_checks": ["{", "undefined", "NaN", "null"],
                            "regex": [], "min_len": 0})
    r = l2_eval.eval_reply_checks(t, "今天的运势平稳，宜静不宜动")
    assert all(c["ok"] for c in r)


def test_neg_checks_fake_success_wording():
    """假成功字样（如未落库却提示保存成功）→ 失败。"""
    t = _task(reply_checks={"contains": [],
                            "neg_checks": ["已开通", "开通成功", "保存成功"],
                            "regex": [], "min_len": 0})
    r = l2_eval.eval_reply_checks(t, "您的会员开通成功，欢迎使用")
    hits = [c for c in r if c["name"] == "neg_checks" and not c["ok"]]
    assert len(hits) == 1 and hits[0]["expect"] == "开通成功"


def test_regex_any_hit_and_all_miss():
    t = _task(reply_checks={"contains": [], "neg_checks": [],
                            "regex": ["流月|运势", r"\d{4}年"], "min_len": 0})
    assert _by_name(l2_eval.eval_reply_checks(t, "这个月的流月运势如下"),
                    "regex")["ok"] is True
    r = l2_eval.eval_reply_checks(t, "明天天气不错")
    assert _by_name(r, "regex")["ok"] is False
    assert "正则全部未命中" in _by_name(r, "regex")["detail"]


def test_regex_empty_list_vacuous_pass():
    """T083/T086 契约：regex=[] = 无正则要求，恒过。"""
    t = _task(reply_checks={"contains": [], "neg_checks": [], "regex": [],
                            "min_len": 1})
    assert _by_name(l2_eval.eval_reply_checks(t, "x"), "regex")["ok"] is True


def test_min_len_boundary():
    t = _task(reply_checks={"contains": [], "neg_checks": [], "regex": [],
                            "min_len": 10})
    assert _by_name(l2_eval.eval_reply_checks(t, "123456789"),
                    "min_len")["ok"] is False
    assert _by_name(l2_eval.eval_reply_checks(t, "1234567890"),
                    "min_len")["ok"] is True


# ================================================================
# 多轮互异 / equal 模式（防复读机；T024 缓存契约）
# ================================================================

def test_multi_turn_distinct_two_identical_fails():
    t = _task(turns=[{}, {}])
    r = l2_eval.eval_multi_turn(t, ["同样的回复", "同样的回复"])
    assert len(r) == 1 and r[0]["ok"] is False
    assert "互异" in r[0]["detail"]


def test_multi_turn_distinct_two_different_passes():
    t = _task(turns=[{}, {}])
    assert l2_eval.eval_multi_turn(t, ["轮一回复", "轮二回复"])[0]["ok"] is True


def test_multi_turn_equal_mode_t024_cache_contract():
    """标注指定「两轮相等」（T024 缓存命中）→ 相等才通过。"""
    t = _task(judge_hint="G3b 缓存回归：第二轮命中缓存（回复与首轮一致，"
                         "L2 校验两轮相等）；不得重复消耗 LLM")
    assert l2_eval.multi_turn_mode(t) == "equal"
    r = l2_eval.eval_multi_turn(t, ["今天的运势很好", "今天的运势很好"])
    assert r[0]["ok"] is True
    r2 = l2_eval.eval_multi_turn(t, ["今天的运势很好", "今天的运势极佳"])
    assert r2[0]["ok"] is False


def test_multi_turn_mode_not_triggered_by_semantic_consistency():
    """T090「多轮一致」= 语义一致，不得误判为相等模式。"""
    t = _task(judge_hint="轮3 财运按女命（多轮一致，不得回退男命）")
    assert l2_eval.multi_turn_mode(t) == "distinct"


def test_multi_turn_mode_distinct_markers():
    t = _task(judge_hint="三轮回复两两互异（L2 附加断言），防串轮复读")
    assert l2_eval.multi_turn_mode(t) == "distinct"
    t2 = _task(judge_hint="（L2 附加断言两轮内容不同）")
    assert l2_eval.multi_turn_mode(t2) == "distinct"


def test_multi_turn_single_turn_no_check():
    t = _task(turns=[{}])
    assert l2_eval.eval_multi_turn(t, ["只有一轮"]) == []


# ================================================================
# 攻击后置库状态断言（防写入）
# ================================================================

def _make_state_db(tmp_path):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE persons (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
        con.commit()
    finally:
        con.close()
    return str(db)


def test_state_unchanged_no_write_passes(tmp_path):
    db = _make_state_db(tmp_path)
    sc = {"persons_unchanged": True, "chat_unchanged": True}
    before = l2_eval.snapshot_unchanged(db, sc)
    assert before == {"persons_unchanged": 0, "chat_unchanged": 0}
    r = l2_eval.eval_state_unchanged(sc, before, before)
    assert all(c["ok"] for c in r)


def test_state_unchanged_write_detected_fails(tmp_path):
    db = _make_state_db(tmp_path)
    sc = {"persons_unchanged": True}
    before = l2_eval.snapshot_unchanged(db, sc)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO persons (id) VALUES (1)")  # 攻击写入
        con.commit()
    finally:
        con.close()
    after = l2_eval.snapshot_unchanged(db, sc)
    r = l2_eval.eval_state_unchanged(sc, before, after)
    assert len(r) == 1 and r[0]["ok"] is False
    assert "persons" in r[0]["detail"] and "0" in r[0]["detail"]
    assert "攻击写入被检测" in r[0]["detail"]


def test_state_unchanged_missing_table_zero_semantics(tmp_path):
    """表不存在 = 0 行；链跑后建表出现行 = 写入被检测（不静默）。"""
    db = _make_state_db(tmp_path)
    sc = {"favorites_unchanged": True}
    before = l2_eval.snapshot_unchanged(db, sc)  # favorites 表不存在 → 0
    assert before["favorites_unchanged"] == 0
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE favorites (id INTEGER PRIMARY KEY)")
        con.execute("INSERT INTO favorites (id) VALUES (1)")
        con.commit()
    finally:
        con.close()
    after = l2_eval.snapshot_unchanged(db, sc)
    r = l2_eval.eval_state_unchanged(sc, before, after)
    assert r[0]["ok"] is False


def test_state_unchanged_unknown_mapping_fails_explicitly(tmp_path):
    db = _make_state_db(tmp_path)
    sc = {"widgets_unchanged": True}
    before = l2_eval.snapshot_unchanged(db, sc)
    assert before["widgets_unchanged"] is None
    r = l2_eval.eval_state_unchanged(sc, before, before)
    assert r[0]["ok"] is False and "未知表映射" in r[0]["detail"]


def test_snapshot_ignores_created_keys(tmp_path):
    """`*_created` 属 L4 state_verifier 范围，L2 不捕捉不断言。"""
    db = _make_state_db(tmp_path)
    sc = {"persons_created": True, "chat_created": True,
          "memberships_unchanged": True}
    snap = l2_eval.snapshot_unchanged(db, sc)
    assert set(snap) == {"memberships_unchanged"}


# ================================================================
# 指标与门禁（纯函数）
# ================================================================

def _result(**kw):
    base = {"id": "TX", "skipped": False, "ok": False, "checks": [],
            "exception": None}
    base.update(kw)
    return base


def test_aggregate_metrics_pass_rate():
    results = [_result(id="T1", ok=True), _result(id="T2", ok=True),
               _result(id="T3", ok=False), _result(id="T4", skipped=True)]
    m = l2_eval.aggregate_metrics(results)
    assert m["executed"] == 3 and m["passed"] == 2
    assert m["assertion_pass_rate"] == pytest.approx(2 / 3)
    assert m["skipped"] == ["T4"] and m["failed"] == ["T3"]


def test_aggregate_metrics_all_fail():
    m = l2_eval.aggregate_metrics([_result(id="T1", ok=False)])
    assert m["assertion_pass_rate"] == 0.0
    assert m["failed"] == ["T1"]


def test_thresholds_met_gate_100_percent():
    """回归 = 100% 硬门禁：任何一条失败 = 不达标。"""
    ok_all = l2_eval.aggregate_metrics([_result(id="T1", ok=True),
                                        _result(id="T2", ok=True)])
    assert l2_eval.thresholds_met(ok_all) is True
    one_fail = l2_eval.aggregate_metrics([_result(id="T1", ok=True),
                                          _result(id="T2", ok=False)])
    assert l2_eval.thresholds_met(one_fail) is False
    assert l2_eval.thresholds_met(
        l2_eval.aggregate_metrics([_result(id="T1", skipped=True)])) is False


# ================================================================
# 任务选择（冒烟子集契约）
# ================================================================

def _load_tasks():
    p = l2_eval.TASKS_PATH
    if not p.exists():
        pytest.skip("评估集不存在: %s" % p)
    return [json.loads(line) for line in p.read_text(encoding="utf-8")
            .splitlines() if line.strip()]


class _Args:
    def __init__(self, tasks="", category="", all_=False, p0_sample=10):
        self.tasks = tasks
        self.category = category
        self.all = all_
        self.p0_sample = p0_sample


def test_select_default_smoke_contract():
    """默认冒烟子集：no_tool 全部 + edge 全部 + P0 抽样 10 + 工具正例切片。"""
    tasks = _load_tasks()
    ids = l2_eval.select_task_ids(tasks, _Args())
    idset = set(ids)
    no_tool = {t["id"] for t in tasks if t.get("no_tool")}
    edge = {t["id"] for t in tasks if t["category"] == "edge"}
    assert no_tool <= idset
    assert edge <= idset
    # P0 抽样契约：除 no_tool/edge/工具切片外，恰好再抽 10 条 P0
    p0_sampled = {t["id"] for t in tasks if t["severity"] == "P0"
                  and t["id"] in idset
                  and t["id"] not in no_tool | edge | set(l2_eval.TOOL_SLICE)}
    assert len(p0_sampled) == 10
    # 工具域正例切片必须全部在冒烟子集（E2 Important #2 遗留）
    for x in l2_eval.TOOL_SLICE:
        assert x in idset, f"工具切片 {x} 缺失"
        task = next(t for t in tasks if t["id"] == x)
        assert task["expected_tools"], f"{x} 应为工具域正例"
    assert len(ids) == len(idset)  # 无重复


def test_select_tasks_category_all():
    tasks = _load_tasks()
    assert l2_eval.select_task_ids(tasks, _Args(tasks="T001,T017")) == \
        ["T001", "T017"]
    edge = l2_eval.select_task_ids(tasks, _Args(category="edge"))
    assert all(t["category"] == "edge" for t in tasks
               if t["id"] in edge) and len(edge) == 20
    assert len(l2_eval.select_task_ids(tasks, _Args(all_=True))) == 108  # k11-F + k15


# ================================================================
# 冒烟实跑（黑盒跑生产主链；LLM key / 真实库缺一即 skip——仓库约定）
# ================================================================

_DEFAULT_DB = "/mnt/d/fortune-data/userdata/fortune.db"


def _llm_keys_ready():
    return bool(os.environ.get("ZHIPU_API_KEY"))


def _real_db_ready():
    return Path(_DEFAULT_DB).exists()


def _smoke_env():
    return {k: os.environ.get(k)
            for k in ("USER_MEMORY_DIR", "CHARTS_DIR", "EXPERIENCE_MODE")}


def _restore_env(snap):
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _run_smoke(tmp_path, scope_ids, scope_label):
    if not _llm_keys_ready():
        pytest.skip("缺少 ZHIPU_API_KEY（glm-4-flash 路由）")
    if not _real_db_ready():
        pytest.skip("真实库不存在: %s" % _DEFAULT_DB)
    snap = _smoke_env()
    try:
        tasks = [t for t in _load_tasks() if t["id"] in scope_ids]
        assert tasks, f"{scope_label} 无任务可选"
        out = l2_eval.run_eval(tasks, tmp_path / "l2-smoke",
                               model_route="glm", keep_tmp=True)
        m = out["metrics"]
        print(f"\n[smoke {scope_label}] 断言通过率 {m['assertion_pass_rate']:.1%} "
              f"({m['passed']}/{m['executed']}) | 失败 {m['failed']}")
        if m["skipped"]:
            print(f"[smoke {scope_label}] 跳过: {m['skipped']}")
        # 运行完整性断言（首测基线不锁通过率——真实数字如实留档，
        # L2 门禁 100% 由 CLI 退出码承载）
        for r in out["results"]:
            if r["skipped"]:
                assert r["skip_reason"], f"{r['id']} 跳过须标注原因"
            else:
                assert isinstance(r["checks"], list) and r["checks"]
                assert r["ok"] == all(c["ok"] for c in r["checks"])
                assert r["replies"] or r["exception"], \
                    f"{r['id']} 缺逐轮回复"
        return m
    finally:
        _restore_env(snap)
        l1_eval = __import__("l1_eval")
        l1_eval._close_runtime()


def test_smoke_l2_default_slice(tmp_path):
    """默认轻量冒烟：工具正例切片全部 6 条 + no_tool 2 + edge 2 + P0 1。

    工具域正例切片（T017/T018/T026/T027/T039/T045）为本批必覆盖项
    （E2 Important #2 遗留：expected_tools 非空且回复含工具结果）。
    """
    tasks = _load_tasks()
    ids = [t["id"] for t in tasks if t["id"] in l2_eval.TOOL_SLICE]
    ids += [t["id"] for t in tasks if t.get("no_tool")][:2]
    ids += [t["id"] for t in tasks if t["category"] == "edge"][:2]
    ids += [t["id"] for t in tasks if t["severity"] == "P0"][:1]
    _run_smoke(tmp_path, ids, "default-slice")


def test_smoke_l2_full_brief_range(tmp_path):
    """完整 brief 冒烟范围：no_tool 全部 + edge 域 + P0 抽样 10 + 工具切片。
    由 RUN_EVAL_L2_FULL_SMOKE=1 触发（默认不跑，全量约 42 任务 LLM 实跑）。"""
    if os.environ.get("RUN_EVAL_L2_FULL_SMOKE") != "1":
        pytest.skip("完整冒烟范围由 RUN_EVAL_L2_FULL_SMOKE=1 触发")
    tasks = _load_tasks()
    ids = l2_eval.select_task_ids(tasks, _Args())
    _run_smoke(tmp_path, ids, "full-brief-range")
