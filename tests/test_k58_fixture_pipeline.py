"""k58 r7：**冻结语料夹具**——让口径改动能被测试抓到（控制方裁决二）。

审查实测的缺口：随包规则表是「提交进库的产物」，**没有任何用例从语料重算** →
改分句器（L4）、改 `sentence_is_usable` 门槛（L5）、加一条标点归一映射（L6）、
**把同源拆成两个键**、把古籍豁免改成**模糊匹配** —— 锁全绿、定向子集也全绿，
口径会**静默漂移**。本文件用一小段**冻结入库**的语料（`tests/data/k58_fixture_*`）
跑一遍管线核心，与冻结期望值逐项比对：**任何口径改动 → 立刻变红，且很快**
（不跑全量、不依赖活语料 `raw/`）。

夹具由 `scripts/k55_dream/freeze_fixture.py` 生成，内含 4 类**定制条目**，
专为让上述植入必红而存在（自检见该脚本 `self_check()`）：
① 5 字判词句（长度门槛）② 仅差 `…`/`...` 的一对句（折叠映射）
③ 与古籍引文近似但**不等**的句子（豁免模糊匹配）④ DF=12 的重复句（公式句判据）。

⚠️ 重新冻结 = 改口径：必须重跑 `freeze_fixture.py` 并重新过审，不许直接改期望值。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.k55_dream import build_rules as B
from scripts.k55_dream import freeze_fixture as FF

FIX_DIR = Path(__file__).resolve().parent / "data"
CORPUS = FIX_DIR / "k58_fixture_corpus.jsonl"
EXPECT = FIX_DIR / "k58_fixture_expected.json"
CLASSICS = FIX_DIR / "k58_fixture_classics.json"


def _load() -> tuple:
    entries = [json.loads(x) for x in CORPUS.read_text(encoding="utf-8").splitlines() if x.strip()]
    classics = json.loads(CLASSICS.read_text(encoding="utf-8"))
    expected = json.loads(EXPECT.read_text(encoding="utf-8"))
    return entries, classics, expected


def test_fixture_files_are_frozen_and_present():
    """夹具三件套必须入库且在（缺了这条，下面的比对会静默跳过）"""
    for p in (CORPUS, EXPECT, CLASSICS):
        assert p.exists() and p.stat().st_size > 0, p
    entries, _, expected = _load()
    crafted = [e for e in entries if e["source"] == "fixture_crafted"]
    assert len(entries) >= 40 and len(crafted) >= 4, (len(entries), len(crafted))
    assert expected["element_count"] == len(expected["elements"]) >= 10


def test_pipeline_output_on_frozen_corpus_fixture_is_unchanged():
    """**核心断言**：管线在冻结夹具上的输出 == 冻结期望值。

    口径一改（分句器 / 可用性门槛 / 折叠映射 / 同源拆分 / 豁免模糊化 / 公式句判据），
    这里就会指出**哪个元素、哪个字段**变了 —— 而不是等到 k60 换语料时才发现档位漂了。
    """
    entries, classics, expected = _load()
    got = FF.run_pipeline(entries, classics)

    assert got["formula_keys"] == expected["formula_keys"], (
        "【口径变了·公式句判据】冻结夹具上判出的公式句与期望值不同：\n"
        f"  现在={got['formula_keys']}\n  期望={expected['formula_keys']}\n"
        "公式句/豁免判据的任何改动都必须重跑 freeze_fixture.py 并重新过审。")

    diffs = []
    for el, exp in expected["elements"].items():
        cur = got["elements"].get(el)
        if cur is None:
            diffs.append((el, "元素缺失", None, exp))
            continue
        # 组装**前**（选句层）+ 组装**后**（产物形态：gloss 的最终形态产自组装层，
        # r8 I-2 —— 不覆盖它，y1 限定语 / y2 反向降级 / y3 兜底文案的改动夹具全绿）
        for field in ("counts", "pool_keys", "df", "usable_sentences", "head_sentences",
                      "luck_raw", "gloss_raw", "gloss_evidence_raw",
                      "luck", "gloss", "gloss_evidence", "luck_basis",
                      "type", "tone", "symbols"):
            if cur[field] != exp[field]:
                diffs.append((el, field, cur[field], exp[field]))
    assert not diffs, (
        "【口径变了】管线在冻结夹具上的输出与 r7 冻结期望值不同（"
        f"{len(diffs)} 处）。前 3 处：\n" +
        "\n".join(f"  {el}.{f}: 现在={c!r} 期望={e!r}" for el, f, c, e in diffs[:3]) +
        "\n\n口径改动必须走「重跑 freeze_fixture.py + 重跑全部审计 + 更新报告 r7 段」三件套；"
        "禁止直接改期望值让测试变绿。")


def test_assembly_layer_boundary_branches_are_covered_or_declared():
    """组装层三类补丁的覆盖边界（r8 I-2「不许两头都要」）。

    - **y1 限定语 / y3 兜底文案**：由上面的夹具用例在**管线级**覆盖（夹具里
      `马` 的 gloss 带「不代表吉凶」、`刀` 走 `fallback_no_same_scenario`）；
    - **y2 反向降级**（`aggregate_only_reverse_gloss`）：**管线里不可达** ——
      选句层 `pick()` 按**宽词表极性**优先同向句、古籍分支跳过反向引文、
      兜底分支强制中性 ⇒ 「方向档 + 只有反向 gloss」这条路径当前无法由语料构造
      （产物表里该证据档次真实命中也是 0）。故这里用**单元级**用例直接覆盖该分支，
      并把它登记为「管线不可达、仅单元覆盖」，而不是假装夹具覆盖了它。
    """
    from scripts.k55_dream.build_rules import assemble_rule_fields
    # y2：luck=吉（方向档）而 gloss 是反向（凶）→ 必须换成聚合说明 + 改证据档次
    out = assemble_rule_fields("某物", 12, 3, 1, "吉",
                               "梦见某物，是不祥之兆（语料同场景判词）",
                               "corpus_same_scenario", "中性类", ["情境变化"])
    assert out["ev_kind"] == "aggregate_only_reverse_gloss", out["ev_kind"]
    assert "未选作依据" in out["gloss"] and "不祥之兆" not in out["gloss"], out["gloss"]
    assert "聚合词频（吉 3 / 凶 1）" in out["luck_basis"], out["luck_basis"]
    assert out["luck"] == "吉", "y2 只换依据、不丢方向（裁决二：不许弃权）"
    # y3：兜底分支必须强制中性 + 用兜底文案
    out3 = assemble_rule_fields("某物", 7, 0, 0, "吉多于凶", "",
                                "fallback_no_same_scenario", "吉兆类", ["情境变化"])
    assert out3["luck"] == "中性" and "没有匹配到" in out3["gloss"], out3
    assert out3["ptype"] == "中性类", out3["ptype"]
    # y1：中性档 + 极性 gloss → 必须加限定语（两种 gloss 形态都要覆盖）
    for g in ("梦见某物，主吉（语料同场景判词）", "《敦煌本梦书》记载：梦见某物，吉"):
        o = assemble_rule_fields("某物", 9, 1, 0, "中性", g, "classic_quote", "中性类", [])
        assert "不代表吉凶" in o["gloss"], (g, o["gloss"])


def test_pool_and_counting_share_one_folding_key():
    """**同源必须是可失败断言**（r7 裁决一）：只改一侧的折叠键 → 必须红。

    审查植入「只改句池的键、不改计数的键」时锁全绿 —— 说明当时「同源」只是
    「恰好一致」。这里断言：句池去重键与计数折叠键是**同一个函数对象**，
    且在夹具上二者行为一致（池键集合 == 对每个池内代表句施加同一函数的结果）。
    """
    assert B.SENTENCE_KEY_FOR_POOL is B.SENTENCE_KEY_FOR_COUNT is B.sentence_key, (
        "【同源被拆开】句池去重键与计数折叠键不再是同一个函数："
        f"pool={B.SENTENCE_KEY_FOR_POOL} count={B.SENTENCE_KEY_FOR_COUNT}。"
        "两者必须同源（否则同一句会在池里算 1 条、在计数里算 N 条）。")

    entries, classics, _ = _load()
    elements = set(FF.ELEMENTS) | {e["title"] for e in entries}
    _uniq, pools = B.element_scope_pool(elements, entries=entries)
    checked = 0
    for el, p in pools.items():
        for k, raw in p["raw"].items():
            assert k == B.SENTENCE_KEY_FOR_POOL(raw) == B.SENTENCE_KEY_FOR_COUNT(raw), (
                f"【同源被拆开】元素 {el} 的池键 {k!r} 与其代表句的归一结果不一致："
                f"{B.SENTENCE_KEY_FOR_POOL(raw)!r} / {B.SENTENCE_KEY_FOR_COUNT(raw)!r}")
            checked += 1
    assert checked > 20, f"夹具上只检查到 {checked} 个池键，样本太少"


# ══════════ k58 r10：⭐ 真跑入口（C-1 的结构性修复） ══════════

def test_main_entrypoint_runs_end_to_end_on_frozen_fixture(tmp_path, monkeypatch):
    """`main()` 必须在夹具上**端到端跑通** —— 「测了函数」不等于「测了入口」。

    背景（r10 C-1，真 Critical）：r8/r9 两次重构 `main()` 都**没跑过入口** ——
    `assemble_rule_fields` 的 `luck_basis` 形参在函数体第一行即被覆盖（调用点却没绑定）、
    `formula` 只是 `scan_corpus` 的局部变量（`main` 从未绑定）。两条都在**第一条规则**
    上抛异常，**产物表因此停在 r6 快照**，而当时 204 条测试 + 冻结夹具 + 23 项注入
    **全绿照放行** —— 因为 `tests/` 里**零处 `.main(`**。
    本用例把入口纳入常规门禁：夹具当输入（快、不依赖活语料）、输出重定向到 tmp
    （**不碰受审的冻结表**）。
    """
    import sys as _sys
    entries, classics, _ = _load()
    el_names = sorted({B.normalize_core(e["title"]) for e in entries})

    full = {el: sum(1 for e in entries if el in B.normalize_core(e["title"]))
            for el in el_names}
    monkeypatch.setattr(B, "load_stats", lambda: ([], {el: [] for el in el_names}, full))
    monkeypatch.setattr(B, "load_site_categories", lambda: {})
    monkeypatch.setattr(B, "load_classic_quotes", lambda: classics)
    monkeypatch.setattr(B, "load_ngram_map", lambda: {})
    monkeypatch.setattr(B, "load_retained_names", lambda spec: set(el_names))
    monkeypatch.setattr(B.colloc_mod, "corpus_stats",
                        lambda els: {"standalone": {}, "head": {}})
    out_mod = tmp_path / "dream_rules_e2e.py"
    monkeypatch.setattr(B, "REPORTS", tmp_path)
    monkeypatch.setattr(B, "OUT_MODULE", out_mod)
    monkeypatch.setattr(_sys, "argv", ["build_rules.py", "--top", "400",
                                       "--min-coverage", "1", "--min-standalone", "1",
                                       "--min-coverage-for-retain", "1",
                                       "--retain-from", str(CORPUS)])

    rc = B.main()
    assert rc == 0, f"入口返回 {rc}"
    # 产物必须**完整**（r10 C-1 的另一半：崩之前会先写 rule_exclusions.json，留半写状态）
    assert out_mod.exists() and out_mod.stat().st_size > 500, "规则模块未生成/过小"
    assert (tmp_path / "rule_exclusions.json").exists(), "排除清单未写"
    ns: dict = {}
    exec(compile(out_mod.read_text(encoding="utf-8"), str(out_mod), "exec"), ns)
    rules = ns["DREAM_PATTERN_RULES"]
    assert len(rules) >= 5, f"入口只产出 {len(rules)} 条规则"
    for r in rules:
        for field in ("name", "match", "type", "luck", "gloss", "counts", "tone"):
            assert r.get(field), f"规则 {r.get('name')} 缺字段 {field}"
        assert r["match"] and "|" in r["match"] or len(r["match"]) >= 2
