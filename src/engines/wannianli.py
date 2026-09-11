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
  通书《协纪辨方书》建除十二神宜忌规则）+
  二十八宿值日（lunar-python getXiu + 传统吉凶表 ERSHIBA_XIU_JIXIONG）。

宜忌规则（标准黄历，与择日引擎 zeri.py 同源数据，注释来源见上）:
  宜/忌 = **直接取 zeri.ZeriEngine._day_yi_ji(jianchu, lunar)** —— 与择吉
       chat/工具路径（select）和计划路径（吉日卡片）同一实现、同一输出，本模块
       不再自行合并/消解（数据一致性铁律；k23 只修了择吉面，万年历面漏到 k27）。
  注（k27, 产品 2026-09-11 拍板「对齐权威黄历」= 选项 A）：建除表忌与当日黄历宜
       冲突时以黄历宜为准（K3-A3 神煞级优先）; 反方向「建除表宜 ∩ 当日黄历忌」同样
       以黄历为准（词归忌）。此前本模块的「忌优先」消解（_resolve_yi_ji_conflicts）
       使万年历与择吉两面朝**相反方向**消解同一冲突（实证 2026-10-01 本模块
       忌 出行/入宅/移徙, 择吉为 宜; 2026-09-20 本模块 忌 安葬, 择吉为 宜）——
       该口径与 _merge_yi_ji/_resolve_yi_ji_conflicts 一并删除。
  哨兵过滤（k26, 实现随 `_day_yi_ji` 一并收敛）：黄历侧哨兵「无」（无忌事/无宜事
       日）在 `_day_yi_ji` 入口统一剔除（zeri.filter_yi_ji_sentinel 单一实现）——
       k23 只修了择吉面，用户面万年历仍放行「忌：无」（2026-11-07 忌第 4 项实证）。
  空忌日（k27 起可能出现, 2026 仅 2026-02-10 一天）: 日详情/月视图如实返回空列表,
       `ji_short` 同为空 —— 文案层由消费方决定（handler._yi_ji_render_lines 整行
       不渲染），本模块不新造文案。
  黄黑道 = lunar-python 十二值神: 青龙/明堂/金匮/天德/玉堂/司命 为黄道（吉）；
           天刑/朱雀/白虎/天牢/玄武/勾陈 为黑道（凶）。
  建除吉凶标签 quality（k27c, 产品 2026-09-11 拍板）: **已下线, 不再输出** ——
       值日只给名（建除十二神名）; 吉凶总评只在择吉/聊天给（三面同源）。原字段由
       本地表 JIANCHU_QUALITY 派生（非权威历法数据）, 与 chat `overall` 在 2026 有
       73 天方向相反（如 2026-10-01 万年历「闭（凶）」↔ 聊天吉）。
"""
import calendar as _cal
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from lunar_python import Solar

from src.engines.zeri import (
    JIANCHU_YI_JI,
    ZeriEngine,
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


def _day_yi_ji(jianchu: str, lunar) -> tuple:
    """单日宜忌（薄封装）—— 直接转发择吉引擎的单一事实源 `_zeri._day_yi_ji`。

    k27：本模块**只经此一处**取宜忌（月视图与日详情同源），不得自行合并/消解 ——
    万年历面与择吉面（chat/工具/计划路径）逐日逐项相等（tests/test_wannianli.py
    k27 扫描钉住）。保留本函数的理由：模块内单一调用点 + 语义注释（合并/消解口径
    见 zeri._day_yi_ji docstring），而非再写一套实现。
    """
    return _zeri._day_yi_ji(jianchu, lunar)


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
            # 宜忌：单一事实源（与择吉面/日详情同源, k27）
            yi, ji = _day_yi_ji(jianchu, lunar)

            days.append({
                "date": f"{year:04d}-{month:02d}-{day:02d}",
                "day": day,
                # 农历小字：节气日显示节气名（传统黄历口径），否则显示农历日
                "cell_lunar": jieqi or _lunar_day_cn(lunar.getDay()),
                "lunar_day": _lunar_day_cn(lunar.getDay()),
                "jieqi": jieqi,
                "festival": festivals[0] if festivals else "",
                "day_ganzhi": lunar.getDayInGanZhi(),
                # 宜忌简表：单一事实源（_day_yi_ji）前 3 项（含"诸事不宜"等原样保留；
                # 空忌日如 2026-02-10 → ji_short=[]，文案层决定是否渲染）
                "yi_short": yi[:3],
                "ji_short": ji[:3],
                "huanghedao": lunar.getDayTianShenType(),   # 黄道/黑道
                "tianshen": lunar.getDayTianShen(),          # 值神（明堂/金匮…）
                "jianchu": jianchu,                          # 建除十二神（值日名）
                # k27c: 原 "quality"（建除吉凶 吉/平/凶）已下线 —— 万年历面不再
                # 向用户展示吉/凶判定, 只留值日名; 吉凶总评在择吉/聊天给（三面同源）。
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
        # 宜忌：单一事实源（与择吉面/月视图同源, k27）
        yi, ji = _day_yi_ji(jianchu, lunar)
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
            # k27c: 原 `quality`（建除吉凶 吉/平/凶）字段已下线（产品 2026-09-11
            # 拍板）—— 万年历面只给值日名; 见模块 docstring。
            "jianchu": {
                "name": jianchu,
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
            # 宜/忌（单一事实源 _day_yi_ji: 建除表 + 神煞级黄历, K3-A3 双向消解；
            # 与择吉 select()/卡片同源, k27。空忌日 → ji=[]）
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
