#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k84 必修6-C：**全仓零引用资产/代码的可复跑判据**（替代"人眼 grep 一遍就宣布干净"）。

## 为什么有这个脚本（根因）
"死资产清理"在本仓已发生至少两次，且**每次都是人肉 grep**：
  · k12（`80e2150`）整体删除 `miniprogram/icons/`，依据是"wxml/wxss/js/json/md 全扩展名
    grep 均为空"；
  · k14（`d912cc3`）删除 `ic-*-dark` 21 png + `lantern-dark.jpg` + `ic-book-on.png`，
    依据是"全扩展名 grep 零代码引用"。

人肉 grep 的问题不是"没做"，而是**做完了不留判据**：
  1. 下一次谁都不知道该 grep 哪些扩展名、哪些目录、哪种路径写法才算穷尽；
  2. 更糟的是**假阴性**——历史批次 plan 文档会把**已删**资产的**文件名逐个列出来**
     （见 `docs/superpowers/plans/2026-09-09-k14-tab-icons.md:80` 的 23 枚清单）。
     只要把这种"叙述性列举"也算作引用，死资产就会被判活，且没人会发现。
     本脚本把这个坑做成了**一等公民**（`narrative` 池，见下"四个引用池"）。
  3. 人肉 grep 无法覆盖"模块从未被 import"这一类（要解析 import 图，不是搜字符串）。

## 判据

### 三类被审对象
| kind    | 面                                                | 判"被引用"的判据 |
|---------|---------------------------------------------------|------------------|
| `asset` | `miniprogram/assets/**`、`src/static/**`、`src/images/**`、`docs/**` 里的图片 | 其**文件名**作为路径字面量出现在引用池文件里的文本中（覆盖 `/assets/images/x.png`、`../../assets/images/x.png`、`assets/images/x.png` 三种写法——按 basename 归一） |
| `py`    | `src/**/*.py`（不含 `__init__.py`）                 | 有任一引用池文件**import**到它（AST 解析，非 grep），或它带 `if __name__ == "__main__"`（自入口） |
| `js`    | `miniprogram/**/*.js`（不含 `tests/`）              | 有任一引用池文件 `require('<字面量>')` 解析到它，或它是 `app.json` 的 `pages` 项 / `app.js` / 被某个 `.json` 的 `usingComponents` 指向（组件） |

### 四个引用池（**这条是判据的核心**，不是"排除"）
| 池          | 面 | 算不算"活着" |
|-------------|----|--------------|
| `prod`      | 会随产品运行/发布的代码与配置：`miniprogram/**`(wxml/wxss/js/json) minus tests、`src/**`、`scripts/**`、`config/**`、`cow-config/**`、`deploy/**`、根级 `*.md`/`*.sh`/`*.toml` | **算**（这就是消费） |
| `docs`      | `docs/**`（非叙述性的产品文档：`DESIGN_SYSTEM` / `DATABASE` / `API` / `MINI_PROGRAM_SECURITY` …）+ `miniprogram/privacy.md` / `审核材料.md` | **不分好坏，单独成类**：只被文档提到 ⇒ 判 `docs-only`（登记，不自动删——文档可能是该资产"仍需存在"的唯一依据） |
| `narrative` | `docs/superpowers/plans/**`、`docs/superpowers/task-*-report.md`（历史批次计划与报告） | **不算**。这些文件按体裁就会逐一列出**已删**文件名 ⇒ 算它=把死资产判活。只被它提到 ⇒ 判 `prose-only`（登记 + 报主控） |
| `test`      | `tests/**`、`miniprogram/tests/**`、`**/*.test.js`、`scripts/test_*.py` | **不算消费，但也不是死**：测试钉既有行为。只被它引用 ⇒ 判 `test-only`（登记，不删） |

