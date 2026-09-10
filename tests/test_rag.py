"""Tests for RAG pipeline."""
from src.rag.chunker import chunk_text, Chunk
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

SAMPLE_TEXT = """
《滴天髓》·十天干论

乙木虽柔，刳羊解牛。怀丁抱丙，跨凤乘猴。
虚湿之地，骑马亦忧。藤萝系甲，可春可秋。

乙木者，甲木之胞弟也。甲为阳木，乙为阴木。甲为栋梁之木，乙为花草之木。
故乙木柔而韧，能曲能伸。生于春则欣欣向荣，生于夏则郁郁葱葱，生于秋则凋零残败，生于冬则蛰伏待时。
"""

def test_chunk_text_basic():
    chunks = chunk_text(SAMPLE_TEXT, source="《滴天髓》", category="bazi", chunk_size=200)
    assert len(chunks) > 0
    for c in chunks:
        assert c.source == "《滴天髓》"
        assert c.category == "bazi"
        assert len(c.text) > 0
        assert c.chunk_id is not None

def test_chunk_text_metadata():
    chunks = chunk_text(
        SAMPLE_TEXT,
        source="《滴天髓》·十天干论·乙木",
        author="京图(宋)",
        category="bazi",
    )
    assert chunks[0].author == "京图(宋)"
    assert chunks[0].category == "bazi"

def test_embedder_initialization():
    """注意：首次运行需要下载 BGE 模型(~1.3GB)，会比较慢"""
    import os
    if os.getenv("SKIP_EMBEDDING_TEST"):
        return
    embedder = Embedder()
    assert embedder is not None

def test_retriever_with_mock():
    """使用 mock 数据测试检索接口"""
    # 不依赖真实向量库，测试接口逻辑
    from src.rag.retriever import ChunkResult
    result = ChunkResult(
        text="乙木虽柔，刳羊解牛",
        source="《滴天髓》·十天干论",
        score=0.95,
        chunk_id="test_001",
    )
    assert result.score > 0.9
    assert "乙木" in result.text


def test_embedder_v2_deterministic():
    """EmbedderV2: same model_name produces same dimension"""
    from src.rag.embedder_v2 import EmbedderV2
    e = EmbedderV2(model_name="BAAI/bge-m3")
    assert e.dimension == 1024


def test_embedder_v2_rejects_empty_model_name():
    """EmbedderV2 raises ValueError for empty model_name"""
    import pytest
    from src.rag.embedder_v2 import EmbedderV2
    with pytest.raises(ValueError, match="model_name"):
        EmbedderV2(model_name="")


def test_embedder_v2_rejects_encode_before_load():
    """EmbedderV2 raises RuntimeError on encode before load"""
    import pytest
    from src.rag.embedder_v2 import EmbedderV2
    e = EmbedderV2(model_name="BAAI/bge-m3")
    with pytest.raises(RuntimeError, match="not loaded"):
        e.encode_single("test")


def test_embedder_v2_unknown_model_raises():
    """EmbedderV2 raises ValueError for unknown model (no hardcoded fallback)"""
    import pytest
    from src.rag.embedder_v2 import EmbedderV2
    e = EmbedderV2(model_name="completely-unknown-model-xyz-12345")
    with pytest.raises(ValueError, match="Unknown model"):
        _ = e.dimension


def test_embedder_v2_load_failure_returns_false():
    """EmbedderV2.load() returns False for non-existent model"""
    from src.rag.embedder_v2 import EmbedderV2
    e = EmbedderV2(model_name="BAAI/bge-m3")
    # Don't actually call load (would try network), just verify the interface
    assert hasattr(e, 'load')
    assert callable(e.load)


def test_known_model_dimensions():
    """All known models have correct dimension mappings"""
    from src.rag.embedder_v2 import EmbedderV2, _MODEL_DIMENSIONS
    for model_name, expected_dim in _MODEL_DIMENSIONS.items():
        e = EmbedderV2(model_name=model_name)
        assert e.dimension == expected_dim, f"{model_name}: expected {expected_dim}, got {e.dimension}"


def test_collection_manager_validation():
    """CollectionManager reports valid=False for non-existent collection"""
    from src.rag.collection_manager import CollectionManager
    cm = CollectionManager("/tmp", "nonexistent_collection_test_xyz", 1024)
    report = cm.validate()
    assert report.exists is False
    assert report.valid is False
    assert len(report.errors) > 0


