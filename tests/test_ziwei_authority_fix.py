"""K2 权威对齐测试：紫微安星公式修复（天府/火星/铃星/地空/地劫）+ 晚子时换日。

权威锚点来源（2026-08-30 三站双向对比，证据存档 /tmp/zw_evidence/）：
- 权威A = iztro（guayao 引擎同源 npm 包，源码级复算 + 本测试同日期实跑）
- 权威B = ziweidoushu.com9.tw（zds_all.json，h4Edition=0 全集默认）
- 交叉C = 见微 ziweimingli.com（zml_all.json）
A/B 逐星两两交叉一致即采用为本文件锚点值；C 与 A/B 在早子时/常规口径一致。
晚子时（F）：主流口径 = iztro 默认 dayDivide='forward'（_day=lunarDay+1，超月尾折回）
+ ZDS（23 时一律次日）+ 问真（用户已确认）→ 本批实现**默认次日**；
见微（C）按当日属少数派，不设开关。
天马：我方=年马（=权威A iztro）；ZDS/见微默认月马 → 2:2 口径待拍板（非 K2 范围），
逐星断言中单独注明。
"""
import pytest

from src.engines.ziwei import DIZHI, ZiweiEngine, ZiweiResult

MAIN14 = ["紫微", "天机", "太阳", "武曲", "天同", "廉贞",
          "天府", "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", "破军"]

# 每案锚点（星名→地支）。main 与权威A/B 逐星全等；aux 不含天马（口径项，单独断言）。
CASES = {
    "ZW1": {  # 1990-01-01 12:00 北京 男 己巳年腊月初五 午时 土五局
        "birth": dict(year=1990, month=1, day=1, hour=12, minute=0, city="北京", gender="男"),
        "wuxing_ju": "土五局", "ming_gong": "未", "shen_gong": "未",
        "main": {"紫微": "寅", "天机": "丑", "太阳": "亥", "武曲": "戌", "天同": "酉",
                 "廉贞": "午", "天府": "寅", "太阴": "卯", "贪狼": "辰", "巨门": "巳",
                 "天相": "午", "天梁": "未", "七杀": "申", "破军": "子"},
        "aux": {"左辅": "卯", "右弼": "亥", "文昌": "辰", "文曲": "戌",
                "天魁": "子", "天钺": "申", "禄存": "午", "擎羊": "未", "陀罗": "巳",
                "火星": "酉", "铃星": "辰", "地空": "巳", "地劫": "巳"},
        "tianma": "亥",  # 年马（A/B/C 三站同宫，巧合）
        "sihua": {"化禄": "武曲", "化权": "贪狼", "化科": "天梁", "化忌": "文曲"},
        "dayun_start": 5, "dayun_dir": "逆行",
    },
    "ZW2": {  # 1992-08-16 14:30 上海 女 壬申年七月十八 未时 木三局
        "birth": dict(year=1992, month=8, day=16, hour=14, minute=30, city="上海", gender="女"),
        "wuxing_ju": "木三局", "ming_gong": "丑", "shen_gong": "卯",
        "main": {"紫微": "未", "天机": "午", "太阳": "辰", "武曲": "卯", "天同": "寅",
                 "廉贞": "亥", "天府": "酉", "太阴": "戌", "贪狼": "亥", "巨门": "子",
                 "天相": "丑", "天梁": "寅", "七杀": "卯", "破军": "未"},
        "aux": {"左辅": "戌", "右弼": "辰", "文昌": "卯", "文曲": "亥",
                "天魁": "卯", "天钺": "巳", "禄存": "亥", "擎羊": "子", "陀罗": "戌",
                "火星": "酉", "铃星": "巳", "地空": "辰", "地劫": "午"},
        # 注：文昌卯/文曲亥 为 A/B/C 三站一致值（原引擎顺逆写反，K2 一并修复）
        "tianma": "寅",  # 年马；B/C 月马亦在寅（巧合同宫）
        "sihua": {"化禄": "天梁", "化权": "紫微", "化科": "左辅", "化忌": "武曲"},
        "dayun_start": 3, "dayun_dir": "逆行",
    },
    "ZW3": {  # 1994-11-11 00:15 成都 男 甲戌年十月初九 早子时 火六局
        "birth": dict(year=1994, month=11, day=11, hour=0, minute=15, city="成都", gender="男"),
        "wuxing_ju": "火六局", "ming_gong": "亥", "shen_gong": "亥",
        "main": {"紫微": "子", "天机": "亥", "太阳": "酉", "武曲": "申", "天同": "未",
                 "廉贞": "辰", "天府": "辰", "太阴": "巳", "贪狼": "午", "巨门": "未",
                 "天相": "申", "天梁": "酉", "七杀": "戌", "破军": "寅"},
        "aux": {"左辅": "丑", "右弼": "丑", "文昌": "戌", "文曲": "辰",
                "天魁": "丑", "天钺": "未", "禄存": "寅", "擎羊": "卯", "陀罗": "丑",
                "火星": "丑", "铃星": "卯", "地空": "亥", "地劫": "亥"},
        "tianma": "申",  # 年马=权威A iztro；B/C 月马=巳（口径 2:2，待拍板，非 K2）
        "sihua": {"化禄": "廉贞", "化权": "破军", "化科": "武曲", "化忌": "太阳"},
        "dayun_start": 6, "dayun_dir": "顺行",  # 阳男顺行（iztro 6-15 命宫乙亥实证）
    },
    "ZW4": {  # 2000-02-05 23:40 深圳 女 庚辰年正月初一 晚子时 → 次日初二安日系星 土五局
        "birth": dict(year=2000, month=2, day=5, hour=23, minute=40, city="深圳", gender="女"),
        "wuxing_ju": "土五局", "ming_gong": "寅", "shen_gong": "寅",
        "main": {"紫微": "亥", "天机": "戌", "太阳": "申", "武曲": "未", "天同": "午",
                 "廉贞": "卯", "天府": "巳", "太阴": "午", "贪狼": "未", "巨门": "申",
                 "天相": "酉", "天梁": "戌", "七杀": "亥", "破军": "卯"},
        "aux": {"左辅": "辰", "右弼": "戌", "文昌": "戌", "文曲": "辰",
                "天魁": "丑", "天钺": "未", "禄存": "申", "擎羊": "酉", "陀罗": "未",
                "火星": "寅", "铃星": "戌", "地空": "亥", "地劫": "亥"},
        "tianma": "寅",  # 年马=权威A iztro；B/C 月马=申（口径 2:2，待拍板，非 K2）
        "sihua": {"化禄": "太阳", "化权": "武曲", "化科": "太阴", "化忌": "天同"},
        "dayun_start": 5, "dayun_dir": "逆行",
    },
}