### 排除面（显式声明 + 为什么）
| 排除 | 为什么 |
|------|--------|
| `.git/`            | VCS 内部对象与 reflog。历史 blob 里出现的文件名**不是**当前引用——把它算进来等于"历史上活过就永远活着"。 |
| `node_modules/`    | 第三方依赖代码。其字面量不会引用**本仓**资产；且它自身有同名文件（如 `icons/`）会污染 basename 匹配。 |
| `__pycache__/`     | `.pyc` 是构建产物（`.gitignore` 已排除），字节码里的常量不构成源码引用。 |
| `.venv/` `venv/`   | 解释器环境，同上。 |
| `.claude/` `/.agents/` | 会话工具配置与 **vendored 第三方 skill 包**（`.agents/skills/design-taste-frontend/SKILL.md` 来自外部）。它们不消费本仓产品资产，但其文本含 `icons/`、`dark` 等通用词，留着只会制造噪声命中。 |
| `data/`            | 语料与运行期数据（`data/**/*.jsonl`、`data/wenzhen_charts.db`、`data/reports/`）。它是**数据**不是消费代码；`data/reports/*.json` 还含真实姓名与八字（k82 必修4 已从 git 移除）。 |
| `.superpowers/`    | 工作记录/证据目录（`.gitignore` 也排除）。本报告写在这里，若不排除会**自我引用**。 |
| `scripts/audit_dead_refs.py` **自身** | 自引用陷阱：脚本内含白名单，白名单键就是被审对象的路径 ⇒ 不排除则"凡是登记过的都永远被判活"，判据自我失效。代码里对此有**显式断言**（`self_excluded` + `--check` 校验）。 |
| `tests/test_k84_dead_refs.py`（判据**自己的回归夹具**） | 同类自引用陷阱的另一形态：该测试为验证"动态拼接必须被降级上报"塞了一个合成样本 `'/assets/images/' + n + '.png'`，进扫描面就会把**真仓库**的 `dynamic_sites` 从 5 处污染成 6 处（第 6 处就是夹具本身）。实测踩到后按同规则排除。 |

### 图片资产的额外判据边界（**图标类不得凭本脚本删**）
- 本脚本只能证明"**仓内文本**没有引用它"。
- **不能**证明小程序**发布配置/微信后台**没有引用（如自定义 tabbar、`packOptions`、
  微信后台素材库）——本仓 `project.config.json` 的 `packOptions.ignore` 与
  `miniprogram/screenshots/` 已被本脚本显式核对（见 `REGISTERED`），但**仓外配置不可见**。
- 故：`asset` 判 `dead` 时**不自动删**，只在报告里列为"待拍板/待确认"。

## 如实边界（本判据**做不到**的，明写出来，别当成"已覆盖"）
  1. **动态拼接路径**：`'/assets/images/' + name + '.png'` 不会被任何字面量匹配到 ⇒
     会被误判为 `dead`。脚本对此有**主动探测**（`dynamic_sites`：出现 `assets/images`
     字样但**同一字符串里没有完整文件名+扩展名**的位置），并把命中的 `dead` 降级为
     `dead-dynamic-risk`（只报不删）。**探测本身是启发式的**：它抓"目录字面量单独出现"，
     抓不到 `['assets','images',n].join('/')` 这类写法。
  2. **`require(变量)` / `importlib.import_module(变量)`**：看不见。本仓实测动态 require
     集中在 `miniprogram/tests/*.test.js`（`require(pagePath)` 等，见 `dynamic_sites`），
     它们属于 `test` 池，不影响 `prod` 结论。
  3. **basename 归一带来的跨目录误活**：两个不同目录下的同名资产（如
     `a/ic-x.png` 与 `b/ic-x.png`）只要有一处引用，两条都判活。脚本对此**主动断言**：
     若被审资产 basename 不唯一 ⇒ 打印 `CRITERION-BROKEN`（判据失效，不是结论）。
  4. **`__init__.py` 的 re-export**：`from .pkg import *` 或 `__init__` 里 `import` 只是
     为了让外部 `from pkg import X` 可用。这类"可达但未必有人用"的模块按**可达**算活
     （判据无法区分"可达"与"在用"）。
  5. **测试专用模块**：只在 `tests/` 里被 import 的模块判 `test-only`，**不判死**。它是
     否该删是产品/测试策略问题，不是引用图问题。
  6. **粒度是"文件"不是"符号"**：模块里**部分**函数零引用不会被发现（本脚本不解析
     属性访问）。这需要另一套判据（本仓 k62 r2 曾对单函数做过，见
     `src/llm/report_prompts.py` 的删除依据注释）。
  7. **`docs/**` 图片零命中 ≠ 文档无图**：本仓 `docs/` 当前确无图片文件（实测 0），
     扫描面保留它是为了**将来**新增文档图时仍被覆盖，不是"已经查过一遍"。

