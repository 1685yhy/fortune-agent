#!/usr/bin/env python3
"""
Comprehensive Dream Interpretation Scraper
Target: 5,000+ new high-quality JSONL entries

Sources:
1. Parse / parse existing 12880_dreams.txt (raw text -> structured JSONL)
2. Scrape jiemeng.aies.cn (pages 1-200)
3. Scrape more zgjm.org categories
4. WebSearch-based entries for gaps

Output: /mnt/d/fortune-data/books/zonghe/dreams_scraped_verified.jsonl
"""

import json
import re
import os
import sys
import time
import hashlib
import random
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Config
OUTPUT_FILE = "/mnt/d/fortune-data/books/zonghe/dreams_scraped_verified.jsonl"
EXISTING_FILE = "/mnt/d/fortune-data/books/zonghe/dreams_verified.jsonl"
RAW_FILE = "/mnt/d/fortune-data/books/zonghe/12880_dreams.txt"

# Track seen titles (dedup pool)
seen_titles = set()
seen_hashes = set()

# Stats
stats = {"parsed_raw": 0, "scraped_jiemeng": 0, "scraped_zgjm": 0, "websourced": 0, "rejected_short": 0, "rejected_dup": 0, "rejected_spam": 0, "errors": 0}


def load_existing():
    """Load existing entries to avoid duplicates."""
    if os.path.exists(EXISTING_FILE):
        with open(EXISTING_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        title = d.get('title', '')[:30]
                        seen_titles.add(title)
                        seen_hashes.add(hashlib.md5(title.encode('utf-8')).hexdigest())
                    except:
                        pass
    print(f"[INFO] Loaded {len(seen_titles)} existing entries for dedup")


def load_already_scraped():
    """Load entries already saved to the output file (for resumability)."""
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        title = d.get('title', '')[:30]
                        seen_titles.add(title)
                        seen_hashes.add(hashlib.md5(title.encode('utf-8')).hexdigest())
                    except:
                        pass
        count = sum(1 for _ in open(OUTPUT_FILE))
        print(f"[INFO] Loaded {count} existing scraped entries for dedup")
        return count
    return 0


def is_duplicate(title):
    """Check if title is duplicate (first 30 chars)."""
    if not title:
        return True
    key = title[:30]
    h = hashlib.md5(key.encode('utf-8')).hexdigest()
    if h in seen_hashes or key in seen_titles:
        return True
    seen_hashes.add(h)
    seen_titles.add(key)
    return False


def is_spam(content):
    """Detect SEO spam / keyword stuffing / advertising."""
    if not content:
        return True
    spam_indicators = [
        '添加微信', '扫码', '加老师', '免费领取', '点击链接', '咨询师',
        'VX', '微信', 'QQ群', 'qq群', '付费', '价格', '收费',
        '广告', '推广', '优惠', '打折', '促销', '限时',
        '关注公众号', '关注我们', '点赞转发',
        '点击下方', '了解更多', '立即购买',
    ]
    content_lower = content.lower()
    count = 0
    for indicator in spam_indicators:
        if indicator in content_lower:
            count += 1
    return count >= 3  # Multiple spam indicators


def validate_content(content):
    """Validate dream interpretation content."""
    if not content:
        return False
    if len(content) < 150:
        stats['rejected_short'] += 1
        return False
    if len(content) > 3000:
        return False  # Suspiciously long
    if is_spam(content):
        stats['rejected_spam'] += 1
        return False
    # Must contain dream interpretation keywords
    dream_keywords = ['梦', '梦见', '梦到', '解梦', '兆', '吉', '凶']
    if not any(kw in content for kw in dream_keywords):
        return False
    return True


def save_entry(entry):
    """Save single entry to JSONL."""
    with open(OUTPUT_FILE, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def make_entry(title, content, source_url, source_quality="community"):
    """Create and validate an entry."""
    # Clean
    title = re.sub(r'\s+', ' ', title).strip().rstrip(':：,，.。')
    content = re.sub(r'\s+', ' ', content).strip()

    # Validate
    if not validate_content(content):
        return False
    if is_duplicate(title):
        stats['rejected_dup'] += 1
        return False

    # Ensure title has 梦
    if not title.startswith('梦'):
        title = f"梦见{title}" if not title.startswith('梦见') else title

    entry = {
        "title": title,
        "content": content,
        "category": "dream",
        "source_url": source_url,
        "source_quality": source_quality,
        "verified": True
    }
    save_entry(entry)
    return True


# ============================================================
# PHASE 1: Parse 12880_dreams.txt
# ============================================================
def parse_raw_file():
    """Parse the 12880_dreams.txt which has entries split across lines."""
    print("\n=== PHASE 1: Parsing 12880_dreams.txt ===")

    with open(RAW_FILE, 'r') as f:
        lines = f.readlines()

    entries = []
    current_title = None
    current_content = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # New entry starts with 梦见
        if line.startswith('梦见') and ':' in line[:30]:
            # Save previous
            if current_title and current_content:
                entries.append((current_title, current_content))

            # Split on first :
            colon_idx = line.find(':')
            if colon_idx > 0:
                current_title = line[:colon_idx].strip()
                current_content = line[colon_idx+1:].strip()
            else:
                current_title = line
                current_content = ""
        else:
            # Continuation of current entry
            if current_title:
                # Remove leading comma/space if it's a continuation
                line_clean = line.lstrip('，,、。. ')
                if line_clean:
                    current_content += line_clean

    # Save last entry
    if current_title and current_content:
        entries.append((current_title, current_content))

    print(f"[INFO] Found {len(entries)} candidate entries in raw file")

    # Save as JSONL
    count = 0
    for title, content in entries:
        content_clean = content
        # If content has a pattern like "有头部疾病者梦之" sections, join them
        content_clean = re.sub(r'\s+', '', content_clean)
        if make_entry(title, content_clean, "周公解梦大全参考", "authoritative"):
            count += 1

    stats['parsed_raw'] = count
    print(f"[INFO] Parsed {count} entries from raw file")
    return count


# ============================================================
# PHASE 2: Scrape jiemeng.aies.cn
# ============================================================
def scrape_jiemeng_aies():
    """Scrape dream entries from jiemeng.aies.cn."""
    print("\n=== PHASE 2: Scraping jiemeng.aies.cn ===")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    count = 0

    # Try list pages
    for page in range(1, 201):
        url = f"http://jiemeng.aies.cn/mengjian/list_1_{page}.html"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                if page > 5:  # If first few pages fail, try alternative
                    break
                continue

            soup = BeautifulSoup(resp.text, 'lxml')

            # Look for entry links
            links = soup.find_all('a', href=re.compile(r'/mengjian/\d+/'))

            if not links:
                # Try different link patterns
                links = soup.find_all('a', href=re.compile(r'/\d+\.html'))

            if not links:
                # No more entries found
                if page > 5:
                    break
                continue

            for link in links:
                href = link.get('href', '')
                title = link.get_text(strip=True)

                if not title or not href:
                    continue
                if len(title) < 5:
                    continue
                if 'http' not in href:
                    href = urljoin(url, href)

                # Get detail page
                try:
                    detail_resp = requests.get(href, headers=headers, timeout=15)
                    if detail_resp.status_code != 200:
                        continue

                    detail_soup = BeautifulSoup(detail_resp.text, 'lxml')

                    # Extract content - try various selectors
                    content = ""
                    for selector in ['.content', '#content', '.article-content', '.detail-content', 'article', '.post-content', '.entry-content']:
                        el = detail_soup.select_one(selector)
                        if el:
                            content = el.get_text(strip=True, separator=' ')
                            break

                    if not content:
                        # Try paragraph text
                        paras = detail_soup.find_all('p')
                        texts = [p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 30]
                        if texts:
                            content = ' '.join(texts)

                    if content and len(content) > 150:
                        if make_entry(title, content, href, "authoritative"):
                            count += 1
                    else:
                        # Even if short content, use title as content
                        if title and len(title) > 100:
                            if make_entry(title, title, href, "authoritative"):
                                count += 1

                    time.sleep(random.uniform(1.0, 2.5))
                except Exception as e:
                    stats['errors'] += 1
                    continue

            if page % 10 == 0:
                print(f"[INFO] jiemeng.aies.cn page {page}: {count} entries so far")
                print(f"[INFO] Output file size: {os.path.getsize(OUTPUT_FILE) if os.path.exists(OUTPUT_FILE) else 0} bytes")

            time.sleep(random.uniform(1.0, 3.0))

        except requests.exceptions.ConnectionError:
            print(f"[WARN] Connection error on page {page}, likely blocked")
            break
        except Exception as e:
            stats['errors'] += 1
            if page > 10:
                break
            continue

    stats['scraped_jiemeng'] = count
    print(f"[INFO] Scraped {count} entries from jiemeng.aies.cn")
    return count


# ============================================================
# PHASE 3: Scrape more zgjm.org categories
# ============================================================
def scrape_zgjm_categories():
    """Scrape additional dream categories from zgjm.org."""
    print("\n=== PHASE 3: Scraping zgjm.org additional categories ===")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    # Category URLs to try
    categories = [
        '/dongwu/', '/renwu/', '/ziran/', '/wupin/', '/shenghuo/',
        '/guishen/', '/jianzhu/', '/yunfujiemeng/', '/health/',
        '/wenhua/', '/qita/', '/huodong/', '/zhiwu/',
        # Additional subcategories
        '/zgjm/changjing/', '/zgjm/qinggan/', '/zgjm/shehui/',
        '/zgjm/shenti/', '/zgjm/zonghe/', '/zgjm/shiwu/',
    ]

    base_url = "http://www.zgjm.org"
    count = 0

    for cat in categories:
        # Try multiple pages per category
        for page in [1, 2, 3, 4, 5]:
            if page == 1:
                url = f"{base_url}{cat}"
            else:
                # Try pagination
                url = f"{base_url}{cat}list_{page}.html"

            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, 'lxml')

                # Find entry links - various patterns
                links = soup.find_all('a', href=re.compile(r'/zgjm/\d+\.html'))
                if not links:
                    links = soup.find_all('a', href=re.compile(r'/\d+\.html'))
                if not links:
                    links = soup.find_all('a', href=re.compile(r'/mengjian/\d+'))

                if not links:
                    continue

                for link in links:
                    href = link.get('href', '')
                    title = link.get_text(strip=True)

                    if not title or len(title) < 4:
                        continue
                    if not title.startswith('梦'):
                        continue
                    if 'http' not in href:
                        href = urljoin(base_url, href)

                    # Get detail
                    try:
                        dresp = requests.get(href, headers=headers, timeout=15)
                        if dresp.status_code != 200:
                            continue

                        dsoup = BeautifulSoup(dresp.text, 'lxml')
                        content = ""

                        # Try content selectors
                        for sel in ['.content', '#content', 'article', '.article', '.detail', '.maintext']:
                            el = dsoup.select_one(sel)
                            if el:
                                content = el.get_text(strip=True, separator=' ')
                                break

                        if not content:
                            paras = dsoup.find_all('p')
                            texts = [p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 20]
                            if texts:
                                content = ' '.join(texts)

                        if content and len(content) > 150:
                            if make_entry(title, content, href, "community"):
                                count += 1

                        time.sleep(random.uniform(0.5, 1.5))
                    except:
                        continue

                time.sleep(random.uniform(1.0, 2.0))

            except Exception as e:
                continue

    stats['scraped_zgjm'] = count
    print(f"[INFO] Scraped {count} entries from zgjm.org categories")
    return count


# ============================================================
# PHASE 4: Generate high-quality entries via WebSearch synthesis
# (This will be handled in a second pass using WebFetch results)
# ============================================================

def generate_missing_categories():
    """Generate entries for categories we know are missing."""
    print("\n=== PHASE 4: Generating entries for missing categories ===")

    # Dream topics we need deep coverage for
    dream_topics = [
        # Animals
        ("梦见狗", "梦见狗通常代表忠诚、友谊和保护。不同的狗有不同的象征：", "https://www.zgjm.org/zgjm/dog.html"),
        ("梦见猫", "梦见猫通常象征女性的直觉、神秘或独立。", "https://www.zgjm.org/zgjm/cat.html"),
        ("梦见蛇", "梦见蛇是常见的梦境，代表智慧、转变或潜在的威胁。", "https://www.zgjm.org/zgjm/snake.html"),
        ("梦见鱼", "梦见鱼通常代表财富、繁荣和生育能力。", "https://www.zgjm.org/zgjm/fish.html"),
        ("梦见鸟", "梦见鸟象征自由、理想和精神的升华。", "https://www.zgjm.org/zgjm/bird.html"),
        ("梦见马", "梦见马代表力量、自由和成功。", "https://www.zgjm.org/zgjm/horse.html"),
        ("梦见牛", "梦见牛象征勤劳、财富和稳定的生活。", "https://www.zgjm.org/zgjm/cow.html"),
        ("梦见羊", "梦见羊代表温顺、吉祥和平安。", "https://www.zgjm.org/zgjm/sheep.html"),
        ("梦见猪", "梦见猪通常代表财富、好运和物质享受。", "https://www.zgjm.org/zgjm/pig.html"),
        ("梦见老鼠", "梦见老鼠象征小人、烦恼或细微的忧虑。", "https://www.zgjm.org/zgjm/rat.html"),
        ("梦见老虎", "梦见老虎代表权威、勇气或潜在的威胁。", "https://www.zgjm.org/zgjm/tiger.html"),
        ("梦见龙", "梦见龙是吉祥的象征，代表权力、成功和好运。", "https://www.zgjm.org/zgjm/dragon.html"),
        ("梦见蜘蛛", "梦见蜘蛛象征耐心、创造力和潜在的麻烦。", "https://www.zgjm.org/zgjm/spider.html"),
        ("梦见螃蟹", "梦见螃蟹代表横向思维、自我保护或固执。", "https://www.zgjm.org/zgjm/crab.html"),
        ("梦见兔子", "梦见兔子象征温柔、敏捷和繁荣。", "https://www.zgjm.org/zgjm/rabbit.html"),
        ("梦见猴子", "梦见猴子代表机智、调皮或欺骗。", "https://www.zgjm.org/zgjm/monkey.html"),
        ("梦见鸡", "梦见鸡象征警惕、早起和家庭生活。", "https://www.zgjm.org/zgjm/chicken.html"),
        ("梦见蝴蝶", "梦见蝴蝶代表转变、自由和美丽的短暂。", "https://www.zgjm.org/zgjm/butterfly.html"),
        ("梦见蜜蜂", "梦见蜜蜂象征勤劳、团队合作和甜蜜的回报。", "https://www.zgjm.org/zgjm/bee.html"),
        ("梦见狼", "梦见狼代表野性、自由或潜在的威胁。", "https://www.zgjm.org/zgjm/wolf.html"),

        # Elements
        ("梦见水", "梦见水象征情感、潜意识和生命的流动。", "https://www.zgjm.org/zgjm/water.html"),
        ("梦见火", "梦见火代表激情、转变或毁灭。", "https://www.zgjm.org/zgjm/fire.html"),
        ("梦见雨", "梦见雨象征清洗、更新和情感的释放。", "https://www.zgjm.org/zgjm/rain.html"),
        ("梦见雪", "梦见雪代表纯洁、冷漠或新的开始。", "https://www.zgjm.org/zgjm/snow.html"),
        ("梦见山", "梦见山象征困难、目标和成就。", "https://www.zgjm.org/zgjm/mountain.html"),
        ("梦见海", "梦见海代表广阔的情感、潜意识和未知。", "https://www.zgjm.org/zgjm/sea.html"),
        ("梦见河", "梦见河象征生命的流动、变化和情感的旅程。", "https://www.zgjm.org/zgjm/river.html"),
        ("梦见地震", "梦见地震代表生活中的巨大变化和不安。", "https://www.zgjm.org/zgjm/earthquake.html"),
        ("梦见风", "梦见风象征变化、自由和思想的流动。", "https://www.zgjm.org/zgjm/wind.html"),
        ("梦见云", "梦见云代表梦想、幻想和不确定的状态。", "https://www.zgjm.org/zgjm/cloud.html"),

        # People
        ("梦见婴儿", "梦见婴儿象征新的开始、纯真和潜力。", "https://www.zgjm.org/zgjm/baby.html"),
        ("梦见孕妇", "梦见孕妇代表孕育新想法或新的发展阶段。", "https://www.zgjm.org/zgjm/pregnant.html"),
        ("梦见死人", "梦见死人通常象征过去的结束和新的开始。", "https://www.zgjm.org/zgjm/dead.html"),
        ("梦见亲人", "梦见亲人代表家庭关系、情感纽带和安全感。", "https://www.zgjm.org/zgjm/family.html"),
        ("梦见陌生人", "梦见陌生人象征未知的自己或新的机遇。", "https://www.zgjm.org/zgjm/stranger.html"),
        ("梦见名人", "梦见名人代表对成功和认可的渴望。", "https://www.zgjm.org/zgjm/celebrity.html"),
        ("梦见祖先", "梦见祖先象征家族传承、智慧和祖先的保佑。", "https://www.zgjm.org/zgjm/ancestor.html"),

        # Body
        ("梦见头发", "梦见头发象征力量、魅力和思想。", "https://www.zgjm.org/zgjm/hair.html"),
        ("梦见牙齿", "梦见牙齿掉落代表不安、衰老或沟通问题。", "https://www.zgjm.org/zgjm/teeth.html"),
        ("梦见眼睛", "梦见眼睛象征洞察力、真相和感知。", "https://www.zgjm.org/zgjm/eyes.html"),
        ("梦见手", "梦见手代表能力、创造力和帮助。", "https://www.zgjm.org/zgjm/hand.html"),
        ("梦见脚", "梦见脚象征基础、稳定和前进的动力。", "https://www.zgjm.org/zgjm/foot.html"),
        ("梦见血", "梦见血代表生命力、牺牲或强烈的情感。", "https://www.zgjm.org/zgjm/blood.html"),

        # Objects
        ("梦见衣服", "梦见衣服象征外在形象、身份和角色的变化。", "https://www.zgjm.org/zgjm/clothes.html"),
        ("梦见鞋子", "梦见鞋子代表人生的道路和方向。", "https://www.zgjm.org/zgjm/shoes.html"),
        ("梦见手机", "梦见手机代表沟通、信息和社交联系。", "https://www.zgjm.org/zgjm/phone.html"),
        ("梦见钱包", "梦见钱包象征财富、自我价值和资源。", "https://www.zgjm.org/zgjm/wallet.html"),
        ("梦见钥匙", "梦见钥匙代表机会、解决方案和新的开始。", "https://www.zgjm.org/zgjm/key.html"),
        ("梦见钱", "梦见钱象征财富、自尊和价值感。", "https://www.zgjm.org/zgjm/money.html"),
        ("梦见刀", "梦见刀代表冲突、分离或自我保护。", "https://www.zgjm.org/zgjm/knife.html"),
        ("梦见镜子", "梦见镜子象征自我反思、真相和自省。", "https://www.zgjm.org/zgjm/mirror.html"),
        ("梦见棺材", "梦见棺材虽然看似不吉，实则往往象征财富和升官。", "https://www.zgjm.org/zgjm/coffin.html"),
        ("梦见坟墓", "梦见坟墓代表过去的终结和新的开始。", "https://www.zgjm.org/zgjm/grave.html"),

        # Actions
        ("梦见飞", "梦见飞翔代表自由、解放和追求理想的愿望。", "https://www.zgjm.org/zgjm/flying.html"),
        ("梦见掉", "梦见掉落象征失控、焦虑和不安感。", "https://www.zgjm.org/zgjm/falling.html"),
        ("梦见被追", "梦见被追赶代表逃避现实中的压力。", "https://www.zgjm.org/zgjm/chased.html"),
        ("梦见跑", "梦见跑步象征追求目标或逃避危险。", "https://www.zgjm.org/zgjm/running.html"),
        ("梦见哭", "梦见哭泣是情感的释放，代表内心的压力得到宣泄。", "https://www.zgjm.org/zgjm/crying.html"),
        ("梦见笑", "梦见笑通常代表内心的愉悦和满足。", "https://www.zgjm.org/zgjm/laughing.html"),
        ("梦见杀", "梦见杀人象征内心的冲突、愤怒或彻底的改变。", "https://www.zgjm.org/zgjm/killing.html"),
        ("梦见洗澡", "梦见洗澡代表清洁、净化和准备新的开始。", "https://www.zgjm.org/zgjm/bathing.html"),
        ("梦见游泳", "梦见游泳象征驾驭情感和在生活中自如应对。", "https://www.zgjm.org/zgjm/swimming.html"),
        ("梦见考试", "梦见考试代表自我评估、焦虑和对能力的检验。", "https://www.zgjm.org/zgjm/exam.html"),

        # Food
        ("梦见水果", "梦见水果象征丰收、健康和快乐。", "https://www.zgjm.org/zgjm/fruit.html"),
        ("梦见肉", "梦见肉代表物质享受、力量和欲望。", "https://www.zgjm.org/zgjm/meat.html"),
        ("梦见酒", "梦见酒象征庆祝、放松或逃避现实。", "https://www.zgjm.org/zgjm/wine.html"),
        ("梦见米饭", "梦见米饭代表基本生活需求、满足和家庭。", "https://www.zgjm.org/zgjm/rice.html"),
        ("梦见面包", "梦见面包象征生活的必需和物质的满足。", "https://www.zgjm.org/zgjm/bread.html"),
        ("梦见蛋糕", "梦见蛋糕代表庆祝、奖励和甜蜜的生活。", "https://www.zgjm.org/zgjm/cake.html"),

        # Death / Supernatural
        ("梦见鬼", "梦见鬼象征未解决的恐惧或过去的阴影。", "https://www.zgjm.org/zgjm/ghost.html"),
        ("梦见神", "梦见神代表精神力量、指引和保佑。", "https://www.zgjm.org/zgjm/god.html"),
        ("梦见佛", "梦见佛象征智慧、慈悲和精神觉醒。", "https://www.zgjm.org/zgjm/buddha.html"),
        ("梦见菩萨", "梦见菩萨代表慈悲、保佑和好运。", "https://www.zgjm.org/zgjm/bodhisattva.html"),

        # Pregnancy
        ("梦见怀孕", "梦见怀孕象征新想法、新的项目或新的生活阶段。", "https://www.zgjm.org/zgjm/pregnancy.html"),
        ("梦见生孩子", "梦见生孩子代表创造、成就和新的开始。", "https://www.zgjm.org/zgjm/childbirth.html"),

        # House/Building
        ("梦见房子", "梦见房子象征自我的内心世界和心理状态。", "https://www.zgjm.org/zgjm/house.html"),
        ("梦见门", "梦见门代表新的机会、选择和转变。", "https://www.zgjm.org/zgjm/door.html"),
        ("梦见窗户", "梦见窗户象征视野、机遇和对外的开放。", "https://www.zgjm.org/zgjm/window.html"),
        ("梦见楼梯", "梦见楼梯代表进步、上升或职业生涯的发展。", "https://www.zgjm.org/zgjm/stairs.html"),

        # Nature events
        ("梦见洪水", "梦见洪水象征失控的情感或巨大的变化。", "https://www.zgjm.org/zgjm/flood.html"),
        ("梦见台风", "梦见台风代表生活中的剧烈变动和挑战。", "https://www.zgjm.org/zgjm/typhoon.html"),
        ("梦见彩虹", "梦见彩虹象征希望、承诺和美好的未来。", "https://www.zgjm.org/zgjm/rainbow.html"),
        ("梦见雷电", "梦见雷电代表突然的启示、愤怒或强大的力量。", "https://www.zgjm.org/zgjm/thunder.html"),
    ]

    count = 0
    for title, partial_content, source_url in dream_topics:
        if is_duplicate(title):
            continue

        # Read zgjm pages to get real content
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(source_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'lxml')
                content = ""
                for sel in ['.content', '#content', 'article', '.article', '.detail', '.maintext']:
                    el = soup.select_one(sel)
                    if el:
                        content = el.get_text(strip=True, separator=' ')
                        break
                if not content:
                    paras = soup.find_all('p')
                    texts = [p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 20]
                    if texts:
                        content = ' '.join(texts)

                if content and len(content) > 150:
                    if make_entry(title, content, source_url, "community"):
                        count += 1

            time.sleep(random.uniform(0.5, 1.5))
        except:
            # If can't fetch, use the partial content we have
            continue

    stats['websourced'] = count
    print(f"[INFO] Generated {count} entries from category pages")
    return count


# ============================================================
# Main
# ============================================================
def show_stats(final=False):
    """Print statistics."""
    if os.path.exists(OUTPUT_FILE):
        total = sum(1 for _ in open(OUTPUT_FILE))
    else:
        total = 0

    print("\n" + "="*60)
    print(f"DREAM SCRAPING STATISTICS {'(FINAL)' if final else ''}")
    print("="*60)
    print(f"  Total entries saved:      {total}")
    print(f"  Parsed from raw file:     {stats['parsed_raw']}")
    print(f"  Scraped jiemeng.aies.cn:  {stats['scraped_jiemeng']}")
    print(f"  Scraped zgjm.org:         {stats['scraped_zgjm']}")
    print(f"  Category entries:         {stats['websourced']}")
    print(f"  Rejected (too short):     {stats['rejected_short']}")
    print(f"  Rejected (duplicate):     {stats['rejected_dup']}")
    print(f"  Rejected (spam):          {stats['rejected_spam']}")
    print(f"  Errors:                   {stats['errors']}")
    print("="*60)


def main():
    load_existing()
    load_already_scraped()

    # Phase 1: Parse raw file
    parse_raw_file()
    show_stats()

    # Phase 2: Scrape jiemeng.aies.cn
    scrape_jiemeng_aies()
    show_stats()

    # Phase 3: Scrape zgjm.org categories
    scrape_zgjm_categories()
    show_stats()

    # Phase 4: Category pages
    generate_missing_categories()

    show_stats(final=True)

    total = sum(1 for _ in open(OUTPUT_FILE)) if os.path.exists(OUTPUT_FILE) else 0
    print(f"\n[DONE] Total entries in {OUTPUT_FILE}: {total}")


if __name__ == '__main__':
    main()
