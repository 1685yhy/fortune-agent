"""择日引擎 - 建除十二神 + 二十八宿 + 择吉日场景评分 (Date Selection)."""
from dataclasses import dataclass, field
from datetime import date, timedelta
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
        # K3-A4: 移除"嫁娶""纳采" —— 建日不宜嫁娶(主流黄历: xzw 2026-09-20 宜无嫁娶纳采,
        # lhl 2026-10-15 建日忌嫁娶 实证)。建日宜表仅保留建事类宜项。
        "yi": ["出行", "上梁", "起基", "纳财", "开市"],
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

# 十二节（交节日即新月令）: 节气日当天为 12 节之一时，建除按新月令起建
# （对齐主流通书"交节日即新月令"口径，如 2026-08-07 立秋 → 申月起建 → 执日）。
# 十二气（雨水/春分/…/大寒）不换月令。
JIEQI_JIE = {
    "立春", "惊蛰", "清明", "立夏", "芒种", "小暑",
    "立秋", "白露", "寒露", "立冬", "大雪", "小寒",
}

# 宜忌哨兵（lunar-python 当日「无忌事/无宜事」→ getDayYi/getDayJi 返回 ['无']）。
# 单一实现：本模块 _day_yi_ji（chat/工具/计划路径）与 wannianli 的合并入口共用，
# 不得各自再写一套过滤（两套口径即分裂 —— k23 只修了 zeri 面，万年历面漏到 k26）。
YI_JI_SENTINEL = "无"


def filter_yi_ji_sentinel(items) -> list:
    """剔除宜忌哨兵「无」（保留其余词与顺序）。"""
    return [x for x in items if x != YI_JI_SENTINEL]


@dataclass
class LuckyDayCard:
    """吉日卡片（三层评分 + Top3 返回）"""
    date: str                 # 公历 YYYY-MM-DD
    lunar_text: str           # 农历+干支+星期, 如 "农历六月廿七 乙卯日 星期日"
    yi: List[str]             # 宜（建除宜 + 当日黄历宜, 引擎在前）
    ji: List[str]             # 忌（建除忌 + 当日黄历忌）
    jishi: str                # 吉时段, 如 "巳时(9-11点)"; 取不到 → "吉时以当日黄历为准"
    xi_fangwei: str           # 喜神方位, 如 "正南"
    cai_fangwei: str          # 财神方位, 如 "西南"
    scene_score: int          # 场景匹配分 0-50
    personal_score: int       # 个人适配分 0-30（无八字默认 24）
    practical_score: int      # 实用加分 0-20（周末 +10, 节假日不判定）
    total: int                # 总分 0-100
    reason_source: str        # 实际最高分项, 如 "成日值日"/"喜用神相合"/"周末宜搬家"

