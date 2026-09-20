# batch2-k61 集成修复报告（k61 × k62 撞车点）

- **工作目录**：`/home/a/b2k61-wt`（分支 `batch2-k61`，起点 tip = `bdcaaa8`）
- **本次提交**：`bc852b0`（仅 ① ；② 按你的停止条件**未动**）
- **红线遵守**：只改测试、未动 `src/` 任何产品代码、未合并、未推送、未碰生产
- **对象**：k61 r8 登记的撞车点 A / B（`task-k61-report.md` §8.2 / §8.3）

---

## 0. 结论速览

| # | 项 | 状态 |
|---|---|---|
| ① | `test_k61_test_env_isolation.py` 子进程锁点名已删文件 | **已修 + 双向实测**（`bc852b0`） |
| ② | `test_k61_e2e_smoke.py` 内联 `LABELED_TEST_CASES` | **⛔ 停下报你**：条数与报告说的 24 不符（实际 **54**） |
| ③ | **新发现（你的 brief 与 k61 r8 都没登记）**：e2e 第 161 行 import 了**被删的产品模块** `src/engines/mood_detector.py` | **⛔ 停下报你**：这是**新撞车点**，需你定处置 |
| ④ | 全仓残留引用扫描 | **已扫**：真引用只剩 e2e 两行（= ② 与 ③），其余全是注释/docstring |

**⚠️ 最关键的一句**：**只做 ② 并不能达成你要的「e2e 不再 ERROR」** —— 那个用例还会死在 ③ 的第 161 行（`ModuleNotFoundError`，实测见 §4）。②③ 必须一起定。

---

## 1. ① 子进程锁：改法 + 双向实测

### 1.1 改法（`bc852b0`）

`tests/test_k61_test_env_isolation.py::TestDotenvLoadingIsDeterministic::test_single_file_run_does_not_silently_skip_glm_tests`

```
- tests/test_mood_detector.py::TestRealAPI::test_real_detection_flow     ← 已被 k62 删
+ tests/test_adaptive_advisor.py::TestIntegration::test_insight_field_in_integration
```

- **断言逐字未动**（仍是 `assert "skipped" not in out` + `assert ("1 passed" in out) or ("1 failed" in out)`）；
  子进程参数、canary `.env` 写法、pin 清理、`PYTHONPATH`、`timeout=300` 全部未动。
- 只改了 ① 点名的 node 字面量 ② 该方法的 docstring（原 docstring 描述的是 MoodDetector
  的兜底人设路径，换替身后那句话不再属实 → 改写为替身实况 + 登记替换理由）。
- 写法用 `os.path.join(REPO, "tests", "test_adaptive_advisor.py") + "::TestIntegration::..."`（显式 `+`，避免隐式字符串拼接被误读）。

### 1.2 替身形态复核（**先自己复核，未采信转述**）

| 复核点 | 实测 |
|---|---|
| 该 node 在 `bdcaaa8` 上存在且**可被单文件选中** | ✔ 单文件跑 → `1 passed`（§1.3） |
| 是否 **ZHIPU 门控** | ✔ `test_insight_field_in_integration(advisor, sample_bazi_a, api_key)` → `api_key` 夹具 `return glm_route` → `glm_route(monkeypatch, glm_api_key)` → `glm_api_key` 在 `os.environ["ZHIPU_API_KEY"]` 为空时 `pytest.skip("缺少 ZHIPU_API_KEY（免费 glm-4-flash 路由）")` |
| 是否**真打 LLM**（而非 mock） | ✔ 夹具把 `llm_client.deepseek_anthropic_completion` 换成 `_glm_route_adapter` → 真调 `glm_openai_completion(ZHIPU_API_KEY, model=FREE_LLM_MODEL)` |
| r8 §8.4 警告的「门控块可能取到 k62/旧版 DeepSeek 门控」 | ✔ **未发生**：本分支 `TestIntegration` 取到的是 **k61 版**（`glm_route` 夹具 + `api_key` 夹具 + k61 的类 docstring），文件内已无 `DEEPSEEK_API_KEY` 门控 skip |

