"""神煞引擎 — 60 甲子速查表（问真八字口径，29 种神煞）+ 按日干计算神煞。

数据基准：问真 getshensha60 API（/tmp/qz_shensha60_full.json，已固化为代码常量，
原始 JSON 副本见 data/qz_shensha60.json 供溯源与测试比对）。

神煞来源：
- 查表：年柱 / 日柱各自查 60 甲子速查表（问真口径，年日两局并查）
- 计算：按日干/日支推算（羊刃、禄神不在 60 甲子表中；天乙贵人、驿马保留
  原有按日干/日支的计算口径，与查表结果双源合并去重）
"""
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------- 60 甲子速查表
# 问真 getshensha60 数据固化：60 甲子 × 神煞列表（29 种）
SHENSHA60 = {
    '甲子': ['天乙贵人', '桃花', '红鸾', '披麻'],
    '乙丑': ['太极贵人', '寡宿', '吊客'],
    '丙寅': ['国印贵人', '亡神'],
    '丁卯': ['将星'],
    '戊辰': ['太极贵人', '地网'],
    '己巳': ['驿马', '孤辰', '丧门', '地网'],
    '庚午': ['勾绞煞', '天喜'],
    '辛未': ['太极贵人', '福星贵人', '华盖'],
    '壬申': ['天乙贵人', '空亡', '金舆', '劫煞', '学堂'],
    '癸酉': ['文昌贵人', '天厨贵人', '空亡', '灾煞'],
    '甲戌': ['太极贵人', '元辰'],
    '乙亥': ['词馆'],
    '丙子': ['天乙贵人', '桃花', '红鸾', '披麻'],
    '丁丑': ['太极贵人', '寡宿', '吊客'],
    '戊寅': ['国印贵人', '亡神'],
    '己卯': ['将星'],
    '庚辰': ['太极贵人', '地网'],
    '辛巳': ['驿马', '孤辰', '丧门', '地网'],
    '壬午': ['勾绞煞', '天喜'],
    '癸未': ['太极贵人', '福星贵人', '华盖'],
    '甲申': ['天乙贵人', '空亡', '金舆', '劫煞', '学堂'],
    '乙酉': ['文昌贵人', '天厨贵人', '空亡', '灾煞'],
    '丙戌': ['太极贵人', '元辰'],
    '丁亥': ['正词馆'],
    '戊子': ['天乙贵人', '桃花', '红鸾', '披麻'],
    '己丑': ['太极贵人', '寡宿', '吊客'],
    '庚寅': ['国印贵人', '亡神'],
    '辛卯': ['将星'],
    '壬辰': ['太极贵人', '地网'],
    '癸巳': ['驿马', '孤辰', '丧门', '地网'],
    '甲午': ['勾绞煞', '天喜'],
    '乙未': ['太极贵人', '福星贵人', '华盖'],
    '丙申': ['天乙贵人', '空亡', '金舆', '劫煞', '学堂'],
    '丁酉': ['文昌贵人', '天厨贵人', '空亡', '灾煞'],
    '戊戌': ['太极贵人', '元辰'],
    '己亥': ['词馆'],
    '庚子': ['天乙贵人', '桃花', '红鸾', '披麻'],
    '辛丑': ['太极贵人', '寡宿', '吊客'],
    '壬寅': ['国印贵人', '亡神'],
    '癸卯': ['将星'],
    '甲辰': ['太极贵人', '地网'],
    '乙巳': ['驿马', '孤辰', '丧门', '地网'],
    '丙午': ['勾绞煞', '天喜'],
    '丁未': ['太极贵人', '福星贵人', '华盖'],
    '戊申': ['天乙贵人', '正学堂', '空亡', '金舆', '劫煞'],
    '己酉': ['文昌贵人', '天厨贵人', '空亡', '灾煞'],
    '庚戌': ['太极贵人', '元辰'],
    '辛亥': ['词馆'],
    '壬子': ['天乙贵人', '桃花', '红鸾', '披麻'],
    '癸丑': ['太极贵人', '寡宿', '吊客'],
    '甲寅': ['国印贵人', '亡神'],
    '乙卯': ['将星'],
    '丙辰': ['太极贵人', '地网'],
    '丁巳': ['驿马', '孤辰', '丧门', '地网'],
    '戊午': ['勾绞煞', '天喜'],
    '己未': ['太极贵人', '福星贵人', '华盖'],
    '庚申': ['天乙贵人', '空亡', '金舆', '劫煞', '学堂'],
    '辛酉': ['文昌贵人', '天厨贵人', '空亡', '灾煞'],
    '壬戌': ['太极贵人', '元辰'],
    '癸亥': ['词馆'],
}

