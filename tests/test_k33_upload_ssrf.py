# -*- coding: utf-8 -*-
"""k33/A23：上传孤儿文件 TTL 清理 + `image_url` SSRF 面（白名单制）。

- SSRF：`handler._handle_image` / 两个 urlretrieve 下载点接入
  `src/bot/image_url_guard.py` 白名单（本服务自有域名 + `/api/chat/uploads/`
  路径，或显式配置的自有 CDN）——内网/云元数据/本机其他端口一律拒绝；
- TTL：`main.cleanup_chat_uploads` 按 mtime 清理过期上传文件，lifespan 起
  每小时 worker（本文件覆盖清理函数语义，不拉起服务）。

运行：OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 -m pytest \
      tests/test_k33_upload_ssrf.py -q
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from src.bot.image_url_guard import (  # noqa: E402
    CHAT_UPLOADS_PATH_PREFIX,
    allowed_image_hosts,
    is_allowed_image_url,
)
from src.main import cleanup_chat_uploads  # noqa: E402

# ══════════════════════════════════════════════════════════════
# 1) SSRF 白名单
# ══════════════════════════════════════════════════════════════

class TestSsrfWhitelist:
    def test_own_uploads_url_allowed(self, monkeypatch):
        monkeypatch.delenv("CHAT_IMAGE_ALLOWED_HOSTS", raising=False)
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        assert is_allowed_image_url(
            "https://yilichat.com/api/chat/uploads/abc.jpg")

    def test_public_base_url_host_allowed(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8767")
        monkeypatch.delenv("CHAT_IMAGE_ALLOWED_HOSTS", raising=False)
        assert is_allowed_image_url(
            "http://127.0.0.1:8767/api/chat/uploads/abc.jpg")

    def test_cdn_host_from_env_allowed(self, monkeypatch):
        monkeypatch.setenv("CHAT_IMAGE_ALLOWED_HOSTS", "cdn.example.com, https://img.example.net")
        assert is_allowed_image_url("https://cdn.example.com/a/b/c.png")
        assert is_allowed_image_url("https://img.example.net/x.webp")
        assert "cdn.example.com" in allowed_image_hosts()

    @pytest.mark.parametrize("url", [
        # 内网/本机（未在 self_hosts 内）
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8768/tts",
        "http://localhost:8767/api/health",
        "http://10.0.0.5/api/chat/uploads/x.jpg",
        "http://192.168.1.1/admin",
        # 自有域名但非上传路径（本机其他端口任意路径 → 拒绝）
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
    ])
    def test_rejected(self, url, monkeypatch):
        monkeypatch.delenv("CHAT_IMAGE_ALLOWED_HOSTS", raising=False)
        assert is_allowed_image_url(url) is False, url

    def test_uppercase_and_port_insensitive(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://YiliChat.com")
        assert is_allowed_image_url(
            "https://yilichat.com:443/api/chat/uploads/x.jpg")

    def test_uploads_path_prefix_contract(self):
        assert CHAT_UPLOADS_PATH_PREFIX == "/api/chat/uploads/"


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
        monkeypatch.delenv("CHAT_IMAGE_ALLOWED_HOSTS", raising=False)
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

    def test_allowed_url_reaches_reading_path(self, monkeypatch):
        from src.bot import handler as handler_mod
        monkeypatch.delenv("CHAT_IMAGE_ALLOWED_HOSTS", raising=False)
        seen = []
        monkeypatch.setattr(handler_mod.MessageHandler, "_try_face_reading",
                            lambda self, url, text, **k: seen.append(url) or "面相结果")
        out = self._handler()._handle_image(
            "https://yilichat.com/api/chat/uploads/a.jpg", "看面相")
        assert seen == ["https://yilichat.com/api/chat/uploads/a.jpg"]
        assert out == "面相结果"

    def test_download_site_guard_blocks_second_layer(self, monkeypatch):
        """下载点自身也有白名单（防未来新调用方绕过 _handle_image）。"""
        monkeypatch.delenv("CHAT_IMAGE_ALLOWED_HOSTS", raising=False)
        h = self._handler()
        assert h._try_face_reading("http://169.254.169.254/x", "看图") is None
        assert h._try_palm_reading("http://127.0.0.1:8768/tts", "看图") is None


# ══════════════════════════════════════════════════════════════
# 2) 上传孤儿文件 TTL 清理
# ══════════════════════════════════════════════════════════════

class TestUploadTtlCleanup:
    def _mk(self, d, name, age_hours):
        p = d / name
        p.write_bytes(b"\xff\xd8\xff\xe0old")
        ts = time.time() - age_hours * 3600
        os.utime(p, (ts, ts))
        return p

    def test_expired_removed_fresh_kept(self, tmp_path):
        old = self._mk(tmp_path, "old.jpg", 100)
        fresh = self._mk(tmp_path, "fresh.jpg", 1)
        stats = cleanup_chat_uploads(ttl_seconds=72 * 3600, dir_path=tmp_path)
        assert stats["scanned"] == 2 and stats["removed"] == 1
        assert not old.exists() and fresh.exists()

    def test_custom_ttl_boundary(self, tmp_path):
        self._mk(tmp_path, "a.jpg", 25)
        assert cleanup_chat_uploads(ttl_seconds=24 * 3600,
                                    dir_path=tmp_path)["removed"] == 1

    def test_ttl_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORTUNE_UPLOAD_TTL_HOURS", "1")
        self._mk(tmp_path, "b.jpg", 3)
        assert cleanup_chat_uploads(dir_path=tmp_path)["removed"] == 1

    def test_invalid_env_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORTUNE_UPLOAD_TTL_HOURS", "abc")
        self._mk(tmp_path, "c.jpg", 1)
        stats = cleanup_chat_uploads(dir_path=tmp_path)
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
        stats = cleanup_chat_uploads(ttl_seconds=1, dir_path=tmp_path)
        assert stats["removed"] == 1
        assert (sub / "keep.jpg").exists()
