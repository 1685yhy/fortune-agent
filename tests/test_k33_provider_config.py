# -*- coding: utf-8 -*-
"""k33/A27：`src/main.py` provider 硬编码 → 配置项化（GLM 生产灰度前提）。

要求：**默认值必须与现状一致（零行为变化）**——迁移前
`FortuneLLM(..., provider="deepseek", ...)` 写死在装配点；现在读
`Settings.llm_provider`（env `FORTUNE_LLM_PROVIDER` 可覆盖），默认仍是 "deepseek"。

运行：OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 -m pytest \
      tests/test_k33_provider_config.py -q
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FORTUNE_LLM_PROVIDER", raising=False)
    yield


class TestSettingsProvider:
    def test_default_is_deepseek(self):
        """默认 == 迁移前硬编码字面量（零行为变化）。"""
        from src.config import Settings
        assert Settings().llm_provider == "deepseek"

    def test_load_settings_default(self):
        from src.config import load_settings
        assert load_settings().llm_provider == "deepseek"

    def test_env_override_normalized(self, monkeypatch):
        from src.config import load_settings
        monkeypatch.setenv("FORTUNE_LLM_PROVIDER", "  GLM ")
        assert load_settings().llm_provider == "glm"


class TestMainProviderResolution:
    def test_resolve_default(self):
        from src.config import Settings
        from src.main import _resolve_llm_provider
        assert _resolve_llm_provider(Settings()) == "deepseek"

    def test_resolve_env_value(self):
        from src.config import Settings
        from src.main import _resolve_llm_provider
        s = Settings()
        s.llm_provider = "GLM"
        assert _resolve_llm_provider(s) == "glm"

    @pytest.mark.parametrize("obj", [object(), None, ""])
    def test_resolve_missing_field_falls_back(self, obj):
        from src.main import _resolve_llm_provider
        assert _resolve_llm_provider(obj) == "deepseek"


class TestAssemblyWiring:
    """装配点护栏：FortuneLLM 的 provider 必须来自配置解析，不得再写死。"""

    def _lifespan_src(self):
        from src.main import lifespan
        import inspect
        return inspect.getsource(lifespan)

    def test_provider_not_hardcoded(self):
        src = self._lifespan_src()
        assert 'provider="deepseek"' not in src, "装配点又写死了 provider"
        assert "provider=_llm_provider" in src

    def test_provider_resolved_from_settings(self):
        src = self._lifespan_src()
        assert "_resolve_llm_provider(settings)" in src

    def test_provider_logged_at_startup(self):
        src = self._lifespan_src()
        assert re.search(r'logger\.info\("LLM provider=', src)
