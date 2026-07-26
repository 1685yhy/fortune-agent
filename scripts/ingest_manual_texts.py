#!/usr/bin/env python3
"""Ingest curated classical text snippets into ChromaDB.

Reads manually curated classical Chinese text excerpts from
data/classical_texts/manual_snippets.py, chunks them, and inserts
them into the vector database with correct category tags.

These snippets cover three categories that web scraping struggles to fill:
  - face_reading: 面相 (physiognomy)
  - marriage: 合婚 (marriage compatibility)
  - naming: 姓名学 (name analysis)

Usage:
    python scripts/ingest_manual_texts.py                     # full ingest
    python scripts/ingest_manual_texts.py --dry-run            # show what would be ingested
    python scripts/ingest_manual_texts.py --category marriage  # only one category
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from tqdm import tqdm

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import manual text snippets
from data.classical_texts.manual_snippets import CATEGORY_MAP, CATEGORY_LABELS
from src.config import load_settings
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def prepare_chunks(
    category_map: dict[str, list[dict]],
    chunk_size: int = 400,
    overlap: int = 40,
) -> list[dict]:
    """Convert manual text snippets into chunks.

    Returns list of Chunk objects ready for DB insertion, and report metadata.
    """
    all_chunks = []
    report_rows = []

    for cat, snippets in category_map.items():
        for idx, snippet in enumerate(snippets):
            text = snippet.get("text", "")
            source = snippet.get("source", "未知")
            author = snippet.get("author", "")

            if not text or len(text) < 10:
                continue

            # Use the existing chunker
            chunks = chunk_text(
                text=text,
                source=source,
                author=author,
                category=cat,
                chunk_size=chunk_size,
                overlap=overlap,
            )

            all_chunks.extend(chunks)
            report_rows.append({
                "source": source,
                "category": cat,
                "doc_chars": len(text),
                "chunk_count": len(chunks),
            })

    return all_chunks, report_rows


def main():
    parser = argparse.ArgumentParser(
        description="Ingest curated classical text snippets into ChromaDB",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be ingested without writing to DB",
    )
    parser.add_argument(
        "--category",
        choices=list(CATEGORY_MAP.keys()) + ["all"],
        default="all",
        help="Only ingest this category (default: all)",
    )
    parser.add_argument(
        "--collection", default=None,
        help="ChromaDB collection name (default: from settings)",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=400,
        help="Max characters per chunk (default: 400)",
    )
    parser.add_argument(
        "--chunk-overlap", type=int, default=40,
        help="Overlap between chunks (default: 40)",
    )
    args = parser.parse_args()

    # Determine which categories to process
    if args.category == "all":
        categories_to_process = list(CATEGORY_MAP.keys())
    else:
        categories_to_process = [args.category]

    category_map = {k: CATEGORY_MAP[k] for k in categories_to_process}

    settings = load_settings()
    vectordb_dir = settings.vectordb_dir
    collection_name = args.collection or settings.embedding_collection

    print("=" * 60)
    print("  易理明灯 - 古典文本入库 (Classical Text Ingestion)")
    print("=" * 60)
    print(f"Vector DB dir:    {vectordb_dir}")
    print(f"Collection:       {collection_name}")
    print(f"Categories:       {', '.join(CATEGORY_LABELS.get(c, c) for c in categories_to_process)}")
    print(f"Snippets total:   {sum(len(category_map[c]) for c in categories_to_process)}")
    if args.dry_run:
        print("DRY RUN — no data will be written to ChromaDB")
    print()

    # Count snippets per category
    print("Snippets per category:")
    for cat in categories_to_process:
        label = CATEGORY_LABELS.get(cat, cat)
        count = len(category_map[cat])
        print(f"  [{cat:<15}] {label:<35} {count} snippets")

    # Chunk
    print("\nChunking text...")
    all_chunks, report_rows = prepare_chunks(
        category_map,
        chunk_size=args.chunk_size,
        overlap=args.chunk_overlap,
    )
    print(f"  Total chunks: {len(all_chunks)}")

    if args.dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN REPORT")
        print("=" * 60)
        _print_report(report_rows)
        return

    # Insert into ChromaDB
    print("\n" + "─" * 50)
    print("Inserting into ChromaDB...")
    print("─" * 50)

    print("  Loading embedder (BGE-M3)...")
    embedder = Embedder(model_name=settings.embedding_model)
    embedder.load()

    print(f"  Connecting to ChromaDB at {vectordb_dir}...")
    retriever = Retriever(str(vectordb_dir), embedder)
    retriever._collection_name = collection_name

    # Add chunks in batches
    batch_size = 128
    for start in tqdm(
        range(0, len(all_chunks), batch_size),
        desc="Inserting",
        unit="batch",
    ):
        end = min(start + batch_size, len(all_chunks))
        batch = all_chunks[start:end]
        retriever.add_chunks(batch)

    # Verify
    final_count = retriever.count()
    print(f"\n  Collection size after ingest: {final_count}")

    # Report
    print("\n" + "=" * 60)
    print("INGESTION REPORT")
    print("=" * 60)
    _print_report(report_rows)
    print(f"\nTotal chunks in collection: {final_count}")

    # Quick verification search for each weak category
    print(f"\n{'─' * 50}")
    print("Verification searches (RAG retrieval check)")
    print(f"{'─' * 50}")
    test_queries = {
        "face_reading": "面相印堂发暗",
        "marriage": "八字合婚年柱相冲",
        "naming": "五格剖象人格吉凶",
    }
    for cat, query in test_queries.items():
        if cat in categories_to_process:
            results = retriever.search(query, category=cat, top_k=3)
            if results:
                print(
                    f"  [{cat:<15}] '{query}' -> top: [{results[0].score:.3f}] "
                    f"{results[0].source}: {results[0].text[:60]}..."
                )
            else:
                print(f"  [{cat:<15}] '{query}' -> (no results)")


def _print_report(rows: list[dict]):
    """Print ingestion report grouped by category and source."""
    from collections import defaultdict
    by_cat: dict[str, dict] = defaultdict(lambda: {"docs": 0, "chunks": 0, "chars": 0})
    for row in rows:
        cat = row["category"]
        by_cat[cat]["docs"] += 1
        by_cat[cat]["chunks"] += row["chunk_count"]
        by_cat[cat]["chars"] += row["doc_chars"]

    print(f"\n{'Category':<20} {'Docs':>6} {'Chunks':>8} {'Chars':>10}")
    print(f"{'─'*20} {'─'*6} {'─'*8} {'─'*10}")
    for cat in sorted(by_cat.keys()):
        info = by_cat[cat]
        label = CATEGORY_LABELS.get(cat, cat)
        print(f"{label:<20} {info['docs']:>6} {info['chunks']:>8} {info['chars']:>10,}")
    print(f"{'─'*20} {'─'*6} {'─'*8} {'─'*10}")
    total_docs = sum(v["docs"] for v in by_cat.values())
    total_chunks = sum(v["chunks"] for v in by_cat.values())
    total_chars = sum(v["chars"] for v in by_cat.values())
    print(f"{'TOTAL':<20} {total_docs:>6} {total_chunks:>8} {total_chars:>10,}")

    # Per-source detail
    print(f"\nPer-source breakdown:")
    for row in sorted(rows, key=lambda r: (r["category"], r["source"])):
        print(
            f"  [{row['category']:<15}] {row['source'][:50]:<52} "
            f"{row['doc_chars']:>6,} chars → {row['chunk_count']:>3} chunks"
        )


if __name__ == "__main__":
    main()
