"""小运（xiaoyun）+ 大运神煞（dyshensha）补全测试（对比报告 P3 项，2026-08-21）。

锚点数据 = 问真语料 data/wenzhen/wenzhen_charts.jsonl（8658 例，raw.xiaoyun /
raw.dyshensha 字段），本实现已全量反推对齐（8658/8658 例 100% 一致），此处固化
代表性案例防回归：
- 1900-01-01 00:00 女（己亥 丙子 甲戌 甲子）：顺行小运 + 12 步大运神煞全集
- 1940-12-31 23:00 女（庚辰 戊子 己酉 甲子）：晚子时归日 + 逆行小运 + 全集
- 1940-04-01 00:00 男（庚辰 己卯 甲戌 甲子）：天罗地网"互补成网"锚点（辛巳步）
断言口径：dyshensha 名序服务端无序（语料 49 对名称顺序两可），按集合比对。
"""
import pytest

from src.engines.bazi import BaziEngine

ENGINE = BaziEngine()


def _calc(date, time, gender):
    y, m, d = (int(x) for x in date.split("-"))
    h, mi = (int(x) for x in time.split(":"))
    return ENGINE.calculate(y, m, d, h, mi, "", gender)


# ---------------------------------------------------------------- 小运锚点
def test_xiaoyun_forward_anchor():
    """顺行小运：1900-01-01 00:00 女（阴年女顺排）——时柱甲子起顺推 110 条。

    语料 raw.xiaoyun[:5] = [乙丑, 丙寅, 丁卯, 戊辰, 己巳]，全 110 条逐位一致。
    """
    r = _calc("1900-01-01", "00:00", "女")
    assert r.bazi == ["己亥", "丙子", "甲戌", "甲子"]
    assert len(r.xiaoyun) == 110
    assert r.xiaoyun[:5] == ["乙丑", "丙寅", "丁卯", "戊辰", "己巳"]
    # 顺行：每步 +1（与语料逐位一致）
    from src.engines.bazi import SHENG_XU, SHENG_XU_MAP
    start = SHENG_XU_MAP["甲子"]
    assert r.xiaoyun == [SHENG_XU[(start + i) % 60] for i in range(1, 111)]


def test_xiaoyun_backward_anchor():
    """逆行小运（晚子时归日）：1940-12-31 23:00 女（庚辰阳年女逆排）——时柱甲子逆推。

    语料 raw.xiaoyun[:5] = [癸亥, 壬戌, 辛酉, 庚申, 己未]（晚子时 → 日柱次日口径）。
    """
    r = _calc("1940-12-31", "23:00", "女")
    assert r.bazi == ["庚辰", "戊子", "己酉", "甲子"]
    assert len(r.xiaoyun) == 110
    assert r.xiaoyun[:5] == ["癸亥", "壬戌", "辛酉", "庚申", "己未"]
    from src.engines.bazi import SHENG_XU, SHENG_XU_MAP
    start = SHENG_XU_MAP["甲子"]
    assert r.xiaoyun == [SHENG_XU[(start - i) % 60] for i in range(1, 111)]


# ---------------------------------------------------------------- 大运神煞锚点（12 步全集）
DYSHENSHA_19000101_F = [  # 语料 raw.dyshensha 全集（1900-01-01 女）
    ["丁丑", ["太极贵人", "天乙贵人", "月德合", "丧门"]],
    ["戊寅", ["国印贵人", "福星贵人", "德秀贵人", "勾绞煞", "禄神", "亡神", "孤辰", "词馆"]],
    ["己卯", ["德秀贵人", "羊刃", "将星", "桃花"]],
    ["庚辰", ["太极贵人", "空亡", "金舆", "红鸾"]],
    ["辛巳", ["文昌贵人", "天厨贵人", "德秀贵人", "天德贵人", "空亡", "驿马", "亡神"]],
    ["壬午", ["太极贵人", "月德贵人", "德秀贵人", "红艳煞", "元辰", "血刃", "将星"]],
    ["癸未", ["太极贵人", "福星贵人", "天乙贵人", "德秀贵人", "华盖"]],
    ["甲申", ["天乙贵人", "德秀贵人", "天德合", "空亡", "金舆", "劫煞", "披麻", "驿马"]],
    ["乙酉", ["文昌贵人", "天厨贵人", "空亡", "飞刃", "流霞", "灾煞", "吊客"]],
    ["丙戌", ["太极贵人", "国印贵人", "天罗地网", "德秀贵人", "寡宿", "天喜", "华盖"]],
    ["丁亥", ["天罗地网", "月德合", "天医", "劫煞", "学堂"]],
    ["戊子", ["天乙贵人", "太极贵人", "福星贵人", "德秀贵人", "桃花"]],
]

