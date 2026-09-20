# k63 批次报告：建议卡人设统一为**豆包式口吻**（去按性别的 persona 分叉）

> **【入库说明 · 2026-09-20 · b2fix 批次】** 本文件**原样入库**：此前它只存在于
> `/mnt/e/fortune-agent-deploy/.superpowers/sdd/` 与 `/home/a/k63-wt/`，
> **未随任何提交进仓库**（k62/k64 都带了报告，k63/k65 漏了 —— 合批审查 I-1）。
> **除本段外，内容与最终版逐字节相同**（入库源文件 sha256
> `b8f8eff8f15015662901083b4fcb02fb3747607a22108add12f5ebf1805b21db`，
> 与 `/home/a/k63-wt` 副本一致）。
> **一处时点提示（不是更正）**：本报告 §9.5 描述的是 **k63 交付时**的 D 段（schema 回显）
> 状态；**其后的集成修复批**把 D 段召回从 **10/18 提到 18/18**（精度实测零退，
> k63 测试断言行 41→49 **只增不减**）。读 §9.5 的"边界/已知缺口"时请一并看
> `docs/superpowers/task-integration-fixup-report.md` §③。

**分支**：`k63-persona-unify`（worktree `/home/a/k63-wt`，起点 main `ea110c3`）
**提交**：`fbe4ec7`（r1 口吻统一）+ `d218a32`（T103 评测输入同步）
+ `c883df6`（r2 schema 回显守卫）+ `7427363`（r2 说明措辞收口）
（**未合并、未推送**；工作区干净，共 5 文件改动）
**一句话**：主对话链早已统一豆包口吻，而**排盘建议卡那条链**仍按性别分叉人设
（女→毒舌闺蜜 / 男·未知→理性分析师，`advisor_v2.py:84-92`），同一用户在"主回复"
与"建议卡"上听到两种人格。本批按用户拍板**统一成豆包口吻**：删除性别→persona 分支
与整个 `style_instructions` 映射（含 0 引用的「温柔陪伴者」死分支），改为与主链
system prompt **同词句**的单一常量。**称谓**（k11-B）与**输出后校验器**（`scrub_turn`）
一行未动 —— 那是称谓不是口吻。

改动面（5 文件）：`src/engines/advisor_v2.py` + `tests/test_k11_fact_discipline.py`（r1）+
`data/eval/agent_tasks.jsonl`（T103 一处描述，见 §9.4）+ `src/utils/fact_guard.py` 新增 D 段 +
`tests/test_k63_schema_echo_guard.py`（r2，见 §9.5）。
`handler.py` / `career_dir.py` / `bazi_formatter.py` **零改动**；
`fact_guard.py` **只在文件尾部新增** D 段（B/C 段与 `scrub_turn` 的判据、返回值、
调用方**一字未改**，见 §9.5 切分说明）；
`person_dao` 与并行批次文件（`mood_detector` / `emotion_soother` / `preference_dao` /
`dream*` / `scripts/k55_dream/*`）**零改动**（见 §8 披露的行级核对）。

---

## 1. 改动清单（文件:行）

| # | 位置 | 改动 | 为什么 |
|---|---|---|---|
| 1 | `src/engines/advisor_v2.py:33-47` | 新增模块常量 `STYLE_INSTRUCTION`（豆包口径，见 §4） | 单一风格事实源；取代 persona 映射 |
| 2 | `src/engines/advisor_v2.py:100-105` | `generate()` 内删 `_g_raw → personality_label` 性别分支 | 分叉的**所在地**（k11-B 引入） |
| 3 | `src/engines/advisor_v2.py:158-166` | `_build_prompt()` 删形参 `personality_label` | 无分支后该形参恒为死参（不给"假可配"留口子） |
| 4 | `src/engines/advisor_v2.py:181`（删） | 删 `style_instructions` 字典整块（3 档） | 见 §2「死分支核实」 |
| 5 | `src/engines/advisor_v2.py:245-247` | prompt 风格段 `当前模式：{label}\n{style_instructions}` → `{STYLE_INSTRUCTION}` | 去掉"模式"概念（常驻模式已不存在） |
| 6 | `src/engines/advisor_v2.py:249-253` | 仅**改注释**：说明"称谓 ≠ 口吻，口吻统一后本段仍不许删" | 防后人误删 k11-B 成果；**代码一行未改** |
| 7 | `src/engines/advisor_v2.py:330` | 核心要求 5 "风格要符合当前模式的要求" → "风格必须符合上方「说话风格要求」" | 文中已无"当前模式" |
| 8 | `tests/test_k11_fact_discipline.py:57-90` | 新增结构锚点辅助（`_style_section` / `_strip_addr_block` / `_strip_gender_line`） | 按 prompt 结构定位，不依赖具体文案 |
| 9 | `tests/test_k11_fact_discipline.py:235-327` | persona 用例拆为 3 条新不变式（§3 逐条论证） | 锁新状态 |
| 10 | `tests/test_k11_fact_discipline.py:363` | 用例改名（断言逐条未动） | 旧名 `…keeps_girlfriend_persona_for_female` 与"prompt 已无人设"矛盾 |
| 11 | `data/eval/agent_tasks.jsonl` T103 `judge_hint`（唯一改动字段） | 陈旧描述"（毒舌闺蜜 persona 仅限女命）"→ 口吻统一口径 + 明示裁判"不得因缺少毒舌口吻扣分" | 见 **§9.4**；**评测输入变更 → 历史分数不具可比性** |
| 12 | `src/utils/fact_guard.py`（文件尾部**新增** D 段） | `SCHEMA_ECHO_STRONG` / `SCHEMA_ECHO_WEAK` + `schema_echo_hits` / `is_schema_echo` / `guard_schema_echo` / `scrub_schema_echo` | 见 **§9.5**；B/C 段与 `scrub_turn` 一字未改 |
| 13 | `src/engines/advisor_v2.py:127-165`（r2 接线） | 字段层叠 `scrub_schema_echo`（与 `scrub_turn` 各自独立）；advice 置空→整条摘除；5 条全摘→通用兜底 | 呈现层兜底，不改解析层 |
| 14 | `tests/test_k63_schema_echo_guard.py`（新增 **47** 例） | 正例 17 形态 / 反例 20 条 / 接线 / 阈值 / 切分 / 解析层不变 / 已知边界 1 条 | 见 §9.5 |

