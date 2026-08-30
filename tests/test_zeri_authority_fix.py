"""K3 择吉排除规则修复 · 权威锚点测试（对比报告 /tmp/compare_zeri.md，只读参考）。

背景（用户铁律：对不上=我们 bug 必须修）: 2026-08-30 择吉批双向对比结论
历法层无 bug（建除/宿/冲 21/21=100%），误报全集中在前置排除规则与建除表关键词，
三吉日命中率 10/15=66.7%。K3 修复 5 处（A1 诸事不宜/馀事勿取未排除、A2 危日未排除、
A3 闭日排除过严、A4 建日宜表含嫁娶、A5 出行关键词过宽），目标命中率 ≥13/15。

本文件锁定:
1. 误报五例（12/12、11/7、11/16、9/20、12/5）修复后不再报 + 漏报一例（10/1）恢复;
2. 15 案例命中率复测（5 窗口 × Top3 ∩ 权威吉日表 ≥13/15）;
3. 排除规则专项（诸事不宜/馀事勿取、危日、闭日、建日+嫁娶、出行 yi 关键词）;
4. 历法字段（建除/宿/冲）与报告锚点全等（此层无 bug，防回归）。
"""
import pytest

from src.engines.zeri import (
    ZeriEngine,
    JIANCHU_YI_JI,
    SCENES,
)

engine = ZeriEngine()

# 权威吉日表（报告 §四/§三，lhl 分类吉日表解析 + xzw 仲裁）:
#   Z1 嫁娶 9 月=[4,5,6,7,9,12,17,19,21,28]; Z2 搬家 10 月=[1,3,4,6,11,13,15,23,24,26,28]
#   Z3 开业 11 月=[4,12,13,19,22,24]; Z4 出行/签约 12-01~15=[1,3,4,6] / 签约=[6]
Z1_MARRY_AUTH = [4, 5, 6, 7, 9, 12, 17, 19, 21, 28]
Z2_MOVE_AUTH = [1, 3, 4, 6, 11, 13, 15, 23, 24, 26, 28]
Z3_OPEN_AUTH = [4, 12, 13, 19, 22, 24]
Z4_CHUXING_AUTH = [1, 3, 4, 6]
Z4_QIANYUE_AUTH = [6]

# 历法层锚点（报告 §三案例表 20 日，逐日建除/宿/冲，我方与权威 21/21 全等）
CALENDAR_ANCHORS = {
    # Z1 嫁娶窗口
    (2026, 9, 1): ("破", "室", "猴"), (2026, 9, 14): ("破", "张", "鸡"),
    (2026, 9, 15): ("危", "翼", "狗"), (2026, 9, 16): ("成", "轸", "猪"),
    (2026, 9, 30): ("开", "壁", "牛"),
    # Z2 搬家窗口
    (2026, 10, 1): ("闭", "奎", "虎"), (2026, 10, 14): ("闭", "轸", "兔"),
    (2026, 10, 15): ("建", "角", "龙"), (2026, 10, 16): ("除", "亢", "蛇"),
    (2026, 10, 31): ("定", "胃", "猴"),
    # Z3 开业窗口
    (2026, 11, 1): ("执", "昴", "鸡"), (2026, 11, 14): ("执", "氐", "狗"),
    (2026, 11, 15): ("破", "房", "猪"), (2026, 11, 16): ("危", "心", "鼠"),
    (2026, 11, 30): ("收", "毕", "虎"),
    # Z4 出行+签约窗口
    (2026, 12, 1): ("开", "觜", "兔"), (2026, 12, 5): ("满", "柳", "羊"),
    (2026, 12, 8): ("定", "翼", "狗"), (2026, 12, 12): ("成", "氐", "虎"),
    (2026, 12, 15): ("闭", "尾", "蛇"),
}


def _top3_dates(scene: str, start: str, end: str) -> list:
    res = engine.select_lucky_days(scene, start, end)
    return [c.date for c in res["cards"]]


# ============================================================
# 1. 误报五例 + 漏报一例（单日窗口确定性断言）
# ============================================================

