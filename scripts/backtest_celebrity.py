#!/usr/bin/env python3
"""名人案例回测：测试机器人能否预测已知人生大事"""
import json, httpx, sys

with open('data/celebrity_cases.json') as f:
    cases = json.load(f)

API = 'http://127.0.0.1:8765/v1/chat/completions'
total_events = 0
correct = 0

for case in cases:
    print('\n' + '='*60)
    print('名人: %s | 出生: %s' % (case['name'], case['birth']))
    print('='*60)

    for event in case['events']:
        total_events += 1
        # 用八字推那年趋势
        prompt = '出生信息：%s\n\n请分析此人%s年的运势，判断当年最可能发生哪类事件。只选一个最可能：事业/财运/婚姻/健康/子女/灾祸。\n格式：答案: 事业' % (
            case['birth'], event['year'])

        try:
            r = httpx.post(API, json={
                'model': 'fortune-agent',
                'messages': [{'role': 'user', 'content': prompt}]
            }, timeout=120.0)
            reply = r.json()['choices'][0]['message']['content'].strip()

        except Exception as e:
            reply = 'ERROR: %s' % str(e)

        # 简单匹配：看回复里有没有正确类别
        is_hit = event['category'] in reply
        if is_hit:
            correct += 1

        status = 'HIT' if is_hit else 'MISS'
        print('  %d年 %s | 实际:%s | %s | %s...' % (
            event['year'], status, event['category'],
            event['event'][:15],
            reply.replace('\n',' ')[:60]))

accuracy = correct/total_events*100 if total_events > 0 else 0
print('\n' + '='*60)
print('回测结果: %d/%d = %.1f%% 命中率' % (correct, total_events, accuracy))
print('='*60)
