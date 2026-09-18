# -*- coding: utf-8 -*-
"""k54：会员改档端点安全收口——`POST /api/membership/{user_id}/upgrade` 证伪集。

k53 审查（P0，对应《接口安全红线》「全接口鉴权 + 归属校验」）：该端点自述
「模拟支付 → 自动确认」，**任何登录用户**都能把自己改成 basic/pro/annual，
免费拿到付费权益（前端无调用，仅本地 dev 脚本用）。

本批处置 = B（改为**超管专属**，复用 k36 A28 既有 `ADMIN_IDS` 机制，不新起一套）：
    放行 = ① JWT 验签（require_user）
         + ② 路径 user_id == JWT sub（ensure_owner，防给别人改档，行为零变化）
         + ③ 已验证 JWT 的 sub ∈ ADMIN_IDS（`src/security/admin.py` 单一事实源；
              未配置/为空 → 恒不命中 → fail-closed）
    普通用户 → 403 且**零副作用**（不下单、不写 memberships/payments）。

文件结构：
  1. 改前复刻（RED FIXTURE）：把改前实现原样挂在一个最小 app 上，实测
     「普通登录用户 → 200 + 会员开通 + payments 落库」——证明漏洞真实存在；
     配套源码级门禁用例（`TestGatePrecedesWrites`）：门禁一旦被搬走/搬后即红；
  2. 改后契约：普通用户 403 + 零副作用、无 token 401、越权 403（超管也不放宽）、
     白名单放行、空白名单 fail-closed、role claim/请求头/API key 不作判据、
     参数校验顺序不变（403 先于 400/503）；
  3. 不误伤：前端实际链路 `POST /api/pay/subscribe`（mock 模式）行为零变化。

运行：
  TMPDIR=/dev/shm OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 \
      -m pytest tests/test_k54_membership_guard.py -q
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Query  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.security import audit as audit_mod  # noqa: E402
from src.security import auth as auth_mod  # noqa: E402
from src.security.auth import (  # noqa: E402
    AuthHandler, JWTHandler, ensure_owner, require_user, set_auth_handler,
)
from src.storage.member_dao import PLANS, MemberDAO  # noqa: E402

JWT_SECRET = "test-secret-key-32-bytes-long!!"
ADMIN_USER = "k54_admin_u"
PLAIN_USER = "k54_plain_u"
OTHER_USER = "k54_other_u"


# ───────────────────────── fixtures ─────────────────────────

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个用例从「零密钥」出发，由用例自己注入（防 .env 串味）。"""
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    yield


@pytest.fixture(autouse=True)
def isolate_audit(monkeypatch, tmp_path):
    """审计重定向到 tmp：仓库 logs/audit.log 是跟踪文件，测试不得污染。"""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setattr(auth_mod, "_ADMIN_AUDIT_LOGGER", None, raising=False)
    yield


@pytest.fixture(autouse=True)
def auth():
    set_auth_handler(AuthHandler())
    yield
    set_auth_handler(None)


@pytest.fixture
def db_path():
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


@pytest.fixture
def client(monkeypatch, member_dao):
    """真实 app（src.main）——DAO 注入，不跑 lifespan。"""
    import src.main as m
    monkeypatch.setattr(m, "member_dao", member_dao)
    return TestClient(m.app)


def _token(sub, role="user", expiry_days=7, secret=JWT_SECRET):
    return JWTHandler(secret).create_token(sub, role=role, expiry_days=expiry_days)


def _rows(db_path, table, user_id):
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


