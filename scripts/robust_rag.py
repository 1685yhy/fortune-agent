"""Robust RAG processing using isolated worker processes with timeouts."""
from __future__ import annotations
import logging
import multiprocessing
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.rag.chunker import chunk_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

BOOKS_DIR = Path("/mnt/d/fortune-data/books")
CHUNK_SIZE = 800
CHUNK_OVERLAP = 80
TEXT_TIMEOUT = 600  # 10 minutes per text


def worker_process_init(vdb_dir: str, model_name: str):
    """Initialize global embedder + retriever in worker process."""
    global _embedder, _retriever
    from src.rag.embedder import Embedder
    from src.rag.retriever import Retriever
    _embedder = Embedder(model_name=model_name)
    _retriever = Retriever(vdb_dir, _embedder)
    logger.info("Worker initialized (model loaded)")


def process_text(txt_path_str: str, source: str, category: str) -> dict:
    """Process a single text file. Runs in worker process."""
    global _embedder, _retriever
    txt_path = Path(txt_path_str)
    try:
        text = txt_path.read_text(encoding="utf-8").strip()
        if len(text) < 100:
            return {"status": "too_short", "chars": len(text)}

        chunks = chunk_text(
            text, source=source, author="", category=category,
            chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP,
        )
        if not chunks:
            return {"status": "no_chunks", "chars": len(text)}

        _retriever.add_chunks(chunks)
        return {"status": "ok", "chunks": len(chunks), "chars": len(text)}
    except Exception as e:
        logger.error("Worker error for %s: %s", source, e)
        return {"status": "error", "error": str(e)}


def main():
    """Process all text files using a persistent worker pool."""
    from src.config import load_settings
    settings = load_settings()
    settings.vectordb_dir.mkdir(parents=True, exist_ok=True)

    # Collect all text files
    all_files: list[tuple[str, str, str]] = []
    for cat_dir in sorted(BOOKS_DIR.iterdir()):
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        for txt_path in sorted(cat_dir.glob("*.txt")):
            source = txt_path.stem
            all_files.append((str(txt_path), source, category))

    logger.info("Total texts to process: %d", len(all_files))

    # Create persistent worker pool (1 worker to avoid DB contention)
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(
        1,
        initializer=worker_process_init,
        initargs=(str(settings.vectordb_dir), settings.embedding_model),
    ) as pool:
        total_chunks = 0
        success = 0
        failed = 0
        t_start = time.time()

        # Submit all tasks
        async_results = []
        for txt_path_str, source, category in all_files:
            async_results.append(
                pool.apply_async(process_text, (txt_path_str, source, category))
            )

        # Collect results with per-task timeout
        for i, (txt_path_str, source, category) in enumerate(all_files):
            elapsed = time.time() - t_start
            logger.info(
                "[%d/%d] Processing: [%s] %s (elapsed: %.1fs)...",
                i + 1, len(all_files), category, source, elapsed,
            )

            try:
                result = async_results[i].get(timeout=TEXT_TIMEOUT)
                s = result["status"]
                if s == "ok":
                    success += 1
                    total_chunks += result["chunks"]
                    logger.info(
                        "  OK: %s -> %d chunks (%d chars) [%d/%d]",
                        source, result["chunks"], result["chars"],
                        success, i + 1,
                    )
                elif s == "too_short":
                    logger.info("  SKIP (too short): %s (%d chars)", source, result["chars"])
                else:
                    failed += 1
                    logger.error("  FAIL: %s - %s", source, result.get("error", "unknown"))
            except multiprocessing.TimeoutError:
                pool.terminate()
                pool.join()
                failed += 1
                logger.error("  TIMEOUT: %s (%ds exceeded)", source, TEXT_TIMEOUT)
                break  # Pool is dead after terminate

    elapsed_total = time.time() - t_start
    logger.info(
        "\nDone: %d ok, %d failed, %d total chunks in %.1fs",
        success, failed, total_chunks, elapsed_total,
    )

    # Final count and test queries
    from src.config import load_settings as _ls
    from src.rag.embedder import Embedder
    from src.rag.retriever import Retriever
    settings2 = _ls()
    embedder = Embedder(model_name=settings2.embedding_model)
    retriever = Retriever(str(settings2.vectordb_dir), embedder)

    logger.info("\nFinal collection size: %d chunks", retriever.count())

    test_queries = {
        "bazi": "乙木日主财运",
        "yijing": "梅花易数起卦方法",
        "fengshui": "风水龙脉砂水",
        "mianxiang": "面相气色吉凶",
        "zeri": "协纪辨方书择日",
        "qimen": "奇门遁甲八门",
        "ziwei": "紫微星在命宫",
    }
    logger.info("\nTest searches:")
    for cat, query in test_queries.items():
        results = retriever.search(query, category=cat, top_k=3)
        if results:
            logger.info("  [%s] '%s' -> [%.3f] %s: %s...", cat, query, results[0].score, results[0].source, results[0].text[:60])
        else:
            logger.info("  [%s] '%s' -> (no results)", cat, query)

    logger.info("\nAll done!")


if __name__ == "__main__":
    main()
