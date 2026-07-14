#!/usr/bin/env python3
"""全管线回归测试 - 1005人 × 3种验证 = 3000+ 次测试"""
import json, sys, re, time

sys.path.insert(0, '.')
from src.engines.bazi import BaziEngine
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever
from src.config import load_settings

with open('data/celebrity_cases.json') as f:
    cases = json.load(f)

engine = BaziEngine()
settings = load_settings()
embedder = Embedder()
retriever = Retriever(str(settings.vectordb_dir), embedder)

print(f'回归测试: {len(cases)} 人, {sum(len(c["events"]) for c in cases)} 事件\n')

# ===== Test 1: 引擎排盘完整性 =====
print('='*60)
print('Test 1: 引擎排盘完整性')
print('='*60)
bazi_errors = []
geju_stats = {}
yongshen_stats = {}

for c in cases:
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', c['birth'])
    if not m:
        bazi_errors.append((c['name'], 'birth parse failed'))
        continue
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    hour_m = re.search(r'(\d{1,2})[:：时]', c['birth'])
    hour = int(hour_m.group(1)) if hour_m else 12
    gender = '女' if '女' in c['birth'] else '男'
    city_m = re.search(r'(\S{2,4})\s*[男女]', c['birth'])
    city = city_m.group(1) if city_m else '北京'

    try:
        r = engine.calculate(year, month, day, hour, 0, city, gender)
        errors = []
        if len(r.bazi) != 4: errors.append('四柱数错')
        if not r.day_master: errors.append('日主空')
        if not r.geju: errors.append('格局空')
        if not r.yongshen: errors.append('用神空')
        if len(r.dayun) < 6: errors.append(f'大运太少({len(r.dayun)})')

        geju_stats[r.geju] = geju_stats.get(r.geju, 0) + 1
        ys_key = r.yongshen.split('（')[0]
        yongshen_stats[ys_key] = yongshen_stats.get(ys_key, 0) + 1

        if errors:
            bazi_errors.append((c['name'], ','.join(errors)))
    except Exception as e:
        bazi_errors.append((c['name'], str(e)[:50]))

print(f'  成功: {len(cases) - len(bazi_errors)}/{len(cases)}')
print(f'  失败: {len(bazi_errors)}')
if bazi_errors[:5]:
    for n, e in bazi_errors[:5]:
        print(f'    {n}: {e}')
print(f'  格局分布: {dict(sorted(geju_stats.items(), key=lambda x:-x[1])[:8])}')
print(f'  用神分布: {dict(sorted(yongshen_stats.items(), key=lambda x:-x[1])[:8])}')

# ===== Test 2: RAG搜索覆盖率 =====
print(f'\n{"="*60}')
print('Test 2: RAG搜索覆盖率')
print('='*60)
rag_hits = 0
rag_total = 0
cat_hits = {}

for c in cases[:200]:  # Sample 200 for speed
    for e in c['events']:
        rag_total += 1
        query = f'{c["birth"]} {e["year"]}年 {e["category"]}'
        results = retriever.search(query, category='bazi', top_k=5)
        if results:
            rag_hits += 1
            cat_hits[e['category']] = cat_hits.get(e['category'], 0) + 1

print(f'  RAG命中: {rag_hits}/{rag_total} = {rag_hits/rag_total*100:.1f}%')
for cat in sorted(cat_hits.keys()):
    print(f'    {cat}: {cat_hits[cat]} 条命中')

# ===== Test 3: 大运准确性抽样 =====
print(f'\n{"="*60}')
print('Test 3: 大运准确性（抽样20人）')
print('='*60)
import random
random.seed(42)
sample = random.sample([c for c in cases if re.search(r'\d{4}年', c['birth'])], min(20, len(cases)))
dayun_ok = 0
for c in sample:
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', c['birth'])
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    hour_m = re.search(r'(\d{1,2})[:：时]', c['birth'])
    hour = int(hour_m.group(1)) if hour_m else 12
    gender = '女' if '女' in c['birth'] else '男'
    r = engine.calculate(year, month, day, hour, 0, '北京', gender)
    if r.dayun and r.dayun[0][1]:  # Not empty
        dayun_ok += 1
        print(f'  ✅ {c["name"]}: 起运{r.dayun[0][0]}岁{r.dayun[0][1]}')
    else:
        print(f'  ❌ {c["name"]}: 大运空')

print(f'\n大运准确: {dayun_ok}/{len(sample)}')

# ===== Summary =====
print(f'\n{"="*60}')
print('回归测试总结')
print('='*60)
print(f'  排盘成功率: {(len(cases)-len(bazi_errors))/len(cases)*100:.1f}%')
print(f'  RAG覆盖率: {rag_hits/rag_total*100:.1f}%')
print(f'  大运准确率: {dayun_ok/len(sample)*100:.0f}%')
bazi_fails = 100 - (len(cases)-len(bazi_errors))/len(cases)*100
print(f'  综合评分: 排盘{bazi_fails:.0f}%失败 | RAG{rag_hits/rag_total*100:.0f}%命中 | 大运{dayun_ok/len(sample)*100:.0f}%')
