"""k64：ML 回答质量预测器（E4）整模块移除锁定。

## 为什么删（控制方裁决 + 用户拍板）

`src/ml/quality_predictor.py` 自称「预测回答质量、决定要不要重试」的在线学习模块。
独立复核（k64 报告 §1，文本 + AST 584 文件双口径）结论：

1. **只写不读**：主链只在用户反馈时调它的 `update()`（`handler.py` 的
   `_handle_feedback`），而两个读方法 `predict()` / `should_retry()` **全仓 0 调用**
   → 没有任何东西消费它的预测结果，「决定要不要重试」这条能力**从未存在**。
2. **唯一调用点本身是坏的（P1）**：`update()` 签名里 `personality` 是**必填位置参数、
   无默认值**，而调用点只传 keywords → 每次反馈都抛
   `TypeError: update() missing 1 required positional argument: 'personality'`，
   被外层 `except Exception` **静默吞掉** → 模型从未真正训练过（`n_updates` 恒为 0），
   同时把 `if is_positive: reply = "感谢认可！"` 与「📊 你的认可率：X%（N次反馈）」
   成熟度回复**整段跳过**，用户只拿到兜底句。**故本批同时是该 P1 缺陷的修复批。**
3. 特征几乎全是常量（`response_len` 硬编码 0；`emotion_label` 的来源
   `self._last_emotion_labels` 全仓只读不写 → 恒 `"neutral"`；`personality` 根本传不进去），
   模型路径 `/opt/fortune-agent/models/quality_model.npz` 不存在 → 每次随机初始化，
   无任何测试、无其它引用，只白耗 CPU 并试图存盘。

**该能力当前未实现**（曾有此模块，但读路径从未接入，已于 k64 移除）；如日后要做，
需重新立项，不要从本模块的残骸里「复活」——本守卫即为防复活。

## 本守卫的口径

1. 文件不存在；
2. **AST 口径**全仓 0 接线：静态 import / 名字 / 属性 / **动态 import 字面量**
   （`importlib.import_module(<字面量>)` / `__import__(<字面量>)`）；
3. 扫描面下限（只许升不许降，k62 同款纪律），防有人把扫描面改窄让守卫恒绿；
4. 机制自检：探测器对「真接线」必须命中、对「纯注释说明」必须不命中
   （证明第 2 条不是恒绿摆设）。

**不单独断言 `should_retry`**：它是通用词，日后别处写一个无关的重试函数会被本守卫误伤
（k62 报告 §10 同款理由：不做越界断言）。它随模块一并消失，已由第 1、2 条覆盖。
"""
import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

MODULE = "src.ml.quality_predictor"
MODULE_PATH = "src/ml/quality_predictor.py"
SYMBOLS = ("QualityPredictor", "quality_predictor")

SCAN_GLOBS = ("src/**/*.py", "scripts/**/*.py", "tests/**/*.py")
EXCLUDE_PARTS = {"__pycache__", ".git"}
SELF = Path(__file__).resolve()

# 实测（k64 改动后）：src=204 / scripts=142 / tests=227 / 合计 573。
# 下限取「只许升不许降」，留少量余量；改窄扫描面即红。
MIN_FILES_PER_GLOB = {
    "src/**/*.py": 200,
    "scripts/**/*.py": 135,
    "tests/**/*.py": 215,
}
MIN_TOTAL_FILES = 560


# ====================================================================
# 探测器（唯一实现，自检与全仓扫描共用同一份逻辑）
# ====================================================================

