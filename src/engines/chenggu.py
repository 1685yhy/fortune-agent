"""称骨算命引擎（问真数据基准，L2-1）。

数据基准：问真 v13.9 排盘 H5 chunk2（2.5bf19775ddc1e8150f39.js）内嵌 hn/_n 函数，
表（data/chenggu/*.json）与歌诀（data/chenggu/chenggu_verses.json）逐字提取，
索引口径 100% 对齐问真：

- 年骨重 fn[60]：按 60 甲子序（甲子=0 … 癸亥=59）。问真算法：以年干序 o 及
  「干组六支」[o, o+10, o+8, o+6, o+4, o+2] (mod 12) 求年支位置 d，
  索引 = o + 10*d —— 与 60 甲子序数严格一致。
- 月骨重 cn[12]：0 基（正月=0 … 腊月=11）。闰月与平月同重
  （问真 G.c 口径：去「闰」字后查表；lunar_python 闰月 getMonth() 为负，abs 归一）。
- 日骨重 ln[30]：0 基（初一=0 … 三十=29）。
- 时骨重 un[12]：时支序（子=0 … 亥=11）。
- 歌诀：男 52 条 / 女 51 条，骨重「X 两 Y 钱」→ 索引 (X-2)*10 + (Y-1)，
  整两（Y=0）→ -1。男命第 52 条（7.1 异文「此格世界罕有生…」）在问真代码中
  不可达（最大骨重 7.1 → 索引 50），原样保留。
- 骨重合计用「钱」整数相加（×10 后求和），与问真 dn() 十进制安全加法等价，
  避免浮点误差（如 0.1+0.2）。

对外接口：
    chenggu_bone(year_ganzhi, lunar_month, lunar_day, hour_ganzhi, gender="男")
        -> (total_liang, total_qian, jieci)
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple, Union

TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
# 问真 G.c 口径的月份中文名（含「冬月」「腊月」）→ 用于字符串形式的农历月
MONTH_CN = ["正月", "二月", "三月", "四月", "五月", "六月",
            "七月", "八月", "九月", "十月", "冬月", "腊月"]
RN = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "chenggu"


@lru_cache(maxsize=1)
def _load_tables() -> dict:
    """加载 data/chenggu/ 下的骨重表与歌诀表（模块级缓存，只读一次）。"""
    def load(name: str) -> List[float]:
        return json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))

    verses = json.loads((_DATA_DIR / "chenggu_verses.json").read_text(encoding="utf-8"))
    return {
        "fn": load("chenggu_fn.json"),      # 年骨重 60 项（60 甲子序）
        "cn": load("chenggu_cn.json"),      # 月骨重 12 项（正月 … 腊月）
        "ln": load("chenggu_ln.json"),      # 日骨重 30 项（初一 … 三十）
        "un": load("chenggu_un.json"),      # 时骨重 12 项（子 … 亥）
        "verses": verses,                   # {"male": [...52], "female": [...51]}
    }


def _year_index(year_ganzhi: str) -> int:
    """年干支 → 60 甲子序数（问真 hn 口径：干序 + 10×支在干组六支中的位置）。"""
    g = TIANGAN.index(year_ganzhi[0])
    z = DIZHI.index(year_ganzhi[1])
    group = [g] + [(g + 10 - 2 * c) % 12 for c in range(5)]
    d = group.index(z)
    return g + 10 * d


def _month_index(lunar_month: Union[int, str]) -> int:
    """农历月 → 月骨重表 0 基索引（正月=0 … 腊月=11）。

    支持 1-12 整数，或 "五月"/"闰五月"/"5月" 等写法；闰月与平月同重
    （问真 G.c：去「闰」字后查表）。
    """
    if isinstance(lunar_month, str):
        s = lunar_month.lstrip("闰").rstrip("月")
        if s.isdigit():
            m = int(s)
        else:
            m = MONTH_CN.index(s + "月") + 1
    else:
        m = int(lunar_month)
    if not 1 <= m <= 12:
        raise ValueError(f"农历月越界: {lunar_month}")
    return m - 1


def _hour_index(hour_ganzhi: str) -> int:
    """时干支/时支 → 时骨重表索引（子=0 … 亥=11）。接受 "甲子"/"子"/"子时"。"""
    s = hour_ganzhi.strip()
    if s.endswith("时"):
        s = s[:-1]
    if len(s) == 2:
        s = s[1]  # 干支取支
    return DIZHI.index(s)


def bone_weight_text(liang: int, qian: int) -> str:
    """骨重文本（问真 weightStr 口径）："X两Y钱"，整两为 "X两"。"""
    if not 1 <= liang <= 10:
        raise ValueError(f"两数越界: {liang}")
    if not 0 <= qian <= 9:
        raise ValueError(f"钱数越界: {qian}")
    text = f"{RN[liang - 1]}两"
    if qian:
        text += f"{RN[qian - 1]}钱"
    return text


def chenggu_parts(year_ganzhi: str, lunar_month: Union[int, str], lunar_day: int,
                  hour_ganzhi: str) -> List[int]:
    """四柱分项骨重（钱整数，问真口径）：[年, 月, 日, 时]。

    与 chenggu_bone 同表同索引取数，故 合计 == chenggu_bone 总骨重（钱）
    ——供 API 层展示分项且保证分项合计恒等于总重（晚子时归日口径下同源）。
    """
    if not 1 <= lunar_day <= 30:
        raise ValueError(f"农历日越界: {lunar_day}")
    tables = _load_tables()
    return [
        int(round(tables["fn"][_year_index(year_ganzhi)] * 10)),
        int(round(tables["cn"][_month_index(lunar_month)] * 10)),
        int(round(tables["ln"][lunar_day - 1] * 10)),
        int(round(tables["un"][_hour_index(hour_ganzhi)] * 10)),
    ]


def part_weight_text(qian: int) -> str:
    """分项骨重文本（总重格式要求两≥1，分项可不足 1 两，如 7 钱 → "七钱"）。"""
    liang, q = divmod(qian, 10)
    text = f"{RN[liang - 1]}两" if liang else ""
    if q:
        text += f"{RN[q - 1]}钱"
    return text


def chenggu_verse(liang: int, qian: int, gender: str = "男") -> str:
    """总骨重 → 歌诀全文（问真 _n 口径：索引 = (X-2)*10 + (Y-1)，整两 Y=0 → -1）。

    :param liang: 总骨重两数（2-7）
    :param qian: 总骨重钱数（0-9）
    :param gender: "男"/"女"（问真 sex=1 → 男命歌诀；其余 → 女命歌诀）
    :return: 歌诀文本，如 "短命非业谓大空，平生灾难事重重，凶祸频临陷逆境，终世困苦事不成"
    """
    key = "male" if gender in ("男", "male") else "female"
    idx = (liang - 2) * 10 + (qian - 1 if qian else -1)
    verses = _load_tables()["verses"][key]
    if not 0 <= idx < len(verses):
        raise ValueError(f"骨重无对应歌诀: {liang}两{qian}钱 (idx={idx})")
    return verses[idx]


def chenggu_bone(year_ganzhi: str, lunar_month: Union[int, str], lunar_day: int,
                 hour_ganzhi: str, gender: str = "男") -> Tuple[int, int, str]:
    """称骨算命：年 + 月 + 日 + 时四骨相加得总骨重，并查歌诀（问真数据基准）。

    :param year_ganzhi: 年干支，如 "甲子"（按 60 甲子序查年骨重表）
    :param lunar_month: 农历月：1-12，或 "五月"/"闰五月"/"5月"（闰月与平月同重）
    :param lunar_day: 农历日 1-30
    :param hour_ganzhi: 时干支或时支，如 "甲子"/"子"/"子时"（取时支查时骨重表）
    :param gender: "男"/"女"（决定歌诀；默认 "男"，与项目排盘 unknown 按男口径一致）
    :return: (total_liang, total_qian, jieci)
        total_liang/total_qian：总骨重两、钱（如 4 两 4 钱 → (4, 4)）
        jieci：歌诀全文
    """
    if not 1 <= lunar_day <= 30:
        raise ValueError(f"农历日越界: {lunar_day}")
    tables = _load_tables()

    # 骨重求和：全部转「钱」整数（×10），与问真 dn() 十进制安全加法等价
    year_qian = int(round(tables["fn"][_year_index(year_ganzhi)] * 10))
    month_qian = int(round(tables["cn"][_month_index(lunar_month)] * 10))
    day_qian = int(round(tables["ln"][lunar_day - 1] * 10))
    hour_qian = int(round(tables["un"][_hour_index(hour_ganzhi)] * 10))

    total_qian = year_qian + month_qian + day_qian + hour_qian
    liang, qian = divmod(total_qian, 10)
    if not 1 <= liang <= 7:
        raise ValueError(f"总骨重越界: {liang}两{qian}钱（理论范围 2.1-7.1 两）")
    return liang, qian, chenggu_verse(liang, qian, gender)


def chenggu_detail(year_ganzhi: str, lunar_month: Union[int, str], lunar_day: int,
                   hour_ganzhi: str, gender: str = "男") -> dict:
    """称骨完整结果（BaziResult.chenggu 结构）：骨重文本 + 数字 + 歌诀。"""
    liang, qian, jieci = chenggu_bone(year_ganzhi, lunar_month, lunar_day,
                                      hour_ganzhi, gender)
    return {
        "weight_text": bone_weight_text(liang, qian),
        "liang": liang,
        "qian": qian,
        "jieci": jieci,
    }


def _split_tips(jieci: str) -> List[str]:
    """按问真口径把歌诀拆成两段（前两句 + 其余），用于页面分段展示。

    问真：/([一-龥]+(，|？)){2}/ 在第二个标点后断开。
    """
    m = re.match(r"([一-龥]+(，|？)){2}", jieci)
    if not m:
        return [jieci, ""]
    return [jieci[: m.end() - 1], jieci[m.end():]]
