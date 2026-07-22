#!/usr/bin/env python3
"""数据扩充 — 定向采集+验证+入库，目标50K+。

优先补充短板:
  奇门遁甲: 155 → 2,000 (+1,845)
  手相知识: 82 → 1,000 (+918)
  面相: 256 → 1,000 (+744)
  八字案例: 4,935 → 5,000+ (+65)
  新增分类: 古籍增补、现代心理

使用: python scripts/expand_data.py [--dry-run]
"""

import json, re, sys, time, logging
from pathlib import Path
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

PROJ = Path(__file__).parent.parent
STAGING = Path("/mnt/d/fortune-data/books/zonghe/staging")
STAGING.mkdir(parents=True, exist_ok=True)
DATA_DIR = PROJ / "data" / "competitor_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(message)s]')
log = logging.getLogger()

MIN_TEXT_LEN = 80  # 最小文本长度（拒收短内容）
MAX_TEXT_LEN = 3000  # 最大文本长度（截断过长内容）
SKIP_KEYWORDS = {"广告", "推广", "赞助", "加微信", "扫一扫", "关注公众号",
                 "免责声明", "版权", "当前位置", "首页", "上一篇", "下一篇",
                 "评论", "注册", "登录", "会员", "付费", "购买"}

# ── 数据源配置 ──────────────────────────────────────────────────
# 每个源: (url, category, parser_func, max_pages)

SOURCES = {
    # ── 奇门遁甲 ──
    "qimen_dunjia": {
        "base": "https://www.dunjia.net",
        "urls": [
            "/qimen/", "/dunjia/", "/qimenjing/", "/qimenying/",
        ],
        "category": "qimen",
        "max_pages": 50,
    },
    # ── 手相 ──
    "palm_reading": {
        "base": "https://www.zhouyi.cc",
        "urls": [
            "/shouxiang/", "/mianxiang/",
        ],
        "category": "palm_reading",
        "max_pages": 50,
    },
}

# 简单解析器（通用）
def parse_generic(html: str, url: str) -> list:
    """通用解析：从 HTML 中提取标题+正文段落"""
    soup = BeautifulSoup(html, 'html.parser')
    entries = []

    # 移除垃圾元素
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside',
                                'form', 'iframe', 'noscript']):
        tag.decompose()

    # 提取标题
    title = ""
    h1 = soup.find('h1')
    if h1:
        title = h1.get_text(strip=True)
    else:
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)

    # 提取正文
    article = soup.find('article') or soup.find('div', class_=re.compile(r'content|article|main|text|body'))
    if not article:
        article = soup.find('body')

    if article:
        paragraphs = []
        for p in article.find_all(['p', 'div', 'li', 'h2', 'h3', 'h4', 'blockquote']):
            text = p.get_text(strip=True)
            if len(text) >= 20 and not any(kw in text for kw in SKIP_KEYWORDS):
                paragraphs.append(text)

        content = "\n".join(paragraphs)
        if len(content) >= MIN_TEXT_LEN:
            entries.append({
                "title": title[:100] or "奇门遁甲知识",
                "content": content[:MAX_TEXT_LEN],
                "source": url,
                "scraped_at": datetime.now().isoformat(),
            })

    return entries


# ── 验证流水线 ──────────────────────────────────────────────────

def validate_entry(entry: dict) -> tuple:
    """3道验证: 来源→内容→去重。返回 (is_valid, reason)"""
    # 验证1: 来源可信
    source = entry.get("source", "")
    if not source or not (source.startswith("http") or source.startswith("https")):
        return False, "invalid_source"

    # 验证2: 内容完整
    content = entry.get("content", "")
    title = entry.get("title", "")
    total_len = len(title) + len(content)
    if total_len < MIN_TEXT_LEN:
        return False, f"too_short({total_len})"
    if total_len < 50:  # 极短内容
        return False, "too_short"

    # 检查内容质量
    junk_ratio = sum(1 for kw in SKIP_KEYWORDS if kw in content) / max(len(content), 1)
    if junk_ratio > 0.05:  # 超过5%的垃圾关键词
        return False, f"junk_ratio({junk_ratio:.2f})"

    # 验证3: 去重（简单哈希，在后续批量去重中处理）
    return True, "ok"


def process_entries(entries: list, category: str, dry_run: bool = False) -> tuple:
    """处理条目: 验证→去重→写入"""
    valid = []
    rejected = 0
    seen = set()  # 简单 content hash 去重

    for entry in entries:
        is_valid, reason = validate_entry(entry)
        if not is_valid:
            rejected += 1
            continue

        # 去重
        content_hash = hash(entry["content"][:200])
        if content_hash in seen:
            rejected += 1
            continue
        seen.add(content_hash)

        # 添加分类和时间戳
        entry["category"] = category
        entry["validated_at"] = datetime.now().isoformat()
        entry["quality"] = "accepted"
        valid.append(entry)

    if not dry_run and valid:
        out_file = STAGING / f"{category}_accepted.jsonl"
        with open(out_file, "a") as f:
            for entry in valid:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return len(valid), rejected


# ── 主流程 ──────────────────────────────────────────────────────

def scrape_source(name: str, config: dict, dry_run: bool = False) -> tuple:
    """爬取单个数据源"""
    base = config["base"]
    category = config["category"]
    urls = config.get("urls", [])
    max_pages = config.get("max_pages", 30)

    all_entries = []
    scraped = 0
    errors = 0

    client = httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; FortuneBot/5.0)"})

    for path in urls:
        try:
            url = f"{base.rstrip('/')}{path}"
            log.info(f"[{name}] Fetching {url}")
            resp = client.get(url)
            if resp.status_code == 200:
                entries = parse_generic(resp.text, url)
                scraped += 1
                all_entries.extend(entries)
                log.info(f"[{name}]   -> {len(entries)} entries from {url}")
            else:
                errors += 1
        except Exception as e:
            log.warning(f"[{name}] Error fetching {path}: {e}")
            errors += 1

        if scraped >= max_pages:
            break

    client.close()

    # 验证+入库
    accepted, rejected = process_entries(all_entries, category, dry_run)
    log.info(f"[{name}] Complete: {accepted} accepted, {rejected} rejected, {errors} fetch errors")
    return accepted, rejected


def main(dry_run: bool = False):
    """主入口"""
    total_accepted = 0
    total_rejected = 0

    log.info(f"=== Data Expansion {'(DRY RUN)' if dry_run else ''} ===")
    log.info(f"Sources: {len(SOURCES)}")
    log.info(f"Staging: {STAGING}")

    for name, config in SOURCES.items():
        log.info(f"\n--- {name} ---")
        acc, rej = scrape_source(name, config, dry_run)
        total_accepted += acc
        total_rejected += rej

    log.info(f"\n=== Summary ===")
    log.info(f"Total accepted: {total_accepted}")
    log.info(f"Total rejected: {total_rejected}")
    reject_rate = total_rejected / max(total_accepted + total_rejected, 1) * 100
    log.info(f"Reject rate: {reject_rate:.1f}%")

    # 报告当前各分类总量
    log.info("\n=== Current category counts ===")
    for f in sorted(STAGING.glob("*_accepted.jsonl")):
        try:
            count = sum(1 for _ in open(f))
            log.info(f"  {f.stem.replace('_accepted','')}: {count}")
        except Exception:
            pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main(dry_run=args.dry_run)
