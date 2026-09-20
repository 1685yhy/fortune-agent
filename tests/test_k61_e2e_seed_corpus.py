# -*- coding: utf-8 -*-
"""k61 集成分支锁：e2e 的**分层种子语料**必须就地定义，且样本逐条逐序冻结。

## 为什么有这个文件

`tests/test_k61_e2e_smoke.py` 的 `LABELED_TEST_CASES` 原先是
`from test_mood_detector import LABELED_TEST_CASES` —— 而 `tests/test_mood_detector.py`
已被 k62（`2a67833`）作为死模块删除。集成分支把它**就地内联**（54 条），
并**移除**了消费它的 `test_e2e_mood_accuracy_reports_only`（被测产品模块
`src/engines/mood_detector.py` 同样已被删除）。

于是本文件锁三件事：

1. **不得改回 import**（防复发）：常量的定义必须在 e2e 文件**内**（AST 取模块级赋值），
   且 e2e 文件不得 import 两个已删模块（`mood_detector` / `emotion_soother`），
   含 `importlib.import_module(<字面量>)` / `__import__(<字面量>)` 这类动态面。
2. **语料形状冻结**：54 条、三个期望类别齐、每类条数固定。
   （条数取证：k61 r8 §8.3 曾写「24 条」，与事实源不符 —— 该常量在 `main` /
   `ea110c3` / `2a67833^` / `k61-registered-r2` 每个含它的 ref 上都是 **54** 条，
   k61 自己 `test_mood_detector.py:518` 亦写「全 54 条」。集成分支按事实源 54 内联。）
3. **抽样结果冻结**：`representative_sample()` 口径（每类前 4 条、类序
   gentle→analyst→sassy、按语料内既有顺序）抽出的 **12 条**，与内联前**逐条逐序一致**
   —— 以**字面快照**钉死（内联前的 k61 原文实测值）。换序 = 换样本，必须红。

## 与 e2e 开关的关系（重要）

本文件**故意不带** `e2e` 标记、**不看** `K61_E2E`：它是**常规门禁**的一部分。
否则「防改回 import」「防语料被换」这两件事会随着冒烟的默认 skip 一起失效。
本文件**零网络、零 LLM、零生产数据**，纯 AST + 字面量比对。
"""
from __future__ import annotations

import ast
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

#: 被锁的目标文件（语料就地在它里面）
E2E_REL = "tests/test_k61_e2e_smoke.py"
#: 常量名
CORPUS_NAME = "LABELED_TEST_CASES"
#: 已删模块的 module 名（import 面）
REMOVED_MODULE_STEMS = ("mood_detector", "emotion_soother")
#: 抽样口径（与 e2e 内联前的 `_stratified_sample` / 门禁 `representative_sample` 同款）
PER_CLASS = 4
CLASS_ORDER = ("gentle", "analyst", "sassy")

#: 内联前的**事实源**（k61 `tests/test_mood_detector.py`）实测值：
#: 54 条 → 三类 17 / 17 / 20；抽样 12 条 = 每类前 4 条。
EXPECTED_TOTAL = 54
EXPECTED_PER_CLASS = {"gentle": 17, "analyst": 17, "sassy": 20}

#: 抽样结果**字面快照**（逐条逐序；内联前实测，内联后必须逐字相同）。
EXPECTED_SAMPLE = [
    ("我好焦虑，不知道该怎么办", "gentle"),
    ("最近压力好大，晚上睡不着", "gentle"),
    ("我害怕这次考试会考砸", "gentle"),
    ("担心老公的身体，他最近总说累", "gentle"),
    ("帮我分析一下明年的财运走势", "analyst"),
    ("从命理角度分析我适合什么职业", "analyst"),
    ("我的八字里木旺不旺？和金的关系是什么", "analyst"),
    ("给我一个数据分析，我什么时候能升职", "analyst"),
    ("哈哈哈大师我的桃花运来了吗", "sassy"),
    ("今天心情超好，感觉要发财了", "sassy"),
    ("笑死，测了好几个八字都说我会发财", "sassy"),
    ("哎呀今天被夸了，开心死了", "sassy"),
]


def _e2e_source() -> str:
    return (ROOT / E2E_REL).read_text(encoding="utf-8")


def _e2e_tree() -> ast.Module:
    return ast.parse(_e2e_source(), filename=E2E_REL)


def _module_level_corpus():
    """取 e2e 文件**模块级**的 `LABELED_TEST_CASES = [...]` 字面量值。

    只认模块级 `Assign` / `AnnAssign` —— 这正是「就地定义」的判据；
    从别处 import 进来的名字不会以赋值形态出现。
    """
    for node in _e2e_tree().body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(getattr(t, "id", None) == CORPUS_NAME for t in targets):
            return ast.literal_eval(node.value)
    return None


