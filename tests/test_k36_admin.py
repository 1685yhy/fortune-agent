"""k36 A28：超管机制（ADMIN_IDS 白名单，最小可用面）——安全证伪测试集。

本文件即「证伪集」：每条安全硬要求都有能把它证伪的用例——
- fail-closed：白名单未配置/为空/全是空白 → 零超管（任意路径一律拒）；
- 判据唯一：只有「已验证 JWT 的 sub ∈ ADMIN_IDS」放行；伪造签名 / 篡改 payload /
  过期 / role claim 自称 admin / 请求头·查询参数自称 admin / API key 一律拒；
- ADMIN_KEY 既有路径行为零变化（正确 key 放行、错误 key 拒、未配置拒）；
- 额度豁免在「每日 chat 额度」与「引擎/工具额度」两处口径一致；
- 运维端点放行写进既有审计通道（含 path + 命中管理员标识）。

运行：
  OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_k36_admin.py -q
"""
import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.security import audit as audit_mod  # noqa: E402
from src.security import auth as auth_mod  # noqa: E402
from src.security.admin import admin_ids, admin_whitelist_summary, is_admin_user  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, require_admin, set_auth_handler  # noqa: E402
from src.services import chat_quota as cq  # noqa: E402
from src.storage.chat_quota_dao import ChatQuotaDAO  # noqa: E402
from src.storage.member_dao import MemberDAO  # noqa: E402

JWT_SECRET = "test-secret-key-32-bytes-long!!"
ADMIN_USER = "su_admin_u"
OTHER_USER = "plain_u"


# ───────────────────────── fixtures ─────────────────────────

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个用例从「未配置任何密钥」出发，由用例自己显式注入（防 .env 泄漏串味）。"""
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    monkeypatch.delenv("FORTUNE_API_KEY", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)
    yield


@pytest.fixture(autouse=True)
def no_experience_mode(monkeypatch):
    """本文件所有用例都在「体验模式关闭」的前提下测（要开体验模式的用例自行显式开启）。

    k54 r3 修夹具隔离（**不改任何断言**）：本文件此前只 patch 了 `chat_quota` 里的
    符号，**漏了 `src.bot.handler` 里已导入的同名符号**——而 `is_experience_mode()`
    是按 `os.getenv("EXPERIENCE_MODE")` **调用时**求值的，于是主检出
    （`.env` 软链 → 生产 .env，`EXPERIENCE_MODE=true`）里跑 pytest 时体验模式会
    泄漏进整个进程：`TestEngineQuotaExemption` 测的就不是它想测的东西。

    修法（两层，语义等价于「让体验模式真的关掉」，与仓库既有惯例一致
    ——见 test_k11b / test_k15 / test_k37 / test_member_quota_limit 的同类 fixture）：
      ① 删掉泄漏源：把 `EXPERIENCE_MODE` 移出进程环境（根因修复，对所有消费模块生效）；
      ② 把 handler 里已导入的符号一并 patch（不依赖「开关只由 env 驱动」这一实现细节）。
    只影响本文件的用例作用域，用例内可再显式开回来（`monkeypatch` 后设者胜）。
    """
    from src.bot import handler as handler_mod  # 延迟导入：不在收集期拉起重依赖
    monkeypatch.delenv("EXPERIENCE_MODE", raising=False)
    monkeypatch.setattr(cq, "is_experience_mode", lambda: False)
    monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
    yield


@pytest.fixture(autouse=True)
def isolate_audit(monkeypatch, tmp_path):
    """审计写入重定向到 tmp：仓库内 logs/audit.log 是 git 跟踪文件，不得被测试污染。

    同时清空 auth 模块的审计器单例缓存，保证每个用例按当前环境变量重建。
    """
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setattr(auth_mod, "_ADMIN_AUDIT_LOGGER", None, raising=False)
    yield


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
def daos(db_path):
    return MemberDAO(db_path), ChatQuotaDAO(db_path)


@pytest.fixture
def ops_client():
    """最小 FastAPI app：一条 require_admin 保护的运维端点（不新增生产端点）。"""
    set_auth_handler(AuthHandler())
    app = FastAPI()

    @app.get("/ops/ping")
    async def ping(ok: bool = Depends(require_admin)):
        return {"ok": True}

    return TestClient(app)


@pytest.fixture
def audit_spy(monkeypatch):
    """替换既有审计通道类，捕获审计写入（不落盘、不新建日志文件）。"""
    calls = []

    class _Spy:
        def __init__(self, *a, **kw):
            pass

        def admin_action(self, admin_id, action, ip, details=None):
            calls.append({"admin_id": admin_id, "action": action,
                          "ip": ip, "details": details or {}})

        def log(self, **kw):
            calls.append({"raw": kw})

    monkeypatch.setattr(auth_mod, "_ADMIN_AUDIT_LOGGER", None, raising=False)
    monkeypatch.setattr(audit_mod, "AuditLogger", _Spy)
    return calls


def _token(sub, role="user", expiry_days=7, secret=JWT_SECRET):
    return JWTHandler(secret).create_token(sub, role=role, expiry_days=expiry_days)


def _tampered_token(sub):
    """取一枚合法 token，只改 payload 里的 sub（签名随之失效）。"""
    tok = _token("victim_u")
    h, p, _sig = tok.split(".")
    payload = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    payload["sub"] = sub
    new_p = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return f"{h}.{new_p}.{_sig}"


# ───────────────────── 1. 白名单来源与判定（fail-closed） ─────────────────────

class TestWhitelistSource:
    def test_unset_env_no_admin(self):
        assert admin_ids() == frozenset()
        assert is_admin_user(ADMIN_USER) is False

    @pytest.mark.parametrize("raw", ["", " ", ",", " , , ", ",,,"])
    def test_empty_or_blank_env_no_admin(self, monkeypatch, raw):
        monkeypatch.setenv("ADMIN_IDS", raw)
        assert admin_ids() == frozenset()
        assert is_admin_user(ADMIN_USER) is False

    def test_parsed_stripped(self, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", " alice , bob ,")
        assert admin_ids() == frozenset({"alice", "bob"})

    def test_no_user_id_inputs(self):
        assert is_admin_user("") is False
        assert is_admin_user(None) is False

    def test_exact_match_case_sensitive(self, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", "alice")
        assert is_admin_user("alice") is True
        assert is_admin_user("Alice") is False
        assert is_admin_user("alice ") is False   # 空白不折叠
        assert is_admin_user("alice,bob") is False  # 不整串匹配
        assert is_admin_user("bob") is False

    def test_summary_counts_without_ids(self, monkeypatch):
        assert admin_whitelist_summary() == "未配置"
        monkeypatch.setenv("ADMIN_IDS", "alice,bob")
        s = admin_whitelist_summary()
        assert "2" in s and "未配置" not in s
        assert "alice" not in s and "bob" not in s  # 不打印具体 user_id

    def test_startup_log_wired_without_ids(self):
        """启动日志接线：main.py 必须打「超管白名单：已配置 N 个 / 未配置」且不带 id。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "src", "main.py"), encoding="utf-8").read()
        m = [ln for ln in src.splitlines() if "超管白名单" in ln]
        assert m, "main.py 启动日志应含「超管白名单」行"
        assert "admin_whitelist_summary" in m[0]


