"""易理明灯 Competitive Benchmarking Engine.

Modules:
    engine:   BenchmarkRunner, BenchmarkResult, DimensionScores
    scorer:   LLMScorer — DeepSeek-based multi-dimension scoring
    reporter: ReportGenerator — results aggregation and reporting
"""

from .engine import BenchmarkRunner, BenchmarkResult, DimensionScores
from .scorer import LLMScorer
from .reporter import ReportGenerator

__all__ = [
    "BenchmarkRunner",
    "BenchmarkResult",
    "DimensionScores",
    "LLMScorer",
    "ReportGenerator",
]
