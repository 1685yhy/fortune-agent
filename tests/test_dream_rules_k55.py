"""k55 批次：解梦规则层扩充（语料统计）+ 输出骨架 + 现实投影口径 + 爬虫基础库。

对标：brief §2（规则层 12→80+，全部语料统计得出）、§3（输出骨架）、
§1.1（爬虫合规：robots/限速/重试/断点续爬/编码修复）。

测试均为离线可跑（不触网）：规则模块是入库产物，爬虫库用本地桩验证。
"""
import json
import re
import time
from pathlib import Path

import pytest

from src.engines.dream import DreamEngine, DreamResult, format_dream_prompt
from src.engines.dream_rules import (
    DREAM_PATTERN_RULES,
    HVDC_NORMS,
    REALITY_PROJECTION,
    RULE_COUNT,
)


class _RecordingRetriever:
    def __init__(self):
        self.queries = []

    def search(self, query, top_k=5, **kw):
        self.queries.append(query)
        return []


# ── ① 规则层规模与硬约束 ────────────────────────────────────────────

def test_rule_count_at_least_80():
    """规则层 ≥80 条（brief 硬指标：12 → 80+）"""
    assert RULE_COUNT >= 80, RULE_COUNT
    assert len(DREAM_PATTERN_RULES) == RULE_COUNT


def test_no_single_char_branch_in_any_pattern():
    """k49 教训：match 的每个分支都必须 ≥2 字（单字会前缀误伤）"""
    bad = []
    for r in DREAM_PATTERN_RULES:
        for branch in r["match"].split("|"):
            if len(branch) < 2:
                bad.append((r["name"], branch))
    assert bad == [], bad


def test_every_rule_has_required_fields():
    """每条规则都要有 类型/核心象征/情绪基调/传统释义/覆盖量（brief §2 口径）"""
    for r in DREAM_PATTERN_RULES:
        assert r["name"] and isinstance(r["name"], str)
        assert r["type"], r
        assert isinstance(r["symbols"], list) and r["symbols"], r
        assert r["tone"], r
        assert r["gloss"], r
        assert isinstance(r["coverage"], int) and r["coverage"] >= 0, r
        assert r["luck"] in ("大吉", "吉", "吉多于凶", "凶多于吉", "凶",
                             "中性", "提醒类"), r
        # r2：覆盖量口径与释义依据档次必须随条目标注（M-3）
        assert r.get("coverage_basis"), r
        assert r.get("gloss_evidence") in (
            "classic_quote", "head_entry", "corpus_same_scenario",
            "fallback_no_same_scenario"), r


def test_rules_contain_no_trigger_or_meta_words():
    """规则条目不得是触发词/元词（「梦见」当正则会命中所有梦境文本）"""
    banned = {"梦见", "梦到", "做梦", "解梦", "周公", "梦境", "梦", "夢"}
    names = {r["name"] for r in DREAM_PATTERN_RULES}
    assert not (names & banned), names & banned


# ── ② 交通驾驶族（控制方真实梦例所在族）必须覆盖 ─────────────────────

DRIVING_FAMILY = ["开车", "车祸", "停车", "找不到车", "迷路", "赶不上车"]
# r2 M-4：交通族**全部 8 条**（含堵车/坐车）都要覆盖，不只抽查前 6 条
DRIVING_FAMILY_ALL = DRIVING_FAMILY + ["堵车", "坐车"]


@pytest.mark.parametrize("name", DRIVING_FAMILY_ALL)
def test_driving_family_covered(name):
    """交通/驾驶族**8/8** 每一类都必须有规则条目（r2 M-4）"""
    assert any(r["name"] == name for r in DREAM_PATTERN_RULES), name


@pytest.mark.parametrize("dream,expect_hit", [
    ("梦见开车", "开车"),
    ("梦见出车祸了", "车祸"),
    ("梦见找不到停车位", "停车"),
    ("梦见车不见了找不着", "找不到车"),
    ("梦见迷路了", "迷路"),
    ("梦见赶不上车", "赶不上车"),
])
def test_driving_family_matching(dream, expect_hit):
    """族内每一类都能被真实句式命中"""
    hits = DreamEngine().match_patterns(dream)
    assert any(h["rule"]["name"] == expect_hit for h in hits), \
        (dream, [h["rule"]["name"] for h in hits])


