# -*- coding: utf-8 -*-
"""k61：`Retriever._collection` 私有逃生口收口（k59 r2 披露的残留）。

## 残留形态（k59 r2 已披露、未修）

k59 修掉了两条写路径（写 API 不复用自愈结果 / 读属性返回只读包装），但
`collection` 的**句柄缓存载体** `_collection` 存的仍是**裸 chroma 句柄**：

    r = Retriever(生产 persist_dir, embedder, collection_name="新集合名")
    r.collection                     # 读路径自愈 → _collection 指向权威库
    r._collection.upsert(...)        # ← 私有直连：写进权威生产集合，绕过护栏

同一个实例、同一份数据，形态与 k55 险情完全一致（27,115 → 27,116），只是
入口从 `collection` 换成了 `_collection`。

## k61 处置

`collection` 缓存进 `_collection` 的改为**只读包装** `_ReadOnlyCollection`：
库层自己开的句柄从此写不进去（读方法 query/get/count/… 逐字透传）。
合法写入口**不变**：`writable_collection` / `add_chunks`（新建实例 + 显式
集合名，k60 的 `dreams_k55` 入库走这条）。

边界（如实披露）：**外部注入**的 `_collection`（k26/k59 的 fake 注入用法）
保持原样返回（`retriever._collection is fake` 依赖它）；注入真实 chroma 句柄
再直写属显式篡改私有属性，不在本护栏承诺面内。

隔离：全部在 `tmp_path`（跑测时 `TMPDIR=/dev/shm`）里用**真实 chroma**，
零网络、零模型（桩 embedder 产定长向量）；不读也不写生产库。
"""
import hashlib
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import chromadb  # noqa: E402
from chromadb.config import Settings as ChromaSettings  # noqa: E402

import src.rag.retriever as rt  # noqa: E402
from src.book_categories import BOOKS_COLLECTION  # noqa: E402
from src.rag.chunker import Chunk  # noqa: E402
from src.rag.retriever import (  # noqa: E402
    ReadPropertyWriteRefused,
    Retriever,
    _ReadOnlyCollection,
)

DIM = 8
K60_COLLECTION = "dreams_k55"  # k60 批次的独立集合名（本次不碰它，只验证路通）


