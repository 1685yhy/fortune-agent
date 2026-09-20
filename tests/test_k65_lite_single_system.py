"""k65：降级档（lite）system 指令唯一性 + 主链 payload 逐字节锁。

缺陷（k65 独立复核实测）：`chat_conversation(lite=True)` 把已拼好
CHAT_PROMPT 的整包 messages 当 history 传给 `_chat_lite`，`_chat_lite`
再前置一条 CHAT_PROMPT_LITE → 最终发给 GLM（以及 GLM 失败后回退的
DeepSeek）的 payload 里同时存在两条互相矛盾的 system：

    [system] CHAT_PROMPT_LITE  ← 370 字：「不调用任何工具」
    [system] CHAT_PROMPT       ← 2398 字：10 组 <tool_calls> 工具教学

实测后果（glm-4-flash 真调）：修前对「帮我排盘：1990年5月20日 下午3点
北京 男」两次采样均输出 `<tool_calls>[{"tool": "bazi_chart", ...}]`，
并继续**编造**一整张八字排盘（庚午/己巳/庚辰/庚午）；修后不再输出工具
调用标记。且修前多付 2398 字符/轮的纯开销（成本控制档反被放大）。

修复点：`chat_conversation` 的 lite 分支改传**调用方原始 history**，主
prompt 不再进入精简链路（见 src/llm/client.py k65 注释）。

本文件锁三件事：
1. lite 路径 system 唯一且 == CHAT_PROMPT_LITE（含 GLM→DeepSeek 回退、
   流式三条子路径）；
2. 调用方自带的其它 system 消息**保留**、只剥离已知主 prompt（边界策略）；
3. 主链（lite=False）发给 DeepSeek 的 messages **逐字节不变**（同一输入
   下与构造式完全相等），以及 :488 无历史单条 lite 路径不受影响。

全部 mock，不调外部 API。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import httpx  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from src.llm import client as llm_client  # noqa: E402
from src.llm.client import (  # noqa: E402
    FortuneLLM, GLM_COMPLETIONS_URL, GLM_DEFAULT_MODEL, ANTHROPIC_MESSAGES_URL,
)
from src.llm.prompts import CHAT_PROMPT, CHAT_PROMPT_LITE  # noqa: E402
from src.bot.handler import MessageHandler  # noqa: E402
from src.bot.capability_registry import build_tool_description  # noqa: E402

HISTORY = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好呀，有什么想问的？"},
    {"role": "user", "content": "帮我看看我的时柱"},
]

# 调用方自带的 system（handler 在 chat_conversation 前注入的[可用工具清单]
# 形态，缩小版）：边界策略要求它必须原样保留。
CALLER_SYSTEM = {"role": "system", "content": "[可用工具清单]\n自定义清单内容"}

# k65 r3：精简档「能力边界」声明——本档没有工具能力，只禁"调用工具"不足以
# 拦住"没有结果时凭空给结论"（实测修前仍 4/9 编出四柱）。三条都必须出现在
# **真正发出去的 payload** 里（不是只出现在常量里）。
BOUNDARY_CHART = "不得给出四柱、干支、喜用神、神煞"   # 命盘类
BOUNDARY_DATE = "不得给出任何具体日期或时辰"           # 日期类
BOUNDARY_EXIT = "精简模式暂不提供该功能"               # 出口（说明或反问）
BOUNDARY_MARKS = (BOUNDARY_CHART, BOUNDARY_DATE, BOUNDARY_EXIT)


class _FakeResp:
    """httpx 响应替身（GLM OpenAI 兼容格式）。"""

    def __init__(self, content="精简回复", status=200):
        self._content = content
        self.status_code = status

    def json(self):
        return {"choices": [{"message": {"role": "assistant",
                                         "content": self._content}}],
                "finish_reason": "stop"}


class _FakeAnthropicResp:
    """httpx 响应替身（DeepSeek Anthropic 兼容格式）。"""

    def __init__(self, content="正常完整回复", status=200):
        self._content = content
        self.status_code = status

    def json(self):
        return {"content": [{"type": "text", "text": self._content}],
                "stop_reason": "end_turn"}


def _capture_post(calls, glm_raises=False):
    """记录真实发往外部的 payload；可选让 GLM 端点抛错以走回退。"""
    def fake_post(self, url, **kw):
        calls.append((url, kw))
        if url == GLM_COMPLETIONS_URL:
            if glm_raises:
                raise httpx.ConnectError("zhipu down")
            return _FakeResp()
        return _FakeAnthropicResp()
    return fake_post


def _systems(messages):
    return [m["content"] for m in messages if m.get("role") == "system"]


def _payload(llm, monkeypatch, history, lite, glm_raises=False):
    """跑一次 chat_conversation，返回真正发给模型的 messages 列表。"""
    calls = []
    monkeypatch.setattr(httpx.Client, "post", _capture_post(calls, glm_raises))
    llm.chat_conversation(history, lite=lite)
    assert calls, "必须发起真实 LLM HTTP 调用（否则本测试无意义）"
    return calls[0][1]["json"]["messages"]


# ───────────────────────── ① lite 路径 system 唯一性 ─────────────────────────

class TestLiteSystemSingularity:
    def test_lite_system_exactly_one_and_is_lite(self, monkeypatch):
        """lite 路径必须恰好一条 system，且内容 == CHAT_PROMPT_LITE。

        （k65 植入实验目标：若把 `history=history` 改回 `history=messages`
        复原双 system，本断言立即变红——实测 system 条数 2→1 的判定点。）
        """
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        msgs = _payload(llm, monkeypatch, HISTORY, lite=True)

        sys_msgs = _systems(msgs)
        assert len(sys_msgs) == 1, f"lite 路径 system 必须唯一，实测 {len(sys_msgs)}"
        assert sys_msgs[0] == CHAT_PROMPT_LITE
        # 主 prompt 不得以任何形态出现在 lite payload 中
        assert CHAT_PROMPT not in sys_msgs
        assert not any("你是一个会用八字紫微帮人看问题的伙伴" in s
                       for s in sys_msgs if s != CHAT_PROMPT_LITE)
        # 对话历史原序保留在 system 之后
        assert msgs[1:] == HISTORY

    def test_lite_payload_drops_main_prompt_chars(self, monkeypatch):
        """lite payload 不含 CHAT_PROMPT 的 2398 字符开销（成本控制档）。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        msgs = _payload(llm, monkeypatch, HISTORY, lite=True)
        total = sum(len(m.get("content") or "") for m in msgs)
        assert total == len(CHAT_PROMPT_LITE) + sum(len(m["content"]) for m in HISTORY)
        assert total < len(CHAT_PROMPT)  # 远低于主 prompt 单条体量

    def test_lite_glm_payload_shape(self, monkeypatch):
        """发往 GLM 端点的 raw payload 三要素：端点/model/system 唯一。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        llm.chat_conversation(HISTORY, lite=True)
        url, kw = calls[0]
        assert url == GLM_COMPLETIONS_URL
        body = kw["json"]
        assert body["model"] == GLM_DEFAULT_MODEL
        assert len(_systems(body["messages"])) == 1
        assert body["messages"][0] == {"role": "system",
                                       "content": CHAT_PROMPT_LITE}

    def test_lite_deepseek_fallback_single_system(self, monkeypatch):
        """GLM 挂 → 回退 DeepSeek 也必须只有一条 system（且仍是精简 prompt）。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(
            httpx.Client, "post", _capture_post(calls, glm_raises=True))
        llm.chat_conversation(HISTORY, lite=True)
        assert len(calls) == 2, "GLM 失败必须回退 DeepSeek（一次重试调用）"
        fallback_url, fallback_kw = calls[1]
        assert fallback_url == ANTHROPIC_MESSAGES_URL
        fb_msgs = fallback_kw["json"]["messages"]
        assert len(_systems(fb_msgs)) == 1
        assert _systems(fb_msgs)[0] == CHAT_PROMPT_LITE
        assert CHAT_PROMPT not in _systems(fb_msgs)

    def test_lite_stream_single_system(self, monkeypatch):
        """流式 lite 路径（stream_cb）同样只有一条 system。"""
        seen = {}

        async def fake_stream(api_key, messages, **kw):
            seen["messages"] = messages
            yield "精简"

        monkeypatch.setattr(
            llm_client, "glm_openai_completion_stream", fake_stream)
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        replies = []
        llm.chat_conversation(HISTORY, stream_cb=lambda t, p: replies.append(p["text"]),
                              lite=True)
        assert len(_systems(seen["messages"])) == 1
        assert _systems(seen["messages"])[0] == CHAT_PROMPT_LITE


