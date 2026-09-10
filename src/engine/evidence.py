"""举证层：把推演链要点转化为检索查询，古籍检索降级为证据引用。"""
from __future__ import annotations

from src.book_categories import BOOKS_COLLECTION, KNOWN_EMPTY_COLLECTIONS
from src.engine.deduction import DeductionChain

# 库内命例实际分类：fortune_books_v2 全量实测 bazi_case=4934 条、bazi=0 条
# （2026-08-15 chroma 精确计数），chroma where 精确匹配必须用 bazi_case。
DEFAULT_CATEGORY = "bazi_case"
# 集合名常量收敛到单一事实源（k24）：本模块不再自带字面量副本，避免分裂。
EMPTY_DEFAULT_COLLECTION = "fortune_books"  # 兼容旧引用（测试/外部脚本）
REAL_BOOKS_COLLECTION = BOOKS_COLLECTION


class EvidenceProvider:
    def __init__(self, retriever=None, top_k: int = 5):
        self._retriever = retriever
        self._top_k = top_k

    def _get_retriever(self):
        if self._retriever is not None:
            return self._retriever
        # 空库守卫（k24 改版）：判据从「EMBEDDING_COLLECTION 未设置」改为
        # 「解析后的集合名落在已知空集合里」——因为配置已能真正生效（yaml +
        # 默认值都指向 fortune_books_v2），「未设置 env」不再是错误状态。
        # 显式指向空集合才是错的：在构造真实 Retriever 之前抛可操作错误；
        # compose_report 的 try/except 会降级 []，直接调用方看到明确指引。
        from src.config import load_settings  # 无模块级单例，按 main.py:627 先例
        _settings = load_settings()
        if _settings.embedding_collection in KNOWN_EMPTY_COLLECTIONS:
            raise RuntimeError(
                f"embedding_collection 指向空库 {_settings.embedding_collection}；"
                f"请设为 {REAL_BOOKS_COLLECTION}（27115条古籍库）"
            )
        # 生产懒加载（注入优先，避免测试加载重模型）
        from src.rag.retriever import Retriever  # 只读复用
        from src.rag.embedder import Embedder
        embedder = Embedder(model_name="BAAI/bge-m3")
        embedder.load()
        # 显式传入配置的集合名（k24：此前只靠 env，yaml 配置驱动不了检索器）
        self._retriever = Retriever(
            str(_settings.vectordb_dir), embedder,
            collection_name=_settings.embedding_collection,
        )
        return self._retriever

    def _build_queries(self, chain: DeductionChain, question: str) -> list[str]:
        day_stem = chain.pills[2][0]
        wuxing_day = {"甲": "木", "乙": "木", "丙": "火", "丁": "火",
                      "戊": "土", "己": "土", "庚": "金", "辛": "金",
                      "壬": "水", "癸": "水"}[day_stem]
        parts = [f"{day_stem}{wuxing_day}"]
        for s in chain.steps:
            if s.rule.startswith("geju."):
                parts.append(s.output)
            if s.rule.startswith("shishen.detect_combos") and s.output and "无经典组合" not in s.output:
                parts.append(s.output)
            if s.rule.startswith("shensha.") and s.output and "无命中" not in s.output:
                parts.append(s.output)
        queries = [" ".join(parts)]
        if question:
            queries.append(" ".join(parts[:2]) + " " + question[:40])
        return queries

    def gather(self, chain: DeductionChain, question: str = "",
               category: str = DEFAULT_CATEGORY) -> list:
        retriever = self._get_retriever()
        seen: set[str] = set()
        results = []
        for q in self._build_queries(chain, question):
            for r in retriever.search(q, category=category, top_k=self._top_k):
                cid = getattr(r, "chunk_id", None) or getattr(r, "doc_id", "")
                if cid and cid in seen:
                    continue
                if cid:
                    seen.add(cid)
                results.append(r)
        return results[: self._top_k]
