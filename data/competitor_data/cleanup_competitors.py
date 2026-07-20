#!/usr/bin/env python3
"""
Competitor Data Cleanup Script
- Reads all JSONL files from competitor_data/
- Deduplicates by URL, preferring cleaner/more authoritative sources
- Classifies records into domains (bazi, ziwei, fengshui, dream, etc.)
- Scores quality 1-5
- Identifies benchmark candidates
- Generates report and README
"""

import json
import glob
import os
import re
import sys
import hashlib
from collections import defaultdict, Counter
from datetime import datetime

DATA_DIR = "/home/a/fortune-agent/data/competitor_data"
CLEAN_OUTPUT = os.path.join(DATA_DIR, "competitors_clean.jsonl")
BENCHMARK_OUTPUT = os.path.join(DATA_DIR, "benchmark_candidates.jsonl")
REPORT_OUTPUT = "/tmp/competitor_cleanup_report.md"
README_OUTPUT = os.path.join(DATA_DIR, "README.md")

# ── Platform quality ranking (lower index = higher priority when deduplicating) ──
PLATFORM_PRIORITY = [
    "zgjm",       # authoritative dream interpretation
    "daosuan",    # comprehensive fortune-telling
    "zhouyihui",  # professional divination
    "smxs",       # popular platform
    "buyiju",     # comprehensive
    "12880",      # dream interpretation
    "64gua",      # I-Ching
    "aqioo",      # fortune telling
    "k366",       # multi-purpose
    "dajiazhao",  # comprehensive
    "shen88",     # zodiac/stars
    "zhouyisuanming", # fortune telling
]

# ── Domain classification keywords (Chinese) ──
DOMAIN_KEYWORDS = {
    "bazi": [
        "八字", "生辰八字", "命理", "八字命理", "八字算命", "生辰", "日柱", "月柱",
        "年柱", "时柱", "四柱", "干支", "天干", "地支", "五行", "金木水火土",
        "命盘", "十神", "食神", "伤官", "正财", "偏财", "正官", "七杀", "偏印",
        "正印", "比肩", "劫财", "大运", "流年", "起运", "交运", "排盘",
        "日干", "身强", "身弱", "喜用神", "忌神", "旺衰", "格局", "从格",
        "化气", "纳音", "藏干", "地支藏干", "地支暗藏",
    ],
    "ziwei": [
        "紫微", "紫微斗数", "命宫", "身宫", "十二宫", "兄弟宫", "夫妻宫", "子女宫",
        "财帛宫", "疾厄宫", "迁移宫", "交友宫", "官禄宫", "田宅宫", "福德宫",
        "父母宫", "紫微星", "天机星", "太阳星", "武曲星", "天同星", "廉贞星",
        "天府星", "太阴星", "贪狼星", "巨门星", "天相星", "天梁星", "七杀星",
        "破军星", "左辅", "右弼", "文昌", "文曲", "天魁", "天钺", "禄存",
        "擎羊", "陀罗", "火星", "铃星", "地空", "地劫",
        "紫微命盘", "三方四正", "四化", "化禄", "化权", "化科", "化忌",
        "命主", "身主",
    ],
    "fengshui": [
        "风水", "阳宅", "阴宅", "住宅风水", "办公室风水", "店铺风水", "财位",
        "煞气", "冲煞", "化煞", "旺财", "招财", "聚财", "破财", "财运",
        "桃花位", "文昌位", "官位", "凶位", "吉位", "朝向", "坐向", "门向",
        "床向", "灶位", "水口", "明堂", "青龙", "白虎", "朱雀", "玄武",
        "峦头", "理气", "罗盘", "玄空", "八宅", "九宫飞星", "飞星",
        "五黄", "二黑", "三煞", "太岁", "五帝钱", "八卦镜", "风水摆件",
        "风水布局", "风水调整", "风水化解",
    ],
    "dream": [
        "周公解梦", "梦见", "做梦", "梦到", "梦境", "梦乡", "解梦", "做梦梦见",
        "梦中的", "梦兆", "吉梦", "凶梦", "梦寓意", "梦象征",
        "睡眠做梦", "夜里做梦",
    ],
    "mianxiang": [
        "面相", "手相", "面相关", "手相关", "五官", "天庭", "地阁", "印堂",
        "鼻相", "眼相", "眉相", "口相", "耳相", "掌纹", "生命线", "智慧线",
        "感情线", "事业线", "婚姻线", "掌色", "手型", "面相分析",
        "看相", "相面", "相手",
    ],
    "qimen": [
        "奇门", "奇门遁甲", "遁甲", "八门", "休门", "生门", "伤门", "杜门",
        "景门", "死门", "惊门", "开门", "九星", "天蓬", "天芮", "天冲",
        "天辅", "天禽", "天心", "天柱", "天任", "天英", "八神", "值符",
        "腾蛇", "太阴", "六合", "白虎", "玄武", "九地", "九天",
        "奇门排盘", "奇门预测", "奇门布局", "奇门风水",
    ],
    "xingming": [
        "姓名", "姓名学", "起名", "取名", "命名", "姓名测试", "姓名打分",
        "姓名吉凶", "姓名笔画", "五格", "三才", "天格", "人格", "地格",
        "外格", "总格", "姓名分析", "姓名配对",
        "宝宝起名", "男孩起名", "女孩起名", "改名",
    ],
    "zeri": [
        "择日", "择吉", "黄道吉日", "良辰吉日", "吉日", "吉时", "择日子",
        "嫁娶择日", "搬家择日", "开业择日", "动土择日", "入宅择日",
        "出行择日", "结婚吉日", "开工吉日", "黄历", "老黄历", "通胜",
        "宜忌", "冲煞", "择吉日", "选日子",
    ],
}

