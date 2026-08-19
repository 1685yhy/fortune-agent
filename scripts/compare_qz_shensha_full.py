"""神煞引擎 vs 问真 szshensha 全集离线对比（250 校准案例，覆盖率统计）。

问真独有 28 种补全后（2026-08-20）：覆盖率 100.00%（4125/4125 实例零漏报零超报）。

用法：cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python scripts/compare_qz_shensha_full.py
输出：覆盖率 / 按类型漏报 / 超报（我们报问真无）/ 缺失清单
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engines.bazi import BaziEngine
from src.engines.shensha import shensha_names

DATA = Path(__file__).resolve().parent.parent / "data" / "calibrate_qz_250.json"
TARGET_COVERAGE = 0.95  # 覆盖率目标 ≥95%（问真特有但规则未明的记录缺失清单）


def parse_case(c):
    m = re.match(r"(\d+)-(\d+)-(\d+) (\d+):(\d+) (\S+)", c)
    return [int(m.group(i)) for i in range(1, 6)] + [m.group(6)]


def main():
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    engine = BaziEngine()

    total = miss = 0
    per_type = {}      # 问真名 -> [命中, 总数]
    over_report = {}   # 我们报但问真无 -> 次数
    diff_cases = []

    for r in data["results"]:
        y, mo, d, h, mi, g = parse_case(r["case"])
        res = engine.calculate(y, mo, d, h, mi, "", g)
        ours = set(shensha_names(res.bazi[0], res.bazi[2],
                                 [p[0] for p in res.bazi], [p[1] for p in res.bazi], g))
        qz = set(r["qz_shensha"])
        for n in qz:
            total += 1
            per_type.setdefault(n, [0, 0])
            per_type[n][1] += 1
            if n in ours:
                per_type[n][0] += 1
            else:
                miss += 1
        for n in ours - qz:
            over_report[n] = over_report.get(n, 0) + 1
        if ours != qz:
            diff_cases.append((r["case"], sorted(qz - ours), sorted(ours - qz)))

    cov = (total - miss) / total * 100 if total else 0
    print(f"问真神煞实例总数: {total}")
    print(f"覆盖率: {total - miss}/{total} = {cov:.2f}%  (目标 ≥{TARGET_COVERAGE * 100:.0f}%)")
    print(f"漏报实例: {miss}")
    print(f"全集一致案例: {len(data['results']) - len(diff_cases)}/{len(data['results'])}")
    if miss:
        print("\n按类型漏报:")
        for n, (hit, tot) in sorted(per_type.items(), key=lambda kv: -kv[1][1]):
            if hit < tot:
                print(f"  {n}: {hit}/{tot}  漏 {tot - hit}")
    if over_report:
        print("\n超报（我们报问真无）:")
        for n, c in sorted(over_report.items(), key=lambda kv: -kv[1]):
            print(f"  {n}: {c} 次")
    if diff_cases:
        print("\n差异案例（前 10）:")
        for c, miss_l, over in diff_cases[:10]:
            print(f"  {c}: 漏={miss_l} 超={over}")

    ok = cov >= TARGET_COVERAGE * 100 and miss == 0
    print(f"\n结论: {'通过 — 全覆盖' if ok else '未达标'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
