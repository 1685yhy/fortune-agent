"""文本向量化 - 使用 BGE 中文嵌入模型."""
from pathlib import Path
from typing import List
import numpy as np


class Embedder:
    """BGE 中文嵌入模型封装."""

    def __init__(self, model_name: str = "BAAI/bge-large-zh-v1.5"):
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode(self, texts: List[str]) -> np.ndarray:
        """将文本列表转为向量"""
        return self.model.encode(texts, normalize_embeddings=True)

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def dimension(self) -> int:
        return self.model.get_sentence_embedding_dimension()
