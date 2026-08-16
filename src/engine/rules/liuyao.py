# src/engine/rules/liuyao.py
"""六爻规则库 v1：六亲/世应/纳甲/动变（确定性查表与计算，不做解释性断语）。

口径说明（audit 依据）：
- 六亲：以日干五行为"我"——生我者父母 / 克我者官鬼 / 我克者妻财 /
  同我者兄弟 / 我生者子孙（《火珠林》六亲定义；本规则库按计划接口取日干口径）。
  analyze()/evaluate() 未给日干时，世爻六亲采排盘结果的卦宫口径并在要点中明示。
- 世应：八宫六十四卦表（《卜筮正宗》安世应：本宫世在六爻，一~五世卦世随变爻数，
  游魂世在四爻、归魂世在三爻；应爻隔三位 = 世爻位 +3 取模 6）。
- 纳甲：火珠林纳甲口诀（乾金甲子外壬午 / 坎水戊寅外戊申 / 艮土丙辰外丙戌 /
  震木庚子外庚午 / 巽木辛丑外辛未 / 离火己卯外己酉 / 坤土乙未外癸丑 /
  兑金丁巳外丁亥）；阳卦顺行(+2)、阴卦逆行(-2)。
- 动变：老阴(6)/老阳(9) 为动爻，动则阴阳互变（1↔0）得变卦。
"""
from __future__ import annotations

# ============================================================
# 基础表：干支五行
# ============================================================

STEM_WUXING = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}

DIZHI_WUXING = {
    "子": "水", "丑": "土", "寅": "木", "卯": "木",
    "辰": "土", "巳": "火", "午": "火", "未": "土",
    "申": "金", "酉": "金", "戌": "土", "亥": "水",
}

WUXING_SET = {"木", "火", "土", "金", "水"}

# 五行相生（我生）：木→火→土→金→水→木
SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
# 五行相克（我克）：木→土→水→火→金→木
KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

# 爻位称谓（0 初爻 ~ 5 上爻）
YAO_TERMS = {0: "初爻", 1: "二爻", 2: "三爻", 3: "四爻", 4: "五爻", 5: "上爻"}

# 三爻卦编码（从下到上：阳=1 阴=0）与反查
TRIGRAM_VALUE = {"乾": 0b111, "兑": 0b110, "离": 0b101, "震": 0b100,
                 "巽": 0b011, "坎": 0b010, "艮": 0b001, "坤": 0b000}
VALUE_TRIGRAM = {v: k for k, v in TRIGRAM_VALUE.items()}


def _to_wuxing(char: str) -> str:
    """干支/五行字 → 五行；非法输入抛 ValueError。"""
    if char in WUXING_SET:
        return char
    if char in STEM_WUXING:
        return STEM_WUXING[char]
    if char in DIZHI_WUXING:
        return DIZHI_WUXING[char]
    raise ValueError(f"无法解析为五行: {char!r}")


# ============================================================
# 六亲（以日干五行为我）
# ============================================================

def liuqin_of(day_gan: str, line_gan_or_wuxing: str) -> str:
    """六亲计算：以日干五行为我。

    - 生我者父母 / 克我者官鬼 / 我克者妻财 / 同我者兄弟 / 我生者子孙
    - line_gan_or_wuxing 接受：五行字（如水）、天干（如庚）、地支（如申）。
    """
    my_wx = _to_wuxing(day_gan)
    line_wx = _to_wuxing(line_gan_or_wuxing)
    if line_wx == my_wx:
        return "兄弟"
    if SHENG[line_wx] == my_wx:      # 爻五行生我
        return "父母"
    if KE[line_wx] == my_wx:         # 爻五行克我
        return "官鬼"
    if SHENG[my_wx] == line_wx:      # 我生爻五行
        return "子孙"
    if KE[my_wx] == line_wx:         # 我克爻五行
        return "妻财"
    raise ValueError(f"六亲无法判定: 我={my_wx} 爻={line_wx}")


# ============================================================
# 世应（八宫六十四卦表）
# ============================================================

