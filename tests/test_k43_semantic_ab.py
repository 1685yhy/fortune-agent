# -*- coding: utf-8 -*-
"""k43 A/B：旧词表层（main=b7d3ee1）vs 新语义层（k43）——同一批问句的混淆矩阵。

- 用例数据 = tests/k43_ab_cases.py（单一事实源；含来源与标注口径）
- 基线 = `git show <base>:src/rag/search_trigger.py`（提交版逐字，非手抄）
- 新层 = 工作区实现（语义路由真实 bge-m3；未就绪时逐字回退词表层）

默认跑「完整性」用例（零模型、秒级）；真机 A/B 需显式开闸：

    K43_AB=1 OMP_NUM_THREADS=1 python -m pytest tests/test_k43_semantic_ab.py -q -s

开闸后额外做：① 真实 bge-m3 语义路由；② 两个视图 × 四个重叠分组的混淆矩阵：
  视图 A = 纯规则层（llm_needs_search=False）
  视图 B = 与模型信号组合（llm_needs_search=llm_signal，理想模型信号）
  分组 full / heldout（与示例集逐字不交）/ overlap（重叠）/ heldout_norm（归一化
  严格：逐字不交 且 归一化相似度 < 0.90）——**结论以 heldout 为准**
  （k43-r1，审查 Important-1：旧口径把训练样本当测试样本，28% 逐字重叠未披露；
   k43-r2，复审 Minor-1：逐字级挡不住标点/措辞近重复 → 加归一化层 + embedding
   余弦披露）；
  另有 k43r1 组（审查 Important-2/3 修复）与 k43r2 组（复审 R1-I1 修复，
  外部公开事实 vs 个人所属/私人体验**双向**用例）单列。
以及失败例句（漏搜/多搜逐条列出）。
"""
import difflib
import os
import re
import subprocess
import sys
import importlib.util
import json
import tempfile
import unicodedata
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from k43_ab_cases import (CASES, CORPUS_COUNT, GUARD_COUNT,  # noqa: E402
                          AUTHORED_COUNT, R1_COUNT, R2_COUNT, POS_COUNT)

BASE_REV = "b7d3ee1"          # k43 base（main）
_AGENT_TASKS = _REPO / "data" / "eval" / "agent_tasks.jsonl"
_EXAMPLES_PATH = _REPO / "src" / "rag" / "semantic_router_examples.json"
_SRC_PREFIXES = ("corpus", "guard", "authored", "k43r1", "k43r2")
_NEAR_DUP_THR = 0.90          # 归一化相似度 ≥ 此值 = 近重复（k43-r2，复审 Minor-1）


def _examples_texts() -> set:
    """示例集句子集合（用于重叠度量；审查 Important-1）。"""
    if not _EXAMPLES_PATH.exists():
        return set()
    return {e["text"] for e in
            json.loads(_EXAMPLES_PATH.read_text(encoding="utf-8"))["examples"]}


def _norm_text(text: str) -> str:
    """近重复检测用归一化：NFKC（全角→半角）+ 去空白/标点（k43-r2，复审 Minor-1）。

    「逐字不交」挡不住标点/空白差异的近重复（复审实测 held-out 里还有 2 条
    ≥0.99：`2026年9月15日搬家，帮我选个日子`、`我在易宝支付上班 今年运势怎么样`）。
    """
    t = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\W_]+", "", t, flags=re.UNICODE)


def _near_dup_scan(cases, examples_texts, thr: float = _NEAR_DUP_THR):
    """逐条求「与示例集的归一化最大相似度」（difflib，零依赖零模型，确定性）。

    返回 [(case, sim, nearest_example)]；归一化后逐字相同 → 1.0。
    """
    ex = [(e, _norm_text(e)) for e in examples_texts]
    rows = []
    for c in cases:
        n = _norm_text(c.text)
        best, nearest = 0.0, ""
        for e, ne in ex:
            if not n or not ne:
                continue
            r = 1.0 if n == ne else difflib.SequenceMatcher(None, n, ne).ratio()
            if r > best:
                best, nearest = r, e
        rows.append((c, best, nearest))
    return rows


