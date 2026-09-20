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
import _io
import io
import os
import posix
import re
import shutil
import socket
import subprocess
import ssl
import sys
import threading
import time
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
    NON_LLM_EGRESS_HOSTS,
    DeepSeekEgressBlocked,
    EgressBlocked,
    PublicEgressBlocked,
)


@pytest.fixture(autouse=True)
def _drain_self_inflicted_violations(_k61_deepseek_egress_guard):
    """本文件是**故意**让守卫响的自证文件：收尾时弹掉自己制造的那几条违规，
    免得污染 session 收尾的「未经授权出站」判定（那个判定是给别的用例用的）。"""
    guard = _k61_deepseek_egress_guard
    before = len(guard.violations)
    yield
    del guard.violations[before:]


def _assert_blocked_by(exc_value, layer: str):
    """**归因断言**：这次拦截必须来自**这一层**（报文里带 `拦截层: <layer>`）。

    为什么必须钉归因（r9 起因）：同族多层会互相兜住 —— 把 `wrap_socket` 摘掉，
    `SSLSocket._create` 仍会拦，于是"锁"看着是绿的，但**它声称覆盖的那一层坏了也没人知道**。
    r8 的教训正是"把'守卫整体还在'当成'每条路径都锁住了'"。加上归因之后，
    "摘掉该层 → 本锁变红"才成立（逐条实测见 `.superpowers/sdd/task-k61-r9-report.md`）；
    多层冗余仍然在（摘单层时全链路照旧拦得住）。
    """
    assert layer in str(exc_value), (
        f"拦截不是来自本用例声称的层（{layer}）—— 该层没有被锁住。\n"
        f"实际报文：{str(exc_value)[:400]}")


# ══════════════════════════════════════════════════════════════════
# 1) 常量契约
# ══════════════════════════════════════════════════════════════════

class TestGuardContract:
    def test_blocked_markers_cover_deepseek(self):
        assert "deepseek" in BLOCKED_HOST_MARKERS
        for host in ("api.deepseek.com", "API.DEEPSEEK.COM", "chat.deepseek.com"):
            assert k61conftest._is_blocked(host), host

    def test_free_whitelist_is_free_only(self):
        """白名单只放免费源：不得含 deepseek，且必须含智谱（免费测试 LLM）。"""
        assert "open.bigmodel.cn" in FREE_LLM_HOSTS
        for h in FREE_LLM_HOSTS:
            assert not any(m in h for m in BLOCKED_HOST_MARKERS), h
        assert FREE_LLM_MODEL == "glm-4-flash"

    def test_every_whitelist_entry_has_a_reason(self):
        """r3：白名单条目必须带理由（防「悄悄放行一个域名」）。"""
        for name, table in (("FREE_LLM_HOSTS", FREE_LLM_HOSTS),
                            ("NON_LLM_EGRESS_HOSTS", NON_LLM_EGRESS_HOSTS)):
            assert table, f"{name} 为空"
            for host, reason in table.items():
                assert reason and len(reason) >= 10, f"{name} 缺理由：{host}"

    def test_whitelist_is_actually_read_at_runtime(self):
        """**r3 核心**：白名单必须真的被判定逻辑读取 —— 不许是死常量。

        审查者实测 r2 的 `FREE_LLM_HOSTS` 运行期从不被读取（加进 markers 也救不了）：
        「看起来有防护」的假象比没有更坏。本用例锁住「它真的在生效」：
        把白名单换成 `open.bigmodel.cn`，判定逻辑必须放行它、拒绝不在名单里的域名。
        """
        assert k61conftest._is_allowed_host("open.bigmodel.cn") is True
        assert k61conftest._is_allowed_host("api.open.bigmodel.cn") is True   # 子域
        assert k61conftest._is_allowed_host("evil.example.com") is False
        assert k61conftest._is_allowed_host("api.deepseek.com") is False
        # 白名单被替换时会立刻改变判定 → 证明它是活的那一份
        assert set(k61conftest.ALLOWED_EGRESS_HOSTS) == \
            set(FREE_LLM_HOSTS) | set(NON_LLM_EGRESS_HOSTS)

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
        with pytest.raises(DeepSeekEgressBlocked) as ei:
            socket.create_connection(("api.deepseek.com", 443), timeout=0.1)
        _assert_blocked_by(ei.value, "socket.create_connection")

    def test_httpx_sync_real_transport_blocked(self):
        """真实传输层（HTTPTransport）必须被拦——这是"会真开 socket"的那层。"""
        with pytest.raises(DeepSeekEgressBlocked) as ei:
            httpx.post("https://api.deepseek.com/anthropic/v1/messages",
                       json={"x": 1}, timeout=0.1)
        _assert_blocked_by(ei.value, "httpx.HTTPTransport.handle_request")

    def test_httpx_async_real_transport_blocked(self):
        import asyncio

        async def _go():
            async with httpx.AsyncClient(timeout=0.1) as c:
                await c.post("https://api.deepseek.com/anthropic/v1/messages",
                             json={"x": 1})

        with pytest.raises(DeepSeekEgressBlocked) as ei:
            asyncio.run(_go())
        _assert_blocked_by(ei.value, "httpx.AsyncHTTPTransport.handle_async_request")

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


# ══════════════════════════════════════════════════════════════════
# 5) r3：代理穿网 + IP 字面量绕过（审查者实测的两条逃逸）
# ══════════════════════════════════════════════════════════════════
#
# 改前（r2）：设了 `HTTPS_PROXY` 时 socket 层看到的是**代理地址**（socket 层失明），
# requests/urllib/httpx/aiohttp 四路静默穿网（监听器实收 `CONNECT api.deepseek.com:443`）；
# 直接用 **IP 字面量**（无主机名可判）也能连出去。
# 改后：C 层按**解析后的地址**判（公网默认拒绝），D 层扫**明文请求头**
# （`CONNECT host:port` / `Host:` / 绝对 URI）—— 代理只是通道，目标同样受判。

