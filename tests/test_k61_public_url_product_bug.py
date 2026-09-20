# -*- coding: utf-8 -*-
"""k61 r3 ⑥：**用户可见产品 bug** —— 下发给客户端的音频 URL 指向回环地址。

## 事实（审查者查实 + 本文件实测复现）

- 生产/部署 `.env`：`PUBLIC_BASE_URL=http://127.0.0.1:8768`
  （`config.py:public_base_url()` 的 docstring 自己写明那是**开发**值）。
- `POST /api/tts`（`src/main.py`）与 `night_soliloquy.synth_lamp_audio()`
  都拿它拼**下发给客户端**的 `audio_url`。
- 小程序端只校验 `indexOf('http') === 0` 就播放 → **真机语音全部不可达**
  （音频地址指向手机自己）—— 正是 H-10 的根因形态。

## 修法（分开"对客户端的 URL"与"服务内部调用"）

- 新增 `config.public_client_base()`：配置值是回环（或空）时**回落到文档化的
  生产域名**并 warning；非回环时照用配置值。仅这两个下发点改用它。
- **服务内部调用完全不动**：转发目标仍是 `tts_upstream_base()`（默认
  `http://127.0.0.1:8768`）—— 8768 上的 TTS 服务与 tunnel `-R 18766:127.0.0.1:8768`
  的使用方式零变化。
- `public_base_url()` **行为零变化**（`bot/image_url_guard.py` 用它判「本服务自有
  域名」，那里回环值是有意义的）。

运行：TMPDIR=/dev/shm OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 \
      -m pytest tests/test_k61_public_url_product_bug.py -q
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

PROD_DEFAULT = "https://yilichat.com"
PROD_ENV_VALUE = "http://127.0.0.1:8768"   # ← 生产 .env 的实际值


class _TtsStub(BaseHTTPRequestHandler):
    """扮演 8768 的 `POST /tts`：只回相对 audio_url（真实服务的行为）。"""

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        body = json.dumps({"audio_url": "/audio/k61_unit.mp3",
                           "duration_ms": 1234}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静音
        pass


@pytest.fixture
def tts_stub():
    srv = HTTPServer(("127.0.0.1", 0), _TtsStub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


# ══════════════════════════════════════════════════════════════════
# 1) 判定语义（纯函数，无网络）
# ══════════════════════════════════════════════════════════════════

class TestPublicClientBase:
    def test_loopback_value_falls_back_to_production_domain(self, monkeypatch):
        """**核心**：回环值不得下发给客户端（回落生产域名 + warning）。"""
        from src.config import public_client_base
        for bad in (PROD_ENV_VALUE, "http://localhost:8768", "http://0.0.0.0:8768",
                    "http://[::1]:8768", ""):
            monkeypatch.setenv("PUBLIC_BASE_URL", bad)
            assert public_client_base() == PROD_DEFAULT, bad

    def test_real_domain_is_used_as_is(self, monkeypatch):
        from src.config import public_client_base
        for good in ("https://yilichat.com", "https://yilichat.com/",
                     "http://example.com:8080", "https://sub.example.cn"):
            monkeypatch.setenv("PUBLIC_BASE_URL", good)
            assert public_client_base() == good.rstrip("/")

    def test_warning_is_emitted_for_loopback(self, monkeypatch, caplog):
        """不静默：回落到默认域名必须留 warning（运维能看到配置错了）。"""
        import logging
        from src.config import public_client_base
        monkeypatch.setenv("PUBLIC_BASE_URL", PROD_ENV_VALUE)
        with caplog.at_level(logging.WARNING):
            public_client_base()
        assert "回环" in caplog.text, caplog.text
        assert PROD_ENV_VALUE in caplog.text, caplog.text


# ══════════════════════════════════════════════════════════════════
# 2) 服务内部调用零变化（不许把内部链路改坏）
# ══════════════════════════════════════════════════════════════════

class TestInternalChainUnchanged:
    def test_tts_upstream_default_is_still_8768(self, monkeypatch):
        """转发目标仍是本机 8768（8768 上的服务与 tunnel 映射都不动）。"""
        monkeypatch.delenv("TTS_UPSTREAM_BASE", raising=False)
        from src.config import tts_upstream_base
        assert tts_upstream_base() == "http://127.0.0.1:8768"

    def test_public_base_url_behavior_unchanged(self, monkeypatch):
        """`public_base_url()` 行为零变化（image_url_guard 依赖它拿到回环值）。"""
        from src.config import public_base_url
        monkeypatch.setenv("PUBLIC_BASE_URL", PROD_ENV_VALUE)
        assert public_base_url() == PROD_ENV_VALUE
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://yilichat.com")
        assert public_base_url() == "https://yilichat.com"

    def test_upstream_still_receives_the_forward(self, tts_stub, monkeypatch):
        """真实调用：转发确实打到 `tts_upstream_base()`（本地桩收到请求）。"""
        monkeypatch.setenv("TTS_UPSTREAM_BASE", tts_stub)
        monkeypatch.setenv("PUBLIC_BASE_URL", PROD_ENV_VALUE)
        from src.engines.night_soliloquy import synth_lamp_audio
        url = synth_lamp_audio("夜深了，早点休息。")
        assert url.endswith("/audio/k61_unit.mp3"), url


# ══════════════════════════════════════════════════════════════════
# 3) 端到端契约：真实请求 + 真实端点 → 客户端拿到的 payload 必须可达
# ══════════════════════════════════════════════════════════════════

class TestClientPayloadIsReachable:
    """真实 `POST /api/tts` 请求（上游用本地桩，**不碰 8768 上的真实服务**）。"""

    @pytest.fixture
    def client_and_token(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "k61-r3-unit-secret-32-bytes-long!!")
        from fastapi.testclient import TestClient
        from src.security.auth import AuthHandler, JWTHandler, set_auth_handler
        from src.main import app
        set_auth_handler(AuthHandler())
        token = JWTHandler("k61-r3-unit-secret-32-bytes-long!!").create_token("k61_unit_user")
        try:
            yield TestClient(app, raise_server_exceptions=False), token
        finally:
            set_auth_handler(None)

    def test_tts_payload_is_not_loopback(self, tts_stub, monkeypatch, client_and_token):
        """**产品 bug 的锁**：生产配置（回环 PUBLIC_BASE_URL）下，客户端拿到的
        audio_url 必须可达（不得含 127.0.0.1）。"""
        monkeypatch.setenv("TTS_UPSTREAM_BASE", tts_stub)
        monkeypatch.setenv("PUBLIC_BASE_URL", PROD_ENV_VALUE)     # ← 生产 .env 的值
        client, token = client_and_token
        r = client.post("/api/tts", json={"text": "你好"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        payload = r.json()
        url = payload["audio_url"]
        assert "127.0.0.1" not in url and "localhost" not in url, \
            f"客户端拿到回环 URL（真机语音不可达）：{url}"
        assert url.startswith("https://"), url
        assert url == f"{PROD_DEFAULT}/audio/k61_unit.mp3"

    def test_real_domain_config_is_respected(self, tts_stub, monkeypatch, client_and_token):
        """部署侧把 PUBLIC_BASE_URL 改成真实域名后，直接用它（无需改代码）。"""
        monkeypatch.setenv("TTS_UPSTREAM_BASE", tts_stub)
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://fortune.example.cn")
        client, token = client_and_token
        r = client.post("/api/tts", json={"text": "你好"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.json()["audio_url"] == "https://fortune.example.cn/audio/k61_unit.mp3"

    def test_lamp_audio_url_is_not_loopback(self, tts_stub, monkeypatch):
        """灯语（night_soliloquy）同一条锁。"""
        monkeypatch.setenv("TTS_UPSTREAM_BASE", tts_stub)
        monkeypatch.setenv("PUBLIC_BASE_URL", PROD_ENV_VALUE)
        from src.engines.night_soliloquy import synth_lamp_audio
        url = synth_lamp_audio("夜深了。")
        assert "127.0.0.1" not in url, f"客户端拿到回环 URL：{url}"
        assert url == f"{PROD_DEFAULT}/audio/k61_unit.mp3"


# ══════════════════════════════════════════════════════════════════
# 4) 反向锁：不许硬编码客户端域名
# ══════════════════════════════════════════════════════════════════

class TestNoHardcodedClientDomain:
    def test_no_hardcoded_loopback_in_client_rewrite(self):
        """两个下发点不得硬编码 127.0.0.1（真机不可达）——必须走配置函数。"""
        import inspect
        from src.engines import night_soliloquy as ns
        src = inspect.getsource(ns.synth_lamp_audio)
        assert "public_client_base()" in src
        # 拼接点不得出现字面量回环
        for line in src.splitlines():
            if "url = f" in line or "audio_url = f" in line:
                assert "127.0.0.1" not in line, line
