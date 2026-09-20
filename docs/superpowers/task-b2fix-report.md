# 批次二审查收口报告（b2fix · ① 第 6 处证伪说法 + ② 报告入库 + ③ Minors）

- **批次**：b2fix · 分支 `batch2-reviewfix` · worktree `/home/a/b2fix-wt` · 起点 `batch2-integration` **`1dae341`**
- **纪律**：**只提交、不合并、不推送、不碰生产**；未改任何行为逻辑、**未改宽任何断言**
- **环境**：`TMPDIR=/dev/shm nice -n 10 ionice -c2 -n7`；**只跑定向子集**（高峰时段，**未跑全量**）；
  本批**未调用任何 LLM**（无真实 LLM 证据需求）
- **上一轮**：`/mnt/e/fortune-agent-deploy/.superpowers/sdd/task-batch2-review.md`（合批审查，I-1/I-2 + M-1~M-5）
- **本报告随其所在提交入库**（与 k62/k64/fixup 的惯例一致）

---

## 0. 结论

| 项 | 结果 |
|---|---|
| **① 第 6 处（Important）** | **已按实测逐句改写**（不是删句）；并**另找到 2 处同族残留**（第 7 处 = k62 报告 §0；第 7b 处 = §10.2 边界表），**一并更正** |
| **② 报告入库（Important）** | k65 报告**已入库**；**并核实发现 k63 报告同样缺失 → 一并入库**（k64 已在库） |
| **③ Minors（5 条）** | **M-1~M-4 全部落地**；**M-5 按控制方口径保持登记**，并写明"为什么本批不做" |
| 行为/断言改动 | **零**（AST 级证明见 §5.1；唯一语义变化是 1 条 **assert 消息字符串**） |
| 本批 r2 | **自查订正**：新 docstring「旧措辞更正」段原先用「前两句／第三条」指代 4 条引文，**"第三条"会被误读**（被证伪的是第 3 条，而"数字为真"的是所引轨迹证据）⇒ 改为编号 ①②③ 逐条点明处置。**内容判定不变，仅消歧义**（仍为 docstring-only，剥离 docstring 后 AST 与前一提交相同）|
| 定向子集 | 守卫五连 **126 passed / 0 failed**；25 文件并集 **742 passed / 1 skipped / 0 failed** |
| 生产 | **零写入**（`data/memory/.json` sha 全程恒 `32b535d9…`，每轮跑前跑后 `git status` 仅本批改动） |

---

## 1. ① Important：第 6 处被证伪的说法（`src/storage/preference_dao.py` 类 docstring）

### 1.1 一手复现（**未采信任何转述**：用历史真实代码文本 exec，只把持久化换成内存字典）

方法：`git show ea110c3:src/storage/preference_dao.py` → 原文 exec（唯一外部依赖
`models.connect` 打桩），`PreferenceDAO.get/_upsert` 换成内存 dict 以去掉 sqlite ——
**`learn()` 的函数体逐字节是历史原文**；调用口径用 handler 的真实口径
`learn(uid, sign, style=prefs.preferred_style)`（`ea110c3:src/bot/handler.py:2400/2415`）。

| 轨迹 | 终值（sassy/analyst/gentle） | 终值 `preferred_style` | 中途访问过的风格 | `to_prompt_hint()` |
|---|---|---|---|---|
| 30× 全👍 | `0.0476/0.0476/0.9049` | `gentle` | {gentle} | 「…用户偏好风格：**温柔陪伴**…」 |
| **10× 全👎** | `0.3675/0.3675/0.2650` | **`sassy`** | {sassy, analyst, gentle} | 「…用户偏好风格：**毒舌直接**…」 |
| 12× 全👎 | `0.3300/0.3300/0.3400` | `gentle` | {sassy, analyst, gentle} | 「…**温柔陪伴**…」 |
| 15× 全👎 | `0.3300/0.3300/0.3400` | `gentle` | {sassy, analyst, gentle} | — |
| 5👍+7👎 | `0.3370/0.3370/0.3261` | **`sassy`** | {sassy, analyst, gentle} | 「…**毒舌直接**…」 |
| 交替 👍👎 ×30 | `0.2943/0.2943/0.4115` | `gentle` | {gentle} | — |

