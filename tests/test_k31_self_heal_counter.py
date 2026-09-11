# -*- coding: utf-8 -*-
"""k31 加固：`_record_self_heal` 计数原子性（k30 审查遗留项）。

k30 审查记录：`evt["count"] += 1` 是读-改-写，真并发下同一集合名的两次
登记可能只 +1（k28 起 count 为进程内运维可观测状态，不参与业务判定）。
本批以 1 处 `threading.Lock` 收口：整个更新步（含首次 setdefault）原子。

判别力：
- `test_record_self_heal_updates_count_inside_lock`：**确定性结构断言**
  （零线程、零 sleep）——`__exit__`（仍持锁）时计数必须已 +1，锁外不可能
  观测到「已进入但未更新」的中间态。旧代码无锁 → 该断言失败。
- `test_record_self_heal_parallel_calls_exact_count`：真并发冒烟网
  （8 线程 × 50 次 → 必须恰好 400；仅在计数正确时通过）。
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture
def rt(monkeypatch):
    """隔离的进程内状态：每个用例一份空 store（不动真实登记）。"""
    import src.rag.retriever as _rt
    monkeypatch.setattr(_rt, "_SELF_HEAL_EVENTS", {})
    return _rt


def test_record_self_heal_updates_count_inside_lock(rt, monkeypatch):
    """计数更新必须发生在锁内（持锁时已是 +1 后状态）。"""
    observed = []

    class _RecordingLock:
        """替身锁：__exit__ 仍在临界区内 → 此处观测到的必须是更新后的值。"""

        def __enter__(self):
            evt = rt._SELF_HEAL_EVENTS.get("k31_lk")
            observed.append(("enter", evt and evt["count"]))   # 快照标量
            return self

        def __exit__(self, *exc):
            evt = rt._SELF_HEAL_EVENTS.get("k31_lk")
            observed.append(("exit", evt and evt["count"]))
            return False

    monkeypatch.setattr(rt, "_SELF_HEAL_LOCK", _RecordingLock())
    assert rt._record_self_heal("k31_lk", "r1", None) == 1
    assert rt._record_self_heal("k31_lk", "r2", "fortune_v6") == 2

    # enter 时尚未登记/更新；exit（持锁）时计数已完成本次自增
    assert [o[0] for o in observed] == ["enter", "exit", "enter", "exit"]
    assert observed[0][1] is None and observed[1][1] == 1
    assert observed[2][1] == 1 and observed[3][1] == 2
    # 返回值即本次登记后的累计；末态字段正确
    assert rt.self_heal_events()["k31_lk"]["last_reason"] == "r2"
    assert rt.self_heal_events()["k31_lk"]["healed_to"] == "fortune_v6"


def test_record_self_heal_parallel_calls_exact_count(rt):
    """真并发冒烟网：8 线程 × 50 次同一集合名 → 恰好 400（不丢失）。"""
    n_threads, per_thread = 8, 50
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()                       # 同时起跑，放大交错窗口
        for _ in range(per_thread):
            rt._record_self_heal("k31_par", "parallel", None)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert rt.self_heal_events()["k31_par"]["count"] == n_threads * per_thread


def test_record_self_heal_mutates_only_process_state(rt):
    """锁只护进程内状态更新：登记函数自身零网络零 DB（可安全持锁）。"""
    import inspect
    src = inspect.getsource(rt._record_self_heal)
    assert "with _SELF_HEAL_LOCK:" in src
    assert "request" not in src and "sqlite" not in src and "dao" not in src
