from src.engine.baseline import BaselinePipeline


class FakeEngine:
    def calculate(self, year, month, day, hour, minute, city, gender):
        return type("R", (), {"day_master": "甲木", "bazi": ["庚午", "乙酉", "甲午", "丁卯"]})()


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def search(self, query, category=None, top_k=20, min_score=0.3):
        self.calls.append((query, category, top_k))
        return [type("CR", (), {"text": "古籍原文", "source": "穷通宝鉴", "score": 0.8,
                                "chunk_id": "c1", "category": "bazi"})()]


class FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        assert extra_system_prompt is None, "基线不得注入推演链"
        return type("AR", (), {"response": "基线回答", "tokens_used": 100, "model": "fake"})()


def test_baseline_runs_with_injected_fakes():
    fake_r = FakeRetriever()
    fake_l = FakeLLM()
    pipe = BaselinePipeline(engine=FakeEngine(), retriever=fake_r, llm=fake_l)
    result = pipe.run({"year": 1974, "month": 4, "day": 28, "hour": 16, "minute": 40,
                       "city": "usa", "gender": "男"}, "今年财运如何？")
    assert result.analysis == "基线回答"
    assert fake_r.calls[0][0] == "甲木 今年财运如何？"   # 生产查询形态：日主+问题
    assert fake_r.calls[0][1] == "bazi"                  # 生产分类参数
    assert fake_r.calls[0][2] == 15                      # 生产 top_k
    assert result.query == "甲木 今年财运如何？"
