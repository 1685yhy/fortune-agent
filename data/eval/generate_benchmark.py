#!/usr/bin/env python3
"""
易理明灯 (Fortune Agent) — Evaluation Benchmark Query Generator

Generates 1200 diverse, realistic Chinese fortune-telling queries
across 8 domains and 3 difficulty levels using the DeepSeek API.

Domains:   八字 | 紫微斗数 | 风水 | 解梦 | 面相 | 奇门遁甲 | 姓名学 | 择日
Levels:    L1 (基础) | L2 (进阶) | L3 (综合)

Usage:
    python generate_benchmark.py                # Generate all 1200 queries
    python generate_benchmark.py --dry-run      # Show 3 samples per domain (no file output)
    python generate_benchmark.py --domain bazi  # Generate only bazi queries
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = "sk-REPLACED-REMOVED-KEY"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL_PRO = "deepseek-v4-pro"
MODEL_FLASH = "deepseek-v4-flash"
OUTPUT_FILE = "benchmark_queries.jsonl"

SCRIPT_DIR = Path(__file__).parent

# Target counts per domain & difficulty
DOMAIN_CONFIGS = {
    "bazi":     {"cn": "八字",     "l1": 120, "l2": 105, "l3": 75},
    "ziwei":    {"cn": "紫微斗数",  "l1": 80,  "l2": 70,  "l3": 50},
    "fengshui": {"cn": "风水",     "l1": 60,  "l2": 55,  "l3": 35},
    "dream":    {"cn": "解梦",     "l1": 60,  "l2": 55,  "l3": 35},
    "mianxiang":{"cn": "面相",     "l1": 40,  "l2": 35,  "l3": 25},
    "qimen":    {"cn": "奇门遁甲",  "l1": 40,  "l2": 35,  "l3": 25},
    "xingming": {"cn": "姓名学",   "l1": 40,  "l2": 35,  "l3": 25},
    "zeri":     {"cn": "择日",     "l1": 40,  "l2": 35,  "l3": 25},
}

# How many queries per API call per difficulty
BATCH_SIZES = {"L1": 15, "L2": 10, "L3": 8}

# ---------------------------------------------------------------------------
# Prompt content for each domain x difficulty
# ---------------------------------------------------------------------------

L1_DESC = ("基础 — 简单通用问题。查询应简短自然，像是普通用户"
           "刚接触该领域时的提问方式。可以使用口语化表达。")

L2_DESC = ("进阶 — 需要具体专业参数的问题。查询需包含干支、星曜、"
           "方位等专业元素，体现一定知识深度。")

L3_DESC = ("综合 — 多维复杂场景。查询涉及多角度分析，结合多个变量，"
           "需要算命师深度推理和综合分析。")

DOMAIN_GUIDES = {
    "bazi": {
        "L1": (
            "用生肖、五行、出生年份等简单元素直接问财运、感情、事业、健康等。\n"
            "例如：「我是属马的，今年本命年运气怎么样？」「金命的人适合做什么工作？」"
        ),
        "L2": (
            "需包含具体八字参数 —— 日主、月令、大运、流年干支、十神等。\n"
            "例如：「甲木日主生在寅月，走庚申大运，2026丙午年财运如何？」"
        ),
        "L3": (
            "综合命局 + 大运 + 流年 + 神煞，从事业/婚姻/财运/健康等多角度探讨，"
            "涉及重大人生决策（创业、换行、移民等）。\n"
            "例如：「八字身弱财旺，现在走正官大运，适合创业吗？合伙还是单干？"
            "选什么行业？」"
        ),
    },
    "ziwei": {
        "L1": (
            "简单问紫微命盘基本格局、某颗主星坐命的影响。\n"
            "例如：「命宫有紫微星，是不是当领导的命？」「太阴在夫妻宫代表什么？」"
        ),
        "L2": (
            "包含具体宫位、星曜组合、四化等信息。\n"
            "例如：「廉贞七杀在巳宫坐命，夫妻宫有天相，2026年感情运势如何？」"
        ),
        "L3": (
            "多宫互动 + 星曜变化 + 大限流年综合推断。\n"
            "例如：「命宫日月并明，财帛宫武曲化禄，但迁移宫擎羊陀罗夹，"
            "40岁后海外发展前景如何？」"
        ),
    },
    "fengshui": {
        "L1": (
            "简单询问住宅、办公室或店铺的风水好坏。\n"
            "例如：「我家大门正对电梯门，风水上有什么问题？」"
        ),
        "L2": (
            "包含坐向、格局、方位度数、飞星等。\n"
            "例如：「子山午向三居室，2026年五黄煞在哪个方位，怎么化解？」"
        ),
        "L3": (
            "理气 + 峦头 + 流年飞星 + 居住者八字，多因素综合。\n"
            "例如：「八运子山午向住宅，门前反弓路，西南方有水池，"
            "2026年九紫入中后如何布局催旺财运？」"
        ),
    },
    "dream": {
        "L1": (
            "常见梦境象征解释。\n"
            "例如：「梦到从高处掉下来是什么意思？」「梦见死人复活代表什么？」"
        ),
        "L2": (
            "包含具体梦境细节 —— 颜色、数量、情绪、场景。\n"
            "例如：「连续一周梦到被黑蛇追，梦里很害怕，代表什么？」"
        ),
        "L3": (
            "多元素复杂梦境，结合现实生活背景（压力、变故、健康等）。\n"
            "例如：「最近换了工作压力大，连续梦到在水里挣扎、掉牙流血，"
            "还梦到去世的奶奶对我笑，这些梦有什么联系？」"
        ),
    },
    "mianxiang": {
        "L1": (
            "简单面相基础问题，问某个五官或特征的运势含义。\n"
            "例如：「额头窄是不是命不好？」「嘴唇厚代表什么性格？」"
        ),
        "L2": (
            "包含具体五官特征、气色、纹路、疤痕等信息。\n"
            "例如：「印堂悬针纹，山根低，眼下泪痣，事业发展如何？」"
        ),
        "L3": (
            "五官搭配 + 骨相 + 气色 + 流年运气，综合分析性格与运势。\n"
            "例如：「脸方下巴尖，眉浓但眉尾散，眼有神但眼下青黑，"
            "这种面相适合做生意还是上班？」"
        ),
    },
    "qimen": {
        "L1": (
            "简单问出行、办事的时机和方位。\n"
            "例如：「明天适合搬家吗？往哪个方向好？」"
        ),
        "L2": (
            "包含具体时间、方位、用神、门星神等。\n"
            "例如：「2026年农历六月开公司，奇门看选哪天好，开门在哪个方位？」"
        ),
        "L3": (
            "复杂时空决策，结合人事、地理、天时，涉及商业竞争等具体场景。\n"
            "例如：「公司投标三千万项目，标书在东南，对手在西北，"
            "起阴盘奇门局看能中标吗？怎么布局增加胜算？」"
        ),
    },
    "xingming": {
        "L1": (
            "简单问名字好坏或寓意。\n"
            "例如：「张伟这名字怎么样？」「名字里带'雨'字好不好？」"
        ),
        "L2": (
            "包含具体姓名、三才五格笔画数、五行属性等。\n"
            "例如：「张伟，天格12人格24地格16，五行缺什么？对事业有帮助吗？」"
        ),
        "L3": (
            "姓名 + 八字 + 行业 + 家族等多维度综合分析，涉及改名方案。\n"
            "例如：「孩子八字火旺土燥，原名'李浩然'总格41画，"
            "想改补水的名字，从三才五格、生肖喜忌、音形义推荐几个方案。」"
        ),
    },
    "zeri": {
        "L1": (
            "简单问某类事情的吉日。\n"
            "例如：「这个月哪天适合搬家？」「今年结婚的好日子有哪些？」"
        ),
        "L2": (
            "包含具体日期范围、事件类型、当事人生肖八字信息。\n"
            "例如：「2026年国庆结婚，新郎1990马，新娘1992猴，哪天最好？」"
        ),
        "L3": (
            "多因素择日，结合八字 + 神煞 + 星宿 + 坐山/朝向等。\n"
            "例如：「老宅重建动土，房主八字甲寅戊辰壬申辛亥，乾山巽向，"
            "2026年哪个月可以动工？需要避开什么神煞和仪式？」"
        ),
    },
}

DIFFICULTY_LABELS = {"L1": "基础", "L2": "进阶", "L3": "综合"}
DIFFICULTY_DESCS = {"L1": L1_DESC, "L2": L2_DESC, "L3": L3_DESC}

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def make_http_client():
    return httpx.Client(
        timeout=httpx.Timeout(120.0, connect=15.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )


def call_deepseek(client, messages, model, max_tokens=4096, temperature=0.85):
    """Call DeepSeek Chat API and return the content text."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    resp = client.post(API_URL, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(domain_key, difficulty, count):
    """Build (system_prompt, user_prompt) for a specific domain x difficulty x count."""
    cfg = DOMAIN_CONFIGS[domain_key]
    guide = DOMAIN_GUIDES[domain_key][difficulty]
    diff_label = DIFFICULTY_LABELS[difficulty]
    diff_desc = DIFFICULTY_DESCS[difficulty]

    system_prompt = f"""你是易理明灯算命平台的评测数据生成专家。你生成真实、自然、多样的用户算命查询。

## 当前生成任务
- 领域：{cfg["cn"]}（{domain_key}）
- 难度：{diff_label}（{difficulty}）
- 难度说明：{diff_desc}
- 需要生成：{count} 条不同的查询

## 查询风格指导
{guide}

## 质量规范（必须遵守）
1. 语言自然真实，听起来像真实用户在问算命师傅
2. 身份多样化：混合不同性别（男女约各半）、年龄段（20代到60代）、职业、地域（北京/上海/广东/东北/四川/浙江等）
3. 每一条查询的场景、语气、内容都要不同，避免重复模式
4. L1 可以简短口语化；L2 和 L3 需要有足够的细节支撑专业分析
5. 查询是完整的问句，不要用关键字堆砌
6. 不要替算命师回答，只输出用户提问
7. 必须严格按 JSON 数组格式输出，不能添加额外文字

## 输出格式
返回一个 JSON 数组，数组每个元素格式如下：

```json
{{
  "id": "{domain_key}_{difficulty}_001",
  "domain": "{domain_key}",
  "difficulty": "{difficulty}",
  "query": "用户查询文本",
  "expected_dimensions": ["维度1", "维度2"],
  "reference_answer_hints": ["提示1", "提示2", "提示3"]
}}
```

字段说明：
- id：{domain_key}_L{difficulty[-1]}_001 三位序号递增
- query：用户提问原文，中文，20~120字
- expected_dimensions：2~4个需要分析的维度
- reference_answer_hints：2~4条正确分析方向的提示"""

    user_prompt = (
        f"请为{cfg['cn']}领域生成{count}条{diff_label}难度的用户查询。"
        f"输出格式为JSON数组，数组包含{count}个元素。"
    )
    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _extract_json_array(text: str):
    """Extract a JSON array from text that may contain markdown fences or extra prose."""
    # Strip markdown code fences
    text = re.sub(r'(?s)^```(?:json)?\s*', '', text)
    text = re.sub(r'(?s)\s*```$', '', text)

    # Find the outermost [...] block
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    else:
        return None

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        return None