- **全差评＝确定性 3-循环** `sassy→analyst→gentle`（t=1 `0.3675/0.3675/0.2650`、
  t=2 `0.2891/0.4130/0.2979`、t=3 回到 `0.33/0.33/0.34`）—— 与上一轮两位审查者的
  逐点数字**三方吻合**。
- **权重确实会分化**：30×👍 时另两列**一动不动**（`0.0476/0.0476`），只有被点名的那一列在动。
- 起步：`get()` 对不存在的行返回 `UserPreferences(user_id=…)` ⇒ **dataclass 默认
  `0.33/0.33/0.34`**；对存在的行走 DB 列（DDL 默认同值）⇒ 两者都落在 `'gentle'`。

### 1.2 逐句判定表（原句 → 成立/不成立 → 依据 → 改成什么）

| # | 原 docstring 原句 | 判定 | 依据（一手实测/代码） | 改成什么 |
|---|---|---|---|---|
| 1 | 「三者由**同一公式、同一输入**更新」 | **半不成立**：公式同（同一 EMA 表达式），**"同一输入"不成立** | `learn()` 是 `if/elif` 三分支 —— **每次反馈只改 `style` 点名的那一列**，另两列原地不动（实测：30×👍 全程 `sassy==analyst==0.0476`），随后才归一化 | **删**；改为「用 `if/elif` **只更新 `style` 形参点名的那一列**，另两列原地不动，随后才归一化」 |
| 2 | 「其中**恒为当前 argmax** 的那一个被自强化」 | **成立** | 唯一调用方 handler 传 `style=prefs.preferred_style`，而 `preferred_style` = 三列 `max()` ⇒ 被更新的列恒是**当时的 argmax** | **保留并点明后果**：「⇒ 它是「把自身输出当输入」的自强化回路」 |
| 3 | 「故 `preferred_style` **对所有用户恒为**建表默认值决定的 `'gentle'`」 | **不成立** | 反例：**10×全👎 → `'sassy'`**、5👍+7👎 → `'sassy'`、全差评是 3-循环（t=10 sassy / t=11 analyst / t=12 gentle） | **删**；改为「起步落在 `'gentle'`（建表/dataclass 默认 0.34），**此后每一跳只取决于「上一跳的 argmax + 本次反馈正负」**」 |
| 4 | 「（实测 30 次全👍/全👎/交替 三条轨迹终值均 `'gentle'`）」 | **数字为真，但作为论据不成立**（是**取样口径**造成的错觉） | 我复跑得同值；但三条轨迹的**取样点 t=30 都是 3 的整数倍**，恰好落在 3-循环的 gentle 相位 —— t=10 就出 `'sassy'` | **保留数字 + 标注为错觉**：「30 是 3 的整数倍，三条轨迹恰好都取样在 3-循环的 gentle 相位，在 t=10 取样就会看到 `'sassy'`」 |
| 5 | 「**不携带任何用户信息**」 | **不成立**（措辞不准） | 取值依赖**用户产生的反馈符号序列**；且它**进了活提示词**并断言了一个"用户偏好" | **改为**：「**它不编码任何用户特异的风格偏好，只是反馈符号序列的确定性函数**（等价于一个计数器落在哪个相位）」 |
| 6 | 「却经 `to_prompt_hint()` 进了**活提示词**」 | **成立**（且是必须删的**第二个**理由） | 活调用链实测：`handler.py:10688` / `main.py:2085,2121` / `api/calendar.py:178` 的 `_get_preference_hint` → `calendar.daily\|week(preferences=…)` → `src/engines/calendar.py:278` 进 prompt | **保留并加强**：「以**中文名**当"用户偏好"注入活提示词——全差评用户也会被写上「用户偏好风格：毒舌直接」，即**注入用户从未表达过的偏好**。**这才是删除依据。**」 |
| 7 | （隐含）「它只是"退化/常数"，删了没损失」 | **不成立** | 同 #3/#6 | 删；`models.py` 的指路同步写明「**删除依据不靠上面这些数字**，而在于它不编码用户特异的风格偏好」 |

