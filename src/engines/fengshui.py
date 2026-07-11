"""风水引擎 - 玄空飞星 (Flying Stars) + 八宅派 (Eight Mansions)."""
from dataclasses import dataclass, field
from typing import Dict, Optional, List


# ============================================================
# 基础数据
# ============================================================

# 三元九运: (运, 起始年, 结束年)
SAN_YUAN_JIU_YUN = [
    (1, 1864, 1883), (2, 1884, 1903), (3, 1904, 1923),
    (4, 1924, 1943), (5, 1944, 1963), (6, 1964, 1983),
    (7, 1984, 2003), (8, 2004, 2023), (9, 2024, 2043),
]

# 二十四山 → 后天八卦
MOUNTAIN24_TRIGRAM = {
    "壬": "坎", "子": "坎", "癸": "坎",
    "丑": "艮", "艮": "艮", "寅": "艮",
    "甲": "震", "卯": "震", "乙": "震",
    "辰": "巽", "巽": "巽", "巳": "巽",
    "丙": "离", "午": "离", "丁": "离",
    "未": "坤", "坤": "坤", "申": "坤",
    "庚": "兑", "酉": "兑", "辛": "兑",
    "戌": "乾", "乾": "乾", "亥": "乾",
}

# 二十四山 阴阳（决定顺飞/逆飞）
MOUNTAIN24_YINYANG = {
    # 天元龙(父母): 乾坤艮巽为阳, 子午卯酉为阴
    "乾": "阳", "坤": "阳", "艮": "阳", "巽": "阳",
    "子": "阴", "午": "阴", "卯": "阴", "酉": "阴",
    # 人元龙(顺子): 寅申巳亥为阳, 乙辛丁癸为阴
    "寅": "阳", "申": "阳", "巳": "阳", "亥": "阳",
    "癸": "阴", "丁": "阴", "乙": "阴", "辛": "阴",
    # 地元龙(逆子): 甲庚壬丙为阳, 辰戌丑未为阴
    "甲": "阳", "庚": "阳", "壬": "阳", "丙": "阳",
    "辰": "阴", "戌": "阴", "丑": "阴", "未": "阴",
}

# 对宫（坐山→向山）
MOUNTAIN24_OPPOSITE = {
    "子": "午", "癸": "丁", "丑": "未", "艮": "坤", "寅": "申", "甲": "庚",
    "卯": "酉", "乙": "辛", "辰": "戌", "巽": "乾", "巳": "亥", "丙": "壬",
    "午": "子", "丁": "癸", "未": "丑", "坤": "艮", "申": "寅", "庚": "甲",
    "酉": "卯", "辛": "乙", "戌": "辰", "乾": "巽", "亥": "巳", "壬": "丙",
}

# 八卦对宫
OPPOSITE_TRIGRAM = {
    "坎": "离", "离": "坎", "震": "兑", "兑": "震",
    "巽": "乾", "乾": "巽", "艮": "坤", "坤": "艮",
}

# 洛书数 → 后天八卦
LUO_SHU_NUM = {"坎": 1, "坤": 2, "震": 3, "巽": 4, "中": 5, "乾": 6, "兑": 7, "艮": 8, "离": 9}
NUM_LUO_SHU = {1: "坎", 2: "坤", 3: "震", 4: "巽", 5: "中", 6: "乾", 7: "兑", 8: "艮", 9: "离"}

# 洛书飞星路径（顺飞）
SHUN_FEI_PATH = ["中", "乾", "兑", "艮", "离", "坎", "坤", "震", "巽"]
# 洛书飞星路径（逆飞）
NI_FEI_PATH = ["中", "巽", "震", "坤", "坎", "离", "艮", "兑", "乾"]

# 九宫八卦显示顺序（3×3 从左到右、从上到下）
PALACE_ORDER = ["巽", "离", "坤", "震", "中", "兑", "艮", "坎", "乾"]

# 八方向 → 后天八卦
DIRECTION_TRIGRAM = {
    "北": "坎", "东北": "艮", "东": "震", "东南": "巽",
    "南": "离", "西南": "坤", "西": "兑", "西北": "乾",
}
TRIGRAM_DIRECTION = {v: k for k, v in DIRECTION_TRIGRAM.items()}

# 八方向顺时针顺序（从北开始）
DIRECTIONS_CW = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]

# 数→卦（用于命卦）
GUA_NUM = {"坎": 1, "坤": 2, "震": 3, "巽": 4, "乾": 6, "兑": 7, "艮": 8, "离": 9}
NUM_GUA = {1: "坎", 2: "坤", 3: "震", 4: "巽", 6: "乾", 7: "兑", 8: "艮", 9: "离"}

