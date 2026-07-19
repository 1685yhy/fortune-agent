"""Competitive benchmarking engine for 易理明灯.

BenchmarkRunner coordinates:
1. Querying our own API (or reading pre-collected responses)
2. Scoring all responses via LLMScorer
3. Determining winners per query
4. Persisting results as versioned JSONL
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from .scorer import LLMScorer, ScoredResponse, DIMENSION_WEIGHTS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DimensionScores:
    """Aggregated dimension scores (averaged across 0-10 scale)."""
    accuracy: float = 0.0
    completeness: float = 0.0
    personalization: float = 0.0
    actionability: float = 0.0
    citation_quality: float = 0.0
    overall: float = 0.0


@dataclass
class BenchmarkResult:
    """Result of benchmarking a single query."""
    query_id: str
    domain: str
    difficulty: str
    query: str
    our_response: str
    competitor_responses: dict[str, str] = field(default_factory=dict)
    scores: dict[str, DimensionScores] = field(default_factory=dict)
    winner: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Convert nested dataclasses
        d["scores"] = {
            k: asdict(v) if hasattr(v, "__dataclass_fields__") else v
            for k, v in d.get("scores", {}).items()
        }
        return d


# ---------------------------------------------------------------------------
# BenchmarkRunner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    """Run competitive benchmarks: query our API, score responses, determine winners.

    Usage:
        runner = BenchmarkRunner(api_key="ds-xxx", our_api_url="http://...")
        result = runner.run_single(query)
        results = await runner.run_batch(queries, competitors=["问真", "测测"])
        runner.save_results(results, "data/benchmarks/run_001.jsonl")
    """

    def __init__(
        self,
        api_key: str,
        our_api_url: str = "http://124.221.233.214:8765/api/chat",
        scorer_model: str = "deepseek-v4-flash",
        scorer_api_base: str = "https://api.deepseek.com/v1",
        our_api_key: Optional[str] = None,
    ):
        self.our_api_url = our_api_url.rstrip("/")
        self.our_api_key = our_api_key or api_key
        self.scorer = LLMScorer(
            api_key=api_key,
            model=scorer_model,
            api_base=scorer_api_base,
        )
        # Shared HTTP client for our API
        self._client = httpx.Client(
            timeout=httpx.Timeout(120.0, connect=15.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    # ------------------------------------------------------------------
    # Run single query
    # ------------------------------------------------------------------

    def run_single(self, query: dict) -> BenchmarkResult:
        """Run one query against our system, score our response.

        Args:
            query: Dict with keys:
                - query_id (str): Unique identifier
                - domain (str): Domain (八字, 紫微斗数, 六爻, 风水, etc.)
                - difficulty (str): easy / medium / hard
                - query (str): The user question text
                - user_info (dict, optional): User details for personalization
                - messages (list, optional): Full message history for context
                - competitors (dict, optional): Pre-collected competitor responses
                  {name: response_text}

        Returns:
            BenchmarkResult with our score (and competitor scores if provided).
        """
        query_id = query.get("query_id", f"q_{int(time.time())}")
        domain = query.get("domain", "general")
        difficulty = query.get("difficulty", "medium")
        query_text = query.get("query", "")
        user_info = query.get("user_info")
        messages = query.get("messages")
        competitors = query.get("competitors", {})

        # 1. Query our API
        our_response = self._query_our_api(query_text, messages)

        # 2. Score our response
        our_scored = self.scorer.score_response(query_text, our_response, domain, user_info)

        # 3. Score competitor responses (if provided)
        all_scores: dict[str, DimensionScores] = {
            "our": self._to_dimension_scores(our_scored),
        }
        comp_responses: dict[str, str] = {}
        for name, comp_response in competitors.items():
            comp_responses[name] = comp_response
            comp_scored = self.scorer.score_response(query_text, comp_response, domain)
            all_scores[name] = self._to_dimension_scores(comp_scored)

        # 4. Determine winner
        winner = self._determine_winner(all_scores)

        return BenchmarkResult(
            query_id=query_id,
            domain=domain,
            difficulty=difficulty,
            query=query_text,
            our_response=our_response,
            competitor_responses=comp_responses,
            scores=all_scores,
            winner=winner,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------
    # Run batch
    # ------------------------------------------------------------------

    def run_batch(
        self,
        queries: list[dict],
        competitors: Optional[list[str]] = None,
    ) -> list[BenchmarkResult]:
        """Run full benchmark suite.

        Args:
            queries: List of query dicts (see run_single for schema).
            competitors: Optional list of competitor names to include.
                         If provided, only these competitor keys from each
                         query's ``competitors`` dict are scored.

        Returns:
            List of BenchmarkResult, one per query.
        """
        if competitors:
            for q in queries:
                comps = q.get("competitors", {})
                q["competitors"] = {
                    k: v for k, v in comps.items() if k in competitors
                }

        results = []
        for i, q in enumerate(queries):
            logger.info("Benchmarking query %d/%d: %s", i + 1, len(queries), q.get("query_id", i))
            try:
                result = self.run_single(q)
                results.append(result)
            except Exception as e:
                logger.error("Query %s failed: %s", q.get("query_id", i), e)
                # Still record a failure entry
                results.append(BenchmarkResult(
                    query_id=q.get("query_id", f"q_{i}"),
                    domain=q.get("domain", "unknown"),
                    difficulty=q.get("difficulty", "unknown"),
                    query=q.get("query", ""),
                    our_response=f"ERROR: {e}",
                    competitor_responses={},
                    scores={"our": DimensionScores()},
                    winner="error",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))
        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_results(self, results: list[BenchmarkResult], path: str):
        """Save results as JSONL with version metadata.

        Each line is a complete BenchmarkResult JSON. A companion ``_meta.json``
        file is written alongside with summary metadata.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write JSONL
        with path.open("w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

        # Write metadata
        meta_path = path.with_suffix(".meta.json")
        scores = [r.scores.get("our", DimensionScores()) for r in results]
        avg_overall = sum(s.overall for s in scores) / max(len(scores), 1)
        meta = {
            "version": self._version_id(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "num_queries": len(results),
            "avg_overall_score": round(avg_overall, 2),
            "domains": list({r.domain for r in results}),
            "difficulties": list({r.difficulty for r in results}),
            "winners": {r.winner: sum(1 for r2 in results if r2.winner == r.winner)
                        for r in results},
        }
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        logger.info("Saved %d results to %s (meta: %s)", len(results), path, meta_path)

    @staticmethod
    def load_results(path: str) -> list[BenchmarkResult]:
        """Load results from a JSONL file created by save_results."""
        results = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                # Reconstruct nested dataclasses
                scores = {}
                for k, v in data.get("scores", {}).items():
                    scores[k] = DimensionScores(**v)
                data["scores"] = scores
                results.append(BenchmarkResult(**data))
        return results

    # ------------------------------------------------------------------
    # Version comparison
    # ------------------------------------------------------------------

    @staticmethod
    def compare_versions(v1_path: str, v2_path: str) -> dict:
        """Compare two benchmark runs — what improved, what regressed.

        Args:
            v1_path: Path to older benchmark JSONL.
            v2_path: Path to newer benchmark JSONL.

        Returns:
            Dict with keys: improved_queries, regressed_queries,
            unchanged_queries, avg_delta, domain_deltas, difficulty_deltas.
        """
        r1 = BenchmarkRunner.load_results(v1_path)
        r2 = BenchmarkRunner.load_results(v2_path)

        # Index by query_id
        idx1 = {r.query_id: r for r in r1}
        idx2 = {r.query_id: r for r in r2}

        common_ids = set(idx1.keys()) & set(idx2.keys())
        improved = []
        regressed = []
        unchanged = []
        deltas = []

        for qid in common_ids:
            s1 = idx1[qid].scores.get("our", DimensionScores()).overall
            s2 = idx2[qid].scores.get("our", DimensionScores()).overall
            delta = s2 - s1
            deltas.append(delta)
            entry = {
                "query_id": qid,
                "domain": idx2[qid].domain,
                "difficulty": idx2[qid].difficulty,
                "old_score": s1,
                "new_score": s2,
                "delta": round(delta, 2),
                "old_winner": idx1[qid].winner,
                "new_winner": idx2[qid].winner,
            }
            if delta > 0.5:
                improved.append(entry)
            elif delta < -0.5:
                regressed.append(entry)
            else:
                unchanged.append(entry)

        # Domain breakdown
        domain_deltas = {}
        for e in improved + regressed + unchanged:
            dom = e["domain"]
            if dom not in domain_deltas:
                domain_deltas[dom] = []
            domain_deltas[dom].append(e["delta"])

        domain_avg = {
            dom: round(sum(ds) / max(len(ds), 1), 2)
            for dom, ds in domain_deltas.items()
        }

        # Difficulty breakdown
        diff_deltas = {}
        for e in improved + regressed + unchanged:
            diff = e["difficulty"]
            if diff not in diff_deltas:
                diff_deltas[diff] = []
            diff_deltas[diff].append(e["delta"])

        diff_avg = {
            d: round(sum(vs) / max(len(vs), 1), 2)
            for d, vs in diff_deltas.items()
        }

        avg_delta = round(sum(deltas) / max(len(deltas), 1), 2) if deltas else 0.0

        return {
            "v1_path": v1_path,
            "v2_path": v2_path,
            "v1_queries": len(r1),
            "v2_queries": len(r2),
            "common_queries": len(common_ids),
            "avg_delta": avg_delta,
            "improved_count": len(improved),
            "regressed_count": len(regressed),
            "unchanged_count": len(unchanged),
            "improved_queries": improved,
            "regressed_queries": regressed,
            "domain_deltas": domain_avg,
            "difficulty_deltas": diff_avg,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _query_our_api(self, query: str, messages: Optional[list] = None) -> str:
        """Send a chat request to our deployed fortune-telling API."""
        payload: dict = {
            "messages": messages or [
                {"role": "user", "content": query},
            ],
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
        }
        if self.our_api_key:
            headers["Authorization"] = f"Bearer {self.our_api_key}"

        resp = self._client.post(f"{self.our_api_url}", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        # Handle various response shapes
        if isinstance(data, str):
            return data
        if "response" in data:
            return data["response"]
        if "content" in data:
            return data["content"]
        if "choices" in data and data["choices"]:
            msg = data["choices"][0].get("message", {})
            return msg.get("content", "")
        if "text" in data:
            return data["text"]
        # Fallback: return the whole JSON
        return json.dumps(data, ensure_ascii=False)

    def _to_dimension_scores(self, scored: ScoredResponse) -> DimensionScores:
        return DimensionScores(
            accuracy=scored.accuracy.score,
            completeness=scored.completeness.score,
            personalization=scored.personalization.score,
            actionability=scored.actionability.score,
            citation_quality=scored.citation_quality.score,
            overall=scored.overall,
        )

    @staticmethod
    def _determine_winner(scores: dict[str, DimensionScores]) -> str:
        """Pick the winner by comparing overall scores."""
        if not scores:
            return "none"
        best = max(scores, key=lambda k: scores[k].overall)  # type: ignore[arg-type]
        best_score = scores[best].overall
        # Check for ties
        tied = [k for k, v in scores.items() if v.overall == best_score]
        if len(tied) > 1:
            return "tie"
        return best

    @staticmethod
    def _version_id() -> str:
        return datetime.now(timezone.utc).strftime("v%Y%m%d_%H%M%S")

    def close(self):
        """Release resources."""
        self.scorer.close()
        self._client.close()