def _near_dup_buckets(rows) -> dict:
    """近重复分桶（披露用）：逐字重复 / ≥0.99 / ≥0.95 / ≥0.90。"""
    return {"verbatim": sum(1 for _, s, _ in rows if s >= 0.999),
            "ge99": sum(1 for _, s, _ in rows if s >= 0.99),
            "ge95": sum(1 for _, s, _ in rows if s >= 0.95),
            "ge90": sum(1 for _, s, _ in rows if s >= 0.90)}


def _overlap_groups() -> dict:
    """按「与示例集的重叠程度」分组（结论以 heldout 为准）。

    full        = 全量（旧口径，含记忆化样本）
    heldout     = 与示例集**无**逐字重叠
    overlap     = 与示例集逐字重叠（训练样本当测试样本，只作对照）
    heldout_norm= **归一化严格** held-out：逐字不交 且 归一化最大相似度 < 0.90
                  （k43-r2，复审 Minor-1：逐字级防污染挡不住标点/空白/措辞近重复）
    """
    ex = _examples_texts()
    rows = {c.cid: s for c, s, _ in _near_dup_scan(CASES, ex)}
    return {"full": list(CASES),
            "heldout": [c for c in CASES if c.text not in ex],
            "overlap": [c for c in CASES if c.text in ex],
            "heldout_norm": [c for c in CASES
                             if c.text not in ex and rows[c.cid] < _NEAR_DUP_THR]}


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
        print(f"\n用例 %d 条（正例 %d / 负例 %d；corpus %d / guard %d / authored %d"
              f" / k43r1 %d / k43r2 %d）"
              % (len(CASES), POS_COUNT, len(CASES) - POS_COUNT,
                 CORPUS_COUNT, GUARD_COUNT, AUTHORED_COUNT, R1_COUNT, R2_COUNT))

    def test_labels_and_sources_valid(self):
        for c in CASES:
            assert c.expect in ("search", "none"), c
            assert c.expect_llm in ("search", "none"), c
            assert c.src.split(":")[0] in _SRC_PREFIXES, c
            assert c.text.strip() == c.text and c.text, c

    def test_new_groups_heldout_and_not_near_duplicate(self):
        """k43-r1/r2（审查 Important-1 + 复审 Minor-1 防自证循环）：

        ① 新增用例与示例集**逐字不交**（k43-r1 起的锁）；
        ② 且**归一化相似度 < 0.90**（去标点/空白/NCFK 后仍近重复 = 训练样本当
           测试样本；复审实测旧 held-out 里还有 2 条标点级 ≥0.99 近重复）；
        同时对既有语料/护栏句子的重叠做**显式披露**（不隐藏、不豁免），并把
        held-out 子集规模下限锁住——报告与结论一律以 held-out（严格口径）重述。
        """
        ex = _examples_texts()
        if not ex:
            pytest.skip("示例集不在本工作区")
        newest = [c for c in CASES if c.src.startswith(("k43r1:", "k43r2:"))]
        assert newest, "新组用例缺失"
        hit = [c.text for c in newest if c.text in ex]
        assert not hit, f"k43r1/r2 用例与示例集逐字重叠（自证循环）: {hit}"
        rows = {c.cid: (s, e) for c, s, e in _near_dup_scan(newest, ex)}
        near = [(c.text, rows[c.cid][0], rows[c.cid][1]) for c in newest
                if rows[c.cid][0] >= _NEAR_DUP_THR]
        assert not near, f"k43r1/r2 用例与示例集近重复（≥{_NEAR_DUP_THR}）: {near}"
        groups = _overlap_groups()
        rate = len(groups["overlap"]) / len(CASES)
        pos = [c for c in CASES if c.expect == "search"]
        pos_rate = sum(1 for c in pos if c.text in ex) / len(pos)
        buckets = _near_dup_buckets(_near_dup_scan(CASES, ex))
        print(f"\n示例集重叠披露：全量重叠 {len(groups['overlap'])}/{len(CASES)}"
              f"（{rate:.0%}）；正例重叠 {pos_rate:.0%}；held-out {len(groups['heldout'])}"
              f" / 归一化严格 held-out {len(groups['heldout_norm'])} 条")
        print(f"近重复披露（归一化相似度）：逐字/标点级 {buckets['verbatim']} 条（=1.0）"
              f" / ≥0.99 {buckets['ge99']} / ≥0.95 {buckets['ge95']}"
              f" / ≥0.90 {buckets['ge90']}（k43r1/r2 新增组均为 0）")
        assert len(groups["heldout"]) >= 100, len(groups["heldout"])
        assert len(groups["heldout_norm"]) >= 100, len(groups["heldout_norm"])
        assert rate <= 0.35, f"重叠率 {rate:.0%} 超警戒（结论须以 held-out 为准）"

    def test_near_duplicate_metric_is_deterministic(self):
        """近重复度量自检（零模型）：归一化吃掉标点/空白/全角差异；
        近重复判定阈值 0.90 对「换措辞但同义」的句子不误伤新组。"""
        assert _norm_text("2026年9月15日搬家，帮我选个日子") == \
               _norm_text("2026年9月15日搬家 帮我选个日子")
        assert _norm_text("今年运势怎么样？") == _norm_text("今年运势怎么样")
        assert _norm_text("ＡＢＣ １２３") == _norm_text("ABC123")
        assert _norm_text("") == ""

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
        ② 词表层漏搜的模型兜底用例（expect=none 且 expect_llm=search）。

        k43-r1：expect_llm=search 亦可不靠模型信号达成——k43r1 组（语义层 ACCEPT /
        VETO 免疫的目标类）显式标 llm_signal=False（真实分析器在这些句上本就漏判），
        故不再要求「expect_llm=search ⇒ llm_signal=True」；expect=none 时
        llm_signal 仍必须为 False（不注入误报）。"""
        for c in CASES:
            if c.expect_llm == "search":
                assert c.llm_signal or c.src.startswith(("k43r1:", "k43r2:")), c
            if not c.llm_signal:
                assert c.expect_llm == c.expect, c

    def test_disable_switch_is_verbatim_base(self):
        """红线锁：SEMANTIC_ROUTER_DISABLE=1 时判定与 base（git b7d3ee1）**逐字一致**。

        覆盖全部用例 × llm∈{False, True}；四元组（should_search/query/entity/reason）
        全等——语义层关掉 = 词表层原行为（含 k43-r1 的外部问句豁免：豁免只改变
        「语义层在场时」的否决/护栏，关掉后行为与 base 无差异）。
        """
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SEMANTIC_ROUTER_DISABLE", "1")
        monkeypatch.setenv("SEMANTIC_ROUTER_PRELOAD", "0")
        try:
            base = _load_baseline()
            from src.rag.search_trigger import decide_search as new_decide
            diff = []
            for c in CASES:
                for llm in (False, True):
                    b = base.decide_search(c.text, llm_needs_search=llm)
                    n = new_decide(c.text, llm_needs_search=llm)
                    if (b.should_search, b.query, b.entity, b.reason) != \
                       (n.should_search, n.query, n.entity, n.reason):
                        diff.append((c.cid, llm, b, n))
            assert not diff, diff[:5]
        finally:
            monkeypatch.undo()


# ============================================================
# 二、A/B 真机矩阵（K43_AB=1 才跑）
# ============================================================

def _confusion(rows):
    tp = sum(1 for r in rows if r["expect"] == "search" and r["got"])
    fp = sum(1 for r in rows if r["expect"] == "none" and r["got"])
    fn = sum(1 for r in rows if r["expect"] == "search" and not r["got"])
    tn = sum(1 for r in rows if r["expect"] == "none" and not r["got"])
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _run_view(decide, expect_field, llm_signal, cases=None):
    """跑一遍用例：expect_field 决定地面真值视图，llm_signal 决定是否注入模型信号。"""
    rows = []
    for c in (CASES if cases is None else cases):
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


def _embedding_near_dup_report(semantic_router) -> list:
    """held-out 近重复披露（**embedding 余弦口径** = 复审 Minor-1 的原始度量）。

    复用路由已加载的 embedder 与示例矩阵（不新建模型实例、不新增依赖）；
    对每条用例取「与示例集的最大余弦」，按组披露 ≥0.99 / ≥0.95 / ≥0.90 条数；
    并锁死新增组（k43r1/k43r2）无 ≥0.99 近重复（措辞/标点级复制）。
    """
    import numpy as np
    with semantic_router._LOCK:
        mats = semantic_router._MATRICES
        emb = semantic_router._EMBEDDER
    if mats is None or emb is None:                       # pragma: no cover
        return ["近重复披露：跳过（embedder/示例矩阵未就绪）"]
    ex = np.vstack([np.asarray(mats[0], dtype=np.float32),
                    np.asarray(mats[1], dtype=np.float32)])
    cases = list(CASES)
    q = np.asarray(emb.encode([c.text for c in cases]), dtype=np.float32)
    mx = (q @ ex.T).max(axis=1)                            # 每条的最大余弦
    idx = {c.cid: i for i, c in enumerate(cases)}
    groups = dict(_overlap_groups(),
                  k43r1=[c for c in CASES if c.src.startswith("k43r1:")],
                  k43r2=[c for c in CASES if c.src.startswith("k43r2:")])
    lines = []
    for gname in ("heldout", "heldout_norm", "k43r1", "k43r2"):
        vals = [float(mx[idx[c.cid]]) for c in groups[gname]]
        lines.append(f"近重复披露（embedding 余弦 | {gname} n={len(vals)}）："
                     f"≥0.99 {sum(1 for v in vals if v >= 0.99)}"
                     f" / ≥0.95 {sum(1 for v in vals if v >= 0.95)}"
                     f" / ≥0.90 {sum(1 for v in vals if v >= 0.90)}")
        if gname == "heldout":                     # 最接近的 3 条（披露用）
            top = sorted(groups[gname], key=lambda c: -mx[idx[c.cid]])[:3]
            lines.append("  最接近示例集的 3 条：" + "；".join(
                f"{float(mx[idx[c.cid]]):.4f} {c.text[:22]!r}" for c in top))
    for gname in ("k43r1", "k43r2"):
        bad = [(c.text, round(float(mx[idx[c.cid]]), 4))
               for c in groups[gname] if mx[idx[c.cid]] >= 0.99]
        assert not bad, (gname, bad)
    return lines


@pytest.mark.skipif(not os.environ.get("K43_AB"),
                    reason="真机 A/B（bge-m3 加载 ~40s）：K43_AB=1 显式开闸")
def test_ab_matrix_real_model():
    from src.rag import semantic_router
    from src.rag.search_trigger import decide_search as new_decide
    base = _load_baseline()
    assert semantic_router._disabled() is False
    assert semantic_router.warmup(blocking=True), "bge-m3 语义路由未就绪"
    assert semantic_router.route("帮我查一下苹果公司的最新新闻").label == "search"
    try:
        _ab_matrix_body(semantic_router, base, new_decide)
    finally:
        # k43-r1（测试隔离）：真机矩阵跑完清进程内状态——真实矩阵/模型**不得泄漏**
        # 到后续测试文件（同进程后续文件的 decide_search 会因语义层在场而改判定：
        # 全量跑实测 k17 parity 用例的「这个行业怎么样」被语义 VETO 成 reason=semantic）。
        semantic_router.reset_for_tests()


def _ab_matrix_body(semantic_router, base, new_decide):

    groups = _overlap_groups()
    print(f"\n重叠披露：全量 {len(groups['full'])} / held-out {len(groups['heldout'])}"
          f" / overlap {len(groups['overlap'])} / 归一化严格 held-out"
          f" {len(groups['heldout_norm'])}（结论以 held-out 为准）")
    report = {}
    for view, expect_field, llm in (("A(llm=False)", "expect", False),
                                    ("B(llm=信号)", "expect_llm", True)):
        report[view] = {}
        for gname, cases in groups.items():
            b = _run_view(base.decide_search, expect_field, llm, cases)
            n = _run_view(new_decide, expect_field, llm, cases)
            report[view][gname] = {
                "n": len(cases),
                "baseline": {**_confusion(b), "failures": _fmt_failures(b, "FN")
                             + _fmt_failures(b, "FP")},
                "k43": {**_confusion(n), "failures": _fmt_failures(n, "FN")
                        + _fmt_failures(n, "FP")},
            }
            print(f"\n=== 视图 {view} | 组 {gname} | 用例 {len(cases)} ===")
            for name, m in (("旧词表层", report[view][gname]["baseline"]),
                            ("k43 语义层", report[view][gname]["k43"])):
                print(f"  {name}: TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']}")
            print("  旧失败例句:")
            for line in report[view][gname]["baseline"]["failures"]:
                print(line)
            print("  k43 失败例句:")
            for line in report[view][gname]["k43"]["failures"]:
                print(line)
        # k43-r1 / k43-r2 新组单列（审查 Important-2/3 与复审 R1-I1 的定向证据）
        for gname, prefix, title in (
                ("k43r1", "k43r1:", "审查 Important-2/3 修复组"),
                ("k43r2", "k43r2:", "复审 R1-I1 修复组（外部公开事实 vs 个人指代）")):
            sub = [c for c in CASES if c.src.startswith(prefix)]
            b = _run_view(base.decide_search, expect_field, llm, sub)
            n = _run_view(new_decide, expect_field, llm, sub)
            report[view][gname] = {"n": len(sub), "baseline": _confusion(b),
                                   "k43": _confusion(n),
                                   "failures": _fmt_failures(n, "FN") + _fmt_failures(n, "FP")}
            print(f"\n=== 视图 {view} | 组 {gname}（{title}）| 用例 {len(sub)} ===")
            print(f"  旧词表层: {report[view][gname]['baseline']}")
            print(f"  k43 语义层: {report[view][gname]['k43']}")
            for line in report[view][gname]["failures"]:
                print(line)
    out = Path(tempfile.gettempdir()) / "k43_ab_matrix.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n矩阵 JSON: {out}")

    # 近重复披露（embedding 余弦口径 = 复审 Minor-1 的原始度量；复用已加载实例）
    for line in _embedding_near_dup_report(semantic_router):
        print(line)

    # 增益锁：新层两个方向都不得劣于基线（漏搜/多搜都不许变差），且召回不降。
    # 全量/held-out/overlap/held-out(norm)/k43r1/k43r2 逐组锁（held-out 为决策依据）。
    for view, gs in report.items():
        for gname, m in gs.items():
            b, n = m["baseline"], m["k43"]
            assert n["fn"] <= b["fn"], (view, gname, b, n)
            assert n["fp"] <= b["fp"], (view, gname, b, n)
            assert n["tp"] >= b["tp"], (view, gname, b, n)
    # k43-r1 修复组：漏搜归零；k43-r2 修复组（双向）：漏搜与多搜都归零
    for view, gs in report.items():
        assert gs["k43r1"]["k43"]["fn"] == 0, (view, gs["k43r1"])
        assert gs["k43r2"]["k43"]["fn"] == 0, (view, gs["k43r2"])
        assert gs["k43r2"]["k43"]["fp"] == 0, (view, gs["k43r2"])

    # 红线：硬否决逐条复验（模型误报 needs_search 也绝不越过金融/命理本地）
    for msg in ("今天股市行情怎么样", "帮我查一下大盘指数", "今年运势如何",
                "我在易宝支付上班，今年运势怎么样？", "这个月适合搬家吗"):
        d = new_decide(msg, llm_needs_search=True)
        assert not d.should_search, (msg, d)
        assert d.reason in ("finance", "local"), (msg, d.reason)