def test_controller_real_dream_three_fields_nonempty():
    """控制方实测梦例：三项（类型/象征/情绪基调）必须非空（改前全空）"""
    dream = "梦见和对象开车出去玩，回来太困了把车留在半道了"
    res = DreamEngine().analyze(dream, _RecordingRetriever())
    assert res.dream_type, "梦境类型为空"
    assert res.symbols, "核心象征为空"
    assert res.tones, "情绪基调为空"
    assert res.rule_hits, "未命中任何规则"
    assert any(n in res.rule_hits for n in ("开车", "车", "停车", "找不到车")), res.rule_hits
    # 策略A 必须拿到检索词（改前：无元素 → 空查询）
    r = _RecordingRetriever()
    DreamEngine().analyze(dream, r)
    assert any(q.startswith("梦见 ") for q in r.queries), r.queries


# ── ②b r2 整改夹具：事故/交通族不做吉凶推断 + 同场景释义 ───────────────

ACCIDENT_DREAMS = ["梦见出车祸了", "梦见车祸", "梦见开车撞了"]


@pytest.mark.parametrize("dream", ACCIDENT_DREAMS)
def test_accident_family_luck_never_auspicious(dream):
    """r2 I-2 夹具：事故/交通族一律「中性」，**永远不许判成吉**。

    改前实测：`analyze("梦见出车祸了")` → rule_luck='吉'（按语料词频推出来的，
    而那些吉向判词谈的是别的场景）——「梦见车祸是吉」是信任事故。
    """
    res = DreamEngine().analyze(dream, _RecordingRetriever())
    assert res.rule_hits, dream
    assert res.rule_luck in ("中性", "提醒类"), (dream, res.rule_luck)
    assert res.rule_luck not in ("大吉", "吉", "吉多于凶"), (dream, res.rule_luck)
    # 提示词里必须明确「不要替用户下吉凶结论」
    prompt = format_dream_prompt(dream, res)
    assert "不要替用户下吉凶结论" in prompt, prompt[:400]


def test_accident_gloss_is_same_scenario_or_honest_fallback():
    """r2 I-2 夹具：车祸/开车的释义依据**不许拿别场景的句子顶**。

    改前实测 gloss = 「梦见老人出车祸，得此梦，虽有财运可得…」（别的场景）、
    开车 gloss = 「梦见开车撞人…」（复合场景，不是「开车」这个词条）。
    """
    for dream, name in (("梦见出车祸了", "车祸"), ("梦见开车", "开车")):
        res = DreamEngine().analyze(dream, _RecordingRetriever())
        note = next(n for n in res.rule_notes if n.startswith(name + "（"))
        rule = next(r for r in DREAM_PATTERN_RULES if r["name"] == name)
        assert rule["gloss_evidence"] in ("classic_quote", "head_entry",
                                          "corpus_same_scenario",
                                          "fallback_no_same_scenario")
        if rule["gloss_evidence"] == "fallback_no_same_scenario":
            # 诚实兜底：必须明说没有同场景依据、且不做吉凶判断
            assert "没有匹配到" in note and "不做吉凶判断" in note, note
            assert "老人出车祸" not in note, note
        else:
            assert "老人出车祸" not in note, note


def test_driving_family_luck_all_neutral():
    """交通族 8 条 luck 全部为无方向档（中性/提醒类）"""
    for name in DRIVING_FAMILY_ALL:
        rule = next((r for r in DREAM_PATTERN_RULES if r["name"] == name), None)
        assert rule, name
        assert rule["luck"] in ("中性", "提醒类"), (name, rule["luck"])
        assert rule["tone"].startswith("提醒"), (name, rule["tone"])


