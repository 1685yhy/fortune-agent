"""k58 批次：解梦单字覆盖缺口（M-1）+ k55 Minor 收口（M-2~M-5）。

M-1 的核心约束（控制方口径）：**match 分支必须来自语料统计**，不许人工挑；
规则名可以仍是单字，但**分支不得是裸单字**（k49 误伤教训）。
"""
import re

import pytest

from src.engines.dream import DreamEngine, DREAM_PATTERN_RULES as _LEGACY_UNUSED  # noqa: F401
from src.engines.dream_rules import DREAM_PATTERN_RULES
from src.engines.dream_sanitize import (
    ALL_RISK_FORMS,
    HIGH_RISK_WORDS,
    NEUTRAL_MAP,
    TRADITIONAL_VARIANTS,
    find_high_risk,
    neutralize,
)


class _NullRetriever:
    def search(self, query, top_k=5, **kw):
        return []


SINGLE_CHAR_RULES = [r for r in DREAM_PATTERN_RULES if len(r["name"]) == 1]


# ── M-1 ① 裸「梦见X」覆盖率 ──────────────────────────────────────────

def test_bare_single_char_queries_are_mostly_covered():
    """裸「梦见X」三项非空率 ≥90%（改前实测 2.2%）"""
    engine = DreamEngine()
    empty = []
    for r in SINGLE_CHAR_RULES:
        res = engine.analyze(f"梦见{r['name']}", _NullRetriever())
        if not (res.dream_type and res.symbols and res.tones):
            empty.append(r["name"])
    total = len(SINGLE_CHAR_RULES)
    assert total >= 40, total
    rate = (total - len(empty)) / total
    assert rate >= 0.90, f"裸查询三项非空率 {rate:.1%}（未覆盖：{empty[:10]}）"


def test_top_single_char_elements_covered():
    """Top 单字意象（覆盖量前 15）必须都能被裸查询覆盖"""
    top = sorted(SINGLE_CHAR_RULES, key=lambda r: -r.get("coverage", 0))[:15]
    engine = DreamEngine()
    missing = []
    for r in top:
        res = engine.analyze(f"梦见{r['name']}", _NullRetriever())
        if not (res.dream_type and res.symbols and res.tones):
            missing.append(r["name"])
    assert missing == [], missing


# ── M-1 ② 分支来自语料统计（不是人工挑） ───────────────────────────────

def test_single_char_rules_have_statistical_match_evidence():
    """每条单字规则的 match 都要带**语料证据**：分支 + 覆盖条数（可复核）"""
    for r in SINGLE_CHAR_RULES:
        branches = r.get("match_branches") or []
        assert branches, f"{r['name']} 缺 match_branches 证据"
        for b in branches:
            assert b["coverage"] >= 0
            assert b["kind"] in ("head_form", "collocation"), b
        # 至少一条分支有正覆盖（否则不该出现在规则表里）
        assert any(b["coverage"] > 0 for b in branches), r["name"]


def test_single_char_match_branches_never_bare_single_char():
    """k49：单字元素的 match 分支不得是裸单字（护栏/搭配都必须 ≥2 字）。

    注意：边界护栏是 `(?=…)` 组、其字符类内部含 `|`，必须先用 split_branches
    剥掉再判 —— 否则护栏内容（`$`/`了`/`的`…）会被误当成单字分支。
    """
    from scripts.k55_dream.collocation import split_branches
    for r in SINGLE_CHAR_RULES:
        for branch in split_branches(r["match"]):
            assert len(branch) >= 2, (r["name"], branch)


def test_head_form_branches_carry_boundary_guard():
    """头部形态分支必须带边界护栏（防止「梦见X杯/腿/门」这类前缀误伤）"""
    guarded = 0
    for r in SINGLE_CHAR_RULES:
        for b in r.get("match_branches") or []:
            if b["kind"] == "head_form" and "(?=" in b["branch"]:
                guarded += 1
    assert guarded >= 30, f"带护栏的头部形态分支只有 {guarded} 个"


# ── M-1 ③ 对抗集（含该字非该意象 + 正当形态） ──────────────────────────

