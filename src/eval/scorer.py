"""DeepSeek-based multi-dimension scoring for fortune-telling responses.

Each response is scored across five dimensions (0-10) with detailed rubrics
to ensure consistency across queries and competitors.
"""

import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dimension weights (must sum to 1.0)
# ---------------------------------------------------------------------------
DIMENSION_WEIGHTS: dict[str, float] = {
    "accuracy": 0.30,
    "completeness": 0.25,
    "personalization": 0.20,
    "actionability": 0.15,
    "citation_quality": 0.10,
}


# ---------------------------------------------------------------------------
# Scoring rubric per dimension — embedded in the prompt for consistency
# ---------------------------------------------------------------------------
SCORING_RUBRIC = """
## Scoring Rubric (each dimension 0-10)

### accuracy (weight 30%)
Are the 命理 (mingli/fate-calculation) theories correctly applied?
- **0-2**: Major factual errors; completely wrong五行,十神, or basic concepts
- **3-4**: Several noticeable errors in theory application
- **5-6**: Mostly correct with minor inaccuracies
- **7-8**: Accurate application; very few if any mistakes
- **9-10**: Flawless; precise theory application with deep understanding

### completeness (weight 25%)
Does it cover all aspects of the user's question?
- **0-2**: Ignores most of the query; barely addresses the topic
- **3-4**: Addresses only one aspect; misses important dimensions
- **5-6**: Covers main points but lacks depth or misses subtopics
- **7-8**: Thorough coverage of all mentioned aspects
- **9-10**: Comprehensive; anticipates related questions the user didn't ask

### personalization (weight 20%)
Does it leverage the user's specific information or is it generic?
- **0-2**: Completely generic template — could apply to anyone
- **3-4**: Mostly generic with token personalization (just name/date)
- **5-6**: Moderate personalization; uses some user details meaningfully
- **7-8**: Strong personalization; analysis clearly tied to user's specifics
- **9-10**: Deeply personalized; each insight is uniquely tied to this user

### actionability (weight 15%)
Are there concrete, practical suggestions the user can act on?
- **0-2**: No suggestions at all; purely theoretical or fatalistic
- **3-4**: Vague advice ("be careful", "good things will come")
- **5-6**: Some specific suggestions but lacking practical detail
- **7-8**: Clear actionable steps with practical guidance
- **9-10**: Detailed, concrete action plan with timing, methods, and rationale

### citation_quality (weight 10%)
Are classical references (三命通会, 渊海子平, etc.) accurate and sourced?
- **0-2**: No references or completely fabricated ones
- **3-4**: References exist but are inaccurate or misattributed
- **5-6**: References generally correct but vague (no specific passage)
- **7-8**: Accurate references with specific book/chapter mentions
- **9-10**: Precise classical citations with correct context and interpretation
"""


SCORING_SYSTEM_PROMPT = """你是一位严谨的算命命理评分专家。你的任务是对AI算命助手的回复进行多维度评分。

你将对每个回复在以下五个维度上打分（0-10分），每个维度都有详细的评分标准。

重要规则：
1. 分数必须为0-10之间的整数或半整数（如7.5）
2. 每次评分必须给出具体的评分理由
3. 保持一致性——相同的回复质量应得到相同的分数
4. 如果回复为空或完全无关，所有维度给0分
5. 基于回复的实际质量评分，不要因为回复风格（如语气）而加减分

{scoring_rubric}

## Output Format

你必须以JSON格式输出评分结果，不要包含其他内容：

```json
{{
  "accuracy": {{
    "score": 7.0,
    "justification": "..."
  }},
  "completeness": ...
}}
```

每个dimension包含score（0-10的浮点数）和justification（中文评分理由）。
"""


@dataclass
class DimensionScore:
    """Score for a single dimension with justification."""
    score: float
    justification: str


@dataclass
class ScoredResponse:
    """Full scoring result for one response."""
    accuracy: DimensionScore
    completeness: DimensionScore
    personalization: DimensionScore
    actionability: DimensionScore
    citation_quality: DimensionScore
    overall: float  # weighted sum
    raw_json: str   # raw LLM response for audit

    def to_dict(self) -> dict:
        d = {k: asdict(v) if hasattr(v, '__dataclass_fields__') else v
             for k, v in asdict(self).items()}
        return d


