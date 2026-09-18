#!/usr/bin/env python3
"""来源链审计：逐条证明「公版古籍条文」从哪来（控制方 2026-09-18 质询）。

回答四个问题（每条数据 → 哪个源）：
1. **来源种类**：corpus_quote（从既有语料 12880/zgjmorg 的正文里抽出的带书名引文）
   / local_book（项目既有的本地古籍文件）；
2. **逐字校验**：把记录文本按空白归一化后，回原文件里找**逐字包含**关系
   ——证明这批文本是**抄出来的**，不是写出来的；
3. **URL 溯源**：corpus_quote 记录回到「所在语料条目」，并用 24 字精确 shingle
   到 dreams_verified.jsonl / dreams_scraped_verified.jsonl 里找 **source_url**
   （精确文本匹配，不做模糊归因）；
4. **无法溯源的部分如实列出**（本地古籍文件的**上游出处**是项目历史数据，
   本批无法进一步证实）。

产出：reports/provenance_audit.json（逐条结论）+ 控制台抽样。
用法：
    python scripts/k55_dream/audit_provenance.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream.crawl_lib import DATA_ROOT, now_iso  # noqa: E402

QUOTES = DATA_ROOT / "clean" / "public_domain_quotes.jsonl"
CORPUS_DIR = Path("/mnt/d/fortune-data/books/zonghe")
OUT = DATA_ROOT / "reports" / "provenance_audit.json"

WS_RE = re.compile(r"\s+")
SHINGLE = 24
# 已知 url 的语料文件（含 source_url 字段）
URL_SOURCES = ["dreams_verified.jsonl", "dreams_scraped_verified.jsonl"]


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


def norm(s: str) -> str:
    return WS_RE.sub("", s or "")


def shingles(text: str, k: int = SHINGLE, n: int = 6) -> list:
    t = norm(text)
    if len(t) < k:
        return [t] if t else []
    if len(t) == k:
        return [t]
    step = max(1, (len(t) - k) // (n - 1)) if n > 1 else 1
    out = []
    for i in range(0, len(t) - k + 1, step):
        out.append(t[i:i + k])
        if len(out) >= n:
            break
    return out


def valid_url(u: str) -> bool:
    """上游 dreams_verified.jsonl 的 source_url 字段**并非都是 URL**——
    实测有「周公解梦大全参考」「周公解梦专业委员会」这类中文串。
    只认 http(s):// 开头的值；首页（path 为空）另算「站点级」而非「页级」。
    """
    return bool(u) and u.lower().startswith(("http://", "https://"))


def url_level(u: str) -> str:
    if not valid_url(u):
        return "invalid"
    m = re.match(r"https?://[^/]+(/.*)?$", u)
    path = (m.group(1) or "/") if m else "/"
    return "site" if path in ("/", "") else "page"


ENTRY_SUFFIX_RE = re.compile(
    r"(的含义|的意思|好不好|是什么|怎么样|是怎么回事|是什么意思|的解析|解析|预兆|"
    r"有什么预兆|是什么预兆|代表什么|代表着什么)+$")


def entry_key(entry_first_line: str) -> str:
    """语料条目首行 → 条目名（梦见X… 的 X 部分），用于条目级 URL 匹配。"""
    s = (entry_first_line or "").split(":")[0].split("：")[0].strip()
    s = re.sub(r"^(梦见|梦到|梦见了|梦到了)", "", s)
    s = ENTRY_SUFFIX_RE.sub("", s).strip()
    return norm(s)[:12]


def build_url_index() -> tuple:
    """(shingle 索引, 标题索引)。

    - shingle 索引：24 字完整匹配 → 强证据（同一段文本）；
    - 标题索引：`梦见X` 条目名精确匹配 → 中等证据（同一**条目**的页面，
      不保证页面正文逐字一致），单独标注 url_match="title"。
    """
    sh_idx, title_idx = {}, {}
    for fname in URL_SOURCES:
        p = CORPUS_DIR / fname
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                url = d.get("source_url") or ""
                if not url:
                    continue
                rec = (url, (d.get("title") or "")[:50], d.get("source_quality", ""))
                for sh in shingles(d.get("content") or d.get("title") or ""):
                    sh_idx.setdefault(sh, rec)
                key = norm(re.sub(r"^(梦见|梦到)", "", (d.get("title") or "")))[:12]
                if key:
                    title_idx.setdefault(key, rec)
    return sh_idx, title_idx


def load_corpus_entries(fname: str) -> list:
    """载入语料条目（用于定位引文所在的条目）。"""
    p = CORPUS_DIR / fname
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    return [e.strip() for e in re.split(r"\n\n+", text) if e.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="公版古籍条文来源链审计")
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()

    recs = [json.loads(l) for l in QUOTES.read_text(encoding="utf-8").splitlines() if l.strip()]
    log(f"[audit] 待审计 {len(recs)} 条")

    # 源文件缓存（用于逐字校验）。
    # 路径解析顺序：记录里的 source_file 全路径 → provenance 里的路径 → 按 basename
    # 在已知目录里找（**初版只按 basename 去 zonghe/ 找，导致 yijing/ 下的
    # 周公解梦.txt 被判「缺失」→ 240 条误报无法逐字校验**，控制方质询后修正）。
    def resolve(path_hint: str):
        if not path_hint:
            return None
        p = Path(path_hint)
        if p.exists():
            return p
        for base in (CORPUS_DIR, Path("/mnt/d/fortune-data/books")):
            cand = base / path_hint
            if cand.exists():
                return cand
        cands = list(Path("/mnt/d/fortune-data/books").rglob(Path(path_hint).name))
        return cands[0] if cands else None

    src_cache, resolved_path = {}, {}
    hints = set()
    for r in recs:
        hints.add(r.get("source_file") or "")
        hints.add(r["provenance"].split(":", 1)[1])
    for h in hints:
        if not h:
            continue
        p = resolve(h)
        if p is None:
            log(f"[audit] 源文件无法定位：{h}")
            continue
        resolved_path[h] = str(p)
        src_cache[h] = norm(p.read_text(encoding="utf-8", errors="replace"))
        log(f"[audit] 源文件 {p}: 已载入 ({len(src_cache[h])} 字)")

    def src_of(rec) -> str:
        for h in (rec.get("source_file") or "", rec["provenance"].split(":", 1)[1]):
            if h in src_cache:
                return h
        return ""

    corpus_entries = {h: load_corpus_entries(Path(h).name)
                      for h in src_cache if Path(h).parent == CORPUS_DIR}
    sh_index, title_index = build_url_index()
    log(f"[audit] URL 索引：shingle {len(sh_index)} 个 / 标题 {len(title_index)} 个"
        f"（来源 {URL_SOURCES}）")

    rows = []
    for r in recs:
        kind, _ = r["provenance"].split(":", 1)
        srcfile = src_of(r)
        quote = norm(r["content"].split("。（《")[0])   # 去掉出处括注
        verbatim = bool(srcfile) and quote in src_cache.get(srcfile, "")
        row = {
            "book": r["book"], "element": r.get("element", ""),
            "text": r["content"], "origin_kind": kind,
            "source_file": srcfile or r.get("source_file", ""),
            "verbatim_in_source": verbatim,
            "source_entry": r.get("source_entry", ""),
            "source_url": r.get("source_url", ""),
            "url_match": "none", "url_quality": "",
        }
        if kind == "corpus_quote":
            if not row["source_entry"] and srcfile:
                for e in corpus_entries.get(srcfile, []):
                    if quote and quote in norm(e):
                        row["source_entry"] = e.split("\n")[0][:80]
                        break
            if not valid_url(row["source_url"]):
                row["source_url"], row["url_match"] = "", "none"
                for sh in shingles(quote):
                    cand = sh_index.get(sh)
                    if cand and valid_url(cand[0]):
                        row["source_url"], row["url_match"], row["url_quality"] = \
                            cand[0], "shingle24", cand[2]
                        break
            if not row["source_url"] and row["source_entry"]:
                # 退一档：按条目名（梦见X）精确匹配同一站点条目页
                key = entry_key(row["source_entry"])
                cand = title_index.get(key) if key else None
                if cand and valid_url(cand[0]):
                    row["source_url"], row["url_match"], row["url_quality"] = \
                        cand[0], "title", cand[2]
        row["url_level"] = url_level(row["source_url"])
        rows.append(row)

    by_book = defaultdict(Counter)
    for r in rows:
        by_book[r["book"]][r["origin_kind"]] += 1
        by_book[r["book"]]["verbatim_ok" if r["verbatim_in_source"] else "verbatim_FAIL"] += 1
        lvl = r.get("url_level", "none")
        by_book[r["book"]]["url_page" if lvl == "page" else
                           ("url_site_only" if lvl == "site" else "url_none")] += 1

    summary = {
        "generated_at": now_iso(),
        "total": len(rows),
        "verbatim_verified": sum(1 for r in rows if r["verbatim_in_source"]),
        "verbatim_failed": [r["text"][:60] for r in rows if not r["verbatim_in_source"]][:20],
        "by_book": {k: dict(v) for k, v in by_book.items()},
        "origin_kinds": dict(Counter(r["origin_kind"] for r in rows)),
        "source_files": dict(Counter(r["source_file"] for r in rows)),
        "url_page_level": sum(1 for r in rows if r.get("url_level") == "page"),
        "url_site_only": sum(1 for r in rows if r.get("url_level") == "site"),
        "url_none": sum(1 for r in rows if r.get("url_level") not in ("page", "site")),
        "url_match_kinds": dict(Counter(r["url_match"] for r in rows)),
        "notes": [
            "source_file 即直接来源：corpus_quote:* = 从既有爬取语料的正文字符串中抽出；"
            "local_book:* = 项目既有本地古籍文件（本批未改动其内容）",
            "verbatim_in_source=True 表示该条文本按空白归一化后能在源文件中逐字找到"
            "（证明是摘录而非撰写）",
            "source_url 仅对能匹配到 dreams_verified / dreams_scraped_verified 的"
            "source_url 字段的记录给出；匹配用 24 字完整 shingle（强）或条目名精确匹配"
            "（中），不做模糊归因。**上游 source_url 字段本身存在非 URL 值**"
            "（如「周公解梦大全参考」），已过滤，只认 http(s)://；其中指向站点首页的"
            "另计 url_site_only（站点级，非页级）。未解析出 URL 只说明「本批无法定位到"
            "页级」，不代表文本不实",
            "**上游局限**：local_book 的两份文件是项目历史数据（本批未创建），"
            "其中 周公解梦_classical.txt 自述来源为 dreamlogic-mcp（未给 URL），"
            "周公解梦.txt 无来源声明 —— 这两份文件的更上游出处本批无法证实",
            "**另一处局限**：corpus_quote 的文本是第三方页面（12880/zgjmorg）对古籍的"
            "引用转录，本批只能证明「我们如实摘录了该页面」，**无法证明该页面对古籍的"
            "转录本身准确无误**",
        ],
    }
    OUT.write_text(json.dumps({"summary": summary, "records": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")

    log("=" * 70)
    log(f"[audit] 逐字校验通过 {summary['verbatim_verified']}/{len(rows)}")
    for b, c in by_book.items():
        log(f"  {b}: {dict(c)}")
    log(f"[audit] URL：页级 {summary['url_page_level']} / 仅站点级 "
        f"{summary['url_site_only']} / 未解析 {summary['url_none']}；"
        f"匹配方式 {summary['url_match_kinds']}")
    log(f"[audit] 明细 → {OUT}")

    log("\n[audit] 抽样（每书 %d 条）:" % args.samples)
    shown = Counter()
    for r in rows:
        if shown[r["book"]] >= args.samples:
            continue
        shown[r["book"]] += 1
        log(f"  《{r['book']}》 {r['text'][:70]}")
        log(f"      来源种类={r['origin_kind']} 源文件={r['source_file']} "
            f"逐字校验={r['verbatim_in_source']}")
        log(f"      所在条目={r['source_entry'][:50] or '(文件级)'} "
            f"URL={r['source_url'] or '(本批无法定位到页级)'}"
            f" [{r.get('url_level')}/{r.get('url_match')}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
