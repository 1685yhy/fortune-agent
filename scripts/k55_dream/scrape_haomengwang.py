#!/usr/bin/env python3
"""P1 适配器：好梦网（www.haomengwang.net，周公解梦词典式条目）。

站点结构（2026-09-18 实测，纯静态 HTML，UTF-8）：
- 分类页：`/{cat}/`、`/{cat}/index_{N}.html`（N≥2），每页 ~30 条详情链接
- 详情页：`/{cat}/{id}.html`
    <div class="listread">
      <h1>【梦见X】</h1>
      <div class="read2" id="art_show">
        <p><strong>梦见X：</strong>现代心理/民俗解读</p>
        <p><strong>梦见X原版周公解梦</strong></p><p>龙蛇入门主得财…（古籍原文）</p>
        <p><strong>梦见X的案例解析</strong></p><p>梦境：…／解梦：…</p>
      </div>
    </div>

产出内容保留三块（现代解读 / 原版周公解梦 / 案例解析），入库时可据此分层；
其中「原版周公解梦」段为古籍引文，可与公版古籍互相印证。

用法：
    python scripts/k55_dream/scrape_haomengwang.py --phase index   # 先枚举详情 URL
    python scripts/k55_dream/scrape_haomengwang.py --phase detail --max-items 3000
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bs4 import BeautifulSoup  # noqa: E402

from scripts.k55_dream.crawl_lib import (  # noqa: E402
    DATA_ROOT, Checkpoint, JsonlWriter, PoliteFetcher, SiteLock, clean_content, now_iso,
)

SITE = "haomengwang"
BASE = "https://www.haomengwang.net"
URLQ = DATA_ROOT / "raw" / "haomengwang_urls.txt"
DETAIL_OUT = DATA_ROOT / "raw" / "haomengwang_detail.jsonl"
CKPT_INDEX = DATA_ROOT / "checkpoints" / "haomengwang_index.json"
CKPT_DETAIL = DATA_ROOT / "checkpoints" / "haomengwang_detail.json"
PROGRESS = DATA_ROOT / "logs" / "haomengwang_progress.log"

CATEGORIES = [
    "dongwu", "renwu", "shenghuo", "guishen", "ganqing", "jianzhu", "ziran",
    "zhiwu", "wupin", "qita", "yunfujiemeng", "yuanbanjiemeng",
    "mengyujiankang", "mengjingjiexi", "jiemengbaike", "dymuying",
    "shiyeceshi", "caifuceshi", "xinggeceshi", "aiqingceshi", "jiemengdashi",
    "chouqian",
]

_TITLE_SUFFIX_RE = re.compile(
    r"(是怎么回事|是什么意思|是什么预兆|代表着什么|代表什么|意味着什么|"
    r"怎么回事|怎么办|有什么预兆|什么征兆|好不好|的意思)[？?]?$"
)


def log(msg: str) -> None:
    line = f"{now_iso()} {msg}"
    print(line, flush=True)
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def norm_title(title: str) -> str:
    t = (title or "").strip().replace("【", "").replace("】", "")
    return _TITLE_SUFFIX_RE.sub("", t).strip()


# ── 分类页：抽详情链接 ──────────────────────────────────────────────
def parse_index(html: str, cat: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.fullmatch(rf"/{cat}/(\d+)\.html", href)
        if m:
            urls.add(f"{BASE}{href}")
    return sorted(urls)


def crawl_index(fetcher: PoliteFetcher, ckpt: Checkpoint, max_pages: int) -> int:
    total = 0
    seen_urls = set()
    if URLQ.exists():
        seen_urls = {l.strip() for l in URLQ.read_text(encoding="utf-8").splitlines() if l.strip()}
    for cat in CATEGORIES:
        empty = 0
        stale = 0
        for n in range(1, max_pages + 1):
            key = f"{cat}:{n}"
            if ckpt.is_done(key):
                continue
            url = f"{BASE}/{cat}/" if n == 1 else f"{BASE}/{cat}/index_{n}.html"
            html = fetcher.get(url, required_prefix=f"{BASE}/{cat}/")
            ckpt.mark(key)
            if html is None:
                empty += 1
                ckpt.save(**fetcher.stats.as_dict())
                if empty >= 5:
                    log(f"[index] {cat} 连续失败 {empty} 次，跳到下一分类")
                    break
                continue
            urls = parse_index(html, cat)
            new = [u for u in urls if u not in seen_urls]
            if not urls:
                empty += 1
                if empty >= 3:
                    log(f"[index] {cat} 第 {n} 页无链接 → 分类结束")
                    break
                continue
            empty = 0
            # 分页到底后站点常返回重复页（链接都见过）：连续 3 页无新链接即收尾，
            # 否则会在同一分类上空转到 max_pages（实测 dongwu 第 250 页起重复）。
            stale = stale + 1 if not new else 0
            if stale >= 3:
                log(f"[index] {cat} 连续 {stale} 页无**新**链接（重复页）→ 分类结束于第 {n} 页")
                break
            seen_urls.update(urls)
            with open(URLQ, "a", encoding="utf-8") as f:
                f.write("\n".join(new) + ("\n" if new else ""))
            total += len(new)
            if n % 10 == 0:
                ckpt.save(**fetcher.stats.as_dict())
                log(f"[index] {cat} n={n} 累计新 URL {total}")
        ckpt.save(**fetcher.stats.as_dict())
        log(f"[index] 分类 {cat} 完成，累计 URL {len(seen_urls)}")
    return total


# ── 详情页 ──────────────────────────────────────────────────────────
def parse_detail(html: str):
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = norm_title(h1.get_text(strip=True)) if h1 else ""
    box = soup.find("div", id="art_show") or soup.find("div", class_="read2")
    if not box:
        return title, ""
    for tag in box.find_all(["script", "style", "ins", "iframe"]):
        tag.decompose()
    text = box.get_text("\n", strip=True)
    return title, clean_content(text, min_len=15)


def crawl_detail(fetcher: PoliteFetcher, ckpt: Checkpoint, max_items: int) -> int:
    writer = JsonlWriter(DETAIL_OUT, SITE, log=log)
    if not URLQ.exists():
        log("[detail] URL 队列不存在，请先跑 --phase index")
        return 0
    urls = [l.strip() for l in URLQ.read_text(encoding="utf-8").splitlines() if l.strip()]
    log(f"[detail] URL 队列 {len(urls)} 条")
    added = tried = 0
    for url in urls:
        if ckpt.is_done(url):
            continue
        if tried >= max_items:
            log(f"[detail] 达本次上限 {max_items}，停止（断点已存）")
            break
        tried += 1
        html = fetcher.get(url, required_prefix=f"{BASE}/")
        if html is None:
            ckpt.mark(url)
            continue
        title, content = parse_detail(html)
        ckpt.mark(url)
        if not title or not content:
            continue
        cat = url.rstrip("/").split("/")[-2]
        writer.write(title=title, content=content, url=url, source=SITE,
                     category=cat, partial=False)
        added += 1
        if tried % 20 == 0:
            writer.flush()
            ckpt.save(**fetcher.stats.as_dict())
            log(f"[detail] {tried}/{max_items}（新入 {added}）")
    writer.flush()
    ckpt.save(**fetcher.stats.as_dict())
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description="好梦网（haomengwang.net）适配器")
    ap.add_argument("--phase", choices=["index", "detail"], default="detail")
    ap.add_argument("--max-pages", type=int, default=400)
    ap.add_argument("--max-items", type=int, default=3000)
    ap.add_argument("--delay", type=float, default=1.2)
    args = ap.parse_args()

    for d in ("raw", "checkpoints", "logs"):
        DATA_ROOT.joinpath(d).mkdir(parents=True, exist_ok=True)

    lock = SiteLock(DATA_ROOT / "checkpoints" / "haomengwang.lock", log=log)
    if not lock.acquire():
        return 3

    with PoliteFetcher(BASE, delay=args.delay, log=log) as fetcher:
        log("=" * 60)
        log(f"好梦网 phase={args.phase} delay={fetcher.delay}s")
        for n in fetcher.gate.notes:
            log(f"[robots] {n}")
        if args.phase == "index":
            ckpt = Checkpoint(CKPT_INDEX, log=log)
            n = crawl_index(fetcher, ckpt, args.max_pages)
            log(f"[index] 完成：本次新增 URL {n}")
        else:
            ckpt = Checkpoint(CKPT_DETAIL, log=log)
            n = crawl_detail(fetcher, ckpt, args.max_items)
            log(f"[detail] 完成：本次新增 {n} 条，累计 {len(ckpt.done)} URL")
        log(f"[stats] {fetcher.stats.as_dict()}")
    lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