**未动一行**（逐项 grep 核对，`git diff` 里不出现）：`_gender_cn` 归一（`:182-184`）、
「称谓硬规则」注入条件与文本（`:249-259`）、`fact_guard.scrub_turn`（函数体在 r2 diff 里
全是**上下文行**，无 `+/-`）、`format_fact_pack_block` 事实包、`career_dir` 方向要点与
防反转条款、JSON schema 与其余核心要求、`_call_llm` / `_parse_llm_output` 解析逻辑。

---

## 2. 死分支核实（删之前先查引用）

命令与结果（worktree 内）：

```
$ grep -rn "personality_label" src/ tests/          # 改前
src/engines/advisor_v2.py:96   prompt = self._build_prompt(..., personality_label)
src/engines/advisor_v2.py:153  personality_label: str,
src/engines/advisor_v2.py:253  当前模式：{personality_label}
tests/test_k11_fact_discipline.py:173,193,200      # 测试直调
$ grep -rn "温柔陪伴者" src/ tests/ scripts/        # 改前
src/engines/advisor_v2.py:180   "温柔陪伴者": (...)   # ← 仅此一处（映射里的一条）
```

- `personality_label` 的**唯一写入点**就是 `generate()` 的性别分支（只产生
  "毒舌闺蜜"/"理性分析师"两个值）→ `温柔陪伴者` 这条**从来没有被选中的路径**。
- `generate()` 是唯一调用 `_build_prompt` 的生产代码（`handler.py:8590/8695/10578`、
  `src/api/advisor.py:140` 都只调 `generate`），故删形参不影响任何生产调用点。
- 因此不是"删了温柔陪伴者"，而是**整张映射都成了死机制**（无分支即无映射）——
  一并删除，不留"配了但不生效"的假可配面。
- 文档里的 `docs/ACCEPTANCE_CRITERIA.md:16-18`（三档人设验收项）为**历史文档**，
  本批不动（见 §8 待裁决项）。

---

## 3. 每条断言变更的等价性论证（未放宽任何一条）

### 3.1 `test_advisor_prompt_injects_fact_pack_and_baseline`（:212）

- **原断言证明**：`_build_prompt(golden, ctx, "理性分析师")` 产出的 prompt 含性别行、
  事实包（周岁 27/虚岁 28）、方向要点（行业五行映射/方位映射）、防反转硬约束。
- **改后证明**：同样的 6 条断言**逐字未动**，仅调用签名 3 参 → 2 参。
- **为什么等价**：被删的第三个实参在旧代码里只用于查风格表；本用例断言的全部是
  事实包/基线/性别行，与风格表无关。**非放宽**（断言条数不减、覆盖面不变）。

### 3.2 旧 `test_advisor_persona_by_gender` → 拆为 3 条（:235/:268/:302）

旧用例的 9 条断言按性质分两类，逐条落到新用例：

| 旧断言 | 性质 | 去向 | 等价性 |
|---|---|---|---|
| `"当前模式：理性分析师" in p_male` | 锁**分叉存在** | **删除**（正是本批要去的状态） | 被 3.3 的更强不变式取代 |
| `"当前模式：毒舌闺蜜" in p_female` | 锁**分叉存在** | **删除**（同上） | 同上 |
| `"毒舌闺蜜" not in p_male` | 锁**男命不受女性人设污染** | 保留并推广（:271-276 全 prompt，三性别） | **更强**：从"男"扩到男/女/未知三种，且新增 6 个旧口癖词 |
| `"像闺蜜一样说实话" not in p_male` | 同上（旧口癖） | 保留并推广 | 更强（三性别） |
| `"称谓硬规则（必须遵守）" in p_male` | **称谓** | 原样保留（:291） | 逐字未动 |
| `"严禁任何女性向称谓或闺蜜口吻" in p_male` | **称谓** | 原样保留（:292） | 逐字未动 |
| `"性别：女" in p_female` | **称谓** | 原样保留（:296） | 逐字未动 |
| `"称谓硬规则（必须遵守）" not in p_female` | **称谓** | 原样保留（:298） | 逐字未动 |

> 净效果：**删除的只有 2 条"锁旧分叉"的断言**（它们描述的旧状态已被用户拍板废止），
> 其余 **7 条全部保留**（4 条逐字未动 + 3 条保留且更强）。**没有任何一条被改宽。**

### 3.3 新增断言（锁新状态，两条不变式）

**I. 口吻不随性别变化** —— `test_style_instruction_uniform_across_genders`（:235）
用 `dataclasses.replace(golden, gender=…)` 固定命盘（**性别是唯一自变量**）：
1. 三性别 prompt 的「说话风格要求」段**逐字相同**（`len(set(...)) == 1`）；
2. 该段 **== `STYLE_INSTRUCTION`**（单一来源，无分支）；
3. 该段含主链豆包口径关键短语（"说话像豆包"/"把专业术语（五行、十神、神煞、大运等）
   讲成大白话"/"禁止油滑/套近乎开场白"/"不挖苦、不嘲讽、不贬低用户"）；
4. 该段不含 `毒舌/闺蜜/理性分析师/温柔陪伴者/该怼就怼/麦肯锡/心理咨询师/当前模式`。

**II. 整段 prompt 除称谓外逐字相同** —— `test_prompt_differs_by_gender_only_in_address`（:268）
把三性别 prompt 归一（抹掉「性别：」行 + 整个「称谓硬规则」段）后要求**完全相等**，
并断言 `raw["男"] != raw["女"]`（防"空比空"的假绿）。这比只比风格段更强：
**任何**未来新增的性别相关分支（不止风格）都会红。

**III. 生产路径同锁** —— `test_generate_prompt_uniform_across_genders`（:302）
`_build_prompt` 是被直调面；真正的分叉地点在 `generate()`。本用例 monkeypatch
`_call_llm` **捕获真实发给 LLM 的 prompt**（即 `handler.py:8692` 消费的那条链），
断言同样两条不变式。

**IV. 称谓校验仍在** —— `test_address_guard_kept_by_gender`（:283）
男/未知 → 有「称谓硬规则」段 + `性别：男` / `性别：未知（请用中性表述，勿假设性别）`；
女 → 有 `性别：女`、**不注入**该段（k11-B 原样）。

### 3.4 改名用例（断言逐条未动）

`test_generate_keeps_girlfriend_persona_for_female` → `test_generate_scrub_keeps_female_terms_for_female_user`（:363）。
该用例的真实语义是 **`fact_guard` 对女命文本不去词**（B 校验器行为），
与"引擎维持闺蜜人设"无关；k63 后旧名会误导读者以为人设仍在。**5 条断言一字未改。**