# ───────────────────── 2. 运维端点：ADMIN_KEY 路径回归 ─────────────────────

class TestAdminKeyPathRegression:
    def test_valid_admin_key_allowed(self, ops_client, monkeypatch):
        monkeypatch.setenv("ADMIN_KEY", "k-secret")
        r = ops_client.get("/ops/ping", headers={"Authorization": "Bearer k-secret"})
        assert r.status_code == 200 and r.json() == {"ok": True}

    def test_wrong_admin_key_rejected(self, ops_client, monkeypatch):
        monkeypatch.setenv("ADMIN_KEY", "k-secret")
        r = ops_client.get("/ops/ping", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403

    def test_no_admin_key_configured_rejected(self, ops_client):
        r = ops_client.get("/ops/ping")
        assert r.status_code == 403

    def test_no_admin_key_and_no_whitelist_rejected_with_token(self, ops_client):
        r = ops_client.get("/ops/ping",
                           headers={"Authorization": f"Bearer {_token(ADMIN_USER)}"})
        assert r.status_code == 403

    def test_admin_key_path_unchanged_when_whitelist_configured(
            self, ops_client, monkeypatch):
        """白名单存在时，ADMIN_KEY 正确/错误/缺失三态行为与既有完全一致。"""
        monkeypatch.setenv("ADMIN_IDS", "alice,bob")
        monkeypatch.setenv("ADMIN_KEY", "k-secret")
        assert ops_client.get("/ops/ping", headers={
            "Authorization": "Bearer k-secret"}).status_code == 200
        assert ops_client.get("/ops/ping", headers={
            "Authorization": "Bearer wrong"}).status_code == 403
        monkeypatch.delenv("ADMIN_KEY", raising=False)
        assert ops_client.get("/ops/ping").status_code == 403


# ───────────────────── 3. 运维端点：JWT 白名单放行 ─────────────────────

class TestJwtWhitelistAllowed:
    def test_whitelisted_jwt_allowed_without_admin_key(self, ops_client, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = ops_client.get("/ops/ping",
                           headers={"Authorization": f"Bearer {_token(ADMIN_USER)}"})
        assert r.status_code == 200, r.text

    def test_whitelisted_jwt_allowed_with_admin_key_too(self, ops_client, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        monkeypatch.setenv("ADMIN_KEY", "k-secret")
        r = ops_client.get("/ops/ping",
                           headers={"Authorization": f"Bearer {_token(ADMIN_USER)}"})
        assert r.status_code == 200, r.text

    def test_multiple_ids_whitelist_hit(self, ops_client, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", f"alice,{ADMIN_USER},bob")
        r = ops_client.get("/ops/ping",
                           headers={"Authorization": f"Bearer {_token(ADMIN_USER)}"})
        assert r.status_code == 200, r.text

    def test_real_security_status_endpoint(self, monkeypatch):
        """真实端点接线：/api/security/status —— 非白名单 403 / 白名单 200。"""
        import src.main as m
        set_auth_handler(AuthHandler())
        client = TestClient(m.app)
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        assert client.get("/api/security/status", headers={
            "Authorization": f"Bearer {_token(OTHER_USER)}"}).status_code == 403
        assert client.get("/api/security/status", headers={
            "Authorization": f"Bearer {_token(ADMIN_USER)}"}).status_code == 200


# ───────────────────── 4. 证伪：伪造 / 篡改 / 过期 / 自称 ─────────────────────

class TestForgeryRejected:
    def test_forged_signature_rejected(self, ops_client, monkeypatch):
        """sub 命中白名单但签名不是本服务密钥 → 拒（白名单不构成绕过签名的手段）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        forged = _token(ADMIN_USER, secret="attacker-secret-0000000000000000")
        r = ops_client.get("/ops/ping", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 403

    def test_tampered_payload_rejected(self, ops_client, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = ops_client.get("/ops/ping",
                           headers={"Authorization": f"Bearer {_tampered_token(ADMIN_USER)}"})
        assert r.status_code == 403

    def test_expired_jwt_rejected(self, ops_client, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        expired = _token(ADMIN_USER, expiry_days=-1)
        r = ops_client.get("/ops/ping", headers={"Authorization": f"Bearer {expired}"})
        assert r.status_code == 403

    def test_role_claim_not_trusted(self, ops_client, monkeypatch):
        """JWT 里 role=admin 但 sub 不在白名单 → 拒（role claim 不是判据）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        tok = _token(OTHER_USER, role="admin")
        r = ops_client.get("/ops/ping", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403

    def test_self_claim_headers_and_params_ignored(self, ops_client, monkeypatch):
        """请求头/查询参数里自称 admin（含 API key 头）一律不作判据。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        monkeypatch.setenv("FORTUNE_API_KEY", "some-api-key")
        r = ops_client.get(
            f"/ops/ping?user_id={ADMIN_USER}&admin_id={ADMIN_USER}",
            headers={"X-Admin-Id": ADMIN_USER, "X-User-Id": ADMIN_USER,
                     "X-API-Key": "some-api-key"})
        assert r.status_code == 403

    def test_api_key_never_jwt_admin(self, ops_client, monkeypatch):
        """API key 即使字面等于白名单 id 也不放行（白名单只认 JWT sub）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        monkeypatch.setenv("FORTUNE_API_KEY", ADMIN_USER)
        r = ops_client.get("/ops/ping",
                           headers={"Authorization": f"Bearer {ADMIN_USER}"})
        assert r.status_code == 403

    def test_empty_whitelist_jwt_rejected(self, ops_client, monkeypatch):
        for raw in ("", " ", ","):
            monkeypatch.setenv("ADMIN_IDS", raw)
            r = ops_client.get("/ops/ping",
                               headers={"Authorization": f"Bearer {_token(ADMIN_USER)}"})
            assert r.status_code == 403, raw

    def test_non_whitelisted_jwt_rejected(self, ops_client, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = ops_client.get("/ops/ping",
                           headers={"Authorization": f"Bearer {_token(OTHER_USER)}"})
        assert r.status_code == 403


# ───────────────────── 5. 审计通道（超管动作可追溯） ─────────────────────

class TestAuditTrail:
    def test_jwt_admin_access_audited_with_path(self, ops_client, monkeypatch, audit_spy):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = ops_client.get("/ops/ping",
                           headers={"Authorization": f"Bearer {_token(ADMIN_USER)}"})
        assert r.status_code == 200
        assert len(audit_spy) == 1, audit_spy
        rec = audit_spy[0]
        assert rec["admin_id"] == ADMIN_USER          # 命中管理员标识
        assert rec["details"]["path"] == "/ops/ping"  # path
        assert "jwt" in rec["details"]["auth"]

    def test_rejected_request_not_audited_as_admin(self, ops_client, monkeypatch, audit_spy):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        ops_client.get("/ops/ping", headers={"Authorization": f"Bearer {_token(OTHER_USER)}"})
        assert audit_spy == []

    def test_admin_key_path_not_newly_audited(self, ops_client, monkeypatch, audit_spy):
        """ADMIN_KEY 路径零变化：不新增审计写入（保持既有行为）。"""
        monkeypatch.setenv("ADMIN_KEY", "k-secret")
        assert ops_client.get("/ops/ping", headers={
            "Authorization": "Bearer k-secret"}).status_code == 200
        assert audit_spy == []


# ───────────────────── 6. 额度豁免（chat 日额度） ─────────────────────

class TestChatQuotaExemption:
    def test_non_admin_free_user_still_limited(self, daos):
        member_dao, quota_dao = daos
        for _ in range(cq.CHAT_DAILY_LIMIT):
            ctx = cq.try_consume_chat_quota(member_dao, quota_dao, OTHER_USER)
            assert ctx["downgraded"] is False
        ctx = cq.try_consume_chat_quota(member_dao, quota_dao, OTHER_USER)
        assert ctx["downgraded"] is True  # 第 16 条起降级（既有行为）

    def test_whitelisted_user_with_different_whitelist_still_limited(self, daos, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", "someone_else")
        member_dao, quota_dao = daos
        for _ in range(cq.CHAT_DAILY_LIMIT):
            cq.try_consume_chat_quota(member_dao, quota_dao, OTHER_USER)
        assert cq.try_consume_chat_quota(
            member_dao, quota_dao, OTHER_USER)["downgraded"] is True

    def test_admin_exempt_and_not_counted(self, daos, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        member_dao, quota_dao = daos
        for _ in range(cq.CHAT_DAILY_LIMIT + 5):
            ctx = cq.try_consume_chat_quota(member_dao, quota_dao, ADMIN_USER)
            assert ctx["downgraded"] is False, ctx
            assert ctx["limit"] is None
        assert quota_dao.get_count(ADMIN_USER, cq._bj_day()) == 0  # 不计入

    def test_admin_status_contract(self, daos, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        member_dao, quota_dao = daos
        st = cq.chat_quota_status(member_dao, quota_dao, ADMIN_USER)
        assert st == {"used": 0, "limit": None, "downgraded": False, "is_member": False}

    def test_empty_whitelist_is_limited(self, daos):
        member_dao, quota_dao = daos
        for _ in range(cq.CHAT_DAILY_LIMIT):
            cq.try_consume_chat_quota(member_dao, quota_dao, ADMIN_USER)
        assert cq.try_consume_chat_quota(
            member_dao, quota_dao, ADMIN_USER)["downgraded"] is True

    def test_experience_mode_unchanged(self, daos, monkeypatch):
        """EXPERIENCE_MODE 既有行为不变（白名单外的既有豁免通道）。"""
        monkeypatch.setattr(cq, "is_experience_mode", lambda: True)
        member_dao, quota_dao = daos
        for _ in range(30):
            assert cq.try_consume_chat_quota(
                member_dao, quota_dao, OTHER_USER)["downgraded"] is False
        assert quota_dao.get_count(OTHER_USER, cq._bj_day()) == 0


# ───────────────────── 7. 额度豁免（引擎/工具额度，口径一致） ─────────────────────

class TestEngineQuotaExemption:
    """handler._check_quota 是「非聊天功能」额度门（memberships.queries_limit）。"""

    def _stub(self, member_dao):
        class _Stub:
            pass
        s = _Stub()
        s.member_dao = member_dao
        return s

    def test_non_admin_limited(self, db_path):
        from src.bot.handler import MessageHandler
        member_dao = MemberDAO(db_path)
        member_dao.create_membership(OTHER_USER, "free")
        remaining, is_limited = MessageHandler._check_quota(self._stub(member_dao), OTHER_USER)
        assert is_limited is True and remaining >= 0

    def test_admin_exempt(self, db_path, monkeypatch):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        from src.bot.handler import MessageHandler
        member_dao = MemberDAO(db_path)
        member_dao.create_membership(ADMIN_USER, "free")
        assert MessageHandler._check_quota(self._stub(member_dao), ADMIN_USER) == (-1, False)

    def test_empty_whitelist_limited(self, db_path):
        from src.bot.handler import MessageHandler
        member_dao = MemberDAO(db_path)
        member_dao.create_membership(ADMIN_USER, "free")
        assert MessageHandler._check_quota(self._stub(member_dao), ADMIN_USER)[1] is True

    def test_admin_consume_path_unchanged(self, db_path, monkeypatch):
        """豁免只加在检查口：_consume_quota 不做白名单分支（仍照常计数）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        from src.bot.handler import MessageHandler
        member_dao = MemberDAO(db_path)
        member_dao.create_membership(ADMIN_USER, "free")
        before = member_dao.get_membership(ADMIN_USER)["queries_used"]
        MessageHandler._consume_quota(self._stub(member_dao), ADMIN_USER)
        after = member_dao.get_membership(ADMIN_USER)["queries_used"]
        assert after == before + 1


# ───────────────── 8. 第二道 ADMIN_KEY 门（main._verify_admin）口径一致 ─────────────────

class TestVerifyAdminGateConsistency:
    """审计 A28 列的推送/统计端点走 main.py::_verify_admin（另一道 ADMIN_KEY 门）。

    超管 JWT 必须在**两道门**上同样放行，否则口径分裂。
    """

    @staticmethod
    def _client():
        import src.main as m
        set_auth_handler(AuthHandler())
        return TestClient(m.app)

    @pytest.mark.parametrize("path", ["/api/admin/stats", "/api/admin/active-members"])
    def test_whitelisted_jwt_allowed(self, monkeypatch, path):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = self._client().get(path, headers={"Authorization": f"Bearer {_token(ADMIN_USER)}"})
        assert r.status_code != 403, r.text  # 鉴权已过（测试进程无 DAO → 503）

    @pytest.mark.parametrize("path", ["/api/admin/stats", "/api/admin/active-members"])
    def test_non_whitelisted_jwt_rejected(self, monkeypatch, path):
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        r = self._client().get(path, headers={"Authorization": f"Bearer {_token(OTHER_USER)}"})
        assert r.status_code == 403

    @pytest.mark.parametrize("path", ["/api/admin/stats", "/api/admin/active-members"])
    def test_empty_whitelist_jwt_rejected(self, monkeypatch, path):
        r = self._client().get(path, headers={"Authorization": f"Bearer {_token(ADMIN_USER)}"})
        assert r.status_code == 403

    def test_admin_key_path_regression(self, monkeypatch):
        """ADMIN_KEY 路径三态零变化（本门读 settings.admin_key，lifespan 外需注入）。"""
        import types
        import src.main as m
        monkeypatch.setattr(m, "settings",
                            types.SimpleNamespace(admin_key="k-secret"))
        c = self._client()
        assert c.get("/api/admin/stats", headers={
            "Authorization": "Bearer k-secret"}).status_code != 403
        assert c.get("/api/admin/stats", headers={
            "Authorization": "Bearer wrong"}).status_code == 403
        monkeypatch.setattr(m, "settings", None)  # 未配置 → 拒
        assert c.get("/api/admin/stats").status_code == 403

    def test_push_daily_gate(self, monkeypatch):
        """推送端点：非白名单 JWT 403；白名单 JWT 过鉴权（无 DAO → 503，不真触发推送）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        c = self._client()
        assert c.post("/api/push-daily", headers={
            "Authorization": f"Bearer {_token(OTHER_USER)}"}).status_code == 403
        assert c.post("/api/push-daily", headers={
            "Authorization": f"Bearer {_token(ADMIN_USER)}"}).status_code != 403


# ───────────────── 9. 审计器单例（防 handler 累积 / 重复写） ─────────────────

class TestAuditLoggerSingleton:
    def test_audit_logger_reused_across_requests(self, monkeypatch):
        """按请求新建 AuditLogger 会反复给 audit logger 挂 handler（重复写/fd 泄漏）→ 必须复用。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        created = []
        real = audit_mod.AuditLogger

        class _Counting(real):
            def __init__(self, *a, **kw):
                created.append(1)
                super().__init__(*a, **kw)

        monkeypatch.setattr(audit_mod, "AuditLogger", _Counting)
        set_auth_handler(AuthHandler())
        app = FastAPI()

        @app.get("/ops/x")
        async def x(ok: bool = Depends(require_admin)):
            return {"ok": True}

        client = TestClient(app)
        for _ in range(3):
            assert client.get("/ops/x", headers={
                "Authorization": f"Bearer {_token(ADMIN_USER)}"}).status_code == 200
        assert len(created) == 1, f"审计器被重复创建 {len(created)} 次"
