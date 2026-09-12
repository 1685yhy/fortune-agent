#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k39 S5 测试：openai_compat 机器人通道身份 = **由 API key 决定**。

演进（两批）：
- 改前（旧代码）：`handler.process(msg, "api_" + md5(当轮最后一条消息)[:12])`
  —— 同一句开场白的不同用户共用一个身份（串档面）；
- k39 S5（ccb5921）：md5 派生废弃，身份 = 调用方显式传入的 `user`，未传 →
  无状态应答。**但 `/v1/*` 复活后暴露出新面**：持任一 key 者可任意指定他人
  `user`（无绑定），且该通道无速率限制；
- k39 审查 I1（本批）：**身份由 key 决定，不由请求决定** —— key→user 绑定
  （`src/security/auth.resolve_key_user_bindings`）：
    ① key 未配置绑定 → 无状态应答 + **零写入**；
    ② 请求 `user` 与绑定身份不一致 → **403**（不得指定他人身份）；
    ③ 一致/不传 → 一律以**绑定身份**落库（归属正确）；
    ④ 鉴权**不放宽**（无 key 401 / 错 key 403 不变）。

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
KEY2 = "k39-test-api-key-2"
BOUND = "u_bound"      # KEY 绑定的身份（唯一可写入的身份）
BOUND2 = "u_bound2"    # KEY2 绑定的身份


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


def _install_auth(monkeypatch, api_keys, bindings=None):
    """装 AuthHandler：`api_keys` 形如 ["<key>:<name>"]，bindings 形如 {key: uid}。"""
    monkeypatch.setenv("API_KEYS", ",".join(api_keys))
    monkeypatch.delenv("FORTUNE_API_KEY", raising=False)
    monkeypatch.delenv("FORTUNE_API_KEY_USER", raising=False)
    if bindings:
        monkeypatch.setenv("FORTUNE_API_KEY_USERS",
                           ",".join(f"{k}:{v}" for k, v in bindings.items()))
    else:
        monkeypatch.delenv("FORTUNE_API_KEY_USERS", raising=False)
    from src.security.auth import AuthHandler, set_auth_handler
    set_auth_handler(AuthHandler())


@pytest.fixture
def env(tmp_path, monkeypatch):
    """已配置 key→user 绑定的环境（I1 后的正常形态）。"""
    _install_auth(monkeypatch, [f"{KEY}:bot"], {KEY: BOUND})
    import src.main as main_module
    from src.main import app
    headers = {"X-API-Key": KEY}
    return TestClient(app), headers, tmp_path, main_module


@pytest.fixture
def env_unbound(tmp_path, monkeypatch):
    """key 合法但**未配置任何绑定**的环境（必须零写入）。"""
    _install_auth(monkeypatch, [f"{KEY}:bot"], None)
    import src.main as main_module
    from src.main import app
    headers = {"X-API-Key": KEY}
    return TestClient(app), headers, tmp_path, main_module


@pytest.fixture
def env_two_keys(tmp_path, monkeypatch):
    """两把 key 各绑一个身份（"身份由 key 决定"的核心用例）。"""
    _install_auth(monkeypatch, [f"{KEY}:bot", f"{KEY2}:bot2"],
                  {KEY: BOUND, KEY2: BOUND2})
    import src.main as main_module
    from src.main import app
    return TestClient(app), tmp_path, main_module


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


def _rows(db):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT user_id, role FROM sessions ORDER BY id").fetchall()
    finally:
        con.close()


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
                              "user": BOUND},
                        headers={"Authorization": f"Bearer {KEY}"})
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "ok"
    finally:
        main_module.handler = None


# ================================================================
# 1. 身份由 key 决定 → 正常落库且归属正确
# ================================================================

