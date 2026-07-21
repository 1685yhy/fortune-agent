"""紫微斗数排盘引擎 - 纯Python实现，无外部JS依赖."""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from lunar_python import Solar

# ============================================================
# 基础数据表
# ============================================================

TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# 六十甲子纳音 → 五行局 mapping
# 五行局: 水二局(2), 木三局(3), 金四局(4), 土五局(5), 火六局(6)
NAYIN_WUXING_JU = {
    "海中金": 4, "剑锋金": 4, "白蜡金": 4, "沙中金": 4, "金箔金": 4, "钗钏金": 4,
    "大林木": 3, "杨柳木": 3, "松柏木": 3, "平地木": 3, "桑柘木": 3, "石榴木": 3,
    "涧下水": 2, "泉中水": 2, "长流水": 2, "天河水": 2, "大溪水": 2, "大海水": 2,
    "炉中火": 6, "山头火": 6, "霹雳火": 6, "山下火": 6, "覆灯火": 6, "天上火": 6,
    "路旁土": 5, "城头土": 5, "屋上土": 5, "壁上土": 5, "大驿土": 5, "沙中土": 5,
}

# 六十甲子纳音表（简化key为天干地支组合）
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

# 六十甲子索引（用于快速查干支组合）
SHENG_XU = ["甲子", "乙丑", "丙寅", "丁卯", "戊辰", "己巳", "庚午", "辛未", "壬申", "癸酉",
            "甲戌", "乙亥", "丙子", "丁丑", "戊寅", "己卯", "庚辰", "辛巳", "壬午", "癸未",
            "甲申", "乙酉", "丙戌", "丁亥", "戊子", "己丑", "庚寅", "辛卯", "壬辰", "癸巳",
            "甲午", "乙未", "丙申", "丁酉", "戊戌", "己亥", "庚子", "辛丑", "壬寅", "癸卯",
            "甲辰", "乙巳", "丙午", "丁未", "戊申", "己酉", "庚戌", "辛亥", "壬子", "癸丑",
            "甲寅", "乙卯", "丙辰", "丁巳", "戊午", "己未", "庚申", "辛酉", "壬戌", "癸亥"]
SHENG_XU_MAP = {v: i for i, v in enumerate(SHENG_XU)}

# 紫微星位置表: 五行局数(2-6) → [日(1-30)] → DIZHI名称
# 来自 紫微斗数 经典排盘表
ZIWEI_POS_TABLE = {
    2: {  # 水二局
         1: "丑",  2: "寅",  3: "寅",  4: "卯",  5: "卯",
         6: "辰",  7: "辰",  8: "巳",  9: "巳", 10: "午",
        11: "午", 12: "未", 13: "未", 14: "申", 15: "申",
        16: "酉", 17: "酉", 18: "戌", 19: "戌", 20: "亥",
        21: "亥", 22: "子", 23: "子", 24: "丑", 25: "丑",
        26: "寅", 27: "寅", 28: "卯", 29: "卯", 30: "辰",
    },
    3: {  # 木三局
         1: "辰",  2: "丑",  3: "寅",  4: "巳",  5: "寅",
         6: "卯",  7: "午",  8: "卯",  9: "辰", 10: "未",
        11: "辰", 12: "巳", 13: "申", 14: "巳", 15: "午",
        16: "酉", 17: "午", 18: "未", 19: "戌", 20: "未",
        21: "申", 22: "亥", 23: "申", 24: "酉", 25: "子",
        26: "酉", 27: "戌", 28: "丑", 29: "戌", 30: "亥",
    },
    4: {  # 金四局
         1: "亥",  2: "辰",  3: "丑",  4: "寅",  5: "子",
         6: "巳",  7: "寅",  8: "卯",  9: "丑", 10: "午",
        11: "卯", 12: "辰", 13: "寅", 14: "未", 15: "辰",
        16: "巳", 17: "卯", 18: "申", 19: "巳", 20: "午",
        21: "辰", 22: "酉", 23: "午", 24: "未", 25: "巳",
        26: "戌", 27: "未", 28: "申", 29: "午", 30: "亥",
    },
    5: {  # 土五局
         1: "午",  2: "亥",  3: "辰",  4: "丑",  5: "寅",
         6: "未",  7: "子",  8: "巳",  9: "寅", 10: "卯",
        11: "申", 12: "丑", 13: "午", 14: "卯", 15: "辰",
        16: "酉", 17: "寅", 18: "未", 19: "辰", 20: "巳",
        21: "戌", 22: "卯", 23: "申", 24: "巳", 25: "午",
        26: "亥", 27: "辰", 28: "酉", 29: "午", 30: "未",
    },
    6: {  # 火六局
         1: "酉",  2: "午",  3: "亥",  4: "辰",  5: "丑",
         6: "寅",  7: "戌",  8: "未",  9: "子", 10: "巳",
        11: "寅", 12: "卯", 13: "亥", 14: "申", 15: "丑",
        16: "午", 17: "卯", 18: "辰", 19: "子", 20: "酉",
        21: "寅", 22: "未", 23: "辰", 24: "巳", 25: "丑",
        26: "戌", 27: "卯", 28: "申", 29: "巳", 30: "午",
    },
}