# 六爻卦值编码：6 bit，从下到上（初爻=LSB），上卦在高 3 位。
# 世位：0 初爻 ~ 5 上爻（本宫世六、游魂世四、归魂世三；京房八宫世应）。
HEXAGRAMS: dict[int, tuple[str, int]] = {
    # 乾宫（属金）
    0b111111: ("乾为天", 5), 0b111011: ("天风姤", 0), 0b111001: ("天山遁", 1),
    0b111000: ("天地否", 2), 0b011000: ("风地观", 3), 0b001000: ("山地剥", 4),
    0b101000: ("火地晋", 3),   # 游魂
    0b101111: ("火天大有", 2),  # 归魂
    # 坎宫（属水）
    0b010010: ("坎为水", 5), 0b010110: ("水泽节", 0), 0b010100: ("水雷屯", 1),
    0b010101: ("水火既济", 2), 0b110101: ("泽火革", 3), 0b100101: ("雷火丰", 4),
    0b000101: ("地火明夷", 3),  # 游魂
    0b000010: ("地水师", 2),    # 归魂
    # 艮宫（属土）
    0b001001: ("艮为山", 5), 0b001101: ("山火贲", 0), 0b001111: ("山天大畜", 1),
    0b001110: ("山泽损", 2), 0b101110: ("火泽睽", 3), 0b111110: ("天泽履", 4),
    0b011110: ("风泽中孚", 3),  # 游魂
    0b011001: ("风山渐", 2),    # 归魂
    # 震宫（属木）
    0b100100: ("震为雷", 5), 0b100000: ("雷地豫", 0), 0b100010: ("雷水解", 1),
    0b100011: ("雷风恒", 2), 0b000011: ("地风升", 3), 0b010011: ("水风井", 4),
    0b110011: ("泽风大过", 3),  # 游魂
    0b110100: ("泽雷随", 2),    # 归魂
    # 巽宫（属木）
    0b011011: ("巽为风", 5), 0b011111: ("风天小畜", 0), 0b011101: ("风火家人", 1),
    0b011100: ("风雷益", 2), 0b111100: ("天雷无妄", 3), 0b101100: ("火雷噬嗑", 4),
    0b001100: ("山雷颐", 3),    # 游魂
    0b001011: ("山风蛊", 2),    # 归魂
    # 离宫（属火）
    0b101101: ("离为火", 5), 0b101001: ("火山旅", 0), 0b101011: ("火风鼎", 1),
    0b101010: ("火水未济", 2), 0b001010: ("山水蒙", 3), 0b011010: ("风水涣", 4),
    0b111010: ("天水讼", 3),    # 游魂
    0b111101: ("天火同人", 2),  # 归魂
    # 坤宫（属土）
    0b000000: ("坤为地", 5), 0b000100: ("地雷复", 0), 0b000110: ("地泽临", 1),
    0b000111: ("地天泰", 2), 0b100111: ("雷天大壮", 3), 0b110111: ("泽天夬", 4),
    0b010111: ("水天需", 3),    # 游魂
    0b010000: ("水地比", 2),    # 归魂
    # 兑宫（属金）
    0b110110: ("兑为泽", 5), 0b110010: ("泽水困", 0), 0b110000: ("泽地萃", 1),
    0b110001: ("泽山咸", 2), 0b010001: ("水山蹇", 3), 0b000001: ("地山谦", 4),
    0b100001: ("雷山小过", 3),  # 游魂
    0b100110: ("雷泽归妹", 2),  # 归魂
}

# 卦名 → (卦值, 世位)
HEXAGRAM_BY_NAME: dict[str, tuple[int, int]] = {
    name: (value, shi) for value, (name, shi) in HEXAGRAMS.items()
}


def shiying_positions(hexagram_name: str) -> tuple[int, int]:
    """世应位：返回 (世爻位 0-5, 应爻位 0-5)。

    八宫卦表直接查世位；应爻隔三位（世位 +3 取模 6）。
    """
    if hexagram_name not in HEXAGRAM_BY_NAME:
        raise ValueError(f"未知卦名: {hexagram_name!r}")
    _, shi = HEXAGRAM_BY_NAME[hexagram_name]
    return shi, (shi + 3) % 6


# ============================================================
# 纳甲（地支 + 天干）
# ============================================================

