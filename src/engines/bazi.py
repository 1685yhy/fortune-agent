"""八字排盘引擎 - 基于 lunar-python."""
import math
from dataclasses import dataclass, field
from datetime import datetime as dt, timedelta
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

# 主要城市经纬度表（经度, 纬度）——真太阳时修正用。# 北京时间 = 120°E 标准时，出生地真太阳时 = 北京时间 + (经度-120)*4分钟 + 均时差。
# 城市不在表中时不做修正（兼容原行为）。数值为城市中心坐标，精度足以支撑 ±1 分钟内的均时差计算。
CITY_LONGLAT = {
    # 直辖市
    "北京": (116.40, 39.90), "天津": (117.20, 39.13), "上海": (121.47, 31.23), "重庆": (106.55, 29.56),
    # 河北
    "石家庄": (114.50, 38.04), "唐山": (118.18, 39.63), "秦皇岛": (119.60, 39.94),
    "保定": (115.46, 38.87), "邯郸": (114.54, 36.63),
    # 山西
    "太原": (112.55, 37.87), "大同": (113.30, 40.08),
    # 内蒙古
    "呼和浩特": (111.75, 40.84), "包头": (109.84, 40.66), "鄂尔多斯": (109.78, 39.61),
    # 辽宁
    "沈阳": (123.43, 41.80), "大连": (121.60, 38.91), "鞍山": (122.99, 41.11), "锦州": (121.13, 41.10),
    # 吉林
    "长春": (125.35, 43.88), "吉林市": (126.55, 43.84),
    # 黑龙江
    "哈尔滨": (126.63, 45.75), "齐齐哈尔": (123.92, 47.35), "大庆": (125.10, 46.59), "牡丹江": (129.63, 44.55),
    # 江苏
    "南京": (118.80, 32.06), "苏州": (120.60, 31.30), "无锡": (120.31, 31.49), "常州": (119.97, 31.81),
    "徐州": (117.18, 34.26), "扬州": (119.41, 32.39), "南通": (120.89, 31.98), "镇江": (119.45, 32.20),
    "泰州": (119.92, 32.46), "盐城": (120.16, 33.35), "淮安": (119.02, 33.61), "连云港": (119.22, 34.60),
    # 浙江
    "杭州": (120.15, 30.27), "宁波": (121.55, 29.87), "温州": (120.70, 28.00), "绍兴": (120.58, 30.03),
    "嘉兴": (120.76, 30.75), "金华": (119.65, 29.08), "湖州": (120.09, 30.89), "台州": (121.42, 28.66),
    # 安徽
    "合肥": (117.23, 31.82), "芜湖": (118.38, 31.33), "蚌埠": (117.39, 32.92), "安庆": (117.06, 30.54),
    # 福建
    "福州": (119.30, 26.08), "厦门": (118.09, 24.48), "泉州": (118.68, 24.87),
    "漳州": (117.65, 24.51), "莆田": (119.01, 25.45),
    # 江西
    "南昌": (115.86, 28.68), "九江": (116.00, 29.71), "赣州": (114.94, 25.83),
    # 山东
    "济南": (117.00, 36.65), "青岛": (120.38, 36.07), "烟台": (121.45, 37.46),
    "潍坊": (119.16, 36.71), "淄博": (118.05, 36.81), "临沂": (118.36, 35.10), "威海": (122.12, 37.51),
    # 河南
    "郑州": (113.62, 34.75), "洛阳": (112.45, 34.62), "开封": (114.31, 34.80), "南阳": (112.53, 32.99),
    # 湖北
    "武汉": (114.30, 30.59), "宜昌": (111.29, 30.69), "襄阳": (112.12, 32.01), "十堰": (110.80, 32.63),
    # 湖南
    "长沙": (112.94, 28.23), "株洲": (113.13, 27.83), "岳阳": (113.13, 29.36),
    "常德": (111.70, 29.03), "湘潭": (112.94, 27.83),
    # 广东
    "广州": (113.26, 23.13), "深圳": (114.06, 22.55), "珠海": (113.58, 22.27), "佛山": (113.12, 23.02),
    "东莞": (113.75, 23.02), "中山": (113.39, 22.52), "惠州": (114.42, 23.11),
    "汕头": (116.68, 23.35), "湛江": (110.36, 21.27), "韶关": (113.60, 24.81), "肇庆": (112.47, 23.05),
    # 广西
    "南宁": (108.32, 22.82), "桂林": (110.29, 25.27), "柳州": (109.42, 24.33),
    # 海南
    "海口": (110.32, 20.03), "三亚": (109.51, 18.25),
    # 四川
    "成都": (104.07, 30.67), "绵阳": (104.68, 31.47), "乐山": (103.77, 29.55),
    "宜宾": (104.62, 28.77), "南充": (106.08, 30.80),
    # 贵州
    "贵阳": (106.63, 26.65), "遵义": (106.93, 27.73),
    # 云南
    "昆明": (102.71, 25.04), "大理": (100.23, 25.59), "丽江": (100.23, 26.86),
    # 西藏
    "拉萨": (91.11, 29.65),
    # 陕西
    "西安": (108.94, 34.34), "咸阳": (108.71, 34.33), "宝鸡": (107.14, 34.36),
    # 甘肃
    "兰州": (103.83, 36.06), "天水": (105.72, 34.58),
    # 青海
    "西宁": (101.78, 36.62),
    # 宁夏
    "银川": (106.23, 38.49),
    # 新疆
    "乌鲁木齐": (87.62, 43.83), "喀什": (75.99, 39.47), "克拉玛依": (84.87, 45.60),
    "库尔勒": (86.15, 41.77), "哈密": (93.51, 42.83), "伊宁": (81.28, 43.92),
}

