#!/usr/bin/env python3
"""
Bazi Accuracy Benchmark against 问真八字 (Wenzhen).

Compares our BaziEngine output against 8,658 reference records from wenzhen_charts.jsonl.
Tests six dimensions with weighted scoring:
  - 四柱 (pillars)     40%
  - 十神 (shishen)     20%
  - 藏干 (canggan)     15%
  - 纳音 (nayin)       10%
  - 大运 (dayun)       10%  (first 6 pillars)
  - 日主 (day_master)   5%

v5.0 acceptance criteria: >= 99.5% overall accuracy.
"""

import json
import random
import sys
import os
from datetime import datetime, timedelta, date
from collections import defaultdict
from typing import List, Tuple, Dict, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.engines.bazi import BaziEngine, TIANGAN, DIZHI, NAYIN

# ============================================================
# Constants
# ============================================================

WEIGHTS = {
    "pillars": 0.40,
    "shishen": 0.20,
    "canggan": 0.15,
    "nayin": 0.10,
    "dayun": 0.10,
    "day_master": 0.05,
}

WENZHEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "wenzhen", "wenzhen_charts.jsonl"
)
RESULTS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "eval", "results"
)

ACCEPTANCE_THRESHOLD = 99.5

# ============================================================
# Helper Functions
# ============================================================


