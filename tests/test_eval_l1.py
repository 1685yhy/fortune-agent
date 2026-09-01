"""L1 工具调用评测（E2）测试：拦截器单测 + 比对/指标单测 + 种子注入单测 + 冒烟实跑。

- 拦截器：record 序列 / 轮次分组 / 恢复无残留 / 双实例防重入 / 名称归一
- 比对：三档（exact/partial/any）/ 顺序敏感 / no_tool 零调用 / 引擎域双语义
- 种子：全键注入落库断言 / 失败显式返回错误串
- 冒烟实跑（黑盒跑生产主链）：按仓库约定缺 LLM key 即 skip；
  默认轻量切片（keys 存在时），完整 brief 范围（12 no_tool + edge + 5 P0）
  由 RUN_EVAL_L1_FULL_SMOKE=1 触发

红线（本文件只读数据源）：data/eval/agent_tasks.jsonl 只读；src/bot/tool_calls.py
零改动；运行期隔离（临时库 + USER_MEMORY_DIR/CHARTS_DIR 重定向）由 l1_eval 兜底。
"""
import argparse
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
from interceptor import ToolCallRecorder, canonical_tool_name  # noqa: E402

# ---------------------------------------------------------------- DDL 常量

_USERS_DDL = """CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY, bazi_info TEXT, ziwei_info TEXT,
    push_enabled INTEGER DEFAULT 1, push_time TEXT DEFAULT '08:00',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    consultation_count INTEGER DEFAULT 0)"""

_PERSONS_DDL = """CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
    name TEXT NOT NULL, relation TEXT DEFAULT '其他',
    is_default INTEGER DEFAULT 0, birth_enc TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')))"""

_MEMBERSHIPS_DDL = """CREATE TABLE IF NOT EXISTS memberships (
    user_id TEXT PRIMARY KEY, plan TEXT NOT NULL, started_at TEXT,
    expires_at TEXT, queries_used INTEGER DEFAULT 0,
    queries_limit INTEGER, auto_renew INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')))"""

_FAVORITES_DDL = """CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
    type TEXT NOT NULL, ref_id TEXT NOT NULL, summary TEXT DEFAULT '',
    imported INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, type, ref_id))"""

_QIAN_DDL = """CREATE TABLE IF NOT EXISTS qian_saves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
    no INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'original',
    drawn_at REAL, UNIQUE (user_id, no, kind))"""

_ZERI_DDL = """CREATE TABLE IF NOT EXISTS zeri_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
    scene TEXT NOT NULL, lucky_date TEXT NOT NULL,
    card_json TEXT NOT NULL, items_json TEXT NOT NULL,
    plan_type TEXT NOT NULL DEFAULT 'free',
    reminder_enabled INTEGER DEFAULT 0, remind_sent_d1 INTEGER DEFAULT 0,
    remind_sent_d0 INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
    created_at REAL)"""

_CHART_DDL = """CREATE TABLE IF NOT EXISTS chart_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
    person_id INTEGER, birth_enc TEXT, bazi_enc TEXT,
    created_at TEXT DEFAULT (datetime('now')))"""

_QUOTA_DDL = """CREATE TABLE IF NOT EXISTS chat_quota (
    user_id TEXT NOT NULL, day TEXT NOT NULL,
    cnt INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, day))"""

_TEST_DDL = [_USERS_DDL, _PERSONS_DDL, _MEMBERSHIPS_DDL, _FAVORITES_DDL,
             _QIAN_DDL, _ZERI_DDL, _CHART_DDL, _QUOTA_DDL]


def _make_db(tmp_path: Path, name: str = "test.db") -> Path:
    db = tmp_path / name
    con = sqlite3.connect(db)
    try:
        for ddl in _TEST_DDL:
            con.execute(ddl)
        con.commit()
    finally:
        con.close()
    return db


# ================================================================
# 拦截器单测（假目标类，零 src 依赖）
# ================================================================

