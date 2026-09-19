# -*- coding: utf-8 -*-
"""k61：LLM 出站守卫自检（守卫必须能被全量套件自然触发，且真的有牙）。

本文件只验证 `tests/conftest.py::_k61_deepseek_egress_guard` 本身：
1. 三个拦截层（socket.getaddrinfo / socket.create_connection /
   httpx 真实传输层）对 deepseek 域一律拦截；
2. 非 deepseek 主机透传（零行为差异——不得把别的网络用例连带打死）；
3. 免费源白名单（open.bigmodel.cn）必须放行；
4. `httpx.MockTransport`（固定请求形状的标准做法）不受守卫影响；
5. 守卫失败**不可被 `except Exception` 吞掉**（BaseException 语义）。

运行：TMPDIR=/dev/shm OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 \
      -m pytest tests/test_k61_llm_egress_guard.py -q
"""
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import pytest  # noqa: E402

# tests/ 无 __init__.py → pytest 以顶层名 `conftest` 加载本目录的 conftest，
# 同名导入才能拿到**同一个模块实例**（同 `_GUARD`），不得写成 `tests.conftest`
# （那会另起一份模块副本，守卫状态就对不上了）。
import conftest as k61conftest  # noqa: E402
from conftest import (  # noqa: E402
    BLOCKED_HOST_MARKERS,
    FREE_LLM_HOSTS,
    FREE_LLM_MODEL,
    DeepSeekEgressBlocked,
)


@pytest.fixture(autouse=True)
def _drain_self_inflicted_violations(_k61_deepseek_egress_guard):
    """本文件是**故意**让守卫响的自证文件：收尾时弹掉自己制造的那几条违规，
    免得污染 session 收尾的「未经授权出站」判定（那个判定是给别的用例用的）。"""
    guard = _k61_deepseek_egress_guard
    before = len(guard.violations)
    yield
    del guard.violations[before:]


# ══════════════════════════════════════════════════════════════════
# 1) 常量契约
# ══════════════════════════════════════════════════════════════════

class TestGuardContract:
    def test_blocked_markers_cover_deepseek(self):
        assert "deepseek" in BLOCKED_HOST_MARKERS
        for host in ("api.deepseek.com", "API.DEEPSEEK.COM", "chat.deepseek.com"):
            assert k61conftest._is_blocked(host), host

    def test_free_whitelist_is_free_only(self):
        """白名单只放免费源：不得含 deepseek，且当前只有智谱。"""
        assert FREE_LLM_HOSTS == ("open.bigmodel.cn",)
        for h in FREE_LLM_HOSTS:
            assert not any(m in h for m in BLOCKED_HOST_MARKERS), h
        assert FREE_LLM_MODEL == "glm-4-flash"

    def test_is_blocked_passthrough(self):
        for host in ("open.bigmodel.cn", "cn.bing.com", "127.0.0.1", None, ""):
            assert not k61conftest._is_blocked(host), host


# ══════════════════════════════════════════════════════════════════
# 2) 三个拦截层真的拦得住（守卫的牙）
# ══════════════════════════════════════════════════════════════════

class TestGuardLayers:
    def test_socket_getaddrinfo_blocked(self):
        with pytest.raises(DeepSeekEgressBlocked) as ei:
            socket.getaddrinfo("api.deepseek.com", 443)
        assert "api.deepseek.com" in str(ei.value)

    def test_socket_create_connection_blocked(self):
        with pytest.raises(DeepSeekEgressBlocked):
            socket.create_connection(("api.deepseek.com", 443), timeout=0.1)

    def test_httpx_sync_real_transport_blocked(self):
        """真实传输层（HTTPTransport）必须被拦——这是"会真开 socket"的那层。"""
        with pytest.raises(DeepSeekEgressBlocked):
            httpx.post("https://api.deepseek.com/anthropic/v1/messages",
                       json={"x": 1}, timeout=0.1)

    def test_httpx_async_real_transport_blocked(self):
        import asyncio

        async def _go():
            async with httpx.AsyncClient(timeout=0.1) as c:
                await c.post("https://api.deepseek.com/anthropic/v1/messages",
                             json={"x": 1})

        with pytest.raises(DeepSeekEgressBlocked):
            asyncio.run(_go())

    def test_library_level_call_blocked(self):
        """端到端形态：走生产同款函数（不受任何 mock）→ 必须被拦。"""
        import src.llm.client as llm_client
        with pytest.raises(DeepSeekEgressBlocked):
            llm_client.deepseek_anthropic_completion(
                "sk-not-a-real-key",
                [{"role": "user", "content": "ping"}],
                timeout=0.1)

    def test_urllib_blocked(self):
        """非 httpx 路径（标准库 urllib → http.client → socket）同样拦得住。"""
        import urllib.request
        with pytest.raises(DeepSeekEgressBlocked):
            urllib.request.urlopen("https://api.deepseek.com/", timeout=0.1)


