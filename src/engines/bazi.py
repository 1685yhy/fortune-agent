"""八字排盘引擎 - 基于 lunar-python."""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from lunar_python import Lunar, Solar

from src.engines.shensha import shensha_of

TIANGAN = ["甲","乙","丙","丁","戊","己","庚","辛","壬","癸"]
DIZHI = ["子","丑","寅","卯","辰","巳","午","未","申","酉","戌","亥"]
WUXING_TG = {"甲":"木","乙":"木","丙":"火","丁":"火","戊":"土","己":"土","庚":"金","辛":"金","壬":"水","癸":"水"}
WUXING_DZ = {"子":"水","丑":"土","寅":"木","卯":"木","辰":"土","巳":"火","午":"火","未":"土","申":"金","酉":"金","戌":"土","亥":"水"}
SHENG_XU = ["甲子","乙丑","丙寅","丁卯","戊辰","己巳","庚午","辛未","壬申","癸酉",
            "甲戌","乙亥","丙子","丁丑","戊寅","己卯","庚辰","辛巳","壬午","癸未",
            "甲申","乙酉","丙戌","丁亥","戊子","己丑","庚寅","辛卯","壬辰","癸巳",
            "甲午","乙未","丙申","丁酉","戊戌","己亥","庚子","辛丑","壬寅","癸卯",
            "甲辰","乙巳","丙午","丁未","戊申","己酉","庚戌","辛亥","壬子","癸丑",
            "甲寅","乙卯","丙辰","丁巳","戊午","己未","庚申","辛酉","壬戌","癸亥"]
SHENG_XU_MAP = {v:i for i,v in enumerate(SHENG_XU)}

# 十神关系（天干五合 + 生克）
SHISHEN_NAMES = {
    "same": "比肩", "same_yin": "劫财",  # 同我
    "me_sheng": "食神", "me_sheng_yin": "伤官",  # 我生
    "sheng_me": "偏印", "sheng_me_yin": "正印",  # 生我
    "me_ke": "偏财", "me_ke_yin": "正财",  # 我克
    "ke_me": "七杀", "ke_me_yin": "正官",  # 克我
}

# 六十甲子纳音（简化）
NAYIN = {
    "甲子":"海中金","乙丑":"海中金","丙寅":"炉中火","丁卯":"炉中火",
    "戊辰":"大林木","己巳":"大林木","庚午":"路旁土","辛未":"路旁土",
    "壬申":"剑锋金","癸酉":"剑锋金","甲戌":"山头火","乙亥":"山头火",
    "丙子":"涧下水","丁丑":"涧下水","戊寅":"城头土","己卯":"城头土",
    "庚辰":"白蜡金","辛巳":"白蜡金","壬午":"杨柳木","癸未":"杨柳木",
    "甲申":"泉中水","乙酉":"泉中水","丙戌":"屋上土","丁亥":"屋上土",
    "戊子":"霹雳火","己丑":"霹雳火","庚寅":"松柏木","辛卯":"松柏木",
    "壬辰":"长流水","癸巳":"长流水","甲午":"沙中金","乙未":"沙中金",
    "丙申":"山下火","丁酉":"山下火","戊戌":"平地木","己亥":"平地木",
    "庚子":"壁上土","辛丑":"壁上土","壬寅":"金箔金","癸卯":"金箔金",
    "甲辰":"覆灯火","乙巳":"覆灯火","丙午":"天河水","丁未":"天河水",
    "戊申":"大驿土","己酉":"大驿土","庚戌":"钗钏金","辛亥":"钗钏金",
    "壬子":"桑柘木","癸丑":"桑柘木","甲寅":"大溪水","乙卯":"大溪水",
    "丙辰":"沙中土","丁巳":"沙中土","戊午":"天上火","己未":"天上火",
    "庚申":"石榴木","辛酉":"石榴木","壬戌":"大海水","癸亥":"大海水",
}

@dataclass
class BaziResult:
    bazi: List[str]       # ["庚午","辛巳","乙酉","甲申"]
    day_master: str       # "乙木"
    wuxing: Dict[str,int] # {"金":3,"木":2,...}
    shishen: List[str]    # ["正官","七杀","日主","劫财"]
    dayun: List[tuple]    # [(start_age, ganzhi),...]
    liunian: Dict[str,str]  # {"2026":"丙午"}
    geju: str             # "正官格"
    yongshen: str         # "水木"
    shensha: List[str]    # ["天乙贵人","驿马"]（年日两局查表+计算，去重）
    nayin: List[str]      # 纳音
    gender: str = ""      # P1-3: 原始性别，可能为 "unknown"
    shensha_detail: List[dict] = field(default_factory=list)  # [{name,source,luck},...]
    raw_data: dict = field(default_factory=dict)