> **⚠️ 对任务书一处措辞的如实修正**：任务书写的口径是「只是**反馈计数**的确定性函数」。
> 实测**计数不够**：**同为 12 次**，全👎 → `'gentle'`（`0.3300/0.3300/0.3400`）而
> 5👍+7👎 → `'sassy'`（`0.3370/0.3370/0.3261`）⇒ 严格说法是
> **反馈符号序列的确定性函数**（不是"计数"）。docstring 与 `models.py` 均按此写。

### 1.3 `models.py` 的指路 —— **指向改正后的文本**（未改成"不指路"）

`src/storage/models.py:77-81`（新建表注释内）现为：明确「删除依据不靠上面这些数字，
而在于**它不编码用户特异的风格偏好，只是反馈符号序列的确定性函数**」，并注明
「准确机制与旧措辞更正见 `preference_dao.py` 类 docstring（2026-09-20 已按一手实测更正，
**原先它指向的那几句是被证伪的旧说法**）」。
⇒ 读者顺着指路走到的**已经是改正后的文本**，且知道"旧说法被更正过"。

### 1.4 第 7 处扫描（全仓，含 `docs/` `tests/` `src/` `scripts/`）

搜索面（字面串，逐个全仓 `git grep`）：`同一公式` / `同一输入` / `对所有用户恒为` /
`恒为 ` / `恒等` / `不携带任何用户信息` / `不携带信息` / `对所有用户` /
`恒为 'gentle'` / `style_sassy|style_analyst|style_gentle` / `退化` / `3 模式人设|三模式人设` /
`没接进产品|接进产品`。

**命中共 3 处「未更正」的（本次全部更正）**：

| 处 | 位置 | 载有的错说法 | 处置 |
|---|---|---|---|
| **第 6 处** | `src/storage/preference_dao.py` 类 docstring | #1/#3/#5（+#4 作论据） | **按 §1.2 改写** |
| **第 7 处** | `docs/superpowers/task-k62-report.md` **§0**（原第 14-16 行） | 「三个**对每个用户恒等于同一个值**的风格权重」+「它们**既没接进产品**」 | 加**更正块**（原文保留不重写）：①「恒等于同一个值」不成立（权重会分化 / `preferred_style` 起步为 gentle 但会被推走）；②「没接进产品」对两个 0 引用模块成立、**对三个权重不成立**（进了活提示词）；③「未动任何活路径」精确化为「未动任何活路径的**代码**」 |
| **第 7b 处** | `docs/superpowers/task-k62-report.md` **§10.2 边界表**（k62 行「拆**死代码**（**没人调用的**）」） | 同族（"没人调用"） | 表下加**口径更正块**：准确边界是「拆的是**没有产品价值**的残留」，不是"都没接进产品" |
| （附带） | 同上报告 §1.3 更正块的自述范围「**本报告仅 §1.3 本段与 §7.7 两处被更正**」 | **计数笔误**（漏计 §7.1；现又加 §0/§10.2） | 改为「**共 4 处**：§0 / §1.3 / §7.1 / §7.7」并注明是计数笔误 |
| **第 7c 处** | `tests/test_k62_deadcode_removal_guard.py:249` 的 **assert 消息** | 「退化权重，**无信息量**」（＝被证伪的"不携带任何用户信息"的压缩版） | 消息改为与同文件 `:250-252` 一致的口径（**断言条件逐字未动**，见 §5.1 证明） |

