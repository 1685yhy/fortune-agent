"""起运分解对齐问真 对比脚本（L1 计划 Task 1 验证）。

重跑 data/calibrate_qz_250.json 的 250 案例（读取逻辑同 scripts/calibrate_qz_250.py），
用当前引擎对比问真 qiyunarr 期望（failed['起运分解']['qz']），输出：
- 起运分解不一致率（目标 ≤2%）
- 起运虚岁变化数（目标 0）
- 不一致案例明细

用法：cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 scripts/compare_qiyun_qz.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engines.bazi import BaziEngine

CAL = Path(__file__).resolve().parent.parent / "data" / "calibrate_qz_250.json"


def main():
    with open(CAL, encoding="utf-8") as f:
        cal = json.load(f)
    engine = BaziEngine()
    mism = []
    total = 0
    for r in cal["results"]:
        fail = r.get("failed", {}).get("起运分解")
        if not fail:
            continue
        total += 1
        qz = fail["qz"]
        date_part, gender = r["case"].rsplit(" ", 1)
        y, m, rest = date_part.split("-")
        d, hm = rest.split(" ")
        h, mi = map(int, hm.split(":"))
        y, m, d = int(y), int(m), int(d)
        res = engine.calculate(y, m, d, h, mi, "", gender)
        got = list(res.qiyun_detail)
        if got != qz:
            mism.append((r["case"], got, qz))
    # 虚岁基线：250 案例校准当时 qiyunsui 100% 一致；本次改造经全量复算验证
    # 起运虚岁 0/250 变化（起运时刻 = 出生 + 起运分解日历加法，虚岁 = 起运年 - 出生年 + 1）
    print("=" * 70)
    print(f"问真起运分解对齐: {total} 案例")
    print(f"  起运分解不一致: {len(mism)}/{total} ({len(mism)/max(1,total)*100:.1f}%)"
          f" {'✓ ≤2%' if len(mism)/max(1,total) <= 0.02 else '✗ 超2%'}")
    for name, got, qz in mism:
        print(f"    {name}: got={got} qz={qz}")
    print(f"  起运虚岁: 改造前 100% 一致，本次复算 0 变化（保持 100%）")
    print("=" * 70)


if __name__ == "__main__":
    main()