# ---------------------------------------------------------------- 吉凶标注
# 问真 API 未提供吉凶分类；以下按通用命理认知保守标注（见各条目注释）。
SHENSHA_LUCK = {
    # 吉（贵人/吉星）
    "天乙贵人": "吉", "太极贵人": "吉", "文昌贵人": "吉", "福星贵人": "吉",
    "天厨贵人": "吉", "将星": "吉", "驿马": "吉", "金舆": "吉", "国印贵人": "吉",
    "学堂": "吉", "词馆": "吉", "正学堂": "吉", "正词馆": "吉",
    "天喜": "吉", "红鸾": "吉",
    # 中性
    "桃花": "中性偏吉",   # 主异性缘/艺术感受力，泛滥则为烂桃花
    "华盖": "中性",       # 主艺术/玄学缘分，亦主孤独
    # 凶
    "寡宿": "凶", "孤辰": "凶", "吊客": "凶", "披麻": "凶", "丧门": "凶",
    "亡神": "凶", "元辰": "凶", "劫煞": "凶", "勾绞煞": "凶", "灾煞": "凶",
    "地网": "凶", "空亡": "凶",
    # 计算类神煞（不在 60 甲子表中）
    "禄神": "吉",         # 禄为养命之源
    "羊刃": "凶",         # 刃为凶器（亦有流派视身强时羊刃为中性）
}

# 查表神煞全集（29 种，用于校验与展示）
SHENSHA_TABLE_NAMES = sorted(
    {name for names in SHENSHA60.values() for name in names}
)


@dataclass
class ShenshaItem:
    """单条神煞输出。

    source: 来源柱位/方式 —— "年柱"（年柱查表）/ "日柱"（日柱查表）/ "计算"（按日干推算）
    luck:   吉凶分类（按通用命理认知标注，问真未提供）
    """
    name: str
    source: str
    luck: str


def shensha_by_jiazi(ganzhi: str) -> List[str]:
    """按 60 甲子查速查表，返回神煞名列表（问真 getshensha60 口径）。

    >>> shensha_by_jiazi("甲子")
    ['天乙贵人', '桃花', '红鸾', '披麻']
    """
    return list(SHENSHA60[ganzhi])


# ---------------------------------------------------------------- 按日干计算神煞
# 天乙贵人：日干 → 贵人地支（保留原有实现口径）
TIANYI_GUIREN = {
    "甲": "丑未", "乙": "子申", "丙": "亥酉", "丁": "亥酉", "戊": "丑未",
    "己": "子申", "庚": "丑未", "辛": "寅午", "壬": "卯巳", "癸": "卯巳",
}

# 驿马：日支三合局 → 驿马地支（保留原有实现口径）
YIMA = {
    "申": "寅", "子": "寅", "辰": "寅",
    "寅": "申", "午": "申", "戌": "申",
    "巳": "亥", "酉": "亥", "丑": "亥",
    "亥": "巳", "卯": "巳", "未": "巳",
}

# 禄神：日干临官位（甲禄在寅...）
LUSHEN = {
    "甲": "寅", "乙": "卯", "丙": "巳", "丁": "午", "戊": "巳",
    "己": "午", "庚": "申", "辛": "酉", "壬": "亥", "癸": "子",
}

