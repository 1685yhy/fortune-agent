# k62 死代码拆除报告 — 早期「情绪-人设」残留管线

- **批次**：k62 · 分支 `k62-deadcode` · worktree `/home/a/k62-wt` · 起点 main `ea110c3`
- **纪律**：只提交、不合并、不推送、不碰生产
- **状态**：全部拆除项完成 + 防复发守卫（改前红 → 改后绿）+ 定向子集 350 通过 / 0 失败

---

## 0. 一句话结论

产品里**从来没有**「人设预设」。仓库里那簇东西是**真死代码**：两个 0 引用模块 +
三个**对每个用户恒等于同一个值**的风格权重，它们既没接进产品，又靠一堆绿测试
（`tests/test_mood_detector.py` 34 例）伪装成产品能力 —— 正是上次骗过审查的那种。
本批已整体拆除，并加守卫锁死，同时**未动任何活路径、未 DROP 任何 DB 列**。

---

## 1. 逐项独立复核（引用计数 + 用的命令）

### 1.1 `src/engines/mood_detector.py` — ✅ 确认为死代码（189 行，已删）

| 复核手段 | 命令 | 结果 |
|---|---|---|
| 原始 grep | `grep -rn "mood_detector\|MoodDetector" --include="*.py" .`（排除 `.git`） | `src/` 下仅 **2** 处：自身类定义 + `emotion_soother.py:50` 的 **docstring 提及**（"Uses the SAME MoodDetector pattern"），**0 处 import** |
| AST 导入面 | 遍历 `src/**/*.py`，解析 `ast.Import` / `ast.ImportFrom` | **`src/` 内 importer = 0** |
| 动态导入面 | AST 扫 `importlib.import_module(<字面量>)` / `__import__(<字面量>)` | 0 命中 |
| 非 `.py` 面 | `grep -rn ... --include="*.md" --include="*.json" --include="*.sh" --include="*.yaml" ...` | 仅 `docs/ACCEPTANCE_CRITERIA_AI_NATIVE.md`（已同步标注撤销） |
| 目录扫描式注册 | `grep -rn "pkgutil\|importlib.import_module\|iter_modules\|__import__" src/` | 唯一命中 `src/images/__init__.py`（与本模块无关） |

**根因（git 证据，不是猜的）**：接线在 **`96586b0`「Task 0: Simplify personality
system from 3 modes to 1 unified tone」** 被摘除，diff 里能看到
`-from src.engines.mood_detector import MoodDetector, MoodResult`、
`-self.mood_detector = MoodDetector(...)`、`-mood_result = self.mood_detector.detect(...)`
—— **文件忘了删**，留成一簇带绿测试的孤儿。

### 1.2 `src/engines/emotion_soother.py` — ✅ 确认为死代码（122 行，已删）

| 复核手段 | 结果 |
|---|---|
| `grep -rn "emotion_soother\|EmotionSoother" --include="*.py" .` | `src/` 下仅 **2** 处：自身类定义 + `message_analyzer.py:3` 的 **docstring 提及**，**0 处 import** |
| AST 导入面（同上） | **`src/` 内 importer = 0** |
| 全仓非 `.py` 面 | 0 命中 |

**根因**：它**从未接线** —— 引入它的 commit `525aa4e` 在**同一个 commit 里**就加了取代它的
`src/engines/message_analyzer.py`（其 docstring：「Replaces two sequential AI calls
(EmotionSoother + IntentClassifier)」）。**生下来就是死的**。
`git log -S "EmotionSoother" -- src/bot/handler.py` → **0 命中**（handler 从未 import 过它）。

> ⚠️ 被它 docstring 引用的活路径是 `MessageAnalyzer`（`src/bot/handler.py:31`）。
> 拆 `emotion_soother` 对活路径**零影响**。

### 1.3 三个退化风格权重 — ⚠️ **控制方给的机制描述有误，结论正确且更强**（已删）

**控制方原话**：「三条权重在 `learn()` 里用同一公式、同一输入更新 → 归一化后**恒等（≈1/3）**」

**我实测复核后修正**：机制不是「恒等 ≈1/3」。