DYSHENSHA_19401231_F = [  # 语料 raw.dyshensha 全集（1940-12-31 女，晚子时）
    ["丁亥", ["太极贵人", "文昌贵人", "天厨贵人", "月德合", "飞刃", "亡神", "红鸾", "天医", "驿马"]],
    ["丙戌", ["太极贵人", "德秀贵人", "金舆"]],
    ["乙酉", ["文昌贵人", "天厨贵人", "空亡", "桃花", "元辰", "将星"]],
    ["甲申", ["天乙贵人", "德秀贵人", "天德合", "空亡", "金舆", "亡神", "词馆"]],
    ["癸未", ["天乙贵人", "太极贵人", "福星贵人", "德秀贵人", "勾绞煞"]],
    ["壬午", ["福星贵人", "月德贵人", "德秀贵人", "禄神", "流霞", "灾煞", "丧门", "血刃", "桃花"]],
    ["辛巳", ["天罗地网", "德秀贵人", "天德贵人", "正学堂", "羊刃", "劫煞", "孤辰", "天喜"]],
    ["庚辰", ["国印贵人", "太极贵人", "红艳煞", "华盖"]],
    ["己卯", ["德秀贵人", "空亡"]],
    ["戊寅", ["太极贵人", "国印贵人", "德秀贵人", "空亡", "驿马", "吊客", "劫煞"]],
    ["丁丑", ["天乙贵人", "太极贵人", "月德合", "寡宿", "披麻", "华盖"]],
    ["丙子", ["天乙贵人", "德秀贵人", "将星"]],
]


@pytest.mark.parametrize("date,time,gender,expect", [
    ("1900-01-01", "00:00", "女", DYSHENSHA_19000101_F),
    ("1940-12-31", "23:00", "女", DYSHENSHA_19401231_F),
], ids=["19000101_f", "19401231_f_latezi"])
def test_dyshensha_full_chart(date, time, gender, expect):
    """12 步大运神煞全集与语料逐位一致（集合比对，名序无关）。"""
    r = _calc(date, time, gender)
    assert len(r.dyshensha) == len(r.dayun) == 12
    # 与 dayun 同序同位：每步干支 == 对应大运干支
    assert [gz for gz, _ in r.dyshensha] == [gz for _, gz in r.dayun]
    assert [gz for gz, _ in r.dyshensha] == [e[0] for e in expect]
    for i, (gz, names) in enumerate(expect):
        assert sorted(r.dyshensha[i][1]) == sorted(names), \
            f"第{i}步 {gz} 神煞 {r.dyshensha[i][1]} != 语料 {names}"


def test_dyshensha_tldw_complement_anchor():
    """天罗地网"互补成网"锚点：1940-04-01（庚辰 己卯 甲戌 甲子）。

    年支辰 + 日支戌：男盘 辛巳步（巳↔辰互补）成网；女盘（逆排）乙亥步
    （亥↔戌互补）成网；非互补步（庚辰：辰自身不成对）不得报天罗地网。
    """
    # 男盘（顺排 庚辰 辛巳 壬午 癸未…）：辛巳 = 巳 ↔ 年支辰 互补 → 成网
    r = _calc("1940-04-01", "00:00", "男")
    assert r.bazi == ["庚辰", "己卯", "甲戌", "甲子"]
    by_gz = {gz: set(names) for gz, names in r.dyshensha}
    # 辛巳（step1）：年支辰 = 巳之互补 → 成网（含正学堂——辛巳自柱纳音 == 年纳音）
    assert "天罗地网" in by_gz["辛巳"]
    assert "正学堂" in by_gz["辛巳"]
    assert sorted(by_gz["辛巳"]) == sorted(
        ["文昌贵人", "天厨贵人", "天罗地网", "天德合", "正学堂", "劫煞", "孤辰", "天喜", "亡神"])
    # 庚辰（step0）：辰自身不成对（互补为巳，年/日支均非巳）→ 不成网
    assert "天罗地网" not in by_gz["庚辰"]
    # 女盘（逆排 戊寅 丁丑 丙子 乙亥…）：乙亥 = 亥 ↔ 日支戌 互补 → 成网
    r2 = _calc("1940-04-01", "00:00", "女")
    assert [gz for _, gz in r2.dayun][3] == "乙亥"
    by_gz2 = {gz: set(names) for gz, names in r2.dyshensha}
    assert "天罗地网" in by_gz2["乙亥"]


def test_dyshensha_dayun_alignment():
    """dyshensha 与 dayun 步数/干支严格对齐（1900-06-28 男，顺排 12 步）。"""
    r = _calc("1900-06-28", "08:00", "男")
    assert [gz for _, gz in r.dayun] == ["癸未", "甲申", "乙酉", "丙戌", "丁亥", "戊子",
                                         "己丑", "庚寅", "辛卯", "壬辰", "癸巳", "甲午"]
    assert [gz for gz, _ in r.dyshensha] == [gz for _, gz in r.dayun]
    # 锚点抽查（语料）：癸未 = [天乙, 国印, 德秀, 元辰]；甲申 = [太极, 学堂]
    assert sorted(r.dyshensha[0][1]) == sorted(["天乙贵人", "国印贵人", "德秀贵人", "元辰"])
    assert sorted(r.dyshensha[1][1]) == sorted(["太极贵人", "学堂"])