**已核实"本来就是对的"（不是新实例，未动）**：
`src/storage/models.py:69-79`、`docs/DATABASE.md:177,187-200`、
`tests/test_k62_deadcode_removal_guard.py` **docstring §2**（正确地写了"自强化回路 +
注入未表达偏好"+「删除决策因此更强，而不是因为它是常数」）、
`docs/superpowers/task-integration-fixup-report.md:111-112,247,254`。

**扫描结论**：除上表外，**其余命中全部是"引用被证伪的旧说法以更正它"**（更正块/裁决记录），
不存在第 8 处。**`docs/`、`tests/`、`scripts/`、`miniprogram/` 内无其他载有该错说法的文本。**

---

## 2. ② Important：k65 报告入库（**并发现 k63 也缺**）

### 2.1 先核：`docs/superpowers/` 到底缺哪几份

| 报告 | 入库前状态 | 处置 |
|---|---|---|
| `task-k62-report.md` | 在库 ✔ | 不动（本批只补更正块） |
| `task-k63-report.md` | **缺失** ❌（只在 `.superpowers/sdd/` 与 `/home/a/k63-wt/`） | **一并入库** |
| `task-k64-report.md` | 在库 ✔ | 不动 |
| `task-k65-report.md` | **缺失** ❌（I-1 点名） | **入库** |
| `task-integration-fixup-report.md` | 在库 ✔ | 本批按 Minors 补更正块 |

（核法：`ls docs/superpowers/*.md` + `git ls-files docs/superpowers` +
`git log --all --diff-filter=A -- '**/task-k63-report.md'` → **空** ⇒ k63 报告从未入库。）

### 2.2 入库方式与内容

- 源：`/mnt/e/fortune-agent-deploy/.superpowers/sdd/task-k63-report.md`、
  `…/task-k65-report.md`（与 `/home/a/k63-wt` / `/home/a/k65-wt` 副本 `diff -q` **逐字节相同**）
- 入库 sha256（**入库前源文件**）：
  - k63 `b8f8eff8f15015662901083b4fcb02fb3747607a22108add12f5ebf1805b21db`
  - k65 `ed4a9a9be79c4648d7a08fdd114b123c67f2de381d76eaedd6c01dfe024d4ab2`
- **内容＝最终版**：k65 含 **r1 + r2 + r3 三段全文**与文末 **`LEGACY-FABRICATION-01`（P1）** 登记项
  （目录核对：`# k65 **r2** 追加` / `# k65 **r3** 追加` / `# k65 遗留登记项 → 转独立批次` 均在）；
  k63 含 **r1 + r2 + T103 输入同步**（§10 交付物列到 `7427363`/`9de35b1` 收口提交）。
- **除各加一段「入库说明」外，正文逐字节未改**（入库说明只加在文件顶部，内容为：
  入库来源/sha、**除本段外逐字节相同**、以及"读时需一起看的后续更正指路"）。
  - k63 的指路：§9.5 描述的是 k63 交付时的 D 段状态，**其后集成修复把 D 段召回
    10/18 → 18/18**（断言 41→49 只增），见 fixup 报告 §③；
  - k65 的指路：§r3-6 的 `741/1` 是**在 k65 自己 worktree 上分段读数相加**，集成 tip 上
    同文件**并集**为 `742/1/0`（详见 §3 M-4）。

---

## 3. ③ Minors：逐条处置

### M-1「两条用例同机制只点名一条」→ **已补齐（守卫注释 + 报告两处）**

脏化 `data/memory/.json` 的是 `tests/test_bot.py` 里的**两条**用例：
`test_handle_voice_with_text_routes_through_process`（原已点名）与
**`test_voice_message_type_routing`**（同机制：`_handle_voice(voice_text=…)` →
`process(text, "", …)` 空 uid）。已补齐处：
- `tests/test_k62k63_fixup_side_effect_guard.py` 机制节（新增 ⚠️ 段，说明二者同属
  **模块级 autouse fixture** 覆盖面 ⇒ **修复不受影响**，点名其一只是取"最小可复现面"）；
