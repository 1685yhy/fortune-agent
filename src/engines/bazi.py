"""八字排盘引擎 - 基于 lunar-python."""
import json as _json
import logging
import math
import os as _os
from dataclasses import dataclass, field
from datetime import datetime as dt, timedelta
from typing import List, Dict, Optional
from lunar_python import Lunar, Solar

from src.engines.shensha import shensha_of, shensha_of_dayun
from src.engines.ganzhi_rel import analyze_relations
from src.engines.chenggu import (chenggu_bone, bone_weight_text, chenggu_parts,
                                 part_weight_text, MONTH_CN, RN)
from src.engines.bazi_formatter import get_changsheng
from src.engines.wuxing import (wuxing_counts, month_wangshuai,
                                changsheng_state, day_master_strength)

logger = logging.getLogger(__name__)

TIANGAN = ["甲","乙","丙","丁","戊","己","庚","辛","壬","癸"]
DIZHI = ["子","丑","寅","卯","辰","巳","午","未","申","酉","戌","亥"]
WUXING_TG = {"甲":"木","乙":"木","丙":"火","丁":"火","戊":"土","己":"土","庚":"金","辛":"金","壬":"水","癸":"水"}
WUXING_DZ = {"子":"水","丑":"土","寅":"木","卯":"木","辰":"土","巳":"火","午":"火","未":"土","申":"金","酉":"金","戌":"土","亥":"水"}
WUXING_ORDER = ["金","木","水","火","土"]  # 五行序（与 data/wuxing_tables.json 表头一致，L2-3）
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


# ── 流年 / 流月 / 流时（L2-4，问真方式：排盘结果带出流年表）──

def xiaoyun_table(time_pillar: str, forward: bool, years: int = 110) -> List[str]:
    """小运（问真 xiaoyun 口径，2026-08-21 从语料 8658 例全量反推 100% 一致）。

    规则：以时柱干支为起点，顺逆与大运同向（阳男阴女顺排、阴男阳女逆排），
    逐年推进一位；第 1 条 = 时柱顺/逆推一位（出生次年），共 110 条（问真语料
    恒 110 条，覆盖出生后 110 年，起运前使用、起运后与流年并列展示）。
    例：xiaoyun_table("甲子", True)[:3] == ["乙丑", "丙寅", "丁卯"]；
        xiaoyun_table("甲子", False)[:3] == ["癸亥", "壬戌", "辛酉"]。
    """
    start = SHENG_XU_MAP.get(time_pillar, 0)
    step = 1 if forward else -1
    return [SHENG_XU[(start + step * i) % 60] for i in range(1, years + 1)]


def liunian_ganzhi(birth_year: int, year_pillar: str, target_year: int) -> str:
    """流年干支（年柱 + 岁差，六十甲子循环）。

    口径：流年干支 = 出生年柱干支 + (目标年 − 出生年) mod 60。
    birth_year 为出生干支年（立春界定，与 year_pillar 同一年，见 BaziEngine._pillar_year）
    ——非农历年，否则 [立春, 正月初一) 出生者岁差整体错位一年。
    例：liunian_ganzhi(1999, "己卯", 2026) == "丙午"（2024 甲辰 / 2020 庚子 同法）。
    """
    return SHENG_XU[(SHENG_XU_MAP.get(year_pillar, 0)
                     + (target_year - birth_year)) % 60]


def liunian_table(birth_year: int, year_pillar: str, years: int = 30) -> List[dict]:
    """流年表：从出生干支年起逐年顺推（年柱 + 岁差 i，问真排盘页流年列表同款口径）。

    每项 {year, age, ganzhi, nayin}：year 为流年公历年（出生干支年 ~ 出生干支年+years−1），
    age 为虚岁（流年年份 − 出生年份 + 1），ganzhi/nayin 为流年干支及纳音。
    birth_year 须为年柱所在干支年（立春界定，与 year_pillar 同一干支年）——非农历年：
    农历年正月初一换年、年柱立春换年，[立春, 正月初一) 出生者两口径差 1，若传农历年
    则整表年份/干支错位一年（2026-08-20 修复）。
    性能口径：本函数只出基础四键（year/age/ganzhi/nayin）；流年神煞/与原局关系/
    大运流年关系由 BaziEngine.calculate 在 BaziResult 层全量补入（批1：30 年全量，
    30×shensha_of_dayun + 30×rel_with ≈ 毫秒级）。
    例：liunian_table(1999, "己卯")[1] == {"year": 2000, "age": 2,
                                         "ganzhi": "庚辰", "nayin": "白蜡金"}。
    """
    start = SHENG_XU_MAP.get(year_pillar, 0)
    return [{
        "year": birth_year + i,
        "age": i + 1,
        "ganzhi": SHENG_XU[(start + i) % 60],
        "nayin": NAYIN.get(SHENG_XU[(start + i) % 60], ""),
    } for i in range(years)]


def liuyue(year_ganzhi: str) -> List[str]:
    """流月十二干支（五虎遁：年干定月干首——甲己之年丙作首…；月支固定 寅=正月）。

    例：liuyue("己卯") == ["丙寅", "丁卯", …, "丁丑"]（己年正月丙寅）。
    """
    base = WUHU_DUN.get(year_ganzhi[0], 0)
    return [TIANGAN[(base + i) % 10] + DIZHI[(2 + i) % 12] for i in range(12)]


