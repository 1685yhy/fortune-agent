#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k41：受信多用户键（显式 opt-in）——`FORTUNE_API_KEY_USERS="<key>:*"`。

需求 SSOT = `.superpowers/sdd/task-k41-brief.md` §3（控制方拍板·保守 opt-in）

背景：`scripts/cow_multi_user.patch` 给外部系统（CoW bot）加了
`user=session.session_id`（每会话一个用户），而 k39 的契约是**身份由 key 决定**
（key→user 绑定）→ 二者不兼容（该补丁的请求会被 403 或无状态化）。

本批口径：**默认仍是严格绑定**；只有把绑定写成 `key:*` 才启用「该 key 允许
请求自带 user_id」（仅限服务端受信集成）。启用时启动日志大声提示（key 打掩码）。

双向用例：
  ① 默认（无 `:*`）：请求自带 user → 403 / 零写入（与现在逐字节一致）；
  ② `:*`：自带 user 生效且归档归属正确；未传 user → 仍无状态零写入；
  ③ 自带 user 非法（字符集/长度）→ 400 + 零写入；
  ④ 回退：去掉 `:*` 即回到严格绑定（同一条用例 ①）。
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

KEY = "k41-trusted-multi-key"      # 受信多用户键
KEY_STRICT = "k41-strict-key"      # 严格绑定键（默认形态）
BOUND = "u_k41_bound"


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


def _install_auth(monkeypatch, api_keys, bindings):
    monkeypatch.setenv("API_KEYS", ",".join(api_keys))
    monkeypatch.delenv("FORTUNE_API_KEY", raising=False)
    monkeypatch.delenv("FORTUNE_API_KEY_USER", raising=False)
    if bindings:
        monkeypatch.setenv(
            "FORTUNE_API_KEY_USERS",
            ",".join(f"{k}:{v}" for k, v in bindings.items()))
    else:
        monkeypatch.delenv("FORTUNE_API_KEY_USERS", raising=False)
    from src.security.auth import AuthHandler, set_auth_handler
    set_auth_handler(AuthHandler())


@pytest.fixture
def env_trusted(tmp_path, monkeypatch):
    """受信多用户键环境：KEY 写 `:*`，KEY_STRICT 保持严格绑定（对照组）。"""
    _install_auth(monkeypatch, [f"{KEY}:bot", f"{KEY_STRICT}:bot2"],
                  {KEY: "*", KEY_STRICT: BOUND})
    import src.main as main_module
    from src.main import app
    return TestClient(app), tmp_path, main_module


def _post_chat(client, headers, content="你好", user=None):
    body = {"messages": [{"role": "user", "content": content}]}
    if user is not None:
        body["user"] = user
    return client.post("/v1/chat/completions", json=body, headers=headers)


def _rows(db):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT user_id, role FROM sessions ORDER BY id").fetchall()
    finally:
        con.close()


# ================================================================
# 一、配置解析（唯一事实源）：`:*` 是显式 marker，非法/歧义一律跳过
# ================================================================

def test_wildcard_marker_parsed_verbatim():
    from src.security.auth import (BINDINGS_ENV, SINGLE_BINDING_ENV,
                                   resolve_key_user_bindings)
    got = resolve_key_user_bindings({
        BINDINGS_ENV: "k1:*, k2:u2 ,broken,k3:"})
    assert got == {"k1": "*", "k2": "u2"}


def test_single_env_never_enables_multi_user():
    """`FORTUNE_API_KEY_USER=*` **不得**被读成多用户开关（该变量是「绑一个用户」，
    歧义配置宁可跳过也不放开——改前它会把身份绑成字面 `*`）。"""
    from src.security.auth import SINGLE_BINDING_ENV, resolve_key_user_bindings
    assert resolve_key_user_bindings(
        {SINGLE_BINDING_ENV: "*", "FORTUNE_API_KEY": "solo-key"}) == {}


