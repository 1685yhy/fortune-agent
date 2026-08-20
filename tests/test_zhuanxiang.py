"""专项论断测试（L2-5）：论财 / 论事业 / 论健康。

- 引擎测试：三类型锚点盘（身强财旺/财多身弱、官印/无官、五行失衡盘）
  + 结构完整性 + 确定性（同盘同论断）+ 批量回归；
- API 测试：高级会员门控（免费 403 + VIP_REQUIRED）、会员/体验模式放行、
  双契约生辰字段、非法入参 400、未登录 401。
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.engines.bazi import BaziEngine  # noqa: E402
from src.engines.zhuanxiang import (  # noqa: E402
    lun_cai, lun_shiye, lun_jiankang, ZANGFU,
)

# ── 锚点盘（经引擎实测锁定，见任务交付锚点验证）─────────────────
ANCHORS = {
    # 财多身弱：己卯 己巳 乙丑 壬午（乙木偏弱，财土透 2 得令）
    "cai_duo_shen_ruo": (1999, 5, 13, 11, 25, "北京", "男"),
    # 身强财旺：壬午 癸丑 戊子 癸丑（戊土偏旺，财水透 3）
    "cai_wang_shen_qiang": (2003, 1, 15, 3, 0, "北京", "女"),
    # 官印相生：丙寅 戊戌 庚寅 丁丑（庚金，七杀+正官+偏印）
    "guan_yin": (1986, 10, 13, 1, 0, "北京", "男"),
    # 无官盘（食伤吐秀）：戊辰 庚申 己亥 辛未（伤官食神透 2，无官杀）
    "wu_guan": (1988, 8, 12, 14, 0, "北京", "男"),
    # 五行失衡盘：己卯 丙子 戊午 丁巳（火 4 过旺、金 0 不足）
    "wuxing_imbalance": (2000, 1, 1, 10, 0, "北京", "男"),
    # 财星不显盘：戊寅 乙卯 己巳 庚午（己土，财水 0 透 0 藏）
    "cai_bu_xian": (1998, 3, 23, 13, 0, "北京", "女"),
    # 身强劫财盘：乙丑 己卯 甲戌 乙丑（甲木旺，劫财透 2 正财透 1、财地支 3）
    # —— 劫财夺财须身弱，身强时劫财为用，应判身财相当而非凶局（终审 Fix）
    "shen_qiang_jiecai": (1985, 4, 5, 2, 0, "北京", "女"),
    # 身弱劫财夺财盘：庚申 丁亥 丙戌 丁酉（丙火弱，劫财透 2、财地支 2）
    # —— 身弱劫财夺财凶局仍须命中（正例锁定，防修复矫枉过正）
    "shen_ruo_jiecai_duocai": (1980, 11, 9, 17, 0, "北京", "女"),
}

_engine = BaziEngine()


def _chart(name: str):
    return _engine.calculate(*ANCHORS[name])


# ── 结构完整性 ───────────────────────────────────────────────────

def _assert_structure(d: dict, expect_luck_phase: bool):
    assert isinstance(d, dict)
    assert isinstance(d.get("summary"), str) and d["summary"]
    points = d.get("points")
    assert isinstance(points, list) and len(points) >= 3
    for p in points:
        assert isinstance(p.get("title"), str) and p["title"]
        assert isinstance(p.get("text"), str) and p["text"]
    if expect_luck_phase:
        lp = d.get("luck_phase")
        assert isinstance(lp, list)
        for item in lp:
            assert isinstance(item.get("sui"), int)
            assert len(item.get("ganzhi", "")) == 2
            assert isinstance(item.get("desc"), str) and item["desc"]
    else:
        assert "luck_phase" not in d


def test_structure_cai():
    _assert_structure(lun_cai(_chart("cai_duo_shen_ruo")), expect_luck_phase=True)


def test_structure_shiye():
    _assert_structure(lun_shiye(_chart("guan_yin")), expect_luck_phase=True)


def test_structure_jiankang():
    _assert_structure(lun_jiankang(_chart("wuxing_imbalance")), expect_luck_phase=False)


def test_batch_structure_regression():
    """批量回归：多盘 × 三类型结构完整性（确定性产出，防引擎退化）。"""
    for name in ANCHORS:
        r = _chart(name)
        _assert_structure(lun_cai(r), expect_luck_phase=True)
        _assert_structure(lun_shiye(r), expect_luck_phase=True)
        _assert_structure(lun_jiankang(r), expect_luck_phase=False)


def test_deterministic():
    """确定性：同一命盘两次论断结果完全一致（纯规则，无随机）。"""
    for name in ANCHORS:
        r = _chart(name)
        for fn in (lun_cai, lun_shiye, lun_jiankang):
            assert json.dumps(fn(r), ensure_ascii=False) == \
                   json.dumps(fn(r), ensure_ascii=False), (name, fn.__name__)


# ── 论财锚点 ────────────────────────────────────────────────────

def test_lun_cai_caiduo_shenruo():
    """财多身弱盘：己卯 己巳 乙丑 壬午 —— 乙木偏弱、财土透 2 得令。"""
    d = lun_cai(_chart("cai_duo_shen_ruo"))
    assert "财多身弱" in d["summary"]
    geju_point = [p for p in d["points"] if p["title"] == "财运格局"][0]
    assert "富屋贫人" in geju_point["text"]
    # 财星透干要点如实反映透干数
    tougan_point = [p for p in d["points"] if p["title"] == "财星透干"][0]
    assert "偏财透 2" in tougan_point["text"]
    # 财运大运阶段非空（土/水运）
    assert d["luck_phase"], "财多身弱盘应存在财星大运阶段"


def test_lun_cai_shenqiang_caiwang():
    """身强财旺盘：壬午 癸丑 戊子 癸丑 —— 戊土偏旺、财水透 3。"""
    d = lun_cai(_chart("cai_wang_shen_qiang"))
    assert "身强财旺" in d["summary"]
    geju_point = [p for p in d["points"] if p["title"] == "财运格局"][0]
    assert "能挣能守" in geju_point["text"]
    assert d["luck_phase"]


def test_lun_cai_cai_bu_xian():
    """财星不显盘：戊寅 乙卯 己巳 庚午 —— 己土，财（水）0 透 0 藏。"""
    d = lun_cai(_chart("cai_bu_xian"))
    assert "财星不显" in d["summary"]
    geju_point = [p for p in d["points"] if p["title"] == "财运格局"][0]
    assert "不透不藏" in geju_point["text"]


def test_lun_cai_dizhi_cang_cai_not_bu_xian():
    """边界：财星不透干但地支有藏（亥水/子水）→ 不落入「财星不显」。"""
    # 戊辰 庚申 己亥 辛未：财水不透干，地支亥水 1 位
    d = lun_cai(_chart("wu_guan"))
    assert "财星不显" not in d["summary"]
    # 己卯 丙子 戊午 丁巳：财水不透干，地支子水 1 位
    d2 = lun_cai(_chart("wuxing_imbalance"))
    assert "财星不显" not in d2["summary"]


def test_lun_cai_shen_qiang_jiecai_not_duocai():
    """身强劫财盘：乙丑 己卯 甲戌 乙丑 —— 甲木旺，劫财透 2 为用（帮身任财），
    不判「劫财夺财」凶局（终审 Fix：劫财夺财须叠加身弱条件）。"""
    d = lun_cai(_chart("shen_qiang_jiecai"))
    assert "旺" in d["summary"]  # 日主身强前提
    assert "劫财夺财" not in d["summary"]
    assert "身财相当" in d["summary"]  # 落入身强吉局分支


def test_lun_cai_shen_ruo_jiecai_duocai():
    """身弱劫财夺财盘：庚申 丁亥 丙戌 丁酉 —— 丙火弱，劫财透 2、财地支 2，
    身弱劫财夺财凶局仍须命中（防修复矫枉过正）。"""
    d = lun_cai(_chart("shen_ruo_jiecai_duocai"))
    assert "弱" in d["summary"]  # 日主身弱前提
    assert "劫财夺财" in d["summary"]
    geju_point = [p for p in d["points"] if p["title"] == "财运格局"][0]
    assert "破财" in geju_point["text"]


# ── 论事业锚点 ──────────────────────────────────────────────────

def test_lun_shiye_guanyin_xiangsheng():
    """官印相生盘：丙寅 戊戌 庚寅 丁丑 —— 七杀+正官+偏印透干。"""
    d = lun_shiye(_chart("guan_yin"))
    assert "官印相生" in d["summary"]
    assert "官杀透干 2" in d["points"][0]["text"]
    geju_point = [p for p in d["points"] if p["title"] == "事业格局"][0]
    assert "贵人" in geju_point["text"]
    assert d["luck_phase"]


def test_lun_shiye_wu_guan():
    """无官盘：戊辰 庚申 己亥 辛未 —— 无官杀无印，食伤吐秀。"""
    d = lun_shiye(_chart("wu_guan"))
    assert "食伤吐秀" in d["summary"]
    # 才华倾向要点应在
    titles = [p["title"] for p in d["points"]]
    assert "才华倾向" in titles
    assert d["luck_phase"]


# ── 论健康锚点 ──────────────────────────────────────────────────

def test_lun_jiankang_wuxing_imbalance():
    """五行失衡盘：己卯 丙子 戊午 丁巳 —— 火 4 过旺、金 0 不足。"""
    d = lun_jiankang(_chart("wuxing_imbalance"))
    s = d["summary"] + "".join(p["text"] for p in d["points"])
    assert "火" in s and "过旺" in s
    assert "金" in s and "不足" in s
    # 脏腑对应：火→心、小肠；金→肺、大肠
    assert "心、小肠" in s and "肺、大肠" in s
    # 冬月（子月）体质倾向
    assert "冬月" in s


def test_lun_jiankang_zangfu_mapping_complete():
    """脏腑映射表完整性：五行 → 脏腑 5 组齐备（木肝/火心/土脾胃/金肺/水肾）。"""
    assert set(ZANGFU.keys()) == {"木", "火", "土", "金", "水"}
    assert ZANGFU["木"][0] == "肝胆"
    assert ZANGFU["火"][0] == "心、小肠"
    assert ZANGFU["土"][0] == "脾胃"
    assert ZANGFU["金"][0] == "肺、大肠"
    assert ZANGFU["水"][0] == "肾、膀胱"


def test_lun_jiankang_no_luck_phase():
    """论健康不输出运程阶段（原局失衡为主）。"""
    for name in ANCHORS:
        d = lun_jiankang(_chart(name))
        assert "luck_phase" not in d


# ── API 测试（高级会员门控）─────────────────────────────────────

class _FakeMemberDAO:
    """测试桩：get_membership 返回指定档位。"""

    def __init__(self, plan: str = "free"):
        self.plan = plan

    def get_membership(self, uid: str) -> dict:
        return {"user_id": uid, "plan": self.plan, "plan_label": "专业版"}


@pytest.fixture(scope="module")
def client():
    from src.security.auth import AuthHandler, JWTHandler, set_auth_handler
    from src.main import app
    from src.api import zhuanxiang as zx_api
    set_auth_handler(AuthHandler())
    zx_api.setup(member_dao=_FakeMemberDAO("free"), bazi_engine=BaziEngine())
    return TestClient(app), JWTHandler("test-secret-key-32-bytes-long!!")


def _post(client, token, payload):
    headers = {"Authorization": "Bearer %s" % token} if token else {}
    return client.post("/api/zhuanxiang", json=payload, headers=headers)


def _birth(**kw):
    # hour=12（时钟小时）→ 绕过 normalize_hour 的时辰序号(0-11)转换，落午时
    base = {"year": 1999, "month": 5, "day": 13, "hour": 12,
            "minute": 25, "city": "北京", "gender": "男"}
    base.update(kw)
    return base


def test_api_free_user_403_vip_required(client, monkeypatch):
    """门控：免费用户 403 + {code: VIP_REQUIRED, message}（前端弹开通引导）。"""
    import src.api.zhuanxiang as zx_api
    zx_api.setup(member_dao=_FakeMemberDAO("free"), bazi_engine=BaziEngine())
    monkeypatch.setattr(zx_api, "is_experience_mode", lambda: False)
    app, jwt = client
    token = jwt.create_token("u_free")
    r = _post(app, token, {"type": "cai", "birth": _birth()})
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["code"] == "VIP_REQUIRED"
    assert "高级会员" in body["detail"]["message"]


def test_api_member_200(client, monkeypatch):
    """门控：会员（plan != free）放行。"""
    import src.api.zhuanxiang as zx_api
    zx_api.setup(member_dao=_FakeMemberDAO("pro"), bazi_engine=BaziEngine())
    monkeypatch.setattr(zx_api, "is_experience_mode", lambda: False)
    app, jwt = client
    token = jwt.create_token("u_vip")
    r = _post(app, token, {"type": "cai", "birth": _birth()})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["type"] == "cai"
    assert data["bazi"] == ["己卯", "己巳", "乙丑", "壬午"]
    assert data["day_master"] == "乙木"
    assert "财多身弱" in data["result"]["summary"]


def test_api_experience_mode_free_200(client, monkeypatch):
    """门控：体验模式全免费（免费用户也放行）。"""
    import src.api.zhuanxiang as zx_api
    zx_api.setup(member_dao=_FakeMemberDAO("free"), bazi_engine=BaziEngine())
    monkeypatch.setattr(zx_api, "is_experience_mode", lambda: True)
    app, jwt = client
    token = jwt.create_token("u_exp")
    r = _post(app, token, {"type": "jiankang", "birth": _birth()})
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "jiankang"


def test_api_unauthorized_401(client):
    """未登录 401。"""
    app, _ = client
    r = _post(app, None, {"type": "cai", "birth": _birth()})
    assert r.status_code == 401


def test_api_invalid_type_400(client):
    """非法 type → 400。"""
    from src.api import zhuanxiang as zx_api
    zx_api.setup(member_dao=_FakeMemberDAO("pro"), bazi_engine=BaziEngine())
    app, jwt = client
    r = _post(app, jwt.create_token("u_vip"), {"type": "love", "birth": _birth()})
    assert r.status_code == 400
    assert "cai" in r.json()["detail"]


def test_api_missing_birth_400(client):
    """缺出生年/月/日 → 400。"""
    from src.api import zhuanxiang as zx_api
    zx_api.setup(member_dao=_FakeMemberDAO("pro"), bazi_engine=BaziEngine())
    app, jwt = client
    r = _post(app, jwt.create_token("u_vip"), {"type": "cai", "birth": {}})
    assert r.status_code == 400


def test_api_invalid_date_400(client):
    """伪日期（2 月 30 日）→ 400。"""
    from src.api import zhuanxiang as zx_api
    zx_api.setup(member_dao=_FakeMemberDAO("pro"), bazi_engine=BaziEngine())
    app, jwt = client
    r = _post(app, jwt.create_token("u_vip"),
              {"type": "cai", "birth": _birth(month=2, day=30)})
    assert r.status_code == 400


def test_api_miniprogram_contract_200(client, monkeypatch):
    """双契约回归：小程序字段（birthYear/birthMonth/birthDay/birthHour 时辰序号）。"""
    import src.api.zhuanxiang as zx_api
    zx_api.setup(member_dao=_FakeMemberDAO("pro"), bazi_engine=BaziEngine())
    monkeypatch.setattr(zx_api, "is_experience_mode", lambda: False)
    app, jwt = client
    payload = {"type": "shiye", "birth": {
        "birthYear": 1986, "birthMonth": 10, "birthDay": 13,
        "birthHour": 1, "gender": "male", "city": "北京"}}
    r = _post(app, jwt.create_token("u_vip"), payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["bazi"] == ["丙寅", "戊戌", "庚寅", "丁丑"]
    assert "官印相生" in data["result"]["summary"]


def test_api_member_dao_unavailable_503(client, monkeypatch):
    """会员服务未注入 → 503（非会员不误放行）。"""
    import src.api.zhuanxiang as zx_api
    zx_api.setup(member_dao=None, bazi_engine=BaziEngine())
    monkeypatch.setattr(zx_api, "is_experience_mode", lambda: False)
    app, jwt = client
    r = _post(app, jwt.create_token("u_none"), {"type": "cai", "birth": _birth()})
    assert r.status_code == 503
