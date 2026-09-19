#!/usr/bin/env python3
"""单字意象「裸梦见X」覆盖率的改前/改后实测（k58 M-1 验收）。

改前 = 上一版规则表（默认从 git 取 `55ea3f7:src/engines/dream_rules.py`）
改后 = 当前工作区规则表（collocation.py 统计产出 + 边界护栏）

指标：裸查询「梦见X」跑到 `DreamEngine.analyze` 后，
`梦境类型 / 核心象征 / 情绪基调` **三项全非空** 的比例。

用法：
    python scripts/k55_dream/measure_single_char.py [--old-rules /tmp/old_rules.py]
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream.crawl_lib import DATA_ROOT, now_iso  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class _NullRetriever:
    def search(self, query, top_k=5, **kw):
        return []


def load_old_rules(path: Path) -> list:
    spec = importlib.util.spec_from_file_location("old_dream_rules", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DREAM_PATTERN_RULES


def measure(names: list, rules: list) -> dict:
    import src.engines.dream as dream_mod
    saved = dream_mod.DREAM_PATTERN_RULES
    dream_mod.DREAM_PATTERN_RULES = rules
    try:
        engine = dream_mod.DreamEngine()
        rows = []
        for n in names:
            res = engine.analyze(f"梦见{n}", _NullRetriever())
            ok = bool(res.dream_type) and bool(res.symbols) and bool(res.tones)
            rows.append({"element": n, "nonempty": ok, "type": res.dream_type,
                         "hits": res.rule_hits,
                         "symbols": res.symbols[:3], "tones": res.tones[:1]})
        return {"rows": rows,
                "rate": round(sum(1 for r in rows if r["nonempty"]) / max(1, len(rows)), 4)}
    finally:
        dream_mod.DREAM_PATTERN_RULES = saved


def main() -> int:
    ap = argparse.ArgumentParser(description="单字意象覆盖率改前/改后")
    ap.add_argument("--old-rules", default="")
    ap.add_argument("--out", default=str(DATA_ROOT / "reports" / "single_char_coverage.json"))
    args = ap.parse_args()

    from src.engines.dream_rules import DREAM_PATTERN_RULES as new_rules

    old_path = Path(args.old_rules) if args.old_rules else Path("/tmp/k58_old_rules.py")
    if not old_path.exists():
        old_path.write_text(
            subprocess.run(["git", "show", "55ea3f7:src/engines/dream_rules.py"],
                           cwd=REPO, capture_output=True, text=True, check=True).stdout,
            encoding="utf-8")
    old_rules = load_old_rules(old_path)

    old_singles = [r["name"] for r in old_rules if len(r["name"]) == 1]
    new_singles = [r["name"] for r in new_rules if len(r["name"]) == 1]
    # 对比口径：**改前那批单字元素**（基准集），以及各自的全集
    old_res = measure(old_singles, old_rules)
    new_res_same_set = measure(old_singles, new_rules)
    new_res_all = measure(new_singles, new_rules)

    table = []
    for o, n in zip(old_res["rows"], new_res_same_set["rows"]):
        table.append({"element": o["element"], "before": o["nonempty"],
                      "after": n["nonempty"], "after_type": n["type"],
                      "after_hits": n["hits"]})
    improved = [t for t in table if t["after"] and not t["before"]]

    out = {
        "generated_at": now_iso(),
        "old_rules_source": str(old_path),
        "old_single_count": len(old_singles),
        "new_single_count": len(new_singles),
        "baseline_set_before_rate": old_res["rate"],
        "baseline_set_after_rate": new_res_same_set["rate"],
        "all_new_singles_rate": new_res_all["rate"],
        "improved_count": len(improved),
        "table": table,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    csv_path = DATA_ROOT / "reports" / "single_char_coverage.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["element", "before_nonempty", "after_nonempty", "after_type", "after_hits"])
        for t in table:
            w.writerow([t["element"], t["before"], t["after"], t["after_type"],
                        "|".join(t["after_hits"])])

    print(f"改前单字规则 {len(old_singles)} 条；改后单字规则 {len(new_singles)} 条")
    print(f"同一批单字元素 裸「梦见X」三项非空："
          f"{old_res['rate']:.1%} → {new_res_same_set['rate']:.1%}"
          f"（提升 {len(improved)} 个）")
    print(f"改后全部单字元素（含新增）：{new_res_all['rate']:.1%}")
    print(f"表 → {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
