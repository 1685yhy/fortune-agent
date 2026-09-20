# k64 报告：移除 ML 回答质量预测器（E4）+ 未实现能力登记 + 防复活守卫

- 批次：**k64-quality-predictor**（分支 `k64-quality-predictor`）
- Worktree：`/home/a/k64-wt`
- 基线：**已按裁决一 fast-forward 合到 `k62k63-integration` tip `49a3e19`**（原起点 main `ea110c3`）
- 时间：2026-09-20 13:55 ~ 14:2x CST
- 交付形态：**已提交，未合并、未推送、生产零写入**
- 一句话：**删掉一个「只写不读」的在线学习模块；顺带修掉它引发的一个 P1 缺陷（反馈回复被吞）；把「ML 回答质量预测 / 低质量自动重试」登记为未实现能力；加 AST 守卫防复活。**

---

## 0. 执行摘要（一处一行）

| # | 位置 | 动作 | 行数 |
|---|---|---|---|
| 1 | `src/ml/quality_predictor.py` | 整文件删除 | −154 |
| 2 | `src/bot/handler.py:76` | 删 import（相邻 `src.utils.cache`(75) / `src.memory.user_memory`(77) 未动） | −1 |
| 3 | `src/bot/handler.py:2300-2301` | 删 `# E4:` 注释 + `self.quality_predictor = QualityPredictor()` | −2 |
| 4 | `src/bot/handler.py:2416-2430` | 删整个 E4 训练块（含 `_last_emotion_labels` 死引用 + `update()` 调用） | −15 |
| 5 | `src/bot/handler.py:2400, 2407-2408` | **裁决二**：删 write-only 的 `last_msg` 三行 | −3 |
| 6 | `src/bot/handler.py:2382-2390` | **能力未实现登记**（F1-F3 段头注释块） | +8 |
| 7 | `tests/test_k62_deadcode_removal_guard.py:622-624` | **裁决一**：k62 那条「文件必须存在」断言**就地反转为「不许复活」** + 模块 docstring 红线清单同步 | ±若干 |
| 8 | `tests/test_k64_quality_predictor_removed.py` | 新增防复活守卫（AST 口径 + 行为面） | +新文件 |

`src/bot/handler.py` 的 `git diff` 只有 5 个 hunk，**全部落在 `_handle_feedback` 与其段头**，引擎/支付/存储零改动。

---

## 1. 独立复核：「预测质量、决定重试」这条读路径 = 0 调用

### 1.1 文本口径（全仓，排除 `.git`）

```
$ grep -rn "QualityPredictor" .                 → 只有 模块定义自身 + handler 两处接线
$ grep -rni "quality_predictor" .               → handler 76/2301/2427（接线①②③）
$ grep -rn "quality_model" .                    → 只有模块自身的默认路径字面量
$ grep -rn "should_retry" .                     → **只有模块自身的定义行**
$ grep -rn "src\.ml\|src/ml\|import ml\b" .     → **只有 handler.py:76**
$ grep -rn "_last_emotion_labels\s*=" .         → **0 命中**（从无写入方）
$ grep setattr/getattr + quality (handler.py)   → 0 命中
```

### 1.2 AST 口径（584 个 `.py` 全扫）

自写脚本 `/tmp/k64_ast_scan.py`，捕捉 5 类面：`Import` / `ImportFrom`（module + name）/ `Name` / `Attribute` / **任意字符串常量**（专覆盖 `importlib.import_module(<字面量>)`、`__import__(<字面量>)`、`getattr(obj, "<字面量>")` 花活）。

```
src/bot/handler.py:76  [ImportFrom]      src.ml.quality_predictor
src/bot/handler.py:76  [ImportFrom-name] src.ml.quality_predictor.QualityPredictor
src/bot/handler.py:2301 [Attribute]      quality_predictor
src/bot/handler.py:2301 [Name]           QualityPredictor
src/bot/handler.py:2427 [Attribute]      quality_predictor
src/ml/quality_predictor.py:44 [StrConst] '/opt/fortune-agent/models/quality_model.npz'
TOTAL HITS: 6
```

→ **0 意外命中**：除 3 处已知接线，没有任何动态 import / getattr 接线。改后同一脚本重跑 = **0 命中**。

### 1.3 读方法面

