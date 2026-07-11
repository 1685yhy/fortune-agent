"""文本向量化 - 使用 BGE 中文嵌入模型."""
from pathlib import Path
from typing import List
import logging
import numpy as np

logger = logging.getLogger(__name__)


class Embedder:
    """BGE 中文嵌入模型封装. Falls back to dummy embeddings if model unavailable."""

    def __init__(self, model_name: str = "BAAI/bge-large-zh-v1.5"):
        self.model_name = model_name
        self._model = None
        self._available = None  # None = not checked yet

    def _try_load(self):
        """Try to load model. Return True if successful."""
        if self._available is not None:
            return self._available
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            self._available = True
            logger.info(f"BGE模型加载成功: {self.model_name}")
        except Exception as e:
            logger.warning(f"BGE模型加载失败，使用降级模式: {e}")
            self._available = False
            self._model = None
        return self._available

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
        return np.zeros((len(texts), 1024), dtype=np.float32)

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def dimension(self) -> int:
        if self._try_load() and self._model:
            return self._model.get_sentence_embedding_dimension()
        return 1024  # BGE-large dimension