def test_adversarial_inputs_do_not_false_match():
    """≥30 条「含该字但不是该意象」的输入：零误命中（k49 验收）"""
    from scripts.k55_dream.adversarial_check import DIFFERENT_OBJECT
    assert len(DIFFERENT_OBJECT) >= 30, len(DIFFERENT_OBJECT)
    engine = DreamEngine()
    bad = []
    for text, banned in DIFFERENT_OBJECT:
        names = [h["rule"]["name"] for h in engine.match_patterns(text)]
        if banned in names:
            bad.append((text, banned, names))
    assert bad == [], f"前缀误伤：{bad[:5]}"


def test_legit_phrasings_are_still_covered():
    """护栏不能把该命中的也挡了：正当口语形态必须被覆盖"""
    from scripts.k55_dream.adversarial_check import LEGIT
    engine = DreamEngine()
    missed = []
    for text, expect in LEGIT:
        names = [h["rule"]["name"] for h in engine.match_patterns(text)]
        res = engine.analyze(text, _NullRetriever())
        if not (names or res.elements or (res.dream_type and res.symbols and res.tones)):
            missed.append((text, expect))
    assert missed == [], missed


# ── M-2 释义依据不得是空壳（纯出处句/小标题/半截句） ─────────────────────

def test_no_empty_shell_glosses():
    """全表扫：gloss 不得是纯出处句 / 页面小标题 / 半截句"""
    from scripts.k55_dream.build_rules import is_citation_only, is_fragment, is_heading
    bad = [(r["name"], r["gloss"][:40]) for r in DREAM_PATTERN_RULES
           if is_citation_only(r["gloss"]) or is_heading(r["gloss"])
           or is_fragment(r["gloss"])]
    assert bad == [], bad[:5]


# ── M-3 无方向档不得配极性引文（或必须带限定语） ────────────────────────

def test_no_direction_rules_have_nonpolar_gloss_or_caveat():
    from scripts.k55_dream.build_rules import sentence_polarity
    bad = []
    for r in DREAM_PATTERN_RULES:
        if r["luck"] not in ("中性", "提醒类"):
            continue
        if sentence_polarity(r["gloss"]) and "不代表吉凶" not in r["gloss"]:
            bad.append((r["name"], r["luck"], r["gloss"][:40]))
    assert bad == [], bad[:5]


# ── M-4 净化表覆盖繁体/异体 ───────────────────────────────────────────

@pytest.mark.parametrize("word", TRADITIONAL_VARIANTS)
def test_traditional_variants_are_neutralized(word):
    out = neutralize(f"传统上认为要{word}才行。")
    assert find_high_risk(out) == [], out


def test_neutral_map_covers_all_risk_forms_including_traditional():
    missing = [w for w in ALL_RISK_FORMS if w not in NEUTRAL_MAP]
    assert missing == [], missing
    dirty = {k: v for k, v in NEUTRAL_MAP.items() if find_high_risk(v)}
    assert dirty == {}, f"替换值含风险形态：{dirty}"
    # 简体 19 词仍是门禁契约的一部分（与 k52 对齐）
    from tests.test_k52_compliance_scan import HIGH_RISK_WORDS as K52_WORDS
    assert set(HIGH_RISK_WORDS) == set(K52_WORDS)


# ── M-5 解梦引擎出口净化有效（含繁体） ─────────────────────────────────

def test_dream_exit_sanitization_effective_including_traditional():
    """锁住「解梦引擎出口净化有效」：prompt + 用户可见字段对 **19 词 + 繁体** 零命中"""
    engine = DreamEngine()
    dreams = [
        "梦见大水冲进了家里，很害怕",
        "梦见破財，大师说要轉運",
        "梦见和对象开车出去玩，回来太困了把车留在半道了",
        "梦见月光", "梦见蜘蛛", "梦见厕所",
    ]
    for dream in dreams:
        from src.engines.dream import format_dream_prompt
        res = engine.analyze(dream, _NullRetriever())
        blob = "\n".join([
            format_dream_prompt(dream, res),
            " ".join(res.interpretations or []),
            " ".join(res.symbols or []),
            " ".join(getattr(res, "rule_notes", []) or []),
            " ".join(getattr(res, "tones", []) or []),
            getattr(res, "reality_projection", "") or "",
        ])
        assert find_high_risk(blob) == [], (dream, find_high_risk(blob))


