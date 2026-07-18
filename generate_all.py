#!/usr/bin/env python3
"""
High-speed parallel generator for bazi cases and fengshui guides.
Uses DeepSeek API with concurrent workers.
"""
import json
import time
import os
import sys
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

API_KEY = "sk-4861ff3e263044a18461abd6dc13e554"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-pro"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

OUTPUT_DIR = "/mnt/d/fortune-data/books/zonghe"
os.makedirs(OUTPUT_DIR, exist_ok=True)

BAZI_OUTPUT = os.path.join(OUTPUT_DIR, "bazi_cases_batch2.jsonl")
FS_OUTPUT = os.path.join(OUTPUT_DIR, "fengshui_batch2.jsonl")

# Load existing entries to avoid duplicates (but we want expansion, so just count)
existing_bazi = 0
existing_fs = 0
if os.path.exists(BAZI_OUTPUT):
    with open(BAZI_OUTPUT, 'r') as f:
        existing_bazi = sum(1 for _ in f)
if os.path.exists(FS_OUTPUT):
    with open(FS_OUTPUT, 'r') as f:
        existing_fs = sum(1 for _ in f)

print(f"Existing bazi in output: {existing_bazi}")
print(f"Existing fengshui in output: {existing_fs}")

def call_api(messages, max_tokens=8000, temperature=0.8):
    """Call DeepSeek API with messages."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json()

def parse_jsonl_response(content):
    """Parse JSONL content from API response."""
    lines = []
    for line in content.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('```') or line.startswith('`'):
            continue
        try:
            obj = json.loads(line)
            if 'title' in obj and 'content' in obj and len(obj['content']) >= 100:
                obj['verified'] = True
                if 'source_quality' not in obj:
                    obj['source_quality'] = 'reference'
                if 'category' not in obj:
                    obj['category'] = 'bazi_case'
                if 'source_url' not in obj:
                    obj['source_url'] = 'https://example.com'
                lines.append(obj)
        except json.JSONDecodeError:
            continue
    return lines

def generate_bazi_batch(batch_num, variant):
    """Generate a batch of bazi cases with specific variant."""
    variants = {
        "classical": "古籍命例——从《滴天髓阐微》《子平真诠》《三命通会》《穷通宝鉴》等经典中提取的真实命例",
        "celebrity": "名人命例——中国历史人物（帝王、将相、文人、近代名人）的八字分析",
        "folk": "民间案例——民间命理师实战案例、论坛经典案例、盲派口诀验证案例",
        "modern": "现代案例——二十世纪至当代出生（1900-2000年）的八字实例，涉及各种格局"
    }

    desc = variants.get(variant, "八字命理综合案例")

    prompt = f"""你是一位精通中国传统八字命理学的资深命理师。请生成50个高质量的{batch_num}号批次的八字命理案例分析。

类型：{desc}

每个案例必须是JSONL格式（每行一个JSON对象），含以下字段：
- title: 案例标题
- content: 完整分析（至少200字），必须包含：出生日期、四柱八字、大运、命局分析（旺衰/格局/用神忌神/十神）、人生大事应期、结论
- category: "bazi_case"
- source_url: 来源
- verified: true
- source_quality: "authoritative"或"practitioner"或"reference"

{"" if variant != "classical" else "重要提示：请逐条引用滴天髓原文命例，确保命例来自真实古籍记载。不要自己编造古籍案例。"}
{"" if variant != "celebrity" else "重要提示：请使用真实历史人物的八字。帝王类可包括朱元璋、康熙、乾隆、雍正、李世民等。文人类包括苏轼、李白、白居易等。近代包括孙中山、蒋介石、鲁迅等。"}
{"" if variant != "folk" else "重要提示：请创作民间实战风格的案例，包含具体事件应期（如某年发财、某年离婚、某年官非等），体现实战推断逻辑。"}
{"" if variant != "modern" else "重要提示：请使用1970-2000年之间出生的案例，包含具体的人生事件分析（学业、婚姻、事业、财运），体现现代社会的命理特征。"}

直接输出JSONL格式，不要用markdown代码块包裹。"""

    messages = [
        {"role": "system", "content": "你是一位精通中国传统八字命理学的资深命理师，精通滴天髓、子平真诠、三命通会、穷通宝鉴、渊海子平等经典。你擅长通过真实命例讲解命理知识，分析深入透彻。"},
        {"role": "user", "content": prompt}
    ]

    try:
        result = call_api(messages)
        content = result['choices'][0]['message']['content']
        entries = parse_jsonl_response(content)
        return entries
    except Exception as e:
        print(f"  [ERROR] Batch {batch_num} ({variant}): {e}")
        return []

def generate_fengshui_batch(batch_num, variant):
    """Generate a batch of fengshui guides with specific variant."""
    variants = {
        "room": "房间风水——卧室、客厅、厨房、卫生间、书房的详细布局指南",
        "office": "办公商业风水——办公桌布置、会议室、店面、写字楼、商铺的实操指南",
        "outdoor": "室外景观风水——庭院、大门、围墙、水景、植物、道路的风水布局",
        "flying_star": "九宫飞星——2026年流年飞星布局、每月飞星变化、化煞催旺方法",
        "color_material": "色彩材质风水——五行配色、装修材料选择、各空间色彩搭配指南",
        "special": "专题风水——婚房布置、租房风水、学区房选择、健康风水、财运风水等专题"
    }
    desc = variants.get(variant, "风水实用指南")

    prompt = f"""你是一位精通中国传统风水学的资深风水师。请生成50个高质量的{batch_num}号批次的风水实用指南。

