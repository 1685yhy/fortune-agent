#!/usr/bin/env python3
"""
Deep Scraper for 9 Chinese fortune-telling websites.
Scrapes all content and saves to data/competitor_data/{site_key}_deep_{date}.jsonl

Usage:
    python3 scripts/deep_scrape_all.py                 # Scrape all sites
    python3 scripts/deep_scrape_all.py --site smxs      # Scrape single site
    python3 scripts/deep_scrape_all.py --limit 20       # Limit articles per site
    python3 scripts/deep_scrape_all.py --delay 2        # Delay between requests
"""

import httpx
import json
import re
import os
import sys
import time
import random
import argparse
import logging
from datetime import datetime, timezone
from collections import Counter
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

DATE_STR = datetime.now().strftime('%Y%m%d')
OUTPUT_TAG = 'deep'
OUTPUT_DIR = '/home/a/fortune-agent/data/competitor_data'
os.makedirs(OUTPUT_DIR, exist_ok=True)

BASE_DOMAINS = [
    'smxs.com', 'k366.com', 'daosuan.cn', 'zhouyihui.com',
    'aqioo.com', 'zhouyisuanming.net', '64gua.com', 'dajiazhao.com', 'buyiju.com'
]

def normalize_url(href, base_url, base_domain):
    """Convert href to absolute URL, keeping it on the same domain."""
    if not href or href.startswith('#') or href.startswith('javascript:'):
        return None
    if href.startswith('http'):
        # Already absolute - keep only if same domain
        for bd in BASE_DOMAINS:
            if bd in href:
                return href
        return None
    if href.startswith('//'):
        full = f'https:{href}'
        for bd in BASE_DOMAINS:
            if bd in full:
                return full
        return None
    # Relative URL
    if href.startswith('/'):
        return f'https://{base_domain}{href}'
    # Path relative
    parsed = urlparse(base_url)
    base_path = '/'.join(parsed.path.rstrip('/').split('/')[:-1]) + '/'
    return f'https://{base_domain}{base_path}{href}'

def safe_get(client, url, max_text_len=0, expected_domain=None):
    """Fetch a URL with encoding handling for Chinese sites. Returns None on failure."""
    try:
        r = client.get(url, timeout=20)
        if r.status_code != 200:
            return None
        # Check we're still on the same domain (avoid following external redirects)
        if expected_domain and expected_domain not in str(r.url):
            return None
        if len(r.text) < 300:
            return None

        text = r.text
        # Try to detect and fix encoding issues
        content_type = r.headers.get('content-type', '')
        ct_lower = content_type.lower()
        if any(enc in ct_lower for enc in ['gb2312', 'gbk', 'gb18030', 'gb_']):
            try:
                raw = r.content
                text = raw.decode('gbk', errors='replace')
            except:
                pass
        # Check for charset in HTML
        if 'charset=gb' in text[:2000].lower():
            try:
                raw = r.content
                text = raw.decode('gbk', errors='replace')
            except:
                pass
        return text
    except Exception as e:
        return None

# ============================================================
# HTTP Client Setup
# ============================================================