# ─────────────────── ② 边界：调用方自带 system 的处理策略 ───────────────────

class TestLiteCallerSystemBoundary:
    def test_caller_system_preserved_main_prompt_stripped(self, monkeypatch):
        """只剥离**已知主 prompt**；调用方自带的其它 system 原样保留。

        策略理由（见报告）：_chat_lite 的契约是"前置唯一精简 prompt"，
        调用方 history 里的其它 system 属调用方自有语义（如工具清单、
        角色/安全叠加），客户端无从判断其意图，盲目全剥会静默改写调用方
        行为；而主 prompt 是本客户端自己注入的已知常量，可安全剥离。
        本用例同时锁住"剥离量 = 恰好 1 条 CHAT_PROMPT"。
        """
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        history = [CALLER_SYSTEM] + HISTORY
        msgs = _payload(llm, monkeypatch, history, lite=True)

        sys_msgs = _systems(msgs)
        assert sys_msgs == [CHAT_PROMPT_LITE, CALLER_SYSTEM["content"]]
        assert CHAT_PROMPT not in sys_msgs  # 主 prompt 已剥离
        assert msgs[2:] == HISTORY  # 其余历史原序保留

    def test_caller_system_not_mutated(self, monkeypatch):
        """剥离不得原地改写调用方传入的 history 对象（调用方可复用）。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        history = [dict(CALLER_SYSTEM)] + [dict(m) for m in HISTORY]
        snapshot = [dict(m) for m in history]
        _payload(llm, monkeypatch, history, lite=True)
        assert history == snapshot, "调用方 history 被就地修改"


# ───────────── ③ 主链 payload 逐字节锁 + :488 单条路径不受影响 ─────────────

class TestMainChainPayloadLock:
    def test_main_chain_messages_byte_identical(self, monkeypatch):
        """lite=False 发给 DeepSeek 的 messages 必须逐字节等于
        [CHAT_PROMPT] + history（k65 修改前/后同一输入下相等）。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        llm.chat_conversation(HISTORY, lite=False)

        url, kw = calls[0]
        assert url == ANTHROPIC_MESSAGES_URL
        expected = [{"role": "system", "content": CHAT_PROMPT}] + HISTORY
        assert kw["json"]["messages"] == expected  # 含字符串内容全等
        # 主链不得被精简 prompt 污染
        assert CHAT_PROMPT_LITE not in _systems(kw["json"]["messages"])

    def test_main_chain_with_caller_system_untouched(self, monkeypatch):
        """主链遇到调用方 system 时顺序/内容一律不变（CHAT_PROMPT 在前）。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        history = [CALLER_SYSTEM] + HISTORY
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        llm.chat_conversation(history, lite=False)
        expected = [{"role": "system", "content": CHAT_PROMPT}] + history
        assert calls[0][1]["json"]["messages"] == expected

    def test_single_message_lite_path_unaffected(self, monkeypatch):
        """:488 无历史单条 lite 路径（llm.chat）不受 k65 影响：一条 system。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        result = llm.chat("帮我看看我的时柱", lite=True)
        assert result.response == "精简回复"
        url, kw = calls[0]
        assert url == GLM_COMPLETIONS_URL
        msgs = kw["json"]["messages"]
        assert msgs == [{"role": "system", "content": CHAT_PROMPT_LITE},
                        {"role": "user", "content": "帮我看看我的时柱"}]

    def test_single_message_lite_ignores_system_prompt(self, monkeypatch):
        """单条 lite 路径忽略 system_prompt（handler B2-11 传主 prompt 也不进
        精简链路）——既有语义，k65 不得改变。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        llm.chat("你好", lite=True,
                 system_prompt="[可用工具清单]\n" + CHAT_PROMPT)
        msgs = calls[0][1]["json"]["messages"]
        assert len(_systems(msgs)) == 1
        assert _systems(msgs)[0] == CHAT_PROMPT_LITE

    def test_main_chain_single_message_uses_system_prompt(self, monkeypatch):
        """非 lite 单条路径仍透传 system_prompt（主链行为不变）。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        llm.chat("你好", lite=False, system_prompt="自定义主链 system")
        msgs = calls[0][1]["json"]["messages"]
        assert _systems(msgs) == ["自定义主链 system"]


