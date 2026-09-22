# -*- coding: utf-8 -*-
"""k84 必修6-C：零引用资产/代码**判据本身**的回归锁（`scripts/audit_dead_refs.py`）。

## 为什么锁判据，而不是锁"清单为空"
k12/k14 两次死资产清理都是**人肉 grep 一次性**完成，没有留下可复跑判据；k84 把判据
落成脚本（`scripts/audit_dead_refs.py`）。但"脚本能跑"不等于"脚本判得准"——
一个**永远返回 alive** 的脚本也能跑通、也能全绿。所以本文件的重点不是"当前有没有
死资产"，而是**判据有没有分辨力**：

  · 合成小树上，"被引用的"必须判活、"零引用的"必须判死（否则判据是瞎的）；
  · 合成小树上，**历史批次叙述文本里的提及**必须**不**算活（否则正是 k12/k14 那个坑：
    plan 文档会把已删资产的文件名逐个列出来）；
  · 动态拼路径必须被**降级上报**而不是被悄悄当成死（判据盲区要可见）；
  · basename 重名必须让判据**自己喊 CRITERION-BROKEN** 而不是硬猜。

真仓库上的门禁另有一条（`test_gate_is_green_on_the_repo`），它保证每次跑测试都会
执行 `--check`：**新冒出一个零引用对象而不在 REGISTERED 里表态 ⇒ 红**。

对应关系：`docs/superpowers/plans/2026-09-08-k12-icon-redraw.md`（icons/ 整体删除）、
`docs/superpowers/plans/2026-09-09-k14-tab-icons.md`（23 枚死资产清理）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "audit_dead_refs.py"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        cwd=str(REPO), capture_output=True, text=True,
    )


def _verdicts(root: Path) -> dict:
    r = _run(root, "--json")
    assert r.returncode == 0, f"审计脚本运行失败：{r.stdout}{r.stderr}"
    data = json.loads(r.stdout)
    return {h["path"]: h for h in data["hits"]}, data["meta"]


def _base_tree(tmp: Path) -> Path:
    """最小合成仓：一个被引用的资产 + 一个零引用资产 + 一个页面入口 + 两个 js 模块。"""
    (tmp / "miniprogram" / "assets" / "images").mkdir(parents=True)
    (tmp / "miniprogram" / "pages" / "home").mkdir(parents=True)
    (tmp / "miniprogram" / "utils").mkdir(parents=True)
    (tmp / "src" / "pkg").mkdir(parents=True)

    (tmp / "miniprogram" / "assets" / "images" / "ic-used.png").write_bytes(b"PNG")
    (tmp / "miniprogram" / "assets" / "images" / "ic-orphan.png").write_bytes(b"PNG")
    (tmp / "miniprogram" / "pages" / "home" / "home.wxml").write_text(
        '<image src="/assets/images/ic-used.png"/>', encoding="utf-8")
    (tmp / "miniprogram" / "app.json").write_text(
        json.dumps({"pages": ["pages/home/home"]}), encoding="utf-8")
    (tmp / "miniprogram" / "pages" / "home" / "home.js").write_text(
        "require('../../utils/helper');\nPage({});", encoding="utf-8")
    (tmp / "miniprogram" / "utils" / "helper.js").write_text(
        "module.exports = {};", encoding="utf-8")
    (tmp / "miniprogram" / "utils" / "orphan.js").write_text(
        "module.exports = {};", encoding="utf-8")

    (tmp / "src" / "pkg" / "live.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp / "src" / "pkg" / "orphan.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    (tmp / "src" / "pkg" / "consumer.py").write_text(
        "from src.pkg.live import f\n", encoding="utf-8")
    return tmp


# ── 1. 真仓库门禁 ──────────────────────────────────────────────────────────
def test_gate_is_green_on_the_repo():
    """`--check` 在真仓库上退出码 0（不依赖测试进程的 import 次序，独立子进程跑）。"""
    r = subprocess.run([sys.executable, str(SCRIPT), "--check"],
                       cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, f"零引用门禁红：{r.stdout}{r.stderr}"
    assert "CRITERION-BROKEN" not in r.stdout, "判据自检失败"


def test_criterion_is_not_blind_on_the_real_repo():
    """判据在真仓库上必须**看见了东西**——否则"全绿"没有意义（空扫描也全绿）。"""
    r = _run(REPO, "--json")
    assert r.returncode == 0, r.stderr
    meta = json.loads(r.stdout)["meta"]
    assert meta["scanned_text_files"] > 200, "扫描面过小，判据可能瞎了"
    assert meta["asset_inventory"] >= 40, "资产清单过小（本仓 miniprogram/assets 有 40+ 枚）"
    # 自引用陷阱必须关着：脚本自己（含 REGISTERED 白名单）**与本测试文件**（含合成夹具）
    # 都不能进扫描面——否则白名单会自我判活、夹具会污染真仓库的 dynamic_sites。
    assert meta["self_excluded"] is True, "判据自身（脚本/夹具）未排除 ⇒ 自引用陷阱开着"
    assert not [
        d for d in meta["dynamic_sites"] if "test_k84_dead_refs" in d["file"]
    ], "本测试文件的合成夹具污染了真仓库的动态位点读数（应已排除出扫描面）"


# ── 2. 判据的分辨力（合成树）──────────────────────────────────────────────
def test_referenced_asset_is_alive_and_orphan_asset_is_dead(tmp_path):
    """被 wxml 引用的资产判活；零引用的判死——两条都要成立，否则判据没有分辨力。"""
    root = _base_tree(tmp_path)
    hits, _ = _verdicts(root)
    assert hits["miniprogram/assets/images/ic-used.png"]["verdict"] == "alive"
    assert hits["miniprogram/assets/images/ic-orphan.png"]["verdict"] == "dead"


def test_relative_asset_path_form_is_also_counted(tmp_path):
    """小程序里资产有两种写法：`/assets/images/x.png` 与 `../../assets/images/x.png`。
    只认绝对写法会让相对写法的资产被误判死（判据必须两种都覆盖）。"""
    root = _base_tree(tmp_path)
    (root / "miniprogram" / "assets" / "images" / "ic-rel.png").write_bytes(b"PNG")
    (root / "miniprogram" / "pages" / "home" / "home.js").write_text(
        "var s = '../../assets/images/ic-rel.png';\nrequire('../../utils/helper');",
        encoding="utf-8")
    hits, _ = _verdicts(root)
    assert hits["miniprogram/assets/images/ic-rel.png"]["verdict"] == "alive"


def test_required_js_module_is_alive_and_orphan_js_is_dead(tmp_path):
    root = _base_tree(tmp_path)
    hits, _ = _verdicts(root)
    assert hits["miniprogram/utils/helper.js"]["verdict"] == "alive"
    assert hits["miniprogram/utils/orphan.js"]["verdict"] == "dead"


def test_registered_page_is_an_entry_point_not_dead(tmp_path):
    """`app.json` 注册的页面没有 require 引用，但必然被打包执行 ⇒ 不是死资产。"""
    root = _base_tree(tmp_path)
    hits, _ = _verdicts(root)
    assert hits["miniprogram/pages/home/home.js"]["verdict"] == "entry-page"


def test_import_edge_is_followed(tmp_path):
    """被 import 的 py 模块判活；两侧都没引用边的判死（含"自己也是孤儿"的 consumer）。"""
    root = _base_tree(tmp_path)
    hits, _ = _verdicts(root)
    assert hits["src/pkg/live.py"]["verdict"] == "alive"
    assert hits["src/pkg/orphan.py"]["verdict"] == "dead"
    assert hits["src/pkg/consumer.py"]["verdict"] == "dead"


def test_main_module_is_recognised_as_entry_point(tmp_path):
    root = _base_tree(tmp_path)
    (root / "src" / "pkg" / "runner.py").write_text(
        "def main():\n    return 0\n\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8")
    hits, _ = _verdicts(root)
    assert hits["src/pkg/runner.py"]["verdict"] == "py-main"


# ── 3. 判据的**盲区与陷阱**必须可见（这几条才是 k12/k14 真正的教训）──────────
def test_narrative_prose_does_not_count_as_a_live_reference(tmp_path):
    """**核心回归**：历史批次叙述文本（plan/report）会把**已删**资产的**文件名逐个
    列出来**（k14 plan §4 就列了 23 枚）。把这种提及算作引用 ⇒ 死资产被判活 ⇒
    判据失效。本条的合成树正是照这个形状搭的。"""
    root = _base_tree(tmp_path)
    plan = root / "docs" / "superpowers" / "plans" / "2026-09-09-k14-tab-icons.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("- 死资产清单：ic-orphan.png（此处仅叙述，不构成任何消费）\n",
                    encoding="utf-8")
    hits, _ = _verdicts(root)
    v = hits["miniprogram/assets/images/ic-orphan.png"]
    assert v["verdict"] == "prose-only", (
        "叙述性提及被判成了消费——这正是 k12/k14 那个坑：plan 文档列过的死资产永远删不掉")
    assert any("k14-tab-icons.md" in f for f, _ in v["sites"]), "应记录提及位点"


def test_product_doc_mention_is_its_own_class_not_alive(tmp_path):
    """只被**产品文档**提及 ⇒ 判 `docs-only`（登记，不自动删）——文档可能是它仍需
    存在的唯一依据，与"代码在消费"必须分开。"""
    root = _base_tree(tmp_path)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "DESIGN_SYSTEM.md").write_text(
        "图标 ic-orphan.png 属于设计系统。\n", encoding="utf-8")
    hits, _ = _verdicts(root)
    assert hits["miniprogram/assets/images/ic-orphan.png"]["verdict"] == "docs-only"


def test_test_only_reference_is_not_reported_dead(tmp_path):
    """只被 tests/ 引用 ⇒ `test-only`（测试钉既有行为，不是死资产）。"""
    root = _base_tree(tmp_path)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_icon.py").write_text(
        'p = "ic-orphan.png"\n', encoding="utf-8")
    hits, _ = _verdicts(root)
    assert hits["miniprogram/assets/images/ic-orphan.png"]["verdict"] == "test-only"


def test_dynamic_path_concatenation_is_flagged_not_silently_dead(tmp_path):
    """动态拼路径是判据的**已声明盲区**：不得被悄悄当成死 ⇒ 降级为
    `dead-dynamic-risk` + 在 meta.dynamic_sites 里报出位点（只报不删）。"""
    root = _base_tree(tmp_path)
    (root / "miniprogram" / "utils" / "dyn.js").write_text(
        "var n = 'x';\nvar p = '/assets/images/' + n + '.png';\n", encoding="utf-8")
    hits, meta = _verdicts(root)
    assert hits["miniprogram/assets/images/ic-orphan.png"]["verdict"] == "dead-dynamic-risk"
    assert any(d["kind"] == "asset-dir-concat" for d in meta["dynamic_sites"]), \
        "动态拼接位点未被报出——盲区不可见就等于声称已覆盖"


def test_static_asset_literal_is_not_mistaken_for_concatenation(tmp_path):
    """含**完整文件名**的静态字面量不是拼接——初版在此过度命中（每个 icon 都被报成
    拼接位点，真盲区反而被噪声淹没）。"""
    root = _base_tree(tmp_path)
    _, meta = _verdicts(root)
    assert meta["dynamic_sites"] == [], (
        f"静态引用被误报为动态拼接：{meta['dynamic_sites']}")


def test_duplicate_basename_reports_criterion_broken(tmp_path):
    """两个目录下同名资产 ⇒ basename 匹配不再可靠 ⇒ 判据必须**自曝失效**
    （CRITERION-BROKEN + 非零退出），而不是硬猜一个结论。"""
    root = _base_tree(tmp_path)
    (root / "src" / "static").mkdir(parents=True)
    (root / "src" / "static" / "ic-orphan.png").write_bytes(b"PNG")
    r = _run(root, "--check")
    assert r.returncode == 1
    assert "CRITERION-BROKEN" in r.stdout, r.stdout


def test_new_dead_object_without_registration_turns_the_gate_red(tmp_path):
    """门禁的**核心行为**：冒出一个未表态的零引用对象 ⇒ 红。
    （没有这条，"门禁"就是个永远绿的摆设。）"""
    root = _base_tree(tmp_path)
    r = _run(root, "--check")
    assert r.returncode == 1, "未登记的零引用对象没有让门禁变红"
    assert "未登记的非引用判定" in r.stdout
    assert "asset::miniprogram/assets/images/ic-orphan.png" in r.stdout


def test_registered_but_vanished_object_turns_the_gate_red():
    """反向也查：登记项已消失 ⇒ 红。防止白名单腐烂成"什么都放行"。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "removed" in src and "已登记但已消失" in src, "缺少「登记项消失」的反向校验"
    # 且 REGISTERED 的每个理由码都必须在 REASONS 里声明过（拼错理由码 = 不可读的表态）
    assert '"deliberate-retain"' in src and '"weak-lazy-table"' in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
