#!/usr/bin/env python3
"""Build the paipan (四柱排盘) standard-answer subset from 问真 real data.

Task batch2-D1: sample a deterministic subset of rows from the 问真 crawled
data (data/wenzhen_charts.db, charts table, 44,493 rows) and write it as
tests/standard_answers/paipan_answers.jsonl.

Sampling strategy (deterministic, rerunnable):
  * Universe: rows with complete anchors (minggong/taiyuan/lunar/jiaoyun all
    non-empty) AND gender text consistent with gender_code (女->0, 男->1).
    8,658 rows qualify (the 35,835 incomplete rows are exactly the rows whose
    gender_code disagrees with gender text — a crawl artifact).
  * All 60 day pillars are covered (each gets >= 1 entry).
  * The 12 (gender x hour_branch) combos are spread evenly: a fixed seed
    shuffles a priority list of the 12 combos, then each pillar (sorted index
    i) walks the list starting at combo i % 12. Layers 0-2 therefore hit all
    60 pillars with each combo used exactly 5 times per layer, so every
    combo ~ gender x branch is used ~equally, and no (day_pillar, gender,
    hour_branch) combo is duplicated across the whole subset.
  * The data only contains 6 of the 12 hour branches (子卯辰午申酉; 丑寅巳未戌亥
    absent); the 6 present branches are distributed evenly, absent ones are
    skipped and reported.
  * Within a combo, the row is picked by a stable hash of (pillar, gender,
    branch) plus a per-combo usage counter, walking candidates sorted by
    (year, date, time) -> deterministic and spreads years/eras.

Usage:
  python scripts/build_answer_bank.py                # default: 200 entries
  python scripts/build_answer_bank.py --count 300    # other size in [150,300]
  python scripts/build_answer_bank.py --check        # re-validate the JSONL on disk

Reads data/wenzhen_charts.db strictly read-only (sqlite URI mode=ro); never
writes to the crawl data. Stdlib only.
"""
import argparse
import hashlib
import json
import random
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "wenzhen_charts.db"
DEFAULT_OUT = ROOT / "tests" / "standard_answers" / "paipan_answers.jsonl"

DEFAULT_COUNT = 200          # target band 150~300 per brief
MIN_TARGET, MAX_TARGET = 150, 300
MAX_ENTRIES = 60 * 12        # 60 day pillars x 12 (gender x hour_branch) combos
DEFAULT_SEED = 20260827

BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
GENDERS = ("男", "女")
GENDER_CODE = {"男": 1, "女": 0}

# Anchors every entry must carry (the D2 assertion contract).
REQUIRED_EXPECTED = (
    "year_pillar", "month_pillar", "day_pillar", "hour_pillar",
    "day_master", "day_branch", "dayun_first3", "qiyunsui",
    "minggong", "taiyuan",
)


