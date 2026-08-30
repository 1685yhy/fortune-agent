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

# 洛书环 (后天八卦环, 权威口径): 离9→坤2→兑7→乾6→坎1→艮8→震3→巽4→离9
# 报告 RING cw = [9,2,7,6,1,8,3,4]; 权威天盘@X = 地盘@ring(X, s)
LUOSHU_RING = [9, 2, 7, 6, 1, 8, 3, 4]
RING_IDX = {p: i for i, p in enumerate(LUOSHU_RING)}

# getJieQiTable() 键: 内部16节为中文名, 表边界8节为拼音大写 (如 DA_XUE) → 统一映射
_TERM_ALIAS = {
    "DONG_ZHI": "冬至", "XIAO_HAN": "小寒", "DA_HAN": "大寒", "LI_CHUN": "立春",
    "YU_SHUI": "雨水", "JING_ZHE": "惊蛰", "CHUN_FEN": "春分", "QING_MING": "清明",
    "GU_YU": "谷雨", "LI_XIA": "立夏", "XIAO_MAN": "小满", "MANG_ZHONG": "芒种",
    "XIA_ZHI": "夏至", "XIAO_SHU": "小暑", "DA_SHU": "大暑", "LI_QIU": "立秋",
    "CHU_SHU": "处暑", "BAI_LU": "白露", "QIU_FEN": "秋分", "HAN_LU": "寒露",
    "SHUANG_JIANG": "霜降", "LI_DONG": "立冬", "XIAO_XUE": "小雪", "DA_XUE": "大雪",
}


# ============================================================
# 工具函数
# ============================================================

def _ring_shift(orig: int, target: int) -> int:
    """洛书环步数 s: 原宫→落宫 沿环顺时针步数 (0-7).

    权威: 值符原宫→落宫 s 步, 天盘/九星/八门/八神整体沿环同转.
    """
    return (RING_IDX[target] - RING_IDX[orig]) % 8