def _fake_handler_cls():
    class _FakeHandler:
        def __init__(self):
            self.calls = []

        def _execute_tool_call(self, name, params, user_id, user_question=""):
            self.calls.append((name, params, user_id, user_question))
            return {"ok": True}
    return _FakeHandler


def test_interceptor_records_sequence_and_passthrough():
    cls = _fake_handler_cls()
    h = cls()
    rec = ToolCallRecorder()
    with rec:
        rec.install(cls)
        r1 = h._execute_tool_call("合婚", {"birth_a": "1995-06-15 08:30"}, "u1")
        h._execute_tool_call("hehun", {"birth_a": "x", "birth_b": "y"}, "u1",
                             user_question="测")
    assert r1 == {"ok": True}          # 返回值原样透传
    assert rec.flat_calls() == [
        ("hehun", {"birth_a": "1995-06-15 08:30"}),
        ("hehun", {"birth_a": "x", "birth_b": "y"}),
    ]                                   # 中文名→cap_id 归一，params 原样


def test_interceptor_turn_grouping():
    cls = _fake_handler_cls()
    h = cls()
    rec = ToolCallRecorder()
    with rec:
        rec.install(cls)
        rec.begin_turn()
        h._execute_tool_call("hehun", {"birth_a": "x"}, "u1")
        rec.begin_turn()
        h._execute_tool_call("naming", {"surname": "王"}, "u1")
        h._execute_tool_call("naming", {"surname": "李"}, "u1")
    turns = rec.turns()
    assert turns == [
        [("hehun", {"birth_a": "x"})],
        [("naming", {"surname": "王"}), ("naming", {"surname": "李"})],
    ]
    assert rec.flat_calls() == [
        ("hehun", {"birth_a": "x"}),
        ("naming", {"surname": "王"}),
        ("naming", {"surname": "李"}),
    ]


def test_interceptor_restore_no_residue():
    cls = _fake_handler_cls()
    h = cls()
    original = cls._execute_tool_call
    rec = ToolCallRecorder()
    with rec:
        rec.install(cls)
        assert cls._execute_tool_call is not original  # 已包装
        h._execute_tool_call("hehun", {}, "u1")
        assert len(rec.flat_calls()) == 1
    # 恢复后：方法还原为原对象、哨兵清除、不再记录
    assert cls._execute_tool_call is original
    assert not hasattr(cls, "_eval_l1_interceptor_installed")
    assert not rec._installed
    rec.restore()  # 幂等：重复 restore 无副作用
    h._execute_tool_call("hehun", {}, "u1")
    # 恢复后不再新增（flat_calls 仍只有块内那 1 条），但调用本身照常执行
    assert rec.flat_calls() == [("hehun", {})]
    assert h.calls[-1][0] == "hehun"
    assert len(h.calls) == 2


def test_interceptor_double_install_guard():
    cls = _fake_handler_cls()
    rec1 = ToolCallRecorder()
    rec2 = ToolCallRecorder()
    with rec1:
        rec1.install(cls)
        with pytest.raises(RuntimeError):
            rec2.install(cls)          # 双实例并发安装 → 显式报错


def test_interceptor_same_instance_reinstall_idempotent():
    cls = _fake_handler_cls()
    h = cls()
    rec = ToolCallRecorder()
    rec.install(cls)
    try:
        rec.install(cls)               # 同实例重复 install → 无操作
        h._execute_tool_call("hehun", {}, "u1")
        assert len(rec.flat_calls()) == 1
    finally:
        rec.restore()


def test_interceptor_auto_turn_on_record_without_begin():
    cls = _fake_handler_cls()
    h = cls()
    rec = ToolCallRecorder()
    with rec:
        rec.install(cls)
        h._execute_tool_call("naming", {"surname": "王"}, "u1")  # 未 begin_turn
    assert rec.flat_calls() == [("naming", {"surname": "王"})]   # 兜底自建分组
    assert rec.turns() == [[("naming", {"surname": "王"})]]


