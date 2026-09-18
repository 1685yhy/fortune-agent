#!/usr/bin/env python3
"""把 k55 清洗后的语料入库（既有 chunker + Retriever 链路）。

落**独立集合**（默认 `dreams_k55`），不动生产集合（红线：不碰生产）。
检索对比时由 benchmark_retrieval.py 做「生产集合 ∪ k55 集合」的联合检索，
这样「改前/改后」可精确区分，且随时可回退（删集合即可）。

用法：
    python scripts/k55_dream/index_corpus.py [--collection dreams_k55] [--limit 0]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream.crawl_lib import DATA_ROOT, now_iso, read_jsonl  # noqa: E402

CORPUS = DATA_ROOT / "clean" / "dream_corpus.jsonl"
REPORT = DATA_ROOT / "reports" / "index_stats.json"


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="k55 解梦语料入库（独立集合）")
    ap.add_argument("--collection", default="dreams_k55")
    ap.add_argument("--vectordb", default=None)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--limit", type=int, default=0, help="0=全部（便于小样本试跑）")
    args = ap.parse_args()

    from src.config import load_settings
    from src.rag.chunker import chunk_text
    from src.rag.embedder import Embedder
    from src.rag.retriever import Retriever

    settings = load_settings()
    vectordb = args.vectordb or str(settings.vectordb_dir)
    embedder = Embedder(model_name="BAAI/bge-m3")
    embedder.load()
    retriever = Retriever(vectordb, embedder, collection_name=args.collection)

    count_before = retriever.count()
    log(f"[index] 集合 {args.collection} 现有 {count_before} 条")

    records = list(read_jsonl(CORPUS))
    if args.limit:
        records = records[:args.limit]
    log(f"[index] 待入库语料 {len(records)} 条（{CORPUS.name}）")

    existing_ids = set(retriever.collection.get()["ids"])
    log(f"[index] 集合内已有 {len(existing_ids)} 个 chunk")

    chunks, new_count = [], 0
    for i, r in enumerate(records):
        text = f"{r['title']}：{r['content']}"
        # 公版古籍标「书名+卷次」，第三方标来源站（引用可溯源，产品既有口径）
        if r.get("corpus_class") == "public_domain":
            author = (r.get("book") or "古籍") + (f"·{r['volume']}" if r.get("volume") else "")
            category = "dream_classic"
        else:
            author = r.get("source") or "解梦语料"
            category = "dream"
        cs = chunk_text(text=text, source=r.get("source") or "k55", author=author,
                        category=category, chunk_size=500, overlap=50)
        for ci, c in enumerate(cs):
            h = hashlib.md5(f"{r['title']}|{r.get('content','')[:40]}".encode()).hexdigest()[:10]
            c.chunk_id = f"k55_{h}_{i:06d}_{ci:02d}"
            c.source = f"{r.get('source') or 'k55'}｜{r.get('book') or ''}".rstrip("｜")
            if c.chunk_id not in existing_ids:
                chunks.append(c)

    log(f"[index] 生成 {len(chunks)} 个新 chunk（跳过已存在）")
    t0 = time.time()
    for i in range(0, len(chunks), args.batch):
        batch = chunks[i:i + args.batch]
        retriever.add_chunks(batch, batch_size=args.batch)
        new_count += len(batch)
        log(f"[index] {new_count}/{len(chunks)}（{time.time()-t0:.0f}s）")

    count_after = retriever.count()
    stats = {
        "generated_at": now_iso(), "collection": args.collection,
        "vectordb": vectordb, "records": len(records),
        "chunks_added": new_count, "count_before": count_before,
        "count_after": count_after, "elapsed_s": round(time.time() - t0, 1),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[index] 完成：{count_before} → {count_after}（+{count_after-count_before}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