def _parse_jsonl_lines(text: str):
    """Fallback: try to parse each line as a JSON object."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items if items else None


def parse_response(text: str):
    """Try multiple strategies to extract a list of items from the LLM response."""
    # Strategy 1: parse as JSON array
    items = _extract_json_array(text)
    if items is not None:
        return items
    # Strategy 2: line-by-line JSONL parse
    items = _parse_jsonl_lines(text)
    if items is not None:
        return items
    return None


def validate_items(items, domain, difficulty):
    """Validate and normalize item fields, return list of clean dicts."""
    valid = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Ensure required fields exist
        item.setdefault("id", f"{domain}_{difficulty}_{len(valid) + 1:03d}")
        item.setdefault("domain", domain)
        item.setdefault("difficulty", difficulty)
        if "query" not in item or not isinstance(item["query"], str) or not item["query"].strip():
            continue
        if not isinstance(item.get("expected_dimensions"), list):
            item["expected_dimensions"] = []
        if not isinstance(item.get("reference_answer_hints"), list):
            item["reference_answer_hints"] = []
        valid.append(item)
    return valid


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------

def generate_batch(client, domain, difficulty, count, dry_run=False):
    """Generate a single batch of queries using the LLM."""
    model = MODEL_FLASH if dry_run else MODEL_PRO
    sys_prompt, user_prompt = build_prompt(domain, difficulty, count)

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    text = call_deepseek(client, messages, model)
    items = parse_response(text)
    if items is None:
        sys.stderr.write(f"    [PARSE-FAIL] Could not parse response for {domain}_{difficulty}\n")
        return []

    items = validate_items(items, domain, difficulty)
    return items[:count]


# ---------------------------------------------------------------------------
# Main generation loop
# ---------------------------------------------------------------------------

def generate_all(dry_run=False, single_domain=None):
    """Generate all benchmark queries across domains and difficulties."""
    client = make_http_client()
    all_queries = []

    domains_to_process = (
        [single_domain] if single_domain
        else list(DOMAIN_CONFIGS.keys())
    )

    for domain in domains_to_process:
        if domain not in DOMAIN_CONFIGS:
            print(f"  [SKIP] Unknown domain: {domain}")
            continue
        cfg = DOMAIN_CONFIGS[domain]

        for diff_key in ("L1", "L2", "L3"):
            count_key = diff_key.lower()
            target = cfg[count_key]
            if target == 0:
                continue

            label = f"{domain}_{diff_key}"

            if dry_run:
                # Dry-run: generate just 3 samples with the flash model
                try:
                    items = generate_batch(client, domain, diff_key, 3, dry_run=True)
                except Exception as exc:
                    items = []
                    print(f"  {label}: ERROR — {exc}")
                print(f"\n{'─' * 60}")
                print(f"  {label} — {len(items)} samples")
                for it in items:
                    print(f"    [{it['id']}] {it['query'][:100]}")
                continue

            # Full generation
            print(f"\n{'─' * 60}")
            print(f"  {label}  →  target {target}")
            generated = []

            for attempt in range(1, 31):  # max 30 attempts per combo
                remaining = target - len(generated)
                if remaining <= 0:
                    break
                to_gen = min(BATCH_SIZES[diff_key], remaining)
                try:
                    items = generate_batch(client, domain, diff_key, to_gen)
                    generated.extend(items)
                    print(f"    batch {attempt:2d}: +{len(items):2d}  →  "
                          f"{len(generated):3d}/{target}")
                    time.sleep(1.2)  # polite rate limiting
                except Exception as exc:
                    print(f"    batch {attempt:2d}: FAILED — {exc}")
                    time.sleep(3.0)
                    continue

            # Renumber IDs sequentially
            for i, item in enumerate(generated):
                item["id"] = f"{domain}_{diff_key}_{i + 1:03d}"

            all_queries.extend(generated)

            if len(generated) < target:
                print(f"  [WARN] {label}: only got {len(generated)}/{target} "
                      f"({target - len(generated)} missing)")

            # Incremental save so partial progress is never lost
            _save_queries(all_queries)

    if not dry_run and all_queries:
        _save_queries(all_queries)
        _print_summary(all_queries)

    return all_queries


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _save_queries(queries):
    """Write queries to JSONL in one shot (atomic-ish via temp file)."""
    out_path = SCRIPT_DIR / OUTPUT_FILE
    tmp = out_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    tmp.replace(out_path)


def _load_existing():
    """Load existing queries from disk (for resuming)."""
    path = SCRIPT_DIR / OUTPUT_FILE
    if not path.exists():
        return []
    queries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))
    return queries


def _print_summary(queries):
    """Print a summary table of generated queries per domain x difficulty."""
    counts = {}
    for q in queries:
        d, diff = q["domain"], q["difficulty"]
        counts[(d, diff)] = counts.get((d, diff), 0) + 1

    print(f"\n{'=' * 62}")
    print(f"  Benchmark Queries — Summary")
    print(f"{'=' * 62}")
    header = f"  {'Domain':<14} {'L1':<8} {'L2':<8} {'L3':<8} {'Total':<8}"
    print(header)
    print(f"  {'─' * 46}")

    grand_total = 0
    all_ok = True
    for domain, cfg in DOMAIN_CONFIGS.items():
        l1 = counts.get((domain, "L1"), 0)
        l2 = counts.get((domain, "L2"), 0)
        l3 = counts.get((domain, "L3"), 0)
        total = l1 + l2 + l3
        exp = cfg["l1"] + cfg["l2"] + cfg["l3"]
        ok = total == exp
        if not ok:
            all_ok = False
        status = "" if ok else f"[expected {exp}]"
        print(f"  {domain:<14} {l1:<8} {l2:<8} {l3:<8} {total:<8} {status}")
        grand_total += total

    print(f"  {'─' * 46}")
    print(f"  {'TOTAL':<14} {'':<8} {'':<8} {'':<8} {grand_total:<8}")
    print()
    if all_ok:
        print(f"  All counts match targets. Total: {grand_total} (目标 1200)")
    else:
        print(f"  Some counts deviate from targets. Total: {grand_total}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate Fortune Agent benchmark evaluation queries"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate 3 samples per domain with Flash model, no file output"
    )
    parser.add_argument(
        "--domain", type=str, default=None,
        help="Generate only queries for a specific domain (e.g. bazi)"
    )
    args = parser.parse_args()

    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"╔{'═' * 58}╗")
    print(f"║  易理明灯 — Evaluation Benchmark Generator")
    print(f"║  Model:   {MODEL_FLASH if args.dry_run else MODEL_PRO}")
    print(f"║  Output:  {SCRIPT_DIR / OUTPUT_FILE}")
    print(f"╚{'═' * 58}╝")

    if args.dry_run:
        print(f"\n  DRY-RUN mode: 3 samples per domain\n")
    elif args.domain:
        print(f"\n  Single-domain mode: {args.domain}\n")

    generate_all(dry_run=args.dry_run, single_domain=args.domain)

    print(f"\nDone.")


if __name__ == "__main__":
    main()
