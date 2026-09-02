"""R2-4 真太阳时默认开（产品口径）测试（2026-09-03，task-R2-4-brief.md）。

背景：用户 2026-09-03 产品裁决「真太阳时默认要开启，但可以允许用户关闭」——
R2-1（2026-09-02）曾默认关对齐问真 App 默认态（用户手动开才修正），本批
反转默认值：calculate 默认 solar_time=True（修正生效，所有排盘统一默认开）；
显式 solar_time=False = 用户关闭 = 北京时间直排（=问真默认态，与问真一致性
在「用户关闭后」成立）。True 分支修正逻辑零改动（与问真开状态一致已验证）。

午时边界复测表（2019-03-15 北京 男，日柱辛亥，实测引擎值）：
    时刻    | 显式关(修正前)    | 默认开(修正后)     | 差异
    9:00    | 09:00 癸巳(巳)    | 08:35 壬辰(辰)     | 翻转：修正入辰（巳时下界）
    11:05   | 11:05 甲午(午)    | 10:40 癸巳(巳)     | 翻转：修正入巳（错位窗口）
    11:30   | 11:30 甲午(午)    | 11:05 甲午(午)     | 同午（不翻转）
    13:00   | 13:00 乙未(未)    | 12:35 甲午(午)     | 翻转：修正掉回午
默认开=修正生效为产品口径（修正更准）；显式关=北京时间直排。边界错位窗口
（北京 3 月中旬修正 ≈ -25 分钟）属开状态修正固有属性——用户可在边界时辰处
关闭开关对比北京时间直排（产品口径，前端排盘页开关已加）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.engines.bazi import BaziEngine  # noqa: E402
from src.api import paipan as paipan_api  # noqa: E402
from src.api.hehun import BaziInput, _resolve_person, HehunRequest  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402


ENGINE = BaziEngine()

# ── 午时边界确定性复测表（2019-03-15 北京 男）──────────────────────────
# (时刻(h,mi), 显式关 corrected_time, 显式关四柱, 修正后 corrected_time, 修正后四柱)
# 默认开（不传参）= 修正后列；显式关（solar_time=False）= 修正前列。
BOUNDARY_TABLE = [
    # 翻转例：9:00 恰为巳时下界 → 默认开修正入辰时（壬辰）；显式关巳时（癸巳）
    ((9, 0), "09:00", ["己亥", "丁卯", "辛亥", "癸巳"],
     "08:35", ["己亥", "丁卯", "辛亥", "壬辰"]),
    # 翻转例（边界错位窗口代表）：默认开修正掉入巳时 → 癸巳；显式关午时甲午
    ((11, 5), "11:05", ["己亥", "丁卯", "辛亥", "甲午"],
     "10:40", ["己亥", "丁卯", "辛亥", "癸巳"]),
    # 11:30：默认开修正后 11:05 仍午时——不翻转（与显式关同为甲午，仅时刻差）
    ((11, 30), "11:30", ["己亥", "丁卯", "辛亥", "甲午"],
     "11:05", ["己亥", "丁卯", "辛亥", "甲午"]),
    # 13:00：默认开修正掉回午时（甲午）；显式关未时（乙未）——翻转例
    ((13, 0), "13:00", ["己亥", "丁卯", "辛亥", "乙未"],
     "12:35", ["己亥", "丁卯", "辛亥", "甲午"]),
]


@pytest.mark.parametrize(
    "hm,off_time,off_bazi,on_time,on_bazi", BOUNDARY_TABLE,
    ids=["0900_flip_into_chen", "1105_flip_into_si",
         "1130_wuzhen_no_flip", "1300_flip_into_wu"])
def test_wuzhen_boundary_recheck_20190315(hm, off_time, off_bazi,
                                          on_time, on_bazi):
    """2019-03-15 北京逐例复测：默认开（不传参 = 修正生效）vs 显式关，
    四柱 + corrected_time 全对比。"""
    h, mi = hm
    default = ENGINE.calculate(2019, 3, 15, h, mi, "北京", "男")  # 不传 = 默认开
    on = ENGINE.calculate(2019, 3, 15, h, mi, "北京", "男", solar_time=True)
    off = ENGINE.calculate(2019, 3, 15, h, mi, "北京", "男", solar_time=False)
    assert default.corrected_time == on.corrected_time == on_time
    assert list(default.bazi) == list(on.bazi) == on_bazi
    assert off.corrected_time == off_time
    assert list(off.bazi) == off_bazi


def test_solar_time_default_on_applies_correction():
    """默认开（不传参）做真太阳时修正：2019-03-15 北京 11:30 → 修正 11:05
    （corrected_time 非北京时间原样）；与显式 True 逐位一致。"""
    r = ENGINE.calculate(2019, 3, 15, 11, 30, "北京", "男")
    assert r.corrected_time == "11:05"          # 修正生效（-25 分）
    assert list(r.bazi) == ["己亥", "丁卯", "辛亥", "甲午"]
    on = ENGINE.calculate(2019, 3, 15, 11, 30, "北京", "男", solar_time=True)
    assert on.corrected_time == r.corrected_time
    assert list(on.bazi) == list(r.bazi)


def test_solar_time_off_no_correction_user_closed():
    """用户关闭（显式 solar_time=False）：不做真太阳时修正、北京时间直排
    （=问真默认态口径）——corrected_time = 北京时间原样，11:30 → 午时甲午。"""
    off = ENGINE.calculate(2019, 3, 15, 11, 30, "北京", "男", solar_time=False)
    assert off.corrected_time == "11:30"          # 未修正
    assert list(off.bazi) == ["己亥", "丁卯", "辛亥", "甲午"]


def test_solar_time_no_city_no_correction_even_default_on():
    """默认开但城市未知/为空 → 不修正（_true_solar_time 原语义保留：
    无出生地经度可校，原样排）。"""
    r = ENGINE.calculate(2019, 3, 15, 11, 30, "", "男")
    assert r.corrected_time == "11:30"
    assert list(r.bazi) == ["己亥", "丁卯", "辛亥", "甲午"]
    # 显式开 + 无城市同样不修正
    on = ENGINE.calculate(2019, 3, 15, 11, 30, "", "男", solar_time=True)
    assert on.corrected_time == "11:30"
    assert list(on.bazi) == list(r.bazi)


# ── T007 场景（1990-05-20 北京 男，日柱乙酉，纠正目标时间 15:00→14:30）──

def test_t007_contract_1430_beijing_wuzhen_guwei():
    """T007 契约口径：默认开下 14:30 北京 → 修正后 ≈14:19 仍未时 → 时柱癸未
    （contains 癸未 不变、契约仍绿）；14:30 在修正开/关双口径下均为未时癸未。
    默认开下 15:00 → 修正 14:49 未时癸未（旧契约目标时刻的修正开口径值）；
    显式关 15:00/15:30 → 北京时间申时甲申（问真默认态）。"""
    for st in (True, False):
        r = ENGINE.calculate(1990, 5, 20, 14, 30, "北京", "男", solar_time=st)
        assert list(r.bazi) == ["庚午", "辛巳", "乙酉", "癸未"]
    # 默认开（不传参）14:30 与显式开一致 → 癸未（契约绿）
    r_def = ENGINE.calculate(1990, 5, 20, 14, 30, "北京", "男")
    assert list(r_def.bazi) == ["庚午", "辛巳", "乙酉", "癸未"]
    # 默认开 15:00 → 修正回未时癸未（旧契约目标时刻口径）；
    # 显式关 15:00 与 15:30 → 申时甲申（与旧盘 15:30 同为甲申 → 失去重排区分力，
    # 契约须锚 14:30 的理由在显式关口径下成立）
    r15 = ENGINE.calculate(1990, 5, 20, 15, 0, "北京", "男")
    assert list(r15.bazi) == ["庚午", "辛巳", "乙酉", "癸未"]
    off15 = ENGINE.calculate(1990, 5, 20, 15, 0, "北京", "男", solar_time=False)
    off1530 = ENGINE.calculate(1990, 5, 20, 15, 30, "北京", "男", solar_time=False)
    assert list(off15.bazi) == list(off1530.bazi) == ["庚午", "辛巳", "乙酉", "甲申"]


# ── BaziInput 契约透传 ─────────────────────────────────────────────

def test_bazi_input_solar_time_default_true_and_resolve():
    """BaziInput.solarTime 默认 True（R2-4 反转，接口只增不减）；_resolve_person
    透传（镜像 daylightSaving/lateChildHour 模式）。"""
    assert BaziInput().solarTime is True
    req = HehunRequest(person_a=BaziInput(
        year=2019, month=3, day=15, hour=11, minute=5, city="北京",
        gender="male", solarTime=False))
    resolved = _resolve_person(req.person_a)
    assert resolved.solarTime is False
    assert resolved.daylightSaving is False and resolved.lateChildHour is False
    # 旧请求（不传）→ True（默认开=本批产品裁决；R2-1 期此断言为 False）
    old = _resolve_person(BaziInput(year=2019, month=3, day=15,
                                    hour=11, minute=5, city="北京"))
    assert old.solarTime is True


# ── API 层端到端（paipan 端点 → _resolve_person → calculate）────────

def _client():
    paipan_api.setup(BaziEngine())
    set_auth_handler(AuthHandler())
    return TestClient(__import__("src.main", fromlist=["app"]).app)


def _headers():
    from src.security.auth import JWTHandler
    return {"Authorization": "Bearer %s" % JWTHandler("test-secret-key-32-bytes-long!!").create_token("u_r24")}


def test_paipan_api_default_on_correction_applies():
    """API 层：不传 solarTime（默认开）→ 2019-03-15 北京 13:00 修正 12:35
    掉回午时甲午（修正生效）；传 solarTime: false → 未时乙未（用户关闭=
    北京时间直排）；显式 true 与不传逐位一致。

    注：hour 0-11 会被归一化为时辰序号（hehun 双契约），故翻转例取 13:00。"""
    c = _client()
    base = {
        "year": 2019, "month": 3, "day": 15, "hour": 13, "minute": 0,
        "city": "北京", "gender": "male",
    }
    default = c.post("/api/paipan", json=base, headers=_headers()).json()
    assert default["bazi"] == ["己亥", "丁卯", "辛亥", "甲午"]
    assert default["meta"]["solar_text"].endswith("13:00")   # meta 为输入时刻回显
    on = c.post("/api/paipan", json=dict(base, solarTime=True),
                headers=_headers()).json()
    assert on["bazi"] == default["bazi"]
    off = c.post("/api/paipan", json=dict(base, solarTime=False),
                 headers=_headers()).json()
    assert off["bazi"] == ["己亥", "丁卯", "辛亥", "乙未"]
    assert off["meta"]["solar_text"].endswith("13:00")
