"""M2：流式断开不留「Task was destroyed but it is pending!」任务残留。

背景（生产 2026-08-28 17:58）：客户端断开后约 +5s/+10s 各出一条
  Task was destroyed but it is pending! coro=<<async_generator_athrow without __name__>()>>
功能无影响，但属「未定属悬挂任务」的日志噪音 + 隐患。

根因（两层，均为断开路径上的悬挂任务）：
1. main.py _sse_wrap 的 async for 在生成器被提前关闭（客户端断开）时，
   按 Python 语义不会对内层 events() 生成器调用 aclose()——内层生成器
   悬挂在 yield 处，待 GC 触发 asyncgen finalizer → 事件循环上被创建为
   create_task(agen.aclose()) 任务（即 async_generator_athrow）→ 进程
   关闭/循环销毁时若仍 pending → 上述告警。
   修复：_sse_wrap 用 contextlib.aclosing 显式收尾内层生成器（断开即刻
   交付 GeneratorExit，同时触发断点续传 watcher，断连语义不变）。
2. chat_stream.py events() finally 里 loop.create_task 安排的断点续传
   watcher 任务未定属（无强引用/无完成回调）——循环关闭而 executor 未
   结束时 GC 到该任务同样会打出告警（coro=_wait_and_mark）。
   修复：模块级强引用注册表持有 + add_done_callback 完成后自动摘除。

本文件覆盖：
- 断开后内层 events() 生成器必须被关闭（不留悬挂 → 无 finalizer athrow 任务）；
- 断开后 watcher 必须被注册表定属持有，后台生成自然完成后自动摘除并补打
  offline_completed 标记（断连语义不回退）；
- 全程零「Task was destroyed」异常上下文。
"""
import asyncio
import gc
import time

USER = "u_m2_cleanup"
SID = "s_m2_cleanup0001"


class _MockMemberDAO:
    def check_quota(self, uid):
        return True

    def use_quota(self, uid):
        pass

    def get_membership(self, uid):
        return {"plan": "free"}


class _MockSessionDAO:
    """记录 mark_offline_completed 调用（等价 SessionDAO 行为）。"""

    def __init__(self):
        self.marks = []

    def get_max_message_id(self, uid, sid):
        return 42

    def mark_offline_completed(self, uid, sid, min_id=None):
        self.marks.append((uid, sid, min_id))


class _SlowHandler:
    """生成中先吐一块 chunk，再睡 3s 模拟 LLM 长生成（断开时仍在跑）。"""

    def __init__(self, session_dao):
        self.session_dao = session_dao
        self.process_calls = 0

    def pop_citations(self, uid):
        return []

    def gen_suggestions(self, uid, q, r):
        return []

    def process(self, message, user_id, stream_cb=None, deep_night=False,
                session_id=None, downgraded=False):
        self.process_calls += 1
        if stream_cb:
            stream_cb("chunk", {"text": "正在思考"})
        time.sleep(3.0)  # LLM 时长：客户端断开时 executor 仍在跑
        return "后台完成的回复"


class _Req:
    def __init__(self, sid):
        self.message = "你好"
        self.message_type = "text"
        self.voice_text = ""
        self.image_url = ""
        self.deep_night = False
        self.session_id = sid


class _Harness:
    """真实 _sse_wrap（src.main.chat_stream）驱动 + 捕获异常上下文。"""

    def __init__(self):
        self.destroyed = []

    def _exception_handler(self, loop, context):
        if "destroyed" in context.get("message", ""):
            self.destroyed.append(context)

    def run(self, coro_fn):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(self._exception_handler)
        try:
            loop.run_until_complete(coro_fn(loop))
        finally:
            try:
                loop.close()
            except RuntimeError:
                pass
        gc.collect()
        time.sleep(0.1)
        gc.collect()
        return loop


def _setup_main(monkeypatch, handler):
    """把 src.main 全局 handler/member_dao 替换为 mock（其余保持 None）。"""
    import src.main as m
    monkeypatch.setattr(m, "handler", handler)
    monkeypatch.setattr(m, "member_dao", _MockMemberDAO())
    return m


def _spy_events(monkeypatch, inner_gens):
    """记录 ChatStreamer.events 创建的内层生成器（events 在 _sse_wrap 内延迟导入，
    因此补丁打在类方法上）。"""
    import src.api.chat_stream as cs
    orig = cs.ChatStreamer.events

    def spy(self, *a, **kw):
        gen = orig(self, *a, **kw)
        inner_gens.append(gen)
        return gen

    monkeypatch.setattr(cs.ChatStreamer, "events", spy)


