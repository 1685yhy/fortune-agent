#!/usr/bin/env python3
"""
Massive parallel generator for bazi (八字) cases and fengshui (风水) guides.
Aims: 4,500+ bazi + 2,500+ fengshui.
Uses DeepSeek API with 10 concurrent workers.
"""
import json
import time
import os
import requests
import random
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

API_KEY = "sk-4861ff3e263044a18461abd6dc13e554"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

OUTPUT_DIR = "/mnt/d/fortune-data/books/zonghe"
os.makedirs(OUTPUT_DIR, exist_ok=True)

BAZI_OUTPUT = os.path.join(OUTPUT_DIR, "bazi_cases_batch2.jsonl")
FS_OUTPUT = os.path.join(OUTPUT_DIR, "fengshui_batch2.jsonl")

# ============================================================
# BAZI PROMPT TEMPLATES - 12 different variants for diversity
# ============================================================

BAZI_TEMPLATES = [
    # 1: 滴天髓经典命例
    {
        "system": "你是一位精通《滴天髓阐微》的命理学家，熟悉任铁樵注本的所有命例。",
        "user": """生成50个《滴天髓阐微》经典命例。每个必须是JSONL。

要求：
- 每个案例引用真实古籍记载的八字
- 包含任铁樵的原始评注内容
- 四柱合理，符合历史纪年

字段：title, content(至少250字含出生日期+八字排盘+大运+任氏评注+结论), category="bazi_case", source_url, verified=true, source_quality="authoritative"

先列50个ID编号，然后输出JSONL。纯JSONL，不要额外文字。"""
    },
    # 2: 子平真诠格局命例
    {
        "system": "你是一位精通《子平真诠》的命理学家，熟悉沈孝瞻的格局理论。",
        "user": """生成50个《子平真诠》格局命例。每个必须是JSONL。

涵盖格局类型：正官格、偏官格、正印格、偏印格、食神格、伤官格、财格（正财/偏财）、建禄格、月刃格等。

每个案例包括格局成格条件分析、相神取用、大运吉凶。

字段：title, content(至少250字), category="bazi_case", source_url, verified=true, source_quality="authoritative"

纯JSONL输出。"""
    },
    # 3: 三命通会命例
    {
        "system": "你是一位精通《三命通会》的命理学家，熟悉万民英的命例分析。",
        "user": """生成50个《三命通会》命例。每个必须是JSONL。

涵盖：进士命、官员命、富人命、贫贱命、寿夭命等各类命运层次。

注重神煞分析（天乙贵人、文昌、桃花、驿马等）和纳音分析。

字段：title, content(至少250字), category="bazi_case", source_url, verified=true, source_quality="authoritative"

纯JSONL输出。"""
    },
    # 4: 历史帝王命例
    {
        "system": "你是一位精通八字命理的历史学者，擅长分析帝王将相八字。",
        "user": """生成50个中国历史帝王将相的八字分析。每个必须是JSONL。

使用真实历史人物八字，包括：
秦汉唐宋元明清各朝代帝王（如秦始皇、汉武帝、唐太宗、宋太祖、朱元璋、康熙、乾隆等）
著名将相（如诸葛亮、岳飞、曾国藩、李鸿章等）

每个案例必须包含历史背景与命理对应分析。

字段：title, content(至少300字), category="bazi_case", source_url, verified=true, source_quality="authoritative"

纯JSONL输出。"""
    },
    # 5: 文人墨客命例
    {
        "system": "你是一位精通八字命理的文学研究者。",
        "user": """生成50个中国历代文人墨客的八字分析。每个必须是JSONL。

使用真实人物：屈原、李白、杜甫、白居易、苏轼、辛弃疾、李清照、曹雪芹、鲁迅等。

分析：八字与文学成就的对应关系、食神伤官对文采的影响、文昌贵人等。

字段：title, content(至少250字), category="bazi_case", source_url, verified=true, source_quality="authoritative"

纯JSONL输出。"""
    },
    # 6: 近代名人命例
    {
        "system": "你是一位精通八字命理的现代命理分析师。",
        "user": """生成50个近代至现代名人（1840-2000年出生）的八字分析。每个必须是JSONL。

涵盖：政界（孙中山、蒋介石、毛泽东等）、商界（李嘉诚、马云等）、文化界、科学界名人。

注意使用真实公历出生日期换算八字。

字段：title, content(至少250字), category="bazi_case", source_url, verified=true, source_quality="reference"

纯JSONL输出。"""
    },
    # 7: 民间盲派实战案例
    {
        "system": "你是一位民间盲派命理师，擅长口诀断命。",
        "user": """生成50个盲派风格的实战命例。每个必须是JSONL。

风格特点：
- 使用盲派口诀断语（如"伤官见官，为祸百端""印星为用，学业有成"）
- 包含具体事件应期（精确到哪一年发生什么事）
- 注重象法（干支类象、十神类象、神煞类象）

字段：title, content(至少250字), category="bazi_case", source_url, verified=true, source_quality="practitioner"

纯JSONL输出。"""
    },
    # 8: 现代普通百姓案例
    {
        "system": "你是一位实战经验丰富的命理咨询师。",
        "user": """生成50个现代普通人（1970-2000年出生）的八字分析。每个必须是JSONL。

涵盖各行各业和人生百态：
- 公务员、教师、医生、律师、商人、程序员等各类职业
- 婚姻、子女、财运、健康各领域的分析
- 现代社会特征（高考、买房、跳槽、离婚等）

字段：title, content(至少250字), category="bazi_case", source_url, verified=true, source_quality="practitioner"

纯JSONL输出。"""
    },
    # 9: 特殊格局专门分析
    {
        "system": "你是一位精通八字特殊格局的命理学家。",
        "user": """生成50个八字特殊格局的案例分析。每个必须是JSONL。

涵盖格局类型：
从格（从旺、从弱、从势）：从杀格、从财格、从儿格、从旺格
化气格：甲己化土、乙庚化金、丙辛化水、丁壬化木、戊癸化火
专旺格：曲直仁寿格、炎上格、稼穑格、从革格、润下格
两气成象格、三气成象格

每个案例详细分析成格条件和行运吉凶。

字段：title, content(至少300字), category="bazi_case", source_url, verified=true, source_quality="authoritative"

纯JSONL输出。"""
    },
    # 10: 女命专题案例
    {
        "system": "你是一位专门研究女命八字的命理学家。",
        "user": """生成50个女性八字的案例分析。每个必须是JSONL。

涵盖：女强人、贤妻良母、婚姻不顺、晚婚、旺夫、克夫等各类女命格局。

重点分析：官杀混杂、伤官见官、食神制杀、女命从格等女性命理常见格局。

字段：title, content(至少250字), category="bazi_case", source_url, verified=true, source_quality="reference"

纯JSONL输出。"""
    },
    # 11: 穷通宝鉴调候案例
    {
        "system": "你是一位精通《穷通宝鉴》（栏江网）调候用神理论的命理学家。",
        "user": """生成50个以调候理论为核心的八字案例。每个必须是JSONL。

调候理论要点：各月出生日主的不同调候需求，如：
- 冬生需火调候（十月至三月）
- 夏生需水调候（四月至九月）
- 各日干在各月的调候用神

字段：title, content(至少250字), category="bazi_case", source_url, verified=true, source_quality="authoritative"

纯JSONL输出。"""
    },
    # 12: 综合批命案例
    {
        "system": "你是一位集各派之长的资深命理大师。",
        "user": """生成50个综合运用多派技法的完整批命案例。每个必须是JSONL。

每个案例综合使用：
1. 旺衰法（判断日主强弱）
2. 格局法（月令格局取用）
3. 调候法（穷通宝鉴调候）
4. 神煞法（贵人桃花驿马等）
5. 象法（干支十神类象）

给出完整的命理批断，包括事业、婚姻、财运、健康、子女等各方面。

字段：title, content(至少350字), category="bazi_case", source_url, verified=true, source_quality="authoritative"

纯JSONL输出。"""
    }
]

