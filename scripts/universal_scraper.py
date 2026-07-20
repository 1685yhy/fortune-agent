#!/usr/bin/env python3
"""Universal scraper for Chinese fortune-telling sites — auto-detect structure, collect all content."""
import asyncio, json, re, time, logging
from pathlib import Path
import httpx
from bs4 import BeautifulSoup

OUT = Path('/home/a/fortune-agent/data/competitor_data')
OUT.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(message)s')
logger = logging.getLogger()
CONCURRENT = 15

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

async def fetch(client, url):
    try:
        r = await client.get(url, timeout=20, follow_redirects=True, headers=HEADERS)
        return r.text if r.status_code == 200 and len(r.text) > 500 else None
    except:
        return None

def clean_text(text):
    skip = {'当前位置','首页','上一篇','下一篇','相关文章','免责声明','版权声明',
            '网友评论','扫一扫','加微信','关注我们','仅供娱乐','请勿盲目迷信','广告',
            '微信','QQ','微博','分享到','举报','报错','投诉'}
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 4: continue
        if any(s in line for s in skip): continue
        lines.append(line)
    return '\n'.join(lines)[:2000]

# ─── Site configurations ───────────────────────────────

SITES = {
    "smxs": {
        "name": "水墨先生",
        "base": "https://www.smxs.com",
        "sections": ["/bazi/", "/jiemeng/", "/fengshui/", "/suanming/", "/huangli/",
                     "/xingming/", "/chouqian/", "/peidui/", "/shengxiao/"],
        "link_patterns": [r'\.html', r'\.htm'],
        "listing_pages": 100,
    },
    "k366": {
        "name": "华易网",
        "base": "https://www.k366.com",
        "sections": ["/bazi/", "/jiemeng/", "/fengshui/", "/suanming/", "/huangli/",
                     "/xingming/", "/chenggu/"],
        "link_patterns": [r'\.html', r'\.htm'],
        "listing_pages": 100,
    },
    "zhouyisuanming": {
        "name": "周易算命网",
        "base": "https://www.zhouyisuanming.net",
        "sections": ["/bazi/", "/jiemeng/", "/fengshui/", "/suanming/"],
        "link_patterns": [r'\.html'],
        "listing_pages": 80,
    },
    "aqioo": {
        "name": "阿启网",
        "base": "https://www.aqioo.com",
        "sections": ["/bazi/", "/jiemeng/", "/suanming/", "/xingming/", "/fengshui/", "/shengxiao/"],
        "link_patterns": [r'\.html', r'\.htm', r'\.asp'],
        "listing_pages": 80,
    },
    "daosuan": {
        "name": "道算网",
        "base": "https://www.daosuan.cn",
        "sections": ["/bazi/", "/jiemeng/", "/fengshui/", "/suanming/", "/xingming/"],
        "link_patterns": [r'\.html'],
        "listing_pages": 80,
    },
    "zhouyihui": {
        "name": "周易汇",
        "base": "https://www.zhouyihui.com",
        "sections": ["/bazi/", "/jiemeng/", "/fengshui/", "/suanming/"],
        "link_patterns": [r'\.html'],
        "listing_pages": 80,
    },
    "64gua": {
        "name": "周易天地",
        "base": "https://www.64gua.com",
        "sections": ["/bazi/", "/jiemeng/", "/fengshui/", "/suanming/"],
        "link_patterns": [r'\.html'],
        "listing_pages": 80,
    },
    "dajiazhao": {
        "name": "大家找算命",
        "base": "https://www.dajiazhao.com",
        "sections": ["/bazi/", "/jiemeng/", "/fengshui/", "/suanming/"],
        "link_patterns": [r'\.html'],
        "listing_pages": 80,
    },
}

