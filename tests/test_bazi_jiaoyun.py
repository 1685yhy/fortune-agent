"""交运 + 人元司令 测试（问真口径，P0-1）。

锚点来源：
1. 问真排盘页（2026-08-19 探索，07_result_now.txt）：1999-05-13 11:25 男 →
   起运"出生后2年4月22天0时起运" / 交运"逢辛、丙年 白露后27天 交大运" / 司令"庚"
2. 问真 API jiaoyun 字段 6 例（data/wenzhen/wenzhen_charts.jsonl，1900 年出生）
3. 问真 chunk2 内嵌《子平真诠》分日决人元司令分野表（寅:戊7丙7甲16 等）

算法口径（与问真服务端一致）：
- 交运时刻 = 起运时刻（出生时刻 + 起运分解，按日历加法）
- 交运年 = 交运时刻所在年按立春界定；X、Y = 交运年天干 + 五合之干
- 节后天数 N = floor(交运时刻 − 所在节时刻)
"""
import re
from datetime import datetime as dt, timedelta

import pytest

from src.engines.bazi import BaziEngine, BaziResult, SILING_TABLE, sizhilingxiu


ENGINE = BaziEngine()


# ---------------------------------------------------------------- 锚点：问真排盘页
def test_anchor_qz_result_page():
    """问真排盘页锚点：1999-05-13 11:25 男（闫海洋命例）。

    页面原文：
      起运：出生后2年4月22天0时起运
      交运：逢辛、丙年 白露后27天 交大运
      司令：庚
    """
    r = ENGINE.calculate(1999, 5, 13, 11, 25, "", "男")
    # 起运（浮点边界：立夏后 7.18333 天整 → 2年4月22天0时，非 21天24时）
    assert list(r.qiyun_detail) == [2, 4, 22, 0, 0]
    assert r.qiyun_desc == "出生后2年4月22天0时起运"
    # 交运（排盘页格式）
    jy = r.jiaoyun
    assert jy["page_text"] == "逢辛、丙年 白露后27天 交大运"
    assert jy["gan_pair"] == "辛、丙"          # 五合天干对（辛丙合）
    assert jy["year"] == 2001 and jy["year_ganzhi"] == "辛巳"  # 立春界定交运年
    assert jy["time"] == "2001-10-05 11:25"    # 交运时刻 = 起运时刻
    assert jy["jie"] == "白露" and jy["days_after_jie"] == 27
    # 问真 fatemaps 客户端格式："每逢 X、Y 年M月D日H时交脱大运"
    assert jy["text"] == "每逢 辛、丙 年10月5日11时交脱大运"
    # 司令
    assert r.siling == "庚"
    assert r.siling_detail["gan"] == "庚"
    assert r.siling_detail["days"] == 9        # 巳月庚金用事 9 天（戊5庚9丙16）
    assert r.siling_detail["jie"] == "立夏"
    assert 7.0 < r.siling_detail["elapsed"] < 7.5
    assert 6.5 < r.siling_detail["remaining"] < 7.0


# ---------------------------------------------------------------- 锚点：问真 API 6 例
API_CASES = [
    ("1900-01-01", "00:00", "女", "逢辛、丙年 白露后2天 交大运"),
    ("1900-01-01", "08:00", "女", "逢辛、丙年 小暑后24天 交大运"),
    ("1900-01-01", "08:00", "男", "逢戊、癸年 惊蛰后19天 交大运"),
    ("1900-01-01", "16:00", "男", "逢戊、癸年 立夏后0天 交大运"),
    ("1900-01-15", "00:00", "男", "逢壬、丁年 大雪后26天 交大运"),
    ("1900-01-28", "00:00", "男", "逢丁、壬年 立夏后10天 交大运"),
]


@pytest.mark.parametrize("date,time,gender,expect", API_CASES,
                         ids=[f"{d} {t} {g}" for d, t, g, _ in API_CASES])
def test_api_jiaoyun(date, time, gender, expect):
    """问真 API jiaoyun 字段 6 例全等（服务端真实返回值）。"""
    y, m, d = (int(x) for x in date.split("-"))
    h, mi = (int(x) for x in time.split(":"))
    r = ENGINE.calculate(y, m, d, h, mi, "", gender)
    assert r.jiaoyun["page_text"] == expect
    # 五合对 = 交运年天干 + 五合之干（甲己/乙庚/丙辛/丁壬/戊癸）
    assert r.jiaoyun["gan_pair"] in ("辛、丙", "丙、辛", "戊、癸", "癸、戊",
                                     "壬、丁", "丁、壬")


# ---------------------------------------------------------------- 输出格式
def test_jiaoyun_text_format():
    """"每逢 X、Y 年M月D日H时交脱大运" 格式对齐问真 fatemaps。"""
    r = ENGINE.calculate(1999, 5, 13, 11, 25, "", "男")
    pat = r"^每逢 [甲乙丙丁戊己庚辛壬癸]、[甲乙丙丁戊己庚辛壬癸] 年\d+月\d+日\d+时交脱大运$"
    assert re.match(pat, r.jiaoyun["text"]), r.jiaoyun["text"]


