"""批次2.5 M1: 晨笺预生成移出事件循环——接线锁定测试。

背景: 启动期 _daily_jian_precompute 首次运行在事件循环主线程同步执行
FAISS 检索 + bge-reranker-v2-m3 重排(分钟级 CPU 计算),事件循环被占死,
uvicorn 端口 20 分钟不监听。修复目标: 预生成整段经 asyncio.to_thread
委托线程池,端口在模型加载完成后立即绑定,预生成后台完成(结果等价)。

本测试锁定接线:
1. _precompute_jian_for 仍在 worker 线程被按当日日期调用(语义不变);
2. 调用发生在 worker 线程而非事件循环主线程(即已移出事件循环)。
"""
import asyncio
import threading
from datetime import datetime, timezone, timedelta

import pytest

import src.main as main_mod


class _StopWorker(Exception):
    """第一次 asyncio.sleep(3600) 时中止 worker 循环,避免死循环。"""


def test_jian_precompute_offloaded_to_worker_thread(monkeypatch):
    """预生成必须经线程池在 worker 线程执行,且仍按当日日期调用。"""
    main_tid = threading.get_ident()
    observed = {"tid": None, "dates": []}
    done = threading.Event()

    def fake_precompute(date_str):
        observed["tid"] = threading.get_ident()
        observed["dates"].append(date_str)
        done.set()
        return {"date": date_str}

    async def _stop_sleep(*_a, **_k):
        raise _StopWorker()

    monkeypatch.setattr(main_mod, "_precompute_jian_for", fake_precompute)
    monkeypatch.setattr(main_mod.asyncio, "sleep", _stop_sleep)

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(_StopWorker):
            loop.run_until_complete(main_mod._daily_jian_precompute())
    finally:
        loop.close()

    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    assert observed["dates"] == [today], \
        "预生成仍按当日日期调用 _precompute_jian_for(语义不变)"
    assert observed["tid"] is not None and observed["tid"] != main_tid, \
        "预生成必须在 worker 线程执行(已移出事件循环)"
