#!/usr/bin/env python3
"""
易理明灯 — Competitor Fortune-Telling Data Scraper

Collects answers from popular Chinese fortune-telling platforms for
accuracy comparison against our own AI fortune-telling system.

Supported platforms:
  - forum-longyin:  龙隐论坛 (bbs.longyinok.com) — real user Q&A, Discuz!
  - forum-64gua:    周易天地 (64gua.com) — real user Q&A, Discuz!
  - forum-yuanheng: 元亨利贞 (yuanhenglizhen.com) — real user Q&A, Discuz!
  - zgjm:           周公解梦 (zgjm.org) — dream interpretation
  - 12880:          12880.com — multi-purpose fortune (dreams, bazi, names)
  - buyiju:         卜易居 (buyiju.com) — comprehensive fortune tools
  - shen88:         神巴巴 (shen88.cn) — astrology/fortune
  - 99yunshi:       99星运 (99yunshi.com) — fortune/astrology
  - iztro:          iztro API — Zi Wei Dou Shu chart + AI interpretation
  - juhe:           聚合数据 API — bazi chart query (needs API key)

Usage:
    python scripts/scrape_competitors.py --platform zgjm --count 50
    python scripts/scrape_competitors.py --platform forum-longyin --count 200
    python scripts/scrape_competitors.py --platform iztro --count 10
    python scripts/scrape_competitors.py --dry-run
    python scripts/scrape_competitors.py --platform all --count 100
"""

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data" / "competitor_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Rate limiting defaults
DEFAULT_DELAY = 1.5  # seconds between requests
DEFAULT_TIMEOUT = 30.0

# User agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def make_output_path(platform: str) -> Path:
    """Generate a date-stamped JSONL output path for a given platform."""
    date_str = datetime.now().strftime("%Y%m%d")
    return DATA_DIR / f"{platform}_{date_str}.jsonl"