def test_jiaoyun_years_list():
    """交运年列表：每步大运 10 年一交，交运年天干与五合对一致。"""
    r = ENGINE.calculate(1999, 5, 13, 11, 25, "", "男")
    years = r.jiaoyun["years"]
    assert len(years) == 9
    assert [(y["sui"], y["year"], y["ganzhi"]) for y in years[:4]] == [
        (3, 2001, "辛巳"), (13, 2011, "辛卯"), (23, 2021, "辛丑"), (33, 2031, "辛亥"),
    ]
    assert all(y["ganzhi"][0] == "辛" for y in years)  # 交运年均落在辛年
    assert years[1]["time"] == "2011-10-05 11:25"       # 大运间隔 10 年


# ---------------------------------------------------------------- 人元司令
SILING_CASES = [
    # (月支, 生日, (司令干, 用事天数), 说明)  2001 年节气：立春2/4 立夏5/5 立秋8/7 大雪12/7
    ("巳", (2001, 5, 8, 12, 0), ("戊", 5), "立夏后3天 戊"),
    ("巳", (2001, 5, 16, 12, 0), ("庚", 9), "立夏后11天 庚"),
    ("巳", (2001, 5, 27, 12, 0), ("丙", 16), "立夏后22天 丙"),
    ("寅", (2001, 2, 8, 12, 0), ("戊", 7), "立春后4天 戊"),
    ("寅", (2001, 2, 16, 12, 0), ("丙", 7), "立春后12天 丙"),
    ("寅", (2001, 3, 2, 12, 0), ("甲", 16), "立春后26天 甲"),
    ("申", (2001, 8, 10, 12, 0), ("戊己", 10), "立秋后3天 戊己共10日"),
    ("申", (2001, 8, 20, 12, 0), ("壬", 3), "立秋后13天 壬"),
    ("子", (2001, 12, 10, 12, 0), ("壬", 10), "大雪后3天 壬"),
    ("子", (2001, 12, 20, 12, 0), ("癸", 20), "大雪后13天 癸"),
]


@pytest.mark.parametrize("zhi,date,expect,note", SILING_CASES,
                         ids=[n for _, _, _, n in SILING_CASES])
def test_siling_table(zhi, date, expect, note):
    """人元司令分野表（问真《子平真诠》分日决）分段正确。"""
    assert sizhilingxiu(zhi, date) == expect


def test_siling_table_totals():
    """分野表各月支用事天数合计 30 天（与问真参考表一致）。"""
    assert all(sum(d for _, d in segs) == 30 for segs in SILING_TABLE.values())


def test_siling_boundary_rule():
    """分野边界：恰好等于段天数 → 落入下一段（>= 规则）。"""
    from lunar_python import Solar
    # 2001 立夏时刻 + 5 天整 → 戊5天已尽 → 庚
    jie = Solar.fromYmdHms(2001, 6, 1, 12, 0, 0).getLunar().getJieQiTable()["立夏"]
    jie_time = dt(jie.getYear(), jie.getMonth(), jie.getDay(),
                  jie.getHour(), jie.getMinute(), jie.getSecond())
    assert sizhilingxiu("巳", jie_time + timedelta(days=5)) == ("庚", 9)
    assert sizhilingxiu("巳", jie_time + timedelta(days=4.9)) == ("戊", 5)


def test_siling_via_calculate():
    """calculate() 集成：巳月出生带出司令。"""
    r = ENGINE.calculate(1999, 5, 13, 11, 25, "", "男")
    assert r.bazi[1][1] == "巳"          # 巳月
    assert r.siling == "庚"
    r2 = ENGINE.calculate(2001, 3, 1, 12, 0, "", "男")
    assert r2.siling == "甲"             # 寅月立春后约25天 → 甲


# ---------------------------------------------------------------- 兼容性
def test_result_backward_compat():
    """新增字段默认值：旧消费方（advisor/hehun/前端等）读 BaziResult 不破坏。"""
    r = BaziResult(bazi=["庚午", "辛巳", "乙酉", "甲申"], day_master="乙木",
                   wuxing={}, shishen=[], dayun=[], liunian={},
                   geju="", yongshen="", shensha=[], nayin=[])
    assert r.jiaoyun == {}
    assert r.siling == ""
    assert r.qiyun_desc == ""
    assert r.siling_detail == {}
    # 计算路径字段齐全
    rr = ENGINE.calculate(1990, 1, 1, 0, 0, "", "男")
    assert isinstance(rr.jiaoyun, dict) and rr.jiaoyun.get("page_text")
    assert rr.qiyun_desc.startswith("出生后")
    assert rr.siling  # 子月出生必有司令
