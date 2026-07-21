#!/usr/bin/env python3
"""
易理明灯 — App Fortune-Telling API Scraper

Scrapes data from Chinese fortune-telling mobile apps and their public APIs
for accuracy comparison against our own AI fortune-telling system.

Supported app APIs:
  - yuanfenju:  缘份居 (yuanfenju.com) — full REST API (八字/紫微/解梦/运势)
  - iztro:      iZtro Chat API (chat-api.iztro.com) — 紫微斗数 AI interpretation
  - juhe:       聚合数据 (juhe.cn) — 八字排盘基础数据
  - wenzhen:    问真八字 (bzapi3.iwzbz.com) — 八字排盘 chart API (extension)

Closed apps (documented but not scrapable without reverse engineering):
  - ceceapp:    测测 (ceceapp.com) — needs mitmproxy + SSL pinning bypass
  - zhunliao:   准了 (zhunliao.com) — needs mitmproxy
  - yiqi:       易奇八字 — needs mitmproxy
  - lingji:     灵机八字 — needs mitmproxy
  - shen88app:  神巴巴 (shen88.cn) — mobile app, low priority

Usage:
    python scripts/scrape_apps.py --platform yuanfenju --count 20
    python scripts/scrape_apps.py --platform all --count 50
    python scripts/scrape_apps.py --dry-run
    python scripts/scrape_apps.py --platform yuanfenju --test  # quick 5-query test
"""

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data" / "competitor_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BENCHMARK_PATH = PROJECT_DIR / "data" / "eval" / "benchmark_queries_v2.jsonl"
BENCHMARK_PATH_V1 = PROJECT_DIR / "data" / "eval" / "benchmark_queries.jsonl"

# Rate limiting defaults
DEFAULT_DELAY = 1.0  # seconds between requests
DEFAULT_TIMEOUT = 30.0

# User agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def make_output_path(platform: str) -> Path:
    """Generate a date-stamped JSONL output path for a given app platform."""
    date_str = datetime.now().strftime("%Y%m%d")
    return DATA_DIR / f"app_{platform}_{date_str}.jsonl"


def save_record(record: dict, output_path: Path):
    """Append one JSON record to a JSONL file."""
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def make_record(query: str, platform: str, response: str, url: str) -> dict:
    """Create a standardized record dict."""
    return {
        "query": query,
        "platform": platform,
        "response": response,
        "url": url,
        "scraped_at": datetime.now().isoformat(),
    }


def random_headers(extra: Optional[dict] = None) -> dict:
    """Return request headers with a random User-Agent."""
    ua = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "application/json, text/plain, */*",
    }
    if extra:
        headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# Benchmark query loader
# ---------------------------------------------------------------------------


