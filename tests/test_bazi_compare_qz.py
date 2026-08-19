"""八字排盘对比测试库 — 与问真八字(bzapi3.iwzbz.com)对齐的固化期望值。

数据来源：问真八字 API（getbasebz8.php，s=0女/s=1男），2026-08-19 采集后写死，测试不依赖网络。
覆盖：四季 / 节气交界前后 / 闰月 / 各时辰 / 晚子时 / 跨年，含闫海洋案例（1999-5-13 11:25 男）。
断言维度：四柱8字、大运前5步干支、起运虚岁（问真 qiyunsui 口径）。
"""
import pytest

from src.engines.bazi import BaziEngine


ENGINE = BaziEngine()


CASES = [
    {
        "name": '闫海洋案例',
        "date": "1999-5-13",
        "time": "11:25",
        "gender": '男',
        "expect": {
            "sizhu": ["己卯", "己巳", "乙丑", "壬午"],
            "dayun": ["戊辰", "丁卯", "丙寅", "乙丑", "甲子"],
            "qiyun_sui": 3,
        },
    },
    {
        "name": '春季-立春后',
        "date": "1990-2-10",
        "time": "08:30",
        "gender": '女',
        "expect": {
            "sizhu": ["庚午", "戊寅", "丙午", "壬辰"],
            "dayun": ["丁丑", "丙子", "乙亥", "甲戌", "癸酉"],
            "qiyun_sui": 3,
        },
    },
    {
        "name": '夏季-立夏前1小时',
        "date": "1999-5-6",
        "time": "06:00",
        "gender": '女',
        "expect": {
            "sizhu": ["己卯", "戊辰", "戊午", "乙卯"],
            "dayun": ["己巳", "庚午", "辛未", "壬申", "癸酉"],
            "qiyun_sui": 1,
        },
    },
    {
        "name": '夏季-立夏后2小时',
        "date": "1999-5-6",
        "time": "09:00",
        "gender": '男',
        "expect": {
            "sizhu": ["己卯", "己巳", "戊午", "丁巳"],
            "dayun": ["戊辰", "丁卯", "丙寅", "乙丑", "甲子"],
            "qiyun_sui": 1,
        },
    },
    {
        "name": '芒种后次日',
        "date": "1999-6-7",
        "time": "12:30",
        "gender": '女',
        "expect": {
            "sizhu": ["己卯", "庚午", "庚寅", "壬午"],
            "dayun": ["辛未", "壬申", "癸酉", "甲戌", "乙亥"],
            "qiyun_sui": 11,
        },
    },
    {
        "name": '秋季-立秋前后',
        "date": "1995-8-8",
        "time": "10:00",
        "gender": '男',
        "expect": {
            "sizhu": ["乙亥", "甲申", "辛未", "癸巳"],
            "dayun": ["癸未", "壬午", "辛巳", "庚辰", "己卯"],
            "qiyun_sui": 1,
        },
    },
    {
        "name": '冬季-冬至附近',
        "date": "1995-12-22",
        "time": "18:30",
        "gender": '女',
        "expect": {
            "sizhu": ["乙亥", "戊子", "丁亥", "己酉"],
            "dayun": ["己丑", "庚寅", "辛卯", "壬辰", "癸巳"],
            "qiyun_sui": 6,
        },
    },
    {
        "name": '闰八月内',
        "date": "1995-9-5",
        "time": "03:15",
        "gender": '男',
        "expect": {
            "sizhu": ["乙亥", "甲申", "己亥", "丙寅"],
            "dayun": ["癸未", "壬午", "辛巳", "庚辰", "己卯"],
            "qiyun_sui": 10,
        },
    },
    {
        "name": '闰五月内',
        "date": "1998-6-10",
        "time": "20:45",
        "gender": '女',
        "expect": {
            "sizhu": ["戊寅", "戊午", "戊子", "壬戌"],
            "dayun": ["丁巳", "丙辰", "乙卯", "甲寅", "癸丑"],
            "qiyun_sui": 2,
        },
    },
    {
        "name": '小寒当日',
        "date": "2000-1-6",
        "time": "09:30",
        "gender": '男',
        "expect": {
            "sizhu": ["己卯", "丁丑", "癸亥", "丁巳"],
            "dayun": ["丙子", "乙亥", "甲戌", "癸酉", "壬申"],
            "qiyun_sui": 1,
        },
    },
    {
        "name": '晚子时-23:30',
        "date": "2000-6-1",
        "time": "23:30",
        "gender": '女',
        "expect": {
            "sizhu": ["庚辰", "辛巳", "辛卯", "戊子"],
            "dayun": ["庚辰", "己卯", "戊寅", "丁丑", "丙子"],
            "qiyun_sui": 10,
        },
    },
    {
        "name": '立春当日-午时',
        "date": "2004-2-4",
        "time": "12:00",
        "gender": '男',
        "expect": {
            "sizhu": ["癸未", "乙丑", "癸丑", "戊午"],
            "dayun": ["甲子", "癸亥", "壬戌", "辛酉", "庚申"],
            "qiyun_sui": 10,
        },
    },
    {
        "name": '年末跨年',
        "date": "2004-12-31",
        "time": "23:15",
        "gender": '女',
        "expect": {
            "sizhu": ["甲申", "丙子", "乙酉", "丙子"],
            "dayun": ["乙亥", "甲戌", "癸酉", "壬申", "辛未"],
            "qiyun_sui": 10,
        },
    },
    {
        "name": '午时-正午',
        "date": "2005-6-1",
        "time": "12:00",
        "gender": '男',
        "expect": {
            "sizhu": ["乙酉", "辛巳", "丙辰", "甲午"],
            "dayun": ["庚辰", "己卯", "戊寅", "丁丑", "丙子"],
            "qiyun_sui": 10,
        },
    },
    {
        "name": '丑时-凌晨',
        "date": "2008-3-8",
        "time": "02:30",
        "gender": '女',
        "expect": {
            "sizhu": ["戊子", "乙卯", "丁未", "辛丑"],
            "dayun": ["甲寅", "癸丑", "壬子", "辛亥", "庚戌"],
            "qiyun_sui": 2,
        },
    },
    {
        "name": '辰时-上午',
        "date": "2010-7-15",
        "time": "07:45",
        "gender": '男',
        "expect": {
            "sizhu": ["庚寅", "癸未", "丙寅", "壬辰"],
            "dayun": ["甲申", "乙酉", "丙戌", "丁亥", "戊子"],
            "qiyun_sui": 9,
        },
    },
    {
        "name": '酉时-傍晚',
        "date": "2012-9-20",
        "time": "17:30",
        "gender": '女',
        "expect": {
            "sizhu": ["壬辰", "己酉", "甲申", "癸酉"],
            "dayun": ["戊申", "丁未", "丙午", "乙巳", "甲辰"],
            "qiyun_sui": 6,
        },
    },
    {
        "name": '巳时-上午',
        "date": "2014-11-11",
        "time": "10:15",
        "gender": '男',
        "expect": {
            "sizhu": ["甲午", "乙亥", "丙戌", "癸巳"],
            "dayun": ["丙子", "丁丑", "戊寅", "己卯", "庚辰"],
            "qiyun_sui": 10,
        },
    },
    {
        "name": '惊蛰当日前后',
        "date": "2016-3-5",
        "time": "14:00",
        "gender": '女',
        "expect": {
            "sizhu": ["丙申", "辛卯", "丙戌", "乙未"],
            "dayun": ["庚寅", "己丑", "戊子", "丁亥", "丙戌"],
            "qiyun_sui": 1,
        },
    },
    {
        "name": '寒露后',
        "date": "2016-10-9",
        "time": "06:30",
        "gender": '男',
        "expect": {
            "sizhu": ["丙申", "戊戌", "甲子", "丁卯"],
            "dayun": ["己亥", "庚子", "辛丑", "壬寅", "癸卯"],
            "qiyun_sui": 11,
        },
    },
    {
        "name": '小雪当月',
        "date": "2018-11-25",
        "time": "22:45",
        "gender": '女',
        "expect": {
            "sizhu": ["戊戌", "癸亥", "辛酉", "己亥"],
            "dayun": ["壬戌", "辛酉", "庚申", "己未", "戊午"],
            "qiyun_sui": 7,
        },
    },
    {
        "name": '大寒当月',
        "date": "2020-1-18",
        "time": "16:00",
        "gender": '男',
        "expect": {
            "sizhu": ["己亥", "丁丑", "庚申", "甲申"],
            "dayun": ["丙子", "乙亥", "甲戌", "癸酉", "壬申"],
            "qiyun_sui": 5,
        },
    },
    {
        "name": '闰四月内',
        "date": "2020-4-28",
        "time": "11:30",
        "gender": '女',
        "expect": {
            "sizhu": ["庚子", "庚辰", "辛丑", "甲午"],
            "dayun": ["己卯", "戊寅", "丁丑", "丙子", "乙亥"],
            "qiyun_sui": 9,
        },
    },
    {
        "name": '立春当日-酉时',
        "date": "2024-2-4",
        "time": "17:30",
        "gender": '男',
        "expect": {
            "sizhu": ["甲辰", "丙寅", "戊戌", "辛酉"],
            "dayun": ["丁卯", "戊辰", "己巳", "庚午", "辛未"],
            "qiyun_sui": 10,
        },
    },
    {
        "name": '夏至附近',
        "date": "2023-6-22",
        "time": "13:15",
        "gender": '女',
        "expect": {
            "sizhu": ["癸卯", "戊午", "辛亥", "乙未"],
            "dayun": ["己未", "庚申", "辛酉", "壬戌", "癸亥"],
            "qiyun_sui": 6,
        },
    },
    {
        "name": '子时-凌晨0点',
        "date": "1993-8-15",
        "time": "00:30",
        "gender": '男',
        "expect": {
            "sizhu": ["癸酉", "庚申", "戊辰", "壬子"],
            "dayun": ["己未", "戊午", "丁巳", "丙辰", "乙卯"],
            "qiyun_sui": 4,
        },
    },
    {
        "name": '卯时-日出',
        "date": "1988-8-8",
        "time": "05:45",
        "gender": '女',
        "expect": {
            "sizhu": ["戊辰", "庚申", "乙未", "己卯"],
            "dayun": ["己未", "戊午", "丁巳", "丙辰", "乙卯"],
            "qiyun_sui": 1,
        },
    },
    {
        "name": '深冬',
        "date": "1985-12-25",
        "time": "19:00",
        "gender": '男',
        "expect": {
            "sizhu": ["乙丑", "戊子", "戊戌", "壬戌"],
            "dayun": ["丁亥", "丙戌", "乙酉", "甲申", "癸未"],
            "qiyun_sui": 8,
        },
    },
    {
        "name": '元宵节',
        "date": "2010-2-28",
        "time": "09:30",
        "gender": '女',
        "expect": {
            "sizhu": ["庚寅", "戊寅", "己酉", "己巳"],
            "dayun": ["丁丑", "丙子", "乙亥", "甲戌", "癸酉"],
            "qiyun_sui": 9,
        },
    },
    {
        "name": '秋季深夜',
        "date": "1978-10-18",
        "time": "21:15",
        "gender": '男',
        "expect": {
            "sizhu": ["戊午", "壬戌", "癸丑", "癸亥"],
            "dayun": ["癸亥", "甲子", "乙丑", "丙寅", "丁卯"],
            "qiyun_sui": 8,
        },
    },
]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_compare_qz(case):
    """四柱 + 大运前5步 + 起运虚岁，三向对比问真八字固化期望值。"""
    y, m, d = (int(x) for x in case["date"].split("-"))
    h, mi = (int(x) for x in case["time"].split(":"))
    # 案例采集自问真 API 原始北京时间（未做真太阳时修正），故 city 传空保持不修正口径；
    # 真太阳时修正按出生地经度生效（见 tests/test_bazi_solar_time.py）
    result = ENGINE.calculate(y, m, d, h, mi, "", case["gender"])

    expect = case["expect"]
    # 四柱 8 字
    assert result.bazi == expect["sizhu"], (
        f"四柱不符 {case['name']}: {result.bazi} != {expect['sizhu']}"
    )
    # 大运前 5 步干支
    our_dayun = [g for _, g in result.dayun[:5]]
    assert our_dayun == expect["dayun"], (
        f"大运不符 {case['name']}: {our_dayun} != {expect['dayun']}"
    )
    # 起运虚岁
    assert result.dayun[0][0] == expect["qiyun_sui"], (
        f"起运虚岁不符 {case['name']}: {result.dayun[0][0]} != {expect['qiyun_sui']}"
    )
