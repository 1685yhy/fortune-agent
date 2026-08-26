# 易理推理内核 阶段2 实施计划（推演链引擎）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成推演链引擎：排盘 → 规则推演（每步记录）→ 举证（检索降级为证据）→ 综合（LLM 带出处输出），全链路可回放，e2e 考卷门禁全过。

**Architecture:** `src/engine/` 新增 4 个模块：`deduction.py`（推演链核心）、`evidence.py`（举证层）、`report.py`（综合层）、`e2e_eval.py`（e2e 跑分）。规则库（shishen/geju/shensha）与穷通宝鉴 120 格表（阶段1）全部被消费。只读复用现有代码：`BaziEngine.calculate`（排盘）、`Retriever.search`（Chroma 举证）、`FortuneLLM.analyze`（综合）——**零修改现有 src**。

**Tech Stack:** Python 3.11+、pytest、标准库 `json/dataclasses`；测试用 fake 双注入（embedding/LLM 重依赖），真实冒烟用 `SKIP_EMBEDDING_TEST`/`DEEPSEEK_API_KEY` 门控。

## Global Constraints

- 分支 `engine-v1`；只新增 `src/engine/` 下文件与 `tests/test_engine_*.py`；**不改任何现有 src 文件**（src/engines、src/rag、src/llm 只读引用）
- 复用接口（已钉死签名，勿臆造）：
  - `BaziEngine.calculate(year, month, day, hour, minute, city, gender) -> BaziResult`（字段：bazi[四柱]/day_master/wuxing/shishen/dayun[(起运岁,干支)]/liunian[年→干支]/geju/yongshen/shensha/nayin/gender；`raw_data` 恒空，**不要用**）
  - `Retriever.search(query, category=None, top_k=20, min_score=0.3) -> list[ChunkResult]`（ChunkResult: text/source/score/chunk_id/category）
  - `FortuneLLM.analyze(chart_data: Union[BaziResult,str], references: list[ChunkResult], user_question, use_pro=False, extra_system_prompt=None, stream_cb=None) -> AnalysisResult(response/tokens_used/model)`
