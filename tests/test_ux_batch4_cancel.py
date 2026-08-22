"""UX批4 · Critical-2 注销账号 422 契约错配回归测试（纯本地，不调外部 API）。

背景：前端 api.js 旧负载 {confirm: '注销'} 在 Pydantic 2.9 下 bool 字段严格
校验 → 422（'注销' 不是合法 bool 字符串）。后端 CancelRequest 本意收
code="注销"（user.py:949 `if not req.confirm and req.code != "注销"`）。

覆盖：
- Pydantic 契约：CancelRequest(confirm='注销') → ValidationError；
  CancelRequest(code='注销') / CancelRequest(confirm=True) 均可正常构造；
- 端点 POST /api/user/cancel：
  * 无 token → 401；
  * 旧负载 {confirm:'注销'} → 422（前端已改为 {code:'注销'}，此用例防回退）；
  * 错误确认码 {code:'取消'} → 400「请二次确认注销」；
  * 新负载 {code:'注销'} → 200 注销成功；
  * 注销后再次登录 → 403「账号已注销」。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.api import user as user_api  # noqa: E402
from src.security.auth import set_auth_handler, AuthHandler, JWTHandler  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402


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
def user_dao(db_path):
    return UserDAO(db_path)


@pytest.fixture
def client(user_dao):
    """挂载 user 路由的独立 app + 同一 AuthHandler（登录发 token 与 require_user 校验同源）。"""
    ah = AuthHandler()
    user_api.setup(user_dao, auth_handler=ah)
    set_auth_handler(ah)
    app = FastAPI()
    app.include_router(user_api.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    set_auth_handler(None)


def _login(client, code="dev_code"):
    return client.post("/api/user/login", json={"code": code})


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ───────────────────────── Pydantic 契约（422 根因） ─────────────────────────

class TestCancelContract:
    def test_old_confirm_chinese_string_rejected(self):
        """旧前端负载 {confirm:'注销'} 必须被 Pydantic 拒绝（422 根因）——防止静默回退。"""
        with pytest.raises(ValidationError):
            user_api.CancelRequest(confirm="注销")

    def test_code_chinese_string_accepted(self):
        """{code:'注销'} 是新契约，必须可构造。"""
        req = user_api.CancelRequest(code="注销")
        assert req.code == "注销"
        assert req.confirm is False

    def test_confirm_true_accepted(self):
        """confirm=true（另一合法二次确认路径）必须可构造。"""
        req = user_api.CancelRequest(confirm=True)
        assert req.confirm is True


# ───────────────────────── 端点全流程 ─────────────────────────

class TestCancelEndpoint:
    def test_cancel_requires_auth(self, client):
        """无 token 调注销 → 401（鉴权不弱化）。"""
        resp = client.post("/api/user/cancel", json={"code": "注销"})
        assert resp.status_code == 401

    def test_old_payload_422(self, client):
        """旧负载 {confirm:'注销'} → 422（回归防回退）。"""
        login = _login(client)
        assert login.status_code == 200
        token = login.json().get("token")
        assert token
        resp = client.post("/api/user/cancel", json={"confirm": "注销"}, headers=_auth(token))
        assert resp.status_code == 422

    def test_wrong_code_400(self, client):
        """确认码不对 → 400「请二次确认注销」。"""
        login = _login(client)
        token = login.json()["token"]
        resp = client.post("/api/user/cancel", json={"code": "取消"}, headers=_auth(token))
        assert resp.status_code == 400
        assert "请二次确认注销" in resp.json().get("detail", "")

    def test_new_payload_cancel_ok(self, client, user_dao):
        """新负载 {code:'注销'} → 200 注销成功，users 状态转 cancelled。"""
        login = _login(client)
        assert login.status_code == 200
        user_id = login.json()["user"]["id"]
        token = login.json()["token"]
        resp = client.post("/api/user/cancel", json={"code": "注销"}, headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("success") is True
        assert user_dao.get_user_status(user_id) == "cancelled"

    def test_login_after_cancel_403(self, client):
        """注销后再次登录 → 403「账号已注销」（登录拦截仍生效）。"""
        login = _login(client)
        token = login.json()["token"]
        resp = client.post("/api/user/cancel", json={"code": "注销"}, headers=_auth(token))
        assert resp.status_code == 200
        again = _login(client)
        assert again.status_code == 403
        assert "账号已注销" in again.json().get("detail", "")

    def test_confirm_true_path_ok(self, client):
        """confirm=true 路径同样 200（后端另一合法确认方式）。"""
        login = _login(client)
        token = login.json()["token"]
        resp = client.post("/api/user/cancel", json={"confirm": True}, headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json().get("success") is True
