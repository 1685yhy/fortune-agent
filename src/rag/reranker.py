"""Rerank 精排（阶段 5·防偏题核心）— bge-reranker-v2-m3 本地模型。

设计（方案 §3.2）：
    召回阶段（找得全）：扩展查询多路召回 → 候选池 Top50
    精排阶段（排得准）：⚠️ 用【用户原问题】rerank 打分 → 低分（<0.3）丢弃 → Top5

- 惰性加载：首次 rerank() 才加载模型（约 5~10s，此后常驻），服务启动不受拖累
- 失败路径：模型加载失败/查询异常 → 返回原候选（按召回分数排序）截取 top_k，
  不抛异常（检索是增强，不能阻塞对话）
- 线程安全：加载与推理受锁保护，可被多 worker 并发调用
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# 本地模型路径（ModelScope 缓存，与 embedder.py 的 _KNOWN_MODELS 同目录约定）
DEFAULT_RERANKER_PATH = (
    os.environ.get(
        "RERANKER_MODEL_PATH",
        "/tmp/modelscope/models/BAAI--bge-reranker-v2-m3/snapshots/master",
    )
)

# 低分阈值（2026-08-08 实测校准）：bge-reranker-v2-m3 sigmoid 输出的绝对
# 分数域随内容类型剧烈变化——梦境/现代文本 0.34~0.99（相关）、0.02~0.05（无关），
# 古籍文言文本（增删卜易/滴天髓/三命通会等）0.01~0.28（相关）甚至 0.005（无关）。
# 绝对阈值（无论 0.1 还是 0.3）都会误杀某一类内容，故采用【相对阈值】：
#   丢弃 score < max(绝对下限, 池内最高分 × REL_DROP_RATIO) 的片段
# 即"比池内最佳差 70% 以上的不要"——梦境池（top1≈0.99）只留 0.3+；
# 古籍池（top1≈0.02）留 0.006+，配合 Top-k 截断取前 5。
DEFAULT_MIN_SCORE = 0.01   # 绝对下限（防止全池极低时误保留噪声）
REL_DROP_RATIO = 0.30      # 相对阈值：低于池内最高分的 30% 丢弃

# 精排打分时的文档截断长度（交叉编码器对超长输入敏感）
RERANK_DOC_CHARS = 512


class Reranker:
    """bge-reranker-v2-m3 交叉编码精排器（惰性加载单例）。"""

    def __init__(self, model_path: str = DEFAULT_RERANKER_PATH):
        self.model_path = Path(model_path)
        self._lock = threading.Lock()
        self._model = None
        self._load_error: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def _ensure(self) -> bool:
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            try:
                if not self.model_path.exists():
                    raise FileNotFoundError(f"精排模型不存在: {self.model_path}")
                # 注意：必须用 CrossEncoder 才能加载 classifier 头（打分），
                # SentenceTransformer 加载会丢掉分类层（输出成句子向量）
                from sentence_transformers.cross_encoder import CrossEncoder
                self._model = CrossEncoder(
                    str(self.model_path), max_length=512,
                )
                logger.info("bge-reranker-v2-m3 精排模型就绪: %s", self.model_path)
            except Exception as e:  # noqa: BLE001 — 加载失败可重试
                self._load_error = f"{type(e).__name__}: {e}"
                logger.error("精排模型加载失败: %s", self._load_error)
                return False
        return True

    def rerank(
        self,
        query: str,
        candidates: List[dict],
        top_k: int = 5,
        min_score: float = DEFAULT_MIN_SCORE,
        rel_ratio: float = REL_DROP_RATIO,
        keep_all: bool = False,
    ) -> List[dict]:
        """用【用户原问题】对候选池精排打分，取 TopK、低分丢弃。

        Args:
            query: 用户原问题（打分基准，非扩展查询）
            candidates: 召回阶段候选池 [{text, score, ...}, ...]
            top_k: 返回条数
            min_score: 绝对下限（低于该分一律丢弃）
            rel_ratio: 相对阈值系数——低于"池内最高分 × rel_ratio"的丢弃
                （默认 0.30：比池内最佳差 70% 以上的不要；绝对分数域随内容
                类型变化，纯绝对阈值会误杀古籍文言类内容）
            keep_all: True 时返回全部（不截断不丢弃），仅重排（测试/调试用）

        Returns:
            按精排分数降序的候选列表；模型不可用/异常 → 原列表截取 top_k
            （字段 score 在精排成功后替换为精排分数）。
        """
        if not candidates:
            return []
        if not query or not query.strip():
            return candidates[:top_k]
        if not self._ensure():
            return candidates[:top_k]
        try:
            docs = [(c.get("text") or "")[:RERANK_DOC_CHARS] for c in candidates]
            pairs = [[query, d] for d in docs]
            scores = self._model.predict(
                pairs, batch_size=8, show_progress_bar=False,
            )
            # 兼容单值/二维输出（sigmoid 概率 [0,1]）
            import numpy as np
            scores = np.asarray(scores).ravel()
            for c, s in zip(candidates, scores):
                c["score"] = round(float(s), 4)
            ranked = sorted(
                candidates,
                key=lambda c: c.get("score", 0.0),
                reverse=True,
            )
            if keep_all:
                return ranked
            if not ranked:
                return []
            top1 = max(ranked[0].get("score", 0.0), 1e-6)
            floor = max(float(min_score), top1 * float(rel_ratio))
            return [c for c in ranked if c.get("score", 0.0) >= floor][:top_k]
        except Exception as e:  # noqa: BLE001 — 精排异常不阻塞检索
            logger.error("精排打分异常，降级为召回序: %s", e, exc_info=True)
            return candidates[:top_k]


# ---------------------------------------------------------------------------
# 进程级单例（惰性加载）
# ---------------------------------------------------------------------------

_singleton: Optional[Reranker] = None
_singleton_lock = threading.Lock()


def get_reranker(model_path: Optional[str] = None) -> Reranker:
    """获取进程级精排器单例（首次调用时惰性加载模型）。"""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = Reranker(model_path=model_path or DEFAULT_RERANKER_PATH)
    return _singleton


def reset_reranker() -> None:
    """测试用：重置单例。"""
    global _singleton
    with _singleton_lock:
        _singleton = None
