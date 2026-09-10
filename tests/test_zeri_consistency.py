"""k23 择吉口径统一 — chat/工具路径与计划路径共用单一事实源（数据一致性）。

背景: K3-A3(神煞级优先, 建除表忌与当日神煞级黄历宜冲突时以神煞级宜为准) 此前
只落在计划路径 `_build_lucky_card`, chat/工具路径 `ZeriResult.select()` 仍返回
裸建除表宜忌 → 同一日期两条路径方向相反。实测抽查 8 日有 4 日方向相反:
10-01(出行/入宅/移徙)、2027-01-01(出行/移徙)、10-14(嫁娶)、09-20(安葬)。

修复: K3-A3 下沉到单日宜忌单一事实源 `ZeriEngine._day_yi_ji()`,
`select()`(chat/工具/意图路径) 与 `_build_lucky_card()`(计划路径) 共用, 不得各自
再合并/过滤（数据一致性铁律: 同一数据单一事实源）。

断言依据: 项目自带 lunar-python 万年历权威口径（getDayYi/getDayJi）。
"""
import pytest
from lunar_python import Solar

from src.engines.zeri import ZeriEngine

engine = ZeriEngine()

# 抽查 8 日中方向相反的 4 日（另 4 日修复前已一致, 见 k23 报告）
# (日期, 该日权威宜中曾被 chat 路径误列入忌的宜项)
FLIP_CASES = [
    ("2026-10-01", ["出行", "移徙", "入宅"]),   # 闭日: 建除表忌 出行/入宅/移徙
    ("2027-01-01", ["出行", "移徙"]),           # 定日: 建除表忌 出行/移徙
    ("2026-10-14", ["嫁娶"]),                    # 闭日: 建除表忌 嫁娶
    ("2026-09-20", ["安葬"]),                    # 建日: 建除表忌 安葬
]


def _ymd(date_str: str) -> tuple:
    return tuple(int(x) for x in date_str.split("-"))


def _lunar(date_str: str):
    return Solar.fromYmd(*_ymd(date_str)).getLunar()


@pytest.mark.parametrize("date_str,items", FLIP_CASES)
def test_select_yi_ji_direction_matches_authority(date_str, items):
    """chat/工具路径 select(): 方向与 lunar-python 权威口径一致 ——
    权威明示之宜不得出现在忌, 且必须出现在宜（K3-A3 神煞级优先）。"""
    r = engine.select(*_ymd(date_str))
    authority_yi = list(_lunar(date_str).getDayYi())
    assert authority_yi, f"{date_str} lunar-python 权威宜表不应为空"
    for item in items:
        assert item in authority_yi, f"{date_str} 权威宜应含 {item}（测试前提锚点）"
        assert item in r.yi, f"{date_str} {item} 应为宜（权威宜: {authority_yi}）"
        assert item not in r.ji, f"{date_str} {item} 不应为忌（我方忌: {r.ji}）"


@pytest.mark.parametrize("date_str", [c[0] for c in FLIP_CASES])
def test_select_never_contradicts_authority_yi(date_str):
    """不变量（全日期成立）: 权威宜 ∩ 我方忌 = 空, 且权威宜全部并入我方宜。"""
    r = engine.select(*_ymd(date_str))
    authority_yi = set(_lunar(date_str).getDayYi())
    assert authority_yi & set(r.ji) == set(), \
        f"{date_str} 权威宜被列为忌（方向相反）: {sorted(authority_yi & set(r.ji))}"
    assert authority_yi <= set(r.yi), \
        f"{date_str} 权威宜未全部并入我方宜: {sorted(authority_yi - set(r.yi))}"


def test_plan_path_and_chat_path_share_one_source():
    """单一事实源: 计划路径卡片宜忌 == chat/工具路径 select() 宜忌（同日同源）,
    且两路径都不与权威口径方向冲突（10-01 权威搬家吉日: 宜含出行/移徙/入宅）。"""
    r = engine.select(2026, 10, 1)
    res = engine.select_lucky_days("搬家", "2026-10-01", "2026-10-01")
    assert len(res["cards"]) == 1, "10-01 为权威搬家吉日, 计划路径应有且仅有该卡"
    card = res["cards"][0]
    assert card.date == "2026-10-01"
    assert card.yi == r.yi, f"卡片宜与 select() 宜不一致:\ncard={card.yi}\nselect={r.yi}"
    assert card.ji == r.ji, f"卡片忌与 select() 忌不一致:\ncard={card.ji}\nselect={r.ji}"
    # 权威锚点复核: 当日宜含 出行/移徙/入宅, 且三者均不在忌
    for item in ("出行", "移徙", "入宅"):
        assert item in card.yi and item not in card.ji


def test_select_ji_keeps_authority_ji_and_jianchu_ji():
    """口径完整性: 建除表忌(未被神煞级宜覆盖的词)与权威忌均在忌中 ——
    修复不得反向误删（权威口径: 忌 嫁娶/开市/祭祀/祈福/斋醮/纳采/修坟）。"""
    r = engine.select(2026, 10, 1)
    authority_ji = set(_lunar("2026-10-01").getDayJi())
    assert authority_ji <= set(r.ji), \
        f"权威忌未保留于忌: {sorted(authority_ji - set(r.ji))}"
    # 建除表闭日忌"开市/嫁娶"与神煞级宜无交集 → 保留（对照: 出行/入宅/移徙被神煞级宜覆盖移除）
    assert {"开市", "嫁娶"} <= set(r.ji)


def test_select_purpose_does_not_reintroduce_conflict():
    """带 purpose 的 chat 路径（真实链路 _do_zeri_analysis 传 purpose）同样不得
    把权威宜项列为忌: 2026-10-01 purpose=搬家 → 宜首项为"入宅", 忌无"入宅/移徙/出行"。
    """
    r = engine.select(2026, 10, 1, purpose="搬家")
    assert r.yi[0] == "入宅", f"purpose=搬家 应把入宅排到宜首位: {r.yi}"
    assert "入宅" not in r.ji and "移徙" not in r.ji and "出行" not in r.ji


def test_select_deterministic_after_shared_source():
    """确定性: 同一日期多次调用宜忌全等（共用纯函数, 无状态）。"""
    r1 = engine.select(2026, 10, 1, purpose="搬家")
    r2 = engine.select(2026, 10, 1, purpose="搬家")
    assert (r1.yi, r1.ji, r1.overall) == (r2.yi, r2.ji, r2.overall)


def test_authority_invariant_sweep_whole_range():
    """全区间扫描不变量（2026-01-01 ~ 2027-01-31, 396 天）:
    每日 权威宜 ∩ 我方忌 = 空 且 权威宜 ⊆ 我方宜 —— 任一日违例即方向冲突回归。"""
    from datetime import date, timedelta
    d, end, scanned = date(2026, 1, 1), date(2027, 1, 31), 0
    while d <= end:
        scanned += 1
        authority_yi = set(_lunar(d.isoformat()).getDayYi())
        r = engine.select(d.year, d.month, d.day)
        inter = authority_yi & set(r.ji)
        missing = authority_yi - set(r.yi)
        assert not inter, f"{d} 权威宜被列为忌（方向相反）: {sorted(inter)}"
        assert not missing, f"{d} 权威宜未并入我方宜: {sorted(missing)}"
        d += timedelta(days=1)
    assert scanned == 396