---

## 4. 与主链豆包口径的一致性说明

**主链原文**（`src/bot/handler.py:3550-3554`，未改动）：

> 你是易理明灯，一位懂命理的温暖朋友。说话像豆包：口语化、有温度、自然不端着，
> 把专业术语讲成大白话。回答纪律：直接专业作答，禁止油滑/套近乎开场白
> （如「哈哈」「挺有意思」）；严格紧扣用户问题，用户没问的（名人相似、旁支话题）
> 不得主动展开。

**新 `STYLE_INSTRUCTION`**（`advisor_v2.py:40-47`）逐句对应：

| 主链句 | advisor 句 | 关系 |
|---|---|---|
| 你是易理明灯，一位懂命理的温暖朋友。 | 同 | **逐字复制** |
| 说话像豆包：口语化、有温度、自然不端着 | 同 | **逐字复制** |
| 把专业术语讲成大白话 | 把专业术语（**五行、十神、神煞、大运等**）讲成大白话 | 同句 + 括注术语名（建议卡正文必须解释这些词，主链不需点名） |
| 直接专业作答，禁止油滑/套近乎开场白（如「哈哈」「挺有意思」） | 同 | **逐字复制** |
| 严格紧扣用户问题，用户没问的（名人相似、旁支话题）不得主动展开 | 同 | **逐字复制**（对 advisor 尤其必要：它有 `serendipity` 字段，天然诱发"主动展开"） |
| （主链无） | 语气始终温暖平等、就事论事：不挖苦、不嘲讽、不贬低用户，实话也要好好说。 | **k63 新增**：把被删掉的"毒舌"档正面表达为"不挖苦"，避免用否定词写"别毒舌"（不给模型回指旧人设的词面） |

**为什么两者不冲突**：

1. **同一个人格定义**：两处都是"温暖朋友 + 豆包口吻 + 直给不套近乎"，不存在两套人设；
2. **任务不同但人格同源**：主链 prompt 是"把引擎结果**润色**成自然回复"，
   advisor prompt 是"生成**结构化 JSON 建议卡**" —— 差的是输出契约（JSON schema、
   时间窗口、5 领域覆盖、引用事实包/方向要点），**不是口吻**。这些脚手架主链没有、
   也不该有，故不复制；
3. **主链独有的部分本就不适用于建议卡**：引用编号 `[n]` 规则、`<tool_calls>` 联网
   工具协议、命盘图片链接保底 —— 建议卡是纯文本字段，无引用/无工具，故不引入；
4. **禁止项同向**：主链禁"油滑/套近乎开场白"，本批删掉的正是"毒舌闺蜜"的
   "该怼就怼/让用户先笑再思考"与"麦肯锡顾问"腔 —— 三条都不是豆包口吻。

---

## 5. 实跑三例（真实 LLM：免费 `glm-4-flash`，**未用 DeepSeek**）

**口径**：走**真实生产路径** `AdaptiveAdvisor.generate()`（prompt 构建 → LLM → 解析 →
`scrub_turn` → 组装卡），只把 `_call_llm` 的**传输层**换成
`src.llm.client.glm_openai_completion`（`model=glm-4-flash`，`max_tokens=3000`，
`temperature=0.8`，`timeout=45.0` —— 与生产 `_call_llm` 同参），messages 结构
（同一 system prompt + 同一 user prompt）与生产一致。
命盘：1999-05-13 09:00 长春（2026-09-06 行 48 事故同款），`user_context="国企还是金融"`；
三例**只差 `gender`**（`dataclasses.replace`）。
脚本 `/dev/shm/k63_live_run.py`，原始产物 `/dev/shm/k63_live_out.json`、日志 `/dev/shm/k63_live.log`。

### 5.1 性别=女（`gender="女"`，32.0s）

```
💼 事业：在2027年9月15日至10月15日期间，考虑转换到与水木相关的行业，如教育、文化或医疗，
        这些行业能帮助你发挥潜力，同时减轻金火的压力。
💰 财运：在2026年11月8日至2027年1月7日期间，财运较好，适合进行投资或财务规划，
        但需注意避免过度消费。
❤️ 感情：在2026年7月20日至8月18日期间，感情运势不错，适合约会和表白，
        但需注意沟通方式，避免过于直接。
🏥 健康：在2026年3月10日至4月9日期间，注意身体健康，特别是消化系统，适当调整饮食，
        避免过度劳累。
🌱 个人成长：在2026年5月22日至6月21日期间，是自我提升的好时机，可以参加培训或学习新技能，
        有助于长远发展。
serendipity：顺便说一句（你可能没问但很重要）：你的命盘显示，2027年将是一个重要转折点，
        可能会带来较大的变化，建议提前做好准备。
daily_tip：保持乐观，每一天都是新的开始。
style_notes：你的命格特点是偏财格，喜水木，忌金火土，适合从事与水木相关的行业，
        注重身心健康和人际关系。
```

### 5.2 性别=男（`gender="男"`，27.1s）

```
💼 事业：考虑转向金融行业，特别是银行或证券领域，这些行业与金和水五行相契合，有利于事业发展。
💰 财运：在2026年9月20日至2027年3月20日期间，投资与水木相关的领域可能会有不错的收益。
❤️ 感情：在2026年9月20日至2027年3月20日期间，多参与社交活动，可能会有新的感情机会。
🏥 健康：注意心脏和眼睛的健康，这段时间可能需要更多的休息和保养。
🌱 个人成长：利用这段时间学习与水木相关的知识或技能，对你的长远发展大有裨益。
serendipity：顺便说一句（你可能没问但很重要）：近期财运和事业都有上升空间，
        但同时也要注意身体健康。
daily_tip：今日小建议：保持心情舒畅，有助于提升运势。
style_notes：命格特点：偏财格，喜水木，需注意金火土的调和。
```

### 5.3 性别=未知（`gender="unknown"`，30.0s）

