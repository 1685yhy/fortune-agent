"""K6 合婚文案修复测试（批 6：合婚+称骨文案修复）。

覆盖四处修复中的前三处（hehun）：
- B1：五行互补文案 {补} 占位符未替换（改前 H1-H3 输出 "一人缺{补}，二人X旺" 乱码）
- B2：日支六合 + 日干相克 同句「天缘甚佳」自相矛盾（改前 H3 实例）
- C3：天干五合（甲己/乙庚/丙辛/丁壬/戊癸）补吉判——问真「日柱天合地合」良缘提示口径，
  改前 戊癸合 被断「相克（凶）」2 分，改后「五合（吉）」8 分

评分变化（改前 → 改后，H1-H3 真实用户路径）：
- H1 周明×林悦（丙寅×甲子，无五合无日支合）：83（38+25+20）→ 83（38+25+20）零变化
- H2 赵磊×孙倩（甲子×甲子，无五合）：58（35+8+15）→ 58（35+8+15）零变化
- H3 吴昊×郑瑶（癸巳×戊申，戊癸五合＋巳申六合）：60（22+16+22）→ 66（22+16+28）
  rizhu: 日干 2→8（相克凶→五合吉）；档位 中等婚配 → 上等婚配（总分档位变化属行为修复，见报告）

案例四柱与批 7 教研报告（/tmp/compare_hehun.md §2）一致，问真 API 复核逐字相同。
"""
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from src.engines.bazi import BaziEngine, BaziResult  # noqa: E402
from src.engines.hehun import HehunEngine, HehunResult, TIANGAN_WUHE  # noqa: E402

_WX_BALANCED = {"金": 1, "木": 1, "水": 1, "火": 1, "土": 5}


def _mk(bazi_pillars, day_master, wuxing=None):
    """构造最小 BaziResult（bazi[0][1]=年支 生肖，bazi[2]=日柱）。"""
    return BaziResult(
        bazi=bazi_pillars, day_master=day_master,
        wuxing=wuxing or dict(_WX_BALANCED),
        shishen=[], dayun=[], liunian={}, geju="", yongshen="",
        shensha=[], nayin=[])


# ============================================================
# H1-H3 真实用户路径（BaziEngine 真实排盘 → HehunEngine.match）
# 输入与批 7 教研报告 §2 案例表逐字一致
# ============================================================
_H_CASES = [
    # (案例名, A 出生, B 出生, 改后总分, 改后 wuxing, shengxiao, rizhu)
    ("H1", (1990, 1, 1, 12, 0, "北京", "男"), (1992, 8, 16, 14, 30, "上海", "女"),
     83, 38, 25, 20),
    ("H2", (1986, 5, 20, 8, 0, "深圳", "男"), (1992, 8, 16, 14, 30, "上海", "女"),
     58, 35, 8, 15),
    ("H3", (1995, 3, 3, 6, 30, "成都", "男"), (1996, 11, 7, 22, 0, "广州", "女"),
     66, 22, 16, 28),
]


def _h3_pair():
    """H3 案例（C3 关键案例）：癸巳 × 戊申，戊癸五合＋巳申六合（日柱天合地合）。"""
    eng = BaziEngine()
    a = eng.calculate(*_H_CASES[2][1])
    b = eng.calculate(*_H_CASES[2][2])
    return a, b


# ---- ① B1：互补文案无 {补} 占位符残留 ----
@pytest.mark.parametrize("name,a,b,score,wx,sx,rz", _H_CASES)
def test_b1_complement_details_no_placeholder(name, a, b, score, wx, sx, rz):
    """B1：H1-H3 互补明细不再出现 `{补}`/未替换占位符，且实际补益五行已代入。"""
    eng = BaziEngine()
    he = HehunEngine()
    r = he.match(eng.calculate(*a), eng.calculate(*b))
    details = r.bazi_match["complement_details"]
    assert details, name
    for item in details:
        assert "{" not in item and "}" not in item, f"{name} 仍有占位符残留: {item}"
        assert "补" not in item, f"{name} 仍有「补」占位字: {item}"
        # 逐条语义：「一人缺X，二人X旺」或「二人缺X，一人X旺」，X 为实际五行
        assert item.count("缺") == 1 and item.count("旺") == 1, item
        wx_in = item.split(":")[0]
        assert f"缺{wx_in}" in item and f"{wx_in}旺" in item, item


def test_b1_h1_verbatim():
    """B1 逐字：H1 互补明细与修复后预期完全一致（5 条，五行已代入；集合遍历序无关）。"""
    eng = BaziEngine()
    r = HehunEngine().match(
        eng.calculate(*_H_CASES[0][1]), eng.calculate(*_H_CASES[0][2]))
    assert sorted(r.bazi_match["complement_details"]) == sorted([
        "金: 一人缺金，二人金旺", "水: 一人缺水，二人水旺", "土: 一人缺土，二人土旺",
        "火: 二人缺火，一人火旺", "木: 二人缺木，一人木旺",
    ])