class LLMScorer:
    """Score fortune-telling responses across 5 dimensions using DeepSeek.

    Uses the DeepSeek chat API to produce consistent, rubric-grounded scores.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        api_base: str = "https://api.deepseek.com/v1",
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_response(
        self,
        query: str,
        response: str,
        domain: str = "general",
        user_info: Optional[dict] = None,
    ) -> ScoredResponse:
        """Score a single (query, response) pair across all 5 dimensions.

        Args:
            query: The original user question.
            response: The fortune-telling response to evaluate.
            domain: Domain context (e.g. "八字", "紫微斗数", "六爻", "风水").
            user_info: Optional dict with user details for personalization eval.

        Returns:
            ScoredResponse with per-dimension scores and weighted overall.
        """
        response_text = (response or "").strip()
        if not response_text:
            return self._zero_score("Empty response")

        domain_hint = f"领域：{domain}\n" if domain else ""
        user_hint = ""
        if user_info:
            user_hint = f"用户信息：{json.dumps(user_info, ensure_ascii=False)}\n"

        user_prompt = (
            f"请对以下命理问答进行评分。\n\n"
            f"{domain_hint}"
            f"{user_hint}"
            f"【用户提问】\n{query}\n\n"
            f"【AI回复】\n{response_text}"
        )

        system_prompt = SCORING_SYSTEM_PROMPT.format(scoring_rubric=SCORING_RUBRIC)

        try:
            raw = self._call_llm(system_prompt, user_prompt)
            parsed = self._parse_scores(raw)
            overall = self._compute_overall(parsed)
            return ScoredResponse(
                accuracy=parsed["accuracy"],
                completeness=parsed["completeness"],
                personalization=parsed["personalization"],
                actionability=parsed["actionability"],
                citation_quality=parsed["citation_quality"],
                overall=overall,
                raw_json=raw,
            )
        except Exception as e:
            logger.warning("Scoring failed for query=%r: %s", query[:80], e)
            return self._zero_score(f"Scoring error: {e}")

    def score_batch(
        self,
        items: list[tuple[str, str, str]],  # (query, response, domain)
        user_infos: Optional[list[Optional[dict]]] = None,
    ) -> list[ScoredResponse]:
        """Score multiple (query, response) pairs.

        Args:
            items: List of (query, response, domain) tuples.
            user_infos: Optional per-item user info dicts.

        Returns:
            List of ScoredResponse in the same order.
        """
        if user_infos is None:
            user_infos = [None] * len(items)
        return [
            self.score_response(q, r, d, ui)
            for (q, r, d), ui in zip(items, user_infos)
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call DeepSeek and return raw response text."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1024,
            "temperature": 0.3,  # low temp for consistency
        }
        resp = self._client.post(
            f"{self.api_base}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _parse_scores(self, raw: str) -> dict[str, DimensionScore]:
        """Extract DimensionScores from raw LLM JSON output.

        Uses multiple fallback strategies for robustness:
        1. Exact JSON parsing after extracting code blocks
        2. Iterative brace-matching extraction
        3. Regex-based score extraction from any text format
        """
        raw_stripped = raw.strip()
        if not raw_stripped:
            logger.info("Empty scoring response — returning zeros")
            return self._build_from_dict({})

        # Strategy 1: Extract JSON block and parse
        json_str = raw_stripped
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        # Try to parse JSON directly
        try:
            data = json.loads(json_str)
            return self._build_from_dict(data)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Find the first { ... } block that looks like JSON
        brace_start = json_str.find("{")
        if brace_start >= 0:
            # Try progressively nearer brace matches
            for end_offset in range(len(json_str), brace_start, -1):
                candidate = json_str[brace_start:end_offset]
                try:
                    data = json.loads(candidate)
                    return self._build_from_dict(data)
                except json.JSONDecodeError:
                    continue

        # Strategy 3: Regex extraction of scores from any text format
        logger.info("Falling back to regex score extraction")
        dim_patterns = {
            dim: re.compile(
                rf'"{dim}"\s*:\s*{{.*?"score"\s*:\s*([\d.]+).*?"justification"\s*:\s*"(.+?)"\s*}}',
                re.DOTALL,
            )
            for dim in ("accuracy", "completeness", "personalization", "actionability", "citation_quality")
        }

        result = {}
        for dim in ("accuracy", "completeness", "personalization", "actionability", "citation_quality"):
            score = 0.0
            justification = "Extracted via regex fallback"

            # Try structured pattern
            m = dim_patterns[dim].search(raw)
            if m:
                score = float(m.group(1))
                justification = m.group(2)[:300]
            else:
                # Broader regex: "dim": { "score": X, ...
                m2 = re.search(
                    rf'"{dim}"[^}}]*?"score"\s*:\s*([\d.]+)',
                    raw, re.DOTALL
                )
                if m2:
                    score = float(m2.group(1))

                # Try to find some justification text nearby
                jm = re.search(
                    rf'"{dim}"[^}}]*?"justification"\s*:\s*"([^"]+)"',
                    raw, re.DOTALL
                )
                if jm:
                    justification = jm.group(1)[:300]

            score = max(0.0, min(10.0, score))
            result[dim] = DimensionScore(score=score, justification=justification)

        return result

    def _build_from_dict(self, data: dict) -> dict[str, DimensionScore]:
        """Build DimensionScores from a parsed dict."""
        result = {}
        for dim in ("accuracy", "completeness", "personalization", "actionability", "citation_quality"):
            entry = data.get(dim, {})
            score = float(entry.get("score", 0))
            score = max(0.0, min(10.0, score))
            justification = entry.get("justification", "") or ""
            result[dim] = DimensionScore(score=score, justification=justification)
        return result

    def _compute_overall(self, parsed: dict[str, DimensionScore]) -> float:
        """Weighted sum of dimension scores."""
        total = 0.0
        for dim, weight in DIMENSION_WEIGHTS.items():
            total += parsed[dim].score * weight
        return round(total, 2)

    def _zero_score(self, reason: str) -> ScoredResponse:
        """Return a zero-score result (for edge cases)."""
        ds = DimensionScore(score=0.0, justification=reason)
        return ScoredResponse(
            accuracy=ds,
            completeness=ds,
            personalization=ds,
            actionability=ds,
            citation_quality=ds,
            overall=0.0,
            raw_json=json.dumps({"error": reason}),
        )

    def close(self):
        """Close the underlying HTTP client."""
        self._client.close()
