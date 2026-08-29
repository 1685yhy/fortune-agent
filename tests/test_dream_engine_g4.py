"""G4 批次：解梦引擎修复测试（关键词提取缺陷 + 吉凶规则层 + 组合梦境拆分）

对标锚点：2026-08-29 佛滔对比实测（/tmp/dream_compare.md §三 8 案例判词）。
测试接法：RecordingRetriever 记录查询串（不依赖真实向量库），
真实检索冒烟为可选（本地向量库可用时运行，否则 skip）。
"""
import builtins
import os
import re
from unittest.mock import Mock

import pytest

from src.engines.dream import (
    DreamEngine,
    DreamResult,
    format_dream_prompt,
    DREAM_ELEMENTS,
)


class _Hit:
    def __init__(self, text, source="周公解梦", score=0.8, chunk_id="1"):
        self.text = text
        self.source = source
        self.score = score
        self.chunk_id = chunk_id


class _RecordingRetriever:
    """记录查询串的假检索器：只验证检索链路，返回 canned 结果。"""

    def __init__(self):
        self.queries = []
        self.results = []

    def search(self, query, top_k=5, **kw):
        self.queries.append(query)
        return list(self.results)


# ── ① 单字词白名单 + 触发词排除 + 情绪词降权 ──────────────────────────

def test_single_char_whitelist_water_kept():
    """梦见发大水 → kw 含「水」（发大水为单字意象白名单覆盖），不含「梦见」"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("梦见发大水了", r)
    assert any("水" in kw for kw in res.keywords), res.keywords
    assert not any("梦见" in kw for kw in res.keywords), res.keywords
    # 变体检索词优先：策略A 首查「梦见 发大水」
    assert "梦见 发大水" in r.queries, r.queries


def test_snake_chase_keywords_and_emotion_separated():
    """被蛇追 → kw 含「追」或「蛇」；「害怕」进 emotions 不进 kw"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("我昨晚梦见被蛇追，很害怕，是不是要倒霉啊", r)
    assert any(kw in ("蛇", "追") for kw in res.keywords), res.keywords
    assert "害怕" not in res.keywords, res.keywords
    assert "害怕" in res.emotions, res.emotions
    assert res.keywords[0] not in ("梦见", "梦到", "做梦"), res.keywords
    # 策略A 不再出现「梦见 梦见」垃圾查询
    assert not any("梦见 梦见" in q for q in r.queries), r.queries
    # 情绪词不参与意象检索（无「梦见 害怕」查询）
    assert not any("害怕" in q for q in r.queries), r.queries


@pytest.mark.parametrize("dream", [
    "梦见被蛇追",
    "梦见发大水了",
    "梦见亲人去世",
    "梦见奶奶去世，哭醒了",
    "梦见被坏人追",
    "梦见自己怀孕了",
    "梦见自己在天上飞",
    "梦见考试",
    "梦见掉牙",
    "梦见掉牙又接上了",
    "梦见被蛇追到水里",
    "梦见老伴去世了，心里不踏实",
    "梦见考试没带准考证",
])
def test_no_trigger_words_in_keywords_and_no_meta_queries(dream):
    """触发词排除：所有案例 keywords[0] ≠ 梦见/梦到；无「梦见 梦见」查询"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze(dream, r)
    assert res.keywords, f"关键词不应为空: {dream}"
    assert res.keywords[0] not in ("梦见", "梦到", "做梦"), (dream, res.keywords)
    assert not any("梦见 梦见" in q for q in r.queries), (dream, r.queries)
    # 触发词/情绪词/疑问词不进关键词
    for kw in res.keywords:
        assert kw not in ("梦见", "梦到", "做梦", "是不是", "好吗"), (dream, kw)


def test_death_verb_extracted():
    """亲人去世 → 「去世」进关键词（词性 t 误标不再丢弃）"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("梦见亲人去世", r)
    assert "去世" in res.keywords, res.keywords


# ── ② 元素→释义→吉凶 规则层（8 案例与佛滔判词同向） ────────────────────

@pytest.mark.parametrize("dream,exp_luck,exp_elements", [
    ("梦见被蛇追", "大吉", ["蛇"]),          # 被蛇追=吉凶指数92【大吉昌】
    ("梦见亲人去世", "大吉", ["亲人去世"]),   # 死人去世76【大吉昌】——用户最怕凶的案例
    ("梦见发大水了", "大吉", ["水"]),        # 发洪水者主进财（周公解梦）
    ("梦见掉牙", "吉多于凶", ["掉牙"]),      # 齿自落者父母凶但整体95【吉多于凶】
    ("梦见自己怀孕了", "大吉", ["怀孕"]),     # 吉凶指数99【大吉】
    ("梦见自己在天上飞", "大吉", ["飞"]),     # 天飞翔90【大吉昌】主升迁
    ("梦见考试", "吉", ["考试"]),           # 考试主吉，学习如鱼得水
    ("梦见被坏人追", "吉多于凶", ["被追"]),   # 被追=压力类；被追到水里【吉多于凶】
])
def test_luck_skeleton_same_direction_as_fotoo(dream, exp_luck, exp_elements):
    """8 案例吉凶基线逐一与佛滔判词同向（反向=bug）"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze(dream, r)
    assert res.luck_level == exp_luck, (dream, res.luck_level, res.element_notes)
    assert set(res.elements) == set(exp_elements), (dream, res.elements)
    assert res.luck_reason, dream
    # 元素库覆盖对比报告全部案例
    assert len(DREAM_ELEMENTS) >= 8


def test_being_chased_luck_reason_mentions_pressure():
    """被追=压力向：吉凶依据必须点明压力/逃避来源"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("梦见被坏人追", r)
    assert "压力" in res.luck_reason, res.luck_reason