# ══════════════════════════════════════════════════════════════════
# 3) 不能误伤：透传 / 白名单 / mock transport
# ══════════════════════════════════════════════════════════════════

class TestGuardDoesNotBreakLegitimateUse:
    def test_mock_transport_unaffected(self):
        """固定请求形状的标准做法（MockTransport）必须照常工作。"""
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as client:
            r = client.post("https://api.deepseek.com/anthropic/v1/messages",
                            json={"model": "deepseek-flash"})
        assert r.status_code == 200
        assert seen["url"] == "https://api.deepseek.com/anthropic/v1/messages"

    def test_free_whitelist_not_blocked_at_guard_layers(self):
        """白名单源不得被守卫拦（守卫拦的是付费生产域，不是免费源）。"""
        for h in FREE_LLM_HOSTS:
            assert not k61conftest._is_blocked(h), h

    def test_non_deepseek_host_not_blocked_by_httpx_layer(self, monkeypatch):
        """非 deepseek 主机：httpx 层必须透传（用 mock transport 之外的探针验证）。"""
        called = {}

        real = httpx.HTTPTransport.handle_request

        def spy(self, request):
            called["host"] = request.url.host
            raise httpx.ConnectError("stop-before-socket")

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", spy)
        with pytest.raises(httpx.ConnectError):
            httpx.get("https://open.bigmodel.cn/api/paas/v4/chat/completions",
                      timeout=0.1)
        assert called["host"] == "open.bigmodel.cn"

    def test_socket_getaddrinfo_passthrough_for_other_hosts(self):
        """非 deepseek 主机走原生解析（本地回环必定可解析，不依赖外网）。"""
        infos = socket.getaddrinfo("localhost", 80)
        assert infos

    def test_guard_installed_in_this_session(self, _k61_deepseek_egress_guard):
        """守卫必须是 autouse：本用例只声明夹具、不做安装动作——拿到即已生效。"""
        guard = _k61_deepseek_egress_guard
        assert guard is k61conftest._GUARD, "夹具与模块不是同一个守卫实例"
        assert guard._orig_getaddrinfo is not None, "守卫未安装（autouse 失效）"


# ══════════════════════════════════════════════════════════════════
# 4) 失败不可被兜底吞掉
# ══════════════════════════════════════════════════════════════════

class TestGuardIsNotSwallowable:
    def test_except_exception_cannot_swallow(self):
        """被测代码普遍 `except Exception:` 兜底——守卫必须穿过去。"""
        swallowed = None
        try:
            try:
                socket.getaddrinfo("api.deepseek.com", 443)
            except Exception as e:  # noqa: BLE001 - 故意模拟生产兜底写法
                swallowed = e
        except DeepSeekEgressBlocked:
            pass
        assert swallowed is None, f"守卫被 except Exception 吞掉了: {swallowed!r}"

    def test_is_baseexception_not_exception(self):
        assert issubclass(DeepSeekEgressBlocked, BaseException)
        assert not issubclass(DeepSeekEgressBlocked, Exception)

    def test_violation_recorded(self, _k61_deepseek_egress_guard):
        """即使异常被吞，违规记录也会让 session 收尾报红（双保险的"记录"半边）。"""
        guard = _k61_deepseek_egress_guard
        before = len(guard.violations)
        with pytest.raises(DeepSeekEgressBlocked):
            socket.getaddrinfo("api.deepseek.com", 443)
        assert len(guard.violations) == before + 1

    def test_violation_survives_swallowing(self, _k61_deepseek_egress_guard):
        """吞掉异常也逃不掉：记录仍在 → session 收尾会报红。"""
        guard = _k61_deepseek_egress_guard
        before = len(guard.violations)
        try:
            socket.getaddrinfo("api.deepseek.com", 443)
        except Exception:  # noqa: BLE001 - 故意用生产兜底写法吞
            pass
        except DeepSeekEgressBlocked:
            pass
        assert len(guard.violations) == before + 1
