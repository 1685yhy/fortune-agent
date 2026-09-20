# k62+k63 合批审查「集成修复」报告

- **修复对象**：合批独立审查报告 `task-k6263-review.md`（Approved with issues，无 Critical）
  的 **2 项 Important + 6 项 Minor 中被点名的 4 项**（① 测试副作用入库 ② 守卫 docstring
  自相矛盾 ③ D 段召回缺口 ④ 报告更正）
- **工作线**：worktree `/home/a/fixup-wt`，分支 **`integration-fixup`**，起点 `90230d9`
  （已含 k62+k63+k64 三批）；**只提交、未合并、未推送、未碰生产**
- **本批提交**（4 个，均在 `90230d9` 之上；`git log --oneline 90230d9..HEAD`）：
  | # | commit | 内容 |
  |---|---|---|
  | ① | `f07570d` | 还原 `data/memory/.json` + `test_bot.py` 隔离修复 + 防复发守卫（新增） |
  | ② | `10e028a` | 风格权重机制措辞逐句更正（注释/docstring，零行为改动） |
  | ③ | `0dd1689` | D 段 schema 回显判据补召回（10/18 → 18/18），精度零退 |
  | ④ | 本文件所在提交 | 集成修复报告（本文件） |
- **环境**：`TMPDIR=/dev/shm nice -n 10 ionice -c2 -n7`；LLM 一律免费 **glm-4-flash**；
  **只跑定向子集**（最宽一次 20 文件 502 passed / 14 skipped / 0 failed，24.0s）；
  未跑全量、未部署、未写生产库

**红线遵守核对**：`src/bot/handler.py`（k65 在改）**零改动** · `src/llm/client.py` /
`src/llm/prompts.py` 零改动 · `src/membership.html` **未删未碰**（按控制方核实：
`main.py:2786` 仍引用它） · **无任何既有断言被改宽或删改**（`git diff` 无 `-assert` 行；
断言行 41 → 49 全是新增） · 唯一改到的既有 `assert` 是 **消息文本**（见 §②.4，条件与阈值逐字未变）

---

## ① 「测试脏文件被误提交」—— 事实确认 + 还原 + 根因修复 + 防复发守卫

### 1.1 事实（复核确认审查者与控制方判定）

- `git show --stat 2a67833` 里确有 `data/memory/.json | 2 +-`：`_updated_at` 由
  `2026-07-22T18:48:21.613527` 漂到 `2026-09-20T11:14:12.172074` ⇒ **该测试副作用
  确实被提交进了 k62 r1**，报告的"未纳入任何提交"与事实不符。
- **k63 / k64 有没有同样带上它？→ 没有。**
  `git log ea110c3..90230d9 -- data/memory/` 只有 `2a67833` 一条；逐条看
  `fbe4ec7` / `d218a32` / `c883df6` / `7427363` / `9de35b1` / `90230d9` 的 `--stat`
  都不含 `data/memory/`（k64 只碰 `handler.py` / `quality_predictor.py` / 两个测试 + 报告）。
  ⇒ 只需还原这一处。
- **已还原**：`data/memory/.json` 现与合并基点 `ea110c3` **逐字节相同**
  （`sha256=32b535d94f85c0609b4ad787a1c5bc717bef020a65c461a240136c209db500f1`，
  提交后 HEAD blob 同一哈希）。
  **另有同源遗留，已一并还原**：部署工作树 `/mnt/e/fortune-agent-deploy` 的同一文件
  也带着早期脏化（`_updated_at=2026-09-20T02:18:24`，mtime 02:18，早于本会话）——
  已按控制方裁决 `git restore` 回 HEAD blob（同哈希），详见下方"部署工作树同源遗留"节。

### 1.2 为什么会发生（实测定位到**具体用例与代码行**，不是"某处测试没隔离"）

复现链（每一环都实测过，不是读代码推的）：

