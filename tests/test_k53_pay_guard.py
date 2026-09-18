"""k53 生产禁用 mock 支付守卫（部署门禁）测试。

背景（k52 审查实测，基线既有漏损）：
    WECHAT_PAY_ENABLED 默认 false → mock 模式：`/api/pay/create` 订单**直接置 paid**、
    `/api/pay/subscribe` **直接开通会员**——分文未收但权益已发（收入漏损 + 与审核材料不符）。

k53 要求：**生产环境 + 真实（虚拟）支付未配置**时，三个支付端点
（`/api/pay/create`、`/api/pay/subscribe`、`/api/pay/virtual/create`）一律
**503 + code=pay_unavailable**，**不置 paid、不开通权益、不落订单**；
非生产（dev/体验态）mock 行为逐字不变。

生产判定（诚实披露）：仓库此前**没有**后端环境判定（客户端 envVersion 不达后端、
EXPERIENCE_MODE 是内容开关而非环境判定），故采用**显式声明 + fail-closed**：
    PAY_REQUIRE_REAL=1/true/yes/on（主闸门） 或 APP_ENV/FORTUNE_ENV=production/prod（别名）
未声明 → 非生产，守卫不生效（dev 行为不变）。

测试不调 LLM（支付链路纯 DB/签名），故不涉及任何模型 provider。
"""
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src import config as app_config  # noqa: E402
from src.api import pay as pay_api  # noqa: E402
from src.api import pay_midas  # noqa: E402
from src.storage.member_dao import MemberDAO  # noqa: E402
from src.security.auth import set_auth_handler, AuthHandler, JWTHandler  # noqa: E402


# ── 改前兼容垫片（k53）────────────────────────────────────────────────────
# 本文件必须能在**改前**代码库上收集并运行：否则「改前失败」只剩一个
# ImportError（收集错误），无法证明 brief 要求的「改前会置 paid / 开会员」。
# 垫片只对 k53 **新增**的守卫 API 做 getattr 兜底——改前这些名字不存在时
# 一律按「守卫不存在 == 改前行为」参与断言，于是断言真实失败（不是被跳过、
# 也不是被放宽）。改后走真实实现，垫片不参与，零放宽。
def is_production() -> bool:
    return getattr(app_config, "is_production", lambda: False)()


def mock_pay_blocked() -> bool:
    return getattr(pay_api, "mock_pay_blocked", lambda: False)()


def real_pay_configured() -> bool:
    return getattr(pay_api, "real_pay_configured", lambda: False)()


PAY_UNAVAILABLE_MESSAGE = getattr(
    pay_api, "PAY_UNAVAILABLE_MESSAGE", "支付暂不可用，请稍后再试")

APP_KEY = "test-app-key-1234567890"
OFFER_ID = "1450613032"
SESSION_KEY = "test-session-key-0123456789"

# 守卫门禁涉及的 env（每个测试前一律清空 → 测试之间零串味、与开发机 shell 无关）
_GUARD_ENVS = ("PAY_REQUIRE_REAL", "APP_ENV", "FORTUNE_ENV", "EXPERIENCE_MODE")
_PAY_ENVS = ("WECHAT_PAY_ENABLED", "MIDAS_OFFER_ID", "MIDAS_APP_KEY", "MIDAS_ENV")


# ───────────────────────── fixtures ─────────────────────────

@pytest.fixture
def db_path():
    """临时数据库文件（绝不触碰生产库）。"""
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
def clean_env(monkeypatch):
    """清空守卫/支付相关 env：默认 = 非生产 + 未配置（判定必须显式声明）。"""
    for name in _GUARD_ENVS + _PAY_ENVS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def auth(monkeypatch):
    set_auth_handler(AuthHandler())
    yield
    set_auth_handler(None)


@pytest.fixture
def client(member_dao, monkeypatch):
    """同时挂载 pay + pay_midas 路由的独立 app（等价 main.py 的装配，不动全局）。"""
    pay_api.setup(member_dao)
    pay_midas.setup(member_dao)
    monkeypatch.setattr(pay_midas, "_get_session_key", lambda uid: SESSION_KEY)
    app = FastAPI()
    app.include_router(pay_api.router)
    app.include_router(pay_midas.router)
    return TestClient(app)


