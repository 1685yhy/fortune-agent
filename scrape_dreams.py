#!/usr/bin/env python3
"""
Comprehensive dream interpretation crawler for 12880.com and zgjmorg.com.

Strategy:
  1. Discover ALL category pages and ALL dream listing pages
  2. Extract ALL individual dream page URLs
  3. Scrape each dream page for keyword + interpretation
  4. Deduplicate against existing data
  5. Save to text files
  6. Chunk and add to ChromaDB
"""

import asyncio
import httpx
import re
import time
import json
import sys
import os
from bs4 import BeautifulSoup
from typing import List, Tuple, Set, Optional
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Add parent to path for imports
sys.path.insert(0, '/home/a/fortune-agent')

# === CONFIG ===
REQUEST_DELAY = 1.0   # seconds between requests (polite crawling)
MAX_CONCURRENT = 3     # concurrent requests
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

OUTPUT_DIR = Path('/mnt/d/fortune-data/books/zonghe')
OUTPUT_12880 = OUTPUT_DIR / '12880_dreams.txt'
OUTPUT_ZGJMORG = OUTPUT_DIR / 'zgjmorg_dreams.txt'

# 12880 categories that have content
CATEGORIES_12880 = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c8', 'c9', 'c10', 'c11', 'c13', 'c14', 'c15', 'c17']

# zgjmorg main categories
ZGJMORG_CATEGORIES = [
    '/dongwu/', '/zhiwu/', '/wupin/', '/huodong/', '/shenghuo/', '/ziran/',
    '/guishen/', '/jianzhu/', '/qita/', '/yunfujiemeng/', '/mengjing/',
    '/wenhua/', '/health/', '/zgjm/',
]

# zgjmorg /renwu/ subcategories (individual pages)
ZGJMORG_RENWU = [
    '/renwu/siren.html', '/renwu/nvren.html', '/renwu/fuqin_baba.html',
    '/renwu/muqin_mama.html', '/renwu/yinger_wawa.html', '/renwu/xiaohai.html',
    '/renwu/yixing.html', '/renwu/anlianderen.html', '/renwu/yachi.html',
    '/renwu/tongshi_gongzuohuoban.html', '/renwu/xue.html', '/renwu/zhangfu.html',
    '/renwu/tongxue.html', '/renwu/qizi_laopo.html', '/renwu/luoti.html',
    '/renwu/laogong.html', '/renwu/qingren.html', '/renwu/pengyou.html',
    '/renwu/jiaren.html', '/renwu/nanpengyou.html', '/renwu/toufa.html',
    '/renwu/erzi.html', '/renwu/rensiliao.html', '/renwu/qingrenyixing.html',
    '/renwu/xiongdijiemei.html', '/renwu/yunfu.html', '/renwu/nanhai.html',
    '/renwu/haizi.html', '/renwu/xiaonanhai.html', '/renwu/shiti.html',
    '/renwu/changbei.html', '/renwu/xiaonvhai.html', '/renwu/nver.html',
    '/renwu/moshengren.html', '/renwu/nanren.html', '/renwu/qinqi.html',
    '/renwu/rufang.html', '/renwu/airen_lianren.html', '/renwu/shuren.html',
    '/renwu/shangsi.html', '/renwu/xiaotou.html', '/renwu/babamama.html',
    '/renwu/jiejie.html', '/renwu/banlv_airen.html', '/renwu/lingdao.html',
    '/renwu/laoren.html',
]


def load_existing_keywords() -> Set[str]:
    """Extract all 梦见 keywords from existing dream files for deduplication."""
    keywords = set()
    for f in OUTPUT_DIR.glob('*.txt'):
        if f.name in ('12880_dreams.txt', 'zgjmorg_dreams.txt', 'new_dreams_combined.txt'):
            continue
        try:
            text = f.read_text(encoding='utf-8')
            found = re.findall(r'梦见(\w+)', text)
            for kw in found:
                keywords.add(kw)
        except Exception:
            pass
    logger.info(f"Loaded {len(keywords)} existing dream keywords for dedup")
    return keywords


