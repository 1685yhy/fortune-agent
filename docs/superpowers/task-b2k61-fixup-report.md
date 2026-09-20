# batch2-k61 集成修复报告（k61 × k62 撞车点）

- **工作目录**：`/home/a/b2k61-wt`（分支 `batch2-k61`，起点 tip = `bdcaaa8`）
- **本次提交**：`bc852b0`(①) → `018a89e`(报告) → `9bbe203`(②③⑤) → `0fdecef`(④)
- **红线遵守**：只改测试、未动 `src/` 任何产品代码、未合并、未推送、未碰生产
- **对象**：k61 r8 登记的撞车点 A / B + 我复核时新发现的撞车点 C

---

## 0. 结论速览

| # | 项 | 状态 | 提交 |
|---|---|---|---|
| ① | `test_k61_test_env_isolation.py` 子进程锁点名已删文件 | **已修 + 双向实测** | `bc852b0` |
| ② | `test_k61_e2e_smoke.py` 内联 `LABELED_TEST_CASES` | **已修**（按事实源 **54** 条内联）→ **后经复核改判：连同语料整体删除** | `9bbe203` → `+本轮` |
| ③ | **新撞车点**：e2e 第 161 行 import **产品模块** `src.engines.mood_detector` | **已修**（按裁决**删除**该用例 + 登记「该能力随模块一起移除」） | `9bbe203` |
| ⑤ | 防复发锁（就地定义 / 不得 import / 样本冻结） | **曾加 → 随语料一并删除**（语料已不存在，锁失去对象） | `9bbe203` → `+本轮` |
| ④ | 子进程锁 timeout 300 → 600（防重负载假红） | **已改** + 判据写入代码注释 | `0fdecef` |
| ⑥ | 全仓残留引用扫描 | 真引用**已清零**（k62 守卫 红 → 绿） | — |
| ⑦ | 54 条语料被 ③ 解除消费后如何处置 | **先裁决「存档保留」→ 我提交三条质量证据后改判「删除」** | `2c0d73a` → `+本轮` |

**k62 守卫已转绿**：`tests/test_k62_deadcode_removal_guard.py::test_no_import_of_removed_modules_anywhere`
改前 `1 failed`（两行命中）→ 改后 **`1 passed`**。

---

## 1. ① 子进程锁：改法 + 双向实测

### 1.1 改法（`bc852b0`）

`tests/test_k61_test_env_isolation.py::TestDotenvLoadingIsDeterministic::test_single_file_run_does_not_silently_skip_glm_tests`

```
- tests/test_mood_detector.py::TestRealAPI::test_real_detection_flow     ← 已被 k62 删
+ tests/test_adaptive_advisor.py::TestIntegration::test_insight_field_in_integration
```

- **断言逐字未动**（仍是 `assert "skipped" not in out` + `assert ("1 passed" in out) or ("1 failed" in out)`）；
  子进程参数、canary `.env` 写法、pin 清理、`PYTHONPATH` 全部未动（`timeout` 见 §4）。
- 另改了该方法 docstring：原 docstring 描述的是 MoodDetector 的兜底人设路径，换替身后不再属实。

### 1.2 替身形态复核（**先自己复核，未采信转述**）

| 复核点 | 实测 |
|---|---|
| 该 node 在 `bdcaaa8` 上存在且**可被单文件选中** | ✔ 单文件跑 → `1 passed`（§1.3） |
| 是否 **ZHIPU 门控** | ✔ `test_insight_field_in_integration(advisor, sample_bazi_a, api_key)` → `api_key` 夹具 `return glm_route` → `glm_route(monkeypatch, glm_api_key)` → `glm_api_key` 在 `os.environ["ZHIPU_API_KEY"]` 为空时 `pytest.skip("缺少 ZHIPU_API_KEY（免费 glm-4-flash 路由）")` |
| 是否**真打 LLM**（而非 mock） | ✔ 夹具把 `llm_client.deepseek_anthropic_completion` 换成 `_glm_route_adapter` → 真调 `glm_openai_completion(ZHIPU_API_KEY, model=FREE_LLM_MODEL)` |
| r8 §8.4 警告的「门控块可能取到 k62/旧版 DeepSeek 门控」 | ✔ **未发生**：本分支 `TestIntegration` 取到的是 **k61 版**（`glm_route` 夹具 + `api_key` 夹具 + k61 的类 docstring），文件内已无 `DEEPSEEK_API_KEY` 门控 skip |