- `docs/superpowers/task-integration-fixup-report.md` §1.2 复现链第 1 条下。

### M-2「另 6 个文件同类写入未点出」→ **已如实登记（守卫"边界"节 + 报告）**

登记内容（**明确禁止"已经全隔离了"的误读**）：除 `test_bot.py` 外，实测另有 **6 个测试文件**
写进**仓库内 `data/memory/`**：`test_eval_r1_2`(40) / `test_k11b_search_trigger`(29) /
`test_k15_eval_tails`(17) / `test_eval_r1_1`(12) / `test_member_pay`(9) / `test_fastpath`(2)；
写的是 `user123.json` / `eval_user_*.json` 等**未被跟踪且被 `.gitignore:35 data/memory/*.json`
忽略**的文件（uid 非空 ⇒ 落不到 `.json`）⇒ **当前无 git 影响，但本守卫与"副作用已隔离"
这句话都不覆盖它们**；**登记项 R-1 一旦落地，这 6 个必须一起看**。

### M-3「反驳理由不精确」→ **已按事实改写**

原写「把 `"category": "事业"` 判成回显 = 把每条正常建议的领域名清掉」/「拦它会把所有
合法分类洗掉」。**实测两处源码后确认该理由在生产行为上不成立**：
① `src/engines/advisor_v2.py:143-152` 的 `scrub_schema_echo` **只作用于 7 个呈现字段**
（`advice`/`timing`/`concrete_steps`/`success_metric` + `serendipity`/`daily_tip`/`style_notes`），
**`category` 根本不在 scrub 名单里**；② 生产侧没有把 `is_schema_echo` 接到 `category` 的调用点
（`is_schema_echo` 只被 `guard_schema_echo` 与 k63 守卫消费）。
⇒ 已改写成**正确理由**：「`category` 是 schema 要求模型**照抄**的**合法输出值域**，
属"模型该输出的内容"而非"对模型说的话"，**不该计入规格泄漏的召回缺口**（计入＝污染分母 18→19）」。
**结论（18 条口径、8 条真缺口）不变**，只订正理由表述。

### M-4「计数差」→ **已核实并更正（并把每次读数钉到"哪棵树/哪个清单"）**

| 读数 | 树 / 时点 | 方法 | 结果 |
|---|---|---|---|
| k65 §r3-6 | **k65 自己的 worktree**（分段读数相加） | 4 段子集分别跑 | `75+331+218+117 = 741 / 1 / 0` |
| 合批审查者 | `1dae341`（`b2-wt`） | 上述 25 文件**并集** | **742 / 1 / 0** |
| **本次 b2fix 复跑** | `1dae341` 起点（`b2fix-wt`） | 同 25 文件**并集** | **742 / 1 / 0**（158.51s）✔ 与审查者同值 |
| **本次 b2fix 复跑** | 同上 | **逐段**复跑 | **75 / 331 / 221 / 115 = 742** ⇒ 报告 **S3 记 218 实为 221**、**S4 记 117 实为 115** |

⇒ **差 1 的成因＝"换树取样 + 分段记账"，不是失败或回归**（三处读数**均 0 failed**，
`skipped` 恒为 **1**，三处一致）。已在 **k65 报告入库说明**与
**fixup 报告新增「计数订正 M-4」节**里写明"哪棵树/哪次读数/哪个文件清单"。

