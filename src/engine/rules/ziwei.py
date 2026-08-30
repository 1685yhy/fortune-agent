# src/engine/rules/ziwei.py
"""紫微斗数确定性规则库 v1：五行局/生年四化/十二宫序/主星分布，纯查表实现。

口径说明（audit 依据，紫微斗数全书·安星诀）：
1. 五行局：以**命宫干支纳音**定局（水二/木三/金四/土五/火六），与
   src/engines/ziwei.py 的 _calc_wuxing_ju 口径一致，考卷断言与 ZiweiEngine
   真实输出交叉验证一致。
   注：任务书所述"生年纳音口径"经实证与引擎不符——如庚午年纳音路旁土为
   土五局，而 1990-05-20（庚午年四月廿六申时）命宫乙酉泉中水实为水二局；
   按计划"考卷断言与 ZiweiEngine 输出一致"的硬要求，本库以命宫干支纳音
   口径为准，生年纳音可经 wuxing_ju_by_nayin 查任意干支纳音。
2. 生年四化：十干四化表（甲廉破武阳…癸破巨阴贪），与引擎 SIHUA_TABLE 一致。
3. 十二宫序：命宫起逆时针布十二宫（命宫→兄弟→…→父母）；阴阳只影响大限
   顺逆（dayun），不影响十二宫本身，此处复现引擎口径。
4. 主星分布：紫微系自紫微起逆时针（紫微→天机→太阳→武曲→天同→廉贞），
   天府系自天府起顺时针（天府→太阴→贪狼→巨门→天相→天梁→七杀→破军）；
   天府与紫微对称于寅申线（紫微寅申天府同宫，子起索引 天府 = (4 - 紫微) mod 12）。

只做确定性查表事实，不做解释性断语（断语归 LLM 综合层）。
"""
from __future__ import annotations

# 六十甲子纳音表（全 60 组，定五行局的基础数据）
NAYIN = {
    "甲子": "海中金", "乙丑": "海中金", "丙寅": "炉中火", "丁卯": "炉中火",
    "戊辰": "大林木", "己巳": "大林木", "庚午": "路旁土", "辛未": "路旁土",
    "壬申": "剑锋金", "癸酉": "剑锋金", "甲戌": "山头火", "乙亥": "山头火",
    "丙子": "涧下水", "丁丑": "涧下水", "戊寅": "城头土", "己卯": "城头土",
    "庚辰": "白蜡金", "辛巳": "白蜡金", "壬午": "杨柳木", "癸未": "杨柳木",
    "甲申": "泉中水", "乙酉": "泉中水", "丙戌": "屋上土", "丁亥": "屋上土",
    "戊子": "霹雳火", "己丑": "霹雳火", "庚寅": "松柏木", "辛卯": "松柏木",
    "壬辰": "长流水", "癸巳": "长流水", "甲午": "沙中金", "乙未": "沙中金",
    "丙申": "山下火", "丁酉": "山下火", "戊戌": "平地木", "己亥": "平地木",
    "庚子": "壁上土", "辛丑": "壁上土", "壬寅": "金箔金", "癸卯": "金箔金",
    "甲辰": "覆灯火", "乙巳": "覆灯火", "丙午": "天河水", "丁未": "天河水",
    "戊申": "大驿土", "己酉": "大驿土", "庚戌": "钗钏金", "辛亥": "钗钏金",
    "壬子": "桑柘木", "癸丑": "桑柘木", "甲寅": "大溪水", "乙卯": "大溪水",
    "丙辰": "沙中土", "丁巳": "沙中土", "戊午": "天上火", "己未": "天上火",
    "庚申": "石榴木", "辛酉": "石榴木", "壬戌": "大海水", "癸亥": "大海水",
}

# 纳音五行 → 五行局数（水二/木三/金四/土五/火六）
NAYIN_JU = {
    "海中金": 4, "剑锋金": 4, "白蜡金": 4, "沙中金": 4, "金箔金": 4, "钗钏金": 4,
    "大林木": 3, "杨柳木": 3, "松柏木": 3, "平地木": 3, "桑柘木": 3, "石榴木": 3,
    "涧下水": 2, "泉中水": 2, "长流水": 2, "天河水": 2, "大溪水": 2, "大海水": 2,
    "炉中火": 6, "山头火": 6, "霹雳火": 6, "山下火": 6, "覆灯火": 6, "天上火": 6,
    "路旁土": 5, "城头土": 5, "屋上土": 5, "壁上土": 5, "大驿土": 5, "沙中土": 5,
}
JU_NAMES = {2: "水二局", 3: "木三局", 4: "金四局", 5: "土五局", 6: "火六局"}

