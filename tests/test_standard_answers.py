"""标准答案回归测试（batch2 D2）：问真八字 API 锚点库 → 本引擎逐字断言。

数据: tests/standard_answers/paipan_answers.jsonl（D1 生成 200 条，60 日柱全覆盖 × 6 时辰 × 2 性别）。
契约: 对每条 input(date/time/gender/gender_code/lunar) 调 BaziEngine.calculate
      （引擎调用方式与 tests/bulk_compare_wenzhen.py 一致：同模块、同签名），断言
      expected 的 10 个锚点逐字一致：
        四柱(year/month/day/hour_pillar) + day_master + day_branch
        + dayun_first3 + qiyunsui + minggong + taiyuan；
      另做十神派生一致性断言（十神 = f(日主, 柱天干)，四柱与日主锚定后十神随之锚定，
      覆盖任务目标中的「十神」项——问真 ss 字段未纳入 D1 锚点库，故以纯函数派生护栏替代）。

口径说明（与数据源对齐的关键，务必遵守）:
  1. city="" 不做真太阳时修正：问真爬取 URL 固定 yzs=0（无真太阳时修正，
     见 scripts/scrape_wenzhen.py:27），故引擎调用传 city=""（_true_solar_time 原样返回）。
     与 test_paipan_api.py 头部注释「city="" 的问真原始锚点」同口径。
  2. 晚子时 23:00 样本 1 条（paipan-000030，1970-12-31 23:00 男）：引擎按次日子时排盘，
     与问真锚点一致（2026-08-27 实测通过，非 skip）。
  3. 闰月样本 1 条（paipan-000088，1995-10-15 闰八月廿一 申时）：引擎 lunar-python
     闰月口径与问真一致（2026-08-27 实测通过，非 skip）。
  4. day_master 锚点为单字天干，引擎 r.day_master 为「丁火」双字 → 取 [0] 比较。
  5. qiyunsui 锚点 = 引擎 r.dayun[0][0]（首步大运起运虚岁，问真 qiyunsui 口径；
     引擎 _calc_qiyun_start_age 有 280/280 (100%) 校准记录）。
  6. 十神派生用 bazi_formatter._get_shishen(day_gan, gan, for_hidden=True)（数据口径：
     同干 → 比肩、日柱位 → 日主），与引擎 r.shishen 及问真 ss 字段逐字一致
     （2026-08-27 以 DB 只读抽查 paipan-000006 同键行确认问真 ss =
     ["食神","比肩","日主","正印"] == 引擎输出）。

已知差异机制: KNOWN_DIFFS: dict[id → 原因注释] 供未来引擎口径变化时逐条 skip + 注释。
当前为空（200/200 全过，2026-08-27 验证）。新增条目时必须注明差异性质——
「引擎已知差异」可 skip 并注释；「待修 bug」不得静默 skip，须同步上报主会话。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engines.bazi import BaziEngine  # noqa: E402
from src.engines.bazi_formatter import _get_shishen  # noqa: E402

_ANS_PATH = Path(__file__).parent / "standard_answers" / "paipan_answers.jsonl"

# 已知差异（引擎口径 vs 问真锚点库）：{id: 原因}。当前为空——
# 含晚子时(paipan-000030)与闰月(paipan-000088)样本，实测均一致，无需 skip。
KNOWN_DIFFS = {}


def _load_cases():
    """读标准答案库（只读）。每行 JSON：input + expected(10 锚点) + meta。"""
    if not _ANS_PATH.exists():
        raise FileNotFoundError(
            f"标准答案库缺失: {_ANS_PATH}（先跑 scripts/build_answer_bank.py 生成）")
    cases = []
    for line_no, line in enumerate(_ANS_PATH.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"标准答案库第 {line_no} 行非法 JSON: {exc}") from exc
    return cases


_CASES = _load_cases()

# 引擎实例与 bulk_compare_wenzhen.py 一致（模块级单例，calculate 每次调用自含状态）
_ENGINE = BaziEngine()

# 十神派生所依据的柱位顺序（与 expected 字段名对应）
_PILLAR_KEYS = ["year_pillar", "month_pillar", "day_pillar", "hour_pillar"]


def test_answer_bank_integrity():
    """数据完整性护栏（D1 契约）：条数 150~300、id 唯一、10 锚点字段齐全合法。"""
    assert 150 <= len(_CASES) <= 300, f"标准答案库条数 {len(_CASES)} 超出 [150, 300]"
    ids = [c["id"] for c in _CASES]
    assert len(set(ids)) == len(ids), "id 存在重复"
    assert all(ids[i] < ids[i + 1] for i in range(len(ids) - 1)), "id 应升序连续"
    required_input = {"date", "time", "gender", "gender_code", "lunar"}
    required_exp = {"year_pillar", "month_pillar", "day_pillar", "hour_pillar",
                    "day_master", "day_branch", "dayun_first3", "qiyunsui",
                    "minggong", "taiyuan"}
    for c in _CASES:
        assert required_input <= set(c["input"]), f"{c['id']} input 字段缺失"
        assert required_exp <= set(c["expected"]), f"{c['id']} expected 字段缺失"
        exp = c["expected"]
        assert len(exp["dayun_first3"]) == 3, f"{c['id']} dayun_first3 应恰 3 步"
        assert 0 <= exp["qiyunsui"] <= 11, f"{c['id']} qiyunsui 越界"
        for k in ("year_pillar", "month_pillar", "day_pillar", "hour_pillar",
                  "minggong", "taiyuan"):
            assert len(exp[k]) == 2, f"{c['id']} {k} 应为 2 字"
        assert len(exp["day_master"]) == 1 and len(exp["day_branch"]) == 1, \
            f"{c['id']} day_master/day_branch 应为 1 字"
        assert exp["day_master"] == exp["day_pillar"][0], \
            f"{c['id']} day_master 与 day_pillar[0] 不一致"


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_standard_paipan_anchors(case):
    """单条命盘断言：10 锚点逐字一致 + 十神派生一致性（引擎口径与 bulk_compare 一致）。"""
    if case["id"] in KNOWN_DIFFS:
        pytest.skip(f"{case['id']} 已知差异: {KNOWN_DIFFS[case['id']]}")

    inp, exp = case["input"], case["expected"]
    year, month, day = (int(x) for x in inp["date"].split("-"))
    hour, minute = (int(x) for x in inp["time"].split(":"))
    # city="" 与问真 yzs=0 同口径（无真太阳时修正，见模块 docstring 口径说明 1）
    r = _ENGINE.calculate(year, month, day, hour, minute, "", inp["gender"])

    # ── 四柱（逐字）──
    assert r.bazi[0] == exp["year_pillar"], \
        f"{case['id']} 年柱: got {r.bazi[0]}, expected {exp['year_pillar']}"
    assert r.bazi[1] == exp["month_pillar"], \
        f"{case['id']} 月柱: got {r.bazi[1]}, expected {exp['month_pillar']}"
    assert r.bazi[2] == exp["day_pillar"], \
        f"{case['id']} 日柱: got {r.bazi[2]}, expected {exp['day_pillar']}"
    assert r.bazi[3] == exp["hour_pillar"], \
        f"{case['id']} 时柱: got {r.bazi[3]}, expected {exp['hour_pillar']}"

    # ── 日主 / 日支 ──
    assert r.day_master[0] == exp["day_master"], \
        f"{case['id']} 日主: got {r.day_master}, expected {exp['day_master']}"
    assert r.bazi[2][1] == exp["day_branch"], \
        f"{case['id']} 日支: got {r.bazi[2][1]}, expected {exp['day_branch']}"

    # ── 大运前 3 步 + 起运虚岁 ──
    assert [g for _, g in r.dayun[:3]] == exp["dayun_first3"], \
        f"{case['id']} 大运前3步: got {[g for _, g in r.dayun[:3]]}, " \
        f"expected {exp['dayun_first3']}"
    assert r.dayun[0][0] == exp["qiyunsui"], \
        f"{case['id']} 起运虚岁: got {r.dayun[0][0]}, expected {exp['qiyunsui']}"

    # ── 命宫 / 胎元 ──
    assert r.minggong == exp["minggong"], \
        f"{case['id']} 命宫: got {r.minggong}, expected {exp['minggong']}"
    assert r.taiyuan == exp["taiyuan"], \
        f"{case['id']} 胎元: got {r.taiyuan}, expected {exp['taiyuan']}"

    # ── 十神派生一致性（纯函数护栏）──
    # 从已锚定的四柱 + 日主派生（数据口径：同干→比肩、日柱位→日主），与问真 ss 同口径。
    pillars = [exp[k] for k in _PILLAR_KEYS]
    derived = [
        "日主" if i == 2 else _get_shishen(exp["day_master"], pillars[i][0],
                                           for_hidden=True)
        for i in range(4)
    ]
    assert r.shishen == derived, \
        f"{case['id']} 十神: got {r.shishen}, derived {derived}"
