"""神煞引擎 — 问真八字口径（2026-08-19 经 50 案例逐柱反推 + 250 案例全量校准）。

问真 szshensha（四柱神煞）为纯计算规则，非 60 甲子查表（速查表仅用于大运神煞 dyshensha）：
- 年干+日干双查：天乙贵人、太极贵人、文昌贵人、天厨贵人、福星贵人、金舆、国印贵人
- 日干单查：禄神、羊刃
- 年支+日支双查：驿马、桃花、劫煞、亡神；年支单查：灾煞
- 将星/华盖：三合局中神/墓库，归属除主柱（年柱/日柱）外的含支柱位
- 年支类：红鸾、天喜、丧门(+2)、吊客(-2)、披麻(-3)、勾绞煞(年支+3)
- 孤辰寡宿：三合局位偏移（孤辰 首+3/中+2/尾+1；寡宿 首-1/中-2/尾-3），年支单查
- 空亡：年柱旬 + 日柱旬 双旬（支在四柱即得）
- 元辰：阳男阴女大耗（对冲+1）、阴男阳女小耗（对冲-1）
- 学堂/正学堂：年纳音长生（除年柱、除正学堂柱）；正学堂 = 自柱纳音长生==自柱支
  且自柱纳音==年纳音
- 词馆/正词馆：年纳音临官（土→亥，最深含支柱）；正词馆 = 自柱纳音临官==自柱支
  且自柱纳音==年纳音
- 天罗/地网：日支戌亥（年纳音火）/ 日支辰巳（年纳音水土）

数据基准：问真 getshensha60（大运神煞速查表）+ szshensha 逐柱反推（250 案例交集 100%）。
"""

from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------- 60 甲子速查表
# 问真 getshensha60 数据固化：60 甲子 × 神煞列表（29 种）——大运神煞（dyshensha）口径。
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
SHENSHA_LUCK = {
    "天乙贵人": "吉", "太极贵人": "吉", "文昌贵人": "吉", "福星贵人": "吉",
    "天厨贵人": "吉", "将星": "吉", "驿马": "吉", "金舆": "吉", "国印贵人": "吉",
    "学堂": "吉", "词馆": "吉", "正学堂": "吉", "正词馆": "吉",
    "天喜": "吉", "红鸾": "吉",
    "桃花": "中性偏吉",
    "华盖": "中性",
    "寡宿": "凶", "孤辰": "凶", "吊客": "凶", "披麻": "凶", "丧门": "凶",
    "亡神": "凶", "元辰": "凶", "劫煞": "凶", "勾绞煞": "凶", "灾煞": "凶",
    "地网": "凶", "空亡": "凶", "天罗": "凶",
    "禄神": "吉",
    "羊刃": "凶",
}

# 查表神煞全集（29 种，用于校验与展示）
SHENSHA_TABLE_NAMES = sorted(
    {name for names in SHENSHA60.values() for name in names}
)

TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
WX_TG = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土", "己": "土",
         "庚": "金", "辛": "金", "壬": "水", "癸": "水"}


@dataclass
class ShenshaItem:
    """单条神煞输出。

    source: 来源柱位/方式 —— "年柱"/"月柱"/"日柱"/"时柱"/"计算"（按日干推算）
    luck:   吉凶分类（按通用命理认知标注，问真未提供）
    """
    name: str
    source: str
    luck: str


def shensha_by_jiazi(ganzhi: str) -> List[str]:
    """按 60 甲子查速查表（大运/流年神煞口径，问真 getshensha60）。"""
    return list(SHENSHA60[ganzhi])


# ---------------------------------------------------------------- 命理口诀映射
# 年干/日干 → 支（双查）
TIANYI_GUIREN = {"甲": "丑未", "戊": "丑未", "庚": "丑未", "乙": "子申", "己": "子申",
                 "丙": "亥酉", "丁": "亥酉", "辛": "寅午", "壬": "卯巳", "癸": "卯巳"}
TAIJI = {"甲": "子午", "乙": "子午", "丙": "卯酉", "丁": "卯酉", "戊": "辰戌丑未",
         "己": "辰戌丑未", "庚": "寅亥", "辛": "寅亥", "壬": "巳申", "癸": "巳申"}