@pytest.fixture
def proxy_listener():
    """进程内 TCP 监听器：扮演「代理/终点」——只记录首行，**从不转发**。

    所以即使守卫失明，也不会有任何真实外呼；本夹具只用来**证明**穿没穿。
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    port = srv.getsockname()[1]
    hits: list = []
    stop = threading.Event()

    def serve():
        srv.settimeout(0.3)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(2)
                data = b""
                while b"\r\n" not in data and len(data) < 4096:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                hits.append(data.split(b"\r\n", 1)[0].decode("latin-1", "replace"))
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        yield port, hits
    finally:
        stop.set()
        try:
            srv.close()
        except Exception:
            pass
        t.join(timeout=2)


def _proxy_env(monkeypatch, port):
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.setenv(var, f"http://127.0.0.1:{port}")
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)


class TestProxyTunnelCannotSlipThrough:
    """四路各打一次 `api.deepseek.com`：必须 GUARD-BLOCK，且监听器**零** deepseek 行。"""

    URL = "https://api.deepseek.com/anthropic/v1/messages"

    def _assert_no_tunnel(self, port, hits):
        assert not [h for h in hits if "deepseek" in h.lower()], \
            f"代理隧道穿网了（监听器实收）：{hits}"

    def test_requests_through_proxy_blocked(self, proxy_listener, monkeypatch):
        port, hits = proxy_listener
        _proxy_env(monkeypatch, port)
        import requests
        with pytest.raises(DeepSeekEgressBlocked):
            requests.post(self.URL, json={"x": 1}, timeout=5)
        self._assert_no_tunnel(port, hits)

    def test_urllib_through_proxy_blocked(self, proxy_listener, monkeypatch):
        port, hits = proxy_listener
        _proxy_env(monkeypatch, port)
        import urllib.request
        with pytest.raises(DeepSeekEgressBlocked):
            urllib.request.urlopen(self.URL, timeout=5)
        self._assert_no_tunnel(port, hits)

    def test_httpx_through_proxy_blocked(self, proxy_listener, monkeypatch):
        port, hits = proxy_listener
        _proxy_env(monkeypatch, port)
        import httpx
        with pytest.raises(DeepSeekEgressBlocked):
            httpx.post(self.URL, json={"x": 1}, timeout=5)
        self._assert_no_tunnel(port, hits)

    def test_aiohttp_through_proxy_blocked(self, proxy_listener, monkeypatch):
        port, hits = proxy_listener
        _proxy_env(monkeypatch, port)
        import asyncio

        import aiohttp

        async def _go():
            async with aiohttp.ClientSession() as s:
                async with s.post(self.URL, json={"x": 1},
                                  timeout=aiohttp.ClientTimeout(total=5)):
                    pass

        with pytest.raises(DeepSeekEgressBlocked):
            asyncio.run(_go())
        self._assert_no_tunnel(port, hits)

    def test_direct_production_style_call_blocked_via_proxy(self, proxy_listener, monkeypatch):
        """端到端形态：生产同款函数（不 mock）→ 代理在场也必须拦。"""
        port, hits = proxy_listener
        _proxy_env(monkeypatch, port)
        import src.llm.client as llm_client
        with pytest.raises(DeepSeekEgressBlocked):
            llm_client.deepseek_anthropic_completion(
                "sk-not-a-real-key", [{"role": "user", "content": "ping"}], timeout=3)
        self._assert_no_tunnel(port, hits)


class TestAddressJudgment:
    """C 层：按**解析后的地址**判定 —— IP 字面量同样拦得住。"""

    PUBLIC_IP = "123.125.246.121"   # 审查者实测用的公网字面量（is_global=True）

    def test_public_ip_literal_direct_socket_blocked(self):
        s = socket.socket()
        s.settimeout(3)
        try:
            with pytest.raises(PublicEgressBlocked):
                s.connect((self.PUBLIC_IP, 443))
        finally:
            s.close()

    def test_public_ip_literal_connect_ex_blocked(self):
        s = socket.socket()
        s.settimeout(3)
        try:
            with pytest.raises(PublicEgressBlocked):
                s.connect_ex((self.PUBLIC_IP, 443))
        finally:
            s.close()

    def test_public_ip_literal_requests_blocked(self):
        import requests
        with pytest.raises(PublicEgressBlocked):
            requests.get(f"http://{self.PUBLIC_IP}/", timeout=3)

    def test_public_ip_literal_urllib_blocked(self):
        import urllib.request
        with pytest.raises(PublicEgressBlocked):
            urllib.request.urlopen(f"http://{self.PUBLIC_IP}/", timeout=3)

    def test_public_ip_literal_httpx_blocked(self):
        import httpx
        with pytest.raises(PublicEgressBlocked):
            httpx.get(f"http://{self.PUBLIC_IP}/", timeout=3)

    def test_non_public_addresses_allowed(self, proxy_listener):
        """回环/私网放行（不涉公网暴露）——守卫不得误伤本地服务。"""
        port, hits = proxy_listener
        s = socket.socket()
        s.settimeout(3)
        try:
            s.connect(("127.0.0.1", port))   # 必须成功
        finally:
            s.close()
        assert k61conftest._is_non_public_ip("127.0.0.1")
        assert k61conftest._is_non_public_ip("10.1.2.3")
        assert k61conftest._is_non_public_ip("192.168.0.9")
        assert not k61conftest._is_non_public_ip(self.PUBLIC_IP)

    def test_whitelisted_host_ips_are_learned_and_allowed(self, _k61_deepseek_egress_guard):
        """白名单主机的解析结果被学习 → C 层放行（否则 GLM 端到端会被自己的守卫打死）。"""
        guard = _k61_deepseek_egress_guard
        probe_ip = "123.125.246.121"      # 真公网 IP（仅做判定，不真连）
        assert not guard._ip_allowed(probe_ip)
        guard._learn_allowed_ip("open.bigmodel.cn", socket.AF_INET, (probe_ip, 443))
        assert guard._ip_allowed(probe_ip)

    def test_proxy_host_ips_are_allowed(self, proxy_listener, monkeypatch):
        """代理主机本身要放行（它是**通道**）：目标由 D 层的 CONNECT 行判。"""
        port, _ = proxy_listener
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")
        assert "127.0.0.1" in k61conftest._proxy_hosts()


class TestPayloadScanParsing:
    """D 层的行解析与"只扫请求头"口径（防 body 假红）。"""

    def test_connect_line(self):
        assert k61conftest._target_host_in_line(
            "CONNECT api.deepseek.com:443 HTTP/1.1") == "api.deepseek.com"

    def test_host_header(self):
        assert k61conftest._target_host_in_line("Host: api.deepseek.com") == "api.deepseek.com"

    def test_absolute_uri(self):
        assert k61conftest._target_host_in_line(
            "GET http://api.deepseek.com/v1 HTTP/1.1") == "api.deepseek.com"

    def test_irrelevant_line(self):
        assert k61conftest._target_host_in_line("Content-Type: application/json") is None
        assert k61conftest._target_host_in_line("") is None

    def test_only_request_heads_are_scanned(self, _k61_deepseek_egress_guard):
        """body 里出现 `http://api.deepseek.com` **不得**触发（否则载荷类用例会假红）。"""
        guard = _k61_deepseek_egress_guard
        before = len(guard.violations)
        s = socket.socket()
        try:
            guard.scan_payload(s, b'{"url": "http://api.deepseek.com/v1"}')   # body 形态
        finally:
            s.close()
        assert len(guard.violations) == before, "body 被误扫 → 会假红"

    def test_request_head_scan_trips(self, _k61_deepseek_egress_guard):
        guard = _k61_deepseek_egress_guard
        before = len(guard.violations)
        s = socket.socket()
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                guard.scan_payload(s, b"CONNECT api.deepseek.com:443 HTTP/1.1\r\n")
        finally:
            s.close()
        assert len(guard.violations) == before + 1


# ══════════════════════════════════════════════════════════════════
# 6) r5（审查整改）：C1 不透明代理 fail-closed / C2 分片写 / I1 www 反向自匹配
# ══════════════════════════════════════════════════════════════════

class TestOpaqueProxyFailClosed:
    """C1：`HTTPS_PROXY=https://…`（TLS 包裹）与 `socks*` → **fail-closed**。

    审查者实测（本批也用真 TLS 监听器复现）：真 `requests` 会把
    `CONNECT api.deepseek.com:443` 发在 **TLS 之内** → r3 的明文扫描**天然看不见**，
    守卫零反应。处置：**目标被加密层藏起来 = 没有可判之处 → 一律拒绝**。
    """

    @pytest.fixture
    def fake_proxy_port(self):
        """一个**不监听**的本地端口即可：fail-closed 发生在 connect 之前。"""
        return 18871

    def _set_opaque_proxy(self, monkeypatch, port, scheme="https"):
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", f"{scheme}://127.0.0.1:{port}")
        monkeypatch.setenv("https_proxy", f"{scheme}://127.0.0.1:{port}")

    @pytest.mark.parametrize("scheme", ["https", "socks5", "socks5h", "socks4"])
    def test_connect_to_opaque_proxy_endpoint_is_refused(self, fake_proxy_port,
                                                         monkeypatch, scheme):
        """不透明代理端点上的连接必须被拒（含 loopback —— loopback 不是豁免理由）。"""
        self._set_opaque_proxy(monkeypatch, fake_proxy_port, scheme)
        s = socket.socket()
        s.settimeout(2)
        try:
            with pytest.raises(PublicEgressBlocked) as ei:
                s.connect(("127.0.0.1", fake_proxy_port))
            assert "不透明代理" in str(ei.value)
        finally:
            s.close()

    def test_requests_via_tls_proxy_never_reaches_target(self, monkeypatch):
        """真 `requests` 走 TLS 代理：必须 GUARD-BLOCK（r3 时它会把 CONNECT 送出）。"""
        self._set_opaque_proxy(monkeypatch, 18871, "https")
        import requests
        with pytest.raises(PublicEgressBlocked):
            requests.post("https://api.deepseek.com/x", json={"a": 1}, timeout=3)

    @staticmethod
    def test_anthropic_sdk_via_tls_proxy_blocked(monkeypatch):
        """anthropic SDK 同款（审查者要求四路 + SDK 全部不得漏）。"""
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "https://127.0.0.1:18871")
        monkeypatch.setenv("https_proxy", "https://127.0.0.1:18871")
        import anthropic
        import httpx
        client = anthropic.Anthropic(
            api_key="sk-not-real",
            base_url="https://api.deepseek.com/anthropic",
            http_client=httpx.Client(timeout=3))
        # 具体"哪一层"先命中取决于 SDK 内部的 httpx 解析顺序（实测命中 E 层
        # httpx 真实传输层）—— 断言父类即可：**必须被守卫拦下**，一条都不得出去。
        with pytest.raises(EgressBlocked):
            client.messages.create(model="deepseek-flash", max_tokens=4,
                                   messages=[{"role": "user", "content": "ping"}])

    def test_plaintext_proxy_is_still_allowed_through(self, proxy_listener, monkeypatch):
        """**对照**：明文 `http://` 代理不受 fail-closed 影响（CONNECT 行可判，
        只有目标不合规才拒）—— 防止"一刀切禁代理"把合法链路打死。"""
        port, _ = proxy_listener
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("https_proxy", f"http://127.0.0.1:{port}")
        # 代理端点本身可连（它是通道）
        s = socket.socket()
        s.settimeout(2)
        try:
            s.connect(("127.0.0.1", port))
        finally:
            s.close()
        # 但目标是 deepseek → 仍被拦
        with pytest.raises(DeepSeekEgressBlocked):
            import requests
            requests.post("https://api.deepseek.com/x", json={"a": 1}, timeout=3)


@pytest.fixture
def local_sink():
    """本地明文接收端（从不转发）——用来证明"字节到底出没出去"。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    seen: list = []
    stop = threading.Event()

    def serve():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(1)
            try:
                seen.append(conn.recv(4096))
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        yield port, seen
    finally:
        stop.set()
        try:
            srv.close()
        except Exception:
            pass
        t.join(timeout=2)


class TestFragmentWriteBypass:
    """C2：分片 / memoryview / sendmsg / os.write 四种写路径都必须判得出。

    审查者实测 r3：这四种**全部**把 `CONNECT api.deepseek.com:443` 送出而守卫静默
    （同文件内"单块整写被拦"的对照证明是**判定漏**、不是观测漏）。
    """

    LINE = b"CONNECT api.deepseek.com:443 HTTP/1.1\r\n\r\n"


    def _raw_sock(self, port):
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        return s

    def test_fragmented_send_blocked(self, local_sink):
        port, seen = local_sink
        s = self._raw_sock(port)
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                s.send(self.LINE[:4])
                s.send(self.LINE[4:])       # 第二片到达时累积前缀已可识别 → 拦
        finally:
            s.close()

    def test_memoryview_send_blocked(self, local_sink):
        port, _ = local_sink
        s = self._raw_sock(port)
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                s.send(memoryview(self.LINE))
        finally:
            s.close()

    def test_sendmsg_blocked(self, local_sink):
        port, _ = local_sink
        s = self._raw_sock(port)
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                s.sendmsg([self.LINE])
        finally:
            s.close()

    def test_os_write_blocked(self, local_sink):
        port, _ = local_sink
        s = self._raw_sock(port)
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                os.write(s.fileno(), self.LINE)
        finally:
            s.close()

    def test_single_block_still_blocked(self, local_sink):
        """**对照**（r3 就拦得住的那条）：继续绿 —— 证明改动没有把判定改松。"""
        port, _ = local_sink
        s = self._raw_sock(port)
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                s.sendall(self.LINE)
        finally:
            s.close()

    def test_os_write_to_a_file_is_not_scanned(self, tmp_path, _k61_deepseek_egress_guard):
        """**对照（r5 自测踩到的坑）**：写**文件**不得被当成网络写扫描。

        `os.write` 的 fd 会被文件复用：收集期 pytest 写 `.pyc` 时若恰好复用一个
        已关闭 socket 的 fd，而那个 .pyc 里含 `CONNECT api.deepseek.com:443` 字面量，
        就会误判成网络写并**打断收集**（实测 7 errors）。故判定用 `fstat` 的
        `S_ISSOCK` 做**权威**类型判定，而不是"这个 fd 曾经登记过"。
        """
        guard = _k61_deepseek_egress_guard
        before = len(guard.violations)
        path = tmp_path / "not_a_socket.bin"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            os.write(fd, self.LINE)          # 文件写：不得触发
        finally:
            os.close(fd)
        assert len(guard.violations) == before, "文件写被误判成网络写"
        assert guard._is_socket_fd(fd) is False

    def test_target_hostname_never_reaches_the_wire(self, local_sink):
        """四种绕过都拦下后，接收端**不得**看到完整目标域名。"""
        port, seen = local_sink
        for path in ("frag", "mv", "sendmsg", "oswrite"):
            s = self._raw_sock(port)
            try:
                if path == "frag":
                    s.send(self.LINE[:4])
                    s.send(self.LINE[4:])
                elif path == "mv":
                    s.send(memoryview(self.LINE))
                elif path == "sendmsg":
                    s.sendmsg([self.LINE])
                else:
                    os.write(s.fileno(), self.LINE)
            except EgressBlocked:
                pass
            finally:
                s.close()
        blob = b"".join(x for x in seen if x)
        assert b"api.deepseek.com" not in blob, f"目标域名漏到线上了：{blob[:120]!r}"