⇒ 替身**与形式相符**，不需要「停下报你」。

### 1.3 双向实测（同款 harness：单文件 + 临时 cwd + 同款 pin 清理）

| 条件 | 实测输出 | 判定 |
|---|---|---|
| cwd 有 canary `ZHIPU_API_KEY`（`.env` = `ZHIPU_API_KEY=k61-canary-not-a-real-key`） | `1 passed, 1 warning in 5.88s`；`'skipped' in out: False` | ✔ **门开、未 skip** |
| cwd **无** `.env` | `1 skipped, 1 warning in 5.53s`；`'skipped' in out: True` | ✔ **缺 ZHIPU_API_KEY 而 skip** |

（第二次复测：`1 passed 13.65s` / `1 skipped 5.55s` —— 两向结论一致。）

**整文件回归**：`pytest tests/test_k61_test_env_isolation.py -q` → **28 passed**（16.00s）。
`test_single_file_run_...` 单跑复测 → **1 passed in 16.41s**（见 §7 的负载说明）。

---

## 2. ⛔ ② 内联：**条数对不上 → 按你的停止条件停下**

你的要求 ③ 是「核实内联的条数与 k61 报告说的 **24** 条一致（不一致 → 停下报我）」。
**核实结果：不一致。**

`LABELED_TEST_CASES` 的**完整定义**（`git show k61-registered-r2:tests/test_mood_detector.py:348`）实为 **54 条**，逐 ref 实测：

| ref | 文件 | `LABELED_TEST_CASES` 条数 |
|---|---|---|
| `main` | 存在 | **54** |
| `ea110c3` | 存在 | **54** |
| `2a67833^`（k62 删它的父提交） | 存在 | **54** |
| `k61-registered-r2`（你指定取原文的分支） | 存在 | **54** |
| `2a67833` / `batch2-integration` / `bdcaaa8` | **已删** | — |

**并且 k61 自己也写的 54**：`test_mood_detector.py:518` 原注释
> 改前样本（前 10 条全 gentle） 1.000 / 分层 12 条（每类 4 条） 0.667 / **全 54 条** 0.556

⇒ 结论：**「24」是 r8 §8.3 的笔误**，事实源是 **54**。
「24」既不等于常量条数（54），也不等于 e2e 实际消费的样本量（**12** = 每类 4 条 × 3 类）。

**为什么我停而不是自己拍 54**：54 vs 24 差 2.25×，且②的硬要求是「**样本语义不变**」——
而样本是从该常量**算出来**的（`representative_sample()` 口径）。**只有内联完整 54 条**才能让
被消费的 12 条样本**逐条、逐序不变**；内联 24 条会**改变样本**（= 违反②的语义不变要求）。
两条要求（"按报告的 24" 与 "语义不变"）在 54 这个事实上**互斥**，我不自行择一。

**待你一句话**：按事实源**内联完整 54 条**（保住样本语义逐字不变）—— 批准我就做，②包含你要求的第 ⑤ 条锁（断言常量在本文件内就地定义、不得 import）。

**样本语义取证**（若批准，内联后应逐字不变）：

| 层 | 条数 | 会被 e2e 取的前 4 条（文件内既有顺序） |
|---|---|---|
| gentle | 17 | 我好焦虑，不知道该怎么办 / 最近压力好大，晚上睡不着 / 我害怕这次考试会考砸 / 担心老公的身体，他最近总说累 |
| analyst | 17 | 帮我分析一下明年的财运走势 / 从命理角度分析我适合什么职业 / 我的八字里木旺不旺？和金的关系是什么 / 给我一个数据分析，我什么时候能升职 |
| sassy | 20 | 哈哈哈大师我的桃花运来了吗 / 今天心情超好，感觉要发财了 / 笑死，测了好几个八字都说我会发财 / 哎呀今天被夸了，开心死了 |
| **合计样本** | **12** | 上表 3×4，顺序 = gentle → analyst → sassy |

