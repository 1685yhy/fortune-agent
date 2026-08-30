# tests/test_liuyao_p0_order.py
"""K1（P0）：六爻装卦位序 bug 回归测试。

背景（权威对比报告 /tmp/compare_liuyao.md Bug #1）：`_cast_coins` 原以 `bit=爻位`
（初爻→bit0）写 hex_value，而 HEXAGRAM_TABLE 约定 下卦 bit2=初爻 bit1=二爻 bit0=三爻、
上卦 bit5=四爻 bit4=五爻 bit3=上爻——同一爻象两种编码仅当「初==三 且 四==上」时同值，
导致 75% 爻象（48/64 本卦、3024/4032 变卦）整盘装卦错（卦名/宫/世应/六亲/纳甲五字段连环错）。

本文件从第一性原理构造固定爻象（6/7/8/9 三钱和值，初爻→上爻），经 monkeypatch 的
随机源注入走真实 `cast()` 链路（随机层不变、仅映射修正），断言：
- 64 种静卦爻象：本卦名/宫/宫五行/世应（世应歌）/六亲/纳甲与卦表定义一致
- 64×63=4032 种动爻组合：变卦名 = 「动爻阴阳互变后按卦表位序重排」独立重算一致
  （且与规则库 bian_hexagram 同构——单一映射事实源）
- L1-L4 权威锚点（卦馆网实测，报告 §案例表）逐字段断言
- seed=353 代表性错例五字段 + 报告取证所用 seed 逐一复现锚点
"""
import pytest

from src.engine.rules.liuyao import bian_hexagram
from src.engines.liuyao import (
    LiuyaoEngine,
    HEXAGRAM_TABLE,
    TRIGRAM_NAMES,
    DIZHI_WUXING,
    PALACE_WUXING,
    calc_liuqin,
)

# 三钱和值 → 三次 choice 结果：6=老阴(动) 7=少阳 8=少阴 9=老阳(动)
COIN_TRIPLES = {6: (2, 2, 2), 7: (2, 2, 3), 8: (2, 3, 3), 9: (3, 3, 3)}


class _ScriptedRandom:
    """按给定 6 爻三钱和值依次吐出硬币结果：随机层仍走真实 choice 链路，爻象完全可控。"""

    def __init__(self, totals):
        self._pool = [c for t in totals for c in COIN_TRIPLES[t]]

    def choice(self, seq):
        assert list(seq) == [2, 3]
        return self._pool.pop(0)


@pytest.fixture
def cast_lines(monkeypatch):
    """注入固定爻象（6 个三钱和值，初爻→上爻）后调用真实 cast。"""

    def _cast(totals, **kw):
        monkeypatch.setattr("src.engines.liuyao.random.Random",
                            lambda seed=None: _ScriptedRandom(list(totals)))
        return LiuyaoEngine().cast(method="random", **kw)

    return _cast


# ---------- 测试侧独立编码器（不依赖引擎内部实现，按卦表位序约定第一性构造） ----------

def _line_bit(i):
    """爻位(0=初爻..5=上爻) → 卦表位序（下卦 bit2=初爻…上卦 bit5=四爻…）。"""
    return 2 - i if i < 3 else 8 - i


def _line_yinyang(value, i):
    """卦表值 value 中爻位 i 的阴阳（True=阳）。"""
    return bool((value >> _line_bit(i)) & 1)


def _lines_to_value(lines):
    """6 爻阴阳（初→上）→ 卦表 6-bit 值：下卦三爻（初二三）bit2=初…、上卦三爻（四五六）bit5=四…，
    等价于 (上卦<<3)|下卦。独立于引擎实现的第一性重算。"""
    lower = ((1 if lines[0] else 0) << 2
             | (1 if lines[1] else 0) << 1
             | (1 if lines[2] else 0))
    upper = ((1 if lines[3] else 0) << 2
             | (1 if lines[4] else 0) << 1
             | (1 if lines[5] else 0))
    return (upper << 3) | lower


def _totals_for(value, changing_mask=0):
    """卦表值 + 动爻掩码（mask 位 i = 爻位 i 动）→ 6/7/8/9 三钱和值（初→上）。"""
    totals = []
    for i in range(6):
        yang = _line_yinyang(value, i)
        if changing_mask & (1 << i):
            totals.append(9 if yang else 6)   # 老阳/老阴（动）
        else:
            totals.append(7 if yang else 8)   # 少阳/少阴（静）
    return totals


