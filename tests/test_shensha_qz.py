"""神煞引擎测试 — 问真 getshensha60 数据基准（29 种，60 甲子速查表）。

覆盖：
1. 全部 60 甲子查表与 data/qz_shensha60.json（问真原始采集）逐条一致
2. 10 个案例（5 年柱 + 5 日柱）查表结果断言
3. shensha_of 年日两局并查 / 合并去重 / 吉凶标注
4. 按日干计算神煞（禄神/羊刃/天乙贵人/驿马）
5. BaziEngine 集成（BaziResult.shensha 带出全部神煞）
"""
import json
import os

from src.engines.shensha import (
    SHENSHA60,
    SHENSHA_LUCK,
    ShenshaItem,
    shensha_by_jiazi,
    shensha_names,
    shensha_of,
)
from src.engines.bazi import BaziEngine

DATA_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "qz_shensha60.json")

# 问真 getshensha60 API 验证过的速查案例：5 年柱 + 5 日柱
YEAR_PILLAR_CASES = {
    "甲子": ["天乙贵人", "桃花", "红鸾", "披麻"],
    "己卯": ["将星"],
    "壬申": ["天乙贵人", "空亡", "金舆", "劫煞", "学堂"],
    "辛巳": ["驿马", "孤辰", "丧门", "地网"],
    "乙亥": ["词馆"],
}
DAY_PILLAR_CASES = {
    "丁卯": ["将星"],
    "癸酉": ["文昌贵人", "天厨贵人", "空亡", "灾煞"],
    "甲戌": ["太极贵人", "元辰"],
    "戊子": ["天乙贵人", "桃花", "红鸾", "披麻"],
    "庚午": ["勾绞煞", "天喜"],
}