WENCHANG = {"甲": "巳", "乙": "午", "丙": "申", "丁": "酉", "戊": "申", "己": "酉",
            "庚": "亥", "辛": "子", "壬": "寅", "癸": "卯"}
TIANCHU = {"甲": "巳", "乙": "午", "丙": "巳", "丁": "午", "戊": "申", "己": "酉",
           "庚": "亥", "辛": "子", "壬": "寅", "癸": "卯"}
FUXING = {"甲": "寅子", "丙": "寅子", "乙": "丑卯", "癸": "丑卯", "戊": "申", "己": "未",
          "丁": "亥", "庚": "午", "辛": "巳", "壬": "辰"}
LUSHEN = {"甲": "寅", "乙": "卯", "丙": "巳", "丁": "午", "戊": "巳",
          "己": "午", "庚": "申", "辛": "酉", "壬": "亥", "癸": "子"}
YANGREN = {"甲": "卯", "乙": "寅", "丙": "午", "丁": "巳", "戊": "午",
           "己": "巳", "庚": "酉", "辛": "申", "壬": "子", "癸": "亥"}
JINYU = {"甲": "辰", "乙": "巳", "丙": "未", "丁": "申", "戊": "未", "己": "申",
         "庚": "戌", "辛": "亥", "壬": "丑", "癸": "寅"}
GUOYIN = {"甲": "戌", "乙": "亥", "丙": "丑", "丁": "寅", "戊": "丑", "己": "寅",
          "庚": "辰", "辛": "巳", "壬": "未", "癸": "申"}

# 年支/日支 → 支（双查）
YIMA = {"申": "寅", "子": "寅", "辰": "寅", "寅": "申", "午": "申", "戌": "申",
        "巳": "亥", "酉": "亥", "丑": "亥", "亥": "巳", "卯": "巳", "未": "巳"}
TAOHUA = {"申": "酉", "子": "酉", "辰": "酉", "寅": "卯", "午": "卯", "戌": "卯",
          "巳": "午", "酉": "午", "丑": "午", "亥": "子", "卯": "子", "未": "子"}
JIANGXING = {"申": "子", "子": "子", "辰": "子", "寅": "午", "午": "午", "戌": "午",
             "巳": "酉", "酉": "酉", "丑": "酉", "亥": "卯", "卯": "卯", "未": "卯"}
HUAGAI = {"申": "辰", "子": "辰", "辰": "辰", "寅": "戌", "午": "戌", "戌": "戌",
          "巳": "丑", "酉": "丑", "丑": "丑", "亥": "未", "卯": "未", "未": "未"}
JIESHA = {"申": "巳", "子": "巳", "辰": "巳", "寅": "亥", "午": "亥", "戌": "亥",
          "巳": "寅", "酉": "寅", "丑": "寅", "亥": "申", "卯": "申", "未": "申"}
ZAISHA = {"申": "午", "子": "午", "辰": "午", "寅": "子", "午": "子", "戌": "子",
          "巳": "卯", "酉": "卯", "丑": "卯", "亥": "酉", "卯": "酉", "未": "酉"}
WANGSHEN = {"申": "亥", "子": "亥", "辰": "亥", "寅": "巳", "午": "巳", "戌": "巳",
            "巳": "申", "酉": "申", "丑": "申", "亥": "寅", "卯": "寅", "未": "寅"}

# 年支类
HONGLUAN = {"子": "卯", "丑": "寅", "寅": "丑", "卯": "子", "辰": "亥", "巳": "戌",
            "午": "酉", "未": "申", "申": "未", "酉": "午", "戌": "巳", "亥": "辰"}
TIANXI = {"子": "酉", "丑": "申", "寅": "未", "卯": "午", "辰": "巳", "巳": "辰",
          "午": "卯", "未": "寅", "申": "丑", "酉": "子", "戌": "亥", "亥": "戌"}

