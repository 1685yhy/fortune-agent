#!/usr/bin/env python3
"""
易理明灯 — 自动评分管线 (Accuracy Scoring Pipeline)

Post-processing for benchmark results. Loads collected JSONL, scores each
response via DeepSeek across 5 dimensions, and produces accuracy reports.

Usage:
    python scripts/score_benchmark.py --input results/bench_2250_local.jsonl --sample 50
    python scripts/score_benchmark.py --input results/sample_2250.jsonl --full
    python scripts/score_benchmark.py --input old.jsonl --compare-with new.jsonl --full
    python scripts/score_benchmark.py --input results/collect_parallel.jsonl --resume
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.eval.scorer import LLMScorer, DIMENSION_WEIGHTS, ScoredResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

RESULTS_DIR = PROJECT_DIR / "data" / "eval" / "results"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIMENSION_ORDER = list(DIMENSION_WEIGHTS.keys())
DIMENSION_LABELS_CN = {
    "accuracy": "准确性",
    "completeness": "完整性",
    "personalization": "个性化",
    "actionability": "可操作性",
    "citation_quality": "引用质量",
}


PLACEHOLDER_PATTERNS = [
    "请提供出生信息",
    "请告诉我您想查询",
    "请提供您的出生",
    "请输入出生",
    "请提供相关信息",
    "请提供详细信息",
]


def is_skip_response(response: Optional[str]) -> tuple[bool, str]:
    """Check if a response should be skipped (no real analysis content).

    Returns (skip, reason).
    """
    if not response:
        return True, "empty_response"
    text = response.strip()
    if not text:
        return True, "blank_response"
    if text.startswith("ERROR"):
        return True, "error_response"
    if text.startswith("error"):
        return True, "error_response"
    if len(text) < 30:
        return True, f"too_short({len(text)}chars)"
    for pat in PLACEHOLDER_PATTERNS:
        if pat in text[:150]:
            return True, f"placeholder_pattern({pat})"
    return False, ""


def load_benchmark_results(filepath: str | Path) -> list[dict]:
    """Load benchmark results from a JSONL or JSON file."""
    filepath = Path(filepath)
    if not filepath.exists():
        logger.error(f"File not found: {filepath}")
        return []

    content = filepath.read_text(encoding="utf-8").strip()

    # Try JSON array format: [ {...}, {...} ]
    if content.startswith("["):
        try:
            records = json.loads(content)
            logger.info(f"Loaded {len(records)} records (JSON array) from {filepath}")
            return records
        except json.JSONDecodeError:
            pass

    # Fallback: JSONL format, one JSON object per line
    records = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning(f"Skipping unparseable line in {filepath}: {line[:100]}")

    logger.info(f"Loaded {len(records)} records (JSONL) from {filepath}")
    return records


def sample_records(records: list[dict], n: int) -> list[dict]:
    """Sample n records, stratified by domain."""
    if n >= len(records):
        return records
    groups = defaultdict(list)
    for r in records:
        groups[r.get("domain", "unknown")].append(r)
    sampled = []
    per_group = max(1, n // len(groups))
    for key, group in sorted(groups.items()):
        take = min(per_group, len(group), n - len(sampled))
        sampled.extend(random.sample(group, take))
    remaining = [r for r in records if r not in sampled]
    random.shuffle(remaining)
    sampled.extend(remaining[: n - len(sampled)])
    random.shuffle(sampled)
    return sampled


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

DEEPSEEK_API_KEY = "sk-REPLACED-REMOVED-KEY"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"


def score_record(record: dict, scorer: LLMScorer) -> Optional[ScoredResponse]:
    """Score a single record using the LLM scorer.

    Returns None if the response should be skipped.
    """
    response = record.get("our_response", "") or ""
    skip, reason = is_skip_response(response)
    if skip:
        logger.info(f"  SKIP [{record.get('id','?')}]: {reason}")
        return None

    query = record.get("query", "")
    domain = record.get("domain", "general")

    try:
        scored = scorer.score_response(
            query=query,
            response=response,
            domain=domain,
            user_info=None,
        )
        return scored
    except Exception as e:
        logger.warning(f"  SCORE ERROR [{record.get('id','?')}]: {e}")
        return None


def format_skipped_response(record: dict) -> dict:
    """Return a zero-score result dict for skipped records."""
    return {
        "id": record.get("id", "?"),
        "domain": record.get("domain", "?"),
        "difficulty": record.get("difficulty", "?"),
        "query": record.get("query", ""),
        "status": "skipped",
        "skip_reason": "empty_or_error",
        "scores": {
            dim: {"score": 0.0, "justification": "响应为空/错误"}
            for dim in DIMENSION_ORDER
        },
        "overall": 0.0,
    }


def format_scored_response(record: dict, scored: ScoredResponse) -> dict:
    """Format a ScoredResponse into a serializable dict together with record metadata."""
    return {
        "id": record.get("id", "?"),
        "domain": record.get("domain", "?"),
        "difficulty": record.get("difficulty", "?"),
        "query": record.get("query", ""),
        "response_preview": (record.get("our_response", "") or "")[:200],
        "status": "scored",
        "scores": {
            dim: {
                "score": getattr(scored, dim).score,
                "justification": getattr(scored, dim).justification,
            }
            for dim in DIMENSION_ORDER
        },
        "overall": scored.overall,
    }


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------


def build_aggregate_stats(scored_results: list[dict]) -> dict:
    """Compute per-domain and per-difficulty aggregates from scored results."""
    scored = [r for r in scored_results if r.get("status") == "scored"]
    if not scored:
        return {}

    totals = {dim: [] for dim in DIMENSION_ORDER}
    overalls = []
    by_domain = defaultdict(lambda: {dim: [] for dim in DIMENSION_ORDER})
    by_domain_overalls = defaultdict(list)
    by_diff = defaultdict(lambda: {dim: [] for dim in DIMENSION_ORDER})
    by_diff_overalls = defaultdict(list)

    for r in scored:
        ov = r.get("overall", 0)
        overalls.append(ov)
        dom = r.get("domain", "unknown")
        diff = r.get("difficulty", "unknown")
        by_domain_overalls[dom].append(ov)
        by_diff_overalls[diff].append(ov)
        for dim in DIMENSION_ORDER:
            s = r.get("scores", {}).get(dim, {}).get("score", 0)
            totals[dim].append(s)
            by_domain[dom][dim].append(s)
            by_diff[diff][dim].append(s)

    def avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else 0.0

    overall_avg = avg(overalls)

    # Per domain
    domain_stats = {}
    for dom in sorted(by_domain.keys()):
        domain_stats[dom] = {
            "count": len(by_domain_overalls[dom]),
            "avg_overall": avg(by_domain_overalls[dom]),
            "dimensions": {dim: avg(by_domain[dom][dim]) for dim in DIMENSION_ORDER},
            "weakest_dimension": min(DIMENSION_ORDER, key=lambda d: avg(by_domain[dom][d])),
        }

    # Per difficulty
    diff_stats = {}
    for d in sorted(by_diff.keys()):
        diff_stats[d] = {
            "count": len(by_diff_overalls[d]),
            "avg_overall": avg(by_diff_overalls[d]),
            "dimensions": {dim: avg(by_diff[d][dim]) for dim in DIMENSION_ORDER},
        }

    # Overall dimension averages
    overall_dims = {dim: avg(totals[dim]) for dim in DIMENSION_ORDER}
    weakest_overall = min(DIMENSION_ORDER, key=lambda d: overall_dims[d])

    return {
        "total_scored": len(scored),
        "total_skipped": len(scored_results) - len(scored),
        "avg_overall": overall_avg,
        "max_overall": round(max(overalls), 2),
        "min_overall": round(min(overalls), 2),
        "median_overall": round(sorted(overalls)[len(overalls)//2], 2),
        "dimension_averages": overall_dims,
        "weakest_dimension": weakest_overall,
        "by_domain": domain_stats,
        "by_difficulty": diff_stats,
    }


def identify_weaknesses(stats: dict, scored_results: list[dict]) -> list[dict]:
    """Identify weakest domains sorted by average overall score."""
    by_domain = stats.get("by_domain", {})
    sorted_domains = sorted(
        by_domain.items(), key=lambda x: x[1]["avg_overall"]
    )
    return [
        {
            "domain": dom,
            "avg_overall": info["avg_overall"],
            "weakest_dimension": info.get("weakest_dimension", ""),
            "count": info["count"],
        }
        for dom, info in sorted_domains
    ]


def find_top_bottom(scored_results: list[dict], top_n: int = 5) -> dict:
    """Find top-N and bottom-N scored responses by overall score."""
    scored = [r for r in scored_results if r.get("status") == "scored"]
    sorted_by_score = sorted(scored, key=lambda r: r.get("overall", 0), reverse=True)
    return {
        "top": [
            {
                "id": r["id"],
                "domain": r["domain"],
                "difficulty": r["difficulty"],
                "overall": r["overall"],
                "query_preview": r.get("query", "")[:100],
            }
            for r in sorted_by_score[:top_n]
        ],
        "bottom": [
            {
                "id": r["id"],
                "domain": r["domain"],
                "difficulty": r["difficulty"],
                "overall": r["overall"],
                "query_preview": r.get("query", "")[:100],
            }
            for r in sorted_by_score[-top_n:]
        ],
    }


def generate_json_report(
    stats: dict,
    weaknesses: list[dict],
    top_bottom: dict,
    scored_results: list[dict],
    args: argparse.Namespace,
    timestamp: str,
) -> dict:
    """Build the complete JSON report dict."""
    return {
        "report_type": "accuracy_scoring",
        "timestamp": timestamp,
        "args": {
            "input_file": args.input,
            "sample": args.sample,
            "full": args.full,
            "compare_with": args.compare_with,
        },
        "summary": {
            "total_records_loaded": stats.get("total_scored", 0) + stats.get("total_skipped", 0),
            "total_scored": stats.get("total_scored", 0),
            "total_skipped": stats.get("total_skipped", 0),
            "avg_overall": stats.get("avg_overall", 0),
            "max_overall": stats.get("max_overall", 0),
            "min_overall": stats.get("min_overall", 0),
            "median_overall": stats.get("median_overall", 0),
            "dimension_averages": stats.get("dimension_averages", {}),
            "weakest_dimension": stats.get("weakest_dimension", ""),
        },
        "by_domain": stats.get("by_domain", {}),
        "by_difficulty": stats.get("by_difficulty", {}),
        "weakness_ranking": weaknesses,
        "top_bottom": top_bottom,
        "individual_results": scored_results,
    }


def generate_markdown_report(
    stats: dict,
    weaknesses: list[dict],
    top_bottom: dict,
    args: argparse.Namespace,
    timestamp: str,
    compare_mode: bool = False,
    compare_stats: Optional[dict] = None,
) -> str:
    """Generate a human-readable markdown report."""
    lines = []
    lines.append("# 易理明灯 — 准确度评分报告")
    lines.append("")
    lines.append(f"- **生成时间**: {timestamp}")
    lines.append(f"- **输入文件**: `{args.input}`")
    lines.append(f"- **采样模式**: {'是' if args.sample else '全部'}")
    lines.append(f"- **采样数量**: {args.sample if args.sample else '全部'}")
    if compare_mode and compare_stats:
        lines.append(f"- **对比模式**: `{args.compare_with}`")
    lines.append("")

    scored_count = stats.get("total_scored", 0)
    skipped_count = stats.get("total_skipped", 0)

    lines.append("## 总体统计")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 评分总数 | {scored_count} |")
    lines.append(f"| 跳过数（空/错误响应） | {skipped_count} |")
    lines.append(f"| 平均分（加权） | {stats.get('avg_overall', 0):.2f}/10 |")
    lines.append(f"| 最高分 | {stats.get('max_overall', 0):.2f} |")
    lines.append(f"| 最低分 | {stats.get('min_overall', 0):.2f} |")
    lines.append(f"| 中位数 | {stats.get('median_overall', 0):.2f} |")
    lines.append("")

    if compare_mode and compare_stats:
        delta_avg = stats.get("avg_overall", 0) - compare_stats.get("avg_overall", 0)
        delta_str = f"{'+' if delta_avg > 0 else ''}{delta_avg:.2f}"
        lines.append(f"> **对比**: 当前平均分 {stats.get('avg_overall', 0):.2f} vs "
                      f"对比版本 {compare_stats.get('avg_overall', 0):.2f} "
                      f"({delta_str} delta)")
        lines.append("")

    # Dimension averages
    lines.append("## 维度得分")
    lines.append("")
    lines.append("| 维度 | 权重 | 平均分 | 柱状图 |")
    lines.append("|------|------|--------|--------|")
    dim_avgs = stats.get("dimension_averages", {})
    for dim in DIMENSION_ORDER:
        label = DIMENSION_LABELS_CN.get(dim, dim)
        weight = DIMENSION_WEIGHTS.get(dim, 0)
        avg_score = dim_avgs.get(dim, 0)
        bar_len = max(1, int(avg_score * 2))
        bar = "█" * bar_len
        lines.append(f"| {label} | {weight*100:.0f}% | {avg_score:.2f} | {bar} |")
    lines.append("")

    # Per-domain breakdown
    lines.append("## 按领域统计")
    lines.append("")
    lines.append("| 领域 | 数量 | 平均分 | 最弱维度 |")
    lines.append("|------|------|--------|----------|")
    by_domain = stats.get("by_domain", {})
    for dom in sorted(by_domain.keys()):
        info = by_domain[dom]
        wd = info.get("weakest_dimension", "")
        wd_label = DIMENSION_LABELS_CN.get(wd, wd)
        lines.append(f"| {dom} | {info['count']} | {info['avg_overall']:.2f} | {wd_label} |")
    lines.append("")

    # Per-difficulty breakdown
    lines.append("## 按难度统计")
    lines.append("")
    lines.append("| 难度 | 数量 | 平均分 |")
    lines.append("|------|------|--------|")
    by_diff = stats.get("by_difficulty", {})
    for diff in sorted(by_diff.keys()):
        info = by_diff[diff]
        lines.append(f"| {diff} | {info['count']} | {info['avg_overall']:.2f} |")
    lines.append("")

    # Weakness identification
    lines.append("## 弱项识别（按得分排序）")
    lines.append("")
    if weaknesses:
        lines.append("| 排名 | 领域 | 平均分 | 最弱维度 | 数量 |")
        lines.append("|------|------|--------|----------|------|")
        for i, w in enumerate(weaknesses, 1):
            wd_label = DIMENSION_LABELS_CN.get(w["weakest_dimension"], w["weakest_dimension"])
            lines.append(f"| {i} | {w['domain']} | {w['avg_overall']:.2f} | {wd_label} | {w['count']} |")
    lines.append("")

    # Top & Bottom
    top_bottom_data = top_bottom
    lines.append("## 最佳回答 Top 5")
    lines.append("")
    for i, item in enumerate(top_bottom_data.get("top", []), 1):
        lines.append(f"{i}. **[{item['domain']}/{item['difficulty']}] {item['id']}** — "
                      f"总分: {item['overall']:.2f}")
        lines.append(f"   - {item['query_preview']}")
    lines.append("")

    lines.append("## 最差回答 Bottom 5")
    lines.append("")
    for i, item in enumerate(top_bottom_data.get("bottom", []), 1):
        lines.append(f"{i}. **[{item['domain']}/{item['difficulty']}] {item['id']}** — "
                      f"总分: {item['overall']:.2f}")
        lines.append(f"   - {item['query_preview']}")
    lines.append("")

    if compare_mode and compare_stats:
        lines.append("## 版本对比")
        lines.append("")
        lines.append("| 指标 | 当前版本 | 对比版本 | 变化 |")
        lines.append("|------|----------|----------|------|")
        cur_avg = stats.get("avg_overall", 0)
        comp_avg = compare_stats.get("avg_overall", 0)
        delta_avg = cur_avg - comp_avg
        delta_str = f"{'+' if delta_avg > 0 else ''}{delta_avg:.2f}"
        lines.append(f"| 平均分 | {cur_avg:.2f} | {comp_avg:.2f} | {delta_str} |")

        cur_dims = stats.get("dimension_averages", {})
        comp_dims = compare_stats.get("dimension_averages", {})
        for dim in DIMENSION_ORDER:
            label = DIMENSION_LABELS_CN.get(dim, dim)
            cv = cur_dims.get(dim, 0)
            co = comp_dims.get(dim, 0)
            delta = cv - co
            delta_s = f"{'+' if delta > 0 else ''}{delta:.2f}"
            lines.append(f"| {label} | {cv:.2f} | {co:.2f} | {delta_s} |")
        lines.append("")

    lines.append("---")
    lines.append("*报告自动生成 by 易理明灯 Scoring Pipeline*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def compare_reports(current: dict, other: dict) -> dict:
    """Compare two report dicts and return deltas."""
    cs = current.get("summary", {})
    os = other.get("summary", {})
    return {
        "avg_overall_delta": round(cs.get("avg_overall", 0) - os.get("avg_overall", 0), 2),
        "total_scored": cs.get("total_scored", 0),
        "other_total_scored": os.get("total_scored", 0),
        "dimension_deltas": {
            dim: round(
                cs.get("dimension_averages", {}).get(dim, 0)
                - os.get("dimension_averages", {}).get(dim, 0),
                2,
            )
            for dim in DIMENSION_ORDER
        },
        "by_domain_deltas": {
            dom: round(
                cs.get("by_domain", {}).get(dom, {}).get("avg_overall", 0)
                - os.get("by_domain", {}).get(dom, {}).get("avg_overall", 0),
                2,
            )
            for dom in set(list(cs.get("by_domain", {}).keys()) + list(os.get("by_domain", {}).keys()))
        },
    }


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------


def run_pipeline(args: argparse.Namespace) -> dict:
    """Run the full scoring pipeline."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    display_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Load results
    records = load_benchmark_results(args.input)
    if not records:
        logger.error("No records loaded. Exiting.")
        return {}

    # Filter to only scored responses (skip if --skip-scored)
    if args.skip_scored:
        # Remove records that already have scores in their output file
        logger.info("--skip-scored mode: checking for scored records...")
        # We handle this by only processing unscored records later

    # Sample if requested
    if args.sample and args.sample < len(records) and not args.full:
        records = sample_records(records, args.sample)
        logger.info(f"Sampled {len(records)} records (requested {args.sample})")
    elif args.full:
        logger.info(f"Using all {len(records)} records")
    else:
        # Default: sample 50 if --full not set and --sample not given
        default_sample = 50
        if len(records) > default_sample:
            records = sample_records(records, default_sample)
            logger.info(f"Default sampling: {len(records)} records")

    # Prepare output directory
    output_dir = RESULTS_DIR / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resume: load existing partial scores if --resume
    existing_scores = {}
    incr_path = output_dir / "incremental_scores.jsonl"
    if args.resume and incr_path.exists():
        with open(incr_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    existing_scores[r.get("id", "")] = r
        logger.info(f"Resumed with {len(existing_scores)} existing scores from {incr_path}")

    # Create scorer
    scorer = LLMScorer(
        api_key=DEEPSEEK_API_KEY,
        model=DEEPSEEK_MODEL,
        api_base=DEEPSEEK_API_BASE,
        timeout=60.0,
    )

    # Score each record
    scored_results = list(existing_scores.values())
    skipped_count = 0
    newly_scored = 0
    total = len(records)

    try:
        for i, record in enumerate(records):
            rid = record.get("id", f"R{i:04d}")

            # Skip already-scored in resume mode
            if args.resume and rid in existing_scores:
                logger.info(f"[{i+1}/{total}] {rid} — already scored, skipping")
                continue

            response = record.get("our_response", "") or ""
            skip, reason = is_skip_response(response)
            if skip:
                logger.info(f"[{i+1}/{total}] {rid} — SKIP ({reason})")
                skipped_count += 1
                scored_results.append(format_skipped_response(record))
                continue

            logger.info(f"[{i+1}/{total}] {rid} [{record.get('domain','?')}/{record.get('difficulty','?')}] "
                        f"scoring...")

            scored = score_record(record, scorer)
            if scored is not None:
                entry = format_scored_response(record, scored)
                scored_results.append(entry)
                newly_scored += 1
                logger.info(f"  -> Score: {scored.overall:.2f}")
            else:
                logger.warning(f"  -> Score failed, marking as skipped")
                skipped_count += 1
                scored_results.append(format_skipped_response(record))

            # Incremental save after each score
            with open(incr_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(scored_results[-1], ensure_ascii=False) + "\n")

            # Rate limit: 1 request/second
            time.sleep(1.0)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Saving partial results...")
    finally:
        scorer.close()

    logger.info(f"\nScoring complete: {newly_scored} scored, {skipped_count} skipped")

    # Compute statistics
    stats = build_aggregate_stats(scored_results)
    weaknesses = identify_weaknesses(stats, scored_results)
    top_bottom_data = find_top_bottom(scored_results, top_n=5)

    # Generate reports
    json_report = generate_json_report(
        stats, weaknesses, top_bottom_data, scored_results, args, timestamp
    )
    md_report = generate_markdown_report(
        stats, weaknesses, top_bottom_data, args, display_ts
    )

    # Version comparison
    compare_stats = None
    compare_deltas = None
    if args.compare_with:
        other_path = Path(args.compare_with)
        if not other_path.is_absolute():
            other_path = PROJECT_DIR / "data" / "eval" / "results" / args.compare_with
        if other_path.exists():
            other_records = load_benchmark_results(str(other_path))
            # Use records that have scores (from a prior scoring run) or all records
            other_filtered = [r for r in other_records
                              if r.get("status") == "scored" and isinstance(r.get("overall"), (int, float))]
            if not other_filtered:
                logger.warning("Comparison file has no scored records; using all records as best-effort")
                other_filtered = other_records
            if other_filtered:
                compare_stats = {
                    "avg_overall": round(sum(r.get("overall", 0) for r in other_filtered) / len(other_filtered), 2),
                    "total_scored": len(other_filtered),
                    "dimension_averages": {
                        dim: round(sum(
                            r.get("scores", {}).get(dim, {}).get("score", 0)
                            for r in other_filtered
                        ) / len(other_filtered), 2)
                        for dim in DIMENSION_ORDER
                    },
                }
                md_report = generate_markdown_report(
                    stats, weaknesses, top_bottom_data, args, display_ts,
                    compare_mode=True, compare_stats=compare_stats,
                )
                json_report["comparison"] = {
                    "other_file": args.compare_with,
                    "other_stats": compare_stats,
                }
        else:
            logger.warning(f"Comparison file not found: {other_path}")

    # Write outputs
    # JSON report
    json_path = output_dir / "accuracy_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_report, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON report -> {json_path}")

    # Markdown report
    md_path = output_dir / "accuracy_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)
    logger.info(f"Markdown report -> {md_path}")

    # Save the complete results as JSON for downstream use
    results_path = output_dir / "scored_results.json"
    clean_out = []
    for r in scored_results:
        clean_out.append({
            "id": r.get("id"),
            "domain": r.get("domain"),
            "difficulty": r.get("difficulty"),
            "query": (r.get("query") or "")[:500],
            "status": r.get("status"),
            "scores": r.get("scores"),
            "overall": r.get("overall", 0),
        })
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(clean_out, f, ensure_ascii=False, indent=2)
    logger.info(f"All results -> {results_path}")

    # Console summary
    print()
    print(f"{'=' * 60}")
    print(f"  准确度评分完成")
    print(f"{'=' * 60}")
    print(f"  总记录数:     {len(records)}")
    print(f"  已评分:       {stats.get('total_scored', 0)}")
    print(f"  已跳过:       {stats.get('total_skipped', 0)}")
    print(f"  平均分:       {stats.get('avg_overall', 0):.2f}/10")
    print(f"  最高分:       {stats.get('max_overall', 0):.2f}")
    print(f"  最低分:       {stats.get('min_overall', 0):.2f}")
    print(f"  最弱维度:     {DIMENSION_LABELS_CN.get(stats.get('weakest_dimension', ''), stats.get('weakest_dimension', ''))}")
    print()
    print(f"  按领域:")
    for dom, info in sorted(stats.get("by_domain", {}).items()):
        bar = "█" * max(1, int(info["avg_overall"] * 2))
        print(f"    {dom:12s}  {info['count']:4d}条  {info['avg_overall']:.2f}  {bar}")
    print()
    print(f"  报告文件:")
    print(f"    JSON:     {json_path}")
    print(f"    Markdown: {md_path}")
    print(f"    Results:  {results_path}")
    print(f"{'=' * 60}")

    # Return for programmatic use
    return {
        "timestamp": timestamp,
        "output_dir": str(output_dir),
        "json_report": str(json_path),
        "md_report": str(md_path),
        "results_file": str(results_path),
        "stats": stats,
        "json_report": json_report,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        description="易理明灯 — 自动评分管线 (Accuracy Scoring Pipeline)"
    )
    parser.add_argument(
        "--input", type=str, required=True,
        help="Path to benchmark results JSONL file (relative to data/eval/results/ or absolute)"
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Number of random records to score (default: 50)"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Score all records (overrides --sample)"
    )
    parser.add_argument(
        "--compare-with", type=str, default=None,
        help="Path to another results file for version comparison"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from previous partial run (looks for incremental scores in output dir)"
    )
    parser.add_argument(
        "--skip-scored", action="store_true",
        help="Skip records that already have scores in their JSONL entry"
    )
    return parser.parse_args()