TIANGAN = "甲乙丙丁戊己庚辛壬癸"
DIZHI = "子丑寅卯辰巳午未申酉戌亥"
SHICHEN_INDEX = {z: i for i, z in enumerate(DIZHI)}

# 五虎遁：年干 → 寅月天干（定命宫天干用）
WUHU_DUN = {
    "甲": "丙", "乙": "戊", "丙": "庚", "丁": "壬", "戊": "甲",
    "己": "丙", "庚": "戊", "辛": "庚", "壬": "壬", "癸": "甲",
}

# 生年四化表：年干 → {禄/权/科/忌: 星曜}（甲廉破武阳…癸破巨阴贪）
SIHUA = {
    "甲": {"禄": "廉贞", "权": "破军", "科": "武曲", "忌": "太阳"},
    "乙": {"禄": "天机", "权": "天梁", "科": "紫微", "忌": "太阴"},
    "丙": {"禄": "天同", "权": "天机", "科": "文昌", "忌": "廉贞"},
    "丁": {"禄": "太阴", "权": "天同", "科": "天机", "忌": "巨门"},
    "戊": {"禄": "贪狼", "权": "太阴", "科": "右弼", "忌": "天机"},
    "己": {"禄": "武曲", "权": "贪狼", "科": "天梁", "忌": "文曲"},
    "庚": {"禄": "太阳", "权": "武曲", "科": "太阴", "忌": "天同"},
    "辛": {"禄": "巨门", "权": "太阳", "科": "文曲", "忌": "文昌"},
    "壬": {"禄": "天梁", "权": "紫微", "科": "左辅", "忌": "武曲"},
    "癸": {"禄": "破军", "权": "巨门", "科": "太阴", "忌": "贪狼"},
}

# 十二宫定序（命宫起逆时针）
PALACE_ORDER = [
    "命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
    "迁移", "交友", "官禄", "田宅", "福德", "父母",
]

# 紫微系：自紫微起逆时针（offset 为地支索引偏移，负数=逆行）
ZIWEI_XI = [
    ("紫微", 0), ("天机", -1), ("太阳", -3), ("武曲", -4), ("天同", -5), ("廉贞", -8),
]
# 天府系：自天府起顺时针；天府与紫微对称于寅申线（紫微寅申天府同宫）：
# 天府idx = (4 - 紫微idx) mod 12（K2 对齐权威 iztro/ZSZ/见微，原 (6 - idx) 为错式）
TIANFU_XI = [
    ("天府", 0), ("太阴", 1), ("贪狼", 2), ("巨门", 3),
    ("天相", 4), ("天梁", 5), ("七杀", 6), ("破军", 10),
]


def nayin_of(ganzhi: str) -> str:
    """六十甲子纳音查表，如 "丙寅" → "炉中火"。"""
    if ganzhi not in NAYIN:
        raise ValueError(f"非法干支: {ganzhi}")
    return NAYIN[ganzhi]


def wuxing_ju_by_nayin(ganzhi: str) -> str:
    """干支纳音定五行局（核心查表），如 "乙酉" → "水二局"。"""
    return JU_NAMES[NAYIN_JU[nayin_of(ganzhi)]]


def ming_gong_of(lunar_month: int, time_zhi: str) -> str:
    """命宫地支：寅上起正月顺数至生月，再从月宫起子时逆至生时。"""
    if not 1 <= lunar_month <= 12:
        raise ValueError(f"非法农历月: {lunar_month}")
    if time_zhi not in SHICHEN_INDEX:
        raise ValueError(f"非法时辰: {time_zhi}")
    month_pos = (DIZHI.index("寅") + (lunar_month - 1)) % 12
    return DIZHI[(month_pos - SHICHEN_INDEX[time_zhi]) % 12]


def shen_gong_of(lunar_month: int, time_zhi: str) -> str:
    """身宫地支：起法同命宫，但自月宫起子时顺至生时。"""
    if not 1 <= lunar_month <= 12:
        raise ValueError(f"非法农历月: {lunar_month}")
    if time_zhi not in SHICHEN_INDEX:
        raise ValueError(f"非法时辰: {time_zhi}")
    month_pos = (DIZHI.index("寅") + (lunar_month - 1)) % 12
    return DIZHI[(month_pos + SHICHEN_INDEX[time_zhi]) % 12]


def ming_gong_gan(year_gan: str, ming_gong_dizhi: str) -> str:
    """命宫天干（五虎遁）：自寅月干顺数至命宫。"""
    if year_gan not in WUHU_DUN:
        raise ValueError(f"非法年干: {year_gan}")
    yin_gan = WUHU_DUN[year_gan]
    offset = (DIZHI.index(ming_gong_dizhi) - DIZHI.index("寅")) % 12
    return TIANGAN[(TIANGAN.index(yin_gan) + offset) % 10]


