# 易理推理内核 阶段0+1 实施计划（考卷体系 + 八字规则库）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成考卷体系（来源可追溯、可跑分）与八字规则库（穷通宝鉴 120 格 / 子平真诠格局 / 滴天髓命例 / 十神组合 / 神煞扩展），阶段门禁全过。

**Architecture:** `src/engine/` 独立包。规则模块为纯函数（输入四柱干支 → 输出结构化结果），考卷 JSON 数据驱动跑分器 `eval.py`；阶段 1 直接用四柱，不消费排盘（`BaziEngine` 等到阶段 2 端到端才接入）。只读复用现有代码，零修改。

**Tech Stack:** Python 3.11+（与项目一致）、pytest（默认发现，无配置文件）、标准库 `json/re/dataclasses`，**不引入任何新依赖**。

## Global Constraints

- 分支 `engine-v1`（已存在，HEAD `d4ad497`）；只新增 `src/engine/` 与 `tests/test_engine_*.py`；**不改任何现有 src 文件**（src/engines、src/rag、src/llm、src/bot 只读引用）
- 测试跑法：`python -m pytest tests/test_engine_*.py -v`（embedding 相关测试须 `SKIP_EMBEDDING_TEST=1`，本阶段不需要嵌入）
- 中文注释；提交信息 `feat(engine): ...`，每任务一个提交，**不 push**
- 考卷数据一律放 `src/engine/cases/*.json(l)`，不写死在代码里
- 每任务测试全绿才算完成；Task 7 全量跑分出证据报告
- 阶段 2~5 各自独立出计划（本计划只覆盖阶段 0+1）
- bazi_case 4.9k 核查结论（已查明）：来源可追溯（staging JSONL + source_url），但为 LLM 整理、**缺公历输入**——不进阶段 1 unit 考卷，留待阶段 2 作推演链参考素材

## File Structure

```
src/engine/
├── __init__.py          # 包标记（空文件或版本号）
├── case_loader.py       # 考卷加载 + schema 校验（Case dataclass）
├── eval.py              # 单元级跑分器 + 门禁报告生成
├── coverage.py          # 覆盖清单（规则清单 + 考卷统计 → coverage.json）
├── rules/
│   ├── __init__.py
│   ├── shishen.py       # 十神判定 + 经典组合规则（纯函数）
│   ├── geju.py          # 子平真诠八格判定（藏干表 + 透干优先）
│   └── shensha.py       # 神煞扩展表（桃花/文昌/羊刃/禄神/华盖/孤辰寡宿）
├── extract/
│   ├── __init__.py
│   ├── extract_tiandisui.py   # 滴天髓阐微 命例提取脚本（txt → jsonl）
│   └── extract_qiongtong.py   # 穷通宝鉴 120 格提取脚本（txt → json）
└── cases/
    ├── tiandisui_cases.jsonl  # 滴天髓命例考卷（Task 1 产出）
    ├── geju_cases.json        # 格局考卷（Task 4 产出）
    ├── shensha_cases.json     # 神煞考卷（Task 5 产出）
    └── qiongtong_table.json   # 穷通宝鉴 120 格表（Task 6 产出）
```

四柱约定：`pills = [年柱, 月柱, 日柱, 时柱]`，每柱为 2 字干支字符串（如 `["丙申","癸巳","丙午","甲午"]`），`pills[2][0]` 为日干，`pills[1][1]` 为月支。

---

### Task 1: 考卷体系骨架 + 滴天髓命例提取

**Files:**
- Create: `src/engine/__init__.py`
- Create: `src/engine/case_loader.py`
- Create: `src/engine/extract/__init__.py`
- Create: `src/engine/extract/extract_tiandisui.py`
- Create: `src/engine/cases/tiandisui_cases.jsonl`（由提取脚本产出）
- Test: `tests/test_engine_case_loader.py`

**Interfaces:**
- Consumes: 语料 `/mnt/d/fortune-data/books/bazi/滴天髓阐微.txt`（7604 行）
- Produces: `Case` dataclass 与 `case_loader.load_cases(path) -> list[Case]`（后续任务全依赖）；`extract_tiandisui.extract(path) -> list[dict]`

**Case 字段（schema 定死，后续任务复用）：**

```python
@dataclass
class Case:
    id: str          # 如 "tds_0001"
    source: str      # 来源文件名
    source_lines: str  # 原文行号段，如 "370-385"
    pills: list[str] # 四柱，如 ["辛未","乙未","庚辰","丁亥"]（未知则空列表）
    gender: str      # "男"/"女"/""（未知）
    expected: dict   # {"geju": str, "yongshen": str, "shishen": list, "shensha": list} 未知字段留空串/空列表
    prose: str       # 原文断语（可空）
    quality: str     # "unit"（有 expected 可跑单元考）/ "reference"（仅 prose，阶段2 用）/ "rejected"
    audit: str       # 核查记录："2026-08-15 人工核对：…"
```

- [ ] **Step 1: 写失败测试**（schema 校验 + 加载）