def test_late_is_separate_time_pressure_rule():
    """r2 I-3：「迟到/来不及」不是车——独立成压力类规则，且不再进交通族。

    改前实测：`analyze("梦见上学迟到了")` → rule_hits=['赶不上车']（误伤）。
    """
    res = DreamEngine().analyze("梦见上学迟到了", _RecordingRetriever())
    assert res.rule_hits, res.rule_hits
    assert "赶不上车" not in res.rule_hits, res.rule_hits
    assert "迟到" in res.rule_hits, res.rule_hits
    late = next(r for r in DREAM_PATTERN_RULES if r["name"] == "迟到")
    assert late["type"] == "压力类", late
    # 赶不上车的 match 里不许再出现时间词
    drive = next(r for r in DREAM_PATTERN_RULES if r["name"] == "赶不上车")
    for bad in ("迟到", "来不及", "时间不够"):
        assert bad not in drive["match"], (bad, drive["match"])


def test_driving_match_stays_vehicle_semantic():
    """交通族 match 只含车辆语义（不含「迟到」这类时间词）"""
    for name in ("开车", "车祸", "停车", "找不到车", "堵车", "坐车", "赶不上车"):
        rule = next(r for r in DREAM_PATTERN_RULES if r["name"] == name)
        for bad in ("迟到", "来不及", "误点", "拖延"):
            assert bad not in rule["match"], (name, bad, rule["match"])


# ── ②c r2 I-4：叙述性/体裁性元词不得进规则表 ─────────────────────────

def test_no_meta_or_narrative_words_in_rules():
    """r2 I-4：META_STOPWORDS（语料体裁产物）+ 叙述词必须 0 条进规则表"""
    from scripts.k55_dream.build_rules import RULE_STOPWORDS
    names = {r["name"] for r in DREAM_PATTERN_RULES}
    leaked = sorted(names & set(RULE_STOPWORDS))
    assert leaked == [], f"元词/叙述词混入规则表：{leaked}"
    # 审查点名的具体词，逐一点名回归
    for w in ("工作", "表示", "生活", "说明", "关系", "可能", "方面", "象征",
              "女性", "运势", "心理", "梦者", "意味", "受到", "看见", "认识"):
        assert w not in names, w


def test_three_fields_rate_not_lowered_by_rule_rebuild():
    """r2 I-4 复核②：剔除元词后三项非空率不降（真实梦例快照上实测 100%）"""
    import json
    from pathlib import Path as _P
    q = _P("/mnt/d/fortune-data/books/k55_dream/reports/benchmark_queries_k55.json")
    if not q.exists():
        pytest.skip("梦例快照未生成")
    queries = json.loads(q.read_text(encoding="utf-8"))["queries"]
    engine = DreamEngine()
    ok = 0
    for item in queries:
        res = engine.analyze(item["text"], _RecordingRetriever())
        if res.dream_type and res.symbols and res.tones:
            ok += 1
    assert ok == len(queries), f"三项非空 {ok}/{len(queries)}"


# ── ②d r3 不变式：兜底必须成立、结论与依据不许反向 ─────────────────────

def test_fallback_implies_no_same_scenario_evidence():
    """r3 I-A 不变式：走「没有匹配到判词句」兜底的规则，计数必须为 0。

    改前实测（机械口径 bug）：句池多了一道 `[主有宜忌吉凶]` 字面过滤，而
    ji/xiong 计数没有 → 「梦见刀，不祥之兆，会面临困难。」这类判词只进计数、
    不进句池 → gloss 找不到 → **假兜底**（刀：xiong=22 却说「没有匹配到」），
    luck 被强制中性，与自身 type=警示类/tone 自相矛盾。共 17 条。
    """
    for r in DREAM_PATTERN_RULES:
        c = r.get("counts") or {}
        if r["gloss_evidence"] == "fallback_no_same_scenario":
            assert c.get("ji", 0) + c.get("xiong", 0) == 0, \
                f"{r['name']}：兜底成立性被违反 counts={c}"
            assert "没有匹配到" in r["gloss"], r["name"]
        else:
            assert r["gloss"], r["name"]
            assert "没有匹配到" not in r["gloss"], r["name"]