1. `tests/test_bot.py::test_handle_voice_with_text_routes_through_process` 走**真实
   `process()` 主链**；`_handle_voice` 以 **`process(text, "", ...)`（空 uid）** 调用
   （该用例内注释就写着这条契约）；
   > **【同机制用例 · 2026-09-20 合批审查 M-1 补齐】脏化 `data/memory/.json` 的是
   > `tests/test_bot.py` 里的**两条**用例**，不止上面点名的那一条：另一条是
   > **`tests/test_bot.py::test_voice_message_type_routing`** —— 它同样走
   > `_handle_voice(voice_text=...)` → `process(text, "", ...)`（空 uid），
   > 落点与机制逐环相同。二者同属模块级 autouse fixture 的覆盖面，**修复不受影响**
   > （fixture 对模块内所有用例生效），此处补齐只为消除"只有一条"的误读。
2. `src/bot/handler.py::process` 内
   `self.memory_system.add_mood_record(user_id, analysis.emotion_label)`（空 uid）；
3. `UserMemory._path("")` → `<memory_dir>` + `".json"` = **`data/memory/.json`**；
4. 而 `MessageHandler.__init__` 的 `self.memory_system = UserMemory()` 取**默认目录**
   = **仓库内** `data/memory/`。

**"是否被 k61 的 `USER_MEMORY_DIR` 重定向覆盖"→ 没有覆盖。** `USER_MEMORY_DIR` 的既有
重定向只见于 `scripts/eval_agent/*`、`scripts/test_*.py`、
`tests/test_eval_l[1-4].py` / `tests/test_eval_e6.py`；**`tests/test_bot.py` 从未设置它**，
`tests/` 下也**没有 conftest.py** 做全局兜底（已确认仓库根与 `tests/` 均无 conftest）。

实测（改造前）：单独跑那**一条**用例 1.36s 就把 `_updated_at` 写成跑测时刻；跑完整
`tests/test_bot.py`（65 例）同样脏化。逐文件二分（`test_bot` / `intent_routing` /
`fastpath` / `k41_search_seam` / `adaptive_advisor` / `k52_compliance` /
`k64_quality_predictor` / `k39_image_persistence` / `eval_l1` / `k41_trusted_multi_user` /
`g1_person_api_gender`）→ **只有 `tests/test_bot.py` 会脏化它**。

> **【同类机制的更外面一层 · 2026-09-20 合批审查 M-2 如实登记（勿当成"已全隔离"）】**
> 上句"只有 `tests/test_bot.py`"限定的宾语是**受版本控制的那一个文件**
> （`data/memory/.json`，空 uid 落点）。**同一机制在更外面一层还有 6 个测试文件**：
> 它们同样把运行期状态写进**仓库内 `data/memory/` 目录**，只是写的是
> `user123.json` / `eval_user_*.json` 等**未被跟踪且被 `.gitignore:35 data/memory/*.json`
> 忽略**的文件 —— 因为它们的 uid 非空，落不到 `.json`。
> 实测清单（逐文件 sha 二分 + `UserMemory._save` 探针，写入次数）：
> **`test_eval_r1_2`(40) / `test_k11b_search_trigger`(29) / `test_k15_eval_tails`(17) /
> `test_eval_r1_1`(12) / `test_member_pay`(9) / `test_fastpath`(2)**（另 `test_bot`(17)
> 即上面已隔离的那一个）。
> **当前无 git 影响**（不入版本控制），但**不等于"测试副作用已全仓隔离"**；
> 一旦登记项 **R-1**（`data/memory/` 移出跟踪 / 加 ignore / 改默认目录）落地，
> **这一层必须一起看**，否则会重演"改了跟踪方式才发现在写目录"。
> 本条的边界口径与守卫 docstring「边界（如实登记）」节一致。

### 1.3 怎么防（已落地，非建议）

1. **根因修复**：`tests/test_bot.py` 加**模块级 autouse fixture**，把 `USER_MEMORY_DIR`
   重定向到 `tmp_path_factory` 临时目录（沿用 k61 既有机制）。**只换目录，未改任何
   断言、未改被测行为**：改后 `tests/test_bot.py` **65 passed**（与改前同数），且
   `data/memory/.json` 逐字节不变。