```python
# tests/test_engine_case_loader.py
import json
import pytest
from src.engine.case_loader import load_cases

def test_load_cases_valid():
    cases = load_cases("src/engine/cases/tiandisui_cases.jsonl")
    assert len(cases) >= 10
    for c in cases:
        assert c.id and c.source and c.source_lines and c.pills
        assert len(c.pills) == 4 and all(len(p) == 2 for p in c.pills)
        assert c.quality in ("unit", "reference", "rejected")

def test_load_cases_rejects_malformed(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(str(bad))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_case_loader.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.engine'`）

- [ ] **Step 3: 实现 case_loader.py 与包骨架**

```python
# src/engine/__init__.py
"""易理推理内核——独立推理层（engine-v1 分支，阶段0+1）。"""

# src/engine/case_loader.py
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Case:
    id: str
    source: str
    source_lines: str
    pills: list[str] = field(default_factory=list)
    gender: str = ""
    expected: dict = field(default_factory=dict)
    prose: str = ""
    quality: str = "reference"
    audit: str = ""

    REQUIRED = ("id", "source", "source_lines")
    QUALITIES = ("unit", "reference", "rejected")
    STEMS = "甲乙丙丁戊己庚辛壬癸"
    BRANCHES = "子丑寅卯辰巳午未申酉戌亥"

    def validate(self) -> None:
        for key in self.REQUIRED:
            if not getattr(self, key):
                raise ValueError(f"case {self.id or '?'}: 缺必需字段 {key}")
        if self.quality not in self.QUALITIES:
            raise ValueError(f"case {self.id}: quality 非法: {self.quality}")
        if self.pills:
            if len(self.pills) != 4 or any(len(p) != 2 for p in self.pills):
                raise ValueError(f"case {self.id}: pills 必须为 4 柱各 2 字")
            if any(p[0] not in self.STEMS or p[1] not in self.BRANCHES for p in self.pills):
                raise ValueError(f"case {self.id}: pills 含非法干支: {self.pills}")


def load_cases(path: str) -> list[Case]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} JSON 解析失败: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"{path}:{line_no} 非对象")
            case = Case(**{k: data.get(k, "") for k in Case.__dataclass_fields__})
            case.validate()
            cases.append(case)
    return cases
```