def _token(user_id: str) -> dict:
    tok = JWTHandler(os.environ["JWT_SECRET_KEY"]).create_token(user_id)
    return {"Authorization": f"Bearer {tok}"}


def _arm_production(monkeypatch):
    """显式声明生产（主闸门 PAY_REQUIRE_REAL）。"""
    monkeypatch.setenv("PAY_REQUIRE_REAL", "true")


def _configure_real_pay(monkeypatch):
    """配齐真实支付：WECHAT_PAY_ENABLED=true + 米大师 offerId/AppKey。"""
    monkeypatch.setenv("WECHAT_PAY_ENABLED", "true")
    monkeypatch.setenv("MIDAS_OFFER_ID", OFFER_ID)
    monkeypatch.setenv("MIDAS_APP_KEY", APP_KEY)
    monkeypatch.setenv("MIDAS_ENV", "0")


def _mock_only(monkeypatch):
    """未配置真实支付（mock 通道可用）。"""
    monkeypatch.setenv("WECHAT_PAY_ENABLED", "false")


def _count(db_path: str, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _paid_count(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM payments WHERE status='paid'").fetchone()[0]
    finally:
        conn.close()


def _sign_body(body_bytes: bytes) -> str:
    return hmac.new(APP_KEY.encode(), body_bytes, hashlib.sha256).hexdigest()


def _notify(client, out_trade_no: str):
    body = json.dumps({"Event": "xpay_goods_deliver_notify", "OutTradeNo": out_trade_no},
                      ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return client.post("/api/pay/virtual/notify", content=body,
                       headers={"X-WeChat-Signature": _sign_body(body)})


# ───────────────────── ① 守卫触发条件（纯函数真值表） ─────────────────────

class TestGuardTriggerCondition:
    """触发条件 = is_production() 且 not real_pay_configured()。"""

    def test_default_non_production_guard_off(self):
        """什么都不声明 → 非生产，守卫不生效（dev mock 可用）。"""
        assert is_production() is False
        assert mock_pay_blocked() is False

    def test_pay_require_real_arms_guard(self, monkeypatch):
        _arm_production(monkeypatch)
        assert is_production() is True
        assert real_pay_configured() is False
        assert mock_pay_blocked() is True

    @pytest.mark.parametrize("value,expected", [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        ("0", False), ("false", False), ("no", False), ("", False), ("staging", False),
    ])
    def test_pay_require_real_truthy_values(self, monkeypatch, value, expected):
        monkeypatch.setenv("PAY_REQUIRE_REAL", value)
        assert is_production() is expected

    @pytest.mark.parametrize("var", ["APP_ENV", "FORTUNE_ENV"])
    @pytest.mark.parametrize("value,expected", [
        ("production", True), ("PRODUCTION", True), ("prod", True),
        ("staging", False), ("dev", False), ("test", False),
    ])
    def test_deploy_env_alias_arms_guard(self, monkeypatch, var, value, expected):
        """常规部署标记别名（便利项；仓库当前无任何代码设置它们）。"""
        monkeypatch.setenv(var, value)
        assert is_production() is expected

    def test_configured_real_pay_disarms_guard(self, monkeypatch):
        """生产 + 真实支付已配置 → 守卫不生效（成功路径不受影响）。"""
        _arm_production(monkeypatch)
        _configure_real_pay(monkeypatch)
        assert real_pay_configured() is True
        assert mock_pay_blocked() is False

    @pytest.mark.parametrize("enabled,offer,key,blocked", [
        ("true", "", "", True),              # 开关开了但米大师没配 → 仍拦（不得静默走 mock）
        ("true", OFFER_ID, "", True),
        ("true", "", APP_KEY, True),
        ("false", OFFER_ID, APP_KEY, True),  # 米大师配了但总开关关 → mock 生效，必须拦
        ("true", OFFER_ID, APP_KEY, False),  # 唯一放行组合
    ])
    def test_production_config_matrix(self, monkeypatch, enabled, offer, key, blocked):
        _arm_production(monkeypatch)
        monkeypatch.setenv("WECHAT_PAY_ENABLED", enabled)
        if offer:
            monkeypatch.setenv("MIDAS_OFFER_ID", offer)
        if key:
            monkeypatch.setenv("MIDAS_APP_KEY", key)
        assert mock_pay_blocked() is blocked

    def test_real_pay_configured_single_source_of_truth(self, monkeypatch):
        """pay_midas.virtual_pay_enabled 与 pay.real_pay_configured 必须同源同值。"""
        cases = [("false", "", ""), ("true", "", ""), ("true", OFFER_ID, ""),
                 ("true", OFFER_ID, APP_KEY), ("false", OFFER_ID, APP_KEY)]
        for enabled, offer, key in cases:
            monkeypatch.setenv("WECHAT_PAY_ENABLED", enabled)
            monkeypatch.setenv("MIDAS_OFFER_ID", offer)
            monkeypatch.setenv("MIDAS_APP_KEY", key)
            assert pay_midas.virtual_pay_enabled() == real_pay_configured(), \
                f"两处判定漂移: enabled={enabled} offer={offer} key={key}"

    def test_non_production_never_blocked_even_unconfigured(self, monkeypatch):
        """非生产（含体验态）无论配没配都不拦。"""
        monkeypatch.setenv("EXPERIENCE_MODE", "true")
        assert mock_pay_blocked() is False
        monkeypatch.setenv("WECHAT_PAY_ENABLED", "false")
        assert mock_pay_blocked() is False


# ──────────── ② 生产 + 未配置 → 三端点 503 且零副作用（改前会发货） ────────────

class TestProductionUnconfiguredRejected:
    def test_create_503_and_no_order_no_paid(self, client, member_dao, db_path, monkeypatch):
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)

        r = client.post("/api/pay/create", json={"product_id": "deep_report"},
                        headers=_token("p1"))
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "pay_unavailable"

        # 零订单、零 paid（改前：订单直接置 paid）
        assert member_dao.get_user_payments("p1") == []
        assert _count(db_path, "payments") == 0
        assert _paid_count(db_path) == 0
        assert member_dao.get_user_purchase("p1", "deep_report") is None

    def test_subscribe_503_and_no_membership(self, client, member_dao, db_path, monkeypatch):
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)

        r = client.post("/api/pay/subscribe", json={"plan_id": "monthly"},
                        headers=_token("p2"))
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "pay_unavailable"

        # 零开通（改前：会员直接开通 basic/30 天）
        mem = member_dao.get_membership("p2")
        assert mem["plan"] == "free"
        assert mem["expires_at"] is None
        assert _count(db_path, "payments") == 0
        assert _paid_count(db_path) == 0

    def test_virtual_create_503_not_fallback_code(self, client, member_dao, db_path, monkeypatch):
        """生产必须 503（code=pay_unavailable，**不能**是 virtual_pay_not_enabled）。

        前端 k52 把 `virtual_pay_not_enabled` 当作唯一「降级 mock」信号——
        生产若回该 code，客户端会回落调用 /api/pay/create（旧行为下即免费发货）。
        """
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)

        r = client.post("/api/pay/virtual/create", json={"product_id": "monthly"},
                        headers=_token("p3"))
        assert r.status_code == 503, r.text
        body = r.json()["detail"]
        assert body["code"] == "pay_unavailable"
        assert body["code"] != "virtual_pay_not_enabled"

        assert _count(db_path, "midas_orders") == 0
        assert _count(db_path, "payments") == 0
        assert member_dao.get_membership("p3")["plan"] == "free"

    def test_503_payload_contract_and_no_config_disclosure(self, client, monkeypatch):
        """503 契约：{"code": "pay_unavailable", "message": "支付暂不可用…"}；不回显缺失配置名。"""
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)
        r = client.post("/api/pay/create", json={"product_id": "deep_report"},
                        headers=_token("p4"))
        assert r.status_code == 503
        raw = r.text
        assert "支付暂不可用" in raw
        assert r.json()["detail"]["message"] == PAY_UNAVAILABLE_MESSAGE
        # 不向客户端泄露部署配置细节（env 名/密钥名只进服务端日志）
        for leaked in ("MIDAS_OFFER_ID", "MIDAS_APP_KEY", "WECHAT_PAY_ENABLED",
                       "PAY_REQUIRE_REAL", "APP_ENV"):
            assert leaked not in raw, f"响应体泄露部署配置项: {leaked}"

    def test_repeated_calls_have_no_side_effects(self, client, member_dao, db_path, monkeypatch):
        """幂等：生产下重复调用始终 503，不产生任何订单/会员/中间状态。"""
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)

        for _ in range(3):
            for path, payload in (("/api/pay/create", {"product_id": "deep_report"}),
                                  ("/api/pay/subscribe", {"plan_id": "yearly"}),
                                  ("/api/pay/virtual/create", {"product_id": "yearly"})):
                r = client.post(path, json=payload, headers=_token("p5"))
                assert r.status_code == 503, f"{path}: {r.text}"

        assert member_dao.get_user_payments("p5") == []
        assert member_dao.get_membership("p5")["plan"] == "free"
        assert _count(db_path, "payments") == 0
        assert _count(db_path, "midas_orders") == 0

    def test_guard_does_not_block_read_endpoints(self, client, monkeypatch):
        """只拦「未收款先发货」的写端点；会员/订单/订单状态查询必须照常可用（不过度拦截）。"""
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)
        assert client.get("/api/user/member", headers=_token("p6")).status_code == 200
        assert client.get("/api/user/orders", headers=_token("p6")).status_code == 200
        assert client.get("/api/user/purchase/deep_report",
                          headers=_token("p6")).status_code == 200
        # 订单状态查询是读路径：返回 404（订单不存在），绝不是 503（不得被守卫误拦）
        r = client.get("/api/pay/virtual/status?outTradeNo=YL12345678901234567",
                       headers=_token("p6"))
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["code"] == "order_not_found"

    def test_guard_does_not_touch_signed_notify(self, client, db_path, monkeypatch):
        """回调端点不是 mock 通道（验签后才发货）——守卫不改变其 fail-closed 行为。"""
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)
        body = json.dumps({"Event": "xpay_goods_deliver_notify", "OutTradeNo": "YL12345678901234567"},
                          ensure_ascii=True).encode("utf-8")
        r = client.post("/api/pay/virtual/notify", content=body,
                        headers={"X-WeChat-Signature": _sign_body(body)})
        assert r.status_code == 200
        assert r.json()["errcode"] == -1  # 未配 AppKey，无法验签 → 拒绝发货（既有行为）


