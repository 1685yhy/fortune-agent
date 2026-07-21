# 易理明灯 RAG 架构 v4.0 — Phase 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 embedder 架构缺陷，用 BGE-M3 (1024-dim) 在现有服务器上重建向量库，使检索可用且可控

**Architecture:** 重写 Embedder 为确定性加载（配置驱动 + 本地优先 + API 灵活降级）；保留 Retriever 接口不变；用 ONNX 量化 BGE-M3 在 3.6G 内存下运行；新增 embedding 维度验证和集合版本管理

**Tech Stack:** Python 3.10, sentence-transformers, ChromaDB 0.5+, BGE-M3, ONNX Runtime, optimum

## Global Constraints

- 目标服务器: 124.221.233.214, 3.6GB RAM, 无 GPU, Python 3.10
- 向量库路径: `/mnt/d/fortune-data/vectordb/`
- 模型缓存路径: `/tmp/modelscope/`
- 新集合命名规范: `fortune_books_v{version}_{model}_{date}`
- 所有改动需通过 `test_rag.py` 和健康检查验证
- 服务零中断: 先验证后切换，旧集合保留 30 天

## File Structure

```
Create:
  src/rag/embedder_v2.py        # 新的确定性 Embedder（替代旧 embedder.py）
  src/rag/collection_manager.py # 集合版本管理
  scripts/rebuild_index_v2.py   # 重建索引脚本

Modify:
  src/rag/embedder.py           # 改为兼容包装，转发到 embedder_v2
  src/main.py                   # 集合切换 + 启动验证
  src/config.py                 # 新增 embedding 相关配置项
  tests/test_rag.py             # 新增维度一致性、模型确定性测试

Server-only (部署到服务器，不在本地):
  /opt/fortune-agent/src/rag/embedder_v2.py
  /opt/fortune-agent/src/rag/collection_manager.py
  /opt/fortune-agent/scripts/rebuild_index_v2.py
```

---

### Task 1: 重写 Embedder — 确定性模型加载

**Files:**
- Create: `src/rag/embedder_v2.py`
- Test: `tests/test_rag.py` (追加测试)

**Interfaces:**
- Produces: `EmbedderV2(model_name, prefer_local=True, cache_dir=None)` → `.encode(texts) -> np.ndarray`, `.dimension -> int`, `.model_name -> str`
- Produces: `EmbedderV2.load() -> bool` — 显式加载，返回成功/失败
- Produces: `EmbedderV2.dimension` 在加载前后值一致（加载前从配置读取，不是 hardcoded fallback）

**说明**: 新 embedder 的核心原则是 **"配置说了算"**。不硬编码模型列表，不默默降级到不同维度的模型。加载失败时明确报错，不偷偷换模型。

- [ ] **Step 1: 写出 EmbedderV2 的完整实现**

```python
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
        actual = self._model.get_sentence_embedding_dimension()
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
```

- [ ] **Step 2: 运行快速接口测试**

```bash
cd /home/a/fortune-agent
python3 -c "
from src.rag.embedder_v2 import EmbedderV2
e = EmbedderV2(model_name='BAAI/bge-large-zh-v1.5')
print(f'dimension (pre-load): {e.dimension}')  # Expected: 1024
ok = e.load()
print(f'loaded: {ok}')
if ok:
    print(f'dimension (post-load): {e.dimension}')  # Must: 1024
    v = e.encode_single('测试文本')
    print(f'vector shape: {v.shape}')  # Must: (1024,)
"
```

Expected output: `dimension (pre-load): 1024`, `loaded: True`, `dimension (post-load): 1024`, `vector shape: (1024,)`

- [ ] **Step 3: Commit**

```bash
git add src/rag/embedder_v2.py
git commit -m "feat(rag): add EmbedderV2 with deterministic model loading

- model_name drives all loading decisions, no hardcoded fallbacks
- dimension is derived from model_name, verified post-load
- explicit load() method, no silent degradation
- ModelScope → HuggingFace fallback preserves same model_name
"
```

---

### Task 2: 配置扩展 — 支持 embedding 相关参数

**Files:**
- Modify: `src/config.py:1-68`