2. **防复发守卫**（新增 `tests/test_k62k63_fixup_side_effect_guard.py`，3 例）：
   - **A 仓库面**：工作区 `data/memory/.json` 必须与 HEAD blob 逐字节相同 ——
     即"跑完定向子集后该文件不得变化"。任何会话把它跑脏（或再次误提交）立刻打红，
     打红信息里直接给还原命令 + "要修写入方隔离，不要只还原"（k62 就是只还原漏了一次）；
   - **B 沙箱复现**（与运行顺序无关、真实仓库零字节写入）：把**当前工作区**的
     `src/ tests/ config/ data/memory/` 复制进 `tmp_path` 沙箱，**显式剔除
     `USER_MEMORY_DIR`**（否则测的是环境不是隔离）后跑那条真实主链用例，断言沙箱
     `data/memory/` **逐文件逐字节不变**；
   - **B2 守卫"有牙"证明（常驻）**：把沙箱副本里的重定向行去掉（模拟改前）再跑同一
     用例 → **必红**，即每次运行都现场复现一次"未隔离 → `data/memory/.json` 被写脏"。
     这样"本守卫确实能抓到 k62 那次事故"不依赖任何人的口头承诺。
3. **改前失败 / 改后通过 的两段证据**：
   - 守卫 A：**提交前红**（`data/memory/.json` vs HEAD blob 不一致，
     `39b28f18…` vs `32b535d9…`）→ **提交后绿**（两侧同 `32b535d9…`）；
   - 守卫 B2：去掉重定向即红（当场复现 k62 机制），保留即绿；
   - 全局面：20 文件定向子集跑完 `git status --short` **为空**（改造前跑同样的面会留下
     ` M data/memory/.json`）。

---

## ② 守卫 docstring 与自己证据矛盾 —— 按一手实测逐句更正

### 2.1 一手复现（`git archive ea110c3` 副本，handler 真实口径
`learn(style=prefs.preferred_style)`；**未采信任何转述**）

```
10× 全差评 → 0.3675/0.3675/0.2650  preferred_style='sassy'
             hint='【用户偏好】用户偏好风格：毒舌直接，关注话题：感情，历史好评率0.0%。'
12× 全差评 → 0.3300/0.3300/0.3400  preferred_style='gentle'（确定性 3-循环 sassy→analyst→gentle）
 5👍+7👎   → 0.3370/0.3370/0.3261  preferred_style='sassy' → hint 同样渲染「毒舌直接」
30× 全好评 → 0.0476/0.0476/0.9049  preferred_style='gentle'
10× 全好评 → 0.1107/0.1107/0.7785  preferred_style='gentle'（换用户同理）
```

### 2.2 原 docstring 错在哪（两句都不成立）

- 「归一化后**恒等**（≈1/3）」→ 假：权重**会分化**（30×全👍 终值 `0.0476/0.0476/0.9049`）；
- 「`preferred_style` 恒为 `gentle`」→ **过度概括**：它是**当时的 argmax**，默认起步落在
  gentle（建表默认 0.34），但反馈符号序列能把它推走（全差评 10 次 → `sassy`，hint 真渲染
  出「毒辣直接」）。k62 的 4 条轨迹之所以都停在 gentle，是因为**都在 t=30（3 的整数倍）
  取样**，正好落在循环的 gentle 相位上 —— 这是取样口径造成的错觉，不是机制。

### 2.3 更正后的口径（已写进长期留存物）

权重会分化；`preferred_style` = **当时 argmax**，与**反馈符号序列确定性相关、不携带
内容偏好信息**；hint 渲染的是**中文名**（毒舌直接 / 温柔陪伴 / 理性分析）。
⇒ 真正的缺陷是「**把自身输出当输入的自强化回路**」+「给用户**注入从未表达过的风格
偏好**（含已废止的「毒舌」口径）」—— **删除决策因此更强**，而不是"因为它是常数"
（原措辞把问题说小了）。

### 2.4 落点（全部是注释/docstring；**唯一一处 assert 只改了消息文本**）

