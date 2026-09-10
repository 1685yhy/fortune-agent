"""检索式基线管线：复刻生产主链路（排盘→检索→LLM），无推演链注入——对比报告的对照组。"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.book_categories import BOOKS_COLLECTION, KNOWN_EMPTY_COLLECTIONS


@dataclass
class BaselineResult:
    chart: dict
    refs: list
    analysis: str
    model: str
    tokens_used: int
    query: str


class BaselinePipeline:
    def __init__(self, engine=None, retriever=None, llm=None):
        self._engine = engine
        self._retriever = retriever
        self._llm = llm

    def _get_engine(self):
        if self._engine is None:
            from src.engines.bazi import BaziEngine
            self._engine = BaziEngine()
        return self._engine

    def _get_retriever(self):
        if self._retriever is not None:
            return self._retriever
        # 与 evidence.py 同款空库守卫（k24 改版：判据为「解析后的集合名是
        # 已知空集合」，配置经 yaml/默认值已能生效，未设 env 不再是错误状态）
        from src.config import load_settings
        _settings = load_settings()
        if _settings.embedding_collection in KNOWN_EMPTY_COLLECTIONS:
            raise RuntimeError(
                f"embedding_collection 指向空库 {_settings.embedding_collection}；"
                f"请设为 {BOOKS_COLLECTION}（27115条古籍库）")
        from src.rag.retriever import Retriever
        from src.rag.embedder import Embedder
        embedder = Embedder(model_name="BAAI/bge-m3")
        embedder.load()
        self._retriever = Retriever(
            str(_settings.vectordb_dir), embedder,
            collection_name=_settings.embedding_collection,
        )
        return self._retriever

    def _get_llm(self):
        if self._llm is None:
            from src.llm.client import FortuneLLM
            from src.config import load_settings
            s = load_settings()
            self._llm = FortuneLLM(api_key=s.claude_api_key, model="deepseek-flash",
                                   deep_model="deepseek-v4-pro", provider="deepseek")
        return self._llm

    def run(self, birth: dict, question: str) -> BaselineResult:
        """birth: {year, month, day, hour, minute, city, gender}"""
        engine = self._get_engine()
        result = engine.calculate(birth["year"], birth["month"], birth["day"],
                                  birth["hour"], birth["minute"],
                                  birth.get("city") or "", birth.get("gender") or "男")
        query = f"{result.day_master} {question}"
        refs = self._get_retriever().search(query, category="bazi", top_k=15)
        analysis = self._get_llm().analyze(result, refs, question)
        return BaselineResult(
            chart={"bazi": result.bazi, "day_master": result.day_master},
            refs=refs, analysis=analysis.response, model=analysis.model,
            tokens_used=analysis.tokens_used, query=query)