# ───────────── ③ 生产 + 已配置 → 行为不变（成功路径不受影响） ─────────────

class TestProductionConfiguredUnchanged:
    def test_virtual_create_success_three_elements(self, client, db_path, monkeypatch):
        _arm_production(monkeypatch)
        _configure_real_pay(monkeypatch)

        r = client.post("/api/pay/virtual/create", json={"product_id": "monthly"},
                        headers=_token("c1"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "short_series_goods"
        assert body["goodsPrice"] == 1990
        assert body["status"] == "pending"
        assert json.loads(body["signData"])["offerId"] == OFFER_ID
        # 下单不发货：订单 pending、会员未开通
        assert _count(db_path, "midas_orders") == 1
        assert _paid_count(db_path) == 0

    def test_create_wechat_branch_pending_not_paid(self, client, member_dao, db_path, monkeypatch):
        """生产 + WECHAT_PAY_ENABLED=true：走既有微信分支（pending、不置 paid）。"""
        _arm_production(monkeypatch)
        _configure_real_pay(monkeypatch)

        r = client.post("/api/pay/create", json={"product_id": "deep_report"},
                        headers=_token("c2"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payment"]["mock"] is False     # 不再是 mock 直发
        assert body["payment"]["requires_config"] is True
        rows = member_dao.get_user_payments("c2")
        assert len(rows) == 1 and rows[0]["status"] == "pending"
        assert rows[0]["payment_method"] == "wechat"
        assert _paid_count(db_path) == 0

    def test_subscribe_wechat_branch_no_membership(self, client, member_dao, monkeypatch):
        _arm_production(monkeypatch)
        _configure_real_pay(monkeypatch)

        r = client.post("/api/pay/subscribe", json={"plan_id": "quarterly"},
                        headers=_token("c3"))
        assert r.status_code == 200, r.text
        assert r.json()["membership"] is None
        assert member_dao.get_membership("c3")["plan"] == "free"

    def test_notify_end_to_end_delivers_in_production(self, client, member_dao, monkeypatch):
        """真实通道端到端：建单 → 米大师回调验签 → 发货开会员（守卫不阻断真发货）。"""
        _arm_production(monkeypatch)
        _configure_real_pay(monkeypatch)

        r = client.post("/api/pay/virtual/create", json={"product_id": "monthly"},
                        headers=_token("c4"))
        out_no = r.json()["outTradeNo"]
        assert _notify(client, out_no).json()["errcode"] == 0

        mem = member_dao.get_membership("c4")
        assert mem["plan"] == "basic"
        delta = datetime.fromisoformat(mem["expires_at"]) - datetime.now()
        assert timedelta(days=28) < delta <= timedelta(days=30)
        assert member_dao.get_user_payments("c4")[0]["status"] == "paid"


# ───────────── ④ 非生产（dev/体验态）→ mock 行为逐字不变 ─────────────

class TestNonProductionUnchanged:
    def test_mock_create_verbatim(self, client, member_dao, monkeypatch):
        _mock_only(monkeypatch)
        r = client.post("/api/pay/create", json={"product_id": "love_compatibility"},
                        headers=_token("d1"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payment"] == {
            "mock": True, "success": True, "mode": "mock",
            "message": "开发环境模拟支付成功（WECHAT_PAY_ENABLED=false）",
        }
        assert body["product_id"] == "love_compatibility"
        assert body["amount"] == 9.9
        assert body["orderId"].isdigit()
        rows = member_dao.get_user_payments("d1")
        assert len(rows) == 1
        assert rows[0]["status"] == "paid" and rows[0]["payment_method"] == "mock"

    def test_mock_subscribe_verbatim(self, client, member_dao, monkeypatch):
        _mock_only(monkeypatch)
        r = client.post("/api/pay/subscribe", json={"plan_id": "pro_monthly"},
                        headers=_token("d2"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payment"]["mock"] is True
        assert body["membership"]["plan"] == "pro"
        assert member_dao.get_membership("d2")["plan"] == "pro"

    def test_virtual_create_still_400_not_enabled(self, client, monkeypatch):
        """非生产未配置 → 仍 400 virtual_pay_not_enabled（前端唯一降级 mock 信号，逐字保留）。"""
        _mock_only(monkeypatch)
        r = client.post("/api/pay/virtual/create", json={"product_id": "monthly"},
                        headers=_token("d3"))
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "virtual_pay_not_enabled"

    def test_experience_mode_mock_unchanged(self, client, member_dao, monkeypatch):
        """体验态（EXPERIENCE_MODE=true）mock 照旧。"""
        monkeypatch.setenv("EXPERIENCE_MODE", "true")
        _mock_only(monkeypatch)
        r = client.post("/api/pay/subscribe", json={"plan_id": "monthly"},
                        headers=_token("d4"))
        assert r.status_code == 200
        assert member_dao.get_membership("d4")["plan"] == "basic"

    def test_dev_after_production_503_recovers(self, client, member_dao, db_path, monkeypatch):
        """生产 503 后再回非生产：mock 正常，无残留状态污染。"""
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)
        assert client.post("/api/pay/subscribe", json={"plan_id": "monthly"},
                           headers=_token("d5")).status_code == 503

        monkeypatch.delenv("PAY_REQUIRE_REAL", raising=False)
        r = client.post("/api/pay/subscribe", json={"plan_id": "monthly"}, headers=_token("d5"))
        assert r.status_code == 200, r.text
        assert member_dao.get_membership("d5")["plan"] == "basic"
        assert _count(db_path, "payments") == 1  # 只有 1 单（503 那次没有落单）


# ───────── ④b 同类一并修：遗留「模拟支付」自助升级端点（brief 三个端点之外） ─────────
# POST /api/membership/{user_id}/upgrade（src/main.py）只有「模拟支付 + 自动确认」一条
# 路径：任何登录用户对**自己**调用即可免费拿到付费会员（生产同样成立，门槛比 mock
# 支付端点更低）。前端不调用该端点（已 grep 确认），仅 scripts/test_auth.py 在本地
# dev 环境用于验 owner 校验。k53 按同一 fail-closed 口径在生产拒绝；非生产逐字不变。

class TestLegacySimulateUpgradeEndpoint:
    def _call(self, member_dao, monkeypatch, user: str, plan: str):
        """直调端点协程（不启 lifespan，避免拉起全应用）。"""
        import asyncio
        import src.main as main_mod
        monkeypatch.setattr(main_mod, "member_dao", member_dao)
        return asyncio.run(main_mod.upgrade_membership(user_id=user, plan=plan, uid=user))

    def test_production_refuses_paid_upgrade(self, member_dao, db_path, monkeypatch):
        from fastapi import HTTPException
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            self._call(member_dao, monkeypatch, "g1", "basic")
        assert ei.value.status_code == 503
        assert ei.value.detail["code"] == "pay_unavailable"
        # 零副作用：不开会员、不下订单
        assert member_dao.get_membership("g1")["plan"] == "free"
        assert _count(db_path, "payments") == 0

    def test_production_refuses_even_when_real_pay_configured(self, member_dao, monkeypatch):
        """该端点没有真实支付通道：生产即使支付已配置也不得自助发放权益。"""
        from fastapi import HTTPException
        _arm_production(monkeypatch)
        _configure_real_pay(monkeypatch)
        for plan in ("basic", "pro", "annual"):
            with pytest.raises(HTTPException) as ei:
                self._call(member_dao, monkeypatch, "g2", plan)
            assert ei.value.status_code == 503

    def test_non_production_demo_path_unchanged(self, member_dao, monkeypatch):
        """非生产：演示路径逐字不变（模拟支付 → 自动确认 → 会员生效）。"""
        _mock_only(monkeypatch)
        r = self._call(member_dao, monkeypatch, "g3", "basic")
        assert r["status"] == "ok"
        assert r["payment_id"] and r["amount"] > 0
        assert r["membership"]["plan"] == "basic"
        # 落库口径也未变：模拟支付单 ⇒ paid
        assert member_dao.get_user_payments("g3")[0]["status"] == "paid"

    def test_production_free_reset_still_allowed(self, member_dao, monkeypatch):
        """plan=free（降级）不放行任何权益 → 不在门禁内，生产仍可用。"""
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)
        r = self._call(member_dao, monkeypatch, "g4", "free")
        assert r["status"] == "ok" and r["plan"] == "free"


# ───────────── ⑤ 非空洞证明：改前漏损真实存在（守卫是唯一拦阻） ─────────────

class TestPreFixLeakWasReal:
    """把守卫按回改前状态（mock_pay_blocked → False），漏损必须复现。

    若本测试通过而 ② 组通过，则证明：② 组测的不是空气——守卫确实是
    「未收款先发权益」的唯一拦阻点（改前 ② 组必失败）。
    """

    def test_guard_off_reproduces_free_grant_on_subscribe(self, client, member_dao, db_path,
                                                          monkeypatch):
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)
        monkeypatch.setattr(pay_api, "mock_pay_blocked", lambda: False, raising=False)  # == 改前行为

        r = client.post("/api/pay/subscribe", json={"plan_id": "yearly"},
                        headers=_token("x1"))
        assert r.status_code == 200, r.text
        assert r.json()["payment"]["mock"] is True
        # 改前漏损复现：分文未收（mock 签名）但订单 paid + 会员已开通
        assert member_dao.get_user_payments("x1")[0]["status"] == "paid"
        assert member_dao.get_membership("x1")["plan"] == "basic"
        assert _paid_count(db_path) == 1

    def test_guard_off_reproduces_free_grant_on_create(self, client, member_dao, monkeypatch):
        _arm_production(monkeypatch)
        _mock_only(monkeypatch)
        monkeypatch.setattr(pay_api, "mock_pay_blocked", lambda: False, raising=False)

        r = client.post("/api/pay/create", json={"product_id": "full_analysis"},
                        headers=_token("x2"))
        assert r.status_code == 200, r.text
        rows = member_dao.get_user_payments("x2")
        assert rows[0]["status"] == "paid"
        assert member_dao.get_user_purchase("x2", "full_analysis") is not None
