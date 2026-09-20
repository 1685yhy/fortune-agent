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
import threading
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


class TestFragmentWriteBypass:
    """C2：分片 / memoryview / sendmsg / os.write 四种写路径都必须判得出。

    审查者实测 r3：这四种**全部**把 `CONNECT api.deepseek.com:443` 送出而守卫静默
    （同文件内"单块整写被拦"的对照证明是**判定漏**、不是观测漏）。
    """

    LINE = b"CONNECT api.deepseek.com:443 HTTP/1.1\r\n\r\n"

    @pytest.fixture
    def local_sink(self):
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
