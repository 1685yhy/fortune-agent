"""十神判定（异性为正、同性为偏）与经典组合规则（v1 五组）。"""
from __future__ import annotations

STEMS = "甲乙丙丁戊己庚辛壬癸"
WUXING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
          "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}  # 生
KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}      # 克
YANG = {"甲", "丙", "戊", "庚", "壬"}


def shishen_of(day_stem: str, other_stem: str) -> str:
    """日干对其他天干的十神：异性为正、同性为偏；比劫相反。"""
    same_yang = (day_stem in YANG) == (other_stem in YANG)
    d, o = WUXING[day_stem], WUXING[other_stem]
    if d == o:
        return "比肩" if same_yang else "劫财"
    if SHENG[o] == d:          # 生我
        return "正印" if not same_yang else "偏印"
    if SHENG[d] == o:          # 我生
        return "食神" if same_yang else "伤官"
    if KE[o] == d:             # 克我
        return "正官" if not same_yang else "七杀"
    if KE[d] == o:             # 我克
        return "正财" if not same_yang else "偏财"
    raise ValueError(f"非法天干: {day_stem} / {other_stem}")


def detect_combos(pills: list[str]) -> list[str]:
    """经典组合规则 v1：伤官见官/官杀混杂/比劫夺财/枭神夺食/财多身弱。"""
    day_stem = pills[2][0]
    stems = [p[0] for p in pills]           # 年月日时天干
    shishens = [shishen_of(day_stem, s) for s in stems]
    hits: list[str] = []

    if "伤官" in shishens and ("正官" in shishens or "七杀" in shishens):
        hits.append("伤官见官")
    if "正官" in shishens and "七杀" in shishens:
        hits.append("官杀混杂")
    if shishens.count("比肩") + shishens.count("劫财") >= 2 and \
       shishens.count("正财") + shishens.count("偏财") >= 1:
        hits.append("比劫夺财")
    if "偏印" in shishens and "食神" in shishens:
        hits.append("枭神夺食")
    # 财多身弱：天干财(正偏) 多于 印比(正偏印+比劫)
    cai = shishens.count("正财") + shishens.count("偏财")
    bi_yin = shishens.count("正印") + shishens.count("偏印") + \
        shishens.count("比肩") + shishens.count("劫财")
    if cai > bi_yin and cai >= 2:
        hits.append("财多身弱")
    return hits


def evaluate(pills: list[str]) -> dict:
    return {"shishen": detect_combos(pills)}