| 文件 | 改动 |
|---|---|
| `tests/test_k62_deadcode_removal_guard.py` | docstring §2 整段重写 + 一处 `assert` 的**消息字符串** |
| `src/storage/models.py` | `user_preferences` 三列旁的废弃说明 |
| `src/api/user.py` / `src/api/dashboard.py` | 字段移除说明 |
| `docs/DATABASE.md` | §2.8 k62 拆除说明 |
| `docs/superpowers/task-k62-report.md` | §1.3、§7.1 就地加"更正"块（保留原文，标注已证伪） |

**边界声明**：`assert not hasattr(UserPreferences, "preferred_style"), "<消息>"` 的
**条件逐字未动**，只把消息里的"恒等 1/3 三选一，无信息量"换成准确措辞；
`git diff` 全文**无 `-assert` 行**；k62 守卫 **26 passed**（与改前同数）。
另：`2a67833` 的 **commit message** 也含同样错误措辞 —— 属已合并历史，按控制方裁决
**不重写**；由控制方在**合并提交信息里注明**"该提交信息中的说法已被后续提交取代"，
长期口径以本批更正后的注释/docstring 为准。

---

## ③ D 段 schema 守卫的召回缺口 —— 修了，精度实测未退

### 3.0 边界（控制方要求写进报告的原话）

> D 只接 `advisor_v2`（`:146`/`:150`）且 `advice` 命中会**整条建议摘除**
> → **误杀代价是用户少一条建议**，所以精度优先于召回。

这句话已同时写进 `src/utils/fact_guard.py` 的 D 段头注释（长期留存物）。

### 3.1 缺口复核（从**渲染后的 prompt** 抽值，不手抄、不采信枚举）

从 `AdaptiveAdvisor()._build_prompt(...)` 的 schema 块解出**全部叶子值**：
**23 条 = 18 条非 category + 5 条 category**（按出现次序）。
逐条喂 `is_schema_echo` → **8 条漏判**：

| 漏判 spec 原文 | 在 prompt 里出现次数 |
|---|---|
| `最佳时间窗口，包含具体日期范围` | **4**（domains 2-5 的 timing） |
| `具体的行动建议，结合八字五行的个性化分析` | 1 |
| `具体的行动建议，结合八字五行和神煞的个性化分析` | 1 |
| `具体的行动建议，结合五行失衡的个性化健康分析` | 1 |
| `具体的行动建议，结合命局的长远发展建议` | 1 |

**与审查者"9/19"的差 1（如实说明，控制方已采纳本口径）**：审查者的 19 条 = 15 条
action 字段值 + serendipity + daily_tip + style_notes + **1 条 `category` 取值**；其
9 条不命中里有 **1 条是 `category` 的合法取值**。⇒ 真实召回目标是 **18 条**，缺口是 **8 条**
（我按 18 条口径统计，并新增用例 `test_category_values_are_legitimate_not_echo` 显式钉住
"合法取值必须放行"）。
（控制方裁决 2026-09-20：**采纳本口径**。）

> **【理由订正 · 2026-09-20 合批审查 M-3：原写的理由不精确，结论不变】**
> 原写「把 `"category": "事业"` 判成回显 = 把**每条正常建议的领域名清掉**」/「拦它会把
> 所有合法分类洗掉」—— **在真实调用链上不成立**。实测核对（两处都读过源码）：
> ① `src/engines/advisor_v2.py:143-152` 的 `scrub_schema_echo` **只作用于 7 个呈现字段**：
> action 内 `advice` / `timing` / `concrete_steps` / `success_metric`，以及
> `serendipity` / `daily_tip` / `style_notes`；**`category` 根本不在 scrub 名单里**
> （它只被用作渲染键与兜底文案的索引），**即使判据误判它也洗不掉任何分类**；
> ② 生产侧根本没有把 `is_schema_echo` 接到 `category` 上的调用点。
> ⇒ **正确的理由是**：`"category"` 是 schema 要求模型**照抄**的**合法输出值域**，
> 属于「模型该输出的内容」而**不是「对模型说的话」**，因此**不该计入"规格泄漏"的
> 召回缺口**（计入＝污染分母 18→19），而不是"拦了会洗掉分类"。
> 结论（18 条口径、8 条真缺口）**不变**；本次只订正理由表述。

### 3.2 补法（三条"规格从句"弱信号，阈值不变）