class TestWwwReverseMatchIsGone:
    """I1：`www.` **反向自匹配**已删除 —— 任意 `www.*` 不得被判在白名单。

    r3 曾在 `_is_allowed_host` 里写「待判主机名以 www. 开头 → 追加其裸域」，
    那是一条**过宽的默认放行**：`www.evil.com` / `www.baidu.com.evil.tld` /
    `WWW.Evil.COM` 全 True，并**污染 IP 学习集**（学到 `www.x → 1.2.3.4` 后，
    `connect(("1.2.3.4", 443))` 直接放行）。
    """

    @pytest.mark.parametrize("host", [
        "www.evil.com", "www.baidu.com.evil.tld", "WWW.Evil.COM",
        "evil-360.cn", "360.cn.attacker.com", "so.com.attacker.com",
        "www.deepseek.com",
    ])
    def test_malicious_forms_are_not_allowed(self, host):
        assert k61conftest._is_allowed_host(host) is False, host

    @pytest.mark.parametrize("host", [
        "www.baidu.com", "baidu.com", "m.so.com", "so.com", "www.so.com",
        "cn.bing.com", "open.bigmodel.cn", "api.open.bigmodel.cn",
    ])
    def test_legitimate_forms_still_allowed(self, host):
        """合法项照旧（"去掉 www." 只在**条目**侧派生，合法面不变）。"""
        assert k61conftest._is_allowed_host(host) is True, host

    def test_ip_learning_is_not_polluted_by_www_hosts(self, _k61_deepseek_egress_guard):
        """**植入实验（反向）**：先"学"一个非白名单 www 主机的 IP → 再连它必须仍被拒。"""
        guard = _k61_deepseek_egress_guard
        poison_ip = "8.8.8.8"   # 真公网 IP（且没有被别的用例学进白名单）
        guard._learn_allowed_ip("www.evil.com", socket.AF_INET, (poison_ip, 443))
        assert guard._ip_allowed(poison_ip) is False, "IP 学习集被 www.* 污染了"
        s = socket.socket()
        s.settimeout(2)
        try:
            with pytest.raises(PublicEgressBlocked):
                s.connect((poison_ip, 443))
        finally:
            s.close()


# ══════════════════════════════════════════════════════════════════
# 7) r7（复审整改）：显式代理参数 / sendto+writev / 发送族 API 面 / D 层形状面
# ══════════════════════════════════════════════════════════════════

class TestExplicitProxyParamsFailClosed:
    """C1 残余：**显式参数**配的代理同样必须 fail-closed。

    审查者实测 r6：`requests(proxies={"https": "https://127.0.0.1:P"})` 时
    loopback 代理被放行，`CONNECT api.deepseek.com:443` 在 TLS 之内送出（69 字节），
    守卫零反应；同文件的环境变量那条路 r6 确实修好了（69 → 0）。
    根因：r6 的判定**只读环境变量**（配置从哪来），而漏了参数路径。
    修法：判据下沉到 **"这次客户端 TLS 握手的对端是谁"**
    （`ssl.SSLContext.wrap_socket`）—— 与代理怎么配的无关。
    """

    def _tls_proxy_no_env(self, monkeypatch, port, scheme="https"):
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        return f"{scheme}://127.0.0.1:{port}"

    def test_requests_explicit_proxies_param_blocked(self, proxy_listener, monkeypatch):
        port, _ = proxy_listener
        proxy = self._tls_proxy_no_env(monkeypatch, port)
        import requests
        with pytest.raises(EgressBlocked):
            requests.post("https://api.deepseek.com/x", json={"a": 1}, timeout=3,
                          verify=False, proxies={"https": proxy, "http": proxy})

    def test_httpx_explicit_proxy_param_blocked(self, proxy_listener, monkeypatch):
        port, _ = proxy_listener
        proxy = self._tls_proxy_no_env(monkeypatch, port)
        import httpx
        with pytest.raises(EgressBlocked):
            httpx.post("https://api.deepseek.com/x", json={"a": 1}, timeout=3,
                       verify=False, proxy=proxy)

    def test_httpx_explicit_mounts_param_blocked(self, proxy_listener, monkeypatch):
        port, _ = proxy_listener
        proxy = self._tls_proxy_no_env(monkeypatch, port)
        import httpx
        with httpx.Client(verify=False, timeout=3,
                          mounts={"https://": httpx.HTTPTransport(proxy=proxy)}) as cl:
            with pytest.raises(EgressBlocked):
                cl.post("https://api.deepseek.com/x", json={"a": 1})

    def test_direct_tls_to_non_whitelisted_endpoint_blocked(self, proxy_listener, monkeypatch):
        """更本质的一条：客户端 TLS 只要打到**非白名单端点**就拒（与代理无关）。"""
        port, _ = proxy_listener
        s = socket.socket()
        s.settimeout(3)
        try:
            s.connect(("127.0.0.1", port))
            import ssl as _ssl
            with pytest.raises(PublicEgressBlocked):
                _ssl._create_unverified_context().wrap_socket(s, server_hostname="x")
        finally:
            s.close()

    def test_whitelisted_hostname_tls_still_allowed(self, monkeypatch):
        """**对照**：白名单主机（其解析出的 IP）上的客户端 TLS 不受影响。"""
        s = socket.socket()
        s.settimeout(3)
        try:
            infos = socket.getaddrinfo("open.bigmodel.cn", 443)     # 学习该主机的 IP
            ip = infos[0][4][0]
            assert k61conftest._GUARD._ip_learned(ip), "白名单主机的 IP 未被学习"
        finally:
            s.close()


class TestSendtoAndWritevBlocked:
    """C2 残余：`sendto` / `os.writev` 在 r6 未挂钩（同类里修了 4 条、漏了 2 条）。"""

    LINE = b"CONNECT api.deepseek.com:443 HTTP/1.1\r\n\r\n"

    def _sock(self, port):
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        return s

    def test_sendto_blocked(self, local_sink):
        port, _ = local_sink
        s = self._sock(port)
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                s.sendto(self.LINE, ("127.0.0.1", port))
        finally:
            s.close()

    def test_os_writev_blocked(self, local_sink):
        port, _ = local_sink
        s = self._sock(port)
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                os.writev(s.fileno(), [self.LINE])
        finally:
            s.close()

    def test_control_sendall_still_blocked(self, local_sink):
        port, _ = local_sink
        s = self._sock(port)
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                s.sendall(self.LINE)
        finally:
            s.close()

    def test_hostname_never_reaches_the_wire_on_new_paths(self, local_sink):
        port, seen = local_sink
        for kind in ("sendto", "writev"):
            s = self._sock(port)
            try:
                if kind == "sendto":
                    s.sendto(self.LINE, ("127.0.0.1", port))
                else:
                    os.writev(s.fileno(), [self.LINE])
            except EgressBlocked:
                pass
            finally:
                s.close()
        blob = b"".join(x for x in seen if x)
        assert b"api.deepseek.com" not in blob, f"目标域名漏到线上：{blob[:120]!r}"