def _upgrade(client, token, user_id, plan="pro", **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(f"/api/membership/{user_id}/upgrade?plan={plan}",
                       headers=headers, **kw)


# ─────────── 1. 改前复刻（RED FIXTURE）：漏洞真实存在 ───────────

def _prefix_replica_app(member_dao):
    """改前实现**逐字复刻**（k53 时点 src/main.py 端点体；仅用于证明漏洞）。

    判据：把改后代码原样搬回此处 → `TestRedFixturePreFix` 立刻变红。
    """
    app = FastAPI()

    @app.post("/api/membership/{user_id}/upgrade")
    async def upgrade_membership(user_id: str,
                                plan: str = Query(..., description="free/basic/pro/annual"),
                                uid: str = Depends(require_user)):
        """升级会员计划 (模拟支付) —— 改前：只需登录 + owner 校验。"""
        ensure_owner(user_id, uid)
        if member_dao is None:
            raise HTTPException(status_code=503, detail="Service not ready")

        from src.storage.member_dao import PLANS as _PLANS
        if plan not in _PLANS:
            raise HTTPException(status_code=400, detail=f"无效计划: {plan}")

        plan_info = _PLANS[plan]
        if plan == "free":
            member_dao.create_membership(user_id, "free")
            return {"status": "ok", "message": "已切换回免费版", "plan": plan}

        payment_id = member_dao.create_payment(
            user_id=user_id, amount=plan_info["price"], plan=plan,
            payment_method="模拟支付",
        )
        member_dao.confirm_payment(payment_id, user_id, plan)
        return {"status": "ok", "message": f"已升级至 {plan_info['label']}！",
                "payment_id": payment_id, "amount": plan_info["price"],
                "membership": member_dao.get_membership(user_id)}

    return app


class TestRedFixturePreFix:
    """改前行为实测（RED）：普通登录用户自助开通付费档 = 200 + 落库。"""

    def test_plain_user_self_upgrade_succeeded_before(self, member_dao):
        client = TestClient(_prefix_replica_app(member_dao))
        r = _upgrade(client, _token(PLAIN_USER), PLAIN_USER, "pro")
        assert r.status_code == 200, r.text                      # 改前：放行
        assert r.json()["membership"]["plan"] == "pro"           # 改前：付费档到手
        # 零元获得付费档：payments 里出现一条「模拟支付」已支付订单
        assert _rows(member_dao.db_path, "payments", PLAIN_USER) == 1
        conn = member_dao._connect()
        row = conn.execute(
            "SELECT amount, plan, status, payment_method FROM payments WHERE user_id = ?",
            (PLAIN_USER,)).fetchone()
        conn.close()
        assert row[2] == "paid" and row[3] == "模拟支付"
        assert row[0] == PLANS["pro"]["price"]                   # 金额照记（假收入）

    def test_plain_user_self_downgrade_wrote_row_before(self, member_dao):
        """改前的另一面：plan=free 也直接写 memberships（无鉴权边界）。"""
        client = TestClient(_prefix_replica_app(member_dao))
        assert _rows(member_dao.db_path, "memberships", PLAIN_USER) == 0
        r = _upgrade(client, _token(PLAIN_USER), PLAIN_USER, "free")
        assert r.status_code == 200, r.text
        assert _rows(member_dao.db_path, "memberships", PLAIN_USER) == 1


class TestGatePrecedesWrites:
    """源码级门禁：超管门必须在任何写库动作之前（回退/搬位即红）。"""

    @staticmethod
    def _endpoint_src():
        import src.main as m
        src = open(m.__file__, encoding="utf-8").read()
        start = src.index('@app.post("/api/membership/{user_id}/upgrade")')
        end = src.index('@app.get("/api/admin/stats")')
        return src[start:end]

    def test_admin_gate_present(self):
        body = self._endpoint_src()
        assert "admin_identity_from_authorization" in body
        assert "ADMIN_IDS" in body  # 判据来源单一事实源已写明

    def test_gate_before_any_write(self):
        body = self._endpoint_src()
        gate = body.index("admin_identity_from_authorization")
        for write in ("create_payment(", "confirm_payment(", "create_membership("):
            assert gate < body.index(write), f"{write} 出现在超管门之前"

    def test_gate_before_plan_validation(self):
        """门禁先于计划名校验：非超管拿不到 400（参数面也不外泄）。"""
        body = self._endpoint_src()
        assert body.index("admin_identity_from_authorization") < body.index("无效计划")

    def test_prefix_replica_has_no_gate(self):
        """复刻体里没有门 → 上面两条只对改后代码成立（夹具确为改前）。"""
        import inspect
        assert "admin_identity_from_authorization" not in inspect.getsource(_prefix_replica_app)


# ─────────── 2. 改后契约：普通用户被拒且零副作用 ───────────

class TestPlainUserRejected:
    @pytest.mark.parametrize("plan", ["basic", "pro", "annual", "free"])
    def test_self_upgrade_403(self, client, plan):
        r = _upgrade(client, _token(PLAIN_USER), PLAIN_USER, plan)
        assert r.status_code == 403, r.text

    def test_zero_side_effects(self, client, member_dao):
        before_pay = _rows(member_dao.db_path, "payments", PLAIN_USER)
        before_mem = _rows(member_dao.db_path, "memberships", PLAIN_USER)
        r = _upgrade(client, _token(PLAIN_USER), PLAIN_USER, "annual")
        assert r.status_code == 403
        assert _rows(member_dao.db_path, "payments", PLAIN_USER) == before_pay == 0
        assert _rows(member_dao.db_path, "memberships", PLAIN_USER) == before_mem == 0
        # 只读视角仍是免费档（没有副作用式开通）
        assert member_dao.get_membership(PLAIN_USER)["plan"] == "free"

    def test_no_token_401_unchanged(self, client):
        r = _upgrade(client, None, PLAIN_USER, "pro")
        assert r.status_code == 401

    def test_invalid_plan_still_403_not_400(self, client):
        """非超管拿不到计划名校验信息（403 先于 400）。"""
        r = _upgrade(client, _token(PLAIN_USER), PLAIN_USER, "ultra_plan")
        assert r.status_code == 403, r.text


class TestOwnershipUnchanged:
    def test_cross_user_still_403(self, client, member_dao):
        r = _upgrade(client, _token(PLAIN_USER), OTHER_USER, "pro")
        assert r.status_code == 403
        assert _rows(member_dao.db_path, "payments", OTHER_USER) == 0
        assert _rows(member_dao.db_path, "memberships", OTHER_USER) == 0

    def test_admin_cannot_touch_others(self, client, member_dao, monkeypatch):
        """归属校验不放宽：**即使是超管**也只能改自己（本批不新增给他人改档面）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = _upgrade(client, _token(ADMIN_USER), OTHER_USER, "pro")
        assert r.status_code == 403
        assert _rows(member_dao.db_path, "payments", OTHER_USER) == 0
        assert _rows(member_dao.db_path, "memberships", OTHER_USER) == 0


class TestAdminAllowed:
    def test_whitelisted_admin_self_upgrade_ok(self, client, member_dao, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = _upgrade(client, _token(ADMIN_USER), ADMIN_USER, "pro")
        assert r.status_code == 200, r.text
        assert r.json()["membership"]["plan"] == "pro"
        assert _rows(member_dao.db_path, "payments", ADMIN_USER) == 1

    def test_whitelisted_admin_downgrade_self_ok(self, client, member_dao, monkeypatch):
        """超管 plan=free 请求放行（200）；无高档在位 → 落到 free。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = _upgrade(client, _token(ADMIN_USER), ADMIN_USER, "free")
        assert r.status_code == 200
        assert member_dao.get_membership(ADMIN_USER)["plan"] == "free"
        assert _rows(member_dao.db_path, "memberships", ADMIN_USER) == 1

    def test_downgrade_keeps_higher_plan_quirk_unchanged(self, client, member_dao, monkeypatch):
        """既有 DAO 口径（L5-2 跨档「取高者」）零变化，本批不动业务逻辑。

        实测：basic 未过期时再请求 plan=free，DAO 保留 basic（端点文案仍说
        「已切换回免费版」）——这是 k54 之前就存在的行为不一致，只记录、不修改。
        """
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        assert _upgrade(client, _token(ADMIN_USER), ADMIN_USER, "basic").status_code == 200
        assert _upgrade(client, _token(ADMIN_USER), ADMIN_USER, "free").status_code == 200
        assert member_dao.get_membership(ADMIN_USER)["plan"] == "basic"

    def test_multi_id_whitelist_hit(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", f"alice,{ADMIN_USER},bob")
        assert _upgrade(client, _token(ADMIN_USER), ADMIN_USER, "pro").status_code == 200

    def test_audit_written_on_admin_hit(self, client, monkeypatch):
        """命中即写既有审计通道（admin_endpoint_access + path）。"""
        calls = []

        class _Spy:
            def __init__(self, *a, **kw):
                pass

            def admin_action(self, admin_id, action, ip, details=None):
                calls.append((admin_id, action, details or {}))

        monkeypatch.setattr(auth_mod, "_ADMIN_AUDIT_LOGGER", None, raising=False)
        monkeypatch.setattr(audit_mod, "AuditLogger", _Spy)
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        assert _upgrade(client, _token(ADMIN_USER), ADMIN_USER, "pro").status_code == 200
        assert calls and calls[0][0] == ADMIN_USER
        assert calls[0][2].get("path") == f"/api/membership/{ADMIN_USER}/upgrade"


class TestFailClosed:
    def test_unset_whitelist_rejects_even_former_admin(self, client, member_dao):
        r = _upgrade(client, _token(ADMIN_USER), ADMIN_USER, "pro")
        assert r.status_code == 403
        assert _rows(member_dao.db_path, "memberships", ADMIN_USER) == 0

    @pytest.mark.parametrize("raw", ["", " ", ",", " , , "])
    def test_blank_whitelist_rejects(self, client, monkeypatch, raw):
        monkeypatch.setenv("ADMIN_IDS", raw)
        assert _upgrade(client, _token(ADMIN_USER), ADMIN_USER, "pro").status_code == 403

    def test_role_claim_not_judged(self, client, monkeypatch):
        """JWT 里 role=admin 但 sub 不在白名单 → 403（role claim 不是判据）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        tok = _token(PLAIN_USER, role="admin")
        assert _upgrade(client, tok, PLAIN_USER, "pro").status_code == 403

    def test_admin_key_alone_not_enough(self, client, monkeypatch):
        """ADMIN_KEY 不是本门判据（本门只认 JWT sub ∈ ADMIN_IDS）。"""
        monkeypatch.setenv("ADMIN_KEY", "k54-secret")
        assert _upgrade(client, _token(PLAIN_USER), PLAIN_USER, "pro").status_code == 403

    def test_self_claim_headers_ignored(self, client, monkeypatch):
        """请求头/查询参数自称 admin 一律不作判据。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = client.post(
            f"/api/membership/{PLAIN_USER}/upgrade?plan=pro&admin_id={ADMIN_USER}&user_id={ADMIN_USER}",
            headers={"Authorization": f"Bearer {_token(PLAIN_USER)}",
                     "X-Admin-Id": ADMIN_USER, "X-User-Id": ADMIN_USER})
        assert r.status_code == 403

    def test_forged_signature_rejected(self, client, monkeypatch):
        """sub 命中白名单但签名不是本服务密钥 → 403（401 亦可，拒绝即达标）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        forged = _token(ADMIN_USER, secret="attacker-secret-0000000000000000")
        assert _upgrade(client, forged, ADMIN_USER, "pro").status_code in (401, 403)


# ─────────── 3. 不误伤：真实链路与只读端点零变化 ───────────

class TestNoCollateral:
    def test_read_only_membership_endpoint_unchanged(self, client):
        """GET /api/membership/{uid} 仍是「登录 + owner」，普通用户可读自己。"""
        assert client.get(f"/api/membership/{PLAIN_USER}",
                          headers={"Authorization": f"Bearer {_token(PLAIN_USER)}"}
                          ).status_code == 200
        assert client.get(f"/api/membership/{OTHER_USER}",
                          headers={"Authorization": f"Bearer {_token(PLAIN_USER)}"}
                          ).status_code == 403
        assert client.get(f"/api/membership/{PLAIN_USER}").status_code == 401

    def test_pay_subscribe_dev_path_still_works(self, monkeypatch, member_dao):
        """前端实际链路（mock 模式）零变化：普通用户仍可走 /api/pay/subscribe。"""
        import src.api.pay as pay_api
        monkeypatch.setenv("WECHAT_PAY_ENABLED", "false")
        monkeypatch.setattr(pay_api, "_member_dao", member_dao)
        app = FastAPI()
        app.include_router(pay_api.router)
        r = TestClient(app).post("/api/pay/subscribe", json={"plan_id": "pro_monthly"},
                                 headers={"Authorization": f"Bearer {_token(PLAIN_USER)}"})
        assert r.status_code == 200, r.text
        assert r.json()["membership"]["plan"] == "pro"