def _stratified_sample(cases):
    by = collections.defaultdict(list)
    for case in cases:
        by[case[1]].append(case)
    sample = []
    for cls in CLASS_ORDER:
        sample.extend(by[cls][:PER_CLASS])
    return sample


# ══════════════════════════════════════════════════════════════════
# 1) 不得改回 import（防复发）
# ══════════════════════════════════════════════════════════════════

def test_corpus_is_defined_in_place_not_imported():
    """**本批核心锁**：语料必须在 e2e 文件内**就地**定义（模块级赋值）。"""
    cases = _module_level_corpus()
    assert cases is not None, (
        f"{E2E_REL} 里没有模块级的 `{CORPUS_NAME} = [...]` —— "
        f"语料被改回 import 了（它曾 `from test_mood_detector import ...`，"
        f"而该文件已随死模块被 k62 删除；import 回来 = 合并/收集即 ERROR）"
    )
    assert cases, "就地定义的语料是空的"


def test_e2e_file_imports_no_removed_module():
    """e2e 文件不得 import 两个已删模块（含动态 import 字面量）。"""
    hits = []
    for node in ast.walk(_e2e_tree()):
        if isinstance(node, ast.Import):
            for a in node.names:
                if any(s in a.name for s in REMOVED_MODULE_STEMS):
                    hits.append(f"{node.lineno}: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if any(s in mod for s in REMOVED_MODULE_STEMS):
                hits.append(f"{node.lineno}: from {mod} import ...")
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if fname in ("import_module", "__import__") and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str) \
                    and any(s in node.args[0].value for s in REMOVED_MODULE_STEMS):
                hits.append(f"{node.lineno}: {fname}({node.args[0].value!r})")
    assert not hits, (
        f"{E2E_REL} 又接线了已删模块：\n" + "\n".join(hits)
        + "\n（k62 已拆除该残留管线；`src/engines/mood_detector.py` 与 "
          "`tests/test_mood_detector.py` 均不存在）"
    )


# ══════════════════════════════════════════════════════════════════
# 2) 语料形状冻结（条数与事实源一致，不得悄悄加减）
# ══════════════════════════════════════════════════════════════════

def test_corpus_shape_matches_source_of_truth():
    """54 条 + 三条目 + 字符串 + 三类别齐 + 每类条数固定。"""
    cases = _module_level_corpus()
    assert len(cases) == EXPECTED_TOTAL, (
        f"语料条数 {len(cases)} != 事实源 {EXPECTED_TOTAL} —— "
        f"（取证：k61 r8 §8.3 的「24」是笔误；该常量在各 ref 上均为 54）"
    )
    for i, case in enumerate(cases):
        assert isinstance(case, tuple) and len(case) == 3, \
            f"第 {i + 1} 条不是 (message, expected_mood, category)：{case!r}"
        assert all(isinstance(x, str) and x for x in case), \
            f"第 {i + 1} 条含空/非字符串字段：{case!r}"
    assert {c[1] for c in cases} == set(CLASS_ORDER), \
        f"期望类别不全：{sorted({c[1] for c in cases})}"
    per = collections.Counter(c[1] for c in cases)
    assert dict(per) == EXPECTED_PER_CLASS, (
        f"每类条数变了：{dict(per)} != {EXPECTED_PER_CLASS}")
    msgs = [c[0] for c in cases]
    assert len(set(msgs)) == len(msgs), "语料出现重复消息（分层样本会随之漂移）"


# ══════════════════════════════════════════════════════════════════
# 3) 抽样结果冻结（逐条逐序，防「换序 = 换样本」）
# ══════════════════════════════════════════════════════════════════

def test_stratified_sample_is_frozen():
    """抽样 12 条必须与内联前**逐条逐序**一致（字面快照比对）。"""
    cases = _module_level_corpus()
    sample = _stratified_sample(cases)
    assert len(sample) == PER_CLASS * len(CLASS_ORDER), (
        f"样本量 {len(sample)} != {PER_CLASS * len(CLASS_ORDER)}")
    got = [(c[0], c[1]) for c in sample]
    assert got == EXPECTED_SAMPLE, (
        "分层样本与内联前不一致（逐条逐序）—— 换序/换条即换样本：\n"
        f"  期望 {EXPECTED_SAMPLE}\n  实测 {got}"
    )


def test_sample_covers_all_three_classes():
    """样本必须覆盖三类（防「全是同一类期望值」的零判别力形态）。"""
    sample = _stratified_sample(_module_level_corpus())
    assert {c[1] for c in sample} == set(CLASS_ORDER), \
        f"样本未覆盖三类：{sorted({c[1] for c in sample})}"
    assert [c[1] for c in sample] == \
        [cls for cls in CLASS_ORDER for _ in range(PER_CLASS)], \
        "样本的类别顺序不是 gentle→analyst→sassy（与门禁 representative_sample 口径不符）"