class TestSendFamilyApiSurface:
    """**写路径 API 面守卫**（r7 新增、r8 加固、**r9 改为显式枚举**）。

    ## 为什么"按前缀猜"必须退场（r9 实测，原始输出见报告）

    r8 的派生谓词是 `name.startswith(("send", "write"))`。它**两个方向都错过**：

    - **漏**（实测把 `CONNECT api.deepseek.com:443` 原样送上监听器、守卫 0 反应）：
      `os.splice`（file→pipe→socket）、`os.eventfd_write`（写语义在**后缀**里，一次塞 8 字节）、
      `io.FileIO` / `os.fdopen` / `builtins.open` / `shutil.copyfileobj`（文件对象家族）。
    - **误收**：名字像却不是通道 —— `socket.sendmsg_afalg`（只对 AF_ALG 生效）、
      `os.pwrite`/`pwritev`（socket 上 `ESPIPE`）、`os.copy_file_range`（socket 上 `EINVAL`）。

    ⇒ r9 起判据是**显式枚举**：每条给「来源 + 处置 + 依据 + 锁它的用例」。
    前缀扫描**退为绊线**（`test_tripwire_names_all_have_a_decision`）：派生集合里出现
    没登记的名字就**红**，逼人先做决定 —— 而不是让"猜"当判据。
    """

    #: **枚举来源**（覆盖"能把字节/值写进 fd 或 socket"的 CPython 文档入口）：
    #:   ① `socket.socket` 发送族（docs: library/socket —— send/sendall/sendmsg/sendto/sendfile）
    #:   ② `os` 的低级 fd 写族（docs: library/os —— write/writev/sendfile/splice/eventfd_write/fdopen）
    #:   ③ `posix` = ② 的**同族别名**（`os.write is posix.write` 为 True，但**是两个模块属性**）
    #:   ④ `io` / `builtins` / `shutil` 的**文件对象**族（C 层 `FileIO.write` 直写 fd）
    #:   ⑤ `_socket.socket`（`socket.socket` 的父类 —— **两套绑定**）
    #: 完备性**无法证明**：绊线是启发式的，理论上可能漏（`eventfd_write` 就是这类）；
    #: 因此本表按文档族逐条对照，并在报告"没验证的"一节显式登记。

    #: 已挂钩（必须是我们自己的包装）→ **锁它的用例**（"摘掉该路径守卫 → 该用例必须变红"）
    HOOKED = {
        # ① socket 发送族（Python 子类层）
        ("socket.socket", "send"): ("_guard_send", "test_memoryview_send_blocked"),
        ("socket.socket", "sendall"): ("_guard_sendall", "test_single_block_still_blocked"),
        ("socket.socket", "sendmsg"): ("_guard_sendmsg", "test_sendmsg_blocked"),
        ("socket.socket", "sendto"): ("_guard_sendto", "test_sendto_blocked"),
        ("socket.socket", "sendfile"): ("_guard_sock_sendfile", "test_aliases_are_actually_blocked[socket.socket.sendfile]"),
        # ② os 低级写族
        ("os", "write"): ("_guard_os_write", "test_os_write_blocked"),
        ("os", "writev"): ("_guard_os_writev", "test_os_writev_blocked"),
        ("os", "sendfile"): ("_guard_os_sendfile", "test_aliases_are_actually_blocked[os.sendfile]"),
        ("os", "splice"): ("_guard_os_splice", "test_os_splice_into_socket_blocked"),
        ("os", "eventfd_write"): ("_guard_eventfd_write", "test_os_eventfd_write_to_socket_blocked"),
        ("os", "fdopen"): ("_guard_fdopen", "test_os_fdopen_socket_fd_blocked"),
        # ③ posix 同族别名（两个模块属性、同一函数 —— 只补一处会漏）
        ("posix", "write"): ("_guard_os_write", "test_aliases_are_actually_blocked[posix.write]"),
        ("posix", "writev"): ("_guard_os_writev", "test_aliases_are_actually_blocked[posix.writev]"),
        ("posix", "sendfile"): ("_guard_os_sendfile", "test_aliases_are_actually_blocked[posix.sendfile]"),
        ("posix", "splice"): ("_guard_os_splice", "test_posix_write_aliases_are_blocked[posix.splice]"),
        ("posix", "eventfd_write"): ("_guard_eventfd_write", "test_posix_write_aliases_are_blocked[posix.eventfd_write]"),
        # ④ 文件对象族
        ("io", "FileIO"): ("_GuardedFileIO", "test_io_fileio_socket_fd_blocked"),
        ("io", "open"): ("_guard_io_open", "test_io_open_socket_fd_blocked"),
        ("builtins", "open"): ("_guard_io_open", "test_builtins_open_socket_fd_blocked"),
        ("shutil", "copyfileobj"): ("_guard_copyfileobj", "test_copyfileobj_into_socket_file_blocked"),
    }

    #: **已声明不可覆盖**（纯 Python 层做不到；必须写清理由 + 兜底）
    DECLARED_UNCOVERABLE = {
        ("_socket.socket", n): (
            "C 扩展的**不可变类型**：实测 `TypeError: cannot set '<n>' attribute of "
            "immutable type '_socket.socket'` → 纯 Python 层**挂不上钩**。"
            "常规代码用的是 Python 子类 `socket.socket`（已挂钩）；"
            "要走到这里必须显式 `_socket.socket.send(sock, …)` 这类刻意绕过。"
            "**结构性兜底 = pin**：`DEEPSEEK_API_KEY` 被设成空串（成员判定挡住 .env 回填、"
            "且被**子进程继承**）→ 即便绕过钩子，进程里也没有可用的生产 key。"
        )
        for n in ("send", "sendall", "sendmsg", "sendto", "sendfile", "sendmsg_afalg")
    }

    #: **已实测"不是通道 / 不是入口"**（每条必须带实测依据）
    DECLARED_NOT_A_CHANNEL = {
        ("socket.socket", "sendmsg_afalg"): (
            "**实测**：在 TCP socket 上调 `sendmsg_afalg` → "
            "`OSError: algset is only supported for AF_ALG` —— 它只对 `AF_ALG` "
            "（内核加密套接字）生效，发的是**内核算法参数**（key/iv/assoclen），"
            "不是请求头明文；本仓与四路 HTTP 客户端都不用 AF_ALG。"
            "（对照：r7 那两条 `sendfile` 豁免理由是**造假**的——引用了不存在的"
            "「文件写守卫」——已收回并改为 fail-closed。）"
        ),
        ("os", "pwrite"): "**实测**：`os.pwrite(sock_fd, b'x', 0)` → `OSError: [Errno 29] Illegal seek`"
                          " —— pwrite 需要可寻址文件，socket 上直接失败。",
        ("os", "pwritev"): "**实测**：`os.pwritev(sock_fd, [b'x'], 0)` → `OSError: [Errno 29] Illegal seek`"
                           "（同 pwrite）。",
        ("posix", "pwrite"): "同 `os.pwrite`（别名，实测 ESPIPE）。",
        ("posix", "pwritev"): "同 `os.pwritev`（别名，实测 ESPIPE）。",
        ("os", "copy_file_range"): "**实测**：`os.copy_file_range(0, sock_fd, 4)` 与反向都 → "
                                   "`OSError: [Errno 22] Invalid argument` —— 只支持普通文件之间复制，"
                                   "socket 不是合法端点。",
        ("posix", "copy_file_range"): "同 `os.copy_file_range`（别名，实测 EINVAL）。",
        ("os", "truncate"): "按**路径**截断，不写字节内容（且 socket 无路径）。",
        ("posix", "truncate"): "同 `os.truncate`（别名）。",
        ("os", "ftruncate"): "按 fd 改长度，**不写字节内容**（改不动 socket 上的数据流）。",
        ("posix", "ftruncate"): "同 `os.ftruncate`（别名）。",
        ("socket.socket", "_sendfile_use_send"): "`sendfile` 的**内部实现**（回退路径）：用 `self.send(...)` "
                                                 "循环发 → 已被 send 钩子判（r9 实测：`s.sendfile(...)` 被拦的是 sendfile 钩子，"
                                                 "该 helper 不是独立入口）。",
        ("socket.socket", "_sendfile_use_sendfile"): "`sendfile` 的**内部实现**（走 `os.sendfile`）→ "
                                                     "已被 os.sendfile 钩子判（不是独立入口）。",
        ("socket.socket", "_check_sendfile_params"): "**参数校验**函数（不写任何字节）。",
        ("socket.socket", "makefile"): "**不是写入口**：返回 SocketIO/缓冲文件对象，其写入经 `socket.send`。"
                                       "**实测**：`s.makefile('wb').write(CONNECT…)` 被拦"
                                       "（**有行为锁**：`test_socket_makefile_writes_are_still_scanned`）。",
        ("socket.socket", "fileno"): "取 fd 号，不写字节。",
        ("_socket.socket", "fileno"): "取 fd 号，不写字节。",
        ("io", "BufferedWriter"): "**不是入口而是包装器**：其 raw 只能来自 `io.FileIO`（已挂钩）、"
                                  "`_io.FileIO`（见 DECLARED_LIMITATIONS）或 `socket.SocketIO`"
                                  "（写经 `socket.send`）。**实测**："
                                  "`io.BufferedWriter(socket.SocketIO(s,'wb')).write(CONNECT…)` 被拦"
                                  "（锁：`test_bufferedwriter_over_socketio_is_still_scanned`）。",
        ("shutil", "copyfile"): "**路径级**（两个参数都是路径）：写目标是 `open(dst, 'wb')`，"
                                "而路径**到不了 socket fd** —— **实测** `open('/proc/self/fd/<sockfd>', 'wb')` "
                                "→ `OSError: [Errno 6] No such device or address`。",
        ("shutil", "copy"): "路径级（内部走 copyfile/copyfileobj，见上）。",
        ("shutil", "copy2"): "路径级（内部走 copyfile/copyfileobj，见上）。",
        ("shutil", "copytree"): "路径级目录树拷贝（内部用文件对象，写的是普通文件）。",
        ("shutil", "copymode"): "只拷权限位，不写字节。",
        ("shutil", "copystat"): "只拷元数据，不写字节。",
        ("shutil", "_copyfileobj_readinto"): "`copyfileobj` 的**备用内部实现**（readinto 版）："
                                             "同样只调 `fdst.write` → 被已挂钩的 copyfileobj 覆盖。",
        ("shutil", "_fastcopy_sendfile"): "`copyfile` 的内部快路径（走 `os.sendfile`）→ 已被 os.sendfile 钩子判（"
                                          "且它是路径级、目标不是 socket）。",
        ("shutil", "_fastcopy_fcopyfile"): "`copyfile` 的内部快路径（fcopyfile）→ **实测** socket 上不可用"
                                           "（copy_file_range/`EINVAL` 同族）。",
        ("shutil", "_copytree"): "`copytree` 的内部实现（路径级）。",
        ("shutil", "_copyxattr"): "只拷扩展属性，不写字节流。",
        ("shutil", "_samefile"): "比较两个路径是否同一文件，不写。",
        ("shutil", "_make_zipfile"): "打包成 zip（路径级，写普通文件）。",
        ("shutil", "_unpack_tarfile"): "解包 tar（路径级）。",
        ("shutil", "_unpack_zipfile"): "解包 zip（路径级）。",
        ("os", "open"): "**路径级**打开（返回 fd，不写字节）：**实测** `os.open('/proc/self/fd/<sockfd>', O_WRONLY)` "
                        "→ `OSError: [Errno 6] No such device or address` —— 路径到不了 socket fd。",
        ("posix", "open"): "同 `os.open`（别名，实测 ENXIO）。",
        ("os", "openpty"): "开一对**伪终端**（不涉及 socket），返回的是 pty 的 fd。",
        ("posix", "openpty"): "同 `os.openpty`（别名）。",
        ("os", "pidfd_open"): "按 pid 打开**进程 fd**（不是写通道）。",
        ("posix", "pidfd_open"): "同 `os.pidfd_open`（别名）。",
        ("os", "popen"): "**起子进程**跑命令（→ 落在 DECLARED_PROCESS_BOUNDARY：socket 在别的进程里，"
                         "本进程钩子看不到；兜底 = pin 让子进程也没有生产 key）。",
        ("io", "open_code"): "按**路径**只读打开源码（执行用）：**实测** `io.open_code(sock_fd)` → "
                             "`TypeError: open_code() argument 'path' must be str, not int` —— 不接受 fd。",
        ("shutil", "SameFileError"): "**异常类**（名字里有 `File`），不是写入口。",
        ("shutil", "SpecialFileError"): "**异常类**（同上）。",
        ("shutil", "_GiveupOnFastCopy"): "**异常类**（内部快拷放弃信号，同上）。",
        ("builtins", "copyright"): "**实测**：`callable(copyright)` 为 True（它是 `_sitebuiltins._Printer` "
                                   "实例），但它**不写字节** —— 只是打印版权信息，名字里有 `copy` 而已。",
        ("builtins", "FileExistsError"): "**异常类**（名字里有 `File`），不是写入口。",
        ("builtins", "FileNotFoundError"): "**异常类**（同上）。",
    }

    #: 进程边界类（同样修不了，一并声明；报告里有专节）
    DECLARED_PROCESS_BOUNDARY = {
        "子进程（子解释器 / curl / fork+exec）": "socket 在别的进程里，本进程钩子看不到",
        "ctypes 裸系统调用（直调 libc）": "不经过任何 Python 属性查找",
    }

    #: 绊线谓词（**只用来逼人做决定，不是判据** —— 它自己就漏过 `eventfd_write`）
    _TRIPWIRE_RE = re.compile(r"(send|write|splice|copy|file|open|truncate)", re.I)

    @staticmethod
    def _modules():
        import _socket
        import builtins as _b
        import posix as _posix
        return (("socket.socket", socket.socket), ("_socket.socket", _socket.socket),
                ("os", os), ("posix", _posix), ("io", io), ("builtins", _b),
                ("shutil", shutil))

    def _surface(self):
        """绊线派生：名字像"写/拷贝/文件"的模块级可调用（**不做判定**）。"""
        out = {}
        for mod_name, mod in self._modules():
            for name in dir(mod):
                if not self._TRIPWIRE_RE.search(name):
                    continue
                if not callable(getattr(mod, name, None)):
                    continue
                out.setdefault((mod_name, name), None)
        return out

    def _decided(self):
        return set(self.HOOKED) | set(self.DECLARED_UNCOVERABLE) | set(self.DECLARED_NOT_A_CHANNEL)

    def test_tripwire_names_all_have_a_decision(self):
        """**绊线**：派生集合里每个名字都必须有明确处置（挂钩 / 不可覆盖 / 不是通道）。

        这条不是"覆盖证明"（枚举完备性无法证明），而是**防"新名字悄悄溜过"**：
        r6 漏 2 条、r7 漏 9 条、r8 漏 4 类，全部是"没人对那个名字做过决定"。
        """
        missing = [k for k in self._surface() if k not in self._decided()]
        assert not missing, (
            f"写路径面出现既未挂钩、也未声明的名字：{missing}\n"
            f"（教训：r6 修 4 条漏 2 条、r7 别名漏 9 条、r8 漏 splice/eventfd/文件对象族 —— "
            f"每个名字都必须先做决定）")

    def test_hooked_names_are_really_our_wrappers(self):
        """**结构锁**：已挂钩的名字必须 `is` 我们的包装（防"被谁换回去了"）。"""
        import conftest as c
        import _socket
        import builtins as _b
        import posix as _posix
        mods = {"socket.socket": socket.socket, "_socket.socket": _socket.socket,
                "os": os, "posix": _posix, "io": io, "builtins": _b, "shutil": shutil}
        for (mod_name, name), (wrapper, _lock) in self.HOOKED.items():
            assert getattr(mods[mod_name], name) is getattr(c, wrapper), \
                f"{mod_name}.{name} 不是我们的包装（被谁换回去了？）"

    def test_every_hooked_path_names_an_existing_lock(self):
        """**零回归锁的自检**：每条已挂钩路径都必须指向本模块里**真实存在**的锁用例。

        r8 的缺陷正是"声称每条路径都有行为锁，实际 `grep wrap_bio tests/` = 0 命中"。
        这条把"锁"变成**可检查的登记**：名字打错/用例被删 → 立刻红。
        """
        import inspect as _inspect
        names = set()
        for _name, obj in vars(sys.modules[__name__]).items():
            if _inspect.isclass(obj):
                for attr in vars(obj):
                    names.add(attr)
        for key, (wrapper, lock) in self.HOOKED.items():
            base = lock.split("[")[0]
            assert base in names, f"{key} 登记的锁用例 {lock} 在本模块里不存在"
            assert wrapper in dir(k61conftest), f"{key} 登记的包装 {wrapper} 在 conftest 里不存在"

    def test_not_a_channel_entries_have_measured_reasons(self):
        for key, reason in self.DECLARED_NOT_A_CHANNEL.items():
            assert any(mark in reason for mark in
                       ("实测", "路径", "别名", "异常", "不写", "只读", "子进程",
                        "伪终端", "进程 fd", "内部实现")), \
                f"{key} 的理由必须给依据（不许看起来像）"

    def test_uncoverable_entries_have_reasons(self):
        for key, reason in self.DECLARED_UNCOVERABLE.items():
            assert len(reason) >= 40, key
            assert "pin" in reason or "兜底" in reason, f"{key} 未写结构性兜底"
        assert self.DECLARED_PROCESS_BOUNDARY, "进程边界声明缺失"

    @pytest.mark.parametrize("name", ["posix.write", "posix.writev",
                                      "os.sendfile", "posix.sendfile",
                                      "socket.socket.sendfile"])
    def test_aliases_are_actually_blocked(self, name, local_sink):
        """**行为锁**：别名路径真的被拦（r9 把原来"四条挤在一条用例里"拆开 ——
        每条路径一个用例，才能在"摘掉该路径守卫 → 对应锁变红"的实测里逐条归因）。"""
        port, seen = local_sink
        line = b"CONNECT api.deepseek.com:443 HTTP/1.1\r\n\r\n"
        import posix as _posix
        cases = {
            "posix.write": lambda s: _posix.write(s.fileno(), line),
            "posix.writev": lambda s: _posix.writev(s.fileno(), [line]),
            "os.sendfile": lambda s: os.sendfile(s.fileno(), os.open("/etc/hostname", os.O_RDONLY), 0, 10),
            "posix.sendfile": lambda s: _posix.sendfile(s.fileno(), os.open("/etc/hostname", os.O_RDONLY), 0, 10),
            "socket.socket.sendfile": lambda s: s.sendfile(open("/etc/hostname", "rb")),
        }
        # 归因（r9）：本用例声称覆盖哪条路径，就必须**是那条路径**在拦。
        # `socket.socket.sendfile` 尤其需要：CPython 的快路径直接调 `os.sendfile`，
        # 只看 `pytest.raises` 的话"本层坏了"会被同族层兜住而**看不出来**（r9 实测）。
        # （D 层的 send/write 族共用同一个扫描器 → 拦截标签是 D 层的，不在此列。）
        layer = {
            "posix.write": None, "posix.writev": None,
            "os.sendfile": "os.sendfile（socket ← file）",
            "posix.sendfile": "os.sendfile（socket ← file）",
            "socket.socket.sendfile": "socket.socket.sendfile（socket ← file）",
        }[name]
        s = socket.socket()
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        try:
            with pytest.raises(EgressBlocked) as ei:
                cases[name](s)
            if layer:
                _assert_blocked_by(ei.value, layer)
        finally:
            s.close()
        blob = b"".join(x for x in seen if x)
        assert b"api.deepseek.com" not in blob