def liushi(day_ganzhi: str) -> List[str]:
    """流时十二时辰干支（五鼠遁：日干定时干首——甲己还加甲…；时辰支固定 子=首）。

    例：liushi("乙丑")[0] == "丙子"（乙日子时丙子）。
    """
    base = WUSHU_DUN.get(day_ganzhi[0], 0)
    return [TIANGAN[(base + i) % 10] + DIZHI[i] for i in range(12)]

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

# 五鼠遁：日干 → 子时天干（甲己还加甲、乙庚丙作初、丙辛从戊起、丁壬庚子居、戊癸壬子头）。
# 返回 TIANGAN 下标（L2-4 流时）。
WUSHU_DUN = {"甲": 0, "己": 0, "乙": 2, "庚": 2, "丙": 4, "辛": 4, "丁": 6, "壬": 6, "戊": 8, "癸": 8}

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

# 人元司令分野表（问真《子平真诠》分日决，问真 chunk2 内嵌参考表，2026-08-19 提取）。
# 各月支自本气节起按天干分段用事（各段用事天数之和为 30 天）；申月首段为戊己共 10 日。
SILING_TABLE = {
    "寅": (("戊", 7), ("丙", 7), ("甲", 16)),
    "卯": (("甲", 10), ("乙", 20)),
    "辰": (("乙", 9), ("癸", 3), ("戊", 18)),
    "巳": (("戊", 5), ("庚", 9), ("丙", 16)),
    "午": (("丙", 10), ("己", 9), ("丁", 11)),
    "未": (("丁", 9), ("乙", 3), ("己", 18)),
    "申": (("戊己", 10), ("壬", 3), ("庚", 17)),
    "酉": (("庚", 10), ("辛", 20)),
    "戌": (("辛", 9), ("丁", 3), ("戊", 18)),
    "亥": (("戊", 7), ("甲", 5), ("壬", 18)),
    "子": (("壬", 10), ("癸", 20)),
    "丑": (("癸", 9), ("辛", 3), ("己", 18)),
}

# 月支 → 本气节（人元司令分野的起点）
JIE_OF_MONTH = {
    "寅": "立春", "卯": "惊蛰", "辰": "清明", "巳": "立夏", "午": "芒种", "未": "小暑",
    "申": "立秋", "酉": "白露", "戌": "寒露", "亥": "立冬", "子": "大雪", "丑": "小寒",
}

# 12 节（交运时刻所在节月判定用，非中气）
JIE_NAMES = ["立春", "惊蛰", "清明", "立夏", "芒种", "小暑", "立秋", "白露", "寒露", "立冬", "大雪", "小寒"]


def _calendar_add(base: dt, years: int, months: int, days: int,
                  hours: int = 0, minutes: int = 0) -> dt:
    """按日历加年月日时分（问真起运/交运口径：加整年整月后日对齐目标月有效天数）。"""
    import calendar
    total_months = years * 12 + months
    y2 = base.year + (base.month - 1 + total_months) // 12
    m2 = (base.month - 1 + total_months) % 12 + 1
    d2 = min(base.day, calendar.monthrange(y2, m2)[1])
    return dt(y2, m2, d2, base.hour, base.minute) + timedelta(days=days, hours=hours, minutes=minutes)


def _jie_time_of(year: int, name: str) -> Optional[dt]:
    """lunar-python 节气时刻（秒级）→ datetime；查不到返回 None。"""
    solar = Solar.fromYmdHms(year, 6, 1, 12, 0, 0)
    v = solar.getLunar().getJieQiTable().get(name)
    if not v:
        return None
    return dt(v.getYear(), v.getMonth(), v.getDay(),
              v.getHour(), v.getMinute(), v.getSecond())


# 问真节气表：节序号 → 节名（序号即公历月：小寒1月 立春2月 … 大雪12月）
JIE_INDEX = {"小寒": 1, "立春": 2, "惊蛰": 3, "清明": 4, "立夏": 5, "芒种": 6,
             "小暑": 7, "立秋": 8, "白露": 9, "寒露": 10, "立冬": 11, "大雪": 12}

_JIEQI_QZ_CACHE = None