def save_record(record: dict, output_path: Path):
    """Append one JSON record to a JSONL file."""
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def make_record(query: str, platform: str, response: str, url: str) -> dict:
    """Create a standardized record dict."""
    return {
        "query": query,
        "platform": platform,
        "response": response,
        "url": url,
        "scraped_at": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# HTTP client factory
# ---------------------------------------------------------------------------


def make_client() -> httpx.AsyncClient:
    """Create an httpx AsyncClient with sensible defaults."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=15.0),
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
    )


def random_headers() -> dict:
    """Return request headers with a random User-Agent."""
    ua = random.choice(USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.google.com/",
    }


async def safe_get(client: httpx.AsyncClient, url: str, delay: float = DEFAULT_DELAY) -> Optional[str]:
    """Fetch a URL with rate limiting and error handling. Returns text or None."""
    await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))  # jitter
    try:
        resp = await client.get(url, headers=random_headers())
        if resp.status_code == 200:
            return resp.text
        elif resp.status_code == 403:
            logger.warning(f"  403 Forbidden: {url}")
        elif resp.status_code == 429:
            logger.warning(f"  429 Rate limited: {url}")
            await asyncio.sleep(5.0)
        else:
            logger.warning(f"  HTTP {resp.status_code}: {url}")
        return None
    except httpx.TimeoutException:
        logger.warning(f"  Timeout: {url}")
        return None
    except httpx.HTTPError as e:
        logger.warning(f"  HTTP error: {url} — {e}")
        return None
    except Exception as e:
        logger.warning(f"  Error fetching {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Platform: 周公解梦 (zgjm.org) — dream interpretation
# ---------------------------------------------------------------------------


async def scrape_zgjm(client: httpx.AsyncClient, count: int, dry_run: bool) -> list:
    """Scrape dream interpretation entries from zgjm.org."""
    results = []
    base_url = "https://www.zgjmorg.com"

    # Categories to scrape
    categories = [
        "/dongwu/", "/zhiwu/", "/wupin/", "/huodong/", "/shenghuo/", "/ziran/",
        "/guishen/", "/jianzhu/", "/qita/", "/yunfujiemeng/", "/mengjing/",
        "/wenhua/", "/health/",
    ]

    if dry_run:
        logger.info(f"  [zgjm] Would scrape ~{count} dream entries from {len(categories)} categories using {base_url}")
        return []

    collected = 0
    for cat in categories:
        if collected >= count:
            break
        cat_url = f"{base_url}{cat}"
        html = await safe_get(client, cat_url)
        if not html:
            continue

        # Extract dream page links
        soup = BeautifulSoup(html, "html.parser")
        links = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if href.endswith(".html") and not href.startswith("http"):
                if "index_" not in href:
                    links.add(f"{base_url}{href}")

        logger.info(f"  [zgjm] Category {cat}: {len(links)} dream pages")

        for link in list(links)[: max(5, count - collected)]:
            if collected >= count:
                break
            html2 = await safe_get(client, link)
            if not html2:
                continue
            soup2 = BeautifulSoup(html2, "html.parser")

            # Title
            h1 = soup2.find("h1")
            if not h1:
                continue
            title = h1.get_text(strip=True)

            # Content
            content_div = soup2.find("div", class_="read-content")
            if not content_div:
                continue
            for tag in content_div.find_all(["script", "style", "ins", "iframe"]):
                tag.decompose()
            text = content_div.get_text(separator="\n", strip=True)

            # Clean
            lines = []
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                skip_words = ["当前位置", "首页", "上一篇", "下一篇", "相关文章",
                              "免责声明", "版权声明", "网友评论", "扫一扫", "加微信",
                              "关注我们", "仅供娱乐", "请勿盲目迷信"]
                if any(s in line for s in skip_words):
                    continue
                lines.append(line)

            response_text = "\n".join(lines)
            if len(response_text) < 20:
                continue

            # Extract dream keyword from title
            keyword = title
            m = re.search(r"梦[见到]([^，。！？\s]+)", title)
            if m:
                keyword = m.group(1).strip()

            results.append(make_record(
                query=f"梦见{keyword}是什么意思",
                platform="zgjm",
                response=response_text[:2000],
                url=link,
            ))
            collected += 1
            if collected % 20 == 0:
                logger.info(f"    [zgjm] Collected {collected}/{count}")

    logger.info(f"  [zgjm] Done: {len(results)} dream entries")
    return results


# ---------------------------------------------------------------------------
# Platform: 12880.com — multi-purpose fortune site
# ---------------------------------------------------------------------------


async def scrape_12880(client: httpx.AsyncClient, count: int, dry_run: bool) -> list:
    """Scrape fortune content from 12880.com (dreams, bazi, zodiac)."""
    results = []
    base_url = "https://www.12880.com"

    sections = [
        ("/jiemeng/c1", "dream"),
        ("/jiemeng/c2", "dream"),
        ("/jiemeng/c3", "dream"),
        ("/shengxiao/", "zodiac"),
    ]

    if dry_run:
        logger.info(f"  [12880] Would scrape ~{count} entries from {len(sections)} sections")
        return []

    collected = 0
    for path, category in sections:
        if collected >= count:
            break
        url = f"{base_url}{path}"
        html = await safe_get(client, url)
        if not html:
            continue

        # 12880 uses /jiemeng/NNNNNN-slug pattern (no .html extension)
        links = set()
        for m in re.finditer(r'/jiemeng/(\d+-[^"\'<>\s]+)', html):
            links.add(f"{base_url}/jiemeng/{m.group(1)}")

        logger.info(f"  [12880] Section {path}: {len(links)} pages")

        for link in list(links)[: max(5, count - collected)]:
            if collected >= count:
                break
            html2 = await safe_get(client, link)
            if not html2:
                continue
            soup2 = BeautifulSoup(html2, "html.parser")

            # Title
            h1 = soup2.find("h1")
            if not h1:
                continue
            title = h1.get_text(strip=True)

            # Content
            content_div = (soup2.find("div", class_="article_content")
                           or soup2.find("div", class_="read-content")
                           or soup2.find("div", class_="content"))
            if not content_div:
                continue

            for tag in content_div.find_all(["script", "style", "ins", "iframe"]):
                tag.decompose()
            text = content_div.get_text(separator="\n", strip=True)

            lines = []
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                skip_words = ["当前位置", "上一篇", "下一篇", "相关文章",
                              "免责声明", "版权声明", "扫一扫", "关注我们",
                              "仅供娱乐", "请勿盲目迷信"]
                if any(s in line for s in skip_words):
                    continue
                lines.append(line)

            response_text = "\n".join(lines)
            if len(response_text) < 20:
                continue

            results.append(make_record(
                query=title,
                platform="12880",
                response=response_text[:2000],
                url=link,
            ))
            collected += 1
            if collected % 20 == 0:
                logger.info(f"    [12880] Collected {collected}/{count}")

    logger.info(f"  [12880] Done: {len(results)} entries")
    return results


# ---------------------------------------------------------------------------
# Platform: 卜易居 (buyiju.com) — comprehensive fortune
# ---------------------------------------------------------------------------


async def scrape_buyiju(client: httpx.AsyncClient, count: int, dry_run: bool) -> list:
    """Scrape fortune content from buyiju.com."""
    results = []
    base_url = "https://www.buyiju.com"

    # Common fortune-telling sections
    targets = [
        ("/bazi/", "bazi"),
        ("/sm/", "bazi"),
        ("/hehun/", "marriage"),
        ("/xingming/", "name"),
    ]

    if dry_run:
        logger.info(f"  [buyiju] Would scrape ~{count} entries from {len(targets)} sections")
        return []

    collected = 0
    for path, category in targets:
        if collected >= count:
            break
        url = f"{base_url}{path}"
        html = await safe_get(client, url)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        # Get all links on the page
        links = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if href.startswith("/") and ".html" in href:
                full = f"{base_url}{href}"
                links.add(full)

        logger.info(f"  [buyiju] Path {path}: found {len(links)} pages")

        for link in list(links)[: max(5, count - collected)]:
            if collected >= count:
                break
            html2 = await safe_get(client, link)
            if not html2:
                continue
            soup2 = BeautifulSoup(html2, "html.parser")

            title_tag = soup2.find("h1") or soup2.find("title")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)

            # Get main content
            content = soup2.find("div", class_="content") or soup2.find("article") or soup2.find("main")
            if content:
                for tag in content.find_all(["script", "style"]):
                    tag.decompose()
                text = content.get_text(strip=True)[:2000]
            else:
                text = soup2.get_text(strip=True)[:2000]

            if len(text) < 30:
                continue

            results.append(make_record(
                query=title,
                platform="buyiju",
                response=text,
                url=link,
            ))
            collected += 1
            if collected % 10 == 0:
                logger.info(f"    [buyiju] Collected {collected}/{count}")

    logger.info(f"  [buyiju] Done: {len(results)} entries")
    return results


# ---------------------------------------------------------------------------
# Platform: Forum (Discuz!-based) — 龙隐论坛
# ---------------------------------------------------------------------------


async def scrape_forum_longyin(client: httpx.AsyncClient, count: int, dry_run: bool) -> list:
    """Scrape Q&A posts from 龙隐论坛 (bbs.longyinok.com)."""
    results = []
    base_url = "https://bbs.longyinok.com"

    # Main sections with fortune-telling content
    sections = [
        "/forum.php?gid=43",   # 四柱八字
        "/forum.php?gid=44",   # 紫微斗数
        "/forum.php?gid=45",   # 六爻八卦
        "/forum.php?gid=46",   # 奇门遁甲
        "/forum.php?gid=47",   # 风水
        "/forum.php?gid=48",   # 解梦
    ]

    if dry_run:
        logger.info(f"  [forum-longyin] Would scrape ~{count} Q&A posts from {len(sections)} sections")
        return []

    collected = 0
    for section in sections:
        if collected >= count:
            break
        url = f"{base_url}{section}"
        html = await safe_get(client, url)
        if not html:
            continue

        # Parse thread list
        thread_links = set()
        for m in re.finditer(r'href="(thread-\d+-\d+-\d+\.html)"', html):
            thread_links.add(f"{base_url}/{m.group(1)}")

        logger.info(f"  [longyin] Section {section}: {len(thread_links)} threads")

        for thread_url in list(thread_links)[: max(10, count - collected)]:
            if collected >= count:
                break
            html2 = await safe_get(client, thread_url)
            if not html2:
                continue

            soup = BeautifulSoup(html2, "html.parser")

            # Get thread title
            title_tag = soup.find("title")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)

            # Skip login-required pages
            if "提示信息" in title or "您没有权限" in html2 or "请登录" in html2:
                logger.debug(f"    [longyin] Skipping login-restricted: {thread_url}")
                continue

            # Remove forum name suffix
            title = re.sub(r"\s*-\s*龙隐.*", "", title).strip()

            # Get first post content
            posts = soup.find_all("td", class_="t_f")
            if not posts:
                posts = soup.find_all("div", class_="postmessage")
            if not posts:
                posts = soup.find_all("div", class_="t_msgfont")

            if posts:
                post_text = posts[0].get_text(strip=True)[:2000]
            else:
                post_text = soup.get_text(strip=True)[:2000]

            if len(post_text) < 20:
                continue

            results.append(make_record(
                query=title,
                platform="forum-longyin",
                response=post_text,
                url=thread_url,
            ))
            collected += 1
            if collected % 20 == 0:
                logger.info(f"    [longyin] Collected {collected}/{count}")

    logger.info(f"  [forum-longyin] Done: {len(results)} posts")
    return results


# ---------------------------------------------------------------------------
# Platform: Forum (Discuz!-based) — 周易天地 (64gua.com)
# ---------------------------------------------------------------------------


async def scrape_forum_64gua(client: httpx.AsyncClient, count: int, dry_run: bool) -> list:
    """Scrape Q&A posts from 周易天地 forum (64gua.com).

    Note: The original bbs.64gua.com forum (1.46M+ posts) appears to be
    offline/restructured. The current 64gua.com is a different site.
    For historical data, use Internet Archive (Wayback Machine).
    """
    results = []

    if dry_run:
        logger.info(f"  [forum-64gua] Would attempt to scrape ~{count} Q&A posts")
        logger.info(f"  [forum-64gua] NOTE: Original forum at bbs.64gua.com appears offline")
        logger.info(f"  [forum-64gua] Use Wayback Machine for historical data")
        return []

    logger.warning(f"  [forum-64gua] Forum appears offline. Check bbs.64gua.com via Wayback Machine for historical data.")
    return results


async def scrape_forum_yuanheng(client: httpx.AsyncClient, count: int, dry_run: bool) -> list:
    """Scrape Q&A posts from 元亨利贞 forum (yuanhenglizhen.com).

    Note: This site is currently unreachable (connection reset).
    The forum used to be at bbs.china95.net.
    For historical data, use Internet Archive (Wayback Machine).
    """
    results = []

    if dry_run:
        logger.info(f"  [forum-yuanheng] Would attempt to scrape ~{count} Q&A posts")
        logger.info(f"  [forum-yuanheng] NOTE: Site currently unreachable")
        logger.info(f"  [forum-yuanheng] Use Wayback Machine for historical data")
        return []

    logger.warning(f"  [forum-yuanheng] Site unreachable. Check via Wayback Machine.")
    return results


# ---------------------------------------------------------------------------
# Platform: 神巴巴 (shen88.cn) — astrology/fortune
# ---------------------------------------------------------------------------


async def scrape_shen88(client: httpx.AsyncClient, count: int, dry_run: bool) -> list:
    """Scrape content from shen88.cn."""
    results = []
    base_url = "https://www.shen88.cn"

    targets = [
        "/bazi/",
        "/sm/",
    ]

    if dry_run:
        logger.info(f"  [shen88] Would scrape ~{count} entries")
        return []

    collected = 0
    for path in targets:
        if collected >= count:
            break
        url = f"{base_url}{path}"
        html = await safe_get(client, url)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        links = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if href.startswith("/") and ".html" in href:
                links.add(f"{base_url}{href}")

        for link in list(links)[: max(5, count - collected)]:
            if collected >= count:
                break
            html2 = await safe_get(client, link)
            if not html2:
                continue
            soup2 = BeautifulSoup(html2, "html.parser")

            title_tag = soup2.find("h1") or soup2.find("title")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)

            content = soup2.find("div", class_="content") or soup2.find("article")
            text = content.get_text(strip=True)[:2000] if content else soup2.get_text(strip=True)[:2000]
            if len(text) < 20:
                continue

            results.append(make_record(
                query=title,
                platform="shen88",
                response=text,
                url=link,
            ))
            collected += 1

    logger.info(f"  [shen88] Done: {len(results)} entries")
    return results


# ---------------------------------------------------------------------------
# Platform: iztro API — Zi Wei Dou Shu AI interpretation
# ---------------------------------------------------------------------------


async def scrape_iztro(client: httpx.AsyncClient, count: int, dry_run: bool) -> list:
    """Use iztro API to get Zi Wei Dou Shu interpretations."""
    results = []

    if dry_run:
        logger.info("  [iztro] Would get ~{} Zi Wei Dou Shu AI interpretations via API".format(count))
        return []

    # iztro API for chart + AI interpretation
    api_base = "https://chat-api.iztro.com"

    # Sample birth dates for diversity
    sample_births = [
        {"year": 1990, "month": 3, "day": 15, "hour": 8, "gender": 1},
        {"year": 1995, "month": 7, "day": 22, "hour": 14, "gender": 0},
        {"year": 1988, "month": 12, "day": 5, "hour": 6, "gender": 1},
        {"year": 2000, "month": 1, "day": 10, "hour": 10, "gender": 0},
        {"year": 1985, "month": 9, "day": 18, "hour": 16, "gender": 1},
        {"year": 1992, "month": 5, "day": 3, "hour": 12, "gender": 0},
    ]

    collected = 0
    for birth in sample_births[:max(1, count)]:
        if collected >= count:
            break

        # Try to create a chat session and get interpretation
        # iztro API format
        try:
            session_payload = {
                "birthday": f"{birth['year']}-{birth['month']:02d}-{birth['day']:02d}",
                "birthTime": f"{birth['hour']:02d}:00",
                "gender": birth["gender"],
            }
            resp = await client.post(
                f"{api_base}/api/session/create",
                json=session_payload,
                headers=random_headers(),
                timeout=30,
            )
            if resp.status_code == 200:
                session_data = resp.json()
                logger.info(f"    [iztro] Session created for {birth['year']}-{birth['month']:02d}")

                # Get interpretation
                chat_payload = {
                    "sessionId": session_data.get("sessionId"),
                    "message": "请分析这个命盘的整体格局、财运和事业运势",
                }
                chat_resp = await client.post(
                    f"{api_base}/api/chat/send",
                    json=chat_payload,
                    headers=random_headers(),
                    timeout=60,
                )
                if chat_resp.status_code == 200:
                    chat_data = chat_resp.json()
                    interpretation = chat_data.get("reply", "") or chat_data.get("message", "")
                    if interpretation:
                        results.append(make_record(
                            query=f"分析{birth['year']}年{birth['month']}月{birth['day']}日{birth['hour']}时出生的紫微斗数命盘",
                            platform="iztro",
                            response=str(interpretation)[:2000],
                            url=f"{api_base}/api/chat/send",
                        ))
                        collected += 1
        except Exception as e:
            logger.warning(f"    [iztro] API error: {e}")

    logger.info(f"  [iztro] Done: {len(results)} interpretations")
    return results


# ---------------------------------------------------------------------------
# Master scraper dispatcher
# ---------------------------------------------------------------------------

PLATFORMS = {
    "zgjm": scrape_zgjm,
    "12880": scrape_12880,
    "buyiju": scrape_buyiju,
    "forum-longyin": scrape_forum_longyin,
    "forum-64gua": scrape_forum_64gua,
    "forum-yuanheng": scrape_forum_yuanheng,
    "shen88": scrape_shen88,
    "iztro": scrape_iztro,
}

PLATFORM_DESCRIPTIONS = {
    "zgjm": "周公解梦 (zgjmorg.com) — dream interpretation",
    "12880": "12880.com — multi-purpose fortune (dreams, bazi, names)",
    "buyiju": "卜易居 (buyiju.com) — comprehensive fortune tools",
    "forum-longyin": "龙隐论坛 (bbs.longyinok.com) — real user Q&A (570K+ posts)",
    "forum-64gua": "周易天地 (64gua.com) — real user Q&A (1.46M+ posts)",
    "forum-yuanheng": "元亨利贞 (yuanhenglizhen.com) — real user Q&A",
    "shen88": "神巴巴 (shen88.cn) — astrology/fortune",
    "iztro": "iztro API — Zi Wei Dou Shu AI interpretation",
}


async def scrape_platform(client: httpx.AsyncClient, platform: str, count: int, dry_run: bool) -> list:
    """Dispatch to the appropriate platform scraper."""
    scraper_fn = PLATFORMS.get(platform)
    if not scraper_fn:
        logger.error(f"Unknown platform: {platform}")
        return []
    try:
        return await scraper_fn(client, count, dry_run)
    except Exception as e:
        logger.error(f"Error scraping {platform}: {e}")
        import traceback
        traceback.print_exc()
        return []


async def main():
    parser = argparse.ArgumentParser(
        description="易理明灯 — Competitor Fortune-Telling Data Scraper"
    )
    parser.add_argument(
        "--platform", type=str, default="all",
        choices=list(PLATFORMS.keys()) + ["all"],
        help="Platform to scrape (default: all)"
    )
    parser.add_argument(
        "--count", type=int, default=50,
        help="Number of entries to scrape per platform (default: 50)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be scraped without actually scraping"
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY,
        help=f"Delay between requests in seconds (default: {DEFAULT_DELAY})"
    )
    args = parser.parse_args()

    platforms_to_scrape = (
        list(PLATFORMS.keys()) if args.platform == "all" else [args.platform]
    )

    print(f"{'=' * 60}")
    print(f"  易理明灯 — Competitor Data Scraper")
    print(f"{'=' * 60}")
    print(f"  Dry run:  {'YES' if args.dry_run else 'NO'}")
    print(f"  Count:    {args.count} per platform")
    print(f"  Delay:    {args.delay}s between requests")
    print(f"  Platforms: {', '.join(platforms_to_scrape)}")
    print(f"  Output:   {DATA_DIR}/")
    print()

    if args.dry_run:
        print(f"{'─' * 60}")
        print(f"  DRY RUN — No data will be saved")
        print(f"{'─' * 60}")

    client = make_client()
    total_records = 0

    try:
        for platform in platforms_to_scrape:
            desc = PLATFORM_DESCRIPTIONS.get(platform, platform)
            print(f"\n{'─' * 60}")
            print(f"  Platform: {platform} — {desc}")
            print(f"{'─' * 60}")

            records = await scrape_platform(client, platform, args.count, args.dry_run)

            if records and not args.dry_run:
                output_path = make_output_path(platform)
                for record in records:
                    save_record(record, output_path)
                print(f"\n  Saved {len(records)} records to {output_path}")
                total_records += len(records)
            elif not records and not args.dry_run:
                print(f"\n  No records collected for {platform}")
            else:
                print(f"\n  [DRY RUN] Would collect records for {platform}")

    finally:
        await client.aclose()

    print(f"\n{'=' * 60}")
    if args.dry_run:
        print(f"  DRY RUN COMPLETE — No data saved")
    else:
        print(f"  Done! {total_records} total records saved to {DATA_DIR}/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