（e2e 的 `_stratified_sample()` 与门禁的 `representative_sample()` **同口径**：按 `case[1]` 分组、每类取前 `4` 条、类序 `("gentle","analyst","sassy")`。已读源码逐行比对，非采信注释。）

---

## 3. ⛔ ③ 新撞车点（brief 与 k61 r8 均未登记）

**`tests/test_k61_e2e_smoke.py:161`**：

```python
def test_e2e_mood_accuracy_reports_only(glm_route):
    from src.engines.mood_detector import MoodDetector   # ← 产品模块已被 k62 删除
```

- r8 §8.3 只登记了第 141 行的 `from test_mood_detector import LABELED_TEST_CASES`（**测试**模块）；
  **漏了第 161 行 import 的 `src/engines/mood_detector.py`（产品模块）**。
- 但 k62 的守卫**两条都抓**（`test_no_import_of_removed_modules_anywhere` 扫 `src/ scripts/ tests/`
  的 import 面）→ 实测**当前 `bdcaaa8` 上该守卫是红的，且红的正是这两行**。
- 影响：**这条 e2e 用例永远跑不起来**（不是"跑起来才 ERROR"，是 import 期就 `ModuleNotFoundError`）。
  实测见 §4。

**为什么我停**：这条用例的**被测对象已被用户拍板删除**（k62：`MoodDetector` 在 `src/` 下 0 引用、
无生产调用点）。可选处置都不是"改个名字"能了事的：

- **(a) 删掉该用例 + 其专属助手**（`_stratified_sample` / `rows_to_moods` / `E2E_PER_CLASS` /
  顶部那段"准确率只报数"的说明）—— 与 k62「死模块不复活」同向，最自洽；
- **(b) 让它 skip 并说明模块已删** —— 保住文件骨架，但留下一段永假的分支；
- **(c) 重新立项复算准确率** —— 属**产品决策**，超出"集成修复"。

我倾向 **(a)**，但这是**范围决策**（删一条既有 e2e 用例），按你的红线我不自行拍。
**并且它必须与 ② 一起定** —— 见 §0 的关键句。

---

## 4. `K61_E2E=1` e2e 实测（真跑，免费 glm-4-flash）

命令（在临时 cwd，`.env` **只含** `ZHIPU_API_KEY`（取自部署 `.env`）→ 生产其余键**未进测试进程**；
未在部署目录执行、未碰生产）：

```
K61_E2E=1 TMPDIR=/dev/shm PYTHONPATH=<repo>:<repo>/tests \
  python3 -m pytest tests/test_k61_e2e_smoke.py -q -s -p no:cacheprovider
```

**如实抄录真实输出**：

```
[k61 e2e] unified-client ping route=glm-4-flash elapsed=0.36s product_slo(5s)=HIT reply='收到'
.
[k61 e2e] advisor.generate end-to-end route=glm-4-flash elapsed=27.74s product_slo(5s)=MISS actions=5 serendipity=True
.F
...
>       from src.engines.mood_detector import MoodDetector
E       ModuleNotFoundError: No module named 'src.engines.mood_detector'
/home/a/b2k61-wt/tests/test_k61_e2e_smoke.py:161: ModuleNotFoundError
...
1 failed, 2 passed, 1 warning in 33.88s
```

| 用例 | 结果 |
|---|---|
| `test_e2e_llm_chain_alive` | **PASSED** —— 真打 GLM，0.36s，`reply='收到'` |
| `test_e2e_advisor_generate_reports_latency` | **PASSED** —— 真打 GLM，27.74s，`actions=5 serendipity=True` |
| `test_e2e_mood_accuracy_reports_only` | **FAILED** —— 第 161 行 `ModuleNotFoundError`（= 撞车点 ③） |