def _jieqi_time(year: int, jie_name: str) -> Optional[dt]:
    """问真节气表查询（data/jieqi_qz.json，1799-2100，12 节秒级时刻）。

    表格式：{"1800": {"1": ["小寒", "6", "07:46:02"], ...12节}, ...}
    （scripts/extract_jieqi_qz.py 从问真 eOvQ 模块提取，2026-08-20 修正年份错位；
    问真表时刻与 lunar-python 有秒级差异，起运分解用问真表可与问真 qiyunarr 对齐）。
    回退契约（不抛异常）：
    - 越界（<1799 或 >2100）、缺失、条目损坏（如 day=32、时间串格式错）→
      按缺失处理，回退 lunar-python（_jie_time_of，返回 lunar 值）；
    - 表文件缺失/损坏（JSON 加载失败）→ logger.warning 一行后返回 None（调用方整表回退 lunar）。
    """
    global _JIEQI_QZ_CACHE
    if _JIEQI_QZ_CACHE is None:
        _path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                              "..", "..", "data", "jieqi_qz.json")
        try:
            with open(_path, encoding="utf-8") as _f:
                _JIEQI_QZ_CACHE = _json.load(_f)
        except Exception as _e:
            # 表文件缺失/损坏：不静默，告警一行后按"无表"处理（返回 None，调用方走 lunar 回退）
            logger.warning("jieqi_qz.json 加载失败，起运节气回退 lunar-python: %s", _e)
            _JIEQI_QZ_CACHE = {}
            return None
    idx = JIE_INDEX.get(jie_name)
    if idx is not None:
        try:
            entry = _JIEQI_QZ_CACHE.get(str(year), {}).get(str(idx))
            if entry and entry[0] == jie_name:
                hh, mm, ss = entry[2].split(":")
                return dt(year, idx, int(entry[1]), int(hh), int(mm), int(ss))
        except (ValueError, TypeError, IndexError, AttributeError):
            pass  # 条目损坏（如 day=32、结构缺字段）：按缺失处理，落到下方 lunar 回退
    return _jie_time_of(year, jie_name)


def _siling_calc(month_zhi: str, solar_date) -> dict:
    """人元司令分野计算（问真《子平真诠》分日决表，P0-1）。

    solar_date: datetime 或 (年,月,日,时,分) 元组。自本气节起按分野表分段，
    生日落在哪段即该干司令。返回 {gan, days, elapsed, remaining, month_zhi, jie}。
    例：巳月立夏后 7.18 天 → 戊5天已尽，庚用事9天 → gan=庚。
    """
    if isinstance(solar_date, (tuple, list)):
        parts = list(solar_date) + [0] * (5 - len(solar_date))
        birth = dt(int(parts[0]), int(parts[1]), int(parts[2]),
                   int(parts[3]), int(parts[4]))
    else:
        birth = solar_date
    jie = (Solar.fromYmdHms(birth.year, birth.month, birth.day,
                            birth.hour, birth.minute, 0)
           .getLunar().getPrevJie())
    jt = jie.getSolar()
    jie_time = dt(jt.getYear(), jt.getMonth(), jt.getDay(),
                  jt.getHour(), jt.getMinute(), jt.getSecond())
    # 防御：月支与本气节不一致属调用方错误（如把巳月生日配成寅月支）
    if JIE_OF_MONTH.get(month_zhi) != jie.getName():
        raise ValueError(
            "month_zhi %s 与生日所在节月(%s)不一致" % (month_zhi, jie.getName()))
    elapsed = (birth - jie_time).total_seconds() / 86400.0
    segs = SILING_TABLE.get(month_zhi, ())
    if not segs:
        return {}
    cum = 0.0
    for gan, days in segs:
        if elapsed < cum + days:
            return {
                "gan": gan, "days": days,
                "elapsed": round(elapsed, 6),
                "remaining": round(cum + days - elapsed, 6),
                "month_zhi": month_zhi,
                "jie": jie.getName(),
            }
        cum += days
    # 节气月可略超 30 天，末段顺延
    gan, days = segs[-1]
    return {
        "gan": gan, "days": days,
        "elapsed": round(elapsed, 6),
        "remaining": round(cum + days - elapsed, 6),
        "month_zhi": month_zhi,
        "jie": jie.getName(),
    }