def test_interceptor_enter_exit_resets_recordings():
    cls = _fake_handler_cls()
    h = cls()
    rec = ToolCallRecorder()
    with rec:
        rec.install(cls)
        h._execute_tool_call("hehun", {}, "u1")
        assert len(rec.flat_calls()) == 1
    with rec:                          # 再次进入 → 清空历史
        rec.install(cls)
        assert rec.flat_calls() == []
        h._execute_tool_call("hehun", {}, "u1")
        assert len(rec.flat_calls()) == 1


def test_canonical_tool_name_normalization():
    # 中文名（注册表事实源）→ cap_id；cap_id 原样；未知名原样透传
    assert canonical_tool_name("合婚") == "hehun"
    assert canonical_tool_name("hehun") == "hehun"
    assert canonical_tool_name("完全不存在的工具") == "完全不存在的工具"


# ================================================================
# 比对逻辑单测（纯函数）
# ================================================================

def test_compare_empty_expected_zero_calls_passes():
    # no_tool 反例桶：期望空 + 实际零调用 = 通过
    assert l1_eval.compare_expected([], []) == (True, True, "")


def test_compare_empty_expected_with_calls_fails():
    # no_tool 反例桶：有实际调用 = 工具选择失败
    ok, pok, detail = l1_eval.compare_expected([], [("hehun", {})])
    assert ok is False and pok is False
    assert "实际 1 次" in detail


def test_compare_engine_domain_empty_expected_dual_semantics():
    # 引擎域（intent 引擎无工具工单，如 zeri 域）expected_tools=[]：
    # 与 no_tool 同断言——期望零调用，实际零调用 = 通过
    assert l1_eval.compare_expected([], []) == (True, True, "")
    ok, pok, _ = l1_eval.compare_expected([], [("zeri", {})])
    assert ok is False and pok is False


def test_compare_exact_match():
    exp = [{"name": "hehun", "params": {"birth_a": "x", "birth_b": "y"},
            "match": "exact"}]
    act = [("hehun", {"birth_a": "x", "birth_b": "y"})]
    assert l1_eval.compare_expected(exp, act) == (True, True, "")


def test_compare_exact_extra_key_fails():
    exp = [{"name": "hehun", "params": {"birth_a": "x"}, "match": "exact"}]
    act = [("hehun", {"birth_a": "x", "birth_b": "y"})]
    ok, pok, detail = l1_eval.compare_expected(exp, act)
    assert ok is True and pok is False
    assert "exact" in detail


def test_compare_partial_subset_passes():
    # partial：期望字段都在实际参数中（实际可多出键，值不锁）
    exp = [{"name": "hehun", "params": {"birth_a": "x", "birth_b": "y"},
            "match": "partial"}]
    act = [("hehun", {"birth_a": "x", "birth_b": "y", "gender": "女"})]
    assert l1_eval.compare_expected(exp, act) == (True, True, "")


def test_compare_partial_missing_key_fails():
    exp = [{"name": "hehun", "params": {"birth_a": "x", "birth_b": "y"},
            "match": "partial"}]
    act = [("hehun", {"birth_a": "x"})]
    ok, pok, detail = l1_eval.compare_expected(exp, act)
    assert ok is True and pok is False
    assert "birth_b" in detail


def test_compare_any_only_checks_name():
    exp = [{"name": "hehun", "params": {"birth_a": "x"}, "match": "any"}]
    act = [("hehun", {"whatever": "由消息动态补全"})]
    assert l1_eval.compare_expected(exp, act) == (True, True, "")


def test_compare_sequence_swap_fails():
    # 顺序敏感：跨轮次扁平化后索引对齐，交换即失败
    exp = [{"name": "hehun", "params": {}, "match": "any"},
           {"name": "naming", "params": {}, "match": "any"}]
    act = [("naming", {}), ("hehun", {})]
    ok, pok, detail = l1_eval.compare_expected(exp, act)
    assert ok is False and pok is False
    assert "期望工具序列" in detail