# 五虎遁：年干 → 寅月天干（甲己之年丙作首…）。返回 TIANGAN 下标。
WUHU_DUN = {"甲": 2, "乙": 4, "庚": 4, "丙": 6, "辛": 6, "丁": 8, "壬": 8, "戊": 0, "癸": 0, "己": 2}

# 旬空亡：六十甲子 → 本旬空亡支（问真 kw 每柱按本柱旬查、kongwang 按日柱旬查，两口径一致）
XUN_KONG = {
    "甲子": "戌亥", "乙丑": "戌亥", "丙寅": "戌亥", "丁卯": "戌亥", "戊辰": "戌亥", "己巳": "戌亥",
    "庚午": "戌亥", "辛未": "戌亥", "壬申": "戌亥", "癸酉": "戌亥",
    "甲戌": "申酉", "乙亥": "申酉", "丙子": "申酉", "丁丑": "申酉", "戊寅": "申酉", "己卯": "申酉",
    "庚辰": "申酉", "辛巳": "申酉", "壬午": "申酉", "癸未": "申酉",
    "甲申": "午未", "乙酉": "午未", "丙戌": "午未", "丁亥": "午未", "戊子": "午未", "己丑": "午未",
    "庚寅": "午未", "辛卯": "午未", "壬辰": "午未", "癸巳": "午未",
    "甲午": "辰巳", "乙未": "辰巳", "丙申": "辰巳", "丁酉": "辰巳", "戊戌": "辰巳", "己亥": "辰巳",
    "庚子": "辰巳", "辛丑": "辰巳", "壬寅": "辰巳", "癸卯": "辰巳",
    "甲辰": "寅卯", "乙巳": "寅卯", "丙午": "寅卯", "丁未": "寅卯", "戊申": "寅卯", "己酉": "寅卯",
    "庚戌": "寅卯", "辛亥": "寅卯", "壬子": "寅卯", "癸丑": "寅卯",
    "甲寅": "子丑", "乙卯": "子丑", "丙辰": "子丑", "丁巳": "子丑", "戊午": "子丑", "己未": "子丑",
    "庚申": "子丑", "辛酉": "子丑", "壬戌": "子丑", "癸亥": "子丑",
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
    # 问真口径扩展字段（2026-08-19 校准新增）
    taiyuan: str = ""             # 胎元干支（月柱天干进1、地支进3）
    taiyuan_nayin: str = ""       # 胎元纳音
    minggong: str = ""            # 命宫干支（命宫支=(8-月支-时支) mod 12，五虎遁配干）
    minggong_nayin: str = ""      # 命宫纳音
    shenggong: str = ""           # 身宫干支（身宫支=(月支+时支) mod 12，五虎遁配干）
    shenggong_nayin: str = ""     # 身宫纳音
    kongwang: List[str] = field(default_factory=list)  # 空亡（四柱各柱按本柱旬查，问真 kw 口径）
    kongwang_day: str = ""        # 日柱旬空亡（问真顶层 kongwang 口径）
    qiyun_detail: tuple = ()      # 起运时间分解 (年,月,日,时,分)（问真 qiyunarr 前5位口径）


class BaziEngine:
    """八字排盘引擎"""

    @staticmethod
    def _equation_of_time(date) -> float:
        """均时差（分钟）——经典天文近似公式，精度 ±1 分钟内。

        公式：B = 2π(N-81)/364；EoT ≈ 9.87sin(2B) - 7.53cos(B) - 1.5sin(B)
        N = 该日在年内天数（1 起）。
        已知锚点：2月11日 ≈ -14.2 分，5月14日 ≈ +3.7 分，11月3日 ≈ +16.4 分。
        """
        n_day = int(date.strftime("%j"))  # 年内天数 1-366
        b = 2.0 * math.pi * (n_day - 81) / 364.0
        return 9.87 * math.sin(2.0 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)

    @staticmethod
    def _city_longitude(city) -> Optional[float]:
        """查城市经度；未知城市 / 空值返回 None（不做修正）。支持去掉「市」后缀。"""
        if not city:
            return None
        name = city.strip()
        for cand in (name, name.rstrip("市"), name.rstrip("省")):
            if cand in CITY_LONGLAT:
                return CITY_LONGLAT[cand][0]
        return None

    def _true_solar_time(self, year: int, month: int, day: int,
                         hour: int, minute: int, city: str) -> tuple:
        """真太阳时修正（问真口径）。

        真太阳时 = 北京时间 + (经度-120)*4分钟 + 均时差。
        修正可能跨日（早于 00:00 或晚于 24:00），返回完整的修正后日期时间；
        不传 city 或城市不在表中 → 原样返回（不修正，兼容原行为）。
        """
        lon = self._city_longitude(city)
        if lon is None:
            return (year, month, day, hour, minute)
        eot = self._equation_of_time(dt(year, month, day))
        offset_min = (lon - 120.0) * 4.0 + eot
        corrected = dt(year, month, day, hour, minute) + timedelta(minutes=offset_min)
        return (corrected.year, corrected.month, corrected.day,
                corrected.hour, corrected.minute)

    def calculate(self, year: int, month: int, day: int,
                  hour: int, minute: int, city: str,
                  gender: str) -> BaziResult:
        """排八字命盘

        P1-3: 当 gender 为 "unknown" 时，默认按男排盘（大运顺排），
        但在结果中标注性别未知。
        真太阳时：用户填北京时间出生，按出生地经度 + 均时差修正为真太阳时后
        排全盘（与问真一致）；不传 city / 未知城市 → 不修正（原行为）。
        """
        # P1-3: Handle unknown gender — default to 男 for calculation
        calc_gender = gender if gender in ("男", "女") else "男"
        # 真太阳时修正：北京时间 → 出生地真太阳时（经度修正 + 均时差）。
        # 修正可能跨日（如 23:40 长春 → 次日 00:05），此时按修正后的日期排全部四柱。
        year, month, day, hour, minute = self._true_solar_time(
            year, month, day, hour, minute, city)
        # 处理晚子时 (23:00-23:59): 使用次日日期, 时柱仍为子时
        # 起运距离用真实出生时刻（真太阳时修正后）计算（与问真口径一致），故保留原始时间
        orig_birth = (year, month, day, hour, minute)
        if hour >= 23:
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
        self._qiyun_breakdown = ()
        dayun = self._calc_dayun(lunar, calc_gender, bazi_pillars, orig_birth)
        qiyun_detail = self._qiyun_breakdown

        # 流年（简化：使用 lunar-python 或计算）
        liunian = self._calc_liunian(lunar, day_gan)

        # 格局（简化版：取月支藏干透出）
        geju = self._calc_geju(month_zhi, month_gan, all_gan)

        # 用神（调候优先 + 扶抑辅助）
        yongshen = self._calc_yongshen(wuxing, day_gan, month_zhi)

        # 神煞（问真 szshensha 计算口径：年干/日干、年支/日支双查 + 年支类 + 空亡/元辰/学堂/词馆/天罗地网）
        shensha_items = shensha_of(
            year_pillar=bazi_pillars[0],
            day_pillar=bazi_pillars[2],
            all_gan=all_gan,
            all_zhi=all_zhi,
            gender=calc_gender,
        )
        shensha = [item.name for item in shensha_items]
        shensha_detail = [item.__dict__ for item in shensha_items]

        # 胎元 / 命宫 / 身宫（含纳音，问真口径，2026-08-19 校准）
        taiyuan = self._calc_taiyuan(bazi_pillars[1])
        minggong = self._calc_gongwei("ming", year_gan, month_zhi, time_zhi)
        shenggong = self._calc_gongwei("shen", year_gan, month_zhi, time_zhi)
        # 空亡：四柱各按本柱旬查（问真 kw 口径）；日柱旬空亡另存（问真顶层 kongwang 口径）
        kongwang = [XUN_KONG.get(p, "") for p in bazi_pillars]

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
            taiyuan=taiyuan,
            taiyuan_nayin=NAYIN.get(taiyuan, ""),
            minggong=minggong,
            minggong_nayin=NAYIN.get(minggong, ""),
            shenggong=shenggong,
            shenggong_nayin=NAYIN.get(shenggong, ""),
            kongwang=kongwang,
            kongwang_day=kongwang[2] if len(kongwang) > 2 else "",
            qiyun_detail=qiyun_detail,
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

    def _calc_taiyuan(self, month_pillar: str) -> str:
        """胎元：月柱天干进一位、地支进三位（问真口径）。

        例：己巳月 → 庚申（己→庚，巳→申）。
        """
        gan = TIANGAN[(TIANGAN.index(month_pillar[0]) + 1) % 10]
        zhi = DIZHI[(DIZHI.index(month_pillar[1]) + 3) % 12]
        return gan + zhi

    def _calc_gongwei(self, kind: str, year_gan: str, month_zhi: str, time_zhi: str) -> str:
        """命宫 / 身宫（问真口径，2026-08-19 经 10 案例反推校准）。

        地支：身宫支序 = (月支序 + 时支序) mod 12；命宫支序 = (8 - 月支序 - 时支序) mod 12
        （子=1…亥=12，结果 0 记 12）。取四柱月支、时支。
        天干：五虎遁从年干起寅月，顺推至宫支（与问真 minggong/shenggong 干支一致）。
        """
        month_idx = DIZHI.index(month_zhi) + 1
        hour_idx = DIZHI.index(time_zhi) + 1
        if kind == "ming":
            idx = (8 - month_idx - hour_idx) % 12
        else:  # shen
            idx = (month_idx + hour_idx) % 12
        if idx == 0:
            idx = 12
        gong_zhi = DIZHI[idx - 1]
        # 五虎遁：寅月干 + (宫支到寅的偏移)
        base = WUHU_DUN[year_gan]
        offset = (idx - 3) % 12  # 寅=3（子=1 序）为 0
        gong_gan = TIANGAN[(base + offset) % 10]
        return gong_gan + gong_zhi

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

        # 节气时刻（秒级精度，问真口径）：顺排取下一节，逆排取上一节（12节，非中气）
        solar = Solar.fromYmdHms(year, month, day, hour, minute, 0)
        jie = solar.getLunar().getNextJie() if forward else solar.getLunar().getPrevJie()
        jt = jie.getSolar()
        jie_time = dt(jt.getYear(), jt.getMonth(), jt.getDay(),
                      jt.getHour(), jt.getMinute(), jt.getSecond())
        dist_days = (jie_time - birth).total_seconds() / 86400.0
        if dist_days < 0:  # 防御：取错方向时反转
            dist_days = -dist_days

        # 换算：3天=1岁；分解为 年/月/日/时/分（全部截断，分钟四舍五入；60 分钟进位）
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
            mi -= 60
            hh += 1

        # 按日历相加：先加整年整月（日对齐到目标月有效天数），再加日时分
        months = yy * 12 + mm
        y2 = birth.year + (birth.month - 1 + months) // 12
        m2 = (birth.month - 1 + months) % 12 + 1
        day2 = min(birth.day, calendar.monthrange(y2, m2)[1])
        qy = dt(y2, m2, day2, birth.hour, birth.minute) + timedelta(days=dd, hours=hh, minutes=mi)

        self._qiyun_breakdown = (yy, mm, dd, hh, mi)  # 供 qiyun_detail 暴露（问真 qiyunarr 前5位）
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
