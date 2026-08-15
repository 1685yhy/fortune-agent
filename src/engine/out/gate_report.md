# 阶段1 门禁报告
通过 6 / 10，失败 4

## shishen: 0/4
# 单元跑分: evaluate
通过 0/4

- FAIL gj_0001 (合成样例:-) geju: 期望 正官格 实际 
- FAIL gj_0002 (滴天髓阐微.txt:370-385) geju: 期望 正官格 实际 
- FAIL gj_0004 (合成样例:-) geju: 期望 正印格 实际 
- FAIL gj_0003 (滴天髓阐微.txt:-) geju: 期望 建禄格 实际 
## geju: 4/4
# 单元跑分: evaluate
通过 4/4

## shensha: 2/2
# 单元跑分: evaluate
通过 2/2


## 结论：门禁未过（4 项待裁决）
4 项 FAIL 全部来自 shishen 规则对 geju_cases.json 的跑分：
shishen.evaluate 契约（Task 2/3）只产出 {"shishen": [...]} 键，
而 geju_cases.json（Task 4）expected 仅含 {"geju": ...} 键，
Task 7 简报门禁脚本将二者配对，shishen 行结构性无法得分。
按硬闸规则未改动任何考卷/规则，提交裁决。
