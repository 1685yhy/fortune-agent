"""BM25 关键词检索器 — RAG 降级方案（向量库重建完成前的稳定检索路径）。

背景
----
- 向量库（chroma 27k 条 384 维）与当前 embedder（bge-m3 1024 维）维度不匹配，
  旧 embedder 模型已不可得；向量重建在 CPU 上需数小时。
- 本模块基于 /mnt/d/fortune-data/all_chunks.json（20350 条古籍切块，字段
  text/source/author/category/chunk_id/keywords）在内存构建倒排索引，
  实现标准 BM25（k1=1.5, b=0.75）关键词检索，让 AI 原生对话的
  <tool_call>检索工具立即可用（目标：单次检索 <200ms）。

升级路径（向量库重建后）
------------------------
- FAISS 向量库（276 万条，bge-m3 1024 维）已接入生产检索：检索工具只走
  src/rag/faiss_retriever.py 的语义检索，不可用/无结果时由 LLM 自然对话兜底。
- 本模块保留但不接入工具层：可被 DreamEngine 等依赖关键词检索的引擎直接复用。

内存设计
--------
- 20350 条文档，平均 ~700 字符。倒排索引使用 array 紧凑存储（每个 postings
  条目仅 6 字节：doc_id 4B + tf 2B），全索引预期 <200MB 常驻内存；
  文本仅保留前 600 字符用于展示，索引仅取前 2000 字符，控制内存与建索引耗时。
- 惰性构建：首次 search() 调用才加载+建索引（避免拖慢服务启动），
  构建完成后打印统计日志。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from src.security.log_redact import redact  # k86 必修2：日志不落对话明文

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 数据文件路径（可用环境变量覆盖，便于部署在不同机器）
DEFAULT_DATA_PATH = os.environ.get(
    "FORTUNE_BM25_DATA", "/mnt/d/fortune-data/all_chunks.json"
)

# 标准 BM25 参数
K1 = 1.5
B = 0.75

# 索引文本长度上限（字符）：过长段落截断，防止超长文档稀释检索质量
INDEX_CHARS = 2000
# 展示文本长度上限（返回给 LLM 引用的原文长度，工具侧还会再截到 200）
DISPLAY_CHARS = 600

# 中文停用词（文言文高频虚词为主，过滤后索引更小、检索更准）
_STOPWORD_CHARS = (
    "的了是在有和与及等或于而以其此彼所将为并若虽则且皆故然亦乃惟岂何者"
    "焉乎哉矣也之么吧吗啊呢个中上下前从对到向把被给让我你就他她它们什这那"
)
_STOPWORDS = set(_STOPWORD_CHARS)

_CJK_RE = re.compile(r"[一-鿿0-9a-zA-Z]")


def _is_token_keep(tok: str) -> bool:
    """过滤停用词与纯标点 token。"""
    if tok in _STOPWORDS:
        return False
    return bool(_CJK_RE.search(tok))


def tokenize(text: str) -> List[str]:
    """中文分词：优先 jieba，未安装时降级为字符 unigram（古典文本也可用）。"""
    tokens: List[str] = []
    try:
        import jieba  # 惰性导入：jieba 缺失不影响本模块导入
        tokens = list(jieba.cut(text))
    except Exception:  # ImportError 或 jieba 初始化失败 → 字符级兜底
        tokens = [ch for ch in text if ch.strip()]
    return [t.strip() for t in tokens if t and t.strip() and _is_token_keep(t)]


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class BM25Doc:
    """单条古籍切块（内存版，仅保留展示与元数据所需字段）。"""

    text: str
    source: str
    category: str
    chunk_id: str
    author: str = ""


# ---------------------------------------------------------------------------
# 检索器
# ---------------------------------------------------------------------------


class BM25Retriever:
    """内存倒排索引 + 标准 BM25 检索。

    线程安全：build/search 均受 _lock 保护，可被多 worker 并发调用。
    """

    def __init__(self, data_path: Optional[str] = None):
        self.data_path = data_path or DEFAULT_DATA_PATH
        self._lock = threading.Lock()
        self._built = False
        self._build_error: Optional[str] = None

        # 构建产物
        self._docs: List[BM25Doc] = []
        self._doc_lengths = array("I")  # 每文档 token 数
        self._postings: Dict[str, Tuple[array, array]] = {}  # term -> (doc_ids, tfs)
        self._total_tokens = 0
        self._avgdl = 0.0
        self._n_docs = 0
        self._build_seconds = 0.0
        self._term_count = 0

    # ------------------------------------------------------------------
    # 构建（惰性，首次 search 时触发）
    # ------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """索引是否已构建可用（失败返回 False，错误见 build_error）。"""
        return self._built

    @property
    def build_error(self) -> Optional[str]:
        return self._build_error

    def ensure_built(self) -> bool:
        """线程安全地确保索引已构建；构建失败返回 False。"""
        if self._built:
            return True
        with self._lock:
            if self._built:
                return True
            t0 = time.time()
            try:
                self._build()
                self._built = True
                self._build_seconds = time.time() - t0
                logger.info(
                    "BM25 索引构建完成: %d 条文档, %d 个词项, 耗时 %.1fs, 内存 %.0fMB, avgdl=%.1f",
                    self._n_docs, self._term_count, self._build_seconds,
                    self.memory_mb(), self._avgdl,
                )
            except Exception as e:  # noqa: BLE001 — 构建失败可重试
                self._build_error = f"{type(e).__name__}: {e}"
                logger.error("BM25 索引构建失败: %s", self._build_error)
            return self._built

    def _build(self) -> None:
        path = Path(self.data_path)
        if not path.exists():
            raise FileNotFoundError(f"古籍数据文件不存在: {path}")

        t0 = time.time()
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        logger.info("BM25 加载 %s: %d 条记录 (%.1fs)", path, len(records), time.time() - t0)

        docs: List[BM25Doc] = []
        doc_lengths = array("I")
        postings: Dict[str, Tuple[array, array]] = {}

        for rec in records:
            if not isinstance(rec, dict):
                continue
            text = (rec.get("text") or "").strip()
            if not text:
                continue
            doc = BM25Doc(
                text=text[:DISPLAY_CHARS],
                source=str(rec.get("source") or "未知"),
                category=str(rec.get("category") or ""),
                chunk_id=str(rec.get("chunk_id") or ""),
                author=str(rec.get("author") or ""),
            )
            doc_id = len(docs)
            docs.append(doc)

            # 索引文本 = 正文前段 + 预置关键词（keywords 字段辅助召回）
            idx_text = text[:INDEX_CHARS]
            kws = rec.get("keywords")
            if kws:
                if isinstance(kws, (list, tuple)):
                    idx_text += " " + " ".join(str(k) for k in kws)
                elif isinstance(kws, str):
                    idx_text += " " + kws

            tf_map: Dict[str, int] = {}
            for tok in tokenize(idx_text):
                tf_map[tok] = tf_map.get(tok, 0) + 1
            doc_lengths.append(sum(tf_map.values()))  # 标准 BM25：文档长度 = token 总数
            for term, tf in tf_map.items():
                entry = postings.get(term)
                if entry is None:
                    doc_ids = array("I")
                    tfs = array("H")
                    doc_ids.append(doc_id)
                    tfs.append(tf)
                    postings[term] = (doc_ids, tfs)
                else:
                    entry[0].append(doc_id)
                    entry[1].append(tf)

        self._docs = docs
        self._doc_lengths = doc_lengths
        self._postings = postings
        self._n_docs = len(docs)
        self._total_tokens = sum(doc_lengths)
        self._avgdl = self._total_tokens / self._n_docs if self._n_docs else 0.0
        self._term_count = len(postings)
        logger.debug("BM25 倒排索引构建完成: %d postings 项", sum(len(v[0]) for v in postings.values()))

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
    ) -> List[dict]:
        """BM25 关键词检索。

        Args:
            query: 查询文本（自然语言或关键词均可，内部统一分词）
            top_k: 返回条数
            category: 可选，限定古籍分类（如 "bazi"/"yijing"，默认全部）

        Returns:
            [{text, source, score, chunk_id, category, author}, ...] 按相关度降序。
        """
        if not self.ensure_built():
            return []
        if not query or not query.strip():
            return []
        q_terms = tokenize(query)
        if not q_terms:
            return []

        t0 = time.time()
        scores = self._score(q_terms, category)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

        results = []
        for doc_id, score in ranked:
            doc = self._docs[doc_id]
            results.append({
                "text": doc.text,
                "source": doc.source,
                "score": round(float(score), 4),
                "chunk_id": doc.chunk_id,
                "category": doc.category,
                "author": doc.author,
            })
        if results:
            # k86 必修2：检索词由用户提问生成 ⇒ 不落明文（原为 query，未截断）
            logger.debug("BM25 检索 %s 返回 %d 条, 耗时 %.1fms",
                         redact(query), len(results), (time.time() - t0) * 1000)
        return results

    def _score(self, q_terms: List[str], category: Optional[str] = None) -> Dict[int, float]:
        """标准 BM25 打分（k1=1.5, b=0.75，IDF 带平滑项避免 df=N 除零）。

        仅遍历查询词项的倒排列表，单次检索时间复杂度与命中文档数成正比，
        实测 20k 条索引 <50ms。
        """
        n = self._n_docs
        if n == 0:
            return {}
        scores: Dict[int, float] = {}
        for term in q_terms:
            entry = self._postings.get(term)
            if entry is None:
                continue
            doc_ids, tfs = entry
            df = len(doc_ids)
            # 平滑 IDF：log(1 + (N - df + 0.5) / (df + 0.5))，词项出现在全部
            # 文档中时（如常见虚词漏网）IDF 仍 > 0 但极小
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for i in range(df):
                doc_id = doc_ids[i]
                if category and self._docs[doc_id].category != category:
                    continue
                tf = tfs[i]
                dl = self._doc_lengths[doc_id]
                denom = tf + K1 * (1.0 - B + B * dl / self._avgdl)
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * (tf * (K1 + 1.0)) / denom
        return scores

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """索引统计（文档数/词项数/平均长度/构建耗时）。"""
        return {
            "docs": self._n_docs,
            "terms": self._term_count,
            "avgdl": round(self._avgdl, 2),
            "build_seconds": round(self._build_seconds, 2),
            "ready": self._built,
            "data_path": self.data_path,
        }

    @staticmethod
    def memory_mb() -> float:
        """当前进程常驻内存（MB，Linux ru_maxrss）。"""
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# 进程级单例（惰性构建）
# ---------------------------------------------------------------------------

_singleton: Optional[BM25Retriever] = None
_singleton_lock = threading.Lock()


def get_bm25_retriever(data_path: Optional[str] = None) -> BM25Retriever:
    """获取进程级 BM25 检索器单例（首次调用时惰性建索引）。"""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = BM25Retriever(data_path=data_path)
    return _singleton


def reset_bm25_retriever() -> None:
    """测试用：重置单例，便于重复构建验证。"""
    global _singleton
    with _singleton_lock:
        _singleton = None
