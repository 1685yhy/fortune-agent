"""L3 质量判卷层（E4）测试：rubric 构建 / 打分调用桩 / 三级兜底 / 加权汇总 / 校准抽样 + 冒烟实跑。

- rubric：五维权重和=1.0、维度名集合、每维 0-10 分段描述齐全（复用
  src/eval/scorer.py 的 DIMENSION_WEIGHTS/SCORING_RUBRIC，权威对齐）
- 打分调用桩：mock call_judge_model 返回合法 JSON → 解析与加权正确；
  调用抛异常 → 全维 0 + judge_error；空回复 → 跳过标注
- JSON 三级兜底：直接 JSON / 代码围栏包裹 / 乱格式正则提取 三档各命中；
  全失败 → 0 分 + judge_error；空输出 → level 0；缺维 → 该维 judge_error
- 加权汇总：手工构造五维分 → 加权平均正确（含 P0/全量/分域/五维分组统计）
- 校准抽样：30 条唯一（P0/P1/P2 各 10，域覆盖上限 8/10/7，并集 10 域），
  确定性可复现
- 冒烟实跑（黑盒跑生产主链 + 真实 glm-4-flash 判卷）：按仓库约定缺 LLM
  key 即 skip；默认 2 条校准任务轻量切片

红线（本文件只读数据源）：data/eval/agent_tasks.jsonl 只读；src/bot/tool_calls.py
零改动；运行期隔离（临时库 + USER_MEMORY_DIR/CHARTS_DIR 重定向）由
l1_eval._init_runtime 兜底；本批零生产模型调用（判卷与主链均 glm-4-flash 路由）。
"""
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_EVAL_DIR = _REPO / "scripts" / "eval_agent"
for _p in (_REPO, _EVAL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402

import judge  # noqa: E402

_DEFAULT_DB = "/mnt/d/fortune-data/userdata/fortune.db"


def _task(**kw):
    base = {"id": "TX", "title": "测试任务", "category": "chat", "severity": "P1",
            "turns": [{"role": "user", "text": "1990年5月20日 15:30 北京 男，帮我排盘"}],
            "judge_hint": "四柱庚午辛巳乙酉甲申"}
    base.update(kw)
    return base


def _valid_judge_json(**overrides):
    data = {"accuracy": {"score": 9.0, "justification": "理论应用准确无误"},
            "completeness": {"score": 8.0, "justification": "覆盖全面"},
            "personalization": {"score": 7.0, "justification": "结合用户信息"},
            "actionability": {"score": 6.0, "justification": "有可操作建议"},
            "citation_quality": {"score": 5.0, "justification": "有引证"}}
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


# ================================================================
# 1. rubric 构建（权重和=1.0、每维 0-10 分段描述齐全）
# ================================================================

def test_rubric_weights_sum_to_one_and_names():
    assert sum(judge.DIMENSION_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(judge.DIMENSION_WEIGHTS) == set(judge.DIMS)
    assert judge.DIMENSION_WEIGHTS == {
        "accuracy": 0.30, "completeness": 0.25, "personalization": 0.20,
        "actionability": 0.15, "citation_quality": 0.10,
    }
    assert len(judge.DIMS) == 5


def test_rubric_each_dimension_has_bands():
    """每维五档（0-2/3-4/5-6/7-8/9-10）分段描述齐全（scorer.py 权威对齐）。"""
    for dim in judge.DIMS:
        assert f"### {dim}" in judge.SCORING_RUBRIC, dim
        for band in ("0-2", "3-4", "5-6", "7-8", "9-10"):
            assert f"- **{band}**" in judge.SCORING_RUBRIC, (dim, band)
    # 三档校准锚点（brief 契约：8-10=准确且无错误 / 4-7=部分准确有遗漏 / 0-3=错误/离题）
    for anchor in ("8-10", "4-7", "0-3"):
        assert anchor in judge.THREE_BAND_ANCHORS, anchor
    # 判卷 system prompt 组装含 rubric + 锚点 + 边界声明（不评预测准不准）
    sp = judge.build_judge_system_prompt()
    assert "accuracy" in sp and "0-10" in sp
    assert "不评判预测准不准" in sp


# ================================================================
# 2. 打分调用桩（mock 判卷模型 → 解析与加权正确）
# ================================================================

def test_judge_task_mock_model_valid_json(monkeypatch):
    seen = {}

    def fake_call(system_prompt, user_prompt, api_key):
        seen.update(system_prompt=system_prompt, user_prompt=user_prompt,
                    api_key=api_key)
        return _valid_judge_json()

    monkeypatch.setattr(judge, "call_judge_model", fake_call)
    task = _task(id="T001", category="paipan", severity="P0")
    r = judge.judge_task(task, ["回复一：四柱庚午辛巳乙酉甲申，大运…"], "test-key")
    assert r["skipped"] is False and r["judge_error"] is False
    assert r["parse_level"] == 1
    assert r["dims"]["accuracy"]["score"] == 9.0
    assert r["dims"]["citation_quality"]["score"] == 5.0
    # 加权: 9*.3+8*.25+7*.2+6*.15+5*.1 = 7.5
    assert r["overall"] == pytest.approx(7.5)
    # 调用上下文：system 含 rubric；user 含轮次与真实回复
    assert "accuracy" in seen["system_prompt"] and "0-10" in seen["system_prompt"]
    assert "轮1" in seen["user_prompt"] and "AI: 回复一" in seen["user_prompt"]
    assert seen["api_key"] == "test-key"


def test_judge_task_mock_model_call_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("上游 500")

    monkeypatch.setattr(judge, "call_judge_model", boom)
    r = judge.judge_task(_task(), ["回复内容"], "k")
    assert r["judge_error"] is True
    assert all(v["score"] == 0.0 for v in r["dims"].values())
    assert all(v["judge_error"] for v in r["dims"].values())
    assert r["overall"] == 0.0
    assert "上游 500" in r["judge_error_reason"]


def test_judge_task_call_model_wiring(monkeypatch):
    """温度/模型/max_tokens 走 brief 契约（temperature=0.3、glm-4-flash）。"""
    seen = {}

    def fake_glm(api_key, messages, model, max_tokens, temperature, timeout):
        seen.update(api_key=api_key, model=model, max_tokens=max_tokens,
                    temperature=temperature, timeout=timeout)
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        return "{}"

    monkeypatch.setattr(judge, "_call_glm", fake_glm)
    judge.call_judge_model("sys", "user", "k")
    assert seen["model"] == judge.JUDGE_MODEL == "glm-4-flash"
    assert seen["temperature"] == 0.3
    assert seen["max_tokens"] == judge.JUDGE_MAX_TOKENS


def test_judge_task_skips_empty_replies():
    for replies in ([], [""], ["  ", None]):
        r = judge.judge_task(_task(), replies, "k")
        assert r["skipped"] is True
        assert "跳过" in r["skip_reason"]
        assert r["judge_error"] is False


# ================================================================
# 3. JSON 三级兜底（直接 / 围栏 / 乱格式正则；全失败 → 0 + judge_error）
# ================================================================

def _five_dims_payload(scores):
    return {d: {"score": s, "justification": f"{d}理由"} for d, s in
            zip(judge.DIMS, scores)}


def test_parse_level1_direct_json():
    raw = json.dumps(_five_dims_payload([8.0, 7.0, 6.0, 5.0, 4.0]))
    p = judge.parse_scores(raw)
    assert p["parse_level"] == 1 and p["judge_error"] is False
    assert p["dims"]["accuracy"]["score"] == 8.0
    assert p["dims"]["citation_quality"]["score"] == 4.0


def test_parse_level2_fenced_json():
    raw = "```json\n" + json.dumps(_five_dims_payload([9.5, 8.0, 7.0, 6.0, 5.0])) + "\n```"
    p = judge.parse_scores(raw)
    assert p["parse_level"] == 2 and p["judge_error"] is False
    assert p["dims"]["completeness"]["score"] == 8.0


def test_parse_level3_regex_messy():
    """乱格式（无合法 JSON，但五维名 + score 键可被正则提取）→ level 3。"""
    raw = ("评分结果：\n"
           '"accuracy": {"score": 6.5\n'
           '"completeness": {"score": 7.5\n'
           '"personalization": {"score": 5.0\n'
           '"actionability": {"score": 4.0\n'
           '"citation_quality": {"score": 3.0\n'
           "（缺 justification，整体非 JSON）")
    p = judge.parse_scores(raw)
    assert p["parse_level"] == 3 and p["judge_error"] is False
    assert p["dims"]["completeness"]["score"] == 7.5
    assert p["dims"]["citation_quality"]["score"] == 3.0


def test_parse_level3_brace_matching_partial():
    """花括号滑动截断命中（仅 accuracy 对象完整）→ 缺失维 judge_error。"""
    raw = '正文… {"accuracy": {"score": 8.0, "justification": "准确"}} 其余为散文'
    p = judge.parse_scores(raw)
    assert p["parse_level"] == 3 and p["judge_error"] is True
    assert p["dims"]["accuracy"]["score"] == 8.0
    assert p["dims"]["accuracy"]["judge_error"] is False
    assert p["dims"]["completeness"]["judge_error"] is True


def test_parse_all_fail_garbage():
    """三级全失败（纯散文无任何分数结构）→ 每维 0 分 + judge_error。"""
    p = judge.parse_scores("今天天气不错，无法给出评分，祝您生活愉快")
    assert p["parse_level"] == 3 and p["judge_error"] is True
    assert all(v["score"] == 0.0 and v["judge_error"] for v in p["dims"].values())


def test_parse_empty_raw():
    p = judge.parse_scores("   ")
    assert p["parse_level"] == 0 and p["judge_error"] is True
    assert all(v["judge_error"] for v in p["dims"].values())


def test_parse_missing_dimension_marked_error():
    raw = json.dumps({"accuracy": {"score": 8.0, "justification": "准"},
                      "completeness": {"score": 7.0, "justification": "全"}})
    p = judge.parse_scores(raw)
    assert p["parse_level"] == 1 and p["judge_error"] is True
    assert p["dims"]["personalization"]["score"] == 0.0
    assert p["dims"]["personalization"]["judge_error"] is True


def test_parse_clamps_out_of_range():
    raw = json.dumps({d: {"score": s, "justification": "x"} for d, s in
                      zip(judge.DIMS, [99.0, -5.0, 7.0, 8.0, 9.0])})
    p = judge.parse_scores(raw)
    assert p["dims"]["accuracy"]["score"] == 10.0
    assert p["dims"]["completeness"]["score"] == 0.0


# ================================================================
# 4. 加权汇总与分组统计（含 P0/全量/分域/五维）
# ================================================================

def test_weighted_overall():
    dims = {d: {"score": v, "judge_error": False, "justification": ""}
            for d, v in zip(judge.DIMS, [9.0, 8.0, 7.0, 6.0, 5.0])}
    assert judge.weighted_overall(dims) == pytest.approx(7.5)
    # 全 8 分 → 加权仍 8
    dims8 = {d: {"score": 8.0, "judge_error": False, "justification": ""}
             for d in judge.DIMS}
    assert judge.weighted_overall(dims8) == pytest.approx(8.0)


def _result(id_, severity, category, score, judge_error=False, skipped=False,
            reason=""):
    return {"id": id_, "category": category, "severity": severity,
            "title": "t", "skipped": skipped, "skip_reason": reason,
            "judge_error": judge_error, "judge_error_reason": reason,
            "dims": {d: {"score": score, "judge_error": False,
                         "justification": ""} for d in judge.DIMS},
            "overall": score}


def test_group_metrics_p0_full_and_per_dim():
    results = [
        _result("T1", "P0", "paipan", 8.0),
        _result("T2", "P1", "fortune", 6.0),
        _result("T3", "P2", "chat", 0.0, judge_error=True),
        _result("T4", "P1", "chat", 0.0, skipped=True, reason="无回复"),
    ]
    m = judge.group_metrics(results)
    assert m["judged"] == 2
    assert m["skipped"] == ["T4"] and m["judge_error"] == ["T3"]
    assert m["weighted_avg"] == pytest.approx(7.0)          # (8+6)/2
    assert m["weighted_avg_incl_errors"] == pytest.approx(14.0 / 3, abs=0.001)
    assert m["p0_avg"] == pytest.approx(8.0) and m["p0_count"] == 1
    assert m["per_dimension"]["accuracy"] == pytest.approx(7.0)
    assert m["by_category"]["paipan"] == pytest.approx(8.0)
    assert m["by_category"]["fortune"] == pytest.approx(6.0)


def test_group_metrics_empty():
    m = judge.group_metrics([_result("T1", "P0", "chat", 0.0,
                                     judge_error=True)])
    assert m["judged"] == 0 and m["weighted_avg"] is None
    assert m["p0_avg"] is None


# ================================================================
# 5. 校准抽样（P0/P1/P2 各 10 覆盖 10 域；确定性可复现）
# ================================================================

def _load_tasks():
    p = judge.TASKS_PATH
    if not p.exists():
        pytest.skip("评估集不存在: %s" % p)
    return [json.loads(line) for line in p.read_text(encoding="utf-8")
            .splitlines() if line.strip()]


def test_calibration_selection_contract():
    tasks = _load_tasks()
    ids = judge.select_calibration_ids(tasks)
    # 30 条唯一
    assert len(ids) == 30 and len(set(ids)) == 30
    by_id = {t["id"]: t for t in tasks}
    assert all(i in by_id for i in ids)
    # P0/P1/P2 各 10
    sev = {}
    for i in ids:
        sev[by_id[i]["severity"]] = sev.get(by_id[i]["severity"], 0) + 1
    assert sev == {"P0": 10, "P1": 10, "P2": 10}
    # 域覆盖：P0 上限 8 域（paipan/fortune/zeri/hehun/xingming/qian/chat/edge）、
    # P1 全部 10 域、P2 7 域；并集覆盖全部 10 域
    covered = {}
    for i in ids:
        covered.setdefault(by_id[i]["severity"], set()).add(by_id[i]["category"])
    assert len(covered["P0"]) == 8
    assert len(covered["P1"]) == 10
    assert len(covered["P2"]) == 7
    assert set().union(*covered.values()) == set(judge.l1_eval.CATEGORIES)
    # 确定性可复现
    assert ids == judge.select_calibration_ids(tasks)
    # 契约锁定（域字母序优先取最小 id，余量按 id 升序补齐）
    assert ids == ["T075", "T083", "T019", "T039", "T001", "T057", "T045",
                   "T028", "T002", "T003",
                   "T071", "T084", "T016", "T040", "T058", "T005", "T051",
                   "T046", "T029", "T064",
                   "T070", "T081", "T061", "T053", "T049", "T036", "T067",
                   "T037", "T054", "T055"]


# ================================================================
# 5b. 临时库 schema 对齐 shim（qian_saves.kind 补列——骨架遗留适配）
# ================================================================

def test_align_temp_schema_adds_kind_column(tmp_path):
    """旧 schema 的 qian_saves（无 kind 列，生产库现状）→ 补列且默认 original。

    骨架种子 DDL 的 CREATE IF NOT EXISTS 不补列，种子 INSERT 会报
    "no column named kind"（E2/E3 遗留，校准首测暴露 T051/T053/T057）——
    shim 使骨架种子代码原样可跑，语义一致（种子恒写 kind='original'）。
    """
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE qian_saves (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " user_id TEXT NOT NULL, no INTEGER NOT NULL, drawn_at REAL)")
        con.commit()
    finally:
        con.close()
    assert judge.align_temp_schema(str(db)) is None
    con = sqlite3.connect(db)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(qian_saves)")]
        assert "kind" in cols
        con.execute("INSERT INTO qian_saves (user_id, no, kind, drawn_at)"
                    " VALUES ('u', 5, 'original', 1.0)")  # 骨架种子同款 INSERT
        con.commit()
    finally:
        con.close()


def test_align_temp_schema_keeps_new_schema(tmp_path):
    """已有 kind 列（新 schema）→ 不动。"""
    import sqlite3
    db = tmp_path / "new.db"
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE qian_saves (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " user_id TEXT NOT NULL, no INTEGER NOT NULL,"
                    " kind TEXT NOT NULL DEFAULT 'original', drawn_at REAL)")
        con.commit()
    finally:
        con.close()
    assert judge.align_temp_schema(str(db)) is None
    con = sqlite3.connect(db)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(qian_saves)")]
        assert cols.count("kind") == 1
    finally:
        con.close()


# ================================================================
# 6. 冒烟实跑（真实主链 + 真实 glm-4-flash 判卷；缺 key/库即 skip）
# ================================================================

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


def test_smoke_judge_calibration_slice(tmp_path):
    """轻量冒烟：校准抽样前 2 条（T001 P0 / T005 P1）真实跑链 + 真实判卷。"""
    if not _llm_keys_ready():
        pytest.skip("缺少 ZHIPU_API_KEY（glm-4-flash 判卷 + 主链路由）")
    if not _real_db_ready():
        pytest.skip("真实库不存在: %s" % _DEFAULT_DB)
    snap = _smoke_env()
    try:
        tasks = [t for t in _load_tasks() if t["id"] in ("T001", "T005")]
        out = judge.run_eval(tasks, tmp_path / "l3-smoke",
                             calibration=False, keep_tmp=True)
        m = out["metrics"]
        print(f"\n[smoke l3] 已判卷 {m['judged']} | 加权平均 {m['weighted_avg']} "
              f"| P0 平均 {m['p0_avg']} | judge_error {m['judge_error']}")
        # 运行完整性断言（首测基线不锁分数——真实数字如实留档）
        for r in out["results"]:
            if r["skipped"]:
                assert r["skip_reason"], f"{r['id']} 跳过须标注原因"
            else:
                assert set(r["dims"]) == set(judge.DIMS)
                assert r["parse_level"] in (0, 1, 2, 3)
                assert isinstance(r["overall"], float)
                assert r["replies"], f"{r['id']} 缺真实回复（判卷输入）"
                assert 0.0 <= r["overall"] <= 10.0
        # 落盘完整性
        for fname in ("meta.json", "results.json", "report.md"):
            assert (tmp_path / "l3-smoke" / fname).exists(), fname
        # 非校准模式不落校准样本（calibration_samples.json 仅校准模式写入）
        assert not (tmp_path / "l3-smoke" / "calibration_samples.json").exists()
    finally:
        _restore_env(snap)
        judge.l1_eval._close_runtime()