# Additional terms that suggest "general" fortune-telling
GENERAL_KEYWORDS = [
    "算命", "占卜", "预测", "卜卦", "六爻", "卦象", "起卦", "解卦",
    "运势", "运程", "财运", "事业", "婚姻", "感情", "健康",
    "抽签", "求签", "灵签", "签文", "月老", "姻缘",
    "星座", "生肖", "属相", "血型", "塔罗", "占星",
    "命理测算", "在线算命", "免费算命", "算命先生",
    "本命年", "犯太岁", "冲太岁", "害太岁",
]

# ── Platforms considered authoritative for benchmark candidates ──
AUTHORITATIVE_PLATFORMS = {"zgjm", "daosuan", "zhouyihui", "smxs", "buyiju"}


# Characters that commonly appear in UTF-8 mojibake but rarely in normal Chinese text.
# These are CJK characters that result from interpreting UTF-8 byte sequences as
# individual Latin-1 characters and then re-encoding as UTF-8.
MOJIBAKE_CHARS = set(
    "涓轰綍瑕佸叓瀛楃畻鍛界殑浜哄憿鍙岀敇鏍忚瘎璁哄巺鍦扮"
    "鐗堣繘琛岃鏄庢槸涓汉鎸囧畾鎻愪緵鏈嫻庢爣鍑嗗垎"
    "绡囩洰鍚勭鍒嗙被鍚堜綔鏂囩珷鍥绾哥綉绔欐墍鏈夋枃"
    "绔犺祫璁墦鍗版妸鎻℃洿鏂板啓浣滅钀界敓鐗╃殑鏃跺埢"
    "鐨勪笢瑗挎湇鍔＄郸鎺у埗璇佷功鎻愪緵鎴愮哗鑱屼綅涓撲骸"
    "鍒哄畠浠繖鏍蜂笉鏂獟浠嬫湅鍙嬪彲鑳藉唴瀹硅瑙佹柊"
    "闂荤湡鏄汉绫诲疂璐濆叾瀹冩寚鏁板嚭鐜板厜绌夸簯闃充究鑸"
    "椋庣殑涓婅但鏄ョ涓嬪浠婃瘯绔熷畠浠鏄鏃舵湁涓滆タ"
    "鏃堕棿璁颁綇閭ｄ簺鍚庢潵濡傛灉鐪嬪埌鏄庣櫧杩涜岀敓鎰忎竴"
    "鏃堕棿涓浗鐢靛奖瑕佹眰绛斿瑷璁鸿儗鏅富瑙掗鑻辩鐢佃"
    "鐞嗚璁烘敹闆嗛洦姘栫殑鍘熸潵鐨勪笢瑗块潪甯镐細瀹屾垚鍛斤紒"
    "鎵浠ラ棶棰樷滅洿鍒伴噷涓婃捣涓轰簡鏄ュぉ鎴戜滑閫氳繃寰堥暱鏃"
    "堕棿鎵嶈兘娲讳竴涓猏瀛楁瘝鍗佸垎蹇咃紝浠栭槦鍚勪釜鐪佷唤鏁板"
    "瓧鎴栬姘达綔",  # common mojibake chars from UTF-8 misreads
)