def test_startup_log_is_loud_and_key_is_masked(caplog):
    """启用 `:*` → 启动日志有大写警示 + key 掩码（绝不打全 key）。"""
    import logging
    os.environ["API_KEYS"] = f"{KEY}:bot"
    os.environ["FORTUNE_API_KEY_USERS"] = f"{KEY}:*"
    try:
        from src.security.auth import AuthHandler
        with caplog.at_level(logging.WARNING):
            auth = AuthHandler()
        assert auth.validate_api_key(KEY) is not None
        text = "\n".join(r.message for r in caplog.records)
        assert KEY not in text, "禁止明文打印完整 key"
        assert "多用户" in text or "user=*" in text
        assert "*" in text
    finally:
        os.environ.pop("FORTUNE_API_KEY_USERS", None)
        os.environ.pop("API_KEYS", None)


def test_wildcard_key_has_no_single_identity():
    """受信多用户键没有「唯一身份」→ `bound_user_for_key` 返回空（限流退 IP 档）。"""
    os.environ["API_KEYS"] = f"{KEY}:bot"
    os.environ["FORTUNE_API_KEY_USERS"] = f"{KEY}:*"
    try:
        from src.security.auth import AuthHandler
        auth = AuthHandler()
        assert auth.bound_user_for_key(KEY) == ""
        assert auth.validate_api_key(KEY) is not None
    finally:
        os.environ.pop("FORTUNE_API_KEY_USERS", None)
        os.environ.pop("API_KEYS", None)


def test_resolve_request_identity_unit(monkeypatch):
    """裁决纯函数（单一实现）：wildcard 放行自带 user / 缺失仍 unbound。"""
    from src.security.auth import AuthHandler, set_auth_handler
    monkeypatch.setenv("API_KEYS", f"{KEY}:bot")
    monkeypatch.setenv("FORTUNE_API_KEY_USERS", f"{KEY}:*")
    set_auth_handler(AuthHandler())
    import src.openai_compat as oc
    assert oc.resolve_request_identity(KEY, "u_alice") == ("u_alice", "")
    assert oc.resolve_request_identity(KEY, "  u_alice  ") == ("u_alice", "")
    assert oc.resolve_request_identity(KEY, None) == ("", "unbound")
    assert oc.resolve_request_identity(KEY, "") == ("", "unbound")


# ================================================================
# 二、端到端：自带 user 生效且归档归属正确
# ================================================================

def test_trusted_key_accepts_self_carried_users(env_trusted):
    client, tmp_path, main_module = env_trusted
    db = str(tmp_path / "multi.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        assert _post_chat(client, {"X-API-Key": KEY}, user="u_alice",
                          content="alice 的问题").status_code == 200
        assert _post_chat(client, {"X-API-Key": KEY}, user="u_bob",
                          content="bob 的问题").status_code == 200
    finally:
        main_module.handler = None
    # 两个会话各自成档（多用户隔离 = CoW 每会话一个用户）
    assert spy.calls == [("alice 的问题", "u_alice"), ("bob 的问题", "u_bob")]
    assert _rows(db) == [("u_alice", "user"), ("u_alice", "assistant"),
                         ("u_bob", "user"), ("u_bob", "assistant")]