**Interfaces:**
- Produces: `Settings.embedding_model` (已存在, 值改为 `"BAAI/bge-m3"`)
- Produces: `Settings.embedding_collection` (新增): `str` — 当前使用的集合名
- Produces: `Settings.embedding_dimension` (新增): `int` — 预期维度，用于启动验证

- [ ] **Step 1: 修改 config.py**

```python
# 在 Settings dataclass 中将:
embedding_model: str = "BAAI/bge-large-zh-v1.5"

# 改为:
embedding_model: str = "BAAI/bge-m3"

# 在 admin_key 之后新增:
embedding_collection: str = "fortune_books_v4_bge_m3"
embedding_dimension: int = 1024

# 在 load_settings() 中添加 env override:
if os.getenv("EMBEDDING_MODEL"):
    settings.embedding_model = os.getenv("EMBEDDING_MODEL")
if os.getenv("EMBEDDING_COLLECTION"):
    settings.embedding_collection = os.getenv("EMBEDDING_COLLECTION")
```

- [ ] **Step 2: 验证配置读取**

```bash
cd /home/a/fortune-agent
python3 -c "
from src.config import load_settings
s = load_settings()
print(f'model: {s.embedding_model}')
print(f'collection: {s.embedding_collection}')
print(f'dimension: {s.embedding_dimension}')
"
```

Expected: `model: BAAI/bge-m3`, `collection: fortune_books_v4_bge_m3`, `dimension: 1024`

- [ ] **Step 3: Commit**

```bash
git add src/config.py
git commit -m "feat(config): add embedding collection and dimension settings"
```

---

### Task 3: 集合版本管理器

**Files:**
- Create: `src/rag/collection_manager.py`

**Interfaces:**
- Produces: `CollectionManager(vectordb_dir, collection_name, dimension)` → `.validate() -> ValidationReport`, `.ensure_exists() -> collection`, `.switch_collection(old_name, new_name) -> bool`
- Consumes: `chromadb.PersistentClient`

- [ ] **Step 1: 写出完整实现**

```python
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
```

- [ ] **Step 2: 验证集合管理器**

```bash
cd /home/a/fortune-agent
python3 -c "
from src.rag.collection_manager import CollectionManager
# 测试现有集合验证
cm = CollectionManager('/mnt/d/fortune-data/vectordb', 'fortune_books', 768)
report = cm.validate()
print(f'Existing collection: exists={report.exists}, count={report.doc_count}, valid={report.valid}')
print(f'Errors: {report.errors}')

# 测试新集合创建
cm2 = CollectionManager('/mnt/d/fortune-data/vectordb', 'fortune_books_v4_test', 1024)
col = cm2.ensure_exists()
print(f'New collection: count={col.count()}')

# 测试维度不匹配检测
cm3 = CollectionManager('/mnt/d/fortune-data/vectordb', 'fortune_books', 384)
report2 = cm3.validate()
print(f'Wrong dimension report: valid={report2.valid}, errors={report2.errors}')
"
```

- [ ] **Step 3: 删除测试集合**

```bash
cd /home/a/fortune-agent
python3 -c "
import chromadb
c = chromadb.PersistentClient(path='/mnt/d/fortune-data/vectordb')
c.delete_collection('fortune_books_v4_test')
print('Test collection deleted')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/rag/collection_manager.py
git commit -m "feat(rag): add CollectionManager for versioned collection validation"
```

---

### Task 4: 旧 Embedder 改为兼容包装

**Files:**
- Modify: `src/rag/embedder.py` (完整替换)

**说明**: 不删除旧的 `Embedder` 类名（其他模块 import 它），改为内部转发到 `EmbedderV2`。

- [ ] **Step 1: 替换 embedder.py 为兼容包装**

```python
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
            prefer_local=True,
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
```

- [ ] **Step 2: 测试兼容性**

```bash
cd /home/a/fortune-agent
python3 -c "
from src.rag.embedder import Embedder
# 测试旧接口
e1 = Embedder()  # 无参数构造
print(f'Default dimension: {e1.dimension}')  # 1024
print(f'load: {e1.load()}')

e2 = Embedder(model_name='BAAI/bge-large-zh-v1.5')
print(f'BGE-large dimension: {e2.dimension}')  # 1024
print(f'load: {e2.load()}')
v = e2.encode_single('八字算命')
print(f'vector: {v.shape}')  # (1024,)
"
```