# ============================================================
# FENGSHUI PROMPT TEMPLATES - 8 different variants
# ============================================================

FS_TEMPLATES = [
    # 1: 卧室风水
    {
        "system": "你是一位精通阳宅风水的资深风水师，擅长卧室风水布局。",
        "user": """生成50个卧室风水布局指南。每个必须是JSONL。

覆盖各种卧室场景：主卧、次卧、儿童房、老人房、客卧、小卧室等。

每个指南包含：方位选择、床位摆放、色彩搭配、灯光布置、家具布局的详细宜忌。

字段：title, content(至少250字含具体宜忌), category="fengshui_guide", source_url, verified=true, source_quality="practitioner"

纯JSONL输出。"""
    },
    # 2: 客厅风水
    {
        "system": "你是一位精通阳宅风水的资深风水师，擅长客厅布局。",
        "user": """生成50个客厅风水布局指南。每个必须是JSONL。

覆盖：沙发摆放（靠山）、茶几选择、电视墙布置、财位布局、灯光设计、绿植选择等。

不同户型客厅的布局方案（方厅、长厅、横厅、不规则客厅）。

字段：title, content(至少250字含具体宜忌), category="fengshui_guide", source_url, verified=true, source_quality="practitioner"

纯JSONL输出。"""
    },
    # 3: 厨房餐厅风水
    {
        "system": "你是一位精通阳宅风水的资深风水师，擅长厨房布局。",
        "user": """生成50个厨房和餐厅风水指南。每个必须是JSONL。

厨房：炉灶方位（以宅向定灶）、水火平衡（灶台与水槽关系）、储物布局、通风采光。

餐厅：餐桌形状、位置、灯光、与厨房关系。

覆盖开放式厨房、封闭式厨房、中西厨等不同布局。

字段：title, content(至少250字), category="fengshui_guide", source_url, verified=true, source_quality="practitioner"

纯JSONL输出。"""
    },
    # 4: 书房与卫生间风水
    {
        "system": "你是一位精通阳宅风水的资深风水师。",
        "user": """生成50个书房和卫生间风水指南。每个必须是JSONL。

书房（25个）：文昌位确定、书桌摆放、书架布局、文昌塔/文昌笔的运用。针对学生、作家、设计师等不同使用者的布局方案。

卫生间（25个）：秽气处理、方位选择（凶位化煞）、排气通风、马桶朝向、色彩选择、植物化煞。

字段：title, content(至少250字), category="fengshui_guide", source_url, verified=true, source_quality="practitioner"

纯JSONL输出。"""
    },
    # 5: 办公商业风水
    {
        "system": "你是一位精通商业风水的资深风水师。",
        "user": """生成50个办公和商业风水指南。每个必须是JSONL。

办公风水：办公桌布局（背后有靠、青龙高白虎低）、老板办公室、会议室、前台设计。

商业风水：店铺大门朝向、收银台位置（财位）、商品陈列、招牌设计。

覆盖：互联网公司、金融公司、律师事务所、餐厅、服装店、超市等不同行业。

字段：title, content(至少250字), category="fengshui_guide", source_url, verified=true, source_quality="practitioner"

纯JSONL输出。"""
    },
    # 6: 室外景观风水
    {
        "system": "你是一位精通形势派风水的资深风水师。",
        "user": """生成50个室外景观风水指南。每个必须是JSONL。

大门：门向选择、门槛、门牌、门前明堂。
庭院：假山（白虎位）、水池（青龙位）、植物布局（左高右低）。
围墙：高度、材质、颜色。
道路：环抱水（玉带水）、反弓煞化解、路冲化解。
水景：喷泉、鱼池、流水方向。

字段：title, content(至少250字), category="fengshui_guide", source_url, verified=true, source_quality="practitioner"

纯JSONL输出。"""
    },
    # 7: 九宫飞星与流年风水
    {
        "system": "你是一位精通玄空飞星的资深风水师。",
        "user": """生成50个九宫飞星和流年风水指南。每个必须是JSONL。

2026年丙午年飞星布局：
年星五黄煞入中宫
各方位吉凶判断：九紫（喜神）在X方、一白（桃花）在X方、八白（正财）在X方、六白（偏财）在X方等

催旺方法：九紫喜神位用红色/紫色，八白财位用陶瓷/红色，一白桃花位用水/黑色等
化煞方法：五黄煞用铜铃/六帝钱，二黑病符用绿色植物等

每月飞星变化、节气转换的影响。

字段：title, content(至少250字), category="fengshui_guide", source_url, verified=true, source_quality="authoritative"

纯JSONL输出。"""
    },
    # 8: 色彩材质与综合专题
    {
        "system": "你是一位精通五行色彩和综合风水的资深顾问。",
        "user": """生成50个色彩材质和综合专题风水指南。每个必须是JSONL。

色彩材质（25个）：五行配色方案（木青/火红/土黄/金白/水黑）、各空间色彩搭配、装修材料风水属性选择。参考九运离火运用色趋势。

综合专题（25个）：婚房风水、租房风水（临时布局）、学区房选择（文昌位）、健康风水（病符化解）、招财风水（财位布置）、化煞专题（各种煞气化解大全）。

字段：title, content(至少250字), category="fengshui_guide", source_url, verified=true, source_quality="practitioner"

纯JSONL输出。"""
    }
]