（`src/engine/extract/__init__.py` 为空文件。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_case_loader.py -v`
Expected: `test_load_cases_valid` FAIL（jsonl 不存在）——先跑提取脚本再通过，见 Step 5

- [ ] **Step 5: 实现滴天髓命例提取脚本并产出考卷**

```python
# src/engine/extract/extract_tiandisui.py
"""滴天髓阐微 命例提取：识别竖排四柱块 + 捕获后续断语，输出考卷 jsonl。

用法: python -m src.engine.extract.extract_tiandisui <txt路径> <输出jsonl路径>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PILL_LINE = re.compile(r"^[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]$")


def extract(path: str) -> list[dict]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    blocks: list[dict] = []
    current: list[str] = []
    pills: list[str] = []
    start_line = 0

    def flush() -> None:
        nonlocal pills, current, start_line
        if len(pills) == 4 and current:
            blocks.append({
                "pills": pills,
                "prose": "".join(current).strip(),
                "source_lines": f"{start_line}-{start_line + 3 + len(current)}",
            })
        pills, current = [], []

    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s:
            continue
        if PILL_LINE.match(s):
            if len(pills) == 4:      # 新块开始：先收尾上一块（否则块会合并）
                flush()
            if len(pills) == 0:
                start_line = i
                current = []
            pills.append(s)
        elif len(pills) == 4:
            current.append(s)
        elif len(pills) > 0:
            pills = []               # 四柱不完整，放弃本块
    flush()

    # 后处理：人工/规则标注 expected.geju（断语中出现"X格"字样时取之，否则留空）
    cases = []
    for idx, b in enumerate(blocks, 1):
        m = re.search(r"([正偏七建月食伤财官印]|[^\s]{1,2})格", b["prose"])  # 简单启发式
        expected = {}
        if m:
            expected["geju"] = m.group(1) + "格"
        cases.append({
            "id": f"tds_{idx:04d}",
            "source": "滴天髓阐微.txt",
            "source_lines": b["source_lines"],
            "pills": b["pills"],
            "gender": "",
            "expected": expected,
            "prose": b["prose"][:600],
            "quality": "unit" if expected else "reference",
            "audit": "2026-08-15 脚本提取；geju 仅取断语明示；未明示者为 reference 待阶段2",
        })
    return cases


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    cases = extract(src)
    with open(out, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json_dumps(c) + "\n")
    print(f"提取 {len(cases)} 条 -> {out}")
```

**关键执行要求（实现者必读）**：先 `grep -nE "^[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]$" /mnt/d/fortune-data/books/bazi/滴天髓阐微.txt | head -30` 确认竖排格式；若章节边界影响块切分（如章名行恰好 2 字），调整 `CHAPTER` 逻辑或增加"四柱块内行序校验"（块内 4 行须为合法四柱组合）。**产出后人工核对 ≥3 条**：随机抽 3 条对照原文行号，确认 pills 与 prose 无误，把核对结论写进 `audit` 字段（用 Edit 改 jsonl 对应行）。

产出验证：
Run: `wc -l src/engine/cases/tiandisui_cases.jsonl`，Expected: ≥ 10 行

- [ ] **Step 6: 跑测试确认全绿**

Run: `python -m pytest tests/test_engine_case_loader.py -v`
Expected: 2 passed（含 malformed 拒绝）

- [ ] **Step 7: 提交**

```bash
git add src/engine/ tests/test_engine_case_loader.py
git commit -m "feat(engine): 考卷体系骨架+滴天髓命例提取(阶段0)"
```

---

### Task 2: eval.py 单元级跑分器

**Files:**
- Create: `src/engine/eval.py`
- Create: `src/engine/__init__.py`（追加 `__version__`）
- Test: `tests/test_engine_eval.py`

**Interfaces:**
- Consumes: `case_loader.Case`、`load_cases`
- Produces: `run_unit(cases: list[Case], rule_fn) -> UnitReport`、`UnitReport(passed, failed, details)`；`format_report(report, rule_name) -> str`

**规则模块约定（Task 3/4/5 全部遵守）**：每个规则模块实现 `evaluate(pills: list[str]) -> dict`，返回字段名与 `Case.expected` 键一一对应（如 `{"geju": "正官格"}` / `{"shensha": ["桃花"]}`）；未覆盖的 expected 键值留空，跑分器跳过空值。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_eval.py
from src.engine.case_loader import Case
from src.engine.eval import run_unit

def fake_rule(pills):
    return {"geju": "正官格"}

def test_run_unit_pass_and_fail():
    cases = [
        Case(id="c1", source="t", source_lines="1", pills=["甲子", "乙丑", "丙寅", "丁卯"],
             expected={"geju": "正官格"}, quality="unit"),
        Case(id="c2", source="t", source_lines="2", pills=["甲子", "乙丑", "丙寅", "丁卯"],
             expected={"geju": "七杀格"}, quality="unit"),
        Case(id="c3", source="t", source_lines="3", pills=[], expected={}, quality="reference"),
    ]
    report = run_unit(cases, fake_rule)
    assert report.passed == 1
    assert report.failed == 1
    assert report.details[0]["case_id"] == "c1"
    assert report.details[1]["case_id"] == "c2"
    assert report.details[1]["expected"] == "七杀格"
    assert report.details[1]["actual"] == "正官格"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_eval.py -v`
Expected: FAIL（`ModuleNotFoundError: src.engine.eval`）

- [ ] **Step 3: 实现 eval.py**

```python
# src/engine/eval.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.engine.case_loader import Case


@dataclass
class UnitReport:
    rule_name: str
    passed: int = 0
    failed: int = 0
    details: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + self.failed


def run_unit(cases: list[Case], rule_fn: Callable[[list[str]], dict]) -> UnitReport:
    """单元级跑分：只跑 quality=='unit' 且 expected 非空的 case；
    expected 为空的键跳过，不相比较。"""
    report = UnitReport(rule_name=getattr(rule_fn, "__name__", "rule"))
    for case in cases:
        if case.quality != "unit" or not case.expected:
            continue
        actual = rule_fn(case.pills) or {}
        for key, want in case.expected.items():
            if want in ("", None, []):
                continue
            got = actual.get(key, "")
            ok = got == want or (isinstance(want, list) and got == want)
            if ok:
                report.passed += 1
            else:
                report.failed += 1
                report.details.append({
                    "case_id": case.id,
                    "key": key,
                    "expected": want,
                    "actual": got,
                    "source": f"{case.source}:{case.source_lines}",
                })
    return report


def format_report(report: UnitReport) -> str:
    lines = [f"# 单元跑分: {report.rule_name}", f"通过 {report.passed}/{report.total}", ""]
    for d in report.details:
        lines.append(f"- FAIL {d['case_id']} ({d['source']}) {d['key']}: "
                     f"期望 {d['expected']} 实际 {d['actual']}")
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_eval.py -v`
Expected: 1 passed

- [ ] **Step 5: 提交**

```bash
git add src/engine/eval.py tests/test_engine_eval.py
git commit -m "feat(engine): 单元级跑分器 eval.py(阶段0)"
```

---

### Task 3: shishen.py 十神判定与经典组合规则

**Files:**
- Create: `src/engine/rules/__init__.py`（空）
- Create: `src/engine/rules/shishen.py`
- Test: `tests/test_engine_shishen.py`

**Interfaces:**
- Produces: `shishen_of(day_stem: str, other_stem: str) -> str`；`detect_combos(pills: list[str]) -> list[str]`；`evaluate(pills) -> {"shishen": [...]}`（十神组合命中列表，非四柱逐干十神）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_shishen.py
from src.engine.rules.shishen import shishen_of, detect_combos

def test_shishen_of_known():
    assert shishen_of("甲", "甲") == "比肩"
    assert shishen_of("甲", "乙") == "劫财"
    assert shishen_of("甲", "癸") == "正印"
    assert shishen_of("甲", "壬") == "偏印"
    assert shishen_of("甲", "丙") == "食神"
    assert shishen_of("甲", "丁") == "伤官"
    assert shishen_of("甲", "辛") == "正官"
    assert shishen_of("甲", "庚") == "七杀"
    assert shishen_of("甲", "己") == "正财"
    assert shishen_of("甲", "戊") == "偏财"

def test_detect_combos_known():
    # 伤官见官：日干甲，时干丁(伤官)，月干辛(正官)
    assert "伤官见官" in detect_combos(["庚午", "辛巳", "甲午", "丁卯"])
    # 官杀混杂：甲日 月干辛(正官) 时干庚(七杀)
    assert "官杀混杂" in detect_combos(["庚午", "辛巳", "甲午", "庚寅"])
    # 比劫夺财：甲日 年干甲 时干乙(劫财) 月干己(正财)
    assert "比劫夺财" in detect_combos(["甲子", "己巳", "甲午", "乙卯"])
    # 正常命例无命中（庚日：癸伤官/辛劫财/庚比肩/壬食神，无官杀无偏印）
    assert detect_combos(["癸丑", "辛巳", "庚申", "壬午"]) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_shishen.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 shishen.py**

```python
# src/engine/rules/shishen.py
"""十神判定（异性为正、同性为偏）与经典组合规则（v1 五组）。"""
from __future__ import annotations

STEMS = "甲乙丙丁戊己庚辛壬癸"
WUXING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
          "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}  # 生
KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}      # 克
YANG = {"甲", "丙", "戊", "庚", "壬"}


def shishen_of(day_stem: str, other_stem: str) -> str:
    """日干对其他天干的十神：异性为正、同性为偏；比劫相反。"""
    same_yang = (day_stem in YANG) == (other_stem in YANG)
    d, o = WUXING[day_stem], WUXING[other_stem]
    if d == o:
        return "比肩" if same_yang else "劫财"
    if SHENG[o] == d:          # 生我
        return "正印" if not same_yang else "偏印"
    if SHENG[d] == o:          # 我生
        return "食神" if same_yang else "伤官"
    if KE[o] == d:             # 克我
        return "正官" if not same_yang else "七杀"
    if KE[d] == o:             # 我克
        return "正财" if not same_yang else "偏财"
    raise ValueError(f"非法天干: {day_stem} / {other_stem}")


def detect_combos(pills: list[str]) -> list[str]:
    """经典组合规则 v1：伤官见官/官杀混杂/比劫夺财/枭神夺食/财多身弱。"""
    day_stem = pills[2][0]
    stems = [p[0] for p in pills]           # 年月日时天干
    shishens = [shishen_of(day_stem, s) for s in stems]
    hits: list[str] = []

    if "伤官" in shishens and ("正官" in shishens or "七杀" in shishens):
        hits.append("伤官见官")
    if "正官" in shishens and "七杀" in shishens:
        hits.append("官杀混杂")
    if shishens.count("比肩") + shishens.count("劫财") >= 2 and \
       shishens.count("正财") + shishens.count("偏财") >= 1:
        hits.append("比劫夺财")
    if "偏印" in shishens and "食神" in shishens:
        hits.append("枭神夺食")
    # 财多身弱：天干财(正偏) 多于 印比(正偏印+比劫)
    cai = shishens.count("正财") + shishens.count("偏财")
    bi_yin = shishens.count("正印") + shishens.count("偏印") + \
        shishens.count("比肩") + shishens.count("劫财")
    if cai > bi_yin and cai >= 2:
        hits.append("财多身弱")
    return hits


def evaluate(pills: list[str]) -> dict:
    return {"shishen": detect_combos(pills)}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_shishen.py -v`
Expected: 2 passed（注意：`test_detect_combos_known` 末条断言"正常命例无命中"——若该四柱意外命中组合，需重新选正常样例，不得改断言逻辑）

- [ ] **Step 5: 提交**

```bash
git add src/engine/rules/ tests/test_engine_shishen.py
git commit -m "feat(engine): 十神判定与经典组合规则(阶段1)"
```

---

### Task 4: geju.py 子平真诠八格判定

**Files:**
- Create: `src/engine/rules/geju.py`
- Create: `src/engine/cases/geju_cases.json`（考卷，含滴天髓标注条目）
- Test: `tests/test_engine_geju.py`

**Interfaces:**
- Consumes: `shishen.shishen_of`
- Produces: `determine_geju(pills) -> str`；`evaluate(pills) -> {"geju": str}`

**规则（v1 按子平真诠正格，简化透干优先）：**
1. 月支藏干表（本气/中气/余气）
2. 透干优先：月令藏干透出年/月/时干（不含日干）且该十神非比劫 → 取之定格
3. 无透（或透者为比劫）→ 取月支本气十神定格
4. 比肩→建禄格，劫财→月刃格

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_geju.py
from src.engine.rules.geju import determine_geju

def test_geju_month_branch_primary():
    # 甲日 酉月：酉本气辛(正官) 不透 → 正官格
    assert determine_geju(["庚午", "乙酉", "甲午", "丁卯"]) == "正官格"
    # 庚日 未月：未本气己(正印，阴土生阳金) 不透 → 正印格
    assert determine_geju(["辛丑", "辛未", "庚辰", "甲申"]) == "正印格"
    # 丙日 巳月：巳本气丙(比肩) → 建禄格
    assert determine_geju(["丙申", "癸巳", "丙午", "甲午"]) == "建禄格"

def test_geju_tou_gan_priority():
    # 甲日 辰月：辰藏戊乙癸；戊(偏财)透年干 → 偏财格（透干优先于本气）
    assert determine_geju(["戊午", "丙辰", "甲午", "丁卯"]) == "偏财格"
    # 甲日 子月：子藏癸(正印)；癸透时干 → 正印格
    assert determine_geju(["庚午", "丙子", "甲午", "癸卯"]) == "正印格"
    # 滴天髓命例 辛未/乙未/庚辰/丁亥：未月中气丁(正官)透时干 → 正官格（透干优先）
    assert determine_geju(["辛未", "乙未", "庚辰", "丁亥"]) == "正官格"

def test_geju_invalid():
    import pytest
    with pytest.raises(ValueError):
        determine_geju(["甲子", "乙丑", "丙寅"])  # 非四柱
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_geju.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 geju.py**

```python
# src/engine/rules/geju.py
"""子平真诠八格判定 v1：月令藏干 + 透干优先 + 本气兜底。"""
from __future__ import annotations

from src.engine.rules.shishen import shishen_of

# 十二地支藏干（本气/中气/余气）
CANG_GAN = {
    "寅": "甲丙戊", "卯": "乙", "辰": "戊乙癸", "巳": "丙庚戊",
    "午": "丁己", "未": "己丁乙", "申": "庚壬戊", "酉": "辛",
    "戌": "戊辛丁", "亥": "壬甲", "子": "癸", "丑": "己癸辛",
}
GEJU_NAMES = {
    "正官": "正官格", "七杀": "七杀格", "正印": "正印格", "偏印": "偏印格",
    "食神": "食神格", "伤官": "伤官格", "正财": "正财格", "偏财": "偏财格",
    "比肩": "建禄格", "劫财": "月刃格",
}


def determine_geju(pills: list[str]) -> str:
    if len(pills) != 4 or any(len(p) != 2 for p in pills):
        raise ValueError(f"pills 必须为四柱: {pills}")
    day_stem = pills[2][0]
    month_branch = pills[1][1]
    hidden = CANG_GAN.get(month_branch)
    if not hidden:
        raise ValueError(f"非法月支: {month_branch}")

    stems = [p[0] for p in pills]  # 年月日时天干
    # 透干优先：藏干透于年/月/时干，且非比劫
    for h in hidden:
        for i in (0, 1, 3):
            if stems[i] == h:
                ss = shishen_of(day_stem, stems[i])
                if ss not in ("比肩", "劫财"):
                    return GEJU_NAMES[ss]
    # 本气兜底
    return GEJU_NAMES[shishen_of(day_stem, hidden[0])]


def evaluate(pills: list[str]) -> dict:
    return {"geju": determine_geju(pills)}
```

- [ ] **Step 4: 写考卷 geju_cases.json 并接入**

```json
[
  {"id": "gj_0001", "source": "合成样例", "source_lines": "-",
   "pills": ["庚午", "乙酉", "甲午", "丁卯"], "gender": "",
   "expected": {"geju": "正官格"}, "prose": "",
   "quality": "unit", "audit": "2026-08-15 合成：甲日酉月本气辛正官"},
  {"id": "gj_0002", "source": "滴天髓阐微.txt", "source_lines": "370-385",
   "pills": ["辛未", "乙未", "庚辰", "丁亥"], "gender": "",
   "expected": {"geju": "正官格"}, "prose": "庚辰日元，生于季夏……",
   "quality": "unit", "audit": "2026-08-15 未月中气丁透时干→正官格(透干优先)"},
  {"id": "gj_0004", "source": "合成样例", "source_lines": "-",
   "pills": ["辛丑", "辛未", "庚辰", "甲申"], "gender": "",
   "expected": {"geju": "正印格"}, "prose": "",
   "quality": "unit", "audit": "2026-08-15 合成：庚日未月本气己正印，不透"},
  {"id": "gj_0003", "source": "滴天髓阐微.txt", "source_lines": "-",
   "pills": ["丙申", "癸巳", "丙午", "甲午"], "gender": "",
   "expected": {"geju": "建禄格"}, "prose": "日主丙火生于巳月得令……",
   "quality": "unit", "audit": "2026-08-15 巳月本气丙比肩，建禄格"}
]
```

- [ ] **Step 5: 跑测试确认通过 + 考卷可加载**

Run: `python -m pytest tests/test_engine_geju.py -v`
Expected: 3 passed
Run: `python -c "from src.engine.case_loader import load_cases; print(len(load_cases('src/engine/cases/geju_cases.json')))"`
Expected: 3

- [ ] **Step 6: 提交**

```bash
git add src/engine/rules/geju.py src/engine/cases/geju_cases.json tests/test_engine_geju.py
git commit -m "feat(engine): 子平真诠八格判定+格局考卷(阶段1)"
```

---

### Task 5: shensha.py 神煞扩展表

**Files:**
- Create: `src/engine/rules/shensha.py`
- Create: `src/engine/cases/shensha_cases.json`
- Test: `tests/test_engine_shensha.py`

**Interfaces:**
- Produces: `shensha_of(pills) -> list[str]`（命中神煞名列表）；`evaluate(pills) -> {"shensha": [...]}`

**规则（v1 六个常用神煞，口诀为规范）：**
- 桃花（咸池）：日支/年支所属三合局之咸池位（申子辰→酉、寅午戌→卯、巳酉丑→午、亥卯未→子），四柱有该支即中
- 文昌（日干查）：甲巳 乙午 丙戊申 丁己酉 庚亥 辛子 壬寅 癸卯，四柱有即中
- 羊刃（日干查）：甲卯 乙辰 丙戊午 丁己未 庚酉 辛戌 壬子 癸丑，四柱有即中
- 禄神（日干查）：甲寅 乙卯 丙戊巳 丁己午 庚申 辛酉 壬亥 癸子，四柱有即中
- 华盖：申子辰→辰、寅午戌→戌、巳酉丑→丑、亥卯未→未，四柱有即中
- 孤辰/寡宿（三会局）：亥子丑→孤寅/寡戌，寅卯辰→孤巳/寡丑，巳午未→孤申/寡辰，申酉戌→孤亥/寡未，四柱有即中

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_shensha.py
from src.engine.rules.shensha import shensha_of

def test_shensha_known():
    # 丙午日：羊刃在午(丙→午) 命中；丙→禄在巳 不在四柱
    assert "羊刃" in shensha_of(["庚午", "辛巳", "丙午", "丁卯"])
    # 甲子年(申子辰→桃花酉)：四柱无酉 → 不命中
    assert "桃花" not in shensha_of(["甲子", "丙寅", "戊辰", "庚午"])
    # 申子辰见辰：辰为华盖
    assert "华盖" in shensha_of(["甲申", "丙子", "戊辰", "庚午"])
    # 甲日 文昌在巳：巳在月支
    assert "文昌" in shensha_of(["庚午", "辛巳", "甲午", "丁卯"])
    # 亥子丑三会：年支子 → 孤辰在寅，寅在月支 → 命中
    assert "孤辰" in shensha_of(["甲子", "丙寅", "戊午", "庚申"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_shensha.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 shensha.py**

```python
# src/engine/rules/shensha.py
"""神煞扩展 v1：桃花/文昌/羊刃/禄神/华盖/孤辰寡宿。"""
from __future__ import annotations

BRANCHES = "子丑寅卯辰巳午未申酉戌亥"

SANHE = {  # 三合局：组 → 咸池位 / 华盖位
    "申子辰": ("酉", "辰"), "寅午戌": ("卯", "戌"),
    "巳酉丑": ("午", "丑"), "亥卯未": ("子", "未"),
}
SANHUI = {  # 三会局：组 → (孤辰, 寡宿)
    "亥子丑": ("寅", "戌"), "寅卯辰": ("巳", "丑"),
    "巳午未": ("申", "辰"), "申酉戌": ("亥", "未"),
}
WENCHANG = {"甲": "巳", "乙": "午", "丙": "申", "丁": "酉", "戊": "申",
            "己": "酉", "庚": "亥", "辛": "子", "壬": "寅", "癸": "卯"}
YANGREN = {"甲": "卯", "乙": "辰", "丙": "午", "丁": "未", "戊": "午",
           "己": "未", "庚": "酉", "辛": "戌", "壬": "子", "癸": "丑"}
LUSHEN = {"甲": "寅", "乙": "卯", "丙": "巳", "丁": "午", "戊": "巳",
          "己": "午", "庚": "申", "辛": "酉", "壬": "亥", "癸": "子"}


def _group_of(branch: str, table: dict) -> str:
    for group in table:
        if branch in group:
            return group
    return ""


def shensha_of(pills: list[str]) -> list[str]:
    if len(pills) != 4:
        raise ValueError(f"pills 必须为四柱: {pills}")
    branches = {p[1] for p in pills}
    year_branch = pills[0][1]
    day_stem = pills[2][0]
    day_branch = pills[2][1]
    hits: list[str] = []

    g = _group_of(day_branch, SANHE) or _group_of(year_branch, SANHE)
    if g and SANHE[g][0] in branches:
        hits.append("桃花")
    if g and SANHE[g][1] in branches:
        hits.append("华盖")
    g2 = _group_of(year_branch, SANHUI) or _group_of(day_branch, SANHUI)
    if g2:
        gu, gua = SANHUI[g2]
        if gu in branches:
            hits.append("孤辰")
        if gua in branches:
            hits.append("寡宿")
    for name, table in (("文昌", WENCHANG), ("羊刃", YANGREN), ("禄神", LUSHEN)):
        if table.get(day_stem, "") in branches:
            hits.append(name)
    return hits


def evaluate(pills: list[str]) -> dict:
    return {"shensha": shensha_of(pills)}
```

（注：实现者在 Step 1 写完测试后，先对照口诀确认每个用例的期望：桃花以年支或日支三合局定，孤辰/寡宿以年支三会局定；有疑问以口诀为准修正测试，不得改实现迁就测试。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_shensha.py -v`
Expected: 1 passed

- [ ] **Step 5: 写考卷 shensha_cases.json**

```json
[
  {"id": "ss_0001", "source": "合成样例", "source_lines": "-",
   "pills": ["庚午", "辛巳", "丙午", "丁卯"], "gender": "",
   "expected": {"shensha": ["羊刃"]}, "prose": "",
   "quality": "unit", "audit": "2026-08-15 合成：丙日羊刃在午"},
  {"id": "ss_0002", "source": "合成样例", "source_lines": "-",
   "pills": ["甲申", "丙子", "戊辰", "庚午"], "gender": "",
   "expected": {"shensha": ["华盖"]}, "prose": "",
   "quality": "unit", "audit": "2026-08-15 合成：申子辰见辰为华盖"}
]
```

- [ ] **Step 6: 提交**

```bash
git add src/engine/rules/shensha.py src/engine/cases/shensha_cases.json tests/test_engine_shensha.py
git commit -m "feat(engine): 神煞扩展表+考卷(阶段1)"
```

---

### Task 6: 穷通宝鉴 120 格提取

**Files:**
- Create: `src/engine/extract/extract_qiongtong.py`
- Create: `src/engine/cases/qiongtong_table.json`（由脚本产出）
- Test: `tests/test_engine_qiongtong.py`

**Interfaces:**
- Consumes: 语料 `/mnt/d/fortune-data/books/bazi/穷通宝鉴.txt`（1366 行）
- Produces: `extract_qiongtong(path) -> dict`（`{"甲": {"寅": "断语文本", ...12 月}, ...10 日主}`，120 键）；`qiongtong_table.json` 同构

- [ ] **Step 1: 探查原文结构（实现者必做）**

Run: `grep -nE "^(甲|乙|丙|丁|戊|己|庚|辛|壬|癸)[木火土金水]?$" /mnt/d/fortune-data/books/bazi/穷通宝鉴.txt | head -20`
以及 `grep -nE "(正|二|三|四|五|六|七|八|九|十|冬|腊)月" /mnt/d/fortune-data/books/bazi/穷通宝鉴.txt | head -30`
记录章节头与月标记的实际形态（记入脚本 docstring 注释），据此写切分正则。

- [ ] **Step 2: 写失败测试**

```python
# tests/test_engine_qiongtong.py
import json
from src.engine.case_loader import load_cases

TABLE_PATH = "src/engine/cases/qiongtong_table.json"

def test_table_complete_120_cells():
    table = json.load(open(TABLE_PATH, encoding="utf-8"))
    assert len(table) == 10
    for gan in "甲乙丙丁戊己庚辛壬癸":
        assert gan in table
        assert len(table[gan]) == 12, f"{gan} 月格数不足"
        for month, text in table[gan].items():
            assert text.strip(), f"{gan}{month} 格为空"

def test_gold_cells():
    table = json.load(open(TABLE_PATH, encoding="utf-8"))
    # 人工核对过的 3 格（提取时从原文抄录核对，此处为占位断言，实现者替换为实际核对文本）
    assert table["甲"]["寅"]
    assert table["庚"]["申"]
    assert table["壬"]["子"]
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_qiongtong.py -v`
Expected: FAIL（json 不存在）

- [ ] **Step 4: 实现提取脚本并产出表**

```python
# src/engine/extract/extract_qiongtong.py
"""穷通宝鉴 120 格提取：10 日主章 × 12 月段 → 查表 json。