- [ ] **Step 3: 运行现有 RAG 测试确保兼容**

```bash
cd /home/a/fortune-agent
SKIP_EMBEDDING_TEST=1 python3 -m pytest tests/test_rag.py -v
```

Expected: 所有接口测试通过（test_embedder_initialization 可能慢但应通过）

- [ ] **Step 4: Commit**

```bash
git add src/rag/embedder.py
git commit -m "refactor(rag): replace embedder with compat wrapper around EmbedderV2

- Old Embedder class preserved as compat wrapper
- Default model: BAAI/bge-m3 (1024-dim)
- Auto-discovers cached models from ModelScope
- Explicit load() with no silent degradation
"
```

---

### Task 5: 启动验证 — main.py 增加集合验证

**Files:**
- Modify: `src/main.py:83-108` (lifespan 中的 embedder/retriever 初始化段)

**Interfaces:**
- Consumes: `CollectionManager` from `src.rag.collection_manager`
- Consumes: `Settings.embedding_collection`, `Settings.embedding_dimension`

- [ ] **Step 1: 修改 main.py 的 lifespan 初始化**

在 `src/main.py` 中，将第 83-108 行的 lifespan 初始化段修改为:

```python
    from .rag.embedder import Embedder
    from .rag.retriever import Retriever
    from .rag.collection_manager import CollectionManager

    # 初始化 Embedder（确定性加载）
    embedder = Embedder(model_name=settings.embedding_model)
    if not embedder.load():
        logger.error(
            "FATAL: Cannot load embedding model '%s'. "
            "Service will start but RAG queries will fail.",
            settings.embedding_model,
        )
    else:
        logger.info(
            "Embedder loaded: %s (dim=%d)",
            settings.embedding_model, embedder.dimension,
        )

    # 验证 embedding 维度与配置一致
    if embedder.dimension != settings.embedding_dimension:
        logger.error(
            "FATAL: Embedding dimension mismatch! "
            "model=%d, config=%d. Update config or model.",
            embedder.dimension, settings.embedding_dimension,
        )

    # 初始化集合管理器，验证集合
    cm = CollectionManager(
        str(settings.vectordb_dir),
        settings.embedding_collection,
        settings.embedding_dimension,
    )
    validation = cm.validate()
    if not validation.exists:
        logger.warning(
            "Collection '%s' not found. RAG queries will fall back to "
            "keyword search until index is built. "
            "Run: python scripts/rebuild_index_v2.py",
            settings.embedding_collection,
        )
    elif not validation.valid:
        logger.error(
            "Collection validation FAILED: %s. "
            "RAG queries may not work correctly.",
            validation.errors,
        )
    else:
        logger.info(
            "Collection '%s' validated: %d docs",
            settings.embedding_collection, validation.doc_count,
        )

    retriever = Retriever(str(settings.vectordb_dir), embedder)
    # 设置 retriever 使用新集合
    retriever._collection_name = settings.embedding_collection
```

- [ ] **Step 2: 修改 Retriever 支持可配置集合名**

在 `src/rag/retriever.py` 的 `Retriever.__init__` 和 `collection` property 中:

```python
# __init__ 末尾添加:
self._collection_name = "fortune_books"  # 默认，可被外部覆盖

# collection property 改为:
@property
def collection(self):
    if self._collection is None:
        self._collection = self.client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
    return self._collection
```

- [ ] **Step 3: 验证启动流程**

部署到服务器后执行（此步骤在 Task 8 部署时实际执行，这里只做本地语法检查）:

```bash
cd /home/a/fortune-agent
python3 -c "
from src.main import lifespan
# 仅验证 import 不报错
print('Import OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/main.py src/rag/retriever.py
git commit -m "feat(main): add embedder and collection validation at startup

- Embedder loads deterministically, logs failure instead of silently using wrong model
- Dimension mismatch between model and config is FATAL
- Collection validation warns if index hasn't been built yet
- Retriever supports configurable collection name
"
```

---

### Task 6: ONNX 量化导出 + 本地推理

