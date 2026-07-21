#!/usr/bin/env python3
"""
Crawl 12880.com - ALL dream entries with incremental saving.
Saves entries every 500 scraped pages to avoid data loss.
"""

import asyncio
import httpx
import re
import time
import sys
from bs4 import BeautifulSoup
from typing import List, Tuple, Set
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)
sys.path.insert(0, '/home/a/fortune-agent')

REQUEST_DELAY = 1.0
MAX_CONCURRENT = 3
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

OUTPUT = Path('/mnt/d/fortune-data/books/zonghe/12880_dreams.txt')
SAVE_EVERY = 500  # Save incrementally every N results

CATEGORIES = ['c1','c2','c3','c4','c5','c6','c8','c9','c10','c11','c13','c14','c15','c17']


def load_existing_keywords() -> Set[str]:
    keywords = set()
    for f in Path('/mnt/d/fortune-data/books/zonghe').glob('*.txt'):
        if f.name == '12880_dreams.txt':
            continue
        try:
            found = re.findall(r'梦见(\w+)', f.read_text(encoding='utf-8'))
            keywords.update(found)
        except:
            pass
    # Also load what we already saved
    if OUTPUT.exists():
        found = re.findall(r'梦见(\w+)', OUTPUT.read_text(encoding='utf-8'))
        keywords.update(found)
    logger.info(f"Loaded {len(keywords)} existing keywords")
    return keywords


def discover_urls() -> List[str]:
    all_urls = set()
    for cat in CATEGORIES:
        url = f'https://www.12880.com/jiemeng/{cat}'
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={'User-Agent': USER_AGENT})
        links = set(re.findall(r'/jiemeng/(\d+-[^"\'<>]+)', r.text))
        for link in links:
            all_urls.add(f'https://www.12880.com/jiemeng/{link}')
        logger.info(f"  {cat}: {len(links)} URLs")
        time.sleep(REQUEST_DELAY)

    # Fotaojiemeng (pages 1-20, 15 each)
    for page in range(1, 50):
        url = f'https://www.12880.com/fotaojiemeng/list_4_{page}.html' if page > 1 else 'https://www.12880.com/fotaojiemeng/'
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={'User-Agent': USER_AGENT})
        links = set(re.findall(r'/fotaojiemeng/(\d+\.html)', r.text))
        if not links:
            logger.info(f"  Fotao page {page}: empty (done)")
            break
        for link in links:
            all_urls.add(f'https://www.12880.com/fotaojiemeng/{link}')
        time.sleep(REQUEST_DELAY)

    return sorted(all_urls)


def parse_dream(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, 'html.parser')
    h1 = soup.find('h1')
    if not h1:
        return "", ""
    title = h1.get_text(strip=True)

    keyword = ""
    m = re.search(r'梦[见到]([^，。！？]+)', title)
    if m:
        keyword = m.group(1).strip()
        keyword = re.sub(r'(好不好|是什么意思|是什么预兆|代表着什么|代表什么|意味着什么|怎么回事|怎么办|有什么预兆|什么征兆|的意思|是好事还是坏事)$', '', keyword).strip()
    if not keyword:
        keyword = title.strip()

    content_div = (soup.find('div', class_='article_content')
                   or soup.find('div', class_='read-content')
                   or soup.find('div', class_='content')
                   or soup.find('article'))
    if not content_div:
        return keyword.strip(), ""

    for tag in content_div.find_all(['script', 'style', 'ins', 'iframe', 'nav']):
        tag.decompose()

    text = content_div.get_text(separator='\n', strip=True)
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if any(s in line for s in ['当前位置','首页','上一篇','下一篇','相关文章','推荐阅读',
            '免责声明','版权声明','来源：','时间：','责任编辑','周公解梦官网','周公解梦网',
            '网友评论','热门文章','扫一扫','加微信','微信公众号','关注我们','郑重声明',
            '仅供娱乐','请勿盲目迷信']):
            continue
        if re.match(r'^[一-鿿]+ > [一-鿿]', line):
            continue
        lines.append(line)

    return keyword.strip(), '\n'.join(lines)


def save_entries(entries: List[str]):
    """Append entries to output file."""
    if not entries:
        return
    mode = 'a' if OUTPUT.exists() else 'w'
    with open(OUTPUT, mode, encoding='utf-8') as f:
        if OUTPUT.exists() and OUTPUT.stat().st_size > 0:
            f.write('\n\n')
        f.write('\n\n'.join(entries))
    logger.info(f"  Saved {len(entries)} entries to {OUTPUT}")


async def main():
    logger.info("=" * 60)
    logger.info("CRAWL: 12880.com")
    logger.info("=" * 60)

    existing_kws = load_existing_keywords()

    logger.info("Discovering URLs...")
    all_urls = discover_urls()
    logger.info(f"Total URLs: {len(all_urls)}")

    logger.info("Scraping dream pages...")
    client = httpx.AsyncClient(timeout=15, follow_redirects=True,
                               headers={'User-Agent': USER_AGENT})
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    results = []
    seen_kws: Set[str] = set()
    pending_save: List[str] = []
    scanned = 0

    async def scrape_one(url: str):
        nonlocal scanned
        async with sem:
            try:
                await asyncio.sleep(REQUEST_DELAY)
                resp = await client.get(url)
                scanned += 1
                if resp.status_code != 200:
                    return

                kw, text = parse_dream(resp.text)
                if not kw or not text or len(text) < 20:
                    return
                if kw in existing_kws or kw in seen_kws:
                    return

                seen_kws.add(kw)
                line = f"梦见{kw}: {text}"
                results.append(line)
                pending_save.append(line)

                if len(results) % SAVE_EVERY == 0:
                    save_entries(pending_save)
                    logger.info(f"  {len(results)} entries ({scanned}/{len(all_urls)} scanned)")
                    pending_save.clear()

            except Exception as e:
                scanned += 1
                if scanned % 50 == 0:
                    logger.debug(f"  Error count: {scanned}")

    tasks = [scrape_one(url) for url in all_urls]
    await asyncio.gather(*tasks)

    # Final save
    if pending_save:
        save_entries(pending_save)
    await client.aclose()

    logger.info(f"\nDone! {len(results)} entries from 12880.com")
    logger.info(f"Saved to {OUTPUT}")


if __name__ == '__main__':
    asyncio.run(main())
