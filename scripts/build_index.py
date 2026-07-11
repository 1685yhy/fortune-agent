"""构建向量索引 - 从 /mnt/d/fortune-data/books/ 读取 .txt 文件构建 ChromaDB 索引."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import load_settings
from src.rag.chunker import Chunk, chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

logger = logging.getLogger(__name__)

# Default books directory
DEFAULT_BOOKS_DIR = Path("/mnt/d/fortune-data/books")


def _detect_category(file_path: Path, books_dir: Path) -> str:
    """Auto-detect category from the directory name relative to books_dir.

    Returns 'general' if the parent directory name doesn't match any known
    category.
    """
    known_categories = {
        "bazi", "ziwei", "fengshui", "yijing", "mianxiang",
        "zeri", "qimen", "xingming", "zonghe",
    }
    try:
        rel = file_path.relative_to(books_dir)
        parent_name = rel.parts[0] if len(rel.parts) > 0 else ""
        if parent_name in known_categories:
            return parent_name
    except ValueError:
        pass
    return "general"


def load_texts_from_directory(
    books_dir: str | Path,
) -> list[dict[str, str]]:
    """Walk books_dir, read all .txt files, return list of {text, source, author, category}.

    Category is auto-detected from the subdirectory name.
    Source is the file stem (filename without extension).
    Author is left empty -- metadata can be added later.
    """
    books_dir = Path(books_dir)
    if not books_dir.exists():
        logger.error("Books directory does not exist: %s", books_dir)
        return []

    txt_files = sorted(books_dir.rglob("*.txt"))
    if not txt_files:
        logger.warning("No .txt files found under %s", books_dir)
        return []

    entries: list[dict[str, str]] = []
    for txt_path in txt_files:
        category = _detect_category(txt_path, books_dir)
        source = txt_path.stem
        try:
            text = txt_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("Failed to read %s: %s", txt_path, e)
            continue

        if not text:
            logger.debug("Skipping empty file: %s", txt_path)
            continue

        entries.append({
            "text": text,
            "source": source,
            "author": "",  # can be enriched later
            "category": category,
        })

    logger.info(
        "Loaded %d text entries from %d files in %s",
        len(entries), len(txt_files), books_dir,
    )
    return entries


def main(
    books_dir: Optional[str] = None,
    vectordb_dir: Optional[str] = None,
    embedding_model: Optional[str] = None,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> None:
    """Build vector index from .txt files in books_dir.

    Args:
        books_dir: Path to books directory (default: /mnt/d/fortune-data/books)
        vectordb_dir: Path to vectordb (default: from settings)
        embedding_model: Embedding model name (default: from settings)
        chunk_size: Max chunk size in characters
        chunk_overlap: Overlap between chunks
    """
    settings = load_settings()
    settings.vectordb_dir.mkdir(parents=True, exist_ok=True)

    # Resolve paths
    books_path = Path(books_dir) if books_dir else DEFAULT_BOOKS_DIR
    vdb_path = Path(vectordb_dir) if vectordb_dir else settings.vectordb_dir
    model_name = embedding_model or settings.embedding_model

    print(f"Books directory: {books_path}")
    print(f"Vector DB directory: {vdb_path}")
    print(f"Embedding model: {model_name}")
    print()

    # Load texts from directory
    entries = load_texts_from_directory(books_path)
    if not entries:
        print("No texts found. Run scripts/ocr_books.py first to extract text from PDFs.")
        return

    # Load embedder
    print("Loading embedder (first run will download BGE model ~1.3GB)...")
    embedder = Embedder(model_name=model_name)

    print("Building vector index...")
    retriever = Retriever(str(vdb_path), embedder)

    total_chunks = 0
    for entry in tqdm(entries, desc="Processing texts", unit="text"):
        chunks = chunk_text(
            entry["text"],
            source=entry["source"],
            author=entry["author"],
            category=entry["category"],
            chunk_size=chunk_size,
            overlap=chunk_overlap,
        )
        if chunks:
            retriever.add_chunks(chunks)
            total_chunks += len(chunks)
            logger.debug(
                "Added %d chunks from '%s' (category: %s)",
                len(chunks), entry["source"], entry["category"],
            )

    print(f"\nDone! Total chunks: {total_chunks}, Collection size: {retriever.count()}")

    # Test search for each category
    test_queries = {
        "bazi": "乙木日主财运",
        "ziwei": "紫微星在命宫",
        "fengshui": "风水布局",
        "yijing": "周易八卦",
    }
    print()
    for cat, query in test_queries.items():
        results = retriever.search(query, category=cat, top_k=3)
        if results:
            print(f"  [{cat}] '{query}' -> top: [{results[0].score:.3f}] {results[0].source}: {results[0].text[:60]}...")
        else:
            print(f"  [{cat}] '{query}' -> (no results)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Build vector index from OCR'd book texts.",
    )
    parser.add_argument(
        "--books-dir",
        default=None,
        help="Path to books directory (default: /mnt/d/fortune-data/books)",
    )
    parser.add_argument(
        "--vectordb-dir",
        default=None,
        help="Path to vectordb directory (default: from settings)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Max chunk size in characters (default: 500)",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=50,
        help="Chunk overlap in characters (default: 50)",
    )
    args = parser.parse_args()
    main(
        books_dir=args.books_dir,
        vectordb_dir=args.vectordb_dir,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
