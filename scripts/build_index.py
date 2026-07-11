"""构建向量索引 - 从文本文件构建 ChromaDB 索引."""
import sys
sys.path.insert(0, '.')
from pathlib import Path
from src.config import load_settings
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

# 核心古籍示例文本（后续会替换为完整 OCR 文本）
CORE_TEXTS = [
    {
        "text": """
《滴天髓》·十天干论

乙木虽柔，刳羊解牛。怀丁抱丙，跨凤乘猴。虚湿之地，骑马亦忧。藤萝系甲，可春可秋。

乙木者，花草之木也。柔而韧，能曲能伸。生于春则欣欣向荣，生于夏则郁郁葱葱。见丙丁火，则才华外露。遇庚辛金，则修剪成器。得壬癸水，则滋润生长。逢戊己土，则根基牢固。
""",
        "source": "《滴天髓》·十天干论",
        "author": "京图(宋) / 刘伯温注(明)",
        "category": "bazi",
    },
    {
        "text": """
《穷通宝鉴》·乙木

正月乙木，必须用丙。因为天气尚有寒冻，非丙不暖。虽有癸水，恐凝寒气，故以丙火为先，癸水次之。

二月乙木，阳气渐升，木不寒矣。以丙为君，癸为臣。丙癸两透，富贵双全。

三月乙木，阳气炽热，先癸后丙。木将枯竭，非水不润。先用癸水，次取丙火。

四月乙木，自有丙火。若见癸水，功名可成。取水为要，但不宜太多，多则木浮。
""",
        "source": "《穷通宝鉴》·乙木",
        "author": "余春台(清)",
        "category": "bazi",
    },
    {
        "text": """
《三命通会》·论五行

五行者，金木水火土也。木主仁，火主礼，土主信，金主义，水主智。

木生火，火生土，土生金，金生水，水生木。此五行相生之序也。

木克土，土克水，水克火，火克金，金克木。此五行相克之序也。

旺则宜泄宜克，衰则宜生宜扶。此五行调理之大法也。
""",
        "source": "《三命通会》·论五行",
        "author": "万民英(明)",
        "category": "bazi",
    },
]


def main():
    settings = load_settings()
    settings.vectordb_dir.mkdir(parents=True, exist_ok=True)

    print("Loading embedder (first run will download BGE model ~1.3GB)...")
    embedder = Embedder(model_name=settings.embedding_model)

    print("Building vector index...")
    retriever = Retriever(str(settings.vectordb_dir), embedder)

    total_chunks = 0
    for book in CORE_TEXTS:
        chunks = chunk_text(
            book["text"],
            source=book["source"],
            author=book["author"],
            category=book["category"],
        )
        retriever.add_chunks(chunks)
        total_chunks += len(chunks)
        print(f"  Added {len(chunks)} chunks from {book['source']}")

    print(f"Done! Total chunks: {total_chunks}, Collection size: {retriever.count()}")

    # 测试检索
    print("\nTest search: '乙木日主财运'")
    results = retriever.search("乙木日主财运", category="bazi", top_k=5)
    for r in results:
        print(f"  [{r.score:.3f}] {r.source}: {r.text[:80]}...")


if __name__ == "__main__":
    main()