（a）`learn()` 只更新 `style` 形参**点名的那一个**权重（`if/elif` 三分支），另两个**不动**，
然后才归一化。

（b）所以它是**自强化**的：`handler._handle_feedback:2400` 喂进来的
`style = prefs.preferred_style`，而 `preferred_style` = 三者的 `max()` → **永远强化当前的
领先者** → 领先者恒为领先者。**初始领先者由建表默认值决定**：`style_gentle DEFAULT 0.34`
> 另两个 `0.33`。

（c）**实测（真实代码路径，非推演）**，调用**真的** `PreferenceDAO.learn(style=prefs.preferred_style)`：

```
=== A. 30x 全好评 (👍) ===  终值 0.0476/0.0476/0.9049  preferred_style='gentle'
=== B. 30x 全差评 (👎) ===  终值 0.3300/0.3300/0.3400  preferred_style='gentle'   ← 中途 sassy→analyst→gentle 确定性 3-循环
=== C. 交替 👍👎 x15   ===  终值 0.2943/0.2943/0.4115  preferred_style='gentle'
=== D. 换一个用户 10x 👍 ===  终值 0.1107/0.1107/0.7785  preferred_style='gentle'
```

**三条轨迹终值全部 `'gentle'`；四种输入下 `preferred_style` 从未离开过 `'gentle'`（除全差评
场景的确定性 3-循环外）。** 权重数值确实会动（不是恒等 1/3），但**与用户反馈无关**——
它只反映「哪一列被建表默认值设成了 0.34」。证据原文：
`.superpowers/sdd/k62-evidence/degeneracy-proof.txt`（worktree 内 `/tmp/k62-evidence/`）。

→ **该字段不携带任何用户信息**（比「恒等 1/3」更彻底的退化），
且它经 `to_prompt_hint()` 进了**活提示词**（`_get_preference_hint` → `/api/calendar/daily|week`
的 `preferences=`）。**同意拆除。**

### 1.4 追加项 `src/llm/report_prompts.py: personality_prompt` — ✅ 确认死参数（已删）

| 复核手段 | 命令 | 结果 |
|---|---|---|
| 调用点 | `grep -rn "build_scenario_system_prompt" .`（排除 `.git`） | **全仓仅 1 处 = 定义行本身**（含 `tests/`、`scripts/`、`/mnt/e/fortune-agent-deploy/` 全盘）→ **函数 0 调用** |
| 形参传入 | `grep -rn "personality_prompt" .` | 仅 3 处，全在该函数**定义 / docstring / 函数体**内 → **0 处传入** |
| 动态面 | `grep -rn "getattr.*report_prompts\|report_prompts\.\|import_module.*report_prompt"` | 0 命中 |
| 是否公开接口 | 模块被谁 import：仅 `src/bot/handler.py:8621`，且**只 import 两个常量**（`STRUCTURED_REPORT_PROMPT` / `SCENARIO_FOCUS_PROMPTS`），**不 import 本函数** | **非接口**（非 HTTP 端点、非包导出、无任何调用方） |
| 约束面 | `tests/test_k52_compliance_scan.py:66 PROMPT_PY_REQUIRED` 只要求**文件存在** | 改函数不违约束 |

**处置**：按追加指令**删掉 `personality_prompt` 形参**（签名
`(personality_prompt: str, category: str = None)` → `(category: str = None)`），
`combined = personality_prompt + "\n\n" + STRUCTURED_REPORT_PROMPT` → `= STRUCTURED_REPORT_PROMPT`。
这样「sassy/analyst/gentle 三档人设」在仓库里的**最后一处痕迹**消失，不留死分支。

**⚠️ 额外发现（请控制方拍板，我未擅自扩大）**：该函数**本身全仓 0 调用**，
其功能与活路径 `src/bot/handler.py:8621-8627`（内联组合同两个常量）**重复**。
我按指令只删了形参、保留函数，并在其 docstring 里写明「⚠️ 本函数当前全仓 0 调用，
活路径是 handler 内联组合，勿重复接线」。**是否整体删除该函数，请拍板。**

---

## 2. 删了什么 / 特意保留了什么

### 2.1 删除清单

> 行数 = `git show --numstat HEAD`（精确值，非估算）。

