#!/usr/bin/env python3
"""Full pipeline backtest: bazi engine + BGE RAG + V4 Pro LLM - same as production."""
import json, httpx, sys, os, time

sys.path.insert(0, '.')
from src.engines.bazi import BaziEngine
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever
from src.config import load_settings
from src.llm.prompts import SYSTEM_PROMPT, USER_CONTEXT_TEMPLATE

with open('data/celebrity_cases.json') as f:
    cases = json.load(f)

total_events = sum(len(c['events']) for c in cases)
print('%d people, %d events' % (len(cases), total_events))

DEEPSEEK_KEY = os.environ.get('DEEPSEEK_KEY', '')
API = 'https://api.deepseek.com/v1/chat/completions'

# Load engines
engine = BaziEngine()
settings = load_settings()
settings.vectordb_dir = settings.data_dir / 'vectordb'  # use local path
embedder = Embedder()
retriever = Retriever('/mnt/d/fortune-data/vectordb', embedder)

correct = 0
total = 0
cat_stats = {}

for ci, case in enumerate(cases[:50]):
    # Parse birth
    import re
    birth_info = case['birth']
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日.*?(\d{1,2})[：:].*?(\d{1,2})', birth_info)
    if m:
        year, month, day, hour, minute = int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5])
    else:
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', birth_info)
        year, month, day = int(m[1]), int(m[2]), int(m[3])
        hour, minute = 12, 0
    gender = '女' if '女' in birth_info else '男'
    city = '北京'
    city_m = re.search(r'(\S{2,4}) [男女]', birth_info)
    if city_m: city = city_m.group(1)

    # 1. Bazi calculation
    result = engine.calculate(year, month, day, hour, minute, city, gender)

    # 2. RAG search
    search_q = '%s 运势' % result.day_master
    refs = retriever.search(search_q, category='bazi', top_k=10)

    for event in case['events']:
        total += 1
        cat = event['category']
        cat_stats.setdefault(cat, {'correct':0,'total':0})
        cat_stats[cat]['total'] += 1

        # 3. Build prompt with full chart data
        chart_str = '八字：%s\n日主：%s\n大运：%s\n%d年流年' % (
            ' '.join(result.bazi), result.day_master,
            ' '.join('%d岁%s' % (a,g) for a,g in result.dayun[:5]),
            event['year'])

        ref_str = '\n'.join('%d. 【%s】%s' % (j+1, r.source, r.text[:100])
                          for j, r in enumerate(refs[:5]))

        user_msg = USER_CONTEXT_TEMPLATE.format(
            chart_data=chart_str, references=ref_str,
            question='%s年此人最可能发生什么类型事件？只回答：事业/财运/婚姻/健康/子女/灾祸' % event['year'])

        # 4. Full LLM call with system prompt
        try:
            r = httpx.post(API, headers={
                'Authorization': 'Bearer ' + DEEPSEEK_KEY,
                'Content-Type': 'application/json'
            }, json={
                'model': 'deepseek-chat',
                'messages': [
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': user_msg}
                ],
                'max_tokens': 50,
                'temperature': 0
            }, timeout=60.0)
            reply = r.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            reply = 'ERR'

        is_hit = cat in reply
        if is_hit:
            correct += 1
            cat_stats[cat]['correct'] += 1

        status = 'HIT' if is_hit else 'MISS'
        print('[%3d/%d] %s | %s %d年 | 实际:%s | 答:%s' % (
            total, total_events, status, case['name'],
            event['year'], cat, reply[:20]))

    if (ci+1) % 10 == 0:
        rate = correct/total*100 if total > 0 else 0
        print('  --- %d人 %d事件 准确率: %d/%d = %.1f%% ---' % (ci+1, total, correct, total, rate))
    time.sleep(0.3)

rate = correct/total*100 if total > 0 else 0
print()
print('='*60)
print('完整管线回测: %d/%d = %.1f%%' % (correct, total, rate))
for cat in sorted(cat_stats.keys()):
    s = cat_stats[cat]
    r = s['correct']/s['total']*100 if s['total'] else 0
    print('  %s: %d/%d = %.1f%%' % (cat, s['correct'], s['total'], r))