```
💼 事业：在2027年9月15日至10月15日期间，考虑从事与水、木元素相关的行业，如教育、文化或医疗领域，
        这些行业能够得到你的用神支持，有助于事业发展。
💰 财运：在2027年农历八月十五至九月初九期间，可以尝试投资与水元素相关的行业，如运输或旅游，
        这段时间你的财运相对旺盛。
❤️ 感情：在2027年1月1日至1月31日期间，多参与社交活动，这段时间有利于提升你的感情运势，
        增加遇到良缘的机会。
🏥 健康：注意心脏和眼睛的健康，2026年9月20日至2027年1月1日期间，这些部位可能较为脆弱，
        建议定期检查。
🌱 个人成长：利用2026年11月11日至2027年2月11日期间的时间，专注于个人兴趣和技能的培养，
        这段时间有利于你的个人成长。
serendipity：你问的是[领域]，但你的命盘同时提示了其他重要信息。格式：'顺便说一句（你可能没问但很重要）：'
        + 简要说明其他领域的好时机与需注意的风险（80字以内）。如果实在没有特别信息，输出空字符串。
daily_tip：保持乐观，今天的你特别适合与人沟通。
style_notes：你的命格喜水木，适合从事与这两个元素相关的行业，同时要注意心脏和眼睛的健康。
```

### 5.4 三例的自动核查（脚本内断言，非人眼）

| 指标 | 女 | 男 | 未知 |
|---|---|---|---|
| 用户可见文本含女性称谓词（`FEMALE_ADDRESS_TERMS` 8 词） | `[]` | `[]` | `[]` |
| **未过 scrub 的原始 LLM 文本**含女性称谓词 | `[]` | `[]` | `[]` |
| 含人设/口癖词（毒舌·闺蜜·理性分析师·温柔陪伴者·宝子·亲爱的…12 词） | `[]` | `[]` | `[]` |
| 发给 LLM 的「说话风格要求」段长度 | 162 | 162 | 162（**逐字相同**） |

**① 口吻一致**：三段建议均为"第二人称 + 口语化 + 把术语说成大白话 + 给具体日期窗口"，
**没有任何一段带"毒舌/怼/姐妹"或"麦肯锡/概率百分比"的旧人设腔**；
且**发给 LLM 的风格指令本身字节相同**（§5.4 末行 + §3.3 不变式），
故口吻差异只可能来自采样噪声，不可能是系统性分叉。
**② 无性别化称谓泄漏**：男/未知两例**零女性向称呼**；女例虽被校验器放行（k11-B 语义），
实际也**未**出现任何女性向称呼 —— 注意 **未过 scrub 的原始文本就是干净的**，
说明 prompt 侧第一道足够，`scrub_turn` 作为第二道仍在（§6 植入实验 M2 锁着）。

> **诚实披露（未知档）**：`serendipity` 字段是 glm-4-flash **把 JSON schema 里的示例
> 文案原样回显**了（已核对：该串与 prompt 中 schema 行**逐字相同**，`echo_verbatim_from_schema: True`）。
> 这是**免费小模型的 JSON 保真度问题**（生产 DeepSeek 未观察到该形态；schema 段 r1 一字未动）；
> 此处如实记录，不作"三例全部完美"的过度声明。
> **控制方裁决：本批修**（判据/正反例/植入实验/边界/实跑复现 → **§9.5**，r2 提交 `c883df6`）。

---

## 6. 植入实验（改回旧状态必须红）

**环境**：`TMPDIR=/dev/shm OMP_NUM_THREADS=4 nice -n 10`，定向子集
（`-k GenderPersona`，8 例）；改前备份 → 改 → 跑 → `cp` 还原（`diff -q` 校验还原后
与备份**逐字节相同**）。

| 实验 | 植入内容 | 结果 |
|---|---|---|
| **基线** | 无 | **8 passed** |
| **M1** | 在 `_build_prompt` 里恢复"女→毒舌闺蜜口吻 / 其余→豆包"的性别分支 | **3 failed**：`test_style_instruction_uniform_across_genders`、`test_prompt_differs_by_gender_only_in_address`、`test_generate_prompt_uniform_across_genders` |
| **M2** | 把「称谓硬规则」注入条件改成 `if False:`（=删除称谓段） | **1 failed**：`test_address_guard_kept_by_gender`（`assert '称谓硬规则（必须遵守）' in p_male` → False） |
| **还原** | — | **8 passed**（`diff -q` 与备份一致） |

M1 真实失败输出（节选）：

```
E  +  where 2 = len({'你是易理明灯，一位懂命理的温暖朋友。说话像豆包：…实话也要好好说。',
                     '风格：说话犀利、直接、带点小毒舌，像闺蜜一样说实话。'})
tests/test_k11_fact_discipline.py:323: AssertionError
```

**结论**：两条不变式各自有**独立**的植入实验支撑（口吻分叉 → 3 红；删称谓校验 → 1 红），
且互不掩盖（M2 只打中称谓用例，M1 只打中口吻用例）。

---

## 7. 测试数字（只跑定向子集，**未跑全量**——高峰时段，全量由控制方今晚统一安排）

| 运行 | 命令 | 结果 |
|---|---|---|
| 本批主文件 | `pytest tests/test_k11_fact_discipline.py -q` | **41 passed / 0 failed**（0.72s） |
| **r2 新守卫文件** | `pytest tests/test_k63_schema_echo_guard.py -q` | **47 passed / 0 failed**（0.07s） |
| 邻接子集（5 文件：k63 守卫 + k11 + adaptive_advisor + k41 seam + k17 persons_first） | `pytest … -q` | **121 passed / 5 skipped / 0 failed**（1.12s） |
| **评测集 schema 面**（T103 改动后复跑） | `pytest tests/test_eval_e6.py -q -k "validate_eval_set_still_green or select_scope_full_and_explicit or l4_no_state_checks_grouping"` | **3 passed / 0 failed**（0.18s） |
| **评测集校验 CLI**（T103 改动后复跑） | `python scripts/eval_agent/validate_tasks.py data/eval/agent_tasks.jsonl` | **ALL GREEN：108 条全部通过 schema + 覆盖矩阵校验** |

- 5 skipped = `test_adaptive_advisor.py::TestIntegration`（需 `DEEPSEEK_API_KEY`，
  **本批未跑真实 DeepSeek**，符合纪律；**未跑全量**）。
- 用例数变化：`test_k11_fact_discipline.py` **39 → 41**（+3 新用例，-1 旧用例被拆；
  见 §3）；新增 `tests/test_k63_schema_echo_guard.py` **47 例**（§9.5）。
- 植入实验：r1 的 3 红 / 1 红 + r2 的 3 红 / 2 红 / 6 红 **全部在还原后消失**
  （`diff -q` 逐字节校验）；最终工作区 `git status` 干净。

---

## 8. 前端核实（`miniprogram/` 有没有依赖"人设标签"）