def test_trusted_key_without_user_is_still_zero_write(env_trusted):
    """未传 user → 无状态应答（零写入）——不放宽「无身份 = 无状态」契约。"""
    import src.openai_compat as oc
    client, tmp_path, main_module = env_trusted
    db = str(tmp_path / "nouser.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        r = _post_chat(client, {"X-API-Key": KEY})
        assert r.status_code == 200
    finally:
        main_module.handler = None
    assert r.json()["choices"][0]["message"]["content"] == oc.UNBOUND_NOTICE
    assert spy.calls == []
    assert _rows(db) == []


@pytest.mark.parametrize("bad", ["u bad", "u/bad", "u;bad", "u\tbad",
                                 "u'bad", "a" * 129])
def test_trusted_key_rejects_illegal_user(env_trusted, bad):
    """自带 user 仍过基本校验（非空/字符集/长度上限）；非法 → 400 + 零写入。"""
    client, tmp_path, main_module = env_trusted
    db = str(tmp_path / "bad.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        r = _post_chat(client, {"X-API-Key": KEY}, user=bad)
        assert r.status_code == 400, r.text
    finally:
        main_module.handler = None
    assert spy.calls == []
    assert _rows(db) == []


def test_trusted_key_length_cap_is_documented_on_identity(env_trusted):
    """长度上限：128 位恰好放行；第 129 位起**拒绝**（不静默截断，防串档）。"""
    client, tmp_path, main_module = env_trusted
    spy = _SpyHandler()
    main_module.handler = spy
    try:
        exact = "u" + "y" * 127          # 恰好 128
        assert _post_chat(client, {"X-API-Key": KEY},
                          user=exact).status_code == 200
        assert _post_chat(client, {"X-API-Key": KEY},
                          user=exact + "z").status_code == 400
    finally:
        main_module.handler = None
    assert [c[1] for c in spy.calls] == [exact]   # 129 位那条零调用


def test_trusted_key_legacy_completions_route(env_trusted):
    client, _, main_module = env_trusted
    spy = _SpyHandler()
    main_module.handler = spy
    try:
        r = client.post("/v1/completions",
                        json={"prompt": "hi", "user": "u_carol"},
                        headers={"X-API-Key": KEY})
        assert r.status_code == 200
    finally:
        main_module.handler = None
    assert spy.calls == [("hi", "u_carol")]


# ================================================================
# 三、默认严格绑定零变化（双向锁 / 回退口径）
# ================================================================

def test_strict_key_still_403_on_self_carried_user(env_trusted):
    """对照组的严格键：自带 user ≠ 绑定身份 → 403 + 零写入（**默认行为不变**）。"""
    client, tmp_path, main_module = env_trusted
    db = str(tmp_path / "strict.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        assert _post_chat(client, {"X-API-Key": KEY_STRICT},
                          user="u_victim").status_code == 403
        # 与绑定一致 / 不传 → 照旧放行（身份 = 绑定身份）
        assert _post_chat(client, {"X-API-Key": KEY_STRICT},
                          user=BOUND).status_code == 200
        assert _post_chat(client, {"X-API-Key": KEY_STRICT}).status_code == 200
    finally:
        main_module.handler = None
    assert spy.calls == [("你好", BOUND), ("你好", BOUND)]
    assert _rows(db) == [(BOUND, "user"), (BOUND, "assistant"),
                         (BOUND, "user"), (BOUND, "assistant")]


def test_no_wildcard_config_behaves_exactly_as_before(monkeypatch, tmp_path):
    """回退口径：去掉 `:*` 即回到严格绑定（同一条请求：200→403）。"""
    client, tmp_path, main_module = _client_with(monkeypatch, tmp_path,
                                                 wildcard=False)
    main_module.handler = _SpyHandler()
    try:
        assert _post_chat(client, {"X-API-Key": KEY},
                          user="u_alice").status_code == 403
    finally:
        main_module.handler = None
    # 换成 `:*` → 同一请求放行
    client2, _, main_module2 = _client_with(monkeypatch, tmp_path,
                                            wildcard=True)
    spy = _SpyHandler()
    main_module2.handler = spy
    try:
        assert _post_chat(client2, {"X-API-Key": KEY},
                          user="u_alice").status_code == 200
    finally:
        main_module2.handler = None
    assert spy.calls == [("你好", "u_alice")]


def _client_with(monkeypatch, tmp_path, wildcard: bool):
    _install_auth(monkeypatch, [f"{KEY}:bot"],
                  {KEY: "*"} if wildcard else {KEY: BOUND})
    import src.main as main_module
    from src.main import app
    return TestClient(app), tmp_path, main_module