## 用法
    python3 scripts/audit_dead_refs.py            # 人读清单
    python3 scripts/audit_dead_refs.py --json     # 机器可读
    python3 scripts/audit_dead_refs.py --check    # 门禁：与 REGISTERED 逐条比对，
                                                  # 有"未登记的死引用"或"登记项已消失"即 exit 1

`--check` 的语义：**每一个非 `alive` 的判定都必须在 `REGISTERED` 里显式表态**
（键 = `<kind>::<rel path>`，值 = 理由码 → 见 `REASONS`）。
**新冒出一个零引用对象而不表态 ⇒ 红** —— 这就是"下次清理不靠人肉 grep"的机制性修法。
反向也查（登记项已消失 ⇒ 红），防止白名单**腐烂**成"什么都放行"。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import warnings
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
SELF_REL = "scripts/audit_dead_refs.py"
#: 判据**自身的回归夹具**也要排除：`tests/test_k84_dead_refs.py` 里为了验证"动态拼接
#: 必须被降级上报"，塞了一个 `'/assets/images/' + n + '.png'` 的合成样本；若它进扫描面，
#: 真仓库的 `dynamic_sites` 会被**自己的夹具**污染（实测：从 5 处变成 6 处，第 6 处就是
#: 本文件的第 192 行）——与"脚本不排除自己"是同一类自引用陷阱，故同规则处理。
SELF_TEST_REL = "tests/test_k84_dead_refs.py"

# ── 被审对象的面 ────────────────────────────────────────────────────────────
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
ASSET_ROOTS = ("miniprogram/assets", "src/static", "src/images", "docs")
#: QA 证据图目录（**单独一类**，永不判死：它是截图产物，不是产品资产；
#: `project.config.json` 的 `packOptions.ignore` 已把整个 `screenshots` 文件夹排除出包）。
ASSET_QA_ROOTS = ("miniprogram/screenshots",)
PY_ROOTS = ("src",)
JS_ROOTS = ("miniprogram",)

#: 可含"路径字面量"的文本扩展名（引用池的扫描面）。
TEXT_EXTS = {
    ".py", ".js", ".json", ".wxml", ".wxss", ".md", ".html", ".css",
    ".sh", ".yaml", ".yml", ".toml", ".txt", ".conf", ".patch",
}

# ── 排除面（显式；每条理由见模块 docstring 的"排除面"表）────────────────────
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".claude", ".agents", "data", ".superpowers",
}
EXCLUDE_FILES = {SELF_REL, SELF_TEST_REL}


def pool_of(rel: str) -> str:
    """把仓库相对路径分到四个引用池之一（判据核心，见 docstring）。"""
    if rel in EXCLUDE_FILES:
        return "self"
    if (rel.startswith("tests/") or rel.startswith("miniprogram/tests/")
            or rel.endswith(".test.js") or rel.startswith("scripts/test_")):
        return "test"
    # `docs/superpowers/**` 整体是**批次工作记录**（plans / specs / qa / eval / task-*-report）：
    # 体裁上就会逐一列出"设计过 / 已删"的文件名 ⇒ 算它=把死资产判活。全部归 narrative。
    if rel.startswith("docs/superpowers/"):
        return "narrative"
    if rel.startswith("docs/") or rel.startswith("miniprogram/privacy.md") \
            or rel.startswith("miniprogram/审核材料.md"):
        return "docs"
    return "prod"


def _is_excluded(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in EXCLUDE_DIRS for p in parts[:-1]) or rel in EXCLUDE_FILES


def iter_files() -> Iterable[str]:
    """遍历仓库内所有文件，产出**仓库相对路径**（POSIX 分隔符）。"""
    for p in sorted(REPO.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO).as_posix()
        if _is_excluded(rel):
            continue
        yield rel


# ── 引用池：文本 → 出现的"路径字面量" basename 集合 ─────────────────────────
_PATH_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_./\\-]*\.(?:png|jpe?g|svg|gif|webp)\b", re.IGNORECASE)
#: 动态拼接探测：出现资产目录字面量、但该字符串里没有"文件名+扩展名"。
_ASSET_DIR_RE = re.compile(r"assets/images?(?![\w.-]*\.(?:png|jpe?g|svg|gif|webp))",
                           re.IGNORECASE)
_STR_LIT_RE = re.compile(r"""(['"`])((?:\\.|(?!\1)[^\\])*)\1""")
_DYN_REQUIRE_RE = re.compile(r"require\(\s*(?!['\"`])[^)]*\)")
_DYN_IMPORT_RE = re.compile(r"import_module\(\s*(?!['\"])[^)]*\)|__import__\(\s*(?!['\"])[^)]*\)")