def test_collection_manager_validate_empty_collection(tmp_path):
    """存在但为空的集合：exists=True, doc_count=0（空集合≠不存在，仅兜底缺失）"""
    from src.rag.collection_manager import CollectionManager
    cm = CollectionManager(str(tmp_path), "m3_empty_coll", 1024)
    cm.ensure_exists()  # 在临时目录创建空集合
    report = cm.validate()
    assert report.exists is True
    assert report.doc_count == 0


def test_main_collection_validation_empty_collection_logs_warning(caplog):
    """启动校验：legacy 集合存在但为空 → 日志为 warning，绝不 ERROR"""
    import logging
    from src.rag.collection_manager import ValidationReport
    from src.main import _log_collection_validation

    report = ValidationReport(
        collection_name="fortune_books", exists=True, doc_count=0,
        dimension=1024, valid=False, errors=["Collection is empty"],
    )
    logger = logging.getLogger("m3.test.empty")
    with caplog.at_level(logging.WARNING, logger="m3.test.empty"):
        _log_collection_validation(logger, report, "fortune_books")
    assert not any(r.levelno >= logging.ERROR for r in caplog.records), \
        "空集合不应产生 ERROR 级日志（FAISS 主路径正常，仅 legacy 兜底缺失）"
    assert any(
        r.levelno == logging.WARNING and "empty" in r.getMessage()
        for r in caplog.records
    ), "空集合应产生 warning 级提示"


def test_main_collection_validation_real_failure_still_error(caplog):
    """启动校验：真实失败（维度不匹配等）仍保持 ERROR，不误降级"""
    import logging
    from src.rag.collection_manager import ValidationReport
    from src.main import _log_collection_validation

    report = ValidationReport(
        collection_name="fortune_books", exists=True, doc_count=27115,
        dimension=1024, valid=False,
        errors=["Dimension mismatch: expected 1024, got 384"],
    )
    logger = logging.getLogger("m3.test.fail")
    with caplog.at_level(logging.ERROR, logger="m3.test.fail"):
        _log_collection_validation(logger, report, "fortune_books")
    assert any(
        r.levelno == logging.ERROR and "FAILED" in r.getMessage()
        for r in caplog.records
    ), "真实校验失败必须仍是 ERROR"


class _M3StubEmbedder:
    """测试用 stub embedder：不加载模型，encode 返回全零向量（1024 维）"""
    dimension = 1024

    def load(self):
        return True

    def encode(self, texts):
        import numpy as np
        if isinstance(texts, str):
            texts = [texts]
        return np.zeros((len(texts), self.dimension), dtype=np.float32)


def test_retriever_search_empty_collection_returns_empty(tmp_path):
    """legacy 集合为空：search() 安全返回空列表（兜底缺失不抛异常）"""
    from src.rag.retriever import Retriever

    r = Retriever(str(tmp_path), _M3StubEmbedder())
    r._collection_name = "m3_empty_search"
    results = r.search("乙木虽柔")
    assert isinstance(results, list)
    assert results == []


def test_retriever_search_ef_conflict_collection_safe(tmp_path, caplog):
    """旧脚本创建（无 embedding_function 配置）的集合：search() 不抛异常，
    返回空列表并记 warning（legacy 兜底缺失的最小安全降级）"""
    import logging
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from src.rag.retriever import Retriever

    client = chromadb.PersistentClient(
        path=str(tmp_path), settings=ChromaSettings(anonymized_telemetry=False),
    )
    client.create_collection(name="m3_old_style", metadata={"hnsw:space": "cosine"})
    r = Retriever(str(tmp_path), _M3StubEmbedder())
    r._collection_name = "m3_old_style"
    with caplog.at_level(logging.WARNING, logger="src.rag.retriever"):
        results = r.search("乙木虽柔")
    assert results == []
    assert any("degraded" in rec.getMessage() for rec in caplog.records), \
        "集合不可用时应记 warning 而非静默/抛异常"


# --- k24：古籍检索断链修复 ------------------------------------------------


def test_resolve_categories_maps_handler_words_to_real_categories():
    """k24 映射表：handler 过滤词 → 库内元数据 category 实际值。

    防回归：`bazi` 在库内实测 0 条（实为 bazi_case），这类错配曾让 8 项能力
    里的 5 项（八字/风水/面相/择吉/姓名外的错配项）即使集合正确也 refs=0。
    """
    from src.book_categories import resolve_categories

    assert resolve_categories("bazi") == ("bazi_case",)
    assert "fengshui_guide" in resolve_categories("fengshui")
    assert "mianxiang_features" in resolve_categories("mianxiang")
    assert "八门" in resolve_categories("qimen")
    # 库内无对应类目 → 空元组（表示「无类目可过滤」，调用方必须走全库）
    assert resolve_categories("zeri") == ()
    assert resolve_categories("hehun") == ()
    # 未知词按原样透传（兼容调用方直接传真实类目名）
    assert resolve_categories("bazi_case") == ("bazi_case",)
    assert resolve_categories(None) == ()
    assert resolve_categories("") == ()


