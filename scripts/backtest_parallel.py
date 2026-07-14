#!/usr/bin/env python3
"""1000人并行回测：多线程同时调用API，大幅加速"""
import json, sys, re, time, httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

with open('data/celebrity_cases.json') as f:
    cases = json.load(f)

API = 'http://124.221.233.214/api/chat'
valid = [c for c in cases if re.search(r'\d{4}年\d{1,2}月\d{1,2}日', c['birth'])]
print(f'并行回测: {len(valid)}人, {sum(len(c["events"]) for c in valid)}事件')

# Build all test cases
tests = []
for case in valid:
    for event in case['events']:
        prompt = '看八字 %s 主要看%d年运势' % (case['birth'], event['year'])
        tests.append({
            'name': case['name'],
            'year': event['year'],
            'event': event['event'][:20],
            'category': event['category'],
            'prompt': prompt,
        })

print(f'任务数: {len(tests)}, 并行度: 5\n')

correct = 0
total = 0
cat_stats = {}
start = time.time()

def test_one(t):
    try:
        r = httpx.post(API, json={'message': t['prompt'], 'user_id': 'btest'}, timeout=180.0)
        reply = r.json().get('reply', '')
        hit = t['category'] in reply
        return {'hit': hit, 'cat': t['category'], 'name': t['name'],
                'year': t['year'], 'event': t['event'], 'len': len(reply)}
    except:
        return {'hit': False, 'cat': t['category'], 'name': t['name'],
                'year': t['year'], 'event': t['event'], 'len': 0}

with ThreadPoolExecutor(max_workers=5) as ex:
    futures = {ex.submit(test_one, t): t for t in tests}
    for f in as_completed(futures):
        r = f.result()
        total += 1
        if r['hit']: correct += 1
        cat_stats.setdefault(r['cat'], {'correct':0,'total':0})
        cat_stats[r['cat']]['total'] += 1
        if r['hit']: cat_stats[r['cat']]['correct'] += 1

        status = 'HIT' if r['hit'] else 'MISS'
        elapsed = time.time() - start
        rate = correct/total*100 if total > 0 else 0
        print('[%d/%d %.0fm] %s | %s %d年 | 实际:%s | 字数:%d | %.1f%%' % (
            total, len(tests), elapsed/60, status, r['name'], r['year'],
            r['cat'], r['len'], rate))

print('\n' + '='*60)
elapsed = time.time() - start
rate = correct/total*100 if total > 0 else 0
print(f'1000人回测: {correct}/{total} = {rate:.1f}%  (耗时{elapsed/60:.0f}分钟)')
print(f'引擎精度: {len(valid)}/{len(cases)} = {len(valid)/len(cases)*100:.1f}%')
for cat in sorted(cat_stats.keys()):
    s = cat_stats[cat]
    r = s['correct']/s['total']*100 if s['total'] else 0
    print(f'  {cat}: {s["correct"]}/{s["total"]} = {r:.1f}%')
