"""G3b H-2：love.py 付费墙后端校验（paid=true 必须真实购买记录 or 体验模式）。

审计发现：love.py 原 `paid = req.paid or exp_mode` 完全信任客户端 paid 标志，
无任何购买记录校验 → POST /api/love/compatibility 传 paid=true 即可免费拿
完整付费报告（付费旁路）。本测试验证修复后：
- 未购 + paid=true → 403；
- 已购（payments 表 status=paid, plan=love_compatibility）+ paid=true → 200 放行；
- 体验模式（无购买记录）+ paid=true → 200 放行（体验模式保留）；
- 购买其他商品（deep_report）不解锁 love_compatibility（商品隔离）；
- paid=false → 摘要版正常（付费章节锁定文案保留）；
- 未登录 → 401。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api import love as love_api  # noqa: E402
from src.storage.member_dao import MemberDAO  # noqa: E402
from src.security.auth import set_auth_handler, AuthHandler, JWTHandler  # noqa: E402


@pytest.fixture
def db_path():
    """临时数据库文件（自动清理 .db/-wal/-shm）。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    yield path
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            try:
                os.unlink(p)
            except OSError:
                pass


@pytest.fixture
def member_dao(db_path):
    return MemberDAO(db_path)


@pytest.fixture(autouse=True)
def auth():
    """JWT 鉴权（不跑 lifespan）。"""
    set_auth_handler(AuthHandler())
    yield
    set_auth_handler(None)


@pytest.fixture(autouse=True)
def no_experience_mode(monkeypatch):
    """.env 是体验版（EXPERIENCE_MODE=true）——测试默认关闭体验模式。"""
    monkeypatch.setattr("src.api.love.is_experience_mode", lambda: False)
    yield


def _token(user_id: str) -> dict:
    tok = JWTHandler(os.environ["JWT_SECRET_KEY"]).create_token(user_id)
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def client(member_dao):
    """挂载 love 路由的独立 app（注入真实 MemberDAO）。"""
    love_api.setup(member_dao)
    app = FastAPI()
    app.include_router(love_api.router)
    return TestClient(app)


_BODY = {
    "birthYear1": 1992, "birthMonth1": 8, "birthDay1": 15,
    "birthHour1": 10, "gender1": "男",
    "birthYear2": 1994, "birthMonth2": 3, "birthDay2": 22,
    "birthHour2": 14, "gender2": "女",
    "paid": True,
}


def _buy(member_dao, user_id: str, product_id: str, amount: float):
    pid = member_dao.create_payment(user_id, amount, product_id, "mock")
    member_dao.mark_payment_paid(pid, user_id)


class TestPaywall:
    def test_paid_true_without_purchase_403(self, client):
        """未购：paid=true 直接 403（不再信任客户端标志）。"""
        r = client.post("/api/love/compatibility", json=_BODY,
                        headers=_token("u-h2-1"))
        assert r.status_code == 403
        assert "解锁" in r.json()["detail"]

    def test_paid_true_with_purchase_200_unlocked(self, client, member_dao):
        """已购 love_compatibility：paid=true 放行，返回完整版。"""
        _buy(member_dao, "u-h2-2", "love_compatibility", 9.9)
        r = client.post("/api/love/compatibility", json=_BODY,
                        headers=_token("u-h2-2"))
        assert r.status_code == 200
        body = r.json()
        assert body["paid"] is True and body["unlocked"] is True
        assert "付费内容" not in body["personality"]
        assert body["paywall"]["product"] == "love_compatibility"

    def test_experience_mode_passes_without_purchase(self, client, monkeypatch):
        """体验模式保留：无购买记录也放行完整版（与 union/zhuanxiang 同款）。"""
        monkeypatch.setattr("src.api.love.is_experience_mode", lambda: True)
        r = client.post("/api/love/compatibility", json=_BODY,
                        headers=_token("u-h2-3"))
        assert r.status_code == 200
        assert r.json()["unlocked"] is True

    def test_other_product_purchase_does_not_unlock(self, client, member_dao):
        """商品隔离：买了 deep_report 不解锁 love_compatibility。"""
        _buy(member_dao, "u-h2-4", "deep_report", 19.9)
        r = client.post("/api/love/compatibility", json=_BODY,
                        headers=_token("u-h2-4"))
        assert r.status_code == 403

    def test_paid_false_free_summary_locked(self, client):
        """未付费的 paid=false：摘要版，四章为锁定文案（契约不变）。"""
        body = dict(_BODY)
        body["paid"] = False
        r = client.post("/api/love/compatibility", json=body,
                        headers=_token("u-h2-5"))
        assert r.status_code == 200
        resp = r.json()
        assert resp["unlocked"] is False
        assert resp["score"] > 0          # 摘要版仍有真实分数
        assert "付费内容" in resp["personality"]
        assert "付费内容" in resp["fate"]

    def test_requires_login_401(self, client):
        """未登录 → 401（鉴权边界）。"""
        r = client.post("/api/love/compatibility", json=_BODY)
        assert r.status_code == 401