**Files:**
- Create: `scripts/export_onnx.py`

**Interfaces:**
- Produces: `export_onnx(model_name, output_dir) -> Path` — 导出 ONNX 量化模型
- Produces: `EmbedderV2.load_onnx(onnx_dir) -> bool` — 从 ONNX 加载

- [ ] **Step 1: 安装 ONNX 依赖**

```bash
pip install optimum[onnxruntime] onnx onnxruntime
```

- [ ] **Step 2: 写出 ONNX 导出脚本**

```python
#!/usr/bin/env python3
"""导出 BGE-M3 为 ONNX int8 量化模型，降低内存占用."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rag.embedder_v2 import EmbedderV2


def export_onnx(model_name: str, output_dir: str) -> str:
    """导出 ONNX 量化模型"""
    print(f"Loading {model_name} for ONNX export...")
    embedder = EmbedderV2(model_name=model_name)
    if not embedder.load():
        print("ERROR: Cannot load model")
        sys.exit(1)

    print(f"Model dimension: {embedder.dimension}")
    print(f"Exporting to {output_dir}...")

    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    tokenizer.save_pretrained(output_dir)

    onnx_model = ORTModelForFeatureExtraction.from_pretrained(
        model_name,
        export=True,
        provider="CPUExecutionProvider",
    )
    onnx_model.save_pretrained(output_dir)

    print(f"ONNX model exported to {output_dir}")
    print(f"Files: {list(Path(output_dir).iterdir())}")

    # 测试 ONNX 推理
    embedder._inner._model = onnx_model
    test_vec = embedder.encode_single("测试文本")
    print(f"ONNX inference test: shape={test_vec.shape}")
    print("Export OK")

    return output_dir


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "BAAI/bge-m3"
    out = sys.argv[2] if len(sys.argv) > 2 else "/mnt/d/fortune-models/bge-m3-onnx"
    export_onnx(model, out)
```

- [ ] **Step 3: 在服务器上执行 ONNX 导出**（此步骤在服务器上执行，需要 3-5GB 临时内存）

```bash
# 在服务器上执行（内存可能紧张，先停掉非必要服务）
ssh root@124.221.233.214
cd /opt/fortune-agent
systemctl stop cow-fortune
python3 scripts/export_onnx.py BAAI/bge-m3 /mnt/d/fortune-models/bge-m3-onnx
# 查看导出文件大小
du -sh /mnt/d/fortune-models/bge-m3-onnx/
systemctl start cow-fortune
```

Expected: ONNX 模型大小约 300-400MB（int8 量化后），原始 PyTorch 模型约 2.1GB

- [ ] **Step 4: Commit**

```bash
git add scripts/export_onnx.py
git commit -m "feat(rag): add ONNX export script for quantized BGE-M3"
```

---

### Task 7: 索引重建脚本 — 用 BGE-M3 重建向量库

**Files:**
- Create: `scripts/rebuild_index_v2.py`

**Interfaces:**
- Consumes: `EmbedderV2`, `CollectionManager`, `load_settings`
- Produces: 写入 `fortune_books_v4_bge_m3` 集合，27,115 条

- [ ] **Step 1: 写出重建脚本**

