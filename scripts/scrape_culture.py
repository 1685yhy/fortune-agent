#!/usr/bin/env python3
"""Scrape Chinese fortune-telling cultural sources.

Targets four categories:
  A. TCM / Health classics (中医/养生古籍) — Baidu Baike entries
  B. Fengshui classics (风水名著) — Baidu Baike + ctext.org
  C. Qimen / Da Liu Ren (奇门/六壬典籍) — Baidu Baike + ctext.org
  D. Modern analysis (现代命理分析) — Zhihu / Douban

Usage:
    python3 scripts/scrape_culture.py                     # full scrape
    python3 scripts/scrape_culture.py --dry-run            # list what would be scraped
    python3 scripts/scrape_culture.py --category tcm       # only one category
    python3 scripts/scrape_culture.py --limit 2            # max pages per source
"""

import argparse
import asyncio
import json
import logging
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from tqdm import tqdm

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Output directories ────────────────────────────────────────────────

OUTPUT_DIR = PROJECT_ROOT / "data" / "scraped"
CATEGORY_DIRS = {
    "tcm": OUTPUT_DIR / "tcm",
    "fengshui": OUTPUT_DIR / "fengshui",
    "qimen": OUTPUT_DIR / "qimen",
    "modern": OUTPUT_DIR / "modern",
}

# ── Rate limiting ─────────────────────────────────────────────────────
MIN_DELAY = 3.0   # seconds between requests to same domain (increased jitter)
MAX_DELAY = 8.0

# ── HTTP client defaults ──────────────────────────────────────────────

