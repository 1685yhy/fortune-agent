#!/usr/bin/env python3
"""
Scrape zgjm.02516.com - massive dream interpretation database.
This site has pages at /jiemeng/{id}.html with thousands of entries.
"""

import requests
from bs4 import BeautifulSoup
import json, os, hashlib, time, re, sys
import random

OUTPUT_FILE = "/mnt/d/fortune-data/books/zonghe/dreams_scraped_verified.jsonl"
EXISTING_FILE = "/mnt/d/fortune-data/books/zonghe/dreams_verified.jsonl"
BASE_URL = "http://zgjm.02516.com"

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# Load dedup
seen_hashes = set()
total_existing = 0
if os.path.exists(EXISTING_FILE):
    with open(EXISTING_FILE) as f:
        for line in f:
            d = json.loads(line)
            seen_hashes.add(hashlib.md5(d.get('title','')[:30].encode('utf-8')).hexdigest())
            total_existing += 1
if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE) as f:
        for line in f:
            d = json.loads(line)
            seen_hashes.add(hashlib.md5(d.get('title','')[:30].encode('utf-8')).hexdigest())

print(f"[INFO] Existing entries in dedup pool: {len(seen_hashes)}")
print(f"[INFO] dreams_verified.jsonl has {total_existing} entries")


def save_entry(title, content, source_url):
    h = hashlib.md5(title[:30].encode('utf-8')).hexdigest()
    if h in seen_hashes:
        return False
    if len(content) < 150:
        return False
    content = re.sub(r'\s+', ' ', content).strip()
    title = re.sub(r'\s+', ' ', title).strip().rstrip(':：,，.。')
    if not title.startswith('梦'):
        title = '梦见' + title
    seen_hashes.add(h)
    entry = {
        'title': title,
        'content': content,
        'category': 'dream',
        'source_url': source_url,
        'source_quality': 'authoritative',
        'verified': True
    }
    with open(OUTPUT_FILE, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return True


def discover_ids():
    """Discover all dream entry IDs from the site."""
    print("[INFO] Discovering dream entry IDs...")
    all_ids = set()

    # Check main jiemeng page
    urls_to_check = [
        'http://zgjm.02516.com/jiemeng/',
    ]

    # Also check category pages
    for cat in ['dongwu', 'renwu', 'ziran', 'wupin', 'shenghuo', 'guishen', 'jianzhu', 'zhiwu', 'yunfu']:
        urls_to_check.append(f'http://zgjm.02516.com/{cat}/')

    for url in urls_to_check:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                ids = set(re.findall(r'/jiemeng/(\d+)\.html', r.text))
                all_ids.update(int(x) for x in ids)
                print(f"  {url}: found {len(ids)} IDs")
        except Exception as e:
            print(f"  {url}: ERROR {e}")
        time.sleep(0.5)

    print(f"[INFO] Total unique IDs discovered: {len(all_ids)}")
    sorted_ids = sorted(all_ids)
    if sorted_ids:
        print(f"[INFO] ID range: {min(sorted_ids)} - {max(sorted_ids)}")
    return sorted_ids


def scrape_entry(entry_id):
    """Scrape a single dream entry by ID."""
    url = f"{BASE_URL}/jiemeng/{entry_id}.html"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None, None

        soup = BeautifulSoup(r.text, 'lxml')

        # Get title from h1
        h1 = soup.find('h1')
        title = h1.get_text(strip=True) if h1 else None
        if not title:
            # Try title tag
            if soup.title:
                title_text = soup.title.string
                # Extract from title like "梦见熊_预兆_解梦_周公解梦查询_我要乐周公解梦大全"
                parts = title_text.split('_')
                if parts:
                    title = parts[0]

        # Get content from .content div
        content = ""
        content_div = soup.select_one('.content')
        if content_div:
            for tag in content_div.find_all(['script', 'style', 'ins', 'nav']):
                tag.decompose()
            content = content_div.get_text(strip=True)

        # If no content, try broader approach
        if not content:
            body = soup.find('body')
            if body:
                for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                    tag.decompose()
                # Find all text that looks like dream interpretation
                paras = soup.find_all('p')
                texts = [p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 30]
                if texts:
                    content = ' '.join(texts)

        return title, content, url
    except Exception as e:
        return None, None, None


def scrape_all():
    """Main scraping function."""
    entry_ids = discover_ids()
    print(f"[INFO] Will scrape up to {len(entry_ids)} entries")

    count = 0
    skipped = 0
    error_count = 0

    for i, eid in enumerate(entry_ids):
        title, content, url = scrape_entry(eid)
        if title and content:
            if save_entry(title, content, url):
                count += 1
            else:
                skipped += 1
        else:
            error_count += 1

        if (i + 1) % 50 == 0:
            total_output = sum(1 for _ in open(OUTPUT_FILE)) if os.path.exists(OUTPUT_FILE) else 0
            print(f"[INFO] Progress: {i+1}/{len(entry_ids)} | Saved: {count} | Skipped: {skipped} | Errors: {error_count} | Output total: {total_output}")
            sys.stdout.flush()

        time.sleep(random.uniform(0.3, 1.0))

    print(f"\n[DONE] Scraping complete!")
    print(f"  Total discovered: {len(entry_ids)}")
    print(f"  Successfully saved: {count}")
    print(f"  Skipped (dup/short): {skipped}")
    print(f"  Errors: {error_count}")

    total_output = sum(1 for _ in open(OUTPUT_FILE)) if os.path.exists(OUTPUT_FILE) else 0
    print(f"  Total in output file: {total_output}")
    return count


if __name__ == '__main__':
    scrape_all()