def test_luck_and_gloss_polarity_never_conflict():
    """r3 I-B 不变式：同一行里「传统倾向」与「释义依据」不许反向。

    改前实测：`妻子`（吉多于凶 / 「秋季有血光之灾」）、`女孩`（吉多于凶 /
    「慎防发生意外」）等 22 条 —— 这行是直接喂给 LLM 且用户可见的骨架。
    """
    from scripts.k55_dream.build_rules import luck_polarity, sentence_polarity
    conflict = [(r["name"], r["luck"], r["gloss"][:30])
                for r in DREAM_PATTERN_RULES
                if luck_polarity(r["luck"]) and sentence_polarity(r["gloss"])
                and luck_polarity(r["luck"]) != sentence_polarity(r["gloss"])]
    assert conflict == [], conflict[:5]


def test_type_and_luck_never_conflict():
    """r3 I-B 附带：无方向 luck 不得配吉兆类/警示类 type（刀那类矛盾）"""
    bad = [(r["name"], r["type"], r["luck"]) for r in DREAM_PATTERN_RULES
           if r["luck"] in ("中性", "提醒类") and r["type"] in ("吉兆类", "警示类")]
    assert bad == [], bad[:5]


def test_prompt_question_one_matches_luck_direction():
    """r3 I-B②：无方向档的 prompt 不许再问「是吉是凶」（与「不要下吉凶结论」对冲）"""
    neutral_dreams = ["梦见出车祸了", "梦见开车撞了"]
    for dream in neutral_dreams:
        res = DreamEngine().analyze(dream, _RecordingRetriever())
        assert res.rule_luck in ("中性", "提醒类"), (dream, res.rule_luck)
        prompt = format_dream_prompt(dream, res)
        assert "不要替用户下吉凶结论" in prompt
        assert "**吉凶判断**：这个梦是吉是凶？" not in prompt, dream
        assert "**梦在提醒什么**" in prompt, dream
    # 有方向档仍保留原第 1 问（既有口径不变）
    res = DreamEngine().analyze("梦见被蛇追", _RecordingRetriever())
    prompt = format_dream_prompt("梦见被蛇追", res)
    assert "**吉凶判断**：这个梦是吉是凶？" in prompt


def test_neutral_luck_tone_has_no_direction_words():
    """r3 I-B 附带：无方向档的 tone 不许带吉凶形容词（自相矛盾）"""
    for name in ("车祸", "开车", "停车", "迷路", "赶不上车", "堵车", "坐车", "找不到车"):
        rule = next(r for r in DREAM_PATTERN_RULES if r["name"] == name)
        assert not re.search(r"偏吉|偏凶|期待与安抚|警惕与压力|警示、焦虑", rule["tone"]), \
            (name, rule["tone"])


# ── ②e r4 输出边界净化（与 k52 合规扫描咬合） ─────────────────────────

def test_neutral_map_covers_all_high_risk_words_and_replacement_is_clean():
    """r4 ①：映射覆盖全部 19 词，且**替换值本身不含任何高风险词**（防净化自我复发）。"""
    from src.engines.dream_sanitize import HIGH_RISK_WORDS, NEUTRAL_MAP, find_high_risk
    from tests.test_k52_compliance_scan import HIGH_RISK_WORDS as K52_WORDS

    assert set(HIGH_RISK_WORDS) == set(K52_WORDS), "词表必须与 k52 门禁一致"
    missing = [w for w in HIGH_RISK_WORDS if w not in NEUTRAL_MAP]
    assert missing == [], f"映射未覆盖：{missing}"
    dirty = {k: v for k, v in NEUTRAL_MAP.items() if find_high_risk(v)}
    assert dirty == {}, f"替换值自身含高风险词（会复发）：{dirty}"


@pytest.mark.parametrize("word", [
    "占卜", "算命", "卜卦", "改运", "转运", "破财", "血光", "消灾", "化解",
    "法事", "开光", "辟邪", "驱邪", "招财", "旺财", "灵验", "大师", "改命",
    "化灾",
])
def test_neutralize_each_word_takes_effect(word):
    """r4 ②：19 词逐词生效（净化后零命中，且替换值出现在结果里 → 不是删词）"""
    from src.engines.dream_sanitize import NEUTRAL_MAP, find_high_risk, neutralize
    text = f"传统上认为，梦见这件事意味着要{word}，可以找人{word}。"
    out = neutralize(text)
    assert find_high_risk(out) == [], out
    assert NEUTRAL_MAP[word] in out, (word, out)