`predict()` / `should_retry()` **只被类自己内部调用**（`update()` 调 `self.predict`；`should_retry` 调 `self.predict`），类外**零调用** → 「决定要不要重试」的预测结果**从没有消费方**。

### 1.4 常量特征复核（结论：比预判更糟，见 §5）

| 特征 | 核对 |
|---|---|
| `personality` | `update()` 里是**必填位置参数、无默认值**；调用点**根本没传** → 直接 TypeError（§5） |
| `response_len` | 硬编码 `0` ✔ |
| `emotion_label` | 源 `self._last_emotion_labels` 全仓只读不写 → 恒 `"neutral"` ✔（跨 28 worktree 复核见 §6） |
| 模型路径 | `/opt/fortune-agent/models/quality_model.npz` 不存在 → 每次随机初始化 ✔ |
| 测试/其它引用 | `tests/` 0 命中（改前）✔ |

---

## 2. 三处接线的周边核对（逐处，未误删）

**① `handler.py:76`**（相邻 75 `src.utils.cache`、77 `src.memory.user_memory` 两个活 import）
**② `handler.py:2300-2301`**（上 2299 `self.cache = ResponseCache(...)`、下 2302 `# Phase 3: User Memory System` + 2303 `self.memory_system = UserMemory()` 两个活初始化）
**③ `handler.py:2416-2430`** —— 块所在函数是 `_handle_feedback`（`:2386` 起），删后结构：

```python
2402        try:
2403            # Detect topic from last conversation
2404            last_topic = ""
2405            if self.session_dao:
...               user_msgs = [...]                    ← 仍被 " ".join(user_msgs) 读，保留
                  last_msgs = " ".join(user_msgs)      ← 保留（detect_topic 在用）
                  last_topic = self.preference_dao.detect_topic(last_msgs)   ← 保留
2411            # Learn preference (EMA)
2412            updated = self.preference_dao.learn(user_id, is_positive, topic=last_topic)  ← 保留
2415
2416            if is_positive:                       ← 原 2431（块后第一行活逻辑）
```

**上方 `topic=last_topic,` 与下方 `if is_positive:` 一字未动**；`user_msgs` / `last_msgs` / `last_topic` **全部保留**（`detect_topic` 仍在消费）。

---

## 3. 裁决一执行：k62 守卫反转（含"同文件相邻改动"的实况）

### 3.1 k62 改的是同一个函数、相邻行（实况）

`diff k64(改前) vs k62-wt` 的全部 hunk：

```
@@ -2395,10 +2395,6 @@   ← k62 删 2398-2400（prefs / current_style）+ 空行
@@ -2411,10 +2407,9 @@   ← k62 改 2414 注释 + 删 2417 style=current_style,
```

k62 最后改到 **2417**，我第一处改动 **2421**，中间 **3 行未改** → 改动行集不重叠。**执行方式**：按裁决一改用 **fast-forward 合到 `k62k63-integration`（`49a3e19`）**，k62 的提交**一个字节没动**（`git log` 里 k62 两笔提交 `2a67833` / `8210ddd` 原样），我的改动成为其**下游**。

### 3.2 断言反转（就地，带归属注释）

```python
    # 2) ML 质量预测器（E4）—— **k64 已按控制方裁决（用户拍板）整模块删除，故本断言反转**。
    #    k62 当时把它判为「活代码」是**按引用面判的**（handler 里有 update() 调用），
    #    k64 的独立复核证明它**只写不读**：两个读方法 predict()/should_retry() 全仓 0 调用，
    #    且唯一调用点缺必填参数 personality → 每次反馈抛 TypeError 被 except Exception
    #    吞掉（P1，k64 修复）。反转后口径：**文件不许复活**（同 k64 守卫
    #    tests/test_k64_quality_predictor_removed.py，此处只作跨批守夜，不重复其 AST 扫描）。
    assert not (ROOT / "src/ml/quality_predictor.py").exists(), \
        "quality_predictor.py 复活 —— 该模块已按控制方裁决（k64）删除，" \
        "该能力当前未实现，要做需重新立项"
```

同文件 docstring 的「红线」第 2 条也随之更新（把 `quality_predictor` 从"必须保留"清单移入"k64 例外：已反转"，并写明**不要加回来**）——否则文件自相矛盾。