def test_where_filter_supports_multi_category():
    """多类目 → chroma $in；单类目 → 精确匹配；空 → 不过滤。"""
    from src.rag.retriever import Retriever

    r = Retriever("/tmp", _M3StubEmbedder())
    assert r._where_filter(None) is None
    assert r._where_filter(()) is None
    assert r._where_filter("bazi") == {"category": "bazi_case"}
    assert r._where_filter(["a", "b"]) == {"category": {"$in": ["a", "b"]}}


def test_search_category_miss_falls_back_to_full_library():
    """k24 核心：类目过滤 0 命中时必须退回全库检索，而不是返回空。

    旧行为是「类目对不上 = 静默 refs=0」→ LLM 无古籍依据只能凭记忆编造引文。
    """
    from src.rag.retriever import ChunkResult, Retriever

    r = Retriever("/tmp", _M3StubEmbedder())
    calls = []

    def fake_vec(query, cats, top_k, min_score):
        calls.append(cats)
        if cats:
            return []  # 类目下无命中
        return [ChunkResult(text="全库命中", source="古籍", score=0.9, chunk_id="c1")]

    r._vector_search = fake_vec
    r._keyword_search = lambda q, c, k: []
    out = r.search("乙木生于申月", category="bazi")
    assert calls == [("bazi_case",), None], f"必须先类目后全库，实际 {calls}"
    assert len(out) == 1 and out[0].text == "全库命中"


def test_search_no_category_alias_goes_straight_to_full_library():
    """库内无对应类目（zeri/hehun）→ 不做无谓的类目检索，直接全库。"""
    from src.rag.retriever import ChunkResult, Retriever

    r = Retriever("/tmp", _M3StubEmbedder())
    calls = []
    r._vector_search = lambda q, c, k, m: (calls.append(c), [
        ChunkResult(text="全库命中", source="古籍", score=0.9, chunk_id="c1")])[1]
    r._keyword_search = lambda q, c, k: []
    out = r.search("择吉 嫁娶 黄道吉日", category="zeri")
    assert calls == [None], f"无对应类目应直接全库，实际 {calls}"
    assert len(out) == 1


def test_search_category_hit_does_not_widen_to_full_library():
    """类目有命中时不得放宽（避免用全库结果稀释领域精度）。"""
    from src.rag.retriever import ChunkResult, Retriever

    r = Retriever("/tmp", _M3StubEmbedder())
    calls = []
    r._vector_search = lambda q, c, k, m: (calls.append(c), [
        ChunkResult(text="类目命中", source="古籍", score=0.9, chunk_id="c1")])[1]
    r._keyword_search = lambda q, c, k: []
    out = r.search("命宫", category="ziwei")
    assert calls == [("ziwei",)]
    assert out[0].text == "类目命中"


def test_retriever_default_collection_is_not_known_empty(monkeypatch):
    """k24 护栏：Retriever 的默认集合名必须指向权威古籍库。"""
    from src.book_categories import BOOKS_COLLECTION, KNOWN_EMPTY_COLLECTIONS
    from src.rag.retriever import Retriever

    monkeypatch.delenv("EMBEDDING_COLLECTION", raising=False)
    r = Retriever("/tmp", _M3StubEmbedder())
    assert r._collection_name == BOOKS_COLLECTION
    assert r._collection_name not in KNOWN_EMPTY_COLLECTIONS
    # 显式传入优先于环境变量与默认值
    r2 = Retriever("/tmp", _M3StubEmbedder(), collection_name="explicit_coll")
    assert r2._collection_name == "explicit_coll"


def test_known_empty_collection_self_heals_with_warning(tmp_path, caplog):
    """指向已知空集合 → 自愈到权威古籍库并记 warning（绝不静默返回空）。"""
    import logging
    from src.rag.retriever import Retriever

    r = Retriever(str(tmp_path), _M3StubEmbedder(), collection_name="fortune_books")
    with caplog.at_level(logging.WARNING, logger="src.rag.retriever"):
        r._ensure_non_empty_collection()
    # 权威库在本 tmp 目录不存在 → 无法自愈，但必须留下明确告警
    assert any("fortune_books" in rec.getMessage() for rec in caplog.records), \
        "空集合必须留下 warning，不能静默"


