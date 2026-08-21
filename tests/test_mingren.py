"""名人命例库测试（L3-1）：2807 例 + 穷通宝鉴评注，免费 35 例/高级会员全量（问真同款门控）。

- 数据：data/mingren/emperors_mingli.json 2807 条（库顺序 = 文件顺序，前 35 免费）
- 门控口径：pro/annual → 全量；free/basic → 前 35 条（列表 + 详情双重强制）
- API 测试：分页/搜索/免费 35 条限制/高级全量/详情 403 解锁/401/体验模式/503
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.api import mingren as mingren_api  # noqa: E402

FREE_LIMIT = 35
LIBRARY_TOTAL = 2807


class _FakeMemberDAO:
    """测试桩：get_membership 返回指定档位。"""

    def __init__(self, plan: str = "free"):
        self.plan = plan

    def get_membership(self, uid: str) -> dict:
        return {"user_id": uid, "plan": self.plan, "plan_label": "会员"}


@pytest.fixture(scope="module")
def client():
    from src.security.auth import AuthHandler, JWTHandler, set_auth_handler
    from src.main import app
    set_auth_handler(AuthHandler())
    mingren_api.setup(member_dao=_FakeMemberDAO("free"))
    return TestClient(app), JWTHandler("test-secret-key-32-bytes-long!!")


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    """默认关闭体验模式（项目 .env 为 true，门控用例须显式关掉）：
    与 test_zhuanxiang 各用例显式 monkeypatch 的口径一致。"""
    monkeypatch.setattr(mingren_api, "is_experience_mode", lambda: False)


def _get(client, token, path, params=None):
    headers = {"Authorization": "Bearer %s" % token} if token else {}
    return client.get(path, params=params or {}, headers=headers)


# ── 数据完整性 ───────────────────────────────────────────────────

def test_data_loaded_2807():
    """数据完整性：2807 条，库顺序稳定（前 35 = 免费可见名单）。"""
    data = mingren_api._load_data()
    assert len(data) == LIBRARY_TOTAL
    names = list(data.keys())
    assert names[0] == "忽必烈"
    assert len(names[:FREE_LIMIT]) == FREE_LIMIT
    # 前 35 与第 36 位确定（门控边界锚点）
    assert names[34] == "严嵩"
    assert names[35] == "赵文华"
    # 结构：info/info2/flist 字段齐备
    item = data["朱元璋"]
    assert item["info"]
    assert "徐乐吾曰" in item["info2"]
    assert isinstance(item["flist"], list) and len(item["flist"]) >= 5


# ── 列表：分页 / 搜索 / 门控 ────────────────────────────────────

def test_list_pagination_pro(client):
    """高级会员：全量分页（total == 2807，is_full=true）。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("pro"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_pro"), "/api/mingren", {"page": 1, "size": 20})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 20
    assert body["total"] == LIBRARY_TOTAL
    assert body["total_all"] == LIBRARY_TOTAL
    assert body["library_total"] == LIBRARY_TOTAL
    assert body["is_full"] is True
    # 条目结构
    it = body["items"][0]
    assert it["name"] == "忽必烈"
    assert "info" in it and "info2" in it and "has_info2" in it

    # 第 2 页与第 1 页不重叠（越过 35 边界仍可取 → 全量）
    r2 = _get(app, jwt.create_token("u_pro"), "/api/mingren", {"page": 2, "size": 20})
    names1 = {i["name"] for i in body["items"]}
    names2 = {i["name"] for i in r2.json()["items"]}
    assert not (names1 & names2)


def test_list_free_only_35(client):
    """免费用户：只返回前 35 条（total=35，is_full=false），35 之后取不到。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("free"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_free"), "/api/mingren", {"page": 1, "size": 100})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == FREE_LIMIT
    assert body["total"] == FREE_LIMIT
    assert body["is_full"] is False
    assert body["library_total"] == LIBRARY_TOTAL
    assert body["items"][0]["name"] == "忽必烈"
    assert body["items"][-1]["name"] == "严嵩"  # 第 35 位

    # 第 3 页（offset 40）已越界 → 空
    r3 = _get(app, jwt.create_token("u_free"), "/api/mingren", {"page": 3, "size": 20})
    assert r3.json()["items"] == []


def test_list_basic_same_as_free(client):
    """门控口径：basic（基础会员）同免费 → 35 条，is_full=false。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("basic"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_basic"), "/api/mingren", {"page": 1, "size": 100})
    body = r.json()
    assert body["is_full"] is False
    assert len(body["items"]) == FREE_LIMIT
    assert body["total"] == FREE_LIMIT


def test_list_annual_full(client):
    """门控口径：annual 属高级会员 → 全量。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("annual"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_annual"), "/api/mingren", {"page": 1, "size": 5})
    body = r.json()
    assert body["is_full"] is True
    assert body["total"] == LIBRARY_TOTAL


def test_list_search(client):
    """姓名搜索：q 子串匹配。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("pro"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_pro"), "/api/mingren", {"q": "朱元璋"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert any(i["name"] == "朱元璋" for i in body["items"])

    # 空结果
    r2 = _get(app, jwt.create_token("u_pro"), "/api/mingren", {"q": "不存在的名人xyz"})
    assert r2.json()["total"] == 0 and r2.json()["items"] == []


def test_list_search_free_gated(client):
    """免费用户搜索也被 35 条门控：命中结果只会落在前 35 位内。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("free"))
    app, jwt = client
    # 严嵩 = 第 35 位（免费可见）；赵文华 = 第 36 位（不可见）
    r1 = _get(app, jwt.create_token("u_free"), "/api/mingren", {"q": "严嵩"})
    assert r1.json()["total"] == 1
    assert r1.json()["is_full"] is False

    r2 = _get(app, jwt.create_token("u_free"), "/api/mingren", {"q": "赵文华"})
    body = r2.json()
    assert body["total"] == 0           # 可见名单内无匹配
    assert body["total_all"] == 1       # 但全库有 1 位 → 前端引导"开通会员查看"
    assert body["items"] == []


def test_list_param_bounds(client):
    """参数边界：size 超限/页码越界 → FastAPI 校验 422。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("pro"))
    app, jwt = client
    assert _get(app, jwt.create_token("u_pro"), "/api/mingren",
                {"size": 101}).status_code == 422
    assert _get(app, jwt.create_token("u_pro"), "/api/mingren",
                {"page": 0}).status_code == 422


# ── 详情：解锁 / 403 / 404 ──────────────────────────────────────

def test_detail_free_first35_200(client):
    """免费用户：前 35 位详情可看（info/info2/flist 全量）。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("free"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_free"), "/api/mingren/朱元璋")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "朱元璋"
    assert "明朝第一位开国皇帝" in d["info"]
    assert "徐乐吾曰" in d["info2"]
    assert d["has_info2"] is True
    assert isinstance(d["flist"], list) and len(d["flist"]) >= 5
    assert any("1352" in f["name"] or "1351" in f["name"] for f in d["flist"])


def test_detail_free_beyond35_403(client):
    """免费用户：第 36 位（赵文华）→ 403 + VIP_REQUIRED（前端弹开通引导）。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("free"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_free"), "/api/mingren/赵文华")
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["code"] == "VIP_REQUIRED"
    assert "高级会员" in body["detail"]["message"]
    assert "2807" in body["detail"]["message"]


def test_detail_basic_beyond35_403(client):
    """门控口径：basic 同免费，第 36 位 → 403。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("basic"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_basic"), "/api/mingren/赵文华")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "VIP_REQUIRED"


def test_detail_pro_beyond35_200(client):
    """高级会员：全量详情可看（35 位之后也放行）。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("pro"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_pro"), "/api/mingren/赵文华")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "赵文华"
    assert isinstance(d["flist"], list)


def test_detail_annual_beyond35_200(client):
    """annual 同 pro：35 位之后放行。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("annual"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_annual"), "/api/mingren/赵文华")
    assert r.status_code == 200


def test_detail_not_found_404(client):
    """未知人名 → 404。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("pro"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_pro"), "/api/mingren/无名氏XYZ")
    assert r.status_code == 404


# ── 鉴权 / 体验模式 / 服务不可用 ────────────────────────────────

def test_unauthorized_401(client):
    """未登录：列表与详情均 401。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("pro"))
    app, _ = client
    assert _get(app, None, "/api/mingren").status_code == 401
    assert _get(app, None, "/api/mingren/朱元璋").status_code == 401


def test_experience_mode_free_full(client, monkeypatch):
    """体验模式：免费用户也全量（列表 total=2807 + 35 之后详情放行）。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("free"))
    monkeypatch.setattr(mingren_api, "is_experience_mode", lambda: True)
    app, jwt = client
    r = _get(app, jwt.create_token("u_exp"), "/api/mingren", {"page": 1, "size": 5})
    assert r.json()["is_full"] is True
    assert r.json()["total"] == LIBRARY_TOTAL
    r2 = _get(app, jwt.create_token("u_exp"), "/api/mingren/赵文华")
    assert r2.status_code == 200
    monkeypatch.undo()


def test_member_dao_unavailable_503(client, monkeypatch):
    """会员服务未注入 → 503（不误放行）。"""
    mingren_api.setup(member_dao=None)
    monkeypatch.setattr(mingren_api, "is_experience_mode", lambda: False)
    app, jwt = client
    r = _get(app, jwt.create_token("u_none"), "/api/mingren")
    assert r.status_code == 503
    r2 = _get(app, jwt.create_token("u_none"), "/api/mingren/朱元璋")
    assert r2.status_code == 503
    monkeypatch.undo()


def test_data_missing_503(client, monkeypatch, tmp_path):
    """数据文件缺失 → 503（不裸 500）。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("pro"), data_path=str(tmp_path / "nope.json"))
    app, jwt = client
    r = _get(app, jwt.create_token("u_pro"), "/api/mingren")
    assert r.status_code == 503