class RefIndex:
    """四个池各自的引用索引 + 动态拼接位点。"""

    def __init__(self) -> None:
        self.asset_basenames: Dict[str, List[Tuple[str, int, str]]] = {
            "prod": [], "docs": [], "narrative": [], "test": []}
        self.dynamic_sites: List[dict] = []
        self.scanned = 0

    def add_asset_hit(self, pool: str, rel: str, line: int, token: str) -> None:
        self.asset_basenames[pool].append((rel, line, token))


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return None


def build_index() -> Tuple[RefIndex, Dict[str, str], Set[str]]:
    """扫一遍引用池，建索引。返回 (索引, {rel: 源码文本}, {rel: 池})。"""
    idx = RefIndex()
    sources: Dict[str, str] = {}
    pools: Dict[str, str] = {}
    for rel in iter_files():
        p = REPO / rel
        if p.suffix.lower() not in TEXT_EXTS:
            continue
        text = _read_text(p)
        if text is None:
            continue
        pools[rel] = pool_of(rel)
        sources[rel] = text
        idx.scanned += 1
        pool = pools[rel]
        if pool == "self":
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _PATH_TOKEN_RE.finditer(line):
                tok = m.group(0).replace("\\", "/")
                idx.add_asset_hit(pool, rel, lineno, tok.rsplit("/", 1)[-1])
            # 动态拼接探测（只对会消费资产的池做，避免文档里的叙述噪声）
            if pool in ("prod", "test"):
                for lm in _STR_LIT_RE.finditer(line):
                    lit = lm.group(2)
                    # 只对"提到资产目录、但**整个字面量里没有任何完整 文件名.扩展名**"
                    # 的字符串报拼接风险。含完整路径的（`/assets/images/ic-x.png`）
                    # 是**静态**引用，不是拼接——初版在此过度命中（每个 icon 都被报）。
                    if ("assets/image" in lit
                            and not _PATH_TOKEN_RE.search(lit)
                            and _ASSET_DIR_RE.search(lit)):
                        idx.dynamic_sites.append(
                            {"file": rel, "line": lineno, "kind": "asset-dir-concat",
                             "text": lit[:80]})
                        break
                if pool == "prod" and (_DYN_REQUIRE_RE.search(line)
                                       or _DYN_IMPORT_RE.search(line)):
                    idx.dynamic_sites.append(
                        {"file": rel, "line": lineno, "kind": "dynamic-module-ref",
                         "text": line.strip()[:80]})
    return idx, sources, pools


# ── Python import 图 ────────────────────────────────────────────────────────
def py_module_name(rel: str) -> str:
    """`src/a/b.py` → `src.a.b`；`src/a/__init__.py` → `src.a`。"""
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_parts(rel: str) -> List[str]:
    return rel[:-3].split("/")[:-1]


