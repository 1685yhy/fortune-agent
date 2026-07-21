"""择日引擎 - 建除十二神 + 二十八宿 (Date Selection)."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from lunar_python import Solar


# ============================================================
# 基础数据
# ============================================================

DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# 建除十二神
JIANCHU = ["建", "除", "满", "平", "定", "执", "破", "危", "成", "收", "开", "闭"]

# 十二地支对应生肖
ZODIAC_MAP = {
    "子": "鼠", "丑": "牛", "寅": "虎", "卯": "兔",
    "辰": "龙", "巳": "蛇", "午": "马", "未": "羊",
    "申": "猴", "酉": "鸡", "戌": "狗", "亥": "猪",
}

# 六冲（地支对冲）
LIU_CHONG = {
    "子": "午", "丑": "未", "寅": "申", "卯": "酉",
    "辰": "戌", "巳": "亥", "午": "子", "未": "丑",
    "申": "寅", "酉": "卯", "戌": "辰", "亥": "巳",
}

# 农历月份对应地支（正月=寅）
MONTH_ZHI = {1: "寅", 2: "卯", 3: "辰", 4: "巳", 5: "午", 6: "未",
             7: "申", 8: "酉", 9: "戌", 10: "亥", 11: "子", 12: "丑"}

# 二十八宿（四象七宿）
ERSHIBA_XIU = [
    "角", "亢", "氐", "房", "心", "尾", "箕",   # 东方青龙
    "斗", "牛", "女", "虚", "危", "室", "壁",   # 北方玄武
    "奎", "娄", "胃", "昴", "毕", "觜", "参",   # 西方白虎
    "井", "鬼", "柳", "星", "张", "翼", "轸",   # 南方朱雀
]

# 二十八宿所属四象
ERSHIBA_XIU_SIXIANG = [
    "青龙", "青龙", "青龙", "青龙", "青龙", "青龙", "青龙",
    "玄武", "玄武", "玄武", "玄武", "玄武", "玄武", "玄武",
    "白虎", "白虎", "白虎", "白虎", "白虎", "白虎", "白虎",
    "朱雀", "朱雀", "朱雀", "朱雀", "朱雀", "朱雀", "朱雀",
]

# 二十八宿吉凶（传统说法）
ERSHIBA_XIU_JIXIONG = {
    "角": "吉", "亢": "凶", "氐": "吉", "房": "吉", "心": "凶", "尾": "吉", "箕": "吉",
    "斗": "吉", "牛": "凶", "女": "凶", "虚": "凶", "危": "凶", "室": "吉", "壁": "吉",
    "奎": "凶", "娄": "吉", "胃": "吉", "昴": "凶", "毕": "吉", "觜": "凶", "参": "吉",
    "井": "吉", "鬼": "凶", "柳": "凶", "星": "吉", "张": "吉", "翼": "凶", "轸": "吉",
}

# 建除十二神宜忌
JIANCHU_YI_JI = {
    "建": {
        "yi": ["出行", "嫁娶", "上梁", "起基", "纳财", "开市", "纳采"],
        "ji": ["动土", "开仓", "掘井", "安葬", "破土"],
        "desc": "健旺之日，宜建事，忌动土",
    },
    "除": {
        "yi": ["除服", "针灸", "沐浴", "治病", "扫舍", "破屋", "入殓"],
        "ji": ["嫁娶", "出行", "开市", "入宅", "安床"],
        "desc": "除旧布新之日，宜扫除沐浴",
    },
    "满": {
        "yi": ["祭祀", "祈福", "移徙", "求嗣", "纳财", "开市"],
        "ji": ["动土", "出火", "栽种", "针灸"],
        "desc": "圆满之日，宜祭祀祈福",
    },
    "平": {
        "yi": ["修屋", "沐浴", "扫舍", "平治", "涂泥", "补垣"],
        "ji": ["开市", "嫁娶", "出行", "入宅", "安葬"],
        "desc": "平常之日，宜修饰",
    },
    "定": {
        "yi": ["订婚", "交易", "纳财", "安床", "纳采", "会亲友"],
        "ji": ["出行", "诉讼", "词讼", "移徙"],
        "desc": "安定之日，宜订婚交易",
    },
    "执": {
        "yi": ["捕猎", "断壁", "建房", "筑堤", "收债", "诉讼"],
        "ji": ["出行", "嫁娶", "开市", "入宅"],
        "desc": "执持之日，宜捕猎诉讼",
    },
    "破": {
        "yi": ["破屋", "坏垣", "求医", "服药", "解除"],
        "ji": ["嫁娶", "出行", "入宅", "开市", "交易", "纳财"],
        "desc": "破败之日，诸事不宜",
    },
    "危": {
        "yi": ["登高", "求财", "交易", "安床", "纳畜"],
        "ji": ["动土", "出行", "嫁娶", "移徙", "开市"],
        "desc": "危险已过，宜求财",
    },
    "成": {
        "yi": ["嫁娶", "开市", "入学", "纳畜", "纳财", "出行", "移徙"],
        "ji": ["诉讼", "词讼", "破土", "动土"],
        "desc": "成就之日，百事皆宜",
    },
    "收": {
        "yi": ["收财", "播种", "进人口", "纳财", "捕捉"],
        "ji": ["出行", "移徙", "开市", "嫁娶", "安葬"],
        "desc": "收成之日，宜收财",
    },
    "开": {
        "yi": ["开市", "出行", "嫁娶", "纳财", "开业", "入学", "祭祀"],
        "ji": ["动土", "破土", "安葬"],
        "desc": "开始之日，宜开市出行",
    },
    "闭": {
        "yi": ["埋葬", "筑堤", "补垣", "封顶", "断壁"],
        "ji": ["开市", "出行", "嫁娶", "入宅", "移徙"],
        "desc": "闭塞之日，宜埋葬",
    },
}

# 建除十二神吉凶判定
JIANCHU_QUALITY = {
    "建": "平",   # 太岁同位，不宜动土
    "除": "吉",   # 除旧布新
    "满": "平",   # 圆满但易满招损
    "平": "平",   # 平常之日
    "定": "吉",   # 安定吉日
    "执": "平",   # 执持之日
    "破": "凶",   # 破败之日
    "危": "吉",   # 凡事宜谨慎
    "成": "吉",   # 成就之日
    "收": "平",   # 收成之日
    "开": "吉",   # 开始吉日
    "闭": "凶",   # 闭塞之日
}

# 常见事宜分类（用于purpose匹配）
PURPOSE_CATEGORIES = {
    "嫁娶": ["嫁娶", "结婚", "婚", "订婚", "纳采", "领证"],
    "开业": ["开业", "开市", "开张", "开工"],
    "出行": ["出行", "旅游", "旅行", "出差", "远行"],
    "入宅": ["入宅", "搬家", "迁居", "乔迁"],
    "动土": ["动土", "建房", "开工", "破土", "奠基"],
    "祭祀": ["祭祀", "祭祖", "上坟", "拜神"],
    "安葬": ["安葬", "下葬", "入殓", "出殡"],
    "交易": ["交易", "签约", "签合同", "买卖", "纳财"],
    "入学": ["入学", "开学", "拜师"],
    "求医": ["求医", "看病", "治病", "手术"],
}

# 二十八宿参考日（以 2000-01-01 为基准，值房宿）
# 2000-01-01 的二十八宿索引（0=角）
# 通过已知历法推算
ER_SH_BA_XIU_REFERENCE = {
    # (year, month, day): xiu_index
    (2000, 1, 1): 13,  # 虚宿
}


@dataclass
class ZeriResult:
    """择日分析结果"""
    jianchu: str              # 建除十二神: 建/除/满/平/定/执/破/危/成/收/开/闭
    ershibaxiu: str           # 二十八宿名
    xiu_jixiong: str          # 二十八宿吉凶
    yi: List[str]             # 宜
    ji: List[str]             # 忌
    chong: str                # 冲生肖
    overall: str              # 吉/凶/平
    raw_data: dict = field(default_factory=dict)


class ZeriEngine:
    """择日引擎 - 建除十二神 + 二十八宿"""

    def select(
        self,
        year: int,
        month: int,
        day: int,
        purpose: str = "",
    ) -> ZeriResult:
        """择日分析

        Args:
            year: 公历年份
            month: 公历月份 (1-12)
            day: 公历日期 (1-31)
            purpose: 事宜用途描述（可选），如 "嫁娶"、"开业"等
        """
        solar = Solar.fromYmd(year, month, day)
        lunar = solar.getLunar()
        eight_char = lunar.getEightChar()

        day_ganzhi = eight_char.getDay()
        day_zhi = day_ganzhi[1]

        month_ganzhi = eight_char.getMonth()
        month_zhi = month_ganzhi[1]

        lunar_month = lunar.getMonth()
        lunar_day = lunar.getDay()

        # ---- 建除十二神 ----
        jianchu = self._calc_jianchu(month_zhi, day_zhi)
        yi = list(JIANCHU_YI_JI[jianchu]["yi"])
        ji = list(JIANCHU_YI_JI[jianchu]["ji"])

        # 根据purpose调整宜忌
        if purpose:
            yi, ji = self._adjust_by_purpose(purpose, yi, ji)

        # ---- 二十八宿 ----
        xiu_name, xiu_jixiong = self._calc_ershibaxiu(year, month, day)

        # ---- 冲生肖 ----
        chong_zhi = LIU_CHONG[day_zhi]
        chong_zodiac = ZODIAC_MAP[chong_zhi]

        # ---- 综合判定 ----
        overall = self._judge_overall(jianchu, xiu_jixiong, purpose, yi, ji)

        return ZeriResult(
            jianchu=jianchu,
            ershibaxiu=xiu_name,
            xiu_jixiong=xiu_jixiong,
            yi=yi,
            ji=ji,
            chong=f"冲{chong_zodiac}({chong_zhi})",
            overall=overall,
            raw_data={
                "year": year,
                "month": month,
                "day": day,
                "purpose": purpose,
                "lunar_month": lunar_month,
                "lunar_day": lunar_day,
                "day_ganzhi": day_ganzhi,
                "month_ganzhi": month_ganzhi,
                "chong_zhi": chong_zhi,
                "chong_zodiac": chong_zodiac,
            },
        )

    # ---- 建除十二神 ----

    def _calc_jianchu(self, month_zhi: str, day_zhi: str) -> str:
        """计算建除十二神

        建: 月支与日支相同之日
        除: 月支后一支对应之日
        ...以此类推

        Args:
            month_zhi: 月地支
            day_zhi: 日地支
        """
        m_idx = DIZHI.index(month_zhi)
        d_idx = DIZHI.index(day_zhi)
        offset = (d_idx - m_idx) % 12
        return JIANCHU[offset]

    # ---- 二十八宿 ----

    def _calc_ershibaxiu(self, year: int, month: int, day: int) -> tuple:
        """计算二十八宿值日星宿

        使用基准日(2000-01-01 ~ 虚宿)推算偏移
        """
        ref_year, ref_month, ref_day = 2000, 1, 1
        ref_idx = ER_SH_BA_XIU_REFERENCE.get((ref_year, ref_month, ref_day), 13)

        # 计算天数差
        days_diff = self._days_between(ref_year, ref_month, ref_day, year, month, day)

        # 二十八宿循环
        xiu_idx = (ref_idx + days_diff) % 28
        xiu_name = ERSHIBA_XIU[xiu_idx]
        xiu_jixiong = ERSHIBA_XIU_JIXIONG.get(xiu_name, "平")

        return xiu_name, xiu_jixiong

    @staticmethod
    def _days_between(y1: int, m1: int, d1: int, y2: int, m2: int, d2: int) -> int:
        """计算两个日期之间的天数差（y2,m2,d2 - y1,m1,d1）"""
        # 使用简单累加算法
        def days_from_0(y, m, d):
            days = 0
            for yr in range(0, y):
                days += 366 if (yr % 4 == 0 and yr % 100 != 0) or (yr % 400 == 0) else 365
            month_days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
            if (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0):
                month_days[1] = 29
            for i in range(m - 1):
                days += month_days[i]
            days += d
            return days

        # 处理负年份（公元前）
        if y1 < 0 or y2 < 0:
            return (y2 - y1) * 365 + (m2 - m1) * 30 + (d2 - d1)

        # 对于正年份，只算相对差
        # 使用差值计算以避免大数溢出
        base_year = min(y1, y2)
        d1_from_base = days_from_0(y1, m1, d1) - days_from_0(base_year, 1, 0)
        d2_from_base = days_from_0(y2, m2, d2) - days_from_0(base_year, 1, 0)
        return d2_from_base - d1_from_base

    # ---- 宜忌调整 ----

    def _adjust_by_purpose(self, purpose: str, yi: List[str], ji: List[str]) -> tuple:
        """根据用途调整宜忌列表"""
        matched_category = None
        purpose_lower = purpose

        for category, keywords in PURPOSE_CATEGORIES.items():
            for kw in keywords:
                if kw in purpose_lower or purpose_lower in kw:
                    matched_category = category
                    break
            if matched_category:
                break

        if matched_category:
            # 如果用途在宜中，排到第一位
            if matched_category in yi:
                yi = [matched_category] + [y for y in yi if y != matched_category]
            elif matched_category not in ji:
                # 添加到宜列表（如果既不宜也不忌）
                yi = [matched_category] + yi

        return yi, ji

    # ---- 综合判定 ----

    def _judge_overall(
        self, jianchu: str, xiu_jixiong: str,
        purpose: str, yi: List[str], ji: List[str],
    ) -> str:
        """综合判定吉凶"""
        # 基础分数（5为中性）
        score = 5.0

        # 建除十二神评分
        jianchu_q = JIANCHU_QUALITY.get(jianchu, "平")
        if jianchu_q == "吉":
            score += 2.0
        elif jianchu_q == "凶":
            score -= 2.0

        # 二十八宿评分
        if xiu_jixiong == "吉":
            score += 1.0
        elif xiu_jixiong == "凶":
            score -= 1.0

        # 用途匹配
        if purpose:
            if yi and yi[0] == purpose or (len(yi) > 0 and any(
                any(kw in purpose for kw in PURPOSE_CATEGORIES.get(y, []))
                for y in [yi[0]]
            )):
                score += 1.0
            if purpose in ji:
                score -= 1.0

        if score >= 6.0:
            return "吉"
        elif score <= 3.0:
            return "凶"
        else:
            return "平"