def _hits_in_text(text: str, filename: str = "<sample>"):
    """返回 [(kind, lineno, detail)]；解析失败抛 SyntaxError（不静默跳过）。"""
    hits = []
    tree = ast.parse(text, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == MODULE or a.name.startswith(MODULE + "."):
                    hits.append(("Import", node.lineno, a.name))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == MODULE or mod.startswith(MODULE + "."):
                hits.append(("ImportFrom", node.lineno, mod))
            for a in node.names:
                if a.name in SYMBOLS:
                    hits.append(("ImportFrom-name", node.lineno, a.name))
        elif isinstance(node, ast.Name):
            if node.id in SYMBOLS:
                hits.append(("Name", node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            if node.attr in SYMBOLS:
                hits.append(("Attribute", node.lineno, node.attr))
        elif isinstance(node, ast.Call):
            # 动态 import 面：只认「字面量实参」，不做「任意字符串含模块名即命中」的宽扫
            fn = node.func
            fname = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if fname in ("import_module", "__import__") and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    if "quality_predictor" in arg0.value or arg0.value.startswith("src.ml"):
                        hits.append(("DynamicImport", node.lineno, arg0.value))
    return hits


def _iter_scan_files():
    seen = []
    for pattern in SCAN_GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            if any(part in EXCLUDE_PARTS for part in p.parts):
                continue
            if p.resolve() == SELF:      # 只排除本守卫自身（见机制自检）
                continue
            seen.append((pattern, p))
    return seen


# ====================================================================
# 1. 文件不存在
# ====================================================================

def test_module_file_removed():
    """模块文件必须不存在，且不能以任何名字复活（.py 之外的变体不认）。"""
    assert not (ROOT / MODULE_PATH).exists(), (
        f"{MODULE_PATH} 复活 —— 该模块已按控制方裁决（用户拍板）整体移除；"
        "「ML 回答质量预测 / 低质量自动重试」能力当前未实现，要做需重新立项")
    assert not (ROOT / "src/ml/quality_predictor").exists(), "出现了同名包/目录"


# ====================================================================
# 2. AST 口径：全仓 0 接线
# ====================================================================

def test_no_wiring_anywhere_ast():
    """AST 口径全仓扫描：静态 import / 名字 / 属性 / 动态 import 字面量 = 0。"""
    bad = []
    parse_failed = []
    for pattern, path in _iter_scan_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:                      # pragma: no cover
            parse_failed.append(f"{path.relative_to(ROOT)}: {e}")
            continue
        try:
            hits = _hits_in_text(text, filename=str(path))
        except SyntaxError as e:                  # 解析不了=扫描面有洞，必须报出来
            parse_failed.append(f"{path.relative_to(ROOT)}: {e}")
            continue
        bad += [f"{path.relative_to(ROOT)}:{ln} [{kind}] {detail}" for kind, ln, detail in hits]
    assert not parse_failed, "扫描面存在无法解析的文件（守卫会漏报）：\n" + "\n".join(parse_failed)
    assert not bad, (
        "quality_predictor 接线复活（该模块已按控制方裁决移除）：\n" + "\n".join(bad))


def test_handler_feedback_block_gone():
    """行为面：`_handle_feedback` 里不得再有 E4 训练块与 `_last_emotion_labels` 死引用。

    只按 AST 名字判定，不按注释/文案判定（说明性注释不算接线）。
    """
    rel = "src/bot/handler.py"
    text = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(text, filename=rel)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for gone in ("quality_predictor", "QualityPredictor"):
        assert gone not in names and gone not in attrs, f"{rel} 仍有 {gone} 引用"
    assert "_last_emotion_labels" not in attrs, (
        "_last_emotion_labels 是只读不写的死引用（全仓从无写入方），已随 E4 块一并移除")


# ====================================================================
# 3. 扫描面下限（只许升不许降）
# ====================================================================

def test_scan_face_floors_hold():
    files = _iter_scan_files()
    per = {}
    for pattern, _p in files:
        per[pattern] = per.get(pattern, 0) + 1
    for pattern, floor in MIN_FILES_PER_GLOB.items():
        assert pattern in SCAN_GLOBS, f"扫描面被改窄：少了 {pattern}"
        assert per.get(pattern, 0) >= floor, (
            f"{pattern} 扫描文件数 {per.get(pattern, 0)} < 下限 {floor}（不得改窄）")
    assert len(files) >= MIN_TOTAL_FILES, (
        f"扫描总数 {len(files)} < 下限 {MIN_TOTAL_FILES}（不得改窄）")


# ====================================================================
# 4. 机制自检：探测器对真接线必须命中（防守卫恒绿）
# ====================================================================

def test_scanner_catches_static_wiring():
    assert _hits_in_text("from src.ml.quality_predictor import QualityPredictor\n")
    assert _hits_in_text("import src.ml.quality_predictor\n")
    assert _hits_in_text("from src.ml import quality_predictor\n")
    assert _hits_in_text("import src.ml.quality_predictor as qp\n")


def test_scanner_catches_name_and_attribute_use():
    assert _hits_in_text("x = QualityPredictor()\n")
    assert _hits_in_text("h.quality_predictor.update(message='x')\n")


def test_scanner_catches_dynamic_import_wiring():
    assert _hits_in_text('import importlib\nimportlib.import_module("src.ml.quality_predictor")\n')
    assert _hits_in_text('__import__("src.ml.quality_predictor")\n')


def test_scanner_ignores_comment_and_docstring_mentions():
    """说明性文字不算接线（否则「登记移除」的注释本身会把守卫判红）。"""
    assert not _hits_in_text("# quality_predictor / QualityPredictor 已于 k64 移除\n")
    assert not _hits_in_text('"""曾被移除的模块：src/ml/quality_predictor.py"""\n')
    assert not _hits_in_text('note = "quality_predictor 已移除"\n')


# ====================================================================
# 5. 行为面：P1 缺陷不复发（反馈回复不得再被异常吞掉）
# ====================================================================

class _FakePrefDAO:
    """`_handle_feedback` 只用 detect_topic / learn 两个方法。"""

    def detect_topic(self, text):
        return "wealth"

    def learn(self, user_id, is_positive, topic=""):
        return SimpleNamespace(is_mature=True, accuracy_pct=90, feedback_count=7)


class _FakeSessionDAO:
    def get_context_for_llm(self, user_id, history_limit=5, session_id=None):
        return [{"role": "user", "content": "我最近很焦虑"},
                {"role": "assistant", "content": "（回复）"}]


def _feedback_handler():
    from src.bot.handler import MessageHandler
    h = object.__new__(MessageHandler)
    h.preference_dao = _FakePrefDAO()
    h.session_dao = _FakeSessionDAO()
    return h


def test_feedback_reply_reaches_maturity_branch():
    """P1 锁定：反馈后必须走到成熟度分支，而不是被例外吞成兜底句。

    改动前：`_handle_feedback` 里的 E4 训练块对 `self.quality_predictor.update(...)`
    抛异常（生产上：缺必填参数 `personality` 的 TypeError；本用例不 import 已删模块，
    故以同样被外层 `except Exception` 吞掉的 AttributeError 呈现），
    于是整个 `try` 后半段被跳过 → 只返回兜底句 → 本用例**红**。
    改动后：块已移除 → 返回「感谢认可！+ 📊 你的认可率」→ **绿**。
    """
    reply = _feedback_handler()._handle_feedback("👍", "u1")
    assert "感谢认可！" in reply, f"未走到正常分支（被兜底句吞掉？）：{reply!r}"
    assert "你的认可率" in reply, f"未走到成熟度分支：{reply!r}"
    assert "90%" in reply and "7次反馈" in reply, f"认可率数字不对：{reply!r}"
    assert "超过80%" in reply, f"≥80% 鼓励文案缺失：{reply!r}"


def test_negative_feedback_reply_reaches_maturity_branch():
    """👎 同一条链：改动前同样被吞成「收到，会继续改进～」。"""
    reply = _feedback_handler()._handle_feedback("👎", "u1")
    assert "收到反馈，我会调整的～" in reply, f"未走到正常分支：{reply!r}"
    assert "你的认可率" in reply, f"未走到成熟度分支：{reply!r}"