DIZHI_INDEX = {dz: i for i, dz in enumerate(
    ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"])}
DIZHI_INDEX_MAP = {i: dz for dz, i in DIZHI_INDEX.items()}

# 纳支起点：阳卦顺行(+2)、阴卦逆行(-2)；(内卦初爻地支索引, 外卦四爻地支索引, 阳卦?)
NAJIA_DIZHI_START = {
    "乾": (0, 6, True),   # 子(0)…… 外午(6)
    "坎": (2, 8, True),   # 寅(2)…… 外申(8)
    "艮": (4, 10, True),  # 辰(4)…… 外戌(10)
    "震": (0, 6, True),   # 子(0)…… 外午(6)
    "巽": (1, 7, False),  # 丑(1)逆行…… 外未(7)逆行
    "离": (3, 9, False),  # 卯(3)逆行…… 外酉(9)逆行
    "坤": (7, 1, False),  # 未(7)逆行…… 外丑(1)逆行
    "兑": (5, 11, False),  # 巳(5)逆行…… 外亥(11)逆行
}

# 纳干口诀：内卦干 / 外卦干（乾金甲子外壬午……）
NAJIA_GAN_START = {
    "乾": ("甲", "壬"), "坎": ("戊", "戊"), "艮": ("丙", "丙"), "震": ("庚", "庚"),
    "巽": ("辛", "辛"), "离": ("己", "己"), "坤": ("乙", "癸"), "兑": ("丁", "丁"),
}


def _najia_indices(upper: str, lower: str) -> list[int]:
    """按纳甲规则生成 6 爻地支索引（初爻→上爻）。"""
    u_start, u_upper_start, u_yang = NAJIA_DIZHI_START[upper]
    l_start, _, l_yang = NAJIA_DIZHI_START[lower]
    dizhi: list[int] = []
    for i in range(6):
        if i < 3:
            step = i * 2 if l_yang else -i * 2
            dizhi.append((l_start + step) % 12)
        else:
            step = (i - 3) * 2 if u_yang else -(i - 3) * 2
            dizhi.append((u_upper_start + step) % 12)
    return dizhi


def najia_dizhi(upper: str, lower: str) -> list[str]:
    """纳甲地支：返回 6 爻地支（初爻→上爻），口径与 LiuyaoEngine.get_line_dizhi 一致。"""
    return [DIZHI_INDEX_MAP[i] for i in _najia_indices(upper, lower)]


def najia_ganzhi(upper: str, lower: str) -> list[str]:
    """纳甲干支：返回 6 爻干支对（初爻→上爻），如 乾为天 = [甲子,甲寅,甲辰,壬午,壬申,壬戌]。"""
    u_gan, u_outer_gan = NAJIA_GAN_START[upper]
    l_gan, _ = NAJIA_GAN_START[lower]
    out = []
    for i, dz_idx in enumerate(_najia_indices(upper, lower)):
        gan = l_gan if i < 3 else u_outer_gan
        out.append(f"{gan}{DIZHI_INDEX_MAP[dz_idx]}")
    return out


# ============================================================
# 动变（老阴/老阳动 → 阴阳互变）
# ============================================================

def bian_hexagram(hexagram_value: int, changing_indices: list[int]) -> str:
    """变卦：动爻（老阴/老阳）阴阳互变（1↔0）后查表得变卦名；静卦返回本卦名。"""
    value = hexagram_value
    for i in changing_indices:
        value ^= (1 << i)
    entry = HEXAGRAMS.get(value)
    if entry is None:
        raise ValueError(f"变卦值非法（不在六十四卦表）: {value}")
    return entry[0]


# ============================================================
# analyze / evaluate
# ============================================================

def _field(result, key: str, default=None):
    """兼容 dataclass（LiuyaoResult）与 dict 两种输入。"""
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _facts(result, day_gan: str | None = None) -> dict:
    """从排盘结果提取确定性事实（本卦/世应/六亲用事/动爻/变卦/纳甲）。"""
    name = _field(result, "original_hexagram")
    if not name:
        raise ValueError("result 缺 original_hexagram（本卦卦名）")
    lines = _field(result, "lines") or []
    changing = list(_field(result, "changing_lines") or [])
    value = _field(result, "original_hexagram_value")
    if value is None:
        value = HEXAGRAM_BY_NAME[name][0]

    shi, ying = shiying_positions(name)
    shi_dz = lines[shi].get("dizhi", "") if len(lines) > shi else ""
    if day_gan and shi_dz:
        lq = liuqin_of(day_gan, shi_dz)
        lq_note = f"（以日干{day_gan}{STEM_WUXING[day_gan]}为我）"
    elif day_gan and not shi_dz:
        lq, lq_note = "", ""
    else:
        lq = lines[shi].get("liuqin", "") if len(lines) > shi else ""
        lq_note = "（卦宫口径，采自排盘）"

    upper = VALUE_TRIGRAM[(value >> 3) & 0b111]
    lower = VALUE_TRIGRAM[value & 0b111]

    return {
        "name": name,
        "value": value,
        "shi": shi,
        "ying": ying,
        "shi_dz": shi_dz,
        "liuqin": lq,
        "liuqin_note": lq_note,
        "changing": changing,
        "upper": upper,
        "lower": lower,
        "bian": bian_hexagram(value, changing),
    }


def analyze(result, day_gan: str | None = None) -> list[str]:
    """确定性要点：本卦 / 世应 / 六亲用事 / 动爻数 / 变卦（不做解释性断语）。"""
    f = _facts(result, day_gan=day_gan)
    points = [f"本卦：{f['name']}",
              f"世应：世在{YAO_TERMS[f['shi']]}应在{YAO_TERMS[f['ying']]}"]
    if f["liuqin"]:
        points.append(f"世爻六亲：{f['shi_dz']}为{f['liuqin']}{f['liuqin_note']}")
    if f["changing"]:
        terms = "、".join(YAO_TERMS[i] for i in f["changing"])
        points.append(f"动爻：{len(f['changing'])}爻动（{terms}）")
    else:
        points.append("动爻：静卦无动爻")
    points.append(f"变卦：{f['bian']}")
    return points


def evaluate(result, day_gan: str | None = None) -> dict:
    """要点字典（供考卷断言）：本卦/世应/纳甲/世爻六亲/动爻数/动爻/变卦/要点。"""
    f = _facts(result, day_gan=day_gan)
    return {
        "本卦": f["name"],
        "世应": f"世在{YAO_TERMS[f['shi']]}应在{YAO_TERMS[f['ying']]}",
        "纳甲": najia_dizhi(f["upper"], f["lower"]),
        "世爻六亲": f["liuqin"],
        "动爻数": len(f["changing"]),
        "动爻": [YAO_TERMS[i] for i in f["changing"]],
        "变卦": f["bian"],
        "要点": analyze(result, day_gan=day_gan),
    }
