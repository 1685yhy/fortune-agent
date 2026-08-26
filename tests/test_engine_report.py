# tests/test_engine_report.py
from src.engine.deduction import DeductionChain, DeductionStep, deduce
from src.engine.report import compose_multi_report, compose_report
from src.engine.synth import SystemResult, synthesize


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


# ---------- 阶段4 Task 2：多体系合成报告 ----------

def _synth_chain(steps):
    chain = DeductionChain(input={}, pills=["庚午", "辛巳", "乙酉", "甲申"])
    for i, (rule, fact, output, source) in enumerate(steps, 1):
        chain.append(DeductionStep(i, rule, fact, output, source, ""))
    return chain


def _two_system_results():
    """八字用神水 vs 紫微金四局（无共识、有分歧、不可比较为空）。"""
    bazi = SystemResult(system="bazi", chain=_synth_chain([
        ("qiongtong_table[乙][巳]", "日干乙 × 月支巳",
         "四月乙木，专用癸水为尊", "穷通宝鉴·乙·巳月"),
    ]))
    ziwei = SystemResult(system="ziwei", chain=_synth_chain([
        ("ziwei.wuxing_ju_of", "命宫申干支纳音定局（壬申）", "金四局", "紫微斗数全书·安星诀"),
    ]))
    return [bazi, ziwei]


def test_compose_multi_report_sections():
    """多体系报告：各体系节→共识节→分歧节→不可比较节结构完整。"""
    results = _two_system_results()
    synth = synthesize(results)
    text = compose_multi_report(results, synth)
    assert "## 各体系" in text
    assert "### 八字（bazi）" in text
    assert "### 紫微（ziwei）" in text
    assert "事实要点" in text
    assert "推演链" in text
    assert "## 共识" in text
    assert "无跨体系共识（不硬造）" in text
    assert "## 分歧" in text
    assert "五行不同：水、金" in text
    assert "两说并存，各带出处，由用户结合实际情况权衡" in text
    assert "## 不可比较" in text
    assert "无不可比较项" in text


def test_compose_multi_report_consensus_section_lists_sources():
    """共识节：共识点 + 参与体系 + 各自出处证据。"""
    bazi = SystemResult(system="bazi", chain=_synth_chain([
        ("qiongtong_table[乙][巳]", "日干乙 × 月支巳",
         "四月乙木，专用癸水为尊", "穷通宝鉴·乙·巳月"),
    ]))
    ziwei = SystemResult(system="ziwei", chain=_synth_chain([
        ("ziwei.wuxing_ju_of", "命宫酉干支纳音定局（乙酉）", "水二局", "紫微斗数全书·安星诀"),
    ]))
    text = compose_multi_report([bazi, ziwei], synthesize([bazi, ziwei]))
    assert "五行一致：水（参与：八字、紫微）" in text
    assert "八字：四月乙木，专用癸水为尊（出处：穷通宝鉴·乙·巳月）" in text
    assert "紫微：水二局（出处：紫微斗数全书·安星诀）" in text


def test_compose_multi_report_llm_invoked():
    """llm 提供时真实调用并追加「LLM 综合解读」节。"""
    fake = FakeLLM()
    results = _two_system_results()
    text = compose_multi_report(results, synthesize(results), llm=fake,
                                question="今年财运如何？")
    assert fake.calls, "llm 必须被调用"
    assert "推演链" in fake.calls[0]["extra"] or "共识" in fake.calls[0]["extra"]
    assert "## LLM 综合解读" in text
    assert "综合解读……（依据：古籍）" in text


def test_compose_multi_report_llm_failure_recorded():
    """llm 调用失败 → 如实记录原因，不硬过（确定性合成结果仍完整）。"""

    class BoomLLM:
        def analyze(self, *args, **kwargs):
            raise RuntimeError("key 缺失/服务不可用")

    results = _two_system_results()
    text = compose_multi_report(results, synthesize(results), llm=BoomLLM())
    assert "## LLM 综合解读（失败：key 缺失/服务不可用" in text
    assert "## 分歧" in text  # 确定性部分不受影响