def test_compare_str_params_fails_structured_contract():
    # 文本标签路径（params 为 str）无结构键 → 不满足 partial 契约
    exp = [{"name": "hehun", "params": {"birth_a": "x"}, "match": "partial"}]
    act = [("hehun", "<tool_call>合婚</tool_call>")]
    ok, pok, detail = l1_eval.compare_expected(exp, act)
    assert ok is True and pok is False


# ================================================================
# 指标与阈值单测
# ================================================================

def _result(**kw):
    base = {"id": "TX", "skipped": False, "no_tool": False,
            "tool_select_ok": False, "params_ok": False, "ok": False,
            "actual_calls": [], "exception": None}
    base.update(kw)
    return base


def test_aggregate_metrics_mixed():
    results = [
        _result(id="T1", tool_select_ok=True, params_ok=True, ok=True),
        _result(id="T2", tool_select_ok=True, params_ok=False),
        _result(id="T3", no_tool=True, actual_calls=[("hehun", {})]),
        _result(id="T4", skipped=True, skip_reason="种子失败"),
    ]
    m = l1_eval.aggregate_metrics(results)
    assert m["executed"] == 3                    # 跳过不进分母
    assert m["tool_selection_accuracy"] == 2 / 3
    assert m["param_accuracy"] == 0.5            # 1/2（T2 参数不过）
    assert m["false_call_rate"] == 1.0           # no_tool 实际调用
    assert m["skipped"] == ["T4"]


def test_aggregate_metrics_no_no_tool_rate_none():
    results = [_result(id="T1", tool_select_ok=True, params_ok=True, ok=True)]
    m = l1_eval.aggregate_metrics(results)
    assert m["false_call_rate"] is None
    assert m["false_call_count"] == 0


def test_thresholds_met():
    ok = _result(id="T1", tool_select_ok=True, params_ok=True, ok=True)
    assert l1_eval.thresholds_met(l1_eval.aggregate_metrics([ok])) is True
    assert l1_eval.thresholds_met(
        l1_eval.aggregate_metrics([ok, _result(id="T2")])) is False  # 50%
    assert l1_eval.thresholds_met(
        l1_eval.aggregate_metrics([_result(id="T3", no_tool=True,
                                            actual_calls=[("x", {})])])) is False
    assert l1_eval.thresholds_met(
        l1_eval.aggregate_metrics([_result(id="T4", skipped=True)])) is False


def test_parse_birth_components():
    b = l1_eval.parse_birth_components("1995-06-15 08:30")
    assert b == {"birth_year": 1995, "birth_month": 6, "birth_day": 15,
                 "birth_hour": 8, "birth_minute": 30}
    b2 = l1_eval.parse_birth_components("1995-06-15")
    assert b2["birth_hour"] is None and b2["birth_minute"] is None
    assert l1_eval.parse_birth_components("")["birth_year"] is None


# ================================================================
# 种子注入单测（自建临时库，零外部依赖）
# ================================================================

