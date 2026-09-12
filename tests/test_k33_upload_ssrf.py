# -*- coding: utf-8 -*-
"""k33/A23 + 审查 I2/I3：`image_url` SSRF 白名单加固 + 上传**孤儿**文件 TTL 清理。

- SSRF（审查 I2 加固）：`handler._handle_image` / 两个下载点接入
  `src/bot/image_url_guard.py`——本机/内网地址**默认拒绝**（即便来自
  `PUBLIC_BASE_URL`），显式名单（`CHAT_IMAGE_ALLOWED_HOSTS`）对本机地址要求
  **端口精确**；路径规范化后比对（防 `..` 穿越 / 编码绕过）；下载走
  `safe_urlretrieve`（每次重定向复检，跨主机 → 内网一律拒绝）。
- 清理（审查 I3）：`main.cleanup_chat_uploads` 只清**无引用**的孤儿——
  删除前反查会话/消息/记录里的上传 URL（`_referenced_upload_names`），
  有引用即便超 TTL 也不删；引用索引不可用时保守不删。

运行：OMP_NUM_THREADS=1 /usr/bin/python3 -m pytest tests/test_k33_upload_ssrf.py -q
"""
import base64
import http.server
import os
import sys
import threading
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from src.bot.image_url_guard import (  # noqa: E402
    CHAT_UPLOADS_PATH_PREFIX,
    allowed_image_hosts,
    is_allowed_image_url,
    is_local_or_private_host,
    safe_urlretrieve,
)
from src.main import _referenced_upload_names, cleanup_chat_uploads  # noqa: E402