class TestDlayerShapeGaps:
    """D 层形状面缺口（r3 同类未清）：三条形态此前能把域名写到线上。"""

    def _scan(self, payload: bytes):
        """每条用**独立 fd**（共用 fd 会让缓冲串味 —— 我自己的探针踩过）。"""
        import conftest as c
        fd = int.from_bytes(os.urandom(4), "big") + 700000
        c._GUARD.scan_payload(None, payload, fd=fd)

    @pytest.mark.parametrize("name,payload", [
        ("Host 在第 9 行",
         b"GET / HTTP/1.1\r\nA: 1\r\nB: 2\r\nC: 3\r\nD: 4\r\nE: 5\r\nF: 6\r\nG: 7\r\n"
         b"Host: api.deepseek.com\r\n\r\n"),
        ("CONNECT\\t 且无 Host 行",
         b"CONNECT\tapi.deepseek.com:443\tHTTP/1.1\r\n\r\n"),
        ("非标准方法名 + 绝对 URI",
         b"M-SEARCH* http://api.deepseek.com/v1 HTTP/1.1\r\n\r\n"),
        ("小写 connect",
         b"connect api.deepseek.com:443 http/1.1\r\n\r\n"),
    ])
    def test_shape_is_blocked(self, name, payload):
        with pytest.raises(DeepSeekEgressBlocked):
            self._scan(payload)

    @pytest.mark.parametrize("name,payload", [
        ("合法 Host", b"GET / HTTP/1.1\r\nHost: open.bigmodel.cn\r\n\r\n"),
        ("合法 CONNECT", b"CONNECT open.bigmodel.cn:443 HTTP/1.1\r\n\r\n"),
        ("合法绝对 URI", b"GET http://www.baidu.com/s HTTP/1.1\r\n\r\n"),
        ("body 里的 deepseek URL", b'{"url": "http://api.deepseek.com/v1"}'),
    ])
    def test_control_is_not_blocked(self, name, payload):
        """**对照**：合法目标 / body 内容不得误伤（否则载荷类用例会假红）。"""
        self._scan(payload)      # 不抛异常即通过


# ══════════════════════════════════════════════════════════════════
# 9) r9：TLS 入口面（C1 合取判据 / C2 同族入口）+ 文件对象面 + 同族写路径
# ══════════════════════════════════════════════════════════════════
#
# ⚠️ **地址分工**（r9 实测踩到的顺序依赖：`_allowed_ips` / `_proxy_ips` 是**会话级**学习集、
#    谁都不清 —— 同一探针"单独跑 BLOCK、串在设过 `HTTPS_PROXY` 的用例之后 ALLOW"）：
#      ATTACK_PEER     = 攻击面对端：**任何**用例都不许把它学进两个学习集
#      PROXY_PEER      = 明文代理正向对照（会被学进 _proxy_ips → 只给代理类用例用）
#      WHITELIST_PEER  = 直连白名单正向对照（会被学进 _allowed_ips → 只给该用例用）
ATTACK_PEER = "127.0.0.2"
PROXY_PEER = "127.0.0.3"
WHITELIST_PEER = "127.0.0.4"
#: 「参数配代理」的过拦取证用（**专用地址**：不许被别的用例学进任何学习集，
#: 否则该取证会因"会话内学习状态"而假绿 —— r9 实测踩到）
PARAM_PROXY_PEER = "127.0.0.5"


@pytest.fixture(scope="session")
def _tls_cert(tmp_path_factory):
    """自签证书（CN + SAN = `open.bigmodel.cn`）—— 真 TLS 握手用。

    `cryptography` 是**产品自身的依赖**（`src/security/encryption.py` 的 AESGCM 走它），
    所以这里直接 import：缺了就是环境坏了，应当**响亮报错**，不用 skip 藏起来。
    """
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    d = tmp_path_factory.mktemp("k61_r9_tls")
    cert_path, key_path = d / "cert.pem", d / "key.pem"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "open.bigmodel.cn")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("open.bigmodel.cn"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address(ATTACK_PEER)),
                x509.IPAddress(ipaddress.ip_address(PROXY_PEER)),
                x509.IPAddress(ipaddress.ip_address(WHITELIST_PEER)),
            ]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return str(cert_path), str(key_path)


class _LocalSink:
    """本地接收端（**从不转发一个字节**）。三种模式：

    - `"tls"`  ：终结 TLS → 记录**解密后**的载荷（证明明文"进了 TLS 之内"）
    - `"plain"`：记录原始字节
    - `"proxy"`：明文代理桩（读 `CONNECT` → 回 `200 Connection established` → 再终结 TLS）
    """

    def __init__(self, host, mode="tls", cert=None, key=None):
        self.host, self.mode = host, mode
        self.seen: list = []            # 载荷（tls/proxy 模式 = 解密后）
        self.raw_bytes: list = []       # 握手失败前**已经到达的字节**（用来证明"一个字节都没来"）
        self.raw_errors: list = []      # 握手失败的类型名
        self.connect_lines: list = []   # proxy 模式：CONNECT 首行
        if mode in ("tls", "proxy"):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            self.ctx = ctx
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, 0))
        srv.listen(8)
        self.port = srv.getsockname()[1]
        self._srv = srv
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        self._srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(2)
                if self.mode == "plain":
                    self.seen.append(bytes(conn.recv(4096)))
                    conn.close()
                    continue
                if self.mode == "proxy":
                    buf = b""
                    while b"\r\n\r\n" not in buf and len(buf) < 4096:
                        chunk = conn.recv(1024)
                        if not chunk:
                            break
                        buf += chunk
                    self.connect_lines.append(buf.split(b"\r\n", 1)[0])
                    conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                tls = self.ctx.wrap_socket(conn, server_side=True)
                self.seen.append(bytes(tls.recv(4096)))
                tls.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                tls.close()
            except Exception as exc:
                # 记录**已经到达的字节**（+ 失败类型）：用来证明"一个字节都没来"
                try:
                    conn.settimeout(0.5)
                    self.raw_bytes.append(bytes(conn.recv(64)))
                except Exception:
                    self.raw_bytes.append(b"")
                self.raw_errors.append(type(exc).__name__.encode())
                try:
                    conn.close()
                except Exception:
                    pass

    def payloads(self, wait=0.35):
        """等接收线程落证据后返回收到的载荷（等待是必要的：证据是异步落下的）。"""
        time.sleep(wait)
        return b"".join(x for x in self.seen if x)

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except Exception:
            pass
        self._t.join(timeout=2)


@pytest.fixture
def make_sink(_tls_cert):
    """按地址造监听器的工厂（测试结束统一关掉）。"""
    made = []

    def _make(host, mode="tls"):
        s = _LocalSink(host, mode=mode, cert=_tls_cert[0], key=_tls_cert[1])
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()


@pytest.fixture
def tls_cert_path(_tls_cert):
    return _tls_cert[0]


def monkeypatch_delenv(var):
    """删一个环境变量（本文件在**不接 monkeypatch 夹具**的取证用例里也要清代理）。"""
    import os as _os
    _os.environ.pop(var, None)


def _assert_peer_not_learned(ip):
    """前置判据：**攻击面对端**不得出现在两个"可接受对端"学习集里（否则本用例无判别力）。"""
    g = k61conftest._GUARD
    with g._lock:
        assert ip not in g._allowed_ips, f"{ip} 被学进白名单学习集（地址分工被破坏）"
        assert ip not in g._proxy_ips, f"{ip} 被学进代理学习集（地址分工被破坏）"


