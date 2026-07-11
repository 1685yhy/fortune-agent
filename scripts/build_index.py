"""构建向量索引 - 从多个来源加载古籍文本构建 ChromaDB 索引.

支持的数据来源（按优先级）:
1. /mnt/d/fortune-data/books/ 下的 .txt 文件（经过 OCR 的文本）
2. ctext.org 上的纯文本格式古籍
3. 内嵌的经典段落（seed_knowledge 回退方案）
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import load_settings
from src.rag.chunker import Chunk, chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

try:
    from scripts.seed_knowledge import EXCERPTS as SEED_EXCERPTS
    HAS_SEED = True
except ImportError:
    SEED_EXCERPTS = {}
    HAS_SEED = False

logger = logging.getLogger(__name__)

# Default books directory
DEFAULT_BOOKS_DIR = Path("/mnt/d/fortune-data/books")

# ── ctext.org 纯文本古籍 ────────────────────────────────────────────

CTEXT_TEXTS: dict[str, list[dict[str, str]]] = {
    "yijing": [
        {
            "url": "https://ctext.org/zhouyi.zh.txt",
            "title": "周易（ctext.org 文本版）",
            "author": "佚名",
        },
    ],
    "zonghe": [
        {
            "url": "https://ctext.org/taixuanjing.zh.txt",
            "title": "太玄经（ctext.org 文本版）",
            "author": "杨雄",
        },
    ],
}


def download_ctext_text(url: str, timeout: int = 30) -> str | None:
    """Download plain text from ctext.org.

    ctext.org hosts classical Chinese texts in UTF-8 plain text format
    at URLs ending in .zh.txt. This function fetches them and returns
    the text content, or None on failure.
    """
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; FortuneAgent/1.0; "
                    "+https://github.com/fortune-agent)"
                ),
            },
        )
        resp.raise_for_status()
        # ctext.org returns GBK-encoded text in some cases; detect encoding
        if resp.encoding and resp.encoding.lower() != "utf-8":
            try:
                text = resp.content.decode("utf-8")
            except UnicodeDecodeError:
                text = resp.content.decode("gbk", errors="replace")
        else:
            text = resp.text
        text = text.strip()
        return text if len(text) > 50 else None
    except Exception as e:
        logger.warning("Failed to fetch ctext URL %s: %s", url, e)
        return None


def load_ctext_texts() -> list[dict[str, str]]:
    """Download all configured ctext.org texts.

    Returns list of {text, source, author, category} dicts.
    """
    entries: list[dict[str, str]] = []
    for category, books in CTEXT_TEXTS.items():
        for book in books:
            print(f"  Fetching ctext: {book['title']}")
            text = download_ctext_text(book["url"])
            if text:
                entries.append({
                    "text": text,
                    "source": book["title"],
                    "author": book["author"],
                    "category": category,
                })
                print(f"    -> {len(text)} characters")
            else:
                print(f"    -> failed")
    return entries


# ── 本地 .txt 文件加载 ──────────────────────────────────────────────

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
    """
    books_dir = Path(books_dir)
    if not books_dir.exists():
        logger.warning("Books directory does not exist: %s", books_dir)
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
            "author": "",
            "category": category,
        })

    logger.info(
        "Loaded %d text entries from %d files in %s",
        len(entries), len(txt_files), books_dir,
    )
    return entries


# ── Seed knowledge 回退 ────────────────────────────────────────────

def load_seed_knowledge() -> list[dict[str, str]]:
    """Load built-in classical text excerpts as a fallback.

    These are verified quotes from major divination classics that are
    embedded in the codebase, so no downloads or OCR are needed.
    """
    if not HAS_SEED:
        print("  seed_knowledge module not available (missing scripts/seed_knowledge.py)")
        return []

    entries: list[dict[str, str]] = []
    for category, excerpts in SEED_EXCERPTS.items():
        for excerpt in excerpts:
            entries.append({
                "text": excerpt["text"],
                "source": excerpt.get("source", "未知"),
                "author": excerpt.get("author", ""),
                "category": category,
            })

    logger.info(
        "Loaded %d seed knowledge entries across %d categories",
        len(entries), len(SEED_EXCERPTS),
    )
    return entries


# ── 主流程 ─────────────────────────────────────────────────────────

