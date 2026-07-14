"""Batch-process all classical texts into RAG vector DB in one efficient pass.

Strategy:
1. Chunk ALL texts first (fast, CPU-light)
2. Collect ALL chunks into one list
3. Embed ALL chunks in one giant batch (optimal GPU/CPU utilization)
4. Add ALL chunks to ChromaDB in one operation
"""
from __future__ import annotations
import json
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
CHUNK_CACHE = Path("/mnt/d/fortune-data/all_chunks.json")
CHUNK_SIZE = 800
CHUNK_OVERLAP = 80


def chunk_all_texts() -> list[dict[str, Any]]:
    """Step 1: Chunk all texts from all categories into a flat list."""
    chunks: list[dict[str, Any]] = []

    # Process book .txt files
    for cat_dir in sorted(BOOKS_DIR.iterdir()):
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        txt_files = sorted(cat_dir.glob("*.txt"))

        for txt_path in txt_files:
            source = txt_path.stem
            text = txt_path.read_text(encoding="utf-8").strip()
            if len(text) < 100:
                logger.warning("Skipping too-short: [%s] %s (%d chars)", category, source, len(text))
                continue

            text_chunks = chunk_text(
                text, source=source, author="", category=category,
                chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP,
            )
            for c in text_chunks:
                chunks.append({
                    "text": c.text,
                    "source": c.source,
                    "author": c.author,
                    "category": c.category,
                    "chunk_id": c.chunk_id,
                    "keywords": c.keywords,
                })

            logger.info("Chunked: [%s] %s -> %d chunks", category, source, len(text_chunks))

    # Also include seed knowledge
    try:
        from scripts.seed_knowledge import EXCERPTS as SEED
        for category, excerpts in SEED.items():
            for excerpt in excerpts:
                seed_chunks = chunk_text(
                    excerpt["text"],
                    source=excerpt.get("source", "未知"),
                    author=excerpt.get("author", ""),
                    category=category,
                    chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP,
                )
                for c in seed_chunks:
                    chunks.append({
                        "text": c.text,
                        "source": c.source,
                        "author": c.author,
                        "category": c.category,
                        "chunk_id": c.chunk_id,
                        "keywords": c.keywords,
                    })
        logger.info("Added %d seed excerpts", len(SEED))
    except ImportError:
        logger.info("No seed knowledge found")

    logger.info("Total chunks created: %d", len(chunks))
    return chunks


def save_chunks(chunks: list[dict[str, Any]]):
    """Save chunks to JSON cache for recovery."""
    with open(CHUNK_CACHE, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False)
    logger.info("Saved %d chunks to %s", len(chunks), CHUNK_CACHE)


def load_chunks() -> list[dict[str, Any]] | None:
    """Load chunks from JSON cache."""
    if CHUNK_CACHE.exists():
        with open(CHUNK_CACHE, encoding="utf-8") as f:
            chunks = json.load(f)
        logger.info("Loaded %d chunks from cache", len(chunks))
        return chunks
    return None


