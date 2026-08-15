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
