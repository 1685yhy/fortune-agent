"""覆盖清单：规则实现与考卷规模，写入 out/coverage.json。

阶段3 Task 8：追加 phase3 段（四体系规则数/考卷数/门禁摘要）；
阶段4 Task 4：追加 phase4 段（合成层规则数/考卷数/门禁摘要）。
`python -m src.engine.coverage` 可再生成（含历史段，保持口径）。
"""
from __future__ import annotations

import json
from pathlib import Path

# 阶段2 段（2026-08-15 收官定稿，纳入脚本保持 coverage.json 可再生成）
PHASE2 = {
    "deduction_steps": 6,
    "deduction_steps_note": "pills-only 链实际 6 步；第 7 步'大运流年.engine'仅 engine_result 非空时条件追加",
    "evidence": "in",
    "report": "in",
    "e2e_cases": 25,
    "gate": "9/9 + e2e 25/25",
}

# 阶段3 段（2026-08-16 定稿，纳入脚本保持再生成不丢历史）
PHASE3 = {
    "rules": {"ziwei": 11, "liuyao": 11, "qimen": 10, "liuren": 9},
    "cases": {"ziwei": 8, "liuyao": 8, "qimen": 9, "liuren": 12, "e2e_phase3": 4},
    "gate": "回归122/122全绿 + unit 8/8/9/12 + e2e 4/4(四体系) + 八字25/25不破"
            " + 真实冒烟四体系链5步非空 + LLM冒烟真实输出(deepseek-flash)",
}


def write_coverage(rules: dict[str, int], case_counts: dict[str, int],
                   out: str = "src/engine/out/coverage.json",
                   phase3: dict | None = PHASE3,
                   phase4: dict | None = None) -> dict:
    """生成覆盖清单；phase3/phase4 段为阶段3/4 门禁新增，传 None 则不带。"""
    data = {
        "phase": "阶段0+1+2+3+4",
        "rules": rules,
        "cases": case_counts,
        "audit": "2026-08-16 阶段3+4门禁全量跑分",
        "phase2": PHASE2,
    }
    if phase3 is not None:
        data["phase3"] = phase3
    if phase4 is not None:
        data["phase4"] = phase4
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


if __name__ == "__main__":
    # 阶段4 门禁（Task 4）再生成：含 phase2+phase3+phase4 三段，历史口径不丢
    write_coverage(
        {"shishen": 3, "geju": 4, "shensha": 2},
        {"tiandisui": 513, "geju": 4, "shensha": 2, "qiongtong_cells": 120},
        phase3=PHASE3,
        phase4={
            "rules": {"synth_judgements": 3, "synth_keys": 2, "synth_extractors": 5},
            "cases": {"synth_cases": 8, "synth_e2e": 2},
            "gate": "回归138/138全绿 + 合成考卷8/8(共识3/分歧3/不可比较2) + e2e真实链合成2/2"
                    "(三分类非空无硬造共识) + 真实冒烟bazi7步/ziwei5步/liuyao5步合成三分类"
                    "共识1分歧1不可比较3 + LLM冒烟真实输出(compose_multi_report 4864字符)",
        },
    )
    print("coverage.json 已生成（含 phase2+phase3+phase4 段）")