def test_seed_task_setup_all_keys(tmp_path):
    db = _make_db(tmp_path)
    setup = {
        "persons": [{"name": "张三", "birth": "1995-06-15 08:30",
                     "gender": "male", "city": "北京"}],
        "chart_records": [{"bazi": ["甲子", "乙丑"], "day_master": "甲",
                           "geju": "正官格", "dayun": [["8", "庚辰"]],
                           "liunian": {"2026": {"ganzhi": "丙午"}},
                           "shensha": ["天乙贵人"]}],
        "favorites": [{"kind": "chat", "title": "什么是正官"},
                      {"kind": "qian", "title": "上上签"}],
        "qian_saves": [{"no": 1}, {"no": 2}],
        "zeri_plans": [{"scene": "出行", "date": "2026-09-01"}],
        "membership": {"queries_used": 3, "queries_limit": 5},
        "chat_quota": {"used": 9},
    }
    assert l1_eval.seed_task_setup(str(db), "u1", setup) is None
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM users WHERE user_id='u1'"
                           ).fetchone()[0] == 1
        # 默认命主：is_default=1 且 birth_enc 不落明文（AES 加密）
        row = con.execute("SELECT is_default, birth_enc FROM persons").fetchone()
        assert row[0] == 1
        # 加密断言用解密比对（确定性）：_encrypt_text 每次随机 IV → base64
        # 密文是随机串，「密文不含 '1995'/'06'」子串断言有 ~1% 概率撞车
        # （base64 字母表含数字），曾致全量回归偶发红（2026-09-01 实测一次）。
        # 存储明文是 parse_birth_components 拼出的 JSON 出生字典
        # （{"birth_year": …, "gender": …}），解密后按 key 断言。
        from src.storage.dao import _decrypt_or_plain
        import json as _json
        assert row[1] != "1995-06-15 08:30"          # 密文 ≠ 明文
        _plain = _json.loads(_decrypt_or_plain(row[1]))
        assert _plain["birth_year"] == 1995 and _plain["birth_month"] == 6
        assert _plain["birth_day"] == 15 and _plain["birth_hour"] == 8
        assert _plain["birth_minute"] == 30 and _plain["gender"] == "男"
        assert con.execute("SELECT COUNT(*) FROM chart_records").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM qian_saves").fetchone()[0] == 2
        assert con.execute(
            "SELECT COUNT(*) FROM zeri_plans WHERE status='active'"
        ).fetchone()[0] == 1
        assert con.execute("SELECT plan FROM memberships").fetchone()[0] == "free"
        assert con.execute("SELECT cnt FROM chat_quota").fetchone()[0] == 9
    finally:
        con.close()


def test_seed_task_setup_creates_lazy_tables(tmp_path):
    # 回归：生产库可能尚无懒建表（chat_quota 由 DAO 首次连接时自建）。
    # 种子注入必须建表兜底，否则 T086 等额度门任务被误跳过。
    db = tmp_path / "lazy.db"
    con = sqlite3.connect(db)
    try:
        con.execute(_USERS_DDL)
        con.commit()
    finally:
        con.close()
    assert l1_eval.seed_task_setup(
        str(db), "u1", {"chat_quota": {"used": 9},
                        "zeri_plans": [{"scene": "出行", "date": "2026-09-01"}],
                        "qian_saves": [{"no": 1}],
                        "favorites": [{"kind": "chat", "title": "x"}]}) is None
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT cnt FROM chat_quota WHERE user_id='u1'").fetchone()[0] == 9
    finally:
        con.close()


def test_seed_task_setup_no_setup_is_noop(tmp_path):
    db = _make_db(tmp_path)
    assert l1_eval.seed_task_setup(str(db), "u2", None) is None
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    finally:
        con.close()


def test_seed_task_setup_failure_returns_error_string(tmp_path):
    # 非 SQLite 文件 → 连接失败 → 显式错误串（调用方据此跳过并标注，不静默）
    bad = tmp_path / "not-a-db.txt"
    bad.write_text("not sqlite")
    err = l1_eval.seed_task_setup(str(bad), "u1", {})
    assert err is not None
    assert "database" in err.lower()


def test_seed_db_copy_isolates_source(tmp_path):
    src = _make_db(tmp_path, "src.db")
    con = sqlite3.connect(src)
    try:
        con.execute("INSERT INTO users (user_id) VALUES ('marker')")
        con.commit()
    finally:
        con.close()
    iso = tmp_path / "iso"
    iso.mkdir()
    dst = l1_eval.seed_db_copy(src, iso)
    assert dst.exists()
    con = sqlite3.connect(dst)
    try:
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    finally:
        con.close()


# ================================================================
# 冒烟实跑（黑盒跑生产主链；LLM key / 真实库缺一即 skip——仓库约定）
# ================================================================

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


