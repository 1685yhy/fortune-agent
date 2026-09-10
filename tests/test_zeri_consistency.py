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

from src.engines.zeri import JIANCHU_YI_JI, ZeriEngine

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
    每日 权威宜 ∩ 我方忌 = 空 且 权威宜 ⊆ 我方宜 —— 任一日违例即方向冲突回归。

    注: 权威宜集合先剔除哨兵 ``无``（F3; 宜侧哨兵 2020-2035 仅 10 天, 如 2024-04-06,
    均不在本区间, 剔除只为区间外扩时不误报）。"""
    from datetime import date, timedelta
    d, end, scanned = date(2026, 1, 1), date(2027, 1, 31), 0
    while d <= end:
        scanned += 1
        authority_yi = {x for x in _lunar(d.isoformat()).getDayYi() if x != "无"}
        r = engine.select(d.year, d.month, d.day)
        inter = authority_yi & set(r.ji)
        missing = authority_yi - set(r.yi)
        assert not inter, f"{d} 权威宜被列为忌（方向相反）: {sorted(inter)}"
        assert not missing, f"{d} 权威宜未并入我方宜: {sorted(missing)}"
        d += timedelta(days=1)
    assert scanned == 396


# ============================================================
# k23 补丁 F3: 哨兵值「无」过滤（lunar-python 无忌事日 getDayJi()=['无']）
# ============================================================

def test_sentinel_wu_anchor_2026_02_10():
    """2026-02-10 实证: 权威 getDayJi()=['无']（无忌事, 宜 24 项）——
    哨兵不得进用户面宜忌; 该日建除「除」表忌（嫁娶/出行/开市/入宅/安床）全部被
    神煞级黄历宜覆盖（K3-A3）→ 过滤后忌为空列表（渲染文案待产品拍板）。"""
    authority_ji = list(_lunar("2026-02-10").getDayJi())
    assert authority_ji == ["无"], f"测试前提: 权威忌应为哨兵, 实际 {authority_ji}"
    r = engine.select(2026, 2, 10)
    assert r.jianchu == "除"
    assert "无" not in r.yi and "无" not in r.ji, f"哨兵泄漏: yi={r.yi} ji={r.ji}"
    assert len(r.yi) >= 24, f"该日神煞级宜应完整保留: {len(r.yi)} 项"
    assert {"嫁娶", "出行", "开市", "入宅", "安床"} <= set(r.yi)
    assert r.ji == [], f"建除表忌全被神煞级宜覆盖 → 忌应为空, 实际 {r.ji}"
    # 计划路径（卡片）同源：同样不得出现哨兵
    res = engine.select_lucky_days("搬家", "2026-02-10", "2026-02-10")
    for c in res["cards"]:
        assert "无" not in c.yi and "无" not in c.ji


def test_sentinel_wu_yi_side_real_day_2024_04_06():
    """宜侧哨兵的真实实例（防御分支）: 2024-04-06 权威 getDayYi()==['无']。
    2020-2035 宜侧哨兵仅 10 天（2020-04-27 / 2021-04-22 / 2022-04-17 / 2023-04-12 /
    2024-04-06 / 2031-04-30 / 2032-04-24 / 2033-04-19 / 2034-04-14 / 2035-04-09,
    均不在 2026 扫描区间）—— 该日我方可依建除表正常出宜, 不得把哨兵当宜项;
    其忌「诸事不宜」是语义项必须保留（与哨兵不同: K3-A1 在计划路径据此排除,
    chat 路径如实展示作警示）。"""
    assert list(_lunar("2024-04-06").getDayYi()) == ["无"], "测试前提: 权威宜应为哨兵"
    r = engine.select(2024, 4, 6)
    assert r.jianchu == "成"
    assert "无" not in r.yi and "无" not in r.ji, f"哨兵泄漏: yi={r.yi} ji={r.ji}"
    assert r.yi == list(JIANCHU_YI_JI[r.jianchu]["yi"]), \
        f"宜侧哨兵过滤后应仅剩建除表宜: {r.yi}"
    assert "诸事不宜" in r.ji, "「诸事不宜」是语义项, 不得被哨兵过滤连带剔除"


def test_sentinel_wu_never_leaks_whole_year():
    """全年扫描（2026）: select() 宜/忌任何一天都不得出现哨兵「无」。
    权威侧当年 13 天 getDayJi()==['无']（01-06/01-11/02-10/04-08/05-11/05-12/
    06-05/07-07/07-09/09-12/10-08/11-07/11-09）, 修复前这 13 天会输出「忌：无」。"""
    from datetime import date, timedelta
    sentinel_days = ["2026-01-06", "2026-01-11", "2026-02-10", "2026-04-08",
                     "2026-05-11", "2026-05-12", "2026-06-05", "2026-07-07",
                     "2026-07-09", "2026-09-12", "2026-10-08", "2026-11-07",
                     "2026-11-09"]
    for ds in sentinel_days:
        assert list(_lunar(ds).getDayJi()) == ["无"], f"测试前提失效: {ds} 权威忌非哨兵"
    d, end, scanned = date(2026, 1, 1), date(2026, 12, 31), 0
    while d <= end:
        scanned += 1
        r = engine.select(d.year, d.month, d.day)
        assert "无" not in r.yi and "无" not in r.ji, f"{d} 出现哨兵: {r.yi} / {r.ji}"
        d += timedelta(days=1)
    assert scanned == 365


# ============================================================
# k23 补丁 F1: 固化 chat 路径「综合判定」现行语义（只钉不改）
# ============================================================

def test_judge_overall_semantics_pinned_k23():
    """F1 只钉不改 —— chat 路径 `_judge_overall` 吉凶语义: **此语义于 k23 确立, 待产品拍板**。

    k23 把宜忌事实源换成合并后的列表（建除表 + 神煞级黄历, 且 K3-A3 神煞级优先），
    而 `_judge_overall` 的用途匹配直接消费该列表:
      - 忌表多出神煞级黄历忌 → `purpose in ji`（-1 分）更易命中;
      - 宜表含神煞级黄历宜, 且 `_adjust_by_purpose` 作用于合并后列表 → `yi[0]`
        命中用途（+1 分）也更易发生。
    结果: 2026 全年相对 k22 前, 出行 63/365 + 嫁娶 85/365 = **148/730** 日期×用途
    的吉凶翻转（审查口径）。判定逻辑本身未改（不在 k23 范围）; 本测试固化现状,
    防止无声漂移 —— 若产品拍板要改语义, 请连同本测试一并更新。

    锚点当前值（每例含 purpose, 复盘用）:
      2026-10-01 出行=凶（闭日 -2 + 奎凶 -1 + 宜首项命中 +1）; 10-01 搬家=凶（入宅 -0）
      2027-01-01 出行=吉; 2026-10-14 嫁娶=平; 2026-09-20 安葬=吉; 2026-02-10 嫁娶=吉
    """
    cases = [
        ((2026, 10, 1), "出行", "凶"),
        ((2026, 10, 1), "搬家", "凶"),
        ((2027, 1, 1), "出行", "吉"),
        ((2026, 10, 14), "嫁娶", "平"),
        ((2026, 9, 20), "安葬", "吉"),
        ((2026, 2, 10), "嫁娶", "吉"),
    ]
    for (y, m, d), purpose, expect in cases:
        assert engine.select(y, m, d, purpose=purpose).overall == expect, \
            f"{y}-{m:02d}-{d:02d} purpose={purpose} 吉凶语义漂移（k23 钉住值 {expect}）"


def test_judge_overall_distribution_pinned_k23():
    """F1 同前（只钉不改）: 2026 全年 × {出行, 嫁娶} = 730 组合的吉凶分布指纹。
    分布变化 = 判定语义或宜忌事实源被改动 → 必须先拍板再改本测试。"""
    from datetime import date, timedelta
    expect = {
        "出行": {"平": 115, "吉": 170, "凶": 80},
        "嫁娶": {"凶": 83, "平": 120, "吉": 162},
    }
    for purpose, want in expect.items():
        dist, d = {}, date(2026, 1, 1)
        while d <= date(2026, 12, 31):
            v = engine.select(d.year, d.month, d.day, purpose=purpose).overall
            dist[v] = dist.get(v, 0) + 1
            d += timedelta(days=1)
        assert dist == want, f"{purpose} 吉凶分布漂移（k23 钉住 {want}）: {dist}"
        assert sum(want.values()) == 365