命令与结果：

```
$ grep -rn "毒舌\|闺蜜\|理性分析\|温柔陪伴\|人设\|persona" miniprogram/ | grep -v node_modules
（无输出）
$ grep -rn "advisor\|persona\|preferred_style\|style_name" miniprogram/utils/api.js miniprogram/pages/
（仅无关命中：取名/名笺页的"期望风格 chips"、CSS style 属性、注释里的"墨韵风格"…
 均与命理人设无关）
```

**结论：前端零依赖**。建议卡在端上是纯文本（`advice/timing/…` 字段），后端换口吻
不需要前端配合，也无需发版。另核实 `miniprogram/pages/love/love.js` 的
`personalityAnalysis` 来自**合婚**接口（`data.personality_analysis`），与本批无关。

---

## 9. 诚实披露 / 需控制方裁决项

### 9.1 跨批次冲突 **已由控制方处置**（k63 不动 k62）

**处置结论（控制方 2026-09-20 裁决）**：k62 会**整条移除**该守卫
（`test_k62_deadcode_removal_guard.py:545-549`，锁 `personality_label` / 毒舌闺蜜），
理由由其写入 k62 报告 —— 那条守卫是**基于控制方当时的错误指令**写的，用户已拍板统一口吻。
**k63 侧不再做任何事**：不改 k62 的守卫、也不要求 k62 替 k63 断言（避免未合并时互红）。
**新状态的守卫 = 本批的 `STYLE_INSTRUCTION` + 3 条新不变式**（§3.3），它们不依赖 k62。

以下为**发现时的原始证据**（留档，便于审查复核我当时的判断）：

<details>
<summary>原始冲突证据（折叠）</summary>



`k62-deadcode` 分支（**未合并**，本批起点 `ea110c3` 上不存在该文件）的
`tests/test_k62_deadcode_removal_guard.py::test_live_personality_paths_untouched`
把本批**必须删掉**的东西当作"绝对不许碰的活代码"锁住了：

```python
# tests/test_k62_deadcode_removal_guard.py:545-549（k62-deadcode 分支）
adv = _read("src/engines/advisor_v2.py")
assert "personality_label" in adv, "advisor_v2 性别人设分支被误删（k11-B 活代码）"
for label in ("毒舌闺蜜", "理性分析师"):
    assert label in adv, f"advisor_v2 人设标签 {label} 被误删（k11-B 活代码）"
```

在本批代码上逐条复跑该守卫的 advisor 子句（真实输出）：

```
personality_label in advisor_v2.py : False   ← 唯一变红的一条
'毒舌闺蜜' in advisor_v2.py       : True    （本批注释里作为"前身"被引用，仍命中）
'理性分析师' in advisor_v2.py     : True    （同上）
night_persona.py exists           : True    （k62 其余守卫不受影响）
handler refs night_persona        : True
quality_predictor.PERSONALITY_MAP : True
```

**我做了什么**：**一行没碰** k62 的文件/分支（不越界、不硬做）。
**建议处置（二选一，需控制方定）**：
(a) k62 合并前把该守卫的 advisor 子句改为断言"称谓纪律仍在"（如
`"称谓硬规则（必须遵守）" in adv` + `"scrub_turn" in adv`），保留其"反向事故守卫"初衷；
(b) 或在 k62 的守卫里改为断言 `STYLE_INSTRUCTION` 存在 + 人设词不在**可执行代码**里
（AST/注释不计，沿用 k62 自己的扫描口径）。
**顺序风险提示**：两分支谁先合并，另一方都必须同步这一条，否则 main 上会红。

</details>

> 复核提示：上述"结论 (a)/(b)"是我当时的建议，**已被控制方裁决取代**（见上 §9.1 处置结论：
> k62 整条移除、k63 不替它断言）。保留原文只为让审查能看到发现时的判断依据。

### 9.2 其余陈旧描述（逐项处置见各行，k63 未越界改动）

| 位置 | 内容 | 说明 |
|---|---|---|
| `data/eval/agent_tasks.jsonl` T103 `judge_hint` | "…（**毒舌闺蜜 persona 仅限女命**）" | **已按控制方裁决改**，见 **§9.4**（此行为原始报备记录） |
| `tests/test_adaptive_advisor.py:451 test_different_personality_different_output` | 名/文档字符串称"不同人格模式"，但**从未传过人设参数**（三次同参调用 `generate`） | **本批之前就已经名不符实**（旧分叉看的是**命盘性别**，三次同一命盘 → 同一人设），仅靠采样噪声通过，且在 `TestIntegration` 里**默认 skip**（需真实 DeepSeek key）。**控制方裁决：由 k62 删除（它本批就是拆"看起来能用实际没人用"的东西）；k63 不动**，避免两边同改一个文件。**k63 一行未碰该文件** |
| `src/engines/mood_detector.py`（k62 已删）/ `preference_dao` 的 `style_names`、`src/api/user.py:899` | "毒舌直接/理性分析/温柔陪伴" 偏好风格体系 | **属 k62 领地**，本批未动。提示：`handler._get_preference_hint`（`:2454`）经 `handler.py:10706` 注入**日历/今日运势**链（非 advisor_v2），若产品口径要求全局豆包化，这条链是**下一处候选**（本批不在授权范围） |
| `docs/ACCEPTANCE_CRITERIA.md:16-18` / `docs/v3.1_AI_NATIVE.md` S5.4/S7.2-S7.4 | 三档人设的验收项/路线图 | 历史文档，未动 |

### 9.3 其他披露

1. **未跑全量**（纪律要求），故不能声明"全仓库无回归"；已跑的定向子集全绿（§7）。
2. **未跑真实 DeepSeek**，全部 LLM 证据来自免费 `glm-4-flash`（按指令）。
   §5.3/§9.5 的 schema 回显提示：**免费模型在小 prompt 保真度上弱于生产模型**，
   本批结论（prompt 侧统一 + 形态守卫）不依赖该模型的输出质量。
3. **未部署、未改生产**：`/mnt/e/fortune-agent-deploy` 内**只新增本报告文件**，
   `src/` 未同步（本批只提交、不合并、不推送）。
4. **测试文件改动 = 改 1 个 + 新增 1 个**：`tests/test_k11_fact_discipline.py`（改）+
   `tests/test_k63_schema_echo_guard.py`（新增 47 例）；**未删除任何测试文件**，
   未碰 `test_adaptive_advisor.py`（其陈旧用例由 k62 处置）。
