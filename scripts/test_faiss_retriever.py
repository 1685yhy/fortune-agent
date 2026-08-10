#!/usr/bin/env python3
"""FAISS 检索器验证脚本 — 易理明灯生产检索接入（276 万条向量库）。

运行（项目根目录）:
    .venv/bin/python scripts/test_faiss_retriever.py

验证项:
  1. 索引惰性加载计时 + docs.db 文本映射正确（抽样 3 条与 SQLite 对账）
  2. 语义检索质量: "梦见蛇" → 蛇/梦相关古籍; "财运" → 财运古籍（语义而非纯关键词）
  3. 查询速度: FAISS index.search < 100ms（编码耗时单独计，不算入）
  4. FAISS 不可用（FAISS_INDEX_DIR 指向不存在路径）→ 返回空 + 工具层对话标记，不崩溃
  5. 工具级 E2E: 真实 handler._execute_tool_call("检索", ...) 走通（成功 + 失败两路）
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def show_results(title: str, refs) -> None:
    print(f"  -- {title} --")
    for i, r in enumerate(refs, 1):
        src = r.get("source") or r.get("title") or "?"
        print(f"    {i}. [{r['score']:.4f}] 【{src}】{(r['text'] or '')[:80]}")


def main() -> int:
    from src.rag.faiss_retriever import (
        get_faiss_retriever,
        reset_faiss_retriever,
    )

    print("=" * 70)
    print("1) 索引加载计时 + docs.db 文本映射校验")
    print("=" * 70)
    reset_faiss_retriever()
    retriever = get_faiss_retriever()
    t0 = time.time()
    ready = retriever.ensure_ready()
    load_sec = time.time() - t0
    print(f"  首次 ensure_ready() 耗时: {load_sec:.1f}s (index.faiss 238MB + docs.db 6.46GB + bge-m3)")
    check("FAISS 索引加载成功", ready, f"load_error={retriever.load_error}")
    if not ready:
        print("  索引加载失败，后续用例跳过"); return 1
    print(f"  索引统计: {retriever.stats()}")
    check("ntotal = 2760956", retriever.ntotal == 2760956, f"got {retriever.ntotal}")
    check("维度 = 1024", retriever.dimension == 1024, f"got {retriever.dimension}")

    # 文本映射对账: 从检索结果取 3 条，与 docs.db 直接查询比对
    refs = retriever.search("梦见蛇", top_k=3)
    check("'梦见蛇' 检索返回 3 条", len(refs) == 3, f"got {len(refs)}")
    mapping_ok = True
    conn = sqlite3.connect(f"file:{retriever.docs_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    for r in refs:
        row = conn.execute(
            "SELECT doc_id, text, title, category, source FROM docs WHERE doc_id = ?",
            (r["doc_id"],),
        ).fetchone()
        if row is None:
            mapping_ok = False
            print(f"    doc_id 未命中: {r['doc_id']}")
            continue
        if (row["text"] or "")[:600] != r["text"]:
            mapping_ok = False
            print(f"    text 不一致: {r['doc_id']}")
        if str(row["source"] or "") != r["source"]:
            mapping_ok = False
            print(f"    source 不一致: {r['doc_id']}")
    conn.close()
    check("docs.db 文本映射一致（抽样 3 条）", mapping_ok)

    print()
    print("=" * 70)
    print("2) 语义检索质量（FAISS vs 关键词直觉）")
    print("=" * 70)
    snake = retriever.search("梦见蛇", top_k=5)
    show_results("梦见蛇", snake)
    check("梦见蛇 → 非空且分数合理", bool(snake) and snake[0]["score"] >= 0.4,
          f"top score={snake[0]['score'] if snake else 'N/A'}")
    snake_hit = sum(1 for r in snake if ("蛇" in r["text"] or "梦" in r["text"]))
    check("梦见蛇 → 至少 1 条含蛇/梦", snake_hit >= 1, f"hits={snake_hit}")

    wealth = retriever.search("财运", top_k=5)
    show_results("财运", wealth)
    check("财运 → 非空", bool(wealth))
    wealth_hit = sum(1 for r in wealth if any(k in r["text"] for k in "财富禄货"))
    check("财运 → 至少 2 条含财/富/禄/货（语义而非纯关键词）", wealth_hit >= 2, f"hits={wealth_hit}")

    fengshui = retriever.search("房屋风水 坐北朝南 大门朝向", top_k=5)
    show_results("房屋风水 坐北朝南", fengshui)
    check("风水 → 非空", bool(fengshui))

    print()
    print("=" * 70)
    print("3) 查询速度（FAISS index.search 裸调用，5 次取均值）")
    print("=" * 70)
    import numpy as np
    vec = retriever._embedder.encode_single("梦见蛇")
    vec = np.ascontiguousarray(vec.astype(np.float32).reshape(1, -1))
    index = retriever._index
    times = []
    for _ in range(5):
        t0 = time.time()
        index.search(vec, 15)
        times.append((time.time() - t0) * 1000)
    avg = sum(times) / len(times)
    print(f"  5 次 index.search 耗时: {[f'{t:.1f}ms' for t in times]}, 均值 {avg:.1f}ms")
    check("FAISS search < 100ms", avg < 100, f"avg={avg:.1f}ms")

    print()
    print("=" * 70)
    print("4) FAISS 不可用 → 空结果 + 对话标记，不崩溃")
    print("=" * 70)
    reset_faiss_retriever()
    os.environ["FAISS_INDEX_DIR"] = "/tmp/nonexistent-faiss-dir"
    try:
        broken = get_faiss_retriever()
        t0 = time.time()
        empty = broken.search("梦见蛇")
        print(f"  不可用路径 search 耗时 {time.time()-t0:.2f}s, "
              f"ready={broken.ready}, error={broken.load_error}")
        check("不可用路径返回空列表（不抛异常）", empty == [])
    finally:
        os.environ.pop("FAISS_INDEX_DIR", None)
    reset_faiss_retriever()

    print()
    print("=" * 70)
    print("5) 工具级 E2E — 真实 handler._execute_tool_call('检索', ...)")
    print("=" * 70)
    from src.bot.handler import MessageHandler

    handler = MessageHandler.__new__(MessageHandler)  # 轻量构造，不初始化各引擎
    res = handler._execute_tool_call("检索", "梦见蛇 解梦", "test_user")
    print(f"  成功路径: ok={res.ok} needs_info={res.needs_info}")
    print(f"  {res.text[:200]}")
    check("E2E 成功: ok=True 且含检索结果", res.ok and "古籍检索结果" in res.text)
    check("E2E 成功: 含相关度分数", "相关度" in res.text)

    # 失败路径: FAISS 目录不存在 → 对话标记（needs_info=True），不崩溃
    reset_faiss_retriever()
    os.environ["FAISS_INDEX_DIR"] = "/tmp/nonexistent-faiss-dir"
    try:
        res2 = handler._execute_tool_call("检索", "梦见蛇 解梦", "test_user")
    finally:
        os.environ.pop("FAISS_INDEX_DIR", None)
    print(f"  失败路径: ok={res2.ok} needs_info={res2.needs_info}")
    print(f"  {res2.text[:120]}")
    check("E2E 失败: 返回对话标记 needs_info=True", res2.needs_info)
    check("E2E 失败: 不抛异常、提示自然对话", not res2.ok and "继续对话" in res2.text)
    reset_faiss_retriever()

    print()
    print("=" * 70)
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
