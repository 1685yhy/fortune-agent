#!/usr/bin/env python3
"""20 条真实梦例的检索命中率对比（改前 / 改后）。

梦例来源（**真实**，不编造）：抓取到的佛滔·梦境百科网友梦境
（k55_dream/raw/sosuo_meng_detail.jsonl，每条带 URL 与提交时间），
外加控制方提供的实测梦例（开车族）。首次运行会把 20 条**冻结**到
reports/benchmark_queries_k55.json，之后每次跑都用同一批，保证可比。

对比口径（三档，用于把增益归因清楚）：
- A 改前：旧引擎（无规则层）+ 旧语料（生产集合 fortune_books_v2）
- B 中间档：新引擎（含规则层）+ 旧语料
- C 改后：新引擎 + 旧语料 + k55 新语料（联合检索）

指标：
- 检索命中率：命中「相关条目」的梦例占比。相关 = 检索到的文本里含该梦的
  核心元素词（由引擎规则层命中的模式名 / keywords[0] 机械判定，非人工打分）；
- 平均检索条数、平均相关条数、top1 相关率；
- 三项非空率（梦境类型/核心象征/情绪基调）——规则层直接影响的指标。

用法：
    python scripts/k55_dream/benchmark_retrieval.py [--n 20]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream.crawl_lib import DATA_ROOT, now_iso, read_jsonl  # noqa: E402

RAW = DATA_ROOT / "raw"
REPORTS = DATA_ROOT / "reports"
QUERIES = REPORTS / "benchmark_queries_k55.json"
OUT = REPORTS / "retrieval_benchmark.json"
K55_COLLECTION = "dreams_k55"
PROD_COLLECTION = "fortune_books_v2"

CONTROLLER_DREAM = "梦见和对象开车出去玩，回来太困了把车留在半道了"


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


def build_queries(n: int) -> list:
    """20 条真实梦例（真实网友梦境，均匀抽样；控制方梦例必含）。"""
    if QUERIES.exists():
        return json.loads(QUERIES.read_text(encoding="utf-8"))["queries"]
    recs = [r for r in read_jsonl(RAW / "sosuo_meng_detail.jsonl")
            if len(r.get("content", "")) >= 25 and r.get("title", "").startswith("梦见")]
    recs.sort(key=lambda r: r.get("site_id", ""))
    if not recs:
        raise SystemExit("没有真实梦例（先跑 scrape_sosuo.py --phase walk/detail）")
    step = max(1, len(recs) // (n - 1))
    picked = recs[::step][:n - 1]
    queries = [{"text": CONTROLLER_DREAM, "origin": "控制方实测梦例", "url": ""}]
    queries += [{"text": r["content"][:200], "origin": "佛滔·梦境百科 网友梦境",
                 "url": r.get("url", ""), "title": r.get("title", ""),
                 "date": r.get("fetched_at", "")} for r in picked]
    QUERIES.parent.mkdir(parents=True, exist_ok=True)
    QUERIES.write_text(json.dumps(
        {"frozen_at": now_iso(), "source": str(RAW / "sosuo_meng_detail.jsonl"),
         "note": "真实梦例快照：一旦冻结，后续对比都用同一批", "queries": queries},
        ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[bench] 冻结 {len(queries)} 条真实梦例 → {QUERIES.name}")
    return queries


class UnionRetriever:
    """多集合联合检索（改后 = 生产集合 ∪ k55 集合）。"""

    def __init__(self, retrievers):
        self.retrievers = retrievers
        self.calls = 0

    def search(self, query, top_k=5, **kw):
        self.calls += 1
        out, seen = [], set()
        for r in self.retrievers:
            try:
                res = r.search(query, top_k=top_k, **kw)
            except Exception as e:
                log(f"[bench] 检索失败（{type(e).__name__}: {e}）")
                continue
            for h in res:
                key = getattr(h, "chunk_id", None) or h.text[:60]
                if key in seen:
                    continue
                seen.add(key)
                out.append(h)
        out.sort(key=lambda h: h.score, reverse=True)
        return out[:top_k]


def core_terms(engine, dream: str) -> list:
    """该梦的「核心元素词」：规则层命中模式名 + keywords[0]（机械判定，非人工）。"""
    terms = []
    for h in engine.match_patterns(dream):
        name = h["rule"]["name"]
        if len(name) >= 2:
            terms.append(name)
    kws = engine._extract_keywords(dream)
    if kws and len(kws[0]) >= 2:
        terms.append(kws[0])
    if not terms:
        # 无规则/关键词命中：退化为最长实词（仍机械）
        terms = [w for w in re.findall(r"[一-鿿]{2,}", dream)][:3]
    return list(dict.fromkeys(terms))[:4]


def is_relevant(text: str, terms: list) -> bool:
    return any(t in text for t in terms) if terms else False


def run_config(name: str, engine, retriever, queries: list, use_patterns: bool) -> dict:
    from src.engines.dream import DreamEngine
    if not use_patterns:
        engine.match_patterns = lambda text, top_n=5: []   # 模拟改前（无规则层）
    rows = []
    for q in queries:
        dream = q["text"]
        terms = core_terms(engine, dream)
        t0 = time.time()
        try:
            res = engine.analyze(dream, retriever)
        except Exception as e:
            log(f"[bench] {name} 分析失败：{type(e).__name__}: {e}")
            continue
        interps = list(res.interpretations or [])
        n_rel = sum(1 for t in interps if is_relevant(t, terms))
        rows.append({
            "dream": dream[:60],
            "n_results": len(interps),
            "n_relevant": n_rel,
            "top1_relevant": bool(interps) and is_relevant(interps[0], terms),
            "type_nonempty": bool(res.dream_type),
            "symbols_nonempty": bool(res.symbols),
            "tones_nonempty": bool(getattr(res, "tones", [])),
            "rule_hits": list(getattr(res, "rule_hits", []) or []),
            "terms": terms,
            "elapsed_s": round(time.time() - t0, 2),
        })
    n = len(rows) or 1
    return {
        "config": name,
        "dreams": len(rows),
        "hit_rate": round(sum(1 for r in rows if r["n_relevant"] > 0) / n, 4),
        "avg_results": round(sum(r["n_results"] for r in rows) / n, 2),
        "avg_relevant": round(sum(r["n_relevant"] for r in rows) / n, 2),
        "top1_relevant_rate": round(sum(1 for r in rows if r["top1_relevant"]) / n, 4),
        "three_fields_rate": round(sum(1 for r in rows
                                      if r["type_nonempty"] and r["symbols_nonempty"]
                                      and r["tones_nonempty"]) / n, 4),
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="解梦检索命中率对比（改前/改后）")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--vectordb", default=None, help="生产库目录（默认取 settings）")
    ap.add_argument("--k55-vectordb", default="/mnt/d/fortune-data/vectordb_k55",
                    help="k55 独立库目录（与生产库物理隔离）")
    args = ap.parse_args()

    queries = build_queries(args.n)
    log(f"[bench] 梦例 {len(queries)} 条（已冻结）")

    from src.config import load_settings
    from src.engines.dream import DreamEngine
    from src.rag.embedder import Embedder
    from src.rag.retriever import Retriever

    settings = load_settings()
    vectordb = args.vectordb or str(settings.vectordb_dir)
    embedder = Embedder(model_name="BAAI/bge-m3")
    embedder.load()

    prod = Retriever(vectordb, embedder, collection_name=PROD_COLLECTION)
    log(f"[bench] 生产集合 {PROD_COLLECTION}: {prod.count()} 条")
    try:
        k55 = Retriever(args.k55_vectordb, embedder, collection_name=K55_COLLECTION)
        n_k55 = k55.count()
    except Exception as e:
        log(f"[bench] k55 集合不可用（{e}）→ 只跑改前档")
        k55, n_k55 = None, 0
    log(f"[bench] k55 集合 {K55_COLLECTION}: {n_k55} 条")

    configs = [
        ("A_改前（旧引擎+旧语料）", DreamEngine(), prod, False),
        ("B_新引擎+旧语料", DreamEngine(), prod, True),
    ]
    if k55 and n_k55:
        configs.append(("C_改后（新引擎+旧语料+k55语料）", DreamEngine(),
                        UnionRetriever([prod, k55]), True))

    results = [run_config(name, eng, rt, queries, use_patterns)
               for name, eng, rt, use_patterns in configs]

    out = {"generated_at": now_iso(), "queries": len(queries),
           "prod_collection": PROD_COLLECTION, "prod_count": prod.count(),
           "k55_collection": K55_COLLECTION, "k55_count": n_k55,
           "results": results}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    log("=" * 70)
    for r in results:
        log(f"{r['config']}: 命中率={r['hit_rate']:.1%} "
            f"top1相关率={r['top1_relevant_rate']:.1%} "
            f"平均相关条数={r['avg_relevant']} 三项非空率={r['three_fields_rate']:.1%}")
    log(f"[bench] 明细 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
