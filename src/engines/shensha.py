"""神煞引擎 — 问真八字口径（2026-08-20：32 种计算 + 问真独有 27 种 = 59 种全集，250 案例覆盖率 100%）。

问真 szshensha（四柱神煞）为纯计算规则，非 60 甲子查表（速查表仅用于大运神煞 dyshensha）：
- 年干+日干双查：天乙贵人、太极贵人、文昌贵人、天厨贵人、福星贵人、金舆、国印贵人
- 日干单查：禄神、羊刃、飞刃（刃之对冲）、红艳煞、流霞
- 年支+日支双查：驿马、桃花、劫煞、亡神；年支单查：灾煞
- 月支查：天医（前一位）、血刃（问真口径逐支表）、月德贵人（三合局阳干）、
  月德合、天德贵人、天德合、德秀贵人（月支三合局 德/秀 天干，申子辰月另计甲丙辛）
- 日柱固定表：十灵日/阴差阳错/十恶大败/八专日/九丑日/孤鸾煞/六秀日/魁罡日/金神（日时）
- 季节（月支）日柱表：四废日/天赦日/天转日/地转日
- 拱禄（日时拱日干之禄）、三奇贵人（年月中连续三柱顺排 甲戊庚/乙丙丁/壬癸辛）
- 童子煞：春秋（月支）日时见寅子、冬夏见卯未辰、金木命见午卯、水火命见酉戌、土命见辰巳
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
- 天罗地网：戌亥/辰巳 对同时出现（仅月时柱(1,3) 不成网），命中柱 = 年支±1/日支±1 ∩ {戌亥辰巳}

数据基准：问真 getshensha60（大运神煞速查表）+ szshensha 逐柱反推
（250 案例全量校准 2026-08-20：59 种神煞 4125 实例覆盖率 100%，250/250 案例全集一致，
scripts/compare_qz_shensha_full.py 可复跑）。
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
# 问真独有 27 种（2026-08-20 校准补全）：吉——德秀/月德/天德/天德合/月德合/天医/
# 天赦/三奇/拱禄/六秀/十灵；凶——红艳/孤鸾/阴差阳错/十恶大败/八专/九丑/四废/天转/地转；
# 中性偏凶——魁罡/金神/飞刃/血刃/流霞/童子/天罗地网（保守标注，问真未提供吉凶）
SHENSHA_LUCK = {
    "天乙贵人": "吉", "太极贵人": "吉", "文昌贵人": "吉", "福星贵人": "吉",
    "天厨贵人": "吉", "将星": "吉", "驿马": "吉", "金舆": "吉", "国印贵人": "吉",
    "学堂": "吉", "词馆": "吉", "正学堂": "吉", "正词馆": "吉",
    "天喜": "吉", "红鸾": "吉",
    "德秀贵人": "吉", "月德贵人": "吉", "月德合": "吉", "天德贵人": "吉",
    "天德合": "吉", "天医": "吉", "天赦日": "吉", "三奇贵人": "吉",
    "拱禄": "吉", "六秀日": "吉", "十灵日": "吉",
    "桃花": "中性偏吉",
    "华盖": "中性",
    "寡宿": "凶", "孤辰": "凶", "吊客": "凶", "披麻": "凶", "丧门": "凶",
    "亡神": "凶", "元辰": "凶", "劫煞": "凶", "勾绞煞": "凶", "灾煞": "凶",
    "地网": "凶", "空亡": "凶", "天罗": "凶",
    "红艳煞": "凶", "孤鸾煞": "凶", "阴差阳错": "凶", "十恶大败": "凶",
    "八专日": "凶", "九丑日": "凶", "四废日": "凶", "天转日": "凶", "地转日": "凶",
    "魁罡日": "中性偏凶", "金神": "中性偏凶", "飞刃": "中性偏凶", "血刃": "中性偏凶",
    "流霞": "中性偏凶", "童子煞": "中性偏凶", "天罗地网": "中性偏凶",
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

# ---------------------------------------------------------------- 问真独有 27 种（250 案例反推，2026-08-20）
# 月德贵人：月支三合局阳干（寅午戌→丙、申子辰→壬、亥卯未→甲、巳酉丑→庚）
YUEDE = {"寅": "丙", "午": "丙", "戌": "丙", "申": "壬", "子": "壬", "辰": "壬",
         "亥": "甲", "卯": "甲", "未": "甲", "巳": "庚", "酉": "庚", "丑": "庚"}
# 月德合：月德之合（丙→辛、壬→丁、甲→己、庚→乙）
YUEDE_HE = {"丙": "辛", "壬": "丁", "甲": "己", "庚": "乙"}
# 天德贵人：月支查（正月丁、二月申、三月壬、四月辛、五月亥、六月甲、七月癸、
# 八月寅、九月丙、十月乙、十一月巳、十二月庚）——目标可为天干或地支
TIANDE = {"寅": "丁", "卯": "申", "辰": "壬", "巳": "辛", "午": "亥", "未": "甲",
          "申": "癸", "酉": "寅", "戌": "丙", "亥": "乙", "子": "巳", "丑": "庚"}
# 天德合：天德目标之合（干支均含：丁→壬、申→巳、壬→丁、辛→丙、亥→寅、甲→己、
# 癸→戊、寅→亥、丙→辛、乙→庚、巳→申、庚→乙）
TIANDE_HE = {"丁": "壬", "壬": "丁", "辛": "丙", "丙": "辛", "甲": "己", "己": "甲",
             "癸": "戊", "戊": "癸", "乙": "庚", "庚": "乙",
             "申": "巳", "巳": "申", "亥": "寅", "寅": "亥"}
# 德秀贵人：月支三合局 → 德/秀天干集（四柱天干任一命中即得，source=命中柱）
# 寅午戌：丙丁为德、戊癸为秀；巳酉丑：庚辛为德、乙庚为秀；亥卯未：甲乙为德、丁壬为秀；
# 申子辰：壬癸为德、戊己为秀，问真口径另计甲丙辛（丙辛合化水=德之化、甲己合=秀之化；
# 丁不在此列——API 逐柱核对 4 例：丁均不挂柱），申子辰月 57/57 全命中，无负例
DEXIU = {"寅": "丙丁戊癸", "午": "丙丁戊癸", "戌": "丙丁戊癸",
         "申": "甲丙辛壬癸戊己", "子": "甲丙辛壬癸戊己", "辰": "甲丙辛壬癸戊己",
         "巳": "乙庚辛", "酉": "乙庚辛", "丑": "乙庚辛",
         "亥": "甲乙丁壬", "卯": "甲乙丁壬", "未": "甲乙丁壬"}
# 天医：月支前一辰（正月丑、二月寅…）
# 飞刃：日干羊刃之对冲（甲酉、乙申、丙戊子、丁己亥、庚卯、辛寅、壬午、癸巳）
FEIREN = {"甲": "酉", "乙": "申", "丙": "子", "丁": "亥", "戊": "子", "己": "亥",
          "庚": "卯", "辛": "寅", "壬": "午", "癸": "巳"}
# 红艳煞：日干查（问真口径：乙红艳在午，异于通行口诀之申）
HONGYAN = {"甲": "午", "乙": "午", "丙": "寅", "丁": "未", "戊": "辰", "己": "辰",
           "庚": "戌", "辛": "酉", "壬": "子", "癸": "申"}
# 流霞：日干查（问真口径：丁→申、己→午、庚→辰、辛→卯、癸→寅，异于通行口诀）
LIUXIA = {"甲": "酉", "乙": "戌", "丙": "未", "丁": "申", "戊": "巳", "己": "午",
          "庚": "辰", "辛": "卯", "壬": "亥", "癸": "寅"}
# 血刃：月支查（问真口径逐支反推：子午、丑子、寅丑、卯未、辰寅、巳申、午卯、
# 未酉、申辰、酉戌、戌巳、亥亥）
XUEREN = {"子": "午", "丑": "子", "寅": "丑", "卯": "未", "辰": "寅", "巳": "申",
          "午": "卯", "未": "酉", "申": "辰", "酉": "戌", "戌": "巳", "亥": "亥"}
# 日柱固定表：十灵日 / 阴差阳错 / 十恶大败 / 八专日（问真 8 日，含癸丑戊戌，
# 不含通行版之戊午壬子癸亥）/ 九丑日（问真 9 日，无丁卯）/ 孤鸾煞 / 六秀日
# （问真 6 日：己丑己未，非通行版己酉辛亥）/ 魁罡日
SHILING_DAYS = {"甲辰", "乙亥", "丙辰", "丁酉", "戊午", "庚戌", "庚寅", "辛亥", "壬寅", "癸未"}
YINCHA_YANGCO = {"丙子", "丙午", "丁丑", "丁未", "戊寅", "戊申", "辛卯", "辛酉",
                 "壬辰", "壬戌", "癸巳", "癸亥"}
SHIE_DA_BAI = {"甲辰", "乙巳", "丙申", "丁亥", "戊戌", "己丑", "庚辰", "辛巳", "壬申", "癸亥"}
BAZHUAN_DAYS = {"甲寅", "乙卯", "丁未", "己未", "庚申", "辛酉", "癸丑", "戊戌"}
JIUCHOU_DAYS = {"丁酉", "壬午", "壬子", "己卯", "己酉", "戊午", "戊子", "辛卯", "辛酉"}
GULUAN_DAYS = {"乙巳", "丙午", "丁巳", "戊午", "戊申", "辛亥", "甲寅", "壬子"}
LIUXIU_DAYS = {"丙午", "丁未", "戊子", "戊午", "己丑", "己未"}
KUI_GANG_DAYS = {"庚辰", "庚戌", "壬辰", "戊戌"}
# 金神：日柱/时柱 ∈ {乙丑,己巳,癸酉}（金神三局；甲午不在此列——250 案例 2 例反证）
JINSHEN_3 = ("乙丑", "己巳", "癸酉")
# 四废日：春庚申辛酉、夏壬子癸亥、秋甲寅乙卯、冬丙午丁巳（按月支季节）
SIFEI_BY_SEASON = {"寅卯辰": ("庚申", "辛酉"), "巳午未": ("壬子", "癸亥"),
                   "申酉戌": ("甲寅", "乙卯"), "亥子丑": ("丙午", "丁巳")}
# 天赦日：春戊寅、夏甲午、秋戊申、冬甲子
TIANSHE_BY_SEASON = {"寅卯辰": "戊寅", "巳午未": "甲午", "申酉戌": "戊申", "亥子丑": "甲子"}
# 天转日：春乙卯、夏丙午、秋辛酉、冬壬子
TIANZHUAN_BY_SEASON = {"寅卯辰": "乙卯", "巳午未": "丙午", "申酉戌": "辛酉", "亥子丑": "壬子"}
# 地转日：春辛卯、夏戊午、秋癸酉、冬丙子
DIZHUAN_BY_SEASON = {"寅卯辰": "辛卯", "巳午未": "戊午", "申酉戌": "癸酉", "亥子丑": "丙子"}
# 童子煞：春秋（月支寅卯辰申酉戌）日时支见寅子；冬夏见卯未辰；
# 金木命（年纳音）日时支见午卯；水火命见酉戌；土命见辰巳
TONGZI_RULE = [
    ("寅卯辰申酉戌", "寅子"), ("亥子丑巳午未", "卯未辰"),
    ("金木", "午卯"), ("水火", "酉戌"), ("土", "辰巳"),
]
# 三奇贵人：年月日时天干连续三柱顺排 = 甲戊庚（天上）/ 乙丙丁（地下）/ 壬癸辛（人中）
SANQI_TRIPLES = (("甲", "戊", "庚"), ("乙", "丙", "丁"), ("壬", "癸", "辛"))

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

    # ---- 问真独有 27 种（250 案例全量反推，2026-08-20 校准）----

    # 月德贵人 / 月德合：月支三合局阳干及其合，命中四柱天干所在柱
    yd = YUEDE.get(month_zhi, "")
    add("月德贵人", {i for i, g in enumerate(all_gan) if g == yd})
    ydh = YUEDE_HE.get(yd, "")
    add("月德合", {i for i, g in enumerate(all_gan) if g == ydh})

    # 天德贵人 / 天德合：月支查天德（天干或地支目标）及其合，命中柱
    td = TIANDE.get(month_zhi, "")
    if td in TIANGAN:
        add("天德贵人", {i for i, g in enumerate(all_gan) if g == td})
        tdh = TIANDE_HE.get(td, "")
        add("天德合", {i for i, g in enumerate(all_gan) if g == tdh})
    else:
        add("天德贵人", {i for i, z in enumerate(all_zhi) if z == td})
        tdh = TIANDE_HE.get(td, "")
        add("天德合", {i for i, z in enumerate(all_zhi) if z == tdh})

    # 德秀贵人：月支三合局 → 德/秀天干集，命中四柱天干所在柱（申子辰月含丙丁，
    # 问真口径 250 案例申子辰月 57 例全命中）
    add("德秀贵人", {i for i, g in enumerate(all_gan) if g in DEXIU.get(month_zhi, "")})

    # 天医：月支前一辰
    add("天医", set(_fired_pillars(pillars, _zhi_plus(month_zhi, -1))))

    # 飞刃 / 红艳煞 / 流霞：日干查支，命中四柱
    add("飞刃", {i for i, z in enumerate(all_zhi) if z in FEIREN.get(day_gan, "")})
    add("红艳煞", {i for i, z in enumerate(all_zhi) if z in HONGYAN.get(day_gan, "")})
    add("流霞", {i for i, z in enumerate(all_zhi) if z in LIUXIA.get(day_gan, "")})

    # 血刃：月支查支，命中四柱（问真口径逐支反推表）
    add("血刃", {i for i, z in enumerate(all_zhi) if z in XUEREN.get(month_zhi, "")})

    # 日柱固定表（十灵日/阴差阳错/十恶大败/八专日/九丑日/孤鸾煞/六秀日/魁罡日）
    for name, days in [("十灵日", SHILING_DAYS), ("阴差阳错", YINCHA_YANGCO),
                       ("十恶大败", SHIE_DA_BAI), ("八专日", BAZHUAN_DAYS),
                       ("九丑日", JIUCHOU_DAYS), ("孤鸾煞", GULUAN_DAYS),
                       ("六秀日", LIUXIU_DAYS), ("魁罡日", KUI_GANG_DAYS)]:
        if pillars[2] in days:
            add(name, {2})

    # 金神：日柱/时柱 ∈ {乙丑,己巳,癸酉}（金神三局）
    for i in (2, 3):
        if pillars[i] in JINSHEN_3:
            add("金神", {i})

    # 四废日 / 天赦日 / 天转日 / 地转日：按季节（月支）查日柱
    season = next(s for s in SIFEI_BY_SEASON if month_zhi in s)
    if pillars[2] in SIFEI_BY_SEASON[season]:
        add("四废日", {2})
    for name, tbl in [("天赦日", TIANSHE_BY_SEASON), ("天转日", TIANZHUAN_BY_SEASON),
                      ("地转日", DIZHUAN_BY_SEASON)]:
        if pillars[2] == tbl[season]:
            add(name, {2})

    # 拱禄：日时天干相同、支相隔一位（拱），拱中支为日干之禄
    if day_gan == time_gan:
        d_i, t_i = DIZHI.index(day_zhi), DIZHI.index(time_zhi)
        if (t_i - d_i) % 12 == 2:
            mid = DIZHI[(d_i + 1) % 12]
            if mid == LUSHEN.get(day_gan, ""):
                add("拱禄", {2})
        elif (d_i - t_i) % 12 == 2:
            mid = DIZHI[(t_i + 1) % 12]
            if mid == LUSHEN.get(day_gan, ""):
                add("拱禄", {2})

    # 三奇贵人：年月日时天干连续三柱顺排 = 甲戊庚/乙丙丁/壬癸辛（source=三奇末柱）
    for i in range(2):
        if tuple(all_gan[i:i + 3]) in SANQI_TRIPLES:
            add("三奇贵人", {i + 2})

    # 童子煞：日时支查（春秋寅子、冬夏卯未辰、金木命午卯、水火命酉戌、土命辰巳）
    tongzi_hits = set()
    for key, target in TONGZI_RULE:
        cond = False
        if key in ("寅卯辰申酉戌", "亥子丑巳午未"):
            cond = month_zhi in key
        else:  # 年纳音五行
            cond = y_nayin in key
        if cond and (set(all_zhi[2:4]) & set(target)):
            tongzi_hits |= {i for i in (2, 3) if all_zhi[i] in target}
    add("童子煞", tongzi_hits)

    # 天罗地网：戌亥/辰巳 对同时出现（罗网成）——但 对 仅落在月柱+时柱 (1,3) 时不成网
    # （250 案例反推：仅 (1,3) 对 的案例问真均不报），命中柱 = 年支±1/日支±1 ∩ {戌亥辰巳}
    def _pair_ok(s1, s2):
        hits1 = [i for i, z in enumerate(all_zhi) if z == s1]
        hits2 = [i for i, z in enumerate(all_zhi) if z == s2]
        for i in hits1:
            for j in hits2:
                if i < j and (i, j) != (1, 3):
                    return True
                if i > j and (j, i) != (1, 3):
                    return True
        return False

    pair_ok = _pair_ok("戌", "亥") or _pair_ok("辰", "巳")
    if pair_ok:
        tldw_targets = set()
        for base in (year_zhi, day_zhi):
            b = DIZHI.index(base)
            for off in (1, -1):
                z = DIZHI[(b + off) % 12]
                if z in "戌亥辰巳":
                    tldw_targets.add(z)
        add("天罗地网", {i for i, z in enumerate(all_zhi) if z in tldw_targets})

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


def _gu_chen_gu_su_zhi(zhi: str, kind: str) -> str:
    """孤辰/寡宿目标支（三合局位偏移：首+3/-1、中+2/-2、尾+1/-3），年支单查。"""
    group = SANHE[zhi]
    pos = group.index(zhi)  # 0 首 1 中 2 尾
    if kind == "gu":
        return _zhi_plus(zhi, 3 - pos)
    return _zhi_plus(zhi, -(1 + pos))


def shensha_of_dayun(dayun_pillar: str, year_pillar: str, month_pillar: str,
                     day_pillar: str, gender: str = "男") -> List[str]:
    """大运神煞（问真 dyshensha 口径，2026-08-21 从语料 8658 例全量反推 100% 一致）。

    问真 dyshensha = 大运干支逐柱套用 szshensha 的通用规则（41 种）：以命盘
    年/月/日柱为规则源，大运干支为命中对象——大运支查支类规则、大运干查干类
    规则；纯日柱规则（十灵日/阴差阳错/十恶大败/八专日/九丑日/孤鸾煞/六秀日/
    魁罡日/金神/四废日/天赦日/天转日/地转日）与柱组合规则（拱禄/三奇/童子煞）
    属原局判定，不适用于大运柱（语料中从未出现）。天罗地网为"互补成网"口径：
    大运支 ∈ {戌亥辰巳} 且 年支或日支 == 其互补支（辰↔巳、戌↔亥）即成网——
    与四柱 szshensha 的"四柱内成对"口径不同（大运柱本身充当网的另一支）。
    名序为引擎规则序（问真服务端输出无序——语料 49 对名称顺序两可，见
    scripts 反推记录），消费方按集合比对。

    :param dayun_pillar: 大运干支，如 "丁丑"
    :param year_pillar/month_pillar/day_pillar: 命盘年/月/日柱
    :param gender: 性别（元辰需用）
    """
    dg, dz = dayun_pillar[0], dayun_pillar[1]
    yg, yz = year_pillar[0], year_pillar[1]
    mz = month_pillar[1]
    day_gan, day_zhi = day_pillar[0], day_pillar[1]
    out = []

    def _add(name: str) -> None:
        out.append(name)

    # ---- 年干/日干双查（支命中）
    for name, mapping in [
        ("天乙贵人", TIANYI_GUIREN), ("太极贵人", TAIJI), ("文昌贵人", WENCHANG),
        ("天厨贵人", TIANCHU), ("福星贵人", FUXING), ("金舆", JINYU), ("国印贵人", GUOYIN),
    ]:
        if dz in set(mapping.get(yg, "")) | set(mapping.get(day_gan, "")):
            _add(name)

    # ---- 日干单查（支命中）
    for name, mapping in [
        ("禄神", LUSHEN), ("羊刃", YANGREN), ("飞刃", FEIREN),
        ("红艳煞", HONGYAN), ("流霞", LIUXIA),
    ]:
        if dz in mapping.get(day_gan, ""):
            _add(name)

    # ---- 年支/日支双查（支命中）
    for name, mapping in [("驿马", YIMA), ("桃花", TAOHUA),
                          ("劫煞", JIESHA), ("亡神", WANGSHEN)]:
        if dz in set(mapping.get(yz, "")) | set(mapping.get(day_zhi, "")):
            _add(name)

    # ---- 灾煞：年支单查
    if dz in ZAISHA.get(yz, ""):
        _add("灾煞")

    # ---- 年支类（红鸾/天喜/丧门/吊客/披麻/勾绞煞）
    if dz == HONGLUAN.get(yz, ""):
        _add("红鸾")
    if dz == TIANXI.get(yz, ""):
        _add("天喜")
    if dz == _zhi_plus(yz, 2):
        _add("丧门")
    if dz == _zhi_plus(yz, -2):
        _add("吊客")
    if dz == _zhi_plus(yz, -3):
        _add("披麻")
    if dz == _zhi_plus(yz, 3):
        _add("勾绞煞")

    # ---- 孤辰/寡宿：年支单查
    if dz == _gu_chen_gu_su_zhi(yz, "gu"):
        _add("孤辰")
    if dz == _gu_chen_gu_su_zhi(yz, "su"):
        _add("寡宿")

    # ---- 元辰：阳男阴女大耗（对冲+1）、阴男阳女小耗（对冲-1）
    is_male = gender == "男"
    year_yang = TIANGAN.index(yg) % 2 == 0
    da_hao = (is_male and year_yang) or (not is_male and not year_yang)
    if dz == (_zhi_plus(yz, 7) if da_hao else _zhi_plus(yz, 5)):
        _add("元辰")

    # ---- 空亡：年柱旬 + 日柱旬（双旬），支命中
    kong = set(_xun_kong_zhi(yg, yz)) | set(_xun_kong_zhi(day_gan, day_zhi))
    if dz in kong:
        _add("空亡")

    # ---- 将星/华盖：年支/日支三合局中神/墓库（大运柱无柱位排除）
    if dz in (JIANGXING.get(yz, ""), JIANGXING.get(day_zhi, "")):
        _add("将星")
    if dz in (HUAGAI.get(yz, ""), HUAGAI.get(day_zhi, "")):
        _add("华盖")

    # ---- 月支查：月德/月德合/天德/天德合/德秀（干命中）、天医/血刃（支命中）
    yd = YUEDE.get(mz, "")
    if dg == yd:
        _add("月德贵人")
    if dg == YUEDE_HE.get(yd, ""):
        _add("月德合")
    td = TIANDE.get(mz, "")
    if td in TIANGAN:
        if dg == td:
            _add("天德贵人")
        if dg == TIANDE_HE.get(td, ""):
            _add("天德合")
    else:
        if dz == td:
            _add("天德贵人")
        if dz == TIANDE_HE.get(td, ""):
            _add("天德合")
    if dg in DEXIU.get(mz, ""):
        _add("德秀贵人")
    if dz == _zhi_plus(mz, -1):
        _add("天医")
    if dz in XUEREN.get(mz, ""):
        _add("血刃")

    # ---- 学堂/正学堂、词馆/正词馆：年纳音长生/临官位（大运柱无柱位排除；
    #      正=大运自柱纳音 == 年纳音，同 szshensha 口径）
    y_nayin = NAYIN_WX.get(year_pillar, "")
    if dz == CS_BY_WX.get(y_nayin, ""):
        if NAYIN_WX.get(dayun_pillar, "") == y_nayin:
            _add("正学堂")
        else:
            _add("学堂")
    if dz == LG_BY_WX.get(y_nayin, ""):
        if NAYIN_WX.get(dayun_pillar, "") == y_nayin:
            _add("正词馆")
        else:
            _add("词馆")

    # ---- 天罗：大运支戌亥（年纳音火）；地网：大运支辰巳（年纳音水土）
    if y_nayin == "火" and dz in "戌亥":
        _add("天罗")
    elif y_nayin in ("水", "土") and dz in "辰巳":
        _add("地网")

    # ---- 天罗地网：互补成网（大运支 ∈ {戌亥辰巳} 且 年支或日支 == 互补支）
    complement = {"戌": "亥", "亥": "戌", "辰": "巳", "巳": "辰"}
    if dz in complement and (complement[dz] == yz or complement[dz] == day_zhi):
        _add("天罗地网")

    return out