def embed_and_store(chunks: list[dict[str, Any]], batch_embed_size: int = 500):
    """Step 2+3: Embed all chunks and add to ChromaDB in small batches.

    Args:
        chunks: List of chunk dicts with text, chunk_id, source, etc.
        batch_embed_size: Number of chunks to embed per batch (to avoid hangs).
    """
    settings = load_settings()
    settings.vectordb_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading embedder...")
    embedder = Embedder(model_name=settings.embedding_model)

    logger.info("Connecting to vector DB...")
    retriever = Retriever(str(settings.vectordb_dir), embedder)

    # Check for existing IDs
    try:
        existing_ids = set(retriever.collection.get()["ids"])
        logger.info("Existing chunks in DB: %d", len(existing_ids))
    except Exception:
        existing_ids = set()
        logger.info("No existing chunks, starting fresh")

    # Filter out existing chunks
    new_chunks = [c for c in chunks if c["chunk_id"] not in existing_ids]
    if not new_chunks:
        logger.info("All chunks already in DB. Nothing to add.")
        return

    logger.info("New chunks to process: %d (out of %d total)", len(new_chunks), len(chunks))

    # Process in smaller batches: embed then immediately upsert
    t_start = time.time()
    total_embedded = 0
    total_added = 0

    for batch_start in range(0, len(new_chunks), batch_embed_size):
        batch_end = min(batch_start + batch_embed_size, len(new_chunks))
        batch = new_chunks[batch_start:batch_end]

        batch_texts = [c["text"] for c in batch]
        batch_ids = [c["chunk_id"] for c in batch]

        # Embed this batch
        t0 = time.time()
        embeddings = embedder.encode(batch_texts)
        embed_elapsed = time.time() - t0
        total_embedded += len(batch_texts)

        # Prepare metadata
        batch_metas = []
        for c in batch:
            batch_metas.append({
                "source": c["source"],
                "author": c.get("author", ""),
                "category": c["category"],
            })

        # Upsert to ChromaDB
        t1 = time.time()
        retriever.collection.upsert(
            embeddings=embeddings.tolist(),
            documents=batch_texts,
            ids=batch_ids,
            metadatas=batch_metas,
        )
        add_elapsed = time.time() - t1
        total_added += len(batch_texts)

        progress = (batch_end / len(new_chunks)) * 100
        elapsed_total = time.time() - t_start
        logger.info(
            "Batch %d-%d/%d: embedded=%d (%.2fs), added=%d (%.2fs) | "
            "progress=%.1f%% | elapsed=%.1fs",
            batch_start, batch_end, len(new_chunks),
            len(batch_texts), embed_elapsed,
            len(batch_texts), add_elapsed,
            progress, elapsed_total,
        )

    total_elapsed = time.time() - t_start
    logger.info("Total: %d chunks in %.1fs (%.2f ms/chunk)",
                total_added, total_elapsed, total_elapsed / total_added * 1000 if total_added else 0)

    # Final count
    final_count = retriever.count()
    logger.info("Final collection size: %d", final_count)

    # Cleanup
    if CHUNK_CACHE.exists():
        CHUNK_CACHE.unlink()
        logger.info("Deleted chunk cache")

    return final_count


def main():
    """Main entry point."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk-only", action="store_true", help="Only chunk texts, don't embed")
    parser.add_argument("--embed-only", action="store_true", help="Only embed cached chunks")
    parser.add_argument("--skip-chunk-cache", action="store_true", help="Don't use cached chunks")
    args = parser.parse_args()

    if args.embed_only:
        # Load cached chunks and embed
        chunks = load_chunks()
        if not chunks:
            logger.error("No cached chunks found. Run without --embed-only first.")
            sys.exit(1)
    elif args.chunk_only:
        # Only chunk
        chunks = chunk_all_texts()
        save_chunks(chunks)
        logger.info("Done chunking. Saved %d chunks.", len(chunks))
        return
    else:
        # Full pipeline
        if not args.skip_chunk_cache:
            chunks = load_chunks()
        if not chunks:
            chunks = chunk_all_texts()
            save_chunks(chunks)

    # Embed and store
    embed_and_store(chunks)

    # Test searches
    logger.info("\nTest searches:")
    settings = load_settings()
    from src.config import load_settings as _ls
    settings = _ls()
    embedder = Embedder(model_name=settings.embedding_model)
    retriever = Retriever(str(settings.vectordb_dir), embedder)

    test_queries = {
        "bazi": "乙木日主财运",
        "yijing": "梅花易数起卦方法",
        "fengshui": "风水龙脉砂水",
        "mianxiang": "面相气色吉凶",
        "zeri": "协纪辨方书择日",
        "qimen": "奇门遁甲八门",
        "ziwei": "紫微星在命宫",
    }
    for cat, query in test_queries.items():
        results = retriever.search(query, category=cat, top_k=3)
        if results:
            print(
                f"  [{cat}] '{query}' -> top: [{results[0].score:.3f}] "
                f"{results[0].source}: {results[0].text[:60]}..."
            )
        else:
            print(f"  [{cat}] '{query}' -> (no results)")

    logger.info("Done!")


if __name__ == "__main__":
    main()
