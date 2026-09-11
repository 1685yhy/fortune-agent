"""k23 择吉口径统一 — chat/工具路径与计划路径共用单一事实源（数据一致性）。

背景: K3-A3(神煞级优先, 建除表忌与当日神煞级黄历宜冲突时以神煞级宜为准) 此前
只落在计划路径 `_build_lucky_card`, chat/工具路径 `ZeriResult.select()` 仍返回
裸建除表宜忌 → 同一日期两条路径方向相反。实测抽查 8 日有 4 日方向相反:
10-01(出行/入宅/移徙)、2027-01-01(出行/移徙)、10-14(嫁娶)、09-20(安葬)。

修复: K3-A3 下沉到单日宜忌单一事实源 `ZeriEngine._day_yi_ji()`,
`select()`(chat/工具/意图路径) 与 `_build_lucky_card()`(计划路径) 共用, 不得各自
再合并/过滤（数据一致性铁律: 同一数据单一事实源）。

断言依据: 项目自带 lunar-python 万年历权威口径（getDayYi/getDayJi）。

k27（2026-09-11, 产品拍板「对齐权威黄历」）在本文件追加两段:
1. 宜忌**双向**消解不变量（yi ∩ ji = ∅ / 权威忌 ∩ 我宜 = ∅）—— 修复前 75/365 天
   同一词既宜又忌; 三面（万年历/择吉/聊天）统一消费 `ZeriEngine._day_yi_ji`;
2. `overall` 三分语义与卡片准入同源 —— 修复前 2026-10-01 搬家 卡片 total=64 推荐
   ↔ chat「综合判定：凶」（当年 8 例「出卡却判凶」）。

k27c（2026-09-11, 产品拍板两口径）在本文件追加第三段:
3. **不带用途时的「通用吉信号」** —— 无用途分支按「非破/危/诸事不宜 且权威宜表
   非空 → 吉」判定（`purpose=""` 也能给肯定答复）; 带用途分支逐字不变。

k27d（2026-09-11, 产品拍板「凶只留给真正诸事不宜的日子」）修订无用途分支的
凶/平边界（**只改无用途分支, 带用途分支逐字不变**）:
4. 凶 侧不再计入「馀事勿取」（归平）—— 凶 = 破/危 或 含「诸事不宜」（宜/忌任一侧）;
   平 = 非凶 且（权威宜表为空 或 含「馀事勿取」）。卡片准入（`_build_lucky_card`
   前置排除）仍按全量排除词, 与 chat 的差异是有意保留的不对称（平 ≠ 吉）。
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
# k27: 宜忌双向消解（自洽）+ overall 与卡片准入同源（三分）
# ============================================================

def test_day_yi_ji_self_consistent_whole_year_2026():
    """k27 不变量: 2026 全年 365 天 `set(yi) & set(ji) == ∅`（0 例外）。

    修复前实测 **75/365** 天同一词既宜又忌 —— `_day_yi_ji` 只单向消解（建除表忌
    ∩ 黄历宜 → 归宜），反方向「建除表宜 ∩ 黄历忌」漏修，例 2026-01-13（建除
    「开」表宜 嫁娶）∩ 当日黄历忌 嫁娶 → 一次回答里同时说「宜嫁娶」和「忌嫁娶」。
    """
    from datetime import date, timedelta
    d, end, scanned, bad = date(2026, 1, 1), date(2026, 12, 31), 0, []
    while d <= end:
        scanned += 1
        r = engine.select(d.year, d.month, d.day)
        inter = set(r.yi) & set(r.ji)
        if inter:
            bad.append((d.isoformat(), sorted(inter)))
        d += timedelta(days=1)
    assert scanned == 365
    assert bad == [], f"同词既宜又忌 {len(bad)} 天（修复前 75 天）: {bad[:8]}"


def test_day_yi_ji_reverse_conflict_anchor_2026_01_13():
    """k27 反方向消解锚点: 2026-01-13（建除「开」）建除表宜 嫁娶 与当日黄历忌
    嫁娶 冲突 → 以黄历为准归**忌**（词只出现在忌, 不再既宜又忌）。

    同型: 2026-01-04（满日）开市/祈福、2026-01-10（危日）安床/纳畜。"""
    r = engine.select(2026, 1, 13)
    assert r.jianchu == "开"
    assert "嫁娶" in _lunar("2026-01-13").getDayJi(), "测试前提: 权威忌应含 嫁娶"
    assert "嫁娶" in r.ji and "嫁娶" not in r.yi, f"yi={r.yi} ji={r.ji}"
    # 建除表内部自洽（反方向消解的结构前提: 同表内无词同时列于宜与忌）
    from src.engines.zeri import JIANCHU_YI_JI as _T
    for jc, tbl in _T.items():
        assert set(tbl["yi"]) & set(tbl["ji"]) == set(), f"建除表{jc}日 宜忌自相交"


def test_authority_ji_never_in_my_yi_whole_year_2026():
    """k27 反方向不变量: 权威忌 ∩ 我方宜 = ∅（2026 全年 0 例外）——
    与既有的「权威宜 ∩ 我方忌 = ∅」对称。建除表为 12 日周期粗粒度近似,
    与神煞级黄历冲突时两侧均以黄历为准（A 口径）。"""
    from datetime import date, timedelta
    d, end, scanned, bad = date(2026, 1, 1), date(2026, 12, 31), 0, []
    while d <= end:
        scanned += 1
        authority_ji = {x for x in _lunar(d.isoformat()).getDayJi() if x != "无"}
        inter = authority_ji & set(engine.select(d.year, d.month, d.day).yi)
        if inter:
            bad.append((d.isoformat(), sorted(inter)))
        d += timedelta(days=1)
    assert scanned == 365
    assert bad == [], f"权威忌被列为宜 {len(bad)} 天: {bad[:8]}"


def test_card_recommended_implies_overall_ji_2026():
    """k27 同源底线（现象 1 反例的通用形式）: 2026 全年逐日, **计划路径出卡（推荐）
    ⟹ chat overall == "吉"**（7 场景, 比「≠凶」更强; 当日即用户可见的「同日同用途
    两面一致」）。

    由构造成立: 卡片准入 = 场景 yi_hits 命中（`_scene_score` ≥20）+ 无
    `cfg["ji_hits"]` 命中 + 非破/危/诸事不宜/馀事勿取 —— 逐条被 `_judge_overall`
    的三分判据包含, 故「出卡却判凶/判平」不可能。
    实测: 7 场景 |出卡 − 吉| 全 0; 搬家/提车 两向相等（吉 ⟺ 出卡）。
    残留（反向）: 出行 14 / 嫁娶 20 / 签约 6 / 晋升 4 / 开业 2 天「吉但无卡」,
    成因均为卡片另有神煞排除/冲生肖（空亡/三娘煞/月破月刑/冲宅主）, 非吉判据可表达。
    修复前锚点: 2026-10-01 搬家 卡片 total=64 推荐 ↔ chat「综合判定：凶」（当年 8 例）。
    k27 审查 I1 后更新: 「诸事不宜」宜侧纳凶（见
    test_overall_2026_05_02_zhushi_buyi_yi_side_is_xiong_k27）使 2026-05-02
    签约/提车/晋升 由「吉+出卡」变「凶+无卡」→ 提车 (170,170) 降为 (169,169);
    搬家 (95,95) 与 出行 (95,109,0,14) 不变（该日两场景本无卡）。
    """
    from datetime import date, timedelta
    from src.engines.zeri import SCENES as _S
    total_cards, not_ji, both = {}, [], {}
    for purpose in ("搬家", "出行", "嫁娶", "开业", "签约", "提车", "晋升"):
        d, end, cards, ji_days = date(2026, 1, 1), date(2026, 12, 31), 0, 0
        card_dates, ji_dates = set(), set()
        while d <= end:
            ds = d.isoformat()
            ov = engine.select(d.year, d.month, d.day, purpose=purpose).overall
            if ov == "吉":
                ji_days += 1
                ji_dates.add(ds)
            if engine.select_lucky_days(purpose, ds, ds)["cards"]:
                cards += 1
                card_dates.add(ds)
                if ov != "吉":
                    not_ji.append((purpose, ds, ov))
            d += timedelta(days=1)
        total_cards[purpose] = cards
        both[purpose] = (cards, ji_days, len(card_dates - ji_dates), len(ji_dates - card_dates))
        assert _S[purpose], purpose
    assert sum(total_cards.values()) > 500, f"出卡天数异常: {total_cards}"
    assert not not_ji, f"卡片推荐但 chat 非吉 {len(not_ji)} 例: {not_ji[:8]}"
    # 搬家/提车 两向相等（该两场景无神煞排除项, 吉 ⟺ 出卡）
    assert both["搬家"] == (95, 95, 0, 0), both["搬家"]
    assert both["提车"] == (169, 169, 0, 0), both["提车"]
    assert both["出行"] == (95, 109, 0, 14), both["出行"]


def test_overall_2026_10_01_move_day_card_and_chat_agree():
    """现象 1 锚点反例消失（k27 验收）: 2026-10-01 搬家 ——
    计划路径出卡（total=64, 权威吉日）且 chat overall=吉, 两边不再相反;
    对照: 权威忌 嫁娶 → purpose=嫁娶 不为吉（不把黄历明示之忌误报为吉）。"""
    res = engine.select_lucky_days("搬家", "2026-10-01", "2026-10-01")
    assert len(res["cards"]) == 1 and res["cards"][0].total == 64
    r = engine.select(2026, 10, 1, purpose="搬家")
    assert r.overall == "吉", r.overall
    assert engine.select(2026, 10, 1, purpose="嫁娶").overall != "吉"


def test_overall_2026_05_02_zhushi_buyi_yi_side_is_xiong_k27():
    """k27 审查 I1 修复锚点: 「诸事不宜」在**宜列**时同样纳凶（与「馀事勿取」两侧对称）。

    权威把「诸事不宜」放在**宜列**的有 81 天（2020-2035; 与忌列 649 天互不相交）,
    修复前凶判据只查忌侧（`"诸事不宜" in ji`）→ 这类日既不判凶、卡片也照出。

    锚点 2026-05-02（建除「成」, 当日权威宜含「诸事不宜」、忌不含 —— 该日只能由
    宜侧判据拦下）修复前实测: purpose=签约/提车/晋升 判「吉」且计划路径出卡
    （total=54）—— 用户看到「诸事不宜之日被推荐且判吉」。

    要求两侧同步: `_judge_overall` 的凶判据与 `_build_lucky_card` 的前置排除
    **同一处判据**（`has_yi_ji_exclusion_word`）, 否则「出卡 ⟹ 非凶」的构造性失效。
    """
    y, m, d = 2026, 5, 2
    # 锚点前提: 权威宜含、忌不含（排除「忌侧早已拦下」的可能, 保证测试有区分力）
    auth_yi = list(_lunar("2026-05-02").getDayYi())
    auth_ji = list(_lunar("2026-05-02").getDayJi())
    assert "诸事不宜" in auth_yi and "诸事不宜" not in auth_ji, (auth_yi, auth_ji)

    r = engine.select(y, m, d)
    assert r.jianchu == "成" and "诸事不宜" in r.yi and "诸事不宜" not in r.ji, \
        (r.jianchu, r.yi, r.ji)

    # 1) 凶判据: 无 purpose 与 7 个用途一律凶
    assert r.overall == "凶", f"无 purpose: {r.overall}"
    for purpose in ("签约", "提车", "晋升", "搬家", "嫁娶", "开业", "出行"):
        ov = engine.select(y, m, d, purpose=purpose).overall
        assert ov == "凶", f"2026-05-02 purpose={purpose} 应为凶（宜含诸事不宜）: {ov}"

    # 2) 卡片前置排除: 全场景不得出卡（修复前 签约/提车/晋升 各出 1 张 total=54）
    for scene in ("签约", "提车", "晋升", "搬家", "嫁娶", "开业", "出行"):
        cards = engine.select_lucky_days(scene, "2026-05-02", "2026-05-02")["cards"]
        assert cards == [], f"2026-05-02 scene={scene} 不应出卡: {cards}"

    # 3) 共用判据两侧对称（凶判据与卡片排除同源, 不得只改一侧）
    from src.engines.zeri import has_yi_ji_exclusion_word as _hit
    assert _hit(["诸事不宜"], []) is True          # 宜侧「诸事不宜」（本 I1 修复点）
    assert _hit([], ["诸事不宜"]) is True          # 忌侧「诸事不宜」（既有）
    assert _hit(["馀事勿取"], []) is True          # 宜侧「馀事勿取」（既有）
    assert _hit([], ["馀事勿取"]) is True          # 忌侧「馀事勿取」（既有）
    assert _hit(["出行", "入宅"], ["嫁娶"]) is False


# ============================================================
# k27: 固化 chat 路径「综合判定」三分语义（替换 k23 的只钉不改版）
# ============================================================

def test_judge_overall_semantics_three_way_k27():
    """k27 三分语义（产品 2026-09-11 拍板 · 选项 E, 取代 k23 的「只钉不改」版）:

      - 破 / 危 / 诸事不宜 / 馀事勿取 → **凶**（= 卡片前置排除规则, 逐条同源;
        故「出卡 ⟹ 非凶」由构造成立, 见 test_card_recommended_implies_...）;
      - 给 purpose 且当日**神煞级黄历宜**命中该用途 → **吉**
        （= 卡片场景准入信号 `_scene_score` 的输入口径, K3-A5）;
      - 其余 → **平**。

    被替换的 k23 语义 = 建除 quality(±2) + 二十八宿吉凶(±1) + 用途(±1), 阈值 6/3
    —— 与卡片判据无一处同维度, 2026-10-01 搬家（权威吉日, 卡片 total=64）被判
    「凶」正是本批要消除的矛盾。建除 quality/二十八宿**不再驱动** overall
    （仍供万年历 quality/宿展示; JIANCHU_QUALITY["危"]="吉" 与 K3「排除危日」相抵,
    是另一处口径错位, 本批一并归位）; 建除表宜仅展示不驱动评分（K3-A4/A5）。

    锚点值（每例含 purpose, 复盘用）:
      2026-10-01 出行=吉（黄历宜含出行）; 10-01 搬家=吉（宜含入宅/移徙, 卡片 total=64）;
      2027-01-01 出行=吉; 2026-10-14 嫁娶=吉（黄历宜含嫁娶, 同日卡片 total=44 —— 同向）;
      2026-09-20 安葬=凶（宜含「馀事勿取」→ 与卡片排除规则同源, 无卡可出）;
      2026-02-10 嫁娶=吉（除日, 黄历宜含嫁娶）;
      2026-01-13 嫁娶=平（黄历**忌**嫁娶 → 反向不误报为吉; 该日即 k27 反方向消解锚点）
    """
    cases = [
        ((2026, 10, 1), "出行", "吉"),
        ((2026, 10, 1), "搬家", "吉"),
        ((2027, 1, 1), "出行", "吉"),
        ((2026, 10, 14), "嫁娶", "吉"),
        ((2026, 9, 20), "安葬", "凶"),
        ((2026, 2, 10), "嫁娶", "吉"),
        ((2026, 1, 13), "嫁娶", "平"),
    ]
    for (y, m, d), purpose, expect in cases:
        got = engine.select(y, m, d, purpose=purpose).overall
        assert got == expect, \
            f"{y}-{m:02d}-{d:02d} purpose={purpose} 三分语义漂移（k27 钉住值 {expect}）: {got}"
    # 破/危 一律凶（与卡片 jianchu_avoid 全场景一致, 权威标准「排除破日、危日」）
    for ds in ("2026-11-15", "2026-11-16"):   # 破日 / 危日（K3 案例表锚点）
        y, m, d = _ymd(ds)
        r = engine.select(y, m, d, purpose="出行")
        assert r.jianchu in ("破", "危") and r.overall == "凶", f"{ds}: {r.jianchu}/{r.overall}"


def test_purpose_hit_words_scene_sourced_k27():
    """k27 审查修复回归: 吉判据词表取自择吉场景 `SCENES[..]["yi_hits"]`（与卡片准入同表）。

    修复前只拿 `PURPOSE_CATEGORIES` 的类目**键**比权威宜 —— 而「开业」在 lunar-python
    getDayYi 全区间 5844 天出现 0 次（权威词表用「开市」）→ purpose=开业 永不判吉
    （2026 实测 0 天吉, 而当年开业场景出卡 81 天）; 提车/晋升 无类目亦永不判吉。
    """
    from src.engines.zeri import SCENES as _S
    assert engine._purpose_hit_words("开业") == _S["开业"]["yi_hits"]
    assert engine._purpose_hit_words("搬家") == _S["搬家"]["yi_hits"]
    assert engine._purpose_hit_words("提车") == _S["提车"]["yi_hits"]
    assert engine._purpose_hit_words("晋升") == _S["晋升"]["yi_hits"]
    # 异名用途经类目→场景映射（入宅→搬家 / 交易→签约 / 入学→晋升）
    assert engine._purpose_hit_words("入宅") == _S["搬家"]["yi_hits"]
    assert engine._purpose_hit_words("结婚") == _S["嫁娶"]["yi_hits"]
    # 无对应场景的用途退回类目键（均为黄历词表实有词）; 无匹配 → 空表
    assert engine._purpose_hit_words("动土") == ["动土"]
    assert engine._purpose_hit_words("安葬") == ["安葬"]
    assert engine._purpose_hit_words("") == [] and engine._purpose_hit_words("zzz") == []
    # 回归: 2026 全年 开业/提车/晋升 均须有吉日（修复前开业为 0）
    from datetime import date, timedelta
    for purpose in ("开业", "提车", "晋升"):
        n = 0
        d = date(2026, 1, 1)
        while d <= date(2026, 12, 31):
            if engine.select(d.year, d.month, d.day, purpose=purpose).overall == "吉":
                n += 1
            d += timedelta(days=1)
        assert n > 50, f"{purpose} 吉日过少（修复前 0）: {n}"


def test_judge_overall_distribution_three_way_k27():
    """k27 同前: 2026 全年 × {出行, 嫁娶, 开业, 搬家} = 1460 组合的三分分布指纹。
    （k23 旧指纹 出行 {平115,吉170,凶80} / 嫁娶 {凶83,平120,吉162} 已随语义变更作废。）
    含「开业」是 k27 审查要求: 该用途是「吉判据词表必须走场景 yi_hits」的回归探针
    （只钉 出行/嫁娶 两个与权威同名的类目时, 开业永不判吉的缺陷不可见）。
    分布变化 = 判定语义或宜忌事实源被改动 → 须先拍板再改本测试。

    2026-09-11 k27 审查 I1 后重钉: 「诸事不宜」宜侧纳凶（锚点 2026-05-02, 见
    test_overall_2026_05_02_zhushi_buyi_yi_side_is_xiong_k27）—— 2026 宜侧
    「诸事不宜」共 3 天（03-07 / 03-19 / 05-02）, 其中 03-19 本已凶, 03-07 与
    05-02 由平转凶, 故四用途 平 −2 / 凶 +2（凶 120→122）; 吉 分量不变。"""
    from datetime import date, timedelta
    expect = {
        "出行": {"吉": 109, "平": 134, "凶": 122},
        "嫁娶": {"平": 131, "吉": 112, "凶": 122},
        "开业": {"平": 160, "吉": 83, "凶": 122},
        "搬家": {"吉": 95, "平": 148, "凶": 122},
    }
    for purpose, want in expect.items():
        dist, d = {}, date(2026, 1, 1)
        while d <= date(2026, 12, 31):
            v = engine.select(d.year, d.month, d.day, purpose=purpose).overall
            dist[v] = dist.get(v, 0) + 1
            d += timedelta(days=1)
        assert dist == want, f"{purpose} 三分分布漂移（k27 钉住 {want}）: {dist}"
        assert sum(want.values()) == 365


# ============================================================
# k27c/k27d: 不带用途分支（通用吉信号 + 馀事勿取归平）
# （产品 2026-09-11 拍板; k27 报告 §6-4 / k27d 报告 §10）
# ============================================================

# 空权威宜日（lunar.getDayYi() == ['无'] → 滤哨兵后为空）: 2020-2035 全部 10 天,
# 硬编码以便锚定「平分支在真实日期上的可达性」。
_KONG_YI_DAYS_2020_2035 = (
    "2020-04-27", "2021-04-22", "2022-04-17", "2023-04-12", "2024-04-06",
    "2031-04-30", "2032-04-24", "2033-04-19", "2034-04-14", "2035-04-09",
)
_EXCLUSION_WORDS = ("诸事不宜", "馀事勿取")


def test_overall_general_signal_without_purpose_k27c():
    """k27c 通用吉信号（**只在无用途分支生效**; 带用途分支逐字不变）:

    产品 2026-09-11 拍板（k27 报告 §6-4 遗留）: 不带用途泛问「今天怎么样」
    也要有肯定答复、且不再有三分之一日子听到「凶」。
    k27d 修订凶/平边界（见 test_overall_without_purpose_yuci_ping_k27d）后,
    本测试锚定的**吉侧**语义不变:

    - **吉**: 非凶 **且权威宜表非空** **且无「馀事勿取」**（`_authority_yi_ji` 宜侧,
      即 lunar-python getDayYi 滤哨兵后; 即 `_judge_overall` 的 `lunar_yi` 输入。
      不用合并后的 `yi` —— 后者恒含建除表宜、永不空, 空宜日正是「权威没给肯定
      信号」的日）;
    - **凶/平**: 见 test_overall_without_purpose_yuci_ping_k27d（k27d 口径）。

    锚点（2026, 实跑）:
    - 10-01（建除「闭」, 权威宜含出行/移徙/入宅, 无排除词）= 产品现象 1 当日
      （万年历「闭（凶）」↔ 聊天吉）→ 无用途为**吉**（k27 旧语义为平）;
    - 01-13 `purpose=嫁娶` → 平: 该日权威宜非空, 但用途分支以「命中用途」为准,
      通用吉信号**不得**越界把带用途的未命中日抬成吉。
    """
    # 1) 普通吉日 → 吉（且权威宜表非空 = 通用吉信号的输入）
    r = engine.select(2026, 10, 1)
    auth_yi, _ = engine._authority_yi_ji(_lunar("2026-10-01"))
    assert r.jianchu == "闭" and auth_yi, (r.jianchu, auth_yi)
    assert not any(w in x for w in _EXCLUSION_WORDS for x in r.yi + r.ji)
    assert r.overall == "吉", f"2026-10-01 无用途应为吉（k27 旧语义为平）: {r.overall}"

    # 2) 带用途分支不变: 权威宜非空但用途未命中 → 平（通用吉信号不越界）
    r13 = engine.select(2026, 1, 13, purpose="嫁娶")
    a13, _ = engine._authority_yi_ji(_lunar("2026-01-13"))
    assert a13 and r13.overall == "平", (a13, r13.overall)

    # 3) 平分支的「空权威宜」入口: 直接喂空权威宜（合成输入, 真实日期不可达）
    assert engine._judge_overall("成", "", [], ["嫁娶"], ["出行"]) == "平"

    # 4) 空宜日实测（2020-2035 全部 10 天）: 均因忌侧「诸事不宜」先判凶 → 该入口
    #    在真实日期上不可达（故平只由「馀事勿取」入口达, 见 k27d 测试）
    for ds in _KONG_YI_DAYS_2020_2035:
        auth, _ji = engine._authority_yi_ji(_lunar(ds))
        assert auth == [], f"{ds} 应为空权威宜（本锚点前提）: {auth}"
        y, m, d = _ymd(ds)
        assert engine.select(y, m, d).overall == "凶", ds


def test_overall_without_purpose_yuci_ping_k27d():
    """k27d 无用途三分的行为区分（产品 2026-09-11 拍板「凶只留给真正诸事不宜的日子」）:

    | 日型 | 判定 | 2026 锚点 |
    |---|---|---|
    | 含「馀事勿取」（宜/忌任一侧）, 非破/危/诸事不宜 | **平** | 01-06（宜侧）/ 08-19 |
    | 建除 破 / 危 | **凶** | 11-15（破）/ 11-16（危） |
    | 含「诸事不宜」（宜或忌任一侧） | **凶** | 05-02（宜侧） |
    | 其余（权威宜表非空） | **吉** | 10-01 |

    - 破/危 优先于「馀事勿取」: 2026 全年 29 个破日**全部**带「馀事勿取」
      （lunar-python 逐日表口径 → 实测）—— 若「馀事勿取」优先, 「凶 = 破日」
      这条判据将永不生效, 故凶判据先于平（本测试 01-06 vs 11-15 两锚点即钉住该序）;
    - 带用途分支**逐字未动**: 同一天 01-06（`purpose=""` → 平）在 `purpose=出行`
      下仍为 凶（`has_yi_ji_exclusion_word` 未改, 卡片准入仍全量排除）;
    - 卡片准入（`_build_lucky_card` 前置排除）**未动** → 上述平/凶日一律不出卡,
      「chat 非吉 而卡片不推荐」的不对称保持（出卡 ⟹ 非凶 仍由构造成立）。
    """
    # 1) 真实日期锚点: 平 / 凶 × 2 类 / 吉
    for ds, want, why in (
        ("2026-01-06", "平", "含「馀事勿取」(宜侧), 非破/危/诸事不宜"),
        ("2026-08-19", "平", "含「馀事勿取」(宜侧), 建除「执」"),
        ("2026-11-15", "凶", "破日（且带馀事勿取 —— 凶优先）"),
        ("2026-11-16", "凶", "危日"),
        ("2026-05-02", "凶", "权威宜侧含「诸事不宜」"),
        ("2026-10-01", "吉", "普通日（权威宜非空、无排除词）"),
    ):
        y, m, d = _ymd(ds)
        rr = engine.select(y, m, d)
        assert rr.overall == want, f"{ds}（{why}）应为{want}: {rr.overall}"
        words = rr.yi + rr.ji
        if want == "平":                      # 前提: 确实由「馀事勿取」入口落平
            assert any("馀事勿取" in x for x in words), (ds, words)
            assert not any("诸事不宜" in x for x in words), (ds, words)
            assert rr.jianchu not in ("破", "危"), ds
        if want == "吉":                      # 前提: 无任何排除词
            assert not any(w in x for w in _EXCLUSION_WORDS for x in words), (ds, words)

    # 2) 带用途分支不变: 同为「馀事勿取」日, 无用途=平, 带用途仍=凶（判据未动）
    assert engine.select(2026, 1, 6).overall == "平"
    for p in ("出行", "嫁娶", "签约"):
        assert engine.select(2026, 1, 6, purpose=p).overall == "凶", p
    # 破日两面同为凶
    assert engine.select(2026, 11, 15, purpose="出行").overall == "凶"

    # 3) 合成输入锁两侧（宜/忌任一侧都算命中, 与接线位置无关）
    assert engine._judge_overall("建", "", ["祈福"], ["祈福"], ["馀事勿取"]) == "平"   # 忌侧
    assert engine._judge_overall("建", "", ["祈福"], ["馀事勿取"], ["祈福"]) == "平"   # 宜侧
    assert engine._judge_overall("建", "", ["祈福"], ["祈福"], ["诸事不宜"]) == "凶"   # 忌侧
    assert engine._judge_overall("建", "", ["祈福"], ["诸事不宜"], ["祈福"]) == "凶"   # 宜侧
    assert engine._judge_overall("建", "", ["祈福"], ["祈福"], ["出行"]) == "吉"
    assert engine._judge_overall("破", "", ["祈福"], ["祈福"], ["出行"]) == "凶"
    assert engine._judge_overall("危", "", ["祈福"], ["祈福"], ["出行"]) == "凶"


def test_judge_overall_distribution_without_purpose_k27d():
    """k27d 无用途 2026 全年三分分布指纹（实跑值: 吉 243 / 平 44 / 凶 78）。

    口径（k27d, 只改无用途分支）:
    - **凶 78** = 破 29 ∪ 危 30 ∪ 含「诸事不宜」31 = 并集 78（破/危 与诸事不宜
      交集 12 天）—— 破/危 判据先于「馀事勿取」;
    - **平 44** = 含「馀事勿取」共 92 天中**不属上述三类**的部分（92 − 48）;
      另 48 天（破 29 全数 + 危 4 + 诸事不宜-only 15）因先判凶而不落平;
    - **吉 243** = 365 − 78 − 44（2026 空权威宜日 0 天, 故「权威宜非空」不扣减）。
    报批预期为 平 ~92 / 凶 ~30（= 把 92 个「馀事勿取」日整体移出凶桶的估算）;
    实际不符的原因即上两条 —— 若「馀事勿取」优先于破/危/诸事不宜, 则 29 个破日
    （全部带「馀事勿取」）会由凶转平, 「凶 = 破日」判据空转（见
    test_overall_without_purpose_yuci_ping_k27d 的 01-06 vs 11-15 两锚点）。

    分布变化 = 判定语义或宜忌事实源被改动 → 须先拍板再改本测试。
    """
    from datetime import date, timedelta
    dist = {"吉": 0, "平": 0, "凶": 0}   # 三桶预置 0: 任一桶为 0 也钉住（不得静默消失）
    d = date(2026, 1, 1)
    while d <= date(2026, 12, 31):
        dist[engine.select(d.year, d.month, d.day).overall] += 1
        d += timedelta(days=1)
    assert dist == {"吉": 243, "平": 44, "凶": 78}, f"无用途三分分布漂移: {dist}"
    assert sum(dist.values()) == 365
