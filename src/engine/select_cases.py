"""对比案例选择：mingli 代表 4 条（按 e2e 顺序 1/2/5/10，覆盖类别分布）+ 滴天髓 pills-only 1 条。"""
import json
from pathlib import Path

_QUESTIONS_SRC = Path("/mnt/d/fortune-data/books/mingli_bench/data.json")


def _load_questions() -> dict:
    """mingli_bench 真实问题表：data.json questions 数组按 id 映射 question（e2e 转换时未写 question）。"""
    if not _QUESTIONS_SRC.exists():
        return {}
    try:
        data = json.loads(_QUESTIONS_SRC.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {q.get("id"): q.get("question") for q in data.get("questions", []) if q.get("id")}


def select_cases(e2e_path: str, tds_path: str) -> list[dict]:
    e2e_cases = [json.loads(l) for l in Path(e2e_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    mingli = [c for c in e2e_cases if c.get("source") == "mingli_bench"]
    picks = [mingli[i - 1] for i in (1, 2, 5, 10)]
    tds_all = [json.loads(l) for l in Path(tds_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    tds_pick = next((c for c in tds_all if c.get("id") == "tds_0001"), tds_all[0])
    questions = _load_questions()
    cases = []
    for c in picks:
        question = questions.get(c.get("source_lines")) or (
            c["question"] if "question" in c else "请分析此命整体运势"
        )
        cases.append({
            "id": c["id"], "source": "mingli_bench", "source_lines": c["source_lines"],
            "birth": c["expected"]["birth"], "question": question,
            "audit": "2026-08-15 对比案例（类别代表）",
        })
    cases.append({
        "id": tds_pick["id"], "source": "滴天髓阐微.txt", "source_lines": tds_pick["source_lines"],
        "pills": tds_pick["pills"], "prose": tds_pick["prose"][:120],
        "question": "请分析此命（古籍命例）",
        "audit": "2026-08-15 对比案例（pills-only）",
    })
    return cases


if __name__ == "__main__":
    from pathlib import Path as _P
    cases = select_cases("src/engine/cases/e2e_cases.jsonl", "src/engine/cases/tiandisui_cases.jsonl")
    out = _P("src/engine/cases/comparison_cases.json")
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"对比案例 {len(cases)} 条 -> {out}")
    for c in cases:
        print(c["id"], c["source"], c.get("birth", {}).get("year", "-"))
