"""万年历引擎 — 日历 + 干支 + 宜忌（问真"吉真万年历"同款确定性功能，0 LLM）。

数据基础（均为确定性纯规则，无任何随机/LLM 成分）:
- lunar-python（Solar→Lunar）: 公历↔农历转换、年/月/日干支、节气、纳音、
  黄黑道十二值神（getDayTianShen/Type/Luck）、吉神宜趋（getDayJiShen）、
  凶煞宜忌（getDayXiongSha）、冲煞（getDayChongDesc/getDaySha）、
  财神/喜神/福神/贵神方位（getDayPosition*）、时辰吉凶（getTimes，早/晚子时
  13 项）、彭祖百忌（getPengZuGan/Zhi）、胎神占方（getDayPositionTai）、
  旬空（八字的 DayXunKong）、节日、星期。
- src/engines/zeri.py: 建除十二神（月支起建 _calc_jianchu_with_jieqi，节气日
  12 节交节即新月令顺推一位，对齐主流通书）+ 建除宜忌表（JIANCHU_YI_JI，传统
  通书《协纪辨方书》建除十二神宜忌规则）+ 建除吉凶（JIANCHU_QUALITY）+
  二十八宿值日（lunar-python getXiu + 传统吉凶表 ERSHIBA_XIU_JIXIONG）。

宜忌规则（标准黄历，与择日引擎 zeri.py 同源数据，注释来源见上）:
  宜 = 建除十二神宜（JIANCHU_YI_JI，建除在前） + lunar-python 当日黄历宜
       （getDayYi，通胜逐日宜忌表）按序去重合并;
  忌 = 同理（建除忌 + getDayJi 去重合并）。
  注（k23 起）：建除表 + 黄历的合并与择吉侧同源（zeri.ZeriEngine._day_yi_ji，
  择吉 chat/工具路径与计划路径的单一事实源），但本模块**不接**其 K3-A3 神煞级
  优先消解（建除表忌与当日黄历宜冲突时以黄历宜为准），只做下方「忌优先」消解 ——
  故在“建除表忌 ∩ 当日黄历宜”冲突日, 万年历页与择吉结果方向可以不同
  （实证 2026-10-01 本模块 忌出行/入宅/移徙, 择吉口径为 宜; 2026-09-20 本模块
  忌安葬, 择吉口径为 宜）。是否统一到神煞级优先待产品拍板, 未拍板前保持本模块
  既有“忌优先”展示语义（见 _resolve_yi_ji_conflicts）。
  冲突消解（对比报告 P2 项，2026-08-21）：合并后同一事项同时出现在宜、忌时
       （如 2026-08-21 既宜又忌"嫁娶"），按忌优先（保守口径——通书惯例：忌示
       不宜行事，宁可错忌不可错宜；见 _resolve_yi_ji_conflicts）从宜中剔除、
       保留于忌。月视图 yi_short/ji_short 与日详情 yi/ji 同一消解。
  哨兵过滤（k26）：黄历侧哨兵「无」（无忌事/无宜事日）在合并入口统一剔除，
       与择吉侧同源同语义（zeri.filter_yi_ji_sentinel 单一实现）——k23 只修了
       择吉面，用户面万年历仍放行「忌：无」（2026-11-07 忌第 4 项实证）。
  黄黑道 = lunar-python 十二值神: 青龙/明堂/金匮/天德/玉堂/司命 为黄道（吉）；
           天刑/朱雀/白虎/天牢/玄武/勾陈 为黑道（凶）。
  值日吉凶 quality = 建除十二神吉凶（JIANCHU_QUALITY: 吉/平/凶）。
"""
import calendar as _cal
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from lunar_python import Solar

from src.engines.zeri import (
    JIANCHU_QUALITY,
    JIANCHU_YI_JI,
    ZeriEngine,
    filter_yi_ji_sentinel,
)

BJT = timezone(timedelta(hours=8))
MIN_YEAR, MAX_YEAR = 1900, 2100  # lunar-python 历法支持范围

_zeri = ZeriEngine()  # 复用建除/二十八宿计算（纯函数，无副作用）


def _month_range(year: int, month: int) -> int:
    """当月天数（标准公历）。"""
    return _cal.monthrange(year, month)[1]