| # | 对象 | 增/删 |
|---|---|---|
| 1 | `src/engines/mood_detector.py`（整文件） | +0 / −189 |
| 2 | `src/engines/emotion_soother.py`（整文件） | +0 / −122 |
| 3 | `tests/test_mood_detector.py`（整文件，34 例收集） | +0 / −506 |
| 4 | `src/storage/preference_dao.py`：`style_sassy/analyst/gentle` 属性、`last_style` 属性、`preferred_style` 属性、`learn()` 里三个权重的更新+归一化（原 156-169 行）、`get()` 反序列化、`_upsert()` 序列化（INSERT 列/占位/`ON CONFLICT` 集）、`get_accuracy_dashboard()` 的 `preferred_style` 键、`learn(style=)` 形参、`to_prompt_hint()` 的风格行 | +28 / −49 |
| 5 | `src/api/user.py`：`style_names` 映射、响应 `preferred_style` + `preferred_style_key`（含 DAO 未就绪分支的 `preferred_style: None`） | +5 / −5 |
| 6 | `src/api/dashboard.py`：`preferences.preferred_style` + `preferences.style_breakdown` | +3 / −6 |
| 7 | `src/bot/handler.py`：`_handle_feedback` 里的 `prefs` / `current_style` 与 `learn(style=...)` 传参 | +1 / −6 |
| 8 | `src/llm/report_prompts.py`：`personality_prompt` 形参 | +11 / −5 |
| 9 | `tests/test_emoji_cleanup.py`：**仅** `TestEmotionSoother` + `TestMoodDetector` 两例 | +4 / −28 |

合计（不含守卫与本报告）：**+52 / −909**；
守卫 `tests/test_k62_deadcode_removal_guard.py` **+592**；报告 **+293**。
提交总计 **979 insertions / 925 deletions / 15 files**（`git show --numstat`）。

### 2.2 特意**保留**的（及理由）

| 保留项 | 理由 |
|---|---|
| **`user_preferences` 的 4 个 DB 列**（`style_*` ×3 + `last_style`） | **硬约束：生产库有数据，禁 DROP**。只删代码引用，列入注解「已废弃（k62 移除代码路径，未 DROP COLUMN）」。实测旧行非默认值逐字节保持（见 §3） |
| `src/engines/advisor_v2.py` **整体**（含 `personality_label` 性别分支「毒舌闺蜜/理性分析师」） | **控制方明示不归 k62**（k11-B 成果，生产在用；k63 改口吻）。**一行未动** |
| `src/bot/night_persona.py`（及 handler 的引用） | 活代码（深夜语气），**一行未动** |
| `src/ml/quality_predictor.py`（含 `PERSONALITY_MAP = {"sassy":0,"analyst":1,"gentle":2}`） | 活代码（E4 ML）。**注意**：它里面的 `sassy/analyst/gentle` 字样**不在本批范围**，故我的守卫**不用全仓 grep 这些词**（那会误伤它），只按**具体文件 + AST 标识符**判定 |
| 话题权重 / 长度偏好 / 好评率 整条链（`topic_*`、`prefer_short`、`accuracy_pct`） | **真实学习到的信号**（实测按用户区分）。守卫显式断言它们**仍在**，防「一刀切删干净」误伤 |
| `to_prompt_hint()` 函数本体 | 只摘**风格那一行**；话题/长度/准确率提示保留 —— 它是 `/api/calendar/daily|week` 的活输入 |
| `tests/test_emoji_cleanup.py` 其余 8 个测试类（12 例） | emoji 收敛是 k10/k47 活功能。**逐条判定后只删死模块那 2 例** |
| `src/llm/report_prompts.py` **文件与两个常量** | 活路径（`handler.py:8621`）在用；k52 合规扫描要求文件存在 |
| `preferred_topic` / `topics` / `accuracy` 响应字段 | 活字段，保留 |

---

## 3. DB 列处置（硬约束 1）

- **未执行任何 DDL**：无 `DROP COLUMN`、无 `DROP TABLE`、无 `ALTER`。
  `CREATE TABLE IF NOT EXISTS` 保持原样 → **存量库的列与数据完全不动**；新库照旧建出这 4 列。
