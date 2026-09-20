# -*- coding: utf-8 -*-
"""k62 守卫（防复发）：早期残留「情绪-人设」管线必须保持**已拆除**状态。

背景（为什么会有这个文件）
──────────────────────────────────────────────────────────────────────
产品里从来没有「人设预设」。但仓库里留着一簇**既没接进产品、又有一堆绿测试**
的早期残留 —— 它已经在一次审查里把审查方骗过一次（把死模块的测试读数当成
产品行为，`tests/test_mood_detector.py` 46 例全绿看着像产品能力）。用户已拍板
拆除，本文件在**仓库面 + 行为面**兜底，防止三件事：

1. **死模块复活**
   - `src/engines/mood_detector.py`：`src/` 下 0 引用。接线在 96586b0
     「Task 0: Simplify personality system from 3 modes to 1 unified tone」
     里被摘除（-from src.engines.mood_detector import ...、-self.mood_detector），
     **文件忘了删**，于是留成一簇带绿测试的孤儿。
   - `src/engines/emotion_soother.py`：`src/` 下 0 引用，且**从未接线**——
     525aa4e 引入它的同一个 commit 里就加了取代它的 `message_analyzer.py`
     （其 docstring：「Replaces two sequential AI calls (EmotionSoother +
     IntentClassifier)」）。生下来就是死的。

2. **三个退化风格权重复活**：`style_sassy` / `style_analyst` / `style_gentle`
   在 `PreferenceDAO.learn()` 里**同一公式、同一输入**更新（只有被选中的那一个
   用 `alpha*(1.0 if is_positive else 0.0)`，另两个不动），归一化后**恒等**
   （≈1/3）—— 不携带任何信息，却经 `to_prompt_hint()` 进了活提示词
   （`_get_preference_hint` → `/api/calendar/daily|week`）。

3. **反向事故（比复发更危险）**：这三个权重对应的 DB 列**不许 DROP**（生产库
   有真实数据）。本文件**同时**锁两件事：代码路径已移除 **且** 列仍在 SCHEMA 里。

── 红线（本文件任何修改都不得违反） ─────────────────────────────────────
1. 不得为了让断言通过而**改宽**扫描面（排除目录、放宽正则、删文件数下限）；
   扫描面有**逐 glob 文件数下限**，只许升不许降；
2. 不得删除本文件里锁定「活代码仍在」的用例（night_persona /
   quality_predictor / emoji 清理用例 / 列不许 DROP）—— 那些是**不许碰**的活
   路径；它们改前改后都是绿的，存在的意义正是拦住「把活代码当残留删掉」；
3. 不设白名单。

── 跨批边界（k62 r2 更正，勿恢复） ──────────────────────────────────────
`src/engines/advisor_v2.py` 的**按性别人设**已由用户拍板改为「统一成单一豆包
口吻」，**归 k63 承担**。本文件**对它不作任何断言** —— 既不要求保留（那会在
k63 合并后变红，两条一起绿不了），也不替 k63 断言其目标状态（那会在 k63 未
合并时变红）。r1 曾按当时的口头指令写过「advisor_v2 性别人设必须保留」，r2 已
移除。**不要把它加回来。**
"""
from __future__ import annotations

import ast
import asyncio
import re
import sys
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── 扫描面（只许加不许减） ──
SCAN_GLOBS = ("src/**/*.py", "scripts/**/*.py")
EXCLUDE_PARTS = {".git", "__pycache__", "node_modules", "tests", "miniprogram"}
# 扫描面下限（实测 2026-09-20：src 207 / scripts 142）。只许升不许降。
MIN_FILES_PER_GLOB = {
    "src/**/*.py": 200,
    "scripts/**/*.py": 130,
}
MIN_SCANNED_FILES = 330