def _lunar_day_cn(day: int) -> str:
    """农历日中文: 初一..初十 / 十一..十九 / 二十 / 廿一..廿九 / 三十（与 zeri 一致）。"""
    _CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
    if day == 10:
        return "初十"
    if day == 20:
        return "二十"
    if day == 30:
        return "三十"
    if day < 10:
        return "初" + _CN[day]
    if day < 20:
        return "十" + _CN[day % 10]
    if day < 30:
        return "廿" + _CN[day % 10]
    return str(day)


def _chong_parse(desc: str) -> Dict[str, str]:
    """解析冲煞描述 "(己未)羊" → {"ganzhi": "己未", "zodiac": "羊"}。"""
    m = re.search(r"[（(]([一-鿿]{2})[)）]\s*([一-鿿]+)", desc or "")
    if m:
        return {"ganzhi": m.group(1), "zodiac": m.group(2)}
    return {"ganzhi": "", "zodiac": desc or ""}


def _merge_yi_ji(jianchu_yi: List[str], day_yi: List[str]) -> List[str]:
    """宜/忌合并: 建除在前 + lunar-python 当日黄历，按序去重（与 zeri.py 同口径）。

    k26：黄历侧（day_yi 入参，调用点恒传 lunar.getDayYi/getDayJi）的哨兵「无」
    在此剔除 —— 与 zeri._day_yi_ji 共用同一实现（filter_yi_ji_sentinel），
    修复万年历面「忌：无」泄漏；建除表侧无哨兵词条（表内实证无「无」）。
    """
    return list(dict.fromkeys(
        list(jianchu_yi) + filter_yi_ji_sentinel(day_yi)))


def _resolve_yi_ji_conflicts(yi: List[str], ji: List[str]) -> tuple:
    """宜忌冲突消解（忌优先，保守口径，对比报告 P2 项）。

    合并后同一事项同时出现在宜、忌（如 2026-08-21 既宜又忌"嫁娶"）时，通书惯例
    以忌为准（忌示当日不宜行事，保守口径：宁可错忌、不可错宜），从宜中剔除该
    事项、保留于忌。忌列表不变（消解只影响宜）。
    返回 (消解后宜, 忌)。月视图与日详情共用此消解，保证两处口径一致。
    """
    ji_set = set(ji)
    return [x for x in yi if x not in ji_set], ji


def _jieqi_and_festival(lunar) -> tuple:
    """(当日节气名 or "", [节日列表])。节气当日返回节气名（立秋等），否则空串。"""
    return (lunar.getJieQi() or ""), list(lunar.getFestivals() or [])


def _day_jishi(lunar) -> List[Dict[str, Any]]:
    """一日时辰吉凶（lunar-python lunar.getTimes()，B5-2 L）。

    getTimes() 返回 13 项覆盖全天 24h：0 = 早子时 00:00-00:59、1..11 = 丑时..亥时、
    12 = 晚子时 23:00-23:59（子时按传统早/晚拆分；晚子时换日，时柱属次日）。
    每项含 时辰名/起止区间/时柱干支/吉凶/值神/黄黑道/宜事/忌事，全部确定性纯规则。
    """
    times = lunar.getTimes() or []
    out: List[Dict[str, Any]] = []
    for i, t in enumerate(times):
        if i == 0:
            name = "早子时"
        elif i == len(times) - 1:
            name = "晚子时"
        else:
            name = f"{t.getZhi()}时"
        out.append({
            "time": name,
            "range": f"{t.getMinHm()}-{t.getMaxHm()}",
            "ganzhi": t.getGanZhi() or "",
            "luck": t.getTianShenLuck() or "",   # 吉/凶（值神口径）
            "tianshen": t.getTianShen() or "",
            "type": t.getTianShenType() or "",   # 黄道/黑道
            "yi": list(t.getYi() or []),
            "ji": list(t.getJi() or []),
        })
    return out


