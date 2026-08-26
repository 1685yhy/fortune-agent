# 易理推理内核 阶段4：多体系合成层（共识/分歧）实施计划

> For agentic workers: 本计划由子代理按 TDD 执行。每个 Task 完成后勾选 `- [ ]`。
> 纪律：只新增文件，只允许修改明确列出的本工程文件；每任务一个 commit；验证留证据。

## Goal

阶段3 后，八字/紫微/六爻/奇门/六壬五个体系各自都能产出推演链。本阶段补上设计文档架构第⑤层**多体系合成**：把多个体系的结论放一起，输出**共识点 / 分歧点 / 不可比较说明**，两说并存、各带出处（专业做法，非挑一弃一），最终合成报告。

## Architecture

- **新写** `src/engine/synth.py`：合成层核心（SynthResult + synthesize）
- **只读复用**：`src/engine/deduction.py`（DeductionChain，各体系产物）、`src/engine/report.py`（报告生成扩展）、五个体系规则库（阶段1-3 产物）
- **核心原则（沿用设计文档第7章）**：只做**可判定的**共识/分歧（跨体系可比较的事实断言），无法比较的如实列入"不可比较"；**绝不硬造共识**

## Tech Stack

- pytest（项目 venv：/mnt/e/fortune-agent/.venv/bin/python）；标准库 json

## Global Constraints

1. 分支 **engine-v1**；只新增：`src/engine/synth.py`、`src/engine/cases/synth_cases.jsonl`、`tests/test_engine_synth.py`；只允许修改：`src/engine/report.py`、`tests/test_engine_report.py`、`src/engine/coverage.py`（phase4 段，Task 4）
2. **零修改其他现有文件**（含五个体系规则库、deduction.py、e2e_eval.py）
3. 复用接口钉死：`deduce(pills, engine_result, question, system)`（T6 产物）、`compose_report(chain, question, llm, evidences, chart_str)`（阶段2 产物）
4. 中文注释；提交 message `feat(engine): ...`；不 add logs/audit.log 与旧未跟踪 plan；**不 push 远程**
5. 冒烟门控沿用：LLM 冒烟受 key 门控，失败只记录原因不硬过

## File Structure

```
src/engine/synth.py            # 合成层（Task 1）
src/engine/cases/synth_cases.jsonl  # 合成考卷（Task 3）
src/engine/report.py           # 多体系报告扩展（Task 2，允许修改）
tests/test_engine_synth.py     # 合成层测试（Task 1/3）
tests/test_engine_report.py    # 报告扩展测试（Task 2，允许修改）
```

---

## Task 1：合成层核心（src/engine/synth.py）

**Files:**
- Create: `src/engine/synth.py`、`tests/test_engine_synth.py`

**Interfaces:**
```python
@dataclass
class SystemResult:
    system: str              # "bazi"/"ziwei"/"liuyao"/"qimen"/"liuren"
    chain: object            # DeductionChain（该体系推演链）
    analysis: str            # LLM 综合输出（可空）
    citations: list          # 出处列表（可空）

@dataclass
class SynthResult:
    systems: list[str]                # 参与体系名列表
    consensus: list[dict]             # 共识：[{"point": "...", "systems": ["bazi","ziwei"], "evidence": [...]}]
    divergences: list[dict]           # 分歧：[{"topic": "...", "views": [{"system":..., "view":..., "source":...}], "note": "两说并存"}]
    unresolved: list[str]             # 不可比较说明（如实，不硬造）

def synthesize(results: list[SystemResult], llm=None) -> SynthResult
```