补的三条**各覆盖多条 spec**（不是给某个例子打补丁），每条对应一类**写法差异**
（规格在描述"值该长什么样"，用户向文案在给出内容）：

| 新信号（**弱**） | 覆盖 | 为什么不是单例补丁 |
|---|---|---|
| `，包含具体<X>`（**不带「的/了」**）且**收在串尾**（容忍尾标点） | 4 条短 timing（各凑满 2 条弱信号，与既有 `时间窗口` 配合） | 无主语的祈使式名词短语**收尾**是规格写法；带"的"不算、句中还有下文也不算 ⇒ 挡得住 `…，包含具体的责任人，…` / `…，包含具体可交付的小项，一周一复盘。` |
| `具体的行动建议[，,]\s*结合` | 4 条短 advice（第 1 条弱信号） | schema 里 **5 条** advice spec 的共同搭配；用户向文案即使写"具体的行动建议"，后面接的是冒号/具体事由，不会紧跟"结合" |
| `结合…的<个性化\|长远>(分析\|建议…)` | 4 条短 advice（第 2 条弱信号） | **名词化的"分析物描述"**（描述分析该长什么样而非给出分析）；"结合…的"偏正结构在用户向文案里极少见（那里写"结合你的情况…"） |

**未动任何既有断言**：k63 测试断言行 41 → 49（`git diff` 无 `-assert` 行）；
k63 测试 47 → **63 passed**。

### 3.3 硬约束实测（同一批语料，BEFORE = `90230d9` 的 `fact_guard.py`，AFTER = 本批）

| 语料 | n | BEFORE 命中 | AFTER 命中 | 判定 |
|---|---|---|---|---|
| schema spec 原文（非 category） | 18 | **10** | **18** | 召回补齐 ✔ |
| `category` 合法取值 | 5 | 0 | **0** | 必须放行 ✔ |
| 自造正常文案（含 12 条对抗性"最像规格"写法） | **55** | **0** | **0** | **误杀未上升** ✔ |
| 已知代价（`时间窗口`+`必须X`） | 8 | 8 | 8 | 未变 ✔ |
| 真实字段 R1+R2（归档：实现者 r1 产物 72 + 审查者 5 轮 65） | **137** | 1\* | 1\* | **零误杀** ✔ |
| 真实字段 R3（**本次自造 4 轮 glm-4-flash 实跑**） | **92** | 0 | **0** | **零误杀** ✔ |

\* 该 1 条是 **真回显**（`r1/unknown/final.serendipity`，GLM 降级档实录样本），
**不是误杀** —— 改前改后都命中它，属 D 段该挡的东西。
真实字段合计 **229 条**（137 归档 + 92 自造），满足"我自己再造一批 ≥30 条"（4 轮 × 23 字段）。
R1 我抽到 72 条而非审查者的 69：我把 3 档的 `category` 值也算进了字段面（多 3 条，
判据放行它们，见 §3.1）。

**结论：误杀零上升（55 条自造正常文案 0→0；229 条真实字段 0→0）、召回 10/18 → 18/18。**
⇒ 未触发"若扩大召回导致误杀上升就停下报我"的条件。

### 3.4 新增守卫（防"覆盖面被高估"重演）

- `test_every_prompt_spec_value_is_detected`：**从当前 prompt 抽 schema 叶子**，断言每个
  非 category 值**逐字回显**都被判泄漏 —— 判据覆盖面与 prompt **同步**，以后谁加字段说明
  而判据跟不上，立刻打红（不必等到用户看见模板说明）；
- `test_schema_spec_originals_are_cleaned`：18 条 spec 原文进正例集（**逐字**）；
- 残留缺口用例见下。

### 3.5 残留缺口（**如实登记，已用测试钉住**）

仍**不命中**的（都**不是 spec 原文**，而是截断/改写形态）：