async def _await_completion(cond, what: str, timeout: float = 10.0,
                            interval: float = 0.2):
    """轮询等待条件成立（0.2s 间隔、上限 10s），超时才以 AssertionError 失败。

    批次2.5 M5: 替代固定 asyncio.sleep(3.5) 等待——executor 侧 sleep(3.0)
    与等待侧同起点、余量仅 0.5s，高负载下线程调度可能击穿（M2 首轮实测 flake）。
    改为完成标志轮询：只改等待逻辑，不改变被测行为与断言语义。
    """
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() >= deadline:
            raise AssertionError(f"等待超时(>{timeout:.0f}s): {what}")
        await asyncio.sleep(interval)


async def _drive_then_disconnect(m, inner_gens):
    """真实端点路径：驱动 _sse_wrap 到生成中，再 aclose（客户端断开）。"""
    resp = await m.chat_stream(_Req(SID), None, {"method": "jwt", "user_id": USER})
    wrap = resp.body_iterator
    it = wrap.__aiter__()
    evts = [await it.__anext__()]                       # start
    evts.append(await asyncio.wait_for(it.__anext__(), timeout=5))  # chunk
    assert len(inner_gens) == 1
    inner = inner_gens[0]
    assert "start" in evts[0] and "chunk" in evts[1]
    await wrap.aclose()                                 # 客户端断开
    return wrap, inner


# ── Fix A：内层生成器必须被显式收尾 ────────────────────────────────

def test_wrap_disconnect_closes_inner_generator(monkeypatch):
    """断开后内层 events() 生成器必须已关闭——不留悬挂，asyncgen finalizer
    就不会再创建 async_generator_athrow 任务（生产告警根因）。"""
    session_dao = _MockSessionDAO()
    handler = _SlowHandler(session_dao)
    m = _setup_main(monkeypatch, handler)
    inner_gens = []
    _spy_events(monkeypatch, inner_gens)

    h = _Harness()

    async def scenario(loop):
        wrap, inner = await _drive_then_disconnect(m, inner_gens)
        # 核心断言：断开即刻内层生成器已被 _sse_wrap 显式 aclose（ag_frame 为 None）
        assert inner.ag_frame is None, \
            "断开后内层 events() 生成器必须被关闭（当前仍悬挂在 yield 处）"
        # executor 继续自然跑完 → watcher 完成 → 离线标记补打（断连语义不回退）
        await _await_completion(
            lambda: session_dao.marks == [(USER, SID, 42)],
            what="离线补打标记 marks",
        )
        assert session_dao.marks == [(USER, SID, 42)]
        # 无任务残留：当前仅剩 scenario 自身
        pending = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
        assert pending == []

    h.run(scenario)
    assert h.destroyed == [], \
        "不得出现 Task-was-destroyed 异常上下文: %s" % h.destroyed


# ── Fix B：watcher 定属（模块级注册表持有 + 完成后摘除） ────────────

def test_disconnect_watcher_owned_registered_then_removed(monkeypatch):
    """断开时 watcher 必须被模块级注册表定属持有（防 GC），后台生成自然完成后
    自动摘除并补打 offline_completed 标记。"""
    import src.api.chat_stream as cs

    registry = getattr(cs, "_OFFLINE_WATCHERS", None)
    assert registry is not None, "watcher 必须有模块级注册表持有（防 GC 销毁）"
    snapshot = set(registry)

    session_dao = _MockSessionDAO()
    handler = _SlowHandler(session_dao)
    m = _setup_main(monkeypatch, handler)
    inner_gens = []
    _spy_events(monkeypatch, inner_gens)

    h = _Harness()

    async def scenario(loop):
        wrap, inner = await _drive_then_disconnect(m, inner_gens)
        # 断开即刻：恰好注册一个 watcher 任务（定属，pending，未完成）
        new = set(registry) - snapshot
        assert len(new) == 1, "断开后必须恰好注册一个 watcher 任务"
        watcher = next(iter(new))
        assert not watcher.done()
        # 后台生成自然跑完 → watcher 完成 → 自动摘除 + 补打离线标记
        await _await_completion(
            lambda: watcher.done()
            and set(registry) == snapshot
            and session_dao.marks == [(USER, SID, 42)],
            what="watcher 完成/摘除/离线标记",
        )
        assert watcher.done()
        assert set(registry) == snapshot, "watcher 完成后必须从注册表摘除"
        assert session_dao.marks == [(USER, SID, 42)]
        pending = [t for t in asyncio.all_tasks(loop)
                   if t is not asyncio.current_task()]
        assert pending == []

    h.run(scenario)
    assert h.destroyed == [], \
        "不得出现 Task-was-destroyed 异常上下文: %s" % h.destroyed