def parse_time(time_str: str) -> Tuple[int, int]:
    """Parse HH:MM time string to (hour, minute)."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


def parse_date(date_str: str) -> Tuple[int, int, int]:
    """Parse YYYY-MM-DD date string to (year, month, day)."""
    parts = date_str.split("-")
    return int(parts[0]), int(parts[1]), int(parts[2])


def handle_late_zi_hour(year: int, month: int, day: int, hour: int, minute: int):
    """
    Handle 晚子时 (23:00-23:59): advance date by 1 day, use hour=0.
    In bazi theory, 23:00-23:59 belongs to the next day's 子时.
    """
    if hour >= 23:
        d = date(year, month, day) + timedelta(days=1)
        return d.year, d.month, d.day, 0, 0
    return year, month, day, hour, minute


def load_samples(
    filepath: str, num_samples: int = 500, seed: int = 42
) -> List[dict]:
    """Load random samples from wenzhen data with reproducibility."""
    random.seed(seed)
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    print(f"Loaded {len(records)} total records from {filepath}")
    samples = random.sample(records, min(num_samples, len(records)))
    print(f"Sampled {len(samples)} records (seed={seed})")
    return samples


def get_canggan_from_eightchar(ec) -> List[List[str]]:
    """
    Get 藏干 (hidden heavenly stems) for all four pillars
    using lunar-python's EightChar methods.
    """
    return [
        ec.getYearHideGan(),
        ec.getMonthHideGan(),
        ec.getDayHideGan(),
        ec.getTimeHideGan(),
    ]


def get_shishen_from_eightchar(ec) -> List[str]:
    """
    Get 十神 for all four pillars using lunar-python's EightChar methods.
    These are per-pillar heavenly stem relationships to the day master.
    Note: The third entry (day pillar) should be '日主'.
    """
    return [
        ec.getYearShiShenGan(),
        ec.getMonthShiShenGan(),
        "日主",  # day pillar shishen is always '日主'
        ec.getTimeShiShenGan(),
    ]


def compute_pillar_accuracy(
    actual_pillars: List[str], expected_pillars: List[str]
) -> Tuple[bool, int, List[dict]]:
    """Compare all four pillars. Returns (all_match, match_count, details)."""
    details = []
    match_count = 0
    all_match = True
    for i, (actual, expected) in enumerate(zip(actual_pillars, expected_pillars)):
        is_match = actual == expected
        if is_match:
            match_count += 1
        else:
            all_match = False
        details.append(
            {
                "pillar_index": i,
                "pillar_name": ["年柱", "月柱", "日柱", "时柱"][i],
                "expected": expected,
                "actual": actual,
                "match": is_match,
            }
        )
    return all_match, match_count, details


def compute_shishen_accuracy(
    actual_shishen: List[str], expected_shishen: List[str]
) -> Tuple[int, List[dict]]:
    """Compare ten gods per pillar. Returns (match_count, details)."""
    details = []
    match_count = 0
    for i, (actual, expected) in enumerate(zip(actual_shishen, expected_shishen)):
        is_match = actual == expected
        if is_match:
            match_count += 1
        details.append(
            {
                "pillar_index": i,
                "pillar_name": ["年柱", "月柱", "日柱", "时柱"][i],
                "expected": expected,
                "actual": actual,
                "match": is_match,
            }
        )
    return match_count, details


def compute_canggan_accuracy(
    actual_cg: List[List[str]], expected_cg: List[List[str]]
) -> Tuple[int, int, List[dict]]:
    """
    Compare 藏干 per pillar (each pillar has 1-3 hidden stems).
    Returns (match_count, total_count, details).
    The order of items within each pillar matters.
    """
    details = []
    match_count = 0
    total_count = 0
    for i, (actual_list, expected_list) in enumerate(zip(actual_cg, expected_cg)):
        pillar_matches = 0
        pillar_total = max(len(actual_list), len(expected_list))
        total_count += pillar_total

        for j in range(pillar_total):
            actual_stem = actual_list[j] if j < len(actual_list) else ""
            expected_stem = expected_list[j] if j < len(expected_list) else ""
            if actual_stem == expected_stem:
                pillar_matches += 1
                match_count += 1

        details.append(
            {
                "pillar_index": i,
                "pillar_name": ["年柱", "月柱", "日柱", "时柱"][i],
                "expected": expected_list,
                "actual": actual_list,
                "match_count": pillar_matches,
                "total_count": pillar_total,
            }
        )
    return match_count, total_count, details


def compute_nayin_accuracy(
    actual_nayin: List[str], expected_nayin: List[str]
) -> Tuple[int, List[dict]]:
    """Compare 纳音 per pillar. Returns (match_count, details)."""
    details = []
    match_count = 0
    for i, (actual, expected) in enumerate(zip(actual_nayin, expected_nayin)):
        is_match = actual == expected
        if is_match:
            match_count += 1
        details.append(
            {
                "pillar_index": i,
                "pillar_name": ["年柱", "月柱", "日柱", "时柱"][i],
                "expected": expected,
                "actual": actual,
                "match": is_match,
            }
        )
    return match_count, details


def compute_dayun_accuracy(
    actual_dayun: List[Tuple[int, str]], expected_dayun_list: List[str], n: int = 6
) -> Tuple[int, List[dict]]:
    """
    Compare first n dayun pillars.
    actual_dayun: list of (start_age, ganzhi) tuples from our engine
    expected_dayun_list: list of ganzhi strings from wenzhen
    """
    details = []
    match_count = 0
    expected_first_n = expected_dayun_list[:n]
    actual_first_n = [d[1] for d in actual_dayun[:n]]

    for i, (actual_ganzhi, expected_ganzhi) in enumerate(
        zip(actual_first_n, expected_first_n)
    ):
        is_match = actual_ganzhi == expected_ganzhi
        if is_match:
            match_count += 1
        details.append(
            {
                "dayun_index": i,
                "expected": expected_ganzhi,
                "actual": actual_ganzhi,
                "match": is_match,
            }
        )
    return match_count, details


def compute_day_master_accuracy(
    actual_day_master: str, expected_day_master_gan: str
) -> Tuple[bool, dict]:
    """
    Compare day master.
    actual_day_master: e.g. "甲木"
    expected_day_master_gan: e.g. "甲" (first char of expected day pillar)
    """
    actual_gan = actual_day_master[0] if actual_day_master else ""
    is_match = actual_gan == expected_day_master_gan
    return is_match, {
        "expected_gan": expected_day_master_gan,
        "actual_gan": actual_gan,
        "actual_full": actual_day_master,
        "match": is_match,
    }


# ============================================================
# Main Benchmark
# ============================================================


def run_benchmark(samples: List[dict]) -> dict:
    """Run the full benchmark comparing our engine vs wenzhen data."""
    engine = BaziEngine()

    # Accumulators
    total = len(samples)
    dim_totals = {
        "pillars": {"correct": 0, "total": total},
        "shishen": {"correct": 0, "total": total * 4},
        "canggan": {"correct": 0, "total_stems": 0},  # track per-stem counts
        "nayin": {"correct": 0, "total": total * 4},
        "dayun": {"correct": 0, "total": total * 6},
        "day_master": {"correct": 0, "total": total},
    }

    discrepancies = []
    sample_results = []
    canggan_total_items = 0
    canggan_correct_items = 0

    for idx, sample in enumerate(samples):
        date_str = sample["date"]
        time_str = sample["time"]
        gender = sample["gender"]

        year, month, day = parse_date(date_str)
        hour, minute = parse_time(time_str)

        # Handle 晚子时
        adj_year, adj_month, adj_day, adj_hour, adj_minute = handle_late_zi_hour(
            year, month, day, hour, minute
        )

        # Expected values from wenzhen
        expected_pillars = sample["pillars"].split()
        expected_ss = sample["ss"]
        expected_cg = sample["cg"]
        expected_ny = sample["ny"]
        expected_dayun_list = sample["dayun"]
        expected_day_master_gan = expected_pillars[2][0]

        try:
            # Run our engine
            result = engine.calculate(
                adj_year, adj_month, adj_day, adj_hour, adj_minute, "北京", gender
            )
        except Exception as e:
            error_entry = {
                "index": idx,
                "sample": f"{date_str} {time_str} {gender}",
                "error": str(e),
            }
            discrepancies.append(error_entry)
            continue

        # ----- 1. Pillars (四柱) -----
        all_match, match_count, pillar_details = compute_pillar_accuracy(
            result.bazi, expected_pillars
        )
        dim_totals["pillars"]["correct"] += 1 if all_match else 0

        # ----- 2. Shishen (十神) -----
        ss_match_count, ss_details = compute_shishen_accuracy(
            result.shishen, expected_ss
        )
        dim_totals["shishen"]["correct"] += ss_match_count

        # ----- 3. Canggan (藏干) -----
        # We use lunar-python directly to get canggan, as our engine doesn't expose it
        from lunar_python import Solar

        solar = Solar.fromYmdHms(
            adj_year, adj_month, adj_day, adj_hour, adj_minute, 0
        )
        lunar = solar.getLunar()
        ec = lunar.getEightChar()
        actual_cg = get_canggan_from_eightchar(ec)

        cg_match_count, cg_total_count, cg_details = compute_canggan_accuracy(
            actual_cg, expected_cg
        )
        canggan_correct_items += cg_match_count
        canggan_total_items += cg_total_count
        dim_totals["canggan"]["total_stems"] += cg_total_count

        # ----- 4. Nayin (纳音) -----
        ny_match_count, ny_details = compute_nayin_accuracy(
            result.nayin, expected_ny
        )
        dim_totals["nayin"]["correct"] += ny_match_count

        # ----- 5. Dayun (大运) -----
        dy_match_count, dy_details = compute_dayun_accuracy(
            result.dayun, expected_dayun_list, n=6
        )
        dim_totals["dayun"]["correct"] += dy_match_count

        # ----- 6. Day Master (日主) -----
        dm_match, dm_detail = compute_day_master_accuracy(
            result.day_master, expected_day_master_gan
        )
        if dm_match:
            dim_totals["day_master"]["correct"] += 1

        # ----- Check for discrepancies -----
        has_discrepancy = not all_match or ss_match_count < 4 or not dm_match

        if has_discrepancy:
            disc = {
                "index": idx,
                "sample": f"{date_str} {time_str} {gender}",
                "date": date_str,
                "time": time_str,
                "gender": gender,
                "expected_pillars": " ".join(expected_pillars),
                "actual_pillars": " ".join(result.bazi),
                "pillars_match": all_match,
                "shishen_match": f"{ss_match_count}/4",
                "day_master_match": dm_match,
                "details": {},
            }

            # Add pillar-level detail if mismatch
            if not all_match:
                disc["details"]["pillars"] = pillar_details
            if ss_match_count < 4:
                disc["details"]["shishen"] = ss_details
            if not dm_match:
                disc["details"]["day_master"] = dm_detail

            discrepancies.append(disc)

        # Store sample-level result
        sample_results.append(
            {
                "sample": f"{date_str} {time_str} {gender}",
                "pillars_match": all_match,
                "shishen_match": ss_match_count,
                "canggan_match": cg_match_count,
                "canggan_total": cg_total_count,
                "nayin_match": ny_match_count,
                "dayun_match": dy_match_count,
                "day_master_match": dm_match,
            }
        )

    # ---- Final statistics ----
    actual_total = len(sample_results)
    dim_totals["canggan"]["total_stems"] = canggan_total_items
    dim_totals["canggan"]["correct"] = canggan_correct_items
    dim_totals["pillars"]["total"] = actual_total
    dim_totals["day_master"]["total"] = actual_total

    # Per-dimension accuracy
    dim_accuracy = {}
    for dim in ["pillars", "shishen", "nayin", "dayun", "day_master"]:
        t = dim_totals[dim]
        acc = (t["correct"] / t["total"] * 100) if t["total"] > 0 else 0
        dim_accuracy[dim] = {
            "correct": t["correct"],
            "total": t["total"],
            "accuracy_pct": round(acc, 2),
        }

    # Canggan is special (per-stem, not per-pillar)
    cg_acc = (
        (dim_totals["canggan"]["correct"] / dim_totals["canggan"]["total_stems"] * 100)
        if dim_totals["canggan"]["total_stems"] > 0
        else 0
    )
    dim_accuracy["canggan"] = {
        "correct": dim_totals["canggan"]["correct"],
        "total": dim_totals["canggan"]["total_stems"],
        "accuracy_pct": round(cg_acc, 2),
    }

    # Weighted overall score
    overall = 0.0
    for dim in WEIGHTS:
        overall += WEIGHTS[dim] * dim_accuracy[dim]["accuracy_pct"]
    overall = round(overall, 2)

    return {
        "overall_accuracy": overall,
        "acceptance_threshold": ACCEPTANCE_THRESHOLD,
        "passed": overall >= ACCEPTANCE_THRESHOLD,
        "total_samples": actual_total,
        "total_discrepancies": len(discrepancies),
        "dimensions": dim_accuracy,
        "weights": WEIGHTS,
        "discrepancies": discrepancies[:100],  # cap at 100 for report
        "sample_results": sample_results,
        "benchmark_time": datetime.now().isoformat(),
    }


# ============================================================
# Reporting
# ============================================================


def print_console_report(report: dict):
    """Print a formatted summary to console."""
    print("=" * 60)
    print("  Bazi Accuracy Benchmark Report")
    print("  vs 问真八字 (Wenzhen)")
    print("=" * 60)
    print()
    print(f"  Tested samples:  {report['total_samples']}")
    print(f"  Discrepancies:   {report['total_discrepancies']}")
    print(f"  Overall:         {report['overall_accuracy']:.2f}%")
    print(
        f"  Status:          {'PASS ✅' if report['passed'] else 'FAIL ❌'}"
    )
    print(f"  Threshold:       {report['acceptance_threshold']}%")
    print()
    print("-" * 60)
    print("  Per-Dimension Breakdown:")
    print("-" * 60)
    for dim in ["pillars", "shishen", "canggan", "nayin", "dayun", "day_master"]:
        d = report["dimensions"][dim]
        w = report["weights"][dim]
        bar = "█" * int(d["accuracy_pct"] / 5) + "░" * (
            20 - int(d["accuracy_pct"] / 5)
        )
        print(
            f"  {dim:<12s} {d['accuracy_pct']:>7.2f}%  [{bar}]  "
            f"{d['correct']:>5d}/{d['total']:<5d}  (weight: {w*100:.0f}%)"
        )
    print()
    if report["discrepancies"]:
        print("-" * 60)
        print(f"  Sample Discrepancies (first {min(10, len(report['discrepancies']))}):")
        print("-" * 60)
        for d in report["discrepancies"][:10]:
            print(f"  #{d['index']}: {d['sample']}")
            print(f"    Expected: {d['expected_pillars']}")
            print(f"    Actual:   {d['actual_pillars']}")
            if not d.get("pillars_match"):
                for pd in d.get("details", {}).get("pillars", []):
                    if not pd["match"]:
                        print(
                            f"      {pd['pillar_name']}: expected={pd['expected']}, actual={pd['actual']}"
                        )
            print()
    print("=" * 60)
    print()


def generate_json_report(report: dict, output_path: str):
    """Save detailed JSON report."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"JSON report saved to: {output_path}")


