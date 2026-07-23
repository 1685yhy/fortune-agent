#!/usr/bin/env python3
"""Data pipeline — ingest scraped culture data into ChromaDB.

Reads all JSON files from data/scraped/ recursively, then:
  1. Clean text (remove HTML, normalize whitespace, deduplicate)
  2. Chunk into BGE-M3-friendly segments (~512 tokens each)
  3. Insert into ChromaDB with category tags

Usage:
    python3 scripts/ingest_new_data.py                          # full ingest
    python3 scripts/ingest_new_data.py --dry-run                 # show what would be ingested
    python3 scripts/ingest_new_data.py --category tcm            # only one category
    python3 scripts/ingest_new_data.py --collection custom_name  # use different collection
"""

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

from tqdm import tqdm

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

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

# ── Paths ─────────────────────────────────────────────────────────────

SCRAPED_DIR = PROJECT_ROOT / "data" / "scraped"

# ── Text cleaning ─────────────────────────────────────────────────────

# HTML tag pattern
HTML_TAG_RE = re.compile(r"<[^>]+>")
# Multiple whitespace (including newlines)
MULTI_WS_RE = re.compile(r"[ \t]+")
MULTI_NL_RE = re.compile(r"\n{3,}")
# Non-Chinese/non-printable noise (but keep CJK, basic Latin, punctuation)
NOISE_RE = re.compile(r"[^一-鿿㐀-䶿豈-﫿"
                       r"　-〿＀-￯"
                       r" -⁯"
                       r" -~"
                       r" ·"
                       r"\n]")
# URL patterns to scrub
URL_RE = re.compile(r"https?://\S+")


def clean_text(text: str) -> str:
    """Clean extracted text: remove HTML, normalize whitespace, strip noise."""
    # Remove HTML tags
    text = HTML_TAG_RE.sub("", text)
    # Remove URLs
    text = URL_RE.sub("", text)
    # Normalize newlines: collapse 3+ to 2
    text = MULTI_NL_RE.sub("\n\n", text)
    # Normalize horizontal whitespace (keep newlines)
    text = MULTI_WS_RE.sub(" ", text)
    # Remove control characters but keep CJK, basic Latin, common punctuation
    text = NOISE_RE.sub("", text)
    # Strip leading/trailing whitespace per line
    lines = [l.strip() for l in text.split("\n")]
    text = "\n".join(l for l in lines if l)
    return text.strip()


def compute_content_hash(text: str) -> str:
    """Compute a hash for deduplication."""
    return hashlib.md5(text[:200].encode("utf-8")).hexdigest()


# ── File discovery ────────────────────────────────────────────────────


def discover_json_files(scraped_dir: Path) -> list[Path]:
    """Recursively find all JSON files under scraped_dir."""
    return sorted(scraped_dir.rglob("*.json"))


def load_records(json_files: list[Path]) -> list[dict]:
    """Load JSON records, returning list of {title, category, content, url, ...}."""
    records = []
    for fpath in json_files:
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            # Normalise record — allow missing fields
            records.append({
                "title": data.get("title", fpath.stem),
                "category": data.get("category", "general"),
                "content": data.get("content", ""),
                "url": data.get("url", ""),
                "source_type": data.get("source_type", "unknown"),
                "file": str(fpath),
            })
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("  Skipping %s: %s", fpath.name, e)
    return records


# ── Deduplication ─────────────────────────────────────────────────────


def deduplicate_records(records: list[dict]) -> list[dict]:
    """Remove duplicate records based on content prefix hash."""
    seen: set[str] = set()
    unique: list[dict] = []
    skipped = 0
    for rec in records:
        h = compute_content_hash(rec.get("content", ""))
        if h not in seen:
            seen.add(h)
            unique.append(rec)
        else:
            skipped += 1
    if skipped:
        logger.info("  Deduplication: skipped %d duplicate records", skipped)
    return unique


# ── Chunking ──────────────────────────────────────────────────────────


def chunk_records(
    records: list[dict],
    chunk_size: int = 400,   # ~512 tokens for Chinese text
    overlap: int = 40,
) -> list[dict]:
    """Chunk all records and return metadata for reporting.

    Returns list of {source, category, chunk_count, chars} for each record.
    """
    all_chunks = []
    report_rows = []

    for rec in tqdm(records, desc="Chunking", unit="doc"):
        content = rec.get("content", "")
        if not content or len(content) < 20:
            continue

        chunks = chunk_text(
            text=content,
            source=rec.get("title", "unknown"),
            author=rec.get("source_type", ""),
            category=rec.get("category", "general"),
            chunk_size=chunk_size,
            overlap=overlap,
        )

        all_chunks.extend(chunks)
        report_rows.append({
            "source": rec.get("title", "unknown"),
            "category": rec.get("category", "general"),
            "doc_chars": len(content),
            "chunk_count": len(chunks),
        })

    return all_chunks, report_rows