def sizhilingxiu(month_zhi: str, solar_date) -> tuple:
    """人元司令（问真分野表，P0-1）→ (司令天干, 用事天数)。

    例：sizhilingxiu("巳", (1999,5,13,11,25)) → ("庚", 9)（立夏后7.18天，戊5天已尽）。
    """
    info = _siling_calc(month_zhi, solar_date)
    return (info.get("gan", ""), info.get("days", 0))


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
    qiyun_desc: str = ""          # 起运描述 "出生后2年4月22天0时起运"（问真排盘页口径，P0-1）
    corrected_time: str = ""      # 真太阳时修正后时间 "HH:MM"（P1-2审查I1：晚子时/归日判定与引擎同口径；
                                  # 引擎按修正后小时判定归日，输出文案须用同一口径）
    jiaoyun: dict = field(default_factory=dict)  # 交运信息（问真口径，P0-1）：见 _calc_jiaoyun
    siling: str = ""              # 人元司令天干（问真排盘页"司令：X"口径，P0-1）
    siling_detail: dict = field(default_factory=dict)  # 司令分野明细 {gan,days,elapsed,remaining,...}
    ganzhi_rel: list = field(default_factory=list)  # 原局四柱间两两干支关系 [{between,type,desc}]（P0-3）
    chenggu: dict = field(default_factory=dict)  # 称骨（问真口径，L2-1）：{weight_text, liang, qian, jieci, parts}
    # parts：分项骨重 [{label, weight}]（年/月/日/时），与 weight_text 同源同口径，
    # 分项合计恒等于总重（晚子时/真太阳时归日后的同一农历日计算）。
    lunar: dict = field(default_factory=dict)  # 归一化后农历口径（晚子时已归日）：
    # {year, month(闰月已 abs 归一), day, day_text}——API 层 meta 农历信息须用此，
    # 与四柱（日柱=次日）自洽。
    # 知识索引（问真点文字查解析同款入口，L2-2）：{category: [名称...]}，
    # 前端据此渲染可点文字 → GET /api/knowledge?category=&name=
    knowledge_index: dict = field(default_factory=dict)
    # 五行能量引擎（L2-3）：{counts, wangshuai, changsheng, strength, yongshen}
    # 供前端"五行进度条"等展示（L4 设计）
    wuxing_energy: dict = field(default_factory=dict)
    # 流年/流月/流时 + 干支关系集成（L2-4，问真方式：排盘结果带出流年表）
    # 批1（流年详解）起每项全量补入：shensha（流年神煞，shensha_of_dayun 问真口径）/
    # rel（流年 vs 原局各柱，rel_with）/ dayun（该年所在大运 {sui,ganzhi,rel}：
    # rel 为大运 vs 流年干支关系，起运前 sui=0/ganzhi=''/rel=[]）
    liunian_full: list = field(default_factory=list)  # 流年表（出生年起 30 年）[{year,age,ganzhi,nayin,shensha,rel,dayun}]
    liunian_rel: dict = field(default_factory=dict)   # 当前流年 vs 原局各柱 {year, ganzhi, rel}（rel_with 结果）
    liuyue: dict = field(default_factory=dict)        # 当前流年 12 流月 {year, ganzhi, months}
    liushi: dict = field(default_factory=dict)        # 今日 12 流时 {day_pillar, hours}
    dayun_rel: list = field(default_factory=list)     # 各步大运 vs 原局 [{sui, ganzhi, rel}]（rel_with 结果）
    # 小运（问真 xiaoyun 口径，P2-2 补全）：110 条干支列表（时柱起、与大运同向顺逆，
    # 语料 8658 例全量对齐 100%，见 xiaoyun_table）
    xiaoyun: list = field(default_factory=list)
    # 每步大运神煞（问真 dyshensha 口径，P2-2 补全）：[[大运干支, [神煞名...]], ...]
    # 与 dayun 同序同位（shensha_of_dayun，语料 8658 例全量对齐 100%）
    dyshensha: list = field(default_factory=list)

    def rel_with(self, ganzhi: str) -> list:
        """大运/流年干支与原局各柱的干支关系（问真点大运流年同款入口，P0-3）。

        :param ganzhi: 大运/流年干支，如 "乙丑"
        :return: [{between, type, desc}]：
            between = 年柱/月柱/日柱/时柱 —— ganzhi 与该柱的作用关系；
            between = 自身 —— ganzhi 柱自身的盖头/截脚。
        """
        out = []
        seen = set()
        # ganzhi 柱自身：盖头/截脚（问真点大运流年时该柱自身的柱内性质）
        for it in analyze_relations(ganzhi, ganzhi, self.bazi):
            if it.type in ("盖头", "截脚"):
                key = (it.type, it.desc)
                if key not in seen:
                    seen.add(key)
                    out.append({"between": "自身", "type": it.type, "desc": it.desc})
        # ganzhi vs 原局各柱（原局柱自身的盖头/截脚为柱内性质，不在此列）
        for name, p in zip(("年柱", "月柱", "日柱", "时柱"), self.bazi):
            for it in analyze_relations(ganzhi, p, self.bazi):
                if it.type in ("盖头", "截脚"):
                    continue
                if it.type in ("合", "争合", "妒合"):
                    key = (it.type, it.desc)  # 多柱参与的作用，仅首个柱位输出
                else:
                    key = (name, it.type, it.desc)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"between": name, "type": it.type, "desc": it.desc})
        return out


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
        # 暴露修正后时间（P1-2审查I1）：晚子时/归日判定须与引擎同口径——
        # 输入 23:00 修正后可能未达 23 点（如北京 22:41，非晚子时、日柱当日）
        corrected_time = "%02d:%02d" % (hour, minute)
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

        # 五行统计（L2-3 统一走五行能量引擎；口径 = 天干 + 地支本气，与问真排盘页"五行"同款）
        wuxing = wuxing_counts(bazi_pillars)

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
        self._qiyun_datetime = None
        self._qiyun_desc = ""
        dayun = self._calc_dayun(lunar, calc_gender, bazi_pillars, orig_birth)
        qiyun_detail = self._qiyun_breakdown
        qiyun_desc = self._qiyun_desc

        # 小运 / 大运神煞（问真口径，P2-2 补全）：顺逆与大运同向（阳男阴女顺、
        # 阴男阳女逆），时柱起逐年推 110 条；大运神煞逐柱套通用规则（shensha_of_dayun）
        year_gan0 = bazi_pillars[0][0]
        is_yang0 = TIANGAN.index(year_gan0) % 2 == 0
        is_male0 = calc_gender == "男"
        dayun_forward = (is_male0 and is_yang0) or (not is_male0 and not is_yang0)
        xiaoyun = xiaoyun_table(bazi_pillars[3], dayun_forward)
        dyshensha = [[gz, shensha_of_dayun(gz, bazi_pillars[0], bazi_pillars[1],
                                           bazi_pillars[2], calc_gender)]
                     for _, gz in dayun]

        # 流年/流月/流时（L2-4）：流年表从出生年柱所在干支年起 30 年——干支年以
        # 立春界定（与年柱同口径）；农历年正月初一换年、年柱立春换年，[立春, 正月初一)
        # 出生者（约 4%）农历年比干支年小 1，若用农历年作起点/岁差基数，流年表/
        # 流月/流年关系将整体错位一年（2026-08-20 修复）。当前流年（真年，立春界定）
        # 驱动 流月/干支关系；流时 = 今日 12 时辰。
        pillar_year = self._pillar_year(year, month, day, hour, minute)
        liunian_full = liunian_table(pillar_year, bazi_pillars[0])
        current_ln_year, current_ln_gz = self._current_liunian(
            pillar_year, bazi_pillars[0])
        _now = dt.now()
        # 今日日柱取正午（规避晚子时口径差异），五鼠遁配 12 流时
        _today_pillar = Solar.fromYmdHms(
            _now.year, _now.month, _now.day, 12, 0, 0).getLunar().getDayInGanZhi()
        liuyue_now = {"year": current_ln_year, "ganzhi": current_ln_gz,
                      "months": liuyue(current_ln_gz)}
        liushi_now = {"day_pillar": _today_pillar, "hours": liushi(_today_pillar)}

        # 交运 + 人元司令（问真口径，P0-1）
        jiaoyun = {}
        if self._qiyun_datetime is not None:
            jiaoyun = self._calc_jiaoyun(
                self._qiyun_datetime, self._qiyun_breakdown,
                dayun[0][0] if dayun else 0)
        siling_detail = {}
        try:
            siling_detail = _siling_calc(month_zhi, orig_birth)
        except Exception:
            siling_detail = {}
        siling = siling_detail.get("gan", "")

        # 流年（简化：使用 lunar-python 或计算）
        liunian = self._calc_liunian(lunar, day_gan)

        # 格局（简化版：取月支藏干透出）
        geju = self._calc_geju(month_zhi, month_gan, all_gan)

        # 用神（调候优先 + 扶抑辅助）
        yongshen = self._calc_yongshen(wuxing, day_gan, month_zhi)

        # 五行能量引擎（L2-3）：统计/月令旺衰/十二长生/日主强弱；用神沿用 _calc_yongshen
        wuxing_energy = {
            "counts": wuxing,  # 同口径：天干 + 地支本气
            "wangshuai": {wx: month_wangshuai(month_zhi, wx) for wx in WUXING_ORDER},
            "changsheng": {
                "年": changsheng_state(day_gan, year_zhi),
                "月": changsheng_state(day_gan, month_zhi),
                "日": changsheng_state(day_gan, day_zhi),
                "时": changsheng_state(day_gan, time_zhi),
            },
            "strength": day_master_strength(bazi_pillars, wuxing),
            "yongshen": yongshen,
        }
        # 旺衰逐字段（问真 zz）核对（对比报告 P3，2026-08-21）：语料 zz = 各柱
        # 本柱天干十二长生（30 例抽样 100% 一致），语义上已被 wangshuai（五行月令
        # 旺衰）与 changsheng（日主十二长生逐柱，即语料 xy）覆盖——前端"五行旺衰
        # 逐字段"展示用 wuxing_energy.wangshuai/changsheng 即可，不重复输出 zz
        # （"已有则不重复"原则）。

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

        # 干支关系：原局四柱间两两（伏吟/反吟/盖头/截脚/争合/妒合，问真同款规则，P0-3）
        ganzhi_rel = self._calc_ganzhi_rel(bazi_pillars)

        # 归一化后农历（晚子时归日/真太阳时跨日口径已体现在 lunar 对象上）：
        # 供称骨分项与 API 层 meta 使用，保证与四柱（日柱=次日）自洽。
        lunar_month = abs(lunar.getMonth())  # 闰月与平月同重（问真 G.c 口径）
        lunar_day = lunar.getDay()
        lunar_disp = {
            "year": int(lunar.getYear()),
            "month": lunar_month,
            "day": lunar_day,
            "day_text": lunar.getDayInChinese(),
        }

        # 称骨（问真口径，L2-1）：年柱干支 + 农历月/日 + 时支。与四柱同用晚子时
        # 归日后的同一农历日，保证称骨与排盘自洽；分项 parts 同源同口径计算，
        # 分项合计恒等于总重（晚子时不再出现分项≠总重矛盾）。
        chenggu = {}
        try:
            cg_liang, cg_qian, cg_jieci = chenggu_bone(
                bazi_pillars[0], lunar_month, lunar_day,
                time_zhi, gender=calc_gender)
            parts_qian = chenggu_parts(
                bazi_pillars[0], lunar_month, lunar_day, time_zhi)
            part_labels = [
                "年 %s" % bazi_pillars[0],
                "月 %s" % MONTH_CN[lunar_month - 1],
                "日 %s" % ("初%s" % RN[lunar_day - 1]
                           if lunar_day <= 10 else lunar.getDayInChinese()),
                "时 %s" % time_zhi,
            ]
            chenggu = {
                "weight_text": bone_weight_text(cg_liang, cg_qian),
                "liang": cg_liang,
                "qian": cg_qian,
                "jieci": cg_jieci,
                "parts": [{"label": part_labels[i],
                           "weight": part_weight_text(parts_qian[i])}
                          for i in range(4)],
            }
        except Exception:
            chenggu = {}

        # 知识索引（L2-2）：各类别可点文字清单，去重保序。
        # 契约：每类名称必须能在本类知识库命中（"日主"位省略——日主天干
        # 已含于 tiangan 类，前端点日主格查 tiangan 即可）。
        def _dedupe(seq):
            return list(dict.fromkeys(x for x in seq if x))

        knowledge_index = {
            "shishen": _dedupe(s for s in shishen if s != "日主"),
            "zhangsheng": _dedupe(get_changsheng(day_gan, p[1]) for p in bazi_pillars),
            "nayin": _dedupe(nayin),
            "shensha": _dedupe(shensha),
            "tiangan": _dedupe(p[0] for p in bazi_pillars),
            "dizhi": _dedupe(p[1] for p in bazi_pillars),
        }

        result = BaziResult(
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
            qiyun_desc=qiyun_desc,
            corrected_time=corrected_time,
            jiaoyun=jiaoyun,
            siling=siling,
            siling_detail=siling_detail,
            ganzhi_rel=ganzhi_rel,
            chenggu=chenggu,
            lunar=lunar_disp,
            knowledge_index=knowledge_index,
            wuxing_energy=wuxing_energy,
            xiaoyun=xiaoyun,
            dyshensha=dyshensha,
        )
        # 干支关系集成（L2-4）+ 流年详解（批1）：当前流年 + 各步大运 vs 原局各柱
        # （复用 rel_with，问真点大运/流年查关系同款入口）。大运各步全量（12 步柱间
        # 判定开销可忽略，前端点每步即时可用）。
        result.liunian_full = liunian_full
        # 批1 流年详解：liunian_full 30 年全量逐项补入 流年神煞（shensha_of_dayun
        # 问真 dyshensha 同款口径，流年干支作目标柱）+ 流年 vs 原局关系（rel_with）
        # + 该年所在大运（虚岁定位，与 API 层 start_year 同源）及其大运 vs 流年
        # 关系（analyze_relations）——30×(神煞+rel) ≈ 毫秒级可接受，做全量；
        # 前端排盘页逐流年胶囊可点开底部弹层详析。
        for item in result.liunian_full:
            item["shensha"] = shensha_of_dayun(
                item["ganzhi"], bazi_pillars[0], bazi_pillars[1], bazi_pillars[2],
                calc_gender)
            item["rel"] = result.rel_with(item["ganzhi"])
            step = next(((s, g) for s, g in dayun if s <= item["age"] <= s + 9),
                        None)
            if step is not None:
                item["dayun"] = {
                    "sui": step[0], "ganzhi": step[1],
                    "rel": [{"type": it.type, "desc": it.desc}
                            for it in analyze_relations(step[1], item["ganzhi"],
                                                         bazi_pillars)],
                }
            else:
                item["dayun"] = {"sui": 0, "ganzhi": "", "rel": []}
        result.liunian_rel = {"year": current_ln_year, "ganzhi": current_ln_gz,
                              "rel": result.rel_with(current_ln_gz)}
        result.liuyue = liuyue_now
        result.liushi = liushi_now
        result.dayun_rel = [{"sui": sui, "ganzhi": gz, "rel": result.rel_with(gz)}
                            for sui, gz in dayun]
        return result

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
        2. 时长：出生时刻到目标节交节时刻的间隔（节气时刻取问真节气表
           data/jieqi_qz.json，秒级精度；越界/缺失回退 lunar-python）
        3. 换算：3天=1年，1天=4个月 → 30天月单位 R = 120 × 距离（天）
        4. 分解为 年/月/日/时/分（问真服务端 qiyunarr 口径，250 案例校准 249/250，
           0.4% 差异仅为浮点末位边界）；按日历加回出生时刻 → 起运日期
        5. 起运虚岁 = 起运日期所在年份 - 出生年份 + 1（问真 qiyunsui 口径）

        经问真 API 校准：280/280 (100%) 一致（虚岁）；起运分解 250 案例 249/250。
        """
        from datetime import datetime as dt, timedelta

        birth = dt(year, month, day, hour, minute)
        # 方向：以年柱天干阴阳 + 性别决定（年柱与四柱同源）
        year_gan = lunar.getEightChar().getYear()[0]
        is_yang = TIANGAN.index(year_gan) % 2 == 0  # 甲丙戊庚壬为阳
        is_male = gender == "男"
        forward = (is_male and is_yang) or (not is_male and not is_yang)

        # 节气时刻（问真表秒级，优先）：顺排取下节、逆排取上节（12 节，与月柱同源）
        jie_time = None
        if forward:
            for yy in (year, year + 1):
                for name in JIE_NAMES:
                    t = _jieqi_time(yy, name)
                    if t is not None and t > birth and (jie_time is None or t < jie_time):
                        jie_time = t
        else:
            for yy in (year - 1, year):
                for name in JIE_NAMES:
                    t = _jieqi_time(yy, name)
                    if t is not None and t <= birth and (jie_time is None or t > jie_time):
                        jie_time = t
        if jie_time is None:
            # 回退：lunar-python 节气表
            solar = Solar.fromYmdHms(year, month, day, hour, minute, 0)
            jie = solar.getLunar().getNextJie() if forward else solar.getLunar().getPrevJie()
            jt = jie.getSolar()
            jie_time = dt(jt.getYear(), jt.getMonth(), jt.getDay(),
                          jt.getHour(), jt.getMinute(), jt.getSecond())
        dist_days = abs((jie_time - birth).total_seconds() / 86400.0)

        # 换算：3天=1年 → 30天月单位分解（问真服务端 qiyunarr 口径，250 案例反推）。
        # ── 单位体系推导（R = 距离(实天) × 120，即 120 单位 = 1 实天）──
        #   1 年 = 3 实天 = 360 单位（古典换算：3 天折 1 年）
        #   1 月 = 1/4 实天 = 30 单位（古典换算：1 实天折 4 月）
        #   1 单位 = 12 实分钟 = 1 个"输出日"；1 实小时 = 5 单位 = 5 输出日
        #     （古典换算：1 时辰 = 2 实小时 = 10 输出日 → 24 实小时 = 120 单位 = 120 输出日）
        # ── 月借位（Df < 1.0 且 mm % 4 == 1）──
        #   mm ≡ 1 (mod 4) ⟺ 整实天之外恰余 1 整月（4 月 = 4×30 = 120 单位 = 1 实天，
        #   故整月数每满 4 即消耗 1 实天；余 1 整月 = 30 单位 = 30 输出日 = 6 实小时）。
        #   再叠加日余不足 1 单位（Df < 1.0，即 <12 实分钟，零头日无）时，问真 qiyunarr
        #   把"X月0天"改写为"X-1月30天"（量值不变，如 1月0天 → 0月30天，250 案例反推）。
        # ── 日借位（Df-dd < 1/24 且 dd % 5 == 1）──
        #   dd ≡ 1 (mod 5) ⟺ 整实小时之外恰余 1 输出日（5 输出日 = 5 单位 = 1 实小时，
        #   故输出日每满 5 即消耗 1 实小时；余 1 输出日 = 24 输出时 = 12 实分钟）。
        #   再叠加日余不足 1 输出时（Df-dd < 1/24 单位，即 <30 实秒）时，qiyunarr
        #   把"X天0时"改写为"X-1天24时"（如 21天26分 → 20天24时26分）。
        # ── 分钟 ──
        #   mi 四舍五入后可为 60：问真服务端存在该输出且不进位（口径如此，勿"修正"进位）。
        # 例：1999-05-13 11:25 → 立夏后 7.18333 天 → 2年4月22天0时。
        R = dist_days * 120.0
        yy = int(R / 360.0)
        R2r = R - 360.0 * yy
        mm = int(R2r / 30.0)
        Df = R2r - 30.0 * mm
        if Df < 1.0 and mm % 4 == 1:
            # 整月不足 1 天：按 30 天回退到上一月（问真 qiyunarr，如 1月0天 → 0月30天）
            mm -= 1
            dd = 30
        else:
            dd = int(Df)
            if Df - dd < 1.0 / 24.0 and dd % 5 == 1:
                # 日余不足 1 小时：借 1 天给小时（问真 qiyunarr，如 21天26分 → 20天24时26分）
                dd -= 1
        H_total = R2r * 24.0
        hh = int(H_total) - 720 * mm - 24 * dd
        mi = int(round((H_total - int(H_total)) * 60.0))

        # 起运时刻（日历相加：先加整年整月[日对齐到目标月有效天数]，再加日时分）
        qy = _calendar_add(birth, yy, mm, dd, hh, mi)

        self._qiyun_breakdown = (yy, mm, dd, hh, mi)  # 供 qiyun_detail 暴露（问真 qiyunarr 前5位）
        self._qiyun_datetime = qy                      # 起运时刻 = 首个交运时刻（问真口径，P0-1）
        self._qiyun_desc = "出生后%d年%d月%d天%d时起运" % (yy, mm, dd, hh)
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

    def _calc_jiaoyun(self, qy_time: dt, breakdown: tuple, start_sui: int) -> dict:
        """交运信息（问真口径，P0-1；2026-08-19 经 7 例服务端 jiaoyun 数据验证 7/7）。

        问真排盘页/API jiaoyun 字段："逢X、Y年 节后N天 交大运"：
          1. 交运时刻 = 起运时刻（出生时刻 + 起运分解，按日历加法）
          2. 交运年 = 交运时刻所在年按立春界定（交运时刻 < 该年立春 → 取前一年）
          3. X、Y = 交运年天干 + 其五合之干（甲己/乙庚/丙辛/丁壬/戊癸）
          4. N = floor(交运时刻 − 所在节时刻)（节为 12 节之一，如"白露后27天"）
        客户端 fatemaps 另输出 "每逢 X、Y 年M月D日H时交脱大运"，本实现同用上述
        服务端口径的交运时刻（与排盘页/API 一致），格式字符串对齐问真。

        口径说明（对比报告 P2 项，2026-08-21）：起运虚岁已与问真 100% 对齐，但
        "节后 N 天"（交运日期）与本引擎节气口径存在 ≤2 天差异——节锚定规则不同
        （本引擎用问真节气表/起运时刻所在节 floor 天数，问真服务端按自身交运日
        口径锚定）。差异属口径而非错误，刻意不改输出（避免引入回归）；前端展示
        时以"约"字弱化精确对比即可。
        """
        if not breakdown or qy_time is None:
            return {}
        jy_time = qy_time
        # 交运年（立春界定）
        lichun = _jie_time_of(jy_time.year, "立春")
        if lichun is None:
            return {}
        V = jy_time.year if jy_time >= lichun else jy_time.year - 1
        stem_idx = (V + 4712 + 24) % 10
        gan1, gan2 = TIANGAN[stem_idx], TIANGAN[(stem_idx + 5) % 10]
        # 交运时刻所在节 + 节后天数（floor）
        jie_t, jie_name, n_days = None, "", -1
        for cand_year in (jy_time.year - 1, jy_time.year):
            for name in JIE_NAMES:
                jt = _jie_time_of(cand_year, name)
                if jt is not None and jt <= jy_time and (jie_t is None or jt > jie_t):
                    jie_t, jie_name = jt, name
        if jie_t is not None:
            n_days = int(math.floor((jy_time - jie_t).total_seconds() / 86400.0))
        # 交运年列表（每步大运 10 年一交，逐年立春界定）
        years = []
        for k in range(9):
            t = _calendar_add(qy_time, 10 * k, 0, 0)
            lc = _jie_time_of(t.year, "立春")
            if lc is None:
                break
            vk = t.year if t >= lc else t.year - 1
            years.append({
                "sui": start_sui + 10 * k,
                "year": vk,
                "ganzhi": SHENG_XU[(vk + 4712 + 24) % 60],
                "time": t.strftime("%Y-%m-%d %H:%M"),
            })
        return {
            "text": "每逢 %s、%s 年%d月%d日%d时交脱大运"
                    % (gan1, gan2, jy_time.month, jy_time.day, jy_time.hour),
            "page_text": "逢%s、%s年 %s后%d天 交大运" % (gan1, gan2, jie_name, n_days),
            "gan_pair": "%s、%s" % (gan1, gan2),
            "year": V,
            "year_ganzhi": SHENG_XU[(V + 4712 + 24) % 60],
            "time": jy_time.strftime("%Y-%m-%d %H:%M"),
            "jie": jie_name,
            "days_after_jie": n_days,
            "years": years,
        }

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

    @staticmethod
    def _pillar_year(year: int, month: int, day: int, hour: int, minute: int) -> int:
        """出生年柱所在干支年（立春界定，与年柱同口径，2026-08-20 流年基准年修复）。

        年柱由 lunar-python 在晚子时归一化后的日期上按立春换年排盘，故干支年 =
        归一化日期所在年，出生时刻早于该年立春 → 取前一年；比较时刻即排盘用到的
        归一化时刻（晚子时已归到次日 00:00，与年柱口径完全一致）。立春时刻取
        lunar-python 节气表（_jie_time_of，与年柱同源）；查不到（越界/表缺失）→
        按当年处理（与 _current_liunian 回退口径一致）。
        例：1999-02-10（立春 02-04 后、正月初一 02-16 前，农历年 1998）→ 1999；
            1999-01-20（立春前）→ 1998。
        """
        lichun = _jie_time_of(year, "立春")
        if lichun is None:
            return year
        return year - 1 if dt(year, month, day, hour, minute) < lichun else year

    def _current_liunian(self, birth_year: int, year_pillar: str) -> tuple:
        """当前流年（真年，立春界定）：返回 (年, 干支)（L2-4）。

        口径：立春前属上一年流年、立春后属当年（问真流年口径，与交运年同用
        立春界定）；立春节气查不到（越界/表缺失）时按当年处理。
        birth_year 为出生干支年（立春界定，与年柱同口径，见 _pillar_year）。
        干支 = 年柱 + 岁差（liunian_ganzhi）。
        """
        now = dt.now()
        lichun = _jie_time_of(now.year, "立春")
        if lichun is None:
            year = now.year
        else:
            year = now.year if now >= lichun else now.year - 1
        return year, liunian_ganzhi(birth_year, year_pillar, year)

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

    @staticmethod
    def _calc_ganzhi_rel(pillars: list) -> list:
        """原局四柱间两两干支关系（问真 newgetGZRelaction 同款规则，P0-3）。

        只输出有关系的对（避免 4×4 噪音）；盖头/截脚为单柱柱内性质，不在此列
        （大运/流年视角的盖头/截脚见 BaziResult.rel_with）。
        返回 [{between: '年-月', type, desc}]。
        """
        out = []
        seen = set()
        for i in range(4):
            for j in range(i + 1, 4):
                for it in analyze_relations(pillars[i], pillars[j], pillars):
                    if it.type in ("盖头", "截脚"):
                        continue
                    if it.type in ("合", "争合", "妒合"):
                        key = (it.type, it.desc)  # 多柱参与的作用，仅首个柱对输出
                    else:
                        key = (i, j, it.type, it.desc)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({
                        "between": "%s-%s" % ("年月日时"[i], "年月日时"[j]),
                        "type": it.type,
                        "desc": it.desc,
                    })
        return out
