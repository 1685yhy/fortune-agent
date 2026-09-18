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
# 书名标注的识别改由 split_line_quotes() 完成（一行可含多条引文，见该函数说明）
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


BOOK_MARK_RE = re.compile(r"《(敦煌本梦书|梦林玄解|断梦秘书|周公解梦)》")


def split_line_quotes(line: str) -> list:
    """把一行按**完整的书名标注**切成多条引文：返回 [(book, quote), ...]。

    为什么不能整行当一条（M-2 实测错误）：
    页面里多条引文常挤在同一行，整行匹配会把**两条不同书的引文粘成一条**，
    例如「梦见蛇咬人，妻必生子。《敦煌本梦书》梦见蛇咬人家者，母丧。（《敦煌本梦书》）」
    被当成一条，书名归属也就跟着错。改为：以每个完整的 `《书名》` 为切点，
    取其**前面**那段文本作为该书的引文；行尾不完整的 `（《敦煌` 之类碎片
    （页面折行造成）直接丢弃，不做拼接猜测。
    """
    marks = list(BOOK_MARK_RE.finditer(line))
    out = []
    if not marks:
        return out
    prev_end = 0
    for m in marks:
        seg = line[prev_end:m.start()]
        # 去掉切点前的残留括注/括号（上一条书名的尾巴）
        seg = re.sub(r"[（(]\s*$", "", seg.strip())
        seg = seg.strip("。，、;；:： \t")
        if seg:
            out.append((m.group(1), seg))
        prev_end = m.end()
    return out


def extract_from_corpus() -> list:
    """从既有语料抽带书名标注的公版引文（行级）。"""
    out, seen = [], set()
    for fname, entry in iter_corpus_entries():
        em = ENTRY_KEY_RE.match(entry)
        entry_key = em.group(1).strip() if em else ""
        entry_key = re.sub(r"(的含义|的意思|好不好|是什么|怎么样)$", "", entry_key)
        for line in entry.split("\n"):
            for book, raw_quote in split_line_quotes(line):
                quote = clean_quote(raw_quote)
                if len(quote) < 5:
                    continue
                # 剔除非条文（现代解读句里偶然带书名括注）。
                # 注意：「梦见…」是古籍条文的**正常起式**（如「梦见哀泣，
                # 有庆贺事。《敦煌本梦书》」），绝不能因为含「梦见」就丢——
                # 初版误剔导致敦煌引文只剩 9 条，实为过滤器写错。
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
                    # 溯源字段（控制方 2026-09-18 要求「每条数据 → 哪个源」）：
                    # 源文件全路径 + 所在语料条目首行（页级 URL 见 audit_provenance.py）
                    "source_file": str(CORPUS_DIR / fname),
                    "source_entry": entry.split("\n")[0][:80],
                    "source_url": "",
                    "provenance_note": "第三方页面引用的古籍条文，原文转录未校勘",
                })
    return out


# 上游文件里被机械加过「梦见」前缀的畸形行：`梦见见X` / `梦见看见X`
MALFORMED_PREFIX_RE = re.compile(r"^梦见(见|看见)")


def extract_local_zhougong() -> list:
    """本地已有的《周公解梦》古典原文文件（**转录文本，未校勘**）。

    两个文件的取得路径与局限（控制方 2026-09-18 要求如实标注）：
    - `zonghe/周公解梦_classical.txt`：头部自述「来源：dreamlogic-mcp（结构化古籍
      数据）」，**未给 URL、未说明版本**；文件格式为成对的
      `梦见X：<判词>` / `  原文：X：<判词>`，其中前者是**上游机械加过前缀**的产物
      （实测 29 行产出 `梦见见做新旗`、`梦见见人死自死` 这类畸形条目）。
      → 本函数改取 **`原文：` 行**（文件自带的规范形态、逐字可验），
        从根上消灭机械前缀造成的畸形，而不是事后字符串修补。
    - `yijing/周公解梦.txt`：**无任何来源声明**，歌诀体（单行多句）。
      只做逐行摘录，不做任何改写。
    """
    out, seen = [], set()

    # ① 结构化文件：取「原文：」行（规范形态，无机械前缀）
    p1 = BOOKS_DIR / "zonghe" / "周公解梦_classical.txt"
    if p1.exists():
        text = p1.read_text(encoding="utf-8", errors="replace")
        n_before = len(out)
        for line in text.split("\n"):
            s = line.strip()
            if not s.startswith("原文："):
                continue
            quote = s[len("原文："):].strip()
            if not (4 <= len(quote) <= 120):
                continue
            if MALFORMED_PREFIX_RE.match(quote):     # 兜底：规范行也不该有畸形前缀
                continue
            if not re.search(r"[主有宜忌吉凶]", quote):
                continue
            if quote in seen:
                continue
            seen.add(quote)
            out.append({
                "book": "周公解梦", "text": quote, "element": "",
                "source": f"local_book:{p1}",
                "volume": "转录文本（上游 dreamlogic-mcp，未给 URL，未校勘）",
                "source_file": str(p1),
                "source_entry": "原文：行（文件自带规范形态）", "source_url": "",
                "provenance_note": (
                    "项目既有文件（非本批创建）：成对条目「梦见X：判词／原文：X：判词」，"
                    "取原文行以避免上游机械前缀（梦见见X 类畸形）；未校勘"),
            })
        log(f"[local] {p1.name}: 取「原文：」行 {len(out)-n_before} 条")

    # ② 歌诀体文件：逐行摘录（无来源声明，如实标注）
    p2 = BOOKS_DIR / "yijing" / "周公解梦.txt"
    if p2.exists():
        text = p2.read_text(encoding="utf-8", errors="replace")
        n_before = len(out)
        for line in text.split("\n"):
            line = line.strip()
            if not line or len(line) < 6 or len(line) > 120:
                continue
            if line.startswith(("=", "来源", "共", "---", "【", "《", "原文：")):
                continue
            if not re.search(r"[主有宜忌吉凶]", line):
                continue
            if line.count("　") > 4:
                continue
            if MALFORMED_PREFIX_RE.match(line):
                continue
            if line in seen:
                continue
            seen.add(line)
            out.append({
                "book": "周公解梦", "text": line, "element": "",
                "source": f"local_book:{p2}", "volume": "通行本（歌诀体）",
                "source_file": str(p2),
                "source_entry": "", "source_url": "",
                "provenance_note": "项目既有文件（非本批创建）：**无来源声明**、未校勘",
            })
        log(f"[local] {p2.name}: 逐行摘录 {len(out)-n_before} 条")
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
        # 出处标注口径（控制方 r2 裁决）：这是**古籍引文（转录，未校勘）**，
        # 不是「《X》原文·某版本」，措辞上不许暗示做过版本校勘。
        vol = (r.get("volume") or "").strip()
        mark = f"《{r['book']}》" + (f"·{vol}" if vol else "")
        content = f"{r['text']}。（{mark}）"
        writer.write(title=title, content=content, url=r.get("source_url", ""),
                     book=r["book"], volume=r.get("volume", ""),
                     element=r.get("element", ""), provenance=r["source"],
                     source_file=r.get("source_file", ""),
                     source_entry=r.get("source_entry", ""),
                     provenance_note=r.get("provenance_note", ""),
                     corpus_class="public_domain")
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