# ---------- 64 卦全量自检 ----------

def test_all_64_static_hexagrams_map_correctly(cast_lines):
    """64 种静卦爻象全量：本卦名/宫/宫五行/世应/六亲/纳甲逐项与卦表定义一致（K1 修复后 64/64）。"""
    engine = LiuyaoEngine()
    for value in range(64):
        totals = _totals_for(value)
        r = cast_lines(totals, question="自检")
        name, palace_idx, shi = HEXAGRAM_TABLE[value]
        ying = (shi + 3) % 6
        assert r.original_hexagram == name, f"{value:06b} 本卦名错: {r.original_hexagram} != {name}"
        assert r.original_hexagram_value == value, f"{name} 卦值错"
        assert r.palace == TRIGRAM_NAMES[palace_idx], f"{name} 宫错"
        assert r.palace_wuxing == PALACE_WUXING[palace_idx], f"{name} 宫五行错"
        assert (r.shi_yao, r.ying_yao) == (shi, ying), f"{name} 世应错（世应歌）"
        assert r.changing_lines == [], f"{name} 静卦出现动爻"
        assert r.changed_hexagram == name, f"{name} 静卦变卦应同本卦"
        for i, line in enumerate(r.lines):
            exp_yao = "世" if i == shi else ("应" if i == ying else "")
            assert line["yao_type"] == exp_yao, f"{name} 第{i}爻世应标注错"
        # 纳甲：按上/下卦名独立计算
        upper_name = TRIGRAM_NAMES[(value >> 3) & 0b111]
        lower_name = TRIGRAM_NAMES[value & 0b111]
        expected_dizhi = engine.get_line_dizhi(upper_name, lower_name)
        assert [l["dizhi"] for l in r.lines] == expected_dizhi, f"{name} 纳甲地支错"
        # 六亲：宫五行 × 爻地支五行（同宫六亲断法）
        for i, line in enumerate(r.lines):
            exp_lq = calc_liuqin(PALACE_WUXING[palace_idx], DIZHI_WUXING[line["dizhi"]])
            assert line["liuqin"] == exp_lq, f"{name} 第{i}爻六亲错: {line['liuqin']} != {exp_lq}"


def test_all_4032_changed_hexagrams_flip_correctly(cast_lines):
    """64 本卦 × 63 非空动爻子集 = 4032 变卦全量：变卦名 = 动爻阴阳互变后按卦表位序
    独立重算的结果（K1 修复后 4032/4032），且与规则库 bian_hexagram 同构。"""
    for base in range(64):
        for mask in range(1, 64):
            totals = _totals_for(base, mask)
            r = cast_lines(totals)
            assert r.original_hexagram_value == base, "本卦值不应受动爻影响"
            lines = [_line_yinyang(base, i) for i in range(6)]
            for i in range(6):
                if mask & (1 << i):
                    lines[i] = not lines[i]
            expected = _lines_to_value(lines)
            expected_name = HEXAGRAM_TABLE[expected][0]
            base_name = HEXAGRAM_TABLE[base][0]
            assert r.changing_lines == [i for i in range(6) if mask & (1 << i)], \
                f"{base_name} 动{mask:06b} 动爻位错"
            assert r.changed_hexagram == expected_name, \
                f"{base_name}({base:06b}) 动{mask:06b} 变卦错: {r.changed_hexagram} != {expected_name}"
            # 数据一致性：规则库 bian_hexagram（断语层消费）与引擎同构，单一映射事实源
            assert bian_hexagram(base, r.changing_lines) == expected_name, \
                f"bian_hexagram({base}, {r.changing_lines}) 与引擎不一致"


# ---------- L1-L4 权威锚点（卦馆网实测，/tmp/compare_liuyao.md §案例表） ----------