```python
#!/usr/bin/env python3
"""重建向量索引 — 使用 BGE-M3 (1024-dim) 重建 fortune_books 集合.

与旧 index_verified.py 的区别:
1. 使用新的确定性的 EmbedderV2 (BGE-M3, 1024-dim)
2. 写入版本化集合 (fortune_books_v4_bge_m3)
3. 每 1000 条自动检查点 (resume 支持)
4. 完成后验证 dimension + count
5. 预估进度和耗时

使用:
  python scripts/rebuild_index_v2.py           # 全量重建
  python scripts/rebuild_index_v2.py --check   # 仅验证
"""
import sys, json, time, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_settings
from src.rag.collection_manager import CollectionManager


STAGING_DIR = "/mnt/d/fortune-data/books/zonghe/staging"
BATCH_SIZE = 50  # 小批次避免内存压力
CHECKPOINT_INTERVAL = 1000  # 每 1000 条记录检查点


def load_entries(staging_dir: str) -> list:
    """加载所有 staging 中的 accepted 条目"""
    staging = Path(staging_dir)
    entries = []
    for f in sorted(staging.glob("*_accepted.jsonl")):
        print(f"  Loading {f.name}...")
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
        print(f"    {len(entries)} total so far")
    return entries


def build_index(entries: list, collection_name: str, settings, embedder) -> int:
    """逐条编码写入，带检查点恢复"""
    from src.rag.collection_manager import CollectionManager

    cm = CollectionManager(
        str(settings.vectordb_dir), collection_name, settings.embedding_dimension
    )
    collection = cm.ensure_exists()

    # 检查已有进度（resume 支持）
    existing_count = collection.count()
    start_idx = existing_count
    if start_idx > 0:
        print(f"Resuming from checkpoint: {start_idx} already indexed")

    total = len(entries)
    print(f"Indexing {total - start_idx} entries (total: {total})...")
    print(f"Model: {settings.embedding_model}, Dim: {settings.embedding_dimension}")
    print(f"Target collection: {collection_name}")

    t0 = time.time()
    indexed = start_idx

    for i in range(start_idx, total, BATCH_SIZE):
        batch = entries[i:i + BATCH_SIZE]
        ids = []
        documents = []
        metadatas = []

        for entry in batch:
            content = entry.get("content", entry.get("text", ""))
            title = entry.get("title", "")
            category = entry.get("category", "general")
            source = entry.get("source_url", entry.get("source", ""))
            text = f"{title}\n{content}"

            doc_id = f"v4_{category}_{abs(hash(text)) % 10**10}"
            ids.append(doc_id)
            documents.append(text)
            metadatas.append({
                "title": title,
                "category": category,
                "source_url": source,
                "verified": True,
            })

        try:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            indexed += len(batch)
        except Exception as e:
            print(f"  Batch error at {i}-{i+len(batch)}: {e}")
            continue

        # 进度显示
        if indexed % CHECKPOINT_INTERVAL == 0 or indexed >= total:
            elapsed = time.time() - t0
            rate = (indexed - start_idx) / max(elapsed, 1)
            remaining = (total - indexed) / max(rate, 0.01)
            print(
                f"  {indexed}/{total} ({100*indexed/total:.1f}%) | "
                f"{rate:.0f} docs/s | ETA: {remaining/60:.0f}min"
            )

    elapsed = time.time() - t0
    print(f"\nDone: {indexed} entries in {elapsed/60:.1f}min ({indexed/elapsed:.0f} docs/s)")

    # 验证
    report = cm.validate()
    print(f"Validation: {report}")
    if not report.valid:
        print(f"WARNING: {report.errors}")

    return indexed


def cmd_check(settings):
    """仅验证集合状态"""
    cm = CollectionManager(
        str(settings.vectordb_dir),
        settings.embedding_collection,
        settings.embedding_dimension,
    )
    report = cm.validate()
    print("=" * 50)
    print(f"Collection: {report.collection_name}")
    print(f"  Exists: {report.exists}")
    print(f"  Docs: {report.doc_count}")
    print(f"  Dimension: {report.dimension}")
    print(f"  Valid: {report.valid}")
    if report.errors:
        print(f"  Errors: {report.errors}")
    print("=" * 50)

    # 也列出所有集合
    print("\nAll collections:")
    for info in cm.list_collections():
        print(f"  {info['name']}: {info['count']} docs")


def cmd_build(settings):
    """全量重建"""
    # 加载数据
    print("Loading staging entries...")
    entries = load_entries(STAGING_DIR)
    print(f"Total entries loaded: {len(entries)}")
    if not entries:
        print("No entries found. Abort.")
        sys.exit(1)

    # 加载 Embedder
    print(f"Loading embedder: {settings.embedding_model}")
    from src.rag.embedder import Embedder
    embedder = Embedder(model_name=settings.embedding_model)
    if not embedder.load():
        print("FATAL: Cannot load embedder. Check network and model name.")
        sys.exit(1)
    print(f"Embedder loaded: dim={embedder.dimension}")

    # 验证维度
    if embedder.dimension != settings.embedding_dimension:
        print(
            f"FATAL: Dimension mismatch. model={embedder.dimension}, "
            f"config={settings.embedding_dimension}"
        )
        sys.exit(1)

    # 构建索引
    build_index(entries, settings.embedding_collection, settings, embedder)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild vector index with BGE-M3")
    parser.add_argument("--check", action="store_true", help="Only validate, don't rebuild")
    args = parser.parse_args()

    settings = load_settings()
    if args.check:
        cmd_check(settings)
    else:
        cmd_build(settings)
```