| 读数 | 树 / 时点 | 方法 | 结果 |
|---|---|---|---|
| fixup 报告原记 | 集成修复工作树 | `pytest <20 files> -q`，**原命令未留文件清单** | `502 / 14 / 0` —— ⚠️ **无法逐字复现** |
| 合批审查者重建 | `1dae341` | 按文字描述重建的 20 文件切片 | `472 / 14 / 0` |
| **本次 b2fix 重建** | `1dae341` 起点 | **19 文件**（清单已写进 fixup 报告 M-4 节，可逐字复跑） | **471 / 14 / 0**（167.95s） |

- **可复现的锚点是 `14 skipped`**：三个读数跳过数完全一致、**均 0 failed** ⇒ 差异**全部来自文件选择**。
- **旁证"原清单确实没留档"**：仓库里 `tests/test_calendar_*` **只有 3 个**，原描述的
  "calendar×4" 无法成立。
- 已把 fixup 报告里「未跑全量…最宽证据 = 20 文件 **502** passed」改为引用 M-4 节的重建读数。

### M-5「宽 except 审计项」→ **保持登记**，并写明**为什么本批不做**

- **现状核实**：k64 报告 §6 该审计项**仍是登记态**（`_free_chat` / `scrub` 等多处
  `except Exception: pass` 仍在）。
- **处置**：**不实施**，在 fixup 报告「登记项」表新增 **R-5** 行并写明理由：
  ① 属**行为/控制流改动**（收窄异常类型或让异常冒泡）⇒ **超出本批授权**
  （本批授权 = 只改注释/docstring + 补报告）；
  ② 需要**扫描面 + 分类清单**（区分"运行故障该降级"与"编程错误不该吞"）⇒ 按控制方口径**单开一批**；
  ③ k61 那批守卫**不得不继承 `BaseException`** 正是因为该兜底面 ⇒ 收窄前必须先有守卫钉住
  "降级档仍可用"，否则会把降级能力一起打掉。
- 该行显式标注它**正是本批 P1 能被藏住的机制**，保证它在批次台账里**保持可见**。

---

## 4. 交付物

| 文件 | 说明 |
|---|---|
| `src/storage/preference_dao.py` | 类 docstring 按 §1.2 逐句改写（**唯一改动 = docstring**） |
| `src/storage/models.py` | 指路改指"改正后的文本"+ 写明真实删除依据（**唯一改动 = 建表注释**） |
| `tests/test_k62_deadcode_removal_guard.py` | **1 条 assert 的消息字符串**（条件逐字未动） |
| `tests/test_k62k63_fixup_side_effect_guard.py` | docstring：M-1 补第二用例 + M-2 登记 6 文件边界 |
| `docs/superpowers/task-k65-report.md` | **新增入库**（I-1） |
| `docs/superpowers/task-k63-report.md` | **新增入库**（先核发现也缺） |
| `docs/superpowers/task-k62-report.md` | §0 更正块 + §10.2 口径更正块 + §1.3 更正范围计数订正 |
| `docs/superpowers/task-integration-fixup-report.md` | M-1/M-2/M-3/M-4 + 登记项 **R-5** |
| `docs/superpowers/task-b2fix-report.md` | 本报告 |

**未动**：`tests/conftest.py`（本 worktree 无此文件）与 **k61 的守卫文件**
（按并行纪律，k61 r8 在 `k61-registered-r2` 分支上改它们）；未动 `src/` 下任何行为逻辑；
未动任何既有断言的**条件**；未跑全量；未碰生产。

---

## 5. 验证证据（命令 + 读数，**全部在本 worktree 实跑**）

### 5.1 "只改注释/不动断言"的**机器证明**

| 文件 | 证明方法 | 结果 |
|---|---|---|
| `src/storage/preference_dao.py` | `ast.dump` 剥离 docstring 后比对 HEAD vs 工作区 | **完全相同** ✔ |
| `tests/test_k62k63_fixup_side_effect_guard.py` | 同上 | **完全相同** ✔ |
| `tests/test_k62_deadcode_removal_guard.py` | **把所有字符串常量置为 `<STR>`** 后再比对 AST | **完全相同** ✔（⇒ 唯一差异＝**字符串常量**，即 assert 的消息；`attr not in names` 条件逐字未动） |
| `src/storage/models.py` | 逐行 diff，检查每条改动行 | 7 条改动**全部是 SQL `--` 注释**（在 SCHEMA 字符串内，SQLite 忽略） ✔ |

