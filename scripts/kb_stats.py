#!/usr/bin/env python3
"""Knowledge base statistics — report on ChromaDB health and scraped data.

Reports:
  - Documents per category (from scraped JSON files)
  - Total document size
  - ChromaDB collection stats (doc count, dimension, health)
  - Health validation

Usage:
    python3 scripts/kb_stats.py
    python3 scripts/kb_stats.py --verbose
    python3 scripts/kb_stats.py --collection fortune_books
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_settings
from src.rag.collection_manager import CollectionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────

SCRAPED_DIR = PROJECT_ROOT / "data" / "scraped"
BOOKS_DIR = Path("/mnt/d/fortune-data/books")


def format_bytes(n: int) -> str:
    """Format byte count as human-readable."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f} MB"
    elif n >= 1_000:
        return f"{n / 1_000:.1f} KB"
    return f"{n} B"


# ══════════════════════════════════════════════════════════════════════
#  Scraped file stats
# ══════════════════════════════════════════════════════════════════════


def count_scraped_files(scraped_dir: Path) -> dict:
    """Walk scraped_dir and count docs per category."""
    categories: dict[str, dict] = defaultdict(lambda: {"files": 0, "chars": 0, "bytes": 0})

    for json_path in sorted(scraped_dir.rglob("*.json")):
        # Infer category from directory structure
        rel = json_path.relative_to(scraped_dir)
        category = rel.parts[0] if len(rel.parts) > 0 else "other"

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            content = data.get("content", "")
            categories[category]["files"] += 1
            categories[category]["chars"] += len(content)
            categories[category]["bytes"] += json_path.stat().st_size
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            categories["__corrupt__"]["files"] += 1
            logger.warning("  Corrupt JSON: %s — %s", json_path, e)

    return dict(categories)


# ══════════════════════════════════════════════════════════════════════
#  ChromaDB stats
# ══════════════════════════════════════════════════════════════════════


def get_chromadb_stats(
    vectordb_dir: Path,
    collection_name: str,
    dimension: int,
) -> Optional[dict]:
    """Get ChromaDB collection stats and validation."""
    manager = CollectionManager(
        persist_dir=str(vectordb_dir),
        collection_name=collection_name,
        dimension=dimension,
    )

    # List all collections
    collections = manager.list_collections()

    # Validate the target collection
    report = manager.validate()

    # Get per-category counts from metadata
    category_counts: dict[str, int] = defaultdict(int)
    try:
        col = manager.client.get_collection(collection_name)
        all_meta = col.get(include=["metadatas"])
        if all_meta and all_meta["metadatas"]:
            for meta in all_meta["metadatas"]:
                cat = meta.get("category", "unknown") if meta else "unknown"
                category_counts[cat] += 1
        else:
            category_counts["(no metadata)"] = 0
    except Exception as e:
        logger.warning("  Cannot read category metadata: %s", e)

    return {
        "collection_name": collection_name,
        "doc_count": report.doc_count if report.exists else 0,
        "exists": report.exists,
        "valid": report.valid,
        "errors": report.errors,
        "all_collections": collections,
        "category_doc_counts": dict(category_counts),
    }


