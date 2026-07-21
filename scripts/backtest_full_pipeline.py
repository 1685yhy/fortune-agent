import os
#!/usr/bin/env python3
"""全管线回测：引擎 + RAG + V4 Pro — 模拟真实用户对话"""
import json, sys, re, time, httpx

sys.path.insert(0, '.')
from src.engines.bazi import BaziEngine
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever
from src.llm.prompts import SYSTEM_PROMPT

with open('data/celebrity_cases.json') as f:
    cases = json.load(f)

engine = BaziEngine()
embedder = Embedder()
retriever = Retriever('/mnt/d/fortune-data/vectordb', embedder)
API_KEY = os.environ.get('DEEPSEEK_KEY', '')
API = 'https://api.deepseek.com/v1/chat/completions'

# Filter valid cases (skip BC dates)
valid = [c for c in cases if re.search(r'\d{4}年\d{1,2}月\d{1,2}日', c['birth'])]
# Run first 200
sample = valid[:200]

print(f'全管线回测: {len(sample)}人, ~{sum(len(c["events"]) for c in sample)}事件\n')

correct = 0
total = 0
cat_stats = {}

for ci, case in enumerate(sample):
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', case['birth'])
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    hour_m = re.search(r'(\d{1,2})[:：时]', case['birth'])
    hour = int(hour_m.group(1)) if hour_m else 12
    gender = '女' if '女' in case['birth'] else '男'

    # Full engine calculation
    r = engine.calculate(year, month, day, hour, 0, '北京', gender)
    chart = '八字: %s | 日主: %s | 格局: %s | 用神: %s | 大运: %s' % (
        ' '.join(r.bazi), r.day_master, r.geju, r.yongshen.split('（')[0],
        ' '.join('%d岁%s' % (a,g) for a,g in r.dayun[:5]))

    # RAG search
    refs = retriever.search('%s 流年运势' % r.day_master, category='bazi', top_k=5)
    ref_text = '\n'.join('%d. %s' % (j+1, rf.text[:150]) for j, rf in enumerate(refs[:3]))

    for event in case['events']:
        total += 1
        cat = event['category']
        cat_stats.setdefault(cat, {'correct':0,'total':0})
        cat_stats[cat]['total'] += 1

        # Full V4 Pro analysis — same prompt as real user
        prompt = f"""请基于命理分析此人{event['year']}年运势。

命盘: {chart}
古籍参考: {ref_text if ref_text else '无'}
事件: {event['event']}

请分析此年运势，判断哪方面最突出（事业/财运/婚姻/健康/子女/灾祸），最后一句只答一个词。"""

        try:
            resp = httpx.post(API, headers={
                'Authorization': 'Bearer ' + API_KEY,
                'Content-Type': 'application/json'
            }, json={
                'model': 'deepseek-v4-pro',
                'messages': [
                    {'role': 'system', 'content': SYSTEM_PROMPT[:500]},  # 精简版prompt
                    {'role': 'user', 'content': prompt}
                ],
                'max_tokens': 300,
                'temperature': 0.3
            }, timeout=120.0)
            reply = resp.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            reply = 'ERR: %s' % str(e)[:50]

        is_hit = cat in reply
        if is_hit:
            correct += 1
            cat_stats[cat]['correct'] += 1

        status = 'HIT' if is_hit else 'MISS'
        last_line = reply.split('\n')[-1][:30] if reply else 'EMPTY'
        print('[%d/%d] %s | %s %d年 %s | 实际:%s | %s' % (
            total, sum(len(c['events']) for c in sample),
            status, case['name'], event['year'],
            event['event'][:15], cat, last_line))

    if (ci+1) % 25 == 0:
        rate = correct/total*100 if total > 0 else 0
        print('  --- %d人, 准确率: %d/%d = %.1f%% ---' % (ci+1, correct, total, rate))

print('\n' + '='*60)
print('全管线回测 (V4 Pro + 引擎 + RAG)')
print('='*60)
rate = correct/total*100 if total > 0 else 0
print(f'总准确率: {correct}/{total} = {rate:.1f}%')
for cat in sorted(cat_stats.keys()):
    s = cat_stats[cat]
    r = s['correct']/s['total']*100 if s['total'] else 0
    print(f'  {cat}: {s["correct"]}/{s["total"]} = {r:.1f}%')