@dataclass
class ZeriResult:
    """择日分析结果"""
    jianchu: str              # 建除十二神: 建/除/满/平/定/执/破/危/成/收/开/闭
    ershibaxiu: str           # 二十八宿名
    xiu_jixiong: str          # 二十八宿吉凶
    yi: List[str]             # 宜（建除表宜 + 神煞级黄历宜, 引擎在前; 见 _day_yi_ji）
    ji: List[str]             # 忌（K3-A3 神煞级优先, k27 双向: 与当日黄历宜冲突的建除表忌
                              #    已剔除; 与当日黄历忌冲突的建除表宜亦已剔除）
    chong: str                # 冲生肖
    overall: str              # 吉/凶/平（k27 三分, 与吉日卡片准入同源; 见 _judge_overall）
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
        jianchu = self._calc_jianchu_with_jieqi(month_zhi, day_zhi, lunar.getJieQi() or "")

        # ---- 单日宜忌: 单一事实源（K3-A3 神煞级优先, k27 双向）----
        # 本方法（chat/工具/意图路径）与计划路径（_build_lucky_card）共用, 禁止各自再合并/过滤。
        yi, ji = self._day_yi_ji(jianchu, lunar)
        # 当日神煞级黄历宜（与 _day_yi_ji 同一实现/同一来源）—— overall 用途判据输入
        lunar_yi, _ = self._authority_yi_ji(lunar)

        # ---- 二十八宿 ----
        xiu_name, xiu_jixiong = self._calc_ershibaxiu(year, month, day)

        # ---- 冲生肖 ----
        chong_zhi = LIU_CHONG[day_zhi]
        chong_zodiac = ZODIAC_MAP[chong_zhi]

        # ---- 综合判定（k27: 与卡片准入同源）----
        # 在 _adjust_by_purpose 之前判定 —— 与计划路径消费的 select()（不传 purpose）
        # 宜忌逐项相同, 故「出卡 ⟹ overall ≠ 凶」由构造成立（见 _judge_overall）。
        overall = self._judge_overall(jianchu, purpose, lunar_yi, yi, ji)

        # 根据purpose调整宜忌（在合并后的单一事实源上排序/补位）
        if purpose:
            yi, ji = self._adjust_by_purpose(purpose, yi, ji)

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

    def _calc_jianchu_with_jieqi(self, month_zhi: str, day_zhi: str, jieqi: str) -> str:
        """建除十二神（月支起建），节气日按新月令顺推一位。

        对齐主流通书"交节日即新月令"口径：当日为十二节之一（立春/惊蛰/…/小寒）
        时，月令已交新月，建除月支顺推一位；十二气（雨水/春分/…/大寒）不换月令。
        例: 2026-08-07 立秋 = 未月癸丑日（旧口径未月起建 → 破日），
        交节新月令申月 → 执日（主流口径）。

        Args:
            month_zhi: 月地支（lunar-python 八字月支，节气日 0 点尚未交节 → 旧月支）
            day_zhi: 日地支
            jieqi: 当日节气名（lunar-python getJieQi，无节气为空串）
        """
        if jieqi in JIEQI_JIE:
            month_zhi = DIZHI[(DIZHI.index(month_zhi) + 1) % 12]
        return self._calc_jianchu(month_zhi, day_zhi)

    # ---- 二十八宿 ----

    def _calc_ershibaxiu(self, year: int, month: int, day: int) -> tuple:
        """计算二十八宿值日星宿（lunar-python getXiu，与外部通书一致）。

        P1-1 审查 C1 修复: 原实现用基准日 (2000-01-01) 锚点推算，锚点错误
        （2000-01-01 实为胃宿，旧代码按 13=壁宿 起算）→ 全部日期差 3 天
        （2026-08-19 应为轸，旧代码出星）。改用 lunar-python getXiu() 统一口径，
        与万年历 day_detail 及外部通书三源验证一致。
        吉凶仍取传统二十八宿吉凶表（与 lunar-python getXiuLuck 全 28 宿核对一致）。
        """
        lunar = Solar.fromYmd(year, month, day).getLunar()
        xiu_name = lunar.getXiu()
        xiu_jixiong = ERSHIBA_XIU_JIXIONG.get(xiu_name, "平")
        return xiu_name, xiu_jixiong

    # ---- 宜忌调整 ----

    def _authority_yi_ji(self, lunar) -> tuple:
        """当日神煞级黄历宜/忌（lunar-python 通胜逐日表, 已滤哨兵「无」）。

        单一实现: `_day_yi_ji`（合并宜忌）与 `select()`（overall 用途判据输入）
        共用 —— 权威侧取数只此一处, 不得各自再读 getDayYi/getDayJi。
        """
        return (filter_yi_ji_sentinel(lunar.getDayYi()),
                filter_yi_ji_sentinel(lunar.getDayJi()))

    def _day_yi_ji(self, jianchu: str, lunar) -> tuple:
        """单日宜忌单一事实源（K3-A3 神煞级优先, k27 双向消解）—— 三面共用。

        宜 = 建除表宜（JIANCHU_YI_JI, 引擎在前） + lunar-python 当日神煞级黄历宜
             （lunar.getDayYi, 通胜逐日宜忌表）, 按序去重, 再剔除出现在最终忌中的词;
        忌 = 建除表忌 + lunar.getDayJi 按序去重后, 剔除全部出现在当日神煞级黄历宜
             中的词 —— K3-A3: 建除表忌为 12 日周期的粗粒度近似, 与神煞级明示之宜
             冲突时以当日黄历宜为准。

        实证（2026-08-30 择吉批 K3-A3, 权威 lhl/xzw 仲裁）:
        - 2026-10-01 闭日: 建除表忌 入宅/移徙, 但当日黄历宜=冠笄沐浴出行修造动土
          移徙入宅破土安葬, 且为权威搬家吉日 → 出行/入宅/移徙 从忌中移除;
        - 2026-10-14 同为闭日: 当日黄历宜无入宅/移徙 → 忌仍保留（对照不受影响）。
        同类方向冲突修复覆盖 chat 路径此前未接修正的 4 日（10-01 出行/入宅/移徙、
        2027-01-01 出行/移徙、10-14 嫁娶、09-20 安葬）。

        k27 补**反方向**消解（原只单向）: 「建除表宜 ∩ 当日黄历忌」此前无人处理,
        2026 全年 **75/365** 天把同一词同时列为宜与忌（如 2026-01-13 建除「开」
        表宜 嫁娶 ∩ 黄历忌 嫁娶）—— 用户在一次回答里同时看到「宜嫁娶」「忌嫁娶」。
        按 A 口径（对齐权威黄历, 产品 2026-09-11 拍板）: 冲突词以黄历为准归**忌**,
        从宜中剔除。权威侧自身 2020-2035 共 5844 天 getDayYi ∩ getDayJi = 0（无对应
        场景）, 故本规则不与 K3-A3 冲突; 「权威宜 ⊆ 我之宜」「权威宜 ∩ 我忌 = ∅」
        「权威忌 ∩ 我宜 = ∅」「yi ∩ ji = ∅」四条不变量 2026 全年 0 例外（见
        tests/test_zeri_consistency.py k27 段）。

        消费方（三面输出必须同源, 不得各自再合并/过滤）:
        - ``select()`` → ``ZeriResult.yi/ji``（chat/工具/意图路径）;
        - ``_build_lucky_card()`` → ``LuckyDayCard.yi/ji``（计划路径）;
        - ``wannianli.WannianliEngine.month_view/day_detail``（万年历面, k27 收敛）。

        哨兵值过滤（k23 补丁 F3, 单一实现 ``_authority_yi_ji``）: lunar-python 当日
        “无忌事/无宜事”时 getDayYi/getDayJi 返回哨兵 ``['无']``（2020-2035 扫描:
        忌 377 天、宜 10 天; 2026 年忌 13 天, 如 2026-02-10）—— 它不是事项词, 直接
        合并会让用户面出现「忌：无」。此处按侧过滤 ``无``; “诸事不宜/馀事勿取”是语义
        词, 不在此列（K3-A1 单独处理）。

        Args:
            jianchu: 建除十二神名（建/除/…/闭）
            lunar: 当日 lunar-python Lunar 对象
        Returns:
            (yi, ji) 两个已去重（且已滤哨兵）的字符串列表, 且 ``set(yi) & set(ji) == ∅``
            （由构造成立: 宜按最终忌集过滤; 建除表内部宜忌无交词, 见 k27 测试）
        """
        lunar_yi, lunar_ji = self._authority_yi_ji(lunar)
        lunar_yi_set = set(lunar_yi)
        # 忌侧（K3-A3 既有）: 建除表忌 + 黄历忌, 剔除当日黄历宜
        ji = [j for j in dict.fromkeys(
            list(JIANCHU_YI_JI[jianchu]["ji"]) + lunar_ji)
            if j not in lunar_yi_set]
        # 宜侧（k27 补反方向）: 建除表宜 + 黄历宜, 剔除最终忌集
        # —— 实际被剔的只有「建除表宜 ∩ (黄历忌 − 黄历宜)」; 权威宜与权威忌互斥
        #（2020-2035 实测 0 天）, 且建除表内部宜忌无交词, 故权威宜一项不失。
        ji_set = set(ji)
        yi = [y for y in dict.fromkeys(
            list(JIANCHU_YI_JI[jianchu]["yi"]) + lunar_yi)
            if y not in ji_set]
        return yi, ji

    def _match_purpose_category(self, purpose: str) -> Optional[str]:
        """用途 → 黄历类目词（PURPOSE_CATEGORIES 键, 如 "搬家"→"入宅"）; 无匹配 → None。

        单一实现: `_adjust_by_purpose`（排序/补位）与 `_judge_overall`（用途判据）
        共用, 不得各写一套匹配（同输入不同口径即分裂）。
        """
        for category, keywords in PURPOSE_CATEGORIES.items():
            for kw in keywords:
                if kw in purpose or purpose in kw:
                    return category
        return None

    def _adjust_by_purpose(self, purpose: str, yi: List[str], ji: List[str]) -> tuple:
        """根据用途调整宜忌列表"""
        matched_category = self._match_purpose_category(purpose)

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
        self, jianchu: str, purpose: str,
        lunar_yi: List[str], yi: List[str], ji: List[str],
    ) -> str:
        """综合判定吉凶（k27 三分, 与吉日卡片准入**同源**; 产品 2026-09-11 拍板选项 E）:

        - **凶**: 建除 破/危, 或 诸事不宜/馀事勿取（宜或忌）—— 逐条等同
          `_build_lucky_card` 的前置排除规则、同一输入列表（调用点在
          `_adjust_by_purpose` 之前, 与卡片消费的 `select()` 宜忌逐项相同）,
          故 **「计划路径出卡 ⟹ overall ≠ 凶」由构造成立**;
        - **吉**: 给了 purpose 且当日**神煞级黄历宜**（`_authority_yi_ji`）命中该用途
          —— 与卡片场景准入 `_scene_score(cfg, jianchu, lunar_yi)` 同一输入口径
          （K3-A5: 建除表宜仅展示、不驱动评分）;
        - **平**: 其余。

        被替换的 k23 语义（5.0 基准 + 建除 quality ±2 + 二十八宿 ±1 + 用途 ±1, 阈值
        6/3）与卡片判据无一处同维度: 2026-10-01 搬家 —— 卡片 total=64（权威吉日,
        建除「闭」已按 K3-A3 移出排除项、奎宿卡片不读）却被判「凶」(闭 −2 / 奎 −1 /
        用途 +1 = 3.0), 即用户可见的「卡片推荐吉日 ↔ chat 说凶」（现象 1）。
        不再驱动 overall 的两项及理由:
        - 建除 quality / 二十八宿吉凶: 卡片路径不消费; 且 `JIANCHU_QUALITY["危"]="吉"`
          与 K3 权威口径「排除危日」相抵, 属本批一并归位的口径错位;
        - 建除表宜: 仅展示不驱动评分（K3-A4/A5）。
        残留（已量化, 见 k27 报告）: 卡片准入另有神煞排除/冲生肖/scene 忌词命中,
        非单一 overall 可表达 —— 「overall=吉 但当日无卡」仍可能存在; 反方向
        （出卡 ⟹ 非凶）已由构造成立。

        Args:
            jianchu: 建除十二神名
            purpose: 事宜用途描述（可为空串）
            lunar_yi: 当日神煞级黄历宜（已滤哨兵）
            yi/ji: 合并后的宜/忌（`_day_yi_ji` 输出, 未按 purpose 调整）
        """
        if jianchu in ("破", "危") \
                or "诸事不宜" in ji or "馀事勿取" in ji or "馀事勿取" in yi:
            return "凶"
        if purpose:
            matched_category = self._match_purpose_category(purpose)
            if matched_category and matched_category in lunar_yi:
                return "吉"
        return "平"

    # ---- 择吉日: 多日扫描 + 三层评分 + Top3 ----

    def select_lucky_days(
        self,
        scene: str,
        start_date: str,
        end_date: str,
        user_bazi: Optional[dict] = None,
        exclude_dates: Optional[list] = None,
        prefer_weekend: bool = False,
    ) -> dict:
        """择吉日: 窗口逐日扫描 + 场景规则命中 + 冲煞排除 + 三层评分, 返回 Top3 吉日

        Args:
            scene: 场景名, 取 SCENES 的 key（嫁娶/搬家/开业/晋升/出行/提车/签约）
            start_date/end_date: 公历日期窗口 "YYYY-MM-DD"（含首尾, 可跨月/跨年）
            user_bazi: 用户八字信息（可选, 全缺则跳过个人分与冲生肖判定）:
                - shengxiao: 生肖, 如 "鼠" —— 冲生肖排除（搬家冲宅主/提车冲车主等）
                - yongshen:  用神五行, 如 "水"（直接给定, 优先）
                - wuxing/day_gan/month_zhi: 齐备时自动调 bazi 引擎 _calc_yongshen 计算用神
            exclude_dates: 排除日期列表 ["YYYY-MM-DD", ...]
            prefer_weekend: True 时周六/周日实用分 +10（节假日不判定）

        Returns:
            {"cards": [LuckyDayCard × ≤3], "scanned": N,
             "suggest_wider": bool, "reason": str|None}
            合格吉日不足 3 天 → suggest_wider=True, cards 如实返回 0-2 个,
            reason 为扩窗建议文本; 合格 ≥3 天 → reason=None。
        """
        if scene not in SCENES:
            raise ValueError(f"未知场景: {scene!r}, 可选: {list(SCENES.keys())}")
        cfg = SCENES[scene]
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as e:
            raise ValueError(f"日期格式须为 YYYY-MM-DD: {e}") from e
        if end < start:
            raise ValueError(f"end_date 早于 start_date: {start_date} > {end_date}")
        excluded = set(exclude_dates or [])

        cards: List[LuckyDayCard] = []
        scanned = 0
        d = start
        while d <= end:
            scanned += 1
            if d.isoformat() not in excluded:
                card = self._build_lucky_card(d, cfg, user_bazi, prefer_weekend)
                if card is not None:
                    cards.append(card)
            d += timedelta(days=1)

        cards.sort(key=lambda c: (-c.total, c.date))
        cards = cards[:3]
        suggest_wider = len(cards) < 3
        return {
            "cards": cards,
            "scanned": scanned,
            "suggest_wider": suggest_wider,
            "reason": None if not suggest_wider else (
                f"合格吉日不足3天（本窗口{scanned}天），建议扩大日期范围或调整偏好"
            ),
        }

    def _build_lucky_card(
        self,
        d,                          # datetime.date
        cfg: dict,
        user_bazi: Optional[dict],
        prefer_weekend: bool,
    ) -> Optional[LuckyDayCard]:
        """构建单日吉日卡片; 被排除或未达场景门槛 → None"""
        solar = Solar.fromYmd(d.year, d.month, d.day)
        lunar = solar.getLunar()
        ec = lunar.getEightChar()
        day_ganzhi = ec.getDay()
        day_gan, day_zhi = day_ganzhi[0], day_ganzhi[1]
        month_zhi = ec.getMonth()[1]
        lunar_month, lunar_day = lunar.getMonth(), lunar.getDay()

        # 复用已有引擎单日分析（不传 purpose, 保持自然宜忌）
        r = self.select(d.year, d.month, d.day)
        # 宜忌单一事实源: select() 已按 K3-A3(神煞级优先) 合并建除表 + 神煞级黄历宜忌
        # （见 _day_yi_ji）, 此处直接消费, 不得再自行合并/过滤（数据一致性铁律）。
        yi, ji = r.yi, r.ji
        # 场景准入评分输入（K3-A5）: 只算 lunar-python 当日神煞级黄历宜, 与上面宜忌
        # 事实源无关 —— 建除表宜仅展示、不驱动场景分（见 _scene_score）。
        # k26：这里同样不得绕过哨兵过滤（与 _day_yi_ji 同一实现/同一口径）——
        # 「宜侧哨兵日」getDayYi()==['无']，未过滤即把哨兵当评分输入（同一事实源
        # 两套口径；实测该日被排除规则提前拦下故用户侧无可见差异，属口径收口）。
        lunar_yi = filter_yi_ji_sentinel(lunar.getDayYi())

        # ---- 排除规则 ----
        # K3-A1: 诸事不宜/馀事勿取日直接排除 —— 权威判定标准「排除破日、危日与
        # 诸事不宜之日」, 此类日不得报为任何场景吉日（12/12 忌=诸事不宜、
        # 11/7 宜=解除+馀事勿取、9/20 宜=…馀事勿取 实证）。
        if "诸事不宜" in ji or "馀事勿取" in ji or "馀事勿取" in yi:
            return None
        if r.jianchu in cfg["jianchu_avoid"]:
            return None
        if any(kw in j for j in ji for kw in cfg["ji_hits"]):
            return None
        lm = abs(lunar_month)  # 闰月按同月数查表
        for rule in cfg["shensha_avoid"]:
            if rule == "三娘煞" and is_sanniang_sha(lm, lunar_day):
                return None
            elif rule == "杨公忌日" and is_yanggong_ji(lm, lunar_day):
                return None
            elif rule == "月破" and is_yuepo(day_zhi, month_zhi):
                return None
            elif rule == "月刑" and is_yuexing(day_zhi, month_zhi):
                return None
            elif rule == "空亡" and is_kongwang(month_zhi, lunar):
                return None
        # 冲 user 生肖（按场景开关 avoid_chong; 无生肖信息则跳过, 不误伤）
        user_zodiac = (user_bazi or {}).get("shengxiao")
        if cfg.get("avoid_chong") and user_zodiac \
                and ZODIAC_MAP[LIU_CHONG[day_zhi]] == user_zodiac:
            return None

        # ---- 三层评分 ----
        # K3-A4/A5 补完(神煞级优先): 场景命中只算 lunar-python 当日神煞级黄历宜,
        # 不算建除表宜 —— 权威对比口径为「以黄历当日明确列出为准入」(A5),
        # 建除表词与神煞级完整黄历不符处均可能误报 (11/7 开表词、11/17 成表词、
        # 12/8 定表词 实证, 报告 A4 同类风险)。建除表宜仍保留在卡片宜列表作展示。
        scene_score, scene_reason = self._scene_score(cfg, r.jianchu, lunar_yi)
        if scene_score < 20:
            # 未命中任何场景宜关键词 → 不构成合格吉日
            return None
        personal_score, personal_reason = self._personal_score(day_gan, user_bazi)
        practical_score, practical_reason = self._practical_score(d, cfg, prefer_weekend)

        parts = [
            (scene_score, scene_reason),
            (personal_score, personal_reason),
            (practical_score, practical_reason),
        ]
        # 最高分项; 平分时按 场景 > 个人 > 实用
        reason_source = max(parts, key=lambda p: (p[0], -parts.index(p)))[1]

        return LuckyDayCard(
            date=d.isoformat(),
            lunar_text=self._lunar_text(solar, lunar_month, lunar_day, day_ganzhi),
            yi=yi,
            ji=ji,
            jishi=self._jishi(lunar),
            xi_fangwei=lunar.getDayPositionXiDesc(),
            cai_fangwei=lunar.getDayPositionCaiDesc(),
            scene_score=scene_score,
            personal_score=personal_score,
            practical_score=practical_score,
            total=scene_score + personal_score + practical_score,
            reason_source=reason_source,
        )

    def _scene_score(self, cfg: dict, jianchu: str, yi: List[str]) -> tuple:
        """场景匹配分(0-50): 宜关键词每命中 +20, 成/开/定值日 +10, 封顶 50

        K3(神煞级优先): yi 实参为 lunar-python 当日神煞级黄历宜（_build_lucky_card
        传入 lunar_yi）, 建除表宜不参与场景分（仅展示）——权威「以黄历当日明确
        列出为准入」; 值日加分仍看建除十二神（成/开/定）。
        """
        hits = [kw for kw in cfg["yi_hits"] if any(kw in y for y in yi)]
        bonus = 10 if jianchu in ("成", "开", "定") else 0
        score = min(50, len(hits) * 20 + bonus)
        if jianchu in ("成", "开", "定"):
            reason = f"{jianchu}日值日"
        elif hits:
            reason = f"宜{'、'.join(hits)}"
        else:
            reason = cfg["label"]
        return score, reason

    def _personal_score(self, day_gan: str, user_bazi: Optional[dict]) -> tuple:
        """个人适配分(0-30):
        - 无八字 → 24（默认满分折算 80%）
        - 有八字: 用神五行 = user_bazi["yongshen"] 直接给定, 或 wuxing/day_gan/month_zhi
          齐备时调 bazi 引擎 _calc_yongshen 计算（可选依赖, 失败回退 24）
          当日天干五行生用神 +10(25) / 比和 +5(20) / 无关 15
        - 冲生肖排除在 _build_lucky_card 统一处理
        """
        if not user_bazi:
            return 24, "基础适配分"
        yongshen = user_bazi.get("yongshen")
        if not yongshen:
            wx = user_bazi.get("wuxing")
            dg = user_bazi.get("day_gan")
            mz = user_bazi.get("month_zhi")
            if wx and dg and mz:
                try:
                    from src.engines.bazi import BaziEngine
                    out = BaziEngine()._calc_yongshen(wx, dg, mz)
                    cand = out[0] if out else ""
                    yongshen = cand if cand in "金木水火土" else None
                except Exception:
                    yongshen = None
        if not yongshen:
            return 24, "基础适配分"
        day_wx = TIAN_GAN_WUXING.get(day_gan, "")
        if day_wx and SHENG_CYCLE.get(day_wx) == yongshen:
            return 25, "喜用神相合"
        if day_wx == yongshen:
            return 20, "喜用神比和"
        return 15, "八字适配"

    def _practical_score(self, d, cfg: dict, prefer_weekend: bool) -> tuple:
        """实用加分(0-20): prefer_weekend 且 cfg['weekend_bonus'] 且周六/周日 +10;
        节假日不判定（不做人为拔高）。

        fix-later: 接入 weekend_bonus 配置键 —— 场景级开关关闭时周末不加分。
        当前 6 场景 weekend_bonus 全为 True, 行为不变, 消除死配置。
        """
        if not prefer_weekend or not cfg.get("weekend_bonus"):
            return 0, "平日无加分"
        if d.weekday() in (5, 6):   # 周六=5 周日=6
            return 10, f"周末宜{cfg['label']}"
        return 0, "平日无加分"

    @staticmethod
    def _jishi(lunar) -> str:
        """吉时段: 12 时辰黄道（lunar-python LunarTime 黄道黑道）取前 3 个吉时,
        时辰时段用标准十二时辰表（如 巳时 9-11点）;
        无黄道吉时 → '吉时以当日黄历为准'（宁缺毋滥, 不自行编造时段）"""
        SHICHEN_HOURS = {
            0: "23-1点", 1: "1-3点", 2: "3-5点", 3: "5-7点", 4: "7-9点",
            5: "9-11点", 6: "11-13点", 7: "13-15点", 8: "15-17点",
            9: "17-19点", 10: "19-21点", 11: "21-23点",
        }
        seen = set()
        good = []
        for t in lunar.getTimes():
            if t.getTianShenType() == "黄道":
                idx = t.getZhiIndex()
                if idx in seen:
                    continue
                seen.add(idx)
                good.append(f"{DIZHI[idx]}时 {SHICHEN_HOURS[idx]}")
            if len(good) >= 3:
                break
        return "、".join(good) if good else "吉时以当日黄历为准"

    @staticmethod
    def _lunar_text(solar, lunar_month: int, lunar_day: int, day_ganzhi: str) -> str:
        """农历文本: 农历[闰]X月X日 干支 星期X"""
        lm = abs(lunar_month)
        prefix = "闰" if lunar_month < 0 else ""
        month_cn = _LUNAR_MONTH_CN.get(lm, str(lm))
        return f"农历{prefix}{month_cn}月{_lunar_day_cn(lunar_day)}日 " \
               f"{day_ganzhi} 星期{solar.getWeekInChinese()}"