def test_rules_table_still_verbatim_for_audit():
    """存储层守卫仍然绿：规则表引文保持逐字（不许被净化改写）"""
    from src.engines.dream_rules import DREAM_PATTERN_RULES as rules
    risky = [r for r in rules if find_high_risk(r["gloss"])]
    assert risky, "预期规则表里仍有逐字引文含风险词（否则本测试失去意义）"
    for r in risky:
        assert find_high_risk(neutralize(r["gloss"])) == [], r["name"]


# ══════════════════ k58 r2：审查 4 条 Important 的不变式锁 ══════════════════

# 上一版（k55）规则里被**明确判为不该保留**的元素（形容词/状态词/繁体导航词），
# 它们不是「梦的意象」→ 允许覆盖回退，但必须逐条登记理由（I-1 要求）。
RETIRED_WITH_REASON = {
    "吉兆": "判词术语，不是意象", "凶兆": "判词术语",
    "漂亮": "形容词", "破旧": "状态词", "干净": "形容词",
    "健康": "形容词（状态，非意象）", "腐烂": "状态词", "成熟": "形容词",
    "解夢": "繁体页面导航词（非意象）",
}


def test_rule_table_does_not_lose_covered_elements_vs_previous_version():
    """r2 I-1 不变式：上一版规则名的裸查询覆盖率不得回退（除登记豁免项）。"""
    import subprocess
    from pathlib import Path as _P
    repo = _P(__file__).resolve().parents[1]
    try:
        src = subprocess.run(["git", "show", "55ea3f7:src/engines/dream_rules.py"],
                             cwd=repo, capture_output=True, text=True, check=True).stdout
    except Exception as e:                                   # pragma: no cover
        pytest.skip(f"取不到上一版规则模块：{e}")
    old_names = re.findall(r'"name": \'([^\']+)\'', src)
    assert len(old_names) >= 200, len(old_names)
    engine = DreamEngine()
    uncovered = []
    for n in old_names:
        res = engine.analyze(f"梦见{n}", _NullRetriever())
        if not (res.dream_type and res.symbols and res.tones):
            uncovered.append(n)
    unexplained = [n for n in uncovered if n not in RETIRED_WITH_REASON]
    assert unexplained == [], f"未经登记就失去覆盖的元素：{unexplained}"
    # 覆盖率本身也要达标（改前 69.2%）
    rate = (len(old_names) - len(uncovered)) / len(old_names)
    assert rate >= 0.95, f"上一版元素覆盖率只有 {rate:.1%}"


def test_head_entry_gloss_label_matches_source():
    """r2 I-4 不变式：标了「语料「梦见X」词条原文」的，必须有该元素的裸条目证据。

    改前：`is_head = core in elements` 按**条目**判定 → 兔子条目的句子被标成
    「梦见子」词条原文（21/229 错配）。现在逐元素判定（core == el）。
    """
    bad = [(r["name"], r.get("head_entry_count", 0)) for r in DREAM_PATTERN_RULES
           if r.get("gloss_evidence") == "head_entry" and r.get("head_entry_count", 0) < 1]
    assert bad == [], f"gloss 标注与来源不一致：{bad[:5]}"


def test_single_char_rules_have_standalone_evidence():
    """r2 I-3 不变式：单字规则必须在语料里**独立成词**（词缀碎片不得成规则名）"""
    for r in SINGLE_CHAR_RULES:
        assert r.get("standalone_count", 0) >= 5, (r["name"], r.get("standalone_count"))


# brief 指定必须覆盖的族（不受自动词性门槛约束：它们是控制方点名要的条目，
# 例如「赶不上车」jieba 会拆成 赶不上(d)+车，但它是交通族的一员）
MANDATED_NAMES = {"开车", "车祸", "停车", "找不到车", "迷路", "赶不上车",
                  "堵车", "坐车", "迟到"}