**未越界的三件事**：① 没删 k62 的用例，只反转其中一条；② 没碰 k62 的扫描面下限（`SCAN_GLOBS` / `MIN_FILES_PER_GLOB` / `MIN_SCANNED_FILES` 全原样）；③ 没设白名单。
**顺带核对**：k62 的 `src/**/*.py >= 200` 下限在本批删 1 个 src 文件后仍成立（实测 205 → **204**，余量 4），**k62 守卫删后仍绿**（见 §8）。

---

## 4. 裁决二执行：`last_msg` 核了一遍**整个函数**，确认无读者 → 一并清

在 `_handle_feedback` **全文（2386-2444）** 内枚举 `last_msg`：

```
2400    last_msg = ""                     ← 初始化（写）
2408    last_msg = user_msgs[-1]          ← 赋值（写）
2417    if last_msg:                      ← 读（在待删块内）
2423    message=last_msg,                 ← 读（在待删块内）
```

- 2417 / 2423 随块删除后，**函数内读者 = 0** → **write-only 成立** → 按裁决二一并删掉 2400 与 2407-2408。
- 排除了"隐式读者"：函数内 `grep locals\(\)|globals\(\)|eval\(|exec\(|getattr\(|vars\(|\*\*` = **0 命中**；`last_msg` 也不作为实参传给别的函数。
- **`user_msgs` 是活的**（`" ".join(user_msgs)` → `detect_topic`），故 `2404`/`2405`/`2407` 的行只删了 `if user_msgs:` 与 `last_msg = user_msgs[-1]` 两行，`user_msgs` 本体与 `last_msgs` / `last_topic` 全保留。

---

## 5. 裁决三：P1 缺陷登记 —— **本批即其修复**

### 5.1 缺陷（登记原文）

> **P1｜`_handle_feedback` 的回复被宽 `except Exception` 吞掉（📊 认可率提示从不显示）**
> 位置：`src/bot/handler.py` `_handle_feedback`（原 2422 行 `self.quality_predictor.update(...)`）
> 根因：`QualityPredictor.update()` 的 `personality` 是**必填位置参数、无默认值**，调用点只传 keywords
> → 抛 `TypeError: update() missing 1 required positional argument: 'personality'`
> → 被同函数 `except Exception`（原 2442）静默吞掉 → **原 2431-2440 的 `感谢认可！` 与
> `📊 你的认可率：X%（N次反馈）`、≥80% 鼓励文案整段被跳过**，用户只拿到兜底句；
> 且模型 `n_updates` **恒为 0**（异常在进入函数体前抛出，「训练」从未发生）。
> **修复：k64 删除该块。登记为 P1（用户可见行为变化），不是"顺带清理"。**

### 5.2 端到端实证（改前 = `49a3e19`，即将要合入的集成态；真实模块 + 真实调用）

```
被吞掉的真实异常 : TypeError -> QualityPredictor.update() missing 1 required positional argument: 'personality'
端到端 _handle_feedback('👍') 返回: '感谢反馈！'          ← 兜底句，不是「感谢认可！」
模型训练次数 n_updates      : 0                          ← 从未训练
```

改后同一装桩：

```
改后 _handle_feedback('👍') 返回: '感谢认可！\n📊 你的认可率：90%（7次反馈）\n🎯 超过80%了！我已经很了解你的偏好了，之后的回答会更贴合你的口味～'
实例上还有 quality_predictor 吗 : False
```

### 5.3 修复后的可观测变化（明确列出）

1. 反馈后重新出现 **「📊 你的认可率：X%（N次反馈）」**（`is_mature` 且 `accuracy_pct` 非空时）；
2. 认可率 **≥80%** 时重新出现 **「🎯 超过80%了！…」** 鼓励文案；
3. 👍 回复回到 **「感谢认可！」**、👎 回到 **「收到反馈，我会调整的～」**（此前两者都被吞成兜底句）；
4. 不再每次反馈抛一次被吞的异常（省一次异常构造 + traceback 开销）。
**不受影响**：偏好 EMA 学习在原 `learn()`（块之前）已执行，删块不动它。

### 5.4 验证方式（已实装为常驻用例，将来可随时补跑）

