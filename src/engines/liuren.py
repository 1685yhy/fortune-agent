# src/engines/liuren.py
"""六壬排盘引擎 v1（大六壬, Da Liu Ren）。

核心算法（口径见 raw_data["口径"]，与古籍对照）：
  1. 月将：以中气定将（雨水后亥将、春分后戌将、谷雨酉、小满申、夏至未、
     大暑午、处暑巳、秋分辰、霜降卯、小雪寅、冬至丑、大寒子），用
     lunar-python getCurrentQi/getPrevQi 判定当前中气
  2. 天地盘：月将加占时（时支），天盘十二将顺布十二宫
  3. 四课：日干寄宫（甲寅乙辰丙戊巳丁己未庚申辛戌壬亥癸丑）取干上神为
     第一课、课上神递生第二课；日支取支上神为第三课、递生第四课
  4. 三传（九宗门确定性判定）：伏吟/返吟盘型优先；常盘先看四课上下克
     （下贼上/上克下），一克取上神发用（元首=上克下、重审=下贼上），
     多克先下贼后上克、比用法去阴阳不同者，俱比/俱不比/互克走涉害
     （孟仲季简便法，涉害深浅未实现则记降级）；无克依次遥克（蒿矢/
     弹射）→ 八专/别责/昴星
  5. 贵人：口诀"甲戊庚牛羊，乙己鼠猴乡，丙丁猪鸡位，壬癸兔蛇藏，
     六辛逢马虎"（前字昼贵说，见 audit）；卯至申时用昼贵；贵人落地盘
     阳支顺行、阴支逆行，布十二天将
  6. 旬空：日柱所在旬之空亡二字
  7. 无法确定处如实记入 raw_data["降级"]，不伪造

仅做确定性排盘事实，不产解释性断语。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from lunar_python import Solar

TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# ---- 月将：中气 -> 月将（月将随中气换将）----
QI_YUEJIANG = {
    "雨水": "亥", "春分": "戌", "谷雨": "酉", "小满": "申",
    "夏至": "未", "大暑": "午", "处暑": "巳", "秋分": "辰",
    "霜降": "卯", "小雪": "寅", "冬至": "丑", "大寒": "子",
}

# ---- 日干寄宫（十干寄十二宫）----
GAN_JIGONG = {
    "甲": "寅", "乙": "辰", "丙": "巳", "丁": "未", "戊": "巳",
    "己": "未", "庚": "申", "辛": "戌", "壬": "亥", "癸": "丑",
}

# ---- 干支五行 ----
GAN_WUXING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
              "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
ZHI_WUXING = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土",
              "巳": "火", "午": "火", "未": "土", "申": "金", "酉": "金",
              "戌": "土", "亥": "水"}
WUXING_KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}  # 前者克后者

# ---- 贵人（旦贵, 暮贵）；口诀"甲戊庚牛羊…"，前字昼贵说（《六壬大全》口径）----
GUIREN_DAY_NIGHT = {
    "甲": ("丑", "未"), "戊": ("丑", "未"), "庚": ("丑", "未"),
    "乙": ("子", "申"), "己": ("子", "申"),
    "丙": ("亥", "酉"), "丁": ("亥", "酉"),
    "壬": ("巳", "卯"), "癸": ("巳", "卯"),
    "辛": ("午", "寅"),
}
DAY_SHIS = ("卯", "辰", "巳", "午", "未", "申")  # 卯至申时属昼（用旦贵）
YANG_ZHI = ("子", "寅", "辰", "午", "申", "戌")
YANG_GAN = ("甲", "丙", "戊", "庚", "壬")

# ---- 十二天将（贵人起头，顺逆布十二宫）----
TIANJIANG = ["贵人", "螣蛇", "朱雀", "六合", "勾陈", "青龙",
             "天空", "白虎", "太常", "玄武", "太阴", "天后"]

# ---- 地盘干（贵人宫位干支用，午宫=丁午）----
DIPAN_GAN = {"子": "癸", "丑": "己", "寅": "甲", "卯": "乙", "辰": "戊",
             "巳": "丙", "午": "丁", "未": "己", "申": "庚", "酉": "辛",
             "戌": "戊", "亥": "壬"}

# ---- 三刑（伏吟中末传）----
XING = {"寅": "巳", "巳": "申", "申": "寅",
        "丑": "戌", "戌": "未", "未": "丑",
        "子": "卯", "卯": "子",
        "辰": "辰", "午": "午", "酉": "酉", "亥": "亥"}
# ---- 六冲（返吟盘型判定 / 伏吟中传自刑取冲）----
CHONG = {"子": "午", "午": "子", "丑": "未", "未": "丑",
         "寅": "申", "申": "寅", "卯": "酉", "酉": "卯",
         "辰": "戌", "戌": "辰", "巳": "亥", "亥": "巳"}
# ---- 驿马（返吟无克"井栏格"发用）----
YIMA = {"申": "寅", "子": "寅", "辰": "寅",   # 申子辰马在寅
        "寅": "申", "午": "申", "戌": "申",   # 寅午戌马在申
        "巳": "亥", "酉": "亥", "丑": "亥",   # 巳酉丑马在亥
        "亥": "巳", "卯": "巳", "未": "巳"}   # 亥卯未马在巳
# ---- 三合下一支（别责阴日：支前一位，酉→丑）----
SANHE_NEXT = {"子": "辰", "辰": "申", "申": "子",
              "寅": "戌", "戌": "午", "午": "寅",
              "巳": "酉", "酉": "丑", "丑": "巳",
              "亥": "卯", "卯": "未", "未": "亥"}
# ---- 天干五合（别责阳日：取合干寄宫上神）----
WUHE = {"甲": "己", "己": "甲", "乙": "庚", "庚": "乙",
        "丙": "辛", "辛": "丙", "丁": "壬", "壬": "丁",
        "戊": "癸", "癸": "戊"}

# 孟仲季（涉害取用优先序：孟>仲>季）
MENG_ZHI = ("寅", "申", "巳", "亥")
ZHONG_ZHI = ("子", "午", "卯", "酉")


def _wuxing_of(char: str) -> str:
    """干支单字取五行（干用干表、支用支表）。"""
    if char in GAN_WUXING:
        return GAN_WUXING[char]
    return ZHI_WUXING[char]


def _ke_relation(upper: str, lower: str) -> str:
    """四课上下克情：上克下 / 下贼上 / 无克（upper=天盘上神, lower=下神）。"""
    upper_wx, lower_wx = _wuxing_of(upper), _wuxing_of(lower)
    if WUXING_KE[upper_wx] == lower_wx:
        return "上克下"
    if WUXING_KE[lower_wx] == upper_wx:
        return "下贼上"
    return "无克"


def _zhi_index(zhi: str) -> int:
    return DIZHI.index(zhi)


@dataclass
class LiurenResult:
    """六壬排盘结果。

    Attributes:
        year_gan..hour_zhi: 年月日时干支（八字口径，lunar-python getBaZi）
        yuejiang: 月将（如"午"，大暑后午将）
        tianpan: 天地盘 {地盘宫: 天盘将}
        sipan: 四课 [{"位置": 课名, "干支": 上神+下神}]
        sanchuan: 三传 [初传, 中传, 末传]
        guiren: 贵人所在宫位（地盘干支，如"丁午"）
        xunkong: 旬空地支（如 ["寅", "卯"]）
        raw_data: 中间量（克情/宗门/贵人详情/天将/降级/口径等）
    """
    year_gan: str = ""
    year_zhi: str = ""
    month_gan: str = ""
    month_zhi: str = ""
    day_gan: str = ""
    day_zhi: str = ""
    hour_zhi: str = ""
    yuejiang: str = ""
    tianpan: Dict[str, str] = field(default_factory=dict)
    sipan: List[dict] = field(default_factory=list)
    sanchuan: List[str] = field(default_factory=list)
    guiren: str = ""
    xunkong: List[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转为 dict（供规则库 analyze/evaluate 使用）。"""
        return {
            "year_gan": self.year_gan, "year_zhi": self.year_zhi,
            "month_gan": self.month_gan, "month_zhi": self.month_zhi,
            "day_gan": self.day_gan, "day_zhi": self.day_zhi,
            "hour_zhi": self.hour_zhi,
            "yuejiang": self.yuejiang,
            "tianpan": self.tianpan,
            "sipan": self.sipan,
            "sanchuan": self.sanchuan,
            "guiren": self.guiren,
            "xunkong": self.xunkong,
            "raw_data": self.raw_data,
        }


