# -*- coding: utf-8 -*-
"""P1 #2 修复（批次 1 部署后暴露）：主链首轮 SYSTEM 缺工具清单 → 工具链触发率 0%。

本文件覆盖 Fix 1（_free_chat 首轮注入工具清单）与 Fix 3（CHAT_PROMPT
教学统一 JSON 工单）；Fix 2（_polish_with_engine_draft）的测试在
test_handler_qa_fix.py（同函数既有测试文件）。

批次 2 B2（P1 #2 残留 Minor，spec C-2）：
- B2-11：无 session_dao 单消息降级分支 → CHAT_PROMPT（JSON 工单）+ [可用工具清单]
- B2-12：SYSTEM_PROMPT（引擎路径模板）教学统一 JSON 工单（旧标签全清）
- B2-13：handler.py 死导入 _web_tool_guide_line 清理
- B2-14：CHAT_PROMPT 负向断言补 解梦/风水/择日 + 参数键交叉核验
- B2-15：CHAT_PROMPT 择日指引明确 exclude_dates 是 params 内键（JSON 语境）
- B2-16：_web_tool_guide_line / _web_tool_guide_line_json 统一为带参函数
"""
import sys
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


def _reload_prompts_with_web(monkeypatch):
    """把 web_search_available 锁 True 后重载 prompts 模块，返回 (模块, 快照)。

    web_search 行沿用「可用时才宣传」门；CHAT_PROMPT/SYSTEM_PROMPT/
    TOOL_CALL_GUIDE 均为模块级常量（导入时求值），重载会重建三者——
    测试后必须用快照还原，避免污染同一 pytest 进程内其它测试的模块常量；
    monkeypatch 自动还原函数，不再直接篡改 _avail/_avail_at，避免污染
    60s 可达性缓存。
    """
    import importlib

    import src.rag.web_search as ws
    from src.llm import prompts as prompts_mod

    monkeypatch.setattr(ws, "web_search_available", lambda force=False: True)
    snap = (prompts_mod.CHAT_PROMPT, prompts_mod.SYSTEM_PROMPT,
            prompts_mod.TOOL_CALL_GUIDE)
    importlib.reload(prompts_mod)
    return prompts_mod, snap


def test_chat_prompt_json_workorder_teaching(monkeypatch):
    """CHAT_PROMPT 工具教学统一 JSON 工单：<tool_calls> 块 + 英文 cap_id，
    参数键与注册表 params_schema 一致；旧 <tool_call> 单标签教学全部移除。

    B2-14：负向断言补 解梦:/风水:/择日:；参数键交叉核验（"text"/"query"
    两键都在教学示例里出现）。
    B2-15：择日指引明确 exclude_dates 是 params 内键（JSON 语境），
    不再教文本内嵌 "exclude_dates: ..."。
    """
    prompts_mod, snap = _reload_prompts_with_web(monkeypatch)
    try:
        cp = prompts_mod.CHAT_PROMPT
        assert "<tool_calls>" in cp
        for cap_id in ("bazi_chart", "quote_rag", "dream", "fengshui", "zeri",
                       "record_lookup", "web_search"):
            assert cap_id in cp
        assert ('{"tool": "web_search", "params": '
                '{"query": "需要联网查证的关键词"}}') in cp
        # 旧文本单标签教学（<tool_call>xx: ...）不再出现（B2-14 补全）
        assert "<tool_call>搜索:" not in cp
        assert "<tool_call>排盘:" not in cp
        assert "<tool_call>检索:" not in cp
        assert "<tool_call>查记录:" not in cp
        assert "<tool_call>解梦:" not in cp
        assert "<tool_call>风水:" not in cp
        assert "<tool_call>择日:" not in cp
        # 参数键交叉核验（B2-14）：text（bazi_chart/dream/fengshui/zeri）
        # 与 query（quote_rag/record_lookup/web_search）都必须在教学里
        assert '"text"' in cp
        assert '"query"' in cp
        # B2-15：exclude_dates 教成 params 内键（JSON 语境）
        assert '"exclude_dates": ["2026-09-03", "2026-09-06"]' in cp
        assert "换一批 exclude_dates: 2026-09-03" not in cp
    finally:
        (prompts_mod.CHAT_PROMPT, prompts_mod.SYSTEM_PROMPT,
         prompts_mod.TOOL_CALL_GUIDE) = snap


# ------------------------------------------------------------ B2-11