# ── 被拆除的对象 ──
REMOVED_MODULE_FILES = (
    "src/engines/mood_detector.py",
    "src/engines/emotion_soother.py",
)
# module 名（import 面）；"mood_detector" 亦覆盖 `import src.engines.mood_detector`
REMOVED_MODULE_STEMS = ("mood_detector", "emotion_soother")
# 三个退化权重（属性名 = 列名）
DEAD_STYLE_ATTRS = ("style_sassy", "style_analyst", "style_gentle")
# 风格字样（提示词/响应里都不得再出现）
STYLE_WORD_RE = re.compile(r"风格|sassy|analyst|gentle|毒舌|理性分析|温柔陪伴")


# ====================================================================
# 工具
# ====================================================================

def _iter_scan_files():
    seen = []
    for pattern in SCAN_GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            if any(part in EXCLUDE_PARTS for part in p.parts):
                continue
            seen.append((pattern, p))
    return seen


def _read(path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """去掉 Python `#` 与 SQL `--` 行注释后再判定（k59 同款口径：注释不是
    可执行写法）。注释里写「未 DROP COLUMN」这类说明不应被当成破坏性迁移。
    """
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith("--"):
            continue
        for marker in ("#", "--"):
            i = line.find(marker)
            if i != -1:
                line = line[:i]
        out.append(line)
    return "\n".join(out)


def _code_identifiers(path):
    """AST 取「代码里的标识符」：Name/Attribute/keyword/AnnAssign 目标。

    只认**可执行引用**（`prefs.style_sassy = x`、`learn(style=...)`、
    `f(preferred_style=...)`）；docstring/注释里解释性提到不算 —— 与 k59
    「注释不是可执行写用法」同一口径。复活一个权重必然产生代码标识符。
    """
    tree = ast.parse(_read(path), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.FunctionDef):
            names.add(node.name)
    return names


def _dynamic_import_targets(path):
    """精确捕捉动态 import：`importlib.import_module(<字面量>)` / `__import__(<字面量>)`。

    不做「任意字符串常量含模块名即命中」的宽扫 —— 那会把解释性 docstring 也
    算成接线（本文件自己的常量表就会误报），且**漏报**真正的 `getattr` 花活
    反而更少。接线必然要 import，故只锁 import 面。
    """
    tree = ast.parse(_read(path), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fname = ""
        if isinstance(fn, ast.Attribute):
            fname = fn.attr
        elif isinstance(fn, ast.Name):
            fname = fn.id
        if fname not in ("import_module", "__import__"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            hits.append(f"{path}:{node.lineno} {fname}({node.args[0].value!r})")
    return hits


def _py_files_under(*subdirs):
    out = []
    for sub in subdirs:
        for p in sorted((ROOT / sub).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            out.append(p)
    return out


# ====================================================================
# 1. 死模块：文件不存在 + 全仓（src/scripts/tests）无 import
# ====================================================================

@pytest.mark.parametrize("rel", REMOVED_MODULE_FILES)
def test_removed_module_file_does_not_exist(rel):
    """两个死模块文件必须不存在（复活即红）。"""
    assert not (ROOT / rel).exists(), (
        f"{rel} 复活了 —— k62 已拆除该残留管线；如确需恢复，"
        f"必须同时接进产品并删掉本守卫（并在 PR 说明理由）"
    )


def test_no_import_of_removed_modules_anywhere():
    """src/ scripts/ tests/ 下**任何** .py 都不得 import 这两个模块。

    AST 扫描三条 import 面：`import a.b.c` / `from a.b import c` /
    动态 `importlib.import_module(<字面量>)`、`__import__(<字面量>)`。
    k62 复核时实测二者在 src/ 下 0 引用；本断言防的是**重新接线**。
    （只锁 import 面：docstring 里解释性提到模块名不算接线 —— k59 同款口径。）
    """
    hits = []
    for p in _py_files_under("src", "scripts", "tests"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if any(s in a.name for s in REMOVED_MODULE_STEMS):
                        hits.append(f"{p.relative_to(ROOT)}:{node.lineno} import {a.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if any(s in mod for s in REMOVED_MODULE_STEMS):
                    hits.append(f"{p.relative_to(ROOT)}:{node.lineno} from {mod} import ...")
        hits.extend(_dynamic_import_targets(p))
    # 动态 import 命中的是本模块自身常量表也照报；上面 _dynamic_import_targets
    # 只在**实参字面量**含模块名时命中，故无误报。
    hits = [h for h in hits
            if any(s in h for s in REMOVED_MODULE_STEMS)]
    assert not hits, "死模块被重新接线：\n" + "\n".join(hits)


# ====================================================================
# 2. preference_dao：三个权重（属性 / 学习 / 序列化）全移除
# ====================================================================

def test_preferences_dataclass_has_no_dead_style_attrs():
    """`UserPreferences` 不得再有这三个属性（或有 preferred_style 属性）。"""
    from src.storage.preference_dao import UserPreferences
    names = {f.name for f in dataclass_fields(UserPreferences)}
    for attr in DEAD_STYLE_ATTRS:
        assert attr not in names, f"UserPreferences.{attr} 复活（退化权重，无信息量）"
    assert not hasattr(UserPreferences, "preferred_style"), \
        "UserPreferences.preferred_style 复活（恒等 1/3 三选一，无信息量）"
    # 话题权重是**活的**，必须还在（防误删）
    for attr in ("topic_wealth", "topic_love", "topic_career",
                 "topic_health", "topic_growth"):
        assert attr in names, f"话题权重 {attr} 被误删 —— 它是活路径（不是本批目标）"


def test_preference_dao_source_free_of_dead_style_weights():
    """DAO 源码面（AST 标识符）：三个权重名与 preferred_style 不得可执行引用。

    改前实测：`style_sassy`（AnnAssign 定义 + Attribute 读 + keyword 传参）、
    `preferred_style`（FunctionDef + Attribute）全部命中 → 红。
    """
    names = _code_identifiers("src/storage/preference_dao.py")
    for attr in DEAD_STYLE_ATTRS:
        assert attr not in names, f"src/storage/preference_dao.py 仍在代码里引用 {attr}"
    assert "preferred_style" not in names, "preference_dao 仍暴露 preferred_style"
    assert "last_style" not in names, "preference_dao 仍在代码里引用 last_style"


def test_to_prompt_hint_carries_no_style_words():
    """行为面：`to_prompt_hint()` 输出不得含风格字样（话题/长度/准确率保留）。"""
    from src.storage.preference_dao import UserPreferences
    p = UserPreferences(user_id="u1", feedback_count=5, positive_count=4,
                        topic_wealth=0.9)
    hint = p.to_prompt_hint()
    assert hint, "成熟用户必须仍产出提示（本批只摘风格，不摘整条 hint）"
    m = STYLE_WORD_RE.search(hint)
    assert m is None, f"to_prompt_hint 仍含风格字样 {m.group()!r}: {hint!r}"
    assert "话题" in hint and "好评率" in hint, f"话题/准确率提示被误删: {hint!r}"


def test_learn_writes_no_dead_style_weights(tmp_path):
    """行为面 + DB 面：`learn()` 不得再写这三个列（列保持库内默认值不动）。

    同时锁「列未 DROP」：新库 SCHEMA 里这三列仍在（生产库有数据）。
    """
    from src.storage.models import init_db, connect
    from src.storage.preference_dao import PreferenceDAO

    db = str(tmp_path / "k62.db")
    init_db(db)
    dao = PreferenceDAO(db)
    dao.learn("u1", True, topic="wealth", response_len=100)
    dao.learn("u1", False, topic="love", response_len=900)

    conn = connect(db)
    conn.row_factory = None
    cols = {r[1] for r in conn.execute("PRAGMA table_info(user_preferences)")}
    for attr in DEAD_STYLE_ATTRS:
        assert attr in cols, (
            f"user_preferences.{attr} 列不见了 —— k62 红线：**只删代码引用，"
            f"不许 DROP COLUMN**（生产库有数据）")
    row = conn.execute(
        "SELECT style_sassy, style_analyst, style_gentle, "
        "topic_wealth, feedback_count FROM user_preferences WHERE user_id='u1'"
    ).fetchone()
    conn.close()
    assert row is not None, "learn() 未落库（话题学习链断了）"
    assert row[0:3] == (0.33, 0.33, 0.34), (
        f"learn() 仍在写退化风格权重（实测 {row[0:3]!r}，应恒为建表默认值）")
    # 话题权重/计数是**活路径**：必须真的学到了（防「一刀切删干净」误伤）
    assert row[3] != 0.2, "话题权重未被学习 —— 把活路径一起删了"
    assert row[4] == 2, f"feedback_count 应为 2，实测 {row[4]!r}"


def test_learn_signature_has_no_style_channel():
    """`learn()` 不再接受 style 形参（三个权重没了，这条入参无处可去）。"""
    import inspect
    from src.storage.preference_dao import PreferenceDAO
    params = set(inspect.signature(PreferenceDAO.learn).parameters)
    assert "style" not in params, "learn(style=...) 复活 —— 退化权重的入参通道"
    for keep in ("user_id", "is_positive", "topic", "response_len"):
        assert keep in params, f"learn() 的活参数 {keep} 被误删"


# ====================================================================
# 3. 活提示词路径：`_get_preference_hint` / `_get_personalized_context`
# ====================================================================

def _handler_with_prefs(tmp_path, mature=True):
    """最小装配：真 PreferenceDAO + 真临时库，只借 Handler 的方法体。"""
    from src.bot.handler import MessageHandler
    from src.storage.models import init_db
    from src.storage.preference_dao import PreferenceDAO

    db = str(tmp_path / "k62h.db")
    init_db(db)
    dao = PreferenceDAO(db)
    n = 4 if mature else 0
    for i in range(n):
        dao.learn("u1", True, topic="wealth", response_len=100)

    h = object.__new__(MessageHandler)
    h.preference_dao = dao
    return h


def test_preference_hint_has_no_style_words(tmp_path):
    """活路径（`/api/calendar/daily|week` 喂给 LLM 的 preferences=）不得含风格字样。"""
    h = _handler_with_prefs(tmp_path)
    hint = h._get_preference_hint("u1")
    assert hint, "成熟用户的偏好提示不应为空（话题/长度/准确率仍在）"
    m = STYLE_WORD_RE.search(hint)
    assert m is None, f"_get_preference_hint 输出仍含风格字样 {m.group()!r}: {hint!r}"


def test_personalized_context_has_no_style_words(tmp_path):
    """`_get_personalized_context`（主聊天链提示词）同样不得含风格字样。"""
    h = _handler_with_prefs(tmp_path)
    ctx = h._get_personalized_context("u1")
    assert ctx, "成熟用户的个性化上下文不应为空"
    m = STYLE_WORD_RE.search(ctx)
    assert m is None, f"_get_personalized_context 仍含风格字样 {m.group()!r}: {ctx!r}"


def test_handler_source_does_not_read_preferred_style():
    names = _code_identifiers("src/bot/handler.py")
    assert "preferred_style" not in names, "handler 仍读 prefs.preferred_style"
    # learn() 的 style 入参通道必须也已拆（它是退化权重的唯一输入）
    from src.bot.handler import MessageHandler
    import inspect
    src = inspect.getsource(MessageHandler._handle_feedback)
    assert "style=" not in src, "_handle_feedback 仍在给 learn() 传 style="


# ====================================================================
# 4. 接口契约：/api/user/preferences 与 /api/dashboard 响应字段
# ====================================================================

def test_user_preferences_api_has_no_style_fields(tmp_path, monkeypatch):
    """真实调用端点函数（非静态断言）：响应不得含风格字段。"""
    from src.api import user as user_api
    from src.storage.models import init_db
    from src.storage.preference_dao import PreferenceDAO

    db = str(tmp_path / "k62api.db")
    init_db(db)
    dao = PreferenceDAO(db)
    for _ in range(4):
        dao.learn("u1", True, topic="wealth", response_len=100)

    monkeypatch.setattr(user_api, "_preference_dao", dao)
    resp = asyncio.run(user_api.user_preferences(uid="u1"))
    assert resp["is_mature"] is True, "成熟判定被误伤"
    for gone in ("preferred_style", "preferred_style_key"):
        assert gone not in resp, f"/api/user/preferences 仍有 {gone}"
    # 活的字段必须还在（防误删）
    for keep in ("has_data", "top_topics", "length_preference",
                 "accuracy_pct", "feedback_count", "is_mature"):
        assert keep in resp, f"/api/user/preferences 的活字段 {keep} 被误删"
    blob = repr(resp)
    m = STYLE_WORD_RE.search(blob)
    assert m is None, f"响应仍含风格字样 {m.group()!r}: {blob}"


def test_dashboard_preferences_has_no_style_fields(tmp_path):
    """真实调用 build_dashboard：preferences 块不得含 preferred_style/style_breakdown。"""
    from src.api.dashboard import build_dashboard
    from src.storage.models import init_db
    from src.storage.preference_dao import PreferenceDAO
    from types import SimpleNamespace

    db = str(tmp_path / "k62dash.db")
    init_db(db)
    dao = PreferenceDAO(db)
    for _ in range(4):
        dao.learn("u1", True, topic="wealth", response_len=100)

    handler = SimpleNamespace(dao=None, preference_dao=dao, session_dao=None, llm=None)
    dash = build_dashboard("u1", handler)
    prefs = dash["preferences"]
    for gone in ("preferred_style", "style_breakdown"):
        assert gone not in prefs, f"dashboard preferences 仍有 {gone}"
    # 活的字段必须还在
    for keep in ("mature", "preferred_topic", "topics"):
        assert keep in prefs, f"dashboard preferences 的活字段 {keep} 被误删"
    m = STYLE_WORD_RE.search(repr(prefs))
    assert m is None, f"dashboard preferences 仍含风格字样 {m.group()!r}"


def test_accuracy_dashboard_has_no_preferred_style(tmp_path):
    """`/api/user/{id}/accuracy`（= get_accuracy_dashboard）同源字段一并摘除。"""
    from src.storage.models import init_db
    from src.storage.preference_dao import PreferenceDAO

    db = str(tmp_path / "k62acc.db")
    init_db(db)
    dao = PreferenceDAO(db)
    for _ in range(4):
        dao.learn("u1", True, topic="wealth", response_len=100)
    acc = dao.get_accuracy_dashboard("u1")
    assert "preferred_style" not in acc, "accuracy 仪表盘仍有 preferred_style"
    assert "preferred_topic" in acc, "preferred_topic 被误删（活字段）"


@pytest.mark.parametrize("rel", ["src/api/user.py", "src/api/dashboard.py"])
def test_api_sources_free_of_style_fields(rel):
    """接口源文件（AST 标识符）：风格字段不得再出现在代码里。"""
    names = _code_identifiers(rel)
    for tok in ("preferred_style", "preferred_style_key", "style_breakdown",
                "style_names") + DEAD_STYLE_ATTRS:
        assert tok not in names, f"{rel} 仍在代码里引用 {tok}"


def test_report_prompts_builder_deleted_and_constants_kept():
    """`build_scenario_system_prompt` 整体删除（k62 r2，控制方批准）。

    删除依据：① 全仓 **0 调用**（含 getattr / importlib.import_module / 测试 /
    脚本 / 文档示例面，实测唯一命中 = 定义行本身）；② 与 `src/bot/handler.py`
    内联组合同源常量**重复** —— 后者才是唯一实现；③ 其首形参
    `personality_prompt` 是「3 档人设」在仓库里的最后一处痕迹。

    本用例同时锁「不许把活路径的常量一起删掉」（反向事故）。
    """
    from src.llm import report_prompts

    assert not hasattr(report_prompts, "build_scenario_system_prompt"), (
        "0 调用的重复实现复活（活路径是 handler.py 内联组合，不要接第二实现）")

    names = _code_identifiers("src/llm/report_prompts.py")
    assert "personality_prompt" not in names, "personality_prompt 形参复活"
    assert "build_scenario_system_prompt" not in names, "被删函数名复活"
    for tok in ("sassy", "analyst", "gentle") + DEAD_STYLE_ATTRS:
        assert tok not in names, f"report_prompts 出现风格代码标识符 {tok}"

    # 活路径的两个常量必须仍在（handler.py:8621 在用）——防「顺手删提示词」误伤
    assert report_prompts.STRUCTURED_REPORT_PROMPT, "STRUCTURED_REPORT_PROMPT 被误删"
    assert report_prompts.SCENARIO_FOCUS_PROMPTS, "SCENARIO_FOCUS_PROMPTS 被误删"
    h = _code_identifiers("src/bot/handler.py")
    for keep in ("STRUCTURED_REPORT_PROMPT", "SCENARIO_FOCUS_PROMPTS"):
        assert keep in h, f"handler 活路径的 {keep} 被误删"
    # handler 内联是唯一实现：不得反手 import 回被删函数
    assert "build_scenario_system_prompt" not in h, \
        "handler 又接回了被删的重复实现"


def test_misnamed_personality_test_does_not_come_back():
    """名不符实的活测试不得复活：`test_different_personality_different_output`。

    k62 r2 删除（控制方指派）。它声称「不同人格模式生成不同风格建议」，实际：
    ① 三次调用逐字节相同（只有局部变量名 r_sassy/r_analyst/r_gentle 假装不同）；
    ② 它测的 `AdaptiveAdvisor.generate(bazi_result, user_context, api_key)`
       **根本没有 personality 形参** → 「传不同人设」结构上不可能；
    ③ 被 `api_key` fixture 默认 skip（实测 SKIPPED）；
    ④ 未 mock，真跑时断言命中的只是 temperature 随机性。职责现由 **k63** 的
       单一豆包口吻不变式承担。

    ⚠️ 跨批边界：本用例**只断言本文件**（`tests/test_adaptive_advisor.py`），
    **不 import / 不断言 `src/engines/advisor_v2.py`**（那是 k63 的文件，k63 正在
    改造它）。故上述 ② 作为**理由写在注释里**，不作为断言 —— 断言它会让 k62 在
    k63 改动时变红。

    本用例锁：① 该名字的用例不得复活；② 该文件里任何**声称测人设**的用例名都
    不许出现（`generate()` 没有人设参数，此类名字按构造即误导）；③ 其余用例仍在。
    """
    rel = "tests/test_adaptive_advisor.py"
    tree = ast.parse(_read(rel), filename=rel)
    test_names = {n.name for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    assert "test_different_personality_different_output" not in test_names, \
        "名不符实的人设用例复活（它不可能验证人设 —— generate() 没有该参数）"
    # ② 名字里声称测「人设」的用例：本文件全部用例都调 generate()，而它没有人设
    #    形参 → 凡名字声称测人设者按构造即误导。若将来真给人设参数并写了真用例，
    #    请连同本守卫与文件内移除说明一并更新。
    liars = sorted(n for n in test_names
                   if "personality" in n.lower() or "人格" in n)
    assert not liars, f"{rel} 出现声称测人设的用例（generate() 无人设参数）：{liars}"
    # ③ 该文件其余用例必须还在（防「顺手清空」误伤）
    assert len(test_names) >= 20, f"该文件用例数骤降到 {len(test_names)}，疑似误删"


def test_no_reference_to_deleted_report_prompt_builder():
    """仓库面（AST 标识符）：被删函数名不得在 src/ scripts/ tests/ 任何代码里出现。

    只认可执行标识符（Name/Attribute/keyword/AnnAssign 目标）—— 注释/docstring
    里解释性提到不算（k59 同款口径），本文件自身与其说明文字因此不误报。
    """
    hits = []
    for p in _py_files_under("src", "scripts", "tests"):
        try:
            names = _code_identifiers(p.relative_to(ROOT))
        except SyntaxError:
            continue
        if "build_scenario_system_prompt" in names:
            hits.append(str(p.relative_to(ROOT)))
    assert not hits, "被删函数被重新接线：\n" + "\n".join(hits)


# ====================================================================
# 5. 反向事故守卫：列不许 DROP；活代码不许碰
# ====================================================================

def test_schema_still_declares_style_columns():
    """SCHEMA 必须仍声明这三列（新建库照旧有列）—— 生产库有数据，禁 DROP。"""
    from src.storage.models import init_db, connect
    import tempfile, os

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "s.db")
        init_db(db)
        conn = connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(user_preferences)")}
        conn.close()
    for attr in DEAD_STYLE_ATTRS + ("last_style",):
        assert attr in cols, f"SCHEMA 丢了列 {attr}（k62 红线：不许 DROP COLUMN）"


def test_legacy_production_row_untouched(tmp_path):
    """**生产库存量数据不动**（k62 红线）：老行里非默认的风格权重 + last_style
    在「读 + learn()」之后必须逐字节保持；且旧行不会让新代码崩。

    这条锁的是真实用户路径：生产库里 k62 之前 learn() 已经写下过非默认值
    （如 0.2755/0.2755/0.4491）—— 只删代码路径、绝不动存量。
    （本断言改前改后都绿：它是「不许碰」守卫，不是「改前红」守卫。）
    """
    from src.storage.models import init_db, connect
    from src.storage.preference_dao import PreferenceDAO

    db = str(tmp_path / "legacy.db")
    init_db(db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO user_preferences (user_id, style_sassy, style_analyst, "
        "style_gentle, topic_wealth, topic_love, topic_career, topic_health, "
        "topic_growth, prefer_short, feedback_count, positive_count, "
        "last_style, last_topic) VALUES ('wx_legacy', 0.2755, 0.2755, 0.4491, "
        "0.2775, 0.1812, 0.1812, 0.1812, 0.1812, 0, 7, 6, 'gentle', 'wealth')")
    conn.commit()
    conn.close()

    dao = PreferenceDAO(db)
    prefs = dao.get("wx_legacy")           # 旧行读取不得崩（已不再按名读这几列）
    assert prefs.feedback_count == 7 and prefs.last_topic == "wealth"
    assert prefs.topic_wealth == pytest.approx(0.2775)
    dao.learn("wx_legacy", True, topic="career", response_len=80)

    conn = connect(db)
    row = conn.execute(
        "SELECT style_sassy, style_analyst, style_gentle, last_style, "
        "feedback_count FROM user_preferences WHERE user_id='wx_legacy'").fetchone()
    conn.close()
    assert (row[0], row[1], row[2], row[3]) == (0.2755, 0.2755, 0.4491, "gentle"), (
        f"生产库存量风格数据被改动：{(row[0], row[1], row[2], row[3])!r}")
    assert row[4] == 8, "learn() 未照常更新 feedback_count（活路径被误伤）"


def test_no_drop_column_on_user_preferences():
    """全仓不得出现针对 user_preferences 的 DROP COLUMN / DROP TABLE。

    注释行（Python `#` / SQL `--`）不扫 —— k59 同款口径：注释不是可执行写法，
    而本批**要求**在列旁注明「已废弃（k62 移除代码路径，未 DROP COLUMN）」。
    真出现破坏性迁移（`ALTER TABLE user_preferences DROP COLUMN ...`）时是
    可执行语句，必然在扫描面内。
    """
    bad = []
    for p in _py_files_under("src", "scripts"):
        text = p.read_text(encoding="utf-8")
        if "user_preferences" not in text:
            continue
        for i, line in enumerate(_strip_comments(text).splitlines(), 1):
            low = line.lower().replace('"', " ").replace("'", " ")
            if "drop column" in low or "drop table" in low:
                bad.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert not bad, "出现破坏性迁移（k62 需先报批）：\n" + "\n".join(bad)


def test_comment_scan_cannot_hide_a_real_drop():
    """机制自检：`_strip_comments` 不得把真的 DROP 语句一起吃掉（防改宽）。"""
    assert "drop column" in _strip_comments(
        'conn.execute("ALTER TABLE user_preferences DROP COLUMN style_sassy")').lower()
    assert "drop column" not in _strip_comments(
        "# 已废弃（k62 移除代码路径，未 DROP COLUMN）").lower()


def test_live_paths_untouched_no_over_deletion():
    """**不许碰**的活路径：本批禁删，改前改后都必须是绿的。

    ⚠️ 跨批边界：**`src/engines/advisor_v2.py` 的按性别人设不在此断言内** ——
    用户已拍板把它从「按性别分叉口吻」统一成单一豆包口吻，**由 k63 承担**
    （k63 未合并时若在此断言「必须保留」，两条合并后必然变红）。故本文件
    对 advisor_v2 **不作任何断言**（既不要求保留、也不替 k63 断言其目标状态）。
    详见报告「跨批边界说明」。
    """
    # 1) 深夜陪伴夜间语气（handler.py 在用）
    assert (ROOT / "src/bot/night_persona.py").exists(), "night_persona.py 被误删（活代码）"
    assert "night_persona" in _read("src/bot/handler.py"), \
        "handler 不再引用 night_persona（活路径被摘）"
    # 2) ML 质量预测器（E4 活代码）
    qp = _read("src/ml/quality_predictor.py")
    assert "PERSONALITY_MAP" in qp, "quality_predictor.PERSONALITY_MAP 被误删（活代码）"


def test_emoji_cleanup_cases_preserved():
    """emoji 收敛（k10/k47 活功能）用例必须保留 —— k62 只许摘死模块那部分。"""
    rel = "tests/test_emoji_cleanup.py"
    text = _read(rel)
    classes = {n.name for n in ast.walk(
        ast.parse(text, filename=rel)) if isinstance(n, ast.ClassDef)}
    for cls in ("TestStripEmoji", "TestFaceReader", "TestPalmReader",
                "TestAdvisorV2", "TestIntentClassifier", "TestCalendar",
                "TestJianQuote", "TestQueryEnhancer"):
        assert cls in classes, f"{cls} 被误删 —— 它验的是活功能的 emoji 清理"
    assert text.count("assert_no_emoji") >= 10, "emoji 断言被削减"
    # 已拆除的两例不得复活（按**类名**判定，docstring 里说明性提到不算）
    for gone in ("TestMoodDetector", "TestEmotionSoother"):
        assert gone not in classes, f"{rel} 的 {gone} 复活（死模块用例）"
    assert not [h for h in [_dynamic_import_targets(ROOT / rel)]
                if any(s in h for s in REMOVED_MODULE_STEMS)], \
        f"{rel} 重新 import 死模块"


# ====================================================================
# 6. 扫描面下限（只许升不许降）
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
    assert len(files) >= MIN_SCANNED_FILES, (
        f"扫描总数 {len(files)} < 下限 {MIN_SCANNED_FILES}（不得改窄）")
