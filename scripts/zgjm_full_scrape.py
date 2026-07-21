#!/usr/bin/env python3
"""ZGJM full pagination fix — crawl ALL category pages to discover ALL dream entries."""
import asyncio, json, re, time, logging
from pathlib import Path
import httpx
from bs4 import BeautifulSoup

OUT = Path('/home/a/fortune-agent/data/competitor_data')
OUT.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(message)s')
logger = logging.getLogger()

CONCURRENT = 15

CATS = ['/dongwu/','/zhiwu/','/wupin/','/huodong/','/shenghuo/','/ziran/',
        '/guishen/','/jianzhu/','/qita/','/yunfujiemeng/','/mengjing/','/wenhua/','/health/']

async def fetch(client, url):
    try:
        r = await client.get(url, timeout=20)
        return r.text if r.status_code == 200 else None
    except: return None

def clean_text(text):
    skip = {'当前位置','首页','上一篇','下一篇','相关文章','免责声明','版权声明',
            '网友评论','扫一扫','加微信','关注我们','仅供娱乐','请勿盲目迷信','广告','推广'}
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 4: continue
        if any(s in line for s in skip): continue
        lines.append(line)
    return '\n'.join(lines)[:2000]

async def main():
    base = "https://www.zgjmorg.com"
    all_links = []  # (url, category_name)

    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=5)) as client:
        # Phase 1: paginate through ALL category listing pages
        for cat in CATS:
            cat_links = 0
            for page in range(1, 100):
                if page == 1:
                    url = f"{base}{cat}"
                else:
                    url = f"{base}{cat}index_{page}.html"

                html = await fetch(client, url)
                if not html or len(html) < 500:
                    break

                soup = BeautifulSoup(html, 'html.parser')
                page_links = 0
                for a in soup.find_all('a', href=True):
                    h = a['href']
                    if h.endswith('.html') and h.startswith('/') and 'index_' not in h:
                        all_links.append((base + h, cat.strip('/')))
                        page_links += 1

                if page_links == 0:
                    break
                cat_links += page_links

            logger.info(f"[zgjm] {cat}: {cat_links} dream links across {page} pages")

        # Deduplicate
        seen = set()
        unique = []
        for link, cat in all_links:
            if link not in seen:
                seen.add(link)
                unique.append((link, cat))
        logger.info(f"[zgjm] Total unique dream pages: {len(unique)}")

        # Phase 2: scrape all dream pages concurrently
        results = []
        sem = asyncio.Semaphore(CONCURRENT)
        done = 0

        async def scrape_one(item):
            nonlocal done
            async with sem:
                url, cat = item
                html = await fetch(client, url)
                if not html: return
                soup = BeautifulSoup(html, 'html.parser')
                h1 = soup.find('h1')
                content = soup.find('div', class_='read-content')
                if not h1 or not content: return
                title = h1.get_text(strip=True)
                for t in content.find_all(['script','style','ins','iframe']):
                    t.decompose()
                text = clean_text(content.get_text(separator='\n', strip=True))
                if len(text) < 30: return
                m = re.search(r'梦[见到]([^，。！？\s]+)', title)
                kw = m.group(1).strip() if m else title
                results.append({
                    'query': f'梦见{kw}是什么意思', 'platform': 'zgjm',
                    'response': text, 'url': url, 'category': cat,
                    'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                })
                done += 1
                if done % 500 == 0:
                    logger.info(f"[zgjm] Scraped {done}/{len(unique)}")

        tasks = [scrape_one(item) for item in unique]
        await asyncio.gather(*tasks)

        # Save
        path = OUT / f'zgjm_all_{time.strftime("%Y%m%d")}.jsonl'
        with open(path, 'w') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        logger.info(f"[zgjm] DONE: {len(results)} records → {path}")

        # Also update 12880 and buyiju
        logger.info("\nNow 12880...")
        await scrape_12880(client)
        logger.info("\nNow buyiju...")
        await scrape_buyiju(client)

    # Final summary
    total = 0
    for f in sorted(OUT.glob('*')):
        n = sum(1 for _ in open(f))
        total += n
        if n > 0:
            logger.info(f"  {f.name}: {n} records")
    logger.info(f"TOTAL: {total}")

async def scrape_12880(client):
    base = "https://www.12880.com"
    sections = ["/zhougongjiemeng/", "/bazi/", "/xingming/"]
    results = []

    for sec in sections:
        # Try various pagination formats
        for page in range(1, 200):
            if page == 1:
                url = f"{base}{sec}"
            else:
                html_test = None
                for fmt in [f"{base}{sec}index_{page}.html", f"{base}{sec}list_{page}.html",
                           f"{base}{sec}{page}.html"]:
                    t = await fetch(client, fmt)
                    if t and len(t) > 500:
                        url = fmt
                        break
                else:
                    break

            html = await fetch(client, url)
            if not html or len(html) < 500: break
            soup = BeautifulSoup(html, 'html.parser')

            page_results = 0
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '.html' in href and 'index_' not in href and 'list_' not in href:
                    full_url = href if href.startswith('http') else base + href
                    page_html = await fetch(client, full_url)
                    if not page_html: continue
                    psoup = BeautifulSoup(page_html, 'html.parser')
                    title_el = psoup.find('h1') or psoup.find('title')
                    content = psoup.find('div', class_='content') or psoup.find('article')
                    if not title_el or not content: continue
                    title = title_el.get_text(strip=True)[:100]
                    for t in content.find_all(['script','style']): t.decompose()
                    text = clean_text(content.get_text(separator='\n', strip=True))
                    if len(text) < 50: continue
                    results.append({
                        'query': title, 'platform': '12880', 'response': text,
                        'url': full_url, 'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                    })
                    page_results += 1

            if page_results == 0 and page > 1:
                break
            logger.info(f"[12880] {sec} page {page}: {page_results} records")

    path = OUT / f'12880_all_{time.strftime("%Y%m%d")}.jsonl'
    with open(path, 'w') as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    logger.info(f"[12880] DONE: {len(results)} records")

async def scrape_buyiju(client):
    base = "https://www.buyiju.com"
    topics = ["/bazi/", "/cha/xm/", "/jiemeng/", "/fengshui/", "/suanming/", "/huangli/"]
    results = []

    for topic in topics:
        for page in range(1, 60):
            if page == 1:
                url = f"{base}{topic}"
            else:
                url = f"{base}{topic}list_{page}.html"

            html = await fetch(client, url)
            if not html or len(html) < 300: break

            soup = BeautifulSoup(html, 'html.parser')
            page_results = 0
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '.html' in href and 'list_' not in href:
                    full_url = href if href.startswith('http') else base + href
                    page_html = await fetch(client, full_url)
                    if not page_html: continue
                    psoup = BeautifulSoup(page_html, 'html.parser')
                    title_el = psoup.find('h1') or psoup.find('title')
                    content = psoup.find('div', class_='content') or psoup.find('article')
                    if not title_el or not content: continue
                    title = title_el.get_text(strip=True)[:100]
                    for t in content.find_all(['script','style']): t.decompose()
                    text = clean_text(content.get_text(separator='\n', strip=True))
                    if len(text) < 50: continue
                    results.append({
                        'query': title, 'platform': 'buyiju', 'response': text,
                        'url': full_url, 'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                    })
                    page_results += 1

            if page_results == 0 and page > 1:
                break
            logger.info(f"[buyiju] {topic} page {page}: {page_results} records")

    path = OUT / f'buyiju_all_{time.strftime("%Y%m%d")}.jsonl'
    with open(path, 'w') as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    logger.info(f"[buyiju] DONE: {len(results)} records")

if __name__ == '__main__':
    asyncio.run(main())