# (标签, 爻象初→上, 本卦, 变卦, 动爻, 世, 应, 宫, 宫五行, 六亲(初→上), 纳甲(初→上))
ANCHORS = [
    ("L1", "888888", "坤为地", "坤为地", [], 5, 2, "坤", "土",
     ["兄弟", "父母", "官鬼", "兄弟", "妻财", "子孙"], ["未", "巳", "卯", "丑", "亥", "酉"]),
    ("L2", "688888", "坤为地", "地雷复", [0], 5, 2, "坤", "土",
     ["兄弟", "父母", "官鬼", "兄弟", "妻财", "子孙"], ["未", "巳", "卯", "丑", "亥", "酉"]),
    ("L3", "886898", "水地比", "地山谦", [2, 4], 2, 5, "坤", "土",
     ["兄弟", "父母", "官鬼", "子孙", "兄弟", "妻财"], ["未", "巳", "卯", "申", "戌", "子"]),
    ("L4", "696969", "火水未济", "水火既济", [0, 1, 2, 3, 4, 5], 2, 5, "离", "火",
     ["父母", "子孙", "兄弟", "妻财", "子孙", "兄弟"], ["寅", "辰", "午", "酉", "未", "巳"]),
]


@pytest.mark.parametrize("label,totals_str,exp_orig,exp_chg,exp_chg_lines,exp_shi,exp_ying,"
                         "exp_palace,exp_wx,exp_liuqin,exp_dizhi", ANCHORS)
def test_authority_anchors_all_fields(cast_lines, label, totals_str, exp_orig, exp_chg,
                                      exp_chg_lines, exp_shi, exp_ying, exp_palace, exp_wx,
                                      exp_liuqin, exp_dizhi):
    """L1-L4 权威锚点逐字段断言（本卦/变卦/世应/六亲/纳甲/宫五行）。"""
    totals = [int(c) for c in totals_str]
    r = cast_lines(totals, question="问财运")
    assert r.original_hexagram == exp_orig, f"{label} 本卦"
    assert r.changed_hexagram == exp_chg, f"{label} 变卦"
    assert r.changing_lines == exp_chg_lines, f"{label} 动爻"
    assert (r.shi_yao, r.ying_yao) == (exp_shi, exp_ying), f"{label} 世应"
    assert r.palace == exp_palace and r.palace_wuxing == exp_wx, f"{label} 宫五行"
    assert [l["liuqin"] for l in r.lines] == exp_liuqin, f"{label} 六亲"
    assert [l["dizhi"] for l in r.lines] == exp_dizhi, f"{label} 纳甲"
    assert r.question == "问财运"


# ---------- seed 复现（真实用户路径 + 代表性错例五字段） ----------

def test_seed353_five_fields_correct():
    """报告 Bug #1 代表性错例 seed=353（爻象 少阳+少阴×5）修复后五字段全对：
    本卦 地雷复 / 坤宫土 / 世0应3 / 纳甲 子寅辰丑亥酉 / 六亲 妻财官鬼兄弟兄弟妻财子孙。"""
    r = LiuyaoEngine().cast(method="random", seed=353)
    assert [rl["value"] for rl in r.raw_data["raw_lines"]] == [7, 8, 8, 8, 8, 8]
    assert r.original_hexagram == "地雷复", f"本卦名错: {r.original_hexagram}"
    assert r.palace == "坤" and r.palace_wuxing == "土", "宫/宫五行错"
    assert (r.shi_yao, r.ying_yao) == (0, 3), "世应错"
    assert [l["dizhi"] for l in r.lines] == ["子", "寅", "辰", "丑", "亥", "酉"], "纳甲错"
    assert [l["liuqin"] for l in r.lines] == \
        ["妻财", "官鬼", "兄弟", "兄弟", "妻财", "子孙"], "六亲错"
    assert r.changed_hexagram == "地雷复", "静卦变卦应同本卦"


def test_anchor_seeds_reproduce_authority_anchors():
    """真实用户路径：报告取证所用 seed（870/1526/5055/80768）逐一复现 L1-L4 锚点爻象与卦名。"""
    engine = LiuyaoEngine()
    for seed, totals, exp_orig, exp_chg in [
        (870, "888888", "坤为地", "坤为地"),
        (1526, "688888", "坤为地", "地雷复"),
        (5055, "886898", "水地比", "地山谦"),
        (80768, "696969", "火水未济", "水火既济"),
    ]:
        r = engine.cast(method="random", seed=seed)
        assert [rl["value"] for rl in r.raw_data["raw_lines"]] == [int(c) for c in totals], \
            f"seed={seed} 爻象与报告不符"
        assert r.original_hexagram == exp_orig, f"seed={seed} 本卦"
        assert r.changed_hexagram == exp_chg, f"seed={seed} 变卦"
