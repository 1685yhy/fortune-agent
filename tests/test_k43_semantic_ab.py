# -*- coding: utf-8 -*-
"""k43 A/B：旧词表层（main=b7d3ee1）vs 新语义层（k43）——同一批问句的混淆矩阵。

- 用例数据 = tests/k43_ab_cases.py（单一事实源；含来源与标注口径）
- 基线 = `git show <base>:src/rag/search_trigger.py`（提交版逐字，非手抄）
- 新层 = 工作区实现（语义路由真实 bge-m3；未就绪时逐字回退词表层）

默认跑「完整性」用例（零模型、秒级）；真机 A/B 需显式开闸：

    K43_AB=1 OMP_NUM_THREADS=1 python -m pytest tests/test_k43_semantic_ab.py -q -s

开闸后额外做：① 真实 bge-m3 语义路由；② 两个视图的混淆矩阵：
  视图 A = 纯规则层（llm_needs_search=False）
  视图 B = 与模型信号组合（llm_needs_search=llm_signal，理想模型信号）
以及失败例句（漏搜/多搜逐条列出）。
"""
import os
import subprocess
import sys
import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from k43_ab_cases import (CASES, CORPUS_COUNT, GUARD_COUNT,  # noqa: E402
                          AUTHORED_COUNT, POS_COUNT)

BASE_REV = "b7d3ee1"          # k43 base（main）
_AGENT_TASKS = _REPO / "data" / "eval" / "agent_tasks.jsonl"


