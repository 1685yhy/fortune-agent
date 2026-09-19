"""混合检索器 - 语义检索 + BM25关键词检索."""
import logging
import os
import threading
from datetime import datetime
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
import chromadb
from chromadb.config import Settings as ChromaSettings

from .embedder import Embedder
from .chunker import Chunk
from src.book_categories import (
    BOOKS_COLLECTION,
    KNOWN_EMPTY_COLLECTIONS,
    resolve_categories,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# k59（数据安全 / k55 险情根因）：空集合自愈**只属于读路径**。
#
# 事故形态（2026-09-14 k55 差点中招）：`Retriever(生产 persist_dir)` +
# 「一个尚不存在的集合名」（新语料集合 dreams_k55）去写入 → 写方法经
# `collection` 属性继承了读路径的自愈 → 集合名被改写成权威库
# `fortune_books_v2` → **静默写进生产检索库**（副本实测 27,115 → 27,116，
# 无任何报错）。实现者在 `add_chunks` 前发现并终止，生产未受影响。
#
# 根因：自愈本身是**读**的降级便利（集合配置写错时检索仍可用），却被写路径
# 继承成「写目标的静默改写」。修法 A+B 纵深防御：
#   A. 写路径不复用自愈结果：本实例一旦已被自愈改写，写方法抛
#      `SelfHealWriteRefused`（绝不把数据写进权威生产集合）；
#   B. 自愈只在读方法上生效：写路径一律用调用方**显式**集合名（不检测、不
#      回落）——「新建一个集合再写」是明确的调用意图，必须落到该集合本身。
# 读路径行为**不变**（集合缺失/为空 → 回落权威库），见
# `_ensure_non_empty_collection`；写路径见 `writable_collection`。
#
# k59 r2（残留口子收口）：A+B 只管住写 API，**绕过写 API 直写读属性**
# （`retriever.collection.upsert(...)`）仍会被自愈改写（副本实测 27,115 →
# 27,116）。故 `collection` 现在返回**只读包装**：读方法原样透传（读语义逐字
# 不变），写方法 `COLLECTION_WRITE_METHODS` 直接抛 `ReadPropertyWriteRefused`；
# 同口径由静态守卫测试 `tests/test_k59_collection_write_scan.py` 在仓库面兜底
# （扫描 `\.collection\.(upsert|add|update|delete|modify)`，命中即失败）。
# ────────────────────────────────────────────────────────────────────────

# k59 r2：读属性 `collection` 上禁用的写方法名 —— **单一事实源**：
# 只读包装（运行时）与静态守卫测试（仓库面）共用此集合；chroma Collection 的
# 写面 = add / update / upsert / delete / modify（只许加不许减）。
COLLECTION_WRITE_METHODS = frozenset({"add", "update", "upsert", "delete", "modify"})


class ReadPropertyWriteRefused(RuntimeError):
    """写经由**读属性** `Retriever.collection` 被拒（k59 r2）。

    读属性是读语义：空集合自愈在此生效，其句柄可能是权威库 `fortune_books_v2`。
    写入一旦经由它，目标就会被自愈静默改写 —— 这正是 k55 险情的形态。所以读
    属性返回**只读包装**：`query`/`get`/`count`/… 原样透传，写方法直接抛本异常。

    正确写法：`retriever.add_chunks(...)` 或 `retriever.writable_collection.upsert(...)`。
    """

    def __init__(self, method: str, collection_name: str):
        self.method = method
        self.collection_name = collection_name
        super().__init__(
            f"拒绝写入：'{method}' 经由读属性 collection 调用（集合 "
            f"'{collection_name}'）。读属性的句柄可能是空集合自愈后的权威库，"
            f"写目标会被静默改写（k55 险情根因）。请改用 add_chunks(...) 或 "
            f"writable_collection.upsert(...)（k59）。"
        )


class _ReadOnlyCollection:
    """`Retriever.collection` 的只读包装（k59 r2）：读透传，写抛错。

    为什么包装而不改 `collection` 的行为：**读路径语义必须逐字不变**（k24 自愈
    回落、k26 那类「注入 fake 集合」的既有用法、各脚本的 `collection.get()`）。
    包装只加一层 `__getattr__` 转发：除 `COLLECTION_WRITE_METHODS` 外全部原样可用。

    契约差异（r3，M-4）：写方法名在 `__getattr__` 里**抛** `ReadPropertyWriteRefused`
    （`RuntimeError`，非 `AttributeError`），所以 `hasattr(col, "upsert")` /
    `getattr(col, "upsert", None)` 会抛而不是返回 `False`/默认值 —— 有意为之
    （写入被拒必须响亮）。仓内 0 处使用此类探测。
    """

    __slots__ = ("_wrapped",)

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def __getattr__(self, name: str):
        if name in COLLECTION_WRITE_METHODS:
            raise ReadPropertyWriteRefused(name, getattr(self._wrapped, "name", ""))
        return getattr(self._wrapped, name)

    def __repr__(self) -> str:
        return f"<read-only collection '{getattr(self._wrapped, 'name', '?')}' (k59)>"


class SelfHealWriteRefused(RuntimeError):
    """写路径拒绝：本实例的集合名已被「空集合自愈」改写（k59）。

    发生条件：同一个 `Retriever` 实例先走过读路径（`search` / `count` /
    `collection` / `collection_name`），而配置的集合不存在或为空 → 自愈把
    `_collection_name` 改写成权威库 `BOOKS_COLLECTION`；此后调用 `add_chunks`
    之类的写方法，数据会落到**权威生产集合**而不是调用方指定的集合。

    处置（r3 澄清，M-2）：自愈是**实例级一次性判定**，本实例已被判为「写目标
    不可信」—— 同实例上改 `_collection_name` / 改配置都**不会**解除（判据是
    `self_healed_to` 属性，不是当前集合名）。唯一出路是**新建一个 Retriever
    实例**专用于写入（构造期给定正确集合名，且不要先读）。**不要**吞掉本异常
    继续写。
    """

    def __init__(self, requested: str, healed_to: str, persist_dir: str):
        self.requested = requested
        self.healed_to = healed_to
        self.persist_dir = persist_dir
        super().__init__(
            f"拒绝写入：本实例的集合名已被空集合自愈改写（请求 '{requested}' → "
            f"实际 '{healed_to}'，persist_dir={persist_dir}），写目标已不再是调用方"
            f"指定的集合。继续写会把数据静默落进权威集合 '{healed_to}'（k55 险情"
            f"根因，k59 起拒绝）。处置：**本实例已自愈，改集合名/改配置都无效** —— "
            f"请**新建一个 Retriever 实例**专用于写入（构造期给定正确集合名、"
            f"不要先读）。"
        )


# ────────────────────────────────────────────────────────────────────────
# k28（k23k24 审查 M-6）：空集合自愈的可观测状态。
# 自愈是「配置写错但检索仍可用」的静默降级点：warning 只在进程内首次触发
# （避免刷屏），低峰期首个检索若发生在低 S 日志窗口，运维就查不到了。这里
# 把每次自愈落成**可查询状态**（进程内累计）+ 重复自愈降级为 info 留痕：
# 既不刷屏，又能事后确认（`self_heal_events()`），日志错过也不丢。
# 键 = 配置请求的集合名；healed_to=None 表示自愈失败（权威库也空/不可用）。
# ────────────────────────────────────────────────────────────────────────
_SELF_HEAL_EVENTS: dict = {}

# k31（k30 审查遗留）：`evt["count"] += 1` 是读-改-写，真并发下会丢计数
# （同一集合名被两个线程同时登记 → 只 +1）。只护这一处进程内状态更新：
# 自愈只发生在「集合配置写错但检索仍可用」的降级路径（非热路径、无嵌套
# 持锁、不涉 DB/检索结果）→ 锁开销可忽略，也无死锁面。
_SELF_HEAL_LOCK = threading.Lock()


def _record_self_heal(name: str, reason: str, healed_to: Optional[str]) -> int:
    """登记一次自愈判定（成功或失败），返回该集合名在进程内的累计次数。

    k31：整体（含首次登记 setdefault）在 `_SELF_HEAL_LOCK` 内完成——计数与
    字段更新是一个原子步，返回值即本次登记后的准确累计。
    """
    with _SELF_HEAL_LOCK:
        evt = _SELF_HEAL_EVENTS.setdefault(
            name, {"count": 0, "last_reason": "", "healed_to": None, "last_ts": ""})
        evt["count"] += 1
        evt["last_reason"] = reason
        evt["healed_to"] = healed_to
        evt["last_ts"] = datetime.now().isoformat(timespec="seconds")
        return evt["count"]


def self_heal_events() -> dict:
    """空集合自愈事件快照（k28，运维/巡检可查；调用方不得就地修改）。

    返回 {请求集合名: {count, last_reason, healed_to, last_ts}} 的浅拷贝。

    k30（遗留③并发加固）：迭代 `list(...)` **快照**而非活视图——另一线程或
    重入路径 `_record_self_heal` 的 `setdefault` 首次登记新集合名，若落在
    迭代两项之间，活视图会抛 `RuntimeError: dictionary changed size during
    iteration`（运维巡检调用面）。返回语义不变：仍是浅拷贝快照（迭代开始
    后新登记的集合名不入本次快照）。
    """
    return {k: dict(v) for k, v in list(_SELF_HEAL_EVENTS.items())}


@dataclass
class ChunkResult:
    text: str
    source: str
    score: float
    chunk_id: str
    category: str = ""
    # k24：库内元数据有 title（如「《穷通宝鉴》从格-张居正命」）与 verified，
    # 此前被检索器丢弃 → 送给 LLM 的引用全标【未知】，模型只能自己「想起」
    # 出处（编造引文的温床）。带上 title，让引用可溯源。
    title: str = ""


class _AppEmbeddingFunction:
    """chroma EmbeddingFunction 包装 — 用 app 的 embedder (bge-m3, 1024维) 编码.

    集合创建时传入，让 chroma 在自动嵌入路径上使用与 add_chunks 相同的模型，
    保证集合维度 (1024) 一致；查询路径使用显式 query_embeddings，同样出自
    self.embedder，杜绝维度不匹配。
    """

    def __init__(self, embedder: Embedder):
        self._embedder = embedder

    def __call__(self, input):
        if isinstance(input, str):
            input = [input]
        return self._embedder.encode(list(input)).tolist()

    def name(self) -> str:
        return "app_embedder_bge_m3"

    def embed_query(self, input) -> list:
        if isinstance(input, (list, tuple)):
            return [self._embedder.encode([t])[0].tolist() for t in input]
        return self._embedder.encode([input])[0].tolist()


class Retriever:
    """混合检索器"""

    def __init__(self, persist_dir: str, embedder: Embedder,
                 collection_name: Optional[str] = None):
        """collection_name: 显式集合名（优先级最高）。

        k24：此前只有 env 与 _collection_name 赋值两条路，settings.yaml 的
        embedding_collection 无法驱动检索器（main.py 靠事后赋值补丁，engine
        侧则漏了）——「配置写了不生效」这一类 bug 的公共形态。现支持构造期
        传入，调用方一律用 settings.embedding_collection。
        优先级：collection_name > EMBEDDING_COLLECTION 环境变量 > 权威古籍库。
        """
        self.persist_dir = persist_dir
        self.embedder = embedder
        self._client = None
        self._collection = None
        self._collection_checked = False
        self._requested_collection_name = (
            collection_name
            or os.environ.get("EMBEDDING_COLLECTION")
            or BOOKS_COLLECTION
        )
        # k59：`_collection_name` = 实际生效的集合名（读路径自愈会改写它）；
        # `_requested_collection_name` = 调用方原始指定值（自愈**不**动它）。
        # 写路径据此判断「集合名是否已被自愈改写」→ 被改写即拒绝写入。
        self._collection_name = self._requested_collection_name
        self._self_healed_to: Optional[str] = None
        self._write_target_warned: set = set()

    @property
    def collection_name(self) -> str:
        """实际生效的集合名（空集合自愈后的结果）。"""
        self._ensure_non_empty_collection()
        return self._collection_name

    def _ensure_non_empty_collection(self) -> None:
        """空集合自愈（k24）：目标集合无数据时回退到权威古籍库。

        配置/环境变量指向空集合（fortune_books / fortune_v6）时，旧行为是
        静默返回空列表 → 8 项能力 refs=0 → LLM 凭记忆编造引文。这里改为
        一次性检测：已知空集合无需查询，其余按 count()==0 判定；命中则改用
        BOOKS_COLLECTION 并记 warning（绝不静默）。权威库同样为空才放弃
        （真·无数据，交由调用方按「未检索到」处理）。
        """
        if self._collection_checked:
            return
        self._collection_checked = True
        name = self._collection_name
        if name in KNOWN_EMPTY_COLLECTIONS:
            reason = "已知空集合"
        elif name == BOOKS_COLLECTION:
            return
        else:
            try:
                if self._raw_count(name) > 0:
                    return
                reason = "count()==0"
            except Exception as e:  # 集合不存在/不可用 → 走自愈
                reason = f"不可用: {e}"
        try:
            if self._raw_count(BOOKS_COLLECTION) <= 0:
                _record_self_heal(name, reason, None)
                logger.warning(
                    "古籍集合自愈失败: '%s'（%s）无数据，权威集合 '%s' 也为空",
                    name, reason, BOOKS_COLLECTION,
                )
                return
        except Exception as e:
            _record_self_heal(name, reason, None)
            logger.warning(
                "古籍集合自愈失败: '%s'（%s）无数据，权威集合 '%s' 不可用: %s",
                name, reason, BOOKS_COLLECTION, e,
            )
            return
        # k28：进程内首次仍用 warning（醒目、不刷屏）；重复自愈降级为 info
        # 留痕（同一进程多处构检索器时不再刷 warning，但日志与事件表都可查）。
        _n = _record_self_heal(name, reason, BOOKS_COLLECTION)
        if _n == 1:
            logger.warning(
                "古籍集合 '%s' %s、无检索价值 → 自动改用权威古籍库 '%s'；"
                "请修正 embedding_collection 配置（yaml 或 EMBEDDING_COLLECTION）",
                name, reason, BOOKS_COLLECTION,
            )
        else:
            logger.info(
                "古籍集合自愈（进程内第 %d 次）: '%s' %s → 改用 '%s'"
                "（首条 warning 已提示；状态见 rag.retriever.self_heal_events()）",
                _n, name, reason, BOOKS_COLLECTION,
            )
        # k59：登记「本实例的集合名已被自愈改写」。这是写路径的唯一判据 ——
        # 写方法一旦看到它非空即抛 `SelfHealWriteRefused`，绝不把数据写进
        # 权威生产集合（k55 险情：生产副本 27,115 → 27,116，静默无报错）。
        self._self_healed_to = BOOKS_COLLECTION
        self._collection_name = BOOKS_COLLECTION
        self._collection = None  # 丢弃已缓存的空集合句柄

    def _raw_count(self, name: str) -> int:
        """指定集合的条数（不触发自愈，避免递归）。"""
        return self.client.get_collection(name).count()

    @property
    def client(self):
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def _open_collection(self, name: str):
        """按集合名取/建 chroma 集合句柄（读、写共用；不缓存）。"""
        return self.client.get_or_create_collection(
            name=name,
            embedding_function=_AppEmbeddingFunction(self.embedder),
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def collection(self):
        """**读**路径的集合句柄（空集合自愈在这里生效，可能已是权威库）。

        ⚠️ k59：本属性是**读语义** —— `_ensure_non_empty_collection()` 会把
        缺失/为空的集合名改写成权威库。**不得用于写入**：写路径一律走
        `writable_collection`（它绝不使用自愈结果，必要时抛
        `SelfHealWriteRefused`）。k55 险情的根因正是写方法继承了这个属性。

        k59 r2：返回值是**只读包装** `_ReadOnlyCollection` —— 读方法
        （`query`/`get`/`count`/`name`/…）逐字透传，写方法
        （`COLLECTION_WRITE_METHODS`）抛 `ReadPropertyWriteRefused`，因此
        「绕过写 API 直写读属性」这条复发面在库层被彻底关掉。

        ⚠️ **契约差异（r3 记录，M-4）**：包装不是 chroma `Collection` 本体，
        故 ① 同一性判断（`retriever.collection is X`）不再成立；
        ② 对**写方法名**做 `hasattr(col, "upsert")` 或
        `getattr(col, "upsert", None)` 会**抛出** `ReadPropertyWriteRefused`
        而不是返回 `False`/默认值（`__getattr__` 抛的不是 `AttributeError`）
        —— 这是**有意**的：写入被拒必须响亮，不得被 `hasattr`/默认值静默吞掉。
        仓内 0 处此类用法；若确需探测，请 `getattr` 写方法名以外的方式判断
        （如 `retriever.self_healed_to` / `writable_collection`）。

        句柄缓存语义与 k59 之前**逐字一致**：只有 `_collection is None` 时才
        取句柄（外部注入 `_collection` 的既有用法/测试不受影响）。
        """
        self._ensure_non_empty_collection()
        if self._collection is None:
            self._collection = self._open_collection(self._collection_name)
        return _ReadOnlyCollection(self._collection)

    @property
    def self_healed_to(self) -> Optional[str]:
        """本实例的集合名是否已被空集合自愈改写（None = 未改写）。

        k59：写路径的判据，同时给运维/脚本一个可读的状态位（配合
        `self_heal_events()` 使用）。
        """
        return self._self_healed_to

    @property
    def writable_collection(self):
        """**写**路径的集合句柄（k59）：显式集合名，绝不使用自愈结果。

        与读路径 `collection` 的三点差异：
        ① **不触发自愈**：目标集合为空/不存在也不改写目标 —— 「新建一个集合
           再写」是调用方的明确意图（各 ingest / 重建脚本依赖此行为），写必须
           落到该集合本身；
        ② **拒绝已自愈的实例**：若本实例已被读路径自愈改写
           （`self_healed_to` 非空）→ 抛 `SelfHealWriteRefused`。此时写下去
           数据会静默落进权威生产集合（k55 险情：副本 27,115 → 27,116）；
        ③ 目标为空/缺失且同目录权威集合有数据时记 warning（高危形态，绝不
           静默），但**不改变写入目标**。

        句柄**不复用**读路径的 `self._collection` 缓存（那是读语义的句柄：可能
        绑定自愈前/改名前的集合名，或由调用方注入），也不写回该缓存 —— 读路径
        行为保持不变。
        """
        if self._self_healed_to is not None:
            raise SelfHealWriteRefused(
                self._requested_collection_name, self._self_healed_to,
                self.persist_dir,
            )
        name = self._collection_name
        self._warn_if_write_target_would_self_heal(name)
        return self._open_collection(name)

    def _warn_if_write_target_would_self_heal(self, name: str) -> None:
        """写目标恰是「会触发自愈」的形态时留 warning（k59，只警告不改写）。

        判定条件与 `_ensure_non_empty_collection` 同源（已知空集合 /
        `count()==0` / 不可用）；但写路径**不**改写目标，故这里只提示调用方
        核对集合名：同一形态若是读路径就会被自愈改写，正是 k55 险情的高危形态
        （生产目录 + 一个全新集合名）。同一实例同一集合名只提示一次。
        """
        if name == BOOKS_COLLECTION or name in self._write_target_warned:
            return
        if name in KNOWN_EMPTY_COLLECTIONS:
            reason = "已知空集合"
        else:
            try:
                if self._raw_count(name) > 0:
                    return
                reason = "count()==0"
            except Exception as e:  # 集合不存在/不可用
                reason = f"不可用: {e}"
        try:
            if self._raw_count(BOOKS_COLLECTION) <= 0:
                return
        except Exception:
            return
        self._write_target_warned.add(name)
        logger.warning(
            "写入目标集合 '%s' %s，而权威集合 '%s' 有数据：本次写入落到 '%s' "
            "本身（写路径不使用自愈结果，k59）；若本意是追加到权威库，请核对"
            "集合名/配置",
            name, reason, BOOKS_COLLECTION, name,
        )

    def add_chunks(self, chunks: List[Chunk], batch_size: int = 128):
        """批量添加切块到向量库 (使用 upsert 避免重复 ID 错误)

        使用小批量避免内存问题，每批次独立编码和写入。

        k59：写目标 = 调用方**显式**集合名（`writable_collection`），不使用
        空集合自愈的结果。若本实例已被读路径自愈改写，直接抛
        `SelfHealWriteRefused` —— 旧行为是把数据静默写进权威生产集合
        （k55 险情：27,115 → 27,116）。
        """
        if not chunks:
            return

        # 写前一次性判定（写目标绝不静默改写；被自愈改写即在此抛错）
        collection = self.writable_collection

        all_texts = [c.text for c in chunks]
        all_ids = [c.chunk_id for c in chunks]
        all_metadatas = [{
            "source": c.source,
            "author": c.author,
            "category": c.category,
        } for c in chunks]

        # Process in small batches to avoid hanging on large encode calls
        for start in range(0, len(chunks), batch_size):
            end = min(start + batch_size, len(chunks))
            batch_texts = all_texts[start:end]
            batch_ids = all_ids[start:end]
            batch_metas = all_metadatas[start:end]

            embeddings = self.embedder.encode(batch_texts)
            collection.upsert(
                embeddings=embeddings.tolist(),
                documents=batch_texts,
                ids=batch_ids,
                metadatas=batch_metas,
            )

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        top_k: int = 20,
        min_score: float = 0.3,
        **kw,
    ) -> List[ChunkResult]:
        """混合检索：先语义检索，无结果则用BM25关键词检索

        类目处理（k24）：`category` 是**产品语义过滤词**（bazi/fengshui/
        mianxiang…），库内元数据 category 是**数据语义值**（bazi_case/
        fengshui_guide/mianxiang_features…）。两者按 src/book_categories.py
        的映射表对齐后检索；映射为空（库内无对应类目，如 zeri/hehun）直接
        走全库。

        **无类目兜底（k24 核心）**：类目过滤后的检索若无命中，退回全库检索，
        而不是返回空列表。旧行为是「类目对不上 = 静默 refs=0」→ 8 项能力
        集体断链 → LLM 凭记忆编造引文。

        **kw：兼容 FAISS 检索接口的扩展参数（expand/rerank/original_query 等），
        本检索器无查询扩展与精排，静默忽略（调用方在两类检索器间可透明切换）。

        集合为空/缺失/配置不兼容（如旧脚本创建的无 embedding_function 集合）时
        不抛异常：安全返回空列表并记 warning——legacy 兜底缺失，FAISS 主路径
        不受影响，调用方（handler/api/engine）零改动。集合为空时另有一层
        自愈（见 _ensure_non_empty_collection）。
        """
        try:
            cats = resolve_categories(category)

            if cats:
                # ① 先按类目精确检索（语义 → 关键词）
                chunk_results = self._vector_search(query, cats, top_k, min_score)
                if not chunk_results:
                    chunk_results = self._keyword_search(query, cats, top_k)
                if chunk_results:
                    return chunk_results
                # ② 类目无命中 → 无类目兜底（返回空才是断链，见 docstring）
                logger.info(
                    "类目 '%s'（映射 %s）无命中 → 退回全库检索: query=%r",
                    category, list(cats), query[:40],
                )

            # 无类目（或类目兜底）：全库检索
            chunk_results = self._vector_search(query, None, top_k, min_score)
            if not chunk_results:
                chunk_results = self._keyword_search(query, None, top_k)
        except Exception as e:
            logger.warning(
                "Legacy retriever search degraded: collection '%s' unavailable "
                "(empty/missing/misconfigured) — returning empty results: %s",
                self._collection_name, e,
            )
            chunk_results = []
        return chunk_results

    @staticmethod
    def _as_category_tuple(categories):
        """兼容入参：None / str（过滤词或真实类目名）/ 类目元组。

        私有检索方法（_vector_search/_keyword_search）历史上接受单个类目字符串，
        外部脚本/测试仍按老签名调用；这里统一归一化，避免调用方各自翻译。
        """
        if not categories:
            return ()
        if isinstance(categories, str):
            return resolve_categories(categories)
        return tuple(categories)

    def _where_filter(self, categories):
        """类目 → chroma where 子句（多值用 $in，单值用精确匹配）。"""
        categories = self._as_category_tuple(categories)
        if not categories:
            return None
        if len(categories) == 1:
            return {"category": categories[0]}
        return {"category": {"$in": list(categories)}}

    def _vector_search(self, query, categories, top_k, min_score):
        """语义向量检索 — 使用 ChromaDB 原生查询，自动匹配维度

        categories: 库内真实类目元组；None/空 表示不过滤（全库）。
        """
        try:
            where_filter = self._where_filter(categories)

            # 显式 query_embeddings：与写入端 (add_chunks) 使用同一个 bge-m3
            # embedder，保证查询/写入维度一致 (1024)，不再依赖 chroma 内置
            # 384 维 embedder。
            query_vec = self.embedder.encode([query])[0].tolist()
            results = self.collection.query(
                query_embeddings=[query_vec],
                n_results=top_k,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )

            chunk_results = []
            if results["ids"] and results["ids"][0]:
                for i, chunk_id in enumerate(results["ids"][0]):
                    score = 1 - results["distances"][0][i]
                    if score >= min_score:
                        meta = results["metadatas"][0][i] or {}
                        title = meta.get("title", "") or ""
                        chunk_results.append(ChunkResult(
                            text=results["documents"][0][i],
                            # k24：库内无 source 键（只有 category/title/verified），
                            # 旧代码一律回落 "未知" → 引用全标【未知】。改用 title，
                            # 让 LLM 拿到真实出处，杜绝凭记忆补出处。
                            # k26：两者都缺（237 条空 title 语料）时**留空**，不得
                            # 再伪造成字面书名 —— 出处兜底是读取契约（ref_title）
                            # 的职责，检索器只搬运库内真实值。
                            source=meta.get("source", "") or title,
                            score=round(score, 4),
                            chunk_id=chunk_id,
                            category=meta.get("category", ""),
                            title=title,
                        ))
            return sorted(chunk_results, key=lambda r: r.score, reverse=True)
        except Exception:
            return []

    def _keyword_search(self, query, categories, top_k):
        """BM25关键词检索（不需要模型下载）

        categories: 库内真实类目元组；None/空 表示不过滤（全库）。
        k24：类目过滤下推到 chroma `where`（原实现全量 get 后在 Python 侧过滤，
        27k 条全读既慢又浪费内存）。
        """
        try:
            # Get documents (类目过滤下推到 chroma)
            all_docs = self.collection.get(
                where=self._where_filter(categories),
                include=["documents", "metadatas"],
            )
            if not all_docs["ids"]:
                return []

            # Simple keyword scoring
            import jieba
            query_words = set(jieba.cut(query))

            scored = []
            for i in range(len(all_docs["ids"])):
                meta = all_docs["metadatas"][i] if all_docs["metadatas"] else {}
                doc = all_docs["documents"][i] if all_docs["documents"] else ""
                doc_words = set(jieba.cut(doc))

                # Score: intersection over query words
                overlap = query_words & doc_words
                if overlap:
                    score = len(overlap) / len(query_words) if query_words else 0
                    title = meta.get("title", "") or ""
                    scored.append(ChunkResult(
                        text=doc[:500],
                        # k26：不注入伪字面量「未知」（同 _vector_search）
                        source=meta.get("source", "") or title,
                        score=round(min(score, 0.99), 4),
                        chunk_id=all_docs["ids"][i],
                        category=meta.get("category", ""),
                        title=title,
                    ))

            return sorted(scored, key=lambda r: r.score, reverse=True)[:top_k]
        except ImportError:
            # Fallback: simple substring matching
            scored = []
            try:
                all_docs = self.collection.get(
                    where=self._where_filter(categories),
                    include=["documents", "metadatas"],
                )
                for i in range(len(all_docs["ids"])):
                    meta = all_docs["metadatas"][i] if all_docs["metadatas"] else {}
                    doc = all_docs["documents"][i] if all_docs["documents"] else ""
                    # Score by character overlap
                    query_chars = set(query)
                    doc_chars = set(doc)
                    overlap = query_chars & doc_chars
                    if overlap:
                        score = len(overlap) / len(query_chars)
                        title = meta.get("title", "") or ""
                        scored.append(ChunkResult(
                            text=doc[:500],
                            # k26：不注入伪字面量「未知」（同 _vector_search）
                            source=meta.get("source", "") or title,
                            score=round(score, 4),
                            chunk_id=all_docs["ids"][i],
                            category=meta.get("category", ""),
                            title=title,
                        ))
                return sorted(scored, key=lambda r: r.score, reverse=True)[:top_k]
            except Exception:
                return []

    def count(self) -> int:
        """集合条数（**读**语义：空集合自愈在此生效，k59 未改）。

        注意：调用本方法即可能触发自愈改写 —— 若之后还要写入，请用
        `writable_collection` 判定（已被改写则写方法抛 `SelfHealWriteRefused`）。
        """
        return self.collection.count()