def test_neutralize_is_idempotent_and_preserves_clean_text():
    from src.engines.dream_sanitize import neutralize
    assert neutralize("梦见蛇主财运") == "梦见蛇主财运"
    once = neutralize("破财消灾")
    assert neutralize(once) == once


def test_dream_path_deterministic_output_has_no_high_risk_words():
    """r4 ③：解梦路径的确定性输出（prompt + 用户可见字段）对 19 词零命中。

    覆盖 5 条会命中「含高风险词 gloss」的规则（光/老公/婴儿/厕所/蜘蛛），
    确保净化点真的盖住了规则层引文这条来路。
    """
    from src.engines.dream_sanitize import find_high_risk
    dreams = [
        "梦见大水冲进了家里，很害怕",
        "梦见月光",
        "梦见老公",
        "梦见婴儿",
        "梦见厕所",
        "梦见蜘蛛",
        "梦见和对象开车出去玩，回来太困了把车留在半道了",
    ]
    for dream in dreams:
        res = DreamEngine().analyze(dream, _RecordingRetriever())
        blob = "\n".join([
            format_dream_prompt(dream, res, "最近换工作", {"day_master": "甲木"}),
            " ".join(res.interpretations or []),
            " ".join(res.symbols or []),
            " ".join(getattr(res, "rule_notes", []) or []),
            " ".join(getattr(res, "tones", []) or []),
            " ".join(res.element_notes or []),
            getattr(res, "luck_reason", "") or "",
            getattr(res, "reality_projection", "") or "",
        ])
        assert find_high_risk(blob) == [], (dream, find_high_risk(blob))


def test_rules_table_stays_verbatim_while_output_is_sanitized():
    """r4 ④：**两层分离同时成立**——规则表仍是逐字引文，输出层已净化。

    若哪天有人「图省事」直接把规则表里的词改掉，这条会红（守卫存储层）。
    """
    from src.engines.dream_sanitize import find_high_risk, neutralize
    risky = [r for r in DREAM_PATTERN_RULES if find_high_risk(r["gloss"])]
    assert risky, "预期规则表里存在含高风险词的逐字引文（否则本测试失去意义）"
    for r in risky:
        # 存储层：原文保留高风险词（逐字性没被破坏）
        assert find_high_risk(r["gloss"]), r["name"]
        # 输出层：同一段文本经净化后零命中
        assert find_high_risk(neutralize(r["gloss"])) == [], r["name"]


# ── ③ 打分匹配（可多命中 + 去重 + 排序） ──────────────────────────────

def test_match_patterns_scored_and_sorted():
    """命中按分降序；覆盖量高/匹配更长者分更高"""
    hits = DreamEngine().match_patterns("梦见开车撞车了")
    assert len(hits) >= 2, hits
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True), scores
    assert all(h["score"] > 0 for h in hits)


def test_match_patterns_multiple_hits_allowed():
    """可多命中：一条梦境同时命中多个模式（旧实现只取第一个）"""
    hits = DreamEngine().match_patterns("梦见开车出去玩，回来找不到车了")
    names = [h["rule"]["name"] for h in hits]
    assert len(names) >= 2, names


def test_match_patterns_containment_dedup():
    """区间包含的重复命中被丢弃（不重复计同一段文本）"""
    hits = DreamEngine().match_patterns("梦见开车")
    spans = [h["span"] for h in hits]
    for i, a in enumerate(spans):
        for b in spans[i + 1:]:
            assert not (a[0] >= b[0] and a[1] <= b[1]), spans


def test_match_patterns_empty_text():
    assert DreamEngine().match_patterns("") == []


def test_rule_luck_combined_conservatively():
    """多规则吉凶合成取最低档（宁勿吓人、勿空许）"""
    engine = DreamEngine()
    hits = [{"rule": {"luck": "大吉"}}, {"rule": {"luck": "凶多于吉"}}]
    assert engine._combine_rule_luck(hits) == "凶多于吉"
    assert engine._combine_rule_luck([]) == ""


# ── ④ 输出骨架：prompt 注入 + 三项 + 分层呈现 ────────────────────────

