# 易理推理内核 阶段5 实施计划（对比报告：检索式 vs 引擎式）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 同一批真实案例，双管线（现有检索式基线 vs 引擎式全链路）并排输出，生成可读对比报告，供用户亲眼验收"差别在哪"——这是合回 main 的钥匙（规格第 8 章：阶段 5 对比报告用户验收通过 + 用户拍板）。

**Architecture:** `baseline.py` 复刻生产主链路（排盘 → `retriever.search(f"{日主} {问题}", category="bazi", top_k=15)` → `llm.analyze`——handler.py:2930-3001 经摸底确认就是这三步，用相同调用参数复刻，不耦合 bot 层）；引擎式走 `deduce → evidence.gather → compose_report`（阶段2 产物）。跑批脚本逐案例双跑并存档，报告生成器输出 markdown。

**Tech Stack:** Python 3.11+、pytest、标准库 `json/dataclasses`；LLM 真实调用走 `settings.claude_api_key`（.env），无 key 如实降级（链+证据部分仍产出）。

## Global Constraints

- 分支 `engine-v1`；只新增 `src/engine/` 下文件与 `tests/test_engine_*.py`；**不改任何生产文件**（handler.py 只读参考）
- 基线复刻（与生产一致的调用参数，勿臆造）：
  - 排盘：`BaziEngine().calculate(year, month, day, hour, minute, city, gender)`
  - 检索：`retriever.search(f"{result.day_master} {question}", category="bazi", top_k=15)`（retriever 走 `EMBEDDING_COLLECTION` 环境，空库守卫同 evidence.py 模式）
  - 综合：`llm.analyze(result, refs, question)`（无 extra_system_prompt——这就是"检索式"的本质差异点）
  - `FortuneLLM` 构造按 main.py:764 先例：`FortuneLLM(api_key=settings.claude_api_key, model="deepseek-flash", deep_model="deepseek-v4-pro", provider="deepseek")`（用 `load_settings()`，不用模块级 settings）
- **诚实边界**：对比报告**不做"准"判分**（预测准确率不可验证）；评估维度 = 推理过程/依据出处/具体度/稳定性/覆盖面，人读不自动打分
- 案例：mingli e2e 考卷中选 4 条代表（事业/财运/婚姻/健康各 1，按 e2e_cases.jsonl 顺序确定性取）+ 滴天髓 pills-only 1 条（e2e_tds_0001 起）
- 中文注释/报告；提交 `feat(engine): ...`；不 push；**绝不 add 工作区并发流文件**；分支被并发流切走先确认再 `git checkout engine-v1`
- 每任务测试全绿才算完成；Task 5 全量回归 + 产物完整性断言

## File Structure

```
src/engine/
├── baseline.py            # 检索式基线管线封装（注入模式，测试用 fake）
├── select_cases.py        # 案例选择 → comparison_cases.json
├── run_comparison.py      # 跑批：双管线逐案例 → comparison_runs.jsonl
├── build_report.py        # 报告生成 → out/comparison_report.md
└── cases/
    └── comparison_cases.json    # 5 条对比案例（Task 2 产出）
```

---

### Task 1: baseline.py 检索式基线管线

**Files:**
- Create: `src/engine/baseline.py`
- Test: `tests/test_engine_baseline.py`

**Interfaces:**
- Consumes: `BaziEngine.calculate`、`Retriever.search`、`FortuneLLM.analyze`（全部注入）
- Produces: `BaselinePipeline(engine=None, retriever=None, llm=None)`、`run(birth: dict, question: str) -> BaselineResult`；`BaselineResult(chart: dict, refs: list, analysis: str, model: str, tokens_used: int, query: str)`
- 懒加载：retriever 为 None 时按 evidence.py 同款空库守卫模式加载（`EMBEDDING_COLLECTION` 未设/指向空库抛 RuntimeError）；llm 为 None 时按 main.py:764 先例构造

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_baseline.py
from src.engine.baseline import BaselinePipeline


class FakeEngine:
    def calculate(self, year, month, day, hour, minute, city, gender):
        return type("R", (), {"day_master": "甲木", "bazi": ["庚午", "乙酉", "甲午", "丁卯"]})()


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def search(self, query, category=None, top_k=20, min_score=0.3):
        self.calls.append((query, category, top_k))
        return [type("CR", (), {"text": "古籍原文", "source": "穷通宝鉴", "score": 0.8,
                                "chunk_id": "c1", "category": "bazi"})()]


class FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        assert extra_system_prompt is None, "基线不得注入推演链"
        return type("AR", (), {"response": "基线回答", "tokens_used": 100, "model": "fake"})()


def test_baseline_runs_with_injected_fakes():
    fake_r = FakeRetriever()
    fake_l = FakeLLM()
    pipe = BaselinePipeline(engine=FakeEngine(), retriever=fake_r, llm=fake_l)
    result = pipe.run({"year": 1974, "month": 4, "day": 28, "hour": 16, "minute": 40,
                       "city": "usa", "gender": "男"}, "今年财运如何？")
    assert result.analysis == "基线回答"
    assert fake_r.calls[0][0] == "甲木 今年财运如何？"   # 生产查询形态：日主+问题
    assert fake_r.calls[0][1] == "bazi"                  # 生产分类参数
    assert fake_r.calls[0][2] == 15                      # 生产 top_k
    assert result.query == "甲木 今年财运如何？"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_baseline.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 baseline.py**

```python
# src/engine/baseline.py
"""检索式基线管线：复刻生产主链路（排盘→检索→LLM），无推演链注入——对比报告的对照组。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class BaselineResult:
    chart: dict
    refs: list
    analysis: str
    model: str
    tokens_used: int
    query: str


class BaselinePipeline:
    def __init__(self, engine=None, retriever=None, llm=None):
        self._engine = engine
        self._retriever = retriever
        self._llm = llm

    def _get_engine(self):
        if self._engine is None:
            from src.engines.bazi import BaziEngine
            self._engine = BaziEngine()
        return self._engine

    def _get_retriever(self):
        if self._retriever is not None:
            return self._retriever
        # 与 evidence.py 同款空库守卫（EMBEDDING_COLLECTION 未设 → 指向空库 fortune_books）
        collection = os.environ.get("EMBEDDING_COLLECTION", "fortune_books")
        if collection == "fortune_books":
            raise RuntimeError(
                "EMBEDDING_COLLECTION 未设置或指向空库 fortune_books；请设为 fortune_books_v2（27115条古籍库）")
        from src.rag.retriever import Retriever
        from src.rag.embedder import Embedder
        embedder = Embedder(model_name="BAAI/bge-m3")
        embedder.load()
        from src.config import load_settings
        self._retriever = Retriever(str(load_settings().vectordb_dir), embedder)
        return self._retriever

    def _get_llm(self):
        if self._llm is None:
            from src.llm.client import FortuneLLM
            from src.config import load_settings
            s = load_settings()
            self._llm = FortuneLLM(api_key=s.claude_api_key, model="deepseek-flash",
                                   deep_model="deepseek-v4-pro", provider="deepseek")
        return self._llm

    def run(self, birth: dict, question: str) -> BaselineResult:
        """birth: {year, month, day, hour, minute, city, gender}"""
        engine = self._get_engine()
        result = engine.calculate(birth["year"], birth["month"], birth["day"],
                                  birth["hour"], birth["minute"],
                                  birth.get("city") or "", birth.get("gender") or "男")
        query = f"{result.day_master} {question}"
        refs = self._get_retriever().search(query, category="bazi", top_k=15)
        analysis = self._get_llm().analyze(result, refs, question)
        return BaselineResult(
            chart={"bazi": result.bazi, "day_master": result.day_master},
            refs=refs, analysis=analysis.response, model=analysis.model,
            tokens_used=analysis.tokens_used, query=query)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_baseline.py -v`
Expected: 1 passed

- [ ] **Step 5: 提交**

```bash
git add src/engine/baseline.py tests/test_engine_baseline.py
git commit -m "feat(engine): 检索式基线管线——复刻生产主链路(阶段5)"
```

---

### Task 2: 案例选择 comparison_cases.json

**Files:**
- Create: `src/engine/select_cases.py`
- Create: `src/engine/cases/comparison_cases.json`
- Test: `tests/test_engine_select_cases.py`

**Interfaces:**
- Consumes: `src/engine/cases/e2e_cases.jsonl`（25 条）、`tiandisui_cases.jsonl`（513 条）
- Produces: `select_cases(e2e_path, tds_path) -> list[dict]`——5 条：mingli 按 e2e 顺序取第 1/2/5/10 条（覆盖婚姻/事业/家庭/健康类分布）+ 滴天髓 pills-only 1 条（e2e_tds_0001）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_select_cases.py
from src.engine.select_cases import select_cases