def test_prompt_contains_luck_skeleton_section():
    """prompt 注入「### 传统解梦吉凶基线」段，且含硬约束措辞"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("梦见被蛇追", r)
    prompt = format_dream_prompt("梦见被蛇追", res)
    assert "### 传统解梦吉凶基线" in prompt
    assert "周公解梦 / 敦煌梦书 / 佛滔判词体系" in prompt
    assert "综合吉凶骨架：大吉" in prompt
    assert "不得反转吉凶方向" in prompt
    assert "被蛇追吉凶指数92【大吉昌】" in prompt


def test_prompt_contains_emotion_section():
    r = _RecordingRetriever()
    res = DreamEngine().analyze("梦见被蛇追，很害怕", r)
    prompt = format_dream_prompt("梦见被蛇追，很害怕", res)
    assert "### 情绪基调" in prompt
    assert "害怕" in prompt


def test_pregnancy_pattern_in_top_ten():
    """TOP_TEN_PATTERNS 补「怀孕/孕妇」模式：梦见怀孕 type 不再为空"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("梦见自己怀孕了", r)
    assert res.dream_type, "怀孕模式缺失 → type 为空 → 无策略A 检索"
    assert re.search(r"怀孕|孕妇|孕|大肚子", "梦见孕妇大肚子了")


# ── ③ 组合梦境拆分与变体覆盖 ────────────────────────────────────────

def test_composite_pregnant_water_split():
    """孕妇+水 → 两元素都命中，各自独立检索词出现，吉凶合成大吉"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("我怀孕了梦见水，好吗", r)
    assert "怀孕" in res.elements and "水" in res.elements, res.elements
    queries = set(r.queries)
    assert "梦见 怀孕" in queries, r.queries
    assert "梦见 水" in queries, r.queries
    assert res.luck_level == "大吉", res.element_notes


def test_composite_death_element_or_keyword():
    """亲人+去世 → 「去世」在关键词或元素匹配中（不再只留奶奶/哭醒）"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("梦见奶奶去世，哭醒了", r)
    assert ("亲人去世" in res.elements) or any("去世" in kw for kw in res.keywords), \
        (res.elements, res.keywords)
    assert "哭醒" in res.emotions, res.emotions


