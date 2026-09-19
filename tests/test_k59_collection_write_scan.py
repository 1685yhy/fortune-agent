# -*- coding: utf-8 -*-
"""k59 r2 静态守卫（防复发）：**不得经由读属性 `collection` 写入向量库**。

背景：`Retriever.collection` 是读语义属性（空集合自愈在此生效，句柄可能是权威库
`fortune_books_v2`）。k55 险情的根因就是写路径继承了它 —— 「生产 persist_dir +
一个不存在的集合名」写入 → 目标被静默改写成权威库（副本实测 27,115 → 27,116，
无任何报错）。k59 已把**写 API**（`add_chunks` / `writable_collection`）与自愈
解耦，并把 `collection` 换成只读包装；本文件在**仓库面**兜底：扫描源码里形如

    .collection.<写方法>

的写法，命中即失败。两层口径同源（`COLLECTION_WRITE_METHODS`）。

── 扫描面（只许加不许减） ───────────────────────────────────────────────
- `src/**/*.py`      （服务/引擎全部代码）
- `scripts/**/*.py`  （离线入库/重建脚本 —— k59 前 3 个真实命中都在这里）
排除：`tests/**`（本文件的机制自检另有用例）、`.git`、`__pycache__`、
      `node_modules`、`miniprogram/**`（不写向量库）。
注释行（`#` 起）不扫：注释不是可执行写用法；docstring/字符串**照扫**（宁可多报）。

── 红线（本文件任何修改都不得违反） ──────────────────────────────────────
1. **不得为了让扫描通过而改宽扫描范围**（排除 scripts、删写方法名、跳过含
   `collection` 的文件等），也不得把写调用点改成同样危险的等价写法；
2. 白名单只许用于**确需保留**处，每条必须写明理由，且**必须真的在压制命中**
   （无过期条目 → 见 `test_whitelist_entries_have_reasons_and_are_used`）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ── 扫描面（只许加不许减） ──
SCAN_GLOBS = ("src/**/*.py", "scripts/**/*.py")
REQUIRED_GLOBS = {"src/**/*.py", "scripts/**/*.py"}
EXCLUDE_PARTS = {".git", "__pycache__", "node_modules", "tests", "miniprogram"}
# 扫描面下限（防止有人「顺手」把 glob 改窄到扫不到东西）
MIN_SCANNED_FILES = 200

# ── 命中规则：读属性上的写方法 ──
WRITE_CALL_RE = re.compile(
    r"\.collection\s*\.\s*(?P<method>upsert|add|update|delete|modify)\b"
)

# ── 白名单：确需保留处 + 理由（键 = 文件 + 行内含标记） ──
WHITELIST = [
    {
        "file": "scripts/k59/replay_incident.py",
        "line_contains": "retriever.collection.upsert(",
        "reason": "k59 改前路径取证工具：`--code legacy` 模式**必须逐字复刻**改前 "
                  "add_chunks 的写法（self.collection.upsert）才能复现 k55 险情，"
                  "且只允许在 /dev/shm 副本上跑（脚本内置红线：--replica 指向生产"
                  "目录即拒绝执行）。在改后代码上它已被只读包装拦下"
                  "（ReadPropertyWriteRefused，见 k59 测试的 r2 用例）。",
    },
    {
        "file": "scripts/k59/replay_incident.py",
        "line_contains": "`self.embedder.encode(...)` + `self.collection.upsert(...)`",
        "reason": "同上：模块 docstring 里对改前写法的**引述**（说明用，非可执行写"
                  "用法）。删掉这段引述会让「legacy 模式复刻的是什么」失去说明，"
                  "故登记保留。",
    },
]


def _strip_comments(source: str) -> str:
    """去掉 `#` 注释（保留行结构）——注释不是可执行写用法。"""
    out = []
    for line in source.split("\n"):
        idx = line.find("#")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def _scanned_files() -> list[Path]:
    files = []
    for pattern in SCAN_GLOBS:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_PARTS for part in path.relative_to(ROOT).parts):
                continue
            files.append(path)
    return sorted(set(files))


class _Hit:
    def __init__(self, path: str, lineno: int, line: str, method: str):
        self.path = path
        self.lineno = lineno
        self.line = line
        self.method = method

    def __repr__(self) -> str:  # pragma: no cover - 调试可读性
        return f"{self.path}:{self.lineno} {self.method}"


def _scan_source(rel_path: str, source: str) -> list[_Hit]:
    """扫单份源码，返回命中（机制自检也用它，保证「合成命中必须判红」）。"""
    hits = []
    for i, line in enumerate(_strip_comments(source).split("\n"), start=1):
        m = WRITE_CALL_RE.search(line)
        if m:
            hits.append(_Hit(rel_path, i, line, m.group("method")))
    return hits


def _collect() -> tuple[list[_Hit], list[Path]]:
    hits: list[_Hit] = []
    files = _scanned_files()
    for path in files:
        rel = str(path.relative_to(ROOT))
        hits.extend(_scan_source(rel, path.read_text(encoding="utf-8")))
    return hits, files


def _whitelisted(hit: _Hit) -> bool:
    return any(
        e["file"] == hit.path and e["line_contains"] in hit.line for e in WHITELIST
    )


# ================================================================
# 扫描面与机制自检
# ================================================================

def test_scan_surface_never_shrinks():
    """扫描面只许加不许减（防「改窄 glob 让测试变绿」）。"""
    assert REQUIRED_GLOBS <= set(SCAN_GLOBS), f"扫描面被删：{REQUIRED_GLOBS - set(SCAN_GLOBS)}"
    assert "tests" in EXCLUDE_PARTS and "scripts" not in EXCLUDE_PARTS
    _, files = _collect()
    assert len(files) >= MIN_SCANNED_FILES, (
        f"扫描面疑似被改窄：只扫到 {len(files)} 个文件（下限 {MIN_SCANNED_FILES}）"
    )
    rel = {str(p.relative_to(ROOT)) for p in files}
    assert "src/rag/retriever.py" in rel, "扫描面必须覆盖 Retriever 本体"


def test_scanner_is_not_vacuous():
    """机制自检：合成一条写用法必须判红（否则本文件只是「永远绿」的摆设）。"""
    synthetic = (
        "def f(retriever, vecs):\n"
        "    retriever.collection.upsert(embeddings=vecs, ids=['x'])\n"
        "    retriever.collection . delete(ids=['x'])\n"
        "    # retriever.collection.add(...)   ← 注释不算\n"
    )
    hits = _scan_source("synthetic.py", synthetic)
    assert [h.method for h in hits] == ["upsert", "delete"], hits
    # 读方法不得误报
    assert _scan_source("synthetic.py", "c = retriever.collection.get()\n") == []
    assert _scan_source("synthetic.py", "n = retriever.collection.count()\n") == []


def test_write_method_names_match_runtime_guard():
    """静态扫描的写方法集必须与运行时只读包装**同源**（单一事实源）。"""
    from src.rag.retriever import COLLECTION_WRITE_METHODS

    scanned = set(WRITE_CALL_RE.pattern.split("(?P<method>")[1].split(")")[0].split("|"))
    assert scanned == set(COLLECTION_WRITE_METHODS), (
        f"静态/运行时口径不一致：只扫 {scanned}，运行时拦 {set(COLLECTION_WRITE_METHODS)}"
    )


# ================================================================
# 主扫描
# ================================================================

def test_no_write_through_read_property():
    """全仓（src/**、scripts/**）不得经读属性 `collection` 写入向量库。"""
    hits, files = _collect()
    offenders = [h for h in hits if not _whitelisted(h)]
    assert not offenders, (
        "禁止经读属性 collection 写入（写请用 add_chunks / writable_collection）：\n"
        + "\n".join(f"  {h.path}:{h.lineno} → .collection.{h.method}(...)" for h in offenders)
    )
    assert files, "扫描面为空（配置错误）"


def test_whitelist_entries_have_reasons_and_are_used():
    """白名单卫生：每条必须有理由、文件必须存在、且确实在压制命中（无过期条目）。"""
    for entry in WHITELIST:
        assert entry.get("reason", "").strip(), f"白名单条目缺理由：{entry}"
        assert len(entry["reason"]) >= 20, f"白名单理由过于简略：{entry}"
        assert (ROOT / entry["file"]).is_file(), f"白名单指向不存在的文件：{entry['file']}"
    hits, _ = _collect()
    used = {
        id(e)
        for h in hits
        for e in WHITELIST
        if e["file"] == h.path and e["line_contains"] in h.line
    }
    stale = [e["line_contains"] for e in WHITELIST if id(e) not in used]
    assert not stale, f"白名单条目已失效（对应命中已消除 → 请删除该条目）：{stale}"


def test_whitelisted_file_is_the_evidence_tool_only():
    """反向锁：白名单**只**覆盖改前路径取证工具，不得扩散到 src/ 或业务脚本。"""
    whitelisted_files = {e["file"] for e in WHITELIST}
    assert whitelisted_files == {"scripts/k59/replay_incident.py"}, (
        f"白名单扩散到了不该有的文件：{whitelisted_files}"
    )
    offenders = [f for f in whitelisted_files if f.startswith("src/")]
    assert not offenders, f"src/ 不得有任何经读属性写入的白名单：{offenders}"


@pytest.mark.parametrize("name", ["upsert", "add", "update", "delete", "modify"])
def test_each_write_method_name_is_scanned(name):
    """参数化兜底：写方法名逐个在扫描规则内（失败时一眼看出被删的是哪个）。"""
    assert _scan_source("synthetic.py", f"r.collection.{name}(ids=['x'])\n"), name