# ============================================================
# 择吉日（Task 1）: 场景规则库 + 三层评分 + 多日 Top3 扫描
#
# 神煞依据（宁缺毋滥: 能确定性计算的才排除, 其余不排除并注明）:
#   - 三娘煞: 农历每月初三、初七、十三、十八、廿二、廿七（标准公历表）
#   - 杨公忌日: 正月十三、二月十一、三月初九、四月初七、五月初五、六月初三、
#               七月初一与廿九、八月廿七、九月廿五、十月廿三、十一月廿一、十二月十九
#   - 月破: 日支与月支六冲（LIU_CHONG, 与建除"破"同日, 传统一致）
#   - 月刑: 支三刑表（无恩/恃势/无礼/自刑）: 月支刑日支
#   - 空亡: 当日日柱旬空（lunar-python getDayXunKong）含当月月支 → 月建逢空为空亡日。
#           注: 日柱地支不可能落入自身旬空（旬空是旬内未出现的两支）, 故取"月建逢空"
#           这一可确定性计算的黄历视角; 其余空亡流派规则不纳入。
#   - 冲生肖: 日支冲 user_bazi 生肖时排除（无生肖信息则跳过该判定, 不误伤）
# 宜忌数据: 复用建除十二神宜忌 + lunar-python 黄历 getDayYi/getDayJi 合并（引擎在前）,
#           保证与当日黄历一致且可离线确定性计算。合并与 K3-A3 神煞级优先冲突消解
#           统一在 ZeriEngine._day_yi_ji()（chat/工具路径与计划路径单一事实源）。
# 吉时/方位: 均取 lunar-python 内建数据（LunarTime 黄道黑道 / getDayPositionXi|CaiDesc）。
# ============================================================

