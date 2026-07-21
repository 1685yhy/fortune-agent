#!/usr/bin/env python3
"""Systematically scrape 问真八字 detailed API for bazi reference data.

Discovers and saves complete bazi charts including:
四柱, 十神, 藏干, 藏干十神, 纳音, 空亡, 星运, 自坐, 大运, 流年

Output: JSONL file with one entry per birth date, ready for verification
and knowledge base population.
"""
import urllib.request
import json
import time
import sys
from pathlib import Path
from datetime import datetime

API = "https://bzapi3.iwzbz.com/getbasebz8.php"

OUTPUT_DIR = Path("/mnt/d/fortune-data/books/zonghe/wenzhen")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_chart(y, m, d, h, mi, gender_code):
    """Fetch complete bazi chart from 问真八字 API."""
    d_str = f'{y:04d}-{m:02d}-{d:02d}%20{h:02d}:{mi:02d}'
    today = datetime.now().strftime('%Y-%m-%d%%20%H:%M:%S')
    url = f'{API}?d={d_str}&s={gender_code}&today={today}&vip=0&yzs=0&pqf=0'

    try:
        resp = urllib.request.urlopen(url, timeout=15)
        data = json.loads(resp.read())
        if 'bz' not in data or not data['bz']:
            return None

        # Normalize: extract the key fields
        bz = data['bz']
        pillars = [bz[str(i)] + bz[str(i+1)] for i in range(0, 8, 2)]

        return {
            "date": f"{y:04d}-{m:02d}-{d:02d}",
            "time": f"{h:02d}:{mi:02d}",
            "gender_code": gender_code,
            "gender": "女" if gender_code == 0 else "男",
            "pillars": " ".join(pillars),
            "ss": data.get("ss", []),
            "cg": data.get("cg", []),
            "cgss": data.get("cgss", []),
            "ny": data.get("ny", []),
            "kw": data.get("kw", []),
            "xy": data.get("xy", []),
            "zz": data.get("zz", []),
            "dayun": data.get("dayun", []),
            "lunar": bz.get("8", ""),
            "raw": data,  # Keep full response
            "source": "问真八字API",
            "scraped_at": datetime.now().isoformat(),
        }
    except Exception as e:
        return None


def generate_cases():
    """Generate diverse birth date combinations."""
    cases = []

    # 1. Every day of every 5th year (1900-2025)
    for y in range(1900, 2026, 5):
        for m in range(1, 13):
            for d in [1, 15, 28]:
                for h in [0, 8, 16]:
                    for s in [0, 1]:
                        cases.append((y, m, min(d, 28), h, 0, s))

    # 2. All days of representative years
    for y in [1950, 1975, 2000, 2024]:
        for m in range(1, 13):
            for d in [1, 5, 10, 15, 20, 25, 28]:
                for h in [0, 6, 12, 18]:
                    for s in [0, 1]:
                        cases.append((y, m, d, h, 0, s))

    # 3. Every zodiac year start (立春附近)
    for y in range(1924, 2025):
        for s in [0, 1]:
            cases.append((y, 2, 4, 0, 0, s))
            cases.append((y, 2, 4, 12, 0, s))
            cases.append((y, 2, 5, 0, 0, s))

    # 4. Edge cases
    for y in range(1900, 2025, 10):
        for s in [0, 1]:
            cases.append((y, 12, 31, 23, 0, s))  # New Year's Eve
            cases.append((y, 1, 1, 0, 0, s))      # New Year's Day

    # Deduplicate
    return list(set(cases))


def scrape(output_path: str = None, delay: float = 0.2,
           resume_from: int = 0):
    """Main scraping function."""
    if output_path is None:
        output_path = str(OUTPUT_DIR / f"wenzhen_charts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")

    cases = generate_cases()
    cases.sort()  # Deterministic order
    total = len(cases)
    print(f"待爬取: {total} 个出生日期")
    print(f"输出: {output_path}")

    # Resume support
    existing = set()
    if Path(output_path).exists():
        with open(output_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    key = (entry["date"], entry["time"], entry["gender_code"])
                    existing.add(key)
                except Exception:
                    pass
        print(f"已存在 {len(existing)} 条，跳过")

    success = 0
    failed = 0
    start_time = time.time()

    with open(output_path, "a") as f:
        for i, (y, m, d, h, mi, s) in enumerate(cases):
            if i < resume_from:
                continue

            key = (f"{y:04d}-{m:02d}-{d:02d}", f"{h:02d}:{mi:02d}", s)
            if key in existing:
                continue

            result = fetch_chart(y, m, d, h, mi, s)
            if result:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
                f.flush()
                success += 1
            else:
                failed += 1

            if (i + 1) % 100 == 0:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed
                eta = (total - i - 1) / rate / 60 if rate > 0 else 0
                print(f"  进度 {i+1}/{total}: {success} 成功 {failed} 失败 "
                      f"({rate:.1f}/s, ETA {eta:.0f}min)")

            time.sleep(delay)

    elapsed = time.time() - start_time
    print(f"\n完成! {success} 成功, {failed} 失败, {elapsed:.0f}s")
    print(f"数据保存在: {output_path}")

    # Save summary
    summary_path = output_path.replace(".jsonl", "_summary.json")
    with open(summary_path, "w") as f:
        json.dump({
            "total_cases": total,
            "success": success,
            "failed": failed,
            "elapsed_seconds": elapsed,
            "output_path": output_path,
            "scraped_at": datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)
    print(f"摘要保存在: {summary_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="Output JSONL path")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Delay between requests (seconds)")
    parser.add_argument("--resume", type=int, default=0,
                        help="Resume from case index")
    args = parser.parse_args()

    scrape(args.output, args.delay, args.resume)