# 三合局：支 → (首, 中, 尾)
SANHE = {"申": ("申", "子", "辰"), "子": ("申", "子", "辰"), "辰": ("申", "子", "辰"),
         "寅": ("寅", "午", "戌"), "午": ("寅", "午", "戌"), "戌": ("寅", "午", "戌"),
         "巳": ("巳", "酉", "丑"), "酉": ("巳", "酉", "丑"), "丑": ("巳", "酉", "丑"),
         "亥": ("亥", "卯", "未"), "卯": ("亥", "卯", "未"), "未": ("亥", "卯", "未")}

# 纳音五行（六十甲子）
NAYIN_WX = {
    "甲子": "金", "乙丑": "金", "丙寅": "火", "丁卯": "火", "戊辰": "木", "己巳": "木",
    "庚午": "土", "辛未": "土", "壬申": "金", "癸酉": "金", "甲戌": "火", "乙亥": "火",
    "丙子": "水", "丁丑": "水", "戊寅": "土", "己卯": "土", "庚辰": "金", "辛巳": "金",
    "壬午": "木", "癸未": "木", "甲申": "水", "乙酉": "水", "丙戌": "土", "丁亥": "土",
    "戊子": "火", "己丑": "火", "庚寅": "木", "辛卯": "木", "壬辰": "水", "癸巳": "水",
    "甲午": "金", "乙未": "金", "丙申": "火", "丁酉": "火", "戊戌": "木", "己亥": "木",
    "庚子": "土", "辛丑": "土", "壬寅": "金", "癸卯": "金", "甲辰": "火", "乙巳": "火",
    "丙午": "水", "丁未": "水", "戊申": "土", "己酉": "土", "庚戌": "金", "辛亥": "金",
    "壬子": "木", "癸丑": "木", "甲寅": "水", "乙卯": "水", "丙辰": "土", "丁巳": "土",
    "戊午": "火", "己未": "火", "庚申": "木", "辛酉": "木", "壬戌": "水", "癸亥": "水",
}
# 纳音长生位 / 临官位（土水同宫；问真 词馆/正词馆 用 土→亥 临官）
CS_BY_WX = {"金": "巳", "木": "亥", "水": "申", "火": "寅", "土": "申"}
LG_BY_WX = {"金": "申", "木": "寅", "水": "亥", "火": "巳", "土": "亥"}


def _zhi_plus(zhi: str, n: int) -> str:
    return DIZHI[(DIZHI.index(zhi) + n) % 12]


def _xun_kong_zhi(gan: str, zhi: str) -> str:
    """日/年柱干支所在旬的空亡支（如 甲子→戌亥）。"""
    head = (DIZHI.index(zhi) - TIANGAN.index(gan)) % 12
    return DIZHI[(head + 10) % 12] + DIZHI[(head + 11) % 12]


def _fired_pillars(pillars, target_zhi) -> List[int]:
    """target_zhi 支出现在四柱的柱位列表。"""
    return [i for i, p in enumerate(pillars) if p[1] == target_zhi]