def test_select_5_cases_with_expected_fields():
    cases = select_cases("src/engine/cases/e2e_cases.jsonl",
                         "src/engine/cases/tiandisui_cases.jsonl")
    assert len(cases) == 5
    mingli = [c for c in cases if c["source"] == "mingli_bench"]
    tds = [c for c in cases if c["source"] != "mingli_bench"]
    assert len(mingli) == 4 and len(tds) == 1
    for c in mingli:
        assert c["birth"]["year"] and c["question"]
    assert tds[0]["pills"] and len(tds[0]["pills"]) == 4

def test_select_is_deterministic():
    a = select_cases("src/engine/cases/e2e_cases.jsonl",
                     "src/engine/cases/tiandisui_cases.jsonl")
    b = select_cases("src/engine/cases/e2e_cases.jsonl",
                     "src/engine/cases/tiandisui_cases.jsonl")
    assert a == b
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_select_cases.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 select_cases.py 并产出**

```python
# src/engine/select_cases.py
"""对比案例选择：mingli 代表 4 条（按 e2e 顺序 1/2/5/10，覆盖类别分布）+ 滴天髓 pills-only 1 条。"""
import json
from pathlib import Path


def select_cases(e2e_path: str, tds_path: str) -> list[dict]:
    e2e_cases = [json.loads(l) for l in Path(e2e_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    mingli = [c for c in e2e_cases if c.get("source") == "mingli_bench"]
    picks = [mingli[i - 1] for i in (1, 2, 5, 10)]
    tds_all = [json.loads(l) for l in Path(tds_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    tds_pick = next((c for c in tds_all if c.get("id") == "tds_0001"), tds_all[0])
    cases = []
    for c in picks:
        cases.append({
            "id": c["id"], "source": "mingli_bench", "source_lines": c["source_lines"],
            "birth": c["expected"]["birth"], "question": c["question"] if "question" in c else "请分析此命整体运势",
            "audit": "2026-08-15 对比案例（类别代表）",
        })
    cases.append({
        "id": tds_pick["id"], "source": "滴天髓阐微.txt", "source_lines": tds_pick["source_lines"],
        "pills": tds_pick["pills"], "prose": tds_pick["prose"][:120],
        "question": "请分析此命（古籍命例）",
        "audit": "2026-08-15 对比案例（pills-only）",
    })
    return cases


if __name__ == "__main__":
    from pathlib import Path as _P
    cases = select_cases("src/engine/cases/e2e_cases.jsonl", "src/engine/cases/tiandisui_cases.jsonl")
    out = _P("src/engine/cases/comparison_cases.json")
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"对比案例 {len(cases)} 条 -> {out}")
    for c in cases:
        print(c["id"], c["source"], c.get("birth", {}).get("year", "-"))
```

（注意：e2e_cases.jsonl 的 mingli 条没有 question 字段——转换时只写了 birth。若实际缺 question，按脚本默认文案"请分析此命整体运势"回退，同时用 e2e 源数据 data.json 补 question 更佳：实现者可在脚本里读 /mnt/d/fortune-data/books/mingli_bench/data.json 的 questions 数组按 id 对应补齐 question 字段——以真实问题为准，测试断言 question 非空。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_select_cases.py -v`
Expected: 2 passed；`python -m src.engine.select_cases` 产出 5 条并打印 id

- [ ] **Step 5: 提交**

```bash
git add src/engine/select_cases.py src/engine/cases/comparison_cases.json tests/test_engine_select_cases.py
git commit -m "feat(engine): 对比案例选择——mingli代表4条+滴天髓1条(阶段5)"
```

---

### Task 3: 跑批 run_comparison.py（双管线逐案例）

**Files:**
- Create: `src/engine/run_comparison.py`
- Test: `tests/test_engine_run_comparison.py`

**Interfaces:**
- Consumes: `BaselinePipeline.run`、`deduce`、`EvidenceProvider`、`compose_report`
- Produces: `run_comparison(cases, baseline=None, engine_pipeline=None, use_real_llm=True) -> list[dict]`；每条输出 `{id, question, baseline: {analysis, refs_n, tokens, model, query}, engine: {analysis, chain_text, citations_n, tokens, model}, note}`；产物写 `src/engine/out/comparison_runs.jsonl`
- 引擎管线封装：`_run_engine(birth_or_pills, question, llm, retriever)`——birth 路径：`BaziEngine().calculate` → `deduce(pills, engine_result)` → `EvidenceProvider(retriever).gather` → `compose_report`；pills 路径：`deduce(pills)` → gather → compose_report
- LLM 门控：`use_real_llm=False` 时用 FakeLLM（测试）；True 时按 main.py 先例构造；`load_settings().claude_api_key` 为空 → 如实记录 `note="LLM不可用(无api_key)，仅产出推演链与证据"` 并跳过分析

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_run_comparison.py
import json
from src.engine.run_comparison import run_comparison


class FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        return type("AR", (), {"response": "综合解读", "tokens_used": 10, "model": "fake"})()


def test_run_comparison_fake_llm_all_cases():
    cases = json.load(open("src/engine/cases/comparison_cases.json", encoding="utf-8"))
    runs = run_comparison(cases, use_real_llm=False)
    assert len(runs) == 5
    for r in runs:
        assert r["baseline"]["analysis"] and r["engine"]["analysis"]
        assert r["engine"]["chain_text"] and "第1步" in r["engine"]["chain_text"]
        assert "note" in r
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_run_comparison.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 run_comparison.py**

```python
# src/engine/run_comparison.py
"""对比跑批：同一案例双管线（检索式基线 vs 引擎式）逐条运行并存档。"""
from __future__ import annotations