def generate_markdown_report(report: dict, output_path: str):
    """Generate a human-readable Markdown report."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    status_emoji = "✅ PASS" if report["passed"] else "❌ FAIL"
    lines = [
        "# Bazi Accuracy Benchmark Report",
        "",
        f"**Date:** {report['benchmark_time']}",
        f"**Engine:** BaziEngine (lunar-python)",
        f"**Reference:** 问真八字 (Wenzhen)",
        f"**Samples tested:** {report['total_samples']}",
        "",
        "## Overall Result",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Overall Accuracy | **{report['overall_accuracy']:.2f}%** |",
        f"| Acceptance Threshold | {report['acceptance_threshold']}% |",
        f"| Status | **{status_emoji}** |",
        f"| Discrepancies | {report['total_discrepancies']} |",
        "",
        "## Per-Dimension Accuracy",
        "",
        "| Dimension | Weight | Accuracy | Correct / Total |",
        "|-----------|--------|----------|-----------------|",
    ]

    for dim in ["pillars", "shishen", "canggan", "nayin", "dayun", "day_master"]:
        d = report["dimensions"][dim]
        w = report["weights"][dim]
        lines.append(
            f"| {dim} | {w*100:.0f}% | {d['accuracy_pct']:.2f}% | "
            f"{d['correct']} / {d['total']} |"
        )

    # Weighted scoring explanation
    lines.extend(
        [
            "",
            "## Scoring Method",
            "",
            f"Overall = Σ(dimension_accuracy% × weight)",
        ]
    )
    terms = []
    for dim in ["pillars", "shishen", "canggan", "nayin", "dayun", "day_master"]:
        d = report["dimensions"][dim]
        w = report["weights"][dim]
        terms.append(f"{d['accuracy_pct']:.2f}% × {w*100:.0f}%")
    lines.append("")
    lines.append("= " + " + ".join(terms))
    lines.append(f"= **{report['overall_accuracy']:.2f}%**")

    # Discrepancies
    lines.extend(
        [
            "",
            "## Discrepancies Found",
            "",
            f"**Total: {report['total_discrepancies']}**",
            "",
        ]
    )

    if report["discrepancies"]:
        lines.append("| # | Sample | Expected Pillars | Actual Pillars | Issues |")
        lines.append("|---|--------|-----------------|----------------|--------|")
        for d in report["discrepancies"][:50]:  # show up to 50 in markdown
            issues = []
            if not d.get("pillars_match"):
                issues.append("pillars")
            if d.get("shishen_match") != "4/4":
                issues.append("shishen")
            if not d.get("day_master_match"):
                issues.append("day_master")
            lines.append(
                f"| {d['index']} | {d['sample']} | {d['expected_pillars']} | "
                f"{d['actual_pillars']} | {', '.join(issues)} |"
            )

        # Detailed discrepancies for worst cases
        lines.extend(
            [
                "",
                "### Detailed Discrepancy Examples",
                "",
            ]
        )
        for d in report["discrepancies"][:10]:
            details_text = ""
            if "details" in d:
                for dim_name, dim_details in d["details"].items():
                    if isinstance(dim_details, list):
                        for item in dim_details[:4]:
                            if not item.get("match", True):
                                details_text += (
                                    f"- {item.get('pillar_name', '')}: "
                                    f"expected `{item.get('expected', '')}`, "
                                    f"actual `{item.get('actual', '')}`\n"
                                )
                    elif isinstance(dim_details, dict):
                        if not dim_details.get("match", True):
                            details_text += (
                                f"- expected `{dim_details.get('expected_gan', '')}`, "
                                f"actual `{dim_details.get('actual_gan', '')}`\n"
                            )

            lines.extend(
                [
                    f"#### #{d['index']}: {d['sample']}",
                    "",
                    f"- Expected: `{d['expected_pillars']}`",
                    f"- Actual:   `{d['actual_pillars']}`",
                    "",
                    "**Details:**",
                    details_text,
                ]
            )

    # Edge cases and notes
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- **晚子时 handling:** Records with `time=23:00` (晚子时) have been adjusted by "
            "advancing the date by 1 day for pillar calculation, as 23:00 is considered the "
            "start of the next day in traditional bazi theory.",
            "- **藏干 (canggan):** Computed using lunar-python's `getXxxHideGan()` methods.",
            "- **大运 (dayun):** Only the first 6 dayun pillars are compared.",
            "- **日主 (day_master):** Only the heavenly stem (天干) of the day pillar is compared.",
            "",
            f"*Report generated at {report['benchmark_time']}*",
        ]
    )

    content = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Markdown report saved to: {output_path}")


# ============================================================
# Main
# ============================================================


def main():
    print()
    print("=" * 60)
    print("  Bazi Accuracy Benchmark against 问真八字")
    print("=" * 60)
    print()

    # Load samples
    samples = load_samples(WENZHEN_PATH, num_samples=500, seed=42)

    # Run benchmark
    print(f"Running benchmark on {len(samples)} samples...")
    report = run_benchmark(samples)

    # Console report
    print_console_report(report)

    # Save JSON report
    json_path = os.path.join(RESULTS_DIR, "bazi_accuracy_benchmark.json")
    generate_json_report(report, json_path)

    # Save Markdown report
    md_path = os.path.join(RESULTS_DIR, "bazi_accuracy_benchmark.md")
    generate_markdown_report(report, md_path)

    # Summary for caller
    print(f"Summary: {'PASS' if report['passed'] else 'FAIL'}")
    print(f"  Overall accuracy: {report['overall_accuracy']:.2f}%")
    print(f"  Target: {report['acceptance_threshold']}%")
    print(
        f"  Result: {'Above threshold ✅' if report['passed'] else 'Below threshold ❌'}"
    )

    # Return exit code
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
