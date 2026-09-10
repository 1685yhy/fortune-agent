"""L5-1 对话额度体系 + 降级链路测试（全部 mock，不调外部 API）。

覆盖（对应实现验收项）：
- 免费用户 15 条内正常（cnt 递增、downgraded=False）；
- 第 16 条起降级（downgraded=True，不 429 硬断、不再累计计数）；
- 会员（plan != free）不限额不计数；体验模式不限额不计数；
- 日重置（day 维度独立，跨天自然清零）；
- 降级链路模型切换：lite 路径调 GLM 端点（open.bigmodel.cn / glm-4-flash /
  CHAT_PROMPT_LITE），GLM 失败回退 DeepSeek；流式同样走 GLM；
- 额度查询接口 chat_quota_status / GET /api/user/chat-quota 契约。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
import httpx  # noqa: E402

from src.storage.chat_quota_dao import ChatQuotaDAO  # noqa: E402
from src.storage.member_dao import MemberDAO  # noqa: E402
from src.services import chat_quota as cq  # noqa: E402
from src.services.chat_quota import CHAT_DAILY_LIMIT  # noqa: E402
from src.llm import client as llm_client  # noqa: E402
from src.llm.client import (  # noqa: E402
    FortuneLLM, GLM_COMPLETIONS_URL, GLM_DEFAULT_MODEL, ANTHROPIC_MESSAGES_URL,
)
from src.llm.prompts import CHAT_PROMPT_LITE  # noqa: E402


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
def daos(db_path):
    """会员 + 对话额度 DAO 对（同一临时库）。"""
    return MemberDAO(db_path), ChatQuotaDAO(db_path)


@pytest.fixture(autouse=True)
def no_experience_mode(monkeypatch):
    """.env 是体验版（EXPERIENCE_MODE=true），测试默认关闭体验模式；
    体验模式用例自行 patch 为 True。"""
    monkeypatch.setattr(cq, "is_experience_mode", lambda: False)
    yield


# ───────────────────────── 额度表（chat_quota） ─────────────────────────

class TestChatQuotaDAO:
    def test_new_user_zero(self, daos):
        _, dao = daos
        assert dao.get_count("u1", "2026-08-21") == 0

    def test_consume_within_limit(self, daos):
        _, dao = daos
        assert dao.consume("u1", "2026-08-21", 15) == 14  # 剩余 14，放行
        assert dao.consume("u1", "2026-08-21", 15) == 13
        assert dao.get_count("u1", "2026-08-21") == 2

    def test_consume_over_limit_returns_negative(self, daos):
        _, dao = daos
        for _ in range(15):
            assert dao.consume("u1", "2026-08-21", 15) >= 0
        # 第 16 条：超限（不计数）
        assert dao.consume("u1", "2026-08-21", 15) == -1
        assert dao.get_count("u1", "2026-08-21") == 15

    def test_day_reset(self, daos):
        """日重置：day 维度独立，跨天自然清零（无定时任务）。"""
        _, dao = daos
        for _ in range(15):
            dao.consume("u1", "2026-08-21", 15)
        # 次日额度独立（0 可用）
        assert dao.get_count("u1", "2026-08-22") == 0
        assert dao.consume("u1", "2026-08-22", 15) == 14
        # 昨日计数不受影响
        assert dao.get_count("u1", "2026-08-21") == 15

    def test_users_isolated(self, daos):
        _, dao = daos
        dao.consume("u_a", "2026-08-21", 15)
        assert dao.get_count("u_b", "2026-08-21") == 0


# ───────────────────────── 额度判定 / 消费（service） ─────────────────────────

class TestChatQuotaService:
    def test_free_within_limit(self, daos):
        """免费 15 条内：每次消费 +1，正常（不降级）。"""
        member_dao, quota_dao = daos
        for i in range(1, CHAT_DAILY_LIMIT + 1):
            ctx = cq.try_consume_chat_quota(member_dao, quota_dao, "free_u")
            assert ctx["is_member"] is False
            assert ctx["used"] == i
            assert ctx["limit"] == CHAT_DAILY_LIMIT
            assert ctx["downgraded"] is False

    def test_free_16th_downgraded(self, daos):
        """第 16 条：降级标记（不 429、不累计）。"""
        member_dao, quota_dao = daos
        for _ in range(CHAT_DAILY_LIMIT):
            cq.try_consume_chat_quota(member_dao, quota_dao, "free_u")
        ctx = cq.try_consume_chat_quota(member_dao, quota_dao, "free_u")
        assert ctx["downgraded"] is True
        assert ctx["used"] == CHAT_DAILY_LIMIT  # 不再累计
        # 继续对话仍降级（不硬断）
        ctx2 = cq.try_consume_chat_quota(member_dao, quota_dao, "free_u")
        assert ctx2["downgraded"] is True
        assert ctx2["used"] == CHAT_DAILY_LIMIT

    def test_member_unlimited(self, daos):
        """会员不限额：不计数、不降级。"""
        member_dao, quota_dao = daos
        member_dao.create_membership("vip_u", "basic")
        for _ in range(30):
            ctx = cq.try_consume_chat_quota(member_dao, quota_dao, "vip_u")
            assert ctx["downgraded"] is False
            assert ctx["is_member"] is True
            assert ctx["limit"] is None
        # 会员不写 chat_quota 表
        assert quota_dao.get_count("vip_u", cq._bj_day()) == 0

    def test_experience_mode_unlimited(self, daos, monkeypatch):
        """体验模式：不计数、不降级。"""
        member_dao, quota_dao = daos
        monkeypatch.setattr(cq, "is_experience_mode", lambda: True)
        for _ in range(30):
            ctx = cq.try_consume_chat_quota(member_dao, quota_dao, "exp_u")
            assert ctx["downgraded"] is False
        assert quota_dao.get_count("exp_u", cq._bj_day()) == 0

    def test_member_plan_free_explicit(self, daos):
        """plan='free' 显式成员 = 免费用户（走 15 条额度）。"""
        member_dao, quota_dao = daos
        ctx = cq.try_consume_chat_quota(member_dao, quota_dao, "any_u")
        assert ctx["is_member"] is False
        assert ctx["limit"] == CHAT_DAILY_LIMIT

    def test_status_contract(self, daos):
        """额度查询接口契约：{used, limit, downgraded, is_member}。"""
        member_dao, quota_dao = daos
        st = cq.chat_quota_status(member_dao, quota_dao, "new_u")
        assert st == {"used": 0, "limit": CHAT_DAILY_LIMIT,
                      "downgraded": False, "is_member": False}
        for _ in range(CHAT_DAILY_LIMIT + 3):
            cq.try_consume_chat_quota(member_dao, quota_dao, "new_u")
        st = cq.chat_quota_status(member_dao, quota_dao, "new_u")
        assert st["downgraded"] is True
        assert st["used"] == CHAT_DAILY_LIMIT

    def test_status_member(self, daos):
        member_dao, quota_dao = daos
        member_dao.create_membership("vip_u", "pro")
        st = cq.chat_quota_status(member_dao, quota_dao, "vip_u")
        assert st["is_member"] is True
        assert st["downgraded"] is False
        assert st["limit"] is None


# ───────────────────────── 降级链路：模型切换（GLM） ─────────────────────────

class _FakeResp:
    """httpx 响应替身（GLM OpenAI 兼容格式）。"""

    def __init__(self, content="今日简要回复", status=200):
        self._content = content
        self.status_code = status

    def json(self):
        return {"choices": [{"message": {"role": "assistant",
                                         "content": self._content}}],
                "finish_reason": "stop"}


class _FakeAnthropicResp:
    """httpx 响应替身（DeepSeek Anthropic 兼容格式）。"""

    def __init__(self, content="正常完整回复", status=200):
        self._content = content
        self.status_code = status

    def json(self):
        return {"content": [{"type": "text", "text": self._content}],
                "stop_reason": "end_turn"}


def _fake_post_by_url(calls, text_by_url=None):
    """按 URL 返回对应格式的 fake 响应（GLM→OpenAI 格式，DeepSeek→Anthropic 格式）。"""
    text_by_url = text_by_url or {}

    def fake_post(self, url, **kw):
        calls.append((url, kw))
        if url == GLM_COMPLETIONS_URL:
            return _FakeResp(text_by_url.get(url, "今日简要回复"))
        return _FakeAnthropicResp(text_by_url.get(url, "正常完整回复"))

    return fake_post


class TestDowngradeChain:
    def test_lite_calls_glm_endpoint(self, monkeypatch):
        """降级链路：lite=True 必须调 GLM 端点（URL/model/精简 prompt 三断言）。"""
        calls = []

        def fake_post(self, url, **kw):
            calls.append((url, kw))
            return _FakeResp()

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        reply = llm.chat_conversation(
            [{"role": "user", "content": "我最近财运如何"}], lite=True)

        assert reply == "今日简要回复"
        assert calls, "lite 路径必须发起 LLM HTTP 调用"
        url, kw = calls[0]
        assert url == GLM_COMPLETIONS_URL  # 智谱 OpenAI 兼容端点
        body = kw["json"]
        assert body["model"] == GLM_DEFAULT_MODEL  # glm-4-flash
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == CHAT_PROMPT_LITE  # 精简 prompt
        assert body["messages"][-1]["content"] == "我最近财运如何"
        assert "Authorization" in kw["headers"]
        assert kw["headers"]["Authorization"] == "Bearer zk-glm"

    def test_normal_chat_not_glm(self, monkeypatch):
        """非降级：正常对话仍走 DeepSeek Anthropic 端点（降级不破坏正常链路）。"""
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _fake_post_by_url(calls))
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        reply = llm.chat_conversation([{"role": "user", "content": "hi"}])
        assert reply == "正常完整回复"
        assert calls[0][0] == ANTHROPIC_MESSAGES_URL  # deepseek 端点
        assert calls[0][1]["json"]["model"].startswith("deepseek-flash")

    def test_lite_fallback_to_deepseek(self, monkeypatch):
        """GLM key 未配置/调用失败 → 回退 DeepSeek（同精简 prompt，绝不崩）。"""
        calls = []

        def fake_post(self, url, **kw):
            calls.append((url, kw))
            if url == GLM_COMPLETIONS_URL:
                raise httpx.ConnectError("zhipu down")  # GLM 挂
            return _FakeAnthropicResp("回退简要回复")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        reply = llm.chat_conversation(
            [{"role": "user", "content": "明天适合出行吗"}], lite=True)
        assert reply == "回退简要回复"
        assert len(calls) == 2
        fallback_url, fallback_kw = calls[1]
        assert fallback_url == ANTHROPIC_MESSAGES_URL
        assert fallback_kw["json"]["messages"][0]["content"] == CHAT_PROMPT_LITE

    def test_lite_no_glm_key_fallback(self, monkeypatch):
        """未配置 GLM key（含环境变量也无）：直接走 DeepSeek 精简 prompt。"""
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _fake_post_by_url(calls))
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="")
        llm.chat_conversation([{"role": "user", "content": "hi"}], lite=True)
        assert len(calls) == 1
        assert calls[0][0] == ANTHROPIC_MESSAGES_URL

    def test_lite_stream_uses_glm(self, monkeypatch):
        """降级流式：stream_cb 路径同样走 GLM 流式（mock，不真连外网）。"""
        seen = {}

        async def fake_stream(api_key, messages, **kw):
            seen["api_key"] = api_key
            seen["model"] = kw.get("model")
            seen["messages"] = messages
            yield "精简"
            yield "回复"

        monkeypatch.setattr(
            llm_client, "glm_openai_completion_stream", fake_stream)
        chunks = []
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        reply = llm.chat_conversation(
            [{"role": "user", "content": "hi"}],
            stream_cb=lambda t, p: chunks.append(p["text"]), lite=True)
        assert reply == "精简回复"
        assert chunks == ["精简", "回复"]
        assert seen["api_key"] == "zk-glm"
        assert seen["model"] == GLM_DEFAULT_MODEL
        assert seen["messages"][0]["content"] == CHAT_PROMPT_LITE

    def test_chat_single_lite(self, monkeypatch):
        """单消息模式（llm.chat）也支持降级。"""
        calls = []

        def fake_post(self, url, **kw):
            calls.append((url, kw))
            return _FakeResp("单条精简回复")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        result = llm.chat("你好", lite=True)
        assert result.response == "单条精简回复"
        assert calls[0][0] == GLM_COMPLETIONS_URL

    def test_lite_all_fail_graceful(self, monkeypatch):
        """GLM 与 DeepSeek 全挂：返回降级文案不抛出（对话不中断）。"""
        def fake_post(self, url, **kw):
            raise httpx.ConnectError("all down")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        reply = llm.chat_conversation(
            [{"role": "user", "content": "hi"}], lite=True)
        assert "额度" in reply  # 礼貌降级文案


# ───────────────────────── 额度查询接口（GET /api/user/chat-quota） ─────────────────────────

class TestChatQuotaEndpoint:
    def _client(self, member_dao, quota_dao, monkeypatch):
        """注入测试 DAO 到 main 全局 + JWT 鉴权（不跑 lifespan，仿 test_paipan_api）。"""
        import src.main as m
        from src.security.auth import set_auth_handler, AuthHandler, JWTHandler
        monkeypatch.setattr(m, "member_dao", member_dao)
        monkeypatch.setattr(m, "chat_quota_dao", quota_dao)
        set_auth_handler(AuthHandler())
        token = JWTHandler("test-secret-key-32-bytes-long!!").create_token("quota_u")
        from fastapi.testclient import TestClient
        return TestClient(m.app), {"Authorization": f"Bearer {token}"}

    def test_get_free_quota(self, daos, monkeypatch):
        member_dao, quota_dao = daos
        client, hdrs = self._client(member_dao, quota_dao, monkeypatch)
        r = client.get("/api/user/chat-quota", headers=hdrs)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_member"] is False
        assert body["used"] == 0
        assert body["limit"] == CHAT_DAILY_LIMIT
        assert body["downgraded"] is False

    def test_get_downgraded(self, daos, monkeypatch):
        member_dao, quota_dao = daos
        for _ in range(CHAT_DAILY_LIMIT):
            cq.try_consume_chat_quota(member_dao, quota_dao, "quota_u")
        client, hdrs = self._client(member_dao, quota_dao, monkeypatch)
        r = client.get("/api/user/chat-quota", headers=hdrs)
        assert r.status_code == 200, r.text
        assert r.json()["downgraded"] is True
        assert r.json()["used"] == CHAT_DAILY_LIMIT

    def test_get_member(self, daos, monkeypatch):
        member_dao, quota_dao = daos
        member_dao.create_membership("quota_u", "basic")
        client, hdrs = self._client(member_dao, quota_dao, monkeypatch)
        r = client.get("/api/user/chat-quota", headers=hdrs)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_member"] is True
        assert body["limit"] is None
        assert body["downgraded"] is False

    def test_get_requires_auth(self, daos, monkeypatch):
        member_dao, quota_dao = daos
        client, _ = self._client(member_dao, quota_dao, monkeypatch)
        r = client.get("/api/user/chat-quota")  # 无 token
        assert r.status_code == 401
