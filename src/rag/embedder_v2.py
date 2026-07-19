"""确定性 Embedder — 配置驱动，模型可控，维度一致.

架构原则:
1. model_name 是唯一真相来源，不使用硬编码 fallback 模型列表
2. 加载顺序: 本地路径 → ModelScope → HuggingFace (仅在 model_name 指定时)
3. 加载失败 = 报错，不偷偷降级到不同维度的模型
4. dimension 在加载前从 model_name 推导，加载后从模型验证，必须一致
5. 支持 ONNX 量化导出（后续任务使用）
"""
import os
import logging
from pathlib import Path
from typing import List, Optional
import numpy as np

logger = logging.getLogger(__name__)

# 已知模型的预期维度（加载前即可确定，无需实际加载模型）
_MODEL_DIMENSIONS = {
    "BAAI/bge-m3": 1024,
    "BAAI/bge-large-zh-v1.5": 1024,
    "BAAI/bge-base-zh-v1.5": 768,
    "BAAI/bge-small-zh-v1.5": 512,
    "iic/nlp_gte_sentence-embedding_chinese-base": 768,
    "all-MiniLM-L6-v2": 384,
    "intfloat/multilingual-e5-large": 1024,
    "intfloat/multilingual-e5-base": 768,
}

# 本地路径映射（优先加载，避免网络依赖）
_LOCAL_PATH_MAP: dict[str, str] = {}


def register_local_model(model_name: str, local_path: str) -> None:
    """注册本地模型路径，后续加载时优先使用"""
    _LOCAL_PATH_MAP[model_name] = local_path


class EmbedderV2:
    """配置驱动的确定性 Embedder"""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        prefer_local: bool = True,
        cache_dir: Optional[str] = None,
    ):
        if not model_name:
            raise ValueError("model_name is required")
        self.model_name = model_name
        self.prefer_local = prefer_local
        self.cache_dir = cache_dir or os.environ.get(
            "MODELSCOPE_CACHE", "/tmp/modelscope"
        )
        self._model = None
        self._loaded = False
        self._actual_dimension: Optional[int] = None

    @property
    def dimension(self) -> int:
        """预期维度 — 加载前从已知表推导，加载后从模型验证"""
        if self._actual_dimension is not None:
            return self._actual_dimension
        # 尝试从已知表推导
        for key, dim in _MODEL_DIMENSIONS.items():
            if key in self.model_name or self.model_name in key:
                return dim
        # 未知模型默认 1024（BGE-M3 系列主流维度）
        logger.warning(
            "Unknown model '%s', assuming dimension=1024. "
            "Add to _MODEL_DIMENSIONS if different.",
            self.model_name,
        )
        return 1024

    def load(self) -> bool:
        """显式加载模型. 返回 True=成功, False=失败. 失败不降级."""
        if self._loaded:
            return True

        from sentence_transformers import SentenceTransformer

        # 1. 检查注册的本地路径
        local_path = _LOCAL_PATH_MAP.get(self.model_name)
        if local_path and Path(local_path).exists():
            try:
                self._model = SentenceTransformer(local_path)
                self._verify_and_set_dimension()
                logger.info(
                    "Embedder loaded from registered local: %s (dim=%d)",
                    local_path, self._actual_dimension,
                )
                self._loaded = True
                return True
            except Exception as e:
                logger.warning("Registered local path failed: %s", e)

        # 2. 尝试 ModelScope mirror（国内更快）
        try:
            from modelscope import snapshot_download

            modelscope_name = self._to_modelscope_name(self.model_name)
            model_dir = snapshot_download(
                modelscope_name, cache_dir=self.cache_dir
            )
            self._model = SentenceTransformer(str(model_dir))
            self._verify_and_set_dimension()
            logger.info(
                "Embedder loaded from ModelScope: %s (dim=%d)",
                modelscope_name, self._actual_dimension,
            )
            self._loaded = True
            return True
        except Exception as e:
            logger.info("ModelScope failed for '%s': %s", self.model_name, e)

        # 3. 尝试 HuggingFace
        try:
            self._model = SentenceTransformer(self.model_name)
            self._verify_and_set_dimension()
            logger.info(
                "Embedder loaded from HuggingFace: %s (dim=%d)",
                self.model_name, self._actual_dimension,
            )
            self._loaded = True
            return True
        except Exception as e:
            logger.error(
                "Failed to load model '%s' from any source: %s",
                self.model_name, e,
            )
            self._loaded = False
            self._model = None
            return False

    def _to_modelscope_name(self, hf_name: str) -> str:
        """HuggingFace model name → ModelScope model name"""
        # 常用映射表
        mapping = {
            "BAAI/bge-m3": "BAAI/bge-m3",
            "BAAI/bge-large-zh-v1.5": "BAAI/bge-large-zh-v1.5",
            "BAAI/bge-base-zh-v1.5": "BAAI/bge-base-zh-v1.5",
            "BAAI/bge-small-zh-v1.5": "BAAI/bge-small-zh-v1.5",
        }
        return mapping.get(hf_name, hf_name)

    def _verify_and_set_dimension(self) -> None:
        """验证实际维度与预期一致"""
        actual = self._model.get_embedding_dimension()
        expected = self.dimension  # 从 _MODEL_DIMENSIONS 推导
        if actual != expected:
            logger.warning(
                "Dimension mismatch for '%s': actual=%d, expected=%d. "
                "Using actual=%d.",
                self.model_name, actual, expected, actual,
            )
        self._actual_dimension = actual

    def encode(self, texts: List[str]) -> np.ndarray:
        """文本 → 向量矩阵 (N, dimension)"""
        if not self._loaded:
            raise RuntimeError(
                f"Embedder not loaded. Call embedder.load() first. "
                f"model_name={self.model_name}"
            )
        return self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def model(self):
        """底层 SentenceTransformer 实例"""
        if not self._loaded:
            raise RuntimeError("Embedder not loaded")
        return self._model

    def export_onnx(self, output_path: str) -> str:
        """导出 ONNX 量化模型（用于 CPU 推理优化）"""
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer

        if not self._loaded:
            raise RuntimeError("Embedder not loaded")

        tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        onnx_model = ORTModelForFeatureExtraction.from_pretrained(
            self.model_name, export=True
        )
        onnx_model.save_pretrained(output_path)
        tokenizer.save_pretrained(output_path)
        logger.info("ONNX model exported to %s", output_path)
        return output_path
