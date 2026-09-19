#!/usr/bin/env python3
"""生产向量库只读指纹（k59）—— 用来证明「生产零变化」。

只读原则
--------
**不**构造 `chromadb.PersistentClient`：客户端初始化可能触发迁移/写盘，而
「只读」是本批实验的硬约束（k59 brief 第 5 条）。本工具直接以 sqlite
`mode=ro` 连接 `chroma.sqlite3` 取数 —— 全程无写、无建文件、不开 chroma。

指纹口径（可复现；NULL 一律记空串）
----------------------------------
对集合 C：
  * count     = C 的 METADATA segment 下去重 `embedding_id` 数
  * meta_md5  = 全量 (embedding_id, key, string_value, int_value, float_value,
                bool_value) 按 (embedding_id, key) 排序 → 每行 TAB 连接、
                行间 "\\n"、UTF-8 编码 → MD5
  * ids_md5   = 排序后的 embedding_id 以 "\\n" 连接 → MD5
  * emb_rows  = `embeddings` 表中属于 C 的行数
  * vec_*     = C 的 VECTOR segment（HNSW 索引目录）：各文件大小、`header.bin`
                与 `index_metadata.pickle` 的 MD5（小文件，读得起；向量本体
                data_level0.bin 只记大小）

用法：
    python3 scripts/k59/prod_fingerprint.py                       # 默认生产库
    python3 scripts/k59/prod_fingerprint.py --db /path/chroma.sqlite3 \
        --collection fortune_books_v2 --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from src.book_categories import BOOKS_COLLECTION  # noqa: E402

DEFAULT_DB = "/home/a/data/vectordb_v2/chroma.sqlite3"


def _rows(cur, collection_name: str):
    cur.execute(
        """
        SELECT e.embedding_id, m.key, m.string_value, m.int_value,
               m.float_value, m.bool_value
        FROM embedding_metadata m
        JOIN embeddings e ON e.id = m.id
        JOIN segments s ON s.id = e.segment_id
        JOIN collections c ON c.id = s.collection
        WHERE c.name = ? AND s.scope = 'METADATA'
        """,
        (collection_name,),
    )
    return sorted(cur.fetchall(), key=lambda r: (r[0], r[1]))


def collection_fingerprint(db_path: str, collection_name: str) -> dict:
    """集合指纹（只读；集合不存在时 count=0、md5 为空串）。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        rows = _rows(cur, collection_name)
        ids = sorted({r[0] for r in rows})
        blob = "\n".join(
            "\t".join("" if v is None else str(v) for v in r) for r in rows
        )
        cur.execute(
            """
            SELECT COUNT(*) FROM embeddings e
            JOIN segments s ON s.id = e.segment_id
            JOIN collections c ON c.id = s.collection
            WHERE c.name = ?
            """,
            (collection_name,),
        )
        emb_rows = cur.fetchone()[0]
        cur.execute(
            """
            SELECT s.id FROM segments s JOIN collections c ON c.id = s.collection
            WHERE c.name = ? AND s.scope = 'VECTOR'
            """,
            (collection_name,),
        )
        vec_seg = cur.fetchone()
        fp = {
            "collection": collection_name,
            "count": len(ids),
            "meta_rows": len(rows),
            "meta_md5": hashlib.md5(blob.encode("utf-8")).hexdigest(),
            "ids_md5": hashlib.md5("\n".join(ids).encode("utf-8")).hexdigest(),
            "emb_rows": emb_rows,
        }
    finally:
        con.close()

    fp["vec_files"] = {}
    if vec_seg:
        seg_dir = Path(db_path).parent / vec_seg[0]
        if seg_dir.is_dir():
            for p in sorted(seg_dir.iterdir()):
                if not p.is_file():
                    continue
                fp["vec_files"][p.name] = p.stat().st_size
                if p.name in ("header.bin", "index_metadata.pickle"):
                    fp[f"vec_{p.stem}_md5"] = hashlib.md5(p.read_bytes()).hexdigest()
    return fp


def main() -> int:
    ap = argparse.ArgumentParser(description="生产向量库只读指纹（k59）")
    ap.add_argument("--db", default=DEFAULT_DB, help="chroma.sqlite3 路径（只读打开）")
    ap.add_argument("--collection", default=BOOKS_COLLECTION)
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"FATAL: 库文件不存在: {args.db}", file=sys.stderr)
        return 1
    fp = collection_fingerprint(args.db, args.collection)
    if args.json:
        print(json.dumps(fp, ensure_ascii=False))
    else:
        print(f"db            : {args.db}（只读）")
        print(f"collection    : {fp['collection']}")
        print(f"count         : {fp['count']}")
        print(f"meta_rows     : {fp['meta_rows']}")
        print(f"meta_md5      : {fp['meta_md5']}")
        print(f"ids_md5       : {fp['ids_md5']}")
        print(f"emb_rows      : {fp['emb_rows']}")
        print(f"vec_files     : {fp['vec_files']}")
        for k in ("vec_header_md5", "vec_index_metadata_md5"):
            if k in fp:
                print(f"{k:<14}: {fp[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