**字段语义：**
- 每个 SystemResult.chain 的步骤中提取**事实性要点**（规则名 `xx.` 前缀步骤的 output 字段，如"格局：正官格""三传：子亥戌（重审课）"）
- **共识判定（确定性）**：跨体系要点按**可比较键**归一化后匹配——本阶段支持两个可比较键：
  1. `五行`：各体系要点的五行倾向（八字用神五行 / 紫微五行局 / 奇门局五行 / 六壬三传五行 / 六爻用事五行）——一致 → 共识，标注参与体系与各自出处
  2. `时间`：各体系的时间断言（八字大运/流年、紫微大限、六壬三传时机）——同一年份/时段一致 → 共识
- **分歧判定（确定性）**：同一可比较键上各体系给出**不同**断言 → 分歧，两说并存各带 source；`note` 固定"两说并存，各带出处，由用户结合实际情况权衡"
- **不可比较**：无公共可比较键的体系组合 → 列入 unresolved，说明"体系间无公共比较维度（如六爻问事 vs 八字命盘），不硬造共识"
- 产出**不做解释性断语**；LLM 参数预留（多体系报告段可在 Task 2 接入）

**TDD 步骤：**
1. 写失败测试：含可手算断言——a) 两体系五行一致 → 共识含该点；b) 两体系五行不同 → 分歧含两说；c) 六爻（无五行可比较）vs 八字 → unresolved 说明；d) 空输入 → 空结果不崩
2. 红 → 实现 synth.py → 绿（`pytest tests/test_engine_synth.py -v`）
3. 提交：`feat(engine): 合成层核心——共识/分歧/不可比较三分类+数据结构(阶段4)`

- [x] Task 1 完成

---

## Task 2：多体系报告集成（src/engine/report.py 扩展）

**Files:**
- Modify: `src/engine/report.py`、`tests/test_engine_report.py`（允许）
- Create: 无

**行为：**
- 新增 `compose_multi_report(results: list[SystemResult], synth: SynthResult, llm=None) -> str`（或等价接口，保持 compose_report 原有签名行为不变）
- 输出结构（markdown 文本）：各体系节（体系名 + 推演链摘要 + LLM 分析）→ 共识节（每条：共识点 + 参与体系 + 证据）→ 分歧节（每条：话题 + 两说各自出处 + 固定说明）→ 不可比较节（如实列表）
- 保持 compose_report（单体系）行为与阶段2/3 完全一致（回归钉死）

**TDD：** 红 → 实现 → 绿（`pytest tests/test_engine_report.py -v`）；提交 `feat(engine): 多体系合成报告——各体系节+共识/分歧/不可比较节(阶段4)`

- [x] Task 2 完成

---

## Task 3：合成考卷（synth_cases.jsonl）+ e2e 合成案例

**Files:**
- Create: `src/engine/cases/synth_cases.jsonl`、`tests/test_engine_synth_cases.py`（或并入 test_engine_synth.py）

**内容（≥8 条，全部人工核实）：**
- 共识样例 3 条（五行一致 / 时间一致 / 多体系一致）
- 分歧样例 3 条（五行不同 / 时间不同 / 两说并存）
- 不可比较样例 2 条（六爻 vs 八字 / 缺体系）
- expected 键直接对应 SynthResult 三分类；audit 注明合成口径与体系来源

**e2e 合成案例**：在 e2e_phase3_cases.jsonl 基础上（不改它），测试内用两个真实体系链（如 ziwei + qimen 同一生日）跑 synthesize，断言三分类节非空、无硬造共识。

**TDD：** 红 → 实现（考卷）→ 绿；提交 `feat(engine): 合成考卷——共识/分歧/不可比较三类样例+真实链合成验证(阶段4)`

- [x] Task 3 完成

---

## Task 4：门禁收官

