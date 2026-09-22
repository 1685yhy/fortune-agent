# -*- coding: utf-8 -*-
"""k85 必修5 · 接续 k83：**SSE 端点层的端到端真调**（k83 登记的第 7 条残留）。

## 背景

k83 报告 §⑧.7：「`chat_stream.py` 的 lite 真实 SSE 链路本批未真调（只测到
`_chat_lite` 返回值层 + 客户端一致性）；判定：拦截点在 `_chat_lite`，与端点无关，
但**未做 SSE 层端到端实测**（如实登记）」。控制方要求本批**补端到端真调**。

## 本文件测什么（真实链路，只有**网络传输**被替身）

    POST /api/chat/stream（真端点）
      → ChatStreamer.events（真编排：配额 → executor → 事件队列 → 看门狗）
      → handler.process（**测试用的最小 handler**：只做 `_free_chat` 里那一次
         LLM 调用，见 `_LiteHandler.process`）
      → FortuneLLM.chat_conversation(lite=True) → `_chat_lite`（真降级链 + 真 E 段拦截）
      → 假的上游 SSE（`glm_openai_completion_stream` 替身，唯一替身 = 网络）

> 为什么 handler 是最小替身而不是真实 `MessageHandler`：真实 `process` 会依次跑
> 安全审计 / 意图分类 LLM / RAG 检索 / 记忆 / 落库，与"E 段在**端点层**是否生效"
> 无关，却会把本用例变成一条脆弱的全链集成测试。被验证的拦截点在 `_chat_lite`，
> 端点的职责是"把定稿文本原样送进 `done.content` 并落库"——这两段本文件都用**真**
> 实现。红线：不联网（上游替身）、不碰生产库（无 dao）、不新增 skip/xfail。

## 端到端实测结论（本文件钉住）

  · 上游编造四柱（k65 r3 的原始形态）→ `done.content`（= 定稿 = 落库文本）
    **不含**任何编造干支，且同句的正常文字**逐字保留**（不误杀）；
  · **已知边界（k83 §⑧.1，本批未改）**：流式 `chunk` 是"已上屏不回撤"的——
    E 段作用在**返回文本/定稿**上，增量在守卫之前就已下发。故本文件**只**断言
    `done.content`，并**显式断言 chunks 可能仍带原文**（把这个边界写成事实，
    防止后来者误以为"流式也拦了"）。
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

import src.llm.client as llm_client  # noqa: E402

USER = "k85-sse-user"
SID = "k85-sse-session"

#: k65 r3 的**原始事故形态**：降级档自报"不提供排盘"却给出四柱干支。
_FAB = "精简模式暂不提供排盘服务，不过看你命里庚午年、己巳月、乙巳日、丙申时，近期要留意。"
_FAB_PILLARS = ("庚午", "己巳", "乙巳", "丙申")
#: 同句里**不是**无依据主张的部分（必须逐字活下来）。
_KEPT = "精简模式暂不提供排盘服务"


class _MockMemberDAO:
    def get_membership(self, uid):
        return {"plan": "free"}


class _LiteHandler:
    """最小 handler：只做 `_free_chat` 里那一次 LLM 调用（真 `_chat_lite`）。"""

    def __init__(self, llm):
        self.llm = llm
        self.session_dao = None

    def pop_citations(self, uid):
        return []

    def gen_suggestions(self, uid, q, r):
        return []

    def process(self, message, user_id, stream_cb=None, deep_night=False,
                session_id=None, downgraded=False, regen=False):
        # 与 handler._free_chat 的 LLM 调用同形（历史 + lite=降级 + stream_cb）
        return self.llm.chat_conversation(
            [{"role": "user", "content": message}],
            lite=downgraded, stream_cb=stream_cb)


class _Req:
    def __init__(self, sid):
        self.message = "帮我看看最近怎么样"
        self.message_type = "text"
        self.voice_text = ""
        self.image_url = ""
        self.deep_night = False
        self.session_id = sid
        self.regen = False


@pytest.fixture
def _fake_glm(monkeypatch):
    """把上游 GLM 流式调用换成"编造四柱"的假实现（唯一替身 = 网络）。"""
    async def _stream(api_key, messages, **kw):
        yield "精简模式暂不提供排盘服务，不过看你命里"
        yield "庚午年、己巳月、乙巳日、丙申时，近期要留意。"

    monkeypatch.setattr(llm_client, "glm_openai_completion_stream", _stream)


def _run_sse(monkeypatch, handler, max_events=80, timeout=20.0):
    """驱动真实 `/api/chat/stream` 端点，收集事件直到 done/error/耗尽。

    复用 `tests/test_k33_chunk_watchdog.py` 的驱动口径（同一个"真端点 + 真
    ChatStreamer"做法），只把 ping/超时压短以便快跑。
    """
    import src.api.chat_stream as cs
    import src.main as m

    class _FastStreamer(cs.ChatStreamer):
        def __init__(self, *a, **kw):
            kw.setdefault("ping_interval", 0.15)
            kw.setdefault("chunk_gap_timeout", 1.0)
            kw.setdefault("simulation_delay", 0)
            super().__init__(*a, **kw)

    monkeypatch.setattr(cs, "ChatStreamer", _FastStreamer)
    monkeypatch.setattr(m, "handler", handler)
    monkeypatch.setattr(m, "member_dao", _MockMemberDAO())
    monkeypatch.setattr(m, "dao", None)
    # 降级档：免费用户第 16 条起（本用例直接把它钉成"超限"）
    from src.services import chat_quota as cq
    monkeypatch.setattr(cq, "try_consume_chat_quota",
                        lambda *a, **kw: {"downgraded": True}, raising=True)

    def _parse_sse(raw):
        out = []
        for line in str(raw).splitlines():
            if not line.startswith("data:"):
                continue
            try:
                out.append(json.loads(line[5:].strip()))
            except Exception:
                continue
        return out

    async def scenario():
        resp = await m.chat_stream(_Req(SID), None, {"method": "jwt", "user_id": USER})
        it = resp.body_iterator.__aiter__()
        events, done = [], False
        deadline = time.monotonic() + timeout
        while not done and len(events) < max_events and time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(it.__anext__(), timeout=5)
            except (StopAsyncIteration, asyncio.TimeoutError):
                break
            for evt in _parse_sse(raw):
                events.append(evt)
                if evt.get("type") in ("done", "error"):
                    done = True
        try:
            await resp.body_iterator.aclose()
        except Exception:
            pass
        pending = [t for t in asyncio.all_tasks()
                   if t is not asyncio.current_task() and not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=10)
        return events

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(scenario())
    finally:
        try:
            loop.close()
        except Exception:
            pass


def _make_llm():
    """真 FortuneLLM（GLM key 走替身上游，DeepSeek key 不会被动到）。"""
    from src.llm.client import FortuneLLM
    return FortuneLLM(api_key="", glm_api_key="zk-k85-test")


class TestSseLiteEndToEnd:
    """端点层端到端：真端点 → 真 ChatStreamer → 真 `_chat_lite` → 真 E 段。"""

    def test_fabricated_pillars_never_reach_done_content(self, monkeypatch, _fake_glm):
        handler = _LiteHandler(_make_llm())
        events = _run_sse(monkeypatch, handler)

        kinds = [e.get("type") for e in events]
        assert "done" in kinds, f"端点没跑完（事件={kinds}）"
        assert "error" not in kinds, f"端点报错：{[e for e in events if e.get('type') == 'error']}"
        done = [e for e in events if e.get("type") == "done"][-1]
        assert done.get("downgraded") is True, "没有走降级档 —— 本用例的前提没了"

        final = done.get("content") or ""
        assert final, "done.content 为空（定稿丢失）"
        for p in _FAB_PILLARS:
            assert p not in final, f"编造干支 {p} 漏到了定稿（端点层 E 段未生效）：{final!r}"
        assert "精简模式暂不提供排盘服务" in final, \
            f"正常文字被误杀（不误杀红线）：{final!r}"

    def test_stream_chunks_are_a_known_unretractable_boundary(self, monkeypatch, _fake_glm):
        """**把 k83 §⑧.1 的边界写成事实**：增量已上屏 ⇒ chunk 可能仍带原文。

        这不是回归：E 段契约只在**返回文本**上生效（`_chat_lite` 的出口），
        而 SSE 增量在守卫之前就已下发。前端 k5 单稿流已就位（`done.content`
        可用于整泡替换），**是否消费属产品决定**（k83 登记项 R-FAB-1）。
        本用例存在的意义：后来者不得宣称"流式也拦了"。
        """
        handler = _LiteHandler(_make_llm())
        events = _run_sse(monkeypatch, handler)
        chunks = "".join(str(e.get("content") or e.get("text") or "")
                         for e in events if e.get("type") == "chunk")
        done = [e for e in events if e.get("type") == "done"][-1]
        final = done.get("content") or ""
        assert not any(p in final for p in _FAB_PILLARS), "定稿仍有编造（见上一条用例）"
        # 边界：chunk 里**可能**有 —— 若这里变成"没有"，说明有人做了增量拦截，
        # 那时请把 k83 R-FAB-1 结项并**改本断言**（属放宽，须控制方拍板）。
        assert any(p in chunks for p in _FAB_PILLARS), (
            "chunk 里已无编造 —— 增量拦截被实现了（好事），"
            "请按 k83 R-FAB-1 结项流程更断断言，勿静默保留一条假的事实描述")

    def test_grounded_lite_reply_is_untouched(self, monkeypatch):
        """**不误杀**的端到端形态：上游只回显用户自报事实 → 定稿逐字不变。"""
        async def _stream(api_key, messages, **kw):
            yield "你说的"
            yield "1990年5月20日我记下了，我们慢慢聊。"

        monkeypatch.setattr(llm_client, "glm_openai_completion_stream", _stream)
        handler = _LiteHandler(_make_llm())

        class _ReqDated(_Req):
            def __init__(self, sid):
                super().__init__(sid)
                self.message = "我是1990年5月20日出生的"

        import src.api.chat_stream as cs
        import src.main as m

        class _FastStreamer(cs.ChatStreamer):
            def __init__(self, *a, **kw):
                kw.setdefault("ping_interval", 0.15)
                kw.setdefault("chunk_gap_timeout", 1.0)
                kw.setdefault("simulation_delay", 0)
                super().__init__(*a, **kw)

        monkeypatch.setattr(cs, "ChatStreamer", _FastStreamer)
        monkeypatch.setattr(m, "handler", handler)
        monkeypatch.setattr(m, "member_dao", _MockMemberDAO())
        monkeypatch.setattr(m, "dao", None)
        from src.services import chat_quota as cq
        monkeypatch.setattr(cq, "try_consume_chat_quota",
                            lambda *a, **kw: {"downgraded": True}, raising=True)

        def _parse(raw):
            out = []
            for line in str(raw).splitlines():
                if line.startswith("data:"):
                    try:
                        out.append(json.loads(line[5:].strip()))
                    except Exception:
                        pass
            return out

        async def scenario():
            resp = await m.chat_stream(_ReqDated(SID), None,
                                       {"method": "jwt", "user_id": USER})
            it = resp.body_iterator.__aiter__()
            events, done = [], False
            deadline = time.monotonic() + 20
            while not done and len(events) < 80 and time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(it.__anext__(), timeout=5)
                except (StopAsyncIteration, asyncio.TimeoutError):
                    break
                for e in _parse(raw):
                    events.append(e)
                    if e.get("type") in ("done", "error"):
                        done = True
            try:
                await resp.body_iterator.aclose()
            except Exception:
                pass
            pending = [t for t in asyncio.all_tasks()
                       if t is not asyncio.current_task() and not t.done()]
            if pending:
                await asyncio.wait(pending, timeout=10)
            return events

        loop = asyncio.new_event_loop()
        try:
            events = loop.run_until_complete(scenario())
        finally:
            loop.close()

        final = [e for e in events if e.get("type") == "done"][-1].get("content") or ""
        assert final == "你说的1990年5月20日我记下了，我们慢慢聊。", \
            f"用户自报日期的回显被误杀：{final!r}"