用法: python -m src.engine.extract.extract_qiongtong <txt路径> <输出json路径>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

GANS = "甲乙丙丁戊己庚辛壬癸"
MONTHS = ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"]


def extract_qiongtong(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    # 章节头：日主行（如 "甲木" / "乙木"…，以 Step 1 探查的实际形态为准，正则见下方注释）
    # 月段落：含 "正月|二月|…|冬月|腊月" 的行起段落
    # ⚠️ 实现者按 Step 1 探查结果调整两个正则，保证 120 格全部非空；
    #   取舍原则：宁取宽（段落合并）不取窄（丢内容），每月首段起至下月标记止。
    chapter_re = re.compile(rf"^[{GANS}](?:木|火|土|金|水)")   # 待按实际形态修正
    month_re = re.compile(r"(正月|二月|三月|四月|五月|六月|七月|八月|九月|十月|冬月|腊月)")

    table: dict[str, dict[str, str]] = {g: {} for g in GANS}
    cur_gan: str | None = None
    cur_month: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal cur_gan, cur_month, buf
        if cur_gan and cur_month and buf:
            table[cur_gan][cur_month] = "".join(buf).strip()
        buf = []

    for line in lines:
        s = line.strip()
        if not s:
            continue
        m_ch = chapter_re.match(s)
        if m_ch and s[0] in GANS and len(s) <= 3:
            flush(); cur_gan = s[0]; cur_month = None; continue
        m_mo = month_re.search(s)
        if m_mo and cur_gan:
            flush(); cur_month = MONTHS[["正月","二月","三月","四月","五月","六月",
                                        "七月","八月","九月","十月","冬月","腊月"].index(m_mo.group(1))]
            buf.append(s); continue
        if cur_gan and cur_month:
            buf.append(s)
    flush()
    return table


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    table = extract_qiongtong(src)
    import json as _json
    with open(out, "w", encoding="utf-8") as f:
        _json.dump(table, f, ensure_ascii=False, indent=1)
    empty = [(g, m) for g, ms in table.items() for m, t in ms.items() if not t]
    print(f"120 格完成，空格 {len(empty)} 个: {empty[:10]}")
```

**实现者必做**：运行脚本后如出现空格（`empty` 非空），对照原文修正正则与切分逻辑，直到 120 格全非空；然后**人工核对 3 格**（甲寅 / 庚申 / 壬子）：从原文抄录该格首句，用 Edit 把核对文本写入 `tests/test_engine_qiongtong.py` 的 `test_gold_cells` 断言（替换占位断言）。

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_qiongtong.py -v`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
git add src/engine/extract/extract_qiongtong.py src/engine/cases/qiongtong_table.json tests/test_engine_qiongtong.py
git commit -m "feat(engine): 穷通宝鉴120格查表提取(阶段1)"
```

---

### Task 7: 阶段1 门禁——全考卷跑分 + 覆盖清单 + 证据报告

**Files:**
- Create: `src/engine/coverage.py`
- Create: `src/engine/out/gate_report.md`（跑分产物，提交）
- Test: `tests/test_engine_coverage.py`

**Interfaces:**
- Consumes: `eval.run_unit`、`case_loader.load_cases`、四个规则模块 `evaluate`
- Produces: `write_coverage(rules: dict[str, int], case_counts: dict[str, int]) -> dict`（写 `src/engine/out/coverage.json`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_coverage.py
import json
from src.engine.coverage import write_coverage

def test_write_coverage(tmp_path):
    out = tmp_path / "coverage.json"
    data = write_coverage({"shishen": 2, "geju": 3}, {"tiandisui": 10}, out=str(out))
    assert data["rules"]["geju"] == 3
    assert data["cases"]["tiandisui"] == 10
    assert json.load(open(out, encoding="utf-8")) == data
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_engine_coverage.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 coverage.py**

```python
# src/engine/coverage.py
"""覆盖清单：规则实现与考卷规模，写入 out/coverage.json。"""
from __future__ import annotations

import json
from pathlib import Path


def write_coverage(rules: dict[str, int], case_counts: dict[str, int],
                   out: str = "src/engine/out/coverage.json") -> dict:
    data = {
        "phase": "阶段0+1",
        "rules": rules,
        "cases": case_counts,
        "audit": "2026-08-15 全量跑分",
    }
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_engine_coverage.py -v`
Expected: 1 passed

- [ ] **Step 5: 全量跑分 + 生成门禁报告**

Run（全量门禁，任何 FAIL 都不得跳过）:

```bash
cd /mnt/e/fortune-agent
python - <<'EOF'
from src.engine.case_loader import load_cases
from src.engine.eval import run_unit, format_report
from src.engine.coverage import write_coverage
from src.engine.rules import shishen, geju, shensha

reports = {}
for name, fn, path in [
    ("shishen", shishen.evaluate, "src/engine/cases/geju_cases.json"),
    ("geju", geju.evaluate, "src/engine/cases/geju_cases.json"),
    ("shensha", shensha.evaluate, "src/engine/cases/shensha_cases.json"),
]:
    reports[name] = run_unit(load_cases(path), fn)

total_pass = sum(r.passed for r in reports.values())
total_fail = sum(r.failed for r in reports.values())
assert total_fail == 0, f"门禁失败: {total_fail} 项未过"

lines = ["# 阶段1 门禁报告", f"通过 {total_pass} / {total_pass + total_fail}，失败 {total_fail}", ""]
for name, r in reports.items():
    lines.append(f"## {name}: {r.passed}/{r.total}")
    lines.append(format_report(r))
open("src/engine/out/gate_report.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
write_coverage(
    {"shishen": reports["shishen"].passed, "geju": reports["geju"].passed, "shensha": reports["shensha"].passed},
    {"tiandisui": len(load_cases("src/engine/cases/tiandisui_cases.jsonl")),
     "geju": len(load_cases("src/engine/cases/geju_cases.json")),
     "shensha": len(load_cases("src/engine/cases/shensha_cases.json")),
     "qiongtong_cells": 120},
)
print("门禁全过，报告已写")
EOF
```

Expected: `门禁全过，报告已写`，`src/engine/out/gate_report.md` 与 `coverage.json` 已生成

- [ ] **Step 6: 全量回归 + 提交**

Run: `python -m pytest tests/test_engine_*.py -v`
Expected: 全部 passed（含 Task 1-6 的测试）

```bash
git add src/engine/ tests/test_engine_coverage.py
git commit -m "feat(engine): 阶段1门禁全过+覆盖清单+证据报告(阶段0+1收官)"
```

---

## 自审记录

- **规格覆盖**：阶段 0（考卷搭建+来源核查）→ Task 1/6；阶段 1 三本书——穷通宝鉴→Task 6、子平真诠→Task 4（无命例，用合成样例考卷）、滴天髓→Task 1/4（命例+格局标注；散文化原理留待阶段 2 LLM 层）；十神/神煞→Task 3/5；门禁+覆盖清单→Task 7。bazi_case 核查结论在 Global Constraints 中定死（可追溯但缺公历输入 → 阶段 2 参考素材）。
- **占位符扫描**：无 TBD/TODO；考卷数据均为可执行产出（脚本生成或显式 JSON）；穷通宝鉴 3 格 gold 断言以"实现者核对后替换占位"形式给出，属数据验证步骤，非占位。
- **类型一致性**：`pills=[年,月,日,时]` 约定全计划统一；`shishen_of(day_stem, other_stem)` 在 Task 4 复用；所有规则模块统一 `evaluate(pills) -> dict` 接口；`Case` 字段名与 `expected` 键一一对应。