class BaziEngine:
    """八字排盘引擎"""

    def calculate(self, year: int, month: int, day: int,
                  hour: int, minute: int, city: str,
                  gender: str) -> BaziResult:
        """排八字命盘

        P1-3: 当 gender 为 "unknown" 时，默认按男排盘（大运顺排），
        但在结果中标注性别未知。
        """
        # P1-3: Handle unknown gender — default to 男 for calculation
        calc_gender = gender if gender in ("男", "女") else "男"
        # 处理晚子时 (23:00-23:59): 使用次日日期, 时柱仍为子时
        # 起运距离用真实出生时刻计算（与问真口径一致），故保留原始时间
        orig_birth = (year, month, day, hour, minute)
        if hour >= 23:
            from datetime import datetime as dt, timedelta
            d = dt(year, month, day) + timedelta(days=1)
            year, month, day = d.year, d.month, d.day
            hour = 0
            minute = 0
        solar = Solar.fromYmdHms(year, month, day, hour, minute, 0)
        lunar = solar.getLunar()
        eight_char = lunar.getEightChar()

        # 四柱
        bazi_pillars = [
            eight_char.getYear(),
            eight_char.getMonth(),
            eight_char.getDay(),
            eight_char.getTime(),
        ]

        # 天干地支拆分
        year_gan = bazi_pillars[0][0]
        year_zhi = bazi_pillars[0][1]
        month_gan = bazi_pillars[1][0]
        month_zhi = bazi_pillars[1][1]
        day_gan = bazi_pillars[2][0]
        day_zhi = bazi_pillars[2][1]
        time_gan = bazi_pillars[3][0]
        time_zhi = bazi_pillars[3][1]

        all_gan = [year_gan, month_gan, day_gan, time_gan]
        all_zhi = [year_zhi, month_zhi, day_zhi, time_zhi]

        # 日主
        day_master = f"{day_gan}{WUXING_TG[day_gan]}"

        # 五行统计
        wuxing = {"金":0,"木":0,"水":0,"火":0,"土":0}
        for g in all_gan:
            wuxing[WUXING_TG[g]] += 1
        for z in all_zhi:
            wuxing[WUXING_DZ[z]] += 1

        # 十神
        shishen = []
        for g in all_gan:
            shishen.append(self._calc_shishen(day_gan, g))
        shishen[2] = "日主"  # 日柱天干就是日主自己

        # 纳音
        nayin = []
        for p in bazi_pillars:
            nayin.append(NAYIN.get(p, ""))

        # 大运
        dayun = self._calc_dayun(lunar, calc_gender, bazi_pillars, orig_birth)

        # 流年（简化：使用 lunar-python 或计算）
        liunian = self._calc_liunian(lunar, day_gan)

        # 格局（简化版：取月支藏干透出）
        geju = self._calc_geju(month_zhi, month_gan, all_gan)

        # 用神（调候优先 + 扶抑辅助）
        yongshen = self._calc_yongshen(wuxing, day_gan, month_zhi)

        # 神煞（60甲子速查表 29 种：年柱+日柱两局并查 + 按日干计算，问真数据基准）
        shensha_items = shensha_of(
            year_pillar=bazi_pillars[0],
            day_pillar=bazi_pillars[2],
            all_gan=all_gan,
            all_zhi=all_zhi,
        )
        shensha = [item.name for item in shensha_items]
        shensha_detail = [item.__dict__ for item in shensha_items]

        return BaziResult(
            bazi=bazi_pillars,
            day_master=day_master,
            wuxing=wuxing,
            shishen=shishen,
            dayun=dayun,
            liunian=liunian,
            geju=geju,
            yongshen=yongshen,
            shensha=shensha,
            shensha_detail=shensha_detail,
            nayin=nayin,
            gender=gender,
        )

    def _calc_shishen(self, day_gan: str, target_gan: str) -> str:
        """计算十神关系"""
        dg_idx = TIANGAN.index(day_gan)
        tg_idx = TIANGAN.index(target_gan)
        if dg_idx == tg_idx:
            return "比肩"

        dg_wx = WUXING_TG[day_gan]
        tg_wx = WUXING_TG[target_gan]

        if dg_wx == tg_wx:
            return "劫财"

        dg_yin = dg_idx % 2  # 0=阳,1=阴
        tg_yin = tg_idx % 2
        same_yin = (dg_yin == tg_yin)

        # 生克循环：木→火→土→金→水→木
        sheng_cycle = {"木":"火","火":"土","土":"金","金":"水","水":"木"}
        ke_cycle = {"木":"土","土":"水","水":"火","火":"金","金":"木"}

        if sheng_cycle.get(dg_wx) == tg_wx:
            return "食神" if same_yin else "伤官"
        elif sheng_cycle.get(tg_wx) == dg_wx:
            return "偏印" if same_yin else "正印"
        elif ke_cycle.get(dg_wx) == tg_wx:
            return "偏财" if same_yin else "正财"
        elif ke_cycle.get(tg_wx) == dg_wx:
            return "七杀" if same_yin else "正官"

        return "比肩"  # fallback

    def _calc_dayun(self, lunar, gender: str, bazi: list, orig_birth: tuple = None) -> list:
        """计算大运起运和排列 — 标准排盘算法（与问真八字对齐）

        规则: 阳年男/阴年女 → 顺排, 阴年男/阳年女 → 逆排
        起运年龄: 标准节气距离算法（三天折一年），虚岁口径与问真 qiyunsui 一致
        """
        return self._calc_dayun_improved(lunar, gender, orig_birth)

    def _calc_qiyun_start_age(self, year: int, month: int, day: int,
                              hour: int, minute: int, lunar, gender: str) -> int:
        """标准起运算法（经典排盘算法，对齐问真八字 qiyunsui 口径）

        1. 方向：阳男阴女顺排（数至下一个节），阴男阳女逆排（数至上一个节）
        2. 时长：出生时刻到目标节交节时刻的间隔（节气时刻精确到分钟，取自 lunar-python 节气表）
        3. 换算：3天=1岁，1天=4个月，1个时辰(2h)=10天 → 精确岁数 = 时长(天) / 3
        4. 分解为 年/月/日/时/分 后按日历加回出生时刻 → 起运日期
        5. 起运虚岁 = 起运日期所在年份 - 出生年份 + 1（问真 qiyunsui 口径）

        经问真 API 校准：280/280 (100%) 一致。
        """
        import calendar
        from datetime import datetime as dt, timedelta

        birth = dt(year, month, day, hour, minute)
        # 方向：以年柱天干阴阳 + 性别决定（年柱与四柱同源）
        year_gan = lunar.getEightChar().getYear()[0]
        is_yang = TIANGAN.index(year_gan) % 2 == 0  # 甲丙戊庚壬为阳
        is_male = gender == "男"
        forward = (is_male and is_yang) or (not is_male and not is_yang)

        # 节气时刻（精确到分）：顺排取下一节，逆排取上一节（12节，非中气）
        solar = Solar.fromYmdHms(year, month, day, hour, minute, 0)
        jie = solar.getLunar().getNextJie() if forward else solar.getLunar().getPrevJie()
        jt = jie.getSolar()
        jie_time = dt(jt.getYear(), jt.getMonth(), jt.getDay(), jt.getHour(), jt.getMinute())
        dist_days = (jie_time - birth).total_seconds() / 86400.0
        if dist_days < 0:  # 防御：取错方向时反转
            dist_days = -dist_days

        # 换算：3天=1岁；分解为 年/月/日/时/分
        years_exact = dist_days / 3.0
        yy = int(years_exact)
        rem_month = (years_exact - yy) * 12
        mm = int(rem_month)
        rem_day = (rem_month - mm) * 30
        dd = int(rem_day)
        rem_hour = (rem_day - dd) * 24
        hh = int(rem_hour)
        mi = int(round((rem_hour - hh) * 60))
        if mi >= 60:
            mi = 59

        # 按日历相加：先加整年整月（日对齐到目标月有效天数），再加日时分
        months = yy * 12 + mm
        y2 = birth.year + (birth.month - 1 + months) // 12
        m2 = (birth.month - 1 + months) % 12 + 1
        day2 = min(birth.day, calendar.monthrange(y2, m2)[1])
        qy = dt(y2, m2, day2, birth.hour, birth.minute) + timedelta(days=dd, hours=hh, minutes=mi)

        return qy.year - birth.year + 1  # 虚岁

    def _calc_dayun_improved(self, lunar, gender: str, orig_birth: tuple = None) -> list:
        """大运计算 — 起运年龄用标准节气距离算法，方向按阳男阴女顺排/阴男阳女逆排"""
        try:
            if orig_birth:
                start_age = self._calc_qiyun_start_age(
                    orig_birth[0], orig_birth[1], orig_birth[2],
                    orig_birth[3], orig_birth[4], lunar, gender)
            else:
                # 兜底：无原始时间时用 lunar 自算起运（周岁）+1 转虚岁
                yun_gender = 0 if gender == "男" else 1
                yun = lunar.getEightChar().getYun(yun_gender)
                start_age = yun.getStartYear() + 1
        except Exception:
            start_age = 5

        # Determine direction
        year_gan = lunar.getEightChar().getYear()[0]
        year_gan_idx = TIANGAN.index(year_gan)
        is_yang = year_gan_idx % 2 == 0  # 甲丙戊庚壬为阳
        is_male = gender == "男"

        # 阳男阴女→顺排, 阴男阳女→逆排
        forward = (is_male and is_yang) or (not is_male and not is_yang)

        month_ganzhi = lunar.getEightChar().getMonth()
        month_gan_idx = TIANGAN.index(month_ganzhi[0])
        month_zhi_idx = DIZHI.index(month_ganzhi[1])

        dayun = []
        for i in range(12):
            if forward:
                gan_idx = (month_gan_idx + i + 1) % 10
                zhi_idx = (month_zhi_idx + i + 1) % 12
            else:
                gan_idx = (month_gan_idx - i - 1) % 10
                zhi_idx = (month_zhi_idx - i - 1) % 12
            dayun.append((start_age + i * 10, TIANGAN[gan_idx] + DIZHI[zhi_idx]))

        return dayun

    def _fallback_dayun(self, lunar, gender: str) -> list:
        """简化大运计算（备选方案）"""
        month_ganzhi = lunar.getEightChar().getMonth()
        month_gan_idx = TIANGAN.index(month_ganzhi[0])
        month_zhi_idx = DIZHI.index(month_ganzhi[1])

        is_male = gender == "男"
        year_gan = lunar.getEightChar().getYear()[0]
        year_gan_idx = TIANGAN.index(year_gan)
        is_yang = year_gan_idx % 2 == 0  # 甲丙戊庚壬为阳

        forward = (is_male and is_yang) or (not is_male and not is_yang)

        dayun = []
        start_age = 5  # 简化起运年龄
        for i in range(10):
            if forward:
                gan_idx = (month_gan_idx + i + 1) % 10
                zhi_idx = (month_zhi_idx + i + 1) % 12
            else:
                gan_idx = (month_gan_idx - i - 1) % 10
                zhi_idx = (month_zhi_idx - i - 1) % 12
            dayun.append((start_age + i * 10, TIANGAN[gan_idx] + DIZHI[zhi_idx]))

        return dayun

    def _calc_liunian(self, lunar, day_gan: str) -> dict:
        """计算流年（简化版：取当年干支）"""
        liunian = {}
        # 取当前年及附近年份（以出生年为基准）
        try:
            current_year = int(lunar.getYear())
            base_year = current_year if current_year else 2026
        except Exception:
            base_year = 2026

        for y in range(base_year - 2, base_year + 3):
            # 手动推算：2024=甲辰年（甲=0,辰=4）为基准
            diff = y - 2024
            gan_idx = (diff) % 10  # 甲=idx 0, 2024=甲
            zhi_idx = (4 + diff) % 12  # 辰=idx 4, 2024=辰
            liunian[str(y)] = TIANGAN[gan_idx] + DIZHI[zhi_idx]
        return liunian

    def _calc_geju(self, month_zhi: str, month_gan: str, all_gan: list) -> str:
        """格局判定：以月干十神定格局（正统子平法）"""
        # 正统格局以月令天干透出十神定
        # 先取月干对日干的十神关系作为主格
        day_gan = all_gan[2]

        # 十神→格局映射
        shishen_to_geju = {
            "正官": "正官格", "七杀": "七杀格", "正财": "正财格", "偏财": "偏财格",
            "正印": "正印格", "偏印": "偏印格", "食神": "食神格", "伤官": "伤官格",
            "比肩": "建禄格", "劫财": "月刃格",
        }

        month_shishen = self._calc_shishen(day_gan, month_gan)
        if month_shishen in shishen_to_geju:
            return shishen_to_geju[month_shishen]

        # 如果月干不透（比如比肩劫财），看月支藏干
        canggan_map = {
            "子":"癸","丑":"己","寅":"甲","卯":"乙","辰":"戊",
            "巳":"丙","午":"丁","未":"己","申":"庚","酉":"辛",
            "戌":"戊","亥":"壬",
        }
        cang_gan = canggan_map.get(month_zhi, "")
        if cang_gan:
            cang_shishen = self._calc_shishen(day_gan, cang_gan)
            if cang_shishen in shishen_to_geju:
                return shishen_to_geju[cang_shishen]

        return "普通格"

    def _calc_yongshen(self, wuxing: dict, day_gan: str, month_zhi: str = "") -> str:
        """用神判定：调候优先 + 扶抑辅助"""
        day_wx = WUXING_TG[day_gan]

        sheng_cycle = {"木":"火","火":"土","土":"金","金":"水","水":"木"}
        ke_cycle = {"木":"土","土":"水","水":"火","火":"金","金":"木"}
        sheng_wo = {v:k for k,v in sheng_cycle.items()}
        ke_wo = {v:k for k,v in ke_cycle.items()}

        # 0. 调候（季节温度调节）
        tiaohou = None
        is_extreme_season = False
        # 夏季(巳午未) → 水调候降温（极端季节：调候优先）
        if month_zhi in ("巳", "午", "未"):
            tiaohou = "水"
            is_extreme_season = True
        # 冬季(亥子丑) → 火调候暖局（极端季节：调候优先）
        elif month_zhi in ("亥", "子", "丑"):
            tiaohou = "火"
            is_extreme_season = True
        # 秋季(申酉戌) → 金旺，用火制金
        elif month_zhi in ("申", "酉", "戌"):
            tiaohou = "火"
        # 春季(寅卯辰) → 木旺，用金修剪
        elif month_zhi in ("寅", "卯", "辰"):
            tiaohou = "金"

        # 1. 扶抑分析
        # 生扶 = 比劫(wx) + 印星(sheng_wo[wx])
        support_wx = [day_wx, sheng_wo.get(day_wx, "")]
        support = sum(wuxing.get(w, 0) for w in support_wx if w)
        # 克泄 = 官杀(ke_wo[wx]) + 食伤(sheng_cycle[wx]) + 财
        suppress_wx = [ke_wo.get(day_wx, ""), sheng_cycle.get(day_wx, "")]
        suppress = sum(wuxing.get(w, 0) for w in suppress_wx if w)

        if support > suppress:
            priorities = [ke_wo.get(day_wx, ""), sheng_cycle.get(day_wx, "")]
        else:
            priorities = [sheng_wo.get(day_wx, ""), day_wx]
        priorities = [p for p in priorities if p]

        # 2. 调候用神介入
        if tiaohou:
            if is_extreme_season:
                # 极端季节（夏/冬）：调候为第一优先
                if tiaohou in priorities:
                    priorities.remove(tiaohou)
                priorities.insert(0, tiaohou)
            elif tiaohou not in priorities:
                # 非极端季节（春/秋）：调候作为辅助补充
                priorities.append(tiaohou)

        # 3. 选最优五行作为用神
        if is_extreme_season and tiaohou:
            # 极端季节（夏/冬）：调候用神为第一优先级，直接作为用神
            best = tiaohou
        else:
            # 非极端季节（春/秋）或无比调候：选扶抑优先级中最弱的五行
            best = priorities[0] if priorities else day_wx
            min_count = wuxing.get(best, 99)
            for wx in priorities[:3]:
                if wuxing.get(wx, 0) < min_count:
                    min_count = wuxing.get(wx, 0)
                    best = wx

        wx_names = {"金":"金","木":"木","水":"水","火":"火","土":"土"}
        helpful = [wx_names[p] for p in priorities[:3] if p in wx_names]
        tiaohou_note = "（调候优先）" if is_extreme_season else ""
        return "%s为用神%s（喜%s）" % (wx_names.get(best, best), tiaohou_note, "、".join(helpful))

    def _calc_shensha(self, bazi_pillars: list, all_gan: list, all_zhi: list) -> list:
        """神煞计算 — 委托 shensha.py 引擎（年柱+日柱查表 29 种 + 按日干计算），
        保留旧方法名供外部兼容；calculate() 主流程已直接用 shensha_of。"""
        return [item.name for item in shensha_of(
            year_pillar=bazi_pillars[0],
            day_pillar=bazi_pillars[2],
            all_gan=all_gan,
            all_zhi=all_zhi,
        )]