| 残留 | 为何不补 |
|---|---|
| `包含具体日期范围`（丢了前半句"最佳时间窗口，"） | 缺逗号锚点；补它就得放宽到"任意位置出现 `包含具体`" |
| `最佳时间窗口，含具体日期范围`（"包含"→"含"改写） | 追同义改写 = 堆词表，且 `时间窗口` 单条弱信号**故意**放行 |
| `结合八字五行的个性化分析`（丢了 spec 头"具体的行动建议，"） | 只有 1 条弱信号 → 被阈值（≥2）**故意**放行；升成强信号会让 `结合你的实际情况的个性化建议` 这类正常句被误杀 |

口径：**逐字回显 18/18 全覆盖；非逐字的"像规格的自造文案"刻意不追** —— 追下去要升强
信号或堆词表，而 D 的误杀代价是用户少一条真建议。`RESIDUAL_GAPS` 用例的作用正是防止
后人误以为召回 100%（要覆盖它们必须**同时**给出"误杀未上升"的实测）。

### 3.6 改前失败证据

新用例跑在 **HEAD 的 `fact_guard.py`** 上（`git archive` 副本 + 本批测试）：
**6 failed / 54 passed** —— 5 条 spec 原文用例（4 条短 timing 与 4 条短 advice
去重后 5 个文本）+ 召回不变式用例。改后 **63 passed**。

---

## ④ 报告更正（明确更正 k62 报告那句 + 本批全部更正点）

1. **k62 报告 §7.7「`data/memory/.json` … 两轮都 `git restore` 还原，**未纳入任何提交**」**
   → **该句与事实不符，已就地更正**（`docs/superpowers/task-k62-report.md`，保留原文 +
   加"【更正 · 2026-09-20 集成修复】"块）：该副作用**确实被提交进了 `2a67833`**，
   已在本次**还原为 `ea110c3` 版本**；k63/k64 未带它；根因（测试隔离缺陷）与防复发
   （`test_bot.py` 的 `USER_MEMORY_DIR` 重定向 + 新守卫）一并写明。**本报告是唯一
   更正该句的地方，k62 报告其余内容未改动。**
2. **k62 报告 §1.3 / §7.1** 的「与用户反馈无关」「对所有用户恒为 `'gentle'`」
   → 已就地加更正块（见 §②），并同步更正 4 处源码/文档注释与守卫 docstring。
3. **k63 侧**：D 段的召回口径（原文案"若模型回显的是纯中文且不含任何规格/结构形态的
   **自造文案**会漏判"，实际漏的**就是 prompt 自己的 spec 原文**）→ 本报告 §③ 给出
   实测数字（10/18 → 18/18）、残余缺口逐条、以及"精度优先于召回"的边界声明；
   `src/utils/fact_guard.py` 头注释同步写明。
4. **本批产生的口径变更**（供后续批次引用）：
   - "三个风格权重退化"的正确表述 = **自强化回路 + 注入未表达偏好**（不是"恒等常数"）；
   - "D 段漏判边界"的正确表述 = **非逐字的截断/改写不命中**（逐字回显 18/18 全中）；
   - "测试副作用"的入库判定 = 以 `git show --stat <commit>` 为准，**report 自述不算数**。

---

## 验证证据汇总

| 项 | 命令/方式 | 结果 |
|---|---|---|
| 定向子集（20 文件） | `pytest <20 files> -q` | **502 passed / 14 skipped / 0 failed**（24.0s）⚠️ 见下方**计数订正 M-4** |
| 守卫三连 | `test_k62`(26) + `test_k63`(63) + `test_k62k63_fixup`(3) + `k64`(10) | 全绿 |
| 仓库洁净 | 跑完子集后 `git status --short` | **空**（改造前必然留下 ` M data/memory/.json`） |
| 守卫 A 改前/改后 | 提交前红（`39b28f18…` ≠ `32b535d9…`）→ 提交后绿（同哈希） | 两段都在 |
| D 召回改前/改后 | `compare_before_after.py`（同一批语料） | 10/18 → **18/18**，误杀 0 → 0 |
| D 召回"改前失败" | 新用例跑 HEAD 的 `fact_guard` | **6 failed / 54 passed** |
| 真实文案 | 4 轮 glm-4-flash 走真实 `AdaptiveAdvisor.generate()` | 92 字段，命中 0 |
| 守卫"有牙" | 沙箱去掉重定向 → 复现脏化 | 必红，常驻用例 |
| 生产零写入 | `git -C /home/a/fortune-run log -1`；生产库 mtime | 仍 `ea110c3`；`2026-09-05 09:19` |