- **能收集**：`--collect-only` → `3 tests collected in 1.01s`，**0 ERROR**。
- 你要的「那两条」= 前两条：**真跑了，都 passed**（不是 skip、不是 error）。
- 第三条**不是**「收集期 ERROR」，是**运行期 ERROR/CASE FAILED**；且**先**死在 161 行，
  所以 141 行那条 import（= ②）在这一轮里**根本还没被执行到** —— 再次说明 ② 单修不够。

---

## 5. 全仓残留引用扫描

命令（限定在 worktree 内、`--include=*.py`，非全盘递归）：

```
grep -rn "mood_detector\|LABELED_TEST_CASES\|emotion_soother" --include=*.py .
```

并用 AST 复核**真 import 面**（`import` / `from ... import` / `importlib.import_module` /
`__import__` 字面量）：**真引用只有 2 处，都在 e2e 文件**。

| 位置 | 形态 | 处置 |
|---|---|---|
| `tests/test_k61_e2e_smoke.py:141` | **真 import**（测试模块） | = 你的 ②，**待你拍 54/24** |
| `tests/test_k61_e2e_smoke.py:161` | **真 import**（产品模块） | = 新撞车点 ③，**待你定处置** |
| `tests/test_k61_e2e_smoke.py:123,157` | 注释 | 无需动（其中 157 行的"被测模块在 src/ 下 0 引用"定级说明，随 ③ 处置一并更新） |
| `tests/test_k61_test_env_isolation.py:240` | docstring（**本次新增**的替换理由说明） | 无需动（AST 不认作接线） |
| `tests/test_k62_deadcode_removal_guard.py:8,12,14,16,89,90,92,93` | 守卫**自己的** docstring + 常量表（`REMOVED_MODULE_FILES` / `REMOVED_MODULE_STEMS`） | 无需动（守卫扫 import 面，不扫自身常量） |
| `tests/test_emoji_cleanup.py:8,9,20` | 注释 | 无需动 |
| `tests/conftest.py:35,621` | 注释 | 无需动 |

⇒ **除 e2e 那两行外，全仓无其它对已删模块的可执行引用**（`emotion_soother` 无任何真引用）。
**未发现需要改产品代码的残留**。

---

## 6. 测试数字（全部定向，未跑全量）

| 命令 | 结果 |
|---|---|
| `pytest tests/test_k61_test_env_isolation.py -q` | **28 passed**（16.00s） |
| ↑ 该文件单跑 `::test_single_file_run_...`（负载平静时） | **1 passed**（16.41s） |
| `pytest tests/test_k62_deadcode_removal_guard.py tests/test_adaptive_advisor.py -q` | **1 failed, 43 passed, 3 skipped**（10.05s） |
| ↑ 其中 `test_no_import_of_removed_modules_anywhere` | **FAILED**，断言输出逐字：`死模块被重新接线：` / `tests/test_k61_e2e_smoke.py:141 from test_mood_detector import ...` / `tests/test_k61_e2e_smoke.py:161 from src.engines.mood_detector import ...` |
| `pytest tests/test_k61_e2e_smoke.py --collect-only -q` | **3 tests collected**，0 ERROR（1.01s） |
| `K61_E2E=1 pytest tests/test_k61_e2e_smoke.py -q -s`（真 GLM） | **1 failed, 2 passed**（33.88s），明细见 §4 |

**k62 守卫的当前状态（重要）**：`bdcaaa8` 上它**本来就是红的**（两行都在）。①**不涉及**它；
它要 **②+③ 一起**才可能转绿。

---

## 7. 诚实披露