def test_prompt_injects_rule_skeleton():
    """prompt 注入规则骨架段，并写明「引擎决定说什么，LLM 决定怎么说」"""
    dream = "梦见和对象开车出去玩，回来太困了把车留在半道了"
    res = DreamEngine().analyze(dream, _RecordingRetriever())
    prompt = format_dream_prompt(dream, res)
    assert "### 梦境模式骨架（引擎规则层，全部由真实语料统计生成）" in prompt
    assert "引擎决定说什么，LLM 决定怎么说" in prompt
    assert "核心象征" in prompt and "情绪基调" in prompt
    for name in res.rule_hits:
        assert name in prompt


def test_prompt_injects_reality_projection_layer():
    """现实投影层：HVDC 常模 + 分层呈现要求 + 落点「现实的投影」"""
    res = DreamEngine().analyze("梦见被蛇追", _RecordingRetriever())
    prompt = format_dream_prompt("梦见被蛇追", res)
    assert "### 现实投影（Hall & Van de Castle 常模视角）" in prompt
    assert "现实的投影" in prompt
    assert "分层呈现" in prompt
    # 常模数字必须来自真实来源（dreams.ucsc.edu/Norms，991 梦样本）
    assert "80%" in prompt          # Negative Emotions Percent
    assert "52%" in prompt          # Familiarity Percent
    assert REALITY_PROJECTION["typical_dreams"][:10] in prompt


def test_hvdc_norms_values_from_source():
    """HVDC 常模数值与来源页一致（证据页已存档 reports/evidence/ucsc_main.html）"""
    assert HVDC_NORMS["negative_emotions_percent"] == "80%"
    assert HVDC_NORMS["familiarity_percent"] == "52%"
    assert HVDC_NORMS["friends_percent"] == "35%"
    assert HVDC_NORMS["dreams_with_aggression"] == "45%"
    assert HVDC_NORMS["dreams_with_misfortune"] == "35%"
    assert HVDC_NORMS["indoor_setting_percent"] == "55%"
    assert HVDC_NORMS["familiar_setting_percent"] == "69%"
    assert "ucsc.edu" in HVDC_NORMS["source"]


def test_reality_projection_is_chinese_only():
    """口径层不夹英文残句（面向用户的中文文案）"""
    res = DreamEngine().analyze("梦见掉牙", _RecordingRetriever())
    text = res.reality_projection
    assert text
    assert not re.search(r"[A-Za-z]{4,}", text.replace("Hall", "").replace("Van", "")), text


def test_rule_notes_carry_coverage_and_gloss():
    """骨架条目要能溯源：带覆盖量与传统释义依据"""
    res = DreamEngine().analyze("梦见开车", _RecordingRetriever())
    assert res.rule_notes
    joined = "\n".join(res.rule_notes)
    assert "语料覆盖" in joined
    assert "释义依据" in joined
    assert "匹配分" in joined


# ── ⑤ 向后兼容（既有契约不许动） ─────────────────────────────────────

def test_existing_luck_level_contract_untouched():
    """规则层不得改写 luck_level 契约：无元素命中仍为空（既有断言不变）"""
    res = DreamEngine().analyze("梦见手机丢了", _RecordingRetriever())
    assert res.elements == []
    assert res.luck_level == ""
    # 但规则层字段可以给出倾向（独立字段，不污染 luck_level）
    assert isinstance(res.rule_luck, str)


def test_element_path_unchanged_for_legacy_cases():
    """既有元素路径行为不变（8 案例的 element 集合不受规则层影响）"""
    cases = {"梦见被蛇追": "蛇", "梦见掉牙": "掉牙", "梦见考试": "考试",
             "梦见发大水了": "水"}
    for dream, el in cases.items():
        res = DreamEngine().analyze(dream, _RecordingRetriever())
        assert res.elements == [el], (dream, res.elements)


def test_new_fields_default_safe():
    """DreamResult 新字段缺省安全（老构造路径 getattr 不炸）"""
    res = DreamResult()
    assert res.tones == []
    assert res.rule_hits == []
    assert res.rule_notes == []
    assert res.rule_luck == ""
    assert res.reality_projection == ""


