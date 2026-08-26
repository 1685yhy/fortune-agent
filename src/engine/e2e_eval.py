"""e2e 跑分器：全链路(排盘→推演→举证→综合)逐条断言"全"与"稳"，不判预测对错。

阶段3 Task 7：按考卷 expected.system 路由各体系排盘/起卦，断言该体系步骤前缀存在、
链非空、断语要点非空（断言参数化）；八字路径保持阶段2 行为完全一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.case_loader import Case
from src.engine.deduction import DeductionChain, deduce
from src.engine.report import compose_report

# 体系 → 必须出现的步骤前缀（阶段3 Task 7 断言参数化；八字另要求穷通宝鉴查表）
SYSTEM_PREFIXES = {
    "bazi": ("geju.", "shishen."),
    "ziwei": ("ziwei.",),
    "liuyao": ("liuyao.",),
    "qimen": ("qimen.",),
    "liuren": ("liuren.",),
}


@dataclass
class E2EReport:
    passed: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)


def _pills_of(year: int, month: int, day: int, hour: int) -> list[str]:
    """公历 → 四柱（与各排盘引擎同一 lunar-python 口径）。"""
    from lunar_python import Solar
    bazi = Solar.fromYmdHms(year, month, day, hour, 0, 0).getLunar().getEightChar()
    return [bazi.getYear(), bazi.getMonth(), bazi.getDay(), bazi.getTime()]


def _build_chain(case: Case, system: str) -> DeductionChain:
    """按体系构建推演链：八字走 BaziEngine（pills-only 直用），紫微/奇门/六壬走各自
    排盘引擎，六爻用 expected.seed 固定起卦（可复现）。"""
    b = case.expected.get("birth")
    question = case.expected.get("question") or case.prose or ""
    if system == "bazi":
        if case.pills:
            return deduce(case.pills, engine_result=None, question="")
        if not b:
            raise ValueError(f"{case.id}: 八字 e2e 需要 pills 或 expected.birth")
        from src.engines.bazi import BaziEngine  # 只读复用
        result = BaziEngine().calculate(
            b["year"], b["month"], b["day"], b["hour"], b["minute"],
            b.get("city") or "", b.get("gender") or "男")
        return deduce(result.bazi, engine_result=result, question=question)
    if not b:
        raise ValueError(f"{case.id}: {system} 体系 e2e 需要 expected.birth")
    if system == "liuyao":
        seed = case.expected.get("seed")
        if seed is None:
            raise ValueError(f"{case.id}: 六爻 e2e 需要固定 expected.seed")
        from src.engines.liuyao import LiuyaoEngine
        result = LiuyaoEngine().cast(method="random", question=question, seed=seed)
    elif system == "ziwei":
        from src.engines.ziwei import ZiweiEngine
        result = ZiweiEngine().calculate(
            b["year"], b["month"], b["day"], b["hour"], b["minute"],
            b.get("city") or "", b.get("gender") or "男")
    elif system == "qimen":
        from src.engines.qimen import QimenEngine
        result = QimenEngine().calculate(
            b["year"], b["month"], b["day"], b["hour"], b.get("minute", 0),
            b.get("city") or "北京")
    elif system == "liuren":
        from src.engines.liuren import LiurenEngine
        result = LiurenEngine().calculate(
            b["year"], b["month"], b["day"], b["hour"], b.get("minute", 0),
            b.get("city") or "北京")
    else:
        raise ValueError(f"未知推演体系: {system}")
    return deduce(_pills_of(b["year"], b["month"], b["day"], b["hour"]),
                  engine_result=result, question=question, system=system)


def _check_chain(chain: DeductionChain, system: str) -> str | None:
    """按体系断言链完整性：体系前缀步骤存在、链非空、断语要点非空。返回 None=通过。

    八字沿用阶段2 断言（步数≥5 + geju./shishen./穷通宝鉴 + 断语要点）；其余体系
    断言该体系步骤前缀存在（ziwei./liuyao./qimen./liuren.）+ 链非空。
    """
    rules = [s.rule for s in chain.steps]
    if system == "bazi":
        if len(chain.steps) < 5:
            return f"推演步骤不足: {len(chain.steps)}"
    elif not chain.steps:
        return "推演链为空"
    for prefix in SYSTEM_PREFIXES.get(system, ()):
        if not any(r.startswith(prefix) for r in rules):
            return f"缺{prefix}步骤"
    if system == "bazi" and not any(r.startswith("qiongtong_table") for r in rules):
        return "缺调候用神步骤"
    duanyu_steps = [s for s in chain.steps if s.rule.endswith("断语要点.compose")]
    if not duanyu_steps:
        return "缺断语要点步骤"
    if not duanyu_steps[-1].output:
        return "断语要点为空"
    return None


def _run_one(case: Case, llm, use_real_retriever: bool) -> str | None:
    """返回 None=通过；否则返回失败原因。按 expected.system 路由各体系。"""
    system = case.expected.get("system") or "bazi"
    try:
        chain = _build_chain(case, system)
    except Exception as exc:
        return f"排盘/推演异常: {exc}"

    err = _check_chain(chain, system)
    if err:
        return err

    try:
        report = compose_report(chain, case.prose or case.id, llm=llm,
                                evidences=None if use_real_retriever else [])
    except Exception as exc:
        return f"综合层异常: {exc}"
    if not report.analysis:
        return "综合输出为空"
    return None


def run_e2e(cases: list[Case], llm, use_real_retriever: bool = False) -> E2EReport:
    report = E2EReport()
    for case in cases:
        err = _run_one(case, llm, use_real_retriever)
        if err:
            report.failed += 1
            report.details.append(f"{case.id}: {err}")
        else:
            report.passed += 1
    return report