⇒ 替身**与形式相符**。

### 1.3 双向实测（同款 harness：单文件 + 临时 cwd + 同款 pin 清理）

| 条件 | 实测输出 | 判定 |
|---|---|---|
| cwd 有 canary `ZHIPU_API_KEY`（`.env` = `ZHIPU_API_KEY=k61-canary-not-a-real-key`） | `1 passed, 1 warning in 5.88s`；`'skipped' in out: False` | ✔ **门开、未 skip** |
| cwd **无** `.env` | `1 skipped, 1 warning in 5.53s`；`'skipped' in out: True` | ✔ **缺 ZHIPU_API_KEY 而 skip** |

（复测：`1 passed 13.65s` / `1 skipped 5.55s` —— 两向结论一致。）

---

## 2. ② 内联：**54 条**（按事实源）→ **最终状态：已随语料整体删除**

> **⚠️ 本节记录的是一段已结束的中间态。** ② 先按控制方裁决「按事实源内联 54 条」实施
> （§2.2/§2.3 的保真证据即当时的验收）；随后我提交了 §8.1.1 的**三条质量证据**，
> 控制方**改判为删除** —— 最终该语料（与 §5 的锁）**已整体移除**。
> 本节保留，因为 **「24 vs 54」的取证**与**保真度证明方法**（源文本逐字比对，
> 而非取值比对）仍是本轮的有效产出与可复用方法。**最终代码里没有该常量。**

### 2.1 「24 vs 54」的取证（先停下报了控制方，获裁决后再做）

| ref | 文件 | `LABELED_TEST_CASES` 条数 |
|---|---|---|
| `main` | 存在 | **54** |
| `ea110c3` | 存在 | **54** |
| `2a67833^`（k62 删它的父提交） | 存在 | **54** |
| `k61-registered-r2`（取原文的分支） | 存在 | **54** |
| `2a67833` / `batch2-integration` / `bdcaaa8` | **已删** | — |

**k61 自己也写的 54**：其 `test_mood_detector.py:518` 原注释
> 改前样本（前 10 条全 gentle） 1.000 / 分层 12 条（每类 4 条） 0.667 / **全 54 条** 0.556

⇒ **r8 §8.3 的「24 与事实源 54 不符，已按事实源 54 内联」**（登记项，按要求留痕）。
控制方裁决：按事实源内联 54（理由：只有完整 54 才能让 e2e 实际消费的 12 条样本逐条逐序不变）。

### 2.2 内联的保真证据（**源文本逐字相同**，不只是取值相同）

| 检查 | 结果 |
|---|---|
| 取值 + 顺序（`ast.literal_eval` 全表相等） | **True**（54 == 54） |
| **源文本逐字相同**（赋值语句整段 diff） | **True**（含原始分组注释、空行、顺序） |
| 分层抽样 12 条逐条逐序相同 | **True**（见 §2.3） |

（顺序是语义的一部分：抽样取「每类前 N 条」，换序即换样本 —— 故按源文本整段搬运，非重排。）

### 2.3 抽样 12 条：内联前 = 内联后（逐条逐序）