# 天府安星表：紫微 12 宫位 → 天府宫位（安天府诀，权威 A/B 全一致）
TIANFU_BY_ZIWEI = {
    "寅": "寅", "卯": "丑", "辰": "子", "巳": "亥", "午": "戌", "未": "酉",
    "申": "申", "酉": "未", "戌": "午", "亥": "巳", "子": "辰", "丑": "卯",
}
# 破军 = 天府 + 10（同表随天府连动）
POJUN_BY_TIANFU = {d: DIZHI[(DIZHI.index(d) + 10) % 12] for d in DIZHI}

# 火铃起宫表（年支组 → (火星起宫, 铃星起宫)，顺数至生时）
HUOLING_GROUP = {
    "寅午戌": (DIZHI.index("丑"), DIZHI.index("卯")),
    "申子辰": (DIZHI.index("寅"), DIZHI.index("戌")),
    "巳酉丑": (DIZHI.index("卯"), DIZHI.index("戌")),
    "亥卯未": (DIZHI.index("酉"), DIZHI.index("戌")),
}

# 地空：亥起子时逆行；地劫：亥起子时顺行
DIKONG_BY_SHICHEN = {d: DIZHI[(DIZHI.index("亥") - i) % 12] for i, d in enumerate(DIZHI)}
DIJIE_BY_SHICHEN = {d: DIZHI[(DIZHI.index("亥") + i) % 12] for i, d in enumerate(DIZHI)}


# ============================================================
# 1. 四案逐星断言（与权威锚点全等）
# ============================================================

