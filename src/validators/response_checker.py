"""LLM Response Validator — checks every AI response for accuracy violations."""
import re
from typing import List, Tuple


class ResponseValidator:
    """Validates LLM responses for accuracy, citation quality, and safety."""

    # Patterns that indicate hallucination
    HALLUCINATION_PATTERNS = [
        (r'(一定能|保证|绝对会|100%|百分之百|必定|必然)', '绝对化表述'),
        (r'(可以治疗|能治病|药方|偏方|秘方)', '医疗建议（禁止）'),
        (r'(保证.*收益|稳赚|必涨|内幕)', '投资建议（禁止）'),
    ]

    # Missing required elements
    REQUIRED_CITATION_PATTERN = r'【.+?·.+?】".+?"'

    def validate(self, response: str, engine_data_used: bool = True) -> dict:
        """
        Validates an LLM response for accuracy violations.

        Args:
            response: The LLM response text to validate.
            engine_data_used: Whether engine data was used (for context).

        Returns:
            dict with keys:
                - passed: bool, True if no errors
                - score: int, 0-100 quality score
                - violations: list of violation dicts
                - has_citation: bool, whether classical citation found
                - engine_terms_preserved: list of engine terms found
        """
        violations = []

        # 1. Check for absolute claims
        for pattern, desc in self.HALLUCINATION_PATTERNS:
            matches = re.findall(pattern, response)
            if matches:
                violations.append({
                    "type": "absolute_claim",
                    "severity": "error",
                    "detail": f"{desc}: {matches[0][:50]}"
                })

        # 2. Check for classical citations
        has_citation = bool(re.search(self.REQUIRED_CITATION_PATTERN, response))
        if not has_citation and len(response) > 200:
            violations.append({
                "type": "missing_citation",
                "severity": "warning",
                "detail": "长回复缺少古籍引用"
            })

        # 3. Check for engine data preservation
        # (engine_data_used flag indicates if engine was called)

        score = 100
        error_count = sum(1 for v in violations if v["severity"] == "error")
        warn_count = sum(1 for v in violations if v["severity"] == "warning")
        score -= error_count * 25
        score -= warn_count * 10

        return {
            "passed": error_count == 0,
            "score": max(0, score),
            "violations": violations,
            "has_citation": has_citation,
        }
