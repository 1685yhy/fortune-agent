# src/engine/rules/shensha.py
"""神煞扩展 v1：桃花/文昌/羊刃/禄神/华盖/孤辰寡宿。"""
from __future__ import annotations

BRANCHES = "子丑寅卯辰巳午未申酉戌亥"

SANHE = {  # 三合局：组 → 咸池位 / 华盖位
    "申子辰": ("酉", "辰"), "寅午戌": ("卯", "戌"),
    "巳酉丑": ("午", "丑"), "亥卯未": ("子", "未"),
}
SANHUI = {  # 三会局：组 → (孤辰, 寡宿)
    "亥子丑": ("寅", "戌"), "寅卯辰": ("巳", "丑"),
    "巳午未": ("申", "辰"), "申酉戌": ("亥", "未"),
}
WENCHANG = {"甲": "巳", "乙": "午", "丙": "申", "丁": "酉", "戊": "申",
            "己": "酉", "庚": "亥", "辛": "子", "壬": "寅", "癸": "卯"}
YANGREN = {"甲": "卯", "乙": "辰", "丙": "午", "丁": "未", "戊": "午",
           "己": "未", "庚": "酉", "辛": "戌", "壬": "子", "癸": "丑"}
LUSHEN = {"甲": "寅", "乙": "卯", "丙": "巳", "丁": "午", "戊": "巳",
          "己": "午", "庚": "申", "辛": "酉", "壬": "亥", "癸": "子"}


def _group_of(branch: str, table: dict) -> str:
    for group in table:
        if branch in group:
            return group
    return ""


def shensha_of(pills: list[str]) -> list[str]:
    if len(pills) != 4:
        raise ValueError(f"pills 必须为四柱: {pills}")
    branches = {p[1] for p in pills}
    year_branch = pills[0][1]
    day_stem = pills[2][0]
    day_branch = pills[2][1]
    hits: list[str] = []

    # 桃花/华盖：口诀"日支/年支"两局分别判定后合并命中（12 支被 4 组三合局全覆盖，
    # 若用 or 短路则年支锚定永不生效）
    g_day = _group_of(day_branch, SANHE)
    g_year = _group_of(year_branch, SANHE)
    peach_pos = {SANHE[g][0] for g in (g_day, g_year) if g}
    canopy_pos = {SANHE[g][1] for g in (g_day, g_year) if g}
    if peach_pos & branches:
        hits.append("桃花")
    if canopy_pos & branches:
        hits.append("华盖")
    g2 = _group_of(year_branch, SANHUI) or _group_of(day_branch, SANHUI)
    if g2:
        gu, gua = SANHUI[g2]
        if gu in branches:
            hits.append("孤辰")
        if gua in branches:
            hits.append("寡宿")
    for name, table in (("文昌", WENCHANG), ("羊刃", YANGREN), ("禄神", LUSHEN)):
        if table.get(day_stem, "") in branches:
            hits.append(name)
    return hits


def evaluate(pills: list[str]) -> dict:
    return {"shensha": shensha_of(pills)}
