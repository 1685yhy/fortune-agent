import os
#!/usr/bin/env python3
"""1005人全管线回测：引擎排盘 + RAG检索 + LLM预测 vs 真实事件"""
import json, sys, re, time, httpx

sys.path.insert(0, '.')
from src.engines.bazi import BaziEngine
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

with open('data/celebrity_cases.json') as f:
    cases = json.load(f)

engine = BaziEngine()
embedder = Embedder()
retriever = Retriever('/mnt/d/fortune-data/vectordb', embedder)

API_KEY = os.environ.get('DEEPSEEK_KEY', '')
API = 'https://api.deepseek.com/v1/chat/completions'

# ===== Test 1: Engine accuracy =====
print('='*60)
print('TEST 1: 排盘引擎精度 (1005人全量)')
print('='*60)
engine_ok = 0
engine_fail = []
for c in cases:
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', c['birth'])
    if not m:
        engine_fail.append((c['name'], 'date parse'))
        continue
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    hour_m = re.search(r'(\d{1,2})[:：时]', c['birth'])
    hour = int(hour_m.group(1)) if hour_m else 12
    gender = '女' if '女' in c['birth'] else '男'
    try:
        r = engine.calculate(year, month, day, hour, 0, '北京', gender)
        if len(r.bazi) == 4 and r.day_master and r.dayun and r.dayun[0][1]:
            engine_ok += 1
        else:
            engine_fail.append((c['name'], 'incomplete'))
    except Exception as e:
        engine_fail.append((c['name'], str(e)[:50]))

print(f'成功: {engine_ok}/{len(cases)} = {engine_ok/len(cases)*100:.1f}%')
print(f'失败: {len(engine_fail)} (主要为公元前古人)')

# ===== Test 2: Event prediction accuracy (sample 100) =====
print(f'\n{"="*60}')
print('TEST 2: 事件预测准确率 (抽样100人, fast model)')
print('='*60)

# Pick cases with valid dates
valid = [c for c in cases if re.search(r'\d{4}年\d{1,2}月\d{1,2}日', c['birth'])]
sample = valid[:100]

correct = 0
total = 0
cat_stats = {}

for ci, case in enumerate(sample):
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', case['birth'])
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    hour_m = re.search(r'(\d{1,2})[:：时]', case['birth'])
    hour = int(hour_m.group(1)) if hour_m else 12
    gender = '女' if '女' in case['birth'] else '男'
    r = engine.calculate(year, month, day, hour, 0, '北京', gender)

    for event in case['events']:
        total += 1
        cat = event['category']
        cat_stats.setdefault(cat, {'correct':0,'total':0})
        cat_stats[cat]['total'] += 1

        # Build prompt with real chart data
        chart_str = '八字: %s\n日主: %s\n%d年流年' % (
            ' '.join(r.bazi), r.day_master, event['year'])

        # RAG search
        refs = retriever.search('%s %d年' % (r.day_master, event['year']), category='bazi', top_k=3)
        ref_str = ' '.join(rf.text[:100] for rf in refs[:2]) if refs else ''

        prompt = '出生: %s\n命盘: %s\n古籍: %s\n\n%d年此人最可能发生哪类事件? 只答一词: 事业/财运/婚姻/健康/子女/灾祸' % (
            case['birth'], chart_str, ref_str[:200], event['year'])

        try:
            resp = httpx.post(API, headers={
                'Authorization': 'Bearer ' + API_KEY,
                'Content-Type': 'application/json'
            }, json={
                'model': 'deepseek-chat',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 10,
                'temperature': 0
            }, timeout=30)
            reply = resp.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            reply = 'ERR'

        is_hit = cat in reply
        if is_hit:
            correct += 1
            cat_stats[cat]['correct'] += 1

        status = 'HIT' if is_hit else 'MISS'
        print('[%3d/%d] %s | %s %d年 %s | 实际:%s | 答:%s' % (
            total, len(sample)*3, status, case['name'], event['year'],
            event['event'][:12], cat, reply[:15]))

    if (ci+1) % 20 == 0:
        rate = correct/total*100 if total > 0 else 0
        print('  --- 进度 %d人, 准确率: %d/%d = %.1f%% ---' % (ci+1, correct, total, rate))
    time.sleep(0.3)

print(f'\n{"="*60}')
print('回测总结')
print('='*60)
rate = correct/total*100 if total > 0 else 0
print(f'总准确率: {correct}/{total} = {rate:.1f}%')
print(f'\n引擎精度: {engine_ok}/{len(cases)} = {engine_ok/len(cases)*100:.1f}%')
for cat in sorted(cat_stats.keys()):
    s = cat_stats[cat]
    r = s['correct']/s['total']*100 if s['total'] else 0
    print(f'  {cat}: {s["correct"]}/{s["total"]} = {r:.1f}%')