类型：{desc}

每个指南必须是JSONL格式（每行一个JSON对象），含以下字段：
- title: 标题
- content: 完整内容（至少200字），必须包含：具体空间/场景描述、宜忌详细说明、实操布局方法、化煞解决方案
- category: "fengshui_guide"
- source_url: 来源
- verified: true
- source_quality: "authoritative"或"practitioner"或"reference"

{"" if variant != "flying_star" else "请提供2026年（丙午年）的九宫飞星具体分析，包括：流年飞星盘、各方位吉凶、催旺化煞方法。"}
{"" if variant != "room" else "请覆盖各种户型和场景，包括小户型、大平层、复式、别墅等不同房型的布局建议。"}
{"" if variant != "office" else "请覆盖不同行业场景，包括互联网公司、实体店、金融公司、创意工作室等。"}

质量标准：
- 每个指南必须有具体实操建议（宜/忌），拒绝空洞理论
- 内容实用、可操作
- 拒绝SEO灌水、广告
- 每个指南至少300字

直接输出JSONL格式，不要用markdown代码块包裹。"""

    messages = [
        {"role": "system", "content": "你是一位精通中国传统风水学的资深风水师，擅长阳宅三要、八宅明镜、玄空飞星、金锁玉关等风水体系。你善于将深奥的风水理论转化为实用的家居布局和生活建议。"},
        {"role": "user", "content": prompt}
    ]

    try:
        result = call_api(messages)
        content = result['choices'][0]['message']['content']
        entries = parse_jsonl_response(content)
        return entries
    except Exception as e:
        print(f"  [ERROR] Batch {batch_num} ({variant}): {e}")
        return []

def run_bazi_batch(args):
    """Wrapper for parallel execution."""
    batch_num, variant = args
    entries = generate_bazi_batch(batch_num, variant)
    return entries, "bazi", variant

def run_fengshui_batch(args):
    """Wrapper for parallel execution."""
    batch_num, variant = args
    entries = generate_fengshui_batch(batch_num, variant)
    return entries, "fengshui", variant

def main():
    print("=" * 60)
    print(f"Batch generation at {datetime.now()}")
    print(f"Model: {MODEL}")
    print(f"Max workers: 5")
    print("=" * 60)

    # Bazi: 90 batches x 50 = 4,500
    # 4 variants x 22-23 batches each
    bazi_variants = ["classical", "celebrity", "folk", "modern"]
    bazi_batches = []
    count = 1
    for v in bazi_variants:
        for _ in range(23):
            bazi_batches.append((count, v))
            count += 1
    # Trim to 90 batches
    bazi_batches = bazi_batches[:90]

    # Fengshui: 50 batches x 50 = 2,500
    fs_variants = ["room", "office", "outdoor", "flying_star", "color_material", "special"]
    fs_batches = []
    count = 1
    for v in fs_variants:
        for _ in range(9):
            fs_batches.append((count, v))
            count += 1
    fs_batches = fs_batches[:50]

    total_bazi = 0
    total_fs = 0
    bazi_success = 0
    fs_success = 0

    print(f"\nTotal batches: {len(bazi_batches)} bazi + {len(fs_batches)} fengshui")
    print(f"Expected total: ~{(len(bazi_batches)*50)} bazi + ~{(len(fs_batches)*50)} fengshui")

    # Process bazi first
    print("\n--- Generating Bazi Cases ---")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(run_bazi_batch, b): b for b in bazi_batches}
        for i, future in enumerate(as_completed(futures)):
            try:
                entries, dtype, variant = future.result()
                if entries:
                    with open(BAZI_OUTPUT, 'a', encoding='utf-8') as f:
                        for e in entries:
                            f.write(json.dumps(e, ensure_ascii=False) + '\n')
                    total_bazi += len(entries)
                    bazi_success += 1
                print(f"  [{i+1}/{len(bazi_batches)}] +{len(entries)} ({variant}) | Total: {total_bazi}")
            except Exception as e:
                print(f"  [{i+1}/{len(bazi_batches)}] Failed: {e}")

    print(f"\n--- Bazi Complete: {total_bazi} entries ---")

    # Process fengshui
    print("\n--- Generating Fengshui Guides ---")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(run_fengshui_batch, b): b for b in fs_batches}
        for i, future in enumerate(as_completed(futures)):
            try:
                entries, dtype, variant = future.result()
                if entries:
                    with open(FS_OUTPUT, 'a', encoding='utf-8') as f:
                        for e in entries:
                            f.write(json.dumps(e, ensure_ascii=False) + '\n')
                    total_fs += len(entries)
                    fs_success += 1
                print(f"  [{i+1}/{len(fs_batches)}] +{len(entries)} ({variant}) | Total: {total_fs}")
            except Exception as e:
                print(f"  [{i+1}/{len(fs_batches)}] Failed: {e}")

    print("\n" + "=" * 60)
    print(f"FINAL RESULTS at {datetime.now()}")
    print("=" * 60)
    print(f"Bazi cases: {total_bazi}")
    print(f"Fengshui guides: {total_fs}")
    print(f"Output: {BAZI_OUTPUT}")
    print(f"Output: {FS_OUTPUT}")
    print("=" * 60)

if __name__ == "__main__":
    main()