def _per_pillar_rules(pillars: List[str], gender: str) -> dict:
    """问真 szshensha 计算口径：返回 name -> set(pillar_idx)。

    基于 50 案例逐柱反推 + 250 案例校准（2026-08-19），全部规则与问真 100% 一致。
    """
    result = {}  # name -> set of pillar indices

    def add(name, idx_set):
        if idx_set:
            result.setdefault(name, set()).update(idx_set)

    year_gan, year_zhi = pillars[0][0], pillars[0][1]
    month_gan, month_zhi = pillars[1][0], pillars[1][1]
    day_gan, day_zhi = pillars[2][0], pillars[2][1]
    time_gan, time_zhi = pillars[3][0], pillars[3][1]
    all_zhi = [pillars[i][1] for i in range(4)]
    all_gan = [pillars[i][0] for i in range(4)]

    # ---- 年干/日干双查（天乙贵人/太极贵人/文昌贵人/天厨贵人/福星贵人/金舆/国印贵人）
    for name, mapping in [
        ("天乙贵人", TIANYI_GUIREN), ("太极贵人", TAIJI), ("文昌贵人", WENCHANG),
        ("天厨贵人", TIANCHU), ("福星贵人", FUXING), ("金舆", JINYU), ("国印贵人", GUOYIN),
    ]:
        targets = set(mapping.get(year_gan, "")) | set(mapping.get(day_gan, ""))
        add(name, {i for i, z in enumerate(all_zhi) if z in targets})

    # ---- 日干单查（禄神/羊刃）
    add("禄神", {i for i, z in enumerate(all_zhi) if z in LUSHEN.get(day_gan, "")})
    add("羊刃", {i for i, z in enumerate(all_zhi) if z in YANGREN.get(day_gan, "")})

    # ---- 年支/日支双查（驿马/桃花/劫煞/亡神）
    for name, mapping in [("驿马", YIMA), ("桃花", TAOHUA),
                          ("劫煞", JIESHA), ("亡神", WANGSHEN)]:
        targets = set(mapping.get(year_zhi, "")) | set(mapping.get(day_zhi, ""))
        add(name, {i for i, z in enumerate(all_zhi) if z in targets})

    # ---- 灾煞：年支单查
    add("灾煞", {i for i, z in enumerate(all_zhi) if z in ZAISHA.get(year_zhi, "")})

    # ---- 将星/华盖：三合局中神/墓库，归属除主柱（年柱/日柱）外的含支柱位
    for name, mapping in [("将星", JIANGXING), ("华盖", HUAGAI)]:
        my = mapping.get(year_zhi, "")
        md = mapping.get(day_zhi, "")
        idx = set()
        for i, z in enumerate(all_zhi):
            if z == my and i != 0:
                idx.add(i)
            if z == md and i != 2:
                idx.add(i)
        add(name, idx)

    # ---- 年支类（红鸾/天喜/丧门/吊客/披麻/勾绞煞）
    add("红鸾", set(_fired_pillars(pillars, HONGLUAN.get(year_zhi, ""))))
    add("天喜", set(_fired_pillars(pillars, TIANXI.get(year_zhi, ""))))
    add("丧门", set(_fired_pillars(pillars, _zhi_plus(year_zhi, 2))))
    add("吊客", set(_fired_pillars(pillars, _zhi_plus(year_zhi, -2))))
    add("披麻", set(_fired_pillars(pillars, _zhi_plus(year_zhi, -3))))
    # 勾绞煞：年支前三辰（阳男阴女前三辰为勾、阴男阳女前三辰为绞，同支）
    add("勾绞煞", set(_fired_pillars(pillars, _zhi_plus(year_zhi, 3))))

    # ---- 孤辰寡宿：三合局位偏移（首+3/-1、中+2/-2、尾+1/-3），年支单查
    def _gu_chen_gu_su(zhi, kind):
        group = SANHE[zhi]
        pos = group.index(zhi)  # 0 首 1 中 2 尾
        if kind == "gu":
            return _zhi_plus(zhi, 3 - pos)
        return _zhi_plus(zhi, -(1 + pos))

    add("孤辰", set(_fired_pillars(pillars, _gu_chen_gu_su(year_zhi, "gu"))))
    add("寡宿", set(_fired_pillars(pillars, _gu_chen_gu_su(year_zhi, "su"))))

    # ---- 空亡：年柱旬 + 日柱旬（双旬），支在四柱即得
    kong = set(_xun_kong_zhi(year_gan, year_zhi)) | set(_xun_kong_zhi(day_gan, day_zhi))
    add("空亡", {i for i, z in enumerate(all_zhi) if z in kong})

    # ---- 元辰：阳男阴女大耗（对冲+1），阴男阳女小耗（对冲-1）
    is_male = gender == "男"
    year_yang = TIANGAN.index(year_gan) % 2 == 0
    da_hao = (is_male and year_yang) or (not is_male and not year_yang)
    yuan = _zhi_plus(year_zhi, 7) if da_hao else _zhi_plus(year_zhi, 5)
    add("元辰", set(_fired_pillars(pillars, yuan)))

    # ---- 学堂/正学堂：年纳音长生（除年柱、除正学堂柱）；
    #      正学堂 = 自柱纳音长生 == 自柱支 且 自柱纳音 == 年纳音
    y_nayin = NAYIN_WX.get(pillars[0], "")
    zt = {i for i in range(1, 4)
          if NAYIN_WX.get(pillars[i], "") == y_nayin
          and CS_BY_WX.get(NAYIN_WX.get(pillars[i], ""), "") == all_zhi[i]}
    add("正学堂", zt)
    xt_target = CS_BY_WX.get(y_nayin, "")
    add("学堂", {i for i in range(1, 4) if all_zhi[i] == xt_target} - zt)

    # ---- 词馆/正词馆：年纳音临官（土亥），最深含支柱（除年柱、除正词馆柱）；
    #      正词馆 = 自柱纳音临官 == 自柱支 且 自柱纳音 == 年纳音
    zc = {i for i in range(1, 4)
          if NAYIN_WX.get(pillars[i], "") == y_nayin
          and LG_BY_WX.get(NAYIN_WX.get(pillars[i], ""), "") == all_zhi[i]}
    add("正词馆", zc)
    cg_target = LG_BY_WX.get(y_nayin, "")
    cg_hits = [i for i in range(1, 4) if all_zhi[i] == cg_target and i not in zc]
    if cg_hits:
        add("词馆", {max(cg_hits)})

    # ---- 天罗地网：日支戌亥（年纳音火）/ 日支辰巳（年纳音水土）
    if y_nayin == "火" and day_zhi in "戌亥":
        add("天罗", {2})
    elif y_nayin in ("水", "土") and day_zhi in "辰巳":
        add("地网", {2})

    return result