> 脚本：`/dev/shm/b2fix/astcheck.py`、`/dev/shm/b2fix/astcheck2.py`；
> 复现：`git diff -U0 -- src/ | grep -E '^[+-]'` 可见全部为注释/docstring 行。

### 5.2 定向子集（**只跑子集，未跑全量**）

| 运行 | 文件 | 结果 |
|---|---|---|
| 守卫五连 | `test_k62_deadcode_removal_guard` + `test_k62k63_fixup_side_effect_guard` + `test_k63_schema_echo_guard` + `test_k64_quality_predictor_removed` + `test_k65_lite_single_system` | **126 passed / 0 failed**（改后复跑同值；分解 26+3+63+10+24） |
| 25 文件并集（含"被改文件的实际消费者"） | k65 §r3-6 四段子集之并集（含 `test_bot` / `test_k11_fact_discipline` / `test_eval_r1_1|2` / `test_fastpath` / `test_member_pay` …） | **742 passed / 1 skipped / 0 failed**（158.51s） |
| 19 文件重建切片（对照 fixup 的 502） | 见 fixup 报告 M-4 节清单 | **471 passed / 14 skipped / 0 failed**（167.95s） |

### 5.3 零副作用（每轮跑前跑后各一次）

- `sha256sum data/memory/.json` → 全程恒
  `32b535d94f85c0609b4ad787a1c5bc717bef020a65c461a240136c209db500f1` ✔
- `git status --short` → 每轮跑完**只有本批有意改动的 5 改 + 2 新增**，**无其他脏化** ✔

---

## 6. 未做 / 未验（如实列出）

1. **未跑全量**（按纪律，高峰时段）⇒ 不能声明"全仓无回归"；最宽证据 = 25 文件 **742 passed**。
2. **本批未调用任何 LLM**（无需要）；因此**未新增真实模型证据**（本批不涉及模型行为）。
3. **fixup 报告原记的 502/14 仍无法逐字复现**（原 20 文件清单未留档，见 §3 M-4）；
   我给的是"按文字描述重建"的 471/14/0 + 可逐字复跑的清单。
4. **未审 `batch2-k61`（`bdcaaa8`）多出来的 k61 那一段**（含三处撞车点修复）——
   本报告只对本 worktree 的 `1dae341` 出发点负责。**建议合并时在 `bdcaaa8` 上复跑
   §5.2 的守卫五连**（本批**未**为 k61 守卫/`conftest.py` 写入任何依赖）。
5. **未把 k63/k65 的 evidence 目录入库**（`k62-evidence/` 也从未入库 ⇒ 属既有惯例，
   本批不为 k63/k65 破例；如需一并入库，请控制方指示）。
6. **未改 `2a67833` 的 commit message**（按控制方裁决不重写历史）—— 合并信息里请继续注明
   "该提交信息里的说法已被后续提交取代"。
7. **未在部署目录执行任何代码/测试**；对 `/mnt/e/fortune-agent-deploy` 的写入仅限
   `.superpowers/sdd/` 下的报告文件（该目录 `.gitignore` 为 `*`）。

---

## 7. 给控制方的一句话

① 与 ② 均已按"说准"而不是"删句"完成（含**主动多找到的第 7 / 7b / 7c 处**）；
Minors 全部落地或按口径保持登记；**行为与断言零改动**（AST 级可复核）。
**本批唯一需要控制方注意的**：入 ② 时发现 **k63 报告也从未入库**，已一并补齐；
以及 M-4 的结论是**"两次读数取自不同的树 + 原清单没留档"**，**没有任何失败**。