TLS_LINE = b"CONNECT api.deepseek.com:443 HTTP/1.1\r\nHost: api.deepseek.com\r\n\r\n"


class TestTlsSniCannotLie:
    """C1（r9 修）：**SNI 撒谎必须拦得住**。

    判据是**合取**：① 有效 SNI 主机在白名单 **且** ② 真实对端可接受
    （白名单主机学到的 IP / **明文代理**学到的 IP）。

    r8 为了收窄误杀把②整个删掉 ⇒ SNI 说了算 ⇒ 用**文档化的库参数**就能把
    真实目标塞进 TLS 之内（本批逐条复现，原始输出见报告）。
    """

    def test_lying_sni_to_local_endpoint_blocked(self, make_sink):
        """裸 `wrap_socket`：SNI 自称白名单、对端是本机监听器 → 必须拦，且**一个字节都不出去**。"""
        _assert_peer_not_learned(ATTACK_PEER)
        sink = make_sink(ATTACK_PEER)
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(PublicEgressBlocked) as ei:
                ssl._create_unverified_context().wrap_socket(
                    s, server_hostname="open.bigmodel.cn")
            assert "合取" in str(ei.value), "报文应说明合取判据（可读性）"
            _assert_blocked_by(ei.value, "ssl.SSLContext.wrap_socket（客户端 TLS）")
        finally:
            s.close()
        assert sink.payloads() == b"", "明文出线了"
        arrived = b"".join(sink.raw_bytes)
        assert arrived == b"", f"有字节到达监听器：{arrived[:40]!r}"

    def test_lying_sni_via_httpx_sni_hostname_extension_blocked(self, make_sink):
        """**文档化参数**：`httpx` 的 `extensions={"sni_hostname": …}`（审查者用的那条）。"""
        _assert_peer_not_learned(ATTACK_PEER)
        sink = make_sink(ATTACK_PEER)
        with pytest.raises(EgressBlocked) as ei:
            with httpx.Client(verify=False, timeout=3) as cl:
                cl.request("GET", "https://%s:%d/" % (ATTACK_PEER, sink.port),
                           extensions={"sni_hostname": "open.bigmodel.cn"})
        _assert_blocked_by(ei.value, "ssl.SSLContext.wrap_socket（客户端 TLS）")
        assert sink.payloads() == b""

    def test_lying_sni_via_urllib3_server_hostname_blocked(self, make_sink):
        """**文档化参数**：`urllib3.HTTPSConnectionPool(server_hostname=…)`。"""
        _assert_peer_not_learned(ATTACK_PEER)
        import urllib3
        sink = make_sink(ATTACK_PEER)
        pool = urllib3.HTTPSConnectionPool(ATTACK_PEER, sink.port,
                                           server_hostname="open.bigmodel.cn",
                                           cert_reqs="CERT_NONE", retries=False)
        with pytest.raises(EgressBlocked) as ei:
            pool.urlopen("GET", "/", timeout=3)
        _assert_blocked_by(ei.value, "ssl.SSLContext.wrap_socket（客户端 TLS）")
        assert sink.payloads() == b""

    def test_lying_sni_through_explicit_tls_proxy_blocked(self, make_sink):
        """审查者的原始形态：`proxy="https://…"` + 撒谎 SNI + 真实 DeepSeek 目标。

        这里把目标换成**非白名单公网域名**，让 E 层（httpx 传输层）不参与命中 ——
        这样这条锁**只**钉 F 层（TLS 判据），归因不含糊。
        """
        _assert_peer_not_learned(ATTACK_PEER)
        sink = make_sink(ATTACK_PEER)
        with pytest.raises(EgressBlocked) as ei:
            with httpx.Client(verify=False, timeout=3,
                              proxy="https://%s:%d" % (ATTACK_PEER, sink.port)) as cl:
                cl.request("POST", "https://not-whitelisted.example/x", json={"a": 1},
                           extensions={"sni_hostname": "open.bigmodel.cn"})
        # 归因：这条路只有 F 层在拦（日志实测：E 层对该 URL 不参与命中）
        _assert_blocked_by(ei.value, "ssl.SSLContext.wrap_socket（客户端 TLS）")
        assert sink.payloads() == b"", "CONNECT 明文进了 TLS 之内并到达监听器"

    def test_whitelisted_sni_with_learned_peer_is_allowed(self, make_sink):
        """**正向对照（不许误杀）**：白名单主机解析出的 IP 上的客户端 TLS 必须放行。"""
        sink = make_sink(WHITELIST_PEER)
        k61conftest._GUARD._learn_allowed_ip("open.bigmodel.cn", socket.AF_INET,
                                             (WHITELIST_PEER, sink.port))
        s = socket.create_connection((WHITELIST_PEER, sink.port), timeout=3)
        tls = ssl._create_unverified_context().wrap_socket(s, server_hostname="open.bigmodel.cn")
        try:
            tls.sendall(b"GET /api/paas/v4/models HTTP/1.1\r\nHost: open.bigmodel.cn\r\n\r\n")
        finally:
            tls.close()
        assert b"GET /api/paas/v4/models" in sink.payloads(), "白名单 TLS 被误杀（没有请求到达）"

    def test_plaintext_proxy_env_whitelisted_tls_is_allowed(self, make_sink, tls_cert_path,
                                                            monkeypatch):
        """**r8 的误杀不许回来**（r9 必须实测的那一条）：配了**明文** `http://` 代理
        （环境变量）时，白名单主机的 TLS 是**端到端**的（对端=代理、SNI=目标）→ 必须放行。

        做法：本机监听器扮演**明文代理**（收 CONNECT → 回 200 → 终结 TLS），
        `requests` 走 `HTTPS_PROXY`，`verify=` 指到本批自签证书。断言三件事：
        ① 没有守卫异常；② 代理**明文**收到 `CONNECT open.bigmodel.cn:443`；
        ③ TLS 之内真的过去了请求（解密后可见）。
        """
        sink = make_sink(PROXY_PEER, "proxy")
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://%s:%d" % (PROXY_PEER, sink.port))
        monkeypatch.setenv("https_proxy", "http://%s:%d" % (PROXY_PEER, sink.port))
        import requests
        r = requests.get("https://open.bigmodel.cn/api/paas/v4/models", timeout=3,
                         verify=tls_cert_path)
        assert r.status_code == 502, "没走到那个桩代理（说明链路被改坏了）"
        assert sink.connect_lines and \
            sink.connect_lines[0] == b"CONNECT open.bigmodel.cn:443 HTTP/1.1", \
            f"代理没收到预期的 CONNECT：{sink.connect_lines!r}"
        assert b"GET /api/paas/v4/models" in sink.payloads()


class TestTlsEntryPointsHooked:
    """C2（r9）：TLS 入口的**同族面** —— r8 只钩了 `wrap_socket`/`wrap_bio` 两个"友好入口"。

    本批实测（原始输出见报告）：`ssl.SSLSocket._create(sock=…, server_hostname="127.0.0.1")`、
    `ssl.SSLObject._create(…, server_hostname="api.deepseek.com")`、
    `ctx._wrap_socket(sock, False, "open.bigmodel.cn")`、`ctx._wrap_bio(…, "api.deepseek.com")`
    **四条全部 0 反应**。r9 全部纳入：前两条是 **Python 类方法**（可直接挂钩）；
    后两条是 **C 方法**（`_ssl._SSLContext` 不可赋值）→ 在 Python 子类 `ssl.SSLContext` 上
    **遮蔽**同名属性（一切经实例的调用都被覆盖；**未绑定直调**仍是声明的残留）。
    """

    def test_sslsocket_create_lying_sni_blocked(self, make_sink):
        _assert_peer_not_learned(ATTACK_PEER)
        sink = make_sink(ATTACK_PEER)
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(PublicEgressBlocked) as ei:
                ssl.SSLSocket._create(sock=s, server_hostname="open.bigmodel.cn",
                                      context=ssl._create_unverified_context())
            _assert_blocked_by(ei.value, "ssl.SSLSocket._create（客户端 TLS）")
        finally:
            s.close()
        assert sink.payloads() == b""

    def test_sslsocket_create_non_whitelisted_sni_blocked(self, make_sink):
        """SNI=对端 IP（非白名单）→ 拒（r8 时期这条 0 反应）。"""
        sink = make_sink(ATTACK_PEER)
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(PublicEgressBlocked):
                ssl.SSLSocket._create(sock=s, server_hostname=ATTACK_PEER,
                                      context=ssl._create_unverified_context())
        finally:
            s.close()

    def test_sslobject_create_non_whitelisted_sni_blocked(self):
        """`SSLObject._create`（BIO 家族，无对端可判 → 只判 SNI）。"""
        inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
        with pytest.raises(PublicEgressBlocked) as ei:
            ssl.SSLObject._create(inc, out, server_side=False,
                                  server_hostname="api.deepseek.com",
                                  context=ssl._create_unverified_context())
        _assert_blocked_by(ei.value, "ssl.SSLObject._create（客户端 TLS / BIO）")

    def test_sslobject_create_whitelisted_sni_allowed(self):
        """**正向对照**：白名单 SNI 不得被误杀（BIO 家族的正常用法）。"""
        inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
        obj = ssl.SSLObject._create(inc, out, server_side=False,
                                    server_hostname="open.bigmodel.cn",
                                    context=ssl._create_unverified_context())
        assert obj is not None

    def test_sslcontext_wrap_socket_shadow_lying_sni_blocked(self, make_sink):
        """**C 层入口的遮蔽层**：`ctx._wrap_socket(sock, False, "open.bigmodel.cn")`。"""
        _assert_peer_not_learned(ATTACK_PEER)
        sink = make_sink(ATTACK_PEER)
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(PublicEgressBlocked) as ei:
                ssl._create_unverified_context()._wrap_socket(
                    s, False, "open.bigmodel.cn", owner=None, session=None)
            _assert_blocked_by(ei.value, "ssl.SSLContext._wrap_socket（C 层入口的遮蔽层）")
        finally:
            s.close()
        assert sink.payloads() == b""

    def test_sslcontext_wrap_bio_shadow_non_whitelisted_sni_blocked(self):
        """同上（`ctx._wrap_bio(…)`，BIO 家族）。"""
        inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
        with pytest.raises(PublicEgressBlocked) as ei:
            ssl._create_unverified_context()._wrap_bio(
                inc, out, server_side=False, server_hostname="api.deepseek.com",
                owner=None, session=None)
        _assert_blocked_by(ei.value, "ssl.SSLContext._wrap_bio（C 层入口的遮蔽层 / 降级）")

    # ── r8 那条路径：**r8 声称"每条路径都有行为锁"，但 `grep wrap_bio tests/` = 0 命中 ──

    def test_wrap_bio_non_whitelisted_sni_blocked(self):
        """`SSLContext.wrap_bio`：非白名单 SNI → 拦（**r8 时期完全没有锁**，这是补的）。"""
        inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
        with pytest.raises(PublicEgressBlocked) as ei:
            ssl._create_unverified_context().wrap_bio(
                inc, out, server_hostname="api.deepseek.com")
        _assert_blocked_by(ei.value, "ssl.SSLContext.wrap_bio（客户端 TLS / 异步家族）")

    def test_wrap_bio_missing_sni_fail_closed(self):
        """`server_hostname=None` → fail-closed（判不了就拒）。"""
        inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
        with pytest.raises(PublicEgressBlocked):
            ssl._create_unverified_context().wrap_bio(inc, out)

    def test_wrap_bio_whitelisted_sni_allowed(self):
        """**正向对照**：白名单 SNI 的 `wrap_bio` 必须放行（否则异步 HTTPS 全废）。"""
        inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
        obj = ssl._create_unverified_context().wrap_bio(
            inc, out, server_hostname="open.bigmodel.cn")
        assert obj is not None

    def test_sslsocket_create_with_learned_peer_allowed(self, make_sink):
        """**正向对照**：白名单 SNI + 白名单学到的对端 → `SSLSocket._create` 全程放行（真握手）。"""
        sink = make_sink(WHITELIST_PEER)
        k61conftest._GUARD._learn_allowed_ip("open.bigmodel.cn", socket.AF_INET,
                                             (WHITELIST_PEER, sink.port))
        s = socket.create_connection((WHITELIST_PEER, sink.port), timeout=3)
        obj = ssl.SSLSocket._create(sock=s, server_hostname="open.bigmodel.cn",
                                    context=ssl._create_unverified_context())
        try:
            obj.sendall(b"GET /v1/models HTTP/1.1\r\nHost: open.bigmodel.cn\r\n\r\n")
        finally:
            obj.close()
        assert b"GET /v1/models" in sink.payloads()