def _clear_host_env(monkeypatch):
    monkeypatch.delenv("CHAT_IMAGE_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)


# ══════════════════════════════════════════════════════════════
# 1) SSRF 白名单（放行面）
# ══════════════════════════════════════════════════════════════

class TestSsrfWhitelistAllow:
    def test_own_uploads_url_allowed(self, monkeypatch):
        _clear_host_env(monkeypatch)
        assert is_allowed_image_url(
            "https://yilichat.com/api/chat/uploads/abc.jpg")

    def test_explicit_public_hosts_allowed(self, monkeypatch):
        monkeypatch.setenv("CHAT_IMAGE_ALLOWED_HOSTS",
                           "cdn.example.com, https://img.example.net")
        assert is_allowed_image_url("https://cdn.example.com/a/b/c.png")
        assert is_allowed_image_url("https://img.example.net/x.webp")
        assert "cdn.example.com" in allowed_image_hosts()

    def test_explicit_local_host_requires_exact_port(self, monkeypatch):
        """开发预览：本机主机必须显式到端口（不得因 base_url 是本机就放开全端口）。"""
        monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8768")
        monkeypatch.setenv("CHAT_IMAGE_ALLOWED_HOSTS", "127.0.0.1:8768")
        assert is_allowed_image_url(
            "http://127.0.0.1:8768/api/chat/uploads/abc.jpg")
        assert not is_allowed_image_url(
            "http://127.0.0.1:9999/api/chat/uploads/abc.jpg")
        assert not is_allowed_image_url(
            "http://127.0.0.1:8768/api/chat/uploads/../../admin")
        # 裸 IP 条目（不带端口）不得放开本机任意端口
        monkeypatch.setenv("CHAT_IMAGE_ALLOWED_HOSTS", "127.0.0.1")
        assert not is_allowed_image_url(
            "http://127.0.0.1:9999/api/chat/uploads/abc.jpg")

    def test_lan_preview_host_requires_exact_port(self, monkeypatch):
        _clear_host_env(monkeypatch)
        monkeypatch.setenv("CHAT_IMAGE_ALLOWED_HOSTS", "192.168.0.104:8767")
        assert is_allowed_image_url(
            "http://192.168.0.104:8767/api/chat/uploads/x.jpg")
        assert not is_allowed_image_url(
            "http://192.168.0.104:8768/api/chat/uploads/x.jpg")

    def test_uppercase_and_default_port(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://YiliChat.com")
        assert is_allowed_image_url(
            "https://yilichat.com:443/api/chat/uploads/x.jpg")

    def test_uploads_path_prefix_contract(self):
        assert CHAT_UPLOADS_PATH_PREFIX == "/api/chat/uploads/"


# ══════════════════════════════════════════════════════════════
# 2) SSRF 白名单（拒绝面：审查 I2 反例 + 同族变体）
# ══════════════════════════════════════════════════════════════

class TestSsrfWhitelistReject:
    @pytest.mark.parametrize("url", [
        # 审查 I2 主反例：PUBLIC_BASE_URL=127.0.0.1 时旧实现把本机全端口放行
        "http://127.0.0.1:9999/api/chat/uploads/x.jpg",
        "http://127.0.0.1:5432/api/chat/uploads/x.jpg",
        "http://127.0.0.1:8768/api/chat/uploads/x.jpg",
        "http://127.0.0.1:8768/tts",
        # 内网 / 云元数据 / 链路本地
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/api/chat/uploads/x.jpg",
        "http://192.168.1.1/admin",
        "http://172.16.0.9/api/chat/uploads/x.jpg",
        "http://100.64.0.1/api/chat/uploads/x.jpg",
        "http://0.0.0.0/api/chat/uploads/x.jpg",
        # 本机别名 / 单标签内网名
        "http://localhost:8767/api/chat/uploads/x.jpg",
        "http://foo.localhost/api/chat/uploads/x.jpg",
        "http://printer.local/api/chat/uploads/x.jpg",
        "http://intranet/api/chat/uploads/x.jpg",
        # 非标准 IP 字面量（十进制/十六进制/短写）
        "http://2130706433/api/chat/uploads/x.jpg",
        "http://0x7f000001/api/chat/uploads/x.jpg",
        "http://127.1/api/chat/uploads/x.jpg",
        "http://0177.0.0.1/api/chat/uploads/x.jpg",
        # IPv6 与 IPv4-mapped
        "http://[::1]/api/chat/uploads/x.jpg",
        "http://[::ffff:127.0.0.1]/api/chat/uploads/x.jpg",
        "http://[fe80::1]/api/chat/uploads/x.jpg",
        "http://[fc00::1]/api/chat/uploads/x.jpg",
        # 编码 host（%2e 绕过）
        "http://127%2e0%2e0%2e1/api/chat/uploads/x.jpg",
        "http://localhost%2e/api/chat/uploads/x.jpg",
        # 自有域名非上传路径（本机其他端口任意路径 → 拒绝）
        "https://yilichat.com/api/health",
        "https://yilichat.com/admin",
        # 非 http(s) 协议（file:// 读文件 / gopher 等）
        "file:///etc/passwd",
        "ftp://yilichat.com/x.jpg",
        "data:image/png;base64,AAAA",
        # 用户名/口令歧义形态
        "http://yilichat.com@evil.com/api/chat/uploads/x.jpg",
        "https://user:pass@yilichat.com/api/chat/uploads/x.jpg",
        # 空/非法
        "",
        "not a url",
        "https:///api/chat/uploads/x.jpg",
        # 子域不自动放行（白名单是精确域名）
        "https://evil-yilichat.com/api/chat/uploads/x.jpg",
        "https://evil.com/api/chat/uploads/x.jpg",
        # 畸形端口（复审 N1：`urlparse(...).port` 惰性抛 ValueError → 一律拒绝）
        "https://yilichat.com:abc/api/chat/uploads/x.jpg",
        "https://yilichat.com:99999/api/chat/uploads/x.jpg",
        "http://127.0.0.1:abc/api/chat/uploads/x.jpg",
    ])
    def test_rejected(self, url, monkeypatch):
        _clear_host_env(monkeypatch)
        assert is_allowed_image_url(url) is False, url

    @pytest.mark.parametrize("url", [
        # 复审 N1：畸形 URL 必须"拒绝"而非"抛异常"（旧实现 ValueError 逃出守卫 →
        # 非流式回显「处理出错」、流式无 done）
        "https://yilichat.com:abc/api/chat/uploads/x.jpg",
        "https://yilichat.com:99999/api/chat/uploads/x.jpg",
        "http://127.0.0.1:abc/api/chat/uploads/x.jpg",
        "http://yilichat.com:abc@evil.com/api/chat/uploads/x.jpg",
        "http://[::1/x",
        "http://[::1]:abc/api/chat/uploads/x.jpg",
    ])
    def test_malformed_url_rejects_without_raising(self, url, monkeypatch):
        _clear_host_env(monkeypatch)
        assert is_allowed_image_url(url) is False, url  # 不得抛 ValueError/其它异常

    @pytest.mark.parametrize("url", [
        # 审查 I2 主反例：路径穿越绕过前缀判定（旧 startswith 放行）
        "https://yilichat.com/api/chat/uploads/../../../admin",
        "https://yilichat.com/api/chat/uploads/%2e%2e/%2e%2e/admin",
        "https://yilichat.com/api/chat/uploads/%252e%252e/%252e%252e/admin",
        "https://yilichat.com/api/chat/uploads/./../../etc/passwd",
        "http://127.0.0.1:8768/api/chat/uploads/../../admin",
        "https://yilichat.com/api/chat/uploads/..%2f..%2fadmin",
        "https://yilichat.com/api/chat/uploads/a/../../../admin",
        "https://yilichat.com/api/chat/uploads\\..\\..\\admin",
        "https://yilichat.com/api/chat/uploads/x.jpg%00.txt",
    ])
    def test_path_traversal_rejected(self, url, monkeypatch):
        _clear_host_env(monkeypatch)
        assert is_allowed_image_url(url) is False, url

    def test_local_host_classifier(self):
        for h in ("127.0.0.1", "::1", "10.1.2.3", "192.168.0.1", "169.254.1.1",
                  "localhost", "box.local", "nas", "0x7f000001", "2130706433"):
            assert is_local_or_private_host(h) is True, h
        for h in ("yilichat.com", "cdn.example.com", "8.8.8.8", "1.1.1.1"):
            assert is_local_or_private_host(h) is False, h


class TestSafeUrlretrieveRedirect:
    """审查 I2：下载不得跟随跨主机重定向到非白名单/内网（或跟随前复检）。"""

    def _server(self, respond):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, headers, body = respond()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        srv.hits = []
        srv_do = srv.RequestHandlerClass.do_GET

        def _counting_do_get(self):
            srv.hits.append(self.path)
            srv_do(self)

        srv.RequestHandlerClass.do_GET = _counting_do_get
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def _pin(self, monkeypatch, *ports):
        monkeypatch.setenv("CHAT_IMAGE_ALLOWED_HOSTS",
                           ",".join(f"127.0.0.1:{p}" for p in ports))
        # 测试环境无代理：清掉代理并重建 opener（防环境代理干扰断言）
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                  "all_proxy", "ALL_PROXY"):
            monkeypatch.delenv(k, raising=False)
        import src.bot.image_url_guard as guard
        monkeypatch.setattr(guard, "_IMAGE_OPENER", None)

    def test_initial_url_not_whitelisted_no_connection(self, monkeypatch, tmp_path):
        _clear_host_env(monkeypatch)
        out = tmp_path / "x.jpg"
        with pytest.raises(ValueError):
            safe_urlretrieve("http://127.0.0.1:9999/api/chat/uploads/x.jpg",
                             str(out))
        assert not out.exists()

    def test_redirect_to_private_blocked(self, monkeypatch, tmp_path):
        """白名单 URL 302 → 169.254.169.254（云元数据）→ 拒绝，不写文件。"""
        srv = self._server(lambda: (
            302, {"Location": "http://169.254.169.254/latest/meta-data/",
                  "Content-Length": "0"}, b""))
        try:
            self._pin(monkeypatch, srv.server_port)
            out = tmp_path / "out.jpg"
            url = f"http://127.0.0.1:{srv.server_port}/api/chat/uploads/x.jpg"
            with pytest.raises(urllib.error.HTTPError):
                safe_urlretrieve(url, str(out))
            assert not out.exists()
        finally:
            srv.shutdown()

    def test_redirect_to_unpinned_local_port_blocked(self, monkeypatch, tmp_path):
        """302 → 同主机的**未授权端口**同样拒绝（端口精确授权）。"""
        target = self._server(lambda: (
            200, {"Content-Length": "3"}, b"img"))
        redirector = self._server(lambda: (
            302, {"Location":
                  f"http://127.0.0.1:{target.server_port}/api/chat/uploads/y.jpg",
                  "Content-Length": "0"}, b""))
        try:
            self._pin(monkeypatch, redirector.server_port)  # 只授权跳转方端口
            out = tmp_path / "out.jpg"
            url = f"http://127.0.0.1:{redirector.server_port}/api/chat/uploads/x.jpg"
            with pytest.raises(urllib.error.HTTPError):
                safe_urlretrieve(url, str(out))
            assert not out.exists()
            assert target.hits == [], "未授权端口不得被连接"
        finally:
            redirector.shutdown()
            target.shutdown()

    def test_redirect_to_non_whitelisted_public_blocked(self, monkeypatch, tmp_path):
        srv = self._server(lambda: (
            302, {"Location": "https://evil.example/x.jpg",
                  "Content-Length": "0"}, b""))
        try:
            self._pin(monkeypatch, srv.server_port)
            out = tmp_path / "out.jpg"
            url = f"http://127.0.0.1:{srv.server_port}/api/chat/uploads/x.jpg"
            with pytest.raises(urllib.error.HTTPError):
                safe_urlretrieve(url, str(out))
            assert not out.exists()
        finally:
            srv.shutdown()

    def test_redirect_to_allowed_target_followed(self, monkeypatch, tmp_path):
        """正向：302 → 白名单内（端口已授权）目标 → 正常下载（不是一刀切禁跳转）。"""
        target = self._server(lambda: (
            200, {"Content-Length": "3"}, b"img"))
        redirector = self._server(lambda: (
            302, {"Location":
                  f"http://127.0.0.1:{target.server_port}/api/chat/uploads/y.jpg",
                  "Content-Length": "0"}, b""))
        try:
            self._pin(monkeypatch, redirector.server_port, target.server_port)
            out = tmp_path / "out.jpg"
            url = f"http://127.0.0.1:{redirector.server_port}/api/chat/uploads/x.jpg"
            safe_urlretrieve(url, str(out))
            assert out.read_bytes() == b"img"
            assert target.hits == ["/api/chat/uploads/y.jpg"]
        finally:
            redirector.shutdown()
            target.shutdown()


# ══════════════════════════════════════════════════════════════
# 3) handler 下载点收口
# ══════════════════════════════════════════════════════════════

class TestHandlerImageGuard:
    """下载点收口：非白名单 URL 不下载、不回显（引导重新上传）。"""

    def _handler(self):
        from src.bot.handler import MessageHandler
        h = object.__new__(MessageHandler)
        h.llm = None
        h.retriever = None
        return h

    def test_disallowed_url_no_download_no_echo(self, monkeypatch):
        from src.bot import handler as handler_mod
        _clear_host_env(monkeypatch)
        called = []

        def _boom(*a, **k):
            called.append(a)
            raise AssertionError("白名单外 URL 不得进入下载路径")

        monkeypatch.setattr(handler_mod.MessageHandler, "_try_face_reading",
                            lambda *a, **k: _boom())
        monkeypatch.setattr(handler_mod.MessageHandler, "_try_palm_reading",
                            lambda *a, **k: _boom())
        out = self._handler()._handle_image(
            "http://169.254.169.254/latest/meta-data/", "看面相")
        assert called == []
        assert "169.254.169.254" not in out  # 不回显用户给的地址
        assert "重新上传" in out

    def test_loopback_base_url_not_allowed_for_face_reading(self, monkeypatch):
        """审查 I2 回归：生产 PUBLIC_BASE_URL=127.0.0.1:8768 时本机图片也不放行。"""
        monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8768")
        monkeypatch.delenv("CHAT_IMAGE_ALLOWED_HOSTS", raising=False)
        h = self._handler()
        assert h._try_face_reading(
            "http://127.0.0.1:8768/api/chat/uploads/a.jpg", "看图") is None
        assert h._try_palm_reading(
            "http://127.0.0.1:9999/api/chat/uploads/a.jpg", "看图") is None

    def test_malformed_port_url_no_exception_in_handle_image(self, monkeypatch):
        """复审 N1：畸形端口不得抛到调用方（应走「重新上传」引导，不 500/无 done）。"""
        _clear_host_env(monkeypatch)
        h = self._handler()
        for url in ("https://yilichat.com:abc/api/chat/uploads/x.jpg",
                    "https://yilichat.com:99999/api/chat/uploads/x.jpg"):
            out = h._handle_image(url, "看面相")   # 旧实现此处抛 ValueError
            assert "重新上传" in out, url
        # 下载点同样不得抛（返回 None = 落到降级分支）
        assert h._try_face_reading(
            "https://yilichat.com:abc/api/chat/uploads/x.jpg", "看图") is None
        assert h._try_palm_reading(
            "https://yilichat.com:99999/api/chat/uploads/x.jpg", "看图") is None

    def test_allowed_url_reaches_reading_path(self, monkeypatch):
        from src.bot import handler as handler_mod
        _clear_host_env(monkeypatch)
        seen = []
        monkeypatch.setattr(handler_mod.MessageHandler, "_try_face_reading",
                            lambda self, url, text, **k: seen.append(url) or "面相结果")
        out = self._handler()._handle_image(
            "https://yilichat.com/api/chat/uploads/a.jpg", "看面相")
        assert seen == ["https://yilichat.com/api/chat/uploads/a.jpg"]
        assert out == "面相结果"

    def test_download_site_uses_safe_urlretrieve(self, monkeypatch):
        """下载点自身也走白名单 + safe_urlretrieve（防未来新调用方绕过）。"""
        import inspect
        from src.bot import handler as handler_mod
        _clear_host_env(monkeypatch)
        src = inspect.getsource(handler_mod.MessageHandler)
        assert "safe_urlretrieve(" in src
        assert "urllib.request.urlretrieve(" not in src


# ══════════════════════════════════════════════════════════════
# 4) 上传孤儿文件清理（引用校验）
# ══════════════════════════════════════════════════════════════

class TestUploadTtlCleanup:
    def _mk(self, d, name, age_hours):
        p = d / name
        p.write_bytes(b"\xff\xd8\xff\xe0old")
        ts = time.time() - age_hours * 3600
        os.utime(p, (ts, ts))
        return p

    def test_expired_orphan_removed_fresh_kept(self, tmp_path):
        old = self._mk(tmp_path, "old.jpg", 100)
        fresh = self._mk(tmp_path, "fresh.jpg", 1)
        stats = cleanup_chat_uploads(ttl_seconds=72 * 3600, dir_path=tmp_path,
                                     referenced=set())
        assert stats["scanned"] == 2 and stats["removed"] == 1
        assert not old.exists() and fresh.exists()

    def test_referenced_file_kept_beyond_ttl(self, tmp_path):
        """审查 I3 主用例：仍被历史消息引用的图片即便超 TTL 也不得删。"""
        keep = self._mk(tmp_path, "keep.jpg", 4 * 24)
        orphan = self._mk(tmp_path, "orphan.jpg", 4 * 24)
        stats = cleanup_chat_uploads(ttl_seconds=72 * 3600, dir_path=tmp_path,
                                     referenced={"keep.jpg"})
        assert stats["removed"] == 1
        assert keep.exists() and not orphan.exists()

    def test_custom_ttl_boundary(self, tmp_path):
        self._mk(tmp_path, "a.jpg", 25)
        assert cleanup_chat_uploads(ttl_seconds=24 * 3600, dir_path=tmp_path,
                                    referenced=set())["removed"] == 1

    def test_ttl_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORTUNE_UPLOAD_TTL_HOURS", "1")
        self._mk(tmp_path, "b.jpg", 3)
        assert cleanup_chat_uploads(dir_path=tmp_path,
                                    referenced=set())["removed"] == 1

    def test_invalid_env_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORTUNE_UPLOAD_TTL_HOURS", "abc")
        self._mk(tmp_path, "c.jpg", 1)
        stats = cleanup_chat_uploads(dir_path=tmp_path, referenced=set())
        assert stats["removed"] == 0
        assert stats["ttl"] == 72 * 3600

    def test_missing_dir_is_noop(self, tmp_path):
        stats = cleanup_chat_uploads(dir_path=tmp_path / "nope")
        assert stats == {"scanned": 0, "removed": 0, "ttl": 72 * 3600}

    def test_subdirectories_untouched(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "keep.jpg").write_bytes(b"x")
        self._mk(tmp_path, "old.jpg", 100)
        stats = cleanup_chat_uploads(ttl_seconds=1, dir_path=tmp_path,
                                     referenced=set())
        assert stats["removed"] == 1
        assert (sub / "keep.jpg").exists()

    def test_ref_index_unavailable_skips_all_deletions(self, tmp_path, monkeypatch):
        """引用索引不可用（密文解不开）→ 保守不删（fail-safe）。"""
        db = tmp_path / "fortune.db"
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE sessions (content TEXT)")
        conn.execute("INSERT INTO sessions (content) VALUES (?)",
                     ("v1:" + base64.b64encode(os.urandom(40)).decode(),))
        conn.commit()
        conn.close()
        monkeypatch.setenv("FORTUNE_DB_PATH", str(db))
        up = tmp_path / "uploads"
        up.mkdir()
        orphan = self._mk(up, "orphan.jpg", 100)
        stats = cleanup_chat_uploads(ttl_seconds=72 * 3600, dir_path=up)
        assert stats["ref_index"] == "unavailable"
        assert stats["removed"] == 0
        assert orphan.exists()


class TestUploadReferenceIndex:
    """引用反查：会话/消息/记录里的上传 URL → 文件名集合（只读、不写库）。"""

    def _db_with_sessions(self, path, rows):
        import sqlite3
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE sessions (user_id TEXT, role TEXT, content TEXT)")
        for content in rows:
            conn.execute("INSERT INTO sessions VALUES ('u1', 'assistant', ?)",
                         (content,))
        conn.commit()
        conn.close()

    def test_missing_db_is_empty_set(self, tmp_path):
        assert _referenced_upload_names(tmp_path / "nope.db") == set()

    def test_db_without_reference_tables_is_empty_set(self, tmp_path):
        db = tmp_path / "empty.db"
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE unrelated (x TEXT)")
        conn.commit()
        conn.close()
        assert _referenced_upload_names(db) == set()

    def test_encrypted_and_plaintext_references(self, tmp_path):
        from src.storage.session_dao import _encrypt_text
        db = tmp_path / "refs.db"
        self._db_with_sessions(db, [
            _encrypt_text("📷 已收到图片：https://yilichat.com/api/chat/uploads/"
                          "aaa.jpg\n分析结果…"),
            "旧明文行：http://127.0.0.1:8768/api/chat/uploads/bbb.jpg",
            "无关消息：今天天气不错",
            None,
        ])
        assert _referenced_upload_names(db) == {"aaa.jpg", "bbb.jpg"}

    def test_deleted_history_stops_referencing(self, tmp_path):
        from src.storage.session_dao import _encrypt_text
        db = tmp_path / "refs2.db"
        self._db_with_sessions(db, [
            _encrypt_text("https://yilichat.com/api/chat/uploads/ccc.jpg"),
        ])
        assert _referenced_upload_names(db) == {"ccc.jpg"}
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM sessions")
        conn.commit()
        conn.close()
        assert _referenced_upload_names(db) == set()

    def test_undecryptable_ciphertext_returns_none(self, tmp_path):
        db = tmp_path / "bad.db"
        self._db_with_sessions(
            db, ["v1:" + base64.b64encode(os.urandom(40)).decode()])
        assert _referenced_upload_names(db) is None

    def test_reference_scan_does_not_write_db(self, tmp_path):
        """只读保证：扫描前后库内容（含密文原值）逐字节一致。"""
        from src.storage.session_dao import _encrypt_text
        db = tmp_path / "ro.db"
        cipher = _encrypt_text("https://yilichat.com/api/chat/uploads/ddd.jpg")
        self._db_with_sessions(db, [cipher])
        assert _referenced_upload_names(db) == {"ddd.jpg"}
        import sqlite3
        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT content FROM sessions").fetchall()
        conn.close()
        assert rows == [(cipher,)], "引用反查不得改写库内容"

    def test_cleanup_uses_sessions_reference_end_to_end(self, tmp_path, monkeypatch):
        """端到端：超 TTL 但被 sessions 消息引用 → 保留；无引用孤儿 → 清理。"""
        from src.storage.session_dao import _encrypt_text
        db = tmp_path / "fortune.db"
        self._db_with_sessions(db, [
            _encrypt_text("📷 已收到图片：https://yilichat.com/api/chat/uploads/"
                          "keep.jpg 这是你的面相分析"),
        ])
        monkeypatch.setenv("FORTUNE_DB_PATH", str(db))
        up = tmp_path / "uploads"
        up.mkdir()
        keep, orphan = up / "keep.jpg", up / "orphan.jpg"
        for p in (keep, orphan):
            p.write_bytes(b"\xff\xd8\xff\xe0old")
            ts = time.time() - 4 * 24 * 3600
            os.utime(p, (ts, ts))
        stats = cleanup_chat_uploads(ttl_seconds=72 * 3600, dir_path=up)
        assert stats["removed"] == 1 and stats["scanned"] == 2
        assert keep.exists() and not orphan.exists()
