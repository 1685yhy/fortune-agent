"""文本向量化 - 使用 BGE 中文嵌入模型."""
import os
from pathlib import Path
from typing import List
import logging
import numpy as np

logger = logging.getLogger(__name__)


class Embedder:
    """BGE 中文嵌入模型封装. Supports local model + ModelScope mirror."""

    # Local model paths — disabled for 384-dim consistency with ChromaDB
    LOCAL_PATHS = []  # Force download of all-MiniLM-L6-v2 (384-dim)

    def __init__(self, model_name: str = None):
        # Use 384-dim model to match ChromaDB default embedding
        self.model_name = model_name or "all-MiniLM-L6-v2"
        self._model = None
        self._available = None

    def _try_load(self):
        """Try to load model from local path, then ModelScope mirror."""
        if self._available is not None:
            return self._available

        from sentence_transformers import SentenceTransformer

        # 1. Try local path first (fastest)
        for local_path in self.LOCAL_PATHS:
            if Path(local_path).exists():
                try:
                    self._model = SentenceTransformer(local_path)
                    self._available = True
                    logger.info("Model loaded from local: %s (dim=%d)", local_path,
                                self._model.get_sentence_embedding_dimension())
                    return True
                except Exception:
                    continue

        # 2. Try ModelScope mirror for 384-dim model
        try:
            from modelscope import snapshot_download
            # Use a 384-dim model available on ModelScope
            model_dir = snapshot_download(
                'iic/nlp_gte_sentence-embedding_chinese-base',
                cache_dir=os.environ.get('MODELSCOPE_CACHE', '/tmp/modelscope'))
            self._model = SentenceTransformer(model_dir)
            self._available = True
            dim = self._model.get_sentence_embedding_dimension()
            logger.info("Model loaded from ModelScope: %s (dim=%d)", model_dir, dim)
            return True
        except Exception as e:
            logger.warning("ModelScope failed: %s, trying HuggingFace...", e)
            try:
                self._model = SentenceTransformer("all-MiniLM-L6-v2")
                self._available = True
                logger.info("Model loaded from HuggingFace (384-dim)")
                return True
            except Exception as e2:
                logger.warning("All model sources failed: %s", e2)
                self._available = False
                self._model = None
                return False

    @property
    def model(self):
        self._try_load()
        return self._model

    def encode(self, texts: List[str]) -> np.ndarray:
        """将文本列表转为向量"""
        if self._try_load() and self._model:
            return self._model.encode(texts, normalize_embeddings=True)
        # Fallback: return zero vectors (will match nothing in search)
        logger.debug(f"Using dummy embeddings for {len(texts)} texts")
        return np.zeros((len(texts), self.dimension), dtype=np.float32)

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def dimension(self) -> int:
        if self._try_load() and self._model:
            return self._model.get_sentence_embedding_dimension()
        return 768  # GTE-base dimension (ModelScope mirror)
