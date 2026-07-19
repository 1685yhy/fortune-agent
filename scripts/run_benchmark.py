#!/usr/bin/env python3
"""
易理明灯 — Benchmark Evaluation Runner

Ties together the full evaluation pipeline:
1. Load benchmark queries from data/eval/benchmark_queries.jsonl
2. For each query: send to our API, collect response
3. If competitor data exists, include it for comparison
4. Run LLM scoring
5. Generate report
6. Save versioned results

Usage:
    python scripts/run_benchmark.py --sample 50              # 50 random queries
    python scripts/run_benchmark.py --domain bazi --sample 100  # Bazi only
    python scripts/run_benchmark.py --domain ziwei --full     # All ziwei queries
    python scripts/run_benchmark.py --full                    # Full 1200
    python scripts/run_benchmark.py --dry-run                 # Report only, no API calls
"""

import argparse
import asyncio
import glob
import json
import logging
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
EVAL_DIR = DATA_DIR / "eval"
BENCHMARK_FILE = EVAL_DIR / "benchmark_queries.jsonl"
RESULTS_DIR = EVAL_DIR / "results"
COMPETITOR_DIR = DATA_DIR / "competitor_data"

# Our API
OUR_API_URL = "http://124.221.233.214:8765/api/chat"
OUR_API_TIMEOUT = 120.0