def test_k3_misreport_12_12_zhushi_avoid_not_lucky():
    """A1: 2026-12-12 忌=诸事不宜(宜仅解除扫舍馀事勿取) → 任何场景不得为吉日。
    报告: lhl 12-12 页「忌: 诸事不宜」; 权威签约 12 月表无 12/12（Z4b 误报实锤）。"""
    assert engine.select_lucky_days("签约", "2026-12-12", "2026-12-12")["cards"] == []
    # 场景无关: 出行/搬家同样不报
    assert engine.select_lucky_days("出行", "2026-12-12", "2026-12-12")["cards"] == []
    assert engine.select_lucky_days("搬家", "2026-12-12", "2026-12-12")["cards"] == []


def test_k3_misreport_11_07_yushiwuqu_not_lucky():
    """A1: 2026-11-07 宜=解除+馀事勿取(xzw 宜打扫/忌馀事勿取) → 不得报为开业吉日。
    报告: 11/7 曾为开业 Top1(84) 严重误报; 权威开业 11 月表无 11/7。"""
    assert engine.select_lucky_days("开业", "2026-11-07", "2026-11-07")["cards"] == []


def test_k3_misreport_11_16_wei_day_not_lucky():
    """A2: 2026-11-16 危日 → 出行场景不得误报（权威标准「排除破日、危日与诸事不宜之日」）。
    报告: 11/14-18 出行窗口 Top1=11/16(危日) 实锤误报; lhl 11/16 宜列出行但不入出行吉日表。"""
    assert engine.select_lucky_days("出行", "2026-11-16", "2026-11-16")["cards"] == []
    # 场景无关: 其他场景也不报危日
    assert engine.select_lucky_days("搬家", "2026-11-16", "2026-11-16")["cards"] == []


def test_k3_misreport_09_20_jian_day_not_marry():
    """A1+A4: 2026-09-20 建日(宜…馀事勿取; xzw 宜无嫁娶纳采) → 嫁娶不得误报。
    报告: 9/20 嫁娶 Top3 误报, 宜中嫁娶纳采完全来自 JIANCHU_YI_JI["建"]。"""
    assert engine.select_lucky_days("嫁娶", "2026-09-20", "2026-09-20")["cards"] == []


def test_k3_misreport_12_05_huifriend_not_chuxing():
    """A5: 2026-12-05 宜=会亲友/安机械…(无出行) → 出行不得误报。
    报告: xzw 12-05 宜无出行; 权威标准「出行吉日必须以黄历当日明确列出'出行'为准入」。"""
    assert engine.select_lucky_days("出行", "2026-12-05", "2026-12-05")["cards"] == []


def test_k3_recover_10_01_bi_day_move_day():
    """A3: 2026-10-01 闭日但神煞级黄历宜=冠笄沐浴出行修造动土移徙入宅破土安葬
    (lhl 当日宜与 lunar-python 全等) → 恢复为搬家吉日。报告: 权威搬家吉日 10 月表含 1 日。"""
    res = engine.select_lucky_days("搬家", "2026-10-01", "2026-10-01")
    assert len(res["cards"]) == 1
    assert res["cards"][0].date == "2026-10-01"
    # 对照: 10-14 同为闭日但神煞级宜无入宅/移徙(lhl 忌入宅) → 仍不报
    assert engine.select_lucky_days("搬家", "2026-10-14", "2026-10-14")["cards"] == []


# ============================================================
# 2. 15 案例命中率复测（5 窗口 × Top3 ∩ 权威吉日表）
# ============================================================