| # | 消息 | 期望类 |
|---|---|---|
| 1 | 我好焦虑，不知道该怎么办 | gentle |
| 2 | 最近压力好大，晚上睡不着 | gentle |
| 3 | 我害怕这次考试会考砸 | gentle |
| 4 | 担心老公的身体，他最近总说累 | gentle |
| 5 | 帮我分析一下明年的财运走势 | analyst |
| 6 | 从命理角度分析我适合什么职业 | analyst |
| 7 | 我的八字里木旺不旺？和金的关系是什么 | analyst |
| 8 | 给我一个数据分析，我什么时候能升职 | analyst |
| 9 | 哈哈哈大师我的桃花运来了吗 | sassy |
| 10 | 今天心情超好，感觉要发财了 | sassy |
| 11 | 笑死，测了好几个八字都说我会发财 | sassy |
| 12 | 哎呀今天被夸了，开心死了 | sassy |

三条目结构（`message, expected_mood, category`）未变；类序 `gentle→analyst→sassy`、
每类取前 `4` 条 —— 与门禁 `representative_sample()`（`REPRESENTATIVE_PER_CLASS = 4`）**同口径**
（读源码逐行比对，非采信注释）。

---

## 3. ③ 新撞车点：e2e 从**产品**模块 import → 按裁决删除该用例

### 3.1 事实

```python
def test_e2e_mood_accuracy_reports_only(glm_route):
    from src.engines.mood_detector import MoodDetector   # ← 产品模块已被 k62 删除
```

- k61 r8 §8.3 只登记了第 141 行的 `from test_mood_detector import LABELED_TEST_CASES`（**测试**模块），
  **漏了第 161 行 import 的 `src/engines/mood_detector.py`（产品模块）**。
- k62 守卫两条都抓 → 改前 `bdcaaa8` 上该守卫是红的，红的正是 141 + 161 两行。
- **② 单修不够**：该用例仍会死在 161 行（实测见 §3.2）。

### 3.2 改前真跑输出（如实抄录）

```
[k61 e2e] unified-client ping route=glm-4-flash elapsed=0.36s product_slo(5s)=HIT reply='收到'
.
[k61 e2e] advisor.generate end-to-end route=glm-4-flash elapsed=27.74s product_slo(5s)=MISS actions=5 serendipity=True
.F
>       from src.engines.mood_detector import MoodDetector
E       ModuleNotFoundError: No module named 'src.engines.mood_detector'
/home/a/b2k61-wt/tests/test_k61_e2e_smoke.py:161: ModuleNotFoundError
1 failed, 2 passed, 1 warning in 33.88s
```

### 3.3 处置（按控制方裁决）

**删除** `test_e2e_mood_accuracy_reports_only` + 其专属助手
（`_stratified_sample` / `rows_to_moods` / `E2E_PER_CLASS` / 顶部"只报数"那段说明）；
**并连同 `LABELED_TEST_CASES`（54 条语料）与 §5 的锁一并移除**
（改判依据见 §8.1.1 —— **模块删了，它的数据跟着走**）。

**登记项（留痕，防止后人误以为丢了一个在用的能力）** —— 已写入
`tests/test_k61_e2e_smoke.py` 模块 docstring，两段：

> ⚠️ **该能力（情绪/人设判定）随模块一起移除 —— 它是死模块，"情绪/人设判定"
> 从未接入产品（`src/` 下 0 引用、无生产调用点）。这里不是"丢了一个在用的能力"，
> 而是"删掉了一个从未被用过的能力的测试"**。如将来真要做该能力，需**重新立项**
> （k62 守卫 `tests/test_k62_deadcode_removal_guard.py` 会拦住模块复活）。

以及**按要求强化**的一段（给将来重建者的明确警示，含 §8.1.1 的三条证据）：

> ## ⚠️ 给将来重建该能力的人：**不要复用旧标签，请重新设计标注**
> （列出三条可复现的一手证据：① 出处文件 4 处写"50 条"而实际 54，
> "声明与事实不符"在源头就存在；② 部分标签编码已废止的人设口径 → **已知为错的样本**；
> ③ 从未做过标注者一致性复核）
> **⇒ 重建时正确做法是重新设计标注（含标注者一致性复核与口径评审），
> 而不是复用这份源头就有「声明与事实不符」、且部分标签编码已废止约定的草稿。**

### 3.4 改后实测

