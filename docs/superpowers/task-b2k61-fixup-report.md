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
| ② | `test_k61_e2e_smoke.py` 内联 `LABELED_TEST_CASES` | **已修**（按控制方裁决 = 事实源 **54** 条） | `9bbe203` |
| ③ | **新撞车点**：e2e 第 161 行 import **产品模块** `src.engines.mood_detector` | **已修**（按裁决**删除**该用例 + 登记「该能力随模块一起移除」） | `9bbe203` |
| ⑤ | 防复发锁（就地定义 / 不得 import / 样本冻结） | **已加**（新文件，常规门禁可见） | `9bbe203` |
| ④ | 子进程锁 timeout 300 → 600（防重负载假红） | **已改** + 判据写入代码注释 | `0fdecef` |
| ⑥ | 全仓残留引用扫描 | 真引用**已清零**（k62 守卫 红 → 绿） | — |
| ⑦ | 54 条语料被 ③ 解除消费后如何处置 | **裁决：存档保留**（框定改写 + 锁注释 + 移除触发条件登记） | `+本轮` |

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

## 2. ② 内联：**54 条**（按事实源）

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
（`_stratified_sample` / `rows_to_moods` / `E2E_PER_CLASS` / 顶部"只报数"那段说明）。

**登记项（留痕，防止后人误以为丢了一个在用的能力）** —— 已写入
`tests/test_k61_e2e_smoke.py` 模块 docstring：

> ⚠️ **该能力（情绪/人设判定）随模块一起移除 —— 它是死模块，"情绪/人设判定"
> 从未接入产品（`src/` 下 0 引用、无生产调用点）。这里不是"丢了一个在用的能力"，
> 而是"删掉了一个从未被用过的能力的测试"**。如将来真要做该能力，需**重新立项**
> （k62 守卫 `tests/test_k62_deadcode_removal_guard.py` 会拦住模块复活）。

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

## 5. ⑤ 防复发锁（新文件 `tests/test_k61_e2e_seed_corpus.py`）

**为什么单独一个文件**：e2e 文件是**模块级 `pytestmark` + `skipif(K61_E2E)`**，
放在里面的锁会随冒烟**默认 skip 而失效** —— 那就锁不住「防改回 import」
（**"声明了但无效"正是今天反复出现的形态，这里在设计层面避开**）。
本文件**不带 e2e 标记、不看 `K61_E2E`**，跑在**常规门禁**里；**零网络、零 LLM、零生产数据**
（纯 AST + 字面量比对）。

**⚠️ 框定（控制方裁决）**：本锁保护的是**存档完整性，不是活资产** ——
被锁的 54 条语料已因能力移除而存档（唯一消费者已随模块删除，**无任何代码在用它**）。
本文件 docstring 与核心锁 docstring 均已写明这一点，避免读者误得"还有个能力在用它"。

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
| `pytest tests/test_k61_e2e_seed_corpus.py -q`（新） | — | **5 passed** |
| `pytest tests/test_k62_deadcode_removal_guard.py tests/test_adaptive_advisor.py -q`（改前基线） | 1 failed / 43 passed / 3 skipped | —（未复跑整对；已用上面的单条守卫替代） |

---

## 8. 诚实披露

1. **语义张力（我已报告 → 控制方裁决「保留为存档」）**：**③ 删掉了语料唯一的 LLM 消费者**
   （那条准确率用例），于是 ② 内联的 54 条**在 LLM 侧没有消费者了** —— 两条裁决单独看都成立，
   合起来会产生"内联了一堆没人用的数据"。
   **裁决：保留，但框定必须是「存档」而非「守住一个活资产」**。已按三条要求落地：
   - **框定改写**：e2e 模块 docstring 与 `LABELED_TEST_CASES` 上方注释**均**明写
     「**为一个从未接入产品的能力所准备的、已标注的种子语料，因能力移除而存档；
     唯一消费者已随被测模块一起删除，本文件里没有任何测试读这个常量**；
     保留原因 = 标注数据不可再生 + 已被显式标注，不会像死模块那样误导人」。
     两处都加了**反向提示**：「别把它读成'还有个能力在用它'」。
   - **锁的注释**：`tests/test_k61_e2e_seed_corpus.py` 模块 docstring + 核心锁的 docstring
     均加「**本锁保护的是存档完整性，不是活资产**」。
   - **移除触发条件（登记项）**：**「若该能力（情绪/人设判定）在后续规划中不再重建，
     则此存档与其锁一并移除」**（= 删 `LABELED_TEST_CASES` + `test_k61_e2e_seed_corpus.py`）。
     已同时写入 e2e docstring 的登记段与锁文件 docstring。
   **断言未变**（纯注释/docstring 改动，见 §8.11 的 AST 证明）。

   ### 1.1 应控制方邀请：我对这 54 条标注质量的如实判断

   控制方留了一次改口机会（"你比我更清楚那 54 条是怎么来的"）。我的判断是：
   **它是手写期望集，不是经验证的标注语料；可作重建时的草稿起点，不宜当基准真值。**
   三条可核实的依据（都能在出处文件里复现）：

   | 依据 | 事实 |
   |---|---|
   | **出处文件自身元数据从未与内容对齐** | 同一文件 4 处写「50 条」（`# Acceptance Test: 50 Test Cases…`、`# 50 labeled test cases…`、类 docstring `Verify MoodDetector accuracy on 50…`、断言消息 `All 50 test cases should have…`），而实际恒为 **54**（断言写死 54）。即"声明与事实不符"这个形态**在它的源头就存在**，与我这次遇到的「24 vs 54」**同源**。 |
   | **部分期望编码的是已废止的口径** | 中性问候被期望成 `sassy`：`你好 / 在吗 / 好的谢谢 / 早上好 / 明白了` —— 这是把问候绑到"3 档人设"的语气约定上（与 k62/k63 拆掉的人设同源），不是自然情绪标签。 |
   | **从未验证过内部一致性** | k61 自己在该语料上的实测（`test_mood_detector.py:518`）：全 54 条 **0.556**，其中 `sassy` **1/20**。20 条里只中 1 条，既可能是模型不行、也可能是标签与自然读法不一致 —— 而**没有任何记录**做过标注者一致性/复核来排除后者。 |

   **我为什么不据此改判为"删"**：① 这三条讲的是**质量不足以当真值**，不是"不可再生"为假 ——
   54 条、三类 17/17/20 的手写工作确实是**一次性人力**，删了就得重写；
   ② 持有成本约百行且已被显式标注 + 有锁，**不会再误导人**（这正是今天所有坑的反面）；
   ③ 我已在**框定里写明质量提示**（"应把它当草稿起点复核，而不是当基准真值"），
   所以保留它**不会**制造"这是个验证过的语料"的假象。
   ⇒ **维持"保留为存档"**；若你认为"质量不足以支撑重建"即可删，请一句话改判。
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
11. **"纯注释/docstring 改动"是**证明**的，不是自称的**：本轮框定改写后，对
    `tests/test_k61_e2e_smoke.py` 与 `tests/test_k61_e2e_seed_corpus.py` 各做了
    **剥掉 docstring 后的 AST 比对**（`ast.dump` 与 HEAD 逐字比对）：
    **两个文件均「完全相同」** ⇒ 断言、常量、逻辑**逐字节未变**，改动只落在注释/docstring。
    锁复跑 **5 passed**；`--collect-only` 仍 **2 collected / 0 ERROR**。
