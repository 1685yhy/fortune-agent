# Task 7: 查记录工具（LLM 可调用）— 注册表 + 解析
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.bot.tool_calls import parse_tool_calls, TOOL_REGISTRY


def test_registry_has_records():
    assert any(t.get("key") == "records" for t in TOOL_REGISTRY.values())


def test_parse_records_call():
    calls = parse_tool_calls("<tool_call>查记录: 我的档案</tool_call>")
    assert calls and calls[0].name == "查记录"
