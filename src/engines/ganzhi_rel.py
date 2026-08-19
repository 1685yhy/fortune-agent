"""干支关系分析 — 伏吟/反吟/盖头/截脚/争合/妒合（问真 newgetGZRelaction 同款规则，P0-3）。

问真接口语义（newgetGZRelaction.php?gz=流年干支+大运干支+四柱）：两个干支之间
（流年 vs 大运 / 大运 vs 原局柱 / 流年 vs 原局柱）的作用关系。本模块为标准命理规则实现：

- 伏吟：两柱干支完全相同（如 乙丑 vs 乙丑）
- 反吟：天干同性相克 + 地支六冲（天克地冲之"无情克"者，如 乙丑 vs 己未：乙己克+丑未冲）
- 天克地冲：天干相克 + 地支六冲但为异性之克（不成反吟，如 甲午 vs 己子）
- 天克 / 地冲：单独的天干相克 / 单独的地支六冲（反吟的构成说明项）
- 盖头：柱内天干克地支（如 庚寅：庚金克寅木；甲子非盖头——子水生甲木）
- 截脚：柱内地支克天干（如 甲申：申金克甲木；乙亥非截脚——亥水生乙木）
- 单合：单干合日主（如 大运庚合日主乙 → "庚合日主乙"）
- 争合：两干及以上争合一干（被合者非日主，如 两庚争合乙）
- 妒合：两干及以上争合日主（被合者为日主，问真口径简化：多干合一日干）

说明：盖头/截脚为单柱柱内性质（a、b 各自判定），单合/争合/妒合需四柱（pillars）
参与；BaziResult.rel_with 为"点大运/流年看干支关系"的入口（大运/流年 vs 原局各柱）。
"""

from dataclasses import dataclass
from typing import List, Optional

TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

WX_TG = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土", "己": "土",
         "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
WX_DZ = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
         "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水"}

# 五行相克：木克土、土克水、水克火、火克金、金克木
KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

# 地支六冲：子午、丑未、寅申、卯酉、辰戌、巳亥
CHONG = {"子": "午", "午": "子", "丑": "未", "未": "丑", "寅": "申", "申": "寅",
         "卯": "酉", "酉": "卯", "辰": "戌", "戌": "辰", "巳": "亥", "亥": "巳"}

# 天干五合：甲己合土、乙庚合金、丙辛合水、丁壬合木、戊癸合火
HE = {"甲": "己", "己": "甲", "乙": "庚", "庚": "乙", "丙": "辛", "辛": "丙",
      "丁": "壬", "壬": "丁", "戊": "癸", "癸": "戊"}


@dataclass
class RelationItem:
    """单条干支关系。

    type:    关系类型（伏吟/反吟/盖头/截脚/单合/争合/妒合/天克地冲/天克/地冲）
    desc:    中文说明（含具体干支作用，如 "天干庚克寅（盖头）"）
    between: 关系归属（集成层填写：如 "年-月" / "日柱" / "自身"；analyze_relations 留空）
    """
    type: str
    desc: str
    between: str = ""


def _stem_ke(ag: str, bg: str) -> bool:
    """天干 ag 五行克 bg（如 乙克己）。"""
    return KE.get(WX_TG.get(ag, "")) == WX_TG.get(bg, "")


def _stem_ke_zhi(g: str, z: str) -> bool:
    """柱内天干克地支（盖头判定，如 庚金克寅木）。"""
    return KE.get(WX_TG.get(g, "")) == WX_DZ.get(z, "")


def _zhi_ke_stem(z: str, g: str) -> bool:
    """柱内地支克天干（截脚判定，如 申金克甲木）。"""
    return KE.get(WX_DZ.get(z, "")) == WX_TG.get(g, "")