class LiurenEngine:
    """六壬排盘引擎。

    Usage:
        engine = LiurenEngine()
        result = engine.calculate(1990, 8, 16, 14, 30)
    """

    def calculate(self, year: int, month: int, day: int,
                  hour: int, minute: int = 0,
                  city: str = "北京") -> LiurenResult:
        """排六壬盘。

        Args:
            year/month/day: 公历年月日
            hour/minute: 时/分（占时按 lunar-python 时支口径）
            city: 城市（保留参数，暂不用于真太阳时校正）

        Returns:
            LiurenResult 完整六壬盘（月将/天地盘/四课/三传/贵人/旬空）。
        """
        solar = Solar.fromYmdHms(year, month, day, hour, minute, 0)
        lunar = solar.getLunar()
        bazi = lunar.getBaZi()

        day_gan, day_zhi = bazi[2][0], bazi[2][1]
        hour_zhi = lunar.getTimeZhi()

        # --- 1. 月将（中气定将）---
        qi = lunar.getCurrentQi()
        if qi is None:
            qi = lunar.getPrevQi()
        qi_name = qi.getName() if qi is not None else ""
        yuejiang = QI_YUEJIANG.get(qi_name, "")
        degrade = []
        if not yuejiang:
            yuejiang = "亥"
            degrade.append("未取到当前中气，月将降级为亥将")

        # --- 2. 天地盘（月将加占时，顺布十二宫）---
        jiang_idx = _zhi_index(yuejiang)
        shi_idx = _zhi_index(hour_zhi)
        tianpan = {
            DIZHI[(shi_idx + i) % 12]: DIZHI[(jiang_idx + i) % 12]
            for i in range(12)
        }

        # --- 3. 四课（日干寄宫递生）---
        jigong = GAN_JIGONG[day_gan]
        s1 = tianpan[jigong]     # 第一课 上神（干上神）
        s2 = tianpan[s1]         # 第二课 上神
        s3 = tianpan[day_zhi]    # 第三课 上神（支上神）
        s4 = tianpan[s3]         # 第四课 上神
        sipan = [
            {"位置": "第一课(干上)", "干支": s1 + day_gan},
            {"位置": "第二课", "干支": s2 + s1},
            {"位置": "第三课(支上)", "干支": s3 + day_zhi},
            {"位置": "第四课", "干支": s4 + s3},
        ]
        # 四课克情（上神五行 vs 下神五行）
        ke_qing = [
            {"课": 1, "上": s1, "下": day_gan, "克": _ke_relation(s1, day_gan)},
            {"课": 2, "上": s2, "下": s1, "克": _ke_relation(s2, s1)},
            {"课": 3, "上": s3, "下": day_zhi, "克": _ke_relation(s3, day_zhi)},
            {"课": 4, "上": s4, "下": s3, "克": _ke_relation(s4, s3)},
        ]

        # --- 4. 盘型（伏吟/返吟/常）---
        if yuejiang == hour_zhi:
            pan_type = "伏吟"
        elif CHONG[yuejiang] == hour_zhi:
            pan_type = "返吟"
        else:
            pan_type = "常"

        # --- 5. 三传（九宗门）---
        sanchuan, zongmen, kename = self._build_sanchuan(
            pan_type, day_gan, day_zhi, tianpan, ke_qing, s1, s3, sipan, degrade)

        # --- 6. 旬空 ---
        gan_idx = TIANGAN.index(day_gan)
        zhi_idx = _zhi_index(day_zhi)
        xun_first_zhi = DIZHI[(zhi_idx - gan_idx) % 12]  # 旬首支（甲X旬）
        xunkong = [DIZHI[(_zhi_index(xun_first_zhi) + 10) % 12],
                   DIZHI[(_zhi_index(xun_first_zhi) + 11) % 12]]

        # --- 7. 贵人 ---
        guiren, guiren_info = self._build_guiren(day_gan, hour_zhi, tianpan)

        result = LiurenResult(
            year_gan=bazi[0][0], year_zhi=bazi[0][1],
            month_gan=bazi[1][0], month_zhi=bazi[1][1],
            day_gan=day_gan, day_zhi=day_zhi,
            hour_zhi=hour_zhi,
            yuejiang=yuejiang,
            tianpan=tianpan,
            sipan=sipan,
            sanchuan=sanchuan,
            guiren=guiren,
            xunkong=xunkong,
            raw_data={
                "bazi": bazi,
                "时干": lunar.getTimeGan(),
                "时支": hour_zhi,
                "中气": qi_name,
                "盘型": pan_type,
                "四课克情": ke_qing,
                "宗门": zongmen,
                "课名": kename,
                "旬首": "甲" + xun_first_zhi,
                **guiren_info,
                "降级": degrade,
                "口径": {
                    "月将": "中气定将（雨水后亥将…大寒子将），气为当前/最近中气",
                    "发用": "贼克取上神（天盘神）为初传，中末传=初传支位天盘神递推",
                    "贵人": "甲戊庚牛羊口诀，前字昼贵说；卯至申时用昼贵；"
                            "贵人落地盘阳支顺行、阴支逆行，布十二天将",
                    "昼夜": "卯至申时为昼，酉至寅时为夜",
                    "涉害": "孟仲季简便法（孟寅申巳亥>仲子午卯酉>季辰戌丑未）",
                    "八专/别责": "八专=干支同宫(干寄宫==日支)且无克，不取遥克；"
                                  "别责=干上神==日支(课2==课3实三课)且无克无遥克",
                },
            },
        )
        return result

    # ---- 三传（九宗门）----

    def _build_sanchuan(self, pan_type: str, day_gan: str, day_zhi: str,
                        tianpan: dict, ke_qing: list,
                        s1: str, s3: str, sipan: list,
                        degrade: list) -> tuple:
        """九宗门确定性判定，返回 (三传, 宗门大类, 课名)。"""
        if pan_type == "伏吟":
            return self._sanchuan_fuyin(day_gan, day_zhi, tianpan, s1, s3)
        if pan_type == "返吟":
            return self._sanchuan_fanyin(day_gan, day_zhi, tianpan, ke_qing,
                                         s1, s3)
        return self._sanchuan_chang(day_gan, day_zhi, tianpan, ke_qing,
                                    s1, s3, sipan, degrade)

    def _sanchuan_fuyin(self, day_gan, day_zhi, tianpan, s1, s3):
        """伏吟课：天地盘同位。

        有克（仅乙/癸类：干上神克日干）→ 不虞课，初传取克者=干上神；
        无克 → 阳日取干上神（自任）、阴日取支上神（自信）。
        中末传取刑；初传自刑（杜传）中传颠倒日辰（取另一上神），
        中传自刑则末传取中传之冲。
        """
        ke = _ke_relation(s1, day_gan)
        if ke != "无克":
            kename, chu = "不虞", s1  # 取克者（干上神）为用
        elif day_gan in YANG_GAN:
            kename, chu = "自任", s1
        else:
            kename, chu = "自信", s3
        zhong = XING[chu]
        if zhong == chu:  # 初传自刑（杜传）：中传颠倒日辰
            zhong = s3 if chu == s1 else s1
        mo = XING[zhong]
        if mo == zhong:  # 中传自刑：末传取中传之冲
            mo = CHONG[zhong]
        return [chu, zhong, mo], "伏吟", kename

    def _sanchuan_fanyin(self, day_gan, day_zhi, tianpan, ke_qing, s1, s3):
        """返吟课：天地盘对冲。

        有克 → 无依课，取克贼（下贼上优先）上神为用，中末传=天盘递推；
        无克（丁丑/己丑/辛丑/丁未/己未/辛未六日）→ 井栏格：
        初传=支上驿马，中传=支上神，末传=干上神。
        """
        down = [k["上"] for k in ke_qing if k["克"] == "下贼上"]
        up = [k["上"] for k in ke_qing if k["克"] == "上克下"]
        if down or up:
            chu = self._pick_ke_candidate(day_gan, down, up)
            return [chu, tianpan[chu], tianpan[tianpan[chu]]], "返吟", "无依"
        chu = YIMA[day_zhi]
        return [chu, s3, s1], "返吟", "井栏格"

    def _sanchuan_chang(self, day_gan, day_zhi, tianpan, ke_qing,
                        s1, s3, sipan, degrade):
        """常盘九宗门：贼克→比用/涉害→遥克→八专/别责/昴星。"""
        down = [k["上"] for k in ke_qing if k["克"] == "下贼上"]
        up = [k["上"] for k in ke_qing if k["克"] == "上克下"]
        total = len(down) + len(up)

        # ---- 有克 ----
        if total == 1:
            chu = (down or up)[0]
            kename = "重审课" if down else "元首课"
            return [chu, tianpan[chu], tianpan[tianpan[chu]]], "贼克", kename
        if total >= 2:
            # 同向多克 → 比用（与日干阴阳比）；互克/俱不比/多比 → 涉害
            if down and up:
                candidates = down + up  # 互克 → 涉害（先下贼后上克排序）
                degrade.append("涉害深浅(涉归本家受克计数)未实现，采用孟仲季简便法")
                chu = self._mengzhongji(candidates, ke_qing)
                return [chu, tianpan[chu], tianpan[tianpan[chu]]], "涉害", "涉害课"
            group = down if down else up
            matched = [c for c in group
                       if (c in YANG_ZHI) == (day_gan in YANG_GAN)]
            if len(matched) == 1:
                return ([matched[0], tianpan[matched[0]],
                         tianpan[tianpan[matched[0]]]], "比用", "比用课")
            degrade.append("涉害深浅(涉归本家受克计数)未实现，采用孟仲季简便法")
            chu = self._mengzhongji(group, ke_qing)
            return [chu, tianpan[chu], tianpan[tianpan[chu]]], "涉害", "涉害课"

        # ---- 无克 ----
        # 八专：干支同宫（干寄宫==日支，四课两两相同），不取遥克。
        # 阳日取干上神在天盘顺数三神（含本位数）为初传，阴日取第四课上神
        # 逆数三位为初传；中末传均取干上神。
        if GAN_JIGONG[day_gan] == day_zhi:
            if day_gan in YANG_GAN:
                chu = DIZHI[(_zhi_index(s1) + 2) % 12]
            else:
                chu = DIZHI[(_zhi_index(ke_qing[3]["上"]) - 2) % 12]
            return [chu, s1, s1], "八专", "八专课"
        # 四课全备 → 遥克（蒿矢=上神克日，弹射=日克上神），无遥克 → 别责/昴星
        uppers = [k["上"] for k in ke_qing]
        she_ke_day = [u for u in uppers
                      if WUXING_KE[_wuxing_of(u)] == GAN_WUXING[day_gan]]
        day_ke_she = [u for u in uppers
                      if WUXING_KE[GAN_WUXING[day_gan]] == _wuxing_of(u)]
        if she_ke_day or day_ke_she:
            group = she_ke_day if she_ke_day else day_ke_she
            kename = "蒿矢课" if she_ke_day else "弹射课"
            if len(group) == 1:
                chu = group[0]  # 单候选不论比直接取用
            else:
                matched = [c for c in group
                           if (c in YANG_ZHI) == (day_gan in YANG_GAN)]
                if len(matched) == 1:
                    chu = matched[0]
                else:
                    degrade.append(f"遥克候选取用候选 {group} 不比/多比，"
                                   "采用孟仲季简便法")
                    chu = self._mengzhongji(group, ke_qing)
            return [chu, tianpan[chu], tianpan[tianpan[chu]]], "遥克", kename
        # 别责：干上神==日支（课2==课3，四课实三课），无克无遥克。
        # 阳日取干合（五合）所寄宫上神为初传，阴日取支三合前一位；
        # 中末传均取干上神。
        if s1 == day_zhi:
            if day_gan in YANG_GAN:
                chu = tianpan[GAN_JIGONG[WUHE[day_gan]]]
            else:
                chu = tianpan[SANHE_NEXT[day_zhi]]
            return [chu, s1, s1], "别责", "别责课"
        # 昴星：阳日取地盘酉上神（虎视），阴日取天盘酉之下神（冬蛇掩目）
        if day_gan in YANG_GAN:
            chu = tianpan["酉"]
            kename = "虎视"
            return [chu, s3, s1], "昴星", kename
        chu = [g for g, p in tianpan.items() if p == "酉"][0]
        kename = "冬蛇掩目"
        return [chu, s1, s3], "昴星", kename

    # ---- 取用辅助 ----

    def _pick_ke_candidate(self, day_gan, down, up):
        """贼克候选取用：先下贼后上克，比用法去阴阳不同者，余者孟仲季。"""
        group = down if down else up
        matched = [c for c in group
                   if (c in YANG_ZHI) == (day_gan in YANG_GAN)]
        if len(matched) == 1:
            return matched[0]
        if not matched and len(group) == 1:
            return group[0]
        return self._mengzhongji(group, [{"上": c} for c in group])

    def _mengzhongji(self, candidates: list, ke_qing: list) -> str:
        """孟仲季简便法：孟(寅申巳亥)>仲(子午卯酉)>季(辰戌丑未)，同者按课序。"""
        def rank(c):
            if c in MENG_ZHI:
                return 0
            if c in ZHONG_ZHI:
                return 1
            return 2
        order = {}
        for k in ke_qing:
            order[k["上"]] = order.get(k["上"], 0) + len(order)  # 首次出现课序
        return min(candidates, key=lambda c: (rank(c), order.get(c, 99)))

    # ---- 贵人 ----

    def _build_guiren(self, day_gan, hour_zhi, tianpan) -> tuple:
        """贵人：口诀前字昼贵；卯至申时昼；落宫地盘阳支顺行、阴支逆行。"""
        is_day = hour_zhi in DAY_SHIS
        guiren_shen = GUIREN_DAY_NIGHT[day_gan][0 if is_day else 1]
        palace = [g for g, p in tianpan.items() if p == guiren_shen][0]
        direction = "顺行" if palace in YANG_ZHI else "逆行"
        # 十二天将顺/逆布十二宫
        tianjiang = {}
        for i, jiang in enumerate(TIANJIANG):
            if direction == "顺行":
                tianjiang[DIZHI[(_zhi_index(palace) + i) % 12]] = jiang
            else:
                tianjiang[DIZHI[(_zhi_index(palace) - i) % 12]] = jiang
        guiren = DIPAN_GAN[palace] + palace
        return guiren, {
            "guiren_shen": guiren_shen,
            "guiren_day_night": "昼贵" if is_day else "夜贵",
            "guiren_direction": direction,
            "天将": tianjiang,
        }
