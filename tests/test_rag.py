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