class TestFileObjectOverSocketFd:
    """I1（r9）：**把 socket fd 包成文件对象**的族。

    r8 时期实测（原始输出见报告）：`io.FileIO(sock_fd, "wb")` / `os.fdopen(sock_fd, "wb")` /
    `shutil.copyfileobj(f, os.fdopen(sock_fd, "wb"))` 三条**全是公开 API、全部 0 反应**、
    明文原样出线，而且**不在任何声明里**（灰区）。根因：`FileIO.write` 是 C 层直写 fd，
    绕过 `os.write` / `socket.send` 全部钩子。
    处置：**fail-closed**（只在这个 fd 真的是 socket 时触发 → 正常文件 I/O 零影响）。
    """

    def test_io_fileio_socket_fd_blocked(self, make_sink):
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(PublicEgressBlocked) as ei:
                io.FileIO(s.fileno(), "wb")
            _assert_blocked_by(ei.value, "io.FileIO（socket fd ← 文件对象）")
        finally:
            s.close()
        assert sink.payloads() == b""

    def test_os_fdopen_socket_fd_blocked(self, make_sink):
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(PublicEgressBlocked) as ei:
                os.fdopen(s.fileno(), "wb")
            _assert_blocked_by(ei.value, "os.fdopen（socket fd ← 文件对象）")
        finally:
            s.close()
        assert sink.payloads() == b""

    def test_builtins_open_socket_fd_blocked(self, make_sink):
        """`open(fd, "wb")`（内建）—— 与 `io.open` 是**两个绑定**，只补一处会漏。"""
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(PublicEgressBlocked):
                open(s.fileno(), "wb")
        finally:
            s.close()
        assert sink.payloads() == b""

    def test_io_open_socket_fd_blocked(self, make_sink):
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(PublicEgressBlocked):
                io.open(s.fileno(), "wb")
        finally:
            s.close()

    def test_copyfileobj_into_socket_file_blocked(self, make_sink, tmp_path):
        """`shutil.copyfileobj(src, 文件对象)`：接收端是 socket 支撑 → **逐块判内容**。

        接收端用 **`_io.FileIO`**（见 DECLARED_LIMITATIONS 的残余项：它不经 `io` 模块属性
        → 没有任何钩子）→ 于是这条锁**只**钉 `copyfileobj` 这一层，归因不含糊。
        """
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        src = tmp_path / "payload.bin"
        src.write_bytes(TLS_LINE)
        try:
            with pytest.raises(EgressBlocked) as ei:
                with open(str(src), "rb") as fsrc:
                    # closefd=False：别让 FileIO 把 socket 的 fd 关掉（否则 finally 里的 s.close() 会 EBADF）
                    shutil.copyfileobj(fsrc, _io.FileIO(s.fileno(), "wb", closefd=False))
            # 归因：copyfileobj 的代理把**内容**交给 D 层扫描器 → 标签是 D 层的。
            # 本层的牙由"单摘 copyfileobj 就完全没有拦截"证明（见 r9 报告）。
            _assert_blocked_by(ei.value, "socket 明文请求头")
        finally:
            s.close()
        assert sink.payloads() == b""

    def test_copyfileobj_benign_content_to_socket_file_is_not_blocked(self, make_sink, tmp_path):
        """**正向对照**：内容可判且无害 → 不误杀（本地 socket 上拷普通数据照常）。"""
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        src = tmp_path / "benign.bin"
        src.write_bytes(b"hello local socket\n")
        try:
            with open(str(src), "rb") as fsrc:
                shutil.copyfileobj(fsrc, _io.FileIO(s.fileno(), "wb", closefd=False))
        finally:
            s.close()
        assert sink.payloads() == b"hello local socket\n"

    def test_socket_makefile_writes_are_still_scanned(self, make_sink):
        """**声明项的正向锁**：`makefile('wb')` 不是独立入口 —— 其写经 `socket.send` → 仍被拦。"""
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        f = s.makefile("wb")
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                f.write(TLS_LINE)
                f.flush()
        finally:
            try:
                s.close()
            except Exception:
                pass
        assert b"api.deepseek.com" not in sink.payloads()

    def test_bufferedwriter_over_socketio_is_still_scanned(self, make_sink):
        """**声明项的正向锁**：`io.BufferedWriter(socket.SocketIO(...))` —— 写经 `socket.send` → 拦。"""
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        bw = io.BufferedWriter(socket.SocketIO(s, "wb"))
        try:
            with pytest.raises(DeepSeekEgressBlocked):
                bw.write(TLS_LINE)
                bw.flush()
        finally:
            try:
                s.close()
            except Exception:
                pass
        assert b"api.deepseek.com" not in sink.payloads()

    def test_normal_file_io_is_untouched(self, tmp_path):
        """**正向对照（零误杀）**：四个被挂钩的文件 API 对**真文件**一行不受影响。"""
        p = tmp_path / "plain.txt"
        with open(str(p), "w") as f:
            f.write("内建 open 正常\n")
        with os.fdopen(os.open(str(p), os.O_RDWR), "w") as f:
            f.write("os.fdopen 正常\n")
        assert io.FileIO(str(p), "r").read()
        with open(str(p), "rb") as a, io.open(str(tmp_path / "copy.txt"), "wb") as b:
            shutil.copyfileobj(a, b)
        assert (tmp_path / "copy.txt").read_bytes() == p.read_bytes()


class TestSameFamilyWritePaths:
    """C2 ③ + r9 新发现：`os.splice`（file→pipe→socket）与 `os.eventfd_write`（值写进 fd）。

    两条都是**前缀猜名字**必然漏掉的形态（本批实测：明文/8 字节真的到了监听器而守卫 0 反应）。
    """

    def test_os_splice_into_socket_blocked(self, make_sink, tmp_path):
        sink = make_sink(ATTACK_PEER, "plain")
        src = tmp_path / "payload.bin"
        src.write_bytes(TLS_LINE)
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        r, w = os.pipe()
        f = os.open(str(src), os.O_RDONLY)
        try:
            with pytest.raises(EgressBlocked) as ei:
                n = os.splice(f, w, 4096)               # file → pipe（合法，不拦）
                os.splice(r, s.fileno(), n)             # pipe → socket（**拦**）
            _assert_blocked_by(ei.value, "os.splice（socket ← file/pipe）")
        finally:
            for fd in (f, r, w):
                try:
                    os.close(fd)
                except Exception:
                    pass
            s.close()
        assert sink.payloads() == b""

    @pytest.mark.parametrize("name", ["posix.splice", "posix.eventfd_write"])
    def test_posix_write_aliases_are_blocked(self, name, make_sink):
        """`posix.*` 是**同族别名**（两个模块属性、同一函数）：只补 `os.*` 会漏。"""
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(EgressBlocked):
                if name == "posix.splice":
                    r, w = os.pipe()
                    os.write(w, TLS_LINE[:16])
                    posix.splice(r, s.fileno(), 16)
                else:
                    posix.eventfd_write(s.fileno(), int.from_bytes(b"CONNECT ", "little"))
        finally:
            s.close()
        assert sink.payloads() == b""

    def test_os_eventfd_write_to_socket_blocked(self, make_sink):
        """`os.eventfd_write(fd, value)`：**实测**一次能把 8 个任意字节（uint64 的
        little-endian）写进 socket → 判定不了内容 → fail-closed。"""
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            with pytest.raises(PublicEgressBlocked) as ei:
                os.eventfd_write(s.fileno(), int.from_bytes(b"CONNECT ", "little"))
            _assert_blocked_by(ei.value, "os.eventfd_write（socket ← 计数器值）")
        finally:
            s.close()
        assert sink.payloads() == b""

    def test_splice_into_pipe_is_not_blocked(self, tmp_path):
        """**正向对照**：file→pipe 的搬运（dst 不是 socket）不得误杀。"""
        src = tmp_path / "x.bin"
        src.write_bytes(b"abc")
        r, w = os.pipe()
        try:
            f = os.open(str(src), os.O_RDONLY)
            n = os.splice(f, w, 3)
            os.close(f)
            assert n == 3
            assert os.read(r, 3) == b"abc"
        finally:
            os.close(r)
            os.close(w)

    def test_eventfd_write_to_real_eventfd_is_not_blocked(self):
        """**正向对照**：写**真的 eventfd** 不得误杀（那才是它的正当用法）。"""
        efd = os.eventfd(0)
        try:
            os.eventfd_write(efd, 1)
        finally:
            os.close(efd)


