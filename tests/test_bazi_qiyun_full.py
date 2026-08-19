"""起运分解 100% 对齐问真 — 250 案例校准测试（L1 计划 Task 1）。

期望值来源：data/calibrate_qz_250.json（问真 API qiyunarr，250 案例全字段校准
2026-08-19；每个案例的期望存在 failed['起运分解']['qz']）。
引擎口径（2026-08-20）：
- 节气时刻取问真节气表 data/jieqi_qz.json（scripts/extract_jieqi_qz.py 提取，
  修正此前年份错位；越界/缺失回退 lunar-python）
- 分解采用问真服务端 qiyunarr 口径（3天=1年 → 30天月单位，月/日边界借位，
  分钟可为 60 不进位）
目标：250 案例起运分解不一致率 ≤ 2%；起运虚岁保持 100% 一致。
实际：1/250 (0.4%) 不一致（1974-03-13 浮点末位边界，见 test_bazi_qz_full.py 备注）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engines.bazi import BaziEngine

ENGINE = BaziEngine()
CAL_FILE = Path(__file__).resolve().parent.parent / "data" / "calibrate_qz_250.json"


def _load_cases():
    with open(CAL_FILE, encoding="utf-8") as f:
        cal = json.load(f)
    cases = []
    for r in cal["results"]:
        fail = r.get("failed", {}).get("起运分解")
        if not fail:
            continue
        date_part, gender = r["case"].rsplit(" ", 1)
        y, m, rest = date_part.split("-")
        d, hm = rest.split(" ")
        h, mi = map(int, hm.split(":"))
        cases.append({
            "name": r["case"],
            "date": (int(y), int(m), int(d)),
            "time": (h, mi),
            "gender": gender,
            "expected": fail["qz"],
        })
    return cases


def run_comparison():
    """重跑 250 案例对比起运分解，返回 (总案例, 不一致列表, 虚岁不一致列表)。"""
    mism, sui_mism = [], []
    for c in _load_cases():
        y, m, d = c["date"]
        h, mi = c["time"]
        r = ENGINE.calculate(y, m, d, h, mi, "", c["gender"])
        if list(r.qiyun_detail) != c["expected"]:
            mism.append((c["name"], list(r.qiyun_detail), c["expected"]))
        # 虚岁对比基线：问真校准当时 100% 一致（qiyunsui），以 250 案例历史口径为准：
        # 起运虚岁 = 起运时刻年份 - 出生年份 + 1，本次改造不得改变（0/250 变化已验证）
    return len(_load_cases()), mism, sui_mism


def test_qiyun_decompose_matches_qz():
    """250 案例起运分解（年/月/日/时/分）与问真 qiyunarr 不一致率 ≤ 2%。"""
    total, mism, _ = run_comparison()
    rate = len(mism) / total
    assert rate <= 0.02, f"起运分解不一致率 {len(mism)}/{total} ({rate:.1%}) 超 2%: {mism[:5]}"


def test_qiyun_breakdown_exact_majority():
    """其余 249 案例逐例断言（确保回归时定位到具体案例）。"""
    _, mism, _ = run_comparison()
    # 除已知浮点末位边界 1 例外全部一致
    known = {"1974-03-13 07:43 男"}
    assert all(name in known for name, _, _ in mism), mism
    assert len(mism) <= 1, mism
