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


def compose_multi_report(results: list, synth, llm=None, question: str = "") -> str:
    """多体系合成报告（阶段4 Task 2）：各体系节→共识节→分歧节→不可比较节。

    results: list[SystemResult]；synth: SynthResult（synthesize 产物）。
    本函数为确定性 markdown 文本；llm 提供时追加「LLM 综合解读」节
    （真实调用，失败只记录原因不硬过）。compose_report（单体系）行为保持不变。
    """
    from src.engine.synth import SYSTEM_NAMES, extract_facts

    def _name(code: str) -> str:
        return SYSTEM_NAMES.get(code, code)

    lines = ["# 多体系合成报告",
             f"参与体系：{'、'.join(_name(s) for s in synth.systems)}", ""]

    # 1. 各体系节（体系名 + 事实要点 + 推演链摘要 + LLM 分析）
    lines.append("## 各体系")
    for r in results:
        lines.append(f"### {_name(r.system)}（{r.system}）")
        facts = extract_facts(r.system, r.chain)
        if facts:
            lines.append("事实要点：")
            for f in facts:
                src = f"（{f['source']}）" if f["source"] else ""
                lines.append(f"- {f['text']}{src}")
        lines.append("推演链：")
        lines.append(r.chain.to_text())
        if r.analysis:
            lines.append(f"LLM 分析：{r.analysis}")
        lines.append("")

    # 2. 共识节（共识点 + 参与体系 + 各自出处证据）
    lines.append("## 共识")
    if synth.consensus:
        for c in synth.consensus:
            names = "、".join(_name(s) for s in c["systems"])
            lines.append(f"- {c['point']}（参与：{names}）")
            for ev in c["evidence"]:
                lines.append(f"  - {_name(ev['system'])}：{ev['text']}（出处：{ev['source']}）")
    else:
        lines.append("- 无跨体系共识（不硬造）")

    # 3. 分歧节（话题 + 两说各自出处 + 固定说明）
    lines.append("## 分歧")
    if synth.divergences:
        for d in synth.divergences:
            lines.append(f"- {d['topic']}")
            for v in d["views"]:
                lines.append(f"  - {_name(v['system'])}：{v['view']}（出处：{v['source']}）")
            lines.append(f"  - 说明：{d['note']}")
    else:
        lines.append("- 无跨体系分歧")

    # 4. 不可比较节（如实列表）
    lines.append("## 不可比较")
    if synth.unresolved:
        lines += [f"- {u}" for u in synth.unresolved]
    else:
        lines.append("- 无不可比较项（各体系均有公共比较维度）")

    # 5. LLM 综合解读（key 门控：llm 提供则真实调用，失败只记录原因）
    if llm is not None:
        extra = ("\n".join(lines) +
                 "\n\n请基于上述合成结果回答用户问题，每条关键结论标注参与体系与出处；"
                 "无共识处如实说明两说并存，不得编造。")
        try:
            result = llm.analyze("多体系合成", [], question, extra_system_prompt=extra)
            lines.append("")
            lines.append("## LLM 综合解读")
            lines.append(getattr(result, "response", "") or "（LLM 未返回文本）")
        except Exception as exc:
            lines.append("")
            lines.append(f"## LLM 综合解读（失败：{exc}；本报告为确定性合成结果）")
    return "\n".join(lines)
