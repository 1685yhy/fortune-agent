"""G3b H-9：报告页幸运色/方向/数字由 id 取模假数据改为按用户日主五行真实派生。

审计发现：get_report_detail 原 `luckyColor = colors[rid % len(colors)]`、
`luckyNumber = str((rid % 9) + 1)`（id 取模轮转的确定性假数据），base 报告
固定「金色/东/8」。修复：按用户日主（日柱天干）五行 → 传统五行对应表
（与今日运势同源 WX_COLOR/WX_NUMBER/WX_DIR）派生；无八字时以当日日干五行
参考并置 lucky_is_reference=True（供前端标注「参考信息」）。

覆盖：
- 纯函数：日主甲木 → 绿/东/3 且 is_reference=False；不同日主不同结果；
  同输入确定性（两次同结果）；无八字 → is_reference=True；
- 端点：base 报告派生正确、无八字兜底标记；咨询报告与报告 id 无关
  （同档案不同 id 结果一致，彻底脱离 id 取模）、不同档案不同结果。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src import main  # noqa: E402
from src.main import _derive_lucky_refs  # noqa: E402
from src.security.auth import set_auth_handler, AuthHandler, JWTHandler  # noqa: E402


@pytest.fixture(autouse=True)
def auth():
    set_auth_handler(AuthHandler())
    yield
    set_auth_handler(None)


def _token(user_id: str) -> dict:
    tok = JWTHandler(os.environ["JWT_SECRET_KEY"]).create_token(user_id)
    return {"Authorization": f"Bearer {tok}"}


# 日主甲（木）：绿/东/3
PROFILE_JIA = {
    "year": 1992, "month": 8, "day": 15, "hour": 10, "minute": 0,
    "gender": "男", "calendar": "solar", "city": "北京",
    "bazi": ["壬申", "戊申", "甲午", "己巳"],
}
# 日主丙（火）：红/南/2
PROFILE_BING = {
    "year": 1992, "month": 8, "day": 15, "hour": 10, "minute": 0,
    "gender": "男", "calendar": "solar", "city": "北京",
    "bazi": ["壬申", "戊申", "丙午", "己巳"],
}


class TestDeriveLuckyRefs:
    def test_day_master_wuxing_derives_lucky(self):
        """日主甲（木）→ 绿/东/3，真实派生非参考。"""
        got = _derive_lucky_refs(PROFILE_JIA)
        assert got["luckyColor"] == "绿色"
        assert got["luckyDirection"] == "东"
        assert got["luckyNumber"] == "3"
        assert got["lucky_is_reference"] is False
        assert "甲" in got["lucky_source"]

    def test_different_day_master_different_values(self):
        """不同日主（丙火）→ 红/南/2，与甲木结果不同。"""
        a = _derive_lucky_refs(PROFILE_JIA)
        b = _derive_lucky_refs(PROFILE_BING)
        assert b["luckyColor"] == "红色"
        assert b["luckyDirection"] == "南"
        assert b["luckyNumber"] == "2"
        assert a != b

    def test_deterministic_same_input_same_output(self):
        """确定性：同档案两次调用结果完全一致。"""
        assert _derive_lucky_refs(PROFILE_JIA) == _derive_lucky_refs(PROFILE_JIA)

    def test_no_bazi_falls_back_to_reference(self):
        """无八字：以当日日干五行参考，is_reference=True。"""
        got = _derive_lucky_refs(None)
        assert got["lucky_is_reference"] is True
        assert got["lucky_source"] == "暂无八字，以当日干支五行参考"
        assert got["luckyColor"] in ("绿色", "红色", "黄色", "金色", "蓝色")
        assert got["luckyDirection"] in ("东", "南", "西", "北", "西南")

    def test_empty_bazi_list_same_as_no_bazi(self):
        """bazi 为空列表：等同无八字 → 参考标记。"""
        got = _derive_lucky_refs({"year": 1992, "bazi": []})
        assert got["lucky_is_reference"] is True


class FakeReportDao:
    def __init__(self, bazi):
        self.bazi = bazi
        self.consultations = {}

    def get_user_bazi(self, user_id):
        return self.bazi

    def get_consultation(self, rid):
        return self.consultations.get(rid)


def _make_consultation(rid: int, user_id: str) -> dict:
    return {
        "id": rid,
        "user_id": user_id,
        "question": f"爱情运势咨询 {rid}",
        "intent": "hehun",
        "created_at": "2026-08-29T10:00:00",
        "feedback": None,
        "analysis": "",  # 空 → 走 _derive_report_content，不影响幸运字段
        "chart_result": None,
    }


class TestReportDetailEndpoint:
    @pytest.fixture
    def client(self, monkeypatch):
        """main.app 全路由，仅替换 dao 全局（不跑 lifespan）。"""
        dao = FakeReportDao(dict(PROFILE_JIA))
        dao.consultations = {101: _make_consultation(101, "u-h9-1"),
                             202: _make_consultation(202, "u-h9-1")}
        monkeypatch.setattr(main, "dao", dao)
        return TestClient(main.app), dao

    def test_base_report_derived_from_day_master(self, client):
        """base 报告：按用户日主派生（原固定金色/东/8 已去除）。"""
        c, _ = client
        r = c.get("/api/reports/base", headers=_token("u-h9-1"))
        assert r.status_code == 200
        item = r.json()["report"]
        assert item["luckyColor"] == "绿色"      # 日主甲（木）
        assert item["luckyDirection"] == "东"
        assert item["luckyNumber"] == "3"
        assert item["lucky_is_reference"] is False
        assert item["fullContent"]              # 契约不破坏

    def test_base_report_no_bazi_reference(self, monkeypatch):
        """base 报告 + 无八字：参考标记 + 契约完整。"""
        dao = FakeReportDao(None)
        monkeypatch.setattr(main, "dao", dao)
        c = TestClient(main.app)
        r = c.get("/api/reports/base", headers=_token("u-h9-1"))
        assert r.status_code == 404  # 无八字时 base 报告本身不存在（原有行为）
        # 无八字但已有咨询报告 → 详情走咨询分支 → 参考标记
        dao.consultations = {7: _make_consultation(7, "u-h9-1")}
        r2 = c.get("/api/reports/7", headers=_token("u-h9-1"))
        assert r2.status_code == 200
        item = r2.json()["report"]
        assert item["lucky_is_reference"] is True
        assert item["luckyColor"] in ("绿色", "红色", "黄色", "金色", "蓝色")

    def test_consultation_report_no_longer_id_modulo(self, client):
        """咨询报告：幸运字段按用户档案派生，与报告 id 无关（脱离取模假数据）。"""
        c, dao = client
        r1 = c.get("/api/reports/101", headers=_token("u-h9-1"))
        r2 = c.get("/api/reports/202", headers=_token("u-h9-1"))
        assert r1.status_code == 200 and r2.status_code == 200
        a, b = r1.json()["report"], r2.json()["report"]
        # 同一档案 → 同一幸运字段（旧实现按 id 取模必然不同）
        assert (a["luckyColor"], a["luckyDirection"], a["luckyNumber"]) == \
               (b["luckyColor"], b["luckyDirection"], b["luckyNumber"])
        assert a["luckyColor"] == "绿色"
        assert a["lucky_is_reference"] is False
        # 与旧取模值不同（101 % 5 → 红色，202 % 9 + 1 → 5）
        assert a["luckyColor"] != "红色"
        assert a["luckyNumber"] != "5"

    def test_different_profile_different_lucky(self, monkeypatch):
        """不同档案（日主丙火）→ 红/南/2，与甲木不同。"""
        dao = FakeReportDao(dict(PROFILE_BING))
        dao.consultations = {7: _make_consultation(7, "u-h9-2")}
        monkeypatch.setattr(main, "dao", dao)
        c = TestClient(main.app)
        r = c.get("/api/reports/7", headers=_token("u-h9-2"))
        assert r.status_code == 200
        item = r.json()["report"]
        assert (item["luckyColor"], item["luckyDirection"], item["luckyNumber"]) == \
               ("红色", "南", "2")
