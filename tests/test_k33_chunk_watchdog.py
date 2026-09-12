# -*- coding: utf-8 -*-
"""k33/A26：门控后引擎静默窗口变长 —— chunk timeout 看门狗验证（验证项）。

背景（k5 Minor-2）：门控/长引擎链背靠背流式（无 thinking/tool 事件时）理论
可触发 chunk timeout 看门狗。缓解已在 `ChatStreamer` 内：**正文流尚未开始
（chunk_texts ≤ 1，只有 welcome 开场）时给 2× 宽限**（`chunk_gap_timeout * 2`），
正文流开始后维持原间隔。

本文件以真实 `src.main.chat_stream` 端点 + 缩小间隔（ping 0.15s / gap 1.0s）
确定性验证两点：
  1. 长引擎静默（>gap 但 <2×gap，正文流未开始）**不得**误杀 → 无 error 事件；
  2. 正文流开始后同样时长的静默**必须**触发 error（看门狗未失效）。

生产侧证据（真机 grep `chunk timeout` 零命中）见 task-k33-report.md。

运行：OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 -m pytest \
      tests/test_k33_chunk_watchdog.py -q
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

USER = "u_k33_watchdog"
SID = "s_k33_watchdog01"
GAP = 1.0          # chunk_gap_timeout（测试缩小值；生产 60s）
SILENCE = 1.3      # 引擎静默时长：> GAP，< 2×GAP（宽限窗内）


class _MockMemberDAO:
    def check_quota(self, uid):
        return True

    def use_quota(self, uid):
        pass

    def get_membership(self, uid):
        return {"plan": "free"}


class _Req:
    def __init__(self, sid):
        self.message = "帮我排个盘"
        self.message_type = "text"
        self.voice_text = ""
        self.image_url = ""
        self.deep_night = False
        self.session_id = sid
        self.regen = False


class _SilentEngineHandler:
    """模拟门控后长引擎静默：先吐 n 块，再静默 SILENCE 秒，最后返回回复。"""

    def __init__(self, welcome_chunks: int, silence: float = SILENCE):
        self.session_dao = None
        self.welcome_chunks = welcome_chunks
        self.silence = silence

    def pop_citations(self, uid):
        return []

    def gen_suggestions(self, uid, q, r):
        return []

    def process(self, message, user_id, stream_cb=None, deep_night=False,
                session_id=None, downgraded=False, regen=False):
        for i in range(self.welcome_chunks):
            if stream_cb:
                stream_cb("chunk", {"text": f"开场第{i + 1}块"})
        time.sleep(self.silence)  # 引擎计算阶段：无任何事件
        if stream_cb:
            stream_cb("chunk", {"text": "正文来了。"})
        return "正文来了。"


def _run(monkeypatch, handler, max_events: int = 60, timeout: float = 15.0):
    """驱动真实 /api/chat/stream 端点，收集事件直到 done/error/耗尽。"""
    import src.api.chat_stream as cs
    import src.main as m

    class _FastStreamer(cs.ChatStreamer):
        def __init__(self, *a, **kw):
            kw.setdefault("ping_interval", 0.15)
            kw.setdefault("chunk_gap_timeout", GAP)
            kw.setdefault("simulation_delay", 0)
            super().__init__(*a, **kw)

    monkeypatch.setattr(cs, "ChatStreamer", _FastStreamer)
    monkeypatch.setattr(m, "handler", handler)
    monkeypatch.setattr(m, "member_dao", _MockMemberDAO())

    def _parse_sse(raw):
        """body_iterator 产出的是 SSE 文本行（data: {json}）→ 解析成事件字典。"""
        import json as _json
        out = []
        for line in str(raw).splitlines():
            if not line.startswith("data:"):
                continue
            try:
                out.append(_json.loads(line[5:].strip()))
            except Exception:
                continue
        return out

    async def scenario():
        resp = await m.chat_stream(_Req(SID), None, {"method": "jwt", "user_id": USER})
        it = resp.body_iterator.__aiter__()
        events = []
        deadline = time.monotonic() + timeout
        done = False
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
        # 收尾：等断点续传 watcher（后台生成完成即结束）跑完，避免关闭事件循环时
        # 打出 "Task was destroyed but it is pending!" 噪音（测试输出干净）
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
        except RuntimeError:
            pass


def _has_timeout_error(events) -> bool:
    return any(e.get("type") == "error" and "超时" in str(e.get("message", ""))
               for e in events)


def test_long_engine_silence_before_body_not_killed(monkeypatch):
    """正文流未开始（仅 1 块 welcome）+ 静默 SILENCE(>GAP,<2GAP) → 不误杀。"""
    events = _run(monkeypatch, _SilentEngineHandler(welcome_chunks=1))
    assert not _has_timeout_error(events), \
        f"宽限窗内不得触发 chunk timeout（2×GAP）；events={events}"
    types = [e.get("type") for e in events]
    assert "start" in types
    assert "正文来了。" in "".join(e.get("content", "") for e in events)


def test_no_welcome_chunk_long_silence_not_killed(monkeypatch):
    """一块正文都没流出（纯引擎阶段静默）→ 同样在宽限窗内不误杀。"""
    events = _run(monkeypatch, _SilentEngineHandler(welcome_chunks=0))
    assert not _has_timeout_error(events), f"events={events}"


def test_silence_after_body_started_still_times_out(monkeypatch):
    """正文流已开始（≥2 块）后同样时长静默 → 看门狗必须照常触发（未失效）。"""
    events = _run(monkeypatch, _SilentEngineHandler(welcome_chunks=2))
    assert _has_timeout_error(events), \
        f"正文流开始后超时看门狗必须触发；events={events}"
    assert events[-1].get("type") == "error"