def _ring_at(palace: int, shift: int) -> int:
    """洛书环位移: 宫位 palace 沿环后退 shift 步的宫位.

    权威天盘@X = 地盘@ring(X, s)  (X 沿环前 s 位看回 origin).
    """
    return LUOSHU_RING[(RING_IDX[palace] - shift) % 8]

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

        # --- 1. 确定节气 & 阴阳遁局数 (时辰级交节判断) ---
        term_name, jieqi_instant = self._resolve_solar_term(solar)
        dun_type, (upper, middle, lower) = JIE_QI_DUN[term_name]

        # --- 2. 确定元 (上/中/下) — 拆补法: 符头段固定元 (段起日地支判, 节气与符头各走各的) ---
        yuan_idx = self._resolve_yuan_idx(jieqi_instant, solar.getJulianDay())
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

        # --- 8. 排天盘九星 (洛书环刚性旋转) ---
        jiuxing = self._build_jiuxing(xunshou_palace, target_palace)

        # --- 9. 排八门 (值使落宫 = 旬首六仪地盘宫 ± steps 飞盘位移, 门位洛书环旋转) ---
        bamen_map = self._build_bamen(xunshou_palace, xunshou_zhi_idx, time_zhi_idx, dun_type)

        # --- 10. 排八神 (值符神领位, 阳顺阴逆环布) ---
        bashen_map = self._build_bashen(dun_type, target_palace)

        # --- 11. 排天盘奇仪 (洛书环刚性旋转) ---
        tianpan = self._build_tianpan_qi(dipan, xunshou_palace, target_palace)

        # --- 12. 转换为宫位名称 ---
        return self._to_result(
            dun_type, ju_number,
            dipan, tianpan, bamen_map, jiuxing, bashen_map,
            zhifu_star, zhishi_door,
            term_name, ["上元", "中元", "下元"][yuan_idx],
            solar, f"甲{xunshou_branch}", xunshou_yi, xunshou_palace, target_palace,
        )

    # ---- 节气相关 ----

    def _jieqi_instants(self, solar: Solar) -> List[Tuple[float, str, Solar]]:
        """节气时刻表 [(JulianDay, 名, Solar)], 北京天文时刻, 按时间升序.

        来自 lunar-python getJieQiTable (覆盖起局时刻前后约15个月, 与权威交节时刻一致).
        键为中文或拼音大写 (边界8节), 统一经 _TERM_ALIAS 映射.
        """
        table = solar.getLunar().getJieQiTable()
        instants = []
        for k, v in table.items():
            if isinstance(v, Solar):
                instants.append((v.getJulianDay(), _TERM_ALIAS.get(k, k), v))
        instants.sort()
        return instants

    def _resolve_solar_term(self, solar: Solar) -> Tuple[str, Solar]:
        """拆补法节气判定: 返回 (节气名, 交节时刻Solar).

        时辰级交节判断: 起局时刻 < 交节时刻 → 归上一节气段 (权威口径).
        边界例: 2025-12-21 23:00 (冬至 23:03 交节前3分钟) = 大雪段; 12/22 00:30 = 冬至段.
        晚子时归次日 (23:00 起时柱归次日) 由 getTimeGan/Zhi 处理, 本方法不动.
        """
        instants = self._jieqi_instants(solar)
        jd = solar.getJulianDay()
        for i, (jj, name, jq_solar) in enumerate(instants):
            if jj > jd:
                if i == 0:
                    break  # 起局早于表中最早节气 (超15个月) → 回退
                return instants[i - 1][1], instants[i - 1][2]
        # 起局晚于表中全部节气, 或早于最早节气 → 用精确时刻回退
        prev = solar.getLunar().getPrevJieQi(False)
        return prev.getName(), prev.getSolar()

    def _resolve_yuan_idx(self, jieqi_instant: Solar, query_jd: float) -> int:
        """拆补法定元 (权威口径, 衍象坊+openfate 双站互证 2026-08-31): 三元按符头段固定.

        三元段 = 通用 5 日符头段网格 (甲/己日 0:00 起), 节气与符头各走各的:
          - 段元由段起日地支固定判定: 子午卯酉=上元, 寅申巳亥=中元, 辰戌丑未=下元
          - 查询有效日: 晚子时 (>=23:00) 归次日
          - 交节时刻只决定节气段边界, 不参与定元 (无正授/超神/接气分档, 无补段概念)
        权威例 (段起日 地支 段元):
          处暑 2026 (交节 8/23 10:18, 段起己巳=中): 中[8/23,8/28) 下[8/28,9/2) 上[9/2,9/7)
          大雪 2025 (交节 12/7 05:04, 段起己酉=上): 上[12/7,12/11) 中[12/11,12/16) 下[12/16,12/21)
          惊蛰 2026 (交节 3/5 21:59, 段起甲戌=下): 3/5 22:30=下元; 3/5 23:30 晚子时归次日
                      段起己卯=上 -> 上元 (3/6 起全段上元)
        返回 0=上元 1=中元 2=下元. 与交节时刻无关, jieqi_instant 仅保留签名兼容.
        """
        # 查询有效日: 晚子时 (>=23:00) 归次日
        q = Solar.fromJulianDay(query_jd)
        if q.getHour() >= 23:
            q = q.nextDay(1)
        # 回找查询日所在符头段起日 (上一甲/己日; 正午日柱避免晚子时偏移)
        day = q
        while True:
            noon = Solar.fromYmdHms(day.getYear(), day.getMonth(), day.getDay(), 12, 0, 0)
            if noon.getLunar().getDayGan() in ("甲", "己"):
                break
            day = day.nextDay(-1)
        zhi = (Solar.fromYmdHms(day.getYear(), day.getMonth(), day.getDay(), 12, 0, 0)
               .getLunar().getDayZhi())
        # 段元: 子午卯酉=上元(0), 寅申巳亥=中元(1), 辰戌丑未=下元(2)
        if zhi in ("子", "午", "卯", "酉"):
            return 0
        if zhi in ("寅", "申", "巳", "亥"):
            return 1
        return 2

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

    def _build_jiuxing(self, xunshou_palace: int, target_palace: int) -> Dict[int, str]:
        """排天盘九星: 洛书环刚性旋转 (权威口径).

        值符星 = 旬首宫之星 (身份, 已在 calculate 确定); 天盘@X = 地盘星@ring(X, s),
        s = 值符原宫→落宫沿环步数. 中5 = 天禽 (我方显示口径; 权威禽芮同宫寄坤2, 计算等价).
        """
        orig = 2 if xunshou_palace == 5 else xunshou_palace
        tgt = 2 if target_palace == 5 else target_palace
        shift = _ring_shift(orig, tgt)

        jiuxing: Dict[int, str] = {5: JIU_XING_ORIGIN[5]}
        for p in LUOSHU_RING:
            jiuxing[p] = JIU_XING_ORIGIN[_ring_at(p, shift)]
        return jiuxing

    # ---- 排八门 ----

    def _build_bamen(self, xunshou_palace: int,
                     xunshou_zhi_idx: int, time_zhi_idx: int,
                     dun_type: str) -> Dict[int, str]:
        """排八门: 值使落宫 = 旬首六仪地盘宫 ± steps (飞盘位移, 阳遁+ 阴遁-).

        steps = (时支-旬首支)%12; 落宫结果中5 显示为坤2 (寄坤二);
        天禽旬 (旬首中5) 从真实中5位置计数.
        八门布局 = 洛书环刚性旋转: s_door = ring_shift(值使原宫, 值使落宫).
        """
        steps = (time_zhi_idx - xunshou_zhi_idx + 12) % 12
        direction = 1 if dun_type == "阳遁" else -1
        door_target = ((xunshou_palace - 1 + direction * steps) % 9) + 1
        if door_target == 5:
            door_target = 2  # 中5 寄坤二显示

        orig = 2 if xunshou_palace == 5 else xunshou_palace
        shift = _ring_shift(orig, door_target)

        bamen: Dict[int, str] = {}
        for p in LUOSHU_RING:
            bamen[p] = BA_MEN_ORIGIN[_ring_at(p, shift)]
        return bamen

    # ---- 排八神 ----

    def _build_bashen(self, dun_type: str, zhifu_palace: int) -> Dict[int, str]:
        """排八神: 值符神领位 (落宫 = 值符星落宫), 其余七神沿洛书环顺逆布列.

        阳遁顺行 (环上+1), 阴遁逆行 (环上-1); 中5空.
        值符落中5 → 寄坤2 显示 (与 值符星/天盘 口径一致).
        """
        cur = 2 if zhifu_palace == 5 else zhifu_palace
        direction = 1 if dun_type == "阳遁" else -1

        bashen: Dict[int, str] = {}
        for i, shen in enumerate(BA_SHEN):
            bashen[cur] = shen
            if i < 7:
                cur = LUOSHU_RING[(RING_IDX[cur] + direction) % 8]
        return bashen

    # ---- 排天盘奇仪 ----

    def _build_tianpan_qi(self, dipan: Dict[int, str],
                           xunshou_palace: int,
                           target_palace: int) -> Dict[int, str]:
        """排天盘奇仪: 洛书环刚性旋转 (权威口径).

        天盘@X = 地盘@ring(X, s); s = 值符原宫→落宫沿环步数; 中5 奇仪不动.
        """
        orig = 2 if xunshou_palace == 5 else xunshou_palace
        tgt = 2 if target_palace == 5 else target_palace
        shift = _ring_shift(orig, tgt)

        tianpan: Dict[int, str] = {5: dipan[5]}
        for p in LUOSHU_RING:
            tianpan[p] = dipan[_ring_at(p, shift)]
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