# ============================================================
# 八宅派数据
# ============================================================

# 八宅大游年歌诀
# 从本宅卦对应方向顺时针排8个方向的吉凶类型
EIGHT_MANSION_MAP = {
    "乾": ["伏位", "六煞", "天医", "五鬼", "祸害", "绝命", "延年", "生气"],
    "坎": ["伏位", "五鬼", "天医", "生气", "延年", "绝命", "祸害", "六煞"],
    "艮": ["伏位", "六煞", "绝命", "祸害", "生气", "延年", "天医", "五鬼"],
    "震": ["伏位", "延年", "生气", "祸害", "绝命", "五鬼", "天医", "六煞"],
    "巽": ["伏位", "天医", "五鬼", "六煞", "祸害", "生气", "绝命", "延年"],
    "离": ["伏位", "六煞", "五鬼", "绝命", "延年", "祸害", "生气", "天医"],
    "坤": ["伏位", "天医", "延年", "绝命", "生气", "祸害", "五鬼", "六煞"],
    "兑": ["伏位", "生气", "祸害", "延年", "绝命", "六煞", "五鬼", "天医"],
}

# 四吉星 / 四凶星
FOUR_AUSPICIOUS = {"生气", "天医", "延年", "伏位"}
FOUR_INAUSPICIOUS = {"绝命", "五鬼", "六煞", "祸害"}


# ============================================================
# 结果数据类
# ============================================================

@dataclass
class FengshuiResult:
    """风水分析结果"""
    flying_stars: dict       # 九宫飞星表: {"坎":{"运星":9,"山星":5,"向星":7},...}
    house_gua: str           # 宅卦, e.g. "坎宅"
    period: int              # 当前运 1-9
    eight_mansions: dict     # 八宅吉凶: {"生气":"东","天医":"东南",...}
    person_gua: str          # 命卦, e.g. "离"
    raw_data: dict = field(default_factory=dict)


# ============================================================
# 风水引擎
# ============================================================

