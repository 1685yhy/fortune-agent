# src/engine/report.py
"""综合层：推演链 + 证据 → LLM 生成带出处解读（链注入 extra_system_prompt，零改 client.py）。"""
from __future__ import annotations

from dataclasses import dataclass

from src.engine.deduction import DeductionChain


@dataclass
class ReportResult:
    analysis: str
    chain: DeductionChain
    citations: list
    model: str
    tokens_used: int


def compose_report(chain: DeductionChain, question: str, llm=None,
                   evidences: list | None = None, chart_str: str | None = None) -> ReportResult:
    """llm 缺省时生产 FortuneLLM（DEEPSEEK 环境就绪才可真实调用）。
    evidences 缺省时走 EvidenceProvider 举证。"""
    if evidences is None:
        from src.engine.evidence import EvidenceProvider
        try:
            evidences = EvidenceProvider().gather(chain, question=question)
        except Exception:
            evidences = []   # 检索不可用 → 降级无引用（诚实标注）

    if llm is None:
        from src.config import load_settings  # 无模块级单例，按 main.py:627 先例
        from src.llm.client import FortuneLLM
        # 按 main.py:764 先例构造：api_key 必填无默认值，其余参数与生产入口保持一致
        _settings = load_settings()
        llm = FortuneLLM(api_key=_settings.claude_api_key,
                         model="deepseek-v4-flash",
                         deep_model="deepseek-v4-pro",
                         provider="deepseek")

    extra = ("## 推演链（引擎逐步推理记录，可审计）\n" + chain.to_text() +
             "\n\n请基于推演链与古籍依据回答，每条关键结论标注引用编号[n]。"
             "推演链未覆盖处如实说明，不得编造。")
    chart = chart_str or " ".join(chain.pills)
    result = llm.analyze(chart, list(evidences), question,
                         extra_system_prompt=extra)
    citations = [{"text": getattr(r, "text", ""), "source": getattr(r, "source", ""),
                  "score": getattr(r, "score", 0.0)} for r in (evidences or [])]
    return ReportResult(analysis=result.response, chain=chain,
                        citations=citations, model=result.model,
                        tokens_used=result.tokens_used)
