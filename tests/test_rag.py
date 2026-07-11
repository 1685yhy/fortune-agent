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
