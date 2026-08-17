"""混合检索器 - 语义检索 + BM25关键词检索."""
import os
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
import chromadb
from chromadb.config import Settings as ChromaSettings

from .embedder import Embedder
from .chunker import Chunk


@dataclass
class ChunkResult:
    text: str
    source: str
    score: float
    chunk_id: str
    category: str = ""


class _AppEmbeddingFunction:
    """chroma EmbeddingFunction 包装 — 用 app 的 embedder (bge-m3, 1024维) 编码.

    集合创建时传入，让 chroma 在自动嵌入路径上使用与 add_chunks 相同的模型，
    保证集合维度 (1024) 一致；查询路径使用显式 query_embeddings，同样出自
    self.embedder，杜绝维度不匹配。
    """

    def __init__(self, embedder: Embedder):
        self._embedder = embedder

    def __call__(self, input):
        if isinstance(input, str):
            input = [input]
        return self._embedder.encode(list(input)).tolist()

    def name(self) -> str:
        return "app_embedder_bge_m3"

    def embed_query(self, input) -> list:
        if isinstance(input, (list, tuple)):
            return [self._embedder.encode([t])[0].tolist() for t in input]
        return self._embedder.encode([input])[0].tolist()


class Retriever:
    """混合检索器"""

    def __init__(self, persist_dir: str, embedder: Embedder):
        self.persist_dir = persist_dir
        self.embedder = embedder
        self._client = None
        self._collection = None
        # 集合名可通过环境变量覆盖（如指向重建后的 fortune_books_v2）
        self._collection_name = os.environ.get("EMBEDDING_COLLECTION", "fortune_books")

    @property
    def client(self):
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self._collection_name,
                embedding_function=_AppEmbeddingFunction(self.embedder),
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def add_chunks(self, chunks: List[Chunk], batch_size: int = 128):
        """批量添加切块到向量库 (使用 upsert 避免重复 ID 错误)

        使用小批量避免内存问题，每批次独立编码和写入。
        """
        if not chunks:
            return

        all_texts = [c.text for c in chunks]
        all_ids = [c.chunk_id for c in chunks]
        all_metadatas = [{
            "source": c.source,
            "author": c.author,
            "category": c.category,
        } for c in chunks]

        # Process in small batches to avoid hanging on large encode calls
        for start in range(0, len(chunks), batch_size):
            end = min(start + batch_size, len(chunks))
            batch_texts = all_texts[start:end]
            batch_ids = all_ids[start:end]
            batch_metas = all_metadatas[start:end]

            embeddings = self.embedder.encode(batch_texts)
            self.collection.upsert(
                embeddings=embeddings.tolist(),
                documents=batch_texts,
                ids=batch_ids,
                metadatas=batch_metas,
            )

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        top_k: int = 20,
        min_score: float = 0.3,
        **kw,
    ) -> List[ChunkResult]:
        """混合检索：先语义检索，无结果则用BM25关键词检索

        **kw：兼容 FAISS 检索接口的扩展参数（expand/rerank/original_query 等），
        本检索器无查询扩展与精排，静默忽略（调用方在两类检索器间可透明切换）。
        """
        # Try vector search first
        chunk_results = self._vector_search(query, category, top_k, min_score)

        # If vector search returned nothing, fall back to keyword search
        if not chunk_results:
            chunk_results = self._keyword_search(query, category, top_k)

        return chunk_results

    def _vector_search(self, query, category, top_k, min_score):
        """语义向量检索 — 使用 ChromaDB 原生查询，自动匹配维度"""
        try:
            where_filter = None
            if category:
                where_filter = {"category": category}

            # 显式 query_embeddings：与写入端 (add_chunks) 使用同一个 bge-m3
            # embedder，保证查询/写入维度一致 (1024)，不再依赖 chroma 内置
            # 384 维 embedder。
            query_vec = self.embedder.encode([query])[0].tolist()
            results = self.collection.query(
                query_embeddings=[query_vec],
                n_results=top_k,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )

            chunk_results = []
            if results["ids"] and results["ids"][0]:
                for i, chunk_id in enumerate(results["ids"][0]):
                    score = 1 - results["distances"][0][i]
                    if score >= min_score:
                        meta = results["metadatas"][0][i]
                        chunk_results.append(ChunkResult(
                            text=results["documents"][0][i],
                            source=meta.get("source", "未知"),
                            score=round(score, 4),
                            chunk_id=chunk_id,
                            category=meta.get("category", ""),
                        ))
            return sorted(chunk_results, key=lambda r: r.score, reverse=True)
        except Exception:
            return []

    def _keyword_search(self, query, category, top_k):
        """BM25关键词检索（不需要模型下载）"""
        try:
            # Get all documents
            all_docs = self.collection.get(include=["documents", "metadatas"])
            if not all_docs["ids"]:
                return []

            # Simple keyword scoring
            import jieba
            query_words = set(jieba.cut(query))

            scored = []
            for i in range(len(all_docs["ids"])):
                meta = all_docs["metadatas"][i] if all_docs["metadatas"] else {}
                if category and meta.get("category") != category:
                    continue

                doc = all_docs["documents"][i] if all_docs["documents"] else ""
                doc_words = set(jieba.cut(doc))

                # Score: intersection over query words
                overlap = query_words & doc_words
                if overlap:
                    score = len(overlap) / len(query_words) if query_words else 0
                    scored.append(ChunkResult(
                        text=doc[:500],
                        source=meta.get("source", "未知"),
                        score=round(min(score, 0.99), 4),
                        chunk_id=all_docs["ids"][i],
                        category=meta.get("category", ""),
                    ))

            return sorted(scored, key=lambda r: r.score, reverse=True)[:top_k]
        except ImportError:
            # Fallback: simple substring matching
            scored = []
            try:
                all_docs = self.collection.get(include=["documents", "metadatas"])
                for i in range(len(all_docs["ids"])):
                    meta = all_docs["metadatas"][i] if all_docs["metadatas"] else {}
                    if category and meta.get("category") != category:
                        continue
                    doc = all_docs["documents"][i] if all_docs["documents"] else ""
                    # Score by character overlap
                    query_chars = set(query)
                    doc_chars = set(doc)
                    overlap = query_chars & doc_chars
                    if overlap:
                        score = len(overlap) / len(query_chars)
                        scored.append(ChunkResult(
                            text=doc[:500],
                            source=meta.get("source", "未知"),
                            score=round(score, 4),
                            chunk_id=all_docs["ids"][i],
                            category=meta.get("category", ""),
                        ))
                return sorted(scored, key=lambda r: r.score, reverse=True)[:top_k]
            except Exception:
                return []

    def count(self) -> int:
        return self.collection.count()