1. **⚠️ 未验：三条"合并后应然"的整批门禁数字**。我只跑了上表的定向集（时段纪律 + 你的"不许跑全量"）。**未跑** k62/k61 之外任何文件，**未跑全量**。
2. **⚠️ 一次负载性假红，已复测为绿**：`tests/test_k61_test_env_isolation.py` 整文件跑曾出现
   `1 failed, 27 passed in 391.01s` —— 失败项正是本锁，成因是**子进程撞到自身 `timeout=300` 上限**
   （当时另一会话在跑重活：`ugrep` 203% CPU + 合并 pytest，机器负载 9.1）。
   **负载平静后同一命令复测 `1 passed in 16.41s`**，前后同代码、同参数 ⇒ 判定**环境性**。
   **根因我未坐实**：我**排除了网络**（单发 canary 调用 `glm_openai_completion("k61-canary-not-a-real-key", ...)`
   实测 **0.06s** 返回 `RuntimeError: {'code':'401','message':'令牌已过期或验证不正确'}` —— 失败是**快**的），
   所以更可能是共享的 **embedding 模型加载 / CPU 争用**（运行日志每次都出现 `Loading weights: 391`）。
   **坐实需要再跑一次加权实验 —— 按你的降载指示我没做**，故只登记现象与排除项，**不下结论**。
   （该敏感性是**这条锁的既有设计属性**：它要求"单文件 + canary key 真跑一条真 LLM 用例"，
   故必然依赖真网络往返 + 模型加载；**不是本次替换引入的**。但我**未**测旧 node 的耗时做 A/B，
   所以**不能**断言两者等价 —— 见第 3 条。）
3. **⚠️ 未做 A/B：旧 node（`test_mood_detector.py::TestRealAPI::test_real_detection_flow`）的 canary 路径耗时**。
   它已被删除、且其产品模块也不在（无法在本 worktree 内跑）。我**刻意没有**去 `/home/a/k61-wt` 跑它
   （避免在别的 worktree 留测试副作用）。可读到的差异：旧 node 是 **1 次** `detector.detect()`；
   替身是 **1 次** `advisor.generate()`（实测端到端 27.74s、产出 5 个领域）—— 替身的**真跑**更重。
   但**失败路径**（canary→401）单发实测 0.06s，故"更重"是否影响 300s 上限**未验**。
4. **未验：k61 分支自身**是否因本替换而有任何变化（②③ 都未动，① 是本分支新增的改动）。
5. **② 的锁（你要求的第 ⑤ 条）未加**：它要求"常量在本文件内就地定义、不得 import"，
   而现状是**从别处 import** → 现在加它就是**提交一条红测试**。它与 ② 同批做才自洽，故**随 ② 待批**。
6. **未改宽任何断言**：① 只换 node 字面量 + 改 docstring；断言、子进程参数、canary 写法、
   pin 清理、`timeout` 全部逐字未动（§1.1）。②③ **一行未动**。
7. **未动 `src/`、未合并、未推送、未碰生产**。e2e 真跑用的是**临时 cwd + 只含 ZHIPU_API_KEY 的 `.env`**，
   生产其余键未进测试进程；GLM 一律免费 `glm-4-flash`。
8. **观察到一条与本次改动无关的守卫告警**（登记备查，非我造成）：
   上面 e2e 真跑时 teardown 出现过
   `[k61 生产数据守卫] 生产数据 mtime 有变化，但**本会话未打开**过它们（判据已按 r7 改为 fd 看门狗）：['/mnt/d/fortune-data/vectordb_v2/chroma.sqlite3']`
   —— 按 r7 的判据它是**告警非失败**，且明示"本会话未打开过"，我未追查其来源。

---

## 8. 待你一句话的三件事

1. **② 内联**：批准「按事实源内联**完整 54 条**」（而非报告的 24）→ 我连同第 ⑤ 条锁一起做。
2. **③ 处置**：`test_e2e_mood_accuracy_reports_only` 选 (a) 删 / (b) 显式 skip / (c) 另立项？我倾向 **(a)**。
   （②③ 同批做完，k62 守卫才会转绿。）
3. **§7 第 2 条的负载敏感性**：是否要我等 18:00 后补一次加权 A/B 把根因坐实？（不补也不影响 ① 的结论。）
