#!/usr/bin/env python3
"""P0 适配器：佛滔·梦境百科（www.sosuo.name/meng/，站点自述收录 68,323 个梦境）。

站点性质：UGC 梦分享社区 —— **真实网友梦境描述 + 佛滔解梦**（与词典式条目
互补：真实梦境是元素统计与「现实投影」口径最直接的语料）。

站点结构（2026-09-18 实测）：
- 列表页：`/meng/p{N}`（N 从 1 起，一直翻到无条目为止）
    <div class="menglist"><dl>
      <dt><span><a href="/meng/{id}" title="梦见X">梦见X</a></span>{日期}</dt>
      <dd>{梦境片段}... ({地区}网友提供梦境)</dd>
    </dl></div>
- 详情页：`/meng/{id}`
    <h1>梦见X是怎么回事？</h1>
    <div class="article_content info_area">
      <h2>1. 梦境内容：</h2><div class="h2_content">…</div>
      <h2>2. 梦境图释：</h2><div class="h2_content">…</div>
      <h2>3. 佛滔解梦：</h2><div class="h2_content">…</div>
    </div>
- 编码：GBK（HTTP 头声明 ISO-8859，需走 crawl_lib.decode_html 修复）

三阶段（各自断点续爬）：
- phase=list   ：翻列表页，产出 {title, content=片段, url, id, date, region, partial}
- phase=detail ：按 id 从新到旧抓详情页，产出完整 {title, content=梦境内容+佛滔解梦}
- phase=walk   ：详情页「上一篇/下一篇」构成一条**可遍历的双向链表**（实测
  990361→990360→990358→990354→990353→990349→990347…），沿 `下一篇`
  反向遍历即可覆盖全部现存条目。列表页实测只公开 20 页（p21 起为空壳页），
  因此 walk 是本站在「列表页 200 条」之外取得数万条的唯一合规途径。
  frontier 落盘在断点文件里，中断可续。

用法：
    python scripts/k55_dream/scrape_sosuo.py --phase list
    python scripts/k55_dream/scrape_sosuo.py --phase detail --max-items 2000
    python scripts/k55_dream/scrape_sosuo.py --phase walk --max-items 20000
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bs4 import BeautifulSoup  # noqa: E402

from scripts.k55_dream.crawl_lib import (  # noqa: E402
    DATA_ROOT, Checkpoint, JsonlWriter, PoliteFetcher, SiteLock, clean_content, now_iso,
)

SITE = "sosuo_12880_meng"
BASE = "https://www.sosuo.name"
MENG = f"{BASE}/meng/"

LIST_OUT = DATA_ROOT / "raw" / "sosuo_meng_list.jsonl"
DETAIL_OUT = DATA_ROOT / "raw" / "sosuo_meng_detail.jsonl"
CKPT_LIST = DATA_ROOT / "checkpoints" / "sosuo_list.json"
CKPT_DETAIL = DATA_ROOT / "checkpoints" / "sosuo_detail.json"
CKPT_WALK = DATA_ROOT / "checkpoints" / "sosuo_walk.json"
SEEDS = DATA_ROOT / "raw" / "sosuo_seeds.txt"
PROGRESS = DATA_ROOT / "logs" / "sosuo_progress.log"

_PREV_RE = re.compile(r'上一篇：<a href="/meng/(\d+)"')
_NEXT_RE = re.compile(r'下一篇：<a href="/meng/(\d+)"')

_TITLE_SUFFIX_RE = re.compile(
    r"(是怎么回事|是什么意思|是什么预兆|代表着什么|代表什么|意味着什么|"
    r"怎么回事|怎么办|有什么预兆|什么征兆|好不好|的意思)[？?]?$"
)
_REGION_RE = re.compile(r"\(([^()]{0,12}网友提供梦境)\)")


def log(msg: str) -> None:
    line = f"{now_iso()} {msg}"
    print(line, flush=True)
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def norm_title(title: str) -> str:
    t = (title or "").strip()
    t = t.replace("【", "").replace("】", "")
    t = _TITLE_SUFFIX_RE.sub("", t).strip()
    return t


# ── 列表页解析 ──────────────────────────────────────────────────────
def parse_list(html: str):
    """返回 (entries, next_page_exists)。每个 entry:
    {id, title, snippet, date, region, url}"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for dl in soup.select("div.menglist dl"):
        a = dl.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        m = re.search(r"/meng/(\d+)", href)
        if not m:
            continue
        eid = m.group(1)
        title = norm_title(a.get("title") or a.get_text(strip=True))
        dt_text = dl.find("dt").get_text(" ", strip=True) if dl.find("dt") else ""
        dm = re.search(r"(\d{4}-\d{2}-\d{2}[ \d:]*)", dt_text)
        dd = dl.find("dd")
        snippet = dd.get_text(" ", strip=True) if dd else ""
        rm = _REGION_RE.search(snippet)
        out.append({
            "id": eid,
            "title": title,
            "snippet": _REGION_RE.sub("", snippet).strip().rstrip(".。…"),
            "date": dm.group(1).strip() if dm else "",
            "region": rm.group(1) if rm else "",
            "url": f"{MENG}{eid}",
        })
    return out