# ══════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Knowledge base statistics and ChromaDB health report",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed per-file information",
    )
    parser.add_argument(
        "--collection", default=None,
        help="ChromaDB collection name (default: from settings)",
    )
    args = parser.parse_args()

    settings = load_settings()
    collection_name = args.collection or settings.embedding_collection

    print("=" * 60)
    print("  易理明灯 - 知识库统计 (Knowledge Base Statistics)")
    print("=" * 60)
    print(f"Settings file:   config/settings.yaml")
    print(f"Vector DB dir:   {settings.vectordb_dir}")
    print(f"Collection:      {collection_name}")
    print(f"Embedding model: {settings.embedding_model}")
    print()

    # ── Part 1: Scraped file stats ────────────────────────────────
    print("─" * 50)
    print("Part 1: Scraped Data Files")
    print("─" * 50)

    if SCRAPED_DIR.exists():
        scraped_stats = count_scraped_files(SCRAPED_DIR)
        if scraped_stats:
            print(f"\n{'Category':<20} {'Files':>6} {'Chars':>12} {'Size':>12}")
            print(f"{'─'*20} {'─'*6} {'─'*12} {'─'*12}")
            total_files = 0
            total_chars = 0
            total_bytes = 0
            for cat in sorted(scraped_stats.keys()):
                info = scraped_stats[cat]
                label = cat if cat != "__corrupt__" else "** CORRUPT **"
                print(f"{label:<20} {info['files']:>6} {info['chars']:>12,} {format_bytes(info['bytes']):>12}")
                total_files += info["files"]
                total_chars += info["chars"]
                total_bytes += info["bytes"]
            print(f"{'─'*20} {'─'*6} {'─'*12} {'─'*12}")
            print(f"{'TOTAL':<20} {total_files:>6} {total_chars:>12,} {format_bytes(total_bytes):>12}")

            verbose_count = 0
            if args.verbose:
                print(f"\n  Files per category:")
                for cat in sorted(scraped_stats.keys()):
                    if cat == "__corrupt__":
                        continue
                    cat_dir = SCRAPED_DIR / cat
                    if cat_dir.exists():
                        files = sorted(cat_dir.glob("*.json"))
                        for f in files:
                            try:
                                data = json.loads(f.read_text(encoding="utf-8"))
                                chars = len(data.get("content", ""))
                                title = data.get("title", f.stem)
                                print(f"    [{cat}] {title:<50} {chars:>8,} chars")
                                verbose_count += 1
                            except Exception:
                                print(f"    [{cat}] {f.name:<50} {'(corrupt)':>8}")
                                verbose_count += 1
                if verbose_count > 50 and not args.verbose:
                    print(f"\n  ({total_files} files total; use --verbose for per-file listing)")
        else:
            print("  (no data files found)")
    else:
        print("  (data/scraped/ does not exist)")
        print("  Run scripts/scrape_culture.py first to populate it.")

    # ── Part 2: Books directory stats ─────────────────────────────
    print(f"\n{'─'*50}")
    print("Part 2: Books Directory")
    print(f"{'─'*50}")
    if BOOKS_DIR.exists():
        txt_files = sorted(BOOKS_DIR.rglob("*.txt"))
        total_book_chars = 0
        total_book_bytes = 0
        book_categories: dict[str, int] = defaultdict(int)
        for f in txt_files:
            try:
                rel = f.relative_to(BOOKS_DIR)
                cat = rel.parts[0] if len(rel.parts) > 0 else "root"
                book_categories[cat] += 1
                total_book_bytes += f.stat().st_size
                total_book_chars += len(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
        print(f"  Total .txt files: {len(txt_files)}")
        print(f"  Total characters: {total_book_chars:,}")
        print(f"  Total size:       {format_bytes(total_book_bytes)}")
        if book_categories:
            print(f"\n  By category:")
            for cat in sorted(book_categories.keys()):
                print(f"    {cat:<20} {book_categories[cat]} files")
    else:
        print(f"  (books directory does not exist: {BOOKS_DIR})")

    # ── Part 3: ChromaDB stats ────────────────────────────────────
    print(f"\n{'─'*50}")
    print("Part 3: ChromaDB Vector Store")
    print(f"{'─'*50}")

    if settings.vectordb_dir.exists():
        db_stats = get_chromadb_stats(
            settings.vectordb_dir,
            collection_name,
            settings.embedding_dimension,
        )

        if db_stats:
            print(f"\n  Collection:             {db_stats['collection_name']}")
            print(f"  Exists:                 {'YES' if db_stats['exists'] else 'NO'}")
            print(f"  Document count:         {db_stats['doc_count']:,}")
            print(f"  Health:                 {'OK' if db_stats['valid'] else 'ISSUES'}")

            if db_stats["errors"]:
                print(f"\n  Issues found:")
                for e in db_stats["errors"]:
                    print(f"    - {e}")

            if db_stats["all_collections"]:
                print(f"\n  All collections:")
                for c in db_stats["all_collections"]:
                    print(f"    - {c['name']:<40} {c['count']:>8,} docs")

            if db_stats["category_doc_counts"]:
                print(f"\n  Docs by category (from metadata):")
                for cat in sorted(db_stats["category_doc_counts"].keys()):
                    count = db_stats["category_doc_counts"][cat]
                    print(f"    {cat:<20} {count:>8,} docs")
        else:
            print("  (could not read ChromaDB stats)")
    else:
        print(f"  (vectordb directory does not exist: {settings.vectordb_dir})")

    # ── Summary ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    scraped_total = sum(v["files"] for v in count_scraped_files(SCRAPED_DIR).values() if v["files"] > 0) if SCRAPED_DIR.exists() else 0
    chroma_count = db_stats["doc_count"] if db_stats and db_stats["exists"] else 0
    print(f"  Scraped JSON files: {scraped_total}")
    print(f"  ChromaDB documents: {chroma_count:,}")
    if db_stats:
        print(f"  ChromaDB health:    {'PASS' if db_stats['valid'] else 'FAIL'}")
    print()


if __name__ == "__main__":
    main()
