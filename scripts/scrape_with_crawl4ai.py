#!/usr/bin/env python3
"""基于 Crawl4AI 的批量知识采集 — 替代旧的 bulk_scrape.py。

用法:
    python scripts/scrape_with_crawl4ai.py                          # 全量采集
    python scripts/scrape_with_crawl4ai.py --category qimen          # 只采集奇门
    python scripts/scrape_with_crawl4ai.py --dry-run                 # 预览不写入
"""

import sys, asyncio, json, time, logging, argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper import scrape_urls, ScrapeResult

STAGING = Path("/mnt/d/fortune-data/books/zonghe/staging")
STAGING.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(message)s]")
log = logging.getLogger()

# ── 数据源配置 ──────────────────────────────────────────────────
# 添加新的目标网站只需在这里加一行 URL

SOURCES = {
    "qimen": [
        "https://www.dunjia.net/qimen/",
        "https://www.dunjia.net/dunjia/",
    ],
    "bazi": [
        "https://www.zhouyi.cc/bazi/",
        "https://www.chinaz.com/mingli/bazi/",
    ],
    "fengshui": [
        "https://www.zhouyi.cc/fengshui/",
    ],
    "dreams": [
        "https://www.zgjm.org/article/",
    ],
    "mianxiang": [
        "https://www.zhouyi.cc/mianxiang/",
    ],
    "general": [
        "https://www.zhouyi.cc/",
    ],
}

MIN_LEN = 80
MAX_LEN = 3000


async def scrape_category(category: str, urls: list, dry_run: bool = False) -> int:
    """采集一个分类的所有 URL"""
    log.info(f"[{category}] Scraping {len(urls)} URLs...")
    results = await scrape_urls(urls, concurrency=5, timeout=30)

    accepted = 0
    rejected = 0
    out_file = STAGING / f"{category}_accepted.jsonl"

    for r in results:
        if not r.success:
            rejected += 1
            continue

        entry = r.to_entry(category=category)
        if entry["quality"] == "rejected":
            rejected += 1
            continue

        if not dry_run:
            with open(out_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        accepted += 1

    log.info(f"  [{category}] {accepted} accepted, {rejected} rejected")
    return accepted


async def main(category: str = None, dry_run: bool = False):
    log.info("=== Crawl4AI Bulk Scraper ===")
    total = 0

    targets = {category: SOURCES[category]} if category else SOURCES

    for cat, urls in targets.items():
        n = await scrape_category(cat, urls, dry_run)
        total += n

    log.info(f"\n=== Total: {total} new entries ===")

    # 汇总
    grand = 0
    for f in sorted(STAGING.glob("*_accepted.jsonl")):
        count = sum(1 for _ in open(f))
        grand += count
        log.info(f"  {f.stem.replace('_accepted', '')}: {count}")
    log.info(f"  GRAND TOTAL: {grand}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", type=str, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.category, args.dry_run))