```
$ pytest tests/test_k61_e2e_smoke.py --collect-only -q
tests/test_k61_e2e_smoke.py::test_e2e_llm_chain_alive
tests/test_k61_e2e_smoke.py::test_e2e_advisor_generate_reports_latency
2 tests collected in 2.27s          ← 3 → 2，0 ERROR（控制方要求）
```

**真跑（`K61_E2E=1`，免费 glm-4-flash）**：

```
[k61 e2e] unified-client ping route=glm-4-flash elapsed=0.48s product_slo(5s)=HIT reply='收到'
[k61 e2e] advisor.generate end-to-end route=glm-4-flash elapsed=27.09s product_slo(5s)=MISS actions=5 serendipity=True
2 passed, 1 warning in 33.60s        ← 改前 1 failed / 2 passed
```

---

## 4. ④ timeout 300 → 600（防今晚门禁假红）

**改的是测试自身参数，非放宽断言**（断句仍是「不得出现 skipped」+「1 passed 或 1 failed」，
子进程参数与 canary 写法未动）。控制方批准。

### 4.1 依据（实测）

| 场景 | 结果 |
|---|---|
| 负载平静（单锁单跑） | **1 passed in 16.41s** |
| 负载平静（整文件） | **28 passed in 13.36–16.00s** |
| **负载 9.1**（并行 pytest + `ugrep` 203% CPU） | **1 failed, 27 passed in 391.01s** —— 失败项正是本锁，**形态是撞自身 300s 上限（非断言失败）** |

**网络已排除**：单发 canary 调用
`glm_openai_completion("k61-canary-not-a-real-key", …)` 实测 **0.06s** 返回
`RuntimeError: {'code': '401', 'message': '令牌已过期或验证不正确'}` —— **失败路径是快的**。
⇒ 最可能是**共享 embedding 模型加载 / CPU 争用**（运行日志每次出现 `Loading weights: 391`）。
**根因未坐实**（加权复跑按降载指示未做），故只登记现象与排除项。

### 4.2 假红判据（已写进代码注释，供今晚门禁判读）

> **若本锁的失败信息是 `subprocess.TimeoutExpired`（而非断言失败），即为负载性假红，重跑即可。**
> 断言语义未变：门控形态不对时仍是断言失败。

---

## 5. ⑤ 防复发锁（新文件 `tests/test_k61_e2e_seed_corpus.py`）→ **最终状态：已删除**

> **⚠️ 该锁文件已随语料一并删除**（控制方改判，依据 §8.1.1）—— 语料不存在了，锁失去对象。
> 本节保留其**设计记录**，因为「**锁不能被静默跳过**」这条设计判断仍然有效且可复用
> （控制方单独肯定过这一点）；其**植入实验**的结论也仍成立（见 §5.1）。
> **最终代码里没有这个文件。**

**为什么单独一个文件**（这条设计判断仍然有效，可复用到任何"锁"上）：e2e 文件是
**模块级 `pytestmark` + `skipif(K61_E2E)`**，放在里面的锁会随冒烟**默认 skip 而失效**
—— 那就锁不住「防改回 import」（**"声明了但无效"正是今天反复出现的形态，
这里在设计层面避开**）。该文件当时**不带 e2e 标记、不看 `K61_E2E`**，跑在**常规门禁**里；
**零网络、零 LLM、零生产数据**（纯 AST + 字面量比对）。

**⚠️ 当时的框定（控制方第一轮裁决，后随语料删除一并作废）**：该锁保护的是
**存档完整性，不是活资产**；其 docstring 与核心锁 docstring 均写明"别读成还有个能力在用它"。
—— 这段框定存在的意义是：**它让"这个数据集到底该不该留"这个问题无法被含糊过去**，
随后我在第二轮被问到时才能拿证据把它推翻。**第一轮的框定改写并非白做。**