@pytest.mark.parametrize("case_name", list(CASES.keys()))
def test_case_full_chart_matches_authority(case_name):
    case = CASES[case_name]
    engine = ZiweiEngine()
    r = engine.calculate(**case["birth"])

    assert r.wuxing_ju == case["wuxing_ju"], f"{case_name} 五行局"
    assert r.ming_gong == case["ming_gong"], f"{case_name} 命宫"
    assert r.shen_gong == case["shen_gong"], f"{case_name} 身宫"

    # 14 主星逐星与权威锚点全等（紫微系+天府系）
    for star in MAIN14:
        assert r.main_stars[star] == case["main"][star], (
            f"{case_name} {star}: 期望 {case['main'][star]} 实际 {r.main_stars[star]}")

    # 辅星（K2 相关 + 稳定项）逐星断言；天马单独（口径项）
    for star, want in case["aux"].items():
        assert r.aux_stars[star] == want, (
            f"{case_name} {star}: 期望 {want} 实际 {r.aux_stars[star]}")
    assert r.aux_stars["天马"] == case["tianma"], f"{case_name} 天马(年马口径)"

    # 四化（全集版，K1 已归版不动）
    assert r.sihua == case["sihua"], f"{case_name} 四化"

    # 大限：起运岁=局数、自命宫起、方向与权威一致
    assert r.dayun[0][0] == case["dayun_start"], f"{case_name} 起运岁"
    assert r.dayun[0][1] == "命宫" and r.dayun[0][2] == case["ming_gong"], f"{case_name} 大限起宫"
    ming_idx = DIZHI.index(case["ming_gong"])
    step = -1 if case["dayun_dir"] == "逆行" else 1
    assert r.dayun[1][2] == DIZHI[(ming_idx + step) % 12], (
        f"{case_name} 大限方向: 期望{case['dayun_dir']}")


# ============================================================
# 2. 每公式专项
# ============================================================

def test_tianfu_all_12_ziwei_positions():
    """天府公式专项：紫微在 12 宫位时，天府落宫与权威安天府诀全等。"""
    engine = ZiweiEngine()
    # 用五虎遁构造紫微落在指定宫位的盘：取紫微表内该宫位对应的 (局, 日)
    # 直接验证 _calc_main_stars（公式级），再挑两案实测引擎整盘
    for ziwei_dizhi, want_tianfu in TIANFU_BY_ZIWEI.items():
        ziwei_idx = DIZHI.index(ziwei_dizhi)
        stars = engine._calc_main_stars(ziwei_idx)
        assert stars["天府"] == want_tianfu, (
            f"紫微{ziwei_dizhi}: 期望天府{want_tianfu} 实际{stars['天府']}")
        # 破军与天府连动（权威 ZW1 紫微寅→破军子 实证）
        assert stars["破军"] == POJUN_BY_TIANFU[want_tianfu], (
            f"紫微{ziwei_dizhi}: 破军应与天府连动")
        # 紫微系不受影响
        assert stars["天机"] == DIZHI[(ziwei_idx - 1) % 12]
        assert stars["廉贞"] == DIZHI[(ziwei_idx - 8) % 12]


@pytest.mark.parametrize("group", list(HUOLING_GROUP.keys()))
@pytest.mark.parametrize("time_zhi,shift", [("子", 0), ("午", 6), ("酉", 9), ("亥", 11)])
def test_huo_ling_start_and_direction(group, time_zhi, shift):
    """火星/铃星专项：四组年支起宫 + 顺数至生时（权威 iztro getHuoLingIndex）。"""
    huo_start, ling_start = HUOLING_GROUP[group]
    # 期望值
    want_huo = DIZHI[(huo_start + shift) % 12]
    want_ling = DIZHI[(ling_start + shift) % 12]
    # 通过引擎实测：该组任一代表年支 + 指定时辰
    year_zhi = group[0]
    engine = ZiweiEngine()
    # 以 2008-08-08 20:00 为基底，换年支/时辰：年支由年份推导（2008=戊子，往前推）
    # 直接构造：用 _calc_aux_stars 公式级验证（年支/时辰 已分离，无需真实日期）
    aux = engine._calc_aux_stars("甲", year_zhi, 8, time_zhi, DIZHI.index(time_zhi))
    assert aux["火星"] == want_huo, f"{group} {time_zhi}时: 火星期望{want_huo} 实际{aux['火星']}"
    assert aux["铃星"] == want_ling, f"{group} {time_zhi}时: 铃星期望{want_ling} 实际{aux['铃星']}"