import json
from pathlib import Path


class _FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        return type("AR", (), {"response": "（测试解读）", "tokens_used": 5, "model": "fake"})()


def _get_llm(use_real: bool):
    if not use_real:
        return _FakeLLM()
    from src.config import load_settings
    key = load_settings().claude_api_key
    if not key:
        return None
    from src.llm.client import FortuneLLM
    return FortuneLLM(api_key=key, model="deepseek-flash",
                      deep_model="deepseek-v4-pro", provider="deepseek")


def _run_engine(case: dict, llm) -> dict:
    from src.engine.deduction import deduce
    from src.engine.evidence import EvidenceProvider
    from src.engine.report import compose_report
    question = case.get("question", "")
    pills = case.get("pills")
    chain = None
    if pills:
        chain = deduce(pills, engine_result=None, question=question)
    else:
        b = case["birth"]
        from src.engines.bazi import BaziEngine
        result = BaziEngine().calculate(b["year"], b["month"], b["day"], b["hour"],
                                        b["minute"], b.get("city") or "", b.get("gender") or "男")
        chain = deduce(result.bazi, engine_result=result, question=question)
    out = {"chain_text": chain.to_text(), "citations_n": 0, "analysis": "", "tokens": 0, "model": ""}
    if llm is None:
        return {**out, "note": "LLM不可用(无api_key)，仅产出推演链与证据"}
    try:
        evidences = EvidenceProvider().gather(chain, question=question)
        report = compose_report(chain, question, llm=llm, evidences=evidences)
        out.update({"citations_n": len(report.citations), "analysis": report.analysis,
                    "tokens": report.tokens_used, "model": report.model,
                    "note": "OK" if evidences else "检索无命中(如实)"})
    except Exception as exc:
        out["note"] = f"综合层降级: {exc}"
    return out


