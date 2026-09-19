# -*- coding: utf-8 -*-
"""k59 写路径护栏（数据安全）：空集合自愈不得改写**写入目标**。

来源：k55 险情（2026-09-14）—— `Retriever(生产 persist_dir)` + 一个**尚不存在
的集合名**去写入，自愈把写目标改写成权威集合 `fortune_books_v2` → **静默写进
生产检索库**（审查者在副本上复现：27,115 → 27,116，无任何报错）。k55 只在脚本
侧绕过，根因未修。

本文件锁住 k59 修后的四条：
1. **危险路径被拦**：生产形态目录 + 缺失的集合名 → 写入绝不落到权威集合；
   若实例已被读路径自愈改写 → 抛 `SelfHealWriteRefused`（改前：静默 +1 条）；
2. **读路径行为不变**：集合缺失/为空 → 仍回落权威库（且 warning + 事件可查）；
3. **正常写入不受影响**：显式集合名照写、新建集合、重复写幂等、脚本式
   `_collection_name` 显式改名的用法照旧；
4. **写路径不再触发自愈**：写操作不置 `_collection_checked`、不产生自愈事件。

r2 追加（残留口子收口）：**绕过写 API 直写读属性**这条复发面 ——
`retriever.collection` 返回只读包装，写方法抛 `ReadPropertyWriteRefused`，
读方法逐字透传（见 `test_k59_r2_*`）；仓库面的静态守卫另见
`tests/test_k59_collection_write_scan.py`。

隔离：全部在 `tmp_path`（本批跑测时 `TMPDIR=/dev/shm`）里用**真实 chroma**，
零网络、零模型（桩 embedder 产定长向量）；不读也不写生产库 —— 生产形态用
「同目录放一个权威集合 + 一个缺失的集合名」在沙箱里等价构造（见 `prod_like`）。

生产库副本重放（可选）：设 `K59_PROD_REPLICA=<副本目录>` 后，最后一个用例会在
副本上跑同一条路径并核对权威集合指纹。
"""
import hashlib
import logging
import os
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
    Retriever,
    ReadPropertyWriteRefused,
    SelfHealWriteRefused,
)

# 沙箱里的向量维度：小尺寸即可（机制与维度无关，1024 维只拖慢测试）
DIM = 8


class _StubEmbedder:
    """桩 embedder：定长确定性向量（零模型、零网络、可复现）。

    维度按需指定（副本重放用 1024，与生产语料一致；其余用 DIM）。
    """

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
    """在沙箱目录里预置一个集合（= 「生产库已有数据」的等价构造）。

    直接用 chroma 客户端 + `_AppEmbeddingFunction`（与生产集合一致：持久化了
    app 的 embedding function，否则 Retriever 再打开会报 EF 冲突）——不走
    `Retriever` 的写路径：预置是「既有生产状态」，应与被测写路径解耦，也让同
    一个文件能在改前代码上跑出「改前失败」证据。
    """
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
    """集合条数（不存在 = 0，不触发自愈）。"""
    try:
        return _client(path).get_collection(name, embedding_function=None).count()
    except Exception:
        return 0


def _fingerprint(path, name: str):
    """集合指纹：(条数, id 列表 MD5, id+文档 MD5)。改前/改后对照用。"""
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