def test_prompt_works_with_old_style_result():
    """老式 DreamResult（无 k55 字段的场景）：prompt 仍可构建"""
    res = DreamResult(original_text="梦见蛇", dream_type="动物类",
                      keywords=["蛇"], interpretations=["梦见蛇，主得财"])
    prompt = format_dream_prompt("梦见蛇", res)
    assert "### 梦境类型" in prompt
    assert "### 梦境模式骨架" not in prompt   # 无命中 → 不展开


def test_empty_text_still_safe():
    res = DreamEngine().analyze("", _RecordingRetriever())
    assert res.rule_hits == []
    assert res.dream_type == ""


def test_rule_module_generated_header_documents_pipeline():
    """生成模块必须写明来源与不可手改（可复现性）"""
    p = Path(__file__).resolve().parents[1] / "src" / "engines" / "dream_rules.py"
    head = p.read_text(encoding="utf-8")[:800]
    assert "自动生成" in head
    assert "scripts/k55_dream/build_rules.py" in head
    assert "k49" in head


# ── ⑥ 爬虫基础库（离线桩，不触网） ───────────────────────────────────

def test_decode_html_gbk_repair():
    """编码修复：老站 GBK 页面按 gb18030 正确解码（HTTP 头谎报 ISO-8859）"""
    from scripts.k55_dream.crawl_lib import decode_html
    raw = "梦见蛇".encode("gbk")
    assert decode_html(raw, "text/html; charset=ISO-8859-1") == "梦见蛇"
    assert decode_html("梦见蛇".encode("utf-8"), "text/html; charset=utf-8") == "梦见蛇"
    # meta 声明 charset
    html = '<meta http-equiv="Content-Type" content="text/html; charset=gb2312">梦见蛇'
    assert "梦见蛇" in decode_html(html.encode("gbk"), "text/html")


def test_clean_content_drops_nav_noise_and_short():
    from scripts.k55_dream.crawl_lib import clean_content
    noisy = "\n".join([
        "当前位置: 首页 > 解梦",
        "梦见蛇：蛇是你所害怕之物，在外郊游则像征着人生的旅途，被窝则是你的栖身处。",
        "上一篇：梦见猫",
        "仅供娱乐，请勿盲目迷信",
    ])
    out = clean_content(noisy)
    assert "梦见蛇" in out
    assert "当前位置" not in out and "上一篇" not in out and "仅供娱乐" not in out
    assert clean_content("太短") == ""          # 最短长度过滤
    assert clean_content("") == ""


def test_checkpoint_roundtrip_and_resume(tmp_path):
    """断点续爬：cursor/done/frontier 落盘后可原样恢复"""
    from scripts.k55_dream.crawl_lib import Checkpoint
    p = tmp_path / "ck.json"
    c = Checkpoint(p, log=lambda *a: None)
    c.cursor = 42
    c.mark("p1")
    c.mark("p2")
    c.frontier = ["990361", "990360"]
    c.save(requests=7)
    c2 = Checkpoint(p, log=lambda *a: None)
    assert c2.cursor == 42
    assert c2.is_done("p2") and not c2.is_done("p9")
    assert c2.frontier == ["990361", "990360"]
    assert c2.stats["requests"] == 7


def test_checkpoint_survives_corrupt_file(tmp_path):
    """断点文件损坏 → 从零开始，不抛异常"""
    from scripts.k55_dream.crawl_lib import Checkpoint
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = Checkpoint(p, log=lambda *a: None)
    assert c.cursor == 0 and c.done == set()


def test_jsonl_writer_unified_shape(tmp_path):
    """统一输出形状：{title, content, source, url, fetched_at}（与既有语料对齐）"""
    from scripts.k55_dream.crawl_lib import JsonlWriter, read_jsonl
    p = tmp_path / "out.jsonl"
    w = JsonlWriter(p, source="sosuo_meng", log=lambda *a: None, flush_every=1)
    w.write(title="梦见蛇", content="蛇主财", url="https://x/1")
    w.flush()
    recs = list(read_jsonl(p))
    assert len(recs) == 1
    for k in ("title", "content", "source", "url", "fetched_at"):
        assert k in recs[0]
    assert recs[0]["source"] == "sosuo_meng"


