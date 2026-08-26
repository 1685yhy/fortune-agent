# -*- coding: utf-8 -*-
"""批次 2 B1：Task 1 验证脚本 Minor 修复测试。

覆盖：
- B1-1：响应 content 为 None 的防御（原 data.get("content", []) 在 content=null 时
  返回 None → [b for b in content ...] TypeError 崩溃）
- B1-2：IGNORED_TOOLS 语义澄清——tool_choice=auto 下纯文本回复 ≠ 工具被忽略，
  标签改名为 NO_TOOL_USE（退出码 1 不变，行为不变）
- 正控：tool_use 块 → SUPPORTED_NATIVE_TOOL_USE（退出码 0）不受影响
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "scripts" / "verify_deepseek_tool_use.py")


@pytest.fixture
def script(monkeypatch):
    """从文件加载脚本模块（不入 sys.modules，每测新实例），mock 掉 key 依赖。"""
    spec = importlib.util.spec_from_file_location(
        "verify_deepseek_tool_use", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("FORTUNE_API_KEY", "test-key")
    return mod


class _Resp:
    """最小 httpx.Response 替身（脚本只读 status_code/json()/text）。"""

    def __init__(self, status_code, data, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self):
        return self._data


def _patch_post(script, monkeypatch, status_code=200, data=None, text=""):
    monkeypatch.setattr(script.httpx, "post",
                        lambda *a, **k: _Resp(status_code, data, text))


def test_content_none_no_crash(script, monkeypatch, capsys):
    """B1-1：content=null（deepseek 偶发空 content）→ 不崩溃，RESULT=UNKNOWN（退出码 4）。

    修复前 data.get("content", []) 在 content 为 None 时返回 None，
    [b for b in None ...] 直接 TypeError → 脚本崩溃无结论。
    """
    _patch_post(script, monkeypatch, data={"stop_reason": "end_turn",
                                           "content": None})
    assert script.main() == 4
    out = capsys.readouterr().out
    assert "RESULT=UNKNOWN" in out


def test_no_tool_use_label_semantics(script, monkeypatch, capsys):
    """B1-2：纯文本回复 + tool_choice=auto → NO_TOOL_USE（模型选择不用工具），
    不再是误导性的 IGNORED_TOOLS（工具并未被忽略）。退出码 1 语义不变。"""
    _patch_post(script, monkeypatch, data={
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "今天北京晴。"}],
    })
    assert script.main() == 1
    out = capsys.readouterr().out
    assert "RESULT=NO_TOOL_USE" in out
    assert "IGNORED_TOOLS" not in out


def test_supported_native_tool_use_positive_control(script, monkeypatch, capsys):
    """B1-2 正控：tool_use 块路径标签/退出码不变（SUPPORTED_NATIVE_TOOL_USE, 0）。"""
    _patch_post(script, monkeypatch, data={
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "t1", "name": "web_search",
                     "input": {"query": "北京天气"}}],
    })
    assert script.main() == 0
    assert "RESULT=SUPPORTED_NATIVE_TOOL_USE" in capsys.readouterr().out


def test_auth_error_path_unchanged(script, monkeypatch, capsys):
    """B1-1/2 邻域：非 200（401 等）→ NOT_SUPPORTED_OR_ERROR 路径不变（退出码 1）。"""
    _patch_post(script, monkeypatch, status_code=401,
                data={"error": {"message": "Authentication Fails"}})
    assert script.main() == 1
    assert "RESULT=NOT_SUPPORTED_OR_ERROR" in capsys.readouterr().out