# ── 详情页解析 ──────────────────────────────────────────────────────
def parse_detail(html: str):
    """返回 (title, content)；解析失败返回 ("", "")。"""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if not h1:
        return "", ""
    title = norm_title(h1.get_text(strip=True))
    box = soup.find("div", class_="article_content")
    if not box:
        return title, ""

    dream, interp, other = "", "", []
    for h2 in box.find_all("h2"):
        head = h2.get_text(strip=True)
        body = h2.find_next_sibling("div")
        if body is None:
            continue
        body = BeautifulSoup(str(body), "html.parser")
        for tag in body.find_all(["script", "style", "ins", "iframe", "img"]):
            tag.decompose()
        text = body.get_text("\n", strip=True)
        if "梦境内容" in head or "梦境描述" in head:
            dream = text
        elif "解梦" in head or "解析" in head:
            interp = text
        else:
            other.append(text)

    parts = []
    if dream:
        parts.append(f"梦境内容：{dream}")
    if interp:
        parts.append(f"佛滔解梦：{interp}")
    if not parts and other:
        parts = other
    return title, clean_content("\n".join(parts), min_len=15)


# ── 阶段一：列表页 ──────────────────────────────────────────────────
def crawl_list(fetcher: PoliteFetcher, ckpt: Checkpoint, max_pages: int) -> int:
    writer = JsonlWriter(LIST_OUT, SITE, log=log)
    page = ckpt.cursor
    empty_streak = 0
    added = 0
    while page < max_pages:
        page += 1
        if ckpt.is_done(f"p{page}"):
            continue
        url = f"{MENG}p{page}" if page > 1 else MENG
        ckpt.cursor = page
        html = fetcher.get(url, required_prefix=MENG)
        if html is None:
            empty_streak += 1
            log(f"[list] p{page} 抓取失败（{empty_streak} 连败）")
            ckpt.save(**fetcher.stats.as_dict())
            if empty_streak >= 8:
                log("[list] 连续失败过多，停止（断点已存，可续跑）")
                break
            continue
        entries = parse_list(html)
        if not entries:
            empty_streak += 1
            log(f"[list] p{page} 无条目（{empty_streak} 连空）")
            ckpt.save(**fetcher.stats.as_dict())
            if empty_streak >= 5:
                log(f"[list] 连续 {empty_streak} 页无条目 → 判定列表到底，停止")
                break
            continue
        empty_streak = 0
        for e in entries:
            writer.write(
                title=e["title"], content=e["snippet"], url=e["url"],
                source=SITE, site_id=e["id"], date=e["date"],
                region=e["region"], partial=True,
            )
        added += len(entries)
        ckpt.mark(f"p{page}")
        if page % 10 == 0:
            writer.flush()
            ckpt.save(**fetcher.stats.as_dict())
            log(f"[list] p{page} 累计 {added} 条（本次运行）")
    writer.flush()
    ckpt.cursor = page
    ckpt.save(**fetcher.stats.as_dict())
    return added