def test_bound_key_lands_data_under_bound_identity(env):
    client, headers, tmp_path, main_module = env
    db = str(tmp_path / "s.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        r = _post_chat(client, headers, user=BOUND)
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == spy.reply
    finally:
        main_module.handler = None
    # handler 收到的就是绑定身份（没有任何派生/改写）
    assert spy.calls == [("1990年5月20日 15:30 北京 男，帮我排盘", BOUND)]
    # 落库归属：两行都挂在绑定身份名下
    assert _rows(db) == [(BOUND, "user"), (BOUND, "assistant")]


def test_bound_key_without_request_user_still_uses_bound_identity(env):
    """绑定后 `user` 可省——身份由 key 决定，不传也是绑定身份（不落匿名/共享身份）。"""
    client, headers, tmp_path, main_module = env
    db = str(tmp_path / "s1.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        assert _post_chat(client, headers).status_code == 200
    finally:
        main_module.handler = None
    assert spy.calls == [("1990年5月20日 15:30 北京 男，帮我排盘", BOUND)]
    assert _rows(db) == [(BOUND, "user"), (BOUND, "assistant")]


def test_two_keys_map_to_their_own_identities(env_two_keys):
    """两把 key 两个身份：谁也不能变成谁（改前"身份由请求决定"正是这个面）。"""
    client, tmp_path, main_module = env_two_keys
    db = str(tmp_path / "s2.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        assert _post_chat(client, {"X-API-Key": KEY}, user=BOUND).status_code == 200
        assert _post_chat(client, {"X-API-Key": KEY2},
                          user=BOUND2).status_code == 200
        # 交叉指定他人身份 → 403（两条方向都堵死）
        assert _post_chat(client, {"X-API-Key": KEY},
                          user=BOUND2).status_code == 403
        assert _post_chat(client, {"X-API-Key": KEY2},
                          user=BOUND).status_code == 403
    finally:
        main_module.handler = None
    assert spy.calls == [("1990年5月20日 15:30 北京 男，帮我排盘", BOUND),
                         ("1990年5月20日 15:30 北京 男，帮我排盘", BOUND2)]
    assert _rows(db) == [(BOUND, "user"), (BOUND, "assistant"),
                         (BOUND2, "user"), (BOUND2, "assistant")]


def test_request_user_mismatch_is_403_and_writes_nothing(env):
    """I1 核心：持 key 者不得用请求 `user` 指定他人身份（403 + 零写入）。"""
    client, headers, tmp_path, main_module = env
    db = str(tmp_path / "s3.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        r = _post_chat(client, headers, user="u_victim")
        assert r.status_code == 403
        assert "不一致" in r.json()["detail"]
        # completions 路由同口径
        r2 = _post_completions(client, headers, prompt="hi", user="u_victim")
        assert r2.status_code == 403
    finally:
        main_module.handler = None
    assert spy.calls == []          # 不进 handler
    assert _rows(db) == []          # 零写入


def test_user_id_is_normalized_before_comparison(env):
    """前后空白归一后与绑定一致 → 放行；归一后仍不等于绑定 → 403。"""
    client, _, _, main_module = env
    spy = _SpyHandler()
    main_module.handler = spy
    try:
        assert _post_chat(client, {"X-API-Key": KEY},
                          user=f"  {BOUND}  ").status_code == 200
        assert _post_chat(client, {"X-API-Key": KEY},
                          user=f" {BOUND}x ").status_code == 403
    finally:
        main_module.handler = None
    assert spy.calls == [("1990年5月20日 15:30 北京 男，帮我排盘", BOUND)]


def test_legacy_completions_route_uses_bound_identity(env):
    client, headers, _, main_module = env
    spy = _SpyHandler()
    main_module.handler = spy
    try:
        r = _post_completions(client, headers, prompt="帮我看看运势", user=BOUND)
        assert r.status_code == 200
    finally:
        main_module.handler = None
    assert spy.calls == [("帮我看看运势", BOUND)]


# ================================================================
# 2. key 未绑定 → 无状态：零写入、零跨用户共享
# ================================================================

def test_unbound_key_never_reaches_handler(tmp_path, env_unbound):
    """未配置绑定 → 不进 handler（因此没有任何落库机会），且如实告知原因。"""
    client, headers, _, main_module = env_unbound
    db = str(tmp_path / "s0.sqlite")
    spy = _SpyHandler(db_path=db)
    main_module.handler = spy
    try:
        r1 = _post_chat(client, headers, user="u_anyone")
        r2 = _post_chat(client, headers, user="u_anyone")
        r3 = _post_chat(client, headers)          # 连 user 都不传
        assert r1.status_code == r2.status_code == r3.status_code == 200
    finally:
        main_module.handler = None
    assert spy.calls == []          # 零调用 = 零写入 = 零跨用户共享
    assert _rows(db) == []
    import src.openai_compat as oc
    for r in (r1, r2, r3):
        body = r.json()["choices"][0]["message"]["content"]
        assert body == oc.UNBOUND_NOTICE
        assert "FORTUNE_API_KEY_USERS" in body   # 告诉控制方怎么配


def test_stateless_reply_is_deterministic_and_shared_nothing(tmp_path,
                                                             env_unbound):
    """两个同开场白的调用方各自拿到同一份**契约说明**（不含任何用户数据）。"""
    import src.openai_compat as oc
    client, headers, _, main_module = env_unbound
    main_module.handler = _SpyHandler(reply="个性化回复")
    try:
        r1 = _post_chat(client, headers, content="你好，我的运势怎么样")
        r2 = _post_chat(client, headers, content="你好，我的运势怎么样")
    finally:
        main_module.handler = None
    t1 = r1.json()["choices"][0]["message"]["content"]
    t2 = r2.json()["choices"][0]["message"]["content"]
    assert t1 == t2 == oc.UNBOUND_NOTICE
    assert "个性化回复" not in t1  # 绝不串到别人的回复
    assert "user" in t1  # 明确告知身份来自密钥绑定


def test_md5_derived_identity_is_gone():
    """废弃 md5 派生身份：模块源码里不得再出现 hashlib / md5 调用 / api_ 前缀派生。"""
    src = (_REPO / "src" / "openai_compat.py").read_text(encoding="utf-8")
    assert "hashlib" not in src
    assert ".md5(" not in src
    assert '"api_" +' not in src
    assert '"api_" ' not in src


def test_two_unbound_callers_with_same_opening_line_do_not_collide(tmp_path,
                                                                   env_unbound):
    """改前的串档面：同开场白 A/B 两用户共用 `api_<md5>` 身份。改后零共享。"""
    import src.openai_compat as oc
    client, headers, _, main_module = env_unbound
    db = str(tmp_path / "s2.sqlite")
    spy = _SpyHandler(db_path=db, reply="A 的回复")
    main_module.handler = spy
    try:
        _post_chat(client, headers, content="你好")
    finally:
        main_module.handler = None
    assert spy.calls == []
    # 旧口径下这里会建出 `api_<md5("你好")>` 的单一身份；现在一条都没有
    assert _rows(db) == []
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
# 2b. 绑定配置解析（唯一事实源：环境变量）
# ================================================================

def test_binding_config_parsing():
    """多 key 映射 + 单 key 绑定 + 非法条目跳过（绝不做模糊匹配）。"""
    from src.security.auth import (BINDINGS_ENV, SINGLE_BINDING_ENV,
                                   resolve_key_user_bindings)
    got = resolve_key_user_bindings({
        BINDINGS_ENV: "k1:u1, k2:u2 ,broken,k3:",
    })
    assert got == {"k1": "u1", "k2": "u2"}
    # 单 key 形态：FORTUNE_API_KEY_USER 绑 FORTUNE_API_KEY 那把 key
    got = resolve_key_user_bindings({
        SINGLE_BINDING_ENV: "u_solo", "FORTUNE_API_KEY": "solo-key"})
    assert got == {"solo-key": "u_solo"}
    # 单 key 变量存在但 FORTUNE_API_KEY 未配 → 不生效（不猜、不绑全部 key）
    assert resolve_key_user_bindings({SINGLE_BINDING_ENV: "u_solo"}) == {}
    # 两条都配 → 多 key 映射优先/并存
    got = resolve_key_user_bindings({
        BINDINGS_ENV: "k1:u1", SINGLE_BINDING_ENV: "u_solo",
        "FORTUNE_API_KEY": "solo-key"})
    assert got == {"k1": "u1", "solo-key": "u_solo"}
    # 空/未配置 → 空（该通道零写入，而不是"随便放行"）
    assert resolve_key_user_bindings({}) == {}


def test_binding_only_applies_to_configured_keys():
    """绑定了不存在的 key → 不新建 key（不给未配置的 key 开后门）。"""
    from src.security.auth import AuthHandler
    os.environ["API_KEYS"] = f"{KEY}:bot"
    os.environ["FORTUNE_API_KEY_USERS"] = "not-a-configured-key:u_x"
    try:
        auth = AuthHandler()
        assert auth.validate_api_key("not-a-configured-key") is None
        assert auth.bound_user_for_key("not-a-configured-key") == ""
        assert auth.bound_user_for_key(KEY) == ""      # 未绑定的既有 key → 无身份
    finally:
        os.environ.pop("FORTUNE_API_KEY_USERS", None)
        os.environ.pop("API_KEYS", None)


# ================================================================
# 3. 既有合规调用方（绑定身份）行为不变
# ================================================================

def test_tool_tag_residue_still_stripped_for_bound_user(env):
    client, headers, _, main_module = env
    main_module.handler = _SpyHandler(reply='正文内容<tool_calls>{"a":1}</tool_calls>')
    try:
        r = _post_chat(client, headers, user=BOUND)
    finally:
        main_module.handler = None
    body = r.json()["choices"][0]["message"]["content"]
    assert "tool_calls" not in body and "正文内容" in body


def test_handler_exception_still_returns_error_text_for_bound_user(env):
    client, headers, _, main_module = env

    class _Boom:
        def process(self, *a, **kw):
            raise RuntimeError("boom")

    main_module.handler = _Boom()
    try:
        r = _post_chat(client, headers, user=BOUND)
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
                              "user": BOUND, "stream": True},
                        headers=headers)
        assert r.status_code == 200
    finally:
        main_module.handler = None
    assert spy.calls == [("hi", BOUND)]


def test_missing_user_message_is_still_400(env):
    """空消息仍是 400（既有契约不变），且不进 handler。"""
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
    r = _post_chat(client, headers, user=BOUND)
    assert r.status_code == 503