def load_rows():
    """Load the sampling universe from the DB (read-only connection)."""
    if not DB_PATH.exists():
        raise SystemExit(f"missing DB: {DB_PATH}")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT id, date, time, gender, gender_code,
                   year_pillar, month_pillar, day_pillar, hour_pillar,
                   day_master, day_branch, dayun, qiyunsui,
                   minggong, taiyuan, lunar, source
            FROM charts
            WHERE minggong IS NOT NULL AND minggong != ''
              AND taiyuan  IS NOT NULL AND taiyuan  != ''
              AND lunar    IS NOT NULL AND lunar    != ''
              AND jiaoyun  IS NOT NULL AND jiaoyun  != ''
              AND ((gender = '女' AND gender_code = 0)
                OR (gender = '男' AND gender_code = 1))
            ORDER BY date, time, id
            """
        ).fetchall()
    finally:
        con.close()
    return [dict(zip(
        ("id", "date", "time", "gender", "gender_code",
         "year_pillar", "month_pillar", "day_pillar", "hour_pillar",
         "day_master", "day_branch", "dayun", "qiyunsui",
         "minggong", "taiyuan", "lunar", "source"), r)) for r in rows]


def _stable_hash(key):
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big")


def sample(rows, count, seed):
    """Deterministic sampling; returns list of entry dicts."""
    if not (60 <= count <= MAX_ENTRIES):
        raise SystemExit(
            f"--count must be in [60, {MAX_ENTRIES}] (60 pillars, 720 combos max); got {count}")

    by_pillar = {}
    for r in rows:
        by_pillar.setdefault(r["day_pillar"], []).append(r)
    pillars = sorted(by_pillar)  # stable iteration order
    if len(pillars) < 60:
        raise SystemExit(f"universe covers only {len(pillars)}/60 day pillars")

    # Per-pillar budget: base + seeded extras.
    rng = random.Random(seed)
    base, rem = divmod(count, 60)
    extra_pillars = set(rng.sample(pillars, rem))
    alloc = {p: base + (1 if p in extra_pillars else 0) for p in pillars}

    # Combo order: seeded shuffle of the (gender x present_branch) combos —
    # exactly 12 items (6 branches x 2 genders), one per combo. The per-pillar
    # start offset rotates within it (pillar at sorted index i starts at
    # combo_order[i % len(combo_order)]), so across 60 pillars every combo is
    # used ~equally (layers 0-2 hit all 60 pillars -> 5 uses per combo each).
    present_branches = sorted(
        {r["hour_pillar"][1] for r in rows}, key=BRANCHES.index)
    combo_order = [(g, b) for g in GENDERS for b in present_branches]
    rng.shuffle(combo_order)
    # Each pillar holds candidates grouped by (gender, hour_branch).
    groups = {}
    for p in pillars:
        g = {}
        for r in by_pillar[p]:
            g.setdefault((r["gender"], r["hour_pillar"][1]), []).append(r)
        groups[p] = g

    used = {}  # combo -> number of rows taken so far (spreads years across pillars)
    entries = []
    for i, p in enumerate(pillars):
        k = alloc[p]
        j = 0
        while k > 0 and j < 12:
            gender, branch = combo_order[(i + j) % 12]
            cands = groups[p].get((gender, branch))
            if cands:
                key = f"{p}|{gender}|{branch}"
                idx = (_stable_hash(key) + used.get(key, 0)) % len(cands)
                used[key] = used.get(key, 0) + 1
                entries.append(_to_entry(cands[idx], key, len(entries)))
                k -= 1
            j += 1
        if k > 0:  # cannot happen: every pillar has all 12 combos, but guard
            raise SystemExit(f"pillar {p}: only {alloc[p] - k}/{alloc[p]} combos available")

    if len(entries) != count:
        raise SystemExit(f"expected {count} entries, got {len(entries)}")
    return entries


def _to_entry(r, sample_key, n):
    dayun = json.loads(r["dayun"]) if isinstance(r["dayun"], str) else r["dayun"]
    return {
        "id": f"paipan-{n + 1:06d}",
        "source": r["source"],
        "input": {
            "date": r["date"],
            "time": r["time"],
            "gender": r["gender"],
            "gender_code": r["gender_code"],
            "lunar": r["lunar"],
        },
        "expected": {
            "year_pillar": r["year_pillar"],
            "month_pillar": r["month_pillar"],
            "day_pillar": r["day_pillar"],
            "hour_pillar": r["hour_pillar"],
            "day_master": r["day_master"],
            "day_branch": r["day_branch"],
            "dayun_first3": dayun[:3],
            "qiyunsui": r["qiyunsui"],
            "minggong": r["minggong"],
            "taiyuan": r["taiyuan"],
        },
        "meta": {
            "sample_key": sample_key,
            "gender": r["gender"],
            "hour_branch": r["hour_pillar"][1],
            "year": int(r["date"][:4]),
        },
    }


def validate_entries(entries, strict=True):
    """Step-3 validation. Returns list of problem strings (empty = ok)."""
    problems = []
    n = len(entries)
    if not (MIN_TARGET <= n <= MAX_TARGET):
        problems.append(f"entry count {n} outside target band [{MIN_TARGET}, {MAX_TARGET}]")
    ids, dedup_keys = set(), set()
    for e in entries:
        if e["id"] in ids:
            problems.append(f"duplicate id {e['id']}")
        ids.add(e["id"])
        # dedup key: day_pillar x gender x hour_branch (brief Step 3)
        dk = (e["expected"]["day_pillar"], e["meta"]["gender"], e["meta"]["hour_branch"])
        if dk in dedup_keys:
            problems.append(f"duplicate dedup key {dk} (id {e['id']})")
        dedup_keys.add(dk)
        for f in REQUIRED_EXPECTED:
            v = e["expected"][f]
            if v in (None, "", []):
                problems.append(f"{e['id']}: expected.{f} empty")
        if len(e["expected"]["dayun_first3"]) != 3:
            problems.append(f"{e['id']}: dayun_first3 != 3 items")
        if not isinstance(e["expected"]["qiyunsui"], int) or not (0 <= e["expected"]["qiyunsui"] <= 11):
            problems.append(f"{e['id']}: qiyunsui not int in [0,11]: {e['expected']['qiyunsui']!r}")
        for f in ("year_pillar", "month_pillar", "day_pillar", "hour_pillar", "minggong", "taiyuan"):
            v = e["expected"][f]
            if len(v) != 2:
                problems.append(f"{e['id']}: {f} not 2 chars: {v!r}")
        if len(e["expected"]["day_master"]) != 1 or len(e["expected"]["day_branch"]) != 1:
            problems.append(f"{e['id']}: day_master/day_branch not 1 char")
        if e["expected"]["day_master"] != e["expected"]["day_pillar"][0]:
            problems.append(f"{e['id']}: day_master != day_pillar[0]")
        if e["meta"]["hour_branch"] != e["expected"]["hour_pillar"][1]:
            problems.append(f"{e['id']}: hour_branch != hour_pillar[1]")
        if GENDER_CODE.get(e["meta"]["gender"]) != e["input"]["gender_code"]:
            problems.append(f"{e['id']}: gender/gender_code inconsistent")
        if not e["input"]["date"] or not e["input"]["time"] or not e["input"]["gender"]:
            problems.append(f"{e['id']}: input fields incomplete")
        if not e["source"]:
            problems.append(f"{e['id']}: source empty")
    if problems and strict:
        raise SystemExit("validation failed:\n  " + "\n  ".join(problems[:30]))
    return problems


def coverage_report(entries):
    """Markdown coverage report: day pillars / gender / hour branches / eras."""
    pillars = {}
    genders = {}
    branches = {}
    eras = {}
    for e in entries:
        pillars[e["expected"]["day_pillar"]] = pillars.get(e["expected"]["day_pillar"], 0) + 1
        genders[e["meta"]["gender"]] = genders.get(e["meta"]["gender"], 0) + 1
        branches[e["meta"]["hour_branch"]] = branches.get(e["meta"]["hour_branch"], 0) + 1
        era = (e["meta"]["year"] // 10) * 10
        eras[era] = eras.get(era, 0) + 1
    n = len(entries)
    lines = [
        f"# Paipan standard-answer subset coverage (n={n})",
        "",
        f"- seed: {DEFAULT_SEED}  |  target band: [{MIN_TARGET}, {MAX_TARGET}]",
        f"- day pillars covered: {len(pillars)}/60"
        f"  (per-pillar min {min(pillars.values())}, max {max(pillars.values())})",
        f"- gender: 男 {genders.get('男', 0)} 女 {genders.get('女', 0)}",
        "- hour branches: " + ", ".join(
            f"{b}×{branches.get(b, 0)}" for b in BRANCHES if branches.get(b)),
        "  (absent from source data: " +
        "".join(b for b in BRANCHES if b not in branches) + ")",
        "- eras (year decade): " + ", ".join(
            f"{d}s×{eras[d]}" for d in sorted(eras)),
        "",
        "## Data-source notes (44,493 rows in charts table)",
        "- 8,658 rows have complete anchors (minggong/taiyuan/lunar/jiaoyun non-empty);",
        "  the other 35,835 rows lack those anchors and were excluded.",
        "- The 35,835 incomplete rows are exactly the rows whose gender_code",
        "  disagrees with gender text (男/0 crawl artifact); the sample universe",
        "  is therefore gender-consistent by construction.",
        "- Hour branches present in data: 子(00:00/23:00) 卯(06:00) 辰(08:00)",
        "  午(12:00) 申(16:00) 酉(18:00); 丑寅巳未戌亥 absent.",
    ]
    return "\n".join(lines)


def write_jsonl(entries, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def check_file(out):
    """--check mode: validate the JSONL on disk without touching the DB."""
    if not out.exists():
        raise SystemExit(f"missing output file: {out}")
    entries = [json.loads(line) for line in open(out, encoding="utf-8") if line.strip()]
    problems = validate_entries(entries, strict=False)
    if problems:
        raise SystemExit(f"check failed ({len(problems)} problems):\n  " + "\n  ".join(problems[:20]))
    print(f"check OK: {len(entries)} entries, all fields valid, no duplicates")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT,
                    help=f"target entry count (default {DEFAULT_COUNT}, band {MIN_TARGET}-{MAX_TARGET})")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED,
                    help=f"deterministic sampling seed (default {DEFAULT_SEED})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--report", type=Path, default=None,
                    help="optional path to write the coverage report (markdown)")
    ap.add_argument("--check", action="store_true",
                    help="validate the existing output file and exit")
    args = ap.parse_args()

    if args.check:
        check_file(args.out)
        return

    rows = load_rows()
    entries = sample(rows, args.count, args.seed)
    validate_entries(entries)                      # strict: exit non-zero on problems
    write_jsonl(entries, args.out)
    print(coverage_report(entries))
    print(f"\nwrote {len(entries)} entries -> {args.out}")
    if args.report:
        args.report.write_text(coverage_report(entries) + "\n", encoding="utf-8")
        print(f"coverage report -> {args.report}")
    check_file(args.out)


if __name__ == "__main__":
    main()
