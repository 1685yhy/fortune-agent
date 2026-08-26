"""举证层：把推演链要点转化为检索查询，古籍检索降级为证据引用。"""
from __future__ import annotations

import os

from src.engine.deduction import DeductionChain

# 库内命例实际分类：fortune_books_v2 全量实测 bazi_case=4934 条、bazi=0 条
# （2026-08-15 chroma 精确计数），chroma where 精确匹配必须用 bazi_case。
DEFAULT_CATEGORY = "bazi_case"
# Retriever 未设 EMBEDDING_COLLECTION 时的默认集合，实测 count=0 空库；
# 真实数据 27115 条全在 fortune_books_v2。
EMPTY_DEFAULT_COLLECTION = "fortune_books"
REAL_BOOKS_COLLECTION = "fortune_books_v2"


class EvidenceProvider:
    def __init__(self, retriever=None, top_k: int = 5):
        self._retriever = retriever
        self._top_k = top_k

    def _get_retriever(self):
        if self._retriever is not None:
            return self._retriever
        # 空库守卫：EMBEDDING_COLLECTION 未显式设置时，Retriever 默认指向
        # fortune_books（chroma 实测 count=0），生产必然 0 命中——在构造真实
        # Retriever 之前直接抛可操作错误；compose_report 的 try/except 会降级
        # []，直接调用方则看到明确指引。
        if os.environ.get("EMBEDDING_COLLECTION", EMPTY_DEFAULT_COLLECTION) == EMPTY_DEFAULT_COLLECTION:
            raise RuntimeError(
                f"EMBEDDING_COLLECTION 未设置或指向空库 {EMPTY_DEFAULT_COLLECTION}；"
                f"请设为 {REAL_BOOKS_COLLECTION}（27115条古籍库）"
            )
        # 生产懒加载（注入优先，避免测试加载重模型）
        from src.rag.retriever import Retriever  # 只读复用
        from src.rag.embedder import Embedder
        embedder = Embedder(model_name="BAAI/bge-m3")
        embedder.load()
        from src.config import load_settings  # 无模块级单例，按 main.py:627 先例
        _settings = load_settings()
        self._retriever = Retriever(str(_settings.vectordb_dir), embedder)
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