def run_comparison(cases: list[dict], use_real_llm: bool = True) -> list[dict]:
    from src.engine.baseline import BaselinePipeline
    llm = _get_llm(use_real_llm)
    baseline = BaselinePipeline(llm=llm) if llm else None
    runs = []
    for case in cases:
        run = {"id": case["id"], "source": case["source"], "question": case.get("question", "")}
        if llm is None:
            run["baseline"] = {"analysis": "", "refs_n": 0, "tokens": 0, "model": "", "query": ""}
            run["baseline_note"] = "LLM不可用(无api_key)"
        else:
            try:
                br = baseline.run(case["birth"], case.get("question", ""))
                run["baseline"] = {"analysis": br.analysis, "refs_n": len(br.refs),
                                   "tokens": br.tokens_used, "model": br.model, "query": br.query}
                run["baseline_note"] = "OK"
            except Exception as exc:
                run["baseline"] = {}
                run["baseline_note"] = f"基线降级: {exc}"
        run["engine"] = _run_engine(case, llm)
        run["note"] = run.get("baseline_note", "") + " / " + run["engine"].get("note", "")
        runs.append(run)
    Path("src/engine/out").mkdir(parents=True, exist_ok=True)
    with open("src/engine/out/comparison_runs.jsonl", "w", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return runs


if __name__ == "__main__":
    cases = json.load(open("src/engine/cases/comparison_cases.json", encoding="utf-8"))
    runs = run_comparison(cases, use_real_llm=True)
    for r in runs:
        print(r["id"], r["note"], "基线", len(r["baseline"].get("analysis", "")),
              "引擎", len(r["engine"].get("analysis", "")))
```

（注意：滴天髓 pills-only 案例的 baseline.run 需要 birth——pills-only 案例无 birth。基线对 pills-only 案例应如实降级：`run["baseline_note"]="基线需要公历出生信息，pills-only 案例不适用"`，`baseline={}`。实现者据此在循环中判断。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_run_comparison.py -v`
Expected: 1 passed（fake LLM 全 5 条跑通，含 pills-only 案例基线降级分支）

- [ ] **Step 5: 提交**

```bash
git add src/engine/run_comparison.py tests/test_engine_run_comparison.py
git commit -m "feat(engine): 对比跑批——双管线逐案例+LLM门控+存档(阶段5)"
```

---

### Task 4: 报告生成 build_report.py → comparison_report.md

**Files:**
- Create: `src/engine/build_report.py`
- Test: `tests/test_engine_build_report.py`

**Interfaces:**
- Consumes: `src/engine/out/comparison_runs.jsonl`
- Produces: `build_report(runs_path) -> str`（markdown 全文）；写 `src/engine/out/comparison_report.md`

**报告结构（人读，PM 友好）：**
1. 说明段：对比目的、诚实边界（不做"准"判分）、双管线定义
2. 每案例一节：
   - 案例信息（id/出生摘要/问题）
   - **检索式输出**（完整文本）+ 引用数 + 查询词
   - **引擎式输出**（完整文本）+ 引用数
   - **推演链**（chain_text 全文——这是引擎式的核心差异证据）
   - 评估维度表（推理过程/依据出处/具体度/稳定性/覆盖面：每维度一句话点评，人工填写模板 `- 推理过程: <说明>` 留空待用户/评审填写——或由脚本按规则输出"引擎式含N步推演链、基线无"这类客观事实行）
3. 汇总表：5 案例 × 双管线（字符数/tokens/引用数/有无推演链）
4. 已知边界节：时区语义（city 未参与计算）、出处元数据（chunk source 缺口）、pills-only 基线不适用、LLM 冒烟门控说明

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_build_report.py
from src.engine.build_report import build_report


def test_report_contains_all_cases_and_sections():
    md = build_report("src/engine/out/comparison_runs.jsonl")
    assert "## " in md and md.count("## ") >= 5          # 每案例一节
    assert "检索式输出" in md and "引擎式输出" in md and "推演链" in md
    assert "诚实边界" in md or "不判准" in md
    assert "汇总" in md
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_build_report.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 build_report.py**

```python
# src/engine/build_report.py
"""对比报告生成：comparison_runs.jsonl → comparison_report.md（人读、不判准）。"""
from __future__ import annotations

import json
from pathlib import Path


def _case_section(run: dict) -> str:
    lines = [f"## {run['id']}（{run['source']}）", "", f"**问题**: {run.get('question', '')}", ""]
    b = run.get("baseline") or {}
    b_note = run.get("baseline_note", "")
    lines.append("### 检索式输出（基线）")
    lines.append("")
    if b.get("analysis"):
        lines.append(b["analysis"])
        lines.append("")
        lines.append(f"- 查询词: `{b.get('query', '')}`；引用 {b.get('refs_n', 0)} 条；tokens {b.get('tokens', 0)}")
    else:
        lines.append(f"（{b_note}）")
    lines.append("")
    e = run["engine"]
    lines.append("### 引擎式输出（推演链+证据+综合）")
    lines.append("")
    if e.get("analysis"):
        lines.append(e["analysis"])
        lines.append("")
        lines.append(f"- 引用 {e.get('citations_n', 0)} 条；tokens {e.get('tokens', 0)}；{e.get('note', '')}")
    else:
        lines.append(f"（{e.get('note', '')}）")
    lines.append("")
    lines.append("### 引擎推演链（可回放）")
    lines.append("")
    lines.append("```")
    lines.append(e.get("chain_text", "（无）"))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def build_report(runs_path: str) -> str:
    runs = [json.loads(l) for l in Path(runs_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    lines = [
        "# 易理推理内核 对比报告：检索式 vs 引擎式",
        "",
        "> 同一案例、同一 LLM，两条管线的输出并排呈现。**本报告不做"准"的判分**",
        "> （命理断语无标准答案，预测准确率不可验证）；观察维度：推理过程、依据出处、具体度、稳定性、覆盖面。",
        "",
        "## 汇总",
        "",
        "| 案例 | 检索式 | 引擎式 | 引擎推演链 |",
        "|---|---|---|---|",
    ]
    for r in runs:
        b = r.get("baseline") or {}
        e = r["engine"]
        lines.append(f"| {r['id']} | {len(b.get('analysis', ''))}字/{b.get('refs_n', 0)}引用 | "
                     f"{len(e.get('analysis', ''))}字/{e.get('citations_n', 0)}引用 | "
                     f"{len(e.get('chain_text', ''))}字/{e.get('chain_text', '').count('第')}步 |")
    lines.append("")
    for r in runs:
        lines.append(_case_section(r))
    lines += [
        "## 已知边界",
        "",
        "- 时区语义：BaziEngine 的 city 参数未参与计算（海外命例按北京时排盘）",
        "- 出处元数据：检索 chunk 的 source 字段在库中存在缺口（部分显示为未知）",
        "- pills-only 案例（滴天髓命例）无公历出生信息，检索式基线不适用",
        "- LLM 冒烟按 DEEPSEEK_API_KEY / .env 配置门控，无 key 时仅产出推演链与证据",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    md = build_report("src/engine/out/comparison_runs.jsonl")
    out = Path("src/engine/out/comparison_report.md")
    out.write_text(md, encoding="utf-8")
    print(f"对比报告已生成 -> {out}（{len(md)} 字符）")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_build_report.py -v`
Expected: 1 passed

- [ ] **Step 5: 提交**

```bash
git add src/engine/build_report.py tests/test_engine_build_report.py
git commit -m "feat(engine): 对比报告生成器——并排输出+推演链+汇总+已知边界(阶段5)"
```

---

### Task 5: 真实跑批 + 门禁交付

**Files:**
- Create: `src/engine/out/comparison_runs.jsonl`（真实 LLM，Task 3 产物）
- Create: `src/engine/out/comparison_report.md`（Task 4 产物）
- Test: 无新测试（复用全部）

**门禁内容：**
1. 全量回归：`python -m pytest tests/test_engine_*.py -v` 全绿
2. 真实跑批：`python -m src.engine.run_comparison`（use_real_llm=True）——前置检查 `load_settings().claude_api_key` 是否可用：
   - 有 key：5 案例全跑（基线 4 条 mingli + 引擎 5 条含 pills-only 降级），产物 comparison_runs.jsonl
   - 无 key：如实记录，跑批降级为链+证据（仍产出 runs，analysis 空 + note 标注）；报告照常生成并注明
3. 真实检索：跑批中 EvidenceProvider 走真实 Chroma（EMBEDDING_COLLECTION=fortune_books_v2 需在跑批命令前 export；无则按守卫抛错并如实降级记录）
4. 报告生成：`python -m src.engine.build_report` → comparison_report.md
5. 产物完整性断言：runs 5 条、每案例引擎推演链非空、报告含 5 案例节
6. 提交：`git add src/engine/out/`（runs + report），message `feat(engine): 阶段5对比报告——双管线真实跑批+并排报告(阶段5收官)`

**注意**：真实 LLM 调用耗时较长（5 案例 × 双管线 ≈ 10 次 analyze），属预期；如网络/API 故障，按"如实记录降级"原则处理，不伪造输出。

---

## 自审记录

- **规格覆盖**：设计文档第 8 章"合回 main 的条件：阶段 5 对比报告用户验收通过"——本计划交付对比报告本身；诚实边界（不判准）贯穿 T2-T5；基线复刻参数与生产一致（T1 测试钉死查询形态/分类/top_k）；pills-only 案例的基线降级如实处理（T3 注明）。
- **占位符扫描**：评估维度表以"客观事实行"（引用数/步数/有无推演链）替代主观判分，不依赖人工填写；无 TBD。
- **类型一致性**：`BaselineResult(chart/refs/analysis/model/tokens_used/query)` 全计划统一；run_comparison 输出结构（id/source/question/baseline/baseline_note/engine/note）T3-T4 一致；案例格式（id/source/source_lines/birth|pills/question/audit）T2-T3 一致。
- **风险**：LLM 真实调用耗时与可用性——门控降级路径已在 T3/T5 定义；mingli e2e 条缺 question 字段——T2 已注明从 data.json 补全或回退默认问题。
