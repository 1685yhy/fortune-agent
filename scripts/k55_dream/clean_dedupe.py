#!/usr/bin/env python3
"""清洗 / 去重 / 合并 / 分类 —— 把多源抓取产物整理成可入库语料（brief §1.1）。

规则：
1. **清洗**：去导航/广告/免责声明噪音、修编码残留、最短长度过滤
   （第三方条目 ≥20 字；公版条文 ≥6 字，古籍判词本就短）；
2. **同源去重**：同一 title 只留一条，优先「完整详情页」> 「列表页片段」，
   同等则留内容更长的；
3. **跨站合并**：不同站点抓到同一条目时合并为一条，记录全部来源站
   （sources 字段），正文取最长；
4. **分类统计**：公版古籍 vs 第三方 分开计数（第三方现代解读商用版权不确定，
   供控制方拍板），并给元素分布。

输出：
- clean/dream_corpus.jsonl     可入库语料（含 corpus_class / sources 字段）
- reports/clean_stats.json     统计报告

用法：
    python scripts/k55_dream/clean_dedupe.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream.crawl_lib import (  # noqa: E402
    DATA_ROOT, JsonlWriter, clean_content, now_iso, read_jsonl,
)

RAW = DATA_ROOT / "raw"
CLEAN = DATA_ROOT / "clean"
REPORTS = DATA_ROOT / "reports"
OUT = CLEAN / "dream_corpus.jsonl"
REPORT = REPORTS / "clean_stats.json"

PUBLIC_DOMAIN = "public_domain"
THIRD_PARTY = "third_party"

# 各抓取产物的元信息（来源站 / 版权分类 / 是否片段）
SOURCES = {
    "sosuo_meng_detail.jsonl": {"site": "sosuo.name(佛滔·梦境百科)", "cls": THIRD_PARTY, "partial": False},
    "sosuo_meng_list.jsonl": {"site": "sosuo.name(佛滔·梦境百科·列表页)", "cls": THIRD_PARTY, "partial": True},
    "haomengwang_detail.jsonl": {"site": "haomengwang.net(好梦网)", "cls": THIRD_PARTY, "partial": False},
    "public_domain_quotes.jsonl": {"site": "公版古籍引文", "cls": PUBLIC_DOMAIN, "partial": False},
}
MIN_LEN = {PUBLIC_DOMAIN: 6, THIRD_PARTY: 20}

TITLE_KEY_RE = re.compile(r"[^\w一-鿿]+")


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


def norm_key(title: str) -> str:
    t = re.sub(r"^(梦见|梦到|梦见了|梦到了)", "", (title or "").strip())
    return TITLE_KEY_RE.sub("", t)[:24]


def load_all() -> list:
    recs = []
    for fname, meta in SOURCES.items():
        p = RAW / fname if (RAW / fname).exists() else CLEAN / fname
        if not p.exists():
            log(f"[skip] 无产物 {fname}")
            continue
        n = 0
        for r in read_jsonl(p):
            title = (r.get("title") or "").strip()
            content = r.get("content") or ""
            cls = r.get("corpus_class") or meta["cls"]
            min_len = MIN_LEN.get(cls, 20)
            content = clean_content(content, min_len=min_len)
            if not title or not content:
                continue
            recs.append({
                "title": title, "content": content,
                "source_site": r.get("source") or meta["site"],
                "site_label": meta["site"],
                "url": r.get("url", ""),
                "corpus_class": cls,
                "book": r.get("book", ""),
                "volume": r.get("volume", ""),
                "element": r.get("element", ""),
                # 溯源字段必须透传（控制方 2026-09-18：每条数据 → 哪个源）
                "provenance": r.get("provenance", ""),
                "source_file": r.get("source_file", ""),
                "source_entry": r.get("source_entry", ""),
                "partial": bool(r.get("partial", meta["partial"])),
                "category": r.get("category", ""),
            })
            n += 1
        log(f"[load] {fname}: {n} 条")
    return recs


def merge(recs: list) -> tuple:
    """同 title 去重 + 跨站合并。返回 (merged, stats)。"""
    by_key = {}
    dropped_short = 0
    for r in recs:
        k = norm_key(r["title"])
        if not k:
            dropped_short += 1
            continue
        cur = by_key.get(k)
        if cur is None:
            by_key[k] = r
            continue
        # 合并：正文取长、来源并集、partial 取更完整的一方
        sites = set(cur.get("sources") or [cur["site_label"]]) | {r["site_label"]}
        if len(r["content"]) > len(cur["content"]):
            keep, other = r, cur
        else:
            keep, other = cur, r
        keep = dict(keep)
        keep["sources"] = sorted(sites)
        keep["partial"] = keep["partial"] and other["partial"]
        keep["urls"] = sorted({u for u in (cur.get("url", ""), r.get("url", "")) if u})
        # 公版优先：同名条目若一方为公版条文，分类以公版为准
        if cur["corpus_class"] == PUBLIC_DOMAIN or r["corpus_class"] == PUBLIC_DOMAIN:
            keep["corpus_class"] = PUBLIC_DOMAIN
        by_key[k] = keep
    merged = list(by_key.values())
    for r in merged:
        r.setdefault("sources", [r["site_label"]])
        r.setdefault("urls", [r["url"]] if r["url"] else [])
    return merged, {"dup_merged": len(recs) - len(merged), "dropped": dropped_short}


def main() -> int:
    ap = argparse.ArgumentParser(description="解梦语料清洗去重")
    ap.add_argument("--min-content", type=int, default=20)
    args = ap.parse_args()

    CLEAN.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()

    recs = load_all()
    log(f"[clean] 载入 {len(recs)} 条")
    merged, mstats = merge(recs)
    log(f"[clean] 去重合并后 {len(merged)} 条（合并掉 {mstats['dup_merged']}）")

    writer = JsonlWriter(OUT, source="dream_corpus", log=log)
    by_class = Counter()
    by_site = Counter()
    elem = Counter()
    for r in merged:
        by_class[r["corpus_class"]] += 1
        for s in r.get("sources", []):
            by_site[s] += 1
        e = (r.get("element") or norm_key(r["title"])).strip()
        if e:
            elem[e[:6]] += 1
        writer.write(title=r["title"], content=r["content"], url=r["url"],
                     source=r["source_site"], corpus_class=r["corpus_class"],
                     book=r.get("book", ""), volume=r.get("volume", ""),
                     element=r.get("element", ""), sources=r.get("sources", []),
                     urls=r.get("urls", []), partial=r.get("partial", False),
                     category=r.get("category", ""),
                     provenance=r.get("provenance", ""),
                     source_file=r.get("source_file", ""),
                     source_entry=r.get("source_entry", ""))
    writer.flush()

    stats = {
        "generated_at": now_iso(),
        "raw_records": len(recs),
        "after_dedupe": len(merged),
        "merged_duplicates": mstats["dup_merged"],
        "by_corpus_class": dict(by_class),
        "public_domain_entries": by_class.get(PUBLIC_DOMAIN, 0),
        "third_party_entries": by_class.get(THIRD_PARTY, 0),
        "by_site": dict(by_site),
        "top_elements": elem.most_common(30),
        "copyright_note": (
            "第三方站点（sosuo.name / haomengwang.net）的现代解读文本版权归属不确定，"
            "商用有风险；公版古籍引文无版权风险。两类已用 corpus_class 字段分开标注，"
            "供控制方决定入库范围。"),
    }
    REPORT.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[clean] 落盘 {writer.written} 条 → {OUT}")
    log(f"[clean] 公版 {stats['public_domain_entries']} 条 / "
        f"第三方 {stats['third_party_entries']} 条")
    for k, v in by_site.most_common():
        log(f"    {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