def _chunks(n: int, prefix: str = "k59"):
    return [
        Chunk(
            text=f"k59 护栏用例文本 {prefix}-{i}：解梦语料占位正文。",
            source="k59",
            author="k59",
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


# ================================================================
# 1. 改前失败夹具：k55 险情路径（生产形态目录 + 缺失集合名）
# ================================================================

def test_k59_incident_fixture_write_never_lands_in_authoritative(prod_like):
    """改前失败夹具：缺失集合名 + 同目录权威集合有数据 → 写入**不得**落到权威集合。

    改前：`add_chunks` 经 `collection` 属性继承自愈 → 权威集合 5 → 6（静默污染，
    完全无报错）→ 本用例的硬断言必然失败。
    改后：抛 `SelfHealWriteRefused`（拒绝）或落到**显式集合名**本身；两种都满足
    「权威集合零变化」，本用例通过。
    """
    before = _fingerprint(prod_like, BOOKS_COLLECTION)
    assert before[0] == 5

    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_dreams_new"
    )
    refused = None
    try:
        retriever.add_chunks(_chunks(2, "incident"))
    except SelfHealWriteRefused as e:
        refused = e

    after = _fingerprint(prod_like, BOOKS_COLLECTION)
    assert after == before, "权威集合必须纹丝不动（改前：静默 +1 条污染）"
    if refused is not None:
        # 拒绝路径：哪都没写
        assert _count(prod_like, "k59_dreams_new") == 0
        assert refused.requested == "k59_dreams_new"
    else:
        # 显式集合名路径：写落到调用方指定的集合本身，绝不落到权威集合
        assert _count(prod_like, "k59_dreams_new") == 2
        assert retriever.self_healed_to is None


def test_k59_incident_fixture_on_prod_replica():
    """在**生产库副本**上重放同一条路径（需 K59_PROD_REPLICA）。

    副本是生产的拷贝：改前该写会把副本权威集合 27,115 → 27,116；改后必须零变化。
    """
    replica = os.environ.get("K59_PROD_REPLICA")
    if not replica:
        pytest.skip("需要 K59_PROD_REPLICA=/dev/shm 上的生产库副本")
    assert Path(replica).exists()

    def _sqlite_fp():
        import sqlite3

        con = sqlite3.connect(
            f"file:{Path(replica) / 'chroma.sqlite3'}?mode=ro", uri=True
        )
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT e.embedding_id, m.key, m.string_value, m.int_value, "
                "m.float_value, m.bool_value FROM embedding_metadata m "
                "JOIN embeddings e ON e.id = m.id JOIN segments s ON "
                "s.id = e.segment_id JOIN collections c ON c.id = s.collection "
                "WHERE c.name = ?", (BOOKS_COLLECTION,),
            )
            rows = sorted(cur.fetchall(), key=lambda r: (r[0], r[1]))
            blob = "\n".join(
                "\t".join("" if v is None else str(v) for v in r) for r in rows
            )
            return len({r[0] for r in rows}), hashlib.md5(blob.encode("utf-8")).hexdigest()
        finally:
            con.close()

    before = _sqlite_fp()
    assert before[0] > 0, "副本里必须有数据"

    retriever = Retriever(
        str(replica), _StubEmbedder(dim=1024), collection_name="k59_replica_new"
    )
    try:
        retriever.add_chunks(_chunks(1, "replica"))
    except SelfHealWriteRefused as e:
        assert e.healed_to == BOOKS_COLLECTION

    assert _sqlite_fp() == before, "副本权威集合必须与改前指纹完全一致"


# ================================================================
# 2. 已自愈实例的写入必须被拒绝（严格抛错路径）
# ================================================================

def test_k59_write_after_read_self_heal_is_refused(prod_like):
    """读路径先自愈（配置集合名不存在）→ 此后写入必须抛错并说清来由。

    改前：写进权威集合（静默污染）。改后：`SelfHealWriteRefused`，且已自愈的
    集合名与实际写目标都不被触碰。
    """
    before = _fingerprint(prod_like, BOOKS_COLLECTION)
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_missing_coll"
    )
    # 读：集合不存在 → 自愈回落权威库（读路径保形）
    assert retriever.count() == 5
    assert retriever.self_healed_to == BOOKS_COLLECTION
    assert retriever.collection_name == BOOKS_COLLECTION

    with pytest.raises(SelfHealWriteRefused) as ei:
        retriever.add_chunks(_chunks(1, "guarded"))
    assert ei.value.requested == "k59_missing_coll"
    assert ei.value.healed_to == BOOKS_COLLECTION
    assert str(prod_like) in str(ei.value)
    assert isinstance(ei.value, RuntimeError)

    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before
    assert _count(prod_like, "k59_missing_coll") == 0


def test_k59_write_after_heal_refused_even_for_known_empty_collection(prod_like):
    """配置指向历史空集合（KNOWN_EMPTY_COLLECTIONS）→ 读自愈后写入同样被拒。

    这正是 k24 的配置踩空形态（settings 写 `fortune_v6`）：旧行为把写入静默
    送到权威库，改后必须拒绝。
    """
    before = _fingerprint(prod_like, BOOKS_COLLECTION)
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="fortune_v6"
    )
    assert retriever.count() == 5  # 读自愈（已知空集合 → 权威库）
    with pytest.raises(SelfHealWriteRefused):
        retriever.add_chunks(_chunks(1, "known_empty"))
    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before
    assert _count(prod_like, "fortune_v6") == 0


# ================================================================
# 3. 读路径保形（集合缺失 → 回落权威库；warning + 事件可查）
# ================================================================

def test_k59_read_path_self_heal_unchanged(prod_like, caplog):
    """读路径行为不变：集合缺失/为空 → 仍回落权威库，检索可用、事件可查。"""
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_missing_read"
    )
    with caplog.at_level(logging.WARNING, logger="src.rag.retriever"):
        results = retriever.search("doc-1", top_k=5, min_score=0.0)

    assert retriever.collection_name == BOOKS_COLLECTION
    assert retriever.self_healed_to == BOOKS_COLLECTION
    assert len(results) >= 1, "回落之后检索必须可用（旧行为：refs=0 断链）"
    assert any(
        rec.levelno >= logging.WARNING and "k59_missing_read" in rec.getMessage()
        for rec in caplog.records
    ), "自愈必须留 warning（绝不静默）"
    evt = rt.self_heal_events()["k59_missing_read"]
    assert evt["healed_to"] == BOOKS_COLLECTION and evt["count"] == 1