def _load_baseline():
    """从 git 取 base 提交的 search_trigger 逐字加载（模块名隔离）。"""
    src = subprocess.run(
        ["git", "-C", str(_REPO), "show", f"{BASE_REV}:src/rag/search_trigger.py"],
        capture_output=True, text=True, check=True).stdout
    tmp = Path(tempfile.mkdtemp(prefix="k43_base_")) / "k43_base_st.py"
    tmp.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("k43_base_st", tmp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["k43_base_st"] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 一、用例集完整性（零模型，默认跑）
# ============================================================

class TestCaseSetIntegrity:
    def test_counts_and_uniqueness(self):
        assert len(CASES) >= 100, len(CASES)          # brief：≥100 条
        assert len({c.cid for c in CASES}) == len(CASES)
        assert len({c.text for c in CASES}) == len(CASES)
        assert POS_COUNT >= 25
        print(f"\n用例 %d 条（正例 %d / 负例 %d；corpus %d / guard %d / authored %d）"
              % (len(CASES), POS_COUNT, len(CASES) - POS_COUNT,
                 CORPUS_COUNT, GUARD_COUNT, AUTHORED_COUNT))

    def test_labels_and_sources_valid(self):
        for c in CASES:
            assert c.expect in ("search", "none"), c
            assert c.expect_llm in ("search", "none"), c
            assert c.src.split(":")[0] in ("corpus", "guard", "authored"), c
            assert c.text.strip() == c.text and c.text, c

    def test_corpus_rows_verbatim_from_agent_tasks(self):
        """corpus: 行必须与 data/eval/agent_tasks.jsonl 逐字一致（防手抄漂移），
        且 src 的任务 id 必须是该 turn 的真实来源任务。"""
        if not _AGENT_TASKS.exists():
            pytest.skip("data/eval/agent_tasks.jsonl 不在本工作区")
        first_id = {}
        for line in _AGENT_TASKS.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for t in row.get("turns") or []:
                text = t.get("text")
                if text and text not in first_id:
                    first_id[text] = row["id"]
        for c in CASES:
            if c.src.startswith("corpus:"):
                assert c.text in first_id, (c.cid, c.src, c.text)
                assert first_id[c.text] == c.src.split(":", 1)[1], (
                    c.cid, c.src, first_id[c.text])

    def test_llm_signal_overrides_are_only_hard_veto_or_fallback(self):
        """llm_signal 覆盖只允许两类：① 硬否决用例（expect_llm=none，模型误报不越权）
        ② 词表层漏搜的模型兜底用例（expect=none 且 expect_llm=search）。"""
        for c in CASES:
            if c.llm_signal and c.expect != "search":
                assert c.expect_llm == "search" or c.expect_llm == "none", c
            if c.expect_llm == "search":
                assert c.llm_signal, c
            if not c.llm_signal:
                assert c.expect_llm == c.expect, c


# ============================================================
# 二、A/B 真机矩阵（K43_AB=1 才跑）
# ============================================================

def _confusion(rows):
    tp = sum(1 for r in rows if r["expect"] == "search" and r["got"])
    fp = sum(1 for r in rows if r["expect"] == "none" and r["got"])
    fn = sum(1 for r in rows if r["expect"] == "search" and not r["got"])
    tn = sum(1 for r in rows if r["expect"] == "none" and not r["got"])
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _run_view(decide, expect_field, llm_signal):
    """跑一遍全部用例：expect_field 决定地面真值视图，llm_signal 决定是否注入模型信号。"""
    rows = []
    for c in CASES:
        res = decide(c.text, llm_needs_search=(c.llm_signal if llm_signal else False))
        rows.append({"cid": c.cid, "text": c.text, "src": c.src,
                     "expect": c.expect if expect_field == "expect" else c.expect_llm,
                     "got": bool(res.should_search), "reason": res.reason})
    return rows


def _fmt_failures(rows, kind, limit=40):
    out = []
    for r in rows:
        if kind == "FN" and r["expect"] == "search" and not r["got"]:
            out.append(f"    [FN {r['reason']}] {r['src']} | {r['text']}")
        if kind == "FP" and r["expect"] == "none" and r["got"]:
            out.append(f"    [FP {r['reason']}] {r['src']} | {r['text']}")
    return out[:limit]


@pytest.mark.skipif(not os.environ.get("K43_AB"),
                    reason="真机 A/B（bge-m3 加载 ~40s）：K43_AB=1 显式开闸")
def test_ab_matrix_real_model():
    from src.rag import semantic_router
    from src.rag.search_trigger import decide_search as new_decide
    base = _load_baseline()
    assert semantic_router._disabled() is False
    assert semantic_router.warmup(blocking=True), "bge-m3 语义路由未就绪"
    assert semantic_router.route("帮我查一下苹果公司的最新新闻").label == "search"

    report = {}
    for view, expect_field, llm in (("A(llm=False)", "expect", False),
                                    ("B(llm=信号)", "expect_llm", True)):
        b = _run_view(base.decide_search, expect_field, llm)
        n = _run_view(new_decide, expect_field, llm)
        report[view] = {
            "baseline": {**_confusion(b), "failures": _fmt_failures(b, "FN")
                         + _fmt_failures(b, "FP")},
            "k43": {**_confusion(n), "failures": _fmt_failures(n, "FN")
                    + _fmt_failures(n, "FP")},
        }
        print(f"\n=== 视图 {view} | 用例 {len(CASES)} ===")
        for name, m in (("旧词表层", report[view]["baseline"]),
                        ("k43 语义层", report[view]["k43"])):
            print(f"  {name}: TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']}")
        print("  旧失败例句:")
        for line in report[view]["baseline"]["failures"]:
            print(line)
        print("  k43 失败例句:")
        for line in report[view]["k43"]["failures"]:
            print(line)
    out = Path(tempfile.gettempdir()) / "k43_ab_matrix.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n矩阵 JSON: {out}")

    # 增益锁：新层两个方向都不得劣于基线（漏搜/多搜都不许变差），且召回不降
    for view, m in report.items():
        b, n = m["baseline"], m["k43"]
        assert n["fn"] <= b["fn"], (view, b, n)
        assert n["fp"] <= b["fp"], (view, b, n)
        assert n["tp"] >= b["tp"], (view, b, n)

    # 红线：硬否决逐条复验（模型误报 needs_search 也绝不越过金融/命理本地）
    for msg in ("今天股市行情怎么样", "帮我查一下大盘指数", "今年运势如何",
                "我在易宝支付上班，今年运势怎么样？", "这个月适合搬家吗"):
        d = new_decide(msg, llm_needs_search=True)
        assert not d.should_search, (msg, d)
        assert d.reason in ("finance", "local"), (msg, d.reason)
