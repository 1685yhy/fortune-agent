"""覆盖清单：规则实现与考卷规模，写入 out/coverage.json。"""
from __future__ import annotations

import json
from pathlib import Path


def write_coverage(rules: dict[str, int], case_counts: dict[str, int],
                   out: str = "src/engine/out/coverage.json") -> dict:
    data = {
        "phase": "阶段0+1",
        "rules": rules,
        "cases": case_counts,
        "audit": "2026-08-15 全量跑分",
    }
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data
