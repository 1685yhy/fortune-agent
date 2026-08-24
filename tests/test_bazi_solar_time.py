"""真太阳时修正测试 — 对齐问真口径（经度修正 + 均时差）。

口径：用户填北京时间出生 → 按出生地经度修正为真太阳时后排全盘。
  真太阳时 = 北京时间 + (经度-120)*4分钟 + 均时差
  不传 city / 未知城市 → 不修正（兼容原行为）。
锚点（问真网页）：长春 1999-05-13 10:56 → 修正 ~11:21 → 午时 → 时柱壬午。
"""
from datetime import date

from src.engines.bazi import BaziEngine


ENGINE = BaziEngine()


# ---------- 均时差 ----------

def test_eot_known_anchors():
    """经典公式已知锚点（±1 分钟容差）：
    2月11日 ≈ -14.2 分，5月14日 ≈ +3.7 分，11月3日 ≈ +16.4 分。"""
    assert abs(ENGINE._equation_of_time(date(1999, 2, 11)) - (-14.2)) <= 1.0
    assert abs(ENGINE._equation_of_time(date(1999, 5, 14)) - 3.7) <= 1.0
    assert abs(ENGINE._equation_of_time(date(1999, 11, 3)) - 16.4) <= 1.0


def test_eot_bounded():
    """全年均时差在 ±17 分钟内（理论极值约 ±16.4）。"""
    for m, d in ((1, 1), (2, 15), (4, 15), (6, 21), (8, 15), (9, 23), (12, 22)):
        assert abs(ENGINE._equation_of_time(date(1999, m, d))) <= 17.0


# ---------- 修正时间 ----------

def test_true_solar_changchun():
    """长春 1999-05-13 10:56 → +25.2 分（经度 +21.4，均时差 +3.8）→ 11:21 午时（问真锚点）。"""
    assert ENGINE._true_solar_time(1999, 5, 13, 10, 56, "长春") == (1999, 5, 13, 11, 21)
    # 带「市」后缀同样可查
    assert ENGINE._true_solar_time(1999, 5, 13, 10, 56, "长春市") == (1999, 5, 13, 11, 21)


def test_true_solar_yushu():
    """榆树 126.53°E → 1999-05-13 10:55 → +29.9 分（经度 +26.1，均时差 +3.75）→ 11:24 午时。
    与吉林市（126.55°E，经度差 0.02°）修正后分钟数一致。"""
    assert ENGINE._true_solar_time(1999, 5, 13, 10, 55, "榆树") == (1999, 5, 13, 11, 24)
    # 带「市」后缀同样可查
    assert ENGINE._true_solar_time(1999, 5, 13, 10, 55, "榆树市") == (1999, 5, 13, 11, 24)
    assert ENGINE._true_solar_time(1999, 5, 13, 10, 55, "吉林市") == (1999, 5, 13, 11, 24)


def test_true_solar_beijing():
    """北京 1999-05-13 10:56 → -10.6 分 → 10:45，仍在巳时（时柱不变）。"""
    assert ENGINE._true_solar_time(1999, 5, 13, 10, 56, "北京") == (1999, 5, 13, 10, 45)


def test_true_solar_urumqi():
    """乌鲁木齐 87.62°E → (87.62-120)*4 = -129.5 分；2000-06-01 20:00 → ~17:52。"""
    assert ENGINE._true_solar_time(2000, 6, 1, 20, 0, "乌鲁木齐") == (2000, 6, 1, 17, 52)


def test_no_correction_for_unknown_or_empty_city():
    """未知城市 / 空 city → 不修正（兼容原行为）。"""
    assert ENGINE._true_solar_time(1999, 5, 13, 10, 56, "火星") == (1999, 5, 13, 10, 56)
    assert ENGINE._true_solar_time(1999, 5, 13, 10, 56, "") == (1999, 5, 13, 10, 56)
    assert ENGINE._true_solar_time(1999, 5, 13, 10, 56, None) == (1999, 5, 13, 10, 56)


