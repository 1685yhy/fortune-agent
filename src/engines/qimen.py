"""奇门遁甲排盘引擎 - 时家转盘奇门 (Hour-based Qimen Dunjia)

Core algorithm:
  1. 排地盘 (Dipan): Based on 节气 (solar term) determine 阳遁/阴遁 + 局数 (1-9)
     Place 六仪三奇 (戊己庚辛壬癸丁丙乙) in 9 palaces
  2. 排天盘 (Tianpan): 值符 star leads rotation, all 9 stars follow
  3. 排八门 (Bamen): 值使 door leads, 7 other doors follow
  4. 排九星 (Jiuxing): 天蓬/天芮/天冲/天辅/天禽/天心/天柱/天任/天英
  5. 排八神 (Bashen): 值符/螣蛇/太阴/六合/白虎/玄武/九地/九天
  6. 排天盘奇仪: 天盘上的六仪三奇分布
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from lunar_python import Solar

# ============================================================
# 基本常量
# ============================================================

TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# 九宫名称 (按宫位数 1-9)
PALACE_NAMES: Dict[int, str] = {
    1: "坎", 2: "坤", 3: "震", 4: "巽", 5: "中", 6: "乾", 7: "兑", 8: "艮", 9: "离",
}
PALACE_NUMS: Dict[str, int] = {v: k for k, v in PALACE_NAMES.items()}

# 六仪三奇 (固定顺序)
YI_QI = ["戊", "己", "庚", "辛", "壬", "癸", "丁", "丙", "乙"]

LIU_YI = ["戊", "己", "庚", "辛", "壬", "癸"]
SAN_QI = ["丁", "丙", "乙"]

# 九星 (按宫位 1-9 排列)
JIU_XING = ["天蓬", "天芮", "天冲", "天辅", "天禽", "天心", "天柱", "天任", "天英"]
JIU_XING_ORIGIN: Dict[int, str] = {i + 1: JIU_XING[i] for i in range(9)}
JIU_XING_PALACE: Dict[str, int] = {v: k for k, v in JIU_XING_ORIGIN.items()}

# 八门 (按宫位 1-9, 中宫无门)
BA_MEN = ["休", "死", "伤", "杜", "开", "惊", "生", "景"]
BA_MEN_ORIGIN: Dict[int, str] = {
    1: "休", 2: "死", 3: "伤", 4: "杜",
    6: "开", 7: "惊", 8: "生", 9: "景",
}
BA_MEN_PALACE: Dict[str, int] = {v: k for k, v in BA_MEN_ORIGIN.items()}

# 八神 (固定顺序)
BA_SHEN = ["值符", "螣蛇", "太阴", "六合", "白虎", "玄武", "九地", "九天"]

# 阳遁节气列表 (冬至 → 芒种)
YANG_DUN_TERMS = [
    "冬至", "小寒", "大寒", "立春", "雨水", "惊蛰",
    "春分", "清明", "谷雨", "立夏", "小满", "芒种",
]

# 阴遁节气列表 (夏至 → 大雪)
YIN_DUN_TERMS = [
    "夏至", "小暑", "大暑", "立秋", "处暑", "白露",
    "秋分", "寒露", "霜降", "立冬", "小雪", "大雪",
]

# 节气 → (阴阳遁, (上元局, 中元局, 下元局))
JIE_QI_DUN = {
    # 阳遁 (冬至→芒种)
    "冬至": ("阳遁", (1, 7, 4)),
    "小寒": ("阳遁", (2, 8, 5)),
    "大寒": ("阳遁", (3, 9, 6)),
    "立春": ("阳遁", (8, 5, 2)),
    "雨水": ("阳遁", (9, 6, 3)),
    "惊蛰": ("阳遁", (1, 7, 4)),
    "春分": ("阳遁", (3, 9, 6)),
    "清明": ("阳遁", (4, 1, 7)),
    "谷雨": ("阳遁", (5, 2, 8)),
    "立夏": ("阳遁", (4, 1, 7)),
    "小满": ("阳遁", (5, 2, 8)),
    "芒种": ("阳遁", (6, 3, 9)),
    # 阴遁 (夏至→大雪)
    "夏至": ("阴遁", (9, 3, 6)),
    "小暑": ("阴遁", (8, 2, 5)),
    "大暑": ("阴遁", (7, 1, 4)),
    "立秋": ("阴遁", (2, 5, 8)),
    "处暑": ("阴遁", (1, 4, 7)),
    "白露": ("阴遁", (9, 3, 6)),
    "秋分": ("阴遁", (7, 1, 4)),
    "寒露": ("阴遁", (6, 9, 3)),
    "霜降": ("阴遁", (5, 8, 2)),
    "立冬": ("阴遁", (6, 9, 3)),
    "小雪": ("阴遁", (5, 8, 2)),
    "大雪": ("阴遁", (4, 7, 1)),
}

# 八门 阳遁路径 (顺时针, 跳过中宫5)
YANG_DOOR_PATH = [1, 2, 3, 4, 6, 7, 8, 9]

# 八门 阴遁路径 (逆时针, 跳过中宫5)
YIN_DOOR_PATH = [1, 9, 8, 7, 6, 4, 3, 2]

# 八神 阳遁顺行路径 (顺时针, 跳过中宫5)
YANG_SHEN_PATH = [1, 2, 3, 4, 6, 7, 8, 9]

# 八神 阴遁逆行路径 (逆时针, 跳过中宫5)
YIN_SHEN_PATH = [1, 9, 8, 7, 6, 4, 3, 2]


# ============================================================
# 工具函数
# ============================================================

def xunshou_to_yi(branch_index: int) -> int:
    """Map 旬首 branch index (0=子/2=寅/4=辰/6=午/8=申/10=戌) to 六仪 index (0-5).

    子(0)→戊(0), 戌(10)→己(1), 申(8)→庚(2),
    午(6)→辛(3), 辰(4)→壬(4), 寅(2)→癸(5)
    """
    return (12 - branch_index) // 2 % 6


# ============================================================
# Data model
# ============================================================

@dataclass
class QimenResult:
    """奇门遁甲排盘结果

    Attributes:
        dun_type: "阳遁" or "阴遁"
        ju_number: 局数 1-9
        dipan: 地盘六仪三奇, {"坎": "戊", "坤": "己", ...}
        tianpan: 天盘奇仪, {"坎": "丁", ...}
        bamen: 八门分布, {"坎": "休", ...}
        jiuxing: 九星分布, {"坎": "天蓬", ...}
        bashen: 八神分布, {"坎": "值符", ...}
        zhifu_star: 值符星, "天蓬"
        zhishi_door: 值使门, "休"
    """
    dun_type: str = ""
    ju_number: int = 0
    dipan: Dict[str, str] = field(default_factory=dict)
    tianpan: Dict[str, str] = field(default_factory=dict)
    bamen: Dict[str, str] = field(default_factory=dict)
    jiuxing: Dict[str, str] = field(default_factory=dict)
    bashen: Dict[str, str] = field(default_factory=dict)
    zhifu_star: str = ""
    zhishi_door: str = ""
    raw_data: dict = field(default_factory=dict)


# ============================================================
# Qimen Engine
# ============================================================

class QimenEngine:
    """奇门遁甲排盘引擎

    Usage:
        engine = QimenEngine()
        result = engine.calculate(2024, 7, 11, 13, 30)
    """

    def calculate(self, year: int, month: int, day: int,
                  hour: int, minute: int = 0,
                  city: str = "北京") -> QimenResult:
        """Calculate the complete Qimen Dunjia chart for a given datetime.

        Args:
            year: 年份
            month: 月份 (1-12)
            day: 日期 (1-31)
            hour: 小时 (0-23)
            minute: 分钟 (0-59)
            city: 城市 (用于时辰校正, 暂未实现)

        Returns:
            QimenResult containing the complete 排盘.
        """
        solar = Solar.fromYmdHms(year, month, day, hour, minute, 0)
        lunar = solar.getLunar()

        # --- 1. 确定节气 & 阴阳遁局数 ---
        term_name = self._resolve_solar_term(solar)
        dun_type, (upper, middle, lower) = JIE_QI_DUN[term_name]

        # --- 2. 确定元 (上/中/下) ---
        term_start = self._resolve_term_start(solar, term_name)
        days_in_term = int(solar.getJulianDay() - term_start.getJulianDay())
        yuan_idx = min(days_in_term // 5, 2)
        ju_number = [upper, middle, lower][yuan_idx]

        # --- 3. 排地盘 ---
        dipan = self._build_dipan(dun_type, ju_number)

        # --- 4. 时辰信息 ---
        time_gan = lunar.getTimeGan()
        time_zhi = lunar.getTimeZhi()
        time_gan_idx = lunar.getTimeGanIndex()
        time_zhi_idx = lunar.getTimeZhiIndex()

        # --- 5. 找旬首 (Xun Shou) ---
        xunshou_zhi_idx = (time_zhi_idx - time_gan_idx + 12) % 12
        xunshou_branch = DIZHI[xunshou_zhi_idx]

        # 旬首 → 六仪 → 值符宫位
        yi_idx = xunshou_to_yi(xunshou_zhi_idx)
        xunshou_yi = YI_QI[yi_idx]

        xunshou_palace = self._find_yi_palace(dipan, xunshou_yi)

        # --- 6. 值符星 & 值使门 ---
        zhifu_star = JIU_XING_ORIGIN[xunshou_palace]
        # 中宫(5)无门, 天禽与天芮同宫, 值使门取坤二宫(2)
        door_palace = xunshou_palace if xunshou_palace != 5 else 2
        zhishi_door = BA_MEN_ORIGIN[door_palace]

        # --- 7. 值符天盘位置 (时干所在宫位) ---
        target_palace = self._find_hour_gan_palace(dipan, time_gan, time_gan_idx, xunshou_palace)

        # --- 8. 排天盘九星 ---
        jiuxing = self._build_jiuxing(zhifu_star, target_palace)

        # --- 9. 排八门 ---
        bamen_map = self._build_bamen(zhishi_door, xunshou_zhi_idx, time_zhi_idx, dun_type)

        # --- 10. 排八神 ---
        bashen_map = self._build_bashen(dun_type, target_palace)

        # --- 11. 排天盘奇仪 ---
        tianpan = self._build_tianpan_qi(dipan, zhifu_star, target_palace)

        # --- 12. 转换为宫位名称 ---
        return self._to_result(
            dun_type, ju_number,
            dipan, tianpan, bamen_map, jiuxing, bashen_map,
            zhifu_star, zhishi_door,
            term_name, ["上元", "中元", "下元"][yuan_idx],
            solar, f"甲{xunshou_branch}", xunshou_yi, xunshou_palace, target_palace,
        )

    # ---- 节气相关 ----

    def _resolve_solar_term(self, solar: Solar) -> str:
        """Resolve which solar term the given date falls in.
        Returns the Chinese name string of the solar term."""
        lunar = solar.getLunar()
        current = lunar.getCurrentJieQi()
        if current is not None:
            return current.getName()
        prev = lunar.getPrevJieQi()
        if prev is not None:
            return prev.getName()
        # Fallback
        return "冬至"

    def _resolve_term_start(self, solar: Solar, term_name: str) -> Solar:
        """Find the exact start date of the current solar term."""
        jq_obj = solar.getLunar().getCurrentJieQi()
        if jq_obj is not None and jq_obj.getName() == term_name:
            return solar
        # Walk back day by day
        for i in range(1, 30):
            s = solar.nextDay(-i)
            jq = s.getLunar().getCurrentJieQi()
            if jq is not None and jq.getName() == term_name:
                return s
        # Walk forward (for rare edge case at term boundary)
        for i in range(1, 3):
            s = solar.nextDay(i)
            jq = s.getLunar().getCurrentJieQi()
            if jq is not None and jq.getName() == term_name:
                return s
        return solar

    # ---- 排地盘 ----

    def _build_dipan(self, dun_type: str, ju_number: int) -> Dict[int, str]:
        """排地盘: place 六仪三奇 in 9 palaces.

        For 阳遁: 戊 starts at palace ju_number, then advance (1→2→3→...).
        For 阴遁: 戊 starts at palace ju_number, then retreat (1→9→8→...).
        """
        dipan: Dict[int, str] = {}
        for i, yi_qi in enumerate(YI_QI):
            if dun_type == "阳遁":
                palace = ((ju_number - 1 + i) % 9) + 1
            else:
                palace = ((ju_number - 1 - i + 9) % 9) + 1
            dipan[palace] = yi_qi
        return dipan

    # ---- 查找 ----

    def _find_yi_palace(self, dipan: Dict[int, str], yi: str) -> int:
        """Find which palace contains a given 仪 on 地盘."""
        for p, v in dipan.items():
            if v == yi:
                return p
        raise ValueError(f"六仪 '{yi}' not found on 地盘: {dipan}")

    def _find_hour_gan_palace(self, dipan: Dict[int, str],
                               time_gan: str, time_gan_idx: int,
                               fallback_palace: int) -> int:
        """Find the 地盘 palace where the hour's 天干 sits.

        If 天干 is 甲 (index 0), return fallback_palace since 甲 is hidden.
        """
        if time_gan_idx == 0:  # 甲旬, hidden
            return fallback_palace
        for p, v in dipan.items():
            if v == time_gan:
                return p
        return fallback_palace

    # ---- 排九星 ----

    def _build_jiuxing(self, zhifu_star: str, target_palace: int) -> Dict[int, str]:
        """排天盘九星: 值符引领, 其余九星按宫位顺序跟随.

        All 9 stars rotate as a group. The 值符 star moves to target_palace,
        and all other stars shift by the same offset.
        """
        zhifu_orig = JIU_XING_PALACE[zhifu_star]
        shift = (target_palace - zhifu_orig + 9) % 9

        jiuxing: Dict[int, str] = {}
        for orig_palace in range(1, 10):
            new_palace = ((orig_palace - 1 + shift) % 9) + 1
            jiuxing[new_palace] = JIU_XING_ORIGIN[orig_palace]
        return jiuxing

    # ---- 排八门 ----

    def _build_bamen(self, zhishi_door: str,
                     xunshou_zhi_idx: int, time_zhi_idx: int,
                     dun_type: str) -> Dict[int, str]:
        """排八门: 值使门引领, 其余七门跟随.

        门按阳遁/阴遁路径排列, 从中宫跳过。
        步数 = (时支 - 旬首支 + 12) % 12, 取模8。
        """
        steps = (time_zhi_idx - xunshou_zhi_idx + 12) % 12
        path = YANG_DOOR_PATH if dun_type == "阳遁" else YIN_DOOR_PATH

        zhishi_orig = BA_MEN_PALACE[zhishi_door]
        orig_idx = path.index(zhishi_orig)
        new_idx = (orig_idx + steps) % 8
        offset = (orig_idx - new_idx + 8) % 8

        bamen: Dict[int, str] = {}
        for i, palace in enumerate(path):
            door_idx = (i + offset) % 8
            bamen[palace] = BA_MEN[door_idx]
        return bamen

    # ---- 排八神 ----

    def _build_bashen(self, dun_type: str, zhifu_palace: int) -> Dict[int, str]:
        """排八神: 值符引领, 其余七神按顺逆排列.

        阳遁顺行, 阴遁逆行。八神只占8个宫(跳过中宫5)。
        """
        path = YANG_SHEN_PATH if dun_type == "阳遁" else YIN_SHEN_PATH

        # Find where 值符神 goes (same as 值符星 on 天盘)
        if zhifu_palace in path:
            zhifu_idx = path.index(zhifu_palace)
        else:
            # If 值符 is in 中宫 (palace 5), place 值符神 at first path palace
            zhifu_idx = 0

        bashen: Dict[int, str] = {}
        for i, palace in enumerate(path):
            bs_idx = (i - zhifu_idx + 8) % 8
            bashen[palace] = BA_SHEN[bs_idx]
        return bashen

    # ---- 排天盘奇仪 ----

    def _build_tianpan_qi(self, dipan: Dict[int, str],
                           zhifu_star: str,
                           target_palace: int) -> Dict[int, str]:
        """排天盘奇仪: 随九星旋转的六仪三奇.

        天盘奇仪第p宫 = 地盘奇仪从(p - shift)宫, shift由值符位移决定。
        """
        zhifu_orig = JIU_XING_PALACE[zhifu_star]
        shift = (target_palace - zhifu_orig + 9) % 9

        tianpan: Dict[int, str] = {}
        for p in range(1, 10):
            orig_p = ((p - 1 - shift + 9) % 9) + 1
            tianpan[p] = dipan[orig_p]
        return tianpan

    # ---- 输出转换 ----

    def _to_result(self, dun_type, ju_number,
                   dipan, tianpan, bamen, jiuxing, bashen,
                   zhifu_star, zhishi_door,
                   term_name, yuan_name,
                   solar, xunshou, xunshou_yi,
                   xunshou_palace, target_palace) -> QimenResult:
        """Convert internal 1-indexed palace dicts to named palace dicts."""
        return QimenResult(
            dun_type=dun_type,
            ju_number=ju_number,
            dipan={PALACE_NAMES[p]: dipan[p] for p in sorted(dipan)},
            tianpan={PALACE_NAMES[p]: tianpan[p] for p in sorted(tianpan)},
            bamen={PALACE_NAMES[p]: bamen[p] for p in sorted(bamen)},
            jiuxing={PALACE_NAMES[p]: jiuxing[p] for p in sorted(jiuxing)},
            bashen={PALACE_NAMES[p]: bashen[p] for p in sorted(bashen)},
            zhifu_star=zhifu_star,
            zhishi_door=zhishi_door,
            raw_data={
                "solar_term": term_name,
                "yuan": yuan_name,
                "bazi": solar.getLunar().getBaZi(),
                "xunshou": xunshou,
                "xunshou_yi": xunshou_yi,
                "xunshou_palace": PALACE_NAMES.get(xunshou_palace, ""),
                "hour_gan_palace": PALACE_NAMES.get(target_palace, ""),
            },
        )

    def print_chart(self, result: QimenResult) -> str:
        """Format the Qimen result as a human-readable 九宫格 chart.

        Lo Shu grid layout (traditional 9-palace grid):

            巽四 | 离九 | 坤二
            -----+------+-----
            震三 | 中五 | 兑七
            -----+------+-----
            艮八 | 坎一 | 乾六
        """
        lo_shu_order = [(4, "巽"), (9, "离"), (2, "坤"),
                        (3, "震"), (5, "中"), (7, "兑"),
                        (8, "艮"), (1, "坎"), (6, "乾")]

        header = (
            f"{'=' * 50}\n"
            f"  奇门遁甲 - {result.dun_type}{result.ju_number}局\n"
            f"{'=' * 50}\n"
        )

        lines = []
        for p, name in lo_shu_order:
            row = [
                f"┌─ {name}宫 ─────────────┐",
                f"│ 八神: {result.bashen.get(name, '-'):6s}           │",
                f"│ 九星: {result.jiuxing.get(name, '-'):6s}           │",
                f"│ 八门: {result.bamen.get(name, '-'):6s}           │",
                f"│ 天盘: {result.tianpan.get(name, '-'):6s}           │",
                f"│ 地盘: {result.dipan.get(name, '-'):6s}           │",
                f"└──────────────────────────┘",
            ]
            lines.append("\n".join(row))

        # Arrange in 3x3 grid
        grid = []
        for i in range(0, 9, 3):
            rows_blocks = []
            for line_idx in range(7):
                part = f"  {lines[i + 0].split(chr(10))[line_idx]}  "
                mid = f"  {lines[i + 1].split(chr(10))[line_idx]}  "
                right = f"  {lines[i + 2].split(chr(10))[line_idx]}  "
                rows_blocks.append(f"{part}{mid}{right}")
            grid.append("\n".join(rows_blocks))
            if i < 6:
                grid.append("")

        body = "\n".join(grid)
        footer = (
            f"\n{'─' * 50}\n"
            f"  值符星: {result.zhifu_star}    值使门: {result.zhishi_door}\n"
            f"  节气: {result.raw_data.get('solar_term', '-')}  "
            f"  元: {result.raw_data.get('yuan', '-')}\n"
            f"  旬首: {result.raw_data.get('xunshou', '-')}  "
            f"  时干宫: {result.raw_data.get('hour_gan_palace', '-')}\n"
            f"{'─' * 50}\n"
        )

        return header + body + footer