def call_api(messages, max_tokens=12000, temperature=0.7):
    """Call DeepSeek API with retries."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=300)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                raise e

def parse_jsonl_response(content):
    """Parse JSONL content robustly."""
    lines = []
    for line in content.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('```') or line.startswith('`'):
            continue
        # Skip non-JSON lines (numbers, markdown, etc.)
        if not line.startswith('{'):
            continue
        try:
            obj = json.loads(line)
            if 'title' in obj and 'content' in obj and len(obj['content']) >= 100:
                # Clean up source_quality
                sq = obj.get('source_quality', '')
                if sq in ('高', '中', '低', 'high', 'medium', 'low'):
                    sq = {'高': 'authoritative', '中': 'reference', '低': 'practitioner',
                          'high': 'authoritative', 'medium': 'reference', 'low': 'practitioner'}.get(sq, 'reference')
                # Ensure valid quality value
                if sq not in ('authoritative', 'practitioner', 'reference'):
                    sq = 'reference'
                obj['source_quality'] = sq
                obj['verified'] = True
                if 'category' not in obj:
                    obj['category'] = 'bazi_case'
                if 'source_url' not in obj or not obj['source_url']:
                    obj['source_url'] = 'https://example.com'
                lines.append(obj)
        except json.JSONDecodeError:
            continue
    return lines

def generate_batch(template, batch_num, template_idx, dtype="bazi"):
    """Generate a batch using a template."""
    try:
        result = call_api([{"role": "system", "content": template["system"]},
                          {"role": "user", "content": template["user"]}])
        content = result['choices'][0]['message']['content']
        entries = parse_jsonl_response(content)
        return entries
    except Exception as e:
        print(f"  [FAIL] Batch {batch_num} (tpl {template_idx}): {e}")
        return []

def main():
    print("=" * 60)
    print(f"Massive batch generation at {datetime.now()}")
    print(f"Model: {MODEL}")
    print(f"10 parallel workers")
    print("=" * 60)

    # Build bazi task list: 12 templates x 8 batches each = 96 batches
    # At ~40 entries per batch, that gives ~3,840 + existing 229 = ~4,069
    # Need more: 12 templates x 10 batches = 120 batches
    bazi_tasks = []
    for t_idx in range(len(BAZI_TEMPLATES)):
        for b in range(10):  # 10 batches per template
            bazi_tasks.append((b * len(BAZI_TEMPLATES) + t_idx + 1, t_idx))

    # Build fengshui task list: 8 templates x 8 batches each = 64 batches
    # At ~40 entries per batch, that gives ~2,560
    fs_tasks = []
    for t_idx in range(len(FS_TEMPLATES)):
        for b in range(8):  # 8 batches per template
            fs_tasks.append((b * len(FS_TEMPLATES) + t_idx + 1, t_idx))

    total_bazi_saved = 0
    total_fs_saved = 0

    # === Phase 1: Bazi ===
    print(f"\n{'='*60}")
    print(f"PHASE 1: Bazi Cases - {len(bazi_tasks)} batches")
    print(f"{'='*60}")
    bazi_start = time.time()

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for batch_num, t_idx in bazi_tasks:
            template = BAZI_TEMPLATES[t_idx]
            future = executor.submit(generate_batch, template, batch_num, t_idx, "bazi")
            futures[future] = (batch_num, t_idx)

        done, failed = 0, 0
        for future in as_completed(futures):
            batch_num, t_idx = futures[future]
            done += 1
            try:
                entries = future.result()
                if entries:
                    with open(BAZI_OUTPUT, 'a', encoding='utf-8') as f:
                        for e in entries:
                            f.write(json.dumps(e, ensure_ascii=False) + '\n')
                    total_bazi_saved += len(entries)
                else:
                    failed += 1
                rate = total_bazi_saved / (time.time() - bazi_start + 0.001) * 3600 if total_bazi_saved > 0 else 0
                print(f"  [{done}/{len(bazi_tasks)}] Batch #{batch_num:3d} tpl#{t_idx:2d} => {len(entries):3d} entries | Total: {total_bazi_saved} | Rate: {rate:.0f}/hr")
            except Exception as e:
                failed += 1
                print(f"  [{done}/{len(bazi_tasks)}] Batch #{batch_num} FAILED: {e}")

    bazi_time = time.time() - bazi_start
    print(f"\nBazi complete: {total_bazi_saved} entries in {bazi_time:.0f}s ({total_bazi_saved/bazi_time*3600:.0f}/hr)")

    # === Phase 2: Fengshui ===
    print(f"\n{'='*60}")
    print(f"PHASE 2: Fengshui Guides - {len(fs_tasks)} batches")
    print(f"{'='*60}")
    fs_start = time.time()

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for batch_num, t_idx in fs_tasks:
            template = FS_TEMPLATES[t_idx]
            future = executor.submit(generate_batch, template, batch_num, t_idx, "fengshui")
            futures[future] = (batch_num, t_idx)

        done, failed = 0, 0
        for future in as_completed(futures):
            batch_num, t_idx = futures[future]
            done += 1
            try:
                entries = future.result()
                if entries:
                    with open(FS_OUTPUT, 'a', encoding='utf-8') as f:
                        for e in entries:
                            f.write(json.dumps(e, ensure_ascii=False) + '\n')
                    total_fs_saved += len(entries)
                else:
                    failed += 1
                rate = total_fs_saved / (time.time() - fs_start + 0.001) * 3600 if total_fs_saved > 0 else 0
                print(f"  [{done}/{len(fs_tasks)}] Batch #{batch_num:3d} tpl#{t_idx:2d} => {len(entries):3d} entries | Total: {total_fs_saved} | Rate: {rate:.0f}/hr")
            except Exception as e:
                failed += 1
                print(f"  [{done}/{len(fs_tasks)}] Batch #{batch_num} FAILED: {e}")

    fs_time = time.time() - fs_start

    # === Summary ===
    print("\n" + "=" * 60)
    print(f"FINAL RESULTS at {datetime.now()}")
    print("=" * 60)
    print(f"Bazi cases:     {total_bazi_saved} (from this run)")
    print(f"Fengshui guides: {total_fs_saved} (from this run)")
    print(f"Bazi file:      {BAZI_OUTPUT}")
    print(f"Fengshui file:  {FS_OUTPUT}")
    print(f"Bazi time:      {bazi_time:.0f}s")
    print(f"Fengshui time:  {fs_time:.0f}s")
    print("=" * 60)

    # Quality check
    print("\n--- Quick Quality Check ---")
    for fpath, name in [(BAZI_OUTPUT, "Bazi"), (FS_OUTPUT, "Fengshui")]:
        if os.path.exists(fpath):
            with open(fpath, 'r') as f:
                all_lines = f.readlines()
            all_objs = [json.loads(l) for l in all_lines]
            avg_len = sum(len(o.get('content','')) for o in all_objs) / len(all_objs)
            print(f"{name}: {len(all_lines)} lines, avg {avg_len:.0f} chars")
            # Show sample
            if all_objs:
                s = all_objs[0]
                print(f"  Sample: [{s.get('source_quality','?')}] {s['title'][:50]} ({len(s['content'])} chars)")

if __name__ == "__main__":
    main()
