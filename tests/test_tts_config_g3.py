"""G3 H-10：TTS 双侧硬编码 127.0.0.1:8768 修复（后端侧）。

覆盖：
- tts_upstream_base()：默认 127.0.0.1:8768（内部转发地址），可经 TTS_UPSTREAM_BASE 覆盖；
- public_base_url()：默认生产域名 https://yilichat.com（TTS 相对路径改写对外前缀），
  开发环境经 PUBLIC_BASE_URL 覆盖——绝不允许默认落到 127.0.0.1（真机语音不可达根因）；
- main.api_tts：转发目标读 tts_upstream_base()，返回的 audio_url 相对路径 →
  public_base_url() 完整 URL；
- night_soliloquy.synth_lamp_audio：同族修复（转发 + 改写均走配置）。
"""
import os
from unittest.mock import patch

from src.config import public_base_url, tts_upstream_base


def test_tts_upstream_base_default():
    # clear=True：模拟干净环境（本地 .env 可能设置了覆盖值）
    with patch.dict(os.environ, {}, clear=True):
        assert tts_upstream_base() == "http://127.0.0.1:8768"


def test_tts_upstream_base_override():
    with patch.dict(os.environ, {"TTS_UPSTREAM_BASE": "http://tts.internal:9000"}, clear=False):
        assert tts_upstream_base() == "http://tts.internal:9000"


def test_tts_upstream_base_strips_trailing_slash():
    with patch.dict(os.environ, {"TTS_UPSTREAM_BASE": "http://tts.internal:9000/"}, clear=False):
        assert tts_upstream_base() == "http://tts.internal:9000"


def test_public_base_url_default_is_production_domain():
    """对外前缀默认必须是生产域名——落到 127.0.0.1 时真机指向手机自身，语音必然不可达。"""
    with patch.dict(os.environ, {}, clear=True):
        assert public_base_url() == "https://yilichat.com"


def test_public_base_url_override_dev():
    with patch.dict(os.environ, {"PUBLIC_BASE_URL": "http://127.0.0.1:8768"}, clear=False):
        assert public_base_url() == "http://127.0.0.1:8768"


class FakeUpstreamResp:
    status_code = 200

    def json(self):
        return {"audio_url": "/audio/x.mp3"}


class FakeUpstreamClient:
    """httpx.AsyncClient 替身：记录请求 URL，返回固定 audio_url。"""

    def __init__(self):
        self.url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.url = url
        return FakeUpstreamResp()


def _run_api_tts(env: dict):
    """env 中值为 None 的键 = 显式清除（防本地 .env 覆盖干扰断言）。"""
    import asyncio

    from src.main import TTSRequest, api_tts

    set_env = {k: v for k, v in env.items() if v is not None}
    pops = [k for k, v in env.items() if v is None]
    client = FakeUpstreamClient()

    async def run():
        with patch("httpx.AsyncClient", return_value=client), patch.dict(os.environ, set_env, clear=False):
            for k in pops:
                os.environ.pop(k, None)
            return await api_tts(TTSRequest(text="你好"), "test-uid")

    body = asyncio.run(run())
    return client, body


def test_api_tts_rewrites_relative_path_with_public_base_url():
    """api_tts：转发走 tts_upstream_base()；相对路径 audio_url → public_base_url() 完整 URL。"""
    client, body = _run_api_tts({
        "TTS_UPSTREAM_BASE": "http://tts.internal:9000",
        "PUBLIC_BASE_URL": "https://yilichat.com",
    })
    assert client.url == "http://tts.internal:9000/tts", client.url
    assert body["audio_url"] == "https://yilichat.com/audio/x.mp3", body


def test_api_tts_default_public_base_is_production_domain():
    """无 PUBLIC_BASE_URL 配置时，改写前缀必须是生产域名——绝不允许落到 127.0.0.1。"""
    client, body = _run_api_tts({
        "TTS_UPSTREAM_BASE": "http://127.0.0.1:8768",
        "PUBLIC_BASE_URL": None,  # 显式清除（本地 .env 可能覆盖）
    })
    assert body["audio_url"] == "https://yilichat.com/audio/x.mp3", body
    assert "127.0.0.1" not in body["audio_url"]


def test_night_soliloquy_synth_lamp_audio_uses_config():
    """synth_lamp_audio：转发与改写均读配置（同族修复），失败返回空串（文字版可用）。"""
    from src.engines import night_soliloquy as ns

    fake_resp = type("R", (), {"status_code": 200, "json": lambda self: {"audio_url": "/audio/lamp.mp3"}})()
    with patch.object(ns.httpx, "post", return_value=fake_resp) as mpost, \
         patch.dict(os.environ, {"TTS_UPSTREAM_BASE": "http://tts.internal:9000",
                                 "PUBLIC_BASE_URL": "https://yilichat.com"}, clear=False):
        url = ns.synth_lamp_audio("灯还亮着。")
    assert mpost.call_args.args[0] == "http://tts.internal:9000/tts"
    assert url == "https://yilichat.com/audio/lamp.mp3"


def test_night_soliloquy_synth_lamp_audio_failure_returns_empty():
    from src.engines import night_soliloquy as ns

    with patch.object(ns.httpx, "post", side_effect=Exception("conn refused")):
        assert ns.synth_lamp_audio("灯还亮着。") == ""