def _smoke_env():
    """运行期 env 快照/恢复（同进程其他测试复用安全）。"""
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
        out = l1_eval.run_eval(tasks, tmp_path / "l1-smoke",
                               model_route="glm", keep_tmp=True)
        m = out["metrics"]
        print(f"\n[smoke {scope_label}] 工具选择 {m['tool_selection_accuracy']:.1%} "
              f"({m['tool_selection_passed']}/{m['executed']}) | "
              f"参数 {m['param_accuracy']:.1%} "
              f"({m['param_passed']}/{m['param_denominator']}) | "
              f"误调率 {m['false_call_count']}/{m['no_tool_count']}")
        if m["skipped"]:
            print(f"[smoke {scope_label}] 跳过: {m['skipped']}")
        assert l1_eval.thresholds_met(m), (
            f"L1 阈值未达标: {m}")
        return m
    finally:
        _restore_env(snap)
        l1_eval._close_runtime()


def test_smoke_l1_default_slice(tmp_path):
    """默认轻量冒烟：no_tool 3 + edge 2 + P0 2（keys 存在时实跑真实指标）。

    完整 brief 范围（12 no_tool + edge 全部 + 5 P0）见
    RUN_EVAL_L1_FULL_SMOKE=1 触发的 test_smoke_l1_full_brief_range。
    """
    tasks = _load_tasks()
    no_tool = [t["id"] for t in tasks if t.get("no_tool")][:3]
    edge = [t["id"] for t in tasks if t["category"] == "edge"][:2]
    p0 = [t["id"] for t in tasks if t["severity"] == "P0"][:2]
    _run_smoke(tmp_path, no_tool + edge + p0, "default-slice")


def test_smoke_l1_full_brief_range(tmp_path):
    """完整 brief 冒烟范围：no_tool 全部 + edge 域 + P0 抽样 5 条。
    由 RUN_EVAL_L1_FULL_SMOKE=1 触发（默认不跑，全量约 37 任务 LLM 实跑）。"""
    if os.environ.get("RUN_EVAL_L1_FULL_SMOKE") != "1":
        pytest.skip("完整冒烟范围由 RUN_EVAL_L1_FULL_SMOKE=1 触发")
    tasks = _load_tasks()
    ids = [t["id"] for t in tasks if t.get("no_tool")]
    ids += [t["id"] for t in tasks if t["category"] == "edge" and t["id"] not in ids]
    p0 = [t["id"] for t in tasks if t["severity"] == "P0" and t["id"] not in ids]
    ids += p0[:5]
    _run_smoke(tmp_path, ids, "full-brief-range")


# ================================================================
# 任务选择单测
# ================================================================

def test_select_task_ids_smoke_subset():
    tasks = [
        {"id": "T001", "category": "paipan", "severity": "P0", "no_tool": True},
        {"id": "T002", "category": "paipan", "severity": "P0"},
        {"id": "T003", "category": "edge", "severity": "P1"},
        {"id": "T004", "category": "fortune", "severity": "P0"},
        {"id": "T005", "category": "fortune", "severity": "P0"},
    ]
    args = argparse.Namespace(tasks="", category="", all=False, p0_sample=2)
    ids = l1_eval.select_task_ids(tasks, args)
    # no_tool 全部 + edge 域 + P0 抽样（不重复）
    assert ids == ["T001", "T003", "T002", "T004"]
    args2 = argparse.Namespace(tasks="T002,T005", category="", all=False,
                               p0_sample=10)
    assert l1_eval.select_task_ids(tasks, args2) == ["T002", "T005"]
    args3 = argparse.Namespace(tasks="", category="fortune", all=False,
                               p0_sample=10)
    assert l1_eval.select_task_ids(tasks, args3) == ["T004", "T005"]
    args4 = argparse.Namespace(tasks="", category="", all=True, p0_sample=10)
    assert l1_eval.select_task_ids(tasks, args4) == [t["id"] for t in tasks]
    with pytest.raises(SystemExit):
        l1_eval.select_task_ids(tasks, argparse.Namespace(
            tasks="T999", category="", all=False, p0_sample=10))
    with pytest.raises(SystemExit):
        l1_eval.select_task_ids(tasks, argparse.Namespace(
            tasks="", category="nope", all=False, p0_sample=10))
