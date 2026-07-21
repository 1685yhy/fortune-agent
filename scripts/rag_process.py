"""Process downloaded texts into RAG, one category at a time, with progress tracking."""
from __future__ import annotations
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import load_settings
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

BOOKS_DIR = Path("/mnt/d/fortune-data/books")

# Categories to process (bazi already done, ziwei already done)
CATEGORIES = ["yijing", "fengshui", "mianxiang", "qimen", "zeri", "zonghe"]


def get_existing_sources(retriever: Retriever) -> set[str]:
    """Get sources already in the vector DB using an existing retriever."""
    try:
        all_meta = retriever.collection.get(include=["metadatas"])
        sources = set()
        for m in all_meta["metadatas"]:
            sources.add(m.get("source", ""))
        return sources
    except Exception:
        return set()


def process_category(category: str, retriever: Retriever, existing_sources: set[str],
                     chunk_size: int = 800, chunk_overlap: int = 80) -> dict[str, Any]:
    """Process all texts in a category that aren't already in the DB."""

    cat_dir = BOOKS_DIR / category
    if not cat_dir.exists():
        return {"category": category, "status": "no_dir", "new_chunks": 0}

    txt_files = sorted(cat_dir.glob("*.txt"))
    total_new = 0
    text_count = 0

    for txt_path in txt_files:
        source = txt_path.stem
        if source in existing_sources:
            logger.info("[%s] Already in DB: %s", category, source)
            continue

        text = txt_path.read_text(encoding="utf-8").strip()
        if len(text) < 100:
            logger.info("[%s] Too short, skipping: %s (%d chars)", category, source, len(text))
            continue

        logger.info("[%s] Processing: %s (%d chars)...", category, source, len(text))
        chunks = chunk_text(
            text, source=source, author="", category=category,
            chunk_size=chunk_size, overlap=chunk_overlap,
        )

        if chunks:
            try:
                t0 = time.time()
                retriever.add_chunks(chunks)
                elapsed = time.time() - t0
                total_new += len(chunks)
                text_count += 1
                existing_sources.add(source)
                logger.info(
                    "[%s] Added %d chunks from %s (%.1fs)",
                    category, len(chunks), source, elapsed,
                )
            except Exception as e:
                logger.error("[%s] Failed adding chunks from %s: %s", category, source, e)
        else:
            logger.warning("[%s] No chunks from %s", category, source)

    return {
        "category": category,
        "total_texts": len(txt_files),
        "processed": text_count,
        "new_chunks": total_new,
    }


def main():
    """Process each category sequentially."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--categories", nargs="+", default=None, help="Categories to process")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--chunk-overlap", type=int, default=80)
    args = parser.parse_args()

    categories = args.categories or CATEGORIES
    all_stats = []

    print(f"Chunk size: {args.chunk_size}, Overlap: {args.chunk_overlap}")
    print(f"Categories: {', '.join(categories)}")
    print()

    settings = load_settings()
    embedder = Embedder(model_name=settings.embedding_model)
    retriever = Retriever(str(settings.vectordb_dir), embedder)
    existing_sources = get_existing_sources(retriever)
    print(f"Existing sources in DB: {len(existing_sources)}")

    for cat in categories:
        print(f"{'='*60}")
        print(f"Processing category: {cat}")
        print(f"{'='*60}")
        stats = process_category(cat, retriever, existing_sources, args.chunk_size, args.chunk_overlap)
        all_stats.append(stats)
        print(f"[{cat}] Done: {stats['new_chunks']} new chunks from {stats.get('processed', 0)} texts")
        print()

    # Summary
    total_new = sum(s["new_chunks"] for s in all_stats)
    print(f"{'='*60}")
    print("Final Summary")
    print(f"{'='*60}")
    for s in all_stats:
        print(f"  [{s['category']}]: {s['new_chunks']} new chunks")
    print(f"\n  Total new chunks: {total_new}")

    # Final DB count
    try:
        print(f"  Final collection size: {retriever.count()}")
    except Exception as e:
        print(f"  Could not get final count: {e}")


if __name__ == "__main__":
    main()