class WannianliEngine:
    """万年历引擎：月视图 + 日详情，全部确定性纯规则。"""

    # ---------------------------------------------------------------- 月视图

    def month_view(self, year: int, month: int) -> Dict[str, Any]:
        """当月日历：每日 公历/农历/干支日/节气标记/宜忌简表/黄黑道/建除。

        Returns:
            {"year", "month", "days_in_month", "first_weekday"(0=周日, calendar.monthrange
             口径，前端用于宫格偏移), "today"(北京时间 YYYY-MM-DD),
             "days": [每日摘要 × 当月天数]}
        """
        if not (MIN_YEAR <= year <= MAX_YEAR):
            raise ValueError(f"年份须在 {MIN_YEAR}-{MAX_YEAR} 之间: {year}")
        if not (1 <= month <= 12):
            raise ValueError(f"月份须在 1-12 之间: {month}")

        total = _month_range(year, month)
        first_weekday = _cal.monthrange(year, month)[0]
        today = datetime.now(BJT).strftime("%Y-%m-%d")

        days = []
        for day in range(1, total + 1):
            solar = Solar.fromYmd(year, month, day)
            lunar = solar.getLunar()
            jieqi, festivals = _jieqi_and_festival(lunar)
            jianchu = _zeri._calc_jianchu_with_jieqi(
                lunar.getEightChar().getMonth()[1],
                lunar.getEightChar().getDay()[1],
                jieqi,
            )
            yi = _merge_yi_ji(JIANCHU_YI_JI[jianchu]["yi"], list(lunar.getDayYi() or []))
            ji = _merge_yi_ji(JIANCHU_YI_JI[jianchu]["ji"], list(lunar.getDayJi() or []))
            # 宜忌冲突消解（忌优先）——与日详情同一口径（对比报告 P2）
            yi, ji = _resolve_yi_ji_conflicts(yi, ji)

            days.append({
                "date": f"{year:04d}-{month:02d}-{day:02d}",
                "day": day,
                # 农历小字：节气日显示节气名（传统黄历口径），否则显示农历日
                "cell_lunar": jieqi or _lunar_day_cn(lunar.getDay()),
                "lunar_day": _lunar_day_cn(lunar.getDay()),
                "jieqi": jieqi,
                "festival": festivals[0] if festivals else "",
                "day_ganzhi": lunar.getDayInGanZhi(),
                # 宜忌简表：建除+黄历合并后前 3 项（含"诸事不宜"等原样保留）
                "yi_short": yi[:3],
                "ji_short": ji[:3],
                "huanghedao": lunar.getDayTianShenType(),   # 黄道/黑道
                "tianshen": lunar.getDayTianShen(),          # 值神（明堂/金匮…）
                "jianchu": jianchu,                          # 建除十二神
                "quality": JIANCHU_QUALITY[jianchu],         # 吉/平/凶（建除口径）
                "is_today": f"{year:04d}-{month:02d}-{day:02d}" == today,
            })

        return {
            "year": year,
            "month": month,
            "days_in_month": total,
            "first_weekday": first_weekday,
            "today": today,
            "days": days,
        }

    # ---------------------------------------------------------------- 日详情

    def day_detail(self, year: int, month: int, day: int) -> Dict[str, Any]:
        """单日详情：干支/纳音/节气/宜/忌/吉神凶煞/冲煞/值神/建除/方位/旬空。

        Args:
            year/month/day: 公历日期。
        Returns:
            全字段详情 dict（见函数体内注释，字段名即前端契约）。
        """
        if not (MIN_YEAR <= year <= MAX_YEAR):
            raise ValueError(f"年份须在 {MIN_YEAR}-{MAX_YEAR} 之间: {year}")
        if not (1 <= month <= 12):
            raise ValueError(f"月份须在 1-12 之间: {month}")
        if not (1 <= day <= _month_range(year, month)):
            raise ValueError(f"日期不存在: {year}-{month}-{day}")

        solar = Solar.fromYmd(year, month, day)
        lunar = solar.getLunar()
        ec = lunar.getEightChar()

        jieqi, festivals = _jieqi_and_festival(lunar)
        day_zhi = ec.getDay()[1]
        month_zhi = ec.getMonth()[1]
        jianchu = _zeri._calc_jianchu_with_jieqi(month_zhi, day_zhi, jieqi)
        yi = _merge_yi_ji(JIANCHU_YI_JI[jianchu]["yi"], list(lunar.getDayYi() or []))
        ji = _merge_yi_ji(JIANCHU_YI_JI[jianchu]["ji"], list(lunar.getDayJi() or []))
        # 宜忌冲突消解（忌优先）——与月视图 yi_short/ji_short 同一口径（对比报告 P2）
        yi, ji = _resolve_yi_ji_conflicts(yi, ji)
        chong_desc = lunar.getDayChongDesc() or ""
        chong = _chong_parse(chong_desc)
        chong["sha"] = lunar.getDaySha() or ""          # 煞方（东/南/西/北）
        xiu_name, xiu_jixiong = _zeri._calc_ershibaxiu(year, month, day)
        leap = lunar.getMonth() < 0                     # 闰月（2025 闰六月等）

        return {
            "date": f"{year:04d}-{month:02d}-{day:02d}",
            "weekday": lunar.getWeekInChinese(),        # 三（星期）
            "jieqi": jieqi,                             # 当日节气（立秋），无则空串
            "festivals": festivals,                     # 传统节日（七夕节…）
            # 农历
            "lunar": {
                "year": f"{lunar.getYearInGanZhi()}年",
                "month": f"{lunar.getMonthInChinese()}月",   # 闰月自带"闰"前缀
                "day": _lunar_day_cn(lunar.getDay()),
                "leap": leap,
                "full": f"{lunar.getYearInChinese()}年{lunar.getMonthInChinese()}月"
                        f"{_lunar_day_cn(lunar.getDay())}",
            },
            # 干支（年月日）
            "ganzhi": {"year": ec.getYear(), "month": ec.getMonth(), "day": ec.getDay()},
            # 纳音（年月日）
            "nayin": {
                "year": lunar.getYearNaYin(),
                "month": lunar.getMonthNaYin(),
                "day": lunar.getDayNaYin(),
            },
            # 建除十二神（标准黄历值日）
            "jianchu": {
                "name": jianchu,
                "quality": JIANCHU_QUALITY[jianchu],    # 吉/平/凶
                "desc": JIANCHU_YI_JI[jianchu]["desc"],
            },
            # 黄黑道十二值神
            "huanghedao": {
                "type": lunar.getDayTianShenType(),     # 黄道/黑道
                "tianshen": lunar.getDayTianShen(),     # 明堂/金匮…
                "luck": lunar.getDayTianShenLuck(),     # 吉/凶
            },
            # 二十八宿值日（lunar-python getXiu 口径，P1-1 审查 C1: 旧锚点 2000-01-01
            # 错标虚宿(实为壁) → 全日期差 3 天，已统一为 getXiu，2000-01-01=胃）
            "ershibaxiu": {"name": xiu_name, "jixiong": xiu_jixiong},
            # 宜/忌（建除 + 当日黄历合并，去重）
            "yi": yi,
            "ji": ji,
            # 吉神宜趋 / 凶煞宜忌（lunar-python 通胜口径）
            "jishen": list(lunar.getDayJiShen() or []),
            "xiongsha": list(lunar.getDayXiongSha() or []),
            # 冲煞: 冲(己未)羊 · 煞东
            "chong": {
                "desc": chong_desc,
                "zodiac": chong.get("zodiac", ""),
                "ganzhi": chong.get("ganzhi", ""),
                "sha": chong.get("sha", ""),
            },
            # 旬空（日柱旬空两支）
            "xunkong": list(ec.getDayXunKong() or []),
            # 财神/喜神/福神/阳贵/阴贵方位
            "positions": {
                "cai": lunar.getDayPositionCaiDesc(),        # 财神方位
                "xi": lunar.getDayPositionXiDesc(),          # 喜神方位
                "fu": lunar.getDayPositionFuDesc(),          # 福神方位
                "yang_gui": lunar.getDayPositionYangGuiDesc(),  # 阳贵神方位
                "yin_gui": lunar.getDayPositionYinGuiDesc(),    # 阴贵神方位
            },
            # 吉时（时辰吉凶，B5-2 L）: 早子时 00:00-00:59 … 晚子时 23:00-23:59
            "jishi": _day_jishi(lunar),
            # 彭祖百忌（B5-2 L）: 天干日忌 + 地支日忌
            "pengzu": {
                "gan": lunar.getPengZuGan() or "",
                "zhi": lunar.getPengZuZhi() or "",
            },
            # 胎神占方（B5-2 L）: 如「碓磨厕 外东南」
            "taishen": {"desc": lunar.getDayPositionTai() or ""},
        }
