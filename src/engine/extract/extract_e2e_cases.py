"""e2e 考卷转换：mingli_bench 前20条 + 滴天髓 reference 5条 → e2e_cases.jsonl。
用法: python -m src.engine.extract.extract_e2e_cases"""
import json
from pathlib import Path

CASES_DIR = Path(__file__).parent.parent / "cases"
OUT = CASES_DIR / "e2e_cases.jsonl"


def convert_mingli(path: str, limit: int = 20) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = []
    for q in data["questions"][:limit]:
        b = q["birth_info"]
        items.append({
            "id": f"e2e_{q['id'].replace('ftb_', '')}",
            "source": "mingli_bench",
            "source_lines": q["id"],
            "pills": [],
            "gender": b["gender"],
            "expected": {"birth": {"year": b["year"], "month": b["month"],
                                   "day": b["day"], "hour": b["hour"],
                                   "minute": b["minute"], "city": b.get("location") or b.get("country") or "",
                                   "gender": b["gender"]}},
            "prose": "",
            "quality": "e2e",
            "audit": "2026-08-15 转换自 mingli_bench/data.json（不写 answer，避免预测判分）",
        })
    return items


def convert_tiandisui(path: str, limit: int = 5) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if len(items) >= limit:
                break
            c = json.loads(line)
            # 需求文字：prose 含"大运/岁运"字样（仅"大运"只能筛出3条，不足5条考卷）
            if c.get("quality") == "reference" and ("大运" in c.get("prose", "") or "岁运" in c.get("prose", "")):
                items.append({
                    "id": "e2e_tds_" + c["id"].split("_")[1],
                    "source": c["source"], "source_lines": c["source_lines"],
                    "pills": c["pills"], "gender": c.get("gender", ""),
                    "expected": {}, "prose": c["prose"][:200],
                    "quality": "e2e",
                    "audit": "2026-08-15 阶段1 reference 升级为 e2e（含大运断语）",
                })
    return items


if __name__ == "__main__":
    import sys
    mingli = sys.argv[1] if len(sys.argv) > 1 else "/mnt/d/fortune-data/books/mingli_bench/data.json"
    tds = sys.argv[2] if len(sys.argv) > 2 else str(CASES_DIR / "tiandisui_cases.jsonl")
    items = convert_mingli(mingli) + convert_tiandisui(tds)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"e2e 考卷 {len(items)} 条 -> {OUT}")
    print("mingli ids:", [i["id"] for i in items if i["source"] == "mingli_bench"])
    print("tds ids:", [i["id"] for i in items if i["source"] != "mingli_bench"])
