"""多盘对比（P1-2）：POST /api/duipan — 锚点 + 晚子时 + 同时辰 + 确定性 + 鉴权 + 校验。

锚点盘：1999-05-13 长春 男（与 test_paipan_api 同生日，长春真太阳时 +25 分钟）：
    辰时（序号4 → 07:00）：己卯 己巳 乙丑 庚辰（时柱庚辰，十神正官）
    午时（序号6 → 11:00）：己卯 己巳 乙丑 壬午（时柱壬午，十神正印）
    仅时柱变：庚辰→壬午；五行 金1木2水0火1土4 → 金0木2水1火2土3；
    用神核心同为「水」（喜用由 水、木 → 水、金、火）；格局同为偏财格；
    起运同为 3 岁（起运分解差 20 天：2年4月2天 vs 2年4月22天）；大运序列相同。
晚子时锚点：亥时（序号10 → 19:00）vs 子时（序号0 → 23:00 晚子时归次日 5/14）：
    日柱 乙丑→丙寅、日主 乙木→丙火（晚子时按次日排盘说明）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.api import duipan as duipan_api  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.engines.duipan import compare_pans, compare_summary  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402

# 锚点请求：1999-05-13 长春 男 辰时(序号4) vs 午时(序号6)
ANCHOR = {
    "birthYear": 1999, "birthMonth": 5, "birthDay": 13,
    "city": "长春", "gender": "male",
    "hourA": 4,          # 时辰序号 4 = 辰时（07:00）
    "hourB": 6,          # 时辰序号 6 = 午时（11:00）
}


def _client():
    duipan_api.setup(BaziEngine())
    set_auth_handler(AuthHandler())
    return TestClient(app)


def _token(uid="u_duipan_1"):
    return JWTHandler("test-secret-key-32-bytes-long!!").create_token(uid)


def _headers():
    return {"Authorization": f"Bearer {_token()}"}


# ---------------------------------------------------------------- 锚点：辰时 vs 午时
def test_duipan_anchor_chen_vs_wu():
    """1999-05-13 长春 男：辰时 vs 午时全字段差异锚点。"""
    r = _client().post("/api/duipan", json=ANCHOR, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()

    # ── pan_a / pan_b：复用排盘全字段 ──
    pa, pb = body["pan_a"], body["pan_b"]
    assert pa["bazi"] == ["己卯", "己巳", "乙丑", "庚辰"]
    assert pb["bazi"] == ["己卯", "己巳", "乙丑", "壬午"]
    assert pa["meta"]["shichen"] == "辰时" and pb["meta"]["shichen"] == "午时"
    assert pa["day_master"] == pb["day_master"] == "乙木"
    assert pa["wuxing"] == {"金": 1, "木": 2, "水": 0, "火": 1, "土": 4}
    assert pb["wuxing"] == {"金": 0, "木": 2, "水": 1, "火": 2, "土": 3}
    assert pa["shishen"] == ["偏财", "偏财", "日主", "正官"]
    assert pb["shishen"] == ["偏财", "偏财", "日主", "正印"]
    # 全字段复用（与 /api/paipan 输出同款）
    for pan in (pa, pb):
        required = {"bazi", "pillars", "day_master", "shishen", "nayin", "gender",
                    "jiaoyun", "siling", "qiyun_desc", "qiyun_detail", "dayun",
                    "liunian_full", "liunian_rel", "liuyue", "liushi",
                    "dayun_rel", "ganzhi_rel", "wuxing_energy", "chenggu",
                    "shensha_detail", "knowledge_index", "meta"}
        assert required <= set(pan.keys())
        assert len(pan["dayun"]) == 12

    # ── diff：四柱差异（仅时柱，变什么） ──
    d = body["diff"]
    assert d["same"] is False
    assert d["hours"] == {"a": "辰时", "b": "午时"}
    fp = d["four_pillars"]
    assert fp["same"] is False and fp["changed"] == ["时柱"]
    assert fp["changes"] == [{
        "pillar": "时柱", "a": "庚辰", "b": "壬午",
        "a_shishen": "正官", "b_shishen": "正印",
        "a_nayin": "白蜡金", "b_nayin": "杨柳木",
    }]

    # ── diff：日主（不变 + 非晚子时说明） ──
    dm = d["day_master"]
    assert dm["a"] == dm["b"] == "乙木" and dm["same"] is True
    assert "非晚子时" in dm["note"]

    # ── diff：五行能量 counts 差 ──
    wx = d["wuxing"]
    assert wx["same"] is False
    assert wx["diff"] == {"金": -1, "木": 0, "水": 1, "火": 1, "土": -1}
    assert wx["changes"] == [{"wuxing": "金", "delta": -1}, {"wuxing": "水", "delta": 1},
                             {"wuxing": "火", "delta": 1}, {"wuxing": "土", "delta": -1}]
    assert wx["a_strength"] == wx["b_strength"] == "偏弱"
    assert wx["strength_same"] is True

    # ── diff：用神（核心同「水」，喜用变） ──
    ys = d["yongshen"]
    assert ys["same"] is False
    assert ys["core_a"] == ys["core_b"] == "水" and ys["core_same"] is True
    assert "喜水、木" in ys["a"] and "喜水、金、火" in ys["b"]

    # ── diff：格局（同） ──
    assert d["geju"] == {"a": "偏财格", "b": "偏财格", "same": True}

    # ── diff：大运（起运岁数同 3 岁、序列同；起运分解差 20 天） ──
    dy = d["dayun"]
    assert dy["start_same"] is True and dy["a_start"] == dy["b_start"] == 3
    assert dy["start_gap"] == 0
    assert dy["a_qiyun"] == "出生后2年4月2天0时起运"
    assert dy["b_qiyun"] == "出生后2年4月22天0时起运"
    assert dy["sequence_same"] is True
    assert dy["a"][0] == {"sui": 3, "ganzhi": "戊辰"} == dy["b"][0]
    assert len(dy["a"]) == len(dy["b"]) == 12

    # ── diff：神煞（新增/消失） ──
    ss = d["shensha"]
    assert ss["same"] is False
    assert ss["added"] == ["文昌贵人", "天厨贵人", "桃花", "天喜", "勾绞煞", "红艳煞"]
    assert ss["removed"] == ["月德贵人", "天医", "童子煞"]
    assert set(ss["common"]) == set(pa["shensha"]) - set(ss["removed"])

    # ── summary：规则摘要（要点 + 变化影响） ──
    s = body["summary"]
    assert "两盘日主同为乙木" in s
    assert "四柱中仅时柱发生变化" in s and "庚辰→壬午" in s
    assert "金减1" in s and "水增1" in s
    assert "用神核心同为「水」" in s
    assert "格局同为偏财格" in s
    assert "起运岁数同为3岁" in s and "戊辰" in s
    assert "新增文昌贵人" in s and "消失月德贵人" in s


# ---------------------------------------------------------------- 晚子时：日主改变
def test_duipan_late_zi_day_master_change():
    """亥时 vs 子时：子时（23 点晚子时）归次日排盘 → 日柱/日主改变 + 说明。"""
    r = _client().post("/api/duipan", json={
        "birthYear": 1999, "birthMonth": 5, "birthDay": 13,
        "city": "长春", "gender": "男",
        "hourA": 10,   # 亥时（19:00）
        "hourB": 0,    # 子时（23:00 晚子时 → 按次日 5/14 排盘）
    }, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["pan_a"]["bazi"] == ["己卯", "己巳", "乙丑", "丙戌"]
    assert body["pan_b"]["bazi"] == ["己卯", "己巳", "丙寅", "戊子"]

    d = body["diff"]
    assert d["four_pillars"]["changed"] == ["日柱", "时柱"]
    dm = d["day_master"]
    assert dm["same"] is False and dm["a"] == "乙木" and dm["b"] == "丙火"
    assert "晚子时" in dm["note"] and "次日" in dm["note"]

    # 日柱改变连带：强弱 偏弱→偏旺、格局 偏财格→伤官格
    assert d["wuxing"]["strength_same"] is False
    assert d["wuxing"]["a_strength"] == "偏弱" and d["wuxing"]["b_strength"] == "偏旺"
    assert d["geju"] == {"a": "偏财格", "b": "伤官格", "same": False}
    assert d["dayun"]["start_same"] is True and d["dayun"]["a_start"] == 3

    s = body["summary"]
    assert "日主改变" in s and "两盘日主不同" in s
    assert "晚子时" in s and "次日排盘" in s
    assert "日主强弱由偏弱转为偏旺" in s
    assert "格局由偏财格变为伤官格" in s


# ---------------------------------------------------------------- 同时辰：diff 空
def test_duipan_same_hour_empty_diff():
    """hourA == hourB：两盘全同，diff 各节 same=True，摘要说明完全相同。"""
    r = _client().post("/api/duipan", json={
        "birthYear": 1999, "birthMonth": 5, "birthDay": 13,
        "city": "长春", "gender": "男", "hourA": 6, "hourB": 6,
    }, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["pan_a"] == body["pan_b"]
    d = body["diff"]
    assert d["same"] is True
    assert d["four_pillars"]["same"] is True and d["four_pillars"]["changed"] == []
    assert d["day_master"]["same"] is True
    assert d["wuxing"]["same"] is True and d["wuxing"]["diff"] == {"金": 0, "木": 0, "水": 0, "火": 0, "土": 0}
    assert d["yongshen"]["same"] is True
    assert d["geju"]["same"] is True
    assert d["dayun"]["start_same"] is True and d["dayun"]["sequence_same"] is True
    assert d["shensha"]["same"] is True
    assert d["shensha"]["added"] == [] and d["shensha"]["removed"] == []
    assert "完全相同" in body["summary"]


# ---------------------------------------------------------------- 确定性
def test_duipan_deterministic():
    """同请求两次 → 响应完全一致（0 LLM，纯规则确定性）。"""
    c = _client()
    r1 = c.post("/api/duipan", json=ANCHOR, headers=_headers())
    r2 = c.post("/api/duipan", json=ANCHOR, headers=_headers())
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()


# ---------------------------------------------------------------- 鉴权
def test_duipan_401_unauthorized():
    """未登录 → 401（require_user 鉴权红线；生辰为敏感数据）。"""
    r = _client().post("/api/duipan", json=ANCHOR)
    assert r.status_code == 401


# ---------------------------------------------------------------- 校验
@pytest.mark.parametrize("payload,msg", [
    ({}, "出生年/月/日不能为空"),
    ({"birthYear": 1999, "birthMonth": 2, "birthDay": 30,
      "hourA": 4, "hourB": 6}, "出生日期无效"),
    ({"birthYear": 1899, "birthMonth": 5, "birthDay": 13,
      "hourA": 4, "hourB": 6}, "出生年份"),
    ({"birthYear": 1999, "birthMonth": 5, "birthDay": 13,
      "hourA": 4}, "hourB 不能为空"),
    ({"birthYear": 1999, "birthMonth": 5, "birthDay": 13,
      "hourA": None, "hourB": 6}, "hourA 不能为空"),
    ({"birthYear": 1999, "birthMonth": 5, "birthDay": 13,
      "hourA": 4, "hourB": 24}, "hourB 须为 0-23"),
    ({"birthYear": 1999, "birthMonth": 5, "birthDay": 13,
      "hourA": -1, "hourB": 6}, "hourA 须为 0-23"),
])
def test_duipan_400_bad_input(payload, msg):
    """伪日期 / 越界 / 时辰缺失或越界 → 400（不直通引擎）。"""
    r = _client().post("/api/duipan", json=payload, headers=_headers())
    assert r.status_code == 400
    assert msg in r.json()["detail"]


# ---------------------------------------------------------------- 引擎级单测
def test_compare_pans_engine_direct():
    """compare_pans 直接调用：返回结构 + 与 API 输出同源。"""
    r = compare_pans((1999, 5, 13), 4, 6, "长春", "male")
    assert set(r.keys()) == {"pan_a", "pan_b", "diff", "summary"}
    assert r["pan_a"]["bazi"] == ["己卯", "己巳", "乙丑", "庚辰"]
    assert r["diff"]["four_pillars"]["changes"][0]["b"] == "壬午"
    assert isinstance(r["summary"], str) and "两盘日主同为乙木" in r["summary"]


def test_compare_summary_rule_templates():
    """compare_summary 规则模板：日主同/异、时柱变、完全相同分支。"""
    eng = BaziEngine()
    r_a = eng.calculate(1999, 5, 13, 7, 0, "长春", "男")    # 辰时
    r_b = eng.calculate(1999, 5, 13, 11, 0, "长春", "男")   # 午时
    r_c = eng.calculate(1999, 5, 13, 23, 0, "长春", "男")   # 晚子时
    s1 = compare_summary(r_a, r_b, (7, 11))
    assert "两盘日主同为乙木" in s1
    assert "四柱中仅时柱发生变化" in s1 and "庚辰→壬午" in s1
    assert "用神核心同为「水」" in s1
    s2 = compare_summary(r_b, r_c, (11, 23))
    assert "两盘日主不同" in s2 and "晚子时" in s2
    assert "日主强弱由偏弱转为偏旺" in s2
    s3 = compare_summary(r_a, r_a)
    assert s3 == "两盘完全相同：同一生辰、同一时辰排出的两盘结果一致，无差异。"