# 三娘煞: 农历每月初三、初七、十三、十八、廿二、廿七
SANNIANG_SHA_DAYS = {3, 7, 13, 18, 22, 27}

# 杨公忌日（农历月: 日; 七月有初一与廿九两天）
YANGGONG_JI_DAYS = {
    1: {13}, 2: {11}, 3: {9}, 4: {7}, 5: {5}, 6: {3},
    7: {1, 29}, 8: {27}, 9: {25}, 10: {23}, 11: {21}, 12: {19},
}

# 支三刑（无恩之刑/恃势之刑/无礼之刑/自刑）
XING_MAP = {
    "寅": "巳", "巳": "申", "申": "寅",
    "丑": "戌", "戌": "未", "未": "丑",
    "子": "卯", "卯": "子",
    "辰": "辰", "午": "午", "酉": "酉", "亥": "亥",
}

# 十天干五行
TIAN_GAN_WUXING = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}

# 五行相生: 木→火→土→金→水→木
SHENG_CYCLE = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}

# 场景规则库
#   yi_hits:      宜关键词（当日宜列表命中 → 加场景分）
#   ji_hits:      忌关键词（当日忌列表命中 → 直接排除; 与 yi_hits 同词即自相矛盾, 不出现）
#   jianchu_avoid: 建除神当日排除 —— K3-A2/A3: 全场景统一排除 破/危 两日
#                 (权威判定标准「排除破日、危日与诸事不宜之日」, 11/16 危日出行误报实证);
#                 闭日不再排除 (权威不排除闭日, 10/1 闭日神煞级宜入宅移徙为搬家吉日实证)
#   avoid_chong:  冲 user_bazi 生肖排除（独立条目, 按场景开关: 搬家冲宅主/提车冲车主;
#                 无生肖信息时跳过; 不混入 shensha_avoid）
#   shensha_avoid: 神煞排除（三娘煞/杨公忌日/月破/月刑/空亡; 冲生肖已独立为 avoid_chong 条目）
#   weekend_bonus: 周末加分开关（prefer_weekend 且 weekend_bonus 才加分; 当前 7 场景全开）
SCENES = {
    "嫁娶": {
        "label": "嫁娶",
        "yi_hits": ["嫁娶", "订盟", "纳采"],
        "ji_hits": ["嫁娶", "纳采", "订盟"],   # fix-later: 补"订盟"与 yi 对称(订盟日不宜嫁娶, 黄历有据)
        "jianchu_avoid": ["破", "危"],
        "avoid_chong": True,
        "shensha_avoid": ["三娘煞", "杨公忌日"],
        "weekend_bonus": True,
    },
    "搬家": {
        "label": "搬家",
        "yi_hits": ["入宅", "移徙", "安床"],
        "ji_hits": ["入宅", "移徙"],
        "jianchu_avoid": ["破", "危"],
        "avoid_chong": True,      # 冲宅主生肖（用 user_bazi 生肖）
        "shensha_avoid": [],
        "weekend_bonus": True,
    },
    "开业": {
        "label": "开业",
        "yi_hits": ["开市", "交易", "纳财"],
        "ji_hits": ["开市", "纳财"],
        "jianchu_avoid": ["破", "危"],
        "avoid_chong": False,
        "shensha_avoid": ["月破", "月刑"],
        "weekend_bonus": True,
    },
    "晋升": {
        "label": "晋升",
        "yi_hits": ["祈福", "会亲友", "出行", "入学"],   # 四词均已核实在宜词表存在（建除宜表+黄历宜）
        "ji_hits": [],
        "jianchu_avoid": ["破", "危"],
        "avoid_chong": False,
        "shensha_avoid": ["月破", "月刑"],
        "weekend_bonus": True,
    },
    "出行": {
        # K3-A5: 只认"出行" —— 权威标准「出行吉日必须以黄历当日明确列出"出行"为准入」,
        # 会亲友/祈福 不等于出行 (12/5 宜会亲友安机械 出行误报实证)。
        "label": "出行",
        "yi_hits": ["出行"],
        "ji_hits": [],   # fix-later: 去掉"出行" —— 与 yi_hits 同词自相矛盾; 实际忌词为空
        "jianchu_avoid": ["破", "危"],
        "avoid_chong": False,
        "shensha_avoid": ["空亡"],
        "weekend_bonus": True,
    },
    "提车": {
        "label": "提车",
        "yi_hits": ["祈福", "出行", "安机械"],
        "ji_hits": [],   # fix-later: 去掉"出行" —— 与 yi_hits 同词自相矛盾; 实际忌词为空
        "jianchu_avoid": ["破", "危"],
        "avoid_chong": True,      # 冲车主生肖
        "shensha_avoid": [],
        "weekend_bonus": True,
    },
    "签约": {
        "label": "签约",
        "yi_hits": ["交易", "订盟", "纳财"],
        "ji_hits": ["交易", "纳财"],
        "jianchu_avoid": ["破", "危"],   # K3-A2: 补齐危日(此前为空); 破日与月破同日, 语义不变
        "avoid_chong": False,
        "shensha_avoid": ["月破", "月刑"],   # 忌日月刑冲
        "weekend_bonus": True,
    },
}