# ============================================================
# SITE 1: www.12880.com
# ============================================================

def discover_12880_urls() -> List[str]:
    """Discover ALL 12880 dream URLs from all categories and fotaojiemeng."""
    all_urls = set()

    # --- Regular categories ---
    for cat in CATEGORIES_12880:
        url = f'https://www.12880.com/jiemeng/{cat}'
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True,
                          headers={'User-Agent': USER_AGENT})
            links = set(re.findall(r'/jiemeng/(\d+-[^"\'<>]+)', r.text))
            for link in links:
                all_urls.add(f'https://www.12880.com/jiemeng/{link}')
            logger.info(f"12880 category {cat}: {len(links)} URLs")
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"Error fetching category {cat}: {e}")

    # --- Fotaojiemeng (20 pages × ~15 entries each) ---
    fotao_urls = set()
    # Scan all pages until we find an empty one
    for page in range(1, 100):
        paginated_url = f'https://www.12880.com/fotaojiemeng/list_4_{page}.html'
        if page == 1:
            paginated_url = 'https://www.12880.com/fotaojiemeng/'
        try:
            r = httpx.get(paginated_url, timeout=15, follow_redirects=True,
                          headers={'User-Agent': USER_AGENT})
            links = set(re.findall(r'/fotaojiemeng/(\d+\.html)', r.text))
            if not links:
                logger.info(f"  Fotao page {page}: empty (done)")
                break
            for link in links:
                full_url = f'https://www.12880.com/fotaojiemeng/{link}'
                if full_url not in fotao_urls:
                    fotao_urls.add(full_url)
            logger.info(f"  Fotao page {page}: {len(links)} URLs (total fotao: {len(fotao_urls)})")
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"Error fetching fotao page {page}: {e}")
            break

    all_urls.update(fotao_urls)
    logger.info(f"12880 total: {len(all_urls)} URLs ({len(fotao_urls)} from fotao)")
    return sorted(all_urls)


def parse_12880_dream(html: str) -> Tuple[str, str]:
    """Parse a 12880 dream page. Returns (keyword, interpretation_text)."""
    soup = BeautifulSoup(html, 'html.parser')

    h1 = soup.find('h1')
    if not h1:
        return "", ""
    title = h1.get_text(strip=True)

    # Extract keyword from title
    keyword = ""
    m = re.search(r'梦[见到]([^，。！？]+)', title)
    if m:
        keyword = m.group(1).strip()
        keyword = re.sub(r'(好不好|是什么意思|是什么预兆|代表着什么|代表什么|意味着什么|怎么回事|怎么办|有什么预兆|什么征兆|的意思|是好事还是坏事)$', '', keyword).strip()
    if not keyword:
        keyword = title.strip()

    # Content
    content_div = (soup.find('div', class_='article_content')
                   or soup.find('div', class_='read-content')
                   or soup.find('div', class_='content')
                   or soup.find('article'))

    if not content_div:
        return keyword.strip(), ""

    for tag in content_div.find_all(['script', 'style', 'ins', 'iframe', 'nav']):
        tag.decompose()

    text = content_div.get_text(separator='\n', strip=True)

    # Clean up
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if any(skip in line for skip in [
            '当前位置', '首页', '上一篇', '下一篇', '相关文章', '推荐阅读',
            '免责声明', '版权声明', '来源：', '时间：', '责任编辑',
            '周公解梦官网', '周公解梦网', '网友评论', '热门文章', '大家都在看',
            '扫一扫', '加微信', '微信公众号', '关注我们', '更多精彩',
            '郑重声明', '仅供娱乐', '请勿盲目迷信', '原文链接',
        ]):
            continue
        lines.append(line)

    text = '\n'.join(lines)
    text = re.sub(r'本文来自.*', '', text)
    text = re.sub(r'更多.*解梦.*', '', text)

    return keyword.strip(), text.strip()


# ============================================================
# SITE 2: www.zgjmorg.com
# ============================================================

