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


class CapturingLLM:
    """捕获构造 kwargs 的 FortuneLLM 替身：验证 llm 缺省路径按 main.py 先例传 api_key。"""

    captured_kwargs = {}

    def __init__(self, **kwargs):
        CapturingLLM.captured_kwargs = kwargs

    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        return type("AR", (), {"response": "缺省LLM解读", "tokens_used": 1,
                               "model": "fake"})()


def test_compose_report_default_llm_passes_api_key(monkeypatch):
    # report.py 在函数内懒加载 `from src.llm.client import FortuneLLM`，
    # 符号解析落在 src.llm.client 模块上，故 monkeypatch 该模块属性。
    # settings 无模块级单例，按 main.py 先例（main.py:627）load_settings() 获取。
    from src.config import load_settings
    settings = load_settings()
    monkeypatch.setattr("src.llm.client.FortuneLLM", CapturingLLM)
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"])
    compose_report(chain, "", llm=None, evidences=[])
    assert CapturingLLM.captured_kwargs["api_key"] == settings.claude_api_key