# 紫微系 14主星 offset (逆行/逆时针)
# 格式: (星名, offset_from_ziwei)
ZIWEI_XI_STARS = [
    ("紫微", 0),
    ("天机", -1),
    # skip 1
    ("太阳", -3),
    ("武曲", -4),
    ("天同", -5),
    # skip 2
    ("廉贞", -8),
]

# 天府系 14主星 offset (顺行/顺时针)
TIANFU_XI_STARS = [
    ("天府", 0),
    ("太阴", 1),
    ("贪狼", 2),
    ("巨门", 3),
    ("天相", 4),
    ("天梁", 5),
    ("七杀", 6),
    # skip 3
    ("破军", 10),
]

# 四化表: 年干 → (化禄, 化权, 化科, 化忌)
SIHUA_TABLE = {
    "甲": ("廉贞", "破军", "武曲", "太阳"),
    "乙": ("天机", "天梁", "紫微", "太阴"),
    "丙": ("天同", "天机", "文昌", "廉贞"),
    "丁": ("太阴", "天同", "天机", "巨门"),
    "戊": ("贪狼", "太阴", "右弼", "天机"),
    "己": ("武曲", "贪狼", "天梁", "文曲"),
    "庚": ("太阳", "武曲", "太阴", "天同"),
    "辛": ("巨门", "太阳", "文曲", "文昌"),
    "壬": ("天梁", "紫微", "左辅", "武曲"),
    "癸": ("破军", "巨门", "太阴", "贪狼"),
}

# 五虎遁: 年干 → 寅月天干
WUHU_DUN = {
    "甲": "丙", "己": "丙",
    "乙": "戊", "庚": "戊",
    "丙": "庚", "辛": "庚",
    "丁": "壬", "壬": "壬",
    "戊": "甲", "癸": "甲",
}

# 天魁天钺: 年干 → (天魁, 天钺)
TIANKUI_TIANYUE = {
    "甲": ("丑", "未"), "己": ("子", "申"),
    "乙": ("子", "申"), "庚": ("丑", "未"),
    "丙": ("亥", "酉"), "辛": ("寅", "午"),
    "丁": ("亥", "酉"), "壬": ("卯", "巳"),
    "戊": ("丑", "未"), "癸": ("卯", "巳"),
}

# 禄存: 年干 → 地支
LUCUN = {
    "甲": "寅", "乙": "卯", "丙": "巳", "丁": "午",
    "戊": "巳", "己": "午", "庚": "申", "辛": "酉",
    "壬": "亥", "癸": "子",
}

# 天马: 年支 → 地支
TIANMA = {
    "寅": "申", "午": "申", "戌": "申",
    "申": "寅", "子": "寅", "辰": "寅",
    "巳": "亥", "酉": "亥", "丑": "亥",
    "亥": "巳", "卯": "巳", "未": "巳",
}

# 左辅右弼: 月 → (左辅, 右弼)
ZUOYOU_TABLE = {}
for m in range(1, 13):
    # 左辅: 辰宫起正月, 顺行 → 辰+(月-1)
    zuo_idx = (DIZHI.index("辰") + m - 1) % 12
    # 右弼: 戌宫起正月, 逆行 → 戌-(月-1)
    you_idx = (DIZHI.index("戌") - (m - 1)) % 12
    ZUOYOU_TABLE[m] = (DIZHI[zuo_idx], DIZHI[you_idx])

