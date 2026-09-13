"""FAISS 语义检索器 — 生产检索主路径（276 万条古籍向量库）。

数据
----
- 索引目录: /mnt/d/fortune-data/faiss（可用环境变量 FAISS_INDEX_DIR 覆盖）
  - index.faiss : IndexIDMap2(IndexIVFPQ)，dim=1024，inner_product（向量已 L2 归一化）
  - docs.db     : SQLite 元数据库，rowid 与索引 ID 一一对应（1-based）
  - meta.json   : ntotal=2760956, nlist=4096, M=64, nbits=8, nprobe=128
- 查询向量: 当前 embedder（BAAI/bge-m3，1024 维，输出已归一化），与索引完全匹配

设计
----
- 惰性加载：首次 search() 才读 index.faiss（238MB，几秒）+ 打开 docs.db +
  加载 bge-m3 embedder（首次调用约 10~30s，此后常驻）；服务启动不受拖累。
- 检索（阶段 5·检索升级，方案 §3.2）：
    query → 查询扩展（LLM+术语表，多路召回）→ bge-m3 编码逐路检索 →
    合并去重 → 候选池 Top50 → 用【用户原问题】rerank 精排（bge-reranker-v2-m3，
    防偏题）→ 低分丢弃 → top_k。
- 兼容旧行为：search(query, top_k, expand=False, rerank=False) 即旧单路路径。
- 失败路径：加载失败/查询异常一律返回空列表，由工具层决定兜底策略
  （易理明灯：FAISS 不可用时不降级关键词，直接走 LLM 自然对话）。
- 线程安全：加载与查询均受锁保护，可被多 worker 并发调用。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 索引目录（环境变量可覆盖，便于部署在不同机器）
DEFAULT_INDEX_DIR = "/mnt/d/fortune-data/faiss"

# 展示文本长度上限（与 BM25 检索器一致，工具侧还会再截到 200）
DISPLAY_CHARS = 600

# 最低相关度（inner_product ≈ 余弦相似度，bge-m3 已归一化，0.3 以下多为噪声）
DEFAULT_MIN_SCORE = 0.3

# 阶段 5·检索升级：候选池规模（召回阶段上限）/ 每路召回条数 / 扩展查询上限
CANDIDATE_POOL = 50
RECALL_PER_QUERY = 40
RECALL_PER_EXPAND_QUERY = 25   # 扩展查询每路少取：给原问题首路让位（防池被挤占）
MAX_EXPAND_QUERIES = 6

try:
    import faiss
    HAS_FAISS = True
except ImportError:  # pragma: no cover — 环境缺 faiss 时优雅降级
    faiss = None
    HAS_FAISS = False
    logger.warning("FAISS 未安装（pip install faiss-cpu），向量检索将不可用")


class FaissRetriever:
    """FAISS 向量检索器：惰性加载索引 + docs.db + embedder。

    search(query, top_k) 输出格式与 BM25 检索器一致：
        [{text, source, score, title, category, doc_id}, ...] 按相关度降序。
    """

    def __init__(
        self,
        index_dir: Optional[str] = None,
        nprobe: int = 128,
        min_score: float = DEFAULT_MIN_SCORE,
    ):
        self.index_dir = Path(
            index_dir or os.environ.get("FAISS_INDEX_DIR") or DEFAULT_INDEX_DIR
        )
        self.index_path = self.index_dir / "index.faiss"
        self.docs_db_path = self.index_dir / "docs.db"
        self.meta_path = self.index_dir / "meta.json"
        self.nprobe = nprobe
        self.min_score = min_score

        self._lock = threading.Lock()
        self._loaded = False
        self._load_error: Optional[str] = None
        self._load_seconds = 0.0

        # 运行时状态（加载后填充）
        self._index = None          # faiss IndexIDMap2
        self._conn: Optional[sqlite3.Connection] = None
        self._embedder = None       # src.rag.embedder.Embedder (bge-m3)
        self.ntotal: int = 0
        self.dimension: int = 0

    # ------------------------------------------------------------------
    # 加载（惰性，首次 search 时触发）
    # ------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """索引是否已加载可用（失败返回 False，原因见 load_error）。"""
        return self._loaded

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    @property
    def loaded_embedder(self):
        """已加载的 bge-m3 embedder（未就绪 = None）。

        k43：同进程的语义路由（src/rag/semantic_router.py）复用同一实例
        （同模型同维度），避免 .load() 第二份 ~2GB 模型副本。只读、不触发加载。
        """
        return self._embedder if self._loaded else None

    def ensure_ready(self) -> bool:
        """线程安全地确保索引 + embedder 已加载；失败返回 False。"""
        if self._loaded:
            return True
        with self._lock:
            if self._loaded:
                return True
            t0 = time.time()
            try:
                self._load()
                self._loaded = True
                self._load_seconds = time.time() - t0
                logger.info(
                    "FAISS 索引加载完成: ntotal=%d dim=%d nprobe=%d, "
                    "索引 %.1fs + embedder 就绪, 共 %.1fs",
                    self.ntotal, self.dimension, self.nprobe,
                    self._load_seconds, self._load_seconds,
                )
            except Exception as e:  # noqa: BLE001 — 加载失败可重试
                self._load_error = f"{type(e).__name__}: {e}"
                logger.error("FAISS 检索器加载失败: %s", self._load_error)
            return self._loaded

    def _load(self) -> None:
        if not HAS_FAISS:
            raise RuntimeError("faiss 未安装（pip install faiss-cpu）")
        if not self.index_path.exists():
            raise FileNotFoundError(f"FAISS 索引不存在: {self.index_path}")
        if not self.docs_db_path.exists():
            raise FileNotFoundError(f"docs.db 不存在: {self.docs_db_path}")

        # -- 元信息 --
        if self.meta_path.exists():
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            self.ntotal = int(meta.get("ntotal") or 0)
            self.dimension = int(meta.get("dimension") or 0)

        # -- 索引 --
        t0 = time.time()
        self._index = faiss.read_index(str(self.index_path))
        # IndexIDMap2 包装的 IndexIVFPQ 才有 nprobe 属性
        inner = self._index
        if isinstance(inner, faiss.IndexIDMap2):
            inner = faiss.downcast_index(inner.index)
        if hasattr(inner, "nprobe"):
            inner.nprobe = self.nprobe
        if not self.ntotal:
            self.ntotal = self._index.ntotal
        logger.info("  index.faiss 读取完成: %s ntotal=%d (%.1fs)",
                    type(self._index).__name__, self._index.ntotal, time.time() - t0)

        # -- docs.db --
        self._conn = sqlite3.connect(
            f"file:{self.docs_db_path}?mode=ro", uri=True, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row

        # -- embedder（bge-m3，与索引同模型，维度必须匹配）--
        t0 = time.time()
        from .embedder import Embedder

        self._embedder = Embedder(model_name="BAAI/bge-m3")
        if not self._embedder.load():
            raise RuntimeError("bge-m3 embedder 加载失败")
        if self._embedder.dimension != self.dimension:
            raise RuntimeError(
                f"维度不匹配: embedder={self._embedder.dimension}, "
                f"索引={self.dimension}"
            )
        logger.info("  embedder(bge-m3) 就绪 dim=%d (%.1fs)",
                    self.dimension, time.time() - t0)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5, original_query: Optional[str] = None,
               expand: bool = True, rerank: bool = True) -> List[dict]:
        """FAISS 语义检索（阶段 5·检索升级：多路召回 + Rerank 精排）。

        Args:
            query: 查询文本（自然语言，如"梦见蛇 解梦 征兆"）
            top_k: 返回条数
            original_query: 用户原问题（精排打分基准，防偏题核心）。
                扩展查询只管召回，相关性一律以原问题为准；None 时用 query。
            expand: 是否查询扩展（LLM+术语表多路召回）
            rerank: 是否 Rerank 精排（bge-reranker-v2-m3 本地模型，惰性加载）

        Returns:
            [{text, source, score, title, category, doc_id, type}, ...]
            按精排分数降序（type="book" 来源标注）；
            加载失败/查询异常返回 []（由工具层决定兜底策略）。
        """
        if not self.ensure_ready():
            return []
        if not query or not query.strip():
            return []

        try:
            # 1) 查询扩展：原问题 + LLM 扩展（命理术语/同义改写）+ 术语表
            t0 = time.time()
            queries = [query]
            if expand:
                try:
                    from .query_expansion import expand_queries
                    queries = expand_queries(query)  # api_key 按环境变量候选取值
                except Exception:  # noqa: BLE001 — 扩展失败走单路
                    queries = [query]
            expand_ms = (time.time() - t0) * 1000

            # 2) 召回阶段（找得全）：多路 FAISS 检索 → 合并去重 → 候选池
            t0 = time.time()
            pool = self._recall(queries[:MAX_EXPAND_QUERIES])
            recall_ms = (time.time() - t0) * 1000
            if not pool:
                return []

            # 3) 精排阶段（排得准）：用【用户原问题】rerank 打分 → 低分丢弃
            t0 = time.time()
            rerank_base = (original_query or query).strip()[:200]
            if rerank and len(pool) > 1:
                from .reranker import get_reranker
                results = get_reranker().rerank(rerank_base, pool, top_k=top_k)
            else:
                results = [c for c in pool
                           if c.get("score", 0.0) >= self.min_score][:top_k]
            rerank_ms = (time.time() - t0) * 1000
        except Exception as e:  # noqa: BLE001 — 查询异常不崩溃，交给工具层兜底
            logger.error("FAISS 检索异常: %s", e, exc_info=True)
            return []

        if results:
            logger.debug(
                "FAISS 检索 '%s' → %d 路扩展 → 候选 %d → Top%d "
                "(扩展 %.0fms + 召回 %.0fms + 精排 %.0fms)",
                query, len(queries), len(pool), len(results),
                expand_ms, recall_ms, rerank_ms,
            )
        return results

    # ------------------------------------------------------------------
    # 召回阶段（多路检索 + 合并去重）
    # ------------------------------------------------------------------

    def _encode(self, text: str) -> np.ndarray:
        """bge-m3 编码（输出已 L2 归一化，与 inner_product 索引匹配）。"""
        vec = self._embedder.encode_single(text)
        return np.ascontiguousarray(vec.astype(np.float32).reshape(1, -1))

    def _recall(self, queries: List[str], pool_cap: int = CANDIDATE_POOL) -> List[dict]:
        """多路 FAISS 检索 → 按 rowid 去重合并（保留最高分）→ 候选池。

        每路召回：首路（用户原问题）RECALL_PER_QUERY 条、扩展查询每路
        RECALL_PER_EXPAND_QUERY 条——保证原问题的最佳匹配永远有池中席位
        （防扩展查询挤占池子）；去重后按召回分降序截取 pool_cap。
        返回条目带 type="book"（来源体系标注）。
        """
        merged: dict = {}
        for qi, q in enumerate(queries):
            if not q or not q.strip():
                continue
            limit = RECALL_PER_QUERY if qi == 0 else RECALL_PER_EXPAND_QUERY
            vec = self._encode(q)
            scores, ids = self._index.search(vec, limit)
            for faiss_id, score in zip(ids[0], scores[0]):
                if faiss_id <= 0 or float(score) < self.min_score:
                    continue
                key = int(faiss_id)
                cur = merged.get(key)
                if cur is None or float(score) > cur["_recall_score"]:
                    row = self._fetch_row(key)
                    if row is None:
                        continue
                    row["_recall_score"] = float(score)
                    merged[key] = row
        out = sorted(merged.values(), key=lambda r: r["_recall_score"], reverse=True)
        for r in out:
            r["score"] = round(r["_recall_score"], 4)
            r["type"] = "book"
            r.pop("_recall_score", None)
        return out[:pool_cap]

    def _fetch_row(self, rowid: int) -> Optional[dict]:
        """docs.db 取单行文本（锁保护，可并发）。"""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT rowid, doc_id, text, title, category, source "
                    "FROM docs WHERE rowid = ?",
                    (int(rowid),),
                ).fetchone()
            except Exception:  # noqa: BLE001 — 单行取数失败跳过
                return None
            if row is None:
                return None
            return {
                "text": (row["text"] or "")[:DISPLAY_CHARS],
                "source": str(row["source"] or ""),
                "title": str(row["title"] or ""),
                "category": str(row["category"] or ""),
                "doc_id": str(row["doc_id"] or ""),
            }

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """索引统计（向量数/维度/加载耗时/索引路径）。"""
        return {
            "ntotal": self.ntotal,
            "dimension": self.dimension,
            "nprobe": self.nprobe,
            "min_score": self.min_score,
            "load_seconds": round(self._load_seconds, 2),
            "ready": self._loaded,
            "index_dir": str(self.index_dir),
        }


# ---------------------------------------------------------------------------
# 进程级单例（惰性加载）
# ---------------------------------------------------------------------------

_singleton: Optional[FaissRetriever] = None
_singleton_lock = threading.Lock()


def get_faiss_retriever(index_dir: Optional[str] = None) -> FaissRetriever:
    """获取进程级 FAISS 检索器单例（首次调用时惰性加载索引）。"""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = FaissRetriever(index_dir=index_dir)
    return _singleton


def reset_faiss_retriever() -> None:
    """测试用：重置单例，便于重复加载验证。"""
    global _singleton
    with _singleton_lock:
        _singleton = None
