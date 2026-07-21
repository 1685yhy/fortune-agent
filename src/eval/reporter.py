"""Results aggregation and reporting for benchmark runs.

ReportGenerator produces structured reports:
- Summary statistics (win rates, average scores)
- Per-domain breakdowns
- Weakness analysis (areas where we lag competitors)
- Trend reports (version-to-version comparison)
"""

import statistics
from collections import defaultdict
from typing import Optional

from .engine import BenchmarkResult, DimensionScores


class ReportGenerator:
    """Generate structured reports from benchmark results."""

    DIMENSION_LABELS = {
        "accuracy": "准确性",
        "completeness": "完整性",
        "personalization": "个性化",
        "actionability": "可操作性",
        "citation_quality": "引用质量",
    }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def generate_summary(self, results: list[BenchmarkResult]) -> dict:
        """Overall stats: win rate, average scores by domain and difficulty.

        Returns:
            Dict with keys:
                total_queries, domains, difficulties,
                win_rate (our vs each competitor),
                avg_scores (overall by domain/by difficulty),
                dimension_averages (global).
        """
        total = len(results)
        if total == 0:
            return {"total_queries": 0, "error": "No results"}

        # Collect all competitor names
        competitor_names = set()
        for r in results:
            competitor_names.update(r.scores.keys())
        competitor_names.discard("our")

        # Win counts
        win_counts: dict[str, int] = defaultdict(int)
        for r in results:
            if r.winner:
                win_counts[r.winner] += 1

        # Average overall scores by domain
        domain_scores: dict[str, list[float]] = defaultdict(list)
        difficulty_scores: dict[str, list[float]] = defaultdict(list)
        our_scores: list[float] = []
        dim_scores: dict[str, list[float]] = defaultdict(list)

        for r in results:
            our_ds = r.scores.get("our", DimensionScores())
            our_scores.append(our_ds.overall)
            domain_scores[r.domain].append(our_ds.overall)
            difficulty_scores[r.difficulty].append(our_ds.overall)

            for dim in ("accuracy", "completeness", "personalization",
                        "actionability", "citation_quality"):
                dim_scores[dim].append(getattr(our_ds, dim, 0.0))

        win_rate = {}
        for comp in competitor_names:
            wins = sum(1 for r in results if r.winner == "our" and comp in r.scores)
            total_with_comp = sum(1 for r in results if comp in r.scores)
            win_rate[comp] = {
                "our_wins": wins,
                "total": total_with_comp,
                "win_rate": round(wins / max(total_with_comp, 1), 3),
            }

        return {
            "total_queries": total,
            "domains": list(domain_scores.keys()),
            "difficulties": list(difficulty_scores.keys()),
            "competitors": list(competitor_names),
            "win_counts": dict(win_counts),
            "win_rate": win_rate,
            "avg_overall": round(statistics.mean(our_scores), 2) if our_scores else 0.0,
            "std_overall": round(statistics.stdev(our_scores), 2) if len(our_scores) > 1 else 0.0,
            "avg_overall_by_domain": {
                d: round(statistics.mean(v), 2)
                for d, v in domain_scores.items()
            },
            "avg_overall_by_difficulty": {
                d: round(statistics.mean(v), 2)
                for d, v in difficulty_scores.items()
            },
            "dimension_averages": {
                self.DIMENSION_LABELS.get(dim, dim): round(statistics.mean(v), 2)
                for dim, v in dim_scores.items()
            },
        }

    # ------------------------------------------------------------------
    # Domain breakdown
    # ------------------------------------------------------------------

    def generate_domain_breakdown(self, results: list[BenchmarkResult]) -> dict:
        """Per-domain detailed comparison.

        Returns:
            Dict keyed by domain, each containing average scores for each
            competitor across all dimensions.
        """
        domains: dict[str, dict[str, list[DimensionScores]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for r in results:
            for system_name, scores in r.scores.items():
                domains[r.domain][system_name].append(scores)

        breakdown = {}
        for domain, systems in domains.items():
            breakdown[domain] = {}
            for system_name, score_list in systems.items():
                breakdown[domain][system_name] = {
                    "count": len(score_list),
                    "avg_accuracy": round(
                        statistics.mean(s.accuracy for s in score_list), 2
                    ),
                    "avg_completeness": round(
                        statistics.mean(s.completeness for s in score_list), 2
                    ),
                    "avg_personalization": round(
                        statistics.mean(s.personalization for s in score_list), 2
                    ),
                    "avg_actionability": round(
                        statistics.mean(s.actionability for s in score_list), 2
                    ),
                    "avg_citation_quality": round(
                        statistics.mean(s.citation_quality for s in score_list), 2
                    ),
                    "avg_overall": round(
                        statistics.mean(s.overall for s in score_list), 2
                    ),
                }
        return breakdown

    # ------------------------------------------------------------------
    # Weakness report
    # ------------------------------------------------------------------

    def generate_weakness_report(
        self,
        results: list[BenchmarkResult],
        top_n: int = 20,
    ) -> list[dict]:
        """Identify areas where we score below competitors — sorted by gap size.

        For each query where a competitor outperforms us, record the gap.
        Returns the largest gaps first.

        Args:
            results: Benchmark results to analyze.
            top_n: Return top N weaknesses (largest gaps).

        Returns:
            List of dicts::
                {
                    "query_id": str,
                    "domain": str,
                    "difficulty": str,
                    "query": str (truncated),
                    "competitor": str,
                    "our_overall": float,
                    "their_overall": float,
                    "gap": float,
                    "weakest_dimension": str,
                    "weakest_gap": float,
                }
        """
        gaps = []

        for r in results:
            our_ds = r.scores.get("our")
            if our_ds is None:
                continue

            for comp_name, comp_ds in r.scores.items():
                if comp_name == "our":
                    continue
                gap = comp_ds.overall - our_ds.overall
                if gap > 0:
                    # Find the dimension with the largest gap
                    dim_gaps = {}
                    for dim in ("accuracy", "completeness", "personalization",
                                "actionability", "citation_quality"):
                        our_val = getattr(our_ds, dim, 0.0)
                        their_val = getattr(comp_ds, dim, 0.0)
                        dim_gaps[dim] = their_val - our_val
                    weakest = max(dim_gaps, key=dim_gaps.__getitem__)

                    gaps.append({
                        "query_id": r.query_id,
                        "domain": r.domain,
                        "difficulty": r.difficulty,
                        "query": r.query[:120] + ("..." if len(r.query) > 120 else ""),
                        "competitor": comp_name,
                        "our_overall": round(our_ds.overall, 2),
                        "their_overall": round(comp_ds.overall, 2),
                        "gap": round(gap, 2),
                        "weakest_dimension": weakest,
                        "weakest_gap": round(dim_gaps[weakest], 2),
                    })

        gaps.sort(key=lambda x: x["gap"], reverse=True)
        return gaps[:top_n]

    # ------------------------------------------------------------------
    # Trend report
    # ------------------------------------------------------------------

    def generate_trend_report(
        self,
        current: list[BenchmarkResult],
        previous: list[BenchmarkResult],
    ) -> dict:
        """Compare current vs previous benchmark run — what changed.

        Args:
            current: Newer benchmark results.
            previous: Older benchmark results.

        Returns:
            Dict with summary of changes across dimensions, domains,
            and difficulties.
        """
        if not previous:
            return {
                "has_previous": False,
                "message": "No previous benchmark data to compare.",
            }

        # Aggregate dimension averages
        def _avg_dim(results: list[BenchmarkResult], dim: str) -> float:
            vals = [getattr(r.scores.get("our", DimensionScores()), dim, 0.0)
                    for r in results]
            return statistics.mean(vals) if vals else 0.0

        prev_summary = self.generate_summary(previous)
        curr_summary = self.generate_summary(current)

        dim_deltas = {}
        for dim in ("accuracy", "completeness", "personalization",
                    "actionability", "citation_quality"):
            prev_avg = _avg_dim(previous, dim)
            curr_avg = _avg_dim(current, dim)
            dim_deltas[dim] = {
                "prev": round(prev_avg, 2),
                "curr": round(curr_avg, 2),
                "delta": round(curr_avg - prev_avg, 2),
            }

        # Domain deltas
        prev_domains = prev_summary.get("avg_overall_by_domain", {})
        curr_domains = curr_summary.get("avg_overall_by_domain", {})
        all_domains = set(prev_domains) | set(curr_domains)
        domain_deltas = {}
        for d in sorted(all_domains):
            p = prev_domains.get(d, 0.0)
            c = curr_domains.get(d, 0.0)
            domain_deltas[d] = {"prev": p, "curr": c, "delta": round(c - p, 2)}

        # Difficulty deltas
        prev_diffs = prev_summary.get("avg_overall_by_difficulty", {})
        curr_diffs = curr_summary.get("avg_overall_by_difficulty", {})
        all_diffs = set(prev_diffs) | set(curr_diffs)
        difficulty_deltas = {}
        for d in sorted(all_diffs):
            p = prev_diffs.get(d, 0.0)
            c = curr_diffs.get(d, 0.0)
            difficulty_deltas[d] = {"prev": p, "curr": c, "delta": round(c - p, 2)}

        # Win rate changes
        prev_wins = prev_summary.get("win_rate", {})
        curr_wins = curr_summary.get("win_rate", {})
        all_comps = set(prev_wins) | set(curr_wins)
        win_rate_deltas = {}
        for comp in sorted(all_comps):
            p_rate = prev_wins.get(comp, {}).get("win_rate", 0.0)
            c_rate = curr_wins.get(comp, {}).get("win_rate", 0.0)
            win_rate_deltas[comp] = {
                "prev_win_rate": p_rate,
                "curr_win_rate": c_rate,
                "delta": round(c_rate - p_rate, 3),
            }

        return {
            "has_previous": True,
            "prev_queries": len(previous),
            "curr_queries": len(current),
            "prev_avg_overall": prev_summary.get("avg_overall", 0.0),
            "curr_avg_overall": curr_summary.get("avg_overall", 0.0),
            "avg_overall_delta": round(
                curr_summary.get("avg_overall", 0.0)
                - prev_summary.get("avg_overall", 0.0),
                2,
            ),
            "dimension_deltas": dim_deltas,
            "domain_deltas": domain_deltas,
            "difficulty_deltas": difficulty_deltas,
            "win_rate_deltas": win_rate_deltas,
        }

    # ------------------------------------------------------------------
    # Full report
    # ------------------------------------------------------------------

    def generate_full_report(
        self,
        results: list[BenchmarkResult],
        previous: Optional[list[BenchmarkResult]] = None,
    ) -> dict:
        """Generate a comprehensive report combining all analyses.

        Args:
            results: Current benchmark results.
            previous: Optional previous results for trend analysis.

        Returns:
            Dict with keys: summary, domain_breakdown, weakness_report,
            and optionally trend_report.
        """
        report = {
            "summary": self.generate_summary(results),
            "domain_breakdown": self.generate_domain_breakdown(results),
            "weakness_report": self.generate_weakness_report(results),
        }

        if previous:
            report["trend_report"] = self.generate_trend_report(results, previous)

        return report