class FengshuiEngine:
    """风水引擎 - 玄空飞星 + 八宅派"""

    def analyze(
        self,
        direction: str,
        year_built: int = None,
        birth_year: int = None,
        gender: str = None,
    ) -> FengshuiResult:
        """风水分析

        Args:
            direction: 坐山方向 (24山名称, 如 "子"/"午"/"乾" 或卦名 "坎"/"离")
            year_built: 建造年份（用于定运）
            birth_year: 出生年份（用于八宅命卦）
            gender: "男"/"女" (用于命卦)
        """
        # 确定坐山
        sitting_mtn = self._resolve_mountain(direction)
        sitting_trigram = MOUNTAIN24_TRIGRAM[sitting_mtn]

        # 确定向山
        facing_mtn = MOUNTAIN24_OPPOSITE[sitting_mtn]
        facing_trigram = MOUNTAIN24_TRIGRAM[facing_mtn]

        # 定元运
        year = year_built if year_built else 2024
        period = self._get_period(year)

        # ---- 玄空飞星 ----
        # 运盘
        period_chart = self._build_period_chart(period)
        # 山盘
        mountain_chart = self._build_star_chart(sitting_mtn, period_chart)
        # 向盘
        facing_chart = self._build_star_chart(facing_mtn, period_chart)

        # 合成九宫飞星表
        flying_stars = {}
        for palace in PALACE_ORDER:
            flying_stars[palace] = {
                "运星": period_chart[palace],
                "山星": mountain_chart[palace],
                "向星": facing_chart[palace],
            }

        # ---- 八宅派 ----
        house_gua = f"{sitting_trigram}宅"
        person_gua = None
        if birth_year is not None and gender is not None:
            person_gua = self._calculate_person_gua(birth_year, gender)

        eight_mansions = self._calculate_eight_mansions(sitting_trigram)

        return FengshuiResult(
            flying_stars=flying_stars,
            house_gua=house_gua,
            period=period,
            eight_mansions=eight_mansions,
            person_gua=person_gua if person_gua else "",
            raw_data={
                "sitting_mountain": sitting_mtn,
                "facing_mountain": facing_mtn,
                "sitting_trigram": sitting_trigram,
                "facing_trigram": facing_trigram,
                "period_chart": period_chart,
                "mountain_chart": mountain_chart,
                "facing_chart": facing_chart,
            },
        )

    # ---- 方向解析 ----

    def _resolve_mountain(self, direction: str) -> str:
        """将用户输入的方向解析为24山名称"""
        d = direction.strip()
        # 直接是24山名
        if d in MOUNTAIN24_TRIGRAM:
            return d
        # 是卦名（如"坎"、"离"）
        trigram_to_mtn = {
            "坎": "子", "艮": "艮", "震": "卯", "巽": "巽",
            "离": "午", "坤": "坤", "兑": "酉", "乾": "乾",
        }
        if d in trigram_to_mtn:
            return trigram_to_mtn[d]
        # 是中文方向名（"北""南"等）
        dir_to_mtn = {
            "北": "子", "东北": "艮", "东": "卯", "东南": "巽",
            "南": "午", "西南": "坤", "西": "酉", "西北": "乾",
        }
        if d in dir_to_mtn:
            return dir_to_mtn[d]
        return "子"  # 默认子山午向

    # ---- 元运 ----

    def _get_period(self, year: int) -> int:
        """根据年份获取当前运数 (1-9)"""
        for period, start, end in SAN_YUAN_JIU_YUN:
            if start <= year <= end:
                return period
        # 1864年之前或2043年之后, 按60年一周期推算
        if year < 1864:
            diff = 1864 - year
            cycles = (diff // 180) + 1
            base_period = 1
            period = ((base_period - 1 - cycles * 9) % 9) + 1
            if year <= SAN_YUAN_JIU_YUN[period - 1][2]:
                return period
            return ((period - 2) % 9) + 1
        # 2043年后
        diff = year - 2043
        cycles = (diff // 180) + 1
        period = ((9 - 1 + cycles * 9) % 9) + 1
        if year >= SAN_YUAN_JIU_YUN[period - 1][1]:
            return period
        return (period % 9) + 1

    # ---- 玄空飞星 ----

    def _build_period_chart(self, period: int) -> Dict[str, int]:
        """构建运盘: 运星入中顺飞"""
        chart = {}
        for i, palace in enumerate(SHUN_FEI_PATH):
            num = ((period - 1 + i) % 9) + 1
            chart[palace] = num
        return chart

    def _build_star_chart(self, mountain: str, period_chart: Dict[str, int]) -> Dict[str, int]:
        """构建山星盘或向星盘

        Args:
            mountain: 24山名称（坐山或向山）
            period_chart: 运盘
        """
        trigram = MOUNTAIN24_TRIGRAM[mountain]
        # 该卦在运盘中的数字 → 入中星数
        center_num = period_chart[trigram]

        # 确定顺飞(阳)还是逆飞(阴)
        yinyang = MOUNTAIN24_YINYANG.get(mountain, "阳")

        if yinyang == "阳":
            path = SHUN_FEI_PATH
            direction = 1   # 顺飞: 递增
        else:
            path = NI_FEI_PATH
            direction = -1  # 逆飞: 递减

        chart = {}
        for i, palace in enumerate(path):
            num = ((center_num - 1 + direction * i) % 9) + 1
            chart[palace] = num
        return chart

    # ---- 命卦 ----

    def _calculate_person_gua(self, birth_year: int, gender: str) -> str:
        """根据出生年份和性别计算命卦"""
        # 计算年份各位之和
        digits = [int(d) for d in str(birth_year)]
        digit_sum = sum(digits)
        while digit_sum > 9:
            digit_sum = sum(int(d) for d in str(digit_sum))

        # 性别处理
        is_male = (gender == "男")

        if is_male:
            if birth_year >= 2000:
                num = 9 - digit_sum
            else:
                num = 11 - digit_sum
        else:
            if birth_year >= 2000:
                num = 6 + digit_sum
            else:
                num = 4 + digit_sum

        # 归化为 1-9
        num = num % 9
        if num == 0:
            num = 9

        # 5 的特殊处理: 男→坤(2), 女→艮(8)
        if num == 5:
            return "坤" if is_male else "艮"

        return NUM_GUA.get(num, "坎")

    # ---- 八宅 ----

    def _calculate_eight_mansions(self, house_trigram: str) -> Dict[str, str]:
        """计算八宅四吉方/四凶方

        Returns:
            {"生气":"东","天医":"东南","延年":"南","伏位":"北",
             "绝命":"西","五鬼":"东北","六煞":"西北","祸害":"西南"}
        """
        # 该宅对应的卦的吉凶序列
        mansion_seq = EIGHT_MANSION_MAP.get(house_trigram, EIGHT_MANSION_MAP["坎"])

        # 找到本宅卦在DIRECTIONS_CW中的起始索引
        house_dir = TRIGRAM_DIRECTION.get(house_trigram, "北")
        start_idx = DIRECTIONS_CW.index(house_dir)

        # 顺时针给8个方向分配吉凶
        result = {}
        for i, energy in enumerate(mansion_seq):
            dir_idx = (start_idx + i) % 8
            direction = DIRECTIONS_CW[dir_idx]
            result[energy] = direction

        return result