def test_variant_term_priority():
    """变体命中时该变体检索词优先（被蛇追/掉牙又接上/飞不起来/追到水里）"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("梦见掉牙又接上了", r)
    assert "梦见 掉牙又接上" in r.queries, r.queries
    assert res.luck_level == "吉", res.element_notes  # 变体：失而复得，转机

    r2 = _RecordingRetriever()
    DreamEngine().analyze("梦见飞不起来", r2)
    assert "梦见 飞不起来" in r2.queries, r2.queries

    r3 = _RecordingRetriever()
    DreamEngine().analyze("梦见被追到水里", r3)
    assert "梦见 被追到水里" in r3.queries, r3.queries


# ── 回归：字段形状不变 + 新字段缺省安全 ──────────────────────────────

def test_result_shape_regression():
    """keywords/interpretations 字段形状不变（既有消费点 handler/tests 不破）"""
    r = _RecordingRetriever()
    r.results = [_Hit("梦见掉牙齿，主父母有灾，须加谨慎...", "周公解梦", 0.9)]
    res = DreamEngine().analyze("梦见掉牙", r)
    assert isinstance(res.keywords, list) and res.keywords
    assert isinstance(res.interpretations, list)
    assert res.interpretations[0].startswith("梦见掉牙齿")
    assert isinstance(res.source, str)
    assert isinstance(res.dream_type, str) and res.dream_type
    # 新字段形状
    assert isinstance(res.symbols, list)
    assert isinstance(res.emotions, list)
    assert isinstance(res.elements, list)
    assert isinstance(res.element_notes, list)
    assert isinstance(res.luck_level, str)


def test_new_fields_default_safe():
    """DreamResult 新字段缺省安全：旧代码 getattr 路径不炸"""
    res = DreamResult()
    assert getattr(res, "symbols", None) == []
    assert getattr(res, "emotions", None) == []
    assert getattr(res, "elements", None) == []
    assert getattr(res, "luck_level", "") == ""
    assert getattr(res, "luck_reason", "") == ""
    assert getattr(res, "keywords", None) == []
    assert getattr(res, "interpretations", None) == []
    assert getattr(res, "dream_type", "") == ""


def test_prompt_mock_result_no_crash():
    """handler 注入 Mock 结果（既有测试路径）→ 不展开 G4 新段、不崩"""
    m = Mock()
    m.dream_type = "动物类"
    m.keywords = ["蛇"]
    m.interpretations = ["梦见蛇，主得大财"]
    prompt = format_dream_prompt("梦见被蛇咬了", m)
    assert "### 传统解梦吉凶基线" not in prompt
    assert "### 梦境类型" in prompt


# ── 失败路径：jieba 缺失 / 空文本 / 无元素命中 ────────────────────────

def test_jieba_missing_fallback_follows_rules():
    """jieba 缺失（ImportError 分支）：同样遵循白名单/排除规则"""
    engine = DreamEngine()
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "jieba" or name.startswith("jieba."):
            raise ImportError("no jieba")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    try:
        kws = engine._extract_keywords("梦见发大水了")
        assert any("水" in k for k in kws), kws
        assert not any("梦见" in k for k in kws), kws

        kws2 = engine._extract_keywords("我昨晚梦见被蛇追，很害怕")
        assert any(k in ("蛇", "追", "被蛇追") for k in kws2), kws2
        assert not any("害怕" in k or "梦见" in k for k in kws2), kws2

        emos = engine._extract_emotions("梦见奶奶去世，哭醒了")
        assert any("哭" in e for e in emos), emos

        # 兜底分支完整 analyze 不崩
        r = _RecordingRetriever()
        res = engine.analyze("梦见发大水了", r)
        assert res.keywords and res.luck_level == "大吉"
    finally:
        builtins.__import__ = real_import


def test_empty_text_no_crash():
    r = _RecordingRetriever()
    res = DreamEngine().analyze("", r)
    assert res.keywords == []
    assert res.elements == []
    assert res.luck_level == ""
    assert isinstance(res.interpretations, list)


def test_no_element_hit_strategy_a_fallback():
    """无元素命中（现代类）→ 策略A 回退 keywords[0]/dream_type，不崩"""
    r = _RecordingRetriever()
    res = DreamEngine().analyze("梦见手机丢了", r)
    assert res.keywords, res.keywords
    assert res.elements == []
    assert res.luck_level == ""
    assert any(q.startswith("梦见 ") for q in r.queries), r.queries


def test_retrieval_cap_at_8():
    """组合拆分检索次数上限：元素命中数 ≤ 8（DREAM_ELEMENTS 规模），每元素独立查询"""
    text = "梦见被蛇追到水里，还梦见怀孕和掉牙"
    engine = DreamEngine()
    hits = engine._match_elements(text)
    assert len(hits) <= 8, hits
    r = _RecordingRetriever()
    engine.analyze(text, r)
    for h in hits:
        assert f"梦见 {h['term']}" in r.queries, r.queries


# ── 真实检索冒烟（可选：本地向量库可用时运行） ─────────────────────────

def test_real_retrieval_water_no_meta_garbage():
    """真实检索冒烟：梦见发大水 top3 不再含「梦见梦见/梦中梦」垃圾

    本地向量库（/mnt/d/fortune-data/vectordb_v2 fortune_books_v2，27115 条）
    可用时运行；否则 skip（不影响测试基线）。"""
    try:
        from src.rag.retriever import Retriever
        from src.rag.embedder import Embedder
        from src.config import load_settings
    except Exception as e:
        pytest.skip(f"检索组件不可用: {e}")
    old_collection = os.environ.get("EMBEDDING_COLLECTION")
    try:
        # 空库守卫（src/engine/evidence.py 同款）：默认 fortune_books 为空库，
        # 真实古籍在 fortune_books_v2
        os.environ["EMBEDDING_COLLECTION"] = "fortune_books_v2"
        settings = load_settings()
        embedder = Embedder(model_name="BAAI/bge-m3")
        embedder.load()
        retriever = Retriever(str(settings.vectordb_dir), embedder)
        engine = DreamEngine()
        res = engine.analyze("梦见发大水", retriever)
    except Exception as e:
        pytest.skip(f"本地向量库不可用: {e}")
    finally:
        if old_collection is None:
            os.environ.pop("EMBEDDING_COLLECTION", None)
        else:
            os.environ["EMBEDDING_COLLECTION"] = old_collection
    if not res.interpretations:
        pytest.skip("本地向量库为空/集合名不匹配，跳过冒烟")
    assert res.keywords and any("水" in kw for kw in res.keywords), res.keywords
    top3 = " ".join(res.interpretations[:3])
    assert "梦见梦见" not in top3 and "梦中梦" not in top3, top3
    assert any(("水" in t or "进财" in t or "大富" in t)
               for t in res.interpretations[:5]), res.interpretations[:5]