def test_k59_empty_but_existing_collection_still_heals_on_read(prod_like):
    """存在但为空的集合：读路径仍自愈到权威库（k24 设计，k59 未改）。"""
    _client(prod_like).get_or_create_collection(
        name="k59_empty_coll", metadata={"hnsw:space": "cosine"}
    )
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_empty_coll"
    )
    assert retriever.count() == 5
    assert retriever.self_healed_to == BOOKS_COLLECTION
    assert rt.self_heal_events()["k59_empty_coll"]["last_reason"] == "count()==0"


# ================================================================
# 4. 正常写入不受影响
# ================================================================

def test_k59_normal_write_to_explicit_collection(prod_like):
    """显式集合名 + 正常写入：条数、绑定名、无自愈改写。"""
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_normal"
    )
    assert retriever.writable_collection.name == "k59_normal"
    retriever.add_chunks(_chunks(3, "normal"))

    assert _count(prod_like, "k59_normal") == 3
    assert retriever.self_healed_to is None
    assert _fingerprint(prod_like, BOOKS_COLLECTION)[0] == 5


def test_k59_new_collection_write_warns_but_keeps_explicit_target(prod_like, caplog):
    """高危形态（全新集合名 + 同目录权威集合有数据）：落显式集合 + warning 留痕。"""
    before = _fingerprint(prod_like, BOOKS_COLLECTION)
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_brand_new"
    )
    with caplog.at_level(logging.WARNING, logger="src.rag.retriever"):
        retriever.add_chunks(_chunks(2, "brandnew"))

    assert any(
        "写入目标集合" in rec.getMessage() for rec in caplog.records
    ), "高危形态必须留痕（绝不静默）"
    assert _count(prod_like, "k59_brand_new") == 2
    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before
    assert retriever.self_healed_to is None
    # 只为该集合名提示一次（重复写不再刷）
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="src.rag.retriever"):
        retriever.add_chunks(_chunks(2, "brandnew"))
    assert not [r for r in caplog.records if "写入目标集合" in r.getMessage()]


def test_k59_write_does_not_trigger_self_heal_check(prod_like):
    """写路径不触发自愈检测：`_collection_checked` 保持 False、无自愈事件。"""
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_no_check"
    )
    retriever.add_chunks(_chunks(1, "nocheck"))
    assert retriever._collection_checked is False
    assert retriever.self_healed_to is None
    assert rt.self_heal_events() == {}


def test_k59_repeat_write_is_idempotent(prod_like):
    """重复写入幂等（upsert 同 id）：指纹前后一致，权威集合不受影响。"""
    retriever = Retriever(str(prod_like), _StubEmbedder(), collection_name="k59_idem")
    chunks = _chunks(4, "idem")
    retriever.add_chunks(chunks)
    first = _fingerprint(prod_like, "k59_idem")
    retriever.add_chunks(chunks)
    second = _fingerprint(prod_like, "k59_idem")

    assert first == second and first[0] == 4
    assert _fingerprint(prod_like, BOOKS_COLLECTION)[0] == 5


def test_k59_explicit_name_reassign_binds_write_target(prod_like):
    """既有脚本用法：构造后显式改 `_collection_name`（`--collection X`）→ 写落到新名。"""
    before = _fingerprint(prod_like, BOOKS_COLLECTION)
    retriever = Retriever(str(prod_like), _StubEmbedder())  # 默认 = 权威集合名
    retriever._collection_name = "k59_reassigned"  # noqa: SLF001（脚本既有用法）
    retriever.add_chunks(_chunks(2, "reassign"))

    assert _count(prod_like, "k59_reassigned") == 2
    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before


def test_k59_heal_failure_does_not_block_writes(tmp_path):
    """自愈失败（权威库也不存在）→ 集合名未被改写 → 写入照旧落到显式集合。"""
    retriever = Retriever(str(tmp_path), _StubEmbedder(), collection_name="k59_solo")
    assert retriever.count() == 0  # 读：自愈失败（权威库不可用）
    assert retriever.self_healed_to is None

    retriever.add_chunks(_chunks(2, "solo"))
    assert _count(tmp_path, "k59_solo") == 2
    assert rt.self_heal_events()["k59_solo"]["healed_to"] is None


class _InjectedCollection:
    """最小集合替身（k26 那类「注入 fake 集合」的既有用法）。"""

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


