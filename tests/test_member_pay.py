"""L5-2 会员支付体系 + 降级补全测试（全部 mock，不调外部 API / 不连微信）。

覆盖（对应实现验收项）：
- 商品定义完整性：基础三档（monthly 19.9/quarterly 49.9/yearly 168 → plan=basic）
  + 高级一档（pro_monthly 39.9 → plan=pro），period_days 30/90/365/30；
- 下单（mock 签名）：POST /api/pay/virtual/create 三要素正确（signData 8 字段、
  paySig/signature HMAC 可复核）、outTradeNo 格式、midas_orders/payments 落库；
- MIDAS_ENV 沙箱/现网切换：env 进 signData 与订单表；
- 回调升级 plan+到期：验签 → 发货 → membership plan/到期按 period_days 生效，
  幂等（重复通知不重复发货）；验签失败拒绝发货；
- 权限：订单状态仅本人可查（他人 403）；下单需登录（无 token 401）；
- mock 支付模式（WECHAT_PAY_ENABLED=false）：/api/pay/subscribe 直接开通会员；
- 续费延长：同档续费到期日从原到期日叠加（monthly×2 ≈ 60 天）；
- 降级补全（L5-1 审查 I-1~I-3）：
  * I-1：downgraded 时 process 不调 _analyze_message/_start_pregen_instant/
    _maybe_compact/gen_suggestions（规则快判替代，零 LLM 前置调用）；
  * I-2：聊天入口不再旧额度硬断（/api/chat 与 /api/chat/stream 移除
    member_dao.check_quota 硬断；旧额度保留在非聊天功能 _tool_zeri）；
  * I-3：_handle_image(downgraded=True) 不透传 api_key → 不调付费 DeepSeek 报告。
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api import pay as pay_api  # noqa: E402
from src.api import pay_midas  # noqa: E402
from src.storage.member_dao import MemberDAO, PLANS  # noqa: E402
from src.security.auth import set_auth_handler, AuthHandler, JWTHandler  # noqa: E402

APP_KEY = "test-app-key-1234567890"
OFFER_ID = "1450613032"
SESSION_KEY = "test-session-key-0123456789"


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
def auth(monkeypatch):
    """JWT 鉴权（仿 test_chat_quota：不跑 lifespan）。"""
    set_auth_handler(AuthHandler())
    yield
    set_auth_handler(None)


@pytest.fixture(autouse=True)
def no_experience_mode(monkeypatch):
    """.env 是体验版（EXPERIENCE_MODE=true）——测试默认关闭体验模式（仿 test_chat_quota）。"""
    from src.services import chat_quota as cq
    monkeypatch.setattr(cq, "is_experience_mode", lambda: False)
    yield


def _token(user_id: str) -> dict:
    tok = JWTHandler(os.environ["JWT_SECRET_KEY"]).create_token(user_id)
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def midas_client(member_dao, monkeypatch):
    """挂载 pay_midas 路由的独立 app（不动 main 全局）。"""
    pay_midas.setup(member_dao)
    monkeypatch.setattr(pay_midas, "_get_session_key", lambda uid: SESSION_KEY)
    monkeypatch.setenv("WECHAT_PAY_ENABLED", "true")
    monkeypatch.setenv("MIDAS_OFFER_ID", OFFER_ID)
    monkeypatch.setenv("MIDAS_APP_KEY", APP_KEY)
    monkeypatch.setenv("MIDAS_ENV", "0")
    app = FastAPI()
    app.include_router(pay_midas.router)
    return TestClient(app)


@pytest.fixture
def pay_client(member_dao, monkeypatch):
    """挂载 pay 路由的独立 app（mock 支付模式：WECHAT_PAY_ENABLED=false）。"""
    pay_api.setup(member_dao)
    monkeypatch.delenv("WECHAT_PAY_ENABLED", raising=False)
    app = FastAPI()
    app.include_router(pay_api.router)
    return TestClient(app)


# ───────────────────────── 商品定义完整性 ─────────────────────────

class TestProducts:
    def test_subscribe_plans_complete(self):
        """基础三档 + 高级一档，价格与 plan 映射符合规格。"""
        from src.api.pay import SUBSCRIBE_PLANS
        assert set(SUBSCRIBE_PLANS.keys()) == {
            "monthly", "quarterly", "yearly", "pro_monthly"}

        assert SUBSCRIBE_PLANS["monthly"]["amount"] == 19.9
        assert SUBSCRIBE_PLANS["monthly"]["internal_plan"] == "basic"
        assert SUBSCRIBE_PLANS["monthly"]["period_days"] == 30

        assert SUBSCRIBE_PLANS["quarterly"]["amount"] == 49.9
        assert SUBSCRIBE_PLANS["quarterly"]["internal_plan"] == "basic"
        assert SUBSCRIBE_PLANS["quarterly"]["period_days"] == 90

        assert SUBSCRIBE_PLANS["yearly"]["amount"] == 168.0
        assert SUBSCRIBE_PLANS["yearly"]["internal_plan"] == "basic"
        assert SUBSCRIBE_PLANS["yearly"]["period_days"] == 365

        assert SUBSCRIBE_PLANS["pro_monthly"]["amount"] == 39.9
        assert SUBSCRIBE_PLANS["pro_monthly"]["internal_plan"] == "pro"
        assert SUBSCRIBE_PLANS["pro_monthly"]["period_days"] == 30

    def test_internal_plans_all_defined(self):
        """internal_plan 必须全部存在于 member_dao.PLANS（发货时才不会落空）。"""
        from src.api.pay import SUBSCRIBE_PLANS
        for pid, p in SUBSCRIBE_PLANS.items():
            assert p["internal_plan"] in PLANS, f"{pid} → {p['internal_plan']} 未定义"
            assert PLANS[p["internal_plan"]]["queries_limit"] > 0

    def test_lookup_product_subscription(self):
        """米大师商品查找：会员商品带 kind/plan/period_days。"""
        prod = pay_midas._lookup_product("quarterly")
        assert prod["kind"] == "subscription"
        assert prod["plan"] == "basic"
        assert prod["period_days"] == 90
        assert prod["amount"] == 49.9
        # 道具商品
        prod2 = pay_midas._lookup_product("deep_report")
        assert prod2["kind"] == "product"
        assert prod2["plan"] is None
        assert pay_midas._lookup_product("nope") is None


# ───────────────────────── 下单（mock 签名） ─────────────────────────

class TestVirtualPayCreate:
    def test_create_requires_auth(self, midas_client):
        r = midas_client.post("/api/pay/virtual/create", json={"product_id": "monthly"})
        assert r.status_code == 401

    def test_create_unknown_product_404(self, midas_client):
        r = midas_client.post("/api/pay/virtual/create", json={"product_id": "nope"},
                              headers=_token("u1"))
        assert r.status_code == 404

    def test_create_member_order_three_elements(self, midas_client):
        """会员下单：signData 8 字段 + paySig/signature 可复核 + 订单落库。"""
        r = midas_client.post("/api/pay/virtual/create",
                              json={"product_id": "monthly"}, headers=_token("u1"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "short_series_goods"
        assert body["env"] == 0
        assert body["offerId"] == OFFER_ID
        assert body["goodsPrice"] == 1990  # 19.9 元 → 分
        assert body["status"] == "pending"

        # signData 固定 8 字段
        sd = json.loads(body["signData"])
        assert sd["offerId"] == OFFER_ID
        assert sd["buyQuantity"] == 1
        assert sd["env"] == 0
        assert sd["currencyType"] == "CNY"
        assert sd["productId"] == "monthly"
        assert sd["goodsPrice"] == 1990
        assert 8 <= len(sd["outTradeNo"]) <= 32
        assert sd["attach"] and "period_days" in sd["attach"]

        # paySig = hmac(appKey, "requestVirtualPayment&" + signData)
        expected_pay_sig = hmac.new(
            APP_KEY.encode(), f"requestVirtualPayment&{body['signData']}".encode(),
            hashlib.sha256).hexdigest()
        assert body["paySig"] == expected_pay_sig
        # signature = hmac(session_key, signData)
        expected_user_sig = hmac.new(
            SESSION_KEY.encode(), body["signData"].encode(),
            hashlib.sha256).hexdigest()
        assert body["signature"] == expected_user_sig

        # midas_orders + payments 落库
        order = pay_midas._get_order(body["outTradeNo"])
        assert order is not None
        assert order["user_id"] == "u1"
        assert order["kind"] == "subscription"
        assert order["plan"] == "basic"
        assert order["amount_cents"] == 1990
        assert order["status"] == "pending"

    def test_create_env_switch_sandbox(self, midas_client, monkeypatch):
        """MIDAS_ENV=1（沙箱）：env 进 signData 与订单表。"""
        monkeypatch.setenv("MIDAS_ENV", "1")
        r = midas_client.post("/api/pay/virtual/create",
                              json={"product_id": "pro_monthly"}, headers=_token("u2"))
        assert r.status_code == 200, r.text
        assert r.json()["env"] == 1
        sd = json.loads(r.json()["signData"])
        assert sd["env"] == 1
        order = pay_midas._get_order(r.json()["outTradeNo"])
        assert order["env"] == 1

    def test_create_env_switch_live(self, midas_client, monkeypatch):
        """MIDAS_ENV 缺省/0（现网）：env=0。"""
        monkeypatch.setenv("MIDAS_ENV", "0")
        r = midas_client.post("/api/pay/virtual/create",
                              json={"product_id": "yearly"}, headers=_token("u2"))
        assert r.status_code == 200, r.text
        assert r.json()["env"] == 0
        sd = json.loads(r.json()["signData"])
        assert sd["env"] == 0
        assert sd["goodsPrice"] == 16800

    def test_create_not_enabled(self, monkeypatch, member_dao):
        """WECHAT_PAY_ENABLED=false → 虚拟支付未启用（400 virtual_pay_not_enabled）。"""
        monkeypatch.setenv("WECHAT_PAY_ENABLED", "false")
        monkeypatch.setattr(pay_midas, "_get_session_key", lambda uid: SESSION_KEY)
        app = FastAPI()
        app.include_router(pay_midas.router)
        client = TestClient(app)
        r = client.post("/api/pay/virtual/create", json={"product_id": "monthly"},
                        headers=_token("u1"))
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "virtual_pay_not_enabled"


# ───────────────────────── 回调升级 plan + 到期 ─────────────────────────

def _sign_body(body_bytes: bytes) -> str:
    return hmac.new(APP_KEY.encode(), body_bytes, hashlib.sha256).hexdigest()


def _notify(client, out_trade_no: str, env: int = 0, sign: bool = True):
    body = json.dumps(
        {"Event": "xpay_goods_deliver_notify", "OutTradeNo": out_trade_no, "Env": env},
        ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    headers = {"X-WeChat-Signature": _sign_body(body)} if sign else {}
    return client.post("/api/pay/virtual/notify", content=body, headers=headers)


class TestVirtualPayNotify:
    def _create_order(self, client, product_id: str, user: str = "buyer"):
        r = client.post("/api/pay/virtual/create", json={"product_id": product_id},
                        headers=_token(user))
        assert r.status_code == 200, r.text
        return r.json()["outTradeNo"]

    def test_notify_upgrades_membership(self, midas_client, member_dao):
        """回调验签通过 → 发货 → plan=basic、到期 ≈ 30 天后。"""
        out_no = self._create_order(midas_client, "monthly")
        r = _notify(midas_client, out_no)
        assert r.status_code == 200, r.text
        assert r.json()["errcode"] == 0

        mem = member_dao.get_membership("buyer")
        assert mem["plan"] == "basic"
        expires = datetime.fromisoformat(mem["expires_at"])
        delta = expires - datetime.now()
        assert timedelta(days=28) < delta <= timedelta(days=30)

        order = pay_midas._get_order(out_no)
        assert order["status"] == "paid"
        # 订单已支付
        rows = member_dao.get_user_payments("buyer")
        assert rows and rows[0]["status"] == "paid"

    def test_notify_quarterly_period(self, midas_client, member_dao):
        """季度商品 → 到期 ≈ 90 天后（period_days 透传不丢）。"""
        out_no = self._create_order(midas_client, "quarterly")
        r = _notify(midas_client, out_no)
        assert r.json()["errcode"] == 0
        mem = member_dao.get_membership("buyer")
        assert mem["plan"] == "basic"
        delta = datetime.fromisoformat(mem["expires_at"]) - datetime.now()
        assert timedelta(days=88) < delta <= timedelta(days=90)

    def test_notify_pro_monthly(self, midas_client, member_dao):
        """高级会员商品 → plan=pro。"""
        out_no = self._create_order(midas_client, "pro_monthly")
        r = _notify(midas_client, out_no)
        assert r.json()["errcode"] == 0
        mem = member_dao.get_membership("buyer")
        assert mem["plan"] == "pro"

    def test_notify_idempotent(self, midas_client, member_dao):
        """重复通知：幂等成功（不重复延长到期）。"""
        out_no = self._create_order(midas_client, "monthly")
        assert _notify(midas_client, out_no).json()["errcode"] == 0
        mem1 = member_dao.get_membership("buyer")
        assert _notify(midas_client, out_no).json()["errcode"] == 0
        mem2 = member_dao.get_membership("buyer")
        assert mem2["expires_at"] == mem1["expires_at"]

    def test_notify_bad_signature_rejected(self, midas_client, member_dao):
        """验签失败：拒绝发货（errcode=-1），会员不变。"""
        out_no = self._create_order(midas_client, "monthly")
        r = _notify(midas_client, out_no, sign=False)
        assert r.json()["errcode"] == -1
        assert member_dao.get_membership("buyer")["plan"] == "free"
        assert pay_midas._get_order(out_no)["status"] == "pending"

    def test_renew_extends_expiry(self, midas_client, member_dao):
        """续费延长：同档月卡买两次 → 到期 ≈ 60 天后（从原到期日叠加）。"""
        out1 = self._create_order(midas_client, "monthly")
        _notify(midas_client, out1)
        mem1 = member_dao.get_membership("buyer")
        out2 = self._create_order(midas_client, "monthly")
        _notify(midas_client, out2)
        mem2 = member_dao.get_membership("buyer")
        delta = datetime.fromisoformat(mem2["expires_at"]) - datetime.fromisoformat(mem1["expires_at"])
        assert timedelta(days=29) < delta <= timedelta(days=31)


# ───────────────────────── 权限（订单 owner 校验） ─────────────────────────

class TestOrderPermission:
    def test_status_owner_only(self, midas_client):
        """订单状态只允许本人查询（他人 403）。"""
        r = midas_client.post("/api/pay/virtual/create",
                              json={"product_id": "monthly"}, headers=_token("owner"))
        out_no = r.json()["outTradeNo"]
        # 本人可查
        r = midas_client.get(f"/api/pay/virtual/status?outTradeNo={out_no}",
                             headers=_token("owner"))
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        # 他人 403
        r = midas_client.get(f"/api/pay/virtual/status?outTradeNo={out_no}",
                             headers=_token("other"))
        assert r.status_code == 403
        # 未登录 401
        r = midas_client.get(f"/api/pay/virtual/status?outTradeNo={out_no}")
        assert r.status_code == 401


# ───────────────────────── mock 支付模式（WECHAT_PAY_ENABLED=false） ─────────────────────────

class TestMockPayMode:
    def test_subscribe_mock_activates(self, pay_client, member_dao):
        """mock 模式：/api/pay/subscribe 直接开通会员（plan=basic，30 天）。"""
        r = pay_client.post("/api/pay/subscribe", json={"plan_id": "monthly"},
                            headers=_token("m1"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payment"]["mock"] is True
        mem = member_dao.get_membership("m1")
        assert mem["plan"] == "basic"
        delta = datetime.fromisoformat(mem["expires_at"]) - datetime.now()
        assert timedelta(days=28) < delta <= timedelta(days=30)

    def test_subscribe_quarterly_mock(self, pay_client, member_dao):
        r = pay_client.post("/api/pay/subscribe", json={"plan_id": "quarterly"},
                            headers=_token("m2"))
        assert r.status_code == 200
        mem = member_dao.get_membership("m2")
        assert mem["plan"] == "basic"
        delta = datetime.fromisoformat(mem["expires_at"]) - datetime.now()
        assert timedelta(days=88) < delta <= timedelta(days=90)

    def test_subscribe_pro_mock(self, pay_client, member_dao):
        r = pay_client.post("/api/pay/subscribe", json={"plan_id": "pro_monthly"},
                            headers=_token("m3"))
        assert r.status_code == 200
        assert member_dao.get_membership("m3")["plan"] == "pro"

    def test_subscribe_unknown_plan_404(self, pay_client):
        r = pay_client.post("/api/pay/subscribe", json={"plan_id": "first_month"},
                            headers=_token("m4"))
        assert r.status_code == 404  # 旧档位已下线

    def test_member_info_after_purchase(self, pay_client, member_dao):
        """购买后 GET /api/user/member：isMember=true + plan=basic + 到期。"""
        pay_client.post("/api/pay/subscribe", json={"plan_id": "monthly"},
                        headers=_token("m5"))
        r = pay_client.get("/api/user/member", headers=_token("m5"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["isMember"] is True
        assert body["plan"] == "basic"
        assert body["expireDate"]


# ───────────────────────── 降级补全（L5-1 审查 I-1~I-3） ─────────────────────────

def _make_handler_with_llm():
    """构造 HandlerBot：llm 全 Mock，可断言 LLM 调用是否发生。"""
    from src.bot.handler import MessageHandler
    mock_llm = Mock()
    mock_llm.api_key = ""
    mock_llm.model = "deepseek-v4-flash"
    mock_llm.chat_conversation.return_value = "🔮 精简回复"
    mock_llm.chat.return_value = Mock(response="🔮 精简回复")
    mock_dao = Mock()
    mock_dao.get_user_bazi.return_value = None
    mock_dao.db_path = ""
    mock_session = Mock()
    mock_session.get_context_for_llm.return_value = []
    mock_session.add_message.return_value = None
    return MessageHandler(
        engine=Mock(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        retriever=Mock(), llm=mock_llm, dao=mock_dao, session_dao=mock_session,
    )


class TestDowngradePrecalls:
    def test_rule_analyze_bazi_fast(self):
        """I-1 规则快判：纯生日陈述 → bazi；带意图词 → None（零 LLM）。"""
        h = _make_handler_with_llm()
        a1 = h._rule_analyze("1990年5月20日 下午3点 北京 男")
        assert a1.intent == "bazi"
        a2 = h._rule_analyze("帮我看看财运怎么样")
        assert a2.intent is None
        a3 = h._rule_analyze("1990年5月20日 我适合去哪个公司")
        assert a3.intent is None  # 含意图词 → 不掐成纯排盘

    def test_downgraded_process_no_analyze_call(self):
        """I-1：downgraded=True 时 process 不调用 _analyze_message（规则快判替代）。"""
        h = _make_handler_with_llm()
        calls = []
        orig_analyze = h._analyze_message

        def spy_analyze(*a, **kw):
            calls.append("analyze")
            return orig_analyze(*a, **kw)

        h._analyze_message = spy_analyze
        # A4：问候"你好呀"已由简单意图快通道承接（0 LLM 直通）——改中性
        # 消息验证降级链路跳过意图分析的目的不变
        reply = h.process("随便聊聊", "d1", downgraded=True)
        assert calls == []  # 未发起 LLM 意图分析
        assert reply  # 自由对话正常回复

    def test_downgraded_skips_pregen(self):
        """I-1：downgraded=True 不提交预生成（无后台 worker/LLM 秒回）。"""
        h = _make_handler_with_llm()
        orig_start = h._start_pregen_instant
        started = []

        def spy_start(msg):
            started.append(msg)
            return orig_start(msg)

        h._start_pregen_instant = spy_start
        h.process("1990年5月20日 下午3点 北京 男 帮我看八字", "d2", downgraded=True)
        assert started == []  # 降级不预生成

    def test_normal_process_still_analyzes(self):
        """非降级：_analyze_message 与预生成照常（降级不破坏正常链路）。"""
        h = _make_handler_with_llm()
        calls = []
        orig = h._analyze_message

        def spy(*a, **kw):
            calls.append(1)
            return orig(*a, **kw)

        h._analyze_message = spy
        # A4：问候"你好呀"已由简单意图快通道承接（0 LLM 直通）——改中性
        # 消息验证正常链路仍走意图分析的目的不变
        h.process("随便聊聊", "n1", downgraded=False)
        assert calls  # 正常链路仍走 LLM 意图分析

    def test_gen_suggestions_disabled_when_downgraded(self):
        """I-1：gen_suggestions 在降级用户上直接返回 []（不调 LLM）。"""
        h = _make_handler_with_llm()
        h._downgraded["d3"] = True
        assert h.gen_suggestions("d3", "我最近财运如何", "回复内容") == []
        h._downgraded["n3"] = False
        out = h.gen_suggestions("n3", "我最近财运如何？", "回复内容")
        # 非降级走正常逻辑（api_key 为空 → 快速返回 []，无 LLM 调用）
        assert out == []


class TestQuotaCoordination:
    def test_chat_entry_no_old_hard_break(self):
        """I-2：/api/chat 与 /api/chat/stream 不再调用旧额度硬断（check_quota）。"""
        import src.main as m
        import src.api.chat_stream as cs
        assert "check_quota(" not in _src_of(m)
        assert "check_quota(" not in _src_of(cs)

    def test_old_quota_kept_for_zeri_tool(self):
        """I-2：旧额度保留在非聊天功能（择日工具 _tool_zeri 引擎调用门）。"""
        import src.bot.handler as hmod
        src = _src_of(hmod)
        # _tool_zeri 内仍用 _check_quota 门控引擎调用
        tool_zeri = src[src.index("def _tool_zeri"):src.index("def _extract_zeri_exclude_dates")]
        assert "_check_quota(user_id)" in tool_zeri

    def test_process_step06_no_hard_break(self):
        """I-2：process 中聊天不再硬断（Step 0.6 已移除剩余=0 直接返回的旧逻辑）。"""
        import src.bot.handler as hmod
        src = _src_of(hmod)
        step06 = src[src.index("# Step 0.6:"):src.index('# Handle "会员" keyword')]
        assert "已用完" not in step06
        assert "remaining <= 0" not in step06

    def test_free_user_always_chats(self, member_dao):
        """I-2：旧额度用尽不影响对话——chat_quota 独立消费，超限降级续聊（不 429）。"""
        from src.services.chat_quota import try_consume_chat_quota, CHAT_DAILY_LIMIT
        from src.storage.chat_quota_dao import ChatQuotaDAO
        qdao = ChatQuotaDAO(member_dao.db_path)
        # 先把旧额度（membership 免费档上限 3）置满（R1-1 产品裁决：建档不
        # 初始化免费额度——get_membership 不再自动建档，行只由显式创建产生，
        # 测试装配改为显式 create_membership）
        member_dao.create_membership("free1", "free")
        conn = member_dao._connect()
        conn.execute("UPDATE memberships SET queries_used=queries_limit WHERE user_id='free1'")
        conn.commit()
        conn.close()
        mem = member_dao.get_membership("free1")
        assert mem["queries_remaining"] == 0  # 旧额度视角已用尽
        # 但对话额度独立消费：15 条内正常，之后降级续聊（绝不硬断）
        ctx = None
        for _ in range(CHAT_DAILY_LIMIT + 2):
            ctx = try_consume_chat_quota(member_dao, qdao, "free1")
        assert ctx["downgraded"] is True  # 降级续聊而非硬断


class TestImageDowngrade:
    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch):
        """下载图片 → 写本地假文件（不真连外网）。"""
        import urllib.request

        def fake_urlretrieve(url, path):
            with open(path, "wb") as f:
                f.write(b"fake-image-bytes")

        monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)
        yield

    def test_handle_image_passes_downgraded(self):
        """I-3：_handle_image 支持 downgraded 参数并透传 CV 读取。"""
        h = _make_handler_with_llm()
        calls = {}

        def fake_face(url, text="", downgraded=False):
            calls["downgraded"] = downgraded
            return None

        h._try_face_reading = fake_face
        h._try_palm_reading = lambda url, text="", downgraded=False: None
        h._handle_image("https://example.com/a.jpg", "看看我的面相", downgraded=True)
        assert calls["downgraded"] is True

    def test_face_reader_lite_skips_api_key(self, monkeypatch):
        """I-3：降级时 _try_face_reading 不把付费 api_key 传给报告生成器。"""
        import src.engines.face_reader as fr
        h = _make_handler_with_llm()
        h.llm.api_key = "sk-deepseek-real"
        seen = {}

        class _FakeReader:
            def analyze(self, path):
                return Mock()

        monkeypatch.setattr(fr, "FaceReader", _FakeReader)

        def fake_gen(metrics, retriever=None, api_key=""):
            seen["api_key"] = api_key
            seen["retriever"] = retriever
            return "📷 面相分析报告"

        monkeypatch.setattr(fr, "generate_report", fake_gen)

        # 降级：api_key 置空、retriever 置空 → 报告生成器走本地规则文案（不调付费）
        out = h._try_face_reading("https://x/a.jpg", "", downgraded=True)
        assert seen["api_key"] == ""
        assert seen["retriever"] is None
        assert "精简回复" in out
        # 非降级：正常传 llm.api_key
        h._try_face_reading("https://x/a.jpg", "", downgraded=False)
        assert seen["api_key"] == "sk-deepseek-real"

    def test_palm_reader_lite_skips_api_key(self, monkeypatch):
        """I-3：手相同款——降级不调付费报告（api_key 置空）。"""
        import src.engines.palm_reader as pr
        h = _make_handler_with_llm()
        h.llm.api_key = "sk-deepseek-real"
        seen = {}

        class _FakeReader:
            def analyze(self, path):
                return Mock()

        monkeypatch.setattr(pr, "PalmReader", _FakeReader)

        def fake_gen(metrics, retriever=None, api_key=""):
            seen["api_key"] = api_key
            return "✋ 手相分析报告"

        monkeypatch.setattr(pr, "generate_palm_report", fake_gen)
        out = h._try_palm_reading("https://x/a.jpg", "", downgraded=True)
        assert seen["api_key"] == ""
        assert "精简回复" in out
        h._try_palm_reading("https://x/a.jpg", "", downgraded=False)
        assert seen["api_key"] == "sk-deepseek-real"


def _src_of(module) -> str:
    """模块源码文本（供行为断言）。"""
    import inspect
    return inspect.getsource(module)


# ───────────────────────── L5-2 修复：并发回调幂等（支付红线） ─────────────────────────

class TestNotifyConcurrency:
    def test_concurrent_notify_delivers_once(self, midas_client, member_dao, monkeypatch):
        """并发回调幂等：两线程同时 notify 同一订单 → 原子抢占只发货一次。

        旧实现 read-then-act（先查 status 再发货再标记），两并发回调都可
        通过 status 检查 → 双倍发货；修复后发货前先原子 UPDATE pending→paid，
        只有一个赢家，输家直接应答"已处理"（不发货）。
        """
        r = midas_client.post("/api/pay/virtual/create",
                              json={"product_id": "monthly"}, headers=_token("buyer"))
        assert r.status_code == 200, r.text
        out_no = r.json()["outTradeNo"]

        delivered = []
        orig_deliver = pay_midas._deliver_goods

        def slow_deliver(order):
            time.sleep(0.05)  # 放大竞态窗口（旧实现两线程都会进入发货）
            delivered.append(order["out_trade_no"])
            return orig_deliver(order)

        monkeypatch.setattr(pay_midas, "_deliver_goods", slow_deliver)

        # 两个独立 TestClient（各自事件循环/portal），同一订单并发打回调
        client_b = TestClient(midas_client.app)
        results, errors = [], []

        def hit(client):
            try:
                results.append(_notify(client, out_no).json())
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=hit, args=(midas_client,))
        t2 = threading.Thread(target=hit, args=(client_b,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, errors
        assert len(delivered) == 1, f"并发回调必须只发货一次，实际 {len(delivered)} 次"
        assert all(res["errcode"] == 0 for res in results)  # 双方都应答成功（幂等）
        assert pay_midas._get_order(out_no)["status"] == "paid"
        # 会员只开通一次（到期不重复延长）
        mem = member_dao.get_membership("buyer")
        assert mem["plan"] == "basic"
        delta = datetime.fromisoformat(mem["expires_at"]) - datetime.now()
        assert timedelta(days=28) < delta <= timedelta(days=30)

    def test_deliver_failure_rolls_back_for_retry(self, midas_client, member_dao, monkeypatch):
        """发货失败 → 回滚 pending（微信按 errcode!=0 重试，订单不丢）。"""
        r = midas_client.post("/api/pay/virtual/create",
                              json={"product_id": "monthly"}, headers=_token("buyer"))
        assert r.status_code == 200, r.text
        out_no = r.json()["outTradeNo"]
        calls = {"n": 0}
        orig_deliver = pay_midas._deliver_goods

        def fail_once(order):
            calls["n"] += 1
            if calls["n"] == 1:
                return False  # 首次发货失败（如 DB 抖动）
            return orig_deliver(order)

        monkeypatch.setattr(pay_midas, "_deliver_goods", fail_once)

        r1 = _notify(midas_client, out_no).json()
        assert r1["errcode"] == -1
        assert pay_midas._get_order(out_no)["status"] == "pending"  # 已回滚可重试

        r2 = _notify(midas_client, out_no).json()  # 微信重试
        assert r2["errcode"] == 0
        assert pay_midas._get_order(out_no)["status"] == "paid"
        assert member_dao.get_membership("buyer")["plan"] == "basic"


# ───────────────────────── L5-2 修复：跨档续费"取高者" ─────────────────────────

class TestCrossTierRenewal:
    def _set_remaining(self, member_dao, user_id: str, days: float):
        """把会员到期时间拨到 days 天后（模拟剩余天数；负数=已过期）。"""
        conn = member_dao._connect()
        try:
            conn.execute(
                "UPDATE memberships SET expires_at=? WHERE user_id=?",
                ((datetime.now() + timedelta(days=days)).isoformat(), user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _buy(self, midas_client, product_id: str, user: str = "buyer") -> None:
        r = midas_client.post("/api/pay/virtual/create",
                              json={"product_id": product_id}, headers=_token(user))
        assert r.status_code == 200, r.text
        out_no = r.json()["outTradeNo"]
        resp = _notify(midas_client, out_no).json()
        assert resp["errcode"] == 0, resp

    def test_pro_buys_basic_keeps_pro(self, midas_client, member_dao):
        """pro 剩 20 天买 basic → plan 仍 pro（不降级），购买天数照常入账。"""
        member_dao.create_membership("cross1", "pro", period_days=30)
        self._set_remaining(member_dao, "cross1", 20)
        before = datetime.fromisoformat(member_dao.get_membership("cross1")["expires_at"])

        self._buy(midas_client, "monthly", user="cross1")  # basic 30 天

        mem = member_dao.get_membership("cross1")
        assert mem["plan"] == "pro"  # 取高者：保留高档，不降级
        after = datetime.fromisoformat(mem["expires_at"])
        delta = after - before
        assert timedelta(days=29) < delta <= timedelta(days=31)  # 从原到期 +30 天

    def test_basic_buys_pro_upgrades_with_gift(self, midas_client, member_dao):
        """basic 剩 15 天买 pro → pro + 赠期（剩余 basic 天数并入 pro 期）。"""
        member_dao.create_membership("cross2", "basic", period_days=30)
        self._set_remaining(member_dao, "cross2", 15)
        before = datetime.fromisoformat(member_dao.get_membership("cross2")["expires_at"])

        self._buy(midas_client, "pro_monthly", user="cross2")  # pro 30 天

        mem = member_dao.get_membership("cross2")
        assert mem["plan"] == "pro"  # 升级
        after = datetime.fromisoformat(mem["expires_at"])
        delta = after - before
        assert timedelta(days=29) < delta <= timedelta(days=31)  # 赠期：原到期 + 30 天

    def test_same_tier_renewal_unchanged(self, midas_client, member_dao):
        """同档续费：plan 不变、到期照常从原到期日延长（既有行为不回归）。"""
        member_dao.create_membership("cross5", "basic", period_days=30)
        self._set_remaining(member_dao, "cross5", 10)
        before = datetime.fromisoformat(member_dao.get_membership("cross5")["expires_at"])

        self._buy(midas_client, "quarterly", user="cross5")  # basic 90 天

        mem = member_dao.get_membership("cross5")
        assert mem["plan"] == "basic"
        delta = datetime.fromisoformat(mem["expires_at"]) - before
        assert timedelta(days=89) < delta <= timedelta(days=91)

    def test_expired_higher_does_not_hold(self, midas_client, member_dao):
        """pro 已过期 → 买 basic → plan=basic（过期高档不拦截购买，从现在起算）。"""
        member_dao.create_membership("cross3", "pro", period_days=30)
        self._set_remaining(member_dao, "cross3", -5)

        self._buy(midas_client, "monthly", user="cross3")

        mem = member_dao.get_membership("cross3")
        assert mem["plan"] == "basic"
        delta = datetime.fromisoformat(mem["expires_at"]) - datetime.now()
        assert timedelta(days=28) < delta <= timedelta(days=30)

    def test_create_membership_same_semantics(self, member_dao):
        """create_membership 与 confirm_payment 同口径（根因一致，不降级）。"""
        member_dao.create_membership("cross4", "pro", period_days=30)
        self._set_remaining(member_dao, "cross4", 20)
        member_dao.create_membership("cross4", "basic", period_days=30)
        mem = member_dao.get_membership("cross4")
        assert mem["plan"] == "pro"  # 高档未过期 → 保留


# ───────────────────────── L5-2 修复：降级 bazi 全量管线门控（降级成本） ─────────────────────────

def _make_real_engine_handler():
    """真实 BaziEngine + Mock LLM 的 handler（bazi 降级/正常路径断言用）。"""
    from src.engines.bazi import BaziEngine
    h = _make_handler_with_llm()
    h.engine = BaziEngine()  # 确定性 0 成本排盘
    h.memory_system = Mock()  # 防记忆 JSON 落盘
    return h


class _FakeChartGen:
    """命盘图片生成打桩（正常路径避免 Playwright 真实渲染）。"""

    def generate(self, result, title=""):
        return "/tmp/fake_chart.png"


class _FakeAnthropicResp:
    """httpx 响应替身（DeepSeek Anthropic 兼容格式，仅防意外调用时崩）。"""

    def __init__(self, content="", status=200):
        self._content = content
        self.status_code = status

    def json(self):
        return {"content": [{"type": "text", "text": self._content}],
                "stop_reason": "end_turn"}


class TestBaziDowngradeLite:
    def test_downgraded_bazi_engine_only_no_llm(self, monkeypatch):
        """降级用户发"1990年5月20日午时男" → 引擎排盘+精简文案，零 LLM 调用。

        断言：不调 DeepSeek 端点（httpx 层零调用）、不调 RAG、不调
        analyze/chat/chat_conversation（仅确定性引擎 + 规则文案）。
        """
        import httpx
        h = _make_real_engine_handler()
        calls = []

        def fake_post(self, url, **kw):
            calls.append((url, kw))
            return _FakeAnthropicResp("不应被调用")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        reply = h.process("1990年5月20日午时男", "d9", downgraded=True)

        assert reply
        assert calls == [], f"降级 bazi 不应发起任何 HTTP/LLM 调用: {[c[0] for c in calls]}"
        h.llm.analyze.assert_not_called()
        h.llm.chat.assert_not_called()
        h.llm.chat_conversation.assert_not_called()
        h.retriever.search.assert_not_called()  # 不调 RAG
        # 回复 = 引擎排盘卡片 + 规则精简要点
        assert "八字命盘" in reply  # format_compact_card 卡片
        assert "日主" in reply
        assert "命局要点" in reply  # 精简文案

    def test_normal_bazi_still_full_pipeline(self, monkeypatch):
        """非降级：bazi 仍走 RAG + 主分析 LLM（门控不破坏正常链路）。"""
        import src.bot.handler as hmod
        h = _make_real_engine_handler()
        calls = {"analyze": 0, "retriever": 0}

        def fake_analyze(*a, **k):
            calls["analyze"] += 1
            return Mock(response="深度分析正文")

        def fake_search(*a, **k):
            calls["retriever"] += 1
            return []

        h.llm.analyze = fake_analyze
        h.retriever.search = fake_search
        # 打桩正常链路的真实副作用：并行 advisor LLM / 图表渲染 / 引用校验嵌入模型
        monkeypatch.setattr(
            hmod, "AdaptiveAdvisor",
            lambda: Mock(generate=lambda *a, **k: {"actions": []}))
        monkeypatch.setattr("src.images.bazi_chart_html.BaziChartHTML", _FakeChartGen)
        monkeypatch.setattr(
            hmod, "verify_citations",
            lambda reply, question, citations: (reply, citations))

        reply = h.process("1990年5月20日午时男", "n9", downgraded=False)

        assert calls["analyze"] == 1  # 主分析 LLM 照常
        assert calls["retriever"] == 1  # RAG 照常
        assert "深度分析正文" in reply

    def test_downgraded_tool_loop_disabled(self):
        """降级时 _run_tool_loop 禁用：残留 <tool_call> 不执行工具、不再调 LLM。"""
        h = _make_handler_with_llm()
        h._downgraded["t1"] = True
        out = h._run_tool_loop("帮我查查", "t1",
                               "今天运势不错<tool_call>检索: 婚姻古籍</tool_call>")
        assert "<tool_call>" not in out
        h.llm.chat_conversation.assert_not_called()

    def test_downgraded_xuetang_goes_lite(self):
        """降级时 xuetang 关键词分支门控 → 走 lite 自由对话（不调学堂 RAG/LLM）。"""
        h = _make_handler_with_llm()
        called = []
        h._handle_xuetang = lambda *a, **k: called.append(1) or "学堂内容"
        reply = h.process("我想学命理入门", "x1", downgraded=True)
        assert called == []  # 未走学堂全量分支
        assert reply == "🔮 精简回复"  # lite 自由对话
        assert h.llm.chat.called
        assert h.llm.chat.call_args.kwargs.get("lite") is True  # lite 模型/prompt

    def test_downgraded_advisor_goes_lite(self):
        """降级时 advisor 关键词分支门控 → 走 lite（不调 AdaptiveAdvisor LLM）。"""
        h = _make_handler_with_llm()
        called = []
        h._handle_advisor = lambda *a, **k: called.append(1) or "建议内容"
        reply = h.process("我最近很迷茫，我该怎么办", "a1", downgraded=True)
        assert called == []
        assert reply == "🔮 精简回复"
        assert h.llm.chat.called

    def test_downgraded_confidant_gated(self):
        """降级时倾诉分支门控：is_sharing 不再触发 confidant 全量 LLM，走 lite。"""
        h = _make_handler_with_llm()
        called = []
        h._handle_confidant = lambda *a, **k: called.append(1) or "倾诉回复"
        reply = h.process("最近工作压力好大，每天都快撑不住了", "c1", downgraded=True)
        assert called == []  # 规则快判 is_sharing=False + 显式门控兜底
        assert reply == "🔮 精简回复"


# ───────────────────────── 会员升级/续费回复包卡片（批次 2 P2 / Task C2） ─────────────────────────

class TestMemberUpgradeCard:
    """P2 拍板：会员升级/续费回复与其他付费动作一致包卡片。

    卡片类型判据：会员域回复归 `data` 卡——E2-1 契约规则 3 将"会员"直读归
    data（RecordQuery _q_会员 同为 data 卡）；升级信息为服务端确定性文案
    （无错误签名），直接判定可包。
    """

    def test_member_keyword_reply_wrapped_as_data_card(self):
        """「会员」→ 回复包 data 卡片（标题/正文保留）。"""
        h = _make_handler_with_llm()
        reply = h.process("会员", "member_card_1")
        assert reply.startswith("[card:data"), reply[:100]
        assert "[/card]" in reply
        assert "会员计划" in reply

    def test_upgrade_renew_keywords_all_wrapped(self):
        """升级/续费同族关键词（升级/付费/套餐/价格/多少钱）→ 全部包 data 卡。"""
        h = _make_handler_with_llm()
        for kw in ("升级", "付费", "套餐", "价格", "多少钱"):
            reply = h.process(kw, "member_card_2")
            assert reply.startswith("[card:data"), (kw, reply[:100])
            assert "[/card]" in reply, (kw, reply[:100])

    def test_member_reply_stored_wrapped_with_session(self):
        """E2-1 契约：落库与返回值一致携带卡片标记（历史消息可回渲染）。"""
        h = _make_handler_with_llm()
        reply = h.process("会员", "member_card_3")
        calls = h.session_dao.add_message.call_args_list
        assert calls, "会员分支应写会话"
        assert calls[-1][0][2] == reply  # 落库内容 = 返回内容（含卡片标记）
        assert "[/card]" in calls[-1][0][2]

    def test_member_reply_whole_body_in_card(self):
        """宁整勿碎：引导语不在已知尾部模式内 → 整体包（不丢内容）。"""
        h = _make_handler_with_llm()
        reply = h.process("会员", "member_card_4")
        assert "开通会员解锁完整版" in reply
        assert "选择套餐即可升级" in reply

    def test_renew_single_word_hits_upgrade_card(self):
        """Q2（批次 2 收尾）：单字「续费」→ 确定性出缴费引导卡（不落 LLM 自由发挥）。

        与「会员」/「升级」同族关键词走同一条 P2 C2 拍板路径：data 卡 +
        _mark_card_turn data_read=True 生效；既有会员词一并回归。
        """
        h = _make_handler_with_llm()
        for kw in ("续费", "会员", "升级"):
            reply = h.process(kw, "member_card_6")
            assert reply.startswith("[card:data"), (kw, reply[:100])
            assert "[/card]" in reply, (kw, reply[:100])
            assert "易理明灯会员计划" in reply, (kw, reply[:100])
            assert h._card_turn["member_card_6"]["data_read"] is True, kw

    def test_renew_embedded_in_sentence_not_matched(self):
        """Q2 反例：含「续费」的句子不命中精确匹配分支（走全流程/LLM）。

        msg.strip() in 元组的精确匹配语义：整条消息等于「续费」才命中；
        「我不想续费了」「续费会员多少钱」应落到全流程——断言不返回升级卡文案。
        """
        h = _make_handler_with_llm()
        for msg in ("我不想续费了", "续费会员多少钱"):
            reply = h.process(msg, "member_card_7")
            assert "会员计划" not in reply, (msg, reply[:100])
            assert not reply.startswith("[card:data"), (msg, reply[:100])

    def test_member_card_not_breaking_error_double_loop(self):
        """E2-1 双闭环不受影响：普通闲聊不误包；错误签名回复仍拒包。"""
        h = _make_handler_with_llm()
        plain = h.process("随便聊聊", "member_card_5")
        assert "[card:" not in plain
        from src.bot.card_mark import wrap_card
        assert wrap_card("⚠️ 服务暂时不可用：x", "data") == "⚠️ 服务暂时不可用：x"
