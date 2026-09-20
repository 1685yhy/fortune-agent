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

from src.llm import client as llm_client  # noqa: E402
from src.llm.client import (  # noqa: E402
    FortuneLLM, GLM_COMPLETIONS_URL, GLM_DEFAULT_MODEL, ANTHROPIC_MESSAGES_URL,
)
from src.llm.prompts import CHAT_PROMPT, CHAT_PROMPT_LITE  # noqa: E402

HISTORY = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好呀，有什么想问的？"},
    {"role": "user", "content": "帮我看看我的时柱"},
]

# 调用方自带的 system（handler 在 chat_conversation 前注入的[可用工具清单]
# 形态，缩小版）：边界策略要求它必须原样保留。
CALLER_SYSTEM = {"role": "system", "content": "[可用工具清单]\n自定义清单内容"}


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