# 农历数字/月名（用于 lunar_text）
_LUNAR_MONTH_CN = {1: "正", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
                   7: "七", 8: "八", 9: "九", 10: "十", 11: "冬", 12: "腊"}
_CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}


def _lunar_day_cn(day: int) -> str:
    """农历日中文: 初一..初十 / 十一..十九 / 二十 / 廿一..廿九 / 三十"""
    if day == 10:
        return "初十"
    if day == 20:
        return "二十"
    if day == 30:
        return "三十"
    if day < 10:
        return "初" + _CN_NUM[day]
    if day < 20:
        return "十" + _CN_NUM[day % 10]
    if day < 30:
        return "廿" + _CN_NUM[day % 10]
    return str(day)


def is_sanniang_sha(lunar_month: int, lunar_day: int) -> bool:
    """三娘煞: 农历每月初三、初七、十三、十八、廿二、廿七（闰月按同月数）"""
    return lunar_day in SANNIANG_SHA_DAYS


def is_yanggong_ji(lunar_month: int, lunar_day: int) -> bool:
    """杨公忌日: 正月十三、二月十一、三月初九、四月初七、五月初五、六月初三、
    七月初一与廿九、八月廿七、九月廿五、十月廿三、十一月廿一、十二月十九"""
    return lunar_day in YANGGONG_JI_DAYS.get(lunar_month, set())


def is_yuepo(day_zhi: str, month_zhi: str) -> bool:
    """月破: 日支与月支六冲"""
    return day_zhi == LIU_CHONG.get(month_zhi)


def is_yuexing(day_zhi: str, month_zhi: str) -> bool:
    """月刑: 月支刑日支（支三刑: 无恩/恃势/无礼/自刑）"""
    return XING_MAP.get(month_zhi) == day_zhi


def is_kongwang(month_zhi: str, lunar) -> bool:
    """空亡: 当日日柱旬空（lunar-python getDayXunKong）含当月月支 → 月建逢空

    说明: 日柱地支不可能落入自身旬空（旬空为旬内未出现的两支）, 故采用
    "月建逢空"这一可确定性计算的黄历视角; 其他空亡流派规则不纳入（宁缺毋滥）。
    注(fix-later): 原签名带 day_zhi 死参数(函数体从未使用), 已移除。
    """
    return month_zhi in lunar.getEightChar().getDayXunKong()