# ---- ② B2：日支六合 + 日干相克 分别成句、方向一致 ----
def test_b2_liuhe_plus_xiangke_two_sentences():
    """B2：日支六合（吉）＋日干相克（凶，非五合）——两句分离，不再同句「天缘甚佳」。"""
    # 构造：日支 子丑六合；日干 甲木克戊土（非五合对）
    h = HehunEngine().match(
        _mk(["甲子", "丙寅", "甲子", "壬午"], "甲木"),
        _mk(["乙丑", "辛巳", "戊丑", "壬午"], "戊土"),
    )
    desc = h.rizhu
    # 日支六合 + 日干相克 均保留，且分句（。）分隔
    assert "日支六合（天赐良缘）" in desc
    assert "日干相克（凶）" in desc
    assert "。" in desc, f"两句未分离: {desc}"
    # 不再无条件断言天缘甚佳
    assert "天缘甚佳" not in desc
    # 吉（日支六合）凶（日干相克）方向各自明示，喜忧参半
    assert "喜忧参半" in desc
    # 评分仍按 六合20 + 相克2 = 22（只改文案，不改分值）
    assert h.rizhu_score == 22
    assert h.rizhu_detail["ri_zhi_relation"] == "六合（天赐良缘）"
    assert h.rizhu_detail["ri_gan_relation"] == "相克"
    assert h.rizhu_detail["rizhi_score"] == 20
    assert h.rizhu_detail["rigan_score"] == 2


def test_b2_sanhe_plus_xiangke_no_tianyuan():
    """B2 同型：日支三合（吉）＋日干相克（凶）——同样不出现「天缘甚佳」。"""
    h = HehunEngine().match(
        _mk(["甲子", "丙寅", "甲申", "壬午"], "甲木"),
        _mk(["乙丑", "辛巳", "戊子", "壬午"], "戊土"),
    )
    desc = h.rizhu
    assert "日支三合" in desc and "相克（凶）" in desc
    assert "天缘甚佳" not in desc
    assert "。" in desc


def test_b2_liuhe_plus_xiangsheng_keeps_tianyuan():
    """B2 反向保护：日支六合＋日干相生（吉吉）——原「天缘甚佳」句保留。"""
    h = HehunEngine().match(
        _mk(["甲子", "丙寅", "甲子", "壬午"], "甲木"),
        _mk(["乙丑", "辛巳", "丙丑", "壬午"], "丙火"),
    )
    desc = h.rizhu
    assert "日支六合（天赐良缘）" in desc and "日干相生" in desc
    assert "天缘甚佳" in desc  # 吉吉组合不受影响
    assert "。" not in desc


# ---- ③ C3：天干五合吉判 ----
@pytest.mark.parametrize("g1,g2", [
    ("甲", "己"), ("乙", "庚"), ("丙", "辛"), ("丁", "壬"), ("戊", "癸"),
    ("己", "甲"), ("庚", "乙"), ("辛", "丙"), ("壬", "丁"), ("癸", "戊"),
])
def test_c3_all_wuhe_pairs_judged_ji(g1, g2):
    """C3：十方向五合对全部判「五合（吉）」8 分，不再按「相克（凶）」2 分计。"""
    h = HehunEngine().match(
        _mk(["甲子", "丙寅", f"{g1}子", "壬午"], "甲木"),
        _mk(["乙丑", "辛巳", f"{g2}寅", "壬午"], "戊土"),
    )
    rd = h.rizhu_detail
    assert rd["ri_gan_relation"] == "五合（吉）", (g1, g2, rd["ri_gan_relation"])
    assert rd["rigan_score"] == 8, (g1, g2, rd["rigan_score"])
    # 日柱关系文案含吉判
    assert "五合（吉）" in h.rizhu


def test_c3_wuhe_table_constant():
    """C3：五合常量 = 五对十向，且每对含五行相克方向（正是旧逻辑误判相克的根因）。"""
    from src.engines.hehun import WUXING_KE, WUXING_TG
    assert len(TIANGAN_WUHE) == 10
    for (a, b) in TIANGAN_WUHE:
        assert (b, a) in TIANGAN_WUHE
    for (a, b) in TIANGAN_WUHE:
        if a < b:  # 每对必有（单向）相克关系，故旧逻辑一律命中「相克（凶）」
            wa, wb = WUXING_TG[a], WUXING_TG[b]
            assert WUXING_KE.get(wa) == wb or WUXING_KE.get(wb) == wa, (a, b)


