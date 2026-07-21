#!/usr/bin/env python3
"""重建向量索引 — 使用 BGE-M3 (1024-dim) 重建 fortune_books 集合.

与旧 index_verified.py 的区别:
1. 使用新的确定性的 EmbedderV2 (BGE-M3, 1024-dim)
2. 写入版本化集合 (fortune_books_v4_bge_m3)
3. 每 1000 条自动检查点 (resume 支持)
4. 完成后验证 dimension + count
5. 预估进度和耗时

使用:
  python scripts/rebuild_index_v2.py           # 全量重建
  python scripts/rebuild_index_v2.py --check   # 仅验证
"""
import sys, json, time, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_settings
from src.rag.collection_manager import CollectionManager


STAGING_DIR = "/mnt/d/fortune-data/books/zonghe/staging"
BATCH_SIZE = 50  # 小批次避免内存压力
CHECKPOINT_INTERVAL = 1000  # 每 1000 条记录检查点


def load_entries(staging_dir: str) -> list:
    """加载所有 staging 中的 accepted 条目"""
    staging = Path(staging_dir)
    entries = []
    for f in sorted(staging.glob("*_accepted.jsonl")):
        print(f"  Loading {f.name}...")
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
        print(f"    {len(entries)} total so far")
    return entries


def build_index(entries: list, collection_name: str, settings, embedder) -> int:
    """逐条编码写入，带检查点恢复"""
    from src.rag.collection_manager import CollectionManager

    cm = CollectionManager(
        str(settings.vectordb_dir), collection_name, settings.embedding_dimension
    )
    collection = cm.ensure_exists()

    # 检查已有进度（resume 支持）
    existing_count = collection.count()
    start_idx = existing_count
    if start_idx > 0:
        print(f"Resuming from checkpoint: {start_idx} already indexed")

    total = len(entries)
    print(f"Indexing {total - start_idx} entries (total: {total})...")
    print(f"Model: {settings.embedding_model}, Dim: {settings.embedding_dimension}")
    print(f"Target collection: {collection_name}")

    t0 = time.time()
    indexed = start_idx

    for i in range(start_idx, total, BATCH_SIZE):
        batch = entries[i:i + BATCH_SIZE]
        ids = []
        documents = []
        metadatas = []

        for entry in batch:
            content = entry.get("content", entry.get("text", ""))
            title = entry.get("title", "")
            category = entry.get("category", "general")
            source = entry.get("source_url", entry.get("source", ""))
            text = f"{title}\n{content}"

            doc_id = f"v4_{category}_{abs(hash(text)) % 10**10}"
            ids.append(doc_id)
            documents.append(text)
            metadatas.append({
                "title": title,
                "category": category,
                "source_url": source,
                "verified": True,
            })

        try:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            indexed += len(batch)
        except Exception as e:
            print(f"  Batch error at {i}-{i+len(batch)}: {e}")
            continue

        # 进度显示
        if indexed % CHECKPOINT_INTERVAL == 0 or indexed >= total:
            elapsed = time.time() - t0
            rate = (indexed - start_idx) / max(elapsed, 1)
            remaining = (total - indexed) / max(rate, 0.01)
            print(
                f"  {indexed}/{total} ({100*indexed/total:.1f}%) | "
                f"{rate:.0f} docs/s | ETA: {remaining/60:.0f}min"
            )

    elapsed = time.time() - t0
    print(f"\nDone: {indexed} entries in {elapsed/60:.1f}min ({indexed/elapsed:.0f} docs/s)")

    # 验证
    report = cm.validate()
    print(f"Validation: {report}")
    if not report.valid:
        print(f"WARNING: {report.errors}")

    return indexed


def cmd_check(settings):
    """仅验证集合状态"""
    cm = CollectionManager(
        str(settings.vectordb_dir),
        settings.embedding_collection,
        settings.embedding_dimension,
    )
    report = cm.validate()
    print("=" * 50)
    print(f"Collection: {report.collection_name}")
    print(f"  Exists: {report.exists}")
    print(f"  Docs: {report.doc_count}")
    print(f"  Dimension: {report.dimension}")
    print(f"  Valid: {report.valid}")
    if report.errors:
        print(f"  Errors: {report.errors}")
    print("=" * 50)

    # 也列出所有集合
    print("\nAll collections:")
    for info in cm.list_collections():
        print(f"  {info['name']}: {info['count']} docs")


def cmd_build(settings):
    """全量重建"""
    # 加载数据
    print("Loading staging entries...")
    entries = load_entries(STAGING_DIR)
    print(f"Total entries loaded: {len(entries)}")
    if not entries:
        print("No entries found. Abort.")
        sys.exit(1)

    # 加载 Embedder
    print(f"Loading embedder: {settings.embedding_model}")
    from src.rag.embedder import Embedder
    embedder = Embedder(model_name=settings.embedding_model)
    if not embedder.load():
        print("FATAL: Cannot load embedder. Check network and model name.")
        sys.exit(1)
    print(f"Embedder loaded: dim={embedder.dimension}")

    # 验证维度
    if embedder.dimension != settings.embedding_dimension:
        print(
            f"FATAL: Dimension mismatch. model={embedder.dimension}, "
            f"config={settings.embedding_dimension}"
        )
        sys.exit(1)

    # 构建索引
    build_index(entries, settings.embedding_collection, settings, embedder)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild vector index with BGE-M3")
    parser.add_argument("--check", action="store_true", help="Only validate, don't rebuild")
    args = parser.parse_args()

    settings = load_settings()
    if args.check:
        cmd_check(settings)
    else:
        cmd_build(settings)
