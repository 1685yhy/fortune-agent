"""文本向量化 - 使用 BGE 中文嵌入模型."""
import os
from pathlib import Path
from typing import List
import logging
import numpy as np

logger = logging.getLogger(__name__)


class Embedder:
    """BGE 中文嵌入模型封装. Supports local model + ModelScope mirror."""

    # Local model paths to check first (no download needed)
    LOCAL_PATHS = [
        "/mnt/d/fortune-models/models/BAAI--bge-large-zh-v1.5/snapshots/master",
        "/mnt/e/fortune-models/models/BAAI--bge-large-zh-v1.5/snapshots/master",
        "/opt/fortune-data/models/bge-large-zh-v1.5",
    ]

    def __init__(self, model_name: str = "BAAI/bge-large-zh-v1.5"):
        self.model_name = model_name
        self._model = None
        self._available = None

    def _try_load(self):
        """Try to load model from local path, then ModelScope mirror, then HuggingFace."""
        if self._available is not None:
            return self._available

        from sentence_transformers import SentenceTransformer

        # 1. Try local path first (fastest)
        for local_path in self.LOCAL_PATHS:
            if Path(local_path).exists():
                try:
                    self._model = SentenceTransformer(local_path)
                    self._available = True
                    logger.info("BGE模型加载成功(本地): %s", local_path)
                    return True
                except Exception:
                    continue

        # 2. Try ModelScope mirror (Chinese, no block)
        try:
            from modelscope import snapshot_download
            model_dir = snapshot_download('BAAI/bge-large-zh-v1.5',
                cache_dir=os.environ.get('MODELSCOPE_CACHE', '/tmp/modelscope'))
            self._model = SentenceTransformer(model_dir)
            self._available = True
            logger.info("BGE模型加载成功(ModelScope): %s", model_dir)
            return True
        except Exception as e:
            logger.warning("BGE模型加载失败: %s", e)
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
        return np.zeros((len(texts), 1024), dtype=np.float32)

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def dimension(self) -> int:
        if self._try_load() and self._model:
            return self._model.get_sentence_embedding_dimension()
        return 1024  # BGE-large dimension