5. **`scrub_turn` 判据/返回值/调用方一字未动**（k11-B 第二道防线原样）：
   r2 对 `src/utils/fact_guard.py` 的 diff 里，`scrub_turn` 函数体全部是**上下文行**；
   本批在**文件尾部新增** D 段（独立函数），并未改动任何既有校验器语义。
6. 本批**未采纳**"female 用户也去掉女性化称谓"的扩张（用户拍板只统一**口吻**；
   `scrub_turn` 的女命放行是 k11-B 明确设计，属**称谓**范畴）。
7. **D 守卫的已知边界（不隐藏）**：判据是**形态学**的，不是语义理解 ——
   a) 若模型回显的是**纯中文且不含任何规格/结构形态**的自造文案，会漏判（本批已把
   schema 六字段 spec 全部纳入正例，覆盖已知形态）；b) 若正常建议里**同时**出现两条
   弱信号（如既说"时间窗口"又说"必须包含"），会误判为空。二者都在报告与测试
   （阈值用例）里显式可见，未做"看起来 100% 干净"的过度声明。

### 9.4 T103 评测输入变更与**可比性声明**（按"改评测输入"规格执行）

**动机**：T103 的 `judge_hint` 写着"（毒舌闺蜜 persona 仅限女命）"——该描述**现已不成立**，
且该 hint 会进裁判 prompt（`scripts/eval_agent/judge.py:185`），**可能让裁判把正确行为判错**
（把"回复里没有毒舌闺蜜口吻"当成缺陷）。

**改动（仅 1 个字段）**：

```
改动前（逐字留档）：
k11-F：advisor 消费点（关键词兜底 _handle_advisor）：男命建议禁女性称谓（毒舌闺蜜 persona 仅限女命）；神煞引用须在本盘全集内

改动后：
k11-F：advisor 消费点（关键词兜底 _handle_advisor）：男命建议禁女性称谓（k63 起建议卡口吻已统一豆包式，任何性别都不应出现「闺蜜/姐妹」等女性向称呼或人设腔；评判时不得因缺少「毒舌闺蜜」口吻而扣分）；神煞引用须在本盘全集内
```

- **`neg_checks` 一字未动**（`["{","undefined","NaN","null","请提供出生","Traceback","服务器内部错误","姐妹","闺蜜"]`）
  —— 该断言在口吻统一后**仍然有效且更强**（三性别都不许出现）；`reply_checks` 整体逐字段相同。
- **变动面证明（两道）**：① 先做**同构往返**（不改任何字段时 `json.dumps` 与原行**逐字节相同**
  → 证明改动不会连带重排其它字段）；② 再逐行比对 `HEAD` 版本：**108 行中仅 `T103.judge_hint`
  不同，其余 107 行逐字节未变**，且 `T103` 除 `judge_hint` 外所有键值完全相同。
- **原文留档与复原**：
  - 原句已逐字写入提交信息（`d218a32`）与本报告；
  - 复原命令（**用提交号，不用 HEAD~N** —— 本批后续又有提交）：
    `git show d218a32^:data/eval/agent_tasks.jsonl | grep '"id": "T103"'`；
  - **未在 `data/eval/` 放 `.bak`** —— 该目录是 E6 的"唯一事实源"（`l1_eval.py:46` 固定路径读取），
    不放旁路副本以免被后续脚本/人误读；留档改由「提交信息 + 本报告 + git 历史」三处承担，
    如需`.bak`我可补。
- **属于哪个门禁集**：**E6 统一运行器**（`scripts/eval_agent/runner.py` + `tests/test_eval_e6.py`），
  即 `data/eval/agent_tasks.jsonl` 的 108 条评测集（100 基线 + k11-F T101-T103 + k15 T104-T108）；
  另有 schema/覆盖矩阵门禁 `tests/test_k11_fact_discipline.py::TestEvalSchema` 与
  `scripts/eval_agent/validate_tasks.py`。
  **控制方裁决：不单独重跑** —— 今晚**全量门禁**会自然跑到 E6 这 108 条；
  本批只确认 `validate_tasks.py` 全绿（已绿 ✓，见 §7）。我未重跑评测、未写台账。
- **副作用核实**：`tests/k43_ab_cases.py:169` 引用的是 T103 的**用户话语**（corpus 用例，
  路由期望 `none`），**与 `judge_hint` 无关**，不受影响。

> **登记（台账用语）：评测输入已变更，历史分数不具可比性。**
> 现状核查：`data/eval/results/ledger.json` 仅 3 条历史运行
> （`unified-20260831-230202` scope 40 / `unified-20260901-014540` scope 100 /
> `unified-20260903-191731` scope 4），**全部早于 T101-T108 加入（k11 2026-09-07、k15）**，
> 其中**没有任何 T103 评分**；因此本次改动**不影响任何既有分数基线**，
> 但**今后任何与 T103 相关的评分对比，都不得跨过本次变更**。
> 改后复跑证据：`validate_tasks.py` **ALL GREEN（108 条）**；E6 定向 3 例 **passed**（§7）。

### 9.5 schema 回显守卫（D）——**本批已修**（r2 追加提交 `c883df6`）

#### 9.5.1 事故形态（实测，非推测）

`advisor_v2._call_llm` 挂 **GLM-4-Flash 降级链**（`src/llm/client.py:glm_openai_completion`，
`ZHIPU_API_KEY` 存在时优先），小模型会把 **prompt 里给模型看的 JSON schema 示例原样回显**进字段
→ 用户直接看到"对模型说的话"。实录（r1 实跑，逐字）：

```
你问的是[领域]，但你的命盘同时提示了其他重要信息。格式：'顺便说一句（你可能没问但很重要）：'
+ 简要说明其他领域的好时机与需注意的风险（80字以内）。如果实在没有特别信息，输出空字符串。
```

生产档（deepseek）未观察到；`_parse_llm_output` 照常解析（回显是**合法 JSON**，不是解析错误）。

#### 9.5.2 判据（按**形态**，不是匹配某段 schema 原文）

`src/utils/fact_guard.py` 新增 D 段，**零 LLM、无网络、与 `scrub_turn` 同层**：