- [ ] **Step 2: 本地验证脚本可运行（空跑）**

```bash
cd /home/a/fortune-agent
python3 -c "
# 仅验证 import 不报错
import ast
with open('scripts/rebuild_index_v2.py') as f:
    ast.parse(f.read())
print('Syntax OK')
"
```

- [ ] **Step 3: 在服务器先 dry-run 验证**

```bash
ssh root@124.221.233.214
cd /opt/fortune-agent
python3 scripts/rebuild_index_v2.py --check
# 预期: 显示现有 fortune_books 状态 + fortune_books_v4_bge_m3 尚不存在
```

- [ ] **Step 4: Commit**

```bash
git add scripts/rebuild_index_v2.py
git commit -m "feat(rag): add rebuild_index_v2 with BGE-M3, checkpoint resume, and validation"
```

---

### Task 8: 部署到服务器 + 执行重建

**说明**: 这一步是关键——将代码部署到服务器，用 BGE-M3 重建索引，然后切换集合。需要约 2-4 小时（取决于 CPU）。

- [ ] **Step 1: 部署代码到服务器**

```bash
# 同步新文件和修改的文件
scp src/rag/embedder_v2.py root@124.221.233.214:/opt/fortune-agent/src/rag/
scp src/rag/collection_manager.py root@124.221.233.214:/opt/fortune-agent/src/rag/
scp src/rag/embedder.py root@124.221.233.214:/opt/fortune-agent/src/rag/
scp src/rag/retriever.py root@124.221.233.214:/opt/fortune-agent/src/rag/
scp src/config.py root@124.221.233.214:/opt/fortune-agent/src/
scp src/main.py root@124.221.233.214:/opt/fortune-agent/src/
scp scripts/rebuild_index_v2.py root@124.221.233.214:/opt/fortune-agent/scripts/
scp scripts/export_onnx.py root@124.221.233.214:/opt/fortune-agent/scripts/
```

- [ ] **Step 2: 在服务器上安装依赖**

```bash
ssh root@124.221.233.214
cd /opt/fortune-agent
source .venv/bin/activate
pip install optimum[onnxruntime] onnx onnxruntime 2>&1 | tail -5
```

- [ ] **Step 3: 下载 BGE-M3 模型（先跑，不重建索引）**

```bash
ssh root@124.221.233.214
cd /opt/fortune-agent
source .venv/bin/activate
# 只下载模型，测试维度
python3 -c "
from src.rag.embedder import Embedder
e = Embedder(model_name='BAAI/bge-m3')
print(f'Expected dim: {e.dimension}')
print(f'Loading model...')
ok = e.load()
print(f'Loaded: {ok}')
if ok:
    v = e.encode_single('测试八字命理')
    print(f'Actual dim: {v.shape[0]}')  # 必须是 1024
"
```

Expected: `Expected dim: 1024`, `Loaded: True`, `Actual dim: 1024`

- [ ] **Step 4: 导出 ONNX 量化模型（节省后续内存）**

```bash
ssh root@124.221.233.214
cd /opt/fortune-agent
source .venv/bin/activate
systemctl stop cow-fortune  # 释放内存
python3 scripts/export_onnx.py BAAI/bge-m3 /mnt/d/fortune-models/bge-m3-onnx
systemctl start cow-fortune
# 检查导出大小
du -sh /mnt/d/fortune-models/bge-m3-onnx/
```

- [ ] **Step 5: 重建向量索引**

```bash
ssh root@124.221.233.214
cd /opt/fortune-agent
source .venv/bin/activate
# 后台执行重建（使用 nohup，防止终端断开）
nohup python3 scripts/rebuild_index_v2.py > /tmp/rebuild_v2.log 2>&1 &
echo "Rebuild PID: $!"

# 监控进度
tail -f /tmp/rebuild_v2.log
```