def test_k59_read_path_keeps_injected_collection_handle(prod_like):
    """读路径句柄语义与 k59 前一致：外部注入 `_collection` 后读**不得**换成新句柄。

    （k59 第一版曾按集合名重绑句柄缓存，直接打破 k26 的 fake 注入用法 —— 回归
    用例锁死：写路径自己取句柄、绝不改写读路径的 `_collection`。）

    r2：读属性返回值现在是只读包装，故断言「读走的是注入的 fake」（而非对象
    同一性）；r2 之前那条 `collection is fake` 断言随包装调整。
    """
    fake = _InjectedCollection()
    retriever = Retriever(str(prod_like), _StubEmbedder(), collection_name="k59_fake")
    retriever._collection_checked = True  # noqa: SLF001（既有测试注入用法）
    retriever._collection = fake  # noqa: SLF001

    assert retriever._collection is fake, "读路径不得替换外部注入的句柄"
    assert retriever.collection.get()["ids"] == ["c1"], "读必须透传到注入的 fake"
    assert retriever.count() == 1
    assert len(retriever.search("正文", top_k=3, min_score=0.0)) == 1
    assert fake.queries >= 1


# ================================================================
# 5. r2：读属性只读包装（绕过写 API 直写读属性这条复发面）
# ================================================================

def test_k59_r2_write_through_read_property_is_refused(prod_like):
    """r2 核心：`retriever.collection.<写方法>(...)` 必须抛错，且什么都没写。

    改前/r2 前：直写读属性会被自愈改写 → 数据静默落进权威集合（副本实测
    27,115 → 27,116）。r2 后：库层拒绝（静态守卫另有仓库面兜底）。
    """
    before = _fingerprint(prod_like, BOOKS_COLLECTION)
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_r2_new"
    )
    for method in ("upsert", "add", "update", "delete", "modify"):
        with pytest.raises(ReadPropertyWriteRefused) as ei:
            getattr(retriever.collection, method)
        assert ei.value.method == method
        assert "writable_collection" in str(ei.value)
    # 真实调用形态（不是只取属性）
    with pytest.raises(ReadPropertyWriteRefused):
        retriever.collection.upsert(
            embeddings=_StubEmbedder().encode(["x"]).tolist(), documents=["x"], ids=["x"]
        )

    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before
    assert _count(prod_like, "k59_r2_new") == 0
    # 读属性自身仍走读语义（本例里集合缺失 → 自愈改写，这是 k24 设计）；
    # 关键是：即便实例已被自愈，经读属性写入依然被拒、且哪都没写。
    assert retriever.self_healed_to == BOOKS_COLLECTION


def test_k59_r2_read_property_still_serves_reads(prod_like):
    """r2 不得改读语义：query/get/count/name 全透传，search/count 结果不变。"""
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name=BOOKS_COLLECTION
    )
    col = retriever.collection
    assert col.name == BOOKS_COLLECTION
    assert col.count() == 5
    assert sorted(col.get(include=["documents"])["ids"]) == [f"books_{i:03d}" for i in range(5)]
    assert col.query(query_embeddings=_StubEmbedder().encode(["doc-1"]).tolist(),
                     n_results=3, include=["documents"])["ids"][0]
    assert retriever.count() == 5
    assert len(retriever.search("doc-1", top_k=3, min_score=0.0)) >= 1


def test_k59_r2_writable_collection_is_not_wrapped(prod_like):
    """写访问器不受包装影响：`writable_collection.upsert(...)` 正常写入。"""
    before = _fingerprint(prod_like, BOOKS_COLLECTION)
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_r2_direct"
    )
    stub = _StubEmbedder()
    col = retriever.writable_collection
    col.upsert(embeddings=stub.encode(["a", "b"]).tolist(),
               documents=["a", "b"], ids=["k59_r2_a", "k59_r2_b"])

    assert col.upsert.__self__ is not None  # 裸 chroma 句柄（非包装）
    assert _count(prod_like, "k59_r2_direct") == 2
    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before


def test_k59_r2_read_property_refuses_write_even_after_self_heal(prod_like):
    """已自愈实例：读属性写入同样被拒（且是包装层的拒，不落到写路径判据）。"""
    before = _fingerprint(prod_like, BOOKS_COLLECTION)
    retriever = Retriever(
        str(prod_like), _StubEmbedder(), collection_name="k59_r2_missing"
    )
    assert retriever.count() == 5  # 读自愈
    assert retriever.collection_name == BOOKS_COLLECTION
    with pytest.raises(ReadPropertyWriteRefused):
        retriever.collection.upsert(embeddings=[[0.0] * DIM], documents=["x"], ids=["x"])
    with pytest.raises(SelfHealWriteRefused):
        retriever.add_chunks(_chunks(1, "r2_healed"))
    assert _fingerprint(prod_like, BOOKS_COLLECTION) == before