def test_free_chat_fallback_injects_tool_list():
    """B2-11：无 session_dao 单消息降级分支——system_prompt 统一为
    CHAT_PROMPT（JSON 工单教学）+ [可用工具清单]（与主链首轮同口径）。

    P1 #2 根因（首轮 SYSTEM 无真实工具清单 → 工具链触发率 0%）在无会话
    存储配置下同样成立：该分支原本只有 llm.chat 默认 CHAT_PROMPT
    （教学示例，无清单）。
    """
    h = _free_chat_harness(session_dao=None)
    h.llm.chat.return_value = Mock(response="好的呀")
    out = h._free_chat("今天心情怎么样", "u1")
    assert out == "好的呀"
    h.llm.chat.assert_called_once()
    _, kwargs = h.llm.chat.call_args
    sp = kwargs.get("system_prompt") or ""
    assert "[可用工具清单]" in sp
    assert build_tool_description() in sp
    # JSON 工单教学（CHAT_PROMPT 全文在 system_prompt 内）
    assert "<tool_calls>" in sp
    assert '{"tool": "bazi_chart", "params": ' in sp
    # 旧文本标签教学不出现
    assert "<tool_call>排盘:" not in sp
    assert "<tool_call>检索:" not in sp
    assert "<tool_call>搜索:" not in sp


# ------------------------------------------------------------ B2-12 / B2-16


def test_system_prompt_json_workorder_teaching(monkeypatch):
    """B2-12：SYSTEM_PROMPT（引擎路径模板）教学统一 JSON 工单。

    旧 <tool_call>中文标签: 教学（排盘/检索/解梦/风水/择日/查记录/搜索）
    全部移除；JSON 工单用英文 cap_id + 注册表参数键（与 CHAT_PROMPT
    同口径）。web 行沿用「可用时才宣传」门。
    """
    prompts_mod, snap = _reload_prompts_with_web(monkeypatch)
    try:
        sp = prompts_mod.SYSTEM_PROMPT
        for cap in ("bazi_chart", "quote_rag", "dream", "fengshui",
                    "zeri", "record_lookup", "web_search"):
            assert cap in sp
        assert '<tool_calls>[{"tool": "bazi_chart", "params": ' in sp
        assert '<tool_calls>[{"tool": "quote_rag", "params": ' in sp
        assert '<tool_calls>[{"tool": "record_lookup", "params": ' in sp
        assert '{"tool": "web_search", "params": ' in sp
        for old in ("<tool_call>排盘:", "<tool_call>检索:", "<tool_call>解梦:",
                    "<tool_call>风水:", "<tool_call>择日:", "<tool_call>查记录:",
                    "<tool_call>搜索:"):
            assert old not in sp
    finally:
        (prompts_mod.CHAT_PROMPT, prompts_mod.SYSTEM_PROMPT,
         prompts_mod.TOOL_CALL_GUIDE) = snap


def test_web_tool_guide_line_merged(monkeypatch):
    """B2-16：_web_tool_guide_line 与 _web_tool_guide_line_json 统一为
    带参函数（json_format 选格式）。

    默认（json_format=True）= JSON 工单块；False = 旧文本标签行（兼容期
    保留，TOOL_CALL_RE 兜底解析仍认旧标签）。旧函数
    _web_tool_guide_line_json 删除。
    """
    prompts_mod, snap = _reload_prompts_with_web(monkeypatch)
    try:
        assert not hasattr(prompts_mod, "_web_tool_guide_line_json")
        json_line = prompts_mod._web_tool_guide_line()
        assert "<tool_calls>" in json_line
        assert '{"tool": "web_search", "params": ' in json_line
        legacy = prompts_mod._web_tool_guide_line(json_format=False)
        assert "<tool_call>搜索:" in legacy
    finally:
        (prompts_mod.CHAT_PROMPT, prompts_mod.SYSTEM_PROMPT,
         prompts_mod.TOOL_CALL_GUIDE) = snap


# ------------------------------------------------------------ B2-13


def test_handler_no_dead_web_tool_guide_import():
    """B2-13：handler.py 死导入清理——模块命名空间不再挂 _web_tool_guide_line。

    该导入在 handler.py 内无任何消费方（唯一引用在 import 语句本身），
    为旧标签 web 行时代的遗留导入。
    """
    import src.bot.handler as handler_mod
    assert not hasattr(handler_mod, "_web_tool_guide_line")
