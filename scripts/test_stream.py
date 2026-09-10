#!/usr/bin/env python3
"""v8 阶段 3（过程体验）后端验证：SSE 流式端点 /api/chat/stream。

覆盖：
  1. 事件序列：start → (thinking/tool)* → chunk* → done
  2. 无工具简单聊天不发 thinking（模式一）
  3. 工具/命理问题 → thinking/tool 事件（模式二）
  4. 客户端中断（abort）→ 后端记录取消日志
  5. 异常路径：未登录 401；error 事件（stub 失败处理器单元级）
  6. 看门狗：chunk 间隔超时 → error 事件（单元级，短间隔）
  7. /api/chat（非流式）兼容回归

用法：
  python3 scripts/test_stream.py [base_url]
  默认 http://127.0.0.1:8769（本机测试实例；生产 8767 由部署方重启）
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("STREAM_TEST_BASE", sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8769")
USER_ID = "stream_test_user"
TOKEN = None

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def login():
    global TOKEN
    req = urllib.request.Request(
        BASE + "/api/user/login",
        data=json.dumps({"code": "dev_code"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    TOKEN = data["token"]
    assert TOKEN, "login failed"
    print(f"[login] token ok (sub={data.get('user', {}).get('id', '?')})")


def post_stream(message, message_type="text", voice_text="", timeout=90):
    """POST /api/chat/stream，返回事件列表。"""
    body = {"message": message, "message_type": message_type}
    if voice_text:
        body["voice_text"] = voice_text
    req = urllib.request.Request(
        BASE + "/api/chat/stream",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
    )
    events = []
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        assert r.headers.get("Content-Type", "").startswith("text/event-stream"), r.headers.get("Content-Type")
        buf = b""
        while True:
            line = r.readline()
            if not line:
                break
            if line.strip() == b"":
                # SSE 事件结束：解析 buffer 中的 data 行
                for raw in buf.split(b"data:"):
                    raw = raw.strip()
                    if raw:
                        events.append(json.loads(raw.decode("utf-8")))
                buf = b""
            else:
                buf += line
        if buf:
            for raw in buf.split(b"data:"):
                raw = raw.strip()
                if raw:
                    events.append(json.loads(raw.decode("utf-8")))
    elapsed = time.monotonic() - t0
    return events, elapsed


def first_start_latency(events):
    """start 事件前无任何内容可测；这里返回 start 之前的 http 建立延迟≈首字节。"""
    return None


def collect_stream_events_until_done(events):
    types = [e["type"] for e in events]
    return types


def test_event_protocol_simple():
    print("\n[1] 简单聊天（模式一：start → chunk* → done，无 thinking）")
    events, elapsed = post_stream("你好，今天心情不错")
    types = collect_stream_events_until_done(events)
    ok("事件序列合法", types and types[0] == "start", f"types={types[:6]}")
    ok("以 done 结束", types[-1] == "done", f"last={types[-1]}")
    ok("中间只有 chunk/ping", all(t in ("chunk", "ping") for t in types[1:-1]), f"mid={types[1:-1][:8]}")
    ok("无 thinking（简单聊天不发思考路径）", "thinking" not in types, f"types={types}")
    text = "".join(e.get("content", "") for e in events if e["type"] == "chunk")
    ok("正文非空", len(text) > 10, f"len={len(text)}")
    print(f"     正文长度={len(text)} 耗时={elapsed:.1f}s 事件数={len(events)}")
    ok("done 携带 consultation_id", events[-1].get("consultation_id") is not None, str(events[-1])[:120])


def test_event_protocol_thinking():
    print("\n[2] 排盘问题（模式二：start → thinking/tool* → chunk* → done）")
    events, elapsed = post_stream("帮我排盘，我1990年5月20日15点30分出生，北京，男")
    types = collect_stream_events_until_done(events)
    ok("事件序列合法", types and types[0] == "start", f"types={types[:6]}")
    ok("以 done 结束", types[-1] == "done", f"last={types[-1]}")
    has_think = "thinking" in types or "tool" in types
    ok("含思考/工具事件", has_think, f"types={[t for t in types if t in ('thinking','tool')][:6]}")
    think_texts = [e.get("text", "") for e in events if e["type"] in ("thinking", "tool")]
    ok("思考文案合理", any("排盘" in t or "八字" in t or "看你的八字" in t for t in think_texts), str(think_texts[:4]))
    text = "".join(e.get("content", "") for e in events if e["type"] == "chunk")
    ok("正文非空", len(text) > 20, f"len={len(text)}")
    print(f"     思考/工具事件={think_texts[:4]} 正文长度={len(text)} 耗时={elapsed:.1f}s")


def test_disconnect_cancellation():
    print("\n[3] 客户端中断 → 后端记录取消日志")
    body = {"message": "帮我详细分析一下我的财运和事业，我1990年5月20日15点30分出生，北京，男"}
    req = urllib.request.Request(
        BASE + "/api/chat/stream",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            got_some = False
            while time.monotonic() - t0 < 8:
                line = r.readline()
                if not line:
                    break
                # data: {"type": "chunk" / "thinking", ...}（json.dumps 默认带空格分隔符）
                if b'"type": "chunk"' in line or b'"type": "thinking"' in line or b'"type":"chunk"' in line:
                    got_some = True
                    break
            ok("断连前收到过流式内容", got_some)
            print("  [abort] 已收到部分事件，现在中断连接")
            # 关闭 socket = 模拟前端 wx.request.abort()
            r.close()
    except urllib.error.URLError as e:
        ok("断连发生异常（预期网络错误）", True, str(e)[:80])

    # 等待后端处理取消并写日志（executor 线程稍后完成）
    time.sleep(4)
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "app.log")
    log_path = os.path.abspath(log_path)
    try:
        content = open(log_path, encoding="utf-8", errors="ignore").read()
    except OSError:
        content = ""
    cancel_seen = "SSE 流被客户端中断" in content or "stream generator closed early" in content
    ok("后端日志出现中断记录", cancel_seen, f"log={log_path} len={len(content)}")
    if not cancel_seen:
        tail = content.splitlines()[-10:]
        print("     log tail:", tail)


def test_auth_401():
    print("\n[4] 未登录 → 401（异常路径）")
    try:
        req = urllib.request.Request(
            BASE + "/api/chat/stream",
            data=json.dumps({"message": "hi"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)
        ok("未登录被拒绝", False, "竟然 200")
    except urllib.error.HTTPError as e:
        ok("未登录被拒绝 401", e.code == 401, f"code={e.code}")
    except Exception as e:
        ok("未登录被拒绝", True, str(e)[:80])


def test_non_stream_compat():
    print("\n[5] /api/chat 非流式兼容回归")
    req = urllib.request.Request(
        BASE + "/api/chat",
        data=json.dumps({"message": "你好"}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    ok("返回 reply", bool(data.get("reply")), f"reply_len={len(data.get('reply',''))}")
    ok("带 consultation_id", data.get("consultation_id") is not None, str(data)[:120])


def unit_error_and_watchdog():
    print("\n[6] 单元级：error 事件 + 看门狗（短间隔）")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import asyncio

    from src.api.chat_stream import ChatStreamer

    class _BadHandler:
        def process(self, message, user_id, stream_cb=None):
            raise RuntimeError("boom")

    class _MemberDAO:
        def check_quota(self, uid):
            return True

        def use_quota(self, uid):
            return True

    class _DAO:
        def get_last_consultation_id(self, uid):
            return 42

    class _Req:
        message = "hi"
        message_type = "text"
        image_url = ""
        voice_text = ""
        user_id = "u"

    class _Auth:
        def get(self, k, d=None):
            return {"user_id": "u"}.get(k, d)

    class _Request:
        headers = {}

    async def run_error():
        st = ChatStreamer(_BadHandler(), _MemberDAO(), _DAO())
        evs = [e async for e in st.events(_Req(), _Request(), _Auth())]
        return evs

    evs = asyncio.run(run_error())
    types = [e["type"] for e in evs]
    ok("处理器异常 → error 事件", "error" in types, str(types))
    ok("error 在 done 之前/替代 done", types[-1] == "error", str(types))

    # 看门狗：慢处理器 + chunk_gap_timeout=1s → error
    import threading
    import time as _t

    class _SlowHandler:
        def process(self, message, user_id, stream_cb=None):
            _t.sleep(3)
            return "太慢了"

    async def run_watchdog():
        st = ChatStreamer(_SlowHandler(), _MemberDAO(), _DAO(),
                          ping_interval=0.3, chunk_gap_timeout=1.0, simulation_delay=0)
        t0 = _t.monotonic()
        evs = [e async for e in st.events(_Req(), _Request(), _Auth())]
        return evs, _t.monotonic() - t0

    evs, elapsed = asyncio.run(run_watchdog())
    types = [e["type"] for e in evs]
    ok("看门狗超时 → error 事件", "error" in types, str(types))
    ok("看门狗及时触发", elapsed < 3.0, f"elapsed={elapsed:.1f}s")
    ok("心跳 ping 已发出", "ping" in types, str(types[:8]))


def unit_tool_event():
    print("\n[7] 单元级：<tool_call> → tool 事件 + 后续正文真实流式")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import asyncio

    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult

    # 轻量实例：只挂 _run_tool_loop 需要的属性
    h = object.__new__(MessageHandler)
    h.llm = type("L", (), {"api_key": "x", "model": "deepseek-flash"})()
    h.session_dao = None
    h._tool_logs = {}
    h._citations = {}
    h._execute_tool_call = lambda name, params, uid, user_question="": ToolResult(name, True, "模拟的古籍检索结果：桃花运与流年红鸾有关。")

    import src.llm.client as _llmc
    _orig = _llmc.deepseek_anthropic_completion

    calls = []
    streamed = []

    def fake_completion(api_key, messages, model="deepseek-flash", max_tokens=1000,
                        temperature=0.7, timeout=60.0, client=None, stream_cb=None):
        # 模拟真实流式：分两次回调增量
        if stream_cb:
            stream_cb("chunk", {"text": "查到了。古籍"})
            stream_cb("chunk", {"text": "记载，今年桃花运不错。"})
        return "查到了。古籍记载，今年桃花运不错。"

    _llmc.deepseek_anthropic_completion = fake_completion
    try:
        events = []
        reply = h._run_tool_loop(
            "帮我查桃花", "u",
            "我去查查古籍。<tool_call>检索: 桃花运</tool_call>",
            stream_cb=lambda t, p: events.append((t, p)),
        )
    finally:
        _llmc.deepseek_anthropic_completion = _orig

    tool_events = [p.get("text", "") for t, p in events if t == "tool"]
    ok("发出 tool 事件", len(tool_events) == 1, str(tool_events))
    ok("tool 文案为「正在查阅古籍…」", tool_events and tool_events[0] == "正在查阅古籍…", str(tool_events))
    chunk_text = "".join(p.get("text", "") for t, p in events if t == "chunk")
    ok("后续正文以增量回调（真实流式）", "查到了。古籍" in chunk_text, f"chunk={chunk_text[:40]}")
    ok("最终回复为工具后文本", reply and "桃花运" in reply, reply[:40])


if __name__ == "__main__":
    print(f"== 易理明灯 v8 阶段 3 流式端点验证 ==  base={BASE}")
    t0 = time.monotonic()
    login()
    test_event_protocol_simple()
    test_event_protocol_thinking()
    test_disconnect_cancellation()
    test_auth_401()
    test_non_stream_compat()
    unit_error_and_watchdog()
    unit_tool_event()
    print(f"\n== 结果: {PASS} 通过, {FAIL} 失败, 总耗时 {time.monotonic()-t0:.0f}s ==")
    sys.exit(1 if FAIL else 0)