# DeepSeek for scoring
DEEPSEEK_API_KEY = "sk-REPLACED-REMOVED-KEY"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_TIMEOUT = 60.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_benchmark_queries(domain: Optional[str] = None) -> list[dict]:
    """Load benchmark queries from JSONL file."""
    if not BENCHMARK_FILE.exists():
        logger.error(f"Benchmark file not found: {BENCHMARK_FILE}")
        logger.error("Run `python data/eval/generate_benchmark.py` first")
        return []

    queries = []
    with open(BENCHMARK_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            if domain and q.get("domain") != domain:
                continue
            queries.append(q)

    logger.info(f"Loaded {len(queries)} benchmark queries" +
                (f" (domain={domain})" if domain else ""))
    return queries


def sample_queries(queries: list[dict], n: int) -> list[dict]:
    """Randomly sample n queries, stratified if possible."""
    if n >= len(queries):
        return queries

    # Try stratified sampling by domain and difficulty
    groups = defaultdict(list)
    for q in queries:
        key = (q.get("domain", "unknown"), q.get("difficulty", "L1"))
        groups[key].append(q)

    sampled = []
    per_group = max(1, n // len(groups))

    for key, group in sorted(groups.items()):
        take = min(per_group, len(group), n - len(sampled))
        sampled.extend(random.sample(group, take))

    # Fill remaining slots randomly
    remaining = [q for q in queries if q not in sampled]
    random.shuffle(remaining)
    sampled.extend(remaining[: n - len(sampled)])

    random.shuffle(sampled)
    return sampled


def load_competitor_data(domain_hint: Optional[str] = None) -> list[dict]:
    """Load competitor data from JSONL files."""
    if not COMPETITOR_DIR.exists():
        return []

    all_records = []
    for fpath in sorted(COMPETITOR_DIR.glob("*.jsonl")):
        try:
            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        all_records.append(json.loads(line))
        except Exception as e:
            logger.warning(f"Error reading {fpath}: {e}")

    logger.info(f"Loaded {len(all_records)} competitor records")
    return all_records


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


async def query_our_api(client: httpx.AsyncClient, query: str) -> Optional[str]:
    """Send a query to our fortune agent API and get the response."""
    payload = {
        "messages": [
            {"role": "user", "content": query}
        ],
        "stream": False,
    }
    try:
        resp = await client.post(
            OUR_API_URL,
            json=payload,
            timeout=OUR_API_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Try common response formats
            if "response" in data:
                return data["response"]
            if "content" in data:
                return data["content"]
            if "message" in data:
                if isinstance(data["message"], dict):
                    return data["message"].get("content", "")
                return str(data["message"])
            if "choices" in data and data["choices"]:
                return data["choices"][0].get("message", {}).get("content", "")
            # Fallback: return the whole response
            return json.dumps(data, ensure_ascii=False)
        else:
            logger.warning(f"  API returned {resp.status_code}: {resp.text[:200]}")
            return None
    except httpx.TimeoutException:
        logger.warning(f"  API timeout for: {query[:50]}...")
        return None
    except Exception as e:
        logger.warning(f"  API error: {e}")
        return None


async def score_with_llm(
    client: httpx.AsyncClient,
    query: str,
    our_response: str,
    expected_dimensions: list[str],
    reference_hints: list[str],
    competitor_context: Optional[str] = None,
) -> dict:
    """Use an LLM judge to score our response quality."""
    dims_str = "、".join(expected_dimensions) if expected_dimensions else "无特定要求"
    hints_str = "\n".join(f"  - {h}" for h in reference_hints) if reference_hints else "无"

    competitor_section = ""
    if competitor_context:
        competitor_section = f"""
## 竞品参考答案（供参考）
以下是从其他算命平台获取的参考答案，可供评分参考：
{competitor_context[:1500]}
"""

    system_prompt = """你是一个专业的算命回答评测专家。你需要对AI算命助手的回答进行打分。

评分维度（每项1-10分）：
1. 准确性：回答在命理学上是否正确、专业
2. 完整性：是否覆盖了问题涉及的各个分析维度
3. 实用性：回答是否对用户有实际指导意义
4. 深度：分析是否有深入、有见地，而非泛泛而谈
5. 结构化：回答是否有清晰的结构、逻辑

最终总分 = 各项得分平均 * 10（百分制）

输出格式：仅返回JSON对象，不要添加其他文字。"""

    user_prompt = f"""## 用户问题
{query}

## 分析维度
{dims_str}

## 参考答案方向提示
{hints_str}
{competitor_section}
## AI助手的回答
{our_response[:3000]}

## 评分要求
请对以上回答进行评分，输出JSON格式：
{{
  "score_accuracy": 8,
  "score_completeness": 7,
  "score_practicality": 8,
  "score_depth": 6,
  "score_structure": 9,
  "total_score": 76,
  "summary": "简要评价（50字以内）",
  "strengths": ["优点1", "优点2"],
  "weaknesses": ["不足1", "不足2"]
}}"""

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = await client.post(
            DEEPSEEK_API_URL,
            json=payload,
            headers=headers,
            timeout=DEEPSEEK_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        # Parse JSON from response
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()

        score_data = json.loads(content)
        score_data["total_score"] = score_data.get("total_score",
            sum(score_data.get(k, 0) for k in ["score_accuracy", "score_completeness",
                 "score_practicality", "score_depth", "score_structure"]) // 5 * 10)
        return score_data

    except Exception as e:
        logger.warning(f"  Scoring error: {e}")
        return {
            "score_accuracy": 0,
            "score_completeness": 0,
            "score_practicality": 0,
            "score_depth": 0,
            "score_structure": 0,
            "total_score": 0,
            "summary": f"评分失败: {e}",
            "strengths": [],
            "weaknesses": [],
        }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(results: list[dict], output_dir: Path):
    """Generate a comprehensive evaluation report."""
    if not results:
        logger.warning("No results to report")
        return

    total = len(results)
    scores = [r.get("score", {}).get("total_score", 0) for r in results]
    avg_score = sum(scores) / len(scores) if scores else 0

    # Group by domain
    by_domain = defaultdict(list)
    for r in results:
        by_domain[r.get("domain", "unknown")].append(r)

    # Group by difficulty
    by_difficulty = defaultdict(list)
    for r in results:
        by_difficulty[r.get("difficulty", "unknown")].append(r)

    now = datetime.now()

    report = []
    report.append(f"# 易理明灯 评测报告")
    report.append(f"")
    report.append(f"- **生成时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"- **评测数量**: {total} 条")
    report.append(f"- **平均分**: {avg_score:.1f}/100")
    report.append(f"")
    report.append(f"## 总体统计")
    report.append(f"")
    report.append(f"| 指标 | 数值 |")
    report.append(f"|------|------|")
    report.append(f"| 评测总数 | {total} |")
    report.append(f"| 平均分 | {avg_score:.1f} |")
    report.append(f"| 最高分 | {max(scores):.0f} |")
    report.append(f"| 最低分 | {min(scores):.0f} |")
    report.append(f"| 中位数 | {sorted(scores)[len(scores)//2]:.0f} |")
    report.append(f"")
    report.append(f"## 按领域统计")
    report.append(f"")
    report.append(f"| 领域 | 数量 | 平均分 |")
    report.append(f"|------|------|--------|")
    for domain, group in sorted(by_domain.items()):
        domain_scores = [r.get("score", {}).get("total_score", 0) for r in group]
        domain_avg = sum(domain_scores) / len(domain_scores)
        report.append(f"| {domain} | {len(group)} | {domain_avg:.1f} |")

    report.append(f"")
    report.append(f"## 按难度统计")
    report.append(f"")
    report.append(f"| 难度 | 数量 | 平均分 |")
    report.append(f"|------|------|--------|")
    for diff, group in sorted(by_difficulty.items()):
        diff_scores = [r.get("score", {}).get("total_score", 0) for r in group]
        diff_avg = sum(diff_scores) / len(diff_scores)
        report.append(f"| {diff} | {len(group)} | {diff_avg:.1f} |")

    # Score distribution
    report.append(f"")
    report.append(f"## 分数分布")
    report.append(f"")
    buckets = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    for lo, hi in buckets:
        count = sum(1 for s in scores if lo <= s < hi)
        bar = "█" * (count * 30 // max(total, 1))
        report.append(f"| {lo}-{hi} | {count:3d} | {bar} |")
    perfect = sum(1 for s in scores if s == 100)
    report.append(f"| 100    | {perfect:3d} | {'█' * perfect} |")

    report.append(f"")
    report.append(f"## 按维度评分")
    report.append(f"")
    dims = ["score_accuracy", "score_completeness", "score_practicality", "score_depth", "score_structure"]
    dim_labels = ["准确性", "完整性", "实用性", "深度", "结构化"]
    for dim, label in zip(dims, dim_labels):
        dim_scores = [r.get("score", {}).get(dim, 0) for r in results]
        dim_avg = sum(dim_scores) / len(dim_scores) if dim_scores else 0
        bar = "█" * int(dim_avg)
        report.append(f"| {label} | {dim_avg:.1f}/10 | {bar}")

    report.append(f"")
    report.append(f"## 详细结果")
    report.append(f"")
    for i, r in enumerate(results, 1):
        score = r.get("score", {})
        total_s = score.get("total_score", 0)
        summary = score.get("summary", "")
        query_short = r.get("query", "")[:60]
        domain = r.get("domain", "?")
        diff = r.get("difficulty", "?")
        report.append(f"### {i}. [{domain}/{diff}] {query_short}")
        report.append(f"")
        report.append(f"- 总分: **{total_s}/100**")
        report.append(f"- 准确性: {score.get('score_accuracy', 0)}/10, "
                      f"完整性: {score.get('score_completeness', 0)}/10, "
                      f"实用性: {score.get('score_practicality', 0)}/10, "
                      f"深度: {score.get('score_depth', 0)}/10, "
                      f"结构化: {score.get('score_structure', 0)}/10")
        if summary:
            report.append(f"- 评语: {summary}")
        if score.get("strengths"):
            report.append(f"- 优点: {'; '.join(score['strengths'][:2])}")
        if score.get("weaknesses"):
            report.append(f"- 不足: {'; '.join(score['weaknesses'][:2])}")
        report.append(f"")

    # Write report
    report_path = output_dir / "report.md"
    report_text = "\n".join(report)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    logger.info(f"Report saved to {report_path}")

    # Also save raw results as JSON
    results_path = output_dir / "results.json"
    clean_results = []
    for r in results:
        clean_results.append({
            "id": r.get("id"),
            "domain": r.get("domain"),
            "difficulty": r.get("difficulty"),
            "query": r.get("query"),
            "our_response": r.get("our_response", "")[:500],
            "score": r.get("score", {}),
            "error": r.get("error"),
        })
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(clean_results, f, ensure_ascii=False, indent=2)
    logger.info(f"Results JSON saved to {results_path}")

    # Summary JSON for programmatic use
    summary = {
        "timestamp": now.isoformat(),
        "total": total,
        "avg_score": round(avg_score, 1),
        "max_score": max(scores),
        "min_score": min(scores),
        "median_score": sorted(scores)[len(scores)//2],
        "by_domain": {d: {"count": len(g), "avg_score": round(
            sum(r.get("score", {}).get("total_score", 0) for r in g) / len(g), 1)}
            for d, g in by_domain.items()},
        "by_difficulty": {d: {"count": len(g), "avg_score": round(
            sum(r.get("score", {}).get("total_score", 0) for r in g) / len(g), 1)}
            for d, g in by_difficulty.items()},
        "results_file": str(results_path),
        "report_file": str(report_path),
    }
    summary_path = output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info(f"Summary saved to {summary_path}")

    return report_text, summary


# ---------------------------------------------------------------------------
# Main evaluation pipeline
# ---------------------------------------------------------------------------


async def run_evaluation(args):
    """Run the full evaluation pipeline."""
    # Ensure results directory exists
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = RESULTS_DIR / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load benchmark queries
    queries = load_benchmark_queries(args.domain)
    if not queries:
        logger.error("No benchmark queries loaded. Cannot proceed.")
        return

    # Sample if needed
    if args.sample and args.sample < len(queries):
        queries = sample_queries(queries, args.sample)
    elif args.full:
        pass  # Use all queries
    elif args.sample is None and not args.full:
        # Default: sample 50 if not specified
        if len(queries) > 50:
            queries = sample_queries(queries, 50)

    logger.info(f"Running evaluation on {len(queries)} queries")
    logger.info(f"Output directory: {output_dir}")

    # Load competitor data for context
    competitor_data = load_competitor_data(args.domain) if args.include_competitor else []

    # Save metadata
    meta = {
        "timestamp": datetime.now().isoformat(),
        "total_queries": len(queries),
        "domain": args.domain,
        "sample": args.sample,
        "full": args.full,
        "include_competitor": args.include_competitor,
        "competitor_records": len(competitor_data),
    }
    with open(output_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if args.dry_run:
        logger.info("DRY RUN — printing query summary:")
        for q in queries:
            logger.info(f"  [{q.get('id','???')}] [{q.get('domain','?')}/{q.get('difficulty','?')}] "
                        f"{q.get('query','')[:80]}")
        logger.info(f"\nWould evaluate {len(queries)} queries. "
                    f"Competitor data: {len(competitor_data)} records available.")
        return

    # Create HTTP clients
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(OUR_API_TIMEOUT, connect=15.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    ) as api_client:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(DEEPSEEK_TIMEOUT, connect=15.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        ) as scoring_client:

            results = []
            completed = 0
            failed = 0

            for i, query in enumerate(queries):
                q_text = query.get("query", "")
                q_id = query.get("id", f"Q{i:04d}")
                q_domain = query.get("domain", "?")
                q_diff = query.get("difficulty", "?")
                expected_dims = query.get("expected_dimensions", [])
                ref_hints = query.get("reference_answer_hints", [])

                logger.info(f"[{i+1}/{len(queries)}] {q_id} [{q_domain}/{q_diff}]: {q_text[:60]}...")

                # Find relevant competitor context
                competitor_context = None
                if competitor_data:
                    # Simple keyword matching
                    keywords = set(q_text)
                    matches = []
                    for c in competitor_data:
                        c_query = c.get("query", "")
                        overlap = len(set(c_query) & keywords)
                        if overlap > 3:
                            matches.append(c)
                    if matches:
                        competitor_context = matches[0].get("response", "")[:1000]

                # Query our API
                our_response = await query_our_api(api_client, q_text)

                if our_response:
                    # Score
                    score = await score_with_llm(
                        scoring_client, q_text, our_response,
                        expected_dims, ref_hints, competitor_context,
                    )

                    result = {
                        "id": q_id,
                        "domain": q_domain,
                        "difficulty": q_diff,
                        "query": q_text,
                        "our_response": our_response,
                        "expected_dimensions": expected_dims,
                        "reference_hints": ref_hints,
                        "score": score,
                        "competitor_context_used": competitor_context is not None,
                    }
                    results.append(result)
                    completed += 1

                    # Log score
                    total_score = score.get("total_score", 0)
                    logger.info(f"  -> Score: {total_score}/100 | {score.get('summary', '')}")
                else:
                    result = {
                        "id": q_id,
                        "domain": q_domain,
                        "difficulty": q_diff,
                        "query": q_text,
                        "our_response": None,
                        "expected_dimensions": expected_dims,
                        "reference_hints": ref_hints,
                        "score": {"total_score": 0, "summary": "API调用失败"},
                        "error": "API call failed",
                        "competitor_context_used": False,
                    }
                    results.append(result)
                    failed += 1
                    logger.warning(f"  -> FAILED to get response")

                # Save incremental results every 10 queries
                if (i + 1) % 10 == 0:
                    inc_path = output_dir / f"incremental_{i+1}.json"
                    with open(inc_path, "w", encoding="utf-8") as f:
                        json.dump(results, f, ensure_ascii=False, indent=2)

                # Polite delay between API calls
                await asyncio.sleep(0.5)

            # Generate final report
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Evaluation complete: {completed} completed, {failed} failed")
            logger.info(f"{'=' * 60}")

            if results:
                report_text, summary = generate_report(results, output_dir)
                avg_score = summary.get("avg_score", 0)
                logger.info(f"Average score: {avg_score}/100")
                logger.info(f"Report: {output_dir}/report.md")
            else:
                logger.warning("No results to report")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        description="易理明灯 — Benchmark Evaluation Runner"
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Number of random queries to evaluate (default: 50)"
    )
    parser.add_argument(
        "--domain", type=str, default=None,
        choices=["bazi", "ziwei", "fengshui", "dream", "mianxiang",
                 "qimen", "xingming", "zeri"],
        help="Filter by domain"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Evaluate all available queries (overrides --sample)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be evaluated without making API calls"
    )
    parser.add_argument(
        "--include-competitor", action="store_true",
        help="Include competitor data in evaluation context"
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    print(f"{'=' * 60}")
    print(f"  易理明灯 — Benchmark Evaluation Runner")
    print(f"{'=' * 60}")
    print(f"  Dry run:      {'YES' if args.dry_run else 'NO'}")
    print(f"  Full eval:    {'YES' if args.full else 'NO'}")
    print(f"  Sample size:  {args.sample if args.sample else 'default'}")
    print(f"  Domain:       {args.domain if args.domain else 'all'}")
    print(f"  Competitor:   {'YES' if args.include_competitor else 'NO'}")
    print(f"  Output dir:   {RESULTS_DIR}/")
    print()

    await run_evaluation(args)


if __name__ == "__main__":
    asyncio.run(main())