def test_no_blocked_pos_rule_names():
    """r2 I-3 不变式：形容词/副词/功能词类不得成为规则名（brief 指定族除外）"""
    from scripts.k55_dream.build_rules import pos_allowed
    bad = [r["name"] for r in DREAM_PATTERN_RULES
           if r["name"] not in MANDATED_NAMES and not pos_allowed(r["name"])]
    assert bad == [], bad
    # 审查点名的泄漏词逐一回归
    names = {r["name"] for r in DREAM_PATTERN_RULES}
    for w in ("子", "面", "公", "母", "活", "男", "身", "很大", "不到", "不见", "上长"):
        assert w not in names, w


def test_novel_compounds_introduce_no_new_false_positive():
    """r2 I-2 不变式：新词/罕见复合（含「改后命中别的字」类）零命中。

    基线 0 命中，k58 r1 一度净增 20 条（水立方→水、火烈鸟→火、猫头鹰→猫/鹰…）。
    """
    from scripts.k55_dream.adversarial_check import NOVEL_COMPOUNDS, NO_HIT_AT_ALL
    engine = DreamEngine()
    hits = [(t, [h["rule"]["name"] for h in engine.match_patterns(t)])
            for t in NOVEL_COMPOUNDS]
    hits = [h for h in hits if h[1]]
    assert hits == [], hits
    viol = [(t, [h["rule"]["name"] for h in engine.match_patterns(t)])
            for t, _ in NO_HIT_AT_ALL]
    viol = [v for v in viol if v[1]]
    assert viol == [], viol


# ══════════════════ k58 r3：极性反向回归（A-1）+ Minor ══════════════════

def test_no_luck_gloss_polarity_conflict_by_reviewer_vocabulary():
    """r3 A-1 不变式：用**审查口径的独立词表**扫全表，gloss 与 luck 不得反向。

    独立复核模块 `scripts/k55_dream/polarity_audit.py` 不复用 build_rules 的实现
    （词表更宽，含 光明/发展/兴旺/不佳/窝火/波折/量入为出/欠顺/谨慎/多小心）。
    改前：基线 0 → r1 5 → r2 9 条（孔雀 luck=凶／gloss 说「前途光明」…）。
    """
    from scripts.k55_dream.polarity_audit import luck_polarity, polarity
    conflicts = []
    for r in DREAM_PATTERN_RULES:
        lp = luck_polarity(r["luck"])
        gp = polarity(r["gloss"])
        if lp and gp and lp != gp:
            conflicts.append((r["name"], r["luck"], gp, r["gloss"][:40]))
    assert conflicts == [], conflicts[:5]


def test_named_reversal_rules_are_self_consistent():
    """r3 A-1 点名三条：孔雀 / 螃蟹 / 购买 —— 同行必须自洽（同向或已降为无方向）"""
    from scripts.k55_dream.polarity_audit import luck_polarity, polarity
    d = {r["name"]: r for r in DREAM_PATTERN_RULES}
    for name in ("孔雀", "螃蟹", "购买"):
        r = d[name]
        lp, gp = luck_polarity(r["luck"]), polarity(r["gloss"])
        assert not (lp and gp and lp != gp), (name, r["luck"], gp, r["gloss"][:40])


def test_compound_word_symmetry_for_single_char_variants():
    """r3 m-1：大蛇/小蛇 之类「形容词+单字意象」不得被词性门槛误排（对称性）"""
    engine = DreamEngine()
    big = [h["rule"]["name"] for h in engine.match_patterns("梦见大蛇")]
    small = [h["rule"]["name"] for h in engine.match_patterns("梦见小蛇")]
    assert small, "小蛇 应命中"
    assert "大蛇" in big, f"大蛇 被误排（命中：{big}）"


def test_longer_compound_does_not_leak_suffix_rule():
    """r3 m-4：更长复合词不得命中其尾部的多字规则（狗尾巴草 ≠ 尾巴）"""
    engine = DreamEngine()
    assert "尾巴" not in [h["rule"]["name"] for h in engine.match_patterns("梦见狗尾巴草")]
    # 正当形态不受影响
    assert "尾巴" in [h["rule"]["name"] for h in engine.match_patterns("梦见尾巴")]