def test_k3_fifteen_case_hit_rate():
    """5 窗口 15 案例命中率 ≥13/15（修复前 10/15=66.7%）。
    窗口: Z1 嫁娶 09-01~30 / Z2 搬家 10-01~31 / Z3 开业 11-01~30 /
    Z4a 出行 12-01~15 / Z4b 签约 12-01~15。"""
    cases = [
        ("嫁娶", "2026-09-01", "2026-09-30", Z1_MARRY_AUTH, 2),
        ("搬家", "2026-10-01", "2026-10-31", Z2_MOVE_AUTH, 3),
        ("开业", "2026-11-01", "2026-11-30", Z3_OPEN_AUTH, 2),
        ("出行", "2026-12-01", "2026-12-15", Z4_CHUXING_AUTH, 2),
        ("签约", "2026-12-01", "2026-12-15", Z4_QIANYUE_AUTH, 1),
    ]
    detail = []
    total_hits = 0
    for scene, start, end, auth, need in cases:
        tops = _top3_dates(scene, start, end)
        hits = [d for d in tops if int(d[8:]) in auth]
        total_hits += len(hits)
        detail.append(f"{scene} {start}~{end}: Top3={tops} 命中={len(hits)}/3(需≥{need})")
        assert len(hits) >= need, f"{scene} 窗口命中不足: {detail[-1]}"
    print("\n".join(detail))
    assert total_hits >= 13, f"15 案例命中率 {total_hits}/15 < 13/15\n" + "\n".join(detail)


# ============================================================
# 3. 排除规则专项
# ============================================================

def test_k3_rule_zhushi_avoid_exclusion():
    """诸事不宜/馀事勿取日排除: 前置排除(场景无关), 靠 lunar-python 忌/宜词识别。"""
    from lunar_python import Solar
    # 12/12 忌=诸事不宜 → 排除; 11/7 宜=解除+馀事勿取 → 排除; 9/20 宜=…馀事勿取 → 排除
    l1212 = Solar.fromYmd(2026, 12, 12).getLunar()
    l1107 = Solar.fromYmd(2026, 11, 7).getLunar()
    l0920 = Solar.fromYmd(2026, 9, 20).getLunar()
    assert "诸事不宜" in l1212.getDayJi()
    assert "馀事勿取" in l1107.getDayYi()
    assert "馀事勿取" in l0920.getDayYi()
    # 正常吉日不受影响: 12/6 签约吉日仍可选
    assert engine.select_lucky_days("签约", "2026-12-06", "2026-12-06")["cards"]


def test_k3_rule_wei_day_excluded_all_scenes():
    """危日排除: 全场景 jianchu_avoid 含"危"（权威标准排除危日）。"""
    for scene, cfg in SCENES.items():
        assert "危" in cfg["jianchu_avoid"], f"{scene} 缺危日排除"
        assert "破" in cfg["jianchu_avoid"], f"{scene} 缺破日排除"


def test_k3_rule_bi_day_not_excluded_all_scenes():
    """闭日不再排除: 全场景 jianchu_avoid 不含"闭"（权威不排除闭日, 只排除破/危/诸事不宜）。"""
    for scene, cfg in SCENES.items():
        assert "闭" not in cfg["jianchu_avoid"], f"{scene} 仍排除闭日"


def test_k3_rule_jian_day_marry_keywords_removed():
    """建日宜表去除嫁娶/纳采（A4）: 建日不宜嫁娶(主流黄历, xzw 9/20 + lhl 10/15 实证)。"""
    assert "嫁娶" not in JIANCHU_YI_JI["建"]["yi"]
    assert "纳采" not in JIANCHU_YI_JI["建"]["yi"]
    # 建日其他建事类宜词保留
    assert {"上梁", "起基", "开市", "纳财"} <= set(JIANCHU_YI_JI["建"]["yi"])
    # 建日单日 select 不再出现嫁娶/纳采（2026-10-15 建日）
    r = engine.select(2026, 10, 15)
    assert r.jianchu == "建"
    assert "嫁娶" not in r.yi and "纳采" not in r.yi


def test_k3_rule_chuxing_yi_keywords():
    """出行场景 yi_hits 只认"出行"（A5）: 权威「出行吉日必须以黄历当日明确列出'出行'为准入」。"""
    assert SCENES["出行"]["yi_hits"] == ["出行"]
    assert "会亲友" not in SCENES["出行"]["yi_hits"]
    assert "祈福" not in SCENES["出行"]["yi_hits"]


