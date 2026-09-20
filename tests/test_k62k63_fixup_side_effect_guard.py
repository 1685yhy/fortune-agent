# -*- coding: utf-8 -*-
"""k62+k63 集成修复守卫（防复发）：**测试副作用不得再写进受版本控制的仓库文件**。

事故（2026-09-20 复核认定，非推测）
──────────────────────────────────────────────────────────────────────
`data/memory/.json` 被 `2a67833`（k62 r1）**带进了提交**（`git show --stat 2a67833`
可见 `data/memory/.json | 2 +-`，`_updated_at` 由 `2026-07-22T18:48:21` 漂到
`2026-09-20T11:14:12`）。k62 报告 §7.7 写的「两轮都 git restore 还原，**未纳入
任何提交**」与事实不符 —— 还原漏了一次。

机制（本文件复现的那条，**已实测定位到具体用例**）
──────────────────────────────────────────────────────────────────────
1. `tests/test_bot.py::test_handle_voice_with_text_routes_through_process` 走
   **真实 `process()` 主链**；`_handle_voice` 以 `process(text, "", ...)` 调用
   （**空 uid**，见该用例内的 tracking 注释）。
2. `src/bot/handler.py` 的 `process()` 内 `self.memory_system.add_mood_record(
   user_id, analysis.emotion_label)` → 空 uid 落到
   `UserMemory._path("")` = `<memory_dir>/.json`。
3. `MessageHandler.__init__` 的 `self.memory_system = UserMemory()` 取默认目录，
   即**仓库内** `data/memory/` —— 因为 `tests/test_bot.py` 当时**没有**重定向
   `USER_MEMORY_DIR`（k61 引入的重定向只覆盖 `scripts/eval_agent/*` 与
   `tests/test_eval_l[1-4]/e6`；`tests/` 下也没有全局 conftest 兜底）。
   ⇒ **跑一次本文件就把测试副作用写进受版本控制的仓库文件**。

修复（本批）
──────────────────────────────────────────────────────────────────────
- 已把 `data/memory/.json` 还原为合并基点 `ea110c3` 的版本（k63/k64 的提交**未**
  带它，`git log ea110c3..90230d9 -- data/memory/` 只有 `2a67833` 一条）；
- 已在 `tests/test_bot.py` 加**模块级 autouse fixture**，把 `USER_MEMORY_DIR`
  重定向到临时目录（沿用 k61 既有机制，只换目录、**不改任何断言与行为**）。

本文件锁两件事
──────────────────────────────────────────────────────────────────────
- **A（仓库面）**：工作区 `data/memory/.json` 必须与 HEAD blob 逐字节相同 ——
  即「跑完定向子集后该文件不得变化」。任何会话把它跑脏（或再次误提交）都会打红。
- **B（沙箱复现，与运行顺序无关）**：把**当前工作区**的 `src/ tests/ config/
  data/memory/` 复制进临时沙箱，在沙箱里跑那条真实主链用例（**显式清掉
  `USER_MEMORY_DIR`**，避免环境变量把缺陷掩盖掉），断言沙箱的 `data/memory/`
  **逐文件逐字节不变**。改前（`tests/test_bot.py` 未隔离）此例必红，改后绿。

边界（如实登记）
──────────────────────────────────────────────────────────────────────
- 本守卫覆盖的是**这一条已定位的真实副作用面**（`add_mood_record` 空 uid →
  `data/memory/.json`），不是「全仓所有测试副作用」的通用证明；
- B 跑的是**一条**用例（最小的可复现面），不是整份 `tests/test_bot.py` 或全量；
- 若将来有批次**故意**改动/取消跟踪 `data/memory/.json`，A 需要同步更新
  （改动必须显式写进提交说明，不能悄悄绕过）。

运行：`TMPDIR=/dev/shm nice -n 10 ionice -c2 -n7 \
  /home/a/fortune-agent/.venv/bin/python -m pytest \
  tests/test_k62k63_fixup_side_effect_guard.py -q`
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR_REL = Path("data") / "memory"
MEMORY_FILE_REL = MEMORY_DIR_REL / ".json"

# 已实测定位的那条真实主链用例（空 uid → data/memory/.json）
REPRO_NODEID = "tests/test_bot.py::test_handle_voice_with_text_routes_through_process"

# 沙箱需要的最小工作区切片（够跑上述用例；全仓 209M 里 200M 是 data/ 语料）
SANDBOX_SLICE = ("src", "tests", "config", str(MEMORY_DIR_REL))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dir_digest(base: Path):
    """目录快照：{相对路径: sha256}（含新增/删除文件，故能抓"凭空写出来"）。"""
    if not base.is_dir():
        return {}
    out = {}
    for p in sorted(base.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(base))] = _sha(p)
    return out


# ============================================================
# A：仓库面 —— 跑完定向子集后该文件不得变化
# ============================================================

def test_repo_memory_json_matches_head_blob():
    """工作区 `data/memory/.json` 必须与 HEAD blob 一致（跑测试不得脏化它）。

    打红时的正确处置：`git checkout <rev> -- data/memory/.json` 还原，**并**去
    定位是谁写脏的（大概率是某个走真实 `process()` 空 uid 的用例没重定向
    `USER_MEMORY_DIR`），按 `tests/test_bot.py` 的 fixture 同款修隔离 ——
    不要只还原了事（k62 就是这么漏的）。
    """
    blob = subprocess.run(["git", "show", f"HEAD:{MEMORY_FILE_REL.as_posix()}"],
                          cwd=ROOT, capture_output=True)
    assert blob.returncode == 0, blob.stderr.decode("utf-8", "replace")
    head_sha = hashlib.sha256(blob.stdout).hexdigest()
    work = ROOT / MEMORY_FILE_REL
    assert work.exists(), f"受跟踪的 {MEMORY_FILE_REL} 不应消失（若改跟踪方式需同步本守卫）"
    assert _sha(work) == head_sha, (
        f"{MEMORY_FILE_REL} 与 HEAD blob 不一致 —— 测试副作用被写进了受版本控制的"
        "仓库文件（k62 误提交同款）。还原：git checkout HEAD -- "
        f"{MEMORY_FILE_REL.as_posix()}；并修写入方的隔离。")


# ============================================================
# B：沙箱复现 —— 与运行顺序无关，且不碰真实仓库
# ============================================================

_REDIRECT_LINE = 'monkeypatch.setenv("USER_MEMORY_DIR", str(_user_memory_dir))'


def _make_sandbox(dst: Path) -> Path:
    """把**当前工作区**的最小切片复制进沙箱（含未提交改动，故反映"现在这份代码"）。"""
    dst.mkdir(parents=True, exist_ok=True)
    for rel in SANDBOX_SLICE:
        src = ROOT / rel
        assert src.exists(), rel
        shutil.copytree(src, dst / rel, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return dst


def _run_repro(sandbox: Path, tmp_path: Path):
    """在沙箱里跑那条真实主链用例。

    子进程**显式剔除** `USER_MEMORY_DIR`：若靠环境变量才不脏，那测的是环境不是
    测试文件自身的隔离 —— 缺陷会被掩盖。
    """
    env = {k: v for k, v in os.environ.items() if k != "USER_MEMORY_DIR"}
    env["TMPDIR"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, "-m", "pytest", REPRO_NODEID, "-q",
         "-p", "no:cacheprovider", "--no-header"],
        cwd=sandbox, env=env, capture_output=True, text=True, timeout=300)


def test_targeted_subset_does_not_dirty_memory_in_sandbox(tmp_path):
    """跑那条真实主链用例（空 uid）→ `data/memory/` 必须逐文件逐字节不变。

    零副作用：所有读写都在 `tmp_path` 内，真实仓库一个字节都不动。
    """
    sandbox = _make_sandbox(tmp_path / "sb")
    before = _dir_digest(sandbox / MEMORY_DIR_REL)
    repo_before = _dir_digest(ROOT / MEMORY_DIR_REL)

    proc = _run_repro(sandbox, tmp_path)

    after = _dir_digest(sandbox / MEMORY_DIR_REL)
    changed = sorted(set(before) ^ set(after)) + \
        sorted(k for k in set(before) & set(after) if before[k] != after[k])

    assert proc.returncode == 0, (
        f"沙箱里 {REPRO_NODEID} 未通过（守卫无法判定副作用面）：\n"
        f"stdout={proc.stdout[-800:]}\nstderr={proc.stderr[-800:]}")
    assert not changed, (
        "沙箱内 data/memory/ 被测试写脏 —— 说明有走真实 process() 空 uid 的用例"
        "没重定向 USER_MEMORY_DIR（k62 误提交同款机制）。\n"
        f"变化文件：{changed}")
    assert _dir_digest(ROOT / MEMORY_DIR_REL) == repo_before, \
        "沙箱用例跑动了真实仓库的 data/memory/（守卫自身副作用，属实现 bug）"


def test_guard_has_teeth_pre_fix_copy_dirties_memory(tmp_path):
    """**改前失败**的常驻证明：把沙箱副本里的重定向**去掉**（模拟改前），同一用例必须打红。

    这条用例的存在意义：让「本守卫真的能抓到 k62 那次事故」不依赖任何人的口头
    承诺 —— 每次跑守卫都会现场复现一次"未隔离 → `data/memory/.json` 被写脏"。
    若哪天它变绿（去掉重定向也不再脏），说明机制变了，守卫的判定前提需要重审。
    """
    sandbox = _make_sandbox(tmp_path / "sb_prefix")
    victim = sandbox / "tests" / "test_bot.py"
    text = victim.read_text(encoding="utf-8")
    assert _REDIRECT_LINE in text, "test_bot.py 的隔离 fixture 换写法了 —— 本守卫需同步"
    victim.write_text(text.replace(_REDIRECT_LINE, "pass  # 模拟改前：未重定向"),
                      encoding="utf-8")

    before = _dir_digest(sandbox / MEMORY_DIR_REL)
    proc = _run_repro(sandbox, tmp_path)
    after = _dir_digest(sandbox / MEMORY_DIR_REL)

    assert proc.returncode == 0, proc.stdout[-800:]      # 用例本身仍应通过
    assert set(before) == set(after), "改前副本不应新增/删除文件"
    changed = sorted(k for k in before if before[k] != after[k])
    assert changed == [MEMORY_FILE_REL.name], (
        "去掉重定向后 data/memory/.json 未被写脏 → 本守卫失去判定力（机制变了），"
        f"需重审守卫前提。实际变化：{changed}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
