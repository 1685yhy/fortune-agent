#!/usr/bin/env python3
"""12880.com full scrape — crawl all dream/fortune entries via pagination."""
import asyncio, json, re, time, logging
from pathlib import Path
import httpx
from bs4 import BeautifulSoup

OUT = Path('/home/a/fortune-agent/data/competitor_data')
OUT.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(message)s')
logger = logging.getLogger()
CONCURRENT = 20

async def fetch(client, url):
    try:
        r = await client.get(url, timeout=20, follow_redirects=True)
        return r.text if r.status_code == 200 and len(r.text) > 500 else None
    except: return None

def clean_text(text):
    skip = {'当前位置','首页','上一篇','下一篇','相关文章','免责声明','版权声明',
            '网友评论','扫一扫','加微信','关注我们','仅供娱乐','请勿盲目迷信','广告'}
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 4: continue
        if any(s in line for s in skip): continue
        lines.append(line)
    return '\n'.join(lines)[:2000]

async def scrape_12880():
    base = "https://www.12880.com"
    results = []
    seen_urls = set()

    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=CONCURRENT)) as client:
        # Phase 1: discover ALL dream entry links from listing pages
        all_links = set()

        # Category listing pages: /jiemeng/c1, /jiemeng/c2, etc.
        for cat_num in range(1, 20):
            cat_url = f"{base}/jiemeng/c{cat_num}"
            cat_links = 0
            for page in range(1, 200):
                if page == 1:
                    url = f"{cat_url}/"
                else:
                    url = f"{cat_url}/index_{page}.html"

                html = await fetch(client, url)
                if not html: break

                soup = BeautifulSoup(html, 'html.parser')
                page_links = 0
                for a in soup.find_all('a', href=True):
                    h = a['href']
                    if '/jiemeng/' in h and re.search(r'/jiemeng/\d+', h):
                        if not h.startswith('http'):
                            h = base + h
                        all_links.add(h)
                        page_links += 1

                if page_links == 0: break
                cat_links += page_links

            if cat_links == 0: break
            logger.info(f"[12880] Category c{cat_num}: {cat_links} links across {page-1} pages")

        logger.info(f"[12880] Total unique dream links: {len(all_links)}")

        # Phase 2: concurrently scrape ALL dream entries
        sem = asyncio.Semaphore(CONCURRENT)
        done = 0
        total = len(all_links)

        async def scrape_one(url):
            nonlocal done
            async with sem:
                html = await fetch(client, url)
                if not html: return
                soup = BeautifulSoup(html, 'html.parser')
                title_el = soup.find('h1') or soup.find('title')
                content = soup.find('div', class_='content') or soup.find('article') or soup.find('div', id='content')
                if not title_el or not content: return
                title = title_el.get_text(strip=True)[:120]
                for t in content.find_all(['script','style','ins']): t.decompose()
                text = clean_text(content.get_text(separator='\n', strip=True))
                if len(text) < 30: return
                results.append({
                    'query': title, 'platform': '12880', 'response': text,
                    'url': url, 'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                })
                done += 1
                if done % 1000 == 0:
                    logger.info(f"[12880] Scraped {done}/{total}")

        tasks = [scrape_one(u) for u in all_links]
        await asyncio.gather(*tasks)

        path = OUT / f'12880_all_{time.strftime("%Y%m%d")}.jsonl'
        with open(path, 'w') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        logger.info(f"[12880] DONE: {len(results)} records")
        return len(results)

async def scrape_zgjm_org():
    """Scrape zgjm.org (different from zgjmorg.com)."""
    base = "https://www.zgjm.org"
    results = []

    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=CONCURRENT)) as client:
        # Discover categories
        html = await fetch(client, base)
        if not html:
            logger.info("[zgjm.org] Homepage not accessible")
            return 0
        soup = BeautifulSoup(html, 'html.parser')
        cats = set()
        for a in soup.find_all('a', href=True):
            h = a['href']
            if h.startswith('/') and h.count('/') == 1 and h != '/':
                cats.add(base + h)
        logger.info(f"[zgjm.org] Found {len(cats)} categories: {list(cats)[:10]}...")

        # Discover all entry links
        all_links = set()
        sem = asyncio.Semaphore(5)
        async def discover_cat(url):
            async with sem:
                for page in range(1, 200):
                    if page == 1:
                        p_url = url
                    else:
                        p_url = f"{url}index_{page}.html"
                    html = await fetch(client, p_url)
                    if not html: break
                    psoup = BeautifulSoup(html, 'html.parser')
                    page_links = 0
                    for a in psoup.find_all('a', href=True):
                        h = a['href']
                        if h.startswith('/') and h.endswith('.html') and 'index_' not in h:
                            all_links.add(base + h)
                            page_links += 1
                    if page_links == 0: break

        await asyncio.gather(*[discover_cat(u) for u in cats])
        logger.info(f"[zgjm.org] Found {len(all_links)} entry links")

        # Scrape all entries
        results = []
        scrape_sem = asyncio.Semaphore(CONCURRENT)
        done = 0
        total = len(all_links)

        async def scrape_one(url):
            nonlocal done
            async with scrape_sem:
                html = await fetch(client, url)
                if not html: return
                psoup = BeautifulSoup(html, 'html.parser')
                title_el = psoup.find('h1') or psoup.find('title')
                content = psoup.find('div', class_='content') or psoup.find('article') or psoup.find('div', id='content')
                if not title_el or not content: return
                title = title_el.get_text(strip=True)[:120]
                for t in content.find_all(['script','style','ins']): t.decompose()
                text = clean_text(content.get_text(separator='\n', strip=True))
                if len(text) < 30: return
                results.append({
                    'query': title, 'platform': 'zgjm_org', 'response': text,
                    'url': url, 'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                })
                done += 1
                if done % 500 == 0:
                    logger.info(f"[zgjm.org] Scraped {done}/{total}")

        tasks = [scrape_one(u) for u in all_links]
        await asyncio.gather(*tasks)

        path = OUT / f'zgjm_org_all_{time.strftime("%Y%m%d")}.jsonl'
        with open(path, 'w') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        logger.info(f"[zgjm.org] DONE: {len(results)} records")
        return len(results)


async def main():
    logger.info("="*60)
    logger.info("12880 + ZGJM.ORG FULL SCRAPE")
    logger.info("="*60)

    n1 = await scrape_12880()
    n2 = await scrape_zgjm_org()

    logger.info(f"\nFinal: 12880={n1}, zgjm.org={n2}, total={n1+n2}")

if __name__ == '__main__':
    asyncio.run(main())
