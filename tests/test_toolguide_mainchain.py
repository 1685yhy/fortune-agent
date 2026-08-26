# -*- coding: utf-8 -*-
"""P1 #2 修复（批次 1 部署后暴露）：主链首轮 SYSTEM 缺工具清单 → 工具链触发率 0%。

本文件覆盖 Fix 1（_free_chat 首轮注入工具清单）与 Fix 3（CHAT_PROMPT
教学统一 JSON 工单）；Fix 2（_polish_with_engine_draft）的测试在
test_handler_qa_fix.py（同函数既有测试文件）。
"""
import sys
import time
from pathlib import Path
from unittest.mock import Mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.bot.handler import MessageHandler  # noqa: E402
from src.bot.capability_registry import build_tool_description  # noqa: E402


def _free_chat_harness(**kw) -> MessageHandler:
    """object.__new__ 装配 _free_chat 全链路所需 Mock 属性（跑真实方法体）。"""
    h = object.__new__(MessageHandler)
    h.engine = Mock()
    h.llm = Mock()
    h.llm.chat_conversation.return_value = Mock(response="回复")
    h.dao = Mock()
    h.dao.get_user_bazi.return_value = None
    h.retriever = Mock()
    h.memory = None
    h.memory_system = None
    h._downgraded = {}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h.compactor = None
    h._emit_stream_event = Mock()
    h._consume_pregen_instant = Mock(return_value=None)
    h._gen_instant_reply = Mock(return_value="")
    h._get_personalized_context = Mock(return_value="")
    h._maybe_compact = Mock(return_value="")
    h._collect_key_facts = Mock(return_value=[])
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = [
        {"role": "user", "content": "你好"}]
    for k, v in kw.items():
        setattr(h, k, v)
    return h


# ------------------------------------------------------------ Fix 1


def test_free_chat_first_round_system_has_tool_list():
    """主链首轮 messages 头部注入 [可用工具清单]（= build_tool_description 全文）。

    根因：生产模型第一轮 SYSTEM 无真实工具清单 → 从不知道有工具可调 →
    _run_tool_loop 的 parse_tool_calls 永远为空 → 工具链触发率 0%。
    """
    h = _free_chat_harness()
    h._free_chat("今天心情怎么样", "u1", session_id="s1")
    h.llm.chat_conversation.assert_called_once()
    msgs = h.llm.chat_conversation.call_args[0][0]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "[可用工具清单]\n" + build_tool_description()
    assert "web_search" in msgs[0]["content"]
    assert "bazi_chart" in msgs[0]["content"]


# ------------------------------------------------------------ Fix 3


def test_chat_prompt_json_workorder_teaching():
    """CHAT_PROMPT 工具教学统一 JSON 工单：<tool_calls> 块 + 英文 cap_id，
    参数键与注册表 params_schema 一致；旧 <tool_call> 单标签教学全部移除。

    web_search 行沿用「可用时才宣传」门（web_search_available 可达性探测），
    测试锁定可用缓存后重载模块（CHAT_PROMPT 为模块级常量，导入时求值）。
    """
    import importlib

    import src.rag.web_search as ws
    from src.llm import prompts as prompts_mod

    ws._avail = True
    ws._avail_at = time.time() + 60
    importlib.reload(prompts_mod)
    cp = prompts_mod.CHAT_PROMPT
    assert "<tool_calls>" in cp
    for cap_id in ("bazi_chart", "quote_rag", "dream", "fengshui", "zeri",
                   "record_lookup", "web_search"):
        assert cap_id in cp
    assert ('{"tool": "web_search", "params": '
            '{"query": "需要联网查证的关键词"}}') in cp
    # 旧文本单标签教学（<tool_call>xx: ...）不再出现
    assert "<tool_call>搜索:" not in cp
    assert "<tool_call>排盘:" not in cp
    assert "<tool_call>检索:" not in cp
    assert "<tool_call>查记录:" not in cp