**门禁内容（全部执行并留证据）：**
1. 全量回归：`pytest tests/test_engine_*.py tests/test_liuren.py -v`（防死锁 env：export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 KMP_BLOCKTIME=0 TOKENIZERS_PARALLELISM=false）全绿
2. 合成考卷 ≥8 条全过
3. 真实冒烟（门控）：同一命例跑两体系真实排盘+推演链 → synthesize → 三分类非空；LLM 冒烟（key 有则真实调 compose_multi_report 一次，失败只记录原因）
4. `coverage.py` 追加 phase4 段（synth 规则数/考卷数/gate 摘要，沿用 phase3 模式，保证再生成不丢历史段）；生成 src/engine/out/coverage.json
5. 生成 `src/engine/out/gate_report_phase4.md`（结果表/冒烟行/降级说明）
6. 计划文档勾选 Task 1-4 + 文末自审记录四段
7. 提交（三个）：`feat(engine): 阶段4收官——coverage phase4+门禁产物(阶段4)`（coverage.py+out/）、`docs(engine): 阶段4计划勾选+自审记录(阶段4)`（plan 文档）；不 push

**自审记录**：规格覆盖 / 诚实边界（不硬造共识）/ 类型一致性 / 依赖。

- [x] Task 4 完成

---

## 自审记录（2026-08-16 阶段4 收官，Task 4 填写）

**规格覆盖**：Task 1 合成层核心（SystemResult/SynthResult + synthesize 三分类 + extract_facts，
可比较键五行/时间、共识含参与体系与 evidence、分歧两说并存各带 source + 固定 note、
无公共键如实入 unresolved、空输入不崩）全部落地；Task 2 compose_multi_report 五节结构
（各体系→共识→分歧→不可比较→LLM 综合解读）落地，compose_report 原行为 3 项回归钉死不破；
Task 3 合成考卷 8/8（共识 3/分歧 3/不可比较 2，人工核实，audit 注明口径与体系来源）+ e2e
真实体系链合成 2/2；Task 4 全量回归 138/138 全绿、coverage phase4 段（再生成含 phase2+3 历史
不丢）、gate_report_phase4.md 生成、计划勾选完成。冒烟与测试结果均为真实执行，无伪造。

**诚实边界（不硬造共识）**：共识仅在两体系规范值相同且 ≥2 体系参与时产生，evidence 全部来自
真实链步骤 output/fact 与出处；真实冒烟中八字用神癸水与紫微水二局同为水是真实数据而非构造。
分歧 note 固定"两说并存，各带出处，由用户结合实际情况权衡"，不挑一弃一。无公共可比较键的
体系对（含空链成员）如实入不可比较并注明各自键清单/无事实要点；六壬三传时机因当前推演链无
三传应期步骤而未纳入时间键，如实记录于门禁报告降级说明。LLM 冒烟 key 门控：有 key 真实调用
成功（4864 字符），失败路径（BoomLLM 测试）只记录原因。

**类型一致性**：SystemResult.chain 为 DeductionChain（可空步），SynthResult 三分类字段与计划
接口逐字一致（systems:list[str] / consensus:list[dict] 含 point/systems/evidence /
divergences:list[dict] 含 topic/views/note / unresolved:list[str]）；extract_facts 返回
dict 列表统一 {"key","value","text","source"}，key 为空串表示普通要点不参与比较；考卷
expected 直接对应 SynthResult 三分类，测试逐项精确匹配（含 note 固定文案）。新文件仅
src/engine/synth.py / src/engine/cases/synth_cases.jsonl / tests/test_engine_synth.py，
修改仅 src/engine/report.py / tests/test_engine_report.py / src/engine/coverage.py /
本计划文档，其余零改动（deduction.py、五体系规则库、e2e_eval.py 未动）。

**依赖**：合成层只读复用 deduction.py（DeductionChain/DeductionStep）、report.py
（compose_report 签名不变）、五体系规则库（analyze 要点格式）；新增依赖仅标准库
（dataclasses/json/re/collections.Counter）。测试依赖真实排盘引擎（BaziEngine/
ZiweiEngine/LiuyaoEngine/QimenEngine）与 lunar-python，与阶段3 口径一致；测试 env
（OMP_NUM_THREADS=1 等）防死锁。未引入任何新第三方包。