def wuxing_ju_of(year_gan: str, lunar_month: int, time_zhi: str) -> str:
    """五行局查表：命宫干支纳音定局（紫微斗数全书·安星诀，与 ZiweiEngine 口径一致）。

    步骤：命宫地支（寅起正月顺数、逆至生时）→ 命宫天干（五虎遁）→ 干支纳音 → 局。
    如 甲子年正月午时：命宫申（壬申剑锋金）→ 金四局。
    """
    ming = ming_gong_of(lunar_month, time_zhi)
    gan = ming_gong_gan(year_gan, ming)
    return wuxing_ju_by_nayin(gan + ming)


def sihua_of(gan: str) -> dict[str, str]:
    """生年四化查表：{"禄"/"权"/"科"/"忌": 星曜}，如 "甲" → {"禄": "廉贞", ...}。"""
    return dict(SIHUA.get(gan, {}))


def palace_order(ming_gong: str) -> dict[str, str]:
    """十二宫定序：命宫起逆时针（复现引擎口径；阴阳只影响大限，不影响宫序）。"""
    if ming_gong not in SHICHEN_INDEX:
        raise ValueError(f"非法命宫地支: {ming_gong}")
    start = SHICHEN_INDEX[ming_gong]
    return {name: DIZHI[(start - i) % 12] for i, name in enumerate(PALACE_ORDER)}


def main_stars_of(ziwei_dizhi: str) -> dict[str, str]:
    """14 主星分布：紫微系逆时针 + 天府系顺时针（天府=紫微对寅申线对称）。

    如 紫微在寅 → 天机丑/太阳亥/武曲戌/天同酉/廉贞午（紫微与天府同宫于寅）；
                  天府寅/太阴卯/贪狼辰/巨门巳/天相午/天梁未/七杀申/破军子。
    （K2 对齐权威：紫微寅→天府寅、卯→丑、辰→子…；原"天府辰/破军寅"为错式）
    """
    if ziwei_dizhi not in SHICHEN_INDEX:
        raise ValueError(f"非法紫微地支: {ziwei_dizhi}")
    ziwei_idx = SHICHEN_INDEX[ziwei_dizhi]
    stars = {star: DIZHI[(ziwei_idx + offset) % 12] for star, offset in ZIWEI_XI}
    tianfu_idx = (4 - ziwei_idx) % 12
    stars.update({star: DIZHI[(tianfu_idx + offset) % 12] for star, offset in TIANFU_XI})
    return stars


def analyze(result) -> list[str]:
    """确定性要点（纯查表事实，不做解释性断语）：五行局/命身宫/四化/主星/十二宫。"""
    s = []
    s.append(f"五行局：{result.wuxing_ju}")
    s.append(f"命宫：{result.ming_gong}，身宫：{result.shen_gong}")
    lu = result.sihua.get("化禄", "")
    quan = result.sihua.get("化权", "")
    ke = result.sihua.get("化科", "")
    ji = result.sihua.get("化忌", "")
    s.append(f"生年四化：{lu}化禄、{quan}化权、{ke}化科、{ji}化忌")
    zw = result.main_stars.get("紫微", "")
    xi_stars = "、".join(f"{k}{result.main_stars[k]}" for k in ("天机", "太阳", "武曲", "天同", "廉贞"))
    s.append(f"紫微系分布：紫微{zw}、{xi_stars}")
    fu_stars = "、".join(
        f"{k}{result.main_stars[k]}"
        for k in ("天府", "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", "破军"))
    s.append(f"天府系分布：{fu_stars}")
    order = palace_order(result.ming_gong)
    s.append("十二宫定序：" + "、".join(f"{name}{order[name]}" for name in PALACE_ORDER))
    return s


def evaluate(result) -> dict:
    """跑分断言用字典：五行局/命宫/身宫/四化/14主星/十二宫，键均为确定性字段。"""
    out = {
        "五行局": result.wuxing_ju,
        "命宫": result.ming_gong,
        "身宫": result.shen_gong,
        "四化禄": result.sihua.get("化禄", ""),
        "四化权": result.sihua.get("化权", ""),
        "四化科": result.sihua.get("化科", ""),
        "四化忌": result.sihua.get("化忌", ""),
    }
    out.update({star: pos for star, pos in result.main_stars.items()})
    out.update({name: info.dizhi for name, info in result.palaces.items()})
    return out
