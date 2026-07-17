#!/usr/bin/env python3
"""
Chunk and index 12880_dreams.txt and zgjmorg_dreams.txt to ChromaDB.
"""
import sys
import re
sys.path.insert(0, '/home/a/fortune-agent')
from pathlib import Path
from src.rag.chunker import chunk_text, Chunk
from src.rag.retriever import Retriever
from src.rag.embedder import Embedder
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = Path('/mnt/d/fortune-data/books/zonghe')
VECTORDB_DIR = '/mnt/d/fortune-data/vectordb'
BATCH_SIZE = 128

def parse_file(filepath: Path) -> list:
    """Parse entries from a dream text file (separated by double newlines)."""
    text = filepath.read_text(encoding='utf-8')
    # Split on double newlines (the separator between entries)
    entries = re.split(r'\n\n+', text)
    entries = [e.strip() for e in entries if e.strip() and e.strip().startswith('梦见')]
    logger.info(f"{filepath.name}: {len(entries)} total entries")
    return entries

def index_file(filepath: Path, source: str, retriever: Retriever):
    """Chunk and index a single file."""
    entries = parse_file(filepath)
    if not entries:
        return 0

    all_chunks = []
    for entry in entries:
        chunks = chunk_text(
            text=entry,
            source=source,
            author="周公解梦",
            category="dream",
            chunk_size=500,
            overlap=50,
        )
        all_chunks.extend(chunks)

    # Dedup by chunk_id
    seen_ids = set()
    unique_chunks = []
    for c in all_chunks:
        if c.chunk_id not in seen_ids:
            seen_ids.add(c.chunk_id)
            unique_chunks.append(c)

    logger.info(f"  {source}: {len(entries)} entries -> {len(unique_chunks)} chunks")
    retriever.add_chunks(unique_chunks, batch_size=BATCH_SIZE)
    return len(unique_chunks)

def main():
    logger.info("=" * 60)
    logger.info("Indexing dream data to ChromaDB")
    logger.info("=" * 60)

    embedder = Embedder()
    retriever = Retriever(VECTORDB_DIR, embedder)

    count_before = retriever.count()
    logger.info(f"ChromaDB count before: {count_before}")

    total_chunks = 0

    for fname, source in [
        ('12880_dreams.txt', '12880_dreams.txt'),
        ('zgjmorg_dreams.txt', 'zgjmorg_dreams.txt'),
    ]:
        fp = DATA_DIR / fname
        if fp.exists():
            logger.info(f"\nIndexing {fname}...")
            n = index_file(fp, source, retriever)
            total_chunks += n
        else:
            logger.warning(f"{fname} not found")

    count_after = retriever.count()
    logger.info(f"\n{'='*60}")
    logger.info(f"INDEXING COMPLETE")
    logger.info(f"Total chunks added: {total_chunks}")
    logger.info(f"ChromaDB before: {count_before}")
    logger.info(f"ChromaDB after:  {count_after}")
    logger.info(f"Net new docs:    {count_after - count_before}")

if __name__ == '__main__':
    main()