| 档 | 判据（形态） | 为什么这样切 |
|---|---|---|
| **强**（命中 1 条即判） | ① JSON 键形态 `"key":` ② JSON 数组片段 `[{` / `}]` ③ 中文占位符 `[领域]`（`[1]`/`[n]` 引用编号**不**命中） ④ 取值域枚举 `high/medium/low` ⑤ 元输出指令（`输出JSON`/`不要markdown`/`输出空字符串`/`不输出其他文字`） ⑥ **值以「（N字以内）」结尾** | 都是"自然中文用户文案几乎不可能出现"的结构/元语言形态 |
| **弱**（命中 ≥2 条才判） | ⑦ `格式：`/`示例：`/`取值：`/`请按以下…` ⑧ `N字以内` ⑨ `N-M句话` ⑩ `不超过N字` ⑪ `必须包含｜结合｜基于…` ⑫ `如'…'` ⑬ `时间窗口` ⑭ 英文字段名裸词（`serendipity`/`advice`/`timing`…12 个） | 单条可能出现在**正常建议**里（实测："2026年的关键时间窗口在立秋之后"、"签合同必须包含违约条款"、"比如'先复盘再行动'"、"每天写30字以内的感恩日记"）→ 单条不定罪 |

**阈值**：`强 ≥1 或 弱 ≥2`。**处置**：命中即**整段置空**（模板说明无部分保留价值；
片段删除只会留下语义残缺的半句）→ `serendipity/daily_tip/style_notes` 置空后
调用方/渲染方天然跳过；`advice` 置空 → 整条建议摘除（避免渲染 `📌 💼 事业： 【…】` 空壳行）；
5 条全摘 → 退回与"LLM 不可用"同款的 `FALLBACK_ADVICE`。

#### 9.5.3 正反例清单（每条都有测试；`tests/test_k63_schema_echo_guard.py`）

- **正例（17 种形态，必须清洗）**：实录回显样本；schema 六个字段的 spec 文本
  （`advice`/`timing`/`daily_tip`/`style_notes`/`confidence`/`serendipity` 各一条）；
  JSON 键；JSON 数组片段；中文占位符 ×2；取值域枚举；元指令 ×2；弱信号 ×2（两组）。
- **反例（20 条，必须一字不动）**：**真实实跑文案 5 条**（男/女/未知三档的 advice、
  daily_tip、style_notes、以及"顺便说一句（你可能没问但很重要）：…"那条）；
  中文引号 ×2；合法花括号 ×2（`{每天读书30分钟}` / `{3/8}`）；`[1]`/`[n]` 引用编号；
  全角括号 `【领域】`；"30字以内"日常建议；单条弱信号真实写法 ×5
  （时间窗口/必须包含/如'…'/1-2句话/不超过30分钟）；干支与普通冒号。
- **真实数据精度抽检**：拿 r1 三档实跑的**全部 39 个字段值**过判据 → 命中 **1** 个，
  且**正是那条真回显**（unknown 档 serendipity）；其余 38 个字段零误杀。

#### 9.5.4 植入实验（三条，互不掩盖）

| 实验 | 植入 | 结果 |
|---|---|---|
| **M1 摘掉接线** | `advisor_v2` 内把 `scrub_schema_echo` 换成恒等函数 | **3 failed**（`test_generate_cleans_echoed_fields` / `…all_actions_echoed_falls_back` / `…echo_path_is_advisor_only`） |
| **M2 判据过宽** | 把裸 `{` 加进强信号（模拟"顺手把花括号当 JSON"） | **2 failed**（反例 `brace_group` / `brace_math`）→ 证明"防误杀"断言有牙 |
| **M3 阈值退化** | `is_schema_echo` 改为"弱信号 ≥1 即判" | **6 failed**（5 条单弱信号反例 + 阈值用例） |
| 还原 | — | **47 passed**（`diff -q` 与备份**逐字节相同**） |

#### 9.5.5 实跑端到端（glm-4-flash，4 轮，守卫已开）

| 轮 | 原始 LLM 文本 | 结果 |
|---|---|---|
| 1 | 含 `[领域]` 回显 | 守卫触发 → `serendipity=""`；**5/5 条建议原文保留**（零误杀） |
| 2 | 含 `[领域]` 回显 | 同上（另有一次 `（30字以内）` 规格置空） |
| 3 | 无回显（模型自行填内容） | `serendipity="你问的是事业，但你的命盘同时提示了感情和健康方面的好时机。"` **原样保留** |
| 4 | 无回显 | 逐字段比对：**改动字段数 = 0**（解析层 5 条 advice 与最终完全一致） |

> 第 3 轮尤其说明判据的分寸：它**长得像**模板（同样以"你问的是…但你的命盘同时提示了…"开头）
> 但内容是真建议 → **放行**；只有**未填写的模板本身**（占位符/元指令）才被判定为泄漏。

#### 9.5.6 边界（明确不做的事）

1. **只动呈现字段**：`_call_llm` 与 `_parse_llm_output` **一块一行未改**（有测试
   `test_generate_echo_path_is_advisor_only` 显式锁"解析层仍返回原文、呈现层才清"）；
   未走"解析层丢弃整条"的路线（那会改到主链行为，按控制方要求先报批——本批未采纳）。
2. **不改 `scrub_turn`**：它是 B/C **词表**防线、被 handler/advisor/chat_stream 多处消费；
   D 是**独立函数**，理由写在 `fact_guard.py` 模块 docstring（消费面/判据正交/告警语义三条）。
3. **未接主链**：主链正文/流式链不在本批范围（`handler.py` 零改动）。
   「主链是否也需要 D」**控制方已裁决：先测量、不现在实现** → 见 **§9.6 登记项**。

---

### 9.6 登记项（带触发条件）：「主链是否也需要 D」——**先测量，不实现**

**裁决（控制方 2026-09-20）**：**不现在实现**。理由：① 主链跑 DeepSeek（保真度更好），
**目前没有证据**表明主链会回显 schema；② `<tool_calls>` 那类形态已由既有
`ToolJsonChunkFilter`（k11-E）负责，再叠一层可能互相干扰；③ D 是**呈现层删段**，
盲扩到主链有误杀风险（§9.3-7 的已知代价就是证据）。

**本批做了什么**：**只测量**（零代码改动、零提交），结果见下。

#### 9.6.1 首轮测量（本批实跑，只读）

| 源 | 样本 | 方法 | D 判泄漏 |
|---|---|---|---|
| **A 主链归档回复**（DeepSeek 产出，2026-07-21） | **275** 条 | `data/eval/results/bench_2250_local.jsonl` 的 `our_response` 逐条过 `is_schema_echo` | **0** |
| **C 降级档专项**（GLM-4-Flash，真实 lite 路径） | **5** 轮 | `FortuneLLM.chat_conversation(history, lite=True)`（= `_chat_lite` → GLM），真实用户话术 5 条（含"帮我看看我的时柱"这类会诱发工具的话题） | **0** |

