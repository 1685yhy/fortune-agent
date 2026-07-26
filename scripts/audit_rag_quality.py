#!/usr/bin/env python3
"""RAG Quality Audit Script — evaluates retrieval quality across fortune types.

Runs sample queries through the RAG system and reports:
- Top-3 retrieved sources per query
- Relevance scoring
- Coverage gaps
- Average relevance across all types

Usage:
    python scripts/audit_rag_quality.py
"""

import json
import logging
import sys
import time
from pathlib import Path
from typing import List, Dict, Any

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rag_audit")


# Sample queries covering all fortune types
SAMPLE_QUERIES = [
    # 八字 (Bazi)
    {"query": "正官格和七杀格的区别是什么？", "type": "八字"},
    {"query": "日主弱而财星旺，应该如何取用神？", "type": "八字"},
    {"query": "八字中食神生财的条件是什么？", "type": "八字"},

    # 紫微斗数 (Ziwei)
    {"query": "紫微星在命宫代表什么性格特征？", "type": "紫微斗数"},
    {"query": "廉贞七杀在丑未宫坐命的格局如何？", "type": "紫微斗数"},
    {"query": "天相星在夫妻宫的含义是什么？", "type": "紫微斗数"},

    # 六爻 (Liuyao)
    {"query": "六爻占卜中妻财爻动化回头克的解读", "type": "六爻"},
    {"query": "六爻中世爻和应爻的关系如何判断？", "type": "六爻"},

    # 风水 (Fengshui)
    {"query": "玄空飞星中当旺星和退气星的区别", "type": "风水"},
    {"query": "八宅风水中的东四命和西四命如何划分？", "type": "风水"},
    {"query": "房屋缺角在风水中的影响及化解方法", "type": "风水"},

    # 择日 (Zeri)
    {"query": "结婚择日要避开哪些神煞？", "type": "择日"},
    {"query": "建除十二神中建日和除日的区别", "type": "择日"},

    # 面相手相 (Mianxiang)
    {"query": "面相中印堂发暗代表什么？", "type": "面相"},
    {"query": "手相中生命线长短与寿命的关系", "type": "面相"},

    # 合婚 (Hehun)
    {"query": "生肖不合真的会影响婚姻吗？", "type": "合婚"},
    {"query": "八字合婚中年柱相冲如何化解？", "type": "合婚"},

    # 姓名学 (Xingming)
    {"query": "五格剖象法中三才五格的配置吉凶", "type": "姓名学"},
    {"query": "姓名中总格数理对人生运势的影响", "type": "姓名学"},

    # 奇门遁甲 (Qimen)
    {"query": "奇门遁甲中值符值使的用法", "type": "奇门遁甲"},
]


def relevance_score(query: str, sources: List[Dict[str, Any]]) -> float:
    """Score retrieval relevance on 0-1 scale based on keyword overlap."""
    if not sources:
        return 0.0

    # Extract key terms from query (Chinese characters excluding stop words)
    import re
    stop_words = {"的", "了", "是", "在", "和", "与", "中", "吗", "什么", "如何", "哪些", "怎样",
                  "会", "不", "有", "就", "都", "而", "及", "等", "或", "之", "以", "这", "那"}
    query_chars = set(re.findall(r'[一-鿿]', query))
    query_terms = query_chars - stop_words

    if not query_terms:
        return 0.0

    scores = []
    for src in sources:
        content = src.get("content", "") or src.get("text", "") or ""
        if not content:
            scores.append(0.0)
            continue
        content_chars = set(content)
        overlap = len(query_terms & content_chars)
        score = overlap / len(query_terms) if query_terms else 0.0
        scores.append(score)

    return sum(scores) / len(scores) if scores else 0.0