def test_huo_ling_zw_cases():
    """火铃四案锚点值（整盘回归）。"""
    engine = ZiweiEngine()
    for case_name in ["ZW1", "ZW2", "ZW3", "ZW4"]:
        r = engine.calculate(**CASES[case_name]["birth"])
        assert r.aux_stars["火星"] == CASES[case_name]["aux"]["火星"], case_name
        assert r.aux_stars["铃星"] == CASES[case_name]["aux"]["铃星"], case_name


@pytest.mark.parametrize("time_zhi", list(DIZHI))
def test_dikong_dijie_all_shichen(time_zhi):
    """地空地劫专项：地空亥起逆行、地劫亥起顺行（12 时辰全表）。"""
    engine = ZiweiEngine()
    aux = engine._calc_aux_stars("甲", "子", 8, time_zhi, DIZHI.index(time_zhi))
    assert aux["地空"] == DIKONG_BY_SHICHEN[time_zhi], (
        f"{time_zhi}时: 地空期望{DIKONG_BY_SHICHEN[time_zhi]} 实际{aux['地空']}")
    assert aux["地劫"] == DIJIE_BY_SHICHEN[time_zhi], (
        f"{time_zhi}时: 地劫期望{DIJIE_BY_SHICHEN[time_zhi]} 实际{aux['地劫']}")
    # 方向反证：地空逆行（亥-1=戌）、地劫顺行（亥+1=子）
    assert DIKONG_BY_SHICHEN["丑"] == "戌" and DIJIE_BY_SHICHEN["丑"] == "子"


def test_dikong_dijie_zw_cases():
    """空劫四案锚点值（整盘回归）。"""
    engine = ZiweiEngine()
    for case_name in ["ZW1", "ZW2", "ZW3", "ZW4"]:
        r = engine.calculate(**CASES[case_name]["birth"])
        assert r.aux_stars["地空"] == CASES[case_name]["aux"]["地空"], case_name
        assert r.aux_stars["地劫"] == CASES[case_name]["aux"]["地劫"], case_name


# ============================================================
# 3. 晚子时换日（F）
# ============================================================

def test_late_child_hour_zw4_rolls_to_next_day():
    """ZW4 晚子时（23:40）：日系星按次日（初二）安星，主星全表对齐权威 A/B。"""
    case = CASES["ZW4"]
    engine = ZiweiEngine()
    r = engine.calculate(**case["birth"])
    assert r.main_stars["紫微"] == "亥", f"晚子时紫微应按次日=亥，实际{r.main_stars['紫微']}"
    for star in MAIN14:
        assert r.main_stars[star] == case["main"][star], star
    # 命身宫/四化/时系星不随换日变
    assert r.ming_gong == "寅" and r.shen_gong == "寅"
    assert r.sihua == case["sihua"]
    assert r.aux_stars["文昌"] == "戌" and r.aux_stars["文曲"] == "辰"  # 时系星（子时）
    assert r.aux_stars["地空"] == "亥" and r.aux_stars["地劫"] == "亥"
    assert r.aux_stars["火星"] == "寅" and r.aux_stars["铃星"] == "戌"


def test_late_child_hour_vs_early_child_hour_same_day():
    """同日早/晚子时：仅日系星换日，时系星与命身宫一致。"""
    engine = ZiweiEngine()
    # 2000-02-05 正月初一（庚辰年）：早子时当日（初一日系星），晚子时次日（初二）
    early = engine.calculate(2000, 2, 5, 0, 15, "深圳", "女")
    late = engine.calculate(2000, 2, 5, 23, 40, "深圳", "女")
    # 土五局 day1=午（当日），day2=亥（次日）——权威 A/B 实证 ZW4 紫微亥
    assert early.main_stars["紫微"] == "午", f"早子时应当日=午，实际{early.main_stars['紫微']}"
    assert late.main_stars["紫微"] == "亥"
    # 天府随紫微连动：紫微午→天府戌（=见微当日口径 ZW4 天府戌）；紫微亥→天府巳（=A/B 次日口径）
    assert early.main_stars["天府"] == "戌" and late.main_stars["天府"] == "巳"
    assert early.main_stars["破军"] == "申" and late.main_stars["破军"] == "卯"
    # 时系星同（同为子时）、命身宫同
    for aux in ["文昌", "文曲", "地空", "地劫", "火星", "铃星"]:
        assert early.aux_stars[aux] == late.aux_stars[aux], f"{aux} 不应随换日变"
    assert early.ming_gong == late.ming_gong == "寅"
    assert early.sihua == late.sihua  # 年干支不随换日变


