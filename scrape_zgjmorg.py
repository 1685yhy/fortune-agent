#!/usr/bin/env python3
"""
Crawl www.zgjmorg.com - ALL dream entries with incremental saving.
Saves entries every 500 scraped pages.
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

OUTPUT = Path('/mnt/d/fortune-data/books/zonghe/zgjmorg_dreams.txt')
SAVE_EVERY = 500

# All categories and subcategories
CATEGORIES = [
    '/dongwu/', '/zhiwu/', '/wupin/', '/huodong/', '/shenghuo/', '/ziran/',
    '/guishen/', '/jianzhu/', '/qita/', '/yunfujiemeng/', '/mengjing/',
    '/wenhua/', '/health/', '/zgjm/',
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
    keywords = set()
    for f in Path('/mnt/d/fortune-data/books/zonghe').glob('*.txt'):
        if f.name == 'zgjmorg_dreams.txt':
            continue
        try:
            found = re.findall(r'梦见(\w+)', f.read_text(encoding='utf-8'))
            keywords.update(found)
        except:
            pass
    if OUTPUT.exists():
        found = re.findall(r'梦见(\w+)', OUTPUT.read_text(encoding='utf-8'))
        keywords.update(found)
    logger.info(f"Loaded {len(keywords)} existing keywords")
    return keywords


def discover_urls() -> List[str]:
    all_urls = set()
    for cat in CATEGORIES:
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

            prev = len(all_urls)
            all_urls.update(links)
            new_count = len(all_urls) - prev
            logger.info(f"  {cat}: {len(links)} links ({new_count} new)")
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"  Error {cat}: {e}")

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
        if any(s in line for s in ['当前位置','首页','上一篇','下一篇','相关文章','推荐阅读',
            '免责声明','版权声明','来源：','时间：','责任编辑','周公解梦官网','周公解梦网',
            '网友评论','热门文章','扫一扫','加微信','微信公众号','关注我们','郑重声明',
            '仅供娱乐','请勿盲目迷信']):
            continue
        lines.append(line)

    return keyword.strip(), '\n'.join(lines)


def save_entries(entries: List[str]):
    if not entries:
        return
    mode = 'a' if OUTPUT.exists() else 'w'
    with open(OUTPUT, mode, encoding='utf-8') as f:
        if OUTPUT.exists() and OUTPUT.stat().st_size > 0:
            f.write('\n\n')
        f.write('\n\n'.join(entries))
    logger.info(f"  Saved {len(entries)} entries")


async def main():
    logger.info("=" * 60)
    logger.info("CRAWL: zgjmorg.com")
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
                    logger.debug(f"  Errors: {scanned}")

    tasks = [scrape_one(url) for url in all_urls]
    await asyncio.gather(*tasks)

    if pending_save:
        save_entries(pending_save)
    await client.aclose()

    logger.info(f"\nDone! {len(results)} entries from zgjmorg.com")
    logger.info(f"Saved to {OUTPUT}")


if __name__ == '__main__':
    asyncio.run(main())
