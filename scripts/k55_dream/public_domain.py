#!/usr/bin/env python3
"""公版古籍采集（解梦）—— 只收「可溯源 + 公版」内容，拿不到的如实标注。

brief §1.2 要求：《敦煌本梦书》《梦林玄解》《断梦秘书》，入库标「书名 + 卷次」。

2026-09-18 本环境实测（见 reports/task-k55-report.md §公版古籍）：
- zh.wikisource.org（维基文库《梦林玄解》DjVu）：**不可达**（IPv4/IPv6 均超时）
- www.360doc.com（《敦煌本梦书》全文转载）：**不可达**
- archive.org（敦煌占卜文献 PDF）：**不可达**
- ctext.org：**Cloudflare Turnstile 人机校验** → 按红线不绕，弃用
- shidianguji.com（识典古籍 P.3908 新集周公解梦书）：页面 200，但正文由
  JS + argus 反爬 SDK 渲染，静态 HTML 无正文 → 按红线不绕，弃用

因此**不伪造缺失全文**，改为采集「**已被引用的公版条文**」：
既有语料（12880 / zgjmorg 页面）的「原版周公解梦」段落以
`<条文>。《书名》` 形式带书名标注引用古籍，本模块把这类条文抽出来，
按书名归档 → 可溯源、内容公版、并已标注书名（卷次多数不可考，如实留空）。

用法：
    python scripts/k55_dream/public_domain.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream.crawl_lib import DATA_ROOT, JsonlWriter, now_iso  # noqa: E402

CORPUS_DIR = Path("/mnt/d/fortune-data/books/zonghe")
BOOKS_DIR = Path("/mnt/d/fortune-data/books")

OUT = DATA_ROOT / "clean" / "public_domain_quotes.jsonl"
REPORT = DATA_ROOT / "reports" / "public_domain_stats.json"

# 公版古籍书名（唐末五代～明代，均远超版权保护期）
CLASSIC_BOOKS = ["敦煌本梦书", "梦林玄解", "断梦秘书", "周公解梦"]

# 语料里「原版/古籍」段落中的带书名标注引文（**以行为单位**，因为语料中
# 引文就是一行一条：`梦见哀泣，有庆贺事。《敦煌本梦书》`；按句号切会把
# 「梦见X，判词」的起式切断，实测只剩半句「乘行，大富。」这种残句）。
LINE_BOOK_RE = re.compile(
    r"^\s*(?P<quote>.{4,220}?)\s*[。\s]*"
    r"[《（(]\s*(?P<book>敦煌本梦书|梦林玄解|断梦秘书|周公解梦)\s*[》）)]?\s*$"
)
# 引文自带的「梦见X」起式 → 直接取作元素标签（比条目级 key 更准）
QUOTE_KEY_RE = re.compile(r"^梦见(.{1,16}?)[，,。：:]")
# 引文尾部常见噪音（页面自带「（由XX提供）」等）
TAIL_NOISE_RE = re.compile(r"[（(]\s*由.{0,12}提供\s*[)）]\s*$")

# 单条语料条目以「梦见X:」开头 → 取 X 作为该条引文的元素标签
ENTRY_KEY_RE = re.compile(r"^梦见(.{1,20}?)[:：]")


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


def iter_corpus_entries():
    """遍历既有语料条目（只读），产出 (source_file, entry_text)。"""
    for fname in ("12880_dreams.txt", "zgjmorg_dreams.txt"):
        p = CORPUS_DIR / fname
        if not p.exists():
            log(f"[skip] 语料不存在：{p}")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for raw in re.split(r"\n\n+", text):
            e = raw.strip()
            if e.startswith("梦见"):
                yield fname, e


def clean_quote(q: str) -> str:
    q = TAIL_NOISE_RE.sub("", q).strip()
    q = re.sub(r"\s+", "", q)          # 古籍条文内部不留空白（页面折行造成）
    q = q.strip("。，、;；:：")
    return q


def extract_from_corpus() -> list:
    """从既有语料抽带书名标注的公版引文（行级）。"""
    out, seen = [], set()
    for fname, entry in iter_corpus_entries():
        em = ENTRY_KEY_RE.match(entry)
        entry_key = em.group(1).strip() if em else ""
        entry_key = re.sub(r"(的含义|的意思|好不好|是什么|怎么样)$", "", entry_key)
        for line in entry.split("\n"):
            lm = LINE_BOOK_RE.match(line)
            if not lm:
                continue
            quote = clean_quote(lm.group("quote"))
            book = lm.group("book")
            if len(quote) < 5:
                continue
            # 剔除非条文（现代解读句里偶然带书名括注）。
            # 注意：「梦见…」是古籍条文的**正常起式**（如「梦见哀泣，有庆贺事。
            # 《敦煌本梦书》」），绝不能因为含「梦见」就丢——初版误剔导致敦煌
            # 引文只剩 9 条，实为过滤器写错。
            if any(x in quote for x in ("表示", "象征", "意味着", "预示你",
                                        "暗指", "提示", "说明", "反映", "因为",
                                        "所以你", "可能会", "要注意", "好不好",
                                        "是什么意思", "请看下面")):
                continue
            km = QUOTE_KEY_RE.match(quote)
            element = km.group(1).strip() if km else entry_key
            key = (book, quote)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "book": book, "text": quote, "element": element,
                "source": f"corpus_quote:{fname}", "volume": "",
            })
    return out


def extract_local_zhougong() -> list:
    """本地已有的公版《周公解梦》古典原文（歌诀体，古籍原文）。"""
    out, seen = [], set()
    for p in (BOOKS_DIR / "yijing" / "周公解梦.txt",
              BOOKS_DIR / "zonghe" / "周公解梦_classical.txt"):
        if not p.exists():
            log(f"[skip] 本地古籍不存在：{p}")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for line in text.split("\n"):
            line = line.strip()
            if not line or len(line) < 6 or len(line) > 120:
                continue
            if line.startswith(("=", "来源", "共", "---", "【", "《", "原文：", "梦见诸佛")):
                continue
            # 歌诀体：单行多句，含「主」「吉」「凶」「有」等判词标志
            if not re.search(r"[主有宜忌吉凶]", line):
                continue
            if line.count("　") > 4:
                continue
            key = line
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "book": "周公解梦", "text": line, "element": "",
                "source": f"local_book:{p.name}", "volume": "通行本（歌诀体）",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="公版解梦古籍引文采集")
    ap.add_argument("--limit-per-book", type=int, default=0, help="0=不限")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    # 本脚本按「全量重算」语义运行：每次从零写，避免与追加写叠加出现重复
    if OUT.exists():
        OUT.unlink()

    corpus_recs = extract_from_corpus()
    local_recs = extract_local_zhougong()
    log(f"[public-domain] 语料引文 {len(corpus_recs)} 条；本地公版原文 {len(local_recs)} 条")

    by_book = Counter(r["book"] for r in corpus_recs)
    by_source = Counter(r["source"] for r in corpus_recs)
    for b, n in by_book.most_common():
        log(f"  {b}: {n} 条引文")

    writer = JsonlWriter(OUT, source="public_domain", log=log)
    for r in corpus_recs + local_recs:
        # 统一 {title, content, source} 形状（与既有语料对齐）：
        # 古籍条目的 title = 条文前 40 字（检索命中时显示的就是判词本身），
        # content = 条文 + 书名卷次标注（引用可溯源，产品既有口径）。
        title = r["text"][:40]
        content = f"{r['text']}。（《{r['book']}》{r.get('volume') or ''}）".replace("》）", "》）")
        writer.write(title=title, content=content, url="", book=r["book"],
                     volume=r.get("volume", ""), element=r.get("element", ""),
                     provenance=r["source"], corpus_class="public_domain")
    writer.flush()

    stats = {
        "generated_at": now_iso(),
        "unavailable_sources": {
            "zh.wikisource.org（梦林玄解 DjVu）": "不可达：IPv4/IPv6 均超时",
            "www.360doc.com（敦煌本梦书全文）": "不可达：连接超时",
            "archive.org（敦煌占卜文献 PDF）": "不可达：连接超时",
            "ctext.org": "Cloudflare Turnstile 人机校验，按红线不绕",
            "shidianguji.com（识典古籍 P.3908）": "JS + argus 反爬渲染，静态页无正文，按红线不绕",
        },
        "quotes_from_corpus": len(corpus_recs),
        "local_classical_entries": len(local_recs),
        "by_book": dict(by_book),
        "by_source": dict(by_source),
        "note": "全文转录本在本环境不可得，仅采集「页面带书名标注的公版条文」，不伪造全文",
    }
    REPORT.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[public-domain] 落盘 {writer.written} 条 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