# ══════════ ④ k65 r2：降级档按能力裁剪——不注入 [可用工具清单] ══════════

TOOL_LIST_MARK = "[可用工具清单]"


def _free_chat_harness(**kw) -> MessageHandler:
    """object.__new__ 装配 _free_chat 全链路所需 Mock 属性（跑真实方法体）。

    与 tests/test_toolguide_mainchain.py::_free_chat_harness 同构（本地副本，
    避免跨测试模块导入）。
    """
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


def _free_chat_messages(h, msg, **kw):
    """跑 _free_chat，返回真正传给 chat_conversation 的 messages 与 lite 标志。"""
    h._free_chat(msg, "u1", session_id="s1", **kw)
    h.llm.chat_conversation.assert_called_once()
    args, kwargs = h.llm.chat_conversation.call_args
    return args[0], kwargs.get("lite")


class TestHandlerDowngradeToolListGate:
    """降级档无工具能力 → 不注入工具清单（按能力裁剪，非全剥）。

    判据来源：`_free_chat` 的 `downgraded` 入参——与同一行
    `chat_conversation(..., lite=downgraded)` 的 `lite` **同源**，
    源头是 `src/services/chat_quota.py::chat_quota_status`
    的 `used >= CHAT_DAILY_LIMIT`，经 `src/main.py` / `src/api/chat_stream.py`
    → `process(downgraded=...)` 一路显式传参。**不是内容猜测。**
    """

    def test_downgraded_payload_excludes_tool_list(self):
        """① 降级档 payload 不含工具清单标记/全文。"""
        h = _free_chat_harness()
        msgs, lite = _free_chat_messages(h, "帮我看看我的时柱", downgraded=True)
        joined = "\n".join(m.get("content") or "" for m in msgs)
        assert TOOL_LIST_MARK not in joined
        assert build_tool_description() not in joined
        assert "bazi_chart" not in joined
        assert lite is True  # 降级标志照旧传给 LLM

    def test_main_chain_payload_still_includes_tool_list(self):
        """② 主链 payload 仍含工具清单（防一刀切把主链也砍了）。"""
        h = _free_chat_harness()
        msgs, lite = _free_chat_messages(h, "今天心情怎么样", downgraded=False)
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == TOOL_LIST_MARK + "\n" + build_tool_description()
        assert "bazi_chart" in msgs[0]["content"]
        assert lite is False

    def test_gate_is_flag_based_not_content_based(self):
        """判据是降级标志而非消息内容：**同一句诱工具话术**在主链注入、
        在降级档不注入。"""
        q = "帮我排盘看看我的命格"
        h_main = _free_chat_harness()
        m_main, lite_main = _free_chat_messages(h_main, q, downgraded=False)
        h_lite = _free_chat_harness()
        m_lite, lite_lite = _free_chat_messages(h_lite, q, downgraded=True)

        assert any(TOOL_LIST_MARK in (m.get("content") or "") for m in m_main)
        assert not any(TOOL_LIST_MARK in (m.get("content") or "") for m in m_lite)
        # 同一句话，唯一变量是降级标志
        assert lite_main is False and lite_lite is True
        # 降级档少一条 system（工具清单那条）
        assert (len(_systems(m_main)) - len(_systems(m_lite))) == 1

    def test_downgraded_keeps_history_intact(self):
        """裁剪只针对工具清单这一条 system：其余消息逐条一律不变。

        强断言：降级档 messages == 主链 messages 去掉那条工具清单 system
        （证明"裁剪量 = 恰好 1 条"，不是把别的指令一起削掉）。
        """
        q = "帮我看看我的时柱"
        h_main = _free_chat_harness()
        m_main, _ = _free_chat_messages(h_main, q, downgraded=False)
        h_lite = _free_chat_harness()
        m_lite, _ = _free_chat_messages(h_lite, q, downgraded=True)

        assert m_lite == [m for m in m_main
                          if TOOL_LIST_MARK not in (m.get("content") or "")]
        assert len(m_main) - len(m_lite) == 1
        assert any(m.get("content") == "你好" for m in m_lite)  # 历史仍在

    def test_single_message_branch_payload_unaffected(self):
        """无 session_dao 单消息分支：降级档 payload 同样不含工具清单
        （该分支 system_prompt 被 llm.chat(lite=True) 忽略，历史行为即满足）。"""
        h = _free_chat_harness(session_dao=None)
        h.llm.chat.return_value = Mock(response="精简")
        h._free_chat("帮我看看我的时柱", "u1", downgraded=True)
        _, kwargs = h.llm.chat.call_args
        assert kwargs.get("lite") is True
        # 传参侧仍按 B2-11 构造，但 lite 分支在 client 层丢弃 →
        # 真实 payload 无工具清单（由 test_single_message_lite_ignores_system_prompt 锁）


