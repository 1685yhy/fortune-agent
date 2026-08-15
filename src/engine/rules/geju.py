# src/engine/rules/geju.py
"""子平真诠八格判定 v1：月令藏干 + 透干优先 + 本气兜底。"""
from __future__ import annotations

from src.engine.rules.shishen import shishen_of

# 十二地支藏干（本气/中气/余气）
CANG_GAN = {
    "寅": "甲丙戊", "卯": "乙", "辰": "戊乙癸", "巳": "丙庚戊",
    "午": "丁己", "未": "己丁乙", "申": "庚壬戊", "酉": "辛",
    "戌": "戊辛丁", "亥": "壬甲", "子": "癸", "丑": "己癸辛",
}
GEJU_NAMES = {
    "正官": "正官格", "七杀": "七杀格", "正印": "正印格", "偏印": "偏印格",
    "食神": "食神格", "伤官": "伤官格", "正财": "正财格", "偏财": "偏财格",
    "比肩": "建禄格", "劫财": "月刃格",
}


def determine_geju(pills: list[str]) -> str:
    if len(pills) != 4 or any(len(p) != 2 for p in pills):
        raise ValueError(f"pills 必须为四柱: {pills}")
    day_stem = pills[2][0]
    month_branch = pills[1][1]
    hidden = CANG_GAN.get(month_branch)
    if not hidden:
        raise ValueError(f"非法月支: {month_branch}")

    stems = [p[0] for p in pills]  # 年月日时天干
    # 透干优先：藏干透于年/月/时干，且非比劫
    for h in hidden:
        for i in (0, 1, 3):
            if stems[i] == h:
                ss = shishen_of(day_stem, stems[i])
                if ss not in ("比肩", "劫财"):
                    return GEJU_NAMES[ss]
    # 本气兜底
    return GEJU_NAMES[shishen_of(day_stem, hidden[0])]


def evaluate(pills: list[str]) -> dict:
    return {"geju": determine_geju(pills)}