- 列旁已加注：`⚠️ 已废弃（k62 移除代码路径，未 DROP COLUMN）` + 依据说明。
- `_upsert()` 的 INSERT 不再列出这 4 列 → 新写入行走**建表默认值**（`0.33/0.33/0.34/''`），
  `ON CONFLICT DO UPDATE` 也不碰它们。
- **生产库仿真实测**（`/tmp/k62-evidence/legacy-db-check.txt`）：造一行带**非默认**存量值
  （`0.2755/0.2755/0.4491`, `last_style='gentle'`, `feedback_count=7`）→
  `get()` 不崩、`learn()` 正常学话题/计数，**四个列逐字节不变**：

  ```
  legacy row BEFORE : (0.2755, 0.2755, 0.4491, 'gentle')
  legacy row AFTER  : (0.2755, 0.2755, 0.4491, 'gentle')   topic_career=0.2665 feedback=8
  ```
- **顺带发现（改前行为，已随拆除消失）**：改前 `learn()` **无条件**跑归一化
  （`total_style = 0.33+0.33+0.34 = 1.0`，但存量行可能是 `1.0001`），
  于是**每次反馈都会把这些列重写一遍**（浮点漂移），即旧代码本就在悄悄改写存量列。
  现在不再触碰。
- **破坏性迁移**：**无需**，未申请、未执行。

---

## 4. 接口契约变更 + 消费方核实方式（硬约束 2）

**删掉的响应字段**（4 个键，2 个端点族）：

| 端点 | 删除的键 |
|---|---|
| `GET /api/user/preferences` | `preferred_style`、`preferred_style_key` |
| `GET /api/dashboard/{user_id}` → `preferences` | `preferred_style`、`style_breakdown` |
| `GET /api/user/{user_id}/accuracy`（= `get_accuracy_dashboard`） | `preferred_style` |

**消费方核实方式（5 路交叉，全部 0 命中）**：

1. `grep -rn "preferred_style\|style_breakdown\|preferred_style_key\|style_names" .`（排除 `.git`）
   → 命中**只有**：本批改的 3 个 py 源文件 + `docs/API.md`（文档，已同步更新）+ 守卫自身常量。
2. **小程序端**：`grep -rni` 在 `miniprogram/` 搜 `preferred_style / preferredStyle /
   style_breakdown / styleBreakdown / style_sassy / preferred_style_key` → **0 命中**；
   `grep -rn "user/preferences\|dashboard" miniprogram/` → **0 命中**（前端根本没调这两个端点）。
3. **camelCase 变体**（`preferredStyle` / `styleBreakdown` / `styleSassy`…）全仓非 py 面
   （`miniprogram/ docs/ config/ cow-config/ deploy/ security/ scripts/`）→ **0 命中**。
4. **部署目录** `/mnt/e/fortune-agent-deploy/` 全盘搜 → 命中只有该目录里 **`src/` 的同一份源码副本**
   （+ `docs/API.md`），**无独立前端消费方**。
5. **测试面**：`grep -rln "preferred_style\|style_breakdown" tests/ scripts/` → **0 命中**
   （**无任何测试断言过这些字段** —— 这也是它们能一直藏着没人发现的原因之一）。
   另注：`grep -rn "\.learn(" --include="*.py" .` → 全仓**仅 1 处调用**（`handler.py:2415`），
   **无测试直接调 `learn()`**。

**结论**：确无消费方 → 按授权删字段，并已同步 `docs/API.md` 的响应示例（留痕）。
**未发现任何消费方**，故**未触发「停下报我」**。

> 附带核实：`miniprogram/pages/love/love.js` 的 `personalityAnalysis` 是**合婚性格分析**
> （`data.personality_analysis`，hehun 接口），与「3 档人设」**无关**，不受影响。

---

## 5. 防复发守卫（硬约束 3）：改前红 → 改后绿

**文件**：`tests/test_k62_deadcode_removal_guard.py`（24 例）
**改前副本**：`git archive ea110c3 | tar -x -C /tmp/k62-baseline`（**删除前的 pristine 副本**，
零 repo 状态改动），把**最终版**守卫拷进去跑。

