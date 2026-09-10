#!/usr/bin/env python3
"""
易理明灯 (Fortune Agent) — Evaluation Benchmark Query Generator v2

Generates 5,000 evaluation queries with proper birth info distribution.
CRITICAL: L2/L3 queries MUST include complete birth info (year/month/day/time/gender)
so the fortune-telling system can calculate bazi.

Target Distribution:
    L1 (基础)    500   (10%)
    L2 (进阶)  2,500   (50%)
    L3 (综合)  2,000   (40%)
    Total      5,000   (100%)

Usage:
    python data/eval/generate_benchmark_v2.py                    # Generate all 5000
    python data/eval/generate_benchmark_v2.py --dry-run          # Show 5 samples per domain
    python data/eval/generate_benchmark_v2.py --resume           # Continue from existing file
    python data/eval/generate_benchmark_v2.py --domain bazi      # Single domain
    python data/eval/generate_benchmark_v2.py --validate-only    # Just validate existing file
"""

import argparse
import json
import os
import random
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
MODEL_PRO = "deepseek-flash"
OUTPUT_FILE = "benchmark_queries_v2.jsonl"
SCRIPT_DIR = Path(__file__).parent

# Retry settings
MAX_RETRIES = 5
BASE_DELAY = 3.0
MAX_CONCURRENT_BATCHES = 3

# Target counts per domain & difficulty (sums to exactly 5000)
DOMAIN_CONFIGS = {
    "bazi":     {"cn": "八字",     "l1": 120, "l2": 300, "l3": 250},
    "ziwei":    {"cn": "紫微斗数",  "l1":  80, "l2": 250, "l3": 200},
    "fengshui": {"cn": "风水",     "l1":  60, "l2": 250, "l3": 150},
    "dream":    {"cn": "解梦",     "l1":  60, "l2": 400, "l3": 250},
    "mianxiang":{"cn": "面相",     "l1":  40, "l2": 350, "l3": 150},
    "qimen":    {"cn": "奇门遁甲",  "l1":  40, "l2": 300, "l3": 200},
    "xingming": {"cn": "姓名学",   "l1":  50, "l2": 350, "l3": 400},
    "zeri":     {"cn": "择日",     "l1":  50, "l2": 300, "l3": 400},
}

# Batch sizes per difficulty (how many queries per API call)
BATCH_SIZES = {"L1": 20, "L2": 12, "L3": 8}

# Gender mix guidance
GENDERS = ["男", "女"]
CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "成都", "重庆", "武汉",
    "西安", "南京", "苏州", "天津", "长沙", "郑州", "东莞", "青岛",
    "沈阳", "宁波", "昆明", "大连", "厦门", "合肥", "佛山", "福州",
    "哈尔滨", "济南", "温州", "长春", "石家庄", "常州", "泉州", "南宁",
    "贵阳", "南昌", "太原", "烟台", "嘉兴", "南通", "金华", "珠海",
    "惠州", "徐州", "海口", "乌鲁木齐", "绍兴", "中山", "台州", "兰州",
]
REGIONS = [
    "广东", "浙江", "江苏", "山东", "河南", "四川", "湖北", "湖南",
    "福建", "安徽", "河北", "辽宁", "陕西", "江西", "广西", "重庆",
    "云南", "山西", "贵州", "甘肃", "海南", "吉林", "黑龙江", "新疆",
]

# Sample birth info pools for age diversity
BIRTH_YEARS = list(range(1960, 2006))  # ~20 to ~66 years old
BIRTH_MONTHS = list(range(1, 13))
BIRTH_DAYS = list(range(1, 29))
BIRTH_HOURS = [
    "子时", "丑时", "寅时", "卯时", "辰时", "巳时",
    "午时", "未时", "申时", "酉时", "戌时", "亥时",
]

# ---------------------------------------------------------------------------
# Prompt builders — each domain x difficulty has tailored prompts
# with STRONG birth info requirements for L2 and L3
# ---------------------------------------------------------------------------

# Shared emphatic birth info requirement for L2
BIRTH_INFO_REQ_L2 = """## 强制要求（必须遵守）—— 这是评分的关键
每条查询必须包含具体的出生信息，让算命系统可以直接排八字进行推算。
每条查询必须包含以下所有信息：
1. **性别**（男/女）—— 必须明确写出"男"或"女"
2. **出生年份**（如1990年、1985年、1998年）—— 必须给出具体年份
3. **出生月份和日期**（如农历3月15日、公历8月8日、八月初八等）—— 必须给出
4. **出生时辰**（如子时、午时、下午3点、申时等）—— 强烈建议包含
5. **出生地或现居地**（如广东人、上海、北京等）—— 建议包含

✅ 好的例子（包含完整出生信息）：
- "女，1989年12月11日子时，广东人，做财务的。想问2025乙巳年投资财运怎么样？"
- "男，1995年农历三月初八午时出生的，上海程序员，最近想跳槽，帮我看下事业运。"

❌ 不好的例子（缺少出生信息，会被评0分）：
- "属鸡的财运怎么样"  ← 没有具体年月和性别
- "我是土命适合做什么"  ← 没有出生年月日时
- "今年运气好不好"  ← 没有任何出生数据"""

