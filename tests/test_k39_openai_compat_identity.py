#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k39 S5 测试：openai_compat 机器人通道身份 = 强制显式用户标识。

改前（旧代码）：`handler.process(msg, "api_" + md5(当轮最后一条消息)[:12])`
—— 同一句开场白的不同用户共用一个身份（串档面）。
改后：只认调用方显式传入的 `user`；未传 → 无状态应答（不建档、零写入、
零跨用户共享），md5 派生身份废弃；`/v1/*` 鉴权不放宽。

覆盖（brief S5 的四个要求）：
1. 传标识 → 正常落库**且归属正确**（sessions.user_id 就是传进来的标识）；
2. 不传 → **零写入**（真实临时库行数不变）**零跨用户共享**（两次同开场白
   的匿名调用不产生任何身份/行，也不进 handler）；
3. 鉴权不放宽（无 key 401 / 错 key 403，两条 POST 路由都在）；
4. 既有合规调用方行为不变（回复仍是 handler 的回复 + strip_tool_calls 兜底；
   异常仍走 "⚠️ 处理出错"）。

红线：本文件只写 tmp_path 下的临时库（绝不碰生产库）；零 LLM 调用
（handler 用桩替换，不触发真实主链）。
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

_REPO = Path(__file__).resolve().parent.parent
for _p in (_REPO,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

KEY = "k39-test-api-key"


class _SpyHandler:
    """记录 process 调用；可选把「用户轮 + 回复轮」写进真实临时库。"""

    def __init__(self, db_path=None, reply="好的，这是回复"):
        self.calls = []
        self.db_path = db_path
        self.reply = reply
        self._dao = None
        if db_path:
            from src.storage.session_dao import SessionDAO
            self._dao = SessionDAO(db_path)

    def process(self, message, user_id, **kw):
        self.calls.append((message, user_id))
        if self._dao is not None:
            self._dao.add_message(user_id, "user", message)
            self._dao.add_message(user_id, "assistant", self.reply)
        return self.reply


@pytest.fixture
def env(tmp_path, monkeypatch):
    """显式 API key 环境 + 可注入 handler 的 TestClient。"""
    monkeypatch.setenv("API_KEYS", KEY)
    from src.security.auth import AuthHandler, set_auth_handler
    set_auth_handler(AuthHandler())
    import src.main as main_module
    from src.main import app
    headers = {"X-API-Key": KEY}
    return TestClient(app), headers, tmp_path, main_module


def _post_chat(client, headers, content="1990年5月20日 15:30 北京 男，帮我排盘",
               user=None):
    body = {"messages": [{"role": "user", "content": content}]}
    if user is not None:
        body["user"] = user
    return client.post("/v1/chat/completions", json=body, headers=headers)


def _post_completions(client, headers, prompt="你好", user=None):
    body = {"prompt": prompt}
    if user is not None:
        body["user"] = user
    return client.post("/v1/completions", json=body, headers=headers)


# ================================================================
# 0. 鉴权不放宽
# ================================================================

def test_v1_requires_api_key_on_both_post_routes(env):
    client, _, _, _ = env
    r = client.post("/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401
    r = client.post("/v1/completions", json={"prompt": "hi"})
    assert r.status_code == 401


def test_v1_rejects_wrong_api_key(env):
    client, _, _, _ = env
    bad = {"X-API-Key": "wrong-key"}
    assert _post_chat(client, bad).status_code == 403
    assert _post_completions(client, bad).status_code == 403


def test_bearer_api_key_still_accepted(env):
    """既有合规调用方（Authorization: Bearer <api_key>）不受影响。"""
    client, _, _, main_module = env
    main_module.handler = _SpyHandler(reply="ok")
    try:
        r = client.post("/v1/chat/completions",
                        json={"messages": [{"role": "user", "content": "hi"}],
                              "user": "u_bearer"},
                        headers={"Authorization": f"Bearer {KEY}"})
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "ok"
    finally:
        main_module.handler = None


# ================================================================
# 1. 传标识 → 正常落库且归属正确
# ================================================================

def test_explicit_user_is_used_verbatim_and_data_lands_under_it(env):
    client, headers, tmp_path, main_module = env
    db = str(tmp_path / "s.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        r = _post_chat(client, headers, user="u_alice")
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == spy.reply
    finally:
        main_module.handler = None
    # handler 收到的就是显式标识（没有任何派生/改写）
    assert spy.calls == [("1990年5月20日 15:30 北京 男，帮我排盘", "u_alice")]
    # 落库归属：两行都挂在 u_alice 名下
    con = sqlite3.connect(db)
    rows = con.execute("SELECT user_id, role FROM sessions ORDER BY id").fetchall()
    con.close()
    assert rows == [("u_alice", "user"), ("u_alice", "assistant")]


def test_user_id_is_stripped_not_derived(env):
    """前后空白归一为显式标识本体；不做任何内容派生。"""
    client, headers, _, main_module = env
    spy = _SpyHandler()
    main_module.handler = spy
    try:
        _post_chat(client, headers, user="  u_bob  ")
    finally:
        main_module.handler = None
    assert spy.calls == [("1990年5月20日 15:30 北京 男，帮我排盘", "u_bob")]


def test_legacy_completions_route_uses_explicit_user(env):
    client, headers, _, main_module = env
    spy = _SpyHandler()
    main_module.handler = spy
    try:
        r = _post_completions(client, headers, prompt="帮我看看运势", user="u_carol")
        assert r.status_code == 200
    finally:
        main_module.handler = None
    assert spy.calls == [("帮我看看运势", "u_carol")]


# ================================================================
# 2. 不传 → 无状态：零写入、零跨用户共享
# ================================================================

def test_missing_user_never_reaches_handler(tmp_path, env):
    """未传 user → 不进 handler（因此不存在任何派生身份，也没有落库机会）。"""
    client, headers, _, main_module = env
    db = str(tmp_path / "s0.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        r1 = _post_chat(client, headers)
        r2 = _post_chat(client, headers)  # 同一句开场白的第二个调用方
        assert r1.status_code == r2.status_code == 200
    finally:
        main_module.handler = None
    assert spy.calls == []  # 零调用 = 零派生身份 = 零落库
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    con.close()
    assert n == 0


def test_stateless_reply_is_deterministic_and_shared_nothing(tmp_path, env):
    """两个同开场白的匿名调用方各自拿到同一份**契约说明**（不含任何用户数据）。"""
    import src.openai_compat as oc
    client, headers, _, main_module = env
    main_module.handler = _SpyHandler(reply="个性化回复")
    try:
        r1 = _post_chat(client, headers, content="你好，我的运势怎么样")
        r2 = _post_chat(client, headers, content="你好，我的运势怎么样")
    finally:
        main_module.handler = None
    t1 = r1.json()["choices"][0]["message"]["content"]
    t2 = r2.json()["choices"][0]["message"]["content"]
    assert t1 == t2 == oc.STATELESS_NOTICE
    assert "个性化回复" not in t1  # 绝不串到别人的回复
    assert "user" in t1  # 明确告知要传 `user`


def test_md5_derived_identity_is_gone():
    """废弃 md5 派生身份：模块源码里不得再出现 hashlib / md5 调用 / api_ 前缀派生。"""
    src = (_REPO / "src" / "openai_compat.py").read_text(encoding="utf-8")
    assert "hashlib" not in src
    assert ".md5(" not in src
    assert '"api_" +' not in src
    assert '"api_" ' not in src


def test_two_anonymous_callers_with_same_opening_line_do_not_collide(tmp_path, env):
    """改前的串档面：同开场白 A/B 两用户共用 `api_<md5>` 身份。改后零共享。"""
    import src.openai_compat as oc
    client, headers, _, main_module = env
    db = str(tmp_path / "s2.sqlite")
    spy = _SpyHandler(db_path=db, reply="A 的回复")
    main_module.handler = spy
    try:
        _post_chat(client, headers, content="你好")
    finally:
        main_module.handler = None
    assert spy.calls == []
    # 旧口径下这里会建出 `api_<md5("你好")>` 的单一身份；现在一条都没有
    con = sqlite3.connect(db)
    ids = [r[0] for r in con.execute("SELECT DISTINCT user_id FROM sessions")]
    con.close()
    assert ids == []
    assert oc.normalize_user(None) == "" and oc.normalize_user("  ") == ""


def test_normalize_user_truncates_overlong_identifier():
    """超长标识截断（防御：不落库、不撑爆日志）；不改写正常标识。"""
    import src.openai_compat as oc
    assert oc.USER_ID_MAX_LEN == 128
    assert oc.normalize_user("x" * 500) == "x" * oc.USER_ID_MAX_LEN
    _exact = "u_" + "y" * 126  # 恰好 128 位：不截断
    assert len(_exact) == oc.USER_ID_MAX_LEN
    assert oc.normalize_user(_exact) == _exact
    assert oc.normalize_user(_exact + "z") == _exact  # 第 129 位起丢弃


# ================================================================
# 3. 既有合规调用方（传了标识）行为不变
# ================================================================

def test_tool_tag_residue_still_stripped_for_named_user(env):
    client, headers, _, main_module = env
    main_module.handler = _SpyHandler(reply='正文内容<tool_calls>{"a":1}</tool_calls>')
    try:
        r = _post_chat(client, headers, user="u_dave")
    finally:
        main_module.handler = None
    body = r.json()["choices"][0]["message"]["content"]
    assert "tool_calls" not in body and "正文内容" in body


def test_handler_exception_still_returns_error_text_for_named_user(env):
    client, headers, _, main_module = env

    class _Boom:
        def process(self, *a, **kw):
            raise RuntimeError("boom")

    main_module.handler = _Boom()
    try:
        r = _post_chat(client, headers, user="u_erin")
    finally:
        main_module.handler = None
    assert r.status_code == 200
    assert "处理出错" in r.json()["choices"][0]["message"]["content"]


def test_stream_flag_does_not_change_contract(env):
    """stream=true 目前仍返回非流式 body（既有行为，本次不动）。"""
    client, headers, _, main_module = env
    spy = _SpyHandler(reply="流式回复")
    main_module.handler = spy
    try:
        r = client.post("/v1/chat/completions",
                        json={"messages": [{"role": "user", "content": "hi"}],
                              "user": "u_frank", "stream": True},
                        headers=headers)
        assert r.status_code == 200
    finally:
        main_module.handler = None
    assert spy.calls == [("hi", "u_frank")]


def test_missing_user_message_is_still_400(env):
    """空消息仍是 400（既有契约不变）。"""
    client, headers, _, main_module = env
    spy = _SpyHandler()
    main_module.handler = spy
    try:
        r = client.post("/v1/chat/completions", json={"messages": []},
                        headers=headers)
        assert r.status_code == 400
    finally:
        main_module.handler = None
    assert spy.calls == []


def test_503_when_handler_not_ready(env):
    """主链未就绪仍是 503（既有契约不变）。"""
    client, headers, _, main_module = env
    main_module.handler = None
    r = _post_chat(client, headers, user="u_g")
    assert r.status_code == 503