def _load_json():
    with open(DATA_JSON, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 1. 60 甲子全量一致
def test_all_60_jiazi_match_qz_json():
    """代码常量与问真采集 JSON 逐条一致（60 条）。"""
    json_data = _load_json()
    assert len(json_data) == 60
    assert len(SHENSHA60) == 60
    for ganzhi, names in json_data.items():
        assert SHENSHA60.get(ganzhi) == names, f"{ganzhi} 不一致: {SHENSHA60.get(ganzhi)} vs {names}"


def test_table_has_29_shensha_types():
    """速查表覆盖 29 种神煞，且全部有吉凶标注。"""
    names = {n for v in SHENSHA60.values() for n in v}
    assert len(names) == 29
    assert names == {
        "丧门", "亡神", "元辰", "劫煞", "勾绞煞", "华盖", "吊客", "国印贵人",
        "地网", "天乙贵人", "天厨贵人", "天喜", "太极贵人", "孤辰", "学堂",
        "寡宿", "将星", "披麻", "文昌贵人", "桃花", "正学堂", "正词馆",
        "灾煞", "福星贵人", "空亡", "红鸾", "词馆", "金舆", "驿马",
    }
    for n in names:
        assert n in SHENSHA_LUCK, f"{n} 缺吉凶标注"


# ---------------------------------------------------------------- 2. 10 个问真验证案例
def test_year_pillar_cases():
    for ganzhi, expected in YEAR_PILLAR_CASES.items():
        assert shensha_by_jiazi(ganzhi) == expected, f"年柱 {ganzhi}"


def test_day_pillar_cases():
    for ganzhi, expected in DAY_PILLAR_CASES.items():
        assert shensha_by_jiazi(ganzhi) == expected, f"日柱 {ganzhi}"


# ---------------------------------------------------------------- 3. 年日两局并查
def test_shensha_of_year_and_day_union():
    """庚午年 + 乙酉日：输出包含年柱查表（勾绞煞/天喜）与日柱查表（文昌贵人/天厨贵人/空亡/灾煞）全部神煞。"""
    items = shensha_of("庚午", "乙酉")
    names = [i.name for i in items]
    for expected in ["勾绞煞", "天喜", "文昌贵人", "天厨贵人", "空亡", "灾煞"]:
        assert expected in names, f"缺少 {expected}: {names}"
    # 年柱在前，日柱在后
    assert names.index("勾绞煞") < names.index("文昌贵人")


def test_shensha_of_dedup_same_pillar():
    """同柱重复的神煞不重复出现（同一甲子查表只出现一次）。"""
    names = shensha_names("甲子", "甲子")
    assert names.count("天乙贵人") == 1
    assert names.count("桃花") == 1
    assert names.count("红鸾") == 1
    assert names.count("披麻") == 1


def test_shensha_of_merge_sources():
    """同名神煞跨来源合并去重，source 合并标注。"""
    # 甲子年（天乙贵人）+ 壬申日（天乙贵人）：天乙贵人 source 应为 年柱+日柱
    items = shensha_of("甲子", "壬申")
    by_name = {i.name: i for i in items}
    assert by_name["天乙贵人"].source == "年柱+日柱"
    assert by_name["桃花"].source == "年柱"
    assert by_name["学堂"].source == "日柱"
    # 全部条目名称唯一
    assert len({i.name for i in items}) == len(items)


def test_shensha_of_item_fields():
    """每项含 name/source/luck 三字段。"""
    items = shensha_of("庚午", "乙酉")
    assert all(isinstance(i, ShenshaItem) for i in items)
    for i in items:
        assert i.name and i.source in ("年柱", "日柱", "计算",
                                       "年柱+日柱", "年柱+计算", "日柱+计算",
                                       "年柱+日柱+计算")
        assert i.luck in ("吉", "凶", "中性", "中性偏吉")


def test_shensha_luck_classification():
    """吉凶标注按通用认知：吉星为吉，凶煞为凶，桃花中性偏吉，华盖中性。"""
    luck = {i.name: i.luck for i in shensha_of("甲子", "癸酉")}
    assert luck["天乙贵人"] == "吉"
    assert luck["桃花"] == "中性偏吉"
    assert luck["文昌贵人"] == "吉"
    assert luck["灾煞"] == "凶"
    assert luck["空亡"] == "凶"


# ---------------------------------------------------------------- 4. 按日干计算神煞（问真 szshensha 口径）
def test_computed_lushen_and_yangren():
    """乙卯日：乙禄在卯（禄神，日支）、乙刃在寅（羊刃，月支）。
    问真口径（2026-08-19 校准）：禄神/羊刃按日干查支，source 记命中柱位。"""
    items = shensha_of("甲子", "乙卯", all_gan=["甲", "丙", "乙", "丙"], all_zhi=["子", "寅", "卯", "午"])
    names = [i.name for i in items]
    assert "禄神" in names
    assert "羊刃" in names  # 寅在月支
    lushen = next(i for i in items if i.name == "禄神")
    assert lushen.source == "日柱"  # 卯 = 日支
    assert lushen.luck == "吉"
    yangren = next(i for i in items if i.name == "羊刃")
    assert yangren.source == "月柱"  # 寅 = 月支
    assert yangren.luck == "凶"


def test_computed_tianyi_yima_kept():
    """乙日主见申（时支）→ 天乙贵人（时柱）；午年支/酉日支三合见申 → 驿马。
    问真口径（2026-08-19 校准）：年干+日干、年支+日支双查，source 记命中柱位。"""
    items = shensha_of("庚午", "乙酉", all_gan=["庚", "辛", "乙", "甲"], all_zhi=["午", "巳", "酉", "申"])
    by_name = {i.name: i for i in items}
    assert by_name["天乙贵人"].source == "时柱"  # 乙→子申，申在时支
    assert "驿马" in by_name  # 年支午三合寅午戌→申（时支）/ 日支酉三合巳酉丑→亥（无）
    assert by_name["驿马"].source == "时柱"


# ---------------------------------------------------------------- 5. BaziEngine 集成
def test_bazi_shensha_integration():
    """1990-05-20 15:00 男 → 庚午 辛巳 乙酉 甲申（问真 250 案例校准已验证全字段一致）。
    问真 szshensha 口径：年干/日干、年支/日支双查 + 年支类 + 空亡/学堂等，source 记命中柱位。
    期望值为北京时间口径，city 传空 = 不修正（真太阳时修正见 test_bazi_solar_time.py）。"""
    engine = BaziEngine()
    result = engine.calculate(1990, 5, 20, 15, 0, "", "男")
    assert result.bazi == ["庚午", "辛巳", "乙酉", "甲申"]
    shensha = result.shensha
    for expected in ["天乙贵人", "太极贵人", "文昌贵人", "天厨贵人", "福星贵人",
                     "金舆", "驿马", "桃花", "亡神", "红鸾", "丧门", "勾绞煞",
                     "孤辰", "空亡", "学堂"]:
        assert expected in shensha, f"BaziResult.shensha 缺少 {expected}: {shensha}"
    assert len(shensha) == len(set(shensha))  # 无重复
    # 详情字段带 source/luck
    detail = {d["name"]: d for d in result.shensha_detail}
    assert detail["勾绞煞"]["source"] == "日柱"   # 午年支前三辰=酉，酉在日支
    assert detail["文昌贵人"]["source"] == "年柱"  # 乙日干→午，午在年支
    assert detail["天乙贵人"]["source"] == "时柱"  # 乙日干→子申，申在时支
    assert detail["文昌贵人"]["luck"] == "吉"
    assert detail["空亡"]["luck"] == "凶"


def test_bazi_shensha_lushen_yangren_integration():
    """2026-02-10 08:00 北京 男 → 丙午 庚寅 乙卯 庚辰。
    乙卯日主：乙禄在卯（禄神，日支）、乙刃在寅（羊刃，月支）→ 神煞进入 BaziResult。"""
    engine = BaziEngine()
    result = engine.calculate(2026, 2, 10, 8, 0, "北京", "男")
    assert result.bazi == ["丙午", "庚寅", "乙卯", "庚辰"]
    assert "禄神" in result.shensha
    assert "羊刃" in result.shensha
    detail = {d["name"]: d for d in result.shensha_detail}
    assert detail["禄神"]["source"] == "日柱"  # 卯 = 日支
    assert detail["禄神"]["luck"] == "吉"
    assert detail["羊刃"]["source"] == "月柱"  # 寅 = 月支
    assert detail["羊刃"]["luck"] == "凶"