| 锁 | 断言 |
|---|---|
| 就地定义 | `LABELED_TEST_CASES` 必须是 e2e 文件**模块级赋值**（改回 import → 红） |
| 不得接线 | e2e 文件不得 import `mood_detector` / `emotion_soother`，含 `import_module(<字面量>)` / `__import__(<字面量>)` 动态面 |
| 形状冻结 | 54 条 / 三条目 / 非空字符串 / 三类齐 / `gentle17·analyst17·sassy20` / 无重复消息 |
| **样本冻结** | 抽样 12 条与**内联前字面快照**逐条逐序一致（换序 = 换样本 → 红） |

### 5.1 植入实验（锁有牙，均已还原并 diff 校验一致）

| 植入 | 期望 | 实测 |
|---|---|---|
| **A**：把「就地定义」换回 `from test_mood_detector import LABELED_TEST_CASES` | 就地定义 + 不得接线 两条红 | ✔ **3 failed**（`assert None is not None` / `assert not ['149: from test_mood_detector import ...']` / 条数） |
| **B**：同条数、仅把 gentle 前两条**换序** | 样本冻结条红 | ✔ **1 failed, 4 passed**（`分层样本与内联前不一致（逐条逐序）—— 换序/换条即换样本`） |

新锁文件本身：**5 passed**。

---

## 6. 全仓残留引用扫描

真引用**已清零**：唯一的两处（e2e 141 / 161）分别由 ② 与 ③ 处置。
AST 复核 `import` / `from … import` / `importlib.import_module(<字面量>)` / `__import__(<字面量>)`
四种 import 面，`src/` `scripts/` `tests/` 全扫 → **0 命中**。

**k62 守卫验收**：`test_no_import_of_removed_modules_anywhere` **红 → 绿**（`1 passed`）。

其余残留全为**注释/docstring**（非可执行引用，无需动）：`tests/test_emoji_cleanup.py:8,9,20`、
`tests/conftest.py:35,621`、`test_k62_deadcode_removal_guard.py` 自身常量表与说明、
`test_k61_test_env_isolation.py`（① 的替换理由说明）、`test_k61_e2e_smoke.py`（③ 的登记说明）。
`emotion_soother` **零真引用**。**未发现需改产品代码的残留。**

---

## 7. 测试数字（全部定向单文件，未跑全量）

| 命令 | 改前 | 改后 |
|---|---|---|
| `pytest tests/test_k62_deadcode_removal_guard.py::test_no_import_of_removed_modules_anywhere -q` | **1 failed**（141+161 两行命中） | **1 passed**（13.31s） |
| `pytest tests/test_k61_test_env_isolation.py -q` | 28 passed（16.00s） | **28 passed**（13.36s） |
| `pytest tests/test_k61_e2e_smoke.py --collect-only -q` | 3 collected | **2 collected**，0 ERROR（2.27s） |
| `K61_E2E=1 pytest tests/test_k61_e2e_smoke.py -q -s`（真 GLM） | 1 failed / 2 passed（33.88s） | **2 passed**（33.60s） |
| `pytest tests/test_k61_e2e_seed_corpus.py -q`（曾新增的锁） | — | 曾 **5 passed** → **文件已随语料删除** |
| `pytest tests/test_k62_deadcode_removal_guard.py tests/test_adaptive_advisor.py -q`（改前基线） | 1 failed / 43 passed / 3 skipped | —（未复跑整对；已用上面的单条守卫替代） |

**最终态复测（语料 + 锁删除后）**：`tests/test_k61_e2e_smoke.py --collect-only -q`
→ **2 collected / 0 ERROR**；悬空引用检查（`seed_corpus` / `test_k61_e2e_seed`）
在 `tests/`、`docs/` 下** 0 命中**。

**最终 tip（`ff033ef`）真跑复测（免费 glm-4-flash，定向单文件）**：

```
[k61 e2e] unified-client ping route=glm-4-flash elapsed=0.61s product_slo(5s)=HIT reply='收到'
[k61 e2e] advisor.generate end-to-end route=glm-4-flash elapsed=28.40s product_slo(5s)=MISS actions=5 serendipity=True
2 passed, 1 warning in 104.03s
```
（同轮 `test_k62_deadcode_removal_guard.py::test_no_import_of_removed_modules_anywhere`
→ **1 passed**，8.43s —— 撞车点仍为零。）