class TestDeclaredLimitations:
    """**声明面**（r9 的头号整改方向）：把"没覆盖 / 覆盖不全"做成**可检查的登记**。

    r8 的缺陷不是代码没写，而是**声称覆盖了实际没覆盖的东西**（"每条路径都有行为锁"，
    实测 `grep wrap_bio tests/` = 0 命中、把整条守卫摘掉该文件仍 91 passed）。
    所以这里：

    - `DECLARED_LIMITATIONS` 每条必须写清「**可被怎么绕**」+「**实测证据**」+「锁」；
    - 三条 `test_declared_*` 是**取证锁**：它们把"声明的缺陷"钉成事实 —— 一旦被修好
      就会变红，逼后来者回来同步删掉声明（**声明与事实不符时，锁会响**）。
    """

    DECLARED_LIMITATIONS = {
        "ssl.SSLContext.wrap_bio / SSLObject._create / SSLContext._wrap_bio（BIO 家族）": (
            "**降级：只判 SNI，可被撒谎绕过**。`wrap_bio` 拿到的是 `MemoryBIO`，"
            "与 socket 之间没有反向引用 → **没有对端可判** → 合取判据的②在这里做不到。"
            "**实测**：`wrap_bio(server_hostname='open.bigmodel.cn')` 无论 BIO 另一头是谁都放行"
            "（取证：`test_declared_wrap_bio_sni_can_lie`）。"
            "不能改成「一律拒」——那会把所有走 asyncio 的 HTTPS（含免费 GLM 正规链路）打死。"
        ),
        "`_ssl._SSLContext._wrap_socket/_wrap_bio` 的**未绑定直调**": (
            "C 类型不可赋值，我们只能在其 Python 子类 `ssl.SSLContext` 上**遮蔽**同名属性；"
            "`_ssl._SSLContext._wrap_bio(ctx, …)` 这种显式未绑定调用绕过遮蔽层。"
            "**实测**：0 反应（取证：`test_declared_unbound_c_level_wrap_bio_can_slip`）。"
        ),
        "`_io.FileIO`（直接从 C 模块取类）": (
            "我们把 `io.FileIO` 换成了守卫子类，但 `_io.FileIO` 是**同一个 C 类型**的另一条绑定"
            "（`io.FileIO is _io.FileIO` 为 True，改的是 `io` 模块属性）→ "
            "`from _io import FileIO; FileIO(sock_fd, 'wb')` 不经钩子。"
            "**实测**：0 反应（取证：`test_declared_io_fileio_c_level_import_can_slip`）。"
            "（`shutil.copyfileobj` 那一层仍能兜住这种对象的写入 —— 见其行为锁。）"
        ),
        "`_socket.socket.send*`（C 类型方法）": (
            "**实测**：不可挂钩（`TypeError: cannot set … of immutable type '_socket.socket'`）→ 见 "
            "`TestSendFamilyApiSurface.DECLARED_UNCOVERABLE`；兜底是 pin（无生产 key）。"
        ),
        "子进程（子解释器 / curl / fork+exec）与 ctypes 裸系统调用": (
            "socket 在别的进程 / 不经 Python 属性查找 → 本进程钩子天然看不到。"
            "**取证**：`test_declared_subprocess_can_slip_and_pin_is_inherited`（子进程真把字节送出去）"
            "与 `test_declared_ctypes_raw_syscall_can_slip`（直调 libc `write(2)`，0 反应）。"
            "兜底：`DEEPSEEK_API_KEY` pin 成空串且**被继承**（结构性，同一条取证用例里断言）。"
        ),
        "残留（r9 实测、**未修**）：环境变量声称明文代理 + 撒谎 SNI": (
            "把 `HTTPS_PROXY` 指到一个**能终结 TLS 的端点**（并真的连过它 → 该端点 IP 落入"
            "`_proxy_ips`），再对同一端点发撒谎 SNI 的客户端 TLS → **放行**，明文进 TLS 之内。"
            "**实测**：`test_declared_env_plaintext_proxy_endpoint_plus_lying_sni_can_slip`。"
            "为什么未修：要收紧就得引入「该 fd 上是否见过明文 CONNECT」的关联判据 —— "
            "那既**放宽**（攻击方可先写一行无害 CONNECT）又**收紧**（参数配的明文代理会误杀），"
            "且本机没有真代理环境可验证，属需拍板的语义变更（报告 §r9-残留有完整分析）。"
        ),
        "**过拦**（不是漏拦）：`proxies=` **参数**配的明文代理": (
            "对端判据只认「白名单学到的 IP」与「**环境变量**里配的明文代理 IP」；"
            "用**参数**配的明文代理其 IP 不在 `_proxy_ips` 里 → 白名单主机的 TLS 被拒"
            "（r7 形态的误杀）。**实测**：单独跑 `proxies={...}` 的 requests 会被拒。"
            "影响面：本仓 `grep -rn 'proxies='` = 0 命中（无此用法）；机器级代理走环境变量 → 已覆盖。"
        ),
    }

    def test_every_declaration_has_evidence_and_lock(self):
        """结构锁：每条声明必须写清"可被怎么绕 + 实测"，不许只有一句"已声明"。"""
        for key, text in self.DECLARED_LIMITATIONS.items():
            assert "实测" in text or "取证" in text, f"{key} 缺实测依据"
            assert len(text) >= 40, key

    # ── 取证锁（**声明与事实不符时，这几条会响**）──

    def test_declared_wrap_bio_sni_can_lie(self):
        """**取证**：BIO 家族只判 SNI → 撒谎的 SNI 挡不住（这是**声明的缺陷**，不是期望行为）。

        ⚠️ 若哪天这条红了，说明 BIO 家族被修好了 → 请同步删掉
        `DECLARED_LIMITATIONS` 里对应条目，并把这条改成断言 BLOCK。
        """
        inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
        obj = ssl._create_unverified_context().wrap_bio(
            inc, out, server_hostname="open.bigmodel.cn")   # 白名单 SNI（对端是谁它看不见）
        assert obj is not None, "BIO 家族开始判对端了？→ 更新声明"

    def test_declared_unbound_c_level_wrap_bio_can_slip(self):
        """**取证**：`_ssl._SSLContext._wrap_bio(ctx, …)` 未绑定直调 → 绕过遮蔽层。

        ⚠️ 若这条红了 → 说明 C 层直调也被覆盖了，请同步更新声明。
        """
        import _ssl
        inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
        obj = _ssl._SSLContext._wrap_bio(
            ssl._create_unverified_context(), inc, out, server_side=False,
            server_hostname="api.deepseek.com", owner=None, session=None)
        assert obj is not None, "C 层直调被覆盖了？→ 更新声明与用例"

    def test_declared_io_fileio_c_level_import_can_slip(self, make_sink):
        """**取证**：`from _io import FileIO` 写 socket fd → 不经任何钩子（声明项）。

        ⚠️ 若这条红了 → 说明 `_io` 那条绑定也被覆盖了，请同步更新声明。
        """
        import _io
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            f = _io.FileIO(s.fileno(), "wb")
            f.write(TLS_LINE)
            f.flush()
        finally:
            s.close()
        assert b"api.deepseek.com" in sink.payloads(), "声明项不再成立？→ 更新声明与用例"

    def test_declared_env_plaintext_proxy_endpoint_plus_lying_sni_can_slip(self, make_sink,
                                                                          monkeypatch):
        """**取证（残留）**：`HTTPS_PROXY` 声称明文代理 + 对它发撒谎 SNI 的 TLS → 放行。

        ⚠️ 若这条红了 → 残留被关掉了，请同步更新声明与报告。
        """
        sink = make_sink(PROXY_PEER)
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://%s:%d" % (PROXY_PEER, sink.port))
        monkeypatch.setenv("https_proxy", "http://%s:%d" % (PROXY_PEER, sink.port))
        # ① 真的连一次"代理端点"（把它的 IP 学进 _proxy_ips —— 与真实链路同形）
        socket.create_connection((PROXY_PEER, sink.port), timeout=3).close()
        # ② 撒谎 SNI 的客户端 TLS 打到这个端点 → 本批实测：放行
        s = socket.create_connection((PROXY_PEER, sink.port), timeout=3)
        tls = ssl._create_unverified_context().wrap_socket(s, server_hostname="open.bigmodel.cn")
        tls.sendall(TLS_LINE)
        tls.close()
        assert b"api.deepseek.com" in sink.payloads(), "残留被关掉了？→ 更新声明与报告"


    def test_declared_subprocess_can_slip_and_pin_is_inherited(self, make_sink):
        """**取证（进程边界）**：子进程里的 socket 不经本进程钩子 → 字节照样出去；
        但**结构性兜底**仍在：子进程**继承** `DEEPSEEK_API_KEY=""`（pin）→ 拿不到生产 key。

        ⚠️ 若"字节出去"这半段红了 → 说明子进程也被覆盖了（例如真做了 sitecustomize 注入），
        请更新声明与用例。
        """
        sink = make_sink(ATTACK_PEER, "plain")
        code = ("import socket;"
                "s=socket.create_connection((%r,%d),timeout=3);"
                "s.sendall(%r);s.close()" % (ATTACK_PEER, sink.port, TLS_LINE))
        out = subprocess.run([sys.executable, "-c", code], env=dict(os.environ),
                             capture_output=True, timeout=120)
        assert out.returncode == 0, out.stderr[-300:]
        assert b"api.deepseek.com" in sink.payloads(), "子进程被覆盖了？→ 更新声明"
        child = subprocess.run(
            [sys.executable, "-c",
             "import os;print(repr(os.environ.get('DEEPSEEK_API_KEY')))"],
            env=dict(os.environ), capture_output=True, text=True, timeout=120)
        assert child.stdout.strip() == "''", f"pin 未被继承：{child.stdout!r}"

    def test_declared_socket_c_type_is_immutable(self):
        """**取证（声明项 4）**：`_socket.socket` 是 C 不可变类型 → 纯 Python 挂不上钩。

        ⚠️ 若这条红了（不再抛 TypeError）→ 说明能挂钩了，请把 `_socket.socket.send*`
        从 `DECLARED_UNCOVERABLE` 移到 `HOOKED` 并补行为锁。
        """
        import _socket
        with pytest.raises(TypeError):
            _socket.socket.send = lambda *a, **k: None      # 赋值失败即证据（不改动任何东西）

    def test_declared_param_proxy_over_block(self, make_sink, tls_cert_path):
        """**取证（过拦面）**：用 **`proxies=` 参数**配明文代理时，白名单主机的 TLS 会被拒。

        这是 r7 形态的**过拦**（对端判据只认"白名单学到的 IP ∪ **环境变量**里的明文代理 IP"，
        参数配的代理 IP 不在集合里）—— 是**声明的缺陷**，不是期望行为。
        ⚠️ 若这条红了 → 说明参数配的代理也被认了，请同步更新 `DECLARED_LIMITATIONS` 与报告。
        """
        _assert_peer_not_learned(PARAM_PROXY_PEER)     # 专用地址：没被学过才有判别力
        sink = make_sink(PARAM_PROXY_PEER, "proxy")
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            monkeypatch_delenv(var)
        import requests
        with pytest.raises(EgressBlocked):
            requests.get("https://open.bigmodel.cn/api/paas/v4/models", timeout=3,
                         verify=tls_cert_path,
                         proxies={"https": "http://%s:%d" % (PARAM_PROXY_PEER, sink.port)})

    def test_declared_ctypes_raw_syscall_can_slip(self, make_sink):
        """**取证（ctypes）**：直调 libc `write(2)` 不经过任何 Python 属性查找 → 守卫 0 反应。

        ⚠️ 若这条红了 → 说明连裸系统调用也被覆盖了，请同步更新声明。
        """
        import ctypes
        sink = make_sink(ATTACK_PEER, "plain")
        s = socket.create_connection((ATTACK_PEER, sink.port), timeout=3)
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            buf = ctypes.create_string_buffer(TLS_LINE)
            written = libc.write(s.fileno(), buf, len(TLS_LINE))
            assert written == len(TLS_LINE), f"libc.write 只写了 {written}"
        finally:
            s.close()
        assert b"api.deepseek.com" in sink.payloads(), "ctypes 被覆盖了？→ 更新声明"