def run_audit(embedder=None, retriever=None) -> Dict[str, Any]:
    """Run RAG quality audit and return report."""
    # Lazy imports — allow running audit checks without full app init
    if embedder is None or retriever is None:
        from src.config import load_settings
        from src.rag.embedder import Embedder
        from src.rag.retriever import Retriever

        settings = load_settings()
        embedder = Embedder(model_name=settings.embedding_model)
        if not embedder.load():
            logger.error("Failed to load embedding model — RAG audit cannot proceed.")
            return {"status": "error", "message": "Embedding model not available"}

        retriever = Retriever(str(settings.vectordb_dir), embedder)
        retriever._collection_name = settings.embedding_collection

    results = []
    type_scores: Dict[str, list] = {}

    for item in SAMPLE_QUERIES:
        query = item["query"]
        ftype = item["type"]

        logger.info(f"Query [{ftype}]: {query}")

        try:
            t0 = time.time()
            docs = retriever.search(query, top_k=3)
            elapsed = time.time() - t0

            sources = []
            for i, doc in enumerate(docs):
                content = doc.get("content", doc.get("text", ""))
                source = doc.get("source", doc.get("metadata", {}).get("source", "unknown"))
                title = doc.get("title", doc.get("metadata", {}).get("title", ""))
                sources.append({
                    "rank": i + 1,
                    "source": source,
                    "title": title,
                    "content_preview": content[:120] if content else "",
                })

            score = relevance_score(query, docs)
            type_scores.setdefault(ftype, []).append(score)

            entry = {
                "query": query,
                "type": ftype,
                "retrieval_time_ms": round(elapsed * 1000, 1),
                "num_results": len(docs),
                "sources": sources,
                "relevance": round(score, 3),
            }
            results.append(entry)

            status = "OK" if score >= 0.3 else "LOW"
            logger.info(f"  -> relevance={score:.3f} [{status}] ({elapsed:.2f}s)")

        except Exception as e:
            logger.error(f"  -> ERROR: {e}")
            results.append({
                "query": query,
                "type": ftype,
                "error": str(e),
                "relevance": 0.0,
            })
            type_scores.setdefault(ftype, []).append(0.0)

    # Compute type-level averages
    type_averages = {}
    for ftype, scores in sorted(type_scores.items()):
        type_averages[ftype] = {
            "avg_relevance": round(sum(scores) / len(scores), 3),
            "query_count": len(scores),
        }

    all_scores = [r.get("relevance", 0) for r in results if "relevance" in r]
    overall_avg = round(sum(all_scores) / len(all_scores), 3) if all_scores else 0

    # Identify coverage gaps (types with avg relevance < 0.3)
    coverage_gaps = [
        ftype for ftype, info in type_averages.items()
        if info["avg_relevance"] < 0.3
    ]

    report = {
        "status": "ok",
        "audit_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_queries": len(results),
        "overall_avg_relevance": overall_avg,
        "type_averages": type_averages,
        "coverage_gaps": coverage_gaps,
        "results": results,
    }

    return report


def print_report(report: Dict[str, Any]) -> None:
    """Print a human-readable audit report."""
    print("=" * 70)
    print("  RAG QUALITY AUDIT REPORT")
    print("=" * 70)
    print(f"  Time: {report.get('audit_timestamp', 'N/A')}")
    print(f"  Total queries: {report.get('total_queries', 0)}")
    print(f"  Overall avg relevance: {report.get('overall_avg_relevance', 'N/A')}")
    print()

    if "coverage_gaps" in report and report["coverage_gaps"]:
        print("  !!! COVERAGE GAPS DETECTED !!!")
        for gap in report["coverage_gaps"]:
            print(f"    - {gap}: avg relevance < 0.3")
        print()

    print("  Per-Type Averages:")
    print(f"  {'Type':<15} {'Avg Relevance':<15} {'Queries':<10}")
    print(f"  {'-'*15} {'-'*15} {'-'*10}")
    for ftype, info in sorted(report.get("type_averages", {}).items()):
        marker = " !!! " if info["avg_relevance"] < 0.3 else "     "
        print(f"  {marker} {ftype:<12} {info['avg_relevance']:<15.3f} {info['query_count']:<10}")
    print()

    print("  Per-Query Details:")
    for r in report.get("results", []):
        rel = r.get("relevance", r.get("score", "N/A"))
        status = "  OK" if isinstance(rel, (int, float)) and rel >= 0.3 else " LOW"
        print(f"  [{r['type']}]{status} rel={rel}")
        print(f"    Q: {r['query']}")
        if "sources" in r:
            for s in r["sources"]:
                src = s.get("source", "?")
                title = s.get("title", "")
                preview = s.get("content_preview", "")[:60]
                print(f"      #{s['rank']}: {src} | {title or preview}")
        print()

    print("=" * 70)
    print("  END OF REPORT")
    print("=" * 70)


if __name__ == "__main__":
    report = run_audit()
    print_report(report)

    # Save JSON report
    output_path = Path(__file__).parent / "generated" / "rag_audit_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"Report saved to {output_path}")