| | 命令 | 结果 |
|---|---|---|
| **改前（红）** | `env -C /tmp/k62-baseline python3 -m pytest tests/test_k62_deadcode_removal_guard.py -q` | **17 failed / 7 passed** |
| **改后（绿）** | `python3 -m pytest tests/test_k62_deadcode_removal_guard.py -q`（k62 worktree） | **24 passed / 0 failed** |

逐例证据：`.superpowers/sdd/k62-evidence/guard-{PRE,POST}-final-unique.txt`

**改前 17 例红**（锁「新状态」的守卫）：死模块文件仍存在 ×2、无 import 面、dataclass 无退化属性、
DAO 源码无标识符、`to_prompt_hint` 无风格字样、`learn()` 无 style 形参、
`_get_preference_hint` 无风格字样、handler 不读 `preferred_style`、`/api/user/preferences` 无字段、
dashboard 无字段、accuracy 无字段、两个 API 源文件无标识符、
`report_prompts` 无 personality 通道、emoji 用例（死模块两例仍在）、
**存量旧行被改写**（§3 那条 —— 改前 `learn()` 归一化会漂移存量值 → 红）。

**改前 7 例绿 / 改后仍绿**（**故意的「不许碰」守卫**，锁的是「活代码与红线仍在」）：
`test_learn_writes_no_dead_style_weights`（列冻结 + 新库默认值）、
`test_personalized_context_has_no_style_words`（该函数本就不含风格）、
`test_schema_still_declares_style_columns`（**禁 DROP**）、
`test_no_drop_column_on_user_preferences`、`test_comment_scan_cannot_hide_a_real_drop`（机制自检）、
`test_live_personality_paths_untouched`（advisor_v2 性别人设 / night_persona / quality_predictor）、
`test_scan_face_floors_hold`（扫描面下限）。
**这 7 例改前绿是设计使然** —— 它们的作用是拦住「把活代码当残留删掉」这**反向事故**，
若它们改前就红，说明守卫写反了。

**守卫的防改宽设计**（沿用 k59 口径）：
- 扫描面 `src/**/*.py` + `scripts/**/*.py`，**逐 glob 文件数下限**（src ≥200 / scripts ≥130，实测 205/142）+ 总数下限 330（实测 347），只许升不许降；
- **只认「可执行引用」**：AST 取 `Name/Attribute/keyword/AnnAssign 目标`，注释/docstring 里解释性提到**不算**（k59 同款「注释不是可执行写用法」）。这既防误报（本批在列旁写了大量废弃说明），也**防不住复活**——复活一个权重必然产生代码标识符；
- 动态接线只锁 `importlib.import_module(<字面量>)` / `__import__(<字面量>)`；
- **反向事故守卫**：显式断言 advisor_v2 的 `personality_label` + 「毒舌闺蜜/理性分析师」、`night_persona.py` 存在且被 handler 引用、`quality_predictor.PERSONALITY_MAP` 仍在、emoji 用例 8 个类 + ≥10 处 `assert_no_emoji` 仍在；
- 无白名单。

---

## 6. 测试数字（只跑定向子集，未跑全量）

**环境**：`TMPDIR=/dev/shm nice -n 10 ionice -c2 -n7`；全程 **0 次真实 LLM 调用**
（所选文件全部 mock；`test_bot.py` 实测用 `MessageAnalyzer(api_key="")`，无 `load_dotenv`，
**未碰生产 DeepSeek**）。

| 运行 | 结果 |
|---|---|
| 定向子集（18 文件：守卫 + emoji_cleanup + k52 合规 + k62 相关 handler/api/calendar/偏好链） | **350 passed, 0 failed**（16.5s） |
| 其中 `tests/test_bot.py`（最大相关件） | 65 passed |
| 其中 `tests/test_k62_deadcode_removal_guard.py` | 24 passed |

**测试数量变化（诚实列账）**：

| 文件 | 改前 | 改后 |
|---|---|---|
| `tests/test_mood_detector.py` | **34 收集（32 passed + 2 skipped）** | **整文件删除** |
| `tests/test_emoji_cleanup.py` | 14 passed | **12 passed**（只少死模块那 2 例） |
| `tests/test_k62_deadcode_removal_guard.py` | — | **+24**（新增守卫） |
| `tests/` 下 `.py` 文件数 | 226 | 226（删 1 加 1） |