DEFAULT_HEADERS = {
    "Referer": "https://www.google.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

# Modern browser User-Agent rotation pool (desktop + mobile)
USER_AGENTS = [
    # Chrome 126 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Chrome 125 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Chrome 126 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Chrome 126 Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Firefox 127 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    # Firefox 127 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
    # Safari 17.5 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/604.1",
    # Edge 126 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
]

MOBILE_UAS = [
    # iPhone Safari 17.5
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    # Android Chrome 126
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    # Android Chrome 125 (Samsung)
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    # iPad Safari
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]

TIMEOUT = httpx.Timeout(30.0, connect=15.0, read=25.0)
RETRIES = 3

# ── Source definitions ─────────────────────────────────────────────────

SOURCES: dict[str, list[dict]] = {
    # ── A. TCM / 养生古籍 ─────────────────────────────────────────
    "tcm": [
        {
            "url": "https://baike.baidu.com/item/%E9%BB%84%E5%B8%9D%E5%86%85%E7%BB%8F",
            "title": "黄帝内经",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E4%BC%A4%E5%AF%92%E8%AE%BA",
            "title": "伤寒论",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E6%9C%AC%E8%8D%89%E7%BA%B2%E7%9B%AE",
            "title": "本草纲目",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E9%92%88%E7%81%B8%E7%94%B2%E4%B9%99%E7%BB%8F",
            "title": "针灸甲乙经",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E4%B8%AD%E5%8C%BB%E4%BA%94%E8%A1%8C",
            "title": "中医五行",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E8%84%8F%E8%85%91%E5%AD%A6%E8%AF%B4",
            "title": "脏腑学说",
            "source_type": "baike",
        },
        {
            "url": "https://ctext.org/huangdi-neijing.zh.txt",
            "title": "黄帝内经（ctext.org 全文）",
            "source_type": "ctext",
        },
        {
            "url": "https://ctext.org/shanghan-lun.zh.txt",
            "title": "伤寒论（ctext.org 全文）",
            "source_type": "ctext",
        },
    ],
    # ── B. 风水名著 ──────────────────────────────────────────────
    "fengshui": [
        {
            "url": "https://baike.baidu.com/item/%E8%91%AC%E4%B9%A6",
            "title": "葬书",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E5%AE%85%E7%BB%8F",
            "title": "宅经",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E9%9D%92%E5%9B%8A%E7%BB%8F",
            "title": "青囊经",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E7%8E%84%E7%A9%BA%E9%A3%9E%E6%98%9F",
            "title": "玄空飞星",
            "source_type": "baike",
        },
        {
            "url": "https://ctext.org/library.pl?if=gb&file=102456",
            "title": "地理人子须知（ctext.org）",
            "source_type": "ctext_web",
        },
        {
            "url": "https://ctext.org/zangshu.zh.txt",
            "title": "葬书（ctext.org 全文）",
            "source_type": "ctext",
        },
    ],
    # ── C. 奇门/六壬 ──────────────────────────────────────────────
    "qimen": [
        {
            "url": "https://baike.baidu.com/item/%E7%83%9F%E6%B3%A2%E9%92%93%E5%8F%94%E8%B5%8B",
            "title": "烟波钓叟赋",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E5%A5%87%E9%97%A8%E9%81%81%E7%94%B2",
            "title": "奇门遁甲秘笈",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E5%85%AD%E5%A3%AC%E7%A5%9E%E8%AF%BE",
            "title": "六壬神课",
            "source_type": "baike",
        },
        {
            "url": "https://baike.baidu.com/item/%E5%A4%A7%E5%85%AD%E5%A3%AC",
            "title": "大六壬",
            "source_type": "baike",
        },
        {
            "url": "https://ctext.org/results.pl?searchu=%E5%A5%87%E9%97%A8%E9%81%81%E7%94%B2",
            "title": "奇门遁甲（ctext.org）",
            "source_type": "ctext_web",
        },
        {
            "url": "https://ctext.org/results.pl?searchu=%E5%85%AD%E5%A3%AC%E7%A5%9E%E8%AF%BE",
            "title": "六壬神课（ctext.org）",
            "source_type": "ctext_web",
        },
    ],
    # ── D. 现代命理分析 ──────────────────────────────────────────
    "modern": [
        {
            "url": "https://www.zhihu.com/search?type=content&q=%E5%85%AB%E5%AD%97%E5%88%86%E6%9E%90",
            "title": "知乎 - 八字分析精选",
            "source_type": "zhihu_search",
        },
        {
            "url": "https://www.zhihu.com/search?type=content&q=%E5%91%BD%E7%90%86%E6%A1%88%E4%BE%8B",
            "title": "知乎 - 命理案例精选",
            "source_type": "zhihu_search",
        },
        {
            "url": "https://www.zhihu.com/search?type=content&q=%E7%B4%AB%E5%BE%AE%E6%96%97%E6%95%B0%E5%AE%9E%E4%BE%8B",
            "title": "知乎 - 紫微斗数实例",
            "source_type": "zhihu_search",
        },
        {
            "url": "https://www.douban.com/search?q=%E5%85%AB%E5%AD%97%E5%88%86%E6%9E%90&cat=1001",
            "title": "豆瓣 - 八字分析话题",
            "source_type": "douban_search",
        },
        {
            "url": "https://www.douban.com/search?q=%E5%91%BD%E7%90%86%E6%A1%88%E4%BE%8B&cat=1001",
            "title": "豆瓣 - 命理案例话题",
            "source_type": "douban_search",
        },
    ],
}


# ══════════════════════════════════════════════════════════════════════
#  Scraping helpers
# ══════════════════════════════════════════════════════════════════════


class RateLimiter:
    """Per-domain rate limiter with jitter."""

    def __init__(self, min_delay: float = MIN_DELAY, max_delay: float = MAX_DELAY):
        self._timestamps: dict[str, float] = {}
        self._min = min_delay
        self._max = max_delay

    def wait_if_needed(self, url: str):
        """Sleep if we've hit this domain recently."""
        domain = urlparse(url).netloc
        now = time.monotonic()
        last = self._timestamps.get(domain, 0.0)
        elapsed = now - last
        needed = self._min + random.random() * (self._max - self._min)
        if elapsed < needed:
            time.sleep(needed - elapsed)
        self._timestamps[domain] = time.monotonic()


rate_limiter = RateLimiter()


def _get_headers(url: str) -> dict:
    """Build request headers with a rotated User-Agent.

    Uses mobile User-Agent for Baidu Baike URLs (bypasses blocking),
    and randomly rotates from the pool for all other domains.
    """
    headers = dict(DEFAULT_HEADERS)
    if "baike.baidu.com" in url:
        headers["User-Agent"] = random.choice(MOBILE_UAS)
    else:
        headers["User-Agent"] = random.choice(USER_AGENTS)
    return headers


async def fetch_url(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Fetch a URL with retries and rate limiting.

    Returns the response text on success, None on persistent failure.
    """
    rate_limiter.wait_if_needed(url)
    last_error = ""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = await client.get(url, headers=_get_headers(url), timeout=TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            # Detect encoding: try utf-8 first, fall back to detected
            content = resp.content
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("gbk", errors="replace")
            return text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("  [404] Not found: %s", url)
                return None
            last_error = str(e)
            if attempt < RETRIES:
                wait = attempt * 2.0
                logger.debug("  Retry %d/%d after %.1fs: %s", attempt, RETRIES, wait, e)
                await asyncio.sleep(wait)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_error = str(e)
            if attempt < RETRIES:
                wait = attempt * 2.0
                logger.debug("  Retry %d/%d after %.1fs: %s", attempt, RETRIES, wait, e)
                await asyncio.sleep(wait)
        except Exception as e:
            last_error = str(e)
            logger.warning("  Unexpected error fetching %s: %s", url, e)
            return None
    logger.warning("  Failed after %d retries: %s — %s", RETRIES, url, last_error)
    return None


# ══════════════════════════════════════════════════════════════════════
#  Content extractors per source type
# ══════════════════════════════════════════════════════════════════════


def extract_baike(html: str, title: str) -> str:
    """Extract main content from a Baidu Baike page."""
    soup = BeautifulSoup(html, "html.parser")
    paragraphs = []

    # Try modern baike layout
    for selector in [
        "div.para",                       # standard content divs
        "div.para-title",                 # section titles
        "div.basic-info-item",            # info table items
        "div.para-content",              # alternative content div
    ]:
        for elem in soup.select(selector):
            text = elem.get_text(strip=True)
            if text and len(text) > 10:
                paragraphs.append(text)

    # Fallback: extract all readable text from the main content area
    if not paragraphs:
        main_selectors = [
            "div.main-content", "div.content-wrapper",
            "div.lemma-content", "article",
        ]
        for sel in main_selectors:
            main = soup.select_one(sel)
            if main:
                for p in main.find_all(["p", "div", "h2", "h3"]):
                    text = p.get_text(strip=True)
                    if text and len(text) > 8:
                        paragraphs.append(text)
                break

    # Last-resort fallback: all paragraph text
    if not paragraphs:
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if text and len(text) > 15:
                paragraphs.append(text)

    # Deduplicate while preserving order
    seen = set()
    unique_paras = []
    for p in paragraphs:
        key = p[:60]
        if key not in seen:
            seen.add(key)
            unique_paras.append(p)

    return "\n\n".join(unique_paras)


def extract_ctext_text(text: str, title: str) -> str:
    """Clean up plain text from ctext.org .zh.txt downloads.

    ctext plain text files have boilerplate header/footer lines.
    """
    lines = text.split("\n")
    # Remove ctext.org boilerplate (first ~5 lines and last ~3 lines)
    content_lines = []
    in_header = True
    header_count = 0
    for line in lines:
        stripped = line.strip()
        # Skip ctext header lines
        if in_header:
            if "ctext" in stripped.lower() or "--" in stripped or not stripped:
                header_count += 1
                if header_count > 8:
                    in_header = False
                continue
            in_header = False
        # Skip footer lines
        if stripped.startswith("==") or stripped.startswith("--"):
            continue
        if stripped.startswith("【") and "ctext" in stripped.lower():
            continue
        content_lines.append(stripped)

    # Filter out very short or empty lines near the end
    cleaned = [l for l in content_lines if len(l) >= 5 or l == ""]
    return "\n".join(cleaned).strip()


def extract_ctext_web(html: str, title: str) -> str:
    """Extract content from ctext.org HTML pages."""
    soup = BeautifulSoup(html, "html.parser")
    paragraphs = []

    # Main content area
    for selector in [".content", "#content-here", "div.text", "div.main-text"]:
        for elem in soup.select(selector):
            text = elem.get_text(strip=True)
            if text and len(text) > 50:
                paragraphs.append(text)

    if not paragraphs:
        for p in soup.find_all(["p", "div"]):
            text = p.get_text(strip=True)
            if text and len(text) > 20 and "ctext" not in text[:20].lower():
                paragraphs.append(text)

    return "\n\n".join(paragraphs)


def extract_zhihu_search(html: str, title: str) -> str:
    """Extract search result snippets from Zhihu.

    Note: Zhihu aggressively blocks scrapers, so we may get limited results.
    This extracts what's visible on the search results page.
    """
    soup = BeautifulSoup(html, "html.parser")
    paragraphs = []

    # Zhihu search result items
    for selector in [
        ".ContentItem-content", ".RichText",
        ".SearchItem-answer", ".AnswerCard",
        "article", ".QuestionItem-title",
    ]:
        for elem in soup.select(selector):
            text = elem.get_text(strip=True)
            if text and len(text) > 20:
                paragraphs.append(text)

    if not paragraphs:
        for p in soup.find_all(["p", "span", "div"]):
            text = p.get_text(strip=True)
            if text and 15 < len(text) < 2000:
                paragraphs.append(text)

    return "\n\n".join(paragraphs)


def extract_douban_search(html: str, title: str) -> str:
    """Extract search result snippets from Douban."""
    soup = BeautifulSoup(html, "html.parser")
    paragraphs = []

    for selector in [
        ".result .content", ".result .title",
        ".result-content", ".article-db",
        ".note", ".topic-doc",
    ]:
        for elem in soup.select(selector):
            text = elem.get_text(strip=True)
            if text and len(text) > 20:
                paragraphs.append(text)

    if not paragraphs:
        for p in soup.find_all(["p", "div", "span"]):
            text = p.get_text(strip=True)
            if 15 < len(text) < 2000:
                paragraphs.append(text)

    return "\n\n".join(paragraphs)


def extract_generic(html: str, title: str) -> str:
    """Generic fallback: extract all paragraph text."""
    soup = BeautifulSoup(html, "html.parser")
    # Remove unwanted elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    paragraphs = []
    for p in soup.find_all(["p", "h1", "h2", "h3", "h4", "li"]):
        text = p.get_text(strip=True)
        if text and len(text) > 10:
            paragraphs.append(text)

    return "\n\n".join(paragraphs)


EXTRACTORS = {
    "baike": extract_baike,
    "ctext": extract_ctext_text,
    "ctext_web": extract_ctext_web,
    "zhihu_search": extract_zhihu_search,
    "douban_search": extract_douban_search,
}


# ══════════════════════════════════════════════════════════════════════
#  Scrape a single source
# ══════════════════════════════════════════════════════════════════════


async def scrape_source(
    client: httpx.AsyncClient,
    source: dict,
    category: str,
    output_dir: Path,
) -> dict:
    """Scrape one source, save to JSON, return metadata.

    Returns dict with {url, title, category, success, error, chars, file_path}
    """
    url = source["url"]
    title = source["title"]
    source_type = source.get("source_type", "generic")

    # Fetch
    html = await fetch_url(client, url)
    if html is None:
        return {
            "url": url,
            "title": title,
            "category": category,
            "success": False,
            "error": "Fetch failed after retries",
            "chars": 0,
            "file_path": None,
        }

    # Extract
    extractor = EXTRACTORS.get(source_type, extract_generic)
    try:
        content = extractor(html, title)
    except Exception as e:
        logger.warning("  Extraction error for %s: %s", title, e)
        content = extract_generic(html, title)

    if not content or len(content) < 20:
        return {
            "url": url,
            "title": title,
            "category": category,
            "success": False,
            "error": "Extracted content too short",
            "chars": 0,
            "file_path": None,
        }

    # Build output record
    safe_name = re.sub(r"[^\w\-一-鿿]+", "_", title)[:80]
    record = {
        "title": title,
        "url": url,
        "source_type": source_type,
        "category": category,
        "content": content,
        "chars": len(content),
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # Save
    file_path = output_dir / f"{safe_name}.json"
    file_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info("  Saved %s (%d chars) → %s", title, len(content), file_path.name)
    return {
        "url": url,
        "title": title,
        "category": category,
        "success": True,
        "error": "",
        "chars": len(content),
        "file_path": str(file_path),
    }


# ══════════════════════════════════════════════════════════════════════
#  Main orchestration
# ══════════════════════════════════════════════════════════════════════


async def scrape_category(
    category: str,
    sources: list[dict],
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> list[dict]:
    """Scrape all sources in a category.

    Returns list of result dicts.
    """
    output_dir = CATEGORY_DIRS[category]
    output_dir.mkdir(parents=True, exist_ok=True)

    if limit and limit > 0:
        sources = sources[:limit]

    if dry_run:
        print(f"\n  [{category}] Sources to scrape ({len(sources)}):")
        for s in sources:
            print(f"    - {s['title']} ({s['source_type']})")
            print(f"      {s['url']}")
        return [{"title": s["title"], "url": s["url"], "category": category, "dry_run": True} for s in sources]

    results = []
    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        for source in tqdm(sources, desc=f"[{category}]", unit="source"):
            result = await scrape_source(client, source, category, output_dir)
            results.append(result)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Chinese fortune-telling cultural sources",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be scraped without fetching",
    )
    parser.add_argument(
        "--category", choices=list(SOURCES.keys()), default=None,
        help="Only scrape this category (default: all)",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max sources per category (default: all)",
    )
    args = parser.parse_args()

    categories_to_scrape = [args.category] if args.category else list(SOURCES.keys())

    print("=" * 60)
    print("  易理明灯 - 文化数据采集 (Data Collection Enhancement)")
    print("=" * 60)
    if args.dry_run:
        print(f"\nDRY RUN — no data will be fetched")
    print(f"Categories: {', '.join(categories_to_scrape)}")
    print(f"Output: {OUTPUT_DIR}")
    print()

    all_results: list[dict] = []
    for cat in categories_to_scrape:
        sources = SOURCES[cat]
        print(f"\n{'─' * 50}")
        print(f"Category: {cat} ({len(sources)} sources)")
        print(f"{'─' * 50}")
        results = asyncio.run(scrape_category(cat, sources, dry_run=args.dry_run, limit=args.limit))
        all_results.extend(results)

    # Summary
    print(f"\n{'=' * 60}")
    if args.dry_run:
        total = sum(1 for r in all_results if r.get("dry_run"))
        print(f"Dry run complete. {total} sources would be scraped.")
    else:
        success = [r for r in all_results if r["success"]]
        failed = [r for r in all_results if not r["success"]]
        total_chars = sum(r["chars"] for r in success)
        print(f"Results: {len(success)} succeeded, {len(failed)} failed")
        print(f"Total content: {total_chars:,} characters")
        if failed:
            print(f"\nFailed sources:")
            for f in failed:
                print(f"  - {f['title']}: {f.get('error', 'unknown')}")
        print(f"\nData saved to: {OUTPUT_DIR}")
        for cat in categories_to_scrape:
            cat_dir = CATEGORY_DIRS[cat]
            if cat_dir.exists():
                files = list(cat_dir.glob("*.json"))
                print(f"  [{cat}] {len(files)} files in {cat_dir}")


if __name__ == "__main__":
    main()