class _StubEmbedder:
    """桩 embedder：定长确定性向量（零模型、零网络、可复现）。"""

    def __init__(self, dim: int = DIM):
        self.dimension = dim

    def encode(self, texts):
        out = []
        for t in texts:
            digest = hashlib.sha256(str(t).encode("utf-8")).digest()
            vec = [b / 255.0 for b in (digest * (self.dimension // 32 + 1))[: self.dimension]]
            out.append(vec)
        return np.array(out, dtype="float32")

    def load(self):
        return True


def _client(path) -> chromadb.PersistentClient:
    return chromadb.PersistentClient(
        path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
    )


def _seed(path, name: str, ids):
    """预置一个集合（=「生产库已有数据」的等价构造，与被测写路径解耦）。"""
    stub = _StubEmbedder()
    col = _client(path).get_or_create_collection(
        name=name,
        embedding_function=rt._AppEmbeddingFunction(stub),
        metadata={"hnsw:space": "cosine"},
    )
    docs = [f"doc-{i}" for i in ids]
    col.upsert(ids=list(ids), documents=docs, embeddings=stub.encode(docs).tolist())
    return col


def _count(path, name: str) -> int:
    try:
        return _client(path).get_collection(name, embedding_function=None).count()
    except Exception:
        return 0


def _fingerprint(path, name: str):
    """集合指纹：(条数, id 列表 MD5, id+文档 MD5)。"""
    try:
        col = _client(path).get_collection(name, embedding_function=None)
    except Exception:
        return (0, "", "")
    got = col.get(include=["documents"])
    docs = dict(zip(got["ids"], got.get("documents") or [""] * len(got["ids"])))
    ids = sorted(got["ids"])
    id_md5 = hashlib.md5("\n".join(ids).encode("utf-8")).hexdigest()
    body_md5 = hashlib.md5(
        "\n".join(f"{i}\t{docs.get(i, '')}" for i in ids).encode("utf-8")
    ).hexdigest()
    return (len(ids), id_md5, body_md5)


def _chunks(n: int, prefix: str = "k61"):
    return [
        Chunk(
            text=f"k61 护栏用例文本 {prefix}-{i}：解梦语料占位正文。",
            source="k61",
            author="k61",
            category="dream",
            chunk_id=f"{prefix}_{i:03d}",
        )
        for i in range(n)
    ]


@pytest.fixture()
def prod_like(tmp_path):
    """生产形态沙箱：目录里已有权威集合（有数据），其余集合名都不存在。"""
    _seed(tmp_path, BOOKS_COLLECTION, [f"books_{i:03d}" for i in range(5)])
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_self_heal_events(monkeypatch):
    """自愈事件表是进程级状态：每个用例用独立登记表，防跨用例串扰。"""
    monkeypatch.setattr(rt, "_SELF_HEAL_EVENTS", {})


def _healed_retriever(prod_like, name="k61_new", embedder=None):
    """走完一次读路径（触发自愈改写 + 建立句柄缓存）的实例 —— 残留口子的前置状态。

    与残留形态的真实触发序列一致：`r.collection`（读属性 = 自愈生效点，
    同时把句柄写进 `_collection` 缓存）→ 此后 `_collection` 就是那次缓存。
    """
    r = Retriever(str(prod_like), embedder or _StubEmbedder(), collection_name=name)
    col = r.collection  # 读路径：自愈改写 + 建缓存
    assert r.collection_name == BOOKS_COLLECTION, "前提：读路径必须已自愈改写"
    assert r.self_healed_to == BOOKS_COLLECTION
    assert col.count() == 5, "前提：自愈后的句柄指向权威库（5 条）"
    return r


# ================================================================
# 1. 私有逃生口已收口（本批核心）
# ================================================================

def test_k61_private_handle_write_refused_and_nothing_written(prod_like):
    """`r._collection.upsert(...)` 必须被拒，且**零写入**（权威库指纹不变）。

    改前：`_collection` 是裸 chroma 句柄且已指向权威库 → upsert 静默落进
    `BOOKS_COLLECTION`（本用例的指纹断言会红，这正是 k61 要关掉的口子）。
    改后：抛 `ReadPropertyWriteRefused`，两侧集合都不动。
    """
    r = _healed_retriever(prod_like)
    before_books = _fingerprint(prod_like, BOOKS_COLLECTION)
    assert before_books[0] == 5

    stub = _StubEmbedder()
    payload = dict(embeddings=stub.encode(["越权写入"]).tolist(),
                   documents=["越权写入"], ids=["k61_escape_001"])
    with pytest.raises(ReadPropertyWriteRefused) as ei:
        r._collection.upsert(**payload)  # noqa: SLF001 - 被测的私有直连形态
    assert ei.value.method == "upsert"
    assert "writable_collection" in str(ei.value)

    # 五个写方法名逐个拒（与运行时口径 COLLECTION_WRITE_METHODS 同源）
    for method in ("add", "update", "upsert", "delete", "modify"):
        with pytest.raises(ReadPropertyWriteRefused) as ei:
            getattr(r._collection, method)  # noqa: SLF001
        assert ei.value.method == method

    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before_books, \
        "权威集合被私有直连写脏了（k55 险情形态）"
    assert _count(prod_like, "k61_new") == 0


def test_k61_private_handle_is_read_only_wrapper(prod_like):
    """收口方式 = `_collection` 持有的就是只读包装（不是另加一层影子缓存）。"""
    r = _healed_retriever(prod_like)
    assert isinstance(r._collection, _ReadOnlyCollection), \
        "库层自己开的句柄必须以只读包装形态缓存，否则私有直连仍可写"


def test_k61_private_handle_reads_still_pass_through(prod_like):
    """读语义逐字不变：私有句柄上的读方法照常可用（护栏不得误伤读路径）。"""
    r = _healed_retriever(prod_like)
    assert r._collection.count() == 5  # noqa: SLF001 - 权威库 5 条
    got = r._collection.get()  # noqa: SLF001
    assert len(got["ids"]) == 5
    assert r.collection.count() == 5          # 读属性照旧
    assert r.collection.get()["ids"]         # 读属性照旧
    assert r.collection_name == BOOKS_COLLECTION


def test_k61_repeated_reads_keep_write_refusal(prod_like):
    """多次读不改变结论：第二次读复用同一包装，写拒依然生效（非一次性）。"""
    r = _healed_retriever(prod_like)
    first = r._collection  # noqa: SLF001
    second = r._collection  # noqa: SLF001
    assert first is second, "缓存语义：只有 _collection is None 时才取句柄"
    for handle in (first, second):
        with pytest.raises(ReadPropertyWriteRefused):
            handle.upsert(embeddings=[[0.0] * DIM], documents=["x"], ids=["x"])


def test_k61_normal_instance_private_handle_also_read_only(prod_like):
    """非自愈实例同样收口：私有句柄一并是只读包装（不是只护「已自愈」态）。"""
    r = Retriever(str(prod_like), _StubEmbedder(), collection_name=BOOKS_COLLECTION)
    r.collection  # 建缓存
    assert isinstance(r._collection, _ReadOnlyCollection)  # noqa: SLF001
    with pytest.raises(ReadPropertyWriteRefused):
        r._collection.delete(ids=["books_000"])  # noqa: SLF001
    assert _count(prod_like, BOOKS_COLLECTION) == 5


# ================================================================
# 2. 合法写入口必须完好（k60 的 dreams_k55 入库路径）
# ================================================================

def test_k61_k60_path_new_instance_new_collection_still_works(prod_like):
    """k60 入库形态（新建实例 + 显式集合名 + writable_collection）必须照常可用。

    与 k60 批次同一用法：**新建实例**（不带任何读路径历史）+ 显式集合名，
    数据落到该集合本身，**绝不**落到同目录的权威集合。
    """
    before_books = _fingerprint(prod_like, BOOKS_COLLECTION)
    r = Retriever(str(prod_like), _StubEmbedder(), collection_name=K60_COLLECTION)
    stub = _StubEmbedder()
    r.writable_collection.upsert(
        embeddings=stub.encode(["解梦语料 A", "解梦语料 B"]).tolist(),
        documents=["解梦语料 A", "解梦语料 B"],
        ids=["k60_001", "k60_002"],
    )
    assert _count(prod_like, K60_COLLECTION) == 2, "新集合必须真拿到数据"
    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before_books, \
        "权威集合不得被顺带写入"


def test_k61_k60_path_add_chunks_still_works(prod_like):
    """同一形态走 `add_chunks`（批量入库的常用入口）也必须照常可用。"""
    before_books = _fingerprint(prod_like, BOOKS_COLLECTION)
    r = Retriever(str(prod_like), _StubEmbedder(), collection_name=K60_COLLECTION)
    r.add_chunks(_chunks(3, prefix="k60"))
    assert _count(prod_like, K60_COLLECTION) == 3
    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before_books


def test_k61_writable_collection_still_refuses_self_healed_instance(prod_like):
    """合法入口的既有拒绝语义不变：已自愈实例上的写入仍被拒（k59 修的不是这条）。"""
    from src.rag.retriever import SelfHealWriteRefused
    r = _healed_retriever(prod_like)
    with pytest.raises(SelfHealWriteRefused):
        r.writable_collection
    assert _count(prod_like, "k61_new") == 0


# ================================================================
# 3. 边界：外部注入的私有句柄（k26/k59 既有用法）
# ================================================================

class _InjectedCollection:
    """最小只读集合替身（k26 那类「注入 fake 集合」的既有用法）。"""

    def __init__(self):
        self.queries = 0

    def count(self):
        return 1

    def query(self, **kw):
        self.queries += 1
        return {"ids": [["c1"]], "distances": [[0.5]],
                "metadatas": [[{"category": "x"}]], "documents": [["正文"]]}

    def get(self, **kw):
        return {"ids": ["c1"], "metadatas": [{"category": "x"}], "documents": ["正文"]}


def test_k61_injected_handle_identity_preserved(prod_like):
    """契约不变：外部注入的 `_collection` 原样保留（k59 既有断言依赖它）。

    k61 只改「库层自己开的句柄」的缓存形态，不替换调用方注入的对象。
    """
    fake = _InjectedCollection()
    r = Retriever(str(prod_like), _StubEmbedder(), collection_name="k61_fake")
    r._collection_checked = True  # noqa: SLF001
    r._collection = fake  # noqa: SLF001
    assert r._collection is fake, "k61 不得替换外部注入的句柄"
    assert r.collection.get()["ids"] == ["c1"], "读必须透传到注入的 fake"
    assert r.count() == 1


def test_k61_documented_boundary_injected_real_handle(prod_like):
    """如实披露的边界：**注入真实 chroma 句柄**再直写不受护栏拦截。

    这是「显式篡改私有属性」，不是库层可达路径 —— 不写成期望行为，只把它
    钉成**已知边界**，避免报告里的承诺面被误读成「私有属性全面免疫」。
    """
    before = _fingerprint(prod_like, BOOKS_COLLECTION)
    r = Retriever(str(prod_like), _StubEmbedder(), collection_name="k61_inject")
    raw = _client(prod_like).get_collection(
        BOOKS_COLLECTION, embedding_function=rt._AppEmbeddingFunction(_StubEmbedder()))
    r._collection = raw  # noqa: SLF001 - 显式篡改，边界自证
    stub = _StubEmbedder()
    raw.upsert(embeddings=stub.encode(["注入直写"]).tolist(),
               documents=["注入直写"], ids=["k61_injected_001"])
    assert _fingerprint(prod_like, BOOKS_COLLECTION) != before, \
        "边界用例前提：注入的真实句柄确实能写（本用例证明边界存在，不是期望行为）"
    assert _count(prod_like, "k61_inject") == 0