def discover_zgjmorg_urls() -> List[str]:
    """Discover ALL zgjmorg dream URLs from all categories."""
    all_urls = set()

    # --- Main categories ---
    for cat in ZGJMORG_CATEGORIES:
        url = f'https://www.zgjmorg.com{cat}'
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True,
                          headers={'User-Agent': USER_AGENT})
            links = set()
            for m in re.finditer(r'href="([^"]*\.html)"', r.text):
                link = m.group(1)
                if 'index_' in link or 'zgjmorg' in link:
                    continue
                if link.startswith('/'):
                    links.add(f'https://www.zgjmorg.com{link}')
                elif link.startswith('http'):
                    links.add(link)
                else:
                    links.add(f'https://www.zgjmorg.com/{link}')

            prev_count = len(all_urls)
            all_urls.update(links)
            new_count = len(all_urls) - prev_count
            logger.info(f"zgjmorg {cat}: {len(links)} URLs ({new_count} new)")
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"Error fetching {cat}: {e}")

    # --- /renwu/ subcategories (individual pages) ---
    for sub in ZGJMORG_RENWU:
        url = f'https://www.zgjmorg.com{sub}'
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True,
                          headers={'User-Agent': USER_AGENT})
            links = set()
            for m in re.finditer(r'href="([^"]*\.html)"', r.text):
                link = m.group(1)
                if 'index_' in link or 'zgjmorg' in link:
                    continue
                if link.startswith('/'):
                    links.add(f'https://www.zgjmorg.com{link}')
                elif link.startswith('http'):
                    links.add(link)
                else:
                    links.add(f'https://www.zgjmorg.com/{link}')

            prev_count = len(all_urls)
            all_urls.update(links)
            new_count = len(all_urls) - prev_count
            logger.info(f"zgjmorg {sub}: {len(links)} URLs ({new_count} new)")
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"Error fetching {sub}: {e}")

    logger.info(f"zgjmorg total: {len(all_urls)} URLs")
    return sorted(all_urls)


def parse_zgjmorg_dream(html: str) -> Tuple[str, str]:
    """Parse a zgjmorg dream page. Returns (keyword, interpretation_text)."""
    soup = BeautifulSoup(html, 'html.parser')

    h1 = soup.find('h1')
    if not h1:
        return "", ""
    title = h1.get_text(strip=True)

    keyword = ""
    m = re.search(r'梦[见到]([^，。！？]+)', title)
    if m:
        keyword = m.group(1).strip()
        keyword = re.sub(r'(好不好|是什么意思|是什么预兆|代表着什么|代表什么|意味着什么|怎么回事|怎么办|有什么预兆|什么征兆|的意思)$', '', keyword).strip()
    if not keyword:
        keyword = title.strip()

    content_div = soup.find('div', class_='read-content')
    if not content_div:
        return keyword.strip(), ""

    for tag in content_div.find_all(['script', 'style', 'ins', 'iframe']):
        tag.decompose()

    text = content_div.get_text(separator='\n', strip=True)

    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if any(skip in line for skip in [
            '当前位置', '首页', '上一篇', '下一篇', '相关文章', '推荐阅读',
            '免责声明', '版权声明', '来源：', '时间：', '责任编辑',
            '周公解梦官网', '网友评论', '热门文章', '大家都在看',
            '扫一扫', '加微信', '微信公众号', '关注我们', '更多精彩',
            '郑重声明', '仅供娱乐', '请勿盲目迷信',
        ]):
            continue
        lines.append(line)

    text = '\n'.join(lines)
    text = re.sub(r'原文链接.*', '', text)
    text = re.sub(r'本文来自.*', '', text)

    return keyword.strip(), text.strip()


# ============================================================
# ASYNC SCRAPER
# ============================================================