---

## 8. 诚实披露

1. **语义张力（我已报告 → 控制方先裁「存档保留」→ 我提交三条质量证据后改判「删除」）**：
   **③ 删掉了语料唯一的 LLM 消费者**（那条准确率用例），于是 ② 内联的 54 条**在 LLM 侧没有消费者了**
   —— 两条裁决单独看都成立，合起来会产生"内联了一堆没人用的数据"。
   - **第一轮裁决**：保留，但框定必须是「存档」而非「守住一个活资产」（我按三条要求落地了
     框定改写 / 锁注释 / 移除触发条件登记，提交 `2c0d73a`）。
   - **控制方随即给了一次改口机会**（"你比我更清楚那 54 条是怎么来的"），
     我提交了 §8.1.1 的**三条可复现证据**。
   - **第二轮裁决：删除**（连同语料与锁）。理由（沿我的三条推理往下走）：
     ① 一个**部分标签已知为错**的数据集，即使标成"草稿"，仍会诱使将来的人拿它当起点 ——
     而"草稿"这个限定**依赖未来的读者去读 docstring**，是脆弱的保障；
     ② 消费者已随模块删除、能力已被用户拍板移除；
     ③ 与既有裁决一致（**模块删了，它的数据跟着走**）；
     ④ 将来重建的**正确做法是重新设计标注**（含标注者一致性复核），而不是复用一份源头就有
     "声明与事实不符"、且部分标签编码已废止约定的草稿。
   - **最终状态**：`LABELED_TEST_CASES` 与 `tests/test_k61_e2e_seed_corpus.py` **均已删除**；
     登记项按要求**强化**（见 §3.3）—— 不只"能力随模块移除"，还**记入三条质量证据**
     并给重建者明确警示「**不要复用旧标签，请重新设计标注**」。

   ### 1.1 ★ 本轮的实质产出：这 54 条的标注质量评估（三条一手证据）

   控制方留了一次改口机会，我回的不是"我觉得"，而是**三条可复现、从出处文件里挖出来的证据**
   （控制方据此改判为"删"）。三条都能在出处文件里复现：

   | # | 依据 | 事实 | 为什么它致命 |
   |---|---|---|---|
   | 1 | **出处文件自身元数据从未与内容对齐** | 同一文件 **4 处写「50 条」**（`# Acceptance Test: 50 Test Cases…`、`# 50 labeled test cases…`、类 docstring `Verify MoodDetector accuracy on 50…`、断言消息 `All 50 test cases should have…`），而实际恒为 **54**（断言写死 54） | 「声明与事实不符」这个形态**在它的源头就存在**，与本次撞上的「k61 r8 写 24 条 vs 事实源 54 条」**同源** ⇒ 它不是一份被精心维护的资产 |
   | 2 | **部分期望编码的是已废止的口径** | `你好 / 在吗 / 好的谢谢 / 早上好 / 明白了` 被期望成 `sassy` —— 把**中性问候**绑到「3 档人设」的**语气约定**上，而这套人设**已被用户拍板拆除**（k62/k63） | ⇒ 这份"标注"里**有已知为错的样本**：它不是"未验证"，是**部分错误**。这是**改判的关键** |
   | 3 | **内部一致性从未被验证** | k61 在该语料上的实测（`tests/test_mood_detector.py:518`）：全 54 条 **0.556**，其中 `sassy` **1/20** | 20 条只中 1 条，既可能是模型不行、也可能是**标签与自然读法不一致** —— 而**没有任何记录**做过标注者一致性复核来排除后者 |

   **这一条也顺带解释了「该能力当年为什么没接进产品」**：标签体系与人设约定**耦合**、
   且从未做过一致性验证 —— 一个连自身元数据都没对齐的期望集，接进产品只会把口径错误
   带进用户可见输出。

   **控制方的改判理由中有一句值得留档**：删掉它**不是"浪费那 54 条人力"** ——
   那 54 条里有已知无效的部分，**它的价值本来就低于表面**；而**这三条证据本身才是产出**。