# 文昌文曲: 时辰 → (文昌, 文曲)
# 文昌: 戌宫起子时, 顺行
# 文曲: 辰宫起子时, 逆行
WENCHANG_WENQU_TABLE = {}
for shi_idx, shi_name in enumerate(DIZHI):
    # 文昌: 戌(10) + shi_idx 顺行
    wc_idx = (DIZHI.index("戌") + shi_idx) % 12
    # 文曲: 辰(4) - shi_idx 逆行
    wq_idx = (DIZHI.index("辰") - shi_idx) % 12
    WENCHANG_WENQU_TABLE[shi_name] = (DIZHI[wc_idx], DIZHI[wq_idx])

# 火星铃星: 年支 → (火星起始位置, 铃星起始位置)
# 火星: 从起始位置起子时, 顺行到生时
# 铃星: 从起始位置起子时, 顺行到生时
HUO_LING_START = {
    "寅": (DIZHI.index("子"), DIZHI.index("丑")),
    "午": (DIZHI.index("子"), DIZHI.index("丑")),
    "戌": (DIZHI.index("子"), DIZHI.index("丑")),
    "申": (DIZHI.index("寅"), DIZHI.index("寅")),
    "子": (DIZHI.index("寅"), DIZHI.index("寅")),
    "辰": (DIZHI.index("寅"), DIZHI.index("寅")),
    "巳": (DIZHI.index("卯"), DIZHI.index("午")),
    "酉": (DIZHI.index("卯"), DIZHI.index("午")),
    "丑": (DIZHI.index("卯"), DIZHI.index("午")),
    "亥": (DIZHI.index("酉"), DIZHI.index("子")),
    "卯": (DIZHI.index("酉"), DIZHI.index("子")),
    "未": (DIZHI.index("酉"), DIZHI.index("子")),
}

# 地空地劫: 时辰 → (地空, 地劫)
# 地空: 亥宫起子时, 顺行
# 地劫: 戌宫起子时, 顺行
DIKONG_DIJIE_TABLE = {}
for shi_idx, shi_name in enumerate(DIZHI):
    # 地空: 亥(11) + shi_idx 顺行
    dk_idx = (DIZHI.index("亥") + shi_idx) % 12
    # 地劫: 戌(10) + shi_idx 顺行
    dj_idx = (DIZHI.index("戌") + shi_idx) % 12
    DIKONG_DIJIE_TABLE[shi_name] = (DIZHI[dk_idx], DIZHI[dj_idx])

# 12宫顺序（从命宫起逆时针）
PALACE_NAMES = [
    "命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
    "迁移", "交友", "官禄", "田宅", "福德", "父母",
]

# 时辰地支索引 (子=0, 丑=1, ...)
SHICHEN_MAP = {z: i for i, z in enumerate(DIZHI)}

# 五行局名称
WUXING_JU_NAMES = {2: "水二局", 3: "木三局", 4: "金四局", 5: "土五局", 6: "火六局"}


# ============================================================
# 数据模型
# ============================================================

@dataclass
class PalaceInfo:
    """一个宫位的信息"""
    dizhi: str                # 地支, e.g. "寅"
    stars: List[str] = field(default_factory=list)  # 主星列表
    aux_stars: List[str] = field(default_factory=list)  # 辅星列表


@dataclass
class ZiweiResult:
    ming_gong: str                    # 命宫地支, e.g. "寅"
    shen_gong: str                    # 身宫地支
    wuxing_ju: str                    # 五行局, e.g. "木三局"
    palaces: Dict[str, PalaceInfo]    # 12宫: {"命宫": PalaceInfo, ...}
    sihua: Dict[str, str]             # 四化: {"化禄": "天机", ...}
    main_stars: Dict[str, str]        # 14主星分布: {"紫微": "寅", "天机": "丑", ...}
    aux_stars: Dict[str, str]         # 辅星分布
    dayun: List[Tuple[int, str, str]]  # 大限: [(start_age, ganzhi, palace_name), ...]
    raw_data: dict = field(default_factory=dict)


# ============================================================
# 紫微斗数引擎
# ============================================================