- [ ] **Step 6: 验证重建结果**

```bash
ssh root@124.221.233.214
# 检查日志最后几行
tail -20 /tmp/rebuild_v2.log

# 验证集合
cd /opt/fortune-agent
source .venv/bin/activate
python3 scripts/rebuild_index_v2.py --check
```

Expected output:
```
Collection: fortune_books_v4_bge_m3
  Exists: True
  Docs: 27115
  Valid: True
```

- [ ] **Step 7: 切换服务使用新集合**

```bash
ssh root@124.221.233.214
# 更新配置为新集合
cd /opt/fortune-agent
# 确认 settings.yaml 中 collection 是 fortune_books_v4_bge_m3
grep 'embedding_collection' config/settings.yaml 2>/dev/null || echo "Using env override"

# 用环境变量方式切换（不改配置文件，方便回滚）
# systemd override
mkdir -p /etc/systemd/system/fortune-agent.service.d/
cat > /etc/systemd/system/fortune-agent.service.d/collection.conf <<'EOF'
[Service]
Environment="EMBEDDING_COLLECTION=fortune_books_v4_bge_m3"
Environment="EMBEDDING_DIMENSION=1024"
EOF

systemctl daemon-reload
systemctl restart fortune-agent
sleep 3
systemctl status fortune-agent --no-pager | head -10
```

- [ ] **Step 8: 验证服务健康 + RAG 查询**

```bash
# 健康检查
curl -s http://124.221.233.214:8765/api/health | python3 -m json.tool

# RAG 查询测试
curl -s -X POST http://124.221.233.214:8765/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"八字财运分析","user_id":"test_rag_v4"}' | python3 -m json.tool | head -20
```

Expected: 健康检查返回 OK，RAG 查询不报维度错误

- [ ] **Step 9: 提交部署记录**

```bash
# 在本地记录
echo "2026-07-19: Deployed BGE-M3 v4 index (27,115 docs, 1024-dim) to 124.221.233.214" >> /home/a/fortune-agent/DEPLOY.md
```

---

### Task 9: 更新测试覆盖

**Files:**
- Modify: `tests/test_rag.py`

- [ ] **Step 1: 追加维度一致性测试**

```python
# 追加到 tests/test_rag.py 末尾

def test_embedder_v2_deterministic():
    """EmbedderV2 必须是确定性的: 同一 model_name 产生同一维度"""
    from src.rag.embedder_v2 import EmbedderV2

    e = EmbedderV2(model_name="BAAI/bge-m3")
    # 未加载时的预期维度
    assert e.dimension == 1024, f"Expected 1024, got {e.dimension}"


def test_embedder_v2_rejects_empty_model_name():
    """EmbedderV2 拒绝空 model_name"""
    import pytest
    from src.rag.embedder_v2 import EmbedderV2

    with pytest.raises(ValueError, match="model_name"):
        EmbedderV2(model_name="")


def test_embedder_v2_rejects_encode_before_load():
    """EmbedderV2 在加载前调用 encode 应报错"""
    import pytest
    from src.rag.embedder_v2 import EmbedderV2

    e = EmbedderV2(model_name="BAAI/bge-m3")
    with pytest.raises(RuntimeError, match="not loaded"):
        e.encode_single("test")


def test_collection_manager_validation():
    """CollectionManager 对不存在集合返回 valid=False"""
    from src.rag.collection_manager import CollectionManager

    cm = CollectionManager("/tmp", "nonexistent_collection_test", 1024)
    report = cm.validate()
    assert report.exists is False
    assert report.valid is False
    assert len(report.errors) > 0


def test_known_model_dimensions():
    """所有已知模型的维度映射正确"""
    from src.rag.embedder_v2 import EmbedderV2, _MODEL_DIMENSIONS

    for model_name, expected_dim in _MODEL_DIMENSIONS.items():
        e = EmbedderV2(model_name=model_name)
        assert e.dimension == expected_dim, f"{model_name}: expected {expected_dim}, got {e.dimension}"
```

- [ ] **Step 2: 运行测试**

```bash
cd /home/a/fortune-agent
SKIP_EMBEDDING_TEST=1 python3 -m pytest tests/test_rag.py -v
```