### 计数订正 M-4（2026-09-20 b2fix 复核；**注明读数所在的树**）

> **为什么会有这一节**：本报告的 `502 / 14` 与 k65 报告的 `741 / 1` 都被合批审查
> 复现为**不同的数**，差 1~30。根因不是"谁跑错了"，而是**两个读数取自不同的树、
> 且原命令未留完整文件清单** —— 本节的目的是把每次读数钉到"哪棵树、哪次读数、
> 哪个文件清单"上，避免以后再出现"不同树比数"。

| 读数 | 树 / 时点 | 命令与文件清单 | 结果 |
|---|---|---|---|
| 本报告原记 | 集成修复工作树（`502`。**原命令未留文件清单**） | `pytest <20 files> -q` | **502 / 14 / 0**（24.0s）—— ⚠️ **无法逐字复现**：20 文件清单未入库/未留档 |
| 合批审查者重建 | `1dae341`（`b2-wt`） | 按本报告文字描述重建的 20 文件切片 | **472 / 14 / 0** |
| **本次（b2fix）重建** | `1dae341` 起点（`b2fix-wt` @ `batch2-reviewfix`） | **19 文件**（下文清单，含 k64 守卫） | **471 passed / 14 skipped / 0 failed**（167.95s） |

- **可复现的锚点是 `14 skipped`**：三个读数（502/472/471）**跳过数完全一致**，
  且**都是 0 failed** ⇒ 差异**全部来自"选了哪些文件"**，不是失败/回归，也不影响任何结论。
- 本次重建用的 19 文件清单（可逐字复跑）：
  `test_k62_deadcode_removal_guard` `test_k63_schema_echo_guard`
  `test_k62k63_fixup_side_effect_guard` `test_k64_quality_predictor_removed`
  `test_k11_fact_discipline` `test_emoji_cleanup` `test_adaptive_advisor` `test_bot`
  `test_eval_l1` `test_eval_l2` `test_eval_l3` `test_eval_l4` `test_eval_e6`
  `test_calendar_llm_route` `test_calendar_persons_read` `test_calendar_today_cache`
  `test_k17_advisor_persons_first` `test_k41_gender_residual` `test_k52_compliance_scan`
  （注意：仓库里 `tests/test_calendar_*` **只有 3 个**，原描述的"calendar×4"无法成立，
  这本身就是"原清单没留档、只能重建"的旁证。）
- **结论不变**：三棵树、三次读数**均为 0 failed**；"最宽证据 = 定向子集、**未跑全量**、
  不能声明全仓无回归"的表述**不变**（`502` 这个具体数字请改引用本节的重建读数）。

## 未做 / 未验（如实列出）

1. **未跑全量**（按纪律）—— 最宽证据 = 定向子集（见上方**计数订正 M-4**：
   本报告记 502/14/0、审查者重建 472/14/0、b2fix 重建 471/14/0，**均为 0 failed**）；
   不能声明"全仓无回归"。
2. **未跑真实 DeepSeek**（按纪律）—— D 段的精度证据是 glm-4-flash + 归档/自造文本，
   生产档（deepseek）输出分布下的误杀率**仍未知**。
3. **未跑 E6 完整 108 条评测**（控制方另有安排）。
4. **未在小程序端真机渲染**（本批无前端字段变更）。
5. **未改写 `2a67833` 的 commit message**（含已被证伪的"恒为 gentle"措辞）——
   按控制方裁决：**不重写历史**；控制方将在**合并提交信息里注明**
   "该提交信息中的说法已被后续提交取代"。更正后的口径见 §② 与
   `tests/test_k62_deadcode_removal_guard.py` docstring。

## 登记项（控制方裁决后的处置 / 候选，**本批不实施**）

