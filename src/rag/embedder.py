"""Embedder 兼容包装 — 转发到 EmbedderV2.

保持原有 import 路径和类名不变，内部使用 EmbedderV2 实现。
旧接口 (无参数构造、dimension属性、encode/encode_single) 完全兼容。
"""
import logging
from typing import List
import numpy as np

from .embedder_v2 import EmbedderV2, register_local_model

logger = logging.getLogger(__name__)

# 模型本地路径注册 — 部署时自动发现已下载的模型
import os
from pathlib import Path

_MODELSCOPE_CACHE = os.environ.get("MODELSCOPE_CACHE", "/tmp/modelscope")

# 自动发现 ModelScope 缓存中的模型
_KNOWN_MODELS = {
    "BAAI/bge-m3": f"{_MODELSCOPE_CACHE}/models/BAAI--bge-m3/snapshots/master",
    "BAAI/bge-large-zh-v1.5": f"{_MODELSCOPE_CACHE}/models/BAAI--bge-large-zh-v1.5/snapshots/master",
    "iic/nlp_gte_sentence-embedding_chinese-base": f"{_MODELSCOPE_CACHE}/models/iic--nlp_gte_sentence-embedding_chinese-base/snapshots/master",
}

for _name, _path in _KNOWN_MODELS.items():
    if Path(_path).exists():
        register_local_model(_name, _path)
        logger.debug("Registered local model: %s → %s", _name, _path)


class Embedder:
    """BGE 中文嵌入模型封装 (兼容包装，内部使用 EmbedderV2).

    Usage:
        embedder = Embedder(model_name="BAAI/bge-m3")
        embedder.load()              # 显式加载
        vec = embedder.encode_single("测试")
    """

    def __init__(self, model_name: str = None):
        # 默认模型改为 BGE-M3
        self._inner = EmbedderV2(
            model_name=model_name or "BAAI/bge-m3",
        )

    def load(self) -> bool:
        """加载模型，返回是否成功"""
        return self._inner.load()

    @property
    def model(self):
        """底层模型实例"""
        self._ensure_loaded()
        return self._inner.model

    @property
    def dimension(self) -> int:
        """嵌入维度"""
        return self._inner.dimension

    def encode(self, texts: List[str]) -> np.ndarray:
        """文本列表 → 向量矩阵"""
        self._ensure_loaded()
        return self._inner.encode(texts)

    def encode_single(self, text: str) -> np.ndarray:
        """单个文本 → 向量"""
        self._ensure_loaded()
        return self._inner.encode_single(text)

    def _ensure_loaded(self):
        """确保模型已加载，未加载则尝试加载"""
        if not self._inner._loaded:
            if not self._inner.load():
                raise RuntimeError(
                    f"Failed to load embedding model: {self._inner.model_name}. "
                    f"Check network or set MODELSCOPE_CACHE env."
                )