def collect_texts(
    books_dir: Path,
    use_ctext: bool = True,
    use_seed_fallback: bool = True,
) -> list[dict[str, str]]:
    """Collect texts from all available sources.

    Priority:
    1. Local .txt files (from OCR'd PDFs or manually placed texts)
    2. ctext.org plain text downloads
    3. Built-in seed knowledge excerpts

    Args:
        books_dir: Path to local books directory.
        use_ctext: Whether to try fetching from ctext.org.
        use_seed_fallback: Whether to use seed knowledge as fallback.

    Returns:
        List of {text, source, author, category} dicts.
    """
    all_entries: list[dict[str, str]] = []

    # Source 1: Local .txt files
    print("\n=== Source 1: Local .txt files ===")
    local_entries = load_texts_from_directory(books_dir)
    all_entries.extend(local_entries)
    print(f"  Found {len(local_entries)} local text entries")

    # Source 2: ctext.org plain text
    if use_ctext:
        print("\n=== Source 2: ctext.org plain text ===")
        ctext_entries = load_ctext_texts()
        all_entries.extend(ctext_entries)
        print(f"  Fetched {len(ctext_entries)} ctext.org entries")

    # Source 3: Seed knowledge fallback
    if use_seed_fallback and not local_entries and not all_entries:
        print("\n=== Source 3: Built-in seed knowledge (fallback) ===")
        seed_entries = load_seed_knowledge()
        all_entries.extend(seed_entries)
        print(f"  Loaded {len(seed_entries)} seed knowledge entries")
    elif use_seed_fallback:
        # Always supplement with seed knowledge to ensure rich content
        print("\n=== Source 3: Built-in seed knowledge (supplement) ===")
        seed_entries = load_seed_knowledge()
        all_entries.extend(seed_entries)
        print(f"  Added {len(seed_entries)} seed knowledge entries as supplement")

    return all_entries


def main(
    books_dir: Optional[str] = None,
    vectordb_dir: Optional[str] = None,
    embedding_model: Optional[str] = None,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    no_ctext: bool = False,
    no_seed: bool = False,
) -> None:
    """Build vector index from multiple sources.

    Args:
        books_dir: Path to books directory (default: /mnt/d/fortune-data/books)
        vectordb_dir: Path to vectordb (default: from settings)
        embedding_model: Embedding model name (default: from settings)
        chunk_size: Max chunk size in characters
        chunk_overlap: Overlap between characters
        no_ctext: Skip fetching from ctext.org
        no_seed: Skip seed knowledge supplement
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
    print(f"Chunk size: {chunk_size}, Overlap: {chunk_overlap}")

    # Collect texts from all sources
    entries = collect_texts(
        books_dir=books_path,
        use_ctext=not no_ctext,
        use_seed_fallback=not no_seed,
    )

    if not entries:
        print("\nNo texts found from any source.")
        print("Options:")
        print("  1. Place .txt files under your books directory")
        print("  2. Run scripts/download_books.py then scripts/ocr_books.py")
        print("  3. Run scripts/seed_knowledge.py to seed with built-in excerpts")
        return

    # Load embedder
    print("\nLoading embedder (first run will download BGE model ~1.3GB)...")
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

    print(f"\nDone! Total chunks: {total_chunks}")
    print(f"Collection size: {retriever.count()}")

    # Test search for each category
    test_queries = {
        "bazi": "乙木日主财运",
        "ziwei": "紫微星在命宫",
        "fengshui": "风水布局",
        "yijing": "周易八卦",
        "mianxiang": "面相气色",
        "zeri": "择日吉凶",
        "qimen": "奇门遁甲八门",
        "xingming": "姓名五行",
        "zonghe": "五行相生相克",
    }
    print()
    for cat, query in test_queries.items():
        results = retriever.search(query, category=cat, top_k=3)
        if results:
            print(
                f"  [{cat}] '{query}' -> top: [{results[0].score:.3f}] "
                f"{results[0].source}: {results[0].text[:60]}..."
            )
        else:
            print(f"  [{cat}] '{query}' -> (no results)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Build vector index from OCR'd book texts, ctext.org, and seed knowledge.",
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
    parser.add_argument(
        "--no-ctext",
        action="store_true",
        help="Skip fetching texts from ctext.org",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Skip supplementing with built-in seed knowledge",
    )
    args = parser.parse_args()
    main(
        books_dir=args.books_dir,
        vectordb_dir=args.vectordb_dir,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        no_ctext=args.no_ctext,
        no_seed=args.no_seed,
    )