def test_late_child_hour_non_late_hours_unaffected():
    """非晚子时时段零影响：日系星仍按当日安星。"""
    engine = ZiweiEngine()
    # 2000-02-05 12:00（午时）：命宫申→甲申泉中水=水二局，当日 day1 → 紫微丑
    r = engine.calculate(2000, 2, 5, 12, 0, "深圳", "女")
    assert r.wuxing_ju == "水二局"
    assert r.main_stars["紫微"] == "丑", f"午时非晚子时按当日 day1=丑，实际{r.main_stars['紫微']}"
    # 22:59（亥时，尚未入晚子时段）：土五局 day1 → 紫微午（当日口径）
    r2 = engine.calculate(2000, 2, 5, 22, 59, "深圳", "女")
    assert r2.wuxing_ju == "土五局"
    assert r2.main_stars["紫微"] == "午", f"22:59 亥时按当日 day1=午，实际{r2.main_stars['紫微']}"
    # 23:00 整进入晚子时段 → 折次日 day2=亥
    r3 = engine.calculate(2000, 2, 5, 23, 0, "深圳", "女")
    assert r3.main_stars["紫微"] == "亥", f"23:00 晚子时按次日 day2=亥，实际{r3.main_stars['紫微']}"


def test_late_child_hour_month_end_wrap():
    """晚子时落在农历月尾：日数折回初一（对齐 iztro: _day>maxDays 则 _day-=maxDays）。"""
    engine = ZiweiEngine()
    # 2000-02-04 = 己卯年腊月廿九（29 天月）水二局：当日 day29=卯，次日初一 day1=丑
    late = engine.calculate(2000, 2, 4, 23, 40, "北京", "男")
    early = engine.calculate(2000, 2, 4, 0, 15, "北京", "男")
    assert late.wuxing_ju == "水二局"
    assert early.main_stars["紫微"] == "卯", "早子时当日=水二局 day29=卯"
    assert late.main_stars["紫微"] == "丑", "晚子时折回次日初一=水二局 day1=丑"
    # 2001-01-23 = 庚辰年腊月廿九 火六局：当日 day29=巳，次日初一 day1=酉
    late2 = engine.calculate(2001, 1, 23, 23, 40, "北京", "男")
    early2 = engine.calculate(2001, 1, 23, 0, 15, "北京", "男")
    assert late2.wuxing_ju == "火六局"
    assert early2.main_stars["紫微"] == "巳", "早子时当日=火六局 day29=巳"
    assert late2.main_stars["紫微"] == "酉", "晚子时折回次日初一=火六局 day1=酉"


def test_early_child_hour_zw3_no_roll():
    """ZW3 早子时（00:15）：不换日，全盘仍对权威锚点（含紫微子）。"""
    case = CASES["ZW3"]
    engine = ZiweiEngine()
    r = engine.calculate(**case["birth"])
    assert r.main_stars["紫微"] == "子"
    for star in MAIN14:
        assert r.main_stars[star] == case["main"][star], star
    assert r.wuxing_ju == "火六局" and r.ming_gong == "亥"


# ============================================================
# 4. 接口契约：ZiweiResult 字段形状不变
# ============================================================

def test_ziwei_result_contract_shape():
    """契约：ZiweiResult 字段形状不变（既有消费点 handler/tests 不破）。"""
    engine = ZiweiEngine()
    r = engine.calculate(**CASES["ZW1"]["birth"])
    assert isinstance(r, ZiweiResult)
    # 顶层字段形状
    assert isinstance(r.ming_gong, str) and isinstance(r.shen_gong, str)
    assert isinstance(r.wuxing_ju, str)
    assert isinstance(r.sihua, dict) and set(r.sihua) == {"化禄", "化权", "化科", "化忌"}
    assert isinstance(r.main_stars, dict) and set(r.main_stars) == set(MAIN14)
    assert isinstance(r.aux_stars, dict) and len(r.aux_stars) == 14
    assert isinstance(r.dayun, list) and len(r.dayun) == 12
    assert isinstance(r.raw_data, dict)
    # 12 宫形状
    assert len(r.palaces) == 12
    for p_name, info in r.palaces.items():
        assert isinstance(info.dizhi, str)
        assert isinstance(info.stars, list) and isinstance(info.aux_stars, list)