# Even stricter for L3
BIRTH_INFO_REQ_L3 = """## 强制要求（必须遵守）—— 这是评分的关键
每条查询必须包含完整的出生信息，让算命系统可以直接排八字进行深度推算。
每条查询必须包含以下所有信息：
1. **性别**（男/女）—— 必须明确
2. **出生年份、月份、日期**（全部必须给出，农历或公历均可）
3. **出生时辰**（必须包含，不能省略！如子时、午时、申时等十二时辰之一）
4. **出生地或现居地**（必须包含城市或省份）
5. **职业或人生阶段信息**（建议包含，如行业、学历状态等）

此外，L3查询还应包含一定的命理知识元素，如大运、流年、十神、用神等概念，
体现用户对自身命局有一定了解。

✅ 好的例子（包含完整出生信息+命理元素）：
- "男，庚午年丁亥月庚申日戊寅时，上海互联网产品经理34岁，八字身弱财旺，
  现在走偏财大运，明年流年正印。想问适合创业还是继续上班？"
- "女，1988年农历六月初八酉时，成都人，做设计的。八字食神生财，
  但日坐伤官婚姻不顺，现在走七杀大运，2026年能遇到正缘吗？"

❌ 不好的例子（会被评0分）：
- "师傅我今年运气怎么样"  ← 没有任何出生数据
- "属马的桃花运如何"  ← 没有具体年月日时
- "我八字缺什么"  ← 没有给出完整出生年月日时"""

# L1 desc – simple, generic, can omit birth info
L1_DESC = ("基础 — 简单通用问题。查询应简短自然，像是普通用户"
           "刚接触该领域时的提问方式。可以使用口语化表达。")

L2_DESC = ("进阶 — 需要具体出生信息的问题。查询必须包含用户的出生年月日和性别，"
           "让算命系统能排八字进行推算。")

L3_DESC = ("综合 — 多维复杂场景。查询涉及多角度分析，需要完整的出生信息"
           "和一定的命理知识背景，体现用户对自身命局的了解。")


def _make_prompt(domain_key, difficulty, count):
    """Build the system + user prompt pair for a domain x difficulty batch."""
    cfg = DOMAIN_CONFIGS[domain_key]
    domain_cn = cfg["cn"]
    diff_label = {"L1": "基础", "L2": "进阶", "L3": "综合"}[difficulty]

    # --- Difficulty-specific guides ---
    guides = _get_domain_guide(domain_key, difficulty)

    # --- System prompt ---
    if difficulty == "L1":
        birth_requirement = (
            "## 注意\n"
            "L1查询可以简短，不要求完整的出生信息。可以用生肖、五行等简单元素提问。"
            "但最好还是包含一些基本信息（如出生年份或生肖）。"
        )
    elif difficulty == "L2":
        birth_requirement = BIRTH_INFO_REQ_L2
    else:
        birth_requirement = BIRTH_INFO_REQ_L3

    system_prompt = f"""你是易理明灯算命平台的评测数据生成专家。你生成真实、自然、多样的用户算命查询。

## 当前生成任务
- 领域：{domain_cn}（{domain_key}）
- 难度：{diff_label}（{difficulty}）
- 需要生成：{count} 条不同的查询

## 查询风格指导
{guides}

{birth_requirement}

## 通用质量规范（必须遵守）
1. 语言自然真实，听起来像真实用户在问算命师傅，使用口语化的表达方式
2. 身份多样化：混合不同性别（男女各半）、年龄段（20代到60代）、行业（互联网/金融/教育/医疗/制造业/自由职业等）、地域（全国各城市）
3. 每一条查询的场景、语气、内容都要不同，避免重复模式和套路化表达
4. 查询是完整的问句，体现用户的具体困惑和焦虑
5. 不要替算命师傅回答，只输出用户提问
6. 必须严格按 JSON 数组格式输出，不能添加额外文字

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
- id：{domain_key}_L{{difficulty[-1]}}_001 三位序号递增
- query：用户提问原文，中文，30~150字（L1可短至15字）
- expected_dimensions：2~4个需要分析的维度
- reference_answer_hints：2~4条正确分析方向的提示"""

    user_prompt = (
        f"请为{domain_cn}领域生成{count}条{diff_label}难度的用户查询。"
        f"输出格式为JSON数组，数组包含{count}个元素。"
    )
    return system_prompt, user_prompt


