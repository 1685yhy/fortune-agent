#!/usr/bin/env python3
"""k55 险情重放（k59）—— 改前/改后对照，只跑沙箱副本。

事故路径（2026-09-14，k55）：`Retriever(生产 persist_dir)` + 「一个尚不存在的
集合名」→ 写入时自愈把集合名改写成权威库 `fortune_books_v2` → 数据静默落进
生产检索库（副本：27,115 → 27,116，无任何报错）。

两种模式
--------
* `--code legacy`：**逐字复刻改前写路径** —— `add_chunks` 当时的写法就是
  `self.embedder.encode(...)` + `self.collection.upsert(...)`（`collection` 属性
  会触发自愈）。用来在副本上复现污染。
* `--code guarded`：改后代码 —— `retriever.add_chunks(...)`（写路径不再使用自愈
  结果；实例已自愈则抛 `SelfHealWriteRefused`）。

红线：`--replica` 指向生产库目录（或 `settings.vectordb_dir`）时**直接拒绝执行**。
副本用 `cp -a` 造，例如：
    cp -a /home/a/data/vectordb_v2 /dev/shm/k59/replica_legacy

用法：
    TMPDIR=/dev/shm nice -n 10 python3 scripts/k59/replay_incident.py \
        --replica /dev/shm/k59/replica_legacy --code both \
        --collection k59_replay_new
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

import numpy as np  # noqa: E402

from src.book_categories import BOOKS_COLLECTION  # noqa: E402
from src.rag.chunker import Chunk  # noqa: E402
from src.rag.retriever import Retriever  # noqa: E402
from scripts.k59.prod_fingerprint import collection_fingerprint  # noqa: E402

PROD_DIR = "/home/a/data/vectordb_v2"


class _StubEmbedder:
    """桩 embedder：1024 维确定性向量（零模型/零网络）。

    维度必须与生产语料一致 —— 否则写入权威集合会被 chroma 的维度校验挡下，
    重放就复现不出污染。
    """

    dimension = 1024

    def encode(self, texts):
        out = []
        for t in texts:
            digest = hashlib.sha256(str(t).encode("utf-8")).digest()
            vec = [b / 255.0 for b in (digest * 32)[: self.dimension]]
            out.append(vec)
        return np.array(out, dtype="float32")

    def load(self):
        return True


def _guard(replica: str) -> None:
    """红线：绝不在生产库上跑本脚本。"""
    rep = Path(replica).resolve()
    for bad in (Path(PROD_DIR).resolve(),):
        if rep == bad:
            raise SystemExit(f"拒绝在生产向量库目录上重放（红线）: {rep}")
    if not (rep / "chroma.sqlite3").exists():
        raise SystemExit(f"副本目录里没有 chroma.sqlite3: {rep}")


def _chunk(tag: str) -> Chunk:
    return Chunk(
        text=f"k59 重放用例 {tag}：解梦语料占位正文（只为触发写入路径）。",
        source="k59-replay",
        author="k59",
        category="dream",
        chunk_id=f"k59_replay_{tag}",
    )


def _legacy_write(retriever: Retriever, chunk: Chunk) -> None:
    """逐字复刻改前的 add_chunks 写法（写目标经 `collection` 属性 → 会被自愈）。"""
    chunk_ok = [chunk]
    texts = [c.text for c in chunk_ok]
    embeddings = retriever.embedder.encode(texts)
    retriever.collection.upsert(
        embeddings=embeddings.tolist(),
        documents=texts,
        ids=[c.chunk_id for c in chunk_ok],
        metadatas=[{"source": c.source, "author": c.author, "category": c.category}
                   for c in chunk_ok],
    )


def _run(replica: str, code: str, collection: str, tag: str) -> dict:
    db = str(Path(replica) / "chroma.sqlite3")
    before = collection_fingerprint(db, BOOKS_COLLECTION)
    retriever = Retriever(str(replica), _StubEmbedder(), collection_name=collection)
    chunk = _chunk(tag)

    result = {"code": code, "requested": collection, "before": before}
    try:
        if code == "legacy":
            _legacy_write(retriever, chunk)
            result["disposition"] = "wrote"
        else:
            if code == "guarded_after_read":
                # 先读一次（触发自愈）—— 最贴近生产的形态：同一实例既读又写
                result["read_count"] = retriever.count()
            retriever.add_chunks([chunk])
            result["disposition"] = "wrote"
    except Exception as e:  # 护栏拒绝：SelfHealWriteRefused
        result["disposition"] = f"raised {type(e).__name__}: {e}"

    result["resolved_collection"] = retriever.collection_name
    # getattr：本脚本同时用来说明**改前**行为（在 55ea3f7 旧代码树上跑 legacy），
    # 旧模块没有 self_healed_to 属性。
    result["self_healed_to"] = getattr(retriever, "self_healed_to", "<旧代码无此属性>")
    result["after"] = collection_fingerprint(db, BOOKS_COLLECTION)
    result["target_collection_count"] = collection_fingerprint(db, collection)["count"]
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="k55 险情重放（k59，沙箱副本）")
    ap.add_argument("--replica", required=True, help="沙箱副本目录（含 chroma.sqlite3）")
    ap.add_argument("--code", default="both",
                    choices=["legacy", "guarded", "guarded_after_read", "both"])
    ap.add_argument("--collection", default="k59_replay_new",
                    help="请求写入的集合名（默认：一个不存在的集合名）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    _guard(args.replica)
    codes = ["legacy", "guarded"] if args.code == "both" else [args.code]
    out = [_run(args.replica, code, args.collection, f"{code}_{i}")
           for i, code in enumerate(codes)]

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    for r in out:
        b, a = r["before"], r["after"]
        print(f"--- code={r['code']} 请求集合='{r['requested']}' ---")
        print(f"  实际生效集合 : {r['resolved_collection']}"
              f"（self_healed_to={r['self_healed_to']}）")
        print(f"  写入处置     : {r['disposition']}")
        print(f"  显式集合条数 : {r['target_collection_count']}")
        print(f"  权威集合     : {b['count']} → {a['count']}"
              f"  meta_md5 {b['meta_md5'][:12]} → {a['meta_md5'][:12]}"
              f"  {'[零变化]' if b == a else '[已变化]'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