async def scrape_site(client, site_key, cfg):
    """Universal scraper: discover links via pagination, then scrape all."""
    base = cfg["base"]
    name = cfg["name"]
    results = []
    all_links = set()

    # Phase 1: Discover all content links
    for section in cfg["sections"]:
        sec_links = 0
        for page in range(1, cfg["listing_pages"] + 1):
            if page == 1:
                url = f"{base}{section}"
            else:
                url = f"{base}{section}index_{page}.html"
                html = await fetch(client, url)
                if not html:
                    # Try alternate pagination formats
                    for fmt in [f"{base}{section}list_{page}.html",
                               f"{base}{section}{page}.html",
                               f"{base}{section}page/{page}/"]:
                        html = await fetch(client, fmt)
                        if html:
                            url = fmt
                            break
                    if not html:
                        break

            if page > 1 and not html:
                break

            soup = BeautifulSoup(html, 'html.parser')
            page_links = 0
            for a in soup.find_all('a', href=True):
                href = a['href']
                # Only collect links that look like content pages
                is_content = False
                for pat in cfg["link_patterns"]:
                    if re.search(pat, href):
                        is_content = True
                        break
                if not is_content: continue
                # Skip pagination links
                if any(s in href for s in ['index_', 'list_', '/page/']): continue

                if href.startswith('/'):
                    full = base + href
                elif href.startswith('http'):
                    full = href
                else:
                    full = f"{base}/{href}"
                all_links.add(full)
                page_links += 1

            if page_links == 0:
                break
            sec_links += page_links

        if sec_links > 0:
            logger.info(f"[{name}] {section}: {sec_links} links across {page-1} pages")

    total = len(all_links)
    logger.info(f"[{name}] Total unique links: {total}")

    if total == 0:
        # Fallback: just crawl the homepage and all linked pages deeply
        html = await fetch(client, base)
        if html:
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if href.startswith('/'):
                    all_links.add(base + href)

        total = len(all_links)
        logger.info(f"[{name}] Homepage fallback: {total} links")

    # Phase 2: Scrape all content pages
    sem = asyncio.Semaphore(CONCURRENT)
    done = [0]

    async def scrape_one(url):
        async with sem:
            html = await fetch(client, url)
            if not html: return
            soup = BeautifulSoup(html, 'html.parser')
            h1 = soup.find('h1')
            if not h1: return
            title = h1.get_text(strip=True)[:120]

            # Find main content — try many selectors
            content = None
            for sel in ['div.read-content', 'div.article_content', 'div.h2_content',
                       'div.cont', 'div.content', 'article', 'div.main-content',
                       'div.entry-content', 'div.post-content', 'div.art_content',
                       'div.con', 'div#content', 'div.main', 'div.col_left']:
                tag, _, cls = sel.partition('.')
                id_ = None
                if '#' in sel:
                    tag, _, id_ = sel.partition('#')
                if cls:
                    content = soup.find(tag, class_=cls)
                elif id_:
                    content = soup.find(tag, id=id_)
                else:
                    content = soup.find(tag)
                if content and len(content.get_text(strip=True)) > 50:
                    break

            if not content:
                # Last resort: get body text
                body = soup.find('body')
                if body:
                    content = body

            if not content: return

            for t in content.find_all(['script','style','ins','iframe','nav','footer','header']):
                t.decompose()

            text = clean_text(content.get_text(separator='\n', strip=True))
            if len(text) < 30: return

            results.append({
                'query': title, 'platform': site_key.split('_')[0],
                'response': text, 'url': url,
                'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S')
            })
            done[0] += 1
            if done[0] % 500 == 0:
                logger.info(f"[{name}] Scraped {done[0]}/{total}")

    tasks = [scrape_one(u) for u in all_links]
    await asyncio.gather(*tasks)

    # Save
    path = OUT / f'{site_key}_full_{time.strftime("%Y%m%d")}.jsonl'
    with open(path, 'w') as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    logger.info(f"[{name}] DONE: {len(results)} records → {path}")
    return len(results)

async def main():
    logger.info("=" * 60)
    logger.info("UNIVERSAL FORTUNE SITE SCRAPER — all working sites")
    logger.info("=" * 60)

    total = 0
    async with httpx.AsyncClient(timeout=25, verify=False,
                                  limits=httpx.Limits(max_connections=CONCURRENT),
                                  headers=HEADERS) as client:
        for key, cfg in SITES.items():
            try:
                n = await scrape_site(client, key, cfg)
                total += n
            except Exception as e:
                logger.error(f"[{cfg['name']}] FAILED: {e}")

    logger.info(f"\n{'=' * 60}")
    logger.info(f"TOTAL: {total} records from {len(SITES)} sites")
    for f in sorted(OUT.glob('*full*.jsonl')):
        n = sum(1 for _ in open(f))
        if n > 0:
            logger.info(f"  {f.name}: {n} records")

if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings()
    asyncio.run(main())
