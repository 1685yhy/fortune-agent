from src.engine.deduction import deduce
from src.engine.evidence import EvidenceProvider


class FakeRetriever:
    def __init__(self):
        self.queries = []

    def search(self, query, category=None, top_k=20, min_score=0.3):
        self.queries.append(query)
        return [type("CR", (), {"text": f"依据-{query[:6]}", "source": "穷通宝鉴",
                                "score": 0.85, "chunk_id": f"c{len(self.queries)}",
                                "category": category})()]


def test_gather_builds_queries_from_chain():
    fake = FakeRetriever()
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"], question="今年财运如何？")
    provider = EvidenceProvider(retriever=fake)
    results = provider.gather(chain, question="今年财运如何？")
    assert results, "举证结果非空"
    assert fake.queries, "必须发起检索"
    assert all(hasattr(r, "text") and hasattr(r, "source") for r in results)
    assert "甲木" in fake.queries[0] or "甲" in fake.queries[0]

def test_gather_dedupe_and_limit():
    fake = FakeRetriever()
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"])
    provider = EvidenceProvider(retriever=fake, top_k=3)
    results = provider.gather(chain)
    ids = [r.chunk_id for r in results]
    assert len(ids) == len(set(ids)), "按 chunk_id 去重"
    assert len(results) <= 3


def test_gather_empty_collection_guard_raises(monkeypatch):
    """显式指向已知空集合时，守卫必须在构造真实 Retriever 前抛 RuntimeError
    给出可操作指引（指向 fortune_books_v2）。

    k24 语义变更：判据从「EMBEDDING_COLLECTION 未设置」改为「解析后的集合名落在
    已知空集合里」。因为配置已能真正生效（yaml + dataclass 默认值都指向
    fortune_books_v2），「未设环境变量」不再是错误状态；显式指向空集合才是。
    """
    import pytest
    monkeypatch.setenv("EMBEDDING_COLLECTION", "fortune_books")
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"])
    provider = EvidenceProvider()  # 不注入 fake，走真实懒加载路径触发守卫
    with pytest.raises(RuntimeError) as excinfo:
        provider.gather(chain)
    assert "fortune_books_v2" in str(excinfo.value)


def test_default_settings_never_point_at_empty_collection(monkeypatch):
    """k24 回归护栏：不设任何 env 时，load_settings 解析出的集合名必须不是
    已知空集合（曾经默认值 fortune_books 实测 0 条 → 8 项能力 refs=0）。"""
    from src.book_categories import BOOKS_COLLECTION, KNOWN_EMPTY_COLLECTIONS
    from src.config import load_settings

    monkeypatch.delenv("EMBEDDING_COLLECTION", raising=False)
    resolved = load_settings().embedding_collection
    assert resolved not in KNOWN_EMPTY_COLLECTIONS, \
        f"默认集合落到已知空集合 {resolved}（线上 refs=0 事故根因）"
    assert resolved == BOOKS_COLLECTION