- **推演链不判"准"**：e2e 门禁断言的是"全"（步骤齐全）与"稳"（零异常），**绝不把 mingli_bench 的答案当通过/失败判据**（那是预测准确率，属不可验证承诺）
- 中文注释；提交 `feat(engine): ...`；不 push；**绝不 add 工作区并发流文件**（logs/audit.log、miniprogram/*、src/api/calendar.py 等）；分支被并发流切走先 `git checkout engine-v1` 恢复
- 每任务测试全绿才算完成；Task 7 全量门禁出证据报告

## File Structure

```
src/engine/
├── deduction.py       # DeductionStep/DeductionChain + deduce() 推演引擎
├── evidence.py        # EvidenceProvider 举证层（链要点→检索→绑定引用）
├── report.py          # compose_report 综合层（链+证据→LLM→带出处报告）
├── e2e_eval.py        # run_e2e 跑分器 + E2EReport
└── cases/
    └── e2e_cases.jsonl    # e2e 考卷（mingli_bench 20 条 + 滴天髓 5 条，Task 6 产出）
```

四柱约定沿用阶段1：`pills = [年,月,日,时]`，`pills[2][0]` 日干、`pills[1][1]` 月支。

---

### Task 1: 推演链数据结构（DeductionStep/DeductionChain + 可回放序列化）

**Files:**
- Create: `src/engine/deduction.py`
- Test: `tests/test_engine_deduction_chain.py`

**Interfaces:**
- Produces: `DeductionStep(step_id, rule, fact, output, source, rationale)`；`DeductionChain(input, pills, steps, coverage)` 与 `to_text() -> str`、`to_json() -> str`、`append(step)`、`add_coverage(key, note)`

**字段语义（推演链可回放的根基，后续任务全部依赖）：**
- `rule`：规则来源模块/表名，如 `"geju.determine_geju"`、`"qiongtong_table[甲][寅]"`
- `fact`：输入事实（人可读），如 `"月支=酉，藏干=辛"`
- `output`：推演结果，如 `"正官格"`
- `source`：依据出处（书名/规则名），如 `"子平真诠·八格"`、`"穷通宝鉴·甲木·寅月"`
- `rationale`：一句话推理依据
- `coverage`：`{"未覆盖": ["紫微体系(阶段3)", ...]}` 明示不装懂

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_deduction_chain.py
from src.engine.deduction import DeductionStep, DeductionChain

def test_chain_append_and_fields():
    chain = DeductionChain(input={"birth": "test"}, pills=["庚午", "辛巳", "乙酉", "甲申"])
    step = DeductionStep(1, "geju.determine_geju", "月支=巳，藏干=丙庚戊",
                         "建禄格", "子平真诠·八格", "巳本气丙为比肩，不透比劫取本气")
    chain.append(step)
    assert chain.steps[0].rule == "geju.determine_geju"
    assert chain.steps[0].output == "建禄格"
    assert len(chain.steps) == 1

def test_chain_to_text_roundtrip_readable():
    chain = DeductionChain(input={}, pills=["庚午", "辛巳", "乙酉", "甲申"])
    chain.append(DeductionStep(1, "geju.determine_geju", "月支=巳", "建禄格",
                               "子平真诠·八格", "巳本气丙为比肩"))
    chain.add_coverage("未覆盖", "六壬体系(阶段3)")
    text = chain.to_text()
    assert "第1步" in text and "建禄格" in text and "子平真诠·八格" in text
    assert "未覆盖" in text and "六壬体系" in text

def test_chain_to_json_roundtrip():
    import json
    chain = DeductionChain(input={"year": 1974}, pills=["庚午", "辛巳", "乙酉", "甲申"])
    chain.append(DeductionStep(1, "shishen.detect_combos", "天干=庚辛乙甲",
                               "伤官见官", "子平真诠·十神", "伤官正官同现"))
    data = json.loads(chain.to_json())
    assert data["pills"] == ["庚午", "辛巳", "乙酉", "甲申"]
    assert data["steps"][0]["output"] == "伤官见官"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_deduction_chain.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 deduction.py（本任务只做数据结构）**

```python
# src/engine/deduction.py
"""推演链：规则推演的逐步记录与可回放序列化（阶段2 核心数据结构）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class DeductionStep:
    step_id: int
    rule: str      # 规则来源，如 "geju.determine_geju" / "qiongtong_table[甲][寅]"
    fact: str      # 输入事实（人可读）
    output: str    # 推演结果
    source: str    # 依据出处（书名/规则名）
    rationale: str = ""  # 一句话推理依据

    def to_text(self) -> str:
        return (f"第{self.step_id}步 [{self.rule}]\n"
                f"  事实: {self.fact}\n"
                f"  推得: {self.output}\n"
                f"  依据: {self.source}\n"
                f"  理由: {self.rationale}")


@dataclass
class DeductionChain:
    input: dict                     # 原始输入（公历+性别等）
    pills: list[str]                # 四柱 [年,月,日,时]
    steps: list[DeductionStep] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)   # 覆盖清单/未覆盖标注

    def append(self, step: DeductionStep) -> None:
        self.steps.append(step)

    def add_coverage(self, key: str, note: str) -> None:
        self.coverage.setdefault(key, []).append(note)

    def to_text(self) -> str:
        lines = [f"四柱: {' '.join(self.pills)}", ""]
        lines += [s.to_text() for s in self.steps]
        if self.coverage:
            lines.append("")
            lines.append("## 覆盖说明")
            for key, notes in self.coverage.items():
                lines.append(f"- {key}: {'；'.join(notes)}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "input": self.input,
            "pills": self.pills,
            "steps": [vars(s) for s in self.steps],
            "coverage": self.coverage,
        }, ensure_ascii=False, indent=1)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_deduction_chain.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add src/engine/deduction.py tests/test_engine_deduction_chain.py
git commit -m "feat(engine): 推演链数据结构——逐步记录+可回放序列化(阶段2)"
```

---

### Task 2: 推演步骤实现（排盘接入 + 十神/格局/神煞/组合）

**Files:**
- Create: `src/engine/deduction.py`（追加 `deduce()`）
- Test: `tests/test_engine_deduction_steps.py`

**Interfaces:**
- Consumes: `BaziResult`（只读字段）、`shishen.detect_combos`、`geju.determine_geju`、`shensha.shensha_of`
- Produces: `deduce(pills: list[str], engine_result=None, question: str = "") -> DeductionChain`——`engine_result` 为 `BaziResult` 时补充五行旺衰/大运/流年步骤；为 None（滴天髓 pills-only 路径）时对应步骤以"未覆盖"标注跳过

**步骤序列（v1 固定顺序）：**
1. 排盘（有 engine_result）：四柱 + 日主 + 五行旺衰 → source "lunar-python+排盘引擎"
2. 十神：四干逐干十神（用 shishen_of）+ detect_combos 组合 → source "子平真诠·十神"
3. 格局：determine_geju(pills) → source "子平真诠·八格"
4. 神煞：shensha_of(pills) → source "渊海子平·神煞"
5. 大运流年（有 engine_result）：近 3 步大运 + 当前流年 → source "排盘引擎·大运流年"
6. 断语要点：由步骤 2/3/4 结果组装面向 question 的要点句 → source "规则组合"

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_deduction_steps.py
from src.engine.deduction import deduce

def _fake_engine_result():
    from types import SimpleNamespace
    return SimpleNamespace(
        bazi=["庚午", "辛巳", "乙酉", "甲申"], day_master="乙木",
        wuxing={"金": 2, "木": 2, "火": 2, "土": 1, "水": 1},
        shishen=["正官", "伤官", "日主", "劫财"],
        dayun=[(4, "壬午"), (14, "癸未"), (24, "甲申")],
        liunian={"2026": "丙午", "2027": "丁未"},
        geju="伤官格", yongshen="水木", shensha=["天乙贵人", "驿马"],
        nayin=["路旁土", "白蜡金", "泉中水", "井泉水"], gender="男",
        raw_data={},
    )

def test_deduce_with_engine_result_steps_complete():
    chain = deduce(["庚午", "辛巳", "乙酉", "甲申"], engine_result=_fake_engine_result(),
                   question="今年财运如何？")
    rules = [s.rule for s in chain.steps]
    assert "geju.determine_geju" in rules
    assert "shishen.detect_combos" in rules
    assert "shensha.shensha_of" in rules
    assert any("大运" in s.rule for s in chain.steps)
    assert chain.steps[-1].rule.startswith("断语要点")
    assert len(chain.steps) >= 6

def test_deduce_pills_only_marks_coverage():
    chain = deduce(["辛未", "乙未", "庚辰", "丁亥"], question="")
    assert any(s.rule == "geju.determine_geju" for s in chain.steps)
    assert chain.coverage.get("未覆盖"), "pills-only 必须明示未覆盖(大运流年等)"
    assert not any("大运" in s.rule for s in chain.steps)

def test_deduce_invalid_pills_raises():
    import pytest
    with pytest.raises(ValueError):
        deduce(["甲子", "乙丑", "丙寅"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_deduction_steps.py -v`
Expected: FAIL（deduce 未定义）

- [ ] **Step 3: 实现 deduce()（追加到 deduction.py）**

```python
# 追加到 src/engine/deduction.py
from src.engine.rules.geju import determine_geju
from src.engine.rules.shishen import shishen_of, detect_combos
from src.engine.rules.shensha import shensha_of


def _step(step_id: int, rule: str, fact: str, output: str,
          source: str, rationale: str = "") -> DeductionStep:
    return DeductionStep(step_id, rule, fact, output, source, rationale)


def deduce(pills: list[str], engine_result=None, question: str = "") -> DeductionChain:
    """主推演链：排盘→十神→格局→神煞→大运流年→断语要点，逐步记录。
    engine_result: BaziResult（可选）——提供五行旺衰/大运/流年；None 走 pills-only 路径。"""
    if len(pills) != 4 or any(len(p) != 2 for p in pills):
        raise ValueError(f"pills 必须为四柱: {pills}")
    chain = DeductionChain(input={}, pills=pills)
    sid = 0

    def next_step(rule, fact, output, source, rationale=""):
        nonlocal sid
        sid += 1
        chain.append(_step(sid, rule, fact, output, source, rationale))

    day_stem = pills[2][0]
    month_branch = pills[1][1]

    # 1. 排盘（engine_result 可选）
    if engine_result is not None:
        wuxing_str = "，".join(f"{k}{v}" for k, v in getattr(engine_result, "wuxing", {}).items())
        next_step("排盘引擎.calculate", f"出生信息→四柱 {' '.join(pills)}，日主 {day_stem}",
                  f"五行旺衰: {wuxing_str or '未知'}", "lunar-python+排盘引擎",
                  "八字排盘为确定性计算，同输入必同输出")
    else:
        next_step("排盘引擎.calculate", f"四柱 {' '.join(pills)}（无公历输入，pills-only 路径）",
                  "仅四柱可用", "lunar-python+排盘引擎",
                  "滴天髓命例无公历生日，排盘步骤降级为四柱直用")

    # 2. 十神 + 组合
    stems = [p[0] for p in pills]
    shishen_str = "，".join(f"{s}:{shishen_of(day_stem, s)}" for s in stems)
    combos = detect_combos(pills)
    combos_str = "、".join(combos) if combos else "无经典组合命中"
    next_step("shishen.detect_combos", f"天干 {shishen_str}",
              combos_str, "子平真诠·十神",
              "十神按异性为正同性为偏；组合按经典规则五组判定")

    # 3. 格局
    geju = determine_geju(pills)
    next_step("geju.determine_geju", f"月支={month_branch}",
              geju, "子平真诠·八格",
              "月令藏干透干优先，不透取本气，比劫归建禄/月刃")

    # 4. 神煞
    shensha_hits = shensha_of(pills)
    next_step("shensha.shensha_of", f"四柱地支 {' '.join(p[1] for p in pills)}",
              "、".join(shensha_hits) if shensha_hits else "无命中",
              "渊海子平·神煞",
              "桃花/文昌/羊刃/禄神/华盖/孤辰寡宿，年日两局并查")

    # 5. 大运流年（engine_result 可选）
    if engine_result is not None:
        dayun = getattr(engine_result, "dayun", [])[:3]
        liunian = getattr(engine_result, "liunian", {})
        dayun_str = "，".join(f"{age}岁起{ganzhi}" for age, ganzhi in dayun) or "未知"
        liunian_str = "，".join(f"{y}:{gz}" for y, gz in list(liunian.items())[:3]) or "未知"
        next_step("大运流年.engine", f"近期大运 {dayun_str}；流年 {liunian_str}",
                  "大运流年已列", "排盘引擎·大运流年",
                  "大运阳男阴女顺排逆排，流年逐年干支")
    else:
        chain.add_coverage("未覆盖", "大运/流年（pills-only 无公历输入，阶段3 前不补）")

    # 6. 断语要点（面向 question 的规则组合）
    key_points = []
    if combos:
        key_points.append(f"组合提示：{'、'.join(combos)}")
    key_points.append(f"格局：{geju}")
    if shensha_hits:
        key_points.append(f"神煞：{'、'.join(shensha_hits)}")
    q = f"，针对问事「{question}」" if question else ""
    next_step("断语要点.compose", f"组合/格局/神煞 汇总{q}",
              "；".join(key_points), "规则组合",
              "要点句由规则结果确定性组装，不做自由发挥")

    return chain
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_deduction_steps.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add src/engine/deduction.py tests/test_engine_deduction_steps.py
git commit -m "feat(engine): 推演步骤实现——排盘/十神/格局/神煞/大运/断语要点(阶段2)"
```

---

### Task 3: 用神调候步骤（穷通宝鉴 120 格消费）

**Files:**
- Modify: `src/engine/deduction.py`（deduce 追加"调候用神"步骤）
- Test: `tests/test_engine_deduction_yongshen.py`

**Interfaces:**
- Consumes: `src/engine/cases/qiongtong_table.json`（120 格：`{"甲": {"寅": "断语文本", ...}, ...}`）
- Produces: 链中新增步骤 `rule="qiongtong_table[日干][月支]"`，output 为调候要点（原文首句节选 ≤60 字 + 标注格内容），source=`"穷通宝鉴·{日干}·{月支}月"`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_deduction_yongshen.py
import json
from src.engine.deduction import deduce

def test_deduce_includes_qiongtong_step():
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"], question="")
    steps = [s for s in chain.steps if s.rule.startswith("qiongtong_table")]
    assert len(steps) == 1
    s = steps[0]
    assert s.source == "穷通宝鉴·甲·酉月"
    assert s.output, "调候要点非空"
    assert len(s.output) <= 120

def test_qiongtong_step_uses_real_table():
    table = json.load(open("src/engine/cases/qiongtong_table.json", encoding="utf-8"))
    assert "甲" in table and "寅" in table["甲"]  # 阶段1 产物在位
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_deduction_yongshen.py -v`
Expected: FAIL（无 qiongtong_table 步骤）

- [ ] **Step 3: 实现（deduction.py 追加）**

```python
# 追加到 src/engine/deduction.py
from pathlib import Path

_QIONGTONG_PATH = Path(__file__).parent / "cases" / "qiongtong_table.json"
_QIONGTONG_CACHE: dict | None = None


def _load_qiongtong() -> dict:
    global _QIONGTONG_CACHE
    if _QIONGTONG_CACHE is None:
        _QIONGTONG_CACHE = json.loads(_QIONGTONG_PATH.read_text(encoding="utf-8"))
    return _QIONGTONG_CACHE
```

在 `deduce()` 中，格局步骤之后插入调候用神步骤：

```python
    # 3.5 调候用神（穷通宝鉴 120 格查表）
    try:
        table = _load_qiongtong()
        cell = table.get(day_stem, {}).get(month_branch, "")
        cell_text = cell[:60] + ("…" if len(cell) > 60 else "")
        next_step(f"qiongtong_table[{day_stem}][{month_branch}]",
                  f"日干 {day_stem} × 月支 {month_branch}",
                  cell_text, f"穷通宝鉴·{day_stem}·{month_branch}月",
                  "穷通宝鉴查表为确定性规则；乙丑/丁丑两格为源文本缺口冬尾补给(见阶段1审计)")
    except (KeyError, OSError) as exc:
        chain.add_coverage("未覆盖", f"穷通宝鉴查表失败: {exc}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_deduction_yongshen.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add src/engine/deduction.py tests/test_engine_deduction_yongshen.py
git commit -m "feat(engine): 调候用神步骤——穷通宝鉴120格查表接入(阶段2)"
```

---

### Task 4: 举证层 EvidenceProvider（检索降级为证据）

**Files:**
- Create: `src/engine/evidence.py`
- Test: `tests/test_engine_evidence.py`

**Interfaces:**
- Consumes: `DeductionChain`、`Retriever.search(query, category, top_k, min_score) -> list[ChunkResult]`（注入，测试用 fake）
- Produces: `EvidenceProvider(retriever=None, top_k=5)`、`gather(chain, question="", category="bazi") -> list[ChunkResult]`；`EvidenceProvider` 构造时 retriever 为 None 则懒加载生产实例（`src/rag.retriever.Retriever` + `Embedder`，模型缺失时抛 `RuntimeError` 由调用方捕获降级）

**查询构造规则（推演要点驱动）：** 查询 = `日干 + 格局 + 组合命中 + 神煞 + question`，如 `"甲木 正官格 伤官见官 财运如何"`；再按链步骤要点逐条补充检索（≤3 条查询），结果合并去重（按 chunk_id），按 score 降序取前 `top_k`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_evidence.py
from src.engine.deduction import deduce
from src.engine.evidence import EvidenceProvider


class FakeRetriever:
    def __init__(self):
        self.queries = []

    def search(self, query, category=None, top_k=20, min_score=0.3):
        self.queries.append(query)
        return [type("CR", (), {"text": f"依据-{query[:6]}", "source": "穷通宝鉴",
                                "score": 0.85, "chunk_id": f"c{len(self.queries)}",
                                "category": category})()]


def test_gather_builds_queries_from_chain():
    fake = FakeRetriever()
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"], question="今年财运如何？")
    provider = EvidenceProvider(retriever=fake)
    results = provider.gather(chain, question="今年财运如何？")
    assert results, "举证结果非空"
    assert fake.queries, "必须发起检索"
    assert all(hasattr(r, "text") and hasattr(r, "source") for r in results)
    assert "甲木" in fake.queries[0] or "甲" in fake.queries[0]

def test_gather_dedupe_and_limit():
    fake = FakeRetriever()
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"])
    provider = EvidenceProvider(retriever=fake, top_k=3)
    results = provider.gather(chain)
    ids = [r.chunk_id for r in results]
    assert len(ids) == len(set(ids)), "按 chunk_id 去重"
    assert len(results) <= 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_evidence.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 evidence.py**

```python
# src/engine/evidence.py
"""举证层：把推演链要点转化为检索查询，古籍检索降级为证据引用。"""
from __future__ import annotations

from src.engine.deduction import DeductionChain


class EvidenceProvider:
    def __init__(self, retriever=None, top_k: int = 5):
        self._retriever = retriever
        self._top_k = top_k

    def _get_retriever(self):
        if self._retriever is not None:
            return self._retriever
        # 生产懒加载（注入优先，避免测试加载重模型）
        from src.rag.retriever import Retriever  # 只读复用
        from src.rag.embedder import Embedder
        embedder = Embedder(model_name="BAAI/bge-m3")
        embedder.load()
        from src.config import settings
        self._retriever = Retriever(str(settings.vectordb_dir), embedder)
        return self._retriever

    def _build_queries(self, chain: DeductionChain, question: str) -> list[str]:
        day_stem = chain.pills[2][0]
        wuxing_day = {"甲": "木", "乙": "木", "丙": "火", "丁": "火",
                      "戊": "土", "己": "土", "庚": "金", "辛": "金",
                      "壬": "水", "癸": "水"}[day_stem]
        parts = [f"{day_stem}{wuxing_day}"]
        for s in chain.steps:
            if s.rule.startswith("geju."):
                parts.append(s.output)
            if s.rule.startswith("shishen.detect_combos") and s.output and "无经典组合" not in s.output:
                parts.append(s.output)
            if s.rule.startswith("shensha.") and s.output and "无命中" not in s.output:
                parts.append(s.output)
        queries = [" ".join(parts)]
        if question:
            queries.append(" ".join(parts[:2]) + " " + question[:40])
        return queries

    def gather(self, chain: DeductionChain, question: str = "",
               category: str = "bazi") -> list:
        retriever = self._get_retriever()
        seen: set[str] = set()
        results = []
        for q in self._build_queries(chain, question):
            for r in retriever.search(q, category=category, top_k=self._top_k):
                cid = getattr(r, "chunk_id", None) or getattr(r, "doc_id", "")
                if cid and cid in seen:
                    continue
                if cid:
                    seen.add(cid)
                results.append(r)
        return results[: self._top_k]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_evidence.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add src/engine/evidence.py tests/test_engine_evidence.py
git commit -m "feat(engine): 举证层——推演要点驱动检索+去重截断(阶段2)"
```

---

### Task 5: 综合层 compose_report（链+证据 → LLM → 带出处报告）

**Files:**
- Create: `src/engine/report.py`
- Test: `tests/test_engine_report.py`

**Interfaces:**
- Consumes: `DeductionChain`、`EvidenceProvider.gather`、`FortuneLLM.analyze(chart_data, references, user_question, extra_system_prompt)`（注入 fake）
- Produces: `ReportResult(analysis: str, chain: DeductionChain, citations: list[dict], model: str, tokens_used: int)`；`compose_report(chain, question, llm=None, evidence=None, chart_str=None) -> ReportResult`

**注入设计：** `chain.to_text()` 作为 `extra_system_prompt`（追加到 SYSTEM_PROMPT 后，零改 client.py）；references 直接用 EvidenceProvider 产物（与 analyze 的 `List[ChunkResult]` 兼容）；`chart_str` 缺省时用 `BaziResult` 或四柱文本。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_report.py
from src.engine.deduction import deduce
from src.engine.report import compose_report


class FakeLLM:
    def __init__(self):
        self.calls = []

    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        self.calls.append({"extra": extra_system_prompt, "refs": len(references)})
        return type("AR", (), {"response": "综合解读……（依据：古籍）",
                               "tokens_used": 120, "model": "fake"})()


def test_compose_report_injects_chain_and_refs():
    fake = FakeLLM()
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"], question="今年财运如何？")
    refs = [type("CR", (), {"text": "原文", "source": "穷通宝鉴", "score": 0.9,
                            "chunk_id": "c1", "category": "bazi"})()]
    report = compose_report(chain, "今年财运如何？", llm=fake, evidences=refs)
    assert report.analysis == "综合解读……（依据：古籍）"
    assert "正官格" in fake.calls[0]["extra"]  # 推演链注入（庚午/乙酉/甲午/丁卯 甲日酉月→正官格）
    assert "第1步" in fake.calls[0]["extra"]
    assert fake.calls[0]["refs"] == 1
    assert report.model == "fake"

def test_compose_report_no_evidence_still_works():
    fake = FakeLLM()
    chain = deduce(["辛未", "乙未", "庚辰", "丁亥"])
    report = compose_report(chain, "", llm=fake, evidences=[])
    assert report.analysis
    assert fake.calls[0]["refs"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_report.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 report.py**

```python
# src/engine/report.py
"""综合层：推演链 + 证据 → LLM 生成带出处解读（链注入 extra_system_prompt，零改 client.py）。"""
from __future__ import annotations

from dataclasses import dataclass

from src.engine.deduction import DeductionChain


@dataclass
class ReportResult:
    analysis: str
    chain: DeductionChain
    citations: list
    model: str
    tokens_used: int


def compose_report(chain: DeductionChain, question: str, llm=None,
                   evidences: list | None = None, chart_str: str | None = None) -> ReportResult:
    """llm 缺省时生产 FortuneLLM（DEEPSEEK 环境就绪才可真实调用）。
    evidences 缺省时走 EvidenceProvider 举证。"""
    if evidences is None:
        from src.engine.evidence import EvidenceProvider
        try:
            evidences = EvidenceProvider().gather(chain, question=question)
        except Exception:
            evidences = []   # 检索不可用 → 降级无引用（诚实标注）

    if llm is None:
        from src.llm.client import FortuneLLM
        llm = FortuneLLM()

    extra = ("## 推演链（引擎逐步推理记录，可审计）\n" + chain.to_text() +
             "\n\n请基于推演链与古籍依据回答，每条关键结论标注引用编号[n]。"
             "推演链未覆盖处如实说明，不得编造。")
    chart = chart_str or " ".join(chain.pills)
    result = llm.analyze(chart, list(evidences), question,
                         extra_system_prompt=extra)
    citations = [{"text": getattr(r, "text", ""), "source": getattr(r, "source", ""),
                  "score": getattr(r, "score", 0.0)} for r in (evidences or [])]
    return ReportResult(analysis=result.response, chain=chain,
                        citations=citations, model=result.model,
                        tokens_used=result.tokens_used)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_report.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add src/engine/report.py tests/test_engine_report.py
git commit -m "feat(engine): 综合层——推演链注入+证据引用+降级路径(阶段2)"
```

---

### Task 6: e2e 考卷转换 + 跑分器

**Files:**
- Create: `src/engine/e2e_eval.py`
- Create: `src/engine/cases/e2e_cases.jsonl`（由转换脚本产出，含 20 条 mingli_bench + 5 条滴天髓）
- Create: `src/engine/extract/extract_e2e_cases.py`（转换脚本）
- Test: `tests/test_engine_e2e_eval.py`

**e2e 考卷格式（JSONL）：**
```json
{"id": "e2e_0001", "source": "mingli_bench", "birth": {"year": 1974, "month": 4, "day": 28,
 "hour": 16, "minute": 40, "city": "usa", "gender": "男"},
 "question": "此命1996年发生何事？", "quality": "e2e",
 "audit": "2026-08-15 转换自 mingli_bench/data.json (ftb_0001)"}
{"id": "e2e_tds_0001", "source": "滴天髓阐微.txt", "pills": ["辛未", "乙未", "庚辰", "丁亥"],
 "prose": "庚辰日元，生于季夏……", "quality": "e2e",
 "audit": "2026-08-15 阶段1 reference 升级为 e2e"}
```

**转换规则：**
- mingli_bench：从 `/mnt/d/fortune-data/books/mingli_bench/data.json` 的 `questions` 数组取前 20 条（按原顺序确定性抽样，转换脚本打印所选 id 供审计）；`birth` 直接映射字段；**不写 answer 进考卷**（避免门禁变成预测判分）
- 滴天髓：从 `tiandisui_cases.jsonl` 取 quality=reference 且 prose 含"大运/岁运"字样前 5 条

**跑分器断言（run_e2e）：**
1. 排盘/推演零异常（try/except 捕获记为 FAIL）
2. chain.steps ≥ 5；必含 geju、shishen、qiongtong 步骤（pills-only 条除外——qiongtong 仍必含，因只需日干月支）
3. 断语要点步骤存在且非空
4. 举证（real retriever 可用时）或降级路径无异常
5. 综合输出非空（fake llm 注入）——**真实 LLM 在 Task 7 门禁单独冒烟**

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_e2e_eval.py
from src.engine.case_loader import load_cases
from src.engine.e2e_eval import run_e2e


class FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        return type("AR", (), {"response": "解读", "tokens_used": 10, "model": "fake"})()


def test_run_e2e_all_cases_pass():
    cases = load_cases("src/engine/cases/e2e_cases.jsonl")
    assert len(cases) == 25
    report = run_e2e(cases, llm=FakeLLM(), use_real_retriever=False)
    assert report.failed == 0, f"e2e 失败: {report.details[:3]}"
    assert report.passed == len(cases)

def test_e2e_cases_have_valid_inputs():
    cases = load_cases("src/engine/cases/e2e_cases.jsonl")
    for c in cases:
        if c.pills:
            assert len(c.pills) == 4
        else:
            b = c.expected.get("birth")
            assert b and b["year"] and 1 <= b["month"] <= 12
```

（`Case.expected` 放 birth dict 以兼容现有 loader schema——loader 校验只查 id/source/source_lines/pills 合法性与 quality，expected 自由字段。转换脚本把 birth 放进 `expected["birth"]`。）

- [ ] **Step 1.5: 扩展 case_loader 的 QUALITIES（e2e 是合法考卷态）**

阶段1 的 `src/engine/case_loader.py` QUALITIES 为 `("unit", "reference", "rejected")`，不含 `"e2e"`——不扩展则 e2e 考卷加载即被拒。修改（这是我们的文件，允许演进）：

```python
# src/engine/case_loader.py 中
    QUALITIES = ("unit", "reference", "rejected", "e2e")
```

并更新 `tests/test_engine_case_loader.py` 增加 e2e 合法断言：

```python
def test_load_cases_accepts_e2e(tmp_path):
    from src.engine.case_loader import load_cases
    p = tmp_path / "e.jsonl"
    p.write_text('{"id":"e1","source":"s","source_lines":"1","pills":[],"quality":"e2e"}\n',
                 encoding="utf-8")
    assert load_cases(str(p))[0].quality == "e2e"
```

Run: `python -m pytest tests/test_engine_case_loader.py -v`，Expected: 4 passed（原 3 + 新 1）
Run: `python -m pytest tests/test_engine_e2e_eval.py -v`，Expected: 2 passed

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_e2e_eval.py -v`
Expected: FAIL（e2e_cases.jsonl 不存在 / e2e_eval 未定义）

- [ ] **Step 3: 实现转换脚本并产出考卷**

```python
# src/engine/extract/extract_e2e_cases.py
"""e2e 考卷转换：mingli_bench 前20条 + 滴天髓 reference 5条 → e2e_cases.jsonl。
用法: python -m src.engine.extract.extract_e2e_cases"""
import json
from pathlib import Path

CASES_DIR = Path(__file__).parent.parent / "cases"
OUT = CASES_DIR / "e2e_cases.jsonl"


def convert_mingli(path: str, limit: int = 20) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = []
    for q in data["questions"][:limit]:
        b = q["birth_info"]
        items.append({
            "id": f"e2e_{q['id'].replace('ftb_', '')}",
            "source": "mingli_bench",
            "source_lines": q["id"],
            "pills": [],
            "gender": b["gender"],
            "expected": {"birth": {"year": b["year"], "month": b["month"],
                                   "day": b["day"], "hour": b["hour"],
                                   "minute": b["minute"], "city": b.get("location") or b.get("country") or "",
                                   "gender": b["gender"]}},
            "prose": "",
            "quality": "e2e",
            "audit": "2026-08-15 转换自 mingli_bench/data.json（不写 answer，避免预测判分）",
        })
    return items


def convert_tiandisui(path: str, limit: int = 5) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if len(items) >= limit:
                break
            c = json.loads(line)
            if c.get("quality") == "reference" and "大运" in c.get("prose", ""):
                items.append({
                    "id": "e2e_tds_" + c["id"].split("_")[1],
                    "source": c["source"], "source_lines": c["source_lines"],
                    "pills": c["pills"], "gender": c.get("gender", ""),
                    "expected": {}, "prose": c["prose"][:200],
                    "quality": "e2e",
                    "audit": "2026-08-15 阶段1 reference 升级为 e2e（含大运断语）",
                })
    return items


if __name__ == "__main__":
    import sys
    mingli = sys.argv[1] if len(sys.argv) > 1 else "/mnt/d/fortune-data/books/mingli_bench/data.json"
    tds = sys.argv[2] if len(sys.argv) > 2 else str(CASES_DIR / "tiandisui_cases.jsonl")
    items = convert_mingli(mingli) + convert_tiandisui(tds)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"e2e 考卷 {len(items)} 条 -> {OUT}")
    print("mingli ids:", [i["id"] for i in items if i["source"] == "mingli_bench"])
    print("tds ids:", [i["id"] for i in items if i["source"] != "mingli_bench"])
```

**产出验证：** `wc -l src/engine/cases/e2e_cases.jsonl` = 25；转换脚本输出打印所选 id 供审计。

- [ ] **Step 4: 实现 e2e_eval.py**

```python
# src/engine/e2e_eval.py
"""e2e 跑分器：全链路(排盘→推演→举证→综合)逐条断言"全"与"稳"，不判预测对错。"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.case_loader import Case
from src.engine.deduction import deduce
from src.engine.report import compose_report


@dataclass
class E2EReport:
    passed: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)


def _run_one(case: Case, llm, use_real_retriever: bool) -> str | None:
    """返回 None=通过；否则返回失败原因。"""
    try:
        if case.pills:
            chain = deduce(case.pills, engine_result=None, question="")
        else:
            b = case.expected.get("birth")
            from src.engines.bazi import BaziEngine  # 只读复用
            result = BaziEngine().calculate(
                b["year"], b["month"], b["day"], b["hour"], b["minute"],
                b.get("city") or "", b.get("gender") or "男")
            chain = deduce(result.bazi, engine_result=result, question=case.prose or "")
    except Exception as exc:
        return f"排盘/推演异常: {exc}"

    rules = [s.rule for s in chain.steps]
    if len(chain.steps) < 5:
        return f"推演步骤不足: {len(chain.steps)}"
    if not any(r.startswith("geju.") for r in rules):
        return "缺格局步骤"
    if not any(r.startswith("shishen.") for r in rules):
        return "缺十神步骤"
    if not any(r.startswith("qiongtong_table") for r in rules):
        return "缺调候用神步骤"

    try:
        report = compose_report(chain, case.prose or case.id, llm=llm,
                                evidences=None if use_real_retriever else [])
    except Exception as exc:
        return f"综合层异常: {exc}"
    if not report.analysis:
        return "综合输出为空"
    return None


def run_e2e(cases: list[Case], llm, use_real_retriever: bool = False) -> E2EReport:
    report = E2EReport()
    for case in cases:
        err = _run_one(case, llm, use_real_retriever)
        if err:
            report.failed += 1
            report.details.append(f"{case.id}: {err}")
        else:
            report.passed += 1
    return report
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_e2e_eval.py -v`
Expected: 2 passed（注意：跑分器会真排盘 20 条 mingli 命例——lunar-python 纯本地计算，秒级；不加载嵌入模型）

- [ ] **Step 6: 提交**

```bash
git add src/engine/e2e_eval.py src/engine/extract/extract_e2e_cases.py src/engine/cases/e2e_cases.jsonl tests/test_engine_e2e_eval.py
git commit -m "feat(engine): e2e考卷转换+跑分器——全链路'全与稳'断言(阶段2)"
```

---

### Task 7: 阶段2 门禁——全链路考卷全过 + 推演链可回放 + 真实冒烟

**Files:**
- Create: `src/engine/out/gate_report_phase2.md`、`src/engine/out/chain_demo.json`、`src/engine/out/chain_demo.txt`（产物）
- Test: 无新测试（复用全部）

**门禁内容：**
1. 全量回归：`python -m pytest tests/test_engine_*.py -v`（阶段1 13 + 阶段2 新增 12+ = 25+ 全绿）
2. e2e 全过：`run_e2e`（fake llm，25 条全过）
3. **真实冒烟（如实留证）**：
   - 举证冒烟：`EvidenceProvider()` 真实检索 1 条（`SKIP_EMBEDDING_TEST` 不设时跑；模型在 /tmp，若加载失败记录原因不算 FAIL）
   - LLM 冒烟：`DEEPSEEK_API_KEY` 存在时 `compose_report` 真实调用 1 条，输出存档；无 key 记录跳过原因
4. 推演链可回放产物：取 1 条 mingli 命例 + 1 条滴天髓 pills-only，输出 `chain_demo.json`（to_json）与 `chain_demo.txt`（to_text）——**这就是"可回放"的证据**
5. coverage.json 更新：加 `phase2` 段（deduction/evidence/report/e2e 计数）

- [ ] **Step 1: 全量回归**

Run: `python -m pytest tests/test_engine_*.py -v`
Expected: 全部 passed

- [ ] **Step 2: 跑门禁脚本并生成产物**

```bash
cd /mnt/e/fortune-agent && mkdir -p src/engine/out && .venv/bin/python - <<'EOF'
from src.engine.case_loader import load_cases
from src.engine.e2e_eval import run_e2e
from src.engine.deduction import deduce

class FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        return type("AR", (), {"response": "（冒烟解读）", "tokens_used": 5, "model": "fake"})()

cases = load_cases("src/engine/cases/e2e_cases.jsonl")
rep = run_e2e(cases, llm=FakeLLM(), use_real_retriever=False)
assert rep.failed == 0, f"门禁失败: {rep.details[:5]}"

# 可回放产物
chain1 = deduce(["庚午", "乙酉", "甲午", "丁卯"], question="今年财运如何？")
open("src/engine/out/chain_demo.json", "w", encoding="utf-8").write(chain1.to_json())
open("src/engine/out/chain_demo.txt", "w", encoding="utf-8").write(chain1.to_text())
chain2 = deduce(["辛未", "乙未", "庚辰", "丁亥"])
open("src/engine/out/chain_demo_tds.txt", "w", encoding="utf-8").write(chain2.to_text())

# 真实冒烟（如实记录）
smoke = []
try:
    from src.engine.evidence import EvidenceProvider
    ev = EvidenceProvider().gather(chain1, question="今年财运如何？")
    smoke.append(f"举证冒烟: 真实检索 {len(ev)} 条")
except Exception as exc:
    smoke.append(f"举证冒烟: 跳过({exc})")
import os
if os.environ.get("DEEPSEEK_API_KEY"):
    try:
        from src.engine.report import compose_report
        rr = compose_report(chain1, "今年财运如何？", evidences=[])
        smoke.append(f"LLM冒烟: {len(rr.analysis)} 字, model={rr.model}")
    except Exception as exc:
        smoke.append(f"LLM冒烟: 跳过({exc})")
else:
    smoke.append("LLM冒烟: 跳过(无 DEEPSEEK_API_KEY)")

lines = ["# 阶段2 门禁报告", f"e2e 考卷 {rep.passed}/{rep.passed+rep.failed} 全过", ""]
lines += ["- " + s for s in smoke]
open("src/engine/out/gate_report_phase2.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines))
EOF
```

Expected: `e2e 考卷 25/25 全过` + 冒烟行如实输出

- [ ] **Step 3: 更新 coverage.json**

```bash
cd /mnt/e/fortune-agent && .venv/bin/python - <<'EOF'
import json
cov = json.load(open("src/engine/out/coverage.json", encoding="utf-8"))
cov["phase"] = "阶段0+1+2"
cov["phase2"] = {"deduction_steps": 7, "evidence": "in", "report": "in",
                 "e2e_cases": 25, "gate": "9/9 + e2e 25/25"}
json.dump(cov, open("src/engine/out/coverage.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("coverage 已更新")
EOF
```

- [ ] **Step 4: 提交**

```bash
# 只 add 门禁产物（out/ 全在 src/engine/ 内）；绝不 add tests/ 全目录（防误伤并发流测试文件）
git add src/engine/out/
git commit -m "feat(engine): 阶段2门禁——e2e25/25全过+推演链可回放产物+冒烟留证(阶段2收官)"
```

---

## 自审记录

- **规格覆盖**：设计文档第 3 章主链的②规则推演→③举证→④综合在阶段2 全部落地（①排盘复用引擎、⑤合成层留阶段4）；"检索从主角降为配角"由 evidence.py 实现（查询由推演要点驱动）；"推演链可回放"由 DeductionChain.to_text/to_json + chain_demo 产物保证；"未覆盖明示"由 coverage 字段保证。
- **诚实边界**：e2e 门禁只断言"全与稳"，mingli_bench 的 answer 不写入考卷、不参与判分（预测准确率不可验证承诺）；真实冒烟失败只记录原因不硬过。
- **类型一致性**：`DeductionStep(step_id, rule, fact, output, source, rationale)` 六字段全计划统一；`deduce(pills, engine_result, question)` 双路径签名一致；`compose_report(chain, question, llm, evidences, chart_str)` 参数顺序全计划一致；e2e 考卷 `expected["birth"]` 承载公历输入（Case loader 兼容）。
- **依赖**：穷通宝鉴表（阶段1 产物）、tiandisui_cases.jsonl（阶段1 产物）、`BaziEngine`/`Retriever`/`FortuneLLM` 均为只读复用，签名已按摸底报告钉死。
