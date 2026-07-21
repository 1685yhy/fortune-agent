"""Bulk comparison: our engine vs 问真八字 API.

Tests hundreds of diverse birth dates and compares 9 metrics.
Identifies patterns in discrepancies for debugging.
"""
import urllib.request, json, time, random, sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.engines.bazi import BaziEngine
from src.engines.bazi_formatter import (
    BRANCH_HIDDEN, NAYIN_TABLE, _get_shishen,
    get_changsheng, get_kongwang, compute_cg_shishen
)

e = BaziEngine()

def test_case(y, m, d, h, mi, gender_code, city="北京"):
    """Compare one birth date against 问真八字 API. Returns dict with results."""
    gender = "女" if gender_code == 0 else "男"
    d_str = f'{y:04d}-{m:02d}-{d:02d}%20{h:02d}:{mi:02d}'

    # Our calculation
    r = e.calculate(y, m, d, h, mi, city, gender)
    ob = list(r.bazi)
    day_gan = ob[2][0]

    our_ss = [r.shishen[i] if i < len(r.shishen) else _get_shishen(day_gan, ob[i][0]) for i in range(4)]
    our_cg = [BRANCH_HIDDEN.get(p[1], ["?"]) for p in ob]
    our_cgss = [compute_cg_shishen(day_gan, h) for h in our_cg]
    our_ny = [NAYIN_TABLE.get(p, "?") for p in ob]
    our_kw = [get_kongwang(p) for p in ob]
    our_xy = [get_changsheng(day_gan, p[1]) for p in ob]
    our_zz = [get_changsheng(p[0], p[1]) for p in ob]
    our_dy = [g for _, g in r.dayun[:8]]

    # 问真八字 API
    try:
        url = f'https://bzapi3.iwzbz.com/getbasebz8.php?d={d_str}&s={gender_code}&today={d_str}&vip=0&yzs=0&pqf=0'
        wz = json.loads(urllib.request.urlopen(url, timeout=10).read())
        wz_pillars = [wz['bz'][str(i)] + wz['bz'][str(i+1)] for i in range(0, 8, 2)]
    except Exception as ex:
        return {"error": str(ex), "input": f"{y}-{m:02d}-{d:02d}"}

    wz_ss = wz.get("ss", [])
    wz_cg = wz.get("cg", [])
    wz_cgss = wz.get("cgss", [])
    wz_ny = wz.get("ny", [])
    wz_kw = wz.get("kw", [])
    wz_xy = wz.get("xy", [])
    wz_zz = wz.get("zz", [])
    wz_dy = wz.get("dayun", [])[:8]

    # Compare 9 metrics
    results = {
        "四柱": (" ".join(ob), " ".join(wz_pillars), our_ss == wz_ss),
        "十神": (our_ss, wz_ss, our_ss == wz_ss),
        "藏干": (our_cg, wz_cg, our_cg == wz_cg),
        "藏干十神": (our_cgss, wz_cgss, our_cgss == wz_cgss),
        "纳音": (our_ny, wz_ny, our_ny == wz_ny),
        "空亡": (our_kw, wz_kw, our_kw == wz_kw),
        "星运": (our_xy, wz_xy, our_xy == wz_xy),
        "自坐": (our_zz, wz_zz, our_zz == wz_zz),
        "大运": (our_dy, wz_dy, our_dy == wz_dy),
    }

    failed = {k: v for k, v in results.items() if not v[2]}
    return {
        "input": f"{y}-{m:02d}-{d:02d} {h:02d}:{mi:02d} {city} {gender}",
        "ok": len(failed) == 0,
        "score": sum(1 for v in results.values() if v[2]),
        "failed": {k: {"ours": v[0], "wz": v[1]} for k, v in failed.items()},
        "dayun_detail": {"ours": our_dy, "wz": wz_dy},
    }


# Generate test cases: diverse coverage
print("生成测试用例...")
cases = []

# 1. Every 5 years from 1950-2020
for y in range(1950, 2021, 5):
    for m in [1, 4, 7, 10]:
        for h in [0, 8, 16]:
            cases.append((y, m, 15, h, 0, y % 2, "北京"))

# 2. Specific edge cases
edge_cases = [
    (1900, 1, 1, 0, 0, 0, "北京"),    # Earliest
    (2024, 2, 29, 12, 0, 1, "上海"),   # Leap day
    (2024, 2, 4, 10, 0, 1, "北京"),    # 立春 boundary
    (2000, 12, 31, 23, 59, 0, "广州"),  # Millennium
    (1984, 2, 4, 23, 0, 1, "北京"),    # 立春 boundary
]
cases.extend(edge_cases)

# 3. All 12 months
for m in range(1, 13):
    cases.append((1999, m, 15, 12, 0, 0, "北京"))

# 4. Different cities
for city in ["哈尔滨", "乌鲁木齐", "广州", "成都", "拉萨"]:
    cases.append((1990, 6, 15, 12, 0, 1, city))

# Deduplicate
cases = list(set(cases))
print(f"总计 {len(cases)} 个测试用例\n")

# Run tests
results = []
errors = []
start = time.time()

for i, (y, m, d, h, mi, s, city) in enumerate(cases):
    result = test_case(y, m, d, h, mi, s, city)
    if "error" in result:
        errors.append(result)
    else:
        results.append(result)

    if (i + 1) % 20 == 0:
        ok = sum(1 for r in results if r["ok"])
        print(f"  进度 {i+1}/{len(cases)}: {ok}/{len(results)} 完全一致")

    time.sleep(0.3)  # Rate limiting

elapsed = time.time() - start

# Summary
print(f"\n{'='*70}")
print(f"批量对比结果 ({len(results)} 有效, {len(errors)} API错误)")
print(f"{'='*70}")

total_score = sum(r["score"] for r in results)
max_score = len(results) * 9
print(f"总得分: {total_score}/{max_score} ({total_score/max_score*100:.1f}%)")

perfect = sum(1 for r in results if r["ok"])
print(f"完全一致(9/9): {perfect}/{len(results)} ({perfect/len(results)*100:.0f}%)")

# Analyze failures
if results:
    failure_counts = {}
    dayun_issues = 0
    for r in results:
        for metric in r.get("failed", {}):
            failure_counts[metric] = failure_counts.get(metric, 0) + 1
            if metric == "大运":
                dayun_issues += 1

    if failure_counts:
        print(f"\n失败指标分布:")
        for metric, count in sorted(failure_counts.items(), key=lambda x: -x[1]):
            print(f"  {metric}: {count}/{len(results)} ({count/len(results)*100:.0f}%)")

    # Show sample failures
    failed_cases = [r for r in results if not r["ok"]]
    if failed_cases:
        print(f"\n失败样例 (前5个):")
        for r in failed_cases[:5]:
            print(f"  {r['input']}: score={r['score']}/9")
            for metric, detail in r["failed"].items():
                print(f"    {metric}: ours={detail['ours']} wz={detail['wz']}")

print(f"\n耗时: {elapsed:.0f}s | 速率: {len(cases)/elapsed:.1f} cases/s")
print(f"结论: {perfect}/{len(results)} 完全一致 = {perfect/len(results)*100:.0f}%")