2. **⚠️ 未验：三条"合并后应然"的整批门禁数字**。只跑了上表定向单文件（时段纪律 + 你的降载指示），**未跑全量、未跑合并集**。
3. **⚠️ 负载性假红的根因未坐实**：已排除网络（0.06s 401），推断是 embedding 加载/CPU 争用。
   坐实需在重负载下加权复跑 —— 按降载指示**没做**。判据已写进代码注释（§4.2）。
4. **⚠️ 未做 A/B：旧 node（`test_mood_detector.py::TestRealAPI::test_real_detection_flow`）的 canary 路径耗时**。
   它与其产品模块都已不在，我**刻意没有**去 `/home/a/k61-wt` 跑它（避免在别的 worktree 留测试副作用）。
   可读到的差异：旧 node 是 **1 次** `detector.detect()`；① 的替身是 **1 次** `advisor.generate()`
   （真跑实测 27.09–27.74s、产出 5 个领域）—— 替身**真跑**更重。故**不能**断言新旧敏感性等价。
5. **④ 的生效面**：只放宽了这一个子进程的 timeout。**其它真实 LLM 用例**在重负载下是否也有同类假红，**未验**。
6. **未改宽任何断言**：①③④ 均逐字保留原断句；②③ 是等量替换与删除（删除理由见 §3.3）。
7. **未动 `src/`、未合并、未推送、未碰生产**。e2e 真跑用**临时 cwd + 只含 ZHIPU_API_KEY 的 `.env`**，
   生产其余键未进测试进程；GLM 一律免费 `glm-4-flash`。
8. **观察到一条与本次改动无关的守卫告警**（登记备查）：e2e 真跑 teardown 出现过
   `[k61 生产数据守卫] 生产数据 mtime 有变化，但**本会话未打开**过它们（判据已按 r7 改为 fd 看门狗）：['/mnt/d/fortune-data/vectordb_v2/chroma.sqlite3']`
   —— 按 r7 判据是**告警非失败**，且明示"本会话未打开过"，未追查来源。
9. **植入实验的还原**：两次植入均以 `cp` 从备份还原并 `diff -q` 校验一致（§5.1），工作区最终 `git status` 干净。
10. **②的锁「不得再从别处 import」的边界**：锁的是 e2e 文件里对该常量的 **import 面**
    （`import` / `from … import` / 动态 import 字面量）。若有人用 `exec`/`getattr` 之类
    非常规花活接回来，本锁抓不到 —— 但那种写法在任何常规 review 下都显眼，且 k62 守卫另有兜底。
11. **"改动范围"是**证明**的，不是自称的**（沿用控制方要求的方式：剥掉 docstring 后比 AST）：
    - **框定改写轮**（`2c0d73a`）：对两个文件各做「剥 docstring 后的 AST 与 HEAD 逐字比对」
      → **均完全相同** ⇒ 断言、常量、逻辑**逐字节未变**，改动只落在注释/docstring。
    - **删除轮（最终态）**：对 `tests/test_k61_e2e_smoke.py` 做**双向** AST 证明：
      ① **两侧同时剥 docstring + 同时删掉 `LABELED_TEST_CASES` 赋值** → AST **完全相同**
      ⇒ **代码侧唯一变化就是删除该常量**（两条 e2e 用例、导入、标记全部未动）；
      ② **不删常量直比** → **不同** ⇒ 反证该常量确实已从代码里移除。
    - **`--collect-only`：2 collected / 0 ERROR**（语料与锁文件删除后复测）。
    - （自曝一个过程瑕疵）第一次写这段证明脚本时我**只在一侧删了该常量节点**，得出"不同"的
      假警报；是**脚本 bug 不是代码问题**，双侧同步后即完全相同。记此以说明该证明是**真跑**的。