def resolve_input_path(path_str: str) -> Path:
    """Resolve input file path — try absolute, then relative to PROJECT_DIR, then RESULTS_DIR."""
    p = Path(path_str)
    if p.is_absolute():
        return p
    # Try relative to PROJECT_DIR (most common: data/eval/results/foo.jsonl)
    candidate = PROJECT_DIR / path_str
    if candidate.exists():
        return candidate
    # Try relative to RESULTS_DIR (just filename: bench_2250_local.jsonl)
    candidate = RESULTS_DIR / path_str
    if candidate.exists():
        return candidate
    # Try path as bare string
    if p.exists():
        return p
    # Default fallback
    return PROJECT_DIR / path_str


def main():
    args = parse_args()

    # Resolve input path
    args.input = str(resolve_input_path(args.input))

    print(f"{'=' * 60}")
    print(f"  易理明灯 — 准确度评分管线")
    print(f"{'=' * 60}")
    print(f"  Input:        {args.input}")
    print(f"  Full:         {'YES' if args.full else 'NO'}")
    print(f"  Sample:       {args.sample if args.sample else 'default (50)'}")
    print(f"  Compare:      {args.compare_with if args.compare_with else 'N/A'}")
    print(f"  Resume:       {'YES' if args.resume else 'NO'}")
    print()

    run_pipeline(args)


if __name__ == "__main__":
    main()
