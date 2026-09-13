"""联网触发**语义路由**（k43，2026-09-13）——本地 bge-m3 双类 kNN，零成本零新依赖。

SSOT：`.superpowers/sdd/task-k43-brief.md`（用户拍板「更彻底向元宝看齐」：业界主流
= 模型/语义决策 + 护栏兜底，纯关键词门控翻车实证）。
宿主：`src/rag/search_trigger.py::decide_search` 的层 ②④ 判定（本模块只提供**信号**，
判定顺序与硬否决仍在 search_trigger——单一事实源不变）。

判定（纯本地、零网络、零付费）：
  问句 → bge-m3 向量（`src/rag/embedder_v2.py`，古籍检索同款模型）
       → 与正例/负例示例各取 top-K 平均余弦相似度（TOP_K=3）
       → margin = pos - neg
           margin ≥ TAU_SEARCH → "search"（该搜）
           margin ≤ -TAU_LOCAL → "local"（不该搜，词表层可据此否决）
           其余               → "unknown"（交回词表层：宁搜勿漏，不新增不抑制）

示例集：`semantic_router_examples.json`（正例 55 / 负例 53，逐条带来源与体积说明；
从 data/eval/agent_tasks.jsonl 137 turn、k11b/k41/k15 护栏样例、F2/QA 排盘场景
派生，见该文件 note 与交接报告）。
k43-r1（审查 Important-1）：示例集与 A/B 用例集共用来源会造成逐字重叠（自证循环）
→ 新增用例一律走 held-out 组（`tests/k43_ab_cases.py` 的 `k43r1:*`，与示例集
逐字不交，且有测试锁）；示例集三条失实 src 标注已更正（insomnia_01→authored、
T079→T026、T084→T084#prefix，text 未动）。

**失败面永远回退 "unknown"**：模型未就绪/加载失败/编码异常/环境关闭 → 判定与
本模块不存在时**逐字一致**（词表层原行为）。因此本模块的任何故障都不会改变
硬否决（金融排除/命理本地）与既有 reason 语义。

性能与**内存（实测口径，k43-r1 更正）**：
  - 示例矩阵首次构建后**落盘缓存**（npz；键=示例集内容 md5 + 模型名），重启不重算；
  - 同问句判定**按文本缓存**（有界 LRU 512，含正负例分数）；
  - 模型**不在请求路径上同步加载**（bge-m3 冷加载 40s+ 会把首轮对话卡死）：
    优先复用进程内已加载的 bge-m3（FAISS 检索器单例），否则后台线程预加载，
    加载完成前一律 "unknown"。
  - **内存实测：模型加载后进程 RSS 80MB → 1290MB（+1.2GB）**，不是「~2GB」。
    **复用路径只在「本进程已先加载 FAISS 检索器」时生效**：本机未装 faiss-cpu
    （`ensure_ready()` 恒失败 → `loaded_embedder` 恒为 None），且常规时序下
    `decide_search` 在路由前接缝执行、天然早于「检索」工具调用 → 语义路由先加载，
    **复用不成立，实际是每进程各自一份**（不宣称省内存；自建路径在
    `_acquire_embedder` 打 warning 日志）。要避免多一份：部署侧显式设置
    SEMANTIC_ROUTER_PRELOAD/DISABLE 或在同进程内先加载 FAISS。

线程安全：状态转换与编码均持锁（`_LOCK` / `_ENCODE_LOCK`）；多进程下每进程各自
一份模型实例（与既有 FAISS 检索器同口径），可用环境变量关闭。

环境变量：
  SEMANTIC_ROUTER_DISABLE=1   关闭语义路由（回词表层；判定与 base 逐字一致）
  SEMANTIC_ROUTER_PRELOAD=0   禁止后台预加载（进程内已有 bge-m3 时仍复用）
  SEMANTIC_ROUTER_CACHE=<dir> 向量缓存目录（默认 ~/.cache/fortune_semantic_router，
                             目录 0700；不可用时退回 <tmp>）
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── 判定常量（改动必须同步 tests/test_k43_semantic_route.py 的锁用例）──
TOP_K = 3              # 双类各取 top-K 平均（对单条示例的过拟合不敏感）
TAU_SEARCH = 0.01      # margin ≥ 此值 → 该搜（偏召回：漏搜是最不可接受错误）
TAU_LOCAL = 0.02       # margin ≤ -此值 → 不该搜（偏保守：否决词表层正信号要更确信）

LABEL_SEARCH = "search"
LABEL_LOCAL = "local"
LABEL_UNKNOWN = "unknown"

_EXAMPLES_PATH = Path(__file__).with_name("semantic_router_examples.json")
_MODEL_NAME = "BAAI/bge-m3"

_ENV_DISABLE = "SEMANTIC_ROUTER_DISABLE"
_ENV_PRELOAD = "SEMANTIC_ROUTER_PRELOAD"
_ENV_CACHE = "SEMANTIC_ROUTER_CACHE"

_QUERY_CACHE_MAX = 512


@dataclass(frozen=True)
class Verdict:
    """语义判定结果：label ∈ {search, local, unknown}；margin = pos - neg。"""
    label: str
    pos: float = 0.0
    neg: float = 0.0
    margin: float = 0.0


_UNKNOWN = Verdict(LABEL_UNKNOWN)


# ── 状态（懒加载；全部经 _LOCK 保护）──────────────────────────────────────
_LOCK = threading.Lock()
_ENCODE_LOCK = threading.Lock()
_EMBEDDER = None                      # 已就绪的 embedder（复用的或自建的）
_MATRICES: Optional[Tuple[np.ndarray, np.ndarray]] = None
_LOAD_STARTED = False                 # 预加载线程是否已启动
_LOAD_FAILED = False
_QUERY_CACHE: "OrderedDict[str, Verdict]" = OrderedDict()   # k43-r1：真 LRU


def _under_pytest() -> bool:
    """单测进程内默认不触发后台模型加载（避免测试期占内存/改变判定时序）。

    显式 `SEMANTIC_ROUTER_PRELOAD=1` 仍可强制打开；真机用例直接调 warmup()。
    """
    return "pytest" in sys.modules


def _disabled() -> bool:
    return bool((os.environ.get(_ENV_DISABLE) or "").strip() not in ("", "0", "false", "False"))


def _preload_allowed() -> bool:
    env = os.environ.get(_ENV_PRELOAD)
    if env is not None:
        return env.strip() not in ("0", "false", "False", "off", "no")
    return not _under_pytest()


def _default_cache_dir() -> str:
    """默认向量缓存目录：用户私有（0700），避免共享 /tmp 被他人预置向量文件。

    k43-r1（审查 Minor-2）：原默认 = <tmp>/fortune_semantic_router（世界可写、
    文件名只由示例集 md5 + 模型名决定）——本机其他用户可预置 npz 影响路由判定。
    改为 ~/.cache/fortune_semantic_router；HOME 不可写时退回 tmp（功能不变，
    仅退回共享目录，缓存写失败也不影响判定）。
    """
    try:
        home = Path.home()
        if str(home) not in ("", "/"):
            return str(home / ".cache" / "fortune_semantic_router")
    except Exception:                                   # pragma: no cover
        pass
    return os.path.join(tempfile.gettempdir(), "fortune_semantic_router")


def _cache_dir() -> Path:
    return Path(os.environ.get(_ENV_CACHE) or _default_cache_dir())


def load_examples() -> Tuple[List[dict], str]:
    """读示例集：返回 (examples, 内容 md5 前 12 位)。异常向上抛（调用处兜底）。"""
    raw = _EXAMPLES_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    examples = list(data.get("examples") or [])
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
    return examples, digest


def _cached_matrices(digest: str, dim: int) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    path = _cache_dir() / f"examples_{digest}_{_MODEL_NAME.replace('/', '-')}.npz"
    try:
        with np.load(path) as z:
            pos, neg = z["pos"], z["neg"]
        if pos.ndim == 2 and neg.ndim == 2 and pos.shape[1] == dim and neg.shape[1] == dim:
            return pos.astype(np.float32), neg.astype(np.float32)
    except Exception as e:
        logger.debug("语义路由向量缓存未命中: %s", e)
    return None


def _write_matrices(digest: str, pos: np.ndarray, neg: np.ndarray) -> None:
    path = _cache_dir() / f"examples_{digest}_{_MODEL_NAME.replace('/', '-')}.npz"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:                     # 目录尽量私有（含缓存文件在内，只放示例向量）
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        # k43-r1（审查 Minor-4）：多进程同时构建时同一 .tmp 名会互相覆盖（罕见半写
        # 文件，虽能自愈）→ tmp 名带 pid 隔离。
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp.npz")
        np.savez(tmp, pos=pos, neg=neg)
        tmp.replace(path)
    except Exception as e:      # 缓存写失败不影响功能（下次重算）
        logger.debug("语义路由向量缓存写失败: %s", e)


def _encode_examples(embedder) -> Tuple[np.ndarray, np.ndarray]:
    examples, digest = load_examples()
    cached = _cached_matrices(digest, embedder.dimension)
    if cached is not None:
        return cached
    pos_texts = [e["text"] for e in examples if e.get("label") == LABEL_SEARCH]
    neg_texts = [e["text"] for e in examples if e.get("label") == LABEL_LOCAL]
    if not pos_texts or not neg_texts:
        raise ValueError("示例集缺正例或负例")
    pos = np.asarray(embedder.encode(pos_texts), dtype=np.float32)
    neg = np.asarray(embedder.encode(neg_texts), dtype=np.float32)
    _write_matrices(digest, pos, neg)
    logger.info("语义路由示例矩阵就绪: 正例 %d / 负例 %d dim=%d",
                len(pos_texts), len(neg_texts), pos.shape[1])
    return pos, neg


def install_embedder(embedder) -> bool:
    """注入已加载的 embedder 并构建示例矩阵（幂等；线程安全）。

    返回 True=就绪。失败置 _LOAD_FAILED（不再重试，判定恒为 unknown）。
    注意：示例矩阵编码（~1-2s）**在锁外**做——`route()` 只在极短的临界区取
    `_MATRICES`，后台预加载期间请求路径不被长锁阻塞（延迟护栏）。
    """
    global _EMBEDDER, _MATRICES, _LOAD_FAILED
    with _LOCK:
        if _MATRICES is not None:
            return True
    try:
        matrices = _encode_examples(embedder)      # 重活在锁外
    except Exception as e:
        with _LOCK:
            _LOAD_FAILED = True
        logger.warning("语义路由示例矩阵构建失败（判定回退词表层）: %s: %s",
                       type(e).__name__, str(e)[:160])
        return False
    with _LOCK:
        if _MATRICES is None:                      # 并发安装：先到者生效
            _EMBEDDER = embedder
            _MATRICES = matrices
        return True


def _shared_embedder():
    """进程内已加载的 bge-m3（FAISS 检索器单例）；无可复用实例 → None。

    k43-r1（审查 Important-4，如实口径）：**只在「本进程已先加载 FAISS 检索器」时
    才有得复用**——本机未装 faiss-cpu（`ensure_ready()` 恒失败 → `loaded_embedder`
    恒为 None）；且常规时序下 `decide_search`（路由前接缝）天然早于「检索」工具
    调用 → 语义路由先加载，复用不成立。**不得据此宣称「省 ~2GB」**；自建路径在
    `_load_worker` 打 warning 日志（实测 RSS +~1.2GB/进程）。
    """
    try:
        from src.rag.faiss_retriever import get_faiss_retriever
        return getattr(get_faiss_retriever(), "loaded_embedder", None)
    except Exception as e:
        logger.debug("复用 FAISS embedder 失败: %s", e)
        return None


def _acquire_embedder():
    """取 embedder：优先复用进程内已加载的 FAISS 实例，否则自建一份（如实记日志）。"""
    shared = _shared_embedder()
    if shared is not None:
        logger.info("语义路由复用进程内已加载的 bge-m3（无额外模型副本）")
        return shared
    logger.warning(
        "语义路由未找到可复用的 bge-m3（FAISS 检索器未加载/faiss 未安装）→ "
        "本进程自建一份（实测 RSS +~1.2GB；可用 SEMANTIC_ROUTER_DISABLE=1 关闭）")
    from src.rag.embedder import Embedder
    embedder = Embedder(model_name=_MODEL_NAME)
    if not embedder.load():
        raise RuntimeError(f"{_MODEL_NAME} 加载失败")
    return embedder


def _load_worker() -> None:
    try:
        install_embedder(_acquire_embedder())
    except Exception as e:
        global _LOAD_FAILED
        with _LOCK:
            _LOAD_FAILED = True
        logger.warning("语义路由预加载失败（判定回退词表层）: %s: %s",
                       type(e).__name__, str(e)[:160])


def _kickoff_preload() -> None:
    """后台预加载（幂等；请求路径绝不同步等模型）。"""
    global _LOAD_STARTED
    with _LOCK:
        if _LOAD_STARTED or _LOAD_FAILED or _MATRICES is not None:
            return
        if _disabled() or not _preload_allowed():
            return
        _LOAD_STARTED = True
    threading.Thread(target=_load_worker, name="k43-semantic-preload",
                     daemon=True).start()


def warmup(blocking: bool = False, timeout: float = 180.0) -> bool:
    """显式预热（运维/真机用例）：blocking=True 时等模型与矩阵就绪。

    k43-r1（审查 Minor-3）：显式预热是**运维侧的有界重试入口**——失败闩死后
    （`_LOAD_FAILED`，进程内默认不自动重试，避免后台反复拉 40s 冷加载/OOM）
    再次调用本函数会清闩重试一次；仍失败则再次闩死。
    """
    global _LOAD_FAILED
    if _disabled():
        return False
    _retry_failed_latch()
    embedder = _shared_embedder()
    if embedder is not None:
        return install_embedder(embedder)
    if not blocking:
        _kickoff_preload()
        return is_ready()
    if _LOAD_STARTED:                      # 已有后台线程 → 等它，不重复加载
        import time
        deadline = time.time() + timeout
        while time.time() < deadline and not is_ready() and not _LOAD_FAILED:
            time.sleep(0.2)
        return is_ready()
    try:
        emb = _acquire_embedder()
    except Exception as e:
        with _LOCK:
            _LOAD_FAILED = True
        logger.warning("语义路由预热失败（判定回退词表层）: %s: %s",
                       type(e).__name__, str(e)[:160])
        return False
    return install_embedder(emb)


def _retry_failed_latch() -> None:
    """清加载失败闩（仅由显式 warmup() 调用，见 warmup 注释）。"""
    global _LOAD_FAILED, _LOAD_STARTED
    with _LOCK:
        if _LOAD_FAILED:
            logger.info("语义路由显式预热：清除上次加载失败标记，重试一次")
            _LOAD_FAILED = False
            _LOAD_STARTED = False


def is_ready() -> bool:
    return _MATRICES is not None


def _store_query_cache(text: str, verdict: Verdict) -> None:
    """写入文本缓存（k43-r1：真 LRU——满时淘汰最久未用，不再整清）。"""
    with _LOCK:
        _QUERY_CACHE[text] = verdict
        _QUERY_CACHE.move_to_end(text)
        while len(_QUERY_CACHE) > _QUERY_CACHE_MAX:
            _QUERY_CACHE.popitem(last=False)


def route(text: str) -> Verdict:
    """问句 → 语义判定（永不抛异常；未就绪/异常 → unknown）。"""
    if _disabled():
        return _UNKNOWN
    t = (text or "").strip()
    if not t:
        return _UNKNOWN
    with _LOCK:
        hit = _QUERY_CACHE.get(t)
        if hit is not None:
            _QUERY_CACHE.move_to_end(t)          # k43-r1：LRU 触达
        matrices = _MATRICES
        embedder = _EMBEDDER
    if hit is not None:
        return hit
    if matrices is None or embedder is None:
        _kickoff_preload()
        return _UNKNOWN
    try:
        with _ENCODE_LOCK:
            vec = embedder.encode_single(t)
        query = np.asarray(vec, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if norm > 0:
            query = query / norm
        pos, neg = matrices
        k = min(TOP_K, pos.shape[0], neg.shape[0])
        pos_score = float(np.sort(pos @ query)[-k:].mean())
        neg_score = float(np.sort(neg @ query)[-k:].mean())
        margin = pos_score - neg_score
        if margin >= TAU_SEARCH:
            label = LABEL_SEARCH
        elif margin <= -TAU_LOCAL:
            label = LABEL_LOCAL
        else:
            label = LABEL_UNKNOWN
        verdict = Verdict(label, round(pos_score, 4), round(neg_score, 4),
                          round(margin, 4))
    except Exception as e:
        logger.warning("语义路由判定异常（回退 unknown）: %s: %s",
                       type(e).__name__, str(e)[:160])
        verdict = _UNKNOWN
    _store_query_cache(t, verdict)
    return verdict


def reset_for_tests() -> None:
    """清空进程内状态（单测用；不影响磁盘缓存）。"""
    global _EMBEDDER, _MATRICES, _LOAD_STARTED, _LOAD_FAILED, _QUERY_CACHE
    with _LOCK:
        _EMBEDDER = None
        _MATRICES = None
        _LOAD_STARTED = False
        _LOAD_FAILED = False
        _QUERY_CACHE = OrderedDict()