class ZiweiEngine:
    """紫微斗数排盘引擎"""

    def calculate(self, year: int, month: int, day: int,
                  hour: int, minute: int, city: str,
                  gender: str) -> ZiweiResult:
        """排紫微斗数命盘

        Args:
            year: 公历年份
            month: 公历月份
            day: 公历日期
            hour: 小时(0-23)
            minute: 分钟(0-59)
            city: 城市名称
            gender: 性别("男"或"女")

        Returns:
            ZiweiResult 包含完整紫微斗数命盘
        """
        # 1. 公历→农历转换
        solar = Solar.fromYmdHms(year, month, day, hour, minute, 0)
        lunar = solar.getLunar()

        lunar_year = lunar.getYear()
        lunar_month = lunar.getMonth()
        lunar_day = lunar.getDay()

        # 时辰
        time_zhi = lunar.getTimeZhi()
        shichen_idx = SHICHEN_MAP[time_zhi]

        # 年干支
        year_ganzhi = lunar.getYearInGanZhi()
        year_gan = year_ganzhi[0]
        year_zhi = year_ganzhi[1]

        # 2. 命宫计算
        ming_gong_idx = self._calc_ming_gong(lunar_month, shichen_idx)
        ming_gong_dizhi = DIZHI[ming_gong_idx]

        # 3. 身宫计算
        shen_gong_idx = self._calc_shen_gong(lunar_month, shichen_idx)
        shen_gong_dizhi = DIZHI[shen_gong_idx]

        # 4. 命宫天干 (五虎遁)
        ming_gong_tiangen = self._calc_ming_gong_gan(year_gan, ming_gong_idx)

        # 5. 五行局
        ming_gong_ganzhi = ming_gong_tiangen + ming_gong_dizhi
        wuxing_ju = self._calc_wuxing_ju(ming_gong_ganzhi)
        wuxing_ju_name = WUXING_JU_NAMES.get(wuxing_ju, "未知")

        # 6. 紫微星位置
        ziwei_dizhi = ZIWEI_POS_TABLE[wuxing_ju][lunar_day]
        ziwei_idx = DIZHI.index(ziwei_dizhi)

        # 7. 14主星位置
        main_stars = self._calc_main_stars(ziwei_idx)

        # 8. 四化
        sihua = self._calc_sihua(year_gan, main_stars)

        # 9. 辅星
        aux_stars = self._calc_aux_stars(year_gan, year_zhi, lunar_month, time_zhi, shichen_idx)

        # 10. 12宫排布
        palaces = self._arrange_palaces(ming_gong_idx, main_stars, aux_stars)

        # 11. 大限
        dayun = self._calc_dayun(wuxing_ju, ming_gong_idx, gender, year_gan)

        # 构建完整的星分布字典（包含主星和辅星）
        all_stars = {**main_stars, **aux_stars}

        return ZiweiResult(
            ming_gong=ming_gong_dizhi,
            shen_gong=shen_gong_dizhi,
            wuxing_ju=wuxing_ju_name,
            palaces=palaces,
            sihua=sihua,
            main_stars=main_stars,
            aux_stars=aux_stars,
            dayun=dayun,
            raw_data={
                "lunar_year": lunar_year,
                "lunar_month": lunar_month,
                "lunar_day": lunar_day,
                "time_zhi": time_zhi,
                "year_gan": year_gan,
                "year_zhi": year_zhi,
                "ming_gong_ganzhi": ming_gong_ganzhi,
                "ziwei_ju": wuxing_ju,
            },
        )

    def _calc_ming_gong(self, month: int, shichen_idx: int) -> int:
        """计算命宫地支索引

        口诀: 从寅上起正月，顺数至本生月；又从月宫起子时，逆至本生时安命。

        Args:
            month: 农历月份 (1-12)
            shichen_idx: 时辰索引 (子=0, 丑=1, ...)

        Returns:
            DIZHI索引 (0=子, 1=丑, ...)
        """
        # 寅为起始(索引2)，顺数至生月
        month_pos = (DIZHI.index("寅") + (month - 1)) % 12
        # 从月宫逆数至生时
        ming_gong = (month_pos - shichen_idx) % 12
        return ming_gong

    def _calc_shen_gong(self, month: int, shichen_idx: int) -> int:
        """计算身宫地支索引

        口诀: 从寅上起正月，顺数至本生月；又从月宫起子时，顺至本生时安身。

        Args:
            month: 农历月份 (1-12)
            shichen_idx: 时辰索引

        Returns:
            DIZHI索引
        """
        month_pos = (DIZHI.index("寅") + (month - 1)) % 12
        shen_gong = (month_pos + shichen_idx) % 12
        return shen_gong

    def _calc_ming_gong_gan(self, year_gan: str, ming_gong_idx: int) -> str:
        """计算命宫天干 (五虎遁)

        Args:
            year_gan: 年天干
            ming_gong_idx: 命宫地支索引

        Returns:
            命宫天干
        """
        # 寅月的天干
        yin_gan = WUHU_DUN[year_gan]
        yin_gan_idx = TIANGAN.index(yin_gan)

        # 寅的索引
        yin_idx = DIZHI.index("寅")

        # 从寅到命宫的偏移
        offset = (ming_gong_idx - yin_idx) % 12

        # 命宫天干
        ming_gan_idx = (yin_gan_idx + offset) % 10
        return TIANGAN[ming_gan_idx]

    def _calc_wuxing_ju(self, ming_gong_ganzhi: str) -> int:
        """计算五行局

        Args:
            ming_gong_ganzhi: 命宫干支, e.g. "丙戌"

        Returns:
            五行局数: 2(水), 3(木), 4(金), 5(土), 6(火)
        """
        nayin = NAYIN.get(ming_gong_ganzhi, "")
        if not nayin:
            # Fallback: 用六十甲子推算
            if ming_gong_ganzhi in SHENG_XU_MAP:
                idx = SHENG_XU_MAP[ming_gong_ganzhi]
                # 用干支索引找同位置的纳音
                # 每两个连续干支共享一个纳音
                pair_idx = idx // 2
                pair_ganzhi = SHENG_XU[pair_idx * 2]
                nayin = NAYIN.get(pair_ganzhi, "")
        return NAYIN_WUXING_JU.get(nayin, 2)

    def _calc_main_stars(self, ziwei_idx: int) -> Dict[str, str]:
        """计算14主星位置

        Args:
            ziwei_idx: 紫微星的地支索引

        Returns:
            {星名: 地支, ...}
        """
        stars = {}

        # 紫微系 (逆行/逆时针 = -1 in DIZHI index)
        for star_name, offset in ZIWEI_XI_STARS:
            pos_idx = (ziwei_idx + offset) % 12
            stars[star_name] = DIZHI[pos_idx]

        # 天府位置: 与紫微对称于寅申线
        # 标准安天府诀: 紫微在寅,天府在辰 → 紫微+天府 ≡ 6 (mod 12)
        # 天府DIZHI_idx = (6 - 紫微DIZHI_idx) % 12
        tianfu_idx = (6 - ziwei_idx) % 12

        # 天府系 (顺行/顺时针 = +1 in DIZHI index)
        for star_name, offset in TIANFU_XI_STARS:
            pos_idx = (tianfu_idx + offset) % 12
            stars[star_name] = DIZHI[pos_idx]

        return stars

    def _calc_sihua(self, year_gan: str, main_stars: Dict[str, str]) -> Dict[str, str]:
        """计算四化

        Args:
            year_gan: 年天干
            main_stars: 所有主星分布

        Returns:
            {"化禄": 星名, "化权": 星名, "化科": 星名, "化忌": 星名}
        """
        if year_gan not in SIHUA_TABLE:
            return {"化禄": "", "化权": "", "化科": "", "化忌": ""}

        lu, quan, ke, ji = SIHUA_TABLE[year_gan]
        return {"化禄": lu, "化权": quan, "化科": ke, "化忌": ji}

    def _calc_aux_stars(self, year_gan: str, year_zhi: str,
                        month: int, time_zhi: str,
                        shichen_idx: int) -> Dict[str, str]:
        """计算辅星位置

        Args:
            year_gan: 年天干
            year_zhi: 年地支
            month: 农历月份(1-12)
            time_zhi: 时辰地支
            shichen_idx: 时辰索引

        Returns:
            {辅星名: 地支, ...}
        """
        aux = {}

        # 左辅右弼 (按月)
        zuo, you = ZUOYOU_TABLE.get(month, ("辰", "戌"))
        aux["左辅"] = zuo
        aux["右弼"] = you

        # 文昌文曲 (按时辰)
        wc, wq = WENCHANG_WENQU_TABLE.get(time_zhi, ("戌", "辰"))
        aux["文昌"] = wc
        aux["文曲"] = wq

        # 天魁天钺 (按年干)
        if year_gan in TIANKUI_TIANYUE:
            kui, yue = TIANKUI_TIANYUE[year_gan]
            aux["天魁"] = kui
            aux["天钺"] = yue

        # 禄存 (按年干)
        if year_gan in LUCUN:
            aux["禄存"] = LUCUN[year_gan]

        # 擎羊陀罗 (禄存前/后一宫)
        if "禄存" in aux:
            lucun_idx = DIZHI.index(aux["禄存"])
            # 擎羊: 禄存前一宫(顺行)
            qingyang_idx = (lucun_idx + 1) % 12
            # 陀罗: 禄存后一宫(逆行)
            tuoluo_idx = (lucun_idx - 1) % 12
            aux["擎羊"] = DIZHI[qingyang_idx]
            aux["陀罗"] = DIZHI[tuoluo_idx]

        # 天马 (按年支)
        if year_zhi in TIANMA:
            aux["天马"] = TIANMA[year_zhi]

        # 火星铃星 (按年支和时辰)
        if year_zhi in HUO_LING_START:
            huo_start, ling_start = HUO_LING_START[year_zhi]
            # 从起始位置起子时，顺行到生时
            huo_idx = (huo_start + shichen_idx) % 12
            ling_idx = (ling_start + shichen_idx) % 12
            aux["火星"] = DIZHI[huo_idx]
            aux["铃星"] = DIZHI[ling_idx]

        # 地空地劫 (按时辰)
        dk, dj = DIKONG_DIJIE_TABLE.get(time_zhi, ("亥", "戌"))
        aux["地空"] = dk
        aux["地劫"] = dj

        return aux

    def _arrange_palaces(self, ming_gong_idx: int,
                         main_stars: Dict[str, str],
                         aux_stars: Dict[str, str]) -> Dict[str, PalaceInfo]:
        """将星曜分配到12宫位

        12宫按逆时针方向从命宫开始排列。

        Args:
            ming_gong_idx: 命宫地支索引
            main_stars: 主星分布
            aux_stars: 辅星分布

        Returns:
            {宫名: PalaceInfo, ...}
        """
        palaces = {}

        # 建立地支到宫名的映射
        for i, palace_name in enumerate(PALACE_NAMES):
            # 逆时针: 从命宫开始递减索引
            dizhi_idx = (ming_gong_idx - i) % 12
            dizhi = DIZHI[dizhi_idx]
            palaces[palace_name] = PalaceInfo(dizhi=dizhi)

        # 分配主星
        for star_name, dizhi in main_stars.items():
            for palace_name, info in palaces.items():
                if info.dizhi == dizhi:
                    info.stars.append(star_name)
                    break

        # 分配辅星
        for star_name, dizhi in aux_stars.items():
            for palace_name, info in palaces.items():
                if info.dizhi == dizhi:
                    info.aux_stars.append(star_name)
                    break

        return palaces

    def _calc_dayun(self, wuxing_ju: int, ming_gong_idx: int,
                    gender: str, year_gan: str) -> List[Tuple[int, str, str]]:
        """计算大限

        大限从命宫开始，阳男阴女顺行，阴男阳女逆行。
        每宫管10年，起运年龄取决于五行局数。

        Args:
            wuxing_ju: 五行局数
            ming_gong_idx: 命宫地支索引
            gender: 性别
            year_gan: 年天干

        Returns:
            [(起始年龄, 宫名, 地支), ...]
        """
        # 起运年龄 = 五行局数
        start_age = wuxing_ju

        # 阳男阴女顺行(clockwise/+1), 阴男阳女逆行(counter-clockwise/-1)
        year_gan_idx = TIANGAN.index(year_gan)
        is_yang = year_gan_idx % 2 == 0  # 甲丙戊庚壬为阳
        is_male = gender == "男"
        forward = (is_male and is_yang) or (not is_male and not is_yang)

        # Build dizhi -> palace_name mapping (逆时针排列)
        dizhi_to_palace = {}
        for i, p_name in enumerate(PALACE_NAMES):
            dz_idx = (ming_gong_idx - i) % 12
            dizhi_to_palace[DIZHI[dz_idx]] = p_name

        dayun = []
        for i in range(12):
            age = start_age + i * 10

            if forward:
                dz_idx = (ming_gong_idx + i) % 12
            else:
                dz_idx = (ming_gong_idx - i) % 12

            dz = DIZHI[dz_idx]
            p_name = dizhi_to_palace[dz]
            dayun.append((age, p_name, dz))

        return dayun