def test_site_lock_blocks_concurrent_same_site(tmp_path):
    """同站互斥：已有存活 PID 持锁时拒绝启动（防同站并发超速）"""
    from scripts.k55_dream.crawl_lib import SiteLock
    import os as _os
    p = tmp_path / "site.lock"
    p.write_text(str(_os.getpid()), encoding="utf-8")   # 自己持锁（存活）
    assert SiteLock(p, log=lambda *a: None).acquire() is False
    p.write_text("999999999", encoding="utf-8")          # 不存在的 PID
    lock = SiteLock(p, log=lambda *a: None)
    assert lock.acquire() is True
    lock.release()
    assert not p.exists()


def test_public_domain_records_are_verbatim_and_traceable():
    """公版古籍条文必须**逐字来自源文件**且**带来源字段**（控制方 2026-09-18 质询）。

    数据在 /mnt/d 时运行，否则 skip（不阻断他人环境）。
    """
    import json
    from pathlib import Path as _P
    quotes = _P("/mnt/d/fortune-data/books/k55_dream/clean/public_domain_quotes.jsonl")
    if not quotes.exists():
        pytest.skip("k55 语料未生成（需先跑 public_domain.py）")
    recs = [json.loads(l) for l in quotes.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert recs, "公版条文为空"
    src_cache = {}
    bad_trace, bad_verbatim = [], []
    for r in recs:
        sf = r.get("source_file") or ""
        if not sf or not r.get("provenance"):
            bad_trace.append(r.get("title", "")[:30])
            continue
        if sf not in src_cache:
            p = _P(sf)
            src_cache[sf] = (re.sub(r"\s+", "", p.read_text(encoding="utf-8", errors="replace"))
                             if p.exists() else "")
        quote = re.sub(r"\s+", "", r["content"].split("。（《")[0])
        if quote and quote not in src_cache[sf]:
            bad_verbatim.append(r["content"][:40])
    assert bad_trace == [], f"缺来源字段：{bad_trace[:5]}"
    assert bad_verbatim == [], f"无法在源文件逐字找到（疑似自撰）：{bad_verbatim[:5]}"


def test_third_party_records_carry_source_url():
    """第三方抓取记录必须带页级 URL（可溯源）；公版记录必须带 source_file。"""
    import json
    from pathlib import Path as _P
    corpus = _P("/mnt/d/fortune-data/books/k55_dream/clean/dream_corpus.jsonl")
    if not corpus.exists():
        pytest.skip("k55 语料未生成")
    recs = [json.loads(l) for l in corpus.read_text(encoding="utf-8").splitlines() if l.strip()]
    tp = [r for r in recs if r.get("corpus_class") == "third_party"]
    pd = [r for r in recs if r.get("corpus_class") == "public_domain"]
    assert tp and pd
    assert all(r.get("url") for r in tp), "第三方记录缺页级 URL"
    assert all(r.get("source_file") for r in pd), "公版记录缺 source_file"


def test_polite_fetcher_respects_delay_and_robots(monkeypatch):
    """限速 ≥1s/请求 + robots 拒绝路径（不发请求）"""
    from scripts.k55_dream import crawl_lib as cl

    class _Gate:
        def __init__(self, *a, **k):
            self.notes = []

        def allowed(self, url):
            return "blocked" not in url

        def crawl_delay(self):
            return None

    monkeypatch.setattr(cl, "RobotsGate", _Gate)
    f = cl.PoliteFetcher("https://example.com", delay=1.0, log=lambda *a: None)
    try:
        # robots 拒绝：直接 None，且不产生请求
        assert f.get("https://example.com/blocked/1") is None
        assert f.stats.requests == 0
        assert f.stats.robots_skipped == 1
        # 越界 URL（非本站前缀）也不请求
        assert f.get("https://other.com/x", required_prefix="https://example.com/") is None
        assert f.stats.requests == 0
        # 限速门禁：连续两次 _wait 之间至少 delay 秒
        t0 = time.monotonic()
        f._wait()
        f._wait()
        assert time.monotonic() - t0 >= 1.0
    finally:
        f.close()