def _he_contend(a: str, b: str, pillars: Optional[List[str]]) -> List[RelationItem]:
    """单合/争合/妒合：a/b 天干与四柱天干并集，多干合一干（按物理存在去重）。

    茎池只按物理存在计入各天干：a/b 若本就是原局柱（rel_with/_calc_ganzhi_rel
    的常态），其天干已在 pillars 中，不重复计入——否则合伴计数虚增、伪造争合、
    方向反转（如"乙、乙两干争合庚"）。被合干为日主（pillars[2][0]）且多干
    争合 → 妒合（问真口径简化：多干合一日干）；被合干非日主且多干 → 争合；
    单干合日主 → 单合（如"庚合日主乙"）。desc 按合伴实际数量措辞
    （1 干"X合Y"、2 干"两干"、3 干"三干"）。
    """
    if not pillars or len(pillars) < 4:
        return []
    day_gan = pillars[2][0]
    stems = [p[0] for p in pillars]
    if a not in pillars:
        stems.append(a[0])
    if b not in pillars and b != a:
        stems.append(b[0])
    out = []
    for target in dict.fromkeys(stems):  # 同 target 只处理一次，防重复输出
        partners = [s for s in stems if s != target and HE.get(s) == target]
        n = len(partners)
        if n == 1:
            # 单合仅向日主报告（方向如实：池中合伴 → 日主）
            if target == day_gan:
                out.append(RelationItem("合", "%s合日主%s" % (partners[0], target)))
            continue
        if n < 2:
            continue
        head = "、".join(partners[:3]) + ("…" if n > 3 else "")
        count_word = {2: "两干", 3: "三干"}.get(n, "%d干" % n)
        desc = "%s%s争合%s" % (head, count_word, target)
        if target == day_gan:
            out.append(RelationItem("妒合", desc + "（日主被争合）"))
        else:
            out.append(RelationItem("争合", desc))
    return out


def analyze_relations(a: str, b: str, pillars: Optional[List[str]] = None) -> List[RelationItem]:
    """分析两个干支 a/b 之间的关系（问真 newgetGZRelaction 同款入口）。

    :param a: 干支一（流年/大运/原局柱），如 "乙丑"
    :param b: 干支二，如 "己未"
    :param pillars: 四柱列表（单合/争合/妒合判定需要；缺省则跳过）
    :return: 关系列表，type ∈ 伏吟/反吟/天克地冲/天克/地冲/盖头/截脚/单合/争合/妒合
    """
    if not a or not b or len(a) < 2 or len(b) < 2:
        return []
    out: List[RelationItem] = []
    ag, az, bg, bz = a[0], a[1], b[0], b[1]

    # 伏吟：两柱干支完全相同
    if a == b:
        out.append(RelationItem("伏吟", "%s与%s完全相同（伏吟）" % (a, b)))

    # 天干相克（双向检出，方向如实描述：如 己卯 vs 乙丑 → 乙克己）+ 地支六冲
    # → 反吟（同性之克，天克地冲之无情者）/ 天克地冲（异性之克）；
    # 单独天克 / 单独地冲 作为说明项输出
    ke_desc = None
    if _stem_ke(ag, bg):
        ke_desc = "天干%s克%s" % (ag, bg)
    elif _stem_ke(bg, ag):
        ke_desc = "天干%s克%s" % (bg, ag)
    branch_chong = CHONG.get(az) == bz
    if ke_desc and branch_chong:
        same_yin = (TIANGAN.index(ag) - TIANGAN.index(bg)) % 2 == 0
        if same_yin:
            out.append(RelationItem(
                "反吟", "%s、地支%s冲%s（天克地冲，反吟）" % (ke_desc, az, bz)))
        else:
            out.append(RelationItem(
                "天克地冲", "%s（异性之克）、地支%s冲%s（不成反吟）" % (ke_desc, az, bz)))
    elif ke_desc:
        out.append(RelationItem("天克", ke_desc))
    elif branch_chong:
        out.append(RelationItem("地冲", "地支%s冲%s" % (az, bz)))

    # 盖头/截脚：柱内天干与地支相克（a、b 各自判定；a==b 时同柱去重）
    seen_self = set()
    for g, z in ((ag, az), (bg, bz)):
        if _stem_ke_zhi(g, z):
            desc = "天干%s克地支%s（盖头）" % (g, z)
            if desc not in seen_self:
                seen_self.add(desc)
                out.append(RelationItem("盖头", desc))
        elif _zhi_ke_stem(z, g):
            desc = "地支%s克天干%s（截脚）" % (z, g)
            if desc not in seen_self:
                seen_self.add(desc)
                out.append(RelationItem("截脚", desc))

    # 单合/争合/妒合：a/b 与四柱天干并集，多干合一干（日主被多干争合 → 妒合）
    out.extend(_he_contend(a, b, pillars))

    return out