→ **产品能力零损失**：删掉的 34 例测的是 `src/` 里 0 引用的模块，删掉的 2 例同理。

---

## 7. 诚实披露（含我改过的判断 / 未做的事）

1. **控制方给的退化机制描述不准确**（§1.3）。原文「同一公式、同一输入 → 归一化后恒等 ≈1/3」
   —— 实测权重**会**分化（全👍 收敛到 `0.05/0.05/0.90`），**不是**恒等 1/3。
   但**结论方向正确且更强**：`preferred_style` 对所有用户恒为 `'gentle'`（建表默认值决定），
   **完全不含用户信息**。我按实测修正了报告与代码注释措辞，未照抄原话。
2. **我把 `last_style` 一并删了**（列保留未 DROP）。它不在原 brief 的清单里，理由：
   ① 它是「上一次用的人设」，唯一下游就是这三个权重；② `learn(style=)` 形参被删后它只能恒为 `''`；
   ③ 唯一调用方 `handler.py:2400` 用的是 `prefs.preferred_style`，属性被删后**该行必然要改**。
   留着 = 一个永远写 `''` 的形参与列引用，正是本批要清的残留。**如认为超出授权，请指示，我可只回退这一项。**
3. **`build_scenario_system_prompt` 我按指令只删形参、保留函数**，尽管实测它**全仓 0 调用**
   且与 `handler.py:8621` 内联逻辑重复。整体删除**超出**追加指令给的两个选项（删参数/给默认值），
   故**未做，报你拍板**（已在 docstring 写明「0 调用、勿重复接线」防误判）。
4. **文档同步（留痕）**：`docs/API.md`（删 2 处示例字段）、`docs/DATABASE.md`（列标废弃 +
   拆除依据 + 接口字段变更）、`docs/ACCEPTANCE_CRITERIA_AI_NATIVE.md`
   （P0.1「用 MoodDetector 替换关键词匹配」**划掉并标注撤销** —— 该验收项本身就是
   基于死模块写的，不标注会让下一个人按它去恢复 dead code）。
5. **未跑全量**（按指令，高峰时段）。**未跑 LLM 类测试**（按指令）。
6. **未做的两件「更大的事」**（仅在报告提出，未擅动）：① `report_prompts.build_scenario_system_prompt`
   整体删除；② 其他 0 调用函数的系统性清查（不在本批范围）。
7. **`data/memory/.json`**：跑测试时被运行期写入（`_updated_at` 时间戳），
   属**测试副作用**，我已 `git checkout` 还原，**未纳入提交**。
8. **提交范围**：本分支仅 1 个提交，**未合并 / 未推送 / 未碰生产**。

---

## 8. 交付物

- 分支 `k62-deadcode`（起点 `ea110c3`），**唯一 1 个提交** = 该分支 tip（`git log -1` 取哈希；
  本文件在该提交内，故不自我引用哈希）。**未合并 / 未推送 / 未碰生产**
- 守卫：`tests/test_k62_deadcode_removal_guard.py`（24 例，+592 行）
- 证据：`.superpowers/sdd/k62-evidence/`（PRE/POST 逐例结果、退化实测、旧库仿真）
- 本报告：`docs/superpowers/task-k62-report.md`（worktree 内）+
  `/mnt/e/fortune-agent-deploy/.superpowers/sdd/task-k62-report.md`

## 9. 需要控制方拍板的 2 件事

1. **`last_style` 是否允许一并删除**（我删了形参+属性+序列化，**列保留未 DROP**）。
   它不在原 brief 清单里，但它是 `learn(style=)` 的唯一下游、且该形参因
   `preferred_style` 被删而必然要改。§7.2 有完整理由，回退只需一处。
2. **`report_prompts.build_scenario_system_prompt` 是否整体删除**：
   实测**全仓 0 调用**，与 `handler.py:8621-8627` 内联逻辑重复；我按追加指令
   只删了 `personality_prompt` 形参、保留了函数（并在 docstring 标注「0 调用、
   勿重复接线」）。是否整体删，请拍板（§7.3）。
