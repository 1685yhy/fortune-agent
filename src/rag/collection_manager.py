"""向量集合版本管理 — 集合创建、验证、切换."""
import logging
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    """集合验证报告"""
    collection_name: str
    exists: bool
    doc_count: int
    dimension: Optional[int]
    valid: bool
    errors: list


class CollectionManager:
    """管理 ChromaDB 集合的创建、验证和版本切换"""

    def __init__(self, persist_dir: str, collection_name: str, dimension: int):
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.dimension = dimension
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            self._client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def validate(self) -> ValidationReport:
        """验证集合是否存在、维度是否正确、有数据"""
        report = ValidationReport(
            collection_name=self.collection_name,
            exists=False,
            doc_count=0,
            dimension=None,
            valid=False,
            errors=[],
        )

        try:
            collections = self.client.list_collections()
            names = [c.name for c in collections]
            report.exists = self.collection_name in names
        except Exception as e:
            report.errors.append(f"Cannot list collections: {e}")
            return report

        if not report.exists:
            report.errors.append(f"Collection '{self.collection_name}' not found")
            return report

        try:
            col = self.client.get_collection(self.collection_name)
            report.doc_count = col.count()

            if report.doc_count == 0:
                report.errors.append("Collection is empty")

            # 验证维度：尝试查询一条看是否报维度错误
            try:
                # 用零向量测试维度匹配
                import numpy as np
                test_vec = np.zeros((1, self.dimension), dtype=np.float32)
                col.query(query_embeddings=test_vec.tolist(), n_results=1)
            except Exception as e:
                err_msg = str(e)
                if "dimension" in err_msg.lower():
                    report.errors.append(f"Dimension mismatch: {err_msg[:200]}")
                # 空集合也查不到，不算错
        except Exception as e:
            report.errors.append(f"Cannot access collection: {e}")

        report.valid = len(report.errors) == 0
        return report

    def ensure_exists(self) -> dict:
        """确保集合存在，返回 collection 对象"""
        import chromadb
        try:
            return self.client.get_collection(
                self.collection_name,
                embedding_function=None,  # 我们自己提供 embedding
            )
        except Exception:
            logger.info("Creating new collection: %s", self.collection_name)
            return self.client.create_collection(
                name=self.collection_name,
                metadata={
                    "hnsw:space": "cosine",
                    "dimension": self.dimension,
                    "model": "bge-m3",
                    "version": "4.0",
                },
            )

    def list_collections(self) -> list:
        """列出所有集合及其统计"""
        result = []
        for c in self.client.list_collections():
            result.append({
                "name": c.name,
                "count": c.count(),
                "metadata": c.metadata,
            })
        return sorted(result, key=lambda x: x["name"])