def test_c3_h3_rizhu_not_xiangke_anymore():
    """C3 核心案例 H3（癸巳×戊申，戊癸五合）：日干不再断「相克（凶）」2 分。"""
    a, b = _h3_pair()
    assert a.bazi[2] == "癸巳" and b.bazi[2] == "戊申"
    h = HehunEngine().match(a, b)
    rd = h.rizhu_detail
    # 问真「日柱天合地合」良缘提示口径：天干戊癸五合（吉）＋地支巳申六合（吉）
    assert rd["ri_gan_relation"] == "五合（吉）"
    assert rd["rigan_score"] == 8
    assert rd["ri_zhi_relation"] == "六合（天赐良缘）"
    assert rd["rizhi_score"] == 20
    assert h.rizhu_score == 28  # 改前 22（2+20）
    assert "五合（吉）" in h.rizhu  # 日柱关系文案含吉判
    assert "天缘甚佳" in h.rizhu  # 吉吉组合保留（问真良缘提示方向）


@pytest.mark.parametrize("name,a,b,score,wx,sx,rz", _H_CASES)
def test_h1_h3_score_changes_recorded(name, a, b, score, wx, sx, rz):
    """C3 评分变化落定：H1-H3 总分与三项分值与修复后预期一致（报告 §评分变化表）。"""
    eng = BaziEngine()
    r = HehunEngine().match(eng.calculate(*a), eng.calculate(*b))
    assert r.score == score, name
    assert r.wuxing_score == wx and r.shengxiao_score == sx and r.rizhu_score == rz, name
    assert r.score == r.wuxing_score + r.shengxiao_score + r.rizhu_score


# ---- 失败路径 / 既有行为保护 ----
def test_no_wuhe_no_liuhe_ordinary_case_unchanged():
    """失败路径：无五合也无日支合的普通案例——除 B1 文案外其余输出零变化。"""
    eng = BaziEngine()
    r = HehunEngine().match(
        eng.calculate(*_H_CASES[0][1]), eng.calculate(*_H_CASES[0][2]))
    # H1 日柱文案逐字不变（日支平和 + 日干相生，且分值不变）
    assert r.rizhu == "日支平和（无特殊关系），日干相生"
    assert r.rizhu_score == 20
    assert r.bazi_match["day_master_relation"] == "相生（吉）"
    # H2 伏吟案例（甲子×甲子）文案逐字不变
    r2 = HehunEngine().match(
        eng.calculate(*_H_CASES[1][1]), eng.calculate(*_H_CASES[1][2]))
    assert r2.rizhu == "日支相同（性格相似），日干比和"
    assert r2.rizhu_score == 15


def test_single_missing_rizhu_no_crash():
    """失败路径：单方缺日柱（不足 3 柱）——走默认日柱，不崩。"""
    h = HehunEngine().match(
        _mk(["甲子", "丙寅"], "甲木"),
        _mk(["乙丑", "辛巳"], "戊土"),
    )
    assert h.rizhu_score == 18
    assert "无法获取完整日柱信息" in h.rizhu


# ---- 契约：HehunResult / rizhu_detail / bazi_match 字段形状不变 ----
def test_contract_hehun_result_fields_unchanged():
    """契约：HehunResult 字段集合与既有定义一致（零新增零删除）。"""
    expected = {
        "score", "bazi_match", "shengxiao", "shengxiao_detail", "rizhu",
        "rizhu_detail", "wuxing_score", "shengxiao_score", "rizhu_score",
        "advice",
    }
    assert {f.name for f in dataclasses.fields(HehunResult)} == expected


def test_contract_rizhu_and_wuxing_detail_keys_unchanged():
    """契约：rizhu_detail / bazi_match 内层字段形状不变（C3/B2 只改取值与文案）。"""
    h = HehunEngine().match(
        _mk(["甲子", "丙寅", "癸巳", "壬午"], "癸水"),
        _mk(["乙丑", "辛巳", "戊申", "壬午"], "戊土"),
    )
    assert set(h.rizhu_detail.keys()) == {
        "score", "description", "rizhu_1", "rizhu_2", "ri_gan_1", "ri_gan_2",
        "ri_zhi_1", "ri_zhi_2", "ri_gan_relation", "ri_zhi_relation",
        "rizhi_score", "rigan_score",
    }
    assert set(h.bazi_match.keys()) == {
        "score", "wuxing_1", "wuxing_2", "day_master_1", "day_master_2",
        "day_master_relation", "complement_count", "complement_details",
        "complement_desc", "score_breakdown",
    }
    # 五合仍计入日柱维度，不动生肖/互补维度
    assert h.shengxiao_score in (8, 12, 16, 23, 25)
    assert 0 <= h.wuxing_score <= 40