async def scrape_all(urls: List[str], site_name: str, parse_fn,
                     existing_keywords: Set[str]) -> List[dict]:
    """Scrape all URLs with rate limiting and dedup."""
    results = []
    seen_keywords: Set[str] = set()
    errors = 0

    client = httpx.AsyncClient(
        timeout=15,
        follow_redirects=True,
        headers={'User-Agent': USER_AGENT}
    )
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def scrape_one(url: str, idx: int, total: int):
        nonlocal errors
        async with sem:
            try:
                await asyncio.sleep(REQUEST_DELAY)
                resp = await client.get(url)
                if resp.status_code != 200:
                    return

                keyword, text = parse_fn(resp.text)
                if not keyword or not text or len(text) < 20:
                    return

                # Dedup against existing and ourselves
                if keyword in existing_keywords or keyword in seen_keywords:
                    return

                seen_keywords.add(keyword)
                results.append({
                    'keyword': keyword,
                    'text': text,
                    'url': url,
                    'site': site_name,
                })

                if len(results) % 200 == 0:
                    logger.info(f"  [{site_name}] {len(results)} entries scraped (scanned {idx+1}/{total})")

            except httpx.TimeoutException:
                errors += 1
                if errors % 20 == 0:
                    logger.debug(f"  [{site_name}] {errors} timeouts so far")
            except Exception as e:
                errors += 1
                if errors <= 5:
                    logger.debug(f"  [{site_name}] Error on {url}: {e}")

    # Process in batches for memory efficiency
    batch_size = 3000
    for start in range(0, len(urls), batch_size):
        batch = urls[start:start+batch_size]
        logger.info(f"  [{site_name}] Processing batch {start//batch_size + 1} ({len(batch)} URLs, {len(results)} results so far)")
        tasks = [scrape_one(url, i + start, len(urls)) for i, url in enumerate(batch)]
        await asyncio.gather(*tasks)

    await client.aclose()
    logger.info(f"  [{site_name}] Done: {len(results)} entries, {errors} errors")
    return results


# ============================================================
# SAVE & INDEX
# ============================================================

def save_entries(entries: List[dict], filepath: Path, site_name: str):
    """Save entries to a text file."""
    new_lines = []
    existing_kws = set()

    if filepath.exists():
        existing_text = filepath.read_text(encoding='utf-8')
        existing_kws = set(re.findall(r'梦见(\w+)', existing_text))

    for entry in entries:
        kw = entry['keyword']
        if kw in existing_kws:
            continue
        new_lines.append(f"梦见{kw}: {entry['text']}")
        existing_kws.add(kw)

    if not new_lines:
        logger.info(f"No new entries to save to {filepath}")
        return

    mode = 'a' if filepath.exists() else 'w'
    with open(filepath, mode, encoding='utf-8') as f:
        if filepath.exists() and filepath.stat().st_size > 0:
            f.write('\n\n')
        f.write('\n\n'.join(new_lines))

    logger.info(f"Saved {len(new_lines)} new entries to {filepath}")


def chunk_and_index(entries: List[dict], source: str):
    """Chunk entries and add to ChromaDB."""
    try:
        from src.rag.chunker import chunk_text
        from src.rag.retriever import Retriever
        from src.rag.embedder import Embedder

        embedder = Embedder()
        retriever = Retriever('/mnt/d/fortune-data/vectordb/', embedder)

        all_chunks = []
        for entry in entries:
            full_text = f"梦见{entry['keyword']}: {entry['text']}"
            chunks = chunk_text(
                text=full_text,
                source=source,
                author="周公解梦",
                category="dream",
                chunk_size=500,
                overlap=50,
            )
            all_chunks.extend(chunks)

        # Dedup by chunk_id
        seen_ids = set()
        unique_chunks = []
        for c in all_chunks:
            if c.chunk_id not in seen_ids:
                seen_ids.add(c.chunk_id)
                unique_chunks.append(c)

        if unique_chunks:
            retriever.add_chunks(unique_chunks, batch_size=128)
            logger.info(f"Added {len(unique_chunks)} chunks to ChromaDB (from {len(entries)} entries)")

        return retriever.count()
    except Exception as e:
        logger.warning(f"Error indexing: {e}")
        import traceback
        traceback.print_exc()
        return 0


# ============================================================
# MAIN
# ============================================================