def test_cross_day_correction():
    """修正可跨日：乌鲁木齐 00:10 → 前一日 22:02（-127.3 分）；长春 23:40 → 次日 00:05。"""
    assert ENGINE._true_solar_time(2000, 6, 1, 0, 10, "乌鲁木齐") == (2000, 5, 31, 22, 2)
    assert ENGINE._true_solar_time(1999, 5, 13, 23, 40, "长春") == (1999, 5, 14, 0, 5)


# ---------- 排盘应用 ----------

def test_changchun_wenzhen_anchor():
    """问真锚点：长春 1999-05-13 10:56 男 → 修正 11:21 午时 → 时柱壬午，全盘与问真一致。"""
    r = ENGINE.calculate(1999, 5, 13, 10, 56, "长春", "男")
    assert r.bazi == ["己卯", "己巳", "乙丑", "壬午"], r.bazi
    assert r.dayun[0][0] == 3  # 起运虚岁与问真一致（修正后起运距离）


def test_yushu_matches_jilin_city():
    """榆树 1999-05-13 10:55 男 → 修正 11:24 午时 → 时柱壬午；
    与吉林市（经度差 0.02°）同输入全盘完全一致。"""
    a = ENGINE.calculate(1999, 5, 13, 10, 55, "榆树", "男")
    b = ENGINE.calculate(1999, 5, 13, 10, 55, "吉林市", "男")
    assert a.corrected_time == "11:24"
    assert a.corrected_time == b.corrected_time
    assert a.bazi == b.bazi == ["己卯", "己巳", "乙丑", "壬午"]


def test_beijing_unchanged():
    """北京 1999-05-13 10:56 → 10:45 巳时，时柱仍为辛巳（与修正前一致）。"""
    r = ENGINE.calculate(1999, 5, 13, 10, 56, "北京", "男")
    assert r.bazi == ["己卯", "己巳", "乙丑", "辛巳"], r.bazi


def test_urumqi_evening_goes_to_you():
    """乌鲁木齐 2000-06-01 20:00 → 17:52 酉时（不做修正则为戌时 丙戌，修正后 乙酉）。"""
    r = ENGINE.calculate(2000, 6, 1, 20, 0, "乌鲁木齐", "男")
    assert r.bazi[3] == "乙酉", r.bazi


def test_urumqi_late_night_same_day():
    """乌鲁木齐 23:30 → 21:22 亥时，不跨日：日柱不变（2000-06-01 庚寅）。"""
    r = ENGINE.calculate(2000, 6, 1, 23, 30, "乌鲁木齐", "女")
    assert r.bazi[2] == "庚寅", r.bazi
    assert r.bazi[3] == "丁亥", r.bazi


def test_cross_day_backwards_pillars():
    """跨日（提前）：乌鲁木齐 2000-06-01 00:10 → 修正 2000-05-31 22:02，
    四柱应按修正后日期（前一日）排全盘。"""
    r = ENGINE.calculate(2000, 6, 1, 0, 10, "乌鲁木齐", "男")
    expected = ENGINE.calculate(2000, 5, 31, 22, 2, "", "男")  # 不修正口径
    assert r.bazi == expected.bazi, f"{r.bazi} != {expected.bazi}"
    assert r.dayun == expected.dayun


def test_cross_day_forward_pillars():
    """跨日（推迟）：长春 1999-05-13 23:40 → 修正 1999-05-14 00:05，日柱用次日。"""
    r = ENGINE.calculate(1999, 5, 13, 23, 40, "长春", "女")
    expected = ENGINE.calculate(1999, 5, 14, 0, 5, "", "女")
    assert r.bazi == expected.bazi, f"{r.bazi} != {expected.bazi}"
    assert r.bazi[2] != "乙丑"  # 日柱确已跨到次日


def test_unknown_city_same_as_no_city():
    """未知城市 = 不修正：与不传 city 结果完全一致（全盘回归兼容）。"""
    a = ENGINE.calculate(1990, 5, 20, 15, 0, "火星", "男")
    b = ENGINE.calculate(1990, 5, 20, 15, 0, "", "男")
    assert a.bazi == b.bazi
    assert a.dayun == b.dayun
    assert a.shensha == b.shensha