`tests/test_k64_quality_predictor_removed.py::test_feedback_reply_reaches_maturity_branch`（及 `..._negative_...`）——
`object.__new__(MessageHandler)` 装 `preference_dao`（`learn()` 返回 `is_mature=True, accuracy_pct=90, feedback_count=7`）与 `session_dao`（返回一条 user 消息），直接调 `_handle_feedback("👍", "u1")`，断言返回文本含 `感谢认可！` / `你的认可率` / `90%` / `7次反馈` / `超过80%`。
**改前红、改后绿**（实测，见 §7）。**本批已跑**（10 例全绿），无需全量。

---

## 6. 裁决四登记（新增审计项，本批不修）

> **登记｜建议单批审计"宽 `except Exception` 吞掉编程错误"的面**
> 形态：`except Exception:` 把**必然是 bug、不是运行故障**的异常（至少 `TypeError` /
> `AttributeError` / `NameError` 三类）静默吞成"降级成功"，于是缺陷以"用户只看到兜底文案"
> 的形式长期潜伏（本批 P1 就是实例：潜伏到无人发现，直到复核调用面才挖出来）。
> 旁证：k61 那批的守卫**不得不继承 `BaseException`**，正是因为被测代码普遍 `except Exception` 兜底降级。
> 建议：单开一批，扫描 `except Exception` / `except BaseException` / 裸 `except:` 的兜底面，
> 区分「预期的运行故障（网络/IO/解析）」与「编程错误（TypeError/AttributeError/NameError）」，
> 后者至少加日志/告警，不得静默降级。
> **本批只登记，不修**（遵守跨批边界）。

---

## 7. 未实现能力登记（三处落笔）

「**ML 回答质量预测 / 低质量自动重试**」**当前未实现**（曾有此模块，但读路径从未接入，已于 k64 移除）：

1. **代码内**：`src/bot/handler.py` 的 `# Feedback Learning (F1-F3)` 段头（§0 第 6 项），写明"只写不读 / 调用点抛异常被吞 / 已整模块移除 / 要做请重新立项 / 守卫在哪"；
2. **k62 守卫内**：反转处注释（§3.2）同样写明"该能力当前未实现，要做需重新立项"；
3. **本报告 §5 + §7**。

---

## 8. 守卫与验证

### 8.1 守卫设计（`tests/test_k64_quality_predictor_removed.py`，10 例）

1. **文件不存在**（`src/ml/quality_predictor.py`，另排除同名包/目录）；
2. **AST 口径全仓 0 接线**：扫 `src/**` + `scripts/**` + `tests/**`（排除自身与 `__pycache__`），捕 `Import` / `ImportFrom`(module+name) / `Name` / `Attribute` / **动态 import 字面量**（`importlib.import_module(<字面量>)`、`__import__(<字面量>)`）；
   - 另断言 **`parse_failed` 为空**：任何一个 `.py` 解析失败就报红，**不许扫描面有洞**；
   - **不单独断言 `should_retry`**（通用词，日后别处写个无关重试函数会被误伤）——此取舍已写进文件 docstring，理由同 k62 报告 §10「不做越界断言」；
3. **`_handle_feedback` 内不得再有 `quality_predictor`/`QualityPredictor` 名字或属性、不得再有 `_last_emotion_labels`**（按 AST 名判，注释不算）；
4. **扫描面下限**（只许升不许降）：`src>=200` / `scripts>=135` / `tests>=215` / 合计 `>=560`（实测 204/142/227/573）；
5. **机制自检**（防守卫恒绿）：探测器对 4 类静态接线、名字/属性用法、2 类动态 import **必须命中**；对纯注释 / docstring / 普通字符串提及 **必须不命中**；
6. **行为面 P1 锁定**：§5.4 的两例（👍 / 👎 都必须走到成熟度分支）。

### 8.2 改前红 → 改后绿（实测）

**改前**（`49a3e19` + 仅放入守卫文件）：
```
FAILED test_module_file_removed
FAILED test_no_wiring_anywhere_ast
FAILED test_handler_feedback_block_gone
FAILED test_feedback_reply_reaches_maturity_branch          ← 实测返回 '感谢反馈！'
FAILED test_negative_feedback_reply_reaches_maturity_branch ← 实测返回 '收到，会继续改进～'
5 failed, 5 passed in 3.41s
```
**改后**：`10 passed in 2.34s`。

### 8.3 定向子集（未跑全量）