async def main():
    logger.info("=" * 70)
    logger.info("COMPREHENSIVE DREAM CRAWLER")
    logger.info("Targets: www.12880.com + www.zgjmorg.com")
    logger.info("=" * 70)

    # Load existing keywords for dedup
    existing_keywords = load_existing_keywords()

    # ========== PHASE 1: Discover ALL URLs ==========
    logger.info("\n>>> PHASE 1: Discovering ALL dream URLs <<<")

    logger.info("\n--- Discovering 12880.com URLs ---")
    urls_12880 = discover_12880_urls()

    logger.info("\n--- Discovering zgjmorg.com URLs ---")
    urls_zgjmorg = discover_zgjmorg_urls()

    total_urls = len(urls_12880) + len(urls_zgjmorg)
    logger.info(f"\nTotal URLs discovered: {total_urls} ({len(urls_12880)} from 12880, {len(urls_zgjmorg)} from zgjmorg)")

    # ========== PHASE 2: Scrape ALL Dream Pages ==========
    logger.info("\n>>> PHASE 2: Scraping ALL dream pages <<<")

    logger.info("\n--- Scraping 12880.com ---")
    entries_12880 = await scrape_all(urls_12880, '12880', parse_12880_dream, existing_keywords)

    # Update existing_keywords with what we just scraped
    for e in entries_12880:
        existing_keywords.add(e['keyword'])

    logger.info("\n--- Scraping zgjmorg.com ---")
    entries_zgjmorg = await scrape_all(urls_zgjmorg, 'zgjmorg', parse_zgjmorg_dream, existing_keywords)

    all_entries = entries_12880 + entries_zgjmorg
    logger.info(f"\nTotal new dream entries scraped: {len(all_entries)}")

    # ========== PHASE 3: Save files ==========
    logger.info("\n>>> PHASE 3: Saving files <<<")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if entries_12880:
        save_entries(entries_12880, OUTPUT_12880, '12880')
    if entries_zgjmorg:
        save_entries(entries_zgjmorg, OUTPUT_ZGJMORG, 'zgjmorg')

    # ========== PHASE 4: Chunk and Index ==========
    logger.info("\n>>> PHASE 4: Indexing to ChromaDB <<<")

    # Get count before
    count_before = 0
    try:
        from src.rag.embedder import Embedder
        from src.rag.retriever import Retriever
        e = Embedder()
        r = Retriever('/mnt/d/fortune-data/vectordb/', e)
        count_before = r.count()
        logger.info(f"ChromaDB count before: {count_before}")
    except Exception as ex:
        logger.warning(f"Cannot get initial count: {ex}")

    # Index 12880 entries
    if entries_12880:
        chunk_and_index(entries_12880, '12880_dreams.txt')

    # Index zgjmorg entries
    if entries_zgjmorg:
        chunk_and_index(entries_zgjmorg, 'zgjmorg_dreams.txt')

    # ========== REPORT ==========
    count_after = 0
    try:
        from src.rag.embedder import Embedder
        from src.rag.retriever import Retriever
        e = Embedder()
        r = Retriever('/mnt/d/fortune-data/vectordb/', e)
        count_after = r.count()
    except Exception:
        pass

    logger.info("\n" + "=" * 70)
    logger.info("CRAWL COMPLETE - FINAL REPORT")
    logger.info("=" * 70)
    logger.info(f"12880.com URLs discovered:    {len(urls_12880)}")
    logger.info(f"zgjmorg.com URLs discovered:  {len(urls_zgjmorg)}")
    logger.info(f"12880.com new entries:         {len(entries_12880)}")
    logger.info(f"zgjmorg.com new entries:       {len(entries_zgjmorg)}")
    logger.info(f"Total new dream entries:       {len(all_entries)}")
    logger.info(f"ChromaDB count before:         {count_before}")
    logger.info(f"ChromaDB count after:          {count_after}")
    logger.info(f"New RAG documents:             {count_after - count_before}")
    logger.info(f"Output files:")
    logger.info(f"  {OUTPUT_12880}")
    logger.info(f"  {OUTPUT_ZGJMORG}")

    if all_entries:
        logger.info("\nSample entries:")
        for entry in all_entries[:10]:
            logger.info(f"  - 梦见{entry['keyword']} ({entry['site']})")


if __name__ == '__main__':
    asyncio.run(main())