# 羊刃：日干帝旺位（阳干为正刃，阴干按通用排盘口径同列，问真亦给）
YANGREN = {
    "甲": "卯", "乙": "寅", "丙": "午", "丁": "巳", "戊": "午",
    "己": "巳", "庚": "酉", "辛": "申", "壬": "子", "癸": "亥",
}


def _computed_by_day_gan(day_gan: str, day_zhi: str, all_zhi: List[str]) -> List[str]:
    """按日干/日支计算的 4 种神煞（不在 60 甲子速查表中或需按原口径计算）。

    规则：神煞支位出现在四柱任意地支即得神煞（与原有实现一致）。
    """
    result = []

    # 天乙贵人（日干查贵人位）
    guiren = TIANYI_GUIREN.get(day_gan, "")
    for z in all_zhi:
        if z in guiren:
            result.append("天乙贵人")
            break

    # 驿马（日支三合局查驿马位）
    yima = YIMA.get(day_zhi, "")
    if yima:
        for z in all_zhi:
            if z == yima:
                result.append("驿马")
                break

    # 禄神（日干临官位）
    lu = LUSHEN.get(day_gan, "")
    if lu:
        for z in all_zhi:
            if z == lu:
                result.append("禄神")
                break

    # 羊刃（日干帝旺位）
    yang = YANGREN.get(day_gan, "")
    if yang:
        for z in all_zhi:
            if z == yang:
                result.append("羊刃")
                break

    return result


def shensha_of(year_pillar: str, day_pillar: str,
               all_gan: Optional[List[str]] = None,
               all_zhi: Optional[List[str]] = None) -> List[ShenshaItem]:
    """年柱 + 日柱两局并查（问真口径），合并按日干计算神煞，按名称去重。

    :param year_pillar: 年柱干支，如 "庚午"
    :param day_pillar:  日柱干支，如 "乙酉"
    :param all_gan:     四柱天干 [年,月,日,时]（缺省时由 day_pillar 推日干）
    :param all_zhi:     四柱地支 [年,月,日,时]（缺省时由 day_pillar 推日支）

    返回顺序：年柱查表 → 日柱查表 → 按日干计算；同名神煞合并去重，
    source 合并为多来源（如 "年柱+日柱"）。
    """
    merged = {}   # name -> ShenshaItem（去重，source 合并）

    def _add(name: str, source: str) -> None:
        luck = SHENSHA_LUCK.get(name, "中性")
        if name in merged:
            old = merged[name]
            if source not in old.source:
                old.source = old.source + "+" + source
        else:
            merged[name] = ShenshaItem(name=name, source=source, luck=luck)

    # 1. 年柱查表
    for name in shensha_by_jiazi(year_pillar):
        _add(name, "年柱")

    # 2. 日柱查表
    for name in shensha_by_jiazi(day_pillar):
        _add(name, "日柱")

    # 3. 按日干/日支计算（天乙贵人/驿马/禄神/羊刃）
    day_gan = day_pillar[0] if not all_gan else all_gan[2]
    day_zhi = day_pillar[1] if not all_zhi else all_zhi[2]
    zhi_for_check = all_zhi if all_zhi else [day_zhi]
    for name in _computed_by_day_gan(day_gan, day_zhi, zhi_for_check):
        _add(name, "计算")

    # 保持插入顺序（年柱 → 日柱 → 计算）
    return list(merged.values())


def shensha_names(year_pillar: str, day_pillar: str,
                  all_gan: Optional[List[str]] = None,
                  all_zhi: Optional[List[str]] = None) -> List[str]:
    """shensha_of 的名称简版（BaziResult.shensha 字段用，兼容旧消费方）。"""
    return [item.name for item in shensha_of(year_pillar, day_pillar, all_gan, all_zhi)]