def detect_garbled(text):
    """
    Detect garbled/mojibake encoding.
    Returns True if the text appears to be garbled.
    """
    if not text:
        return True

    total_chars = len(text)
    if total_chars < 5:
        return False  # too short to judge

    # Check for replacement character
    if '�' in text:
        return True

    # Check for the specific mojibake characters (fast check)
    mojibake_char_count = 0
    for c in text:
        if c in MOJIBAKE_CHARS:
            mojibake_char_count += 1

    # If more than 10% of chars are mojibake indicators
    if mojibake_char_count > max(3, total_chars * 0.10):
        return True

    # Check for unnatural character range: mojibake text tends to have
    # high proportion of chars from U+6Dxx-U+8Fxx range
    suspicious_count = 0
    for c in text:
        cp = ord(c)
        # These ranges are over-represented in UTF-8 mojibake
        if 0x6d00 <= cp <= 0x70ff or 0x8e00 <= cp <= 0x9100:
            suspicious_count += 1
        elif 0x7b00 <= cp <= 0x8000:
            suspicious_count += 1
        elif 0xfe00 <= cp <= 0xff00:
            suspicious_count += 1
        elif cp == 0xfffd:
            suspicious_count += 5

    threshold = max(5, total_chars * 0.08)
    return suspicious_count > threshold


def classify_domain(record):
    """
    Auto-classify a record into one of the domains.
    Returns the domain name and confidence score.
    """
    text = (record.get("query", "") + " " + record.get("response", "")).lower()
    domain_scores = {}

    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if kw.lower() in text:
                score += 1
        if score > 0:
            domain_scores[domain] = score

    # Check general keywords
    general_score = 0
    for kw in GENERAL_KEYWORDS:
        if kw.lower() in text:
            general_score += 1

    if domain_scores:
        # Return highest-scoring domain
        best = max(domain_scores, key=domain_scores.get)
        return best, domain_scores[best]
    elif general_score > 0:
        return "general", general_score
    else:
        return "general", 0


def extract_source_file_priority(filename):
    """
    Extract priority from filename for deduplication ordering.
    Lower number = higher priority (preferred).
    """
    basename = os.path.basename(filename)
    if "_max_" in basename:
        return 6  # Largest files, lowest priority
    elif "_deep_" in basename:
        return 1  # Deep-scraped, high quality
    elif "_full_" in basename:
        return 2
    elif "_all_" in basename:
        return 3
    else:
        return 4


def quality_score(record):
    """
    Score record quality 1-5.
    - Content length (>200 chars = +1, >500 = +2)
    - Structure (has analysis/reasoning = +1)
    - Classical references = +1
    - Clean text (no garbled encoding) = +1
    """
    response = record.get("response", "")
    query = record.get("query", "")
    response_len = len(response) if response else 0
    query_len = len(query) if query else 0
    total_len = response_len + query_len

    score = 1  # base

    # Content length
    if total_len > 500:
        score += 2
    elif total_len > 200:
        score += 1

    # Structure: presence of analysis/reasoning keywords
    analysis_keywords = [
        "分析", "解析", "解读", "详解", "说明", "解释", "含义", "寓意",
        "代表", "象征", "表示", "说明", "特点", "特征", "性格",
        "运势", "发展", "建议", "提醒", "注意", "化解",
        "原因", "结果", "影响", "作用",
    ]
    if any(kw in response for kw in analysis_keywords):
        score += 1

    # Classical references
    classical_keywords = [
        "周易", "易经", "论语", "道德经", "诗经", "皇帝内经", "山海经",
        "周公", "孔子", "老子", "庄子", "孟子", "诸葛亮", "刘伯温",
        "邵雍", "徐子平", "李虚中", "鬼谷子", "袁天罡", "李淳风",
        "紫微斗数全书", "渊海子平", "三命通会", "穷通宝鉴", "滴天髓",
        "子平真诠", "千里命稿", "神峰通考", "梅花易数",
        "古籍", "经典", "记载",
    ]
    if any(kw in response for kw in classical_keywords):
        score += 1

    # Clean text - detect garbled
    if not detect_garbled(response) and not detect_garbled(query):
        score += 1

    # Clamp
    return max(1, min(5, score))