# ── 阶段二：详情页 ──────────────────────────────────────────────────
def load_ids() -> list:
    """从列表产物取 id（按出现顺序=新→旧），供详情抓取排队。"""
    ids, seen = [], set()
    if not LIST_OUT.exists():
        return ids
    import json
    with open(LIST_OUT, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            sid = d.get("site_id")
            if sid and sid not in seen:
                seen.add(sid)
                ids.append(sid)
    return ids


def crawl_detail(fetcher: PoliteFetcher, ckpt: Checkpoint, max_items: int) -> int:
    writer = JsonlWriter(DETAIL_OUT, SITE, log=log)
    ids = load_ids()
    log(f"[detail] 待抓 id 队列 {len(ids)} 条（列表页产出）")
    added, tried = 0, 0
    for sid in ids:
        if ckpt.is_done(sid):
            continue
        if tried >= max_items:
            log(f"[detail] 本次运行达上限 {max_items}，停止（断点已存）")
            break
        tried += 1
        html = fetcher.get(f"{MENG}{sid}", required_prefix=MENG)
        if html is None:
            ckpt.mark(sid)  # 失效页（404）不再重试
            continue
        title, content = parse_detail(html)
        ckpt.mark(sid)
        if not title or not content:
            continue
        writer.write(title=title, content=content, url=f"{MENG}{sid}",
                     source=SITE, site_id=sid, partial=False)
        added += 1
        if tried % 20 == 0:
            writer.flush()
            ckpt.save(**fetcher.stats.as_dict())
            log(f"[detail] 已抓 {tried}/{max_items}（新入 {added}），累计完成 {len(ckpt.done)}")
    writer.flush()
    ckpt.save(**fetcher.stats.as_dict())
    return added


def load_seeds() -> list:
    """额外种子 id（多来源，用于跨越「已删条目」造成的链表断层）。

    实测：只从列表页 200 条出发，链表会在一小片幸存条目里走完
    （frontier 归零，仅 282 条）——因为 2019 年那批条目大量删除。种子来自：
    - sitemap_meng.txt（站点自报的最新 500 条）
    - 移动站 / 主站首页展示的条目（含**更老**的 id 区段）
    """
    ids = []
    if SEEDS.exists():
        for line in SEEDS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            m = re.search(r"/meng/(\d+)", line) or re.fullmatch(r"(\d+)", line)
            if m:
                ids.append(m.group(1))
    return list(dict.fromkeys(ids))


# 种子发现源（公开展示页；都走同一个限速抓取器）
SEED_SOURCES = [
    "https://www.12880.com/sitemap/sitemap_meng.txt",   # 站点自报 sitemap
    "https://m.sosuo.name/meng/",                        # 移动站首页（含较老 id）
    "https://www.12880.com/meng/",                       # 主站首页
]


def _collect_seed_ids(fetcher: PoliteFetcher, ckpt: Checkpoint) -> int:
    """从 sitemap / 首页收集额外种子 id（幂等：已抓过的 id 不重复入种）。"""
    known = set()
    if SEEDS.exists():
        known = {l.strip() for l in SEEDS.read_text(encoding="utf-8").splitlines() if l.strip()}
    new = []
    for url in SEED_SOURCES:
        html = fetcher.get(url)
        if not html:
            continue
        for m in re.finditer(r"/(?:meng/)(\d{3,})", html):
            sid = m.group(1)
            if sid not in known and not ckpt.is_done(sid):
                known.add(sid)
                new.append(sid)
    if new:
        SEEDS.parent.mkdir(parents=True, exist_ok=True)
        with open(SEEDS, "a", encoding="utf-8") as f:
            f.write("\n".join(new) + "\n")
        log(f"[walk] 种子发现：新增 {len(new)} 个 id → {SEEDS.name}")
    return len(new)


def crawl_walk(fetcher: PoliteFetcher, ckpt: Checkpoint, max_items: int,
               discover_seeds: bool = True) -> int:
    """沿详情页「上一篇/下一篇」链表遍历现存条目（断点续爬，frontier 落盘）。

    frontier 走空时，从种子文件补种（跨断层继续找新条目），直到种子也耗尽。
    """
    writer = JsonlWriter(DETAIL_OUT, SITE, log=log)
    if not ckpt.frontier:
        seed = load_ids() + load_seeds()
        ckpt.frontier = [s for s in dict.fromkeys(seed) if not ckpt.is_done(s)]
        log(f"[walk] frontier 以列表页产出+种子为种子：{len(ckpt.frontier)} 个 id")
    if discover_seeds:
        _collect_seed_ids(fetcher, ckpt)
    added = tried = 0
    while True:
        if not ckpt.frontier and discover_seeds:
            fresh = [s for s in load_seeds() if not ckpt.is_done(s)]
            if fresh:
                ckpt.frontier = fresh
                log(f"[walk] frontier 走空 → 补种 {len(fresh)} 个 id")
            else:
                break
        if not ckpt.frontier or tried >= max_items:
            break
        sid = ckpt.frontier.pop()
        if ckpt.is_done(sid):
            continue
        tried += 1
        url = f"{MENG}{sid}"
        html = fetcher.get(url, required_prefix=MENG)
        ckpt.mark(sid)
        if html is None:
            continue
        title, content = parse_detail(html)
        if title and content:
            writer.write(title=title, content=content, url=url, source=SITE,
                         site_id=sid, partial=False)
            added += 1
        for m in (_NEXT_RE.search(html), _PREV_RE.search(html)):
            if m:
                nid = m.group(1)
                if nid not in ckpt.done and nid not in ckpt.frontier:
                    ckpt.frontier.append(nid)
        if tried % 25 == 0:
            writer.flush()
            ckpt.save(**fetcher.stats.as_dict())
            log(f"[walk] 本次 {tried}/{max_items}（新入 {added}）"
                f" frontier={len(ckpt.frontier)} done={len(ckpt.done)}")
    writer.flush()
    ckpt.save(**fetcher.stats.as_dict())
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description="佛滔·梦境百科（sosuo.name）适配器")
    ap.add_argument("--phase", choices=["list", "detail", "walk"], default="list")
    ap.add_argument("--max-pages", type=int, default=8000)
    ap.add_argument("--max-items", type=int, default=4000)
    ap.add_argument("--delay", type=float, default=1.2)
    args = ap.parse_args()

    DATA_ROOT.joinpath("raw").mkdir(parents=True, exist_ok=True)
    DATA_ROOT.joinpath("checkpoints").mkdir(parents=True, exist_ok=True)

    lock = SiteLock(DATA_ROOT / "checkpoints" / "sosuo.lock", log=log)
    if not lock.acquire():
        return 3

    with PoliteFetcher(BASE, delay=args.delay, log=log) as fetcher:
        log("=" * 60)
        log(f"佛滔·梦境百科 phase={args.phase} delay={fetcher.delay}s")
        for n in fetcher.gate.notes:
            log(f"[robots] {n}")
        if args.phase == "list":
            ckpt = Checkpoint(CKPT_LIST, log=log)
            n = crawl_list(fetcher, ckpt, args.max_pages)
            log(f"[list] 完成：本次新增 {n} 条，累计翻页 {ckpt.cursor}")
        elif args.phase == "detail":
            ckpt = Checkpoint(CKPT_DETAIL, log=log)
            n = crawl_detail(fetcher, ckpt, args.max_items)
            log(f"[detail] 完成：本次新增 {n} 条，累计完成 {len(ckpt.done)} 页")
        else:
            ckpt = Checkpoint(CKPT_WALK, log=log)
            n = crawl_walk(fetcher, ckpt, args.max_items)
            log(f"[walk] 完成：本次新增 {n} 条，frontier 余 {len(ckpt.frontier)}，"
                f"累计已访 {len(ckpt.done)}")
        log(f"[stats] {fetcher.stats.as_dict()}")
    lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