# --- QueryEnhancer tests ---

def test_enhanced_query_dataclass():
    """EnhancedQuery dataclass stores and exposes all fields correctly"""
    from src.rag.query_enhancer import EnhancedQuery

    eq = EnhancedQuery(
        original="我最近运气不好，想看看八字",
        rewritten="近期运势低迷，询问八字命局中流年气运变化及其对事业、财运的影响",
        sub_queries=["流年运势分析", "八字用神与忌神"],
        category="bazi",
        keywords=["运势", "八字", "流年"],
    )
    assert eq.original == "我最近运气不好，想看看八字"
    assert eq.rewritten.startswith("近期运势低迷")
    assert len(eq.sub_queries) == 2
    assert eq.category == "bazi"
    assert "运势" in eq.keywords
    assert eq.to_dict()["original"] == eq.original


def test_enhanced_query_empty_sub_queries():
    """EnhancedQuery handles empty sub_queries and keywords gracefully"""
    from src.rag.query_enhancer import EnhancedQuery

    eq = EnhancedQuery(
        original="test",
        rewritten="test",
        sub_queries=[],
        category="general",
        keywords=[],
    )
    assert eq.sub_queries == []
    assert eq.keywords == []
    assert eq.category == "general"


def test_query_enhancer_parse_response_valid():
    """QueryEnhancer._parse_response handles valid JSON correctly"""
    from src.rag.query_enhancer import QueryEnhancer

    enhancer = QueryEnhancer(api_key="test-key")
    json_content = '''{
        "rewritten": "询问八字命局中五行平衡与用神喜忌",
        "sub_queries": ["八字五行强弱分析", "用神取用建议"],
        "category": "bazi",
        "keywords": ["八字", "五行", "用神"]
    }'''
    result = enhancer._parse_response(json_content, "我想看八字五行")
    assert result.original == "我想看八字五行"
    assert "五行平衡" in result.rewritten
    assert len(result.sub_queries) == 2
    assert result.category == "bazi"
    assert "用神" in result.keywords


def test_query_enhancer_parse_response_invalid_json():
    """QueryEnhancer._parse_response falls back gracefully on malformed JSON"""
    from src.rag.query_enhancer import QueryEnhancer

    enhancer = QueryEnhancer(api_key="test-key")
    result = enhancer._parse_response("not valid json", "我运气不好")
    assert result.original == "我运气不好"
    assert "专业命理分析" in result.rewritten
    assert result.sub_queries == []
    assert result.category == "general"
    assert result.keywords == []


def test_query_enhancer_parse_response_unknown_category():
    """QueryEnhancer._parse_response defaults unknown categories to 'general'"""
    from src.rag.query_enhancer import QueryEnhancer

    enhancer = QueryEnhancer(api_key="test-key")
    json_content = '''{
        "rewritten": "测试",
        "sub_queries": [],
        "category": "astrology",
        "keywords": []
    }'''
    result = enhancer._parse_response(json_content, "test")
    assert result.category == "general"


def test_query_enhancer_parse_response_missing_fields():
    """QueryEnhancer._parse_response handles missing fields gracefully"""
    from src.rag.query_enhancer import QueryEnhancer

    enhancer = QueryEnhancer(api_key="test-key")
    json_content = '{"category": "dream"}'
    result = enhancer._parse_response(json_content, "梦见水")
    assert result.original == "梦见水"
    assert "专业命理分析" in result.rewritten  # falls back
    assert result.sub_queries == []
    assert result.category == "dream"
    assert result.keywords == []


def test_query_enhancer_build_fallback():
    """QueryEnhancer._build_fallback produces safe fallback"""
    from src.rag.query_enhancer import QueryEnhancer

    enhancer = QueryEnhancer(api_key="test-key")
    result = enhancer._build_fallback("我最近总做噩梦")
    assert result.original == "我最近总做噩梦"
    assert "专业命理分析" in result.rewritten
    assert result.sub_queries == []
    assert result.category == "general"
    assert result.keywords == []


def test_query_enhancer_enhance_empty_query():
    """QueryEnhancer.enhance handles empty query without API call"""
    import asyncio
    from src.rag.query_enhancer import QueryEnhancer

    enhancer = QueryEnhancer(api_key="test-key")

    async def _test():
        result = await enhancer.enhance("")
        assert result.original == ""
        assert result.category == "general"
        assert result.sub_queries == []

    asyncio.run(_test())