| # | 事项 | 是什么 | 为什么本批不做 | 现状 |
|---|---|---|---|---|
| R-1 | `data/memory/.json` **移出 git 跟踪 / 加 `.gitignore`**（审查 M-1 的"根治"选项） | 该文件是**运行时状态文件**（`UserMemory` 按 uid 写 `data/memory/<uid>.json`，空 uid 落到 `.json`），理论上不该被跟踪 | 属**跟踪方式 + 部署契约**变更（会牵动部署脚本/同步清单），需单独评估 | **候选，未做** |
| R-2 | 审查 **M-4**：k63 报告"邻接 5 文件 121 passed / 5 skipped"，审查者复现为 **122**（k63 tip 122/5、合并态 122/4），差 1 未找到可复现的文件组合 | 记账误差，不影响结论 | 不属控制方点名的 4 项 | **登记，未做** |
| R-3 | 审查 **M-5**：k63 §3.2 散文称"9 条断言…其余 7 条"，实际该用例是 **8 条**（删 2、留 6 = 4 逐字未动 + 2 更强；报告自己的表格是 8 行） | 计数口径差 1，方向与结论正确 | 同上 | **登记，未做** |
| R-4 | 审查 **M-6**：k62 守卫扫描器 `except SyntaxError: continue` 跳过不可解析文件，动态面只锁 `import_module(<字面量>)` / `__import__(<字面量>)` ⇒ **不覆盖"计算式模块名 / 字符串 exec"**；建议在红线区显式写一句 | 守卫的**能力边界声明**（与 k59 同款口径） | 同上；一句话级补充，随下批做 | **登记，未做** |
| R-5 | **宽 `except Exception` 吞掉编程错误**（`TypeError`/`AttributeError`/`NameError` 等"必然是 bug"的异常）的**面**（源自 k64 报告 §6 的审计项；合批审查 M-5 复核仍在） | 它正是本批 **P1** 能被藏住的机制（`quality_predictor.update()` 缺必填参数 → 每次都抛 `TypeError` → 被 `except Exception` 静默吞掉 → 「感谢认可！」整段从不显示）；`_free_chat` / `scrub` 等多处 `except Exception: pass` 仍在 | **本批不做，理由有三**：① **属行为/控制流改动**（要么收窄异常类型、要么让异常冒泡），会触及活路径，**超出本批"只改注释/docstring + 补报告"的授权**；② 需要一个**扫描面 + 分类清单**（区分"运行故障该降级"与"编程错误不该吞"），属单批工程量，按控制方口径应**单开一批**；③ k61 那批的守卫**不得不继承 `BaseException`** 正是因为这个兜底面 —— 收窄前必须先有守卫钉住"降级档仍可用"，否则会把降级能力一起打掉 | **登记，未做**（本批**保持可见**：`k64` 报告 §6 + 本行）|

## 部署工作树同源遗留（**已按控制方裁决还原**）

`/mnt/e/fortune-agent-deploy`（= 部署工作树）此前也带着**同源**脏化：
`git status --short` 显示 ` M data/memory/.json`，`_updated_at` → `2026-09-20T02:18:24`
（mtime **02:18:23**，早于本会话 14:06 左右 ⇒ **不是本批造成**，是更早某次在该目录
跑测试留下的，与 §① 完全同款机制）。

**处置（2026-09-20，按控制方裁决，只补做这一步、未改任何代码）**：
`git -C /mnt/e/fortune-agent-deploy restore data/memory/.json` →
该文件与 HEAD blob 逐字节相同
（`sha256=32b535d94f85c0609b4ad787a1c5bc717bef020a65c461a240136c209db500f1`，
与合并基点 `ea110c3` 的版本一致），`git status --short -- data/memory/` 现为**空**。
该目录其余他人产物（`data/eval/results/*`、`src/engine/out/comparison_runs.jsonl` 等）
**未触碰**。本批对部署目录的唯一写入是报告文件本身
（`.superpowers/sdd/.gitignore` 为 `*`，不污染其 git 状态）。
**不影响部署**（控制方只同步 `src/`）。

---

*本报告为集成修复产物；worktree 内副本 `docs/superpowers/task-integration-fixup-report.md`
（随本文件所在提交入库），部署目录副本
`/mnt/e/fortune-agent-deploy/.superpowers/sdd/task-integration-fixup-report.md`（内容相同）。*