def collect_py_imports(rel: str, text: str) -> Tuple[Set[str], Set[str]]:
    """返回 (强引用 dotted 名集合, 弱引用=字符串字面量 dotted 名集合)。

    强 = `import a.b` / `from a.b import c` / `import_module("a.b")` 常量实参；
    弱 = 任意等于 dotted 路径的字符串常量（覆盖 `src/images/__init__.py` 的
         `_LAZY_MODULES` 表驱动导入，**判据更弱，如实标注**）。
    """
    strong: Set[str] = set()
    weak: Set[str] = set()
    try:
        with warnings.catch_warnings():
            # 被扫文件里可能有非法转义序列（如 `"\d"`），ast.parse 会打 SyntaxWarning。
            # 那是**被扫文件**的噪声，不是本脚本的问题，抑制掉保持输出可读。
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError:
        return strong, weak
    pkg = _package_parts(rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                strong.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            if mod:
                strong.add(mod)
            for a in node.names:
                if a.name != "*" and mod:
                    strong.add(f"{mod}.{a.name}")
                if a.name == "*" or (node.level and not node.module):
                    pass
        elif isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    strong.add(arg.value)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if re.fullmatch(r"src(\.[a-z_][a-z0-9_]*)+", v):
                weak.add(v)
    return strong, weak


def imported_targets(strong: Set[str]) -> Set[str]:
    """把 `a.b.c` 展开成它自己 + 全部前缀（Python 导入子模块必然导入父包）。"""
    out: Set[str] = set()
    for n in strong:
        parts = n.split(".")
        for i in range(1, len(parts) + 1):
            out.add(".".join(parts[:i]))
    return out


# ── JS require 图 ───────────────────────────────────────────────────────────
_REQUIRE_RE = re.compile(r"""require\(\s*(['"])([^'"]+)\1\s*\)""")


def _resolve_js(req: str, from_rel: str) -> Optional[str]:
    """把 require 的字面量解析成仓库相对路径（`.js` 省略 / 目录 → index.js）。"""
    base_dir = Path(from_rel).parent
    if req.startswith("/"):
        cand = Path(req.lstrip("/"))
    elif req.startswith("."):
        cand = base_dir / req
    else:
        # 第三方包（miniprogram-automator 等）→ 非本仓模块
        return None
    # 必须 normpath：`../../utils/x` 不归一化会得到带 `..` 的字符串，
    # 与被审对象的规范相对路径对不上 ⇒ 全部误判 dead（本脚本初版的真 bug）。
    norm = Path(os.path.normpath(cand.as_posix()))
    for suffix in ("", ".js", "/index.js"):
        c = Path(str(norm) + suffix)
        if (REPO / c).is_file() and (REPO / c).suffix == ".js":
            return c.as_posix()
    return None


def _app_json_entry_js() -> Set[str]:
    """`app.json` 的 pages → 页面 js 入口 + `app.js` 本身。"""
    out = {"miniprogram/app.js"}
    try:
        cfg = json.loads((REPO / "miniprogram/app.json").read_text(encoding="utf-8"))
    except Exception:
        return out
    for page in cfg.get("pages", []):
        out.add(f"miniprogram/{page}.js")
    return out


def _using_components_js(sources: Dict[str, str]) -> Set[str]:
    """所有 `.json` 的 `usingComponents` → 组件 js。"""
    out: Set[str] = set()
    for rel, text in sources.items():
        if not rel.endswith(".json"):
            continue
        if not rel.startswith("miniprogram/"):
            continue
        try:
            cfg = json.loads(text)
        except Exception:
            continue
        uc = cfg.get("usingComponents")
        if not isinstance(uc, dict):
            continue
        for _, target in uc.items():
            if not isinstance(target, str):
                continue
            rel_target = target.lstrip("/")
            if rel_target.startswith("components/") or rel_target.startswith("plugin://"):
                cand = Path("miniprogram") / rel_target
                if (REPO / f"{cand}.js").is_file():
                    out.add(f"{cand}.js")
    return out


#: ── 已表态清单（`--check` 的比对对象）──────────────────────────────────────
#: 键 = `<kind>::<rel path>`；值 = `REASONS` 里的理由码。
#: **每个非 `alive` 判定都必须在这里，且理由要指到"为什么它不该被删"。**
REASONS = {
    "entry-page": "小程序页面入口（app.json pages 注册），无 require 也必然被打包/执行",
    "entry-app": "小程序 app.js（运行时入口）",
    "entry-component": "自定义组件（被 .json usingComponents 引用，无 require）",
    "py-main": "模块自带 `if __name__ == \"__main__\"` —— 可直接执行的入口",
    "qa-artifact": "QA 截图证据图（packOptions.ignore 已排除出包），非产品资产，永不删",
    "test-only": "仅被 tests/ 引用——测试钉既有行为，不构成死资产",
    "docs-only": "仅被产品文档提及——文档可能是它仍需存在的唯一依据，不自动删",
    "prose-only": "仅被历史批次叙述文本（plans/reports）提及——**判活会掩盖死资产**，只报不删",
    "dynamic-risk": "零字面量引用，但仓内存在动态拼接/动态 require ⇒ 判据不可靠，只报不删",
    "deliberate-retain": "文件/模块**自述**为「已停用，保留仅供历史参考」（或已由控制方拍板保留）⇒ 零引用是**有意为之**，删它才是违背既有决定",
    "weak-lazy-table": "仅通过 `_LAZY_MODULES` 表 + `importlib` 可达（**弱判据**）：模块名以字符串形式存在，脚本无法证明无人访问包属性 ⇒ 判活。实测唯一触发者是 tests/",
    "keep-decision": "零引用属实，但删除属产品/范围决策（见报告「待拍板」节），本脚本不自行处置",
}

#: ── 已表态清单（每个"判据给不出确定答案"的判定都必须在这里）────────────────
#: 键 = `<kind>::<rel path>`。理由见 `REASONS`；每条都写清"**为什么它不该被删**"。
#: 生成方式：先跑 `--check` 看未登记项，再**逐条**判断（判断依据写进本报告
#: `.superpowers/sdd/task-k84-agentB-report.md` 的判定表，脚本只存结论+理由码）。
REGISTERED: Dict[str, str] = {
    # ── 零引用但**有意保留**（自述/已拍板）────────────────────────────────
    "py::src/engines/similarity.py": "deliberate-retain",

    # ── 零引用 + 尚未有人表态（本批**不自行删**，交控制方拍板）──────────────
    # 组件是"目录整体"消费的：.js 判死 ⇒ 同目录 .wxml/.wxss/.json 一并死
    # （`--check` 只登记 .js 键，同伴文件由扫描期从目录推导，见 scan() 的 companion）。
    "js::miniprogram/components/score-ring/score-ring.js": "keep-decision",

    # ── 仅测试引用（测试钉既有行为，不是死资产）────────────────────────────
    "py::src/engine/e2e_eval.py": "test-only",
    "py::src/engine/eval.py": "test-only",
    "py::src/engines/intent_classifier.py": "test-only",
    "py::src/rag/bm25_retriever.py": "test-only",
    "py::src/rag/query_enhancer.py": "test-only",

    # ── 弱判据可达（表驱动 importlib）→ 判活，但必须留下"这句话是弱判据"──────
    "py::src/images/bazi_chart.py": "weak-lazy-table",
    "py::src/images/fengshui_chart.py": "weak-lazy-table",
    "py::src/images/ziwei_chart.py": "weak-lazy-table",
}


def _key(kind: str, rel: str) -> str:
    return f"{kind}::{rel}"


def scan() -> Tuple[List[dict], List[dict], Dict[str, str]]:
    """执行全仓扫描。

    Returns: (hits, dynamic_sites, meta)
    """
    idx, sources, pools = build_index()

    # 资产 basename 唯一性断言（判据前提；破坏 = CRITERION-BROKEN，不是结论）
    inventory: List[Tuple[str, str, str]] = []   # (kind, rel, basename)
    for root in ASSET_ROOTS + ASSET_QA_ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                kind = "asset" if root in ASSET_ROOTS else "asset-qa"
                inventory.append((kind, p.relative_to(REPO).as_posix(), p.name))

    seen: Dict[str, List[str]] = {}
    for _, rel, name in inventory:
        seen.setdefault(name, []).append(rel)
    dup = {n: v for n, v in seen.items() if len(v) > 1}

    asset_hit_map: Dict[str, Dict[str, List[Tuple[str, int]]]] = {}
    for pool in ("prod", "docs", "narrative", "test"):
        for rel, line, tok in idx.asset_basenames[pool]:
            asset_hit_map.setdefault(tok, {}).setdefault(pool, []).append((rel, line))

    dynamic_in_prod = [d for d in idx.dynamic_sites if pools.get(d["file"]) == "prod"]

    hits: List[dict] = []

    # ── 资产 ──
    for kind, rel, name in inventory:
        got = asset_hit_map.get(name, {})
        if kind == "asset-qa":
            verdict, reason = "qa-artifact", "qa-artifact"
        elif got.get("prod"):
            verdict, reason = "alive", ""
        elif got.get("test"):
            verdict, reason = "test-only", "test-only"
        elif got.get("docs"):
            verdict, reason = "docs-only", "docs-only"
        elif got.get("narrative"):
            verdict, reason = "prose-only", "prose-only"
        elif dynamic_in_prod:
            verdict, reason = "dead-dynamic-risk", "dynamic-risk"
        else:
            verdict, reason = "dead", "keep-decision"
        hits.append({
            "kind": kind, "path": rel, "basename": name, "verdict": verdict,
            "reason": reason,
            "sites": sorted({(s[0], s[1]) for pool in got.values() for s in pool})[:8],
            "criterion_broken": bool(name in dup),
            "dup_with": dup.get(name, []) if name in dup else [],
            "companion": [],
            # 任一池存在"动态拼路径"位点 ⇒ 该目录下**任何**资产都可能是被模板拼出来的
            # （如 `miniprogram/tests/k34_icon_family.test.js:111`
            #   `/assets/images/${n}\\.png` 遍历裸名列表）。故对 dead 判定附风险标记：
            # **判 dead ≠ 可以删**，必须先排除这些位点覆盖（本脚本做不到，见边界 1）。
            "dynamic_risk": bool(idx.dynamic_sites),
        })

    # ── Python 模块 ──
    # 分池收集：prod 池的 import ⇒ 活；只有 test 池 import ⇒ test-only（测试钉行为）。
    strong_prod: Set[str] = set()
    strong_test: Set[str] = set()
    weak_prod: Set[str] = set()
    for rel, text in sources.items():
        if not rel.endswith(".py") or pools.get(rel) == "self":
            continue
        s, w = collect_py_imports(rel, text)
        if pools.get(rel) == "prod":
            strong_prod |= s
            weak_prod |= w
        elif pools.get(rel) == "test":
            strong_test |= s
    reachable_prod = imported_targets(strong_prod)
    reachable_test = imported_targets(strong_test)

    py_candidates = [rel for rel in sources
                     if rel.endswith(".py") and rel.startswith(tuple(f"{r}/" for r in PY_ROOTS))]
    for rel in sorted(py_candidates):
        if pools.get(rel) != "prod" or rel.endswith("/__init__.py"):
            continue
        mod = py_module_name(rel)
        is_main = bool(re.search(r'if\s+__name__\s*==\s*[\'"]__main__[\'"]', sources[rel]))
        if mod in reachable_prod:
            verdict, reason = "alive", ""
        elif is_main:
            verdict, reason = "py-main", "py-main"
        elif mod in weak_prod:
            verdict, reason = "alive-weak-string", ""
        elif mod in reachable_test:
            verdict, reason = "test-only", "test-only"
        else:
            verdict, reason = "dead", "keep-decision"
        hits.append({"kind": "py", "path": rel, "module": mod, "verdict": verdict,
                     "reason": reason, "sites": [], "criterion_broken": False,
                     "dup_with": [], "companion": []})

    # ── JS 模块 ──
    entries = _app_json_entry_js() | _using_components_js(sources)
    required_prod: Set[str] = set()
    required_test: Set[str] = set()
    for rel, text in sources.items():
        if not rel.endswith(".js") or pools.get(rel) not in ("prod", "test"):
            continue
        bucket = required_prod if pools.get(rel) == "prod" else required_test
        for m in _REQUIRE_RE.finditer(text):
            tgt = _resolve_js(m.group(2), rel)
            if tgt:
                bucket.add(tgt)

    js_candidates = [rel for rel in sources
                     if rel.endswith(".js") and rel.startswith(tuple(f"{r}/" for r in JS_ROOTS))
                     and pools.get(rel) == "prod"]
    for rel in sorted(js_candidates):
        companion: List[str] = []
        if rel in required_prod:
            verdict, reason = "alive", ""
        elif rel in entries:
            verdict = "entry-component" if "components/" in rel else (
                "entry-app" if rel.endswith("app.js") else "entry-page")
            reason = verdict
        elif rel in required_test:
            verdict, reason = "test-only", "test-only"
        else:
            verdict, reason = "dead", "keep-decision"
            # 组件是"目录整体"消费的：判死的组件 js ⇒ 同目录同名 wxml/wxss/json 一并死
            if "/components/" in rel:
                stem = rel[:-3]
                companion = [f"{stem}{ext}" for ext in (".wxml", ".wxss", ".json")
                             if (REPO / f"{stem}{ext}").is_file()]
        hits.append({"kind": "js", "path": rel, "verdict": verdict, "reason": reason,
                     "sites": [], "criterion_broken": False, "dup_with": [],
                     "companion": companion})

    meta = {
        "scanned_text_files": idx.scanned,
        "asset_inventory": len(inventory),
        "duplicate_basenames": dup,
        "dynamic_sites": idx.dynamic_sites,
        "excluded_dirs": sorted(EXCLUDE_DIRS),
        "excluded_files": sorted(EXCLUDE_FILES),
        "self_excluded": SELF_REL not in sources and SELF_TEST_REL not in sources,
    }
    return hits, idx.dynamic_sites, meta


#: 需要**显式登记**的判定：都是"判据给不出确定答案、必须有人表态"的那类。
#: 不在此列的 verdict（alive / entry-page / entry-app / entry-component / py-main /
#: qa-artifact）是**结构化推导**的：入口来自 `app.json` 的 pages 与各 `.json` 的
#: usingComponents、`py-main` 来自文件里的 `__main__`、`qa-artifact` 来自
#: `ASSET_QA_ROOTS`。它们每次运行都**重新从仓库配置推导** ⇒ 不可能腐烂，
#: 再抄一份白名单只会制造"白名单与事实不同步"的新风险。
#: （任务书要求"入口点也要显式登记"——这里用**更强**的方式满足：登记在仓库配置里，
#: 而非登记在脚本里；新增页面自动进面，不会漏。）
NEEDS_REGISTRATION = {
    "dead", "dead-dynamic-risk", "prose-only", "docs-only", "test-only",
    "alive-weak-string",
}


def check(hits: List[dict]) -> Tuple[List[str], List[str]]:
    """门禁：需表态的判定必须在 REGISTERED 里；已表态但已消失 ⇒ 也红（防白名单腐烂）。"""
    need = [h for h in hits if h["verdict"] in NEEDS_REGISTRATION]
    keys = {_key(h["kind"], h["path"]) for h in need}
    added = sorted(k for k in keys if k not in REGISTERED)
    removed = sorted(k for k in REGISTERED if k not in keys)
    # 理由码必须是 REASONS 里声明过的
    bad = sorted(f"{k}(未知理由码 {REGISTERED[k]})"
                 for k in REGISTERED if REGISTERED[k] not in REASONS)
    return added + bad, removed


def main() -> int:
    global REPO
    ap = argparse.ArgumentParser(description="全仓零引用资产/代码盘点（可复跑判据）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--check", action="store_true", help="门禁：与 REGISTERED 比对")
    # --root 的存在意义：让**判据本身**可在合成小树上被测试（tests/test_k84_dead_refs.py）
    # ——"判据能不能分辨活/死"必须有测试，否则它只是一段没人验证过的正则。
    ap.add_argument("--root", default=str(REPO), help="仓库根（默认脚本所在仓库）")
    args = ap.parse_args()
    REPO = Path(args.root).resolve()

    hits, dyn, meta = scan()

    if args.json:
        print(json.dumps({"hits": hits, "meta": meta}, ensure_ascii=False, indent=2))
        return 0

    if args.check:
        added, removed = check(hits)
        if meta["duplicate_basenames"]:
            print(f"CRITERION-BROKEN：资产 basename 不唯一 {meta['duplicate_basenames']}")
            return 1
        if not meta["self_excluded"]:
            print("CRITERION-BROKEN：脚本未把自己排除出扫描面（自引用陷阱）")
            return 1
        if not added and not removed:
            print(f"OK：{len(hits)} 个被审对象全部已表态（非 alive 判定 {sum(1 for h in hits if h['verdict'] != 'alive')} 条均在 REGISTERED）")
            return 0
        for a in added:
            print(f"未登记的非引用判定: {a}")
        for r in removed:
            print(f"已登记但已消失（请更新 REGISTERED）: {r}")
        return 1

    order = {"dead": 0, "dead-dynamic-risk": 1, "prose-only": 2, "docs-only": 3,
             "test-only": 4, "keep-decision": 5, "qa-artifact": 6, "entry-component": 7,
             "entry-page": 8, "entry-app": 9, "py-main": 10, "alive-weak-string": 11,
             "alive": 12}
    for h in sorted(hits, key=lambda x: (order.get(x["verdict"], 99), x["kind"], x["path"])):
        extra = ""
        if h.get("module"):
            extra = f"  ({h['module']})"
        if h["sites"]:
            extra += "  <- " + ", ".join(f"{f}:{ln}" for f, ln in h["sites"][:3])
        if h.get("dynamic_risk") and h["verdict"] in ("dead", "dead-dynamic-risk"):
            extra += "  [dynamic-risk: 仓内存在动态拼路径位点]"
        if h.get("companion"):
            extra += "  +同伴文件 " + ", ".join(h["companion"])
        if h["verdict"] == "alive":
            continue
        print(f"[{h['verdict']:18}] {h['kind']:8} {h['path']}{extra}")
    print(f"TOTAL {len(hits)}  非 alive {sum(1 for h in hits if h['verdict'] != 'alive')}"
          f"  dead {sum(1 for h in hits if h['verdict'] == 'dead')}")
    if meta["dynamic_sites"]:
        print(f"动态拼接位点 {len(meta['dynamic_sites'])} 处（判据盲区，见 docstring 边界 1）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