def shensha_of(year_pillar: str, day_pillar: str,
               all_gan: Optional[List[str]] = None,
               all_zhi: Optional[List[str]] = None,
               gender: str = "男") -> List[ShenshaItem]:
    """四柱神煞（问真 szshensha 口径）。

    :param year_pillar: 年柱干支，如 "庚午"
    :param day_pillar:  日柱干支，如 "乙酉"
    :param all_gan:     四柱天干 [年,月,日,时]
    :param all_zhi:     四柱地支 [年,月,日,时]
    :param gender:      性别（勾绞煞需用）
    """
    pillars = []
    if all_gan and all_zhi and len(all_gan) == 4 and len(all_zhi) == 4:
        pillars = [all_gan[i] + all_zhi[i] for i in range(4)]
    else:
        # 兼容旧调用：只有年柱/日柱时，其他柱用空占位
        pillars = [year_pillar, "??", day_pillar, "??"]

    if len(pillars) == 4 and "?" not in "".join(pillars):
        rules = _per_pillar_rules(pillars, gender)
        merged = {}
        for name, idx_set in rules.items():
            source = "".join("年月日时"[i] for i in sorted(idx_set)) + "柱"
            luck = SHENSHA_LUCK.get(name, "中性")
            merged[name] = ShenshaItem(name=name, source=source, luck=luck)
        return list(merged.values())

    # 兜底：旧口径（年柱+日柱查表 + 按日干计算）
    merged = {}

    def _add(name: str, source: str) -> None:
        luck = SHENSHA_LUCK.get(name, "中性")
        if name in merged:
            old = merged[name]
            if source not in old.source:
                old.source = old.source + "+" + source
        else:
            merged[name] = ShenshaItem(name=name, source=source, luck=luck)

    for name in shensha_by_jiazi(year_pillar):
        _add(name, "年柱")
    for name in shensha_by_jiazi(day_pillar):
        _add(name, "日柱")

    day_gan = day_pillar[0]
    day_zhi = day_pillar[1]
    zhi_for_check = all_zhi if all_zhi else [day_zhi]
    for name, targets in [("天乙贵人", TIANYI_GUIREN.get(day_gan, "")),
                          ("驿马", YIMA.get(day_zhi, "")),
                          ("禄神", LUSHEN.get(day_gan, "")),
                          ("羊刃", YANGREN.get(day_gan, ""))]:
        if any(z in targets for z in zhi_for_check):
            _add(name, "计算")

    return list(merged.values())


def shensha_names(year_pillar: str, day_pillar: str,
                  all_gan: Optional[List[str]] = None,
                  all_zhi: Optional[List[str]] = None,
                  gender: str = "男") -> List[str]:
    """shensha_of 的名称简版（BaziResult.shensha 字段用，兼容旧消费方）。"""
    return [item.name for item in shensha_of(year_pillar, day_pillar, all_gan, all_zhi, gender)]
