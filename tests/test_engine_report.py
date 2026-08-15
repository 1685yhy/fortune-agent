# tests/test_engine_report.py
from src.engine.deduction import deduce
from src.engine.report import compose_report


class FakeLLM:
    def __init__(self):
        self.calls = []

    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        self.calls.append({"extra": extra_system_prompt, "refs": len(references)})
        return type("AR", (), {"response": "综合解读……（依据：古籍）",
                               "tokens_used": 120, "model": "fake"})()


def test_compose_report_injects_chain_and_refs():
    fake = FakeLLM()
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"], question="今年财运如何？")
    refs = [type("CR", (), {"text": "原文", "source": "穷通宝鉴", "score": 0.9,
                            "chunk_id": "c1", "category": "bazi"})()]
    report = compose_report(chain, "今年财运如何？", llm=fake, evidences=refs)
    assert report.analysis == "综合解读……（依据：古籍）"
    assert "正官格" in fake.calls[0]["extra"]  # 推演链注入（庚午/乙酉/甲午/丁卯 甲日酉月→正官格）
    assert "第1步" in fake.calls[0]["extra"]
    assert fake.calls[0]["refs"] == 1
    assert report.model == "fake"

def test_compose_report_no_evidence_still_works():
    fake = FakeLLM()
    chain = deduce(["辛未", "乙未", "庚辰", "丁亥"])
    report = compose_report(chain, "", llm=fake, evidences=[])
    assert report.analysis
    assert fake.calls[0]["refs"] == 0