# ══════════ k65 r3：精简档「能力边界」声明（编造受控） ══════════

class TestLiteCapabilityBoundary:
    """断言**真正发出去的 payload 携带能力边界声明**，不断言具体回复文本。

    背景（r2 实测未达成项）：工具清单裁剪把「假装调用工具」断了（7/9→0/9），
    但模型被要求排盘又无工具时**仍凭空编四柱**（4/9）。根因是 prompt 只禁
    "调用工具"、没禁"没有结果时凭空给结论"。r3 在 CHAT_PROMPT_LITE 的
    硬性要求里补了命盘类 + 日期类两条边界与出口。
    """

    def test_prompt_constant_declares_both_boundaries(self):
        """常量层：命盘类 / 日期类 / 出口三类措辞齐备。"""
        for mark in BOUNDARY_MARKS:
            assert mark in CHAT_PROMPT_LITE, f"能力边界缺失: {mark}"
        # 原有精简档纪律不得被顺手改掉
        assert "回复精简：80-150 字" in CHAT_PROMPT_LITE
        assert "禁止使用任何 emoji" in CHAT_PROMPT_LITE
        assert "不调用任何工具" in CHAT_PROMPT_LITE

    def test_glm_payload_carries_boundary(self, monkeypatch):
        """发往 GLM 的 system 必须携带边界声明（不是只写在常量里）。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        llm.chat_conversation(HISTORY, lite=True)
        sys_text = _systems(calls[0][1]["json"]["messages"])[0]
        for mark in BOUNDARY_MARKS:
            assert mark in sys_text

    def test_deepseek_fallback_payload_carries_boundary(self, monkeypatch):
        """GLM 挂 → 回退 DeepSeek 走的是同一个精简 prompt，边界声明同样生效。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(
            httpx.Client, "post", _capture_post(calls, glm_raises=True))
        llm.chat_conversation(HISTORY, lite=True)
        assert calls[1][0] == ANTHROPIC_MESSAGES_URL
        sys_text = _systems(calls[1][1]["json"]["messages"])[0]
        for mark in BOUNDARY_MARKS:
            assert mark in sys_text

    def test_single_message_lite_payload_carries_boundary(self, monkeypatch):
        """:488 无历史单条 lite 路径同样携带边界声明。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        llm.chat("帮我排盘", lite=True)
        sys_text = _systems(calls[0][1]["json"]["messages"])[0]
        for mark in BOUNDARY_MARKS:
            assert mark in sys_text

    def test_stream_lite_payload_carries_boundary(self, monkeypatch):
        """流式 lite 路径同样携带边界声明。"""
        seen = {}

        async def fake_stream(api_key, messages, **kw):
            seen["messages"] = messages
            yield "精简"

        monkeypatch.setattr(
            llm_client, "glm_openai_completion_stream", fake_stream)
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        llm.chat_conversation(HISTORY, stream_cb=lambda t, p: None, lite=True)
        sys_text = _systems(seen["messages"])[0]
        for mark in BOUNDARY_MARKS:
            assert mark in sys_text

    def test_main_chain_carries_no_lite_boundary(self, monkeypatch):
        """主链不得被精简档边界句污染（边界只属于无工具能力的降级档）。"""
        llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        llm.chat_conversation(HISTORY, lite=False)
        joined = "\n".join(m.get("content") or ""
                           for m in calls[0][1]["json"]["messages"])
        assert CHAT_PROMPT_LITE not in joined
        for mark in BOUNDARY_MARKS:
            assert mark not in joined

    def test_handler_downgraded_end_to_end_carries_boundary(self, monkeypatch):
        """handler 降级档端到端：真实 MessageHandler + 真实 FortuneLLM →
        最终发给 GLM 的 payload 带边界声明，且不含工具清单。"""
        calls = []
        monkeypatch.setattr(httpx.Client, "post", _capture_post(calls))
        h = _free_chat_harness()
        h.llm = FortuneLLM(api_key="sk-deepseek", glm_api_key="zk-glm")
        h._free_chat("帮我看看我的时柱", "u1", session_id="s1", downgraded=True)

        assert calls, "必须真的发出 LLM 调用"
        url, kw = calls[0]
        assert url == GLM_COMPLETIONS_URL
        msgs = kw["json"]["messages"]
        joined = "\n".join(m.get("content") or "" for m in msgs)
        for mark in BOUNDARY_MARKS:
            assert mark in joined          # 边界声明随精简 prompt 下发
        assert TOOL_LIST_MARK not in joined  # 且工具清单已被裁掉（r2）
        assert len(_systems(msgs)) == 1