- 5 轮降级档回复**全部是自然中文**：`<tool_calls>` 标签 **0**、裸 JSON 片段（`"tool":`）**0**。
- **合计 280 条主链样本，0 命中。**
- **样本局限（不夸大）**：A 是**旧档**（2026-07-21，人设时期）且由 DeepSeek 产出；
  C 只有 5 轮，且 lite prompt 明说"不调用任何工具"（天然压低工具 JSON 泄漏面）。
  结论是"**尚无证据表明有风险**"，**不是**"已证明无风险"。

#### 9.6.2 触发条件（满足任一条即重启该决定）

| # | 触发条件 | 为什么是这条 |
|---|---|---|
| **T1** | **主链出现一次实测回显**：用户反馈 / 客服转述 / 日志抽查 / 例行抽样命中"给模型看的话"进了回复 | 事实触发，最硬 |
| **T2** | **降级档对用户可见**成为常态：额度耗尽后的 GLM 精简回复、或 `FORTUNE_LLM_PROVIDER=glm` 灰度上线成为常规路径 | 回显是**小模型**现象；DeepSeek 档未观察到 |
| **T3** | 主链 prompt **新增/变更 schema 段**：往 `CHAT_PROMPT`/润色 prompt 里加 JSON 示例、字段名清单、占位符、取值域 | 回显由 prompt 里的 schema 形态诱发（advisor 事故的直接机制） |
| **T4** | 主链工具协议切到 **JSON 工单路径**（`handler.py:3072`：`provider != 'deepseek'`）并在线上常态运行 | 该路径的输出契约本身就是结构化的 |

#### 9.6.3 测量方法（复用本批的只读手段，三源；**无需新代码**）

```bash
# 源 A：归档主链回复（离线、零成本、可随时跑）
cd /home/a/k63-wt && /home/a/fortune-agent/.venv/bin/python -c "
import json,sys; sys.path.insert(0,'.')
from src.utils.fact_guard import is_schema_echo, schema_echo_hits
rows=[json.loads(l) for l in open('data/eval/results/bench_2250_local.jsonl',encoding='utf-8') if l.strip()]
hits=[(r['id'], schema_echo_hits(r.get('our_response') or '')[:3]) for r in rows if is_schema_echo(r.get('our_response') or '')]
print(f'{len(rows)} 条命中 {len(hits)}'); [print(h) for h in hits[:10]]"

# 源 C：降级档专项（GLM 免费；脚本 /dev/shm/k63_mainchain_measure.py，样本/话术可换）
TMPDIR=/dev/shm nice -n 10 /home/a/fortune-agent/.venv/bin/python /dev/shm/k63_mainchain_measure.py

# 源 B（**需控制方批准**，本批未做）：线上真实回复抽样 —— 生产库 consultations.analysis
#   只读连接 + LIMIT 抽样 + 禁止高峰时段跑；输出仅统计与命中片段，不落盘原文。
```

- 判据复用本批 D（`is_schema_echo` / `schema_echo_hits`）；
- 建议节奏：**每月一次**，或 T1-T4 任一触发时对源 A+B 跑一遍，**命中 > 0 即重启决定**；
- 若届时确认要接主链，建议**先只接"工具 JSON 片段"子集**（`"tool":` / `"params":` 形态），
  并与 `ToolJsonChunkFilter` 的边界先在报告里划清，避免全量形态误杀。

#### 9.6.4 附带观察（本批测量时发现，**非 k63 引入、未改**）

降级档多轮对话经 `chat_conversation(lite=True)` 时，最终发给 GLM 的 messages 是
`[system: CHAT_PROMPT_LITE, system: CHAT_PROMPT, …history]` —— **两条 system 同时在场**，
且二者对工具的态度相反。**探针验证**（拦截 `glm_openai_completion` 的 messages，不真实外呼）：

```
messages 条数: 3
  [0] role=system len= 370  CHAT_PROMPT_LITE（"回复精简…不调用任何工具"）
  [1] role=system len=2398  CHAT_PROMPT（含 10 处 <tool_calls> 工具示例）
  [2] role=user   len=   6  今天适合干嘛
```

本批 5 轮实测**未**出现工具 JSON 泄漏，但这是 T2/T4 触发时值得一并核查的结构性隐患；
属**既有实现**（`src/llm/client.py:509`），本批按边界**一行未动**，仅登记。

---

## 10. 交付物

| 文件 | 说明 |
|---|---|
| 分支 `k63-persona-unify` @ `fbe4ec7` | r1 口吻统一：`src/engines/advisor_v2.py` + `tests/test_k11_fact_discipline.py`（未合并/未推送） |
| 分支 `k63-persona-unify` @ `d218a32` | T103 评测输入同步：`data/eval/agent_tasks.jsonl`（1 字段；原句留档在提交信息，见 §9.4） |
| 分支 `k63-persona-unify` @ `c883df6` | r2 schema 回显守卫：`src/utils/fact_guard.py`（新增 D 段）+ `advisor_v2.py` 接线 + `tests/test_k63_schema_echo_guard.py`（47 例） |
| 分支 `k63-persona-unify` @ `7427363` / `9de35b1` | r2 收口：D 段说明措辞 + 已知边界用例（均无行为变更） |
| 本报告 | `/mnt/e/fortune-agent-deploy/.superpowers/sdd/task-k63-report.md` + worktree 副本 `/home/a/k63-wt/.superpowers/sdd/task-k63-report.md` |
| r1 实跑脚本与产物 | `/dev/shm/k63_live_run.py`、`/dev/shm/k63_live_out.json`（三段完整卡 JSON）、`/dev/shm/k63_live.log` |
| r2 实跑脚本与产物 | `/dev/shm/k63r2_live_guard.py`、`/dev/shm/k63r2_live_guard_out.json`（3 轮守卫后结果）、`/dev/shm/k63r2_diff.py`、`/dev/shm/k63r2_diff_out.json`（守卫前/后逐字段比对） |
| §9.6 主链测量脚本与产物（只读，无提交） | `/dev/shm/k63_mainchain_measure.py`、`/dev/shm/k63_mainchain_measure.json`（5 轮降级档原文 + 判据结果）；源 A 为仓库内 `data/eval/results/bench_2250_local.jsonl`（275 条，未改动） |