def _get_domain_guide(domain_key, difficulty):
    """Return domain-specific query style guide per difficulty level."""
    if difficulty == "L1":
        guides = {
            "bazi": (
                "用生肖、五行、出生年份等简单元素直接问财运、感情、事业、健康。\n"
                "例如：「师傅，我属马的，今年本命年运气怎么样？」"
            ),
            "ziwei": (
                "简单问紫微命盘基本格局、某颗主星坐命的影响。\n"
                "例如：「命宫有紫微星，是不是当领导的命？」"
            ),
            "fengshui": (
                "简单询问住宅、办公室或店铺的风水好坏。\n"
                "例如：「我家大门正对电梯门，风水上有什么问题？」"
            ),
            "dream": (
                "常见梦境象征解释。\n"
                "例如：「梦到从高处掉下来是什么意思？」"
            ),
            "mianxiang": (
                "简单面相基础问题，问某个五官或特征的运势含义。\n"
                "例如：「额头窄是不是命不好？」"
            ),
            "qimen": (
                "简单问出行、办事的时机和方位。\n"
                "例如：「明天适合搬家吗？往哪个方向好？」"
            ),
            "xingming": (
                "简单问名字好坏或寓意。\n"
                "例如：「张伟这名字怎么样？」"
            ),
            "zeri": (
                "简单问某类事情的吉日。\n"
                "例如：「这个月哪天适合搬家？」"
            ),
        }
    elif difficulty == "L2":
        guides = {
            "bazi": (
                "需包含具体生辰信息（出生年月日时+性别）以及一定的八字术语（如日主、大运、流年、十神等）。\n"
                "每条必须包含完整的出生年月日和性别，最好包含出生时辰。\n"
                "例如：「女，1991年农历五月十二子时出生的，上海人做财务。\n"
                "          想问2026丙午年事业运怎么样，能升职加薪吗？」"
            ),
            "ziwei": (
                "需包含具体生辰信息、宫位、星曜组合、四化等。\n"
                "每条必须包含完整的出生年月日和性别，建议包含时辰。\n"
                "例如：「男，1988年公历8月15日午时出生，广州人。\n"
                "          命宫太阳太阴，财帛宫武曲化禄，2026年投资适合吗？」"
            ),
            "fengshui": (
                "需包含居住者的出生信息、房屋具体坐向、格局、方位度数、飞星等。\n"
                "查询中必须包含居住者或使用者的出生年月日和性别。\n"
                "例如：「男，1975年九月初五卯时生，住子山午向三居室，\n"
                "          2026年五黄煞在哪个方位，怎么化解？」"
            ),
            "dream": (
                "需包含具体梦境细节（颜色、数量、情绪、场景）以及做梦人的出生信息。\n"
                "查询必须包含做梦人的性别、出生年月日时。\n"
                "例如：「女，1993年农历腊月十八辰时生，最近换了工作压力大，\n"
                "          连续梦到在水里挣扎、掉牙流血，这些梦有什么联系？」"
            ),
            "mianxiang": (
                "需包含具体五官特征和面相者的出生信息。\n"
                "查询必须包含面相者的性别、出生年月日时。\n"
                "例如：「男，1985年公历3月20日寅时生的，印堂悬针纹，\n"
                "          山根低，眼下有泪痣，事业发展如何？」"
            ),
            "qimen": (
                "需包含具体时间、方位、用神、门星神等，以及问事者的出生信息。\n"
                "查询必须包含问事者的性别、出生年月日时。\n"
                "例如：「女，1992年八月十八酉时生，深圳做电商的，\n"
                "          2026年农历六月想开新店，奇门看选哪天好？」"
            ),
            "xingming": (
                "需包含具体姓名和这个人的出生信息（三才五格结合八字分析）。\n"
                "查询必须包含姓主的性别、出生年月日时。\n"
                "例如：「男，2019年公历6月1日辰时生的儿子，\n"
                "          取名'李浩宇'，天格8人格12地格15，五行缺什么？」"
            ),
            "zeri": (
                "需包含具体日期范围、事件类型、当事人的出生信息。\n"
                "查询必须包含当事人的性别、出生年月日时（至少年份和生肖）。\n"
                "例如：「男，1990年庚午马年八月廿二辰时生，女，1992年壬申猴年\n"
                "          腊月初三子时生，2026年国庆适合结婚吗？」"
            ),
        }
    else:  # L3
        guides = {
            "bazi": (
                "综合命局+大运+流年+神煞，从事业/婚姻/财运/健康等多角度探讨。\n"
                "涉及重大人生决策（创业、换行、移民等）。\n"
                "必须包含完整八字信息和一定命理认知。\n"
                "例如：「男，庚午年丁亥月庚申日戊寅时，上海互联网产品经理34岁，\n"
                "          八字身弱财旺，现在走偏财大运，明年流年正印。\n"
                "          最近想辞职创业，从命理角度看适合单干还是继续上班？」"
            ),
            "ziwei": (
                "多宫互动+星曜变化+大限流年综合推断，结合命主完整生辰。\n"
                "必须包含性别、出生年月日时、现居地。\n"
                "例如：「女，丙寅年庚寅月癸未日壬子时，成都人，\n"
                "          命宫日月并明，财帛宫武曲化禄但迁移宫擎羊陀罗夹，\n"
                "          40岁后海外发展前景如何？」"
            ),
            "fengshui": (
                "理气+峦头+流年飞星+居住者八字，多因素综合。\n"
                "必须包含居住者的完整出生信息和房屋详细情况。\n"
                "例如：「男，1972年壬子年五月初九辰时生，\n"
                "          八运子山午向住宅，门前反弓路，西南方有水池，\n"
                "          2026年九紫入中后如何布局催旺财运？」"
            ),
            "dream": (
                "多元素复杂梦境，结合现实生活背景（压力、变故、健康等）和做梦人完整生辰。\n"
                "必须包含做梦人的性别、出生年月日时、现居地和生活背景。\n"
                "例如：「女，1987年丁卯年七月十六戌时，深圳做金融的，\n"
                "          最近换了工作压力大，连续梦到在水里挣扎、掉牙流血，\n"
                "          还梦到去世的外婆，这些梦跟流年运势有关系吗？」"
            ),
            "mianxiang": (
                "五官搭配+骨相+气色+流年运气+生辰八字，综合分析。\n"
                "必须包含被看面相者的完整出生信息。\n"
                "例如：「男，戊辰年乙卯月壬戌日丙午时，北京创业三年了，\n"
                "          脸方下巴尖，眉浓但眉尾散，眼有神但眼下青黑，\n"
                "          这种面相适合继续创业还是回去上班？」"
            ),
            "qimen": (
                "复杂时空决策，结合人事、地理、天时，涉及商业竞争等场景。\n"
                "必须包含问事者的完整出生信息和具体决策场景。\n"
                "例如：「男，甲子年丙寅月戊戌日庚申时，杭州做跨境电商的，\n"
                "          公司投标三千万项目，标书放东南位，竞争对手在西北，\n"
                "          起阴盘奇门局看能中标吗？怎么布局增加胜算？」"
            ),
            "xingming": (
                "姓名+八字+行业+家族等多维度综合分析，涉及改名方案。\n"
                "必须包含姓主的完整出生信息。\n"
                "例如：「男，2020年庚子年五月初八巳时生，八字火旺土燥，\n"
                "          原名'李浩然'总格41画，想改一个补水的名字，\n"
                "          从三才五格、生肖喜忌、音形义推荐几个方案。」"
            ),
            "zeri": (
                "多因素择日，结合八字+神煞+星宿+坐山/朝向等。\n"
                "必须包含事主的完整出生信息和详细 事件参数。\n"
                "例如：「男，1969年己酉年七月廿三寅时，老宅重建动土，\n"
                "          乾山巽向，家人还有一个女孩2001年辛巳年五月十六辰时生。\n"
                "          2026年哪个月可以动工？需要避开什么神煞？」"
            ),
        }
    return guides.get(domain_key, "")


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def make_http_client():
    return httpx.Client(
        timeout=httpx.Timeout(180.0, connect=15.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )


def call_deepseek(client, messages, model=MODEL_PRO, max_tokens=8192, temperature=0.9):
    """Call DeepSeek Chat API and return the content text.

    V4 Pro uses reasoning tokens before output, so max_tokens must be generous.
    """
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
    data = resp.json()
    msg = data["choices"][0]["message"]
    text = (msg.get("content") or "").strip()
    if not text:
        text = (msg.get("reasoning_content") or "").strip()
    return text


def call_with_retry(client, messages, model=MODEL_PRO, max_tokens=8192, temperature=0.9):
    """Call API with exponential backoff retry."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return call_deepseek(client, messages, model, max_tokens, temperature)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                delay = BASE_DELAY * (2 ** (attempt - 1))
                sys.stderr.write(f"    [RATE-LIMIT] attempt {attempt}/{MAX_RETRIES}, "
                                 f"retrying in {delay:.0f}s\n")
                sys.stderr.flush()
                time.sleep(delay)
                last_exc = exc
            elif exc.response.status_code >= 500:
                delay = BASE_DELAY * (2 ** (attempt - 1))
                sys.stderr.write(f"    [SERVER-ERROR {exc.response.status_code}] "
                                 f"attempt {attempt}/{MAX_RETRIES}, retrying in {delay:.0f}s\n")
                sys.stderr.flush()
                time.sleep(delay)
                last_exc = exc
            else:
                raise
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            delay = BASE_DELAY * (2 ** (attempt - 1))
            sys.stderr.write(f"    [NET-ERROR] attempt {attempt}/{MAX_RETRIES}, "
                             f"retrying in {delay:.0f}s\n")
            sys.stderr.flush()
            time.sleep(delay)
            last_exc = exc
    raise RuntimeError(f"All {MAX_RETRIES} retries exhausted") from last_exc


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _extract_json_array(text):
    """Extract a JSON array from text that may contain markdown fences or extra prose."""
    text = re.sub(r'(?s)^```(?:json)?\s*', '', text)
    text = re.sub(r'(?s)\s*```$', '', text)
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


def _parse_jsonl_lines(text):
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


def parse_response(text):
    """Try multiple strategies to extract a list of items from the LLM response."""
    items = _extract_json_array(text)
    if items is not None:
        return items
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
        item.setdefault("domain", domain)
        item.setdefault("difficulty", difficulty)
        if "query" not in item or not isinstance(item["query"], str) or not item["query"].strip():
            continue
        # Ensure id is set
        if "id" not in item or not item["id"]:
            item["id"] = f"{domain}_{difficulty}_{len(valid) + 1:03d}"
        # Normalize expected_dimensions
        dims = item.get("expected_dimensions")
        if isinstance(dims, str):
            dims = [d.strip() for d in dims.replace("、", ",").split(",") if d.strip()]
        if not isinstance(dims, list):
            dims = []
        item["expected_dimensions"] = dims
        # Normalize reference_answer_hints
        hints = item.get("reference_answer_hints")
        if isinstance(hints, str):
            hints = [h.strip() for h in hints.replace("；", ";").split(";") if h.strip()]
        if not isinstance(hints, list):
            hints = []
        item["reference_answer_hints"] = hints
        valid.append(item)
    return valid


# ---------------------------------------------------------------------------
# Birth info validation
# ---------------------------------------------------------------------------

BIRTH_PATTERNS = {
    "year_4digit": r'(?:19|20)\d{2}年',        # 1990年, 2024年
    "year_ganzhi": r'[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]年',  # 庚午年
    "year_zodiac": r'属[鼠牛虎兔龙蛇马羊猴鸡狗猪]',  # 属马
    "year_2digit": r'\d{2}后',                  # 90后, 85后
    "month_day": r'\d{1,2}月\d{1,2}[日号]',     # 3月15日, 8月8号
    "month_day_lunar": r'[正二三四五六七八九十冬腊]月[初一二三四五六七八九十廿卅][一二三四五六七八九十]?',  # 八月初八, 腊月廿三
    "month_lunar_alt": r'农历[^，。；,;\s]{1,6}[月日]', # 农历三月初八
    "time_ganzhi": r'[子丑寅卯辰巳午未申酉戌亥]时',  # 子时, 午时
    "time_digit": r'[上中下]?午?\s*\d{1,2}[：:]\d{2}',  # 14:30, 下午3点
    "time_am_pm": r'[早中晚下]午\s*\d{1,2}点',    # 下午3点
    "time_approx": r'(?:凌晨|早上|上午|中午|下午|晚上|深夜)\s*\d{1,2}[点时]',  # 早上8点
    "gender_male": r'(?<![男女])(?:男)(?:性|生|的|人)?(?!性|生)',  # 男
    "gender_female": r'(?<![男女])(?:女)(?:性|生|的|人)?(?!性|生)',  # 女
    "gender_alt": r'(?:姑娘|女生|男士|先生)',     # 姑娘, 男士
    "city_loc": r'(?:来自|住在|出生于|在|现居)[^，。；,;\s]{2,6}(?:市|省|州|城)',  # 在广州市, 来自浙江省
    "city_people": (r'(?:广东|广西|湖南|湖北|河北|河南|山东|山西|江苏|浙江|江西|四川|福建|'
                     r'安徽|辽宁|云南|贵州|甘肃|陕西|海南|吉林|黑龙江|新疆|西藏|宁夏|青海|'
                     r'北京|上海|天津|重庆)人'),
    "city_name": (r'(?:北京|上海|广州|深圳|杭州|成都|重庆|武汉|西安|南京|天津|长沙|'
                   r'郑州|东莞|青岛|沈阳|宁波|昆明|大连|厦门|合肥|佛山|福州|哈尔滨|'
                   r'济南|温州|长春|石家庄|常州|泉州|南宁|贵阳|南昌|太原|烟台|嘉兴|'
                   r'南通|金华|珠海|惠州|徐州|海口|乌鲁木齐|绍兴|中山|台州|兰州)'),
}

# Compiled patterns
COMPILED_BIRTH = {}
for key, pat in BIRTH_PATTERNS.items():
    try:
        COMPILED_BIRTH[key] = re.compile(pat)
    except re.error:
        pass


def check_birth_info(query):
    """Check how much birth info a query contains. Returns a dict of findings."""
    result = {}
    for key, pat in COMPILED_BIRTH.items():
        result[key] = bool(pat.search(query))
    # Simplified gender check: just look for standalone 男 or 女 characters.
    # This is sufficient for validation statistics across thousands of queries.
    result["gender"] = bool(re.search(r'[男女]', query))
    return result


def birth_info_score(check_result):
    """Score birth info completeness (0-5)."""
    score = 0
    if check_result.get("year_4digit") or check_result.get("year_ganzhi") or check_result.get("year_zodiac"):
        score += 1  # Has year
    if check_result.get("month_day") or check_result.get("month_day_lunar") or check_result.get("month_lunar_alt"):
        score += 1  # Has month/day
    if check_result.get("time_ganzhi") or check_result.get("time_digit") or check_result.get("time_am_pm") or check_result.get("time_approx"):
        score += 1  # Has time
    if check_result.get("gender"):
        score += 1  # Has gender
    if check_result.get("city_loc") or check_result.get("city_people") or check_result.get("city_name"):
        score += 1  # Has location
    return score


def validate_birth_coverage(queries):
    """Comprehensive birth info validation across all queries.
    Returns statistics per domain/difficulty.
    """
    stats = {}
    for q in queries:
        domain = q["domain"]
        difficulty = q["difficulty"]
        key = (domain, difficulty)
        if key not in stats:
            stats[key] = {"total": 0, "has_year": 0, "has_month_day": 0, "has_time": 0,
                          "has_gender": 0, "has_city": 0, "scores": []}
        stats[key]["total"] += 1
        check = check_birth_info(q["query"])
        if check.get("year_4digit") or check.get("year_ganzhi") or check.get("year_zodiac"):
            stats[key]["has_year"] += 1
        if check.get("month_day") or check.get("month_day_lunar") or check.get("month_lunar_alt"):
            stats[key]["has_month_day"] += 1
        if check.get("time_ganzhi") or check.get("time_digit") or check.get("time_am_pm") or check.get("time_approx"):
            stats[key]["has_time"] += 1
        if check.get("gender"):
            stats[key]["has_gender"] += 1
        if check.get("city_loc") or check.get("city_people") or check.get("city_name"):
            stats[key]["has_city"] += 1
        stats[key]["scores"].append(birth_info_score(check))

    # Aggregate
    report = {}
    for (domain, difficulty), s in sorted(stats.items()):
        n = s["total"]
        report[f"{domain}_{difficulty}"] = {
            "total": n,
            "year_pct": round(s["has_year"] / n * 100, 1) if n else 0,
            "month_day_pct": round(s["has_month_day"] / n * 100, 1) if n else 0,
            "time_pct": round(s["has_time"] / n * 100, 1) if n else 0,
            "gender_pct": round(s["has_gender"] / n * 100, 1) if n else 0,
            "city_pct": round(s["has_city"] / n * 100, 1) if n else 0,
            "avg_score": round(sum(s["scores"]) / n, 2) if n else 0,
        }
    return report


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------

def generate_batch(client, domain, difficulty, count):
    """Generate a batch of queries for one domain x difficulty using JSON mode."""
    sys_prompt, user_prompt = _make_prompt(domain, difficulty, count)

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    text = call_with_retry(client, messages, model=MODEL_PRO)
    items = parse_response(text)
    if items is None:
        sys.stderr.write(f"    [PARSE-FAIL] Could not parse response for {domain}_{difficulty}\n")
        sys.stderr.flush()
        return []

    items = validate_items(items, domain, difficulty)
    return items[:count]


def generate_batch_plaintext(client, domain, difficulty, count):
    """Fallback: generate queries as plain text numbered list."""
    cfg = DOMAIN_CONFIGS[domain]
    domain_cn = cfg["cn"]
    diff_label = {"L1": "基础", "L2": "进阶", "L3": "综合"}[difficulty]
    guide = _get_domain_guide(domain, difficulty)

    prompt = f"""请为{domain_cn}领域生成{count}条{diff_label}难度的用户查询。

## 查询风格指导
{guide}

{"## 强制要求（必须遵守）\n每条查询必须包含具体的出生年月日时和性别，让算命系统可以直接排八字。" if difficulty in ("L2", "L3") else ""}

## 质量要求
1. 语言自然真实，像用户在问算命师傅
2. 混合不同性别、年龄段(20-60代)、职业和地域
3. 每一条都不同，避免重复
4. 输出纯文本，每行一条，用"1. " "2. " "3. "开头
5. 不要出现额外的说明文字，只要查询列表
6. L2和L3必须在每条查询中嵌入完整的出生年月日时和性别信息

例：
{"1. 女，1992年农历八月初八午时生，深圳做会计的，最近三年财运不好，什么时候能好转？" if difficulty in ("L2", "L3") else "1. 我属马的，今年本命年运气怎么样？"}

请输出{count}条查询："""

    messages = [{"role": "user", "content": prompt}]
    text = call_with_retry(client, messages, model=MODEL_PRO)

    lines = text.strip().split("\n")
    queries = []
    for line in lines:
        line = line.strip()
        match = re.match(r'^\d+[\.\、\)]\s*(.*?)$', line)
        if match:
            q = match.group(1).strip()
            if len(q) > 5:
                queries.append(q)

    if not queries:
        sys.stderr.write(f"    [PT-FAIL] Could not extract plaintext queries "
                         f"for {domain}_{difficulty}\n")
        sys.stderr.flush()
        return []

    return queries[:count]


def make_items_from_plaintext(queries, domain, difficulty):
    """Wrap plaintext queries into benchmark JSON items."""
    dims_pool = {
        "bazi":      ["财运", "事业", "感情", "婚姻", "健康", "学业", "流年运势", "大运"],
        "ziwei":     ["财运", "事业", "感情", "健康", "迁移", "官禄", "夫妻", "福德"],
        "fengshui":  ["财运", "事业", "健康", "家庭", "学业", "官非", "人际关系"],
        "dream":     ["事业", "感情", "健康", "财运", "家庭", "心理", "人际关系"],
        "mianxiang": ["事业", "财运", "感情", "健康", "性格", "婚姻", "子女"],
        "qimen":     ["事业", "财运", "出行", "决策", "合作", "考试", "诉讼"],
        "xingming":  ["事业", "财运", "健康", "婚姻", "学业", "改名", "人际关系"],
        "zeri":      ["婚姻", "搬家", "开业", "动土", "出行", "签约", "入学"],
    }
    dims = dims_pool.get(domain, ["事业", "财运", "感情"])
    random.seed(hash(f"{domain}_{difficulty}"))

    items = []
    for i, q in enumerate(queries):
        n_dims = min(len(dims), random.randint(2, 3))
        selected = random.sample(dims, n_dims)
        items.append({
            "id": f"{domain}_{difficulty}_{i + 1:03d}",
            "domain": domain,
            "difficulty": difficulty,
            "query": q,
            "expected_dimensions": selected,
            "reference_answer_hints": [
                "需要分析相关维度运势",
                "结合用户具体信息判断",
                "注意流年大运影响",
            ],
        })
    return items


# ---------------------------------------------------------------------------
# Main generation orchestration
# ---------------------------------------------------------------------------

def _log(msg):
    print(msg, flush=True)


def _load_existing(path=None):
    """Load existing queries from disk (for resuming)."""
    if path is None:
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


def _save_queries(queries, path=None):
    """Write queries to JSONL atomically."""
    if path is None:
        path = SCRIPT_DIR / OUTPUT_FILE
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _print_dry_run_samples(domain, difficulty, items):
    """Print sample queries for dry-run mode."""
    _log(f"\n  {'─' * 60}")
    _log(f"  {domain}_{difficulty} — {len(items)} samples:")
    for it in items:
        q = it["query"]
        # Highlight birth info presence
        check = check_birth_info(q)
        score = birth_info_score(check)
        birth_tag = f" [BIRTH-INFO:{score}/5]" if score > 0 else " [⚠️ NO BIRTH INFO]"
        _log(f"    [{it['id']}] {q[:120]}{birth_tag}")


def _print_summary(queries):
    """Print a summary table of generated queries per domain x difficulty."""
    counts = {}
    for q in queries:
        d, diff = q["domain"], q["difficulty"]
        counts[(d, diff)] = counts.get((d, diff), 0) + 1

    _log(f"\n{'=' * 70}")
    _log(f"  Benchmark v2 Queries — Summary")
    _log(f"{'=' * 70}")
    header = f"  {'Domain':<14} {'L1':<8} {'L2':<8} {'L3':<8} {'Total':<8}"
    _log(header)
    _log(f"  {'─' * 46}")

    grand_total = 0
    all_ok = True
    for domain in sorted(DOMAIN_CONFIGS.keys()):
        cfg = DOMAIN_CONFIGS[domain]
        l1 = counts.get((domain, "L1"), 0)
        l2 = counts.get((domain, "L2"), 0)
        l3 = counts.get((domain, "L3"), 0)
        total = l1 + l2 + l3
        exp = cfg["l1"] + cfg["l2"] + cfg["l3"]
        ok = total == exp
        if not ok:
            all_ok = False
        status = "" if ok else f"[exp {exp}]"
        _log(f"  {domain:<14} {l1:<8} {l2:<8} {l3:<8} {total:<8} {status}")
        grand_total += total

    _log(f"  {'─' * 46}")
    _log(f"  {'TOTAL':<14} {'':<8} {'':<8} {'':<8} {grand_total:<8}")
    _log()
    if all_ok and grand_total == 5000:
        _log(f"  All counts match targets. Total: {grand_total} (目标 5000)")
    else:
        _log(f"  Some counts deviate from targets. Total: {grand_total} (目标 5000)")

    # Run birth info validation
    _log()
    bio = validate_birth_coverage(queries)
    _log(f"{'─' * 70}")
    _log(f"  Birth Info Coverage Report")
    _log(f"{'─' * 70}")
    _log(f"  {'Combo':<20} {'Year%':<8} {'MonthDay%':<10} {'Time%':<8} "
         f"{'Gender%':<10} {'City%':<8} {'Avg Score':<10}")
    _log(f"  {'─' * 74}")
    total_score = 0
    total_count = 0
    for key, s in sorted(bio.items()):
        _log(f"  {key:<20} {s['year_pct']:<8} {s['month_day_pct']:<10} "
             f"{s['time_pct']:<8} {s['gender_pct']:<10} {s['city_pct']:<8} "
             f"{s['avg_score']:<10}")
        total_score += s['avg_score'] * s['total']
        total_count += s['total']
    overall = round(total_score / total_count, 2) if total_count else 0
    _log(f"  {'─' * 74}")
    _log(f"  Overall average birth info score: {overall}/5")

    # Highlight problem areas
    _log()
    problems = []
    for key, s in sorted(bio.items()):
        difficulty = key.split("_")[1]
        if difficulty in ("L2", "L3") and s["year_pct"] < 95:
            problems.append(f"    {key}: year coverage only {s['year_pct']}%")
        if difficulty in ("L2", "L3") and s["month_day_pct"] < 90:
            problems.append(f"    {key}: month/day coverage only {s['month_day_pct']}%")
        if difficulty in ("L3",) and s["time_pct"] < 90:
            problems.append(f"    {key}: time coverage only {s['time_pct']}% (L3 must have time)")
        if difficulty in ("L2", "L3") and s["gender_pct"] < 95:
            problems.append(f"    {key}: gender coverage only {s['gender_pct']}%")
    if problems:
        _log(f"  ⚠️  Problem areas:")
        for p in problems:
            _log(f"  {p}")
    else:
        _log(f"  ✅ All L2/L3 queries have adequate birth info coverage.")


def generate_all(dry_run=False, single_domain=None, resume=False):
    """Generate all benchmark queries across domains and difficulties."""
    client = make_http_client()

    # Load existing if resuming
    all_queries = _load_existing() if resume else []
    if resume and all_queries:
        _log(f"  Resumed with {len(all_queries)} existing queries")

    domains_to_process = (
        [single_domain] if single_domain
        else sorted(DOMAIN_CONFIGS.keys())
    )

    for domain in domains_to_process:
        if domain not in DOMAIN_CONFIGS:
            _log(f"  [SKIP] Unknown domain: {domain}")
            continue
        cfg = DOMAIN_CONFIGS[domain]

        for diff_key in ("L1", "L2", "L3"):
            count_key = diff_key.lower()
            target = cfg[count_key]
            if target == 0:
                continue

            label = f"{domain}_{diff_key}"

            # Check existing count
            existing_count = len([q for q in all_queries
                                  if q["domain"] == domain and q["difficulty"] == diff_key])
            if existing_count >= target and not dry_run:
                _log(f"  {label}: already {existing_count}/{target}, skipping")
                continue

            if dry_run:
                # Generate just 3-5 samples
                try:
                    items = generate_batch(client, domain, diff_key, 5)
                    if not items:
                        queries = generate_batch_plaintext(client, domain, diff_key, 5)
                        items = make_items_from_plaintext(queries, domain, diff_key)
                except Exception as exc:
                    items = []
                    _log(f"    ERROR — {exc}")
                _print_dry_run_samples(domain, diff_key, items)
                continue

            # Full generation
            _log(f"\n{'─' * 60}")
            _log(f"  {label}  →  target {target} (existing {existing_count})")
            generated = [q for q in all_queries
                         if q["domain"] == domain and q["difficulty"] == diff_key]

            for attempt in range(1, 101):  # up to 100 batches per combo
                remaining = target - len(generated)
                if remaining <= 0:
                    break
                to_gen = min(BATCH_SIZES[diff_key], remaining)
                try:
                    # Primary: JSON mode
                    items = generate_batch(client, domain, diff_key, to_gen)
                    if not items:
                        # Fallback: plaintext mode
                        queries = generate_batch_plaintext(client, domain, diff_key, to_gen)
                        items = make_items_from_plaintext(queries, domain, diff_key)
                    generated.extend(items)
                    _log(f"    batch {attempt:3d}: +{len(items):3d}  →  "
                         f"{len(generated):3d}/{target}")
                    time.sleep(1.0)  # polite rate limiting
                except Exception as exc:
                    _log(f"    batch {attempt:3d}: FAILED — {exc}")
                    time.sleep(5.0)
                    continue

            # Renumber IDs sequentially
            for i, item in enumerate(generated):
                item["id"] = f"{domain}_{diff_key}_{i + 1:03d}"

            # Replace old entries for this combo with new ones
            all_queries = [q for q in all_queries
                           if not (q["domain"] == domain and q["difficulty"] == diff_key)]
            all_queries.extend(generated)

            if len(generated) < target:
                _log(f"  [WARN] {label}: only got {len(generated)}/{target} "
                     f"({target - len(generated)} missing)")

            # Incremental save
            _save_queries(all_queries)
            _log(f"  Saved. Total so far: {len(all_queries)}")

    if not dry_run and all_queries:
        _save_queries(all_queries)
        _print_summary(all_queries)

    return all_queries


# ---------------------------------------------------------------------------
# Validation-only mode
# ---------------------------------------------------------------------------

def validate_only():
    """Validate birth info coverage in existing file without generating."""
    path = SCRIPT_DIR / OUTPUT_FILE
    if not path.exists():
        _log(f"  File not found: {path}")
        return

    queries = _load_existing(path)
    _log(f"  Loaded {len(queries)} queries from {OUTPUT_FILE}")

    # Breakdown
    counts = {}
    for q in queries:
        d, diff = q["domain"], q["difficulty"]
        counts[(d, diff)] = counts.get((d, diff), 0) + 1

    _log(f"\n{'=' * 70}")
    _log(f"  Benchmark v2 — Counts")
    _log(f"{'=' * 70}")
    for domain in sorted(DOMAIN_CONFIGS.keys()):
        cfg = DOMAIN_CONFIGS[domain]
        l1 = counts.get((domain, "L1"), 0)
        l2 = counts.get((domain, "L2"), 0)
        l3 = counts.get((domain, "L3"), 0)
        total = l1 + l2 + l3
        exp = cfg["l1"] + cfg["l2"] + cfg["l3"]
        status = "OK" if total == exp else f"MISMATCH (need {exp})"
        _log(f"  {domain:<12} L1:{l1:<4} L2:{l2:<4} L3:{l3:<4} Total:{total:<5} {status}")
    _log()

    # Birth info validation
    bio = validate_birth_coverage(queries)
    _log(f"{'─' * 70}")
    _log(f"  Birth Info Coverage Report")
    _log(f"{'─' * 70}")
    _log(f"  {'Combo':<20} {'Year%':<8} {'MonthDay%':<10} {'Time%':<8} "
         f"{'Gender%':<10} {'City%':<8} {'Avg Score':<10}")
    _log(f"  {'─' * 74}")
    total_score = 0
    total_count = 0
    for key, s in sorted(bio.items()):
        _log(f"  {key:<20} {s['year_pct']:<8} {s['month_day_pct']:<10} "
             f"{s['time_pct']:<8} {s['gender_pct']:<10} {s['city_pct']:<8} "
             f"{s['avg_score']:<10}")
        total_score += s['avg_score'] * s['total']
        total_count += s['total']
    overall = round(total_score / total_count, 2) if total_count else 0
    _log(f"  {'─' * 74}")
    _log(f"  Overall average birth info score: {overall}/5")

    # Summary
    problems = []
    for key, s in sorted(bio.items()):
        difficulty = key.split("_")[1]
        if difficulty in ("L2", "L3"):
            if s["year_pct"] < 90:
                problems.append(f"  ⚠️  {key}: year coverage only {s['year_pct']}%")
            if s["month_day_pct"] < 85:
                problems.append(f"  ⚠️  {key}: month/day coverage only {s['month_day_pct']}%")
            if s["gender_pct"] < 90:
                problems.append(f"  ⚠️  {key}: gender coverage only {s['gender_pct']}%")
        if difficulty == "L3":
            if s["time_pct"] < 85:
                problems.append(f"  ⚠️  {key}: time coverage only {s['time_pct']}% (L3 req)")
    if problems:
        _log(f"\n  {'─' * 70}")
        for p in problems:
            _log(p)
    else:
        _log(f"\n  ✅ All L2/L3 queries have adequate birth info coverage.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="易理明灯 Benchmark v2 — Generate 5000 queries with birth info"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print 5 samples per domain (no file output)")
    parser.add_argument("--resume", action="store_true",
                        help="Continue from existing benchmark_queries_v2.jsonl")
    parser.add_argument("--domain", type=str, default=None,
                        help="Generate only one domain (e.g. bazi)")
    parser.add_argument("--validate-only", action="store_true",
                        help="Validate birth info in existing file, don't generate")
    args = parser.parse_args()

    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)

    if args.validate_only:
        _log(f"╔{'═' * 58}╗")
        _log(f"║  易理明灯 — Benchmark v2 Validator")
        _log(f"╚{'═' * 58}╝")
        validate_only()
        return

    model_name = MODEL_PRO
    _log(f"╔{'═' * 58}╗")
    _log(f"║  易理明灯 — Benchmark v2 Generator")
    _log(f"║  Model:   {model_name}")
    _log(f"║  Output:  {SCRIPT_DIR / OUTPUT_FILE}")
    _log(f"║  Target:  5,000 queries (L1:500, L2:2500, L3:2000)")
    _log(f"╚{'═' * 58}╝")

    if args.dry_run:
        _log(f"\n  DRY-RUN mode: 5 samples per domain (no file output)\n")
    elif args.resume:
        _log(f"\n  RESUME mode: continuing from existing file\n")
    elif args.domain:
        _log(f"\n  Single-domain mode: {args.domain}\n")
    else:
        _log(f"\n  Full generation mode: all 8 domains\n")

    generate_all(dry_run=args.dry_run, single_domain=args.domain, resume=args.resume)

    _log(f"\nDone.")


if __name__ == "__main__":
    main()