def load_benchmark_queries(limit: Optional[int] = None) -> list[dict]:
    """Load queries from benchmark_queries_v2.jsonl (fallback to v1)."""
    path = BENCHMARK_PATH if BENCHMARK_PATH.exists() else BENCHMARK_PATH_V1
    if not path.exists():
        logger.warning(f"No benchmark file found at {path}")
        # Generate minimal default queries
        return [
            {"id": "default_001", "domain": "bazi", "query": "师傅，我属龙的，1988年生的，这两年工作一直不顺，什么时候能好转啊？"},
            {"id": "default_002", "domain": "bazi", "query": "我五行属火，缺木，这种八字是不是财运很差？"},
            {"id": "default_003", "domain": "dream", "query": "梦见蛇是什么意思？"},
            {"id": "default_004", "domain": "bazi", "query": "大师，我83年属猪，老婆85年属牛，我们老吵架，八字合吗？"},
            {"id": "default_005", "domain": "ziwei", "query": "请分析这个命盘的整体格局、财运和事业运势"},
        ]

    queries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))

    if limit and limit < len(queries):
        # Try to get diverse samples across domains
        domains = {}
        for q in queries:
            domain = q.get("domain", "unknown")
            if domain not in domains:
                domains[domain] = []
            domains[domain].append(q)

        sampled = []
        per_domain = max(1, limit // len(domains))
        for domain, domain_queries in sorted(domains.items()):
            sampled.extend(domain_queries[:per_domain])
        # If we still need more, add from remaining
        remaining = limit - len(sampled)
        if remaining > 0:
            already_added = {q["id"] for q in sampled}
            for q in queries:
                if q["id"] not in already_added:
                    sampled.append(q)
                    remaining -= 1
                    if remaining <= 0:
                        break
        queries = sampled[:limit]

    logger.info(f"Loaded {len(queries)} benchmark queries from {path.name}")
    return queries


# ---------------------------------------------------------------------------
# API Client: 缘份居 (yuanfenju.com)
# ---------------------------------------------------------------------------
# Public REST API with free API key
# Base: https://api.yuanfenju.com/index.php/v1/
# Auth: api_key parameter
# Format: application/x-www-form-urlencoded
# ---------------------------------------------------------------------------

YUANFENJU_BASE = "https://api.yuanfenju.com/index.php/v1"
YUANFENJU_API_KEY = os.environ.get("YUANFENJU_API_KEY", "")

# Mapping of query domains to yuanfenju API endpoints
YUANFENJU_ENDPOINTS = {
    "bazi": "/Bazi/paipan",       # 八字排盘
    "bazi_cesuan": "/Bazi/cesuan",  # 八字测算
    "bazi_yunshi": "/Bazi/yunshi",  # 八字运势
    "ziwei": "/Ziwei/paipan",     # 紫微斗数排盘 (推测路径)
    "dream": "/Zhanbu/dream",     # 周公解梦 (推测路径)
    "yunshi": "/Zhanbu/yunshi",   # 每日运势 (星座/生肖)
    "hehun": "/Bazi/hehun",       # 合婚分析 (推测路径)
}


def _extract_birth_info(query: str) -> dict:
    """Extract birth year, zodiac, and gender from a query string.

    Returns dict with keys: year, zodiac, gender, month, day, hour (all optional).
    """
    info = {"year": None, "zodiac": None, "gender": None, "month": None, "day": None, "hour": None}

    # Chinese zodiac animals
    zodiac_map = {
        "鼠": 0, "牛": 1, "虎": 2, "兔": 3, "龙": 4, "蛇": 5,
        "马": 6, "羊": 7, "猴": 8, "鸡": 9, "狗": 10, "猪": 11,
    }

    # Year patterns: 19xx年, 20xx年, 19xx, xxxx年
    year_match = re.search(r'(\d{4})年?', query)
    if year_match:
        year = int(year_match.group(1))
        if 1900 <= year <= 2100:
            info["year"] = year

    # Two-digit year patterns: 83年, 88年, 91年
    if not info["year"]:
        year_match = re.search(r'(\d{2})年', query)
        if year_match:
            year_suffix = int(year_match.group(1))
            # Assume 1900s for 00-99, prefer 1900s for most users
            if 0 <= year_suffix <= 30:
                info["year"] = 2000 + year_suffix
            else:
                info["year"] = 1900 + year_suffix

    # Zodiac patterns
    for animal, _ in zodiac_map.items():
        if f"属{animal}" in query:
            info["zodiac"] = animal
            break

    # Gender
    if any(w in query for w in ["男", "先生", "哥", "爸", "兄弟"]):
        info["gender"] = 1
    elif any(w in query for w in ["女", "女士", "姐", "妈", "姐妹", "女生"]):
        info["gender"] = 0

    # Age-based gender hints
    if info["gender"] is None:
        # Check common phrases
        if re.search(r'(我|\s)(老公|男朋友|儿子|爸爸|兄弟|弟弟|哥哥|爷爷|孙子|女婿)', query):
            info["gender"] = 1
        elif re.search(r'(我|\s)(老婆|女朋友|女儿|妈妈|姐妹|姐姐|妹妹|奶奶|孙女|儿媳)', query):
            info["gender"] = 0

    # Month/day patterns (for more specific queries)
    month_match = re.search(r'(\d{1,2})月', query)
    if month_match:
        info["month"] = int(month_match.group(1))

    day_match = re.search(r'(\d{1,2})[日号]', query)
    if day_match:
        info["day"] = int(day_match.group(1))

    # Hour patterns
    hour_map = {
        "子": 23, "丑": 1, "寅": 3, "卯": 5, "辰": 7, "巳": 9,
        "午": 11, "未": 13, "申": 15, "酉": 17, "戌": 19, "亥": 21,
    }
    for hz, hour in hour_map.items():
        if f"{hz}时" in query:
            info["hour"] = hour
            break

    # Numeric hour: 早上8点, 下午2点, 3点
    if not info["hour"]:
        hour_match = re.search(r'(\d{1,2})[点时]', query)
        if hour_match:
            info["hour"] = int(hour_match.group(1))

    return info


def _build_yuanfenju_params(endpoint: str, query: str, info: dict) -> Optional[dict]:
    """Build API parameters for a yuanfenju endpoint based on query type."""
    if not YUANFENJU_API_KEY:
        logger.warning("YUANFENJU_API_KEY not set. Use environment variable or config.")
        return None

    params = {"api_key": YUANFENJU_API_KEY}

    if endpoint in ("/Bazi/paipan", "/Bazi/cesuan", "/Bazi/yunshi"):
        # Need birth info for bazi queries
        year = info.get("year")
        if not year:
            # Try to use a reasonable default year based on zodiac
            zodiac = info.get("zodiac")
            if zodiac:
                zodiac_years = {
                    "鼠": [1996, 1984, 1972], "牛": [1997, 1985, 1973],
                    "虎": [1998, 1986, 1974], "兔": [1999, 1987, 1975],
                    "龙": [2000, 1988, 1976], "蛇": [2001, 1989, 1977],
                    "马": [2002, 1990, 1978], "羊": [2003, 1991, 1979],
                    "猴": [2004, 1992, 1980], "鸡": [2005, 1993, 1981],
                    "狗": [2006, 1994, 1982], "猪": [2007, 1995, 1983],
                }
                years = zodiac_years.get(zodiac, [1990])
                year = years[0]  # Pick the youngest typical age

        if not year:
            year = 1990  # Default

        params.update({
            "name": query[:20],  # Use query as name placeholder
            "sex": str(info.get("gender") or 1),
            "type": "1",  # 1=公历, 0=农历
            "year": str(year),
            "month": str(info.get("month") or 6),
            "day": str(info.get("day") or 15),
            "hours": str(info.get("hour") or 12),
            "minute": "0",
        })
        return params

    elif endpoint == "/Zhanbu/yunshi":
        # Daily fortune by zodiac or constellation
        zodiac = info.get("zodiac")
        if zodiac:
            zodiac_ids = {
                "鼠": 0, "牛": 1, "虎": 2, "兔": 3, "龙": 4, "蛇": 5,
                "马": 6, "羊": 7, "猴": 8, "鸡": 9, "狗": 10, "猪": 11,
            }
            params.update({
                "type": "1",  # 1=生肖
                "title_yunshi": str(zodiac_ids.get(zodiac, 0)),
            })
        else:
            # Default to 鼠 (rat)
            params.update({
                "type": "1",
                "title_yunshi": "0",
            })
        return params

    elif endpoint == "/Zhanbu/dream":
        # Dream interpretation - use query as dream description
        params.update({
            "keyword": query[:50],
        })
        return params

    elif endpoint == "/Ziwei/paipan":
        year = info.get("year") or 1990
        params.update({
            "name": query[:20],
            "sex": str(info.get("gender") or 1),
            "type": "1",
            "year": str(year),
            "month": str(info.get("month") or 6),
            "day": str(info.get("day") or 15),
            "hours": str(info.get("hour") or 12),
            "minute": "0",
        })
        return params

    elif endpoint == "/Bazi/hehun":
        # Need two people's info; we'll use the query as-is with partial info
        year = info.get("year") or 1990
        params.update({
            "name1": "甲",
            "sex1": str(info.get("gender") or 1),
            "year1": str(year),
            "month1": "6",
            "day1": "15",
            "hours1": "12",
            "name2": "乙",
            "sex2": "1",
            "year2": str(year - 2),
            "month2": "3",
            "day2": "10",
            "hours2": "8",
        })
        return params

    return params


async def scrape_yuanfenju(
    client: httpx.AsyncClient,
    queries: list[dict],
    count: int,
    dry_run: bool,
) -> list[dict]:
    """Scrape data from 缘份居 API."""
    results = []

    if dry_run:
        logger.info(
            f"  [yuanfenju] Would query ~{count} benchmark queries via "
            f"缘份居 API (base: {YUANFENJU_BASE})"
        )
        return []

    if not YUANFENJU_API_KEY:
        logger.warning(
            "  [yuanfenju] YUANFENJU_API_KEY not set. "
            "Register at https://doc.yuanfenju.com to get a free key."
        )
        logger.warning("  [yuanfenju] Set via: export YUANFENJU_API_KEY='your_key_here'")
        return []

    collected = 0
    for q in queries[:count]:
        if collected >= count:
            break

        query_text = q.get("query", "")
        domain = q.get("domain", "bazi")

        # Determine which endpoint to use based on domain
        if domain == "dream":
            endpoint_key = "dream"
        elif domain == "ziwei":
            endpoint_key = "ziwei"
        elif "合" in query_text or domain == "hehun":
            endpoint_key = "hehun"
        elif "运势" in query_text or "运气" in query_text:
            endpoint_key = "bazi_yunshi"
        else:
            endpoint_key = "bazi"

        endpoint_path = YUANFENJU_ENDPOINTS.get(endpoint_key, YUANFENJU_ENDPOINTS["bazi"])
        url = f"{YUANFENJU_BASE}{endpoint_path}"

        info = _extract_birth_info(query_text)
        params = _build_yuanfenju_params(endpoint_path, query_text, info)

        if not params:
            continue

        await asyncio.sleep(DEFAULT_DELAY * (0.8 + 0.4 * random.random()))

        try:
            resp = await client.post(
                url,
                data=params,
                headers=random_headers({"Content-Type": "application/x-www-form-urlencoded"}),
                timeout=30,
            )

            if resp.status_code == 200:
                data = resp.json()
                errcode = data.get("errcode", -1)

                if errcode == 0:
                    # Success - extract response text
                    response_text = json.dumps(data, ensure_ascii=False)

                    # Limit response size
                    if len(response_text) > 5000:
                        response_text = response_text[:5000]

                    results.append(make_record(
                        query=query_text,
                        platform="yuanfenju",
                        response=response_text,
                        url=url,
                    ))
                    collected += 1

                    if collected % 10 == 0:
                        logger.info(f"  [yuanfenju] Collected {collected}/{count}")
                elif errcode == -1:
                    errmsg = data.get("errmsg", "")
                    logger.warning(f"  [yuanfenju] API auth error: {errmsg}")
                    # If auth fails, stop trying
                    logger.warning("  [yuanfenju] Check your YUANFENJU_API_KEY")
                    break
                else:
                    logger.debug(f"  [yuanfenju] API returned errcode={errcode}: {data.get('errmsg', '')}")
            elif resp.status_code == 429:
                logger.warning("  [yuanfenju] Rate limited, waiting...")
                await asyncio.sleep(5)
            else:
                logger.warning(f"  [yuanfenju] HTTP {resp.status_code}: {url}")

        except httpx.TimeoutException:
            logger.warning(f"  [yuanfenju] Timeout: {url}")
        except json.JSONDecodeError:
            logger.warning(f"  [yuanfenju] Invalid JSON response from {url}")
        except httpx.HTTPError as e:
            logger.warning(f"  [yuanfenju] HTTP error: {e}")
        except Exception as e:
            logger.warning(f"  [yuanfenju] Error: {e}")

    logger.info(f"  [yuanfenju] Done: {len(results)} records from {YUANFENJU_BASE}")
    return results


# ---------------------------------------------------------------------------
# API Client: iZtro Chat API (v2)
# ---------------------------------------------------------------------------
# Zi Wei Dou Shu AI interpretation via chat-api.iztro.com
# Docs: https://api-doc.iztro.com
#
# v2 API endpoints:
#   POST /v2/platform/sessions              — Create session
#   POST /v2/platform/sessions/{id}/messages — Send message
#
# Auth: Authorization: Bearer $IZTRO_API_KEY
# ---------------------------------------------------------------------------

IZTRO_API_BASE = "https://chat-api.iztro.com"
IZTRO_API_KEY = os.environ.get("IZTRO_API_KEY", "")

# Sample birth profiles for iztro queries
IZTRO_SAMPLE_BIRTHS = [
    {"year": 1988, "month": 12, "day": 5, "hour": 6, "gender": 1,
     "zodiac": "龙", "query_hint": "事业财运"},
    {"year": 1990, "month": 3, "day": 15, "hour": 8, "gender": 1,
     "zodiac": "马", "query_hint": "感情婚姻"},
    {"year": 1995, "month": 7, "day": 22, "hour": 14, "gender": 0,
     "zodiac": "猪", "query_hint": "整体运势"},
    {"year": 2000, "month": 1, "day": 10, "hour": 10, "gender": 0,
     "zodiac": "龙", "query_hint": "学业事业"},
    {"year": 1985, "month": 9, "day": 18, "hour": 16, "gender": 1,
     "zodiac": "牛", "query_hint": "财运"},
    {"year": 1992, "month": 5, "day": 3, "hour": 12, "gender": 0,
     "zodiac": "猴", "query_hint": "健康"},
    {"year": 1983, "month": 11, "day": 8, "hour": 4, "gender": 1,
     "zodiac": "猪", "query_hint": "婚姻合盘"},
    {"year": 1998, "month": 8, "day": 25, "hour": 18, "gender": 0,
     "zodiac": "虎", "query_hint": "事业方向"},
    {"year": 1976, "month": 6, "day": 12, "hour": 2, "gender": 1,
     "zodiac": "龙", "query_hint": "晚年运势"},
    {"year": 2005, "month": 4, "day": 3, "hour": 9, "gender": 0,
     "zodiac": "鸡", "query_hint": "学业考试"},
]

IZTRO_QUERIES = [
    "请分析这个命盘的整体格局、财运和事业运势",
    "请分析这个命盘的感情婚姻情况，什么时候能遇到正缘？",
    "请分析这个命盘的健康运势和大运走势",
    "请全面解读这个紫微斗数命盘，包括事业、财运、感情和健康",
    "这个命盘的格局如何？适合从事什么行业？",
]


async def scrape_iztro(
    client: httpx.AsyncClient,
    queries: list[dict],
    count: int,
    dry_run: bool,
) -> list[dict]:
    """Scrape data from iZtro Zi Wei Dou Shu AI Chat API (v2)."""
    results = []

    if dry_run:
        logger.info(f"  [iztro] Would query ~{count} Zi Wei Dou Shu interpretations via iZtro v2 API")
        return []

    if not IZTRO_API_KEY:
        logger.warning("  [iztro] IZTRO_API_KEY not set. Get one from https://iztro.com")
        logger.warning("  [iztro] Set via: export IZTRO_API_KEY='your_key_here'")
        return []

    collected = 0

    # Use benchmark queries that are about ziwei or general fortune
    ziwei_queries = [q for q in queries if q.get("domain") in ("ziwei", "bazi", None)]
    iztro_prompts = IZTRO_QUERIES.copy()
    for q in ziwei_queries[:max(1, count // 2)]:
        iztro_prompts.append(q.get("query", ""))

    for i, birth in enumerate(IZTRO_SAMPLE_BIRTHS[:max(1, count)]):
        if collected >= count:
            break

        prompt = iztro_prompts[i % len(iztro_prompts)]

        await asyncio.sleep(DEFAULT_DELAY * (0.8 + 0.4 * random.random()))

        try:
            # Step 1: Create session (v2)
            auth_headers = {
                "Authorization": f"Bearer {IZTRO_API_KEY}",
                "Content-Type": "application/json",
            }

            session_payload = {
                "external_user_id": f"fortune_bot_{birth['year']}_{birth['month']}_{birth['day']}",
            }

            session_resp = await client.post(
                f"{IZTRO_API_BASE}/v2/platform/sessions",
                json=session_payload,
                headers=random_headers(auth_headers),
                timeout=30,
            )

            if session_resp.status_code != 200:
                logger.warning(f"  [iztro] Session create failed: HTTP {session_resp.status_code}")
                continue

            session_data = session_resp.json()
            session_id = (
                session_data.get("sessionId") or
                session_data.get("id") or
                session_data.get("data", {}).get("sessionId")
            )
            if not session_id:
                logger.warning(f"  [iztro] No sessionId in response: {str(session_data)[:200]}")
                continue

            logger.info(f"  [iztro] Session created for {birth['year']}-{birth['month']:02d}")

            # Step 2: Send message with birth info
            message_payload = {
                "message": f"分析我的命盘。生日是 {birth['year']}-{birth['month']:02d}-{birth['day']:02d}，出生时辰 {birth['hour']} 点，性别{'男' if birth['gender'] == 1 else '女'}。{prompt}",
                "title": f"{birth['year']}年命盘分析 — {birth.get('query_hint', '')}",
                "language": "zh",
                "enable_iztro_call": True,
            }

            msg_resp = await client.post(
                f"{IZTRO_API_BASE}/v2/platform/sessions/{session_id}/messages",
                json=message_payload,
                headers=random_headers(auth_headers),
                timeout=60,
            )

            if msg_resp.status_code == 200:
                msg_data = msg_resp.json()
                interpretation = (
                    msg_data.get("reply", "") or
                    msg_data.get("message", "") or
                    msg_data.get("content", "") or
                    msg_data.get("data", {}).get("reply", "") or
                    msg_data.get("data", {}).get("message", "") or
                    json.dumps(msg_data, ensure_ascii=False)
                )

                if interpretation and len(str(interpretation)) > 20:
                    query_title = (
                        f"分析{birth['year']}年{birth['month']}月{birth['day']}日"
                        f"{birth['hour']}时出生的紫微斗数命盘 — {birth.get('query_hint', '')}"
                    )
                    results.append(make_record(
                        query=query_title,
                        platform="iztro",
                        response=str(interpretation)[:2000],
                        url=f"{IZTRO_API_BASE}/v2/platform/sessions/{{id}}/messages",
                    ))
                    collected += 1
                    logger.info(f"  [iztro] Collected {collected}/{count}")
            else:
                logger.warning(f"  [iztro] Message send failed: HTTP {msg_resp.status_code}")

        except httpx.TimeoutException:
            logger.warning(f"  [iztro] Timeout for birth {birth['year']}-{birth['month']:02d}")
        except json.JSONDecodeError:
            logger.warning(f"  [iztro] Invalid JSON response")
        except httpx.HTTPError as e:
            logger.warning(f"  [iztro] HTTP error: {e}")
        except Exception as e:
            logger.warning(f"  [iztro] Error: {e}")

    logger.info(f"  [iztro] Done: {len(results)} interpretations")
    return results


# ---------------------------------------------------------------------------
# API Client: 聚合数据 (juhe.cn)
# ---------------------------------------------------------------------------
# Basic bazi chart data via juhe API
# ---------------------------------------------------------------------------

JUHE_BASE = "http://apis.juhe.cn/birthEight"
JUHE_API_KEY = os.environ.get("JUHE_API_KEY", "")


async def scrape_juhe(
    client: httpx.AsyncClient,
    queries: list[dict],
    count: int,
    dry_run: bool,
) -> list[dict]:
    """Scrape basic bazi chart data from 聚合数据 API."""
    results = []

    if dry_run:
        logger.info(f"  [juhe] Would query ~{count} bazi charts via 聚合数据 API")
        return []

    if not JUHE_API_KEY:
        logger.warning("  [juhe] JUHE_API_KEY not set (optional, skip if not needed)")
        logger.warning("  [juhe] Set via: export JUHE_API_KEY='your_key_here'")
        return []

    collected = 0
    for q in queries[:count]:
        if collected >= count:
            break

        query_text = q.get("query", "")
        info = _extract_birth_info(query_text)
        year = info.get("year") or 1990

        await asyncio.sleep(DEFAULT_DELAY * (0.8 + 0.4 * random.random()))

        try:
            params = {
                "key": JUHE_API_KEY,
                "year": str(year),
                "month": str(info.get("month") or 6),
                "day": str(info.get("day") or 15),
                "hour": str(info.get("hour") or 12),
            }

            resp = await client.get(
                f"{JUHE_BASE}/query",
                params=params,
                headers=random_headers(),
                timeout=15,
            )

            if resp.status_code == 200:
                data = resp.json()
                if data.get("error_code") == 0:
                    result_data = data.get("result", {})
                    response_text = json.dumps(result_data, ensure_ascii=False)

                    results.append(make_record(
                        query=query_text,
                        platform="juhe",
                        response=response_text[:2000],
                        url=f"{JUHE_BASE}/query",
                    ))
                    collected += 1
                else:
                    reason = data.get("reason", "unknown")
                    logger.debug(f"  [juhe] API error: {reason}")
            else:
                logger.warning(f"  [juhe] HTTP {resp.status_code}")

        except Exception as e:
            logger.warning(f"  [juhe] Error: {e}")

    logger.info(f"  [juhe] Done: {len(results)} records")
    return results


# ---------------------------------------------------------------------------
# API Client: 问真八字 (bzapi3.iwzbz.com)
# ---------------------------------------------------------------------------
# Chart calculation API (already extensively scraped in scrape_wenzhen.py)
# This is an extension to query specific benchmark queries
# Note: This is a GET-based API with no authentication
# ---------------------------------------------------------------------------

WENZHEN_BASE = "http://bzapi3.iwzbz.com"

# Gender codes for wenzhen API
GENDER_CODES = {1: "1", 0: "0"}  # 男=1, 女=0


async def scrape_wenzhen(
    client: httpx.AsyncClient,
    queries: list[dict],
    count: int,
    dry_run: bool,
) -> list[dict]:
    """Scrape bazi chart data from 问真八字 API for specific benchmark queries."""
    results = []

    if dry_run:
        logger.info(f"  [wenzhen] Would query ~{count} bazi charts via 问真八字 API")
        return []

    collected = 0
    for q in queries[:count]:
        if collected >= count:
            break

        query_text = q.get("query", "")
        info = _extract_birth_info(query_text)
        year = info.get("year")

        if not year:
            continue  # Skip queries without year info

        gender_code = GENDER_CODES.get(info.get("gender") or 1, "1")
        month = info.get("month") or 6
        day = info.get("day") or 15
        hour = info.get("hour") or 12

        await asyncio.sleep(DEFAULT_DELAY * (0.5 + 0.3 * random.random()))

        try:
            url = f"{WENZHEN_BASE}/getbasebz8.php"
            params = {
                "date": f"{year}-{month:02d}-{day:02d}",
                "time": f"{hour:02d}:00",
                "gender": gender_code,
            }

            resp = await client.get(
                url,
                params=params,
                headers=random_headers(),
                timeout=15,
            )

            if resp.status_code == 200:
                data = resp.json()
                # The wenzhen API returns data with keys: bz (八字), ss (十神), etc.
                if isinstance(data, dict) and "bz" in data:
                    response_text = json.dumps(data, ensure_ascii=False)[:2000]
                    results.append(make_record(
                        query=query_text,
                        platform="wenzhen",
                        response=response_text,
                        url=url,
                    ))
                    collected += 1
                elif isinstance(data, dict) and data.get("code") == 0:
                    response_text = json.dumps(data, ensure_ascii=False)[:2000]
                    results.append(make_record(
                        query=query_text,
                        platform="wenzhen",
                        response=response_text,
                        url=url,
                    ))
                    collected += 1
                else:
                    logger.debug(f"  [wenzhen] Unexpected response format for {year}-{month:02d}-{day:02d}: {str(data)[:200]}")
            elif resp.status_code == 404:
                logger.debug(f"  [wenzhen] 404 for date {year}-{month:02d}-{day:02d}")
            else:
                logger.debug(f"  [wenzhen] HTTP {resp.status_code} for {year}-{month:02d}-{day:02d}")

        except httpx.TimeoutException:
            logger.debug(f"  [wenzhen] Timeout for {year}-{month:02d}-{day:02d}")
        except json.JSONDecodeError:
            logger.debug(f"  [wenzhen] Invalid JSON")
        except Exception as e:
            logger.debug(f"  [wenzhen] Error: {e}")

    logger.info(f"  [wenzhen] Done: {len(results)} records")
    return results


# ---------------------------------------------------------------------------
# Master scraper dispatcher
# ---------------------------------------------------------------------------

PLATFORMS = {
    "yuanfenju": {
        "scraper": scrape_yuanfenju,
        "desc": "缘份居 (yuanfenju.com) — 八字/紫微/解梦/运势 REST API",
        "status": "Ready (needs API key)",
        "priority": 1,
    },
    "iztro": {
        "scraper": scrape_iztro,
        "desc": "iZtro (chat-api.iztro.com) — 紫微斗数 AI 解读 (v2 API)",
        "status": "Ready (needs API key)",
        "priority": 2,
    },
    "juhe": {
        "scraper": scrape_juhe,
        "desc": "聚合数据 (juhe.cn) — 八字排盘基础数据",
        "status": "Ready (needs API key)",
        "priority": 3,
    },
    "wenzhen": {
        "scraper": scrape_wenzhen,
        "desc": "问真八字 (bzapi3.iwzbz.com) — 八字排盘图表 API (已有44k+数据)",
        "status": "Ready",
        "priority": 4,
    },
}

CLOSED_APPS = {
    "ceceapp": {
        "name": "测测",
        "desc": "测测 (ceceapp.com) — 星座/八字/紫微斗数",
        "status": "需要 mitmproxy + Frida 绕过 SSL Pinning",
        "difficulty": "高",
    },
    "zhunliao": {
        "name": "准了",
        "desc": "准了 (zhunliao.com) — 星盘/八字/紫微斗数",
        "status": "需要 mitmproxy 抓包测试",
        "difficulty": "中高",
    },
    "yiqi": {
        "name": "易奇八字",
        "desc": "易奇八字 — 八字/解梦/合婚",
        "status": "需要 mitmproxy 抓包测试",
        "difficulty": "中",
    },
    "lingji": {
        "name": "灵机八字",
        "desc": "灵机八字 — 八字/运势/黄历",
        "status": "需要 mitmproxy 抓包测试",
        "difficulty": "中",
    },
    "shen88app": {
        "name": "神巴巴",
        "desc": "神巴巴 (shen88.cn) — 移动端 (Web已有28条数据)",
        "status": "移动端与Web同源概率大",
        "difficulty": "低 (价值有限)",
    },
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def create_http_client() -> httpx.AsyncClient:
    """Create an httpx AsyncClient with sensible defaults."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=15.0),
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )


def print_report():
    """Print a summary of all apps and their scraping status."""
    print(f"\n{'=' * 70}")
    print(f"  易理明灯 — App Fortune-Telling API Research Report")
    print(f"{'=' * 70}")

    print(f"\n{'─' * 70}")
    print(f"  ACCESSIBLE APIs (sorted by priority)")
    print(f"{'─' * 70}")
    for key, info in sorted(PLATFORMS.items(), key=lambda x: x[1]["priority"]):
        print(f"  [{info['priority']}] {key:15s} — {info['desc']}")
        print(f"      Status: {info['status']}")

    print(f"\n{'─' * 70}")
    print(f"  CLOSED Apps (require reverse engineering)")
    print(f"{'─' * 70}")
    for key, info in CLOSED_APPS.items():
        print(f"  • {info['name']:10s} ({key})")
        print(f"    {info['desc']}")
        print(f"    Status: {info['status']} [难度: {info['difficulty']}]")

    print(f"\n{'─' * 70}")
    print(f"  Environment Variables")
    print(f"{'─' * 70}")
    print(f"  YUANFENJU_API_KEY = {'✓ Set' if YUANFENJU_API_KEY else '✗ Not set'}")
    print(f"  IZTRO_API_KEY     = {'✓ Set' if IZTRO_API_KEY else '✗ Not set'}")
    print(f"  JUHE_API_KEY      = {'✓ Set' if JUHE_API_KEY else '✗ Not set (optional)'}")

    print(f"\n  API Keys:")
    print(f"  • 缘份居: Register at https://doc.yuanfenju.com for a free API key")
    print(f"  • iZtro:  Get API key from https://iztro.com (iztro-ziwei-v3 model)")
    print(f"  • 聚合数据: Register at https://www.juhe.cn for a free API key")
    print(f"\n")


async def main():
    parser = argparse.ArgumentParser(
        description="易理明灯 — App Fortune-Telling Data Scraper"
    )
    parser.add_argument(
        "--platform", type=str, default="all",
        choices=list(PLATFORMS.keys()) + ["all"],
        help="App platform to scrape (default: all accessible APIs)"
    )
    parser.add_argument(
        "--count", type=int, default=50,
        help="Number of queries to process per platform (default: 50)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be scraped without actually scraping"
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY,
        help=f"Delay between requests in seconds (default: {DEFAULT_DELAY})"
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Print research report of all apps and their API status"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Quick test mode: run with 5 queries only"
    )
    args = parser.parse_args()

    if args.report:
        print_report()
        return

    # Load benchmark queries
    test_count = 5 if args.test else args.count
    queries = load_benchmark_queries(limit=min(test_count * 3, 200))

    if not queries:
        logger.error("No benchmark queries found. Cannot proceed.")
        sys.exit(1)

    platforms_to_scrape = (
        list(PLATFORMS.keys()) if args.platform == "all" else [args.platform]
    )

    print(f"\n{'=' * 70}")
    print(f"  易理明灯 — App Fortune-Telling Data Scraper")
    print(f"{'=' * 70}")
    print(f"  Mode:       {'TEST (5 queries)' if args.test else f'{test_count} queries'}")
    print(f"  Dry run:    {'YES' if args.dry_run else 'NO'}")
    print(f"  Delay:      {args.delay}s between requests")
    print(f"  Platforms:  {', '.join(platforms_to_scrape)}")
    print(f"  Output:     {DATA_DIR}/app_{{platform}}_{{date}}.jsonl")
    print(f"  Queries:    {len(queries)} loaded from benchmark")
    print()

    if args.dry_run:
        print(f"{'─' * 70}")
        print(f"  DRY RUN — No data will be saved")
        print(f"{'─' * 70}")

    if not args.dry_run and not args.test:
        # Remind about API keys
        if "yuanfenju" in platforms_to_scrape and not YUANFENJU_API_KEY:
            print(f"  ⚠  YUANFENJU_API_KEY not set. yuanfenju will produce 0 records.")
            print(f"     Get a free key at https://doc.yuanfenju.com")
            print(f"     Then: export YUANFENJU_API_KEY='your_key_here'")
            print()

    client = create_http_client()
    total_records = 0

    try:
        for platform in platforms_to_scrape:
            info = PLATFORMS.get(platform)
            if not info:
                continue

            print(f"\n{'─' * 70}")
            print(f"  Platform: {platform} — {info['desc']}")
            print(f"{'─' * 70}")

            scraper_fn = info["scraper"]
            records = await scraper_fn(client, queries, test_count if args.test else args.count, args.dry_run)

            if records and not args.dry_run:
                output_path = make_output_path(platform)
                for record in records:
                    save_record(record, output_path)
                print(f"\n  ✓ Saved {len(records)} records to {output_path}")
                total_records += len(records)
            elif not records and not args.dry_run:
                print(f"  — No records collected")
            else:
                print(f"  — [DRY RUN] Would attempt to collect records")

    finally:
        await client.aclose()

    print(f"\n{'=' * 70}")
    if args.dry_run:
        print(f"  DRY RUN COMPLETE — No data saved")
    else:
        print(f"  Done! {total_records} total records saved to {DATA_DIR}/")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    asyncio.run(main())
