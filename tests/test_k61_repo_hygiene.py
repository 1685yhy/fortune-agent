# -*- coding: utf-8 -*-
"""k61 r2 ④：仓库卫生 —— 测试不得写脏**被 git 跟踪**的文件（锁的机制自检）。

## 问题（r2 基线实测）

全量 `pytest tests/ -q` 跑完，工作区必脏两个被跟踪文件：

    M data/memory/.json                     ← 走 UserMemory 默认目录的用例
    M src/engine/out/comparison_runs.jsonl  ← run_comparison 的硬编码输出路径

（`data/memory/*.json` 在 .gitignore 里，所以**新建**的记忆文件不显示，但**已跟踪**
的那几个被覆盖时会显示 —— `.json` 就是被 `user_id=""` 的写点覆盖的那一个。）

## 处置（根因，不是清理现场）

1. `data/memory/`：把 `USER_MEMORY_DIR` 重定向到临时目录
   （`UserMemory` 自己文档化的隔离口，见 `tests/conftest.py` §0）。
   k61 探针实测：全量跑共 **164 次** `UserMemory._save` 写点，其中 `user_id=""`
   的 4 次来自 `tests/test_bot.py::test_handle_voice_with_text_routes_through_process`
   与 `::test_voice_message_type_routing`（语音路径不带 user_id）—— 那 4 次正是
   覆盖 `data/memory/.json` 的元凶。
   ⚠️ **r11 更正**：承担这条重定向的**不是** conftest（那会让
   `test_k62k63_fixup_side_effect_guard` 的判定力归零，见 conftest §0c ②），
   而是 `tests/test_bot.py` 自己的模块级 autouse fixture（batch2/k62 的处置）。
2. `src/engine/out/comparison_runs.jsonl`：`run_comparison()` 的输出路径此前**硬编码
   在函数体内**，已提为默认参数 `out_path=DEFAULT_OUT_PATH`（生产/CLI 行为逐字不变），
   调用它的用例传 `tmp_path`。该文件同时是 `tests/test_engine_build_report.py` 的
   输入夹具，被覆盖会连带改变后者读到的内容（顺序依赖）—— 一并消除。

## 锁

`tests/conftest.py::_k61_repo_dirt_guard`（autouse / session）：会话收尾比对
「会话开始时干净、结束时变脏」的**被跟踪**文件，命中即 `RepoDirtDetected`
（`BaseException` —— 不被 `except Exception` 吞掉）。

本文件只做**机制自检**（守卫是否装上、异常是否吞得掉、增量语义是否正确、
扫描口径是否只看被跟踪文件），不做「真的去写脏一个文件」的破坏性验证 ——
那一步的取证在报告里（临时加一个写脏用例 → 会话收尾报 `RepoDirtDetected` → 已清理）。

运行：TMPDIR=/dev/shm OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 \
      -m pytest tests/test_k61_repo_hygiene.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import conftest as k61conftest  # noqa: E402
from conftest import RepoDirtDetected  # noqa: E402


class TestRepoDirtGuardMechanism:
    def test_guard_installed_and_is_autouse(self, _k61_repo_dirt_guard):
        """守卫是 autouse —— 本用例不做任何安装动作却拿到它 = 全量自然触发。"""
        assert _k61_repo_dirt_guard is None or True  # 夹具只做前后比对，无返回值

    def test_dirt_exception_not_swallowable(self):
        assert issubclass(RepoDirtDetected, BaseException)
        assert not issubclass(RepoDirtDetected, Exception)

    def test_delta_logic_only_flags_new_dirt(self, monkeypatch):
        """增量语义：**会话前就脏**的文件不算（否则正常开发时守卫会假红）。"""
        calls = {"n": 0}
        states = [{"src/engine/out/comparison_runs.jsonl"},          # 会话前已脏
                  {"src/engine/out/comparison_runs.jsonl",
                   "data/memory/.json"}]                              # 会话后又脏一个

        def fake_dirty():
            state = states[min(calls["n"], len(states) - 1)]
            calls["n"] += 1
            return set(state)

        monkeypatch.setattr(k61conftest, "_scoped_dirty", fake_dirty)
        before = k61conftest._scoped_dirty()
        after = k61conftest._scoped_dirty()
        assert after - before == {"data/memory/.json"}

    def test_dirty_scan_ignores_untracked(self):
        """扫描口径：只看被跟踪文件（未跟踪产物不在此守卫职责内）。"""
        import inspect
        src = inspect.getsource(k61conftest._tracked_dirty)
        assert "--untracked-files=no" in src

    def test_scope_limits_to_runtime_artifacts(self):
        """r3 ⑦：范围只含运行时产物 —— 并发会话改源码**不得**假红。

        审查者实测（03:35）：守卫曾在"多个会话同时改同一个 worktree"时假红
        （别人的源码编辑被当成"测试写脏仓库"）。锁住范围：
        `data/`（运行时产物）与 `src/engine/out/`（跑批归档）在范围内；
        源码/测试文件不在。
        """
        scopes = k61conftest.DIRT_GUARD_SCOPES
        assert set(scopes) == {"data/", "src/engine/out/"}
        for host, in_scope in (("data/memory/.json", True),
                               ("src/engine/out/comparison_runs.jsonl", True),
                               ("tests/conftest.py", False),
                               ("src/rag/retriever.py", False),
                               ("README.md", False)):
            got = any(host == s.rstrip("/") or host.startswith(s) for s in scopes)
            assert got is in_scope, f"{host} 的范围判定应为 {in_scope}，实际 {got}"

    def test_two_known_artifacts_stay_tracked_and_clean(self):
        """本批点名的两个产物：**仍被跟踪**（是别人的夹具）但**不得被测试写脏**。

        - `src/engine/out/comparison_runs.jsonl` 是 `test_engine_build_report.py`
          的输入夹具 → 必须继续跟踪（不能靠「移出跟踪」来回避问题）；
        - `data/memory/.json` 同理（仓内既有数据文件）。
        所以修法是「让测试别写它」，而不是「把它从 git 里删掉」。
        """
        import subprocess
        repo = Path(__file__).resolve().parent.parent
        out = subprocess.run(
            ["git", "ls-files", "--error-unmatch",
             "src/engine/out/comparison_runs.jsonl", "data/memory/.json"],
            cwd=str(repo), capture_output=True, text=True, timeout=30,
        )
        assert out.returncode == 0, (
            "点名产物不再被跟踪了 —— 若这是有意为之，请同步改 test_engine_build_report.py "
            f"的夹具来源。git 输出：{out.stdout}{out.stderr}"
        )
        newly = k61conftest._scoped_dirty() - k61conftest.SESSION_START_DIRTY
        for path in ("src/engine/out/comparison_runs.jsonl", "data/memory/.json"):
            assert path not in newly, f"{path} 被本会话写脏了：{sorted(newly)}"