def main():
    print("=" * 60)
    print("Competitor Data Cleanup Pipeline")
    print("=" * 60)

    # ── Step 0: Find all JSONL files ──
    jsonl_files = glob.glob(os.path.join(DATA_DIR, "*.jsonl"))
    # Exclude output files if they already exist
    jsonl_files = [f for f in jsonl_files if "competitors_clean" not in f and "benchmark_candidates" not in f]
    jsonl_files.sort()

    print(f"\nFound {len(jsonl_files)} JSONL files to process")

    # ── Step 1: Read all records ──
    all_records = []
    file_stats = defaultdict(lambda: {"platform": "unknown", "count": 0, "files": set()})
    raw_total = 0

    for fpath in jsonl_files:
        fname = os.path.basename(fpath)
        fsize = os.path.getsize(fpath)
        if fsize == 0:
            print(f"  SKIP (empty): {fname}")
            continue

        platform_name = fname.split("_")[0]
        file_count = 0

        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    # Normalize platform field
                    if "platform" not in record or not record["platform"]:
                        record["platform"] = platform_name
                    # Add source tracking
                    record["_source_file"] = fname
                    record["_source_priority"] = extract_source_file_priority(fname)
                    all_records.append(record)
                    file_count += 1
                    raw_total += 1
                except json.JSONDecodeError:
                    print(f"  SKIP (bad json): {fname} line {file_count + 1}")
                    continue

        print(f"  READ {file_count:>6}  {fname}")
        file_stats[platform_name]["count"] += file_count
        file_stats[platform_name]["platform"] = platform_name
        file_stats[platform_name]["files"].add(fname)

    # Count raw records by their actual platform field (not filename-derived)
    from collections import Counter as counter
    raw_platform_counts = counter(r.get("platform", "unknown") for r in all_records)

    print(f"\nTotal raw records read: {raw_total:,}")
    print(f"Unique platforms: {len(file_stats)}")
    print(f"Platforms: {', '.join(sorted(file_stats.keys()))}")

    # ── Step 2: Deduplicate by URL ──
    print("\n--- Step 2: Deduplicating by URL ---")

    url_groups = defaultdict(list)
    for record in all_records:
        url = record.get("url", "").strip()
        if not url:
            # Generate a hash-based key for records without URLs
            content = (record.get("query", "") + record.get("response", "")).strip()
            if content:
                url = "no-url:" + hashlib.md5(content.encode("utf-8")).hexdigest()
            else:
                url = f"no-url:{len(url_groups)}"
        url_groups[url].append(record)

    unique_records = []
    duplicates_found = 0
    platform_duplicates = defaultdict(int)

    for url, records in url_groups.items():
        if len(records) == 1:
            unique_records.append(records[0])
        else:
            duplicates_found += len(records) - 1
            # Sort by: priority (lower=better), then by response length (longer=better)
            # Prefer non-garbled records
            def sort_key(r):
                is_garbled = detect_garbled(r.get("response", ""))
                response_len = len(r.get("response", ""))
                priority = r.get("_source_priority", 5)
                return (1 if is_garbled else 0, priority, -response_len)

            records.sort(key=sort_key)
            best = records[0]
            unique_records.append(best)

            # Track which platforms had duplicates
            platforms_in_group = set(r.get("platform", "unknown") for r in records)
            for p in platforms_in_group:
                platform_duplicates[p] += len([r for r in records if r.get("platform") == p]) - 1

    print(f"Total URLs (unique keys): {len(url_groups):,}")
    print(f"Unique records after dedup: {len(unique_records):,}")
    print(f"Duplicates removed: {duplicates_found:,}")

    # Per-platform dedup report
    print("\nRecords per platform (after dedup):")
    platform_counts = Counter(r.get("platform", "unknown") for r in unique_records)
    for platform in sorted(platform_counts.keys(), key=lambda p: -platform_counts[p]):
        raw_count = raw_platform_counts.get(platform, 0)
        clean_count = platform_counts[platform]
        deduped = raw_count - clean_count
        if raw_count > 0:
            pct = f"{deduped/raw_count*100:.1f}%"
        else:
            pct = "0.0%"
        print(f"  {platform:>15}: {raw_count:>6} raw -> {clean_count:>6} clean ({deduped:>6} removed, {pct})")

    # ── Step 3: Classify by domain ──
    print("\n--- Step 3: Classifying by domain ---")

    for record in unique_records:
        domain, confidence = classify_domain(record)
        record["domain"] = domain
        record["domain_confidence"] = confidence

    domain_counts = Counter(r["domain"] for r in unique_records)
    print("\nDomain distribution:")
    for domain in sorted(domain_counts.keys(), key=lambda d: -domain_counts[d]):
        print(f"  {domain:>15}: {domain_counts[domain]:>6} records")

    # ── Step 4: Quality scoring ──
    print("\n--- Step 4: Quality scoring ---")

    for record in unique_records:
        record["quality_score"] = quality_score(record)

    quality_distribution = Counter(r["quality_score"] for r in unique_records)
    print(f"\nQuality score distribution (1-5):")
    for score in sorted(quality_distribution.keys()):
        count = quality_distribution[score]
        bar = "#" * (count // max(1, max(quality_distribution.values()) // 50))
        print(f"  Score {score}: {count:>6} records  {bar}")

    low_quality = [r for r in unique_records if r["quality_score"] <= 2]
    print(f"\nLow-quality records (score 1-2): {len(low_quality):,} ({len(low_quality)/len(unique_records)*100:.1f}%)")

    # ── Step 5: Save clean merged file ──
    print(f"\n--- Step 5: Saving clean merged file ---")

    # Remove internal fields before saving
    clean_records = []
    for r in unique_records:
        clean = {k: v for k, v in r.items() if not k.startswith("_")}
        clean_records.append(clean)

    with open(CLEAN_OUTPUT, "w", encoding="utf-8") as f:
        for record in clean_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Saved {len(clean_records):,} records to {CLEAN_OUTPUT}")

    # ── Step 6: Identify benchmark candidates ──
    print(f"\n--- Step 6: Identifying benchmark candidates ---")

    benchmark_candidates = []
    # Target ~500 benchmark candidates
    # Build a candidate pool from all clean, non-garbled, substantial records
    candidate_pool = [
        r for r in clean_records
        if r.get("quality_score", 0) >= 3
        and len(r.get("response", "")) > 100
        and not detect_garbled(r.get("response", ""))
    ]

    # Determine how many to take per domain
    # Larger domains get more, but all domains get at least some representation
    domain_available = Counter(r["domain"] for r in candidate_pool)
    print(f"\n  Candidate pool size: {len(candidate_pool)}")
    print(f"  Available per domain: {dict(domain_available)}")

    # Target distribution across domains (aim for 500 total)
    benchmark_candidates = []

    for domain in sorted(domain_counts.keys()):
        avail = domain_available.get(domain, 0)
        if avail == 0:
            continue

        # Take up to 80 per major domain, all available for minor domains
        if domain in ("dream", "fengshui", "general"):
            take = min(avail, 80)
        elif domain in ("bazi", "xingming", "zeri"):
            take = min(avail, 60)
        else:
            take = min(avail, 30)  # ziwei, qimen - take all available

        # Prefer higher quality and longer responses
        domain_candidates = [r for r in candidate_pool if r["domain"] == domain]
        domain_candidates.sort(key=lambda r: (r.get("quality_score", 0), len(r.get("response", ""))), reverse=True)

        # Prefer authoritative platforms within the top N
        auth = [r for r in domain_candidates if r.get("platform", "") in AUTHORITATIVE_PLATFORMS]
        other = [r for r in domain_candidates if r.get("platform", "") not in AUTHORITATIVE_PLATFORMS]

        selected = auth[:take]
        if len(selected) < take:
            selected.extend(other[:take - len(selected)])

        benchmark_candidates.extend(selected)

    # If we have fewer than 450, supplement with additional records
    if len(benchmark_candidates) < 450:
        print(f"    Supplementing (have {len(benchmark_candidates)}, targeting >= 450)...")
        existing_urls = set(r["url"] for r in benchmark_candidates)
        supplement = [
            r for r in clean_records
            if r["url"] not in existing_urls
            and len(r.get("response", "")) > 50
            and not detect_garbled(r.get("response", ""))
            and r.get("quality_score", 0) >= 3
        ]
        supplement.sort(key=lambda r: (r.get("quality_score", 0), len(r.get("response", ""))), reverse=True)

        needed_per_domain = {}
        for domain in sorted(domain_counts.keys()):
            cap = 80 if domain in ("dream", "fengshui", "general", "bazi", "xingming", "zeri") else 30
            have = sum(1 for r in benchmark_candidates if r["domain"] == domain)
            needed_per_domain[domain] = max(0, cap - have)

        for domain, needed in sorted(needed_per_domain.items()):
            if needed <= 0:
                continue
            extras = [r for r in supplement if r["domain"] == domain]
            benchmark_candidates.extend(extras[:needed])

    # Limit to 500
    benchmark_candidates = benchmark_candidates[:500]

    # Save benchmark candidates
    with open(BENCHMARK_OUTPUT, "w", encoding="utf-8") as f:
        for record in benchmark_candidates:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Saved {len(benchmark_candidates):,} benchmark candidates to {BENCHMARK_OUTPUT}")
    benchmark_domain_counts = Counter(r["domain"] for r in benchmark_candidates)
    print("\nBenchmark candidates per domain:")
    for domain in sorted(benchmark_domain_counts.keys(), key=lambda d: -benchmark_domain_counts[d]):
        print(f"  {domain:>15}: {benchmark_domain_counts[domain]:>5}")

    # ── Step 7: Generate report ──
    print(f"\n--- Step 7: Generating report ---")

    report_lines = [
        "# Competitor Data Cleanup Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- **Total raw records**: {raw_total:,}",
        f"- **Unique records (after dedup)**: {len(unique_records):,}",
        f"- **Duplicates removed**: {duplicates_found:,} ({duplicates_found/raw_total*100:.1f}%)",
        f"- **Source files**: {len(jsonl_files)}",
        f"- **Platforms**: {len(file_stats)}",
        "",
        "## Platforms",
        "",
        "| Platform | Raw Records | Clean Records | Duplicates Removed | Dedup % |",
        "|----------|------------:|--------------:|-------------------:|--------:|",
    ]

    for platform in sorted(platform_counts.keys(), key=lambda p: -platform_counts[p]):
        raw_count = raw_platform_counts.get(platform, 0)
        clean_count = platform_counts[platform]
        deduped = raw_count - clean_count
        pct = f"{deduped/raw_count*100:.1f}%" if raw_count > 0 else "0.0%"
        report_lines.append(f"| {platform:>12} | {raw_count:>10} | {clean_count:>10} | {deduped:>15} | {pct:>7} |")

    report_lines += [
        "",
        "## Domain Distribution",
        "",
        "| Domain | Count | Percentage |",
        "|--------|------:|----------:|",
    ]

    for domain in sorted(domain_counts.keys(), key=lambda d: -domain_counts[d]):
        pct = f"{domain_counts[domain]/len(unique_records)*100:.1f}%"
        report_lines.append(f"| {domain:>15} | {domain_counts[domain]:>5} | {pct:>8} |")

    report_lines += [
        "",
        "## Quality Distribution",
        "",
        "| Score | Count | Percentage |",
        "|-------|------:|----------:|",
    ]

    for score in sorted(quality_distribution.keys()):
        pct = f"{quality_distribution[score]/len(unique_records)*100:.1f}%"
        report_lines.append(f"| {score:>5} | {quality_distribution[score]:>5} | {pct:>8} |")

    report_lines += [
        "",
        f"**Low-quality records (score 1-2)**: {len(low_quality):,} ({len(low_quality)/len(unique_records)*100:.1f}%)",
        "",
        "## Benchmark Candidates",
        "",
        f"- **Total candidates**: {len(benchmark_candidates):,}",
        "",
        "| Domain | Count |",
        "|--------|------:|",
    ]

    for domain in sorted(benchmark_domain_counts.keys(), key=lambda d: -benchmark_domain_counts[d]):
        report_lines.append(f"| {domain:>15} | {benchmark_domain_counts[domain]:>5} |")

    report_lines += [
        "",
        "## Data Quality Notes",
        "",
        "- **Mojibake**: Records from `daosuan_max_20260719.jsonl` (37,657 records) have garbled encoding. When cleaner versions exist for the same URL, they were preferred.",
        "- **Empty fields**: Some records have empty query or response fields; these are retained but likely score low on quality.",
        "- **URL normalization**: Deduplication is based on exact URL match. Some platforms may have semantically identical pages with different URLs (counted as separate records).",
        "- **Domain classification**: Based on keyword matching; some records may be misclassified or belong to multiple domains.",
        "",
        "## Files",
        "",
        f"- **Clean data**: `{CLEAN_OUTPUT}`",
        f"- **Benchmark candidates**: `{BENCHMARK_OUTPUT}`",
        f"- **This report**: `{REPORT_OUTPUT}`",
        f"- **README**: `{README_OUTPUT}`",
    ]

    report = "\n".join(report_lines)
    with open(REPORT_OUTPUT, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Report saved to {REPORT_OUTPUT}")

    # ── Step 8: Create README ──
    print(f"\n--- Step 8: Creating README ---")

    readme_lines = [
        "# Competitor Data",
        "",
        "## Overview",
        "",
        f"This directory contains cleaned and deduplicated competitor data from {len(file_stats)} fortune-telling platforms.",
        "The data was collected via web scraping and processed through a deduplication, classification, and quality-scoring pipeline.",
        "",
        "## Data Files",
        "",
        f"| File | Description | Records |",
        "|------|-------------|--------:|",
        f"| `competitors_clean.jsonl` | Main clean dataset (deduplicated) | {len(unique_records):,} |",
        f"| `benchmark_candidates.jsonl` | High-quality reference records for benchmarking | {len(benchmark_candidates):,} |",
        f"| `README.md` | This file | - |",
        "",
        "## Schema",
        "",
        "Each record in `competitors_clean.jsonl` has the following fields:",
        "",
        "| Field | Type | Description |",
        "|-------|------|-------------|",
        "| `query` | string | The user's query/fortune-telling question |",
        "| `response` | string | The platform's response/prediction/analysis |",
        "| `platform` | string | Source platform name |",
        "| `url` | string | Original URL |",
        "| `scraped_at` | string | ISO timestamp of scraping |",
        "| `domain` | string | Classified domain (bazi, ziwei, fengshui, dream, mianxiang, qimen, xingming, zeri, general) |",
        "| `domain_confidence` | int | Number of matching keywords |",
        "| `quality_score` | int | Quality score 1-5 |",
        "",
        "## Platforms",
        "",
        "| Platform | Description |",
        "|----------|-------------|",
        "| zgjm | 周公解梦 (Zhou Gong Dream Interpretation) |",
        "| daosuan | 道算网 (Dao Suan) |",
        "| zhouyihui | 周易汇 (Zhou Yi Hui) |",
        "| smxs | 算命先生 (Suàn Mìng Xiān Shēng) |",
        "| buyiju | 卜易居 (Bu Yi Ju) |",
        "| 12880 | 12880.com (Dream Interpretation) |",
        "| 64gua | 64卦网 (64 Hexagrams) |",
        "| aqioo | Aqioo (Fortune Telling) |",
        "| k366 | K366 (Multi-purpose) |",
        "| dajiazhao | 大家找 (Da Jia Zhao) |",
        "| shen88 | 神88网 (Shen 88) |",
        "| zhouyisuanming | 周易算命 (Zhou Yi Fortune) |",
        "",
        "## Domains",
        "",
    ]

    for domain in sorted(domain_counts.keys(), key=lambda d: -domain_counts[d]):
        pct = f"{domain_counts[domain]/len(unique_records)*100:.1f}%"
        readme_lines.append(f"- **{domain}**: {domain_counts[domain]:,} records ({pct})")

    readme_lines += [
        "",
        "## Quality Distribution",
        "",
    ]

    for score in sorted(quality_distribution.keys()):
        pct = f"{quality_distribution[score]/len(unique_records)*100:.1f}%"
        readme_lines.append(f"- **Score {score}**: {quality_distribution[score]:,} records ({pct})")

    readme_lines += [
        "",
        "## Usage",
        "",
        "### Loading the data in Python",
        "",
        "```python",
        "import json",
        "",
        "records = []",
        "with open('competitors_clean.jsonl', encoding='utf-8') as f:",
        "    for line in f:",
        "        if line.strip():",
        "            records.append(json.loads(line))",
        "",
        f"# {len(unique_records):,} records loaded",
        "```",
        "",
        "### Filtering by domain",
        "",
        "```python",
        "bazi_records = [r for r in records if r['domain'] == 'bazi']",
        "```",
        "",
        "### Filtering by quality",
        "",
        "```python",
        "high_quality = [r for r in records if r['quality_score'] >= 4]",
        "```",
        "",
        "### Benchmark candidates",
        "",
        "```python",
        "benchmarks = []",
        "with open('benchmark_candidates.jsonl', encoding='utf-8') as f:",
        "    for line in f:",
        "        if line.strip():",
        "            benchmarks.append(json.loads(line))",
        "```",
        "",
        "## Processing Pipeline",
        "",
        "1. **Read**: Load all records from ~30 JSONL source files (12 platforms)",
        "2. **Deduplicate**: Group by URL, keep highest-quality version per group",
        "3. **Classify**: Assign domain labels via keyword matching on query + response text",
        "4. **Score**: Quality scoring (1-5) based on content length, structure, classical references, and encoding quality",
        "5. **Select**: Identify benchmark candidates (high-quality, authoritative platforms, well-distributed across domains)",
        "",
        "## Notes",
        "",
        f"- The original source data contains {raw_total:,} records across {len(jsonl_files)} files.",
        f"- After URL-based deduplication, {len(unique_records):,} unique records remain.",
        f"- The `daosuan_max` source file (37,657 records) has garbled encoding issues; cleaner versions were preferred during dedup where available.",
        "- Domain classification uses keyword matching and may have some misclassifications.",
    ]

    readme = "\n".join(readme_lines)
    with open(README_OUTPUT, "w", encoding="utf-8") as f:
        f.write(readme)

    print(f"README saved to {README_OUTPUT}")

    # ── Final Summary ──
    print("\n" + "=" * 60)
    print("CLEANUP COMPLETE")
    print("=" * 60)
    print(f"  Raw records read:    {raw_total:>8,}")
    print(f"  Unique records:      {len(unique_records):>8,}")
    print(f"  Duplicates removed:  {duplicates_found:>8,}")
    print(f"  Domains:             {len(domain_counts)}")
    print(f"  Benchmark cand.:     {len(benchmark_candidates):>8,}")
    print(f"  Low quality (1-2):   {len(low_quality):>8,}")
    print()
    print(f"  Output files:")
    print(f"    {CLEAN_OUTPUT}")
    print(f"    {BENCHMARK_OUTPUT}")
    print(f"    {REPORT_OUTPUT}")
    print(f"    {README_OUTPUT}")


if __name__ == "__main__":
    main()