Expected: 所有新测试 PASS（test_embedder_v2_deterministic, test_embedder_v2_rejects_*, test_collection_manager_validation, test_known_model_dimensions）

- [ ] **Step 3: Commit**

```bash
git add tests/test_rag.py
git commit -m "test(rag): add dimension determinism and validation tests"
```

---

### Task 10: 更新项目文档 + 内存

**Files:**
- Modify: `docs/superpowers/specs/2026-07-19-rag-architecture-v4-design.md` (添加 Phase 1 完成状态)
- Modify: `/home/a/.claude/projects/-home-a/memory/project_fortune_agent.md` (更新状态)

- [ ] **Step 1: 更新设计文档添加完成标记**

在 Phase 1 部分添加 `(completed YYYY-MM-DD)` 注释

- [ ] **Step 2: 更新 memory**

更新 `/home/a/.claude/projects/-home-a/memory/project_fortune_agent.md`:
- 向量库状态: fortune_books_v4_bge_m3 (BGE-M3, 1024-dim, 27,115 docs)
- Embedder 已修复为确定性加载
- 旧集合 fortune_books (768-dim) 保留但不再使用

- [ ] **Step 3: Commit**

```bash
git add docs/ memory/
git commit -m "docs: update project state after Phase 1 completion"
```

---

## Phase 2-5 概要

Phase 2-5 依赖 GPU 服务器，以下为任务清单（具体 step-by-step 计划在各 Phase 启动前细化）:

### Phase 2: Query Enhancement + Reranking (预计 2 周)

| 任务 | 文件 | 说明 |
|------|------|------|
| Query Rewriter | `src/rag/query_enhancer.py` | LLM-based query rewriting + multi-query decomposition |
| BGE-Reranker 集成 | `src/rag/reranker.py` | Cross-encoder reranking on Top-20 |
| RRF Fusion | `src/rag/fusion.py` | Dense + Sparse RRF merge |
| Pipeline 串联 | `src/rag/pipeline.py` | Wire up: enhance → retrieve → fuse → rerank → return |
| 部署验证 | 服务器 | 端到端 10 条 query 人工评估 |

### Phase 3: Knowledge Graph + GraphRAG (预计 4 周)

| 任务 | 文件 | 说明 |
|------|------|------|
| 实体抽取 | `src/kg/entity_extractor.py` | LLM 抽取天干/地支/十神/格局等实体 |
| 关系抽取 | `src/kg/relation_extractor.py` | 生克合冲刑害 + 古籍引用关系 |
| 图谱存储 | `src/kg/graph_store.py` | SQLite 存储，轻量查询 API |
| 社区发现 | `src/kg/community.py` | Leiden 算法聚类 + LLM 摘要生成 |
| GraphRAG 检索 | `src/kg/retriever.py` | 全局摘要 + 局部遍历 + 命例相似度 |
| 部署验证 | 服务器 | 知识图谱上线，复杂问题全局分析测试 |

### Phase 4: Self-RAG Reflection (预计 2 周)

| 任务 | 文件 | 说明 |
|------|------|------|
| Reflection 逻辑 | `src/rag/reflection.py` | LLM 评估检索质量，建议补搜 |
| 补搜策略 | `src/rag/补搜索略.py` | 按维度(理论/案例/化解)自动补搜 |
| Pipeline 集成 | `src/rag/pipeline.py` | 加入反思回路，2轮限制 |
| 部署验证 | 服务器 | 检索覆盖率提升验证 |

### Phase 5: Evaluation + Fine-tuning (预计 3 周)

| 任务 | 文件 | 说明 |
|------|------|------|
| 基准数据集 | `data/eval/golden_200.jsonl` | 人工标注 200 条 query→docs |
| 自动评估 | `scripts/eval_rag.py` | NDCG@5, MRR, Recall@20 自动计算 |
| LoRA 微调 | `scripts/finetune_embedding.py` | 对比学习，基于 44k 命盘 + 27k 古籍 |
| A/B 测试框架 | `src/rag/ab_test.py` | 新模型 vs 旧模型并行对比 |
| 部署验证 | 服务器 | 首个微调模型上线，A/B 测试运行 |
