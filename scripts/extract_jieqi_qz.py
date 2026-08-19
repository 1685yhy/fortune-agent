"""问真节气表提取脚本（1799-2100，12 节秒级时刻）→ data/jieqi_qz.json。

数据源：/tmp/qz_extracted/data/eOvQ_jie.json（问真 app 静态节气表 eOvQ 模块的
已解析版，ALGORITHMS.md 4.1 节记载：年→月→[[节名,日,时:分:秒]]，12 节）。
问真表时刻与农历/lunar-python 存在秒级差异（最多约 17s），起运分解按问真表
计算即可与问真 qiyunarr 对齐（250 案例校准 249/250，0.4% 差异仅为浮点边界）。

输出格式（与 L1 计划 Task 1 落盘格式一致）：
    {"1800": {"1": ["小寒", "6", "07:46:02"], "2": ["立春", "5", "18:14:44"], ...12节}, ...}
键 = 节序号 1-12（小寒1 立春2 惊蛰3 清明4 立夏5 芒种6 小暑7 立秋8 白露9 寒露10
立冬11 大雪12，序号即公历月），值 = [节名, 日, 时刻]。

⚠️ 2026-08-20 修正：此前落盘的 data/jieqi_qz.json 来自 eOvQ_jie_flat.json
（拍平版提取时年份错位，与真实节气时刻偏差可达半天以上，250 案例起运分解
0% 一致）。本脚本改从 eOvQ_jie.json（年→月 结构，2024 小寒 04:49:09 与
真实时刻一致）提取，并内置自校验。

用法：cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python3 scripts/extract_jieqi_qz.py
"""
import json
from pathlib import Path

SRC = Path("/tmp/qz_extracted/data/eOvQ_jie.json")
OUT = Path(__file__).resolve().parent.parent / "data" / "jieqi_qz.json"


def main():
    with open(SRC, encoding="utf-8") as f:
        nested = json.load(f)

    out = {}
    for year, months in nested.items():
        yi = int(year)
        if not (1799 <= yi <= 2100):
            continue
        out[year] = {}
        for idx, arr in months.items():
            if not arr:
                continue
            name, day, hhmmss = arr[0]
            out[year][str(idx)] = [name, str(day), hhmmss]

    # 自校验：2024 小寒 = 2024-01-06 04:49:09（真实节气时刻）
    assert out["2024"]["1"] == ["小寒", "6", "04:49:09"], out["2024"]["1"]
    assert out["2024"]["2"] == ["立春", "4", "16:26:53"], out["2024"]["2"]
    assert len(out) == 302, len(out)  # 1799-2100

    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"已写入 {OUT}: {len(out)} 年（1799-2100），每年 12 节，自校验通过（2024 小寒 04:49:09）")


if __name__ == "__main__":
    main()
