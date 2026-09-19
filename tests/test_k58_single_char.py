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
    """k49：单字元素的 match 分支不得是裸单字（护栏/搭配都必须 ≥2 字）"""
    for r in SINGLE_CHAR_RULES:
        for branch in r["match"].split("|"):
            literal = re.split(r"\(\?!", branch)[0]      # 去掉护栏 lookahead 后再看长度
            assert len(literal) >= 2, (r["name"], branch)


def test_head_form_branches_carry_boundary_guard():
    """头部形态分支必须带边界护栏（防止「梦见X杯/腿/门」这类前缀误伤）"""
    guarded = 0
    for r in SINGLE_CHAR_RULES:
        for b in r.get("match_branches") or []:
            if b["kind"] == "head_form" and "(?!" in b["branch"]:
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
