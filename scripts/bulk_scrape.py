#!/usr/bin/env python3
"""Bulk scraper — concurrent, deep crawl, collect ALL competitor content."""
import asyncio, json, re, sys, time, logging
from pathlib import Path
import httpx
from bs4 import BeautifulSoup

OUT = Path('/home/a/fortune-agent/data/competitor_data')
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(message)s]')
logger = logging.getLogger()

CONCURRENT = 10  # concurrent requests

async def fetch(client, url):
    try:
        r = await client.get(url, timeout=30)
        if r.status_code == 200:
            return r.text
        return None
    except:
        return None

def clean_text(text):
    skip = {'当前位置','首页','上一篇','下一篇','相关文章','免责声明','版权声明',
            '网友评论','扫一扫','加微信','关注我们','仅供娱乐','请勿盲目迷信',
            '广告','推广','赞助'}
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 4:
            continue
        if any(s in line for s in skip):
            continue
        lines.append(line)
    return '\n'.join(lines)[:2000]

# ─── ZGJM 周公解梦 ────────────────────────────────
async def crawl_zgjm():
    base = "https://www.zgjmorg.com"
    cats = ["/dongwu/","/zhiwu/","/wupin/","/huodong/","/shenghuo/","/ziran/",
            "/guishen/","/jianzhu/","/qita/","/yunfujiemeng/","/mengjing/","/wenhua/","/health/"]

    all_links = set()
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=CONCURRENT)) as client:
        # Phase 1: discover all page links
        logger.info(f"[zgjm] Discovering links from {len(cats)} categories...")
        for cat in cats:
            html = await fetch(client, base + cat)
            if not html: continue
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a', href=True):
                h = a['href']
                if h.endswith('.html') and h.startswith('/') and 'index_' not in h:
                    all_links.add(base + h)
        logger.info(f"[zgjm] Found {len(all_links)} dream pages")

        # Phase 2: concurrently scrape all pages
        results = []
        links = list(all_links)
        sem = asyncio.Semaphore(CONCURRENT)

        async def scrape_one(url):
            async with sem:
                html = await fetch(client, url)
                if not html: return
                soup = BeautifulSoup(html, 'html.parser')
                h1 = soup.find('h1')
                content = soup.find('div', class_='read-content')
                if not h1 or not content: return
                title = h1.get_text(strip=True)
                for t in soup.find_all(['script','style','ins','iframe']):
                    t.decompose()
                text = clean_text(content.get_text(separator='\n', strip=True))
                if len(text) < 30: return
                m = re.search(r'梦[见到]([^，。！？\s]+)', title)
                kw = m.group(1).strip() if m else title
                results.append({
                    'query': f'梦见{kw}是什么意思',
                    'platform': 'zgjm', 'response': text, 'url': url,
                    'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                })

        tasks = [scrape_one(u) for u in links]
        await asyncio.gather(*tasks)

        # Save
        path = OUT / f'zgjm_full_{time.strftime("%Y%m%d")}.jsonl'
        with open(path, 'w') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        logger.info(f"[zgjm] Saved {len(results)} records to {path}")
        return len(results)

# ─── 12880.com ────────────────────────────────────
async def crawl_12880():
    base = "https://www.12880.com"
    sections = [
        ("/zhougongjiemeng/", "dream"),
        ("/bazi/", "bazi"),
        ("/xingming/", "xingming"),
    ]

    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=CONCURRENT)) as client:
        all_links = set()
        for path, cat in sections:
            for page in range(1, 50):  # 50 pages per section
                url = f"{base}{path}" if page == 1 else f"{base}{path}index_{page}.html"
                html = await fetch(client, url)
                if not html: break
                soup = BeautifulSoup(html, 'html.parser')
                for a in soup.find_all('a', href=True):
                    h = a['href']
                    if h.endswith('.html') and h.startswith('/') and 'index_' not in h:
                        all_links.add((base + h, cat))

        logger.info(f"[12880] Found {len(all_links)} pages")

        results = []
        sem = asyncio.Semaphore(CONCURRENT)

        async def scrape_one(item):
            async with sem:
                url, cat = item
                html = await fetch(client, url)
                if not html: return
                soup = BeautifulSoup(html, 'html.parser')
                title_el = soup.find('h1') or soup.find('title')
                content = soup.find('div', class_='content') or soup.find('article') or soup.find('div', id='content')
                if not title_el or not content: return
                title = title_el.get_text(strip=True)
                for t in content.find_all(['script','style']):
                    t.decompose()
                text = clean_text(content.get_text(separator='\n', strip=True))
                if len(text) < 50: return
                results.append({
                    'query': title, 'platform': '12880', 'response': text,
                    'url': url, 'category': cat,
                    'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                })

        tasks = [scrape_one(item) for item in all_links]
        await asyncio.gather(*tasks)

        path = OUT / f'12880_full_{time.strftime("%Y%m%d")}.jsonl'
        with open(path, 'w') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        logger.info(f"[12880] Saved {len(results)} records")
        return len(results)

# ─── buyiju 卜易居 ──────────────────────────────────
async def crawl_buyiju():
    urls = [
        "https://www.buyiju.com/bazi/", "https://www.buyiju.com/cha/xm/",
        "https://www.buyiju.com/jiemeng/", "https://www.buyiju.com/fengshui/",
    ]

    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=CONCURRENT)) as client:
        all_links = set()
        for start_url in urls:
            for page in range(1, 60):
                url = start_url if page == 1 else f"{start_url}list_{page}.html"
                html = await fetch(client, url)
                if not html: break
                soup = BeautifulSoup(html, 'html.parser')
                for a in soup.find_all('a', href=True):
                    h = a['href']
                    if '.html' in h and 'list_' not in h:
                        if h.startswith('/'):
                            h = f"https://www.buyiju.com{h}"
                        elif not h.startswith('http'):
                            h = f"{start_url.rstrip('/')}/{h}"
                        all_links.add(h)

        logger.info(f"[buyiju] Found {len(all_links)} pages")

        results = []
        sem = asyncio.Semaphore(CONCURRENT)

        async def scrape_one(url):
            async with sem:
                html = await fetch(client, url)
                if not html: return
                soup = BeautifulSoup(html, 'html.parser')
                title_el = soup.find('h1') or soup.find('title')
                content = soup.find('div', class_='content') or soup.find('article') or soup.find('div', id='content')
                if not title_el or not content: return
                title = title_el.get_text(strip=True)
                for t in content.find_all(['script','style']):
                    t.decompose()
                text = clean_text(content.get_text(separator='\n', strip=True))
                if len(text) < 50: return
                results.append({
                    'query': title, 'platform': 'buyiju', 'response': text,
                    'url': url, 'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                })

        tasks = [scrape_one(u) for u in all_links]
        await asyncio.gather(*tasks)

        path = OUT / f'buyiju_full_{time.strftime("%Y%m%d")}.jsonl'
        with open(path, 'w') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        logger.info(f"[buyiju] Saved {len(results)} records")
        return len(results)

# ─── shen88 神巴巴 ──────────────────────────────────
async def crawl_shen88():
    urls = [
        "https://www.shen88.cn/bazi/", "https://www.shen88.cn/ziwei/",
        "https://www.shen88.cn/jiemeng/", "https://www.shen88.cn/fengshui/",
    ]

    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=CONCURRENT)) as client:
        all_links = set()
        for start_url in urls:
            for page in range(1, 50):
                url = start_url if page == 1 else f"{start_url}index_{page}.html"
                html = await fetch(client, url)
                if not html: break
                soup = BeautifulSoup(html, 'html.parser')
                for a in soup.find_all('a', href=True):
                    h = a['href']
                    if '.html' in h and 'index_' not in h:
                        if h.startswith('/'):
                            h = f"https://www.shen88.cn{h}"
                        elif not h.startswith('http'):
                            h = f"{start_url.rstrip('/')}/{h}"
                        all_links.add(h)

        logger.info(f"[shen88] Found {len(all_links)} pages")

        results = []
        sem = asyncio.Semaphore(CONCURRENT)

        async def scrape_one(url):
            async with sem:
                html = await fetch(client, url)
                if not html: return
                soup = BeautifulSoup(html, 'html.parser')
                title_el = soup.find('h1') or soup.find('title')
                content = soup.find('div', class_='content') or soup.find('article')
                if not title_el or not content: return
                title = title_el.get_text(strip=True)
                for t in content.find_all(['script','style']):
                    t.decompose()
                text = clean_text(content.get_text(separator='\n', strip=True))
                if len(text) < 50: return
                results.append({
                    'query': title, 'platform': 'shen88', 'response': text,
                    'url': url, 'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                })

        tasks = [scrape_one(u) for u in all_links]
        await asyncio.gather(*tasks)

        path = OUT / f'shen88_full_{time.strftime("%Y%m%d")}.jsonl'
        with open(path, 'w') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        logger.info(f"[shen88] Saved {len(results)} records")
        return len(results)

# ─── Main ──────────────────────────────────────────
async def main():
    logger.info("="*60)
    logger.info("BULK COMPETITOR SCRAPER — deep crawl mode")
    logger.info("="*60)

    tasks = [
        crawl_zgjm(),
        crawl_12880(),
        crawl_buyiju(),
        crawl_shen88(),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total = 0
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error(f"Platform {i}: FAILED — {r}")
        else:
            total += r

    logger.info(f"\n{'='*60}")
    logger.info(f"TOTAL: {total} records across all platforms")
    logger.info(f"Output: {OUT}/")
    for f in sorted(OUT.glob('*')):
        logger.info(f"  {f.name}: {sum(1 for _ in open(f))} records")

if __name__ == '__main__':
    asyncio.run(main())