| 子集 | 结果 |
|---|---|
| `test_k64_quality_predictor_removed.py` + `test_k62_deadcode_removal_guard.py` + `test_k63_schema_echo_guard.py` + `test_b3_2_footer_removed.py` + `test_handler_analysis_flow.py` + `test_handler_pillar_k7d.py` + `test_handler_qa_fix.py` + `test_bot.py` + `test_emoji_cleanup.py` | **202 passed, 0 failed**（38.35s） |
| `test_fastpath.py` + `test_k18_direct_reads.py` + `test_intent_routing.py` + `test_tool_scene_routing.py` + `test_chart_reuse.py` + `test_bazi_dash_format_regression.py` + `test_r13_profile_routing.py` + `test_chat_entry_fixes.py` + `test_member_quota_limit.py` | **154 passed, 0 failed**（3.36s） |
| **合计** | **356 passed, 0 failed** |

环境：`TMPDIR=/dev/shm nice -n 10 ionice -c2 -n7`，venv `/home/a/fortune-agent/.venv/bin/python`（pytest 9.1.1；**`/home/a/fortune-agent/venv` 无 pytest，别混用**）。本批**无 LLM 调用**（未触发任何模型，故不涉及 glm-4-flash 选择）。全量由控制方今晚统一安排。

### 8.4 旁证：k62 守卫在本批之后仍绿

`test_k62_deadcode_removal_guard.py` 已含在 §8.3 第一子集内 → **绿**；其扫描面下限（src ≥200，实测 204）与"反向事故"断言（`DROP COLUMN` 不在代码里 / 列仍在 SCHEMA）均未受影响。

---

## 9. 诚实披露

1. **本批没跑全量**，只跑 §8.3 两个定向子集（356 例）+ 守卫的改前红/改后绿；全量按安排留今晚。
2. **改前的端到端 P1 复现**用的是临时 detached worktree（`/tmp/k64-pre` @ `49a3e19`），**已 `git worktree remove --force` 清理**；未改动任何既有 worktree（`git worktree list` 已核对，28 个并行 worktree 原样）。
3. **改前红的守卫行为用例**（未 import 已删模块）触发的是 **`AttributeError`**，而生产上真实触发的是 **`TypeError`**——两者都落在同一条语句 `self.quality_predictor.update(...)` 上、被同一个 `except Exception` 吞掉，性质等价；**真实 `TypeError` 已由 §5.2 的端到端复现单独坐实**（真实模块 + 真实调用形状）。
4. **`_last_emotion_labels` 未做逐提交 `git log -S` 考古**：结论覆盖"当前 `49a3e19` + 28 个并行 worktree 现状"（k50 → k65 全历史快照里**只有读、从无写**）。若需要历史回溯请明示。
5. **`/opt/fortune-agent/models/quality_model.npz` 未去生产机核实**（本机无此路径）；k64 **不删任何 `/opt` 下文件**——若生产上确有该残留模型文件，是否清理是**另一个决定**，未做。
6. **未做 trial merge**：本批是 fast-forward 到集成分支，无合并冲突面（`49a3e19` 是 `ea110c3` 直系后代，`--ff-only` 成功）。
7. **`src/ml/` 未删除**：目录未变空（`src/ml/__init__.py` 在册、0 字节），删后成为"零模块的空包"；全仓无 `import src.ml`。按纪律**未擅自删目录**——如需连同空包一并删，请明示（`src/ml/__pycache__/quality_predictor.cpython-312.pyc` 这个陈旧字节码已顺手清掉，git 未跟踪）。
8. **§6 的审计项只登记未执行**；其中"三类异常"的清单是我基于严重性提的**建议范围**，不是穷举。
9. **未碰**并行批次禁碰文件（`advisor_v2.py` / `fact_guard.py` / `mood_detector.py` / `emotion_soother.py` / `preference_dao.py` / `api/user.py` / `api/dashboard.py` / `dream*.py` / `scripts/k55_dream/*`）；唯一"跨批文件"是 `tests/test_k62_deadcode_removal_guard.py`，且**仅按裁决一反转一条断言 + 同步其 docstring**。

---

## 10. 附录：改动清单（`git show --stat`）

见同批提交 `refactor: k64 ...`；核心 4 处：handler ×5 hunk、模块删除、k62 守卫反转、新守卫文件。
`git diff` 已逐 hunk 复核（§2），`py_compile` 三个改动文件通过。
