"""R2-1 真太阳时默认关（问真口径）测试（2026-09-02，task-R2-1-brief.md）。

背景：用户拍板「对齐问真八字」——问真 App 真太阳时默认关（用户手动开才修正）。
此前 bazi.calculate 无条件做真太阳时修正（传 city 即修正，无开关）→ 时辰边界
错位（如 2019-03-15 北京 11:00-11:24 出生被修正入巳时，问真默认排午时）。

本批：calculate 加 solar_time: bool = False（默认关=问真口径）——False 时不做
修正、北京时间直接排盘；True 时保留原修正逻辑（问真用户手动开场景）。

午时边界复测表（2019-03-15 北京 男，日柱辛亥，实测引擎值——brief 要求实测确认）：
    时刻    | 默认关(修正前)   | 修正开(修正后)      | 差异
    9:00    | 09:00 癸巳(巳)   | 08:35 壬辰(辰)      | 翻转：修正入辰（巳时下界）
    11:05   | 11:05 甲午(午)   | 10:40 癸巳(巳)      | 翻转：修正入巳（错位窗口）
    11:30   | 11:30 甲午(午)   | 11:05 甲午(午)      | 同午（不翻转）
    13:00   | 13:00 乙未(未)   | 12:35 甲午(午)      | 翻转：修正掉回午
真正错位窗口（北京 3 月中旬，修正 ≈ -25 分钟）：每时辰下界后约 25 分钟内出生
者被移入前一时辰（9:00-9:24 → 辰；11:00-11:24 → 巳；13:00-13:24 → 午…）。
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
# (时刻(h,mi), 默认关 corrected_time, 默认关四柱, 修正开 corrected_time, 修正开四柱)
BOUNDARY_TABLE = [
    # 翻转例：9:00 恰为巳时下界 → 默认关巳时（癸巳），修正开入辰时（壬辰）
    ((9, 0), "09:00", ["己亥", "丁卯", "辛亥", "癸巳"],
     "08:35", ["己亥", "丁卯", "辛亥", "壬辰"]),
    # 翻转例（brief 修复目标 4 点名的错位窗口）：修正开掉入巳时 → 癸巳
    ((11, 5), "11:05", ["己亥", "丁卯", "辛亥", "甲午"],
     "10:40", ["己亥", "丁卯", "辛亥", "癸巳"]),
    # 11:30：修正开 11:05 仍午时——不翻转（brief 验证节「11:30 修正后落巳」为
    # 未经实测的预估值，实测修正后 11:05 ≥ 11:00 仍午时；错位窗口实测为
    # 11:00-11:24，测试以实测为准并取 11:05 为翻转代表例）
    ((11, 30), "11:30", ["己亥", "丁卯", "辛亥", "甲午"],
     "11:05", ["己亥", "丁卯", "辛亥", "甲午"]),
    # 13:00：默认关未时（乙未）vs 修正开掉回午时（甲午）——翻转例
    ((13, 0), "13:00", ["己亥", "丁卯", "辛亥", "乙未"],
     "12:35", ["己亥", "丁卯", "辛亥", "甲午"]),
]


@pytest.mark.parametrize(
    "hm,off_time,off_bazi,on_time,on_bazi", BOUNDARY_TABLE,
    ids=["0900_flip_into_chen", "1105_flip_into_si",
         "1130_wuzhen_no_flip", "1300_flip_into_wu"])
def test_wuzhen_boundary_recheck_20190315(hm, off_time, off_bazi,
                                          on_time, on_bazi):
    """2019-03-15 北京逐例复测：默认关 vs 修正开，四柱 + corrected_time 全对比。"""
    h, mi = hm
    off = ENGINE.calculate(2019, 3, 15, h, mi, "北京", "男")  # 不传 = 默认关
    on = ENGINE.calculate(2019, 3, 15, h, mi, "北京", "男", solar_time=True)
    assert off.corrected_time == off_time
    assert list(off.bazi) == off_bazi
    assert on.corrected_time == on_time
    assert list(on.bazi) == on_bazi


def test_solar_time_default_off_no_correction():
    """默认关（不传参）不做真太阳时修正：corrected_time = 北京时间原样；
    11:30 北京 → 午时（甲午）——与问真默认一致（修复前被修正挪盘的问题场景）。"""
    r = ENGINE.calculate(2019, 3, 15, 11, 30, "北京", "男")
    assert r.corrected_time == "11:30"          # 未修正
    assert list(r.bazi) == ["己亥", "丁卯", "辛亥", "甲午"]
    # 显式 False 与不传逐位一致
    off = ENGINE.calculate(2019, 3, 15, 11, 30, "北京", "男", solar_time=False)
    assert off.corrected_time == r.corrected_time
    assert list(off.bazi) == list(r.bazi)


def test_solar_time_on_keeps_old_correction_behavior():
    """开修正保持旧行为：2019-03-15 北京 11:05 修正后 10:40 → 巳时癸巳
    （旧无条件修正行为 = 问真用户手动开真太阳时的场景）。"""
    r = ENGINE.calculate(2019, 3, 15, 11, 5, "北京", "男", solar_time=True)
    assert r.corrected_time == "10:40"
    assert list(r.bazi) == ["己亥", "丁卯", "辛亥", "癸巳"]


def test_solar_time_no_city_no_correction_even_when_on():
    """修正开但城市未知/为空 → 不修正（_true_solar_time 原语义保留）。"""
    r = ENGINE.calculate(2019, 3, 15, 11, 30, "", "男", solar_time=True)
    assert r.corrected_time == "11:30"
    assert list(r.bazi) == ["己亥", "丁卯", "辛亥", "甲午"]


# ── T007 场景（1990-05-20 北京 男，日柱乙酉，纠正目标时间 15:00→14:30）──

def test_t007_contract_1430_beijing_wuzhen_guwei():
    """T007 契约口径：默认关下 14:30 北京 → 未时 → 时柱癸未（断言 contains 癸未
    不变）；14:30 在修正开/关双口径下均为未时癸未（修正开 14:19 亦未时），
    重排测试力保留（旧盘 15:30 → 申时甲申 ≠ 癸未）。"""
    for st in (False, True):
        r = ENGINE.calculate(1990, 5, 20, 14, 30, "北京", "男", solar_time=st)
        assert list(r.bazi) == ["庚午", "辛巳", "乙酉", "癸未"]
    # 修正开下 15:00 → 14:49 未时（旧契约目标时刻）与默认关 15:00 → 申时差异：
    # 默认关 15:00 与旧盘 15:30 同为甲申 → 失去重排区分力 → 契约须移到 14:30
    r15 = ENGINE.calculate(1990, 5, 20, 15, 0, "北京", "男")
    r1530 = ENGINE.calculate(1990, 5, 20, 15, 30, "北京", "男")
    assert list(r15.bazi) == list(r1530.bazi) == ["庚午", "辛巳", "乙酉", "甲申"]
    on15 = ENGINE.calculate(1990, 5, 20, 15, 0, "北京", "男", solar_time=True)
    assert list(on15.bazi)[3] == "癸未"          # 旧口径（修正开）的 15:00 期望值


# ── BaziInput 契约透传 ─────────────────────────────────────────────

def test_bazi_input_solar_time_field_and_resolve():
    """BaziInput.solarTime 默认 False（接口只增不减）；_resolve_person 透传
    （镜像 daylightSaving/lateChildHour 模式）。"""
    assert BaziInput().solarTime is False
    req = HehunRequest(person_a=BaziInput(
        year=2019, month=3, day=15, hour=11, minute=5, city="北京",
        gender="male", solarTime=True))
    resolved = _resolve_person(req.person_a)
    assert resolved.solarTime is True
    assert resolved.daylightSaving is False and resolved.lateChildHour is False
    # 旧请求（不传）→ False
    old = _resolve_person(BaziInput(year=2019, month=3, day=15,
                                    hour=11, minute=5, city="北京"))
    assert old.solarTime is False


# ── API 层端到端（paipan 端点 → _resolve_person → calculate）────────

def _client():
    paipan_api.setup(BaziEngine())
    set_auth_handler(AuthHandler())
    return TestClient(__import__("src.main", fromlist=["app"]).app)


def _headers():
    from src.security.auth import JWTHandler
    return {"Authorization": "Bearer %s" % JWTHandler("test-secret-key-32-bytes-long!!").create_token("u_r21")}


def test_paipan_api_default_off_vs_solar_time_true():
    """API 层：不传 solarTime（默认关）→ 2019-03-15 北京 13:00 = 未时乙未（问真
    口径，时钟小时 13 原样透传）；传 solarTime: true → 修正 12:35 掉回午时甲午
    （修正开保留）；显式 False 与不传逐位一致（接口只增不减，旧请求零影响）。

    注：hour 0-11 会被归一化为时辰序号（hehun 双契约），故翻转例取 13:00。"""
    c = _client()
    base = {
        "year": 2019, "month": 3, "day": 15, "hour": 13, "minute": 0,
        "city": "北京", "gender": "male",
    }
    off = c.post("/api/paipan", json=base, headers=_headers()).json()
    assert off["bazi"] == ["己亥", "丁卯", "辛亥", "乙未"]
    assert off["meta"]["solar_text"].endswith("13:00")
    on = c.post("/api/paipan", json=dict(base, solarTime=True),
                headers=_headers()).json()
    assert on["bazi"] == ["己亥", "丁卯", "辛亥", "甲午"]
    # 显式 False 与不传逐位一致（旧请求零影响）
    ex = c.post("/api/paipan", json=dict(base, solarTime=False),
                headers=_headers()).json()
    assert ex["bazi"] == off["bazi"]
