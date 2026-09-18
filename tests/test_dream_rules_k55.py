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
        assert r["luck"] in ("大吉", "吉", "吉多于凶", "凶多于吉", "凶"), r


def test_rules_contain_no_trigger_or_meta_words():
    """规则条目不得是触发词/元词（「梦见」当正则会命中所有梦境文本）"""
    banned = {"梦见", "梦到", "做梦", "解梦", "周公", "梦境", "梦", "夢"}
    names = {r["name"] for r in DREAM_PATTERN_RULES}
    assert not (names & banned), names & banned


# ── ② 交通驾驶族（控制方真实梦例所在族）必须覆盖 ─────────────────────

DRIVING_FAMILY = ["开车", "车祸", "停车", "找不到车", "迷路", "赶不上车"]


@pytest.mark.parametrize("name", DRIVING_FAMILY)
def test_driving_family_covered(name):
    """交通/驾驶族每一类都必须有规则条目"""
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
