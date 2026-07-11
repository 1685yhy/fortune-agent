"""语义检索器."""
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


class Retriever:
    """语义检索器"""

    def __init__(self, persist_dir: str, embedder: Embedder):
        self.persist_dir = persist_dir
        self.embedder = embedder
        self._client = None
        self._collection = None

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
                name="fortune_books",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def add_chunks(self, chunks: List[Chunk]):
        """批量添加切块到向量库"""
        if not chunks:
            return
        texts = [c.text for c in chunks]
        embeddings = self.embedder.encode(texts)
        ids = [c.chunk_id for c in chunks]
        metadatas = [{
            "source": c.source,
            "author": c.author,
            "category": c.category,
        } for c in chunks]
        self.collection.add(
            embeddings=embeddings.tolist(),
            documents=texts,
            ids=ids,
            metadatas=metadatas,
        )

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        top_k: int = 20,
        min_score: float = 0.3,
    ) -> List[ChunkResult]:
        """语义检索"""
        query_embedding = self.embedder.encode_single(query)
        where_filter = None
        if category:
            where_filter = {"category": category}

        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        chunk_results = []
        if results["ids"] and results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                score = 1 - results["distances"][0][i]  # cosine distance → similarity
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

    def count(self) -> int:
        return self.collection.count()