def make_client(user_agent=None):
    """Create an httpx client with sensible defaults."""
    ua = user_agent or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    return httpx.Client(
        timeout=30,
        headers={'User-Agent': ua, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'},
        follow_redirects=True,
        max_redirects=5
    )

# ============================================================
# Utility Functions
# ============================================================

def clean_text(text):
    """Clean extracted text by removing extra whitespace."""
    if not text:
        return ''
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_content_generic(soup, selectors):
    """Try multiple selectors to find content, return first match."""
    for selector in selectors:
        elem = soup.select_one(selector)
        if elem:
            # Remove script, style, nav elements
            for tag in elem.find_all(['script', 'style', 'nav', 'footer', 'iframe', 'noscript']):
                tag.decompose()
            return clean_text(elem.get_text(separator=' ', strip=True))
    return ''

def save_records(records, site_key):
    """Save a list of records to a JSONL file."""
    if not records:
        log.warning(f'  No records to save for {site_key}')
        return

    output_path = os.path.join(OUTPUT_DIR, f'{site_key}_{OUTPUT_TAG}_{DATE_STR}.jsonl')
    count = 0
    with open(output_path, 'w', encoding='utf-8') as f:
        for rec in records:
            # Ensure content is truncated to max 2000 chars
            if len(rec['response']) > 2000:
                rec['response'] = rec['response'][:2000]
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            count += 1
    log.info(f'  Saved {count} records to {output_path}')
    return output_path


CHECKPOINT_PATHS = {}
CHECKPOINT_COUNTS = {}

def save_checkpoint(records, site_key):
    """Save records incrementally to a JSONL file (append mode)."""
    if not records:
        return
    output_path = CHECKPOINT_PATHS.get(site_key)
    if not output_path:
        output_path = os.path.join(OUTPUT_DIR, f'{site_key}_{OUTPUT_TAG}_{DATE_STR}.jsonl')
        CHECKPOINT_PATHS[site_key] = output_path
        CHECKPOINT_COUNTS[site_key] = 0

    with open(output_path, 'a', encoding='utf-8') as f:
        for rec in records:
            if len(rec['response']) > 2000:
                rec['response'] = rec['response'][:2000]
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    CHECKPOINT_COUNTS[site_key] += len(records)
    total = CHECKPOINT_COUNTS[site_key]
    log.info(f'  Checkpoint: +{len(records)} records (total: {total}) -> {output_path}')
    return output_path


def build_record(title, content, url, platform, category):
    """Build a standardized record."""
    return {
        'query': clean_text(title),
        'platform': platform,
        'response': clean_text(content)[:2000],
        'url': url,
        'category': category,
        'scraped_at': datetime.now(timezone.utc).isoformat()
    }

# ============================================================
# Site 1: 水墨先生 smxs.com
# ============================================================

def scrape_smxs(limit=0):
    """Scrape smxs.com - use sitemap.html and section listing pages."""
    platform = 'smxs'
    records = []
    client = make_client()
    domain = 'www.smxs.com'

    log.info('=== SMXS (smxs.com) ===')

    try:
        visited = set()

        def add_url(url):
            if url and url not in visited:
                visited.add(url)
                return url
            return None

        urls_to_scrape = []

        # Strategy 1: Parse sitemap.html for all URLs
        log.info('Fetching sitemap.html...')
        text = safe_get(client, f'https://{domain}/sitemap.html', expected_domain=domain)
        if text:
            soup = BeautifulSoup(text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '.html' in href and len(href) > 10:
                    url = normalize_url(href, f'https://{domain}/sitemap.html', domain)
                    url = add_url(url)
                    if url:
                        urls_to_scrape.append(url)

        log.info(f'  Found {len(urls_to_scrape)} URLs from sitemap.html')

        # Strategy 2: Section listing pages
        sections = ['/jiemeng/', '/fengshui/', '/huangli/', '/xingming/']
        for section in sections:
            text = safe_get(client, f'https://{domain}{section}', expected_domain=domain)
            if not text:
                continue
            soup = BeautifulSoup(text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '.html' in href and len(href) > 10 and 'category' not in href:
                    url = normalize_url(href, f'https://{domain}{section}', domain)
                    url = add_url(url)
                    if url:
                        urls_to_scrape.append(url)

        # Strategy 3: Category listing pages
        for cat_id in range(1, 20):
            text = safe_get(client, f'https://{domain}/category/id/{cat_id}.html', expected_domain=domain)
            if not text:
                continue
            soup = BeautifulSoup(text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '.html' in href and 'category' not in href and 'content' not in href and len(href) > 10:
                    url = normalize_url(href, f'https://{domain}/category/id/{cat_id}.html', domain)
                    url = add_url(url)
                    if url:
                        urls_to_scrape.append(url)

        log.info(f'  Total unique URLs to scrape: {len(urls_to_scrape)}')

        if limit:
            urls_to_scrape = urls_to_scrape[:limit]

        for i, url in enumerate(urls_to_scrape):
            text = safe_get(client, url, expected_domain=domain)
            if not text:
                continue

            soup = BeautifulSoup(text, 'lxml')

            path = urlparse(url).path
            category = 'unknown'
            for sec in sections:
                if sec in path:
                    category = sec.strip('/')
                    break

            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else path.split('/')[-1].replace('.html', '')

            content_selectors = [
                'div.article-content', 'div.content', 'div.main', 'div.article',
                'div.text', 'div.news-content', 'div.post-content', 'div.entry-content',
                'div[class*="content"]', 'div[class*="article"]', 'div[class*="text"]',
                'article', 'main'
            ]
            content = extract_content_generic(soup, content_selectors)

            if not content or len(content) < 50:
                body = soup.find('body')
                if body:
                    for nav in body.find_all(['nav', 'header', 'footer']):
                        nav.decompose()
                    ps = body.find_all(['p', 'li', 'span'])
                    texts = [clean_text(p.get_text()) for p in ps if len(clean_text(p.get_text())) > 20]
                    content = ' '.join(texts)

            if content and len(content) > 50:
                records.append(build_record(title, content, url, platform, category))
                if (i + 1) % 20 == 0:
                    log.info(f'  Progress: {i+1}/{len(urls_to_scrape)} ({len(records)} records)')

            time.sleep(random.uniform(0.3, 0.8))

    except Exception as e:
        log.error(f'SMXS error: {e}')

    log.info(f'  Total records: {len(records)}')
    save_records(records, 'smxs')
    return records


# ============================================================
# Site 2: 华易网 k366.com
# ============================================================

def scrape_k366(limit=0):
    """Scrape k366.com - only /bazi/ section works."""
    platform = 'k366'
    records = []
    client = make_client()
    domain = 'www.k366.com'

    log.info('=== K366 (k366.com) ===')

    try:
        # Scrape main bazi page
        text = safe_get(client, f'https://{domain}/bazi/', expected_domain=domain)
        if not text:
            log.warning('  /bazi/ page returned no content')
            return records
        soup = BeautifulSoup(text, 'lxml')

        # Find all article links
        article_urls = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/bazi/' in href and href.endswith('.htm'):
                url = normalize_url(href, f'https://{domain}/bazi/', domain)
                if url:
                    article_urls.add(url)

        # Find pagination
        pages = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            t = a.get_text(strip=True)
            if t.isdigit() and 'index_' in href:
                pages.append(t)

        # Scrape pages 2+
        max_page = max([int(p) for p in pages]) if pages else 1
        log.info(f'  Found {len(article_urls)} articles on page 1, max page: {max_page}')

        for page_num in range(2, max_page + 1):
            text = safe_get(client, f'https://{domain}/bazi/index_{page_num}.htm', expected_domain=domain)
            if not text:
                continue
            soup_p = BeautifulSoup(text, 'lxml')
            for a in soup_p.find_all('a', href=True):
                href = a['href']
                if '/bazi/' in href and href.endswith('.htm'):
                    url = normalize_url(href, f'https://{domain}/bazi/', domain)
                    if url:
                        article_urls.add(url)

        log.info(f'  Total unique articles: {len(article_urls)}')

        if limit:
            article_urls = list(article_urls)[:limit]

        # Scrape each article
        for i, url in enumerate(article_urls):
            text = safe_get(client, url, expected_domain=domain)
            if not text:
                continue

            soup = BeautifulSoup(text, 'lxml')
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else ''

            content = ''
            for selector in ['div.content', 'div.article', 'div.main', 'div.text',
                             'div[class*="content"]', 'div[class*="article"]', 'article']:
                elem = soup.select_one(selector)
                if elem:
                    for tag in elem.find_all(['script', 'style', 'nav', 'footer', 'iframe']):
                        tag.decompose()
                    content = clean_text(elem.get_text(separator=' ', strip=True))
                    if len(content) > 100:
                        break
            else:
                body = soup.find('body')
                if body:
                    for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    ps = body.find_all(['p'])
                    texts = [clean_text(p.get_text()) for p in ps if len(clean_text(p.get_text())) > 20]
                    content = ' '.join(texts)

            if content and len(content) > 50:
                records.append(build_record(title, content, url, platform, 'bazi'))

            if (i + 1) % 20 == 0:
                log.info(f'  Progress: {i+1}/{len(article_urls)} ({len(records)} records)')

            time.sleep(random.uniform(0.3, 0.8))

    except Exception as e:
        log.error(f'K366 error: {e}')

    log.info(f'  Total records: {len(records)}')
    save_records(records, 'k366')
    return records


# ============================================================
# Site 3: 道算网 daosuan.cn
# ============================================================

def scrape_daosuan(limit=0):
    """Scrape daosuan.cn - articles at /Zhuanti/detail/id/N.html with pagination."""
    platform = 'daosuan'
    records = []
    client = make_client()
    domain = 'www.daosuan.cn'

    log.info('=== Daosuan (daosuan.cn) ===')

    try:
        # First, get total pages from index
        text = safe_get(client, f'https://{domain}/Zhuanti/index.html', expected_domain=domain)
        if not text:
            log.warning('  Index page returned no content')
            return records
        soup = BeautifulSoup(text, 'lxml')

        # Find pagination to determine max page
        max_page = 1
        for a in soup.find_all('a', href=True):
            href = a['href']
            m = re.search(r'/Zhuanti/index/p/(\d+)\.html', href)
            if m:
                p = int(m.group(1))
                if p > max_page:
                    max_page = p

        log.info(f'  Max page: {max_page}')

        # Collect article URLs in batches
        article_urls = set()
        batch_size = 100

        for page_num in range(1, max_page + 1):
            page_url = f'https://{domain}/Zhuanti/index/p/{page_num}.html' if page_num > 1 else f'https://{domain}/Zhuanti/index.html'
            text = safe_get(client, page_url, expected_domain=domain)
            if not text:
                continue
            soup_p = BeautifulSoup(text, 'lxml')
            for a in soup_p.find_all('a', href=True):
                href = a['href']
                if '/Zhuanti/detail/id/' in href:
                    url = normalize_url(href, page_url, domain)
                    if url:
                        article_urls.add(url)

            if page_num % batch_size == 0:
                log.info(f'  Page {page_num}/{max_page}, URLs: {len(article_urls)}')

            # Short delay between pages
            time.sleep(random.uniform(0.1, 0.3))

        log.info(f'  Total unique article URLs: {len(article_urls)}')

        if limit:
            article_urls = list(article_urls)[:limit]

        # Scrape each article
        for i, url in enumerate(article_urls):
            text = safe_get(client, url, expected_domain=domain)
            if not text:
                continue

            soup = BeautifulSoup(text, 'lxml')
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else ''

            # Find content - daosuan uses div.blog-post or div.panel-body
            content = ''
            for selector in ['div.blog-post', 'div.panel-body', 'div[class*="detail"]',
                             'div[class*="content"]', 'article']:
                elem = soup.select_one(selector)
                if elem:
                    for tag in elem.find_all(['script', 'style', 'nav', 'footer', 'iframe']):
                        tag.decompose()
                    content = clean_text(elem.get_text(separator=' ', strip=True))
                    if len(content) > 100:
                        break

            if not content or len(content) < 50:
                body = soup.find('body')
                if body:
                    for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    ps = body.find_all(['p'])
                    texts = [clean_text(p.get_text()) for p in ps if len(clean_text(p.get_text())) > 20]
                    content = ' '.join(texts)

            if content and len(content) > 50:
                records.append(build_record(title, content, url, platform, 'zhuanti'))

            if (i + 1) % 50 == 0:
                log.info(f'  Progress: {i+1}/{len(article_urls)} ({len(records)} records)')

            # Checkpoint every 500 records
            if len(records) > 0 and len(records) % 500 == 0:
                save_checkpoint(records[-500:], 'daosuan')

            time.sleep(random.uniform(0.2, 0.5))

    except Exception as e:
        log.error(f'Daosuan error: {e}')

    log.info(f'  Total records: {len(records)}')
    if len(records) > 0:
        # Save remaining records (last batch not yet checkpointed)
        remainder_start = (len(records) // 500) * 500
        if remainder_start < len(records):
            save_checkpoint(records[remainder_start:], 'daosuan')
        elif CHECKPOINT_COUNTS.get('daosuan', 0) == 0:
            save_checkpoint(records, 'daosuan')
    return records


# ============================================================
# Site 4: 周易汇 zhouyihui.com
# ============================================================

def scrape_zhouyihui(limit=0):
    """Scrape zhouyihui.com - use the HUGE sitemap with 13K URLs."""
    platform = 'zhouyihui'
    records = []
    client = make_client()
    domain = 'www.zhouyihui.com'

    log.info('=== Zhouyihui (zhouyihui.com) ===')

    try:
        # Parse the sitemap
        r = client.get(f'https://{domain}/sitemap.xml', timeout=30)
        urls = re.findall(r'<loc>(.*?)</loc>', r.text)
        log.info(f'  Found {len(urls)} URLs in sitemap')

        # Filter to content pages (skip listing, search, tag pages)
        skip_patterns = ['/search/', '/tag/', '/baike/', '/peidui/']
        content_urls = []
        for u in urls:
            path = urlparse(u).path
            # Skip non-content URL patterns
            if any(p in path for p in skip_patterns):
                continue
            # Keep URLs that look like articles
            if u.endswith('.html') or re.search(r'/\d+$', u):
                if not path.endswith('/') or len(path.split('/')) > 2:
                    content_urls.append(u)

        log.info(f'  Content URLs: {len(content_urls)}')

        if limit:
            content_urls = content_urls[:limit]

        for i, url in enumerate(content_urls):
            text = safe_get(client, url, expected_domain=domain)
            if not text:
                continue

            soup = BeautifulSoup(text, 'lxml')
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else ''

            # Determine category
            path = urlparse(url).path
            parts = path.strip('/').split('/')
            category = parts[0] if parts else 'unknown'

            content = ''
            for selector in ['div.content', 'div.article', 'div.main', 'div.text',
                             'div[class*="content"]', 'div[class*="article"]', 'article',
                             'div.news-content', 'div.post-content', 'div.entry-content']:
                elem = soup.select_one(selector)
                if elem:
                    for tag in elem.find_all(['script', 'style', 'nav', 'footer', 'iframe']):
                        tag.decompose()
                    content = clean_text(elem.get_text(separator=' ', strip=True))
                    if len(content) > 100:
                        break

            if not content or len(content) < 50:
                body = soup.find('body')
                if body:
                    for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    ps = body.find_all(['p'])
                    texts = [clean_text(p.get_text()) for p in ps if len(clean_text(p.get_text())) > 20]
                    content = ' '.join(texts)

            if content and len(content) > 50:
                records.append(build_record(title, content, url, platform, category))

            if (i + 1) % 100 == 0:
                log.info(f'  Progress: {i+1}/{len(content_urls)} ({len(records)} records)')

            # Checkpoint every 500 records
            if len(records) > 0 and len(records) % 500 == 0:
                save_checkpoint(records[-500:], 'zhouyihui')

            time.sleep(random.uniform(0.2, 0.5))

    except Exception as e:
        log.error(f'Zhouyihui error: {e}')

    log.info(f'  Total records: {len(records)}')
    if len(records) > 0:
        remainder_start = (len(records) // 500) * 500
        if remainder_start < len(records):
            save_checkpoint(records[remainder_start:], 'zhouyihui')
        elif CHECKPOINT_COUNTS.get('zhouyihui', 0) == 0:
            save_checkpoint(records, 'zhouyihui')
    return records


# ============================================================
# Site 5: 阿启网 aqioo.com
# ============================================================

def scrape_aqioo(limit=0):
    """Scrape aqioo.com - use sitemap.html and section listing pages."""
    platform = 'aqioo'
    records = []
    client = make_client()
    domain = 'www.aqioo.com'

    log.info('=== Aqioo (aqioo.com) ===')

    try:
        visited = set()

        def add_url(url, category):
            if url and url not in visited:
                visited.add(url)
                return (url, category)
            return None

        url_items = []

        # Strategy 1: Parse sitemap.html
        text = safe_get(client, f'https://{domain}/sitemap.html', expected_domain=domain)
        if text:
            soup = BeautifulSoup(text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if href.count('/') >= 2 and '.html' in href:
                    url = normalize_url(href, f'https://{domain}/sitemap.html', domain)
                    if url:
                        parts = urlparse(url).path.strip('/').split('/')
                        cat = parts[1] if len(parts) > 1 else (parts[0] if parts else 'other')
                        item = add_url(url, cat)
                        if item:
                            url_items.append(item)

        # Strategy 2: Section listing pages
        sections = ['/fengshui/', '/jiemeng/', '/xingming/', '/bazi/', '/suanming/']
        for section in sections:
            text = safe_get(client, f'https://{domain}{section}', expected_domain=domain)
            if not text:
                continue
            soup = BeautifulSoup(text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '.html' in href and section.rstrip('/') in href:
                    url = normalize_url(href, f'https://{domain}{section}', domain)
                    if url:
                        cat = section.strip('/')
                        item = add_url(url, cat)
                        if item:
                            url_items.append(item)

        log.info(f'  Total URLs to scrape: {len(url_items)}')

        if limit:
            url_items = url_items[:limit]

        for i, (url, category) in enumerate(url_items):
            text = safe_get(client, url, expected_domain=domain)
            if not text:
                continue

            soup = BeautifulSoup(text, 'lxml')
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else ''

            # Find content - aqioo uses div.read-content
            content = ''
            for selector in ['div.read-content', 'div.content', 'div.article-content',
                             'div[class*="content"]', 'div[class*="article"]', 'article']:
                elem = soup.select_one(selector)
                if elem:
                    for tag in elem.find_all(['script', 'style', 'nav', 'footer', 'iframe']):
                        tag.decompose()
                    content = clean_text(elem.get_text(separator=' ', strip=True))
                    if len(content) > 100:
                        break

            if not content or len(content) < 50:
                body = soup.find('body')
                if body:
                    for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    ps = body.find_all(['p'])
                    texts = [clean_text(p.get_text()) for p in ps if len(clean_text(p.get_text())) > 20]
                    content = ' '.join(texts)

            if content and len(content) > 50:
                records.append(build_record(title, content, url, platform, category))

            if (i + 1) % 50 == 0:
                log.info(f'  Progress: {i+1}/{len(url_items)} ({len(records)} records)')

            time.sleep(random.uniform(0.2, 0.5))

    except Exception as e:
        log.error(f'Aqioo error: {e}')

    log.info(f'  Total records: {len(records)}')
    save_records(records, 'aqioo')
    return records


# ============================================================
# Site 6: 周易算命 zhouyisuanming.net
# ============================================================

def scrape_zhouyisuanming(limit=0):
    """Scrape zhouyisuanming.net."""
    platform = 'zhouyisuanming'
    records = []
    client = make_client()
    domain = 'www.zhouyisuanming.net'

    log.info('=== Zhouyisuanming (zhouyisuanming.net) ===')

    try:
        url_items = {}
        url_order = []

        def add_url(url, cat):
            if url and url not in url_items:
                url_items[url] = cat
                url_order.append((url, cat))

        # Scrape section pages
        sections = [
            ('/bazi/', 'bazi'),
            ('/xingming/', 'xingming'),
            ('/suanming/', 'suanming'),
        ]

        for section_path, category in sections:
            text = safe_get(client, f'https://{domain}{section_path}', expected_domain=domain)
            if not text:
                continue
            soup = BeautifulSoup(text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if section_path.rstrip('/') in href and '.html' in href:
                    url = normalize_url(href, f'https://{domain}{section_path}', domain)
                    add_url(url, category)

        log.info(f'  Total URLs to scrape: {len(url_items)}')

        url_list = url_order
        if limit:
            url_list = url_list[:limit]

        for i, (url, category) in enumerate(url_list):
            text = safe_get(client, url, expected_domain=domain)
            if not text:
                continue

            soup = BeautifulSoup(text, 'lxml')
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else ''

            content = ''
            for selector in ['div.content', 'div.main', 'div.article', 'div.text',
                             'div[class*="content"]', 'div[class*="article"]', 'article']:
                elem = soup.select_one(selector)
                if elem:
                    for tag in elem.find_all(['script', 'style', 'nav', 'footer', 'iframe']):
                        tag.decompose()
                    content = clean_text(elem.get_text(separator=' ', strip=True))
                    if len(content) > 100:
                        break

            if not content or len(content) < 50:
                body = soup.find('body')
                if body:
                    for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    ps = body.find_all(['p'])
                    texts = [clean_text(p.get_text()) for p in ps if len(clean_text(p.get_text())) > 20]
                    content = ' '.join(texts)

            if content and len(content) > 50:
                records.append(build_record(title, content, url, platform, category))

            if (i + 1) % 20 == 0:
                log.info(f'  Progress: {i+1}/{len(url_list)} ({len(records)} records)')

            time.sleep(random.uniform(0.3, 0.8))

    except Exception as e:
        log.error(f'Zhouyisuanming error: {e}')

    log.info(f'  Total records: {len(records)}')
    save_records(records, 'zhouyisuanming')
    return records


# ============================================================
# Site 7: 周易天地 64gua.com
# ============================================================

def scrape_64gua(limit=0):
    """Scrape 64gua.com - book/library format."""
    platform = '64gua'
    records = []
    client = make_client()
    domain = 'www.64gua.com'

    log.info('=== 64gua (64gua.com) ===')

    try:
        # Get list of categories
        text = safe_get(client, f'https://{domain}/index.php?s=book&c=category&id=1', expected_domain=domain)
        if not text:
            log.warning('  Category page returned no content')
            return records
        soup = BeautifulSoup(text, 'lxml')

        category_ids = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            m = re.search(r'catid=(\d+)', href)
            if m:
                category_ids.add(int(m.group(1)))

        log.info(f'  Found {len(category_ids)} categories')

        book_urls = set()
        for cat_id in sorted(category_ids):
            page_num = 1
            while True:
                page_url = f'https://{domain}/index.php?s=book&c=search&catid={cat_id}&page={page_num}'
                text = safe_get(client, page_url, expected_domain=domain)
                if not text:
                    break
                soup_p = BeautifulSoup(text, 'lxml')

                found_new = False
                for a in soup_p.find_all('a', href=True):
                    href = a['href']
                    if 'c=show&id=' in href:
                        url = normalize_url(href, page_url, domain)
                        if url and url not in book_urls:
                            book_urls.add(url)
                            found_new = True

                if not found_new:
                    break
                page_num += 1
                time.sleep(random.uniform(0.3, 0.8))

        log.info(f'  Total books/articles: {len(book_urls)}')

        if limit:
            book_urls = list(book_urls)[:limit]

        for i, url in enumerate(book_urls):
            text = safe_get(client, url, expected_domain=domain)
            if not text:
                continue

            soup = BeautifulSoup(text, 'lxml')
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else ''

            content = ''
            for selector in ['div.content', 'div.main', 'div.book-content', 'div.text',
                             'div[class*="content"]', 'div[class*="book"]', 'article']:
                elem = soup.select_one(selector)
                if elem:
                    for tag in elem.find_all(['script', 'style', 'nav', 'footer', 'iframe']):
                        tag.decompose()
                    content = clean_text(elem.get_text(separator=' ', strip=True))
                    if len(content) > 100:
                        break

            if not content or len(content) < 50:
                body = soup.find('body')
                if body:
                    for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    ps = body.find_all(['p'])
                    texts = [clean_text(p.get_text()) for p in ps if len(clean_text(p.get_text())) > 20]
                    content = ' '.join(texts)

            if content and len(content) > 50:
                records.append(build_record(title, content, url, platform, 'book'))

            if (i + 1) % 20 == 0:
                log.info(f'  Progress: {i+1}/{len(book_urls)} ({len(records)} records)')

            time.sleep(random.uniform(0.3, 0.8))

    except Exception as e:
        log.error(f'64gua error: {e}')

    log.info(f'  Total records: {len(records)}')
    save_records(records, '64gua')
    return records


# ============================================================
# Site 8: 大家找算命 dajiazhao.com
# ============================================================

def scrape_dajiazhao(limit=0):
    """Scrape dajiazhao.com - focus on /zhougongjiemeng/ and other sections."""
    platform = 'dajiazhao'
    records = []
    client = make_client()
    domain = 'www.dajiazhao.com'

    log.info('=== Dajiazhao (dajiazhao.com) ===')

    try:
        urls_to_scrape = {}
        url_order = []

        def add_url(url, category):
            if url and url not in urls_to_scrape:
                urls_to_scrape[url] = category
                url_order.append(url)

        # Collect URLs from dream section sub-categories
        dream_categories = [
            '/zhougongjiemeng/renwu/', '/zhougongjiemeng/shenghuo/',
            '/zhougongjiemeng/guishen/', '/zhougongjiemeng/wupin/',
            '/zhougongjiemeng/dongwu/', '/zhougongjiemeng/zhiwu/',
            '/zhougongjiemeng/ziran/', '/zhougongjiemeng/huodong/',
            '/zhougongjiemeng/jianzhu/', '/zhougongjiemeng/qita/',
        ]

        for dream_cat in dream_categories:
            try:
                text = safe_get(client, f'https://{domain}{dream_cat}')
                if not text:
                    continue
                soup = BeautifulSoup(text, 'lxml')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if dream_cat.replace('/', '') in href.replace('https://', '') and href.endswith('.html'):
                        url = normalize_url(href, f'https://{domain}{dream_cat}', domain)
                        add_url(url, 'jiemeng')
            except Exception as e:
                log.warning(f'  Error with {dream_cat}: {e}')
                continue

        # Also get URLs from sitemap
        try:
            text = safe_get(client, f'https://{domain}/sitemap.html')
            if text:
                soup = BeautifulSoup(text, 'lxml')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if '.html' in href and len(href) > 10:
                        url = normalize_url(href, f'https://{domain}/sitemap.html', domain)
                        if url:
                            parts = urlparse(url).path.strip('/').split('/')
                            cat = parts[0] if parts else 'unknown'
                            add_url(url, cat)
        except Exception as e:
            log.warning(f'  Error with sitemap: {e}')

        # Add URLs from astrology and zodiac sections
        other_sections = ['/sm/', '/sx/', '/xingming/', '/astro/', '/yuce/']
        for section in other_sections:
            try:
                text = safe_get(client, f'https://{domain}{section}')
                if not text:
                    continue
                soup = BeautifulSoup(text, 'lxml')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if '.html' in href and section.rstrip('/') in href:
                        url = normalize_url(href, f'https://{domain}{section}', domain)
                        if url:
                            add_url(url, section.strip('/'))
            except Exception as e:
                continue

        log.info(f'  Total URLs to scrape: {len(urls_to_scrape)}')

        url_items = list(urls_to_scrape.items())
        if limit:
            url_items = url_items[:limit]

        for i, (url, category) in enumerate(url_items):
            try:
                text = safe_get(client, url)
                if not text:
                    continue

                soup = BeautifulSoup(text, 'lxml')
                title_tag = soup.find('title')
                title = title_tag.get_text(strip=True) if title_tag else ''

                # Find content - dajiazhao uses div.box.wzbody
                content = ''
                for selector in ['div.wzbody', 'div.box.wzbody', 'div.content', 'div.main', 'div.article',
                                 'div[class*="content"]', 'div[class*="article"]', 'article']:
                    elem = soup.select_one(selector)
                    if elem:
                        for tag in elem.find_all(['script', 'style', 'nav', 'footer', 'iframe']):
                            tag.decompose()
                        content = clean_text(elem.get_text(separator=' ', strip=True))
                        if len(content) > 100:
                            break

                if not content or len(content) < 50:
                    body = soup.find('body')
                    if body:
                        for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                            tag.decompose()
                        ps = body.find_all(['p'])
                        texts = [clean_text(p.get_text()) for p in ps if len(clean_text(p.get_text())) > 20]
                        content = ' '.join(texts)

                if content and len(content) > 50:
                    records.append(build_record(title, content, url, platform, category))

                if (i + 1) % 50 == 0:
                    log.info(f'  Progress: {i+1}/{len(url_items)} ({len(records)} records)')

                time.sleep(random.uniform(0.2, 0.5))

            except Exception as e:
                continue

    except Exception as e:
        log.error(f'Dajiazhao error: {e}')

    log.info(f'  Total records: {len(records)}')
    save_records(records, 'dajiazhao')
    return records


# ============================================================
# Site 9: 卜易居 buyiju.com
# ============================================================

def scrape_buyiju(limit=0):
    """Scrape buyiju.com - requires Baidu spider User-Agent."""
    platform = 'buyiju'
    client = make_client('Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)')
    records = []
    domain = 'www.buyiju.com'

    log.info('=== Buyiju (buyiju.com) ===')

    try:
        url_items = {}
        url_order = []

        def add_url(url, cat):
            if url and url not in url_items:
                url_items[url] = cat
                url_order.append((url, cat))

        # Explore sections
        sections = [
            ('/fengshui/', 'fengshui'),
            ('/bazi/', 'bazi'),
            ('/jiemeng/', 'jiemeng'),
            ('/suanming/', 'suanming'),
            ('/huangli/', 'huangli'),
            ('/cgsm/', 'chenggu'),
            ('/guanyin/', 'chouqian'),
        ]

        for section, category in sections:
            text = safe_get(client, f'https://{domain}{section}', expected_domain=domain)
            if not text:
                continue
            soup = BeautifulSoup(text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '.html' in href and section.rstrip('/') in href:
                    url = normalize_url(href, f'https://{domain}{section}', domain)
                    add_url(url, category)

        # Also check sub-sections
        fengshui_subs = ['/fengshui/minsu/', '/fengshui/jiaju/', '/fengshui/bangong/']
        for sub in fengshui_subs:
            text = safe_get(client, f'https://{domain}{sub}', expected_domain=domain)
            if not text:
                continue
            soup = BeautifulSoup(text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if sub.rstrip('/') in href and href.endswith('.html'):
                    url = normalize_url(href, f'https://{domain}{sub}', domain)
                    add_url(url, 'fengshui')

        log.info(f'  Total URLs to scrape: {len(url_items)}')

        url_list = url_order
        if limit:
            url_list = url_list[:limit]

        for i, (url, category) in enumerate(url_list):
            text = safe_get(client, url, expected_domain=domain)
            if not text:
                continue

            soup = BeautifulSoup(text, 'lxml')
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else ''

            content = ''
            for selector in ['div.read-content', 'div.content', 'div.viewbox', 'div.main',
                             'div[class*="content"]', 'div[class*="article"]', 'article']:
                elem = soup.select_one(selector)
                if elem:
                    for tag in elem.find_all(['script', 'style', 'nav', 'footer', 'iframe']):
                        tag.decompose()
                    content = clean_text(elem.get_text(separator=' ', strip=True))
                    if len(content) > 100:
                        break

            if not content or len(content) < 50:
                body = soup.find('body')
                if body:
                    for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    ps = body.find_all(['p'])
                    texts = [clean_text(p.get_text()) for p in ps if len(clean_text(p.get_text())) > 20]
                    content = ' '.join(texts)

            if content and len(content) > 50:
                records.append(build_record(title, content, url, platform, category))

            if (i + 1) % 20 == 0:
                log.info(f'  Progress: {i+1}/{len(url_list)} ({len(records)} records)')

            time.sleep(random.uniform(0.3, 0.8))

    except Exception as e:
        log.error(f'Buyiju error: {e}')

    log.info(f'  Total records: {len(records)}')
    save_records(records, 'buyiju')
    return records


# ============================================================
# Main Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='Deep scrape Chinese fortune-telling websites')
    parser.add_argument('--site', choices=['smxs', 'k366', 'daosuan', 'zhouyihui', 'aqioo',
                                           'zhouyisuanming', '64gua', 'dajiazhao', 'buyiju', 'all'],
                       default='all', help='Site to scrape (default: all)')
    parser.add_argument('--limit', type=int, default=0, help='Limit articles per site (0=unlimited)')
    parser.add_argument('--no-limit', action='store_true', help='Scrape ALL available articles (sets limit to 50000)')
    parser.add_argument('--tag', default='deep', help='Output filename tag (default: deep)')
    parser.add_argument('--delay', type=float, default=0.5, help='Base delay between requests (default: 0.5s)')
    args = parser.parse_args()

    # Handle --no-limit
    if args.no_limit:
        args.limit = 50000

    # Set output tag globally
    global OUTPUT_TAG
    OUTPUT_TAG = args.tag

    sites = {
        'smxs': scrape_smxs,
        'k366': scrape_k366,
        'daosuan': scrape_daosuan,
        'zhouyihui': scrape_zhouyihui,
        'aqioo': scrape_aqioo,
        'zhouyisuanming': scrape_zhouyisuanming,
        '64gua': scrape_64gua,
        'dajiazhao': scrape_dajiazhao,
        'buyiju': scrape_buyiju,
    }

    start_time = time.time()
    total_records = 0

    if args.site == 'all':
        log.info(f'Starting deep scrape of ALL 9 sites (limit={args.limit}, delay={args.delay})')
        for site_key, scrape_func in sites.items():
            try:
                records = scrape_func(limit=args.limit)
                total_records += len(records)
            except Exception as e:
                log.error(f'Fatal error scraping {site_key}: {e}')
    else:
        log.info(f'Starting deep scrape of {args.site} (limit={args.limit})')
        records = sites[args.site](limit=args.limit)
        total_records += len(records)

    elapsed = time.time() - start_time
    log.info(f'=' * 50)
    log.info(f'DEEP SCRAPE COMPLETE: {total_records} total records in {elapsed:.1f}s')

    # Write summary
    summary_path = '/tmp/deep_scrape_report.md'
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(f'# Deep Scrape Report\n')
        f.write(f'Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        f.write(f'Total records: {total_records}\n')
        f.write(f'Duration: {elapsed:.1f}s\n\n')
        f.write('| Site | Platform | Records | File |\n')
        f.write('|------|----------|---------|------|\n')
        for site_key in sites:
            output_path = os.path.join(OUTPUT_DIR, f'{site_key}_{OUTPUT_TAG}_{DATE_STR}.jsonl')
            if os.path.exists(output_path):
                with open(output_path, 'r', encoding='utf-8') as pf:
                    count = sum(1 for _ in pf)
                f.write(f'| {sites[site_key].__name__} | {site_key} | {count} | {output_path} |\n')
            else:
                f.write(f'| {sites[site_key].__name__} | {site_key} | 0 | (no file) |\n')

    log.info(f'Summary written to {summary_path}')


if __name__ == '__main__':
    main()