# ══════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Ingest scraped culture data into ChromaDB",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be ingested without writing to DB",
    )
    parser.add_argument(
        "--category", default=None,
        help="Only ingest records from this category subdirectory",
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
    parser.add_argument(
        "--data-dir", default=None,
        help="Override scraped data directory",
    )
    args = parser.parse_args()

    # Resolve paths
    scraped_dir = Path(args.data_dir) if args.data_dir else SCRAPED_DIR
    if not scraped_dir.exists():
        logger.error("Scraped data directory does not exist: %s", scraped_dir)
        logger.error("Run scripts/scrape_culture.py first.")
        sys.exit(1)

    settings = load_settings()
    vectordb_dir = settings.vectordb_dir
    collection_name = args.collection or settings.embedding_collection

    print("=" * 60)
    print("  易理明灯 - 数据入库管线 (Data Ingestion Pipeline)")
    print("=" * 60)
    print(f"Scraped data dir: {scraped_dir}")
    print(f"Vector DB dir:    {vectordb_dir}")
    print(f"Collection:       {collection_name}")
    if args.dry_run:
        print("DRY RUN — no data will be written to ChromaDB")
    print()

    # Step 1: Discover files
    print("Step 1/5: Discovering JSON files...")
    json_files = discover_json_files(scraped_dir)
    if args.category:
        json_files = [f for f in json_files if args.category in f.parts]
    print(f"  Found {len(json_files)} JSON files")

    if not json_files:
        logger.warning("No JSON files found. Nothing to ingest.")
        return

    # Step 2: Load records
    print("\nStep 2/5: Loading records...")
    records = load_records(json_files)
    print(f"  Loaded {len(records)} records")

    # Step 3: Clean text
    print("\nStep 3/5: Cleaning text...")
    for rec in tqdm(records, desc="Cleaning", unit="doc"):
        rec["content"] = clean_text(rec.get("content", ""))
    total_chars = sum(len(rec.get("content", "")) for rec in records)
    print(f"  Total content: {total_chars:,} characters")

    # Step 4: Deduplicate
    print("\nStep 4/5: Deduplicating...")
    records = deduplicate_records(records)
    print(f"  Unique records: {len(records)}")

    # Step 5: Chunk
    print("\nStep 5/5: Chunking text...")
    all_chunks, report_rows = chunk_records(
        records,
        chunk_size=args.chunk_size,
        overlap=args.chunk_overlap,
    )
    print(f"  Total chunks: {len(all_chunks)}")

    if args.dry_run:
        # Report without writing
        print("\n" + "=" * 60)
        print("DRY RUN REPORT")
        print("=" * 60)
        _print_report(report_rows)
        return

    # ── Insert into ChromaDB ──────────────────────────────────────
    print("\n" + "─" * 50)
    print("Inserting into ChromaDB...")
    print("─" * 50)

    print("  Loading embedder (BGE-M3)...")
    embedder = Embedder(model_name=settings.embedding_model)
    if not embedder.load():
        # Some embedders auto-load on first encode; try anyway
        pass

    print(f"  Connecting to ChromaDB at {vectordb_dir}...")
    retriever = Retriever(str(vectordb_dir), embedder)
    retriever._collection_name = collection_name

    # Add chunks in batches
    from src.rag.chunker import Chunk
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


def _print_report(rows: list[dict]):
    """Print ingestion report grouped by category and source."""

    # Summarise by category
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
        print(f"{cat:<20} {info['docs']:>6} {info['chunks']:>8} {info['chars']:>10,}")
    print(f"{'─'*20} {'─'*6} {'─'*8} {'─'*10}")
    total_docs = sum(v["docs"] for v in by_cat.values())
    total_chunks = sum(v["chunks"] for v in by_cat.values())
    total_chars = sum(v["chars"] for v in by_cat.values())
    print(f"{'TOTAL':<20} {total_docs:>6} {total_chunks:>8} {total_chars:>10,}")

    # Per-source detail
    print(f"\nPer-source breakdown:")
    for row in sorted(rows, key=lambda r: (r["category"], r["source"])):
        print(
            f"  [{row['category']:<10}] {row['source'][:50]:<52} "
            f"{row['doc_chars']:>6,} chars → {row['chunk_count']:>3} chunks"
        )


if __name__ == "__main__":
    main()
