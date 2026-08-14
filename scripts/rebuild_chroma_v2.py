#!/usr/bin/env python3
"""古籍向量库重建 — 用 bge-m3 (1024维) 重建 chroma 集合为 fortune_books_v2.

背景: 旧 fortune_books 集合由 chroma 内置 MiniLM (384维) 自动嵌入构建，
而 app 的 Embedder 是 bge-m3 (1024维)，导致启动校验报
"Dimension mismatch: Collection expecting 384, got 1024"。
本脚本用与 app 完全一致的 bge-m3 重新编码并写入新集合。

数据来源（两种模式）:
  1. `--source collection` (默认): 把旧集合的现有文档/元数据原样重编码。
     旧集合内容并非纯 books 目录产物（实测 27,115 条全部 source 为空，
     元数据为 {title, verified, category}，含 dream/bazi_case 等分类），
     只有"原样重编码"才能保证换库后服务行为与之前完全一致。
  2. `--source books`: 走 /mnt/d/fortune-data/books 目录，复用 chunk_text
     切块 + build_index 的目录名分类检测，经 retriever.add_chunks 摄入
     （约 5.5 万块，与旧集合内容不同，谨慎使用）。

用法:
  EMBEDDING_COLLECTION=fortune_books_v2 nohup .venv/bin/python3 \
      scripts/rebuild_chroma_v2.py > /tmp/rebuild_chroma_v2.log 2>&1 &
进度: 每 2000 条打印一次。GPU 可用时自动使用 (SentenceTransformer 自动检测)。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_settings
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

logger = logging.getLogger("rebuild_chroma_v2")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DEFAULT_BOOKS_DIR = Path("/mnt/d/fortune-data/books")
BATCH_SIZE = 128  # 编码批次
FETCH_BATCH = 5000  # 从旧集合拉取批次
LOG_INTERVAL = 2000  # 进度日志间隔


def _check_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return f"cuda:{torch.cuda.current_device()}"
    except Exception:
        pass
    return "cpu"


def _validate_target(settings, collection_name: str) -> None:
    """用与 main.py 相同的 CollectionManager 校验新集合"""
    from src.rag.collection_manager import CollectionManager

    cm = CollectionManager(
        str(settings.vectordb_dir),
        collection_name,
        settings.embedding_dimension,
    )
    report = cm.validate()
    print("=" * 50)
    print(f"Validation: collection={report.collection_name}")
    print(f"  exists={report.exists} docs={report.doc_count} "
          f"dimension={report.dimension} valid={report.valid}")
    if report.errors:
        print(f"  errors: {report.errors}")
    return report


def rebuild_from_collection(settings, source_name: str, target_name: str) -> int:
    """模式1: 旧集合文档原样重编码 → 新集合"""
    from src.rag.collection_manager import CollectionManager

    embedder = Embedder(model_name=settings.embedding_model)
    print(f"Loading embedder {settings.embedding_model} (device={_check_device()})...")
    t0 = time.time()
    if not embedder.load():
        print("FATAL: Cannot load embedder")
        sys.exit(1)
    print(f"Embedder loaded: dim={embedder.dimension} "
          f"({time.time()-t0:.0f}s), device={_check_device()}")

    if embedder.dimension != settings.embedding_dimension:
        print(f"FATAL: model dim {embedder.dimension} != config "
              f"{settings.embedding_dimension}")
        sys.exit(1)

    cm = CollectionManager(str(settings.vectordb_dir), source_name, settings.embedding_dimension)
    src = cm.client.get_collection(source_name, embedding_function=None)
    total = src.count()
    print(f"Source collection '{source_name}': {total} docs")

    target = cm.client.get_or_create_collection(
        name=target_name,
        embedding_function=None,  # 我们自己提供 embedding
        metadata={
            "hnsw:space": "cosine",
            "dimension": settings.embedding_dimension,
            "model": settings.embedding_model,
            "source": source_name,
            "rebuilt": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    if target.count() > 0:
        print(f"WARNING: target collection already has {target.count()} docs; "
              f"skipping existing ids via upsert")

    t_start = time.time()
    indexed = 0
    for offset in range(0, total, FETCH_BATCH):
        got = src.get(
            limit=FETCH_BATCH,
            offset=offset,
            include=["documents", "metadatas"],
        )
        batch_ids = got["ids"]
        batch_docs = got["documents"]
        batch_metas = got["metadatas"]

        for start in range(0, len(batch_ids), BATCH_SIZE):
            end = start + BATCH_SIZE
            embeddings = embedder.encode(batch_docs[start:end]).tolist()
            target.upsert(
                ids=batch_ids[start:end],
                documents=batch_docs[start:end],
                metadatas=batch_metas[start:end],
                embeddings=embeddings,
            )
            indexed += (end - start)
            if indexed % LOG_INTERVAL == 0 or indexed >= total:
                elapsed = time.time() - t_start
                rate = indexed / max(elapsed, 1)
                print(
                    f"  {indexed}/{total} ({100*indexed/total:.1f}%) | "
                    f"{rate:.0f} docs/s | {elapsed/60:.1f}min elapsed | "
                    f"ETA {(total-indexed)/max(rate,0.01)/60:.0f}min"
                )

    print(f"Done: {indexed} docs in {(time.time()-t_start)/60:.1f}min")
    _validate_target(settings, target_name)
    return indexed


def rebuild_from_books(settings, target_name: str) -> int:
    """模式2: 走 books 目录重切块摄入（任务原始方案，保留为可选）"""
    from scripts.build_index import load_texts_from_directory

    embedder = Embedder(model_name=settings.embedding_model)
    print(f"Loading embedder {settings.embedding_model} (device={_check_device()})...")
    if not embedder.load():
        print("FATAL: Cannot load embedder")
        sys.exit(1)
    print(f"Embedder loaded: dim={embedder.dimension}")

    retriever = Retriever(str(settings.vectordb_dir), embedder)
    # 目标集合名：显式覆盖
    retriever._collection_name = target_name

    entries = load_texts_from_directory(DEFAULT_BOOKS_DIR)
    print(f"Loaded {len(entries)} text entries from {DEFAULT_BOOKS_DIR}")

    t_start = time.time()
    total_chunks = 0
    for entry in entries:
        chunks = chunk_text(
            entry["text"],
            source=entry["source"],
            author=entry["author"],
            category=entry["category"],
        )
        if chunks:
            retriever.add_chunks(chunks)
            total_chunks += len(chunks)
            if total_chunks % LOG_INTERVAL == 0:
                elapsed = time.time() - t_start
                rate = total_chunks / max(elapsed, 1)
                print(
                    f"  {total_chunks} chunks | {rate:.0f} chunks/s | "
                    f"{elapsed/60:.1f}min elapsed"
                )

    print(f"Done: {total_chunks} chunks in {(time.time()-t_start)/60:.1f}min")
    _validate_target(settings, target_name)
    return total_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=["collection", "books"],
        default="collection",
        help="数据来源: collection=旧集合原样重编码(默认), books=books 目录重切块",
    )
    parser.add_argument(
        "--source-collection",
        default="fortune_books",
        help="旧集合名 (默认 fortune_books)",
    )
    parser.add_argument(
        "--target-collection",
        default=os.environ.get("EMBEDDING_COLLECTION", "fortune_books_v2"),
        help="新集合名 (默认 fortune_books_v2, 可被 EMBEDDING_COLLECTION 覆盖)",
    )
    parser.add_argument(
        "--vectordb-dir",
        default=None,
        help="vectordb 目录 (默认取自 settings)",
    )
    args = parser.parse_args()

    settings = load_settings()
    if args.vectordb_dir:
        settings.vectordb_dir = Path(args.vectordb_dir)
    settings.vectordb_dir.mkdir(parents=True, exist_ok=True)

    print(f"Vectordb dir: {settings.vectordb_dir}")
    print(f"Embedding model: {settings.embedding_model} "
          f"(dim {settings.embedding_dimension})")
    print(f"Source: {args.source} -> target collection: {args.target_collection}")

    if args.source == "books":
        rebuild_from_books(settings, args.target_collection)
    else:
        rebuild_from_collection(
            settings, args.source_collection, args.target_collection
        )


if __name__ == "__main__":
    main()