def test_k3_phenomenal_priority_scoring_rule():
    """神煞级优先评分（K3-A4/A5 补完, 报告 A4 同类风险）:
    场景命中只算 lunar-python 当日神煞级黄历宜, 建除表宜仅展示不驱动评分。
    三个 建除表词 驱动误报的实证日修复后不再报:
    - 11/17 成日(开市/纳财仅来自建除"成"表, 当日黄历宜无) → 开业不报
      (11/17 不在权威开业 11 月吉日表 [4,12,13,19,22,24])
    - 12/8 定日(交易/纳财仅来自建除"定"表) → 签约不报 (报告 Z4 案例表 ❌ 误报)
    - 9/30 开日(嫁娶仅来自建除"开"表) → 嫁娶不报 (报告 B3 方向冲突例)
    """
    assert engine.select_lucky_days("开业", "2026-11-17", "2026-11-17")["cards"] == []
    assert engine.select_lucky_days("签约", "2026-12-08", "2026-12-08")["cards"] == []
    assert engine.select_lucky_days("嫁娶", "2026-09-30", "2026-09-30")["cards"] == []
    # 神煞级宜命中的吉日不受影响: 12/6 定日(当日黄历宜交易纳财) 签约仍可选
    assert engine.select_lucky_days("签约", "2026-12-06", "2026-12-06")["cards"]


# ============================================================
# 4. 历法字段回归（此层无 bug，防回归）+ 失败路径
# ============================================================

def test_k3_calendar_layer_anchors_unchanged():
    """历法层 20 日锚点（建除/宿/冲）与对比报告全等 —— 修复只动前置排除与关键词, 历法零变化。"""
    for (y, m, d), (jc, xiu, chong) in CALENDAR_ANCHORS.items():
        r = engine.select(y, m, d)
        assert r.jianchu == jc, f"{y}-{m}-{d} 建除应为{jc}, 实际{r.jianchu}"
        assert r.ershibaxiu == xiu, f"{y}-{m}-{d} 宿应为{xiu}, 实际{r.ershibaxiu}"
        assert chong in r.chong, f"{y}-{m}-{d} 冲应为{chong}, 实际{r.chong}"


def test_k3_cross_year_window():
    """失败路径: 窗口跨年不崩且行为正确（2026-12-28 ~ 2027-01-05）。"""
    res = engine.select_lucky_days("搬家", "2026-12-28", "2027-01-05")
    assert res["scanned"] == 9
    for c in res["cards"]:
        assert c.date[:4] in ("2026", "2027")
        assert c.total == c.scene_score + c.personal_score + c.practical_score


def test_k3_empty_unknown_inputs():
    """失败路径: 未知场景抛 ValueError; select 空 purpose 正常工作。"""
    with pytest.raises(ValueError):
        engine.select_lucky_days("", "2026-12-01", "2026-12-15")
    with pytest.raises(ValueError):
        engine.select_lucky_days("经商", "2026-12-01", "2026-12-15")
    r = engine.select(2026, 12, 1, purpose="")
    assert r.jianchu in ("建", "除", "满", "平", "定", "执", "破", "危", "成", "收", "开", "闭")


def test_k3_interface_shape_unchanged():
    """接口契约零破坏: 卡片字段形状不变, Top3 排序与 suggest_wider 语义不变。"""
    res = engine.select_lucky_days("嫁娶", "2026-09-01", "2026-09-30")
    assert set(res.keys()) == {"cards", "scanned", "suggest_wider", "reason"}
    assert res["scanned"] == 30
    assert len(res["cards"]) == 3 and res["suggest_wider"] is False and res["reason"] is None
    totals = [c.total for c in res["cards"]]
    assert totals == sorted(totals, reverse=True)
    for c in res["cards"]:
        assert {"date", "lunar_text", "yi", "ji", "jishi", "xi_fangwei",
                "cai_fangwei", "scene_score", "personal_score", "practical_score",
                "total", "reason_source"} <= set(c.__dataclass_fields__)
