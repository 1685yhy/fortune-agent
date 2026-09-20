# k65 报告：降级档（lite）双 system 指令自相矛盾 — 修复

> **【入库说明 · 2026-09-20 · b2fix 批次】** 本文件**原样入库**：此前它只存在于
> `/mnt/e/fortune-agent-deploy/.superpowers/sdd/` 与 `/home/a/k65-wt/`，
> **未随任何提交进仓库**（k62/k64 都带了报告，k63/k65 漏了 —— 合批审查 I-1）。
> **除本段外，内容与最终版逐字节相同**（入库源文件 sha256
> `ed4a9a9be79c4648d7a08fdd114b123c67f2de381d76eaedd6c01dfe024d4ab2`），
> 含 **r1 + r2 + r3 三段全文**与文末 **`LEGACY-FABRICATION-01`（P1）** 登记项。
> **一处计数订正（细节见 `task-integration-fixup-report.md` §计数订正 M-4）**：
> 本报告 §r3-6 的「合计 741 passed / 1 skipped」是**在 k65 自己的 worktree 上按 4 段
> 子集分别读数相加**；在集成 tip（`1dae341`）上按**同样的 25 个文件跑并集**得
> **742 passed / 1 skipped / 0 failed**（b2fix 于 `batch2-reviewfix` 复跑同值
> 742/1/0，158.51s，跑完 `git status` 空）。**逐段复跑为 75/331/221/115 = 742**
> （其中本报告 §r3-6 第 3 段记 218 实为 **221**、第 4 段记 117 实为 **115**）
> ⇒ 差 1 来自**换树取样 + 分段记账**，**不是失败或回归**（两次读数均 **0 failed**）。
> 另：本报告 §r2-5 的「降级档 383 / 主链 4,573 字符」与审查者 harness 的
> 578 / 4,590 **量级一致**，差值可由 harness 历史长度解释（**未改**，仅备注）。

- 批次：k65
- 分支：`k65-lite-double-system`（worktree `/home/a/k65-wt`，起点 main `ea110c3`）
- 状态：**只提交，未合并、未推送、未碰生产**（含 r2 / r3）；k65 已收口
- 提交：`a7387c2`（r1：client 双 system）+ `0e18972`（r2：handler 按能力裁剪工具清单）+ `582b778`（r3：CHAT_PROMPT_LITE 补能力边界）
- 改动文件：`src/llm/client.py`（+8/-3）、`src/bot/handler.py`（r2，+12/-4）、`src/llm/prompts.py`（r3，+7/-0）、新增 `tests/test_k65_lite_single_system.py`（**24 用例**）
- ⚠️ **遗留登记项见文末「LEGACY-FABRICATION-01」（P1，已转独立批次）**
- 未改宽任何既有断言

---

## 1. 独立复核（拦截真实 messages 实测，非读码推断）

方法：monkeypatch `src/llm.client` 的**两个 transport 出口**
（`glm_openai_completion` / `deepseek_anthropic_completion`），把真正要发出去的
`messages` 列表原样捕获打印（脚本 `.superpowers/sdd/k65-evidence/capture.py`，纯本地、不联网）。

**修前实测（lite=True + 3 轮历史）**：

```
--- chat_conversation(lite=True) transport=GLM model=glm-4-flash
    messages 条数=5  system 条数=2  总字符=2789
    [system#0] len=370   tool_calls标记=0   head='你是易理明灯，一个懂命理的 AI 助手。说话像朋友一样自然亲切，用大白话。'
    [system#1] len=2398  tool_calls标记=10  head='你是易理明灯，一个懂命理的 AI 助手。说话方式像一个见多识广的朋友——自然、亲'
    [2] role=user      len=2  head='你好'
    [3] role=assistant len=11 head='你好呀，有什么想问的？'
    [4] role=user      len=8  head='帮我看看我的时柱'
```

⇒ **控制方描述完全复现**：两条 system 同时在发给 GLM 的 payload 里，
一条禁工具、一条教 10 组工具调用。

**与控制方数字的两处差异（如实标注，非推翻结论）**：

| 项 | 控制方说 | 我实测 | 说明 |
|---|---|---|---|
| `CHAT_PROMPT` 长度 | 2,045 字 | **2,398 字符** | 差值 353 字符 = `_web_tool_guide_line()` 在 import 时按 `web_search_available()` 追加的联网工具宣传行；2,045 是**静态正文**长度，部署态运行时是 2,398 |
| `<tool_calls>` 处数 | 20 处 | 开标签 **10** 个 / 开+闭 **20** 个 | 口径一致，控制方含闭标签 |

**对照组（同输入、同历史，只切 lite）**：

```
--- chat_conversation(lite=False) transport=DeepSeek
    messages 条数=4  system 条数=1  总字符=2419
    [system#0] len=2398 tool_calls标记=10   ← 只有主 prompt，无精简 prompt
```

**`:488` 单条路径（禁止改动的那条）实测本来就是对的**：

```
--- chat(lite=True) transport=GLM
    messages 条数=2  system 条数=1  总字符=378
    [system#0] len=370  = CHAT_PROMPT_LITE
    [1] role=user len=8
```
⇒ 确认它走 `_chat_lite(user_message=...)` 无历史分支，只有一条 system。**未改动。**

**边界探测**：往 history 里塞一条调用方自带的 system，修前 system 变 **3 条**：

```
--- chat_conversation(lite=True, extra system) transport=GLM
    [system#0] len=370  ← CHAT_PROMPT_LITE
    [system#1] len=2398 ← CHAT_PROMPT（多余、矛盾）
    [system#2] len=12   ← 调用方自带的 [可用工具清单]（自定义）
```

---

## 2. 修法与其边界策略

### 2.1 修法（根因修复，改在源头）

`chat_conversation()` 的 lite 分支原本把**已经拼好主 prompt 的整包**
当 `history` 往下传，而 `_chat_lite()` 的契约是"前置唯一精简 prompt + 展开 history"，
于是主 prompt 被当成"历史"混进去。改为**把调用方原始 `history` 直接传下去**：

```python
 if lite:
     # k65：只传调用方原始 history；主 prompt 由 _chat_lite 独占注入。
     return self._chat_lite(history=history, max_tokens=400,
                            stream_cb=stream_cb)
 messages = [{"role": "system", "content": CHAT_PROMPT}]
 messages.extend(history)
```

- 主链的两行 payload 构造**原样保留、只是挪到 lite 早返回之后**；
- 选择"改源头"而非"在 `_chat_lite` 内剥离主 prompt"的原因：
  `_chat_lite` 不是本缺陷的过错方——它的契约本身正确，是上游违约；
  且**植入实验要求"把双 system 改回去必须红"**，若在 `_chat_lite` 内加剥离兜底，
  会把上游的回归重新掩盖成绿色（吸收掉变异），违背该验收项。
- 修后语义：`_chat_lite` 独占注入唯一的 `CHAT_PROMPT_LITE`；
  主 prompt 从结构上**不可能**再进入精简链路（不是"注入后再删"，是"根本不注入"）。

### 2.2 边界策略：调用方 history 里本来就含别的 system 消息

**策略：只剥离已知主 prompt（本例是"从源头不注入"），其余 system 一律原样保留。**

理由：
1. `_chat_lite` 只拥有"我来前置唯一精简 prompt"这一项权力；调用方 history 里的
   其它 system 是**调用方自有语义**（工具清单、角色/安全叠加、A/B 实验开关……），
   LLM 客户端无从判断其意图；
2. 盲目全剥会**静默改写调用方行为**——调用方以为自己在给模型下指令，
   而指令在精简档被悄悄吞掉，属于比本缺陷更难排查的隐性契约破坏；
3. 主 prompt 是本客户端**自己注入的已知常量**（模块级 `CHAT_PROMPT`），
   剥离它没有语义风险，因而只对它做处理。

**实测验证（修后）**：

| 场景 | 修前 | 修后 |
|---|---|---|
| 纯净 history | 5 条 / **2** system `[370, 2398]` / 2789 字符 | 4 条 / **1** system `[370]` / **391** 字符 |
| history 含调用方 system | 6 条 / **3** system `[370, 2398, 16]` / 2805 字符 | 5 条 / **2** system `[370, 16]` / **407** 字符 |

⇒ 两种情况都**恰好剥离 1 条、且恰是那 2398 字符的主 prompt**；
调用方自带的 16 字符 system 在修后仍在原位（顺序 `LITE → 调用方 system → 历史`）。
未使用"全剥"，故未触发"停下报我"红线。

---

## 3. 主链逐字节不变（对照证据）

方法：把修前（`git show HEAD:src/llm/client.py`）与修后各跑一遍**同一输入**的
payload dump，序列化后比 `messages` 全等 + sha256（脚本 `.superpowers/sdd/k65-evidence/dump.py`）。

| 场景 | 修前 sha256(msgs) | 修后 sha256(msgs) | 逐字节相同 |
|---|---|---|---|
| `main_chain`（lite=False） | `812dcd2aba017562` | `812dcd2aba017562` | ✅ **True** |
| `main_extra_system`（lite=False + 调用方 system） | `2844c3eb32127797` | `2844c3eb32127797` | ✅ **True** |
| `lite_path`（lite=True） | `2804d127699576bb` | `5b5eb69e9b7222ba` | 预期不同（本次修复目标） |
| `lite_extra_system` | `b74670faaf206916` | `ebf8bc6ee13c4048` | 预期不同（剥掉且仅剥掉主 prompt） |

主链两条场景的 `n_messages / n_system / system_lens / total_chars` 也全等
（4/1/[2398]/2419 与 5/2/[2398,16]/2435）。**主链对 DeepSeek 的 payload 零变化。**

---

## 4. 降级档真实输出对照（真调免费 `glm-4-flash`，未用 DeepSeek）

`glm-4-flash`、`max_tokens=400`、`temperature=0.7`，与控制档位一致；
key 取自部署 `.env` 的 `ZHIPU_API_KEY`。脚本 `.superpowers/sdd/k65-evidence/glm_ab.py / glm_ab2.py / glm_ab3.py`。

### 4.1 实验一：纯净形态（无 handler 工具清单），4 条输入

| 输入 | 修前(双 system) | 修后(单 system) | 工具标记 |
|---|---|---|---|
| T1「帮我看看我的时柱」 | 40 字 | 46 字 | 均无 |
| T2「帮我查一下《滴天髓》里怎么讲正官格的」 | 113 字 | 119 字 | 均无 |
| T3「我昨晚梦见大海和淋雨，帮我解梦」 | 118 字 | 78 字 | 均无 |
| T4「最近工作有点累，随便聊聊」 | 41 字 | 64 字 | 均无 |

⇒ **诚实结论：在这一组里差异不明显**，`glm-4-flash` 四次都没吐工具调用标记，
长度也没有一致方向。**这一组不足以支撑"修了就好很多"的说法。**

### 4.2 实验二：**真实生产形态**（含 handler 注入的 `[可用工具清单]`），2 次采样

这是生产里真正发生的形态——`handler.py:11219` 对 lite 与非 lite **无条件**
注入 `[可用工具清单]`（2,153 字符），所以修前是 **3 条 system**：

```
修前 payload: n_msg=6 n_sys=3 total_chars=4971
修后 payload: n_msg=5 n_sys=2 total_chars=2573      ← -48% 字符
```

**P1「帮我排盘：1990年5月20日 下午3点 北京 男」**

- 修前（双 system）rep1/rep2 **都吐出真工具调用**：
  `好的，我来帮你排个盘。请稍等一下。<tool_calls>[{"tool": "bazi_chart", "params": {"text": "1990年5月20日 下午3点 北京 男"}}]</tool_calls>`
  且 rep2 在此之后**继续编造了一整张八字命盘**
  （「年柱：庚午（金）月柱：己巳（土）日柱：庚辰（金）时柱：庚午（金）」
  并给出喜用神、"适合从事与土金相关的行业"等具体结论）——
  **降级档既违约（说了不调工具）又编造了排盘结果**。
- 修后（单 system）rep1/rep2 **均不再输出 `<tool_calls>` 标记**，
  回复 57 字符：`排盘\n{"出生年月日时": "1990年5月20日 下午3点", "出生地点": "北京", "性别": "男"}`。

**P2「下个月搬家，帮我选个日子」**

- 修前 rep1 **编造具体日期**：「2023年10月27日（星期五）/ 10月28日 / 10月29日」
  —— 年份错到 **2023**（今天 2026-09-20），且 lite 档根本没有择日工具可用；
  rep2 28 字符反问。
- 修后 rep1/rep2 均无编造日期，转为反问补充信息。

**P3「帮我查一下我的档案记录」**

- 修前 rep2 输出 `record_lookup\n{"query": "我的档案"}`（工具味的裸参数行）；
- 修后 rep2 改为先说"让我帮你查一下"再答，不再输出该参数行。

**可观测差异总结（降级档）**：
1. **不再输出 `<tool_calls>` 工具调用标记** —— 该标记会真的被主链 `_run_tool_loop` 解析，
   而 lite 档设计上不该调工具（`handler.py:11232` 注释写明的意图），修前等于把模型的
   工具意图直接漏给了用户（用户看到 `[{"tool": "bazi_chart"...}]` 这种机器文本）；
2. **减少编造**：修前会"假装调了工具"进而编造排盘结果 / 编造 2023 年的吉日；
3. **成本**：真实生产形态 payload 从 4,971 → 2,573 字符（**-48%**），
   纯净形态 2,789 → 391（**-86%**）——降级档本来是成本控制档，修前反而每轮多付 2,398 字符。

---

## 5. 植入实验（把双 system 改回去必须红）

在**已修复的** `src/llm/client.py` 上植入变异体，精确复原修前行为：

```python
 if lite:
     _m = [{"role": "system", "content": CHAT_PROMPT}]
     _m.extend(history)
     return self._chat_lite(history=_m, max_tokens=400, stream_cb=stream_cb)
```

`python3 -m pytest tests/test_k65_lite_single_system.py -q` →
**`6 failed, 6 passed`**（红）。

失败的 6 条正是 lite system 唯一性/边界族：

```
FAILED TestLiteSystemSingularity::test_lite_system_exactly_one_and_is_lite
FAILED TestLiteSystemSingularity::test_lite_payload_drops_main_prompt_chars
FAILED TestLiteSystemSingularity::test_lite_glm_payload_shape
FAILED TestLiteSystemSingularity::test_lite_deepseek_fallback_single_system
FAILED TestLiteSystemSingularity::test_lite_stream_single_system
FAILED TestLiteCallerSystemBoundary::test_caller_system_preserved_main_prompt_stripped
```

仍在绿的 6 条 = 主链逐字节锁（5 条）+ 调用方 history 不被就地修改（1 条）——
**符合预期**：变异只影响 lite 档，主链锁就该纹丝不动。

还原修复后 → **`12 passed`**。

---

## 6. 测试

新增 `tests/test_k65_lite_single_system.py`（12 用例，全 mock 不联网）：

- `TestLiteSystemSingularity`（5）：① 纯净 history 的 system 唯一且 == `CHAT_PROMPT_LITE`；
  ② 字符数 == LITE + 历史（把 2398 开销钉死）；③ GLM raw payload 三要素；
  ④ **GLM 挂 → DeepSeek 回退也只有一条 system 且仍是精简 prompt**；⑤ 流式同断言。
- `TestLiteCallerSystemBoundary`（2）：⑥ 边界策略锁 —— 只剥主 prompt、调用方 system 原序保留；
  ⑦ 剥离不得就地改写调用方传入的 history 对象。
- `TestMainChainPayloadLock`（5）：⑧ 主链 `messages == [CHAT_PROMPT] + history` 逐字节锁；
  ⑨ 主链遇调用方 system 顺序/内容不变；⑩ `:488` 单条 lite 路径 == 精确两条消息；
  ⑪ 单条 lite 忽略 `system_prompt`（handler B2-11 传主 prompt 也不进精简链路）；
  ⑫ 非 lite 单条仍透传 `system_prompt`。

**既有断言一条未改、未改宽**：`tests/test_chat_quota.py::TestDowngradeChain` 原样通过
（它只断言 `messages[0] == CHAT_PROMPT_LITE`，正是这个"只看第一条"的断言面
让双 system 漏过了历史评审——本次用**新增**用例补齐"唯一性"，而不是去动它）。

**定向子集回归**（按要求只跑子集，全量留给控制方今晚统一安排；
命令前缀 `TMPDIR=/dev/shm nice -n 10 ionice -c2 -n7`）：

| 子集 | 结果 |
|---|---|
| `test_k65_lite_single_system` + `test_chat_quota` + `test_toolguide_mainchain` | 44 passed |
| `test_fastpath` `test_single_stream_dedupe` `test_intent_routing` `test_tool_scene_routing` `test_member_pay` | 132 passed |
| `test_k33_glm_drift_format` `test_k33_llm_unified_route` `test_k11b_search_trigger` `test_k15_eval_tails` `test_eval_r1_1` `test_eval_r1_2` `test_k38_e6_red_fixes` `test_capability_registry` `test_emoji_cleanup` | 331 passed, 1 skipped |
| `test_bot` `test_chart_reuse` `test_k41_search_seam` `test_k11_fact_discipline` `test_r13_profile_routing` `test_handler_qa_fix` | 167 passed |

**合计 674 passed / 1 skipped / 0 failed。**

---

## 7. 登记：缺陷影响面

- **谁在什么档位走 lite**：`src/bot/handler.py` 的自由对话分支——
  `downgraded=True` 时（免费用户当日对话额度 15 条用尽后，`src/services/chat_quota.py`）
  走 `chat_conversation(..., lite=downgraded)`（`handler.py:11223`）；
  `src/llm/client.py::_chat_lite` 优先 GLM-4-Flash，GLM 失败回退 DeepSeek 同款精简 prompt。
- **命中面**：**所有额度耗尽的免费用户，只要该用户会话有历史**
  （`handler.py` 有会话存储 → `chat_conversation` 多轮分支；单消息无历史分支
  `llm.chat` 走 `:488`，本来就正常）。
- **修复前可观测差异（对用户）**：
  1. 降级回复里可能直接出现 `<tool_calls>[{"tool": ...}]</tool_calls>` 机器文本；
  2. 模型"假装排了盘"并给出编造的四柱/喜用神（实测样本），或编造具体吉日（实测年份 2023）；
  3. 每轮多付 2,398 字符（约 48%~86% 的 payload 是冗余）——**成本控制档的成本反而被放大**。
- **修复后**：lite payload system 唯一且 == `CHAT_PROMPT_LITE`；不再出现工具调用标记；
  真实生产形态 payload 字符数 -48%。

---

## 8. 诚实披露 / 待控制方拍板

1. **【需拍板｜我停手未做】修后生产形态的 lite 路径仍剩 2 条 system**：
   `handler.py:11219` 对 lite/非 lite **无条件**注入 `[可用工具清单]`（2,153 字符），
   所以修后生产 payload 是 `[LITE] + [工具清单] + history`。
   我按控制方倾向"其余 system 保留"执行；但实测该残留清单**仍有工具味**：
   - 修后 P1 输出 `排盘\n{"出生年月日时": ...}`（裸参数行）；
   - 修后 P3 输出"请稍等，我帮你查一下"。
   隔离实验（P1/P2/P3 各 1 次，去掉清单）输出明显更干净、更诚实：
   「我这里不提供排盘服务」/「这个我不敢乱说，建议你请教专业的命理师」/
   「我无法访问个人档案记录」，payload 再降到 397~411 字符。
   ⇒ 若要把 lite 档做干净，需在 `handler.py` 对 `downgraded` 加条件跳过该注入。
   **这属于"全剥 system"红线，我不动 `handler.py`，等您指示**（选项 A：维持现状，
   接受 2 条 system 与残留工具味；选项 B：`if not downgraded:` gate 掉注入，
   lite 档 payload 再 -84%）。
2. **采样量小**：实验一 4 条输入各 1 次、实验二 3 条输入各 2 次，`temperature=0.7`，
   **不构成统计显著性**。结论"修前会输出工具调用/编造"由 P1 两次采样稳定复现支撑，
   "修后更精简"只在强诱导场景显著，普通闲聊场景差异不稳定（见 4.1）。
3. `CHAT_PROMPT` 实测 2,398 字符 ≠ 控制方给的 2,045（差 353 字符的联网工具宣传行），
   已在 §1 标注。
4. **边界策略是"只剥主 prompt"而非"全剥"**，已按您倾向执行并给了理由；
   若您要改成全剥，请回我，我改断言与实现。
5. 测试副作用（**非本批次引入**）：跑定向子集时 `data/memory/.json` 被某用例写入
   （`_updated_at` 被刷新），属既有测试隔离问题（`data/memory/*.json` 虽在
   `.gitignore` 但该文件已被 track，gitignore 不生效）。我已 `git checkout --` 还原，
   提交里不含它。建议另开批次处理。
6. **未跑全量**（按纪律）；未改任何引擎/支付/存储；未碰并行批次文件
   （`advisor_v2/fact_guard/mood_detector/emotion_soother/preference_dao/user.py/
   dashboard.py/quality_predictor/dream*/k55_dream*`）；`src/llm/prompts.py` 只读未改。
7. `tests/test_k65_lite_single_system.py` 中 `TestMainChainPayloadLock::test_main_chain_messages_byte_identical`
   的"逐字节"是**同一进程内构造式全等 + 跨进程 sha256 对照**（见 §3），
   不是持久化 golden 文件；若您希望锁定为磁盘 golden，请说，我加。

---

# k65 **r2** 追加：降级档按能力裁剪 `[可用工具清单]` 注入

- 提交：`0e18972`（r1 = `a7387c2`），分支 `k65-lite-double-system`
- 裁决：**选 B —— 在 `handler.py` 对降级档跳过 `[可用工具清单]` 注入**（控制方批准动 `handler.py`）
- 改动：`src/bot/handler.py`（+12/-4）、`tests/test_k65_lite_single_system.py`（12 → **17** 用例）
- **未动 `src/llm/prompts.py`，未改 `CHAT_PROMPT_LITE` 文本**（本轮明确禁止）；未改宽任何既有断言

---

## r2-1. gating 判据（要求 1）

**判据 = `_free_chat` 的 `downgraded` 形参**，不是消息内容猜测，也不需要跨层传参。

来源链路（全部是显式传参，可逐跳核对）：

```
src/services/chat_quota.py::chat_quota_status()
    "downgraded": used >= CHAT_DAILY_LIMIT          # 免费用户第 16 条起
  → src/main.py:1654 / 1703 / 1712                  process(..., downgraded=downgraded)
  → src/api/chat_stream.py:425/523/531/541          process(..., downgraded=downgraded)
  → MessageHandler.process(downgraded=...)   handler.py:6024
        handler.py:6049  self._downgraded[user_id] = bool(downgraded)
  → MessageHandler._free_chat(downgraded=...) handler.py:11064
        handler.py:11223  llm.chat_conversation(messages, lite=downgraded)   ← 同源
        handler.py:11220  if not downgraded:   ← r2 新增的 gate（同一变量）
```

⇒ gate 用的 `downgraded` 与**同一行传给 LLM 的 `lite` 是同一个变量**，
即"本轮是否走降级链路"的权威事实源，与消息内容无关。
（控制方担心的"需要跨层传参"不成立，未触发停止条件。）

**实测证明"判据是标志而非内容"**：**同一句**诱工具话术
`帮我排盘看看我的命格`，主链注入、降级档不注入（测试
`test_gate_is_flag_based_not_content_based`）；`_free_chat` 的真实返回值
`lite` 分别为 `False` / `True`。

**未改动的另一处注入**（`handler.py:11233` 无 session_dao 单消息分支）：
它把 `[可用工具清单]+CHAT_PROMPT` 作为 `system_prompt` 传给 `llm.chat()`，
而 `llm.chat(lite=True)` 在 client 层**丢弃** `system_prompt`（既有 B2-11 语义），
该分支的真实 payload 本来就无工具清单（由既有测试
`test_single_message_lite_ignores_system_prompt` 与 r2 新增
`test_single_message_branch_payload_unaffected` 双锁）——故**不改**，
避免为"行为等价"在 handler 里塞第二个冗余 gate。

---

## r2-2. 主链 payload 逐字节不变（要求 2）

方法同 r1：修前 = `git show HEAD:src/bot/handler.py`（即 `a7387c2`，只有 r1 的 client 修复），
修后 = 工作树；**端到端**跑真 `MessageHandler._free_chat` + 真 `FortuneLLM`，
拦截最终发给模型的 payload，比 `messages` 全等 + sha256
（脚本 `.superpowers/sdd/k65-evidence/r2_e2e.py` / `r2_cmp_e2e.py`）。

| 场景 | transport | 修前 sha256 | 修后 sha256 | 逐字节相同 |
|---|---|---|---|---|
| `e2e_main` | DeepSeek | `060d1fe06c8c1f0d` | `060d1fe06c8c1f0d` | ✅ **True** |
| `e2e_main_caller_system`（history 含调用方自带 system） | DeepSeek | `4863f564749748db` | `4863f564749748db` | ✅ **True** |
| `e2e_downgraded` | GLM | `6b22a1937cfdb712` | `91203af64e2103c3` | 预期不同（r2 目标） |
| `e2e_downgraded_caller_system` | GLM | `5b75a51a1dd333e0` | `6860e674fdfdccad` | 预期不同（r2 目标） |

主链两条场景的 `n_messages / n_system / system_lens / total_chars` 亦全等：
`e2e_main` = 5 / 2 / `[2398, 2162]` / 4573；`e2e_main_caller_system` = 6 / 3 / `[2398, 2162, 19]` / 4592，
且两条**都仍含工具清单**（`tool_list_present=True`）。**主链零变化。**

handler 装配层（不含 client 注入的 LITE）另有一份 dump 对照，结论一致
（脚本 `r2_dump_handler.py` / `r2_cmp_handler.py`）：
`handler_main` sha `16e411835c12810e`、`handler_main_caller_system` sha `a0d94cd829567aa9` 前后相同。

---

## r2-3. 降级档真实输出对照（要求 3，**每格 N=9 采样**）

`glm-4-flash`、`max_tokens=400`、`temperature=0.7`，真调（key 取自部署 `.env`）。
修前 = `[LITE]+[CHAT_PROMPT]+[工具清单]+历史`；修后 = `[LITE]+历史`。
脚本 `r2_glm_r2_ab.py` / `r2_glm_r2_ab2.py` / `r2_analyze_A.py`。

### A「帮我排盘：1990年5月20日 下午3点 北京 男」

| 指标 | 修前 | 修后 |
|---|---|---|
| payload 字符 | 4,971 | **411** |
| 输出 `<tool_calls>` 工具标记 | **7/9** | **0/9** ✅ |
| 「工具标记 + 接着编盘」同时出现 | **6/9** | **0/9** ✅ |
| 编造具体四柱/喜用神 | 6/9 | **4/9** ❌ |

- **已修**：工具调用标记 7/9 → 0/9，"假装调了工具再编结果"这条链彻底断掉（6/9 → 0/9）。
- **未修**：即使没有工具清单，模型**仍会凭空编四柱**，修后 4/9，例如
  「年柱：庚午（金马）/ 月柱：己巳（土蛇）/ 日柱：庚辰（金龙）/ 时柱：壬午（水马）」、
  「你的日柱是庚辰，月柱是己巳，年柱是庚午……适合从事与火土相关的行业」。
  同一个 payload 的 4 次编造**互不一致**（庚辰/壬午 vs 壬午/庚午），是明确幻觉。
- 另有 5/9 修后回复是诚实的拒绝：「这个我不敢乱说，我无法直接排盘」「建议找专业的命理师」。
- （注：A 的"具体日期"指标被**用户自己输入的生日**污染，不计入结论。）

> ⚠️ **验收项未达成**：控制方要求 A「**不得再出现任何具体四柱/喜用神结论**」，
> 修后仍有 4/9。见 r2-6。

### B「下个月搬家，帮我选个日子」

| 指标 | 修前 | 修后 |
|---|---|---|
| payload 字符 | 4,958 | **398** |
| 编造具体日期 | **0/9** | **2/9** ❌ |

- 修后 2 例编造：「**这个月15号或16号比较适合搬家，这两天吉利。**」
  「**这个月15号或23号比较适合搬家**，这两天五行中木旺，有利于迁移。」
- **诚实结论**：N=9 vs 9，Fisher 精确检验 p≈0.47，**不显著**；
  且 **r1 曾在修前侧观察到编造「2023年10月27日」**。
  ⇒ 两轮合起来看，**择日编造在两个条件下都以低概率出现，方差主导**，
  既不能说 r2 修好了它，也不能说 r2 导致了它。**该验收项同样未达成**，
  且**不能**归因于这次的工具清单裁剪。

### C「最近工作有点累，随便聊聊」（对照组）

| 指标 | 修前 | 修后 |
|---|---|---|
| payload 字符 | 4,958 | **398** |
| 编造/异常 | 0/9 | **0/9** ✅ |

修后回复正常（「工作累是常有的事，适当休息调整很重要……」「可以试试冥想或者做一些轻松的运动」），
**没有把正常回复削没**。✅

### A'「1990年5月20日 想给孩子起名」（scene_hint 真实诱工具路径）

| 指标 | 修前 | 修后 |
|---|---|---|
| payload 字符 | 4,963 | **403** |
| 编造 | 0/6 | **0/6** ✅ |

### 生产可达性实测（重要限定）

那条排盘话术在生产 handler 里**不一定**走到 lite LLM——实测（脚本 `r2_routing.py`）：

| 场景 | 实际分支 |
|---|---|
| 无档案 + 完整生日 | **非 LLM 分支**（回「好的，已记下：出生于1990年…」出生信息引导） |
| 无档案 + 完整生日 + `scene_hint`（起名/择日等） | **chat_conversation（lite 多轮）** ← 风险路径 |
| 有档案 + 完整生日 | `_handle_bazi`（确定性排盘引擎，非 lite LLM） |

⇒ A 的编造风险**经由 `scene_hint` 场景**（话术里同时带生日）真实可达；
不是所有"帮我排盘 + 生日"都会命中 lite LLM。上文 A 的采样是**直接对 LLM 层**做的，
用于隔离变量，不代表该话术 100% 命中生产 lite 路径。

---

## r2-4. 测试（要求 4）

`tests/test_k65_lite_single_system.py`，**12 → 17 用例**，新增
`TestHandlerDowngradeToolListGate`（5 条，跑 `_free_chat` 真实方法体）：

| 要求 | 用例 | 断言 |
|---|---|---|
| ① 降级档 payload **不含**工具清单 | `test_downgraded_payload_excludes_tool_list` | `[可用工具清单]` / `build_tool_description()` / `bazi_chart` 均不出现；`lite is True` |
| ② 主链 payload **仍含**（防一刀切） | `test_main_chain_payload_still_includes_tool_list` | `msgs[0] == "[可用工具清单]\n" + build_tool_description()`；`lite is False` |
| 判据是标志非内容 | `test_gate_is_flag_based_not_content_based` | **同一句话**两种结果；system 条数差恰好 1 |
| 裁剪量恰好 1 条 | `test_downgraded_keeps_history_intact` | 降级档 `messages` == 主链 messages 去掉那条工具清单（其余逐条相等） |
| 单消息分支不受影响 | `test_single_message_branch_payload_unaffected` | 该分支 `lite=True`，payload 无工具清单 |
| ③ r1 的 lite system 唯一性锁 | `TestLiteSystemSingularity` / `TestLiteCallerSystemBoundary` / `TestMainChainPayloadLock`（12 条） | **原样保留未改** |

**④ 植入实验（把注入改回无条件 → 必红）**：变异体
`if not downgraded:` → `if True:`，跑本文件：

```
3 failed, 14 passed
FAILED TestHandlerDowngradeToolListGate::test_downgraded_payload_excludes_tool_list
FAILED TestHandlerDowngradeToolListGate::test_gate_is_flag_based_not_content_based
FAILED TestHandlerDowngradeToolListGate::test_downgraded_keeps_history_intact
```

红的正是 gate 那 3 条；绿的 14 条 = 主链锁 + client 层 lite 锁 + 单条路径锁
（**符合预期**：变异只该影响降级档）。还原后 → **17 passed**。

**既有断言一条未改**：`test_toolguide_mainchain.py::test_free_chat_first_round_system_has_tool_list`
（主链注入，`downgraded` 默认 False）原样通过，未被 r2 影响。

**定向子集回归**（未跑全量）：

| 子集 | 结果 |
|---|---|
| `test_k65_lite_single_system` `test_toolguide_mainchain` `test_chat_quota` `test_handler_qa_fix` | 68 passed |
| `test_fastpath` `test_single_stream_dedupe` `test_intent_routing` `test_tool_scene_routing` `test_member_pay` `test_bot` `test_chart_reuse` | 205 passed |
| `test_k33_glm_drift_format` `test_k33_llm_unified_route` `test_k11b_search_trigger` `test_k15_eval_tails` `test_eval_r1_1` `test_eval_r1_2` `test_k38_e6_red_fixes` `test_k41_search_seam` `test_k11_fact_discipline` `test_r13_profile_routing` | 344 passed, 1 skipped |

**合计 617 passed / 1 skipped / 0 failed。**

---

## r2-5. payload 体积（要求 5）

端到端真实 payload（含 client 层注入的 LITE）：

| 场景 | 修前 | 修后 | 降幅 |
|---|---|---|---|
| 降级档（纯净 history） | 2,545 字符（system `[370, 2162]`） | **383 字符**（system `[370]`） | **-85.0%** |
| 降级档（history 含调用方 system） | 2,564 字符（system `[370, 2162, 19]`） | **402 字符**（system `[370, 19]`） | **-84.3%** |
| 主链 | 4,573 / 4,592 字符 | 4,573 / 4,592 | **0%**（不变） |

⇒ 达标：**~400 量级**（383 / 402），且降级档 system 条数
2→1 / 3→2（工具清单那条被裁掉，调用方自带 system 保留）。
相对 k65 原始状态（r1 前）：真实生产 payload 从 4,971 → **383**（**-92.3%**）。

---

## r2-6. ⚠️ 未达成项与待决策（必须上报）

**控制方给的第 3 条验收，A / B 两条都未达成：**

1. **A「不得再出现任何具体四柱/喜用神结论」→ 未达成**（修后仍 4/9）。
   工具清单裁剪把「假装调用工具」这条路径断了（7/9→0/9、组合 6/9→0/9），
   但**编造本身还在**。
   **根因**：`CHAT_PROMPT_LITE` 只写了"**不调用任何工具**（不排盘、不检索古籍、
   不解梦、不联网）"，**没有**禁止"**没有排盘结果时凭空给出四柱/喜用神**"。
   精简档模型被要求排盘又没有工具，就直接凭记忆编。
2. **B「不得再出现具体日期」→ 未达成**（修后 2/9，修前 0/9；N=9 不显著，
   且 r1 在修前侧也见过编造）。**不能**归因于本次裁剪。

**这两条都不是本轮的 gate 能解决的**——按纪律：

> **遇到"要改 prompt 文本 / 要全剥 system / 要改宽断言"就停下报我。**

`CHAT_PROMPT_LITE` 文本修改被本轮**明确禁止**（"不要动 `src/llm/prompts.py`、
不改 `CHAT_PROMPT_LITE` 文本"），故**我停手未改**，请控制方裁决。

**可选的下一步（供拍板，我没有实施）**：

- **选项 ①（最小、推荐）**：在 `CHAT_PROMPT_LITE` 的「硬性要求」里加一行能力边界，
  例如"**没有真实排盘结果时，不要凭空给出四柱/干支/喜用神等具体命盘结论，
  直接说明精简模式暂不提供排盘，或反问用户**"。一行文本，不动结构。
- **选项 ②**：lite 档加输出侧守卫（拦截四柱形态文本）——属新子系统，
  且有误伤风险，且与 k63 的 `fact_guard.py`（本批禁碰）职责重叠，不推荐。
- **选项 ③**：接受现状（工具泄漏已修 0/9，编造率 6/9→4/9），
  把"精简档编造命盘"单列一个新批次，与主链 fact_guard 一起统一治。

**本轮我只做了被批准的事（B），没有越界去改 prompt 或加过滤。**

---

## r2-7. 断言面太窄会让缺陷隐身（值得写进显著位置的洞察）

k65 这个缺陷能在评审里活下来，**不是因为没人测降级链**——
`tests/test_chat_quota.py::TestDowngradeChain` 有 6 条降级链测试，
`test_lite_calls_glm_endpoint` 甚至**逐字段**断言了 `body["messages"][0]["role"] == "system"`
与 `body["messages"][0]["content"] == CHAT_PROMPT_LITE`，端点、model、Authorization 全查了。

它漏掉的原因是**断言面太窄**：`messages[0]` 只说"**第一条**是对的"，
**没有任何断言说"system 只有一条"**。于是上游多塞一条矛盾 system 时，
6 条测试**全绿**，缺陷隐身了一整轮。

⇒ 教训：**断言"某元素正确"不等于断言"没有多余元素"**。
凡是 §系统指令/权限/工具清单这类"叠加即矛盾"的位置，
必须配一条**唯一性/排除性**断言（本批的
`len(systems) == 1`、`CHAT_PROMPT not in systems`、`工具清单 not in payload` 即是）。

**推论（更值得记住的）**：本次裁掉工具清单后，`A` 的编造率只从 6/9 降到 4/9 ——
**如果 r2 只测"payload 有没有工具清单"这一个窄断言，就会宣布"信任级故障已修"，
而把 4/9 的编造留在生产**。所以本轮我按"真实输出对照"验收，
才把这个未达成项挖出来。**窄断言不只是漏缺陷，还会制造"修好了"的假象。**

---

## r2-8. 诚实披露（r2 追加）

1. **A / B 两条验收未达成**（见 r2-6），未越界修，等裁决。
2. **采样量与统计**：每格 N=9（首轮 3 + 追加 6），`temperature=0.7`。
   B 的 0/9 vs 2/9 **不显著**（Fisher p≈0.47），不能当结论，只能说"未改善"。
   A 的 7/9 → 0/9 是**结构性**差异（有/无工具教学），可信度高。
3. **A 的测试话术不必然命中生产 lite 路径**（见 r2-3 生产可达性表）——
   风险真实存在但经由 `scene_hint` 场景，样本是对 LLM 层直接做的，用于隔离变量。
4. **"具体日期"检测器口径**：正则覆盖 `X月X日 / X年X月X日 / X号 / X日`；
   「农历初七、初八」这类**没有被判为编造**（属黄历常识而非具体日期承诺），
   若按更严口径，B 修后的编造率会更高（2/9 → 4/9）。**我按宽口径报，偏保守。**
5. A 的"具体日期"指标被用户自带的生日污染，未计入结论。
6. 未跑全量（按纪律）；`src/llm/prompts.py` 只读；未碰并行批次文件；
   测试副作用 `data/memory/.json` 已还原，不在提交内。
7. 两个提交都**未合并、未推送**，生产零写入。

---

# k65 **r3** 追加：精简档补「能力边界」硬性要求（编造受控）

- 提交：`582b778`（r1 = `a7387c2`、r2 = `0e18972`），分支 `k65-lite-double-system`
- 批准范围：**改 `CHAT_PROMPT_LITE` 文本**（采纳选项①，措辞覆盖"任何具体结果"而非只写命盘）
- 改动：`src/llm/prompts.py`（+7/-0：注释 2 行 + 硬性要求 2 条）、`tests/test_k65_lite_single_system.py`（17 → **24** 用例）
- 精简档原有的「精简 80-150 字」「禁 emoji」「安全底线」**一律未动**

## r3-1. 改了什么（要求 1）

只在「## 硬性要求（精简模式）」里补 2 条，**两类编造都覆盖**：

```
+- 能力边界：精简模式没有排盘/择日/检索能力，没有真实结果就不得给出具体结论——
+  没有真实排盘结果时，不得给出四柱、干支、喜用神、神煞等任何具体命盘结论；
+  没有真实择日结果时，不得给出任何具体日期或时辰（"15号""农历初八"这类也算）
+- 遇到需要排盘/择日/检索的请求：直接说明精简模式暂不提供该功能，或反问澄清，
+  不要凭想象补一个答案
```

`CHAT_PROMPT_LITE`：**370 → 548 字符**。原有的
「不调用任何工具…」「回复精简：80-150 字」「禁止使用任何 emoji」「不确定的事如实说」
「## 安全底线」全部逐字保留（测试 `test_prompt_constant_declares_both_boundaries`
对 4 条原纪律做回归锁）。

## r3-2. 主链逐字节不变 + 回退路径一并生效（要求 4）

端到端（真 `MessageHandler._free_chat` + 真 `FortuneLLM`，拦截最终 payload；
脚本 `r3_verify.py` / `r3_cmp.py`）：

| 场景 | transport | 修前 sha256 | 修后 sha256 | 逐字节相同 | payload 含 LITE | 含边界句 |
|---|---|---|---|---|---|---|
| `e2e_main` | DeepSeek | `060d1fe06c8c1f0d` | `060d1fe06c8c1f0d` | ✅ **True** | **False** | False |
| `e2e_main_caller_system` | DeepSeek | `4863f564749748db` | `4863f564749748db` | ✅ **True** | **False** | False |
| `e2e_downgraded` | GLM | `91203af64e2103c3` | `11efb9e051a1e1d5` | 预期不同 | True | **True** |
| `e2e_downgraded_caller_system` | GLM | `6860e674fdfdccad` | `9b1242eb758be5bf` | 预期不同 | True | **True** |
| **`fallback_deepseek`**（GLM 挂 → 回退） | DeepSeek | `91203af64e2103c3` | `11efb9e051a1e1d5` | 预期不同 | True | **True** |

- **① 主链 sha256 前后相同**（含"调用方自带 system"场景）；
  主链 `system_lens` 恒为 `[2398, 2162]` / `[2398, 2162, 19]`，`total_chars` 4573 / 4592 **零变化**。
- **② 已核实 `CHAT_PROMPT_LITE` 没有被主链用到**：代码上它只在
  `src/llm/client.py::_chat_lite`（`from .prompts import CHAT_PROMPT_LITE`）出现；
  实测主链 payload `LITE在payload=False`、`边界句在payload=False`（测试
  `test_main_chain_carries_no_lite_boundary` 同为回归锁）。
- **③ 回退路径同样生效**：GLM 抛错 → DeepSeek 拿到的是**同一个精简 prompt**，
  实测 `sys_lens` `[370] → [548]`、`边界句在payload=True`。**这次改动对回退路径一并生效，已验证。**

降级档 payload 体积：383 → **561** 字符（纯净）/ 402 → **580**（含调用方 system）——
比 k65 原始状态（4,971 / 2,545）仍低一个数量级。

## r3-3. 真实输出四格对照（要求 2、3，**每格 N=12**）

`glm-4-flash`、`max_tokens=400`、`temperature=0.7`，真调（部署 `.env` 的 `ZHIPU_API_KEY`）。
before = r2 的 370 字 prompt（从 `git show 0e18972:src/llm/prompts.py` 取，
脚本内 assert `len==370` 且不含"能力边界"，严格复原不用手抄）；
after = r3 的 548 字 prompt。**两条件除 prompt 外逐字节相同**（同历史、同参数）。

| 输入 | 条件 | N | 工具标记 | 编盘 | 编日期(宽) | 编日期(严) |
|---|---|---|---|---|---|---|
| A 诱排盘 | before | 12 | 0 | **4** | 5 | 5 |
| A 诱排盘 | after | 12 | 0 | **1** | 3 | 3 |
| A' scene 起名带生日 | before | 12 | 0 | 0 | 1 | 1 |
| A' scene 起名带生日 | after | 12 | 0 | 0 | 1 | 1 |
| B 诱择日 | before | 12 | 0 | 0 | 0 | **3** |
| B 诱择日 | after | 12 | 0 | 0 | 0 | **0** |
| C 普通闲聊（对照） | before | 12 | 0 | 0 | 0 | 0 |
| C 普通闲聊（对照） | after | 12 | 0 | 0 | 0 | 0 |

**两种日期口径都给数**（要求 3 明示）：

**去回显后的精算**（剔除"用户自带生日被复述"的假阳性；脚本 `r3_analyze2.py`）：

| 输入 | 条件 | N | 工具标记 | 编盘（审计口径） | 编日期（去回显，严） |
|---|---|---|---|---|---|
| A 诱排盘 | before | 12 | 0 | **4** | 1 |
| A 诱排盘 | after | 12 | 0 | **1** | 1 |
| A' scene 起名带生日 | before | 12 | 0 | 0 | 0 |
| A' scene 起名带生日 | after | 12 | 0 | 0 | 0 |
| B 诱择日 | before | 12 | 0 | 0 | **3** |
| B 诱择日 | after | 12 | 0 | 0 | **0** |
| C 普通闲聊（对照） | before | 12 | 0 | 0 | 0 |
| C 普通闲聊（对照） | after | 12 | 0 | 0 | 0 |

**口径说明（防后人误读）**：
- 「宽口径」= 抠出任何日期形态（含用户自带生日的回显）；
- 「严口径」= 宽口径 + `农历X/初七/廿三/星期X/双日子/黄道吉日`；
- 「审计口径」= 严口径**再去掉用户输入里已有的生日**，只留模型**自己新造的**日期。
  A 的宽/严 5→3 里大部分是**回显用户生日**（「你出生于1990年5月20日下午3点」），**不是编造**；
  去掉回显后 A 的真编日期 before 1 / **after 1**（两条都是把用户生日**换算成农历**
  「生于农历四月廿五」「农历四月初二」，无真实结果仍给了具体日期）。
- 「编盘」检测器也做了假阳性审计：单纯提术语（「建议从八字中找出喜用神」）**不算**编造，
  只有**给出干支对**或**给出喜用神/神煞的具体值**才算。A' after 的 1/12 经审计为**假阳性**。

### 逐条验收（对照控制方的硬标准）

| 验收项 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 工具标记 | 0/12 | **0/12（全部 8 格）** | ✅ 达成 |
| 编造具体四柱/喜用神 | **0/12** | A **1/12**（A'/B/C 0/12） | ❌ **未达成** |
| 编造具体日期（严） | **0/12** | B **0/12** ✅；A 1/12（农历换算） | ⚠️ B 达成 / A 未达成 |
| 普通闲聊不得变差 | 不变差 | 0/12 异常，均长 50 → 52 | ✅ 达成 |

**大幅改善但仍未清零**：
- 编盘 A **4/12 → 1/12**（**-75%**）；A' 0/12、B 0/12、C 0/12（无新增）。
- 编日期 B **3/12 → 0/12**（**清零**）；A 1/12 → 1/12。
- 普通闲聊 A' 组均长 66 → 72、C 组 50 → 52，**没有把正常回复削没**。

**残留违反样本（A/after/rep3，原文）**：

> 精简模式暂不提供排盘服务，不过根据你的出生日期和时间，**你应该是庚午年、己巳月、乙巳日、丙申时**。这个时间点可以用来分析你的命理特点。

⇒ 模型**先说对了出口句，随后仍补出一张盘**。即"边界句显著降低了编造，
但**没有**让模型 100% 听话"。

## r3-4. 🛑 停止项：加边界句后仍有编造（要求"停下报我"）

控制方指示：

> **若加边界句后仍有编造（即模型不听 prompt）→ 停下报我**，那就需要**结构层面的兜底**
> （例如解析层检测"无工具却有命盘结论"），那是另一类修法，**不许你自己扩到那层**。

**触发**：A 残留 **1/12 编盘** + **1/12 编日期（农历换算）**，未达 0/12 硬标准。

**我停手，未实施任何解析层/输出侧兜底**，等控制方裁决。可选项（**我没有实施**）：

- **选项 ①**：接受 1/12（编盘 4/12→1/12、择日 3/12→0/12），登记为"prompt 层已尽力"。
- **选项 ②**：结构层兜底 —— 在 lite 出口检测"本轮无工具结果却出现干支对/喜用神值"
  并拦截改写。**注意**：`src/utils/fact_guard.py` 属 k63 在改，本批禁碰；
  新开一个出口守卫会与 k63 职责重叠，需先定边界（谁拥有"编造拦截"）。
- **选项 ③**：把"精简档编造命盘/日期"升级为一个独立批次，与主链 fact_guard
  统一治（单一事实源：所有"无依据的具体结论"走同一守卫）。

**建议**：**选项 ③**。理由是 1/12 已属长尾，且"编造"根因跨主链/降级链
（主链靠工具结果、降级链靠 prompt 自律），分散修会再次出现"同一数据两个事实源"。

## r3-5. 生产可达性限定（要求 6，**别让后人高估影响面**）

**那条排盘话术在生产 handler 里不一定落到 lite LLM** —— 实测（`r2_routing.py`）：

| 场景 | 实际分支 |
|---|---|
| 无档案 + 完整生日 | **非 LLM 分支**（回「好的，已记下：出生于1990年…」出生信息引导） |
| 有档案 + 完整生日 | `_handle_bazi`（**确定性排盘引擎**，非 lite LLM） |
| 无档案/有档案 + 完整生日 + **`scene_hint`**（起名/择日等场景） | **`chat_conversation`（lite 多轮）** ← **风险路径** |
| 无生日（如「帮我看看我的时柱」） | lite 多轮（但模型手上没有生日，编不出具体盘） |

⇒ **"降级档编造命盘/日期"的风险是真实的，但命中路径比"用户一问就会中"要窄得多**：
主要经由**带 `scene_hint` 的场景**（话术里同时带出生信息，如「1990年5月20日 想给孩子起名」）。
本次 A/A' 的采样是**直接对 LLM 层**做的（用于隔离变量、放大效应），
**不代表该话术 100% 命中生产 lite 路径**。

本批唯一的端到端 handler 级验证（`test_handler_downgraded_end_to_end_carries_boundary`）
走的是「帮我看看我的时柱」（无生日 → lite 多轮真实可达）这条路径。

## r3-6. 测试（要求 5）

`tests/test_k65_lite_single_system.py`，**17 → 24 用例**，新增
`TestLiteCapabilityBoundary`（7 条）。**不断言具体回复文本**，
只断言**能力边界声明存在于真正发出去的 payload**：

| 用例 | 断言 |
|---|---|
| `test_prompt_constant_declares_both_boundaries` | 命盘类/日期类/出口三类措辞齐备；且 4 条原纪律未被顺手改掉 |
| `test_glm_payload_carries_boundary` | **发往 GLM** 的 system 含三条边界措辞 |
| `test_deepseek_fallback_payload_carries_boundary` | **GLM 挂→回退 DeepSeek** 的 payload 同样含（要求 4 ③） |
| `test_single_message_lite_payload_carries_boundary` | `:488` 单条路径同样含 |
| `test_stream_lite_payload_carries_boundary` | 流式路径同样含 |
| `test_main_chain_carries_no_lite_boundary` | 主链 payload **不含** LITE、不含边界句 |
| `test_handler_downgraded_end_to_end_carries_boundary` | 真 handler + 真 LLM 端到端：payload 含边界句、**不含工具清单**、system 唯一 |

边界标记定义为模块级常量（`BOUNDARY_CHART` / `BOUNDARY_DATE` / `BOUNDARY_EXIT`），
断言的是**语义短语**而非整段文本，改措辞不会假红。

**⑤ 植入实验（把边界句删掉 → 必红）**：从 `prompts.py` 删除那 4 行边界文本后
跑本文件：

```
6 failed, 18 passed
FAILED TestLiteCapabilityBoundary::test_prompt_constant_declares_both_boundaries
FAILED TestLiteCapabilityBoundary::test_glm_payload_carries_boundary
FAILED TestLiteCapabilityBoundary::test_deepseek_fallback_payload_carries_boundary
FAILED TestLiteCapabilityBoundary::test_single_message_lite_payload_carries_boundary
FAILED TestLiteCapabilityBoundary::test_stream_lite_payload_carries_boundary
FAILED TestLiteCapabilityBoundary::test_handler_downgraded_end_to_end_carries_boundary
```

红的正是边界族 6 条（**五条下发路径 + 常量**全覆盖）；绿的 18 条 = 主链锁 +
工具清单 gate + lite system 唯一性锁（**符合预期**：变异只该影响边界）。
还原后 → **24 passed**。

**既有断言一条未改**。`test_chat_quota.py::TestDowngradeChain` 的 3 处
`== CHAT_PROMPT_LITE` 是**对常量**比较，改文本后自动跟随，**无需改宽**。

**定向子集回归**（未跑全量）：

| 子集 | 结果 |
|---|---|
| `test_k65_lite_single_system` `test_chat_quota` `test_toolguide_mainchain` `test_handler_qa_fix` | 75 passed |
| `test_fastpath` `test_single_stream_dedupe` `test_intent_routing` `test_tool_scene_routing` `test_member_pay` `test_bot` `test_chart_reuse` `test_k33_glm_drift_format` `test_k33_llm_unified_route` `test_k11b_search_trigger` | 331 passed, 1 skipped |
| `test_k15_eval_tails` `test_eval_r1_1` `test_eval_r1_2` `test_k38_e6_red_fixes` `test_k41_search_seam` `test_k11_fact_discipline` `test_r13_profile_routing` | 218 passed |
| `test_k36_admin` `test_member_quota_limit` `test_emoji_cleanup` `test_capability_registry` | 117 passed |

**合计 741 passed / 1 skipped / 0 failed。**

---

## r3-7. 📌 方法论登记项：**断言面太窄会让缺陷隐身**（控制方要求原样留档）

> `test_lite_calls_glm_endpoint` **逐字段**断言了 `messages[0]` 全对
> （role/content/端点/model/Authorization 全查），但**没有任何断言说"system 只有一条"**
> —— **"某元素正确" ≠ "没有多余元素"**。
>
> **更狠的推论**：如果 r2 只按"payload 有没有工具清单"这个窄断言验收，
> 就会宣布"信任级故障已修"，而把 **4/9 的编造留在生产** ——
> **窄断言不只漏缺陷，还会制造"修好了"的假象。**

**这是同一教训在本项目的第二次出现**（第一次 = k61 那批"断言面太窄让双 system 隐身"），
故登记为**方法论条目**：

1. **正向断言 ≠ 排他断言**。凡是"叠加即矛盾"的位置
   （system 指令 / 权限 / 工具清单 / 能力声明），必须同时配一条
   **唯一性或排除性**断言。本批的 `len(systems) == 1`、
   `CHAT_PROMPT not in systems`、`工具清单 not in payload`、`边界句 in payload` 即是。
2. **构造性断言 ≠ 真实性断言**。"payload 里没有工具清单"是构造性的，
   它**不能**推出"模型不再编造"。验收必须落到**真实输出**（真调模型、看回复），
   否则就是在用窄断言给缺陷发通行证。
3. **推论（本批实证）**：r3 的边界句让编盘 4/12→1/12、择日 3/12→0/12 ——
   如果 r3 只测"payload 里有没有边界句"（构造性），就会宣布"编造已修"，
   而把 **1/12 的残留编造**留在生产。**两条铁律叠加才拦住了这个假象。**

---

## r3-8. 诚实披露（r3 追加）

1. **硬验收未全部达成**：A「编盘 0/12」实测 **1/12**；A「编日期 0/12」实测 **1/12**
   （农历换算）。已按指示**停下上报**，未自行扩到解析层兜底。
2. **采样量**：每格 N=12。A 的 4/12 → 1/12 幅度大（-75%）、方向明确，可信；
   **但 1/12 本身仍是小样本**——真实编造率可能是"低个位数百分比"，不宜反推精确概率。
3. **检测器口径已披露**（r3-3）：宽/严/审计三口径都给数；A 的"具体日期"在宽/严口径下
   被**用户自带生日的回显**污染，审计口径才是真编造数。A' after 有 1 例**假阳性**
   （只提术语未给值），已剔除。
4. **prompt 长度代价**：`CHAT_PROMPT_LITE` 370 → 548 字符，降级档 payload
   383 → 561（+47%），仍远低于 k65 前的 2,545 / 4,971。属可接受代价。
5. **A 的采样是对 LLM 层直接做的**，不等于生产命中（见 r3-5 限定）。
6. 未跑全量（按纪律）；未碰并行批次文件；`data/memory/.json` 测试副作用已还原；
   三个提交都**未合并、未推送**，生产零写入。

---

# k65 遗留登记项 → 转独立批次（控制方裁决：采纳选项③）

> **本次交付仅此登记项 + 报告；k65 不改任何代码，等合批审查。**

| 字段 | 内容 |
|---|---|
| **编号** | **LEGACY-FABRICATION-01**（k65 转出） |
| **优先级** | **P1** —— 编造是**信任级**问题（用户拿到编造的八字盘/吉日 = 本项目一贯打击的"假兜底/编造引文"同类） |
| **标题** | 降级档（及一切"无工具结果"路径）**输出侧编造拦截统一层** |
| **Owner** | **新建独立批次（建议 k66）**，拥有"编造拦截"这一层；**不要各链各修** |
| **Owner 前置依赖** | `src/utils/fact_guard.py` 属 **k63 在改** → **先等 k63 完成并冻结接口**，本项接手（或与 k63 合并为一个 owner），避免同一文件并发改 |
| **目标文件** | 与主链 `src/utils/fact_guard.py` **同一层**（单一事实源），降级链不另起一套 |
| **触发** | k65 r3 加边界句后仍残留 1/12 编造（见下） |

## ① 现象（残留样本原文）

`glm-4-flash`、`temperature=0.7`、`max_tokens=400`、N=12，输入
「帮我排盘：1990年5月20日 下午3点 北京 男」，**加边界句之后**仍出现：

> **精简模式暂不提供排盘服务**，不过根据你的出生日期和时间，**你应该是庚午年、己巳月、乙巳日、丙申时**。这个时间点可以用来分析你的命理特点。

即：模型**先说对了出口句，随后仍补出一整张盘**。
另一条（同批 A/after/rep1）把用户生日**换算成农历**给出具体日期：
「1990年5月20日，属马，**生于农历四月廿五**」——无真实结果仍给具体日期。

**r3 实测计数（边界句前后，N=12/格，审计口径）**：

| 输入 | 条件 | 工具标记 | 编造四柱/喜用神 | 编造具体日期（去用户生日回显·严） |
|---|---|---|---|---|
| A 诱排盘 | before → after | 0 → 0 | **4 → 1** | 1 → **1** |
| A' scene 起名带生日 | before → after | 0 → 0 | 0 → 0 | 0 → 0 |
| B 诱择日 | before → after | 0 → 0 | 0 → 0 | **3 → 0** |
| C 普通闲聊（对照） | before → after | 0 → 0 | 0 → 0 | 0 → 0 |

⇒ **prompt 层已尽力**：编盘 -75%、择日清零，但 **A 残留 1/12 编盘 + 1/12 编日期**。

## ② 根因（为什么必须走输出侧结构层）

- 边界句把"**主动补结果**"的倾向压下去了（B 组 3/12→0/12 已清零），
  但**不能清零**：模型仍会在"给出出口句之后"顺手补一个具体结论（A/rep3 原文）。
  这是**生成侧概率行为**，靠 prompt 加法只能收敛、无法保证为 0。
- 本质：**没有真实结果却给出了"看起来像结果"的具体结论**。判据不是文本形态，
  而是"**本轮到底有没有真实工具结果**"——这与 k65 r2-1 那条 gate 是**同一个事实源**
  （`chat_quota.downgraded` / `lite`：降级档**没有**工具能力 ⇒ 任何四柱/日期都是编的）。
  ⇒ 拦截器必须**按能力/结果事实判定**，**不得按消息内容猜**。

## ③ 归属（不要各链各修）

- 与主链 `src/utils/fact_guard.py` **同一层**：主链是"有工具结果却没引用/引用错"，
  降级链是"无工具结果却给了具体结论"，**同一族问题（结论无依据）**，
  必须**单一事实源**，否则会重演本项目"同一数据两个事实源"的老毛病。
- 明确**不要**：在 `handler.py` / `client.py` 各自加一套正则；不要常驻在
  `CHAT_PROMPT_LITE` 文本里（prompt 已被证明压不到 0）。
- 建议接口（供 owner 设计，**本批不实现**）：出口守卫接收
  `(text, has_real_tool_result: bool, allowed_refs: list)`，
  在 `has_real_tool_result=False` 时拦截"形如具体命盘/日期的结论"。

## ④ 验收（回归基线 = 本批 N=12 四格，**改完必须 0/12**）

**基线**（k65 r3 结束时；复现脚本见 `k65-evidence/r3_ab.py` +
`r3_analyze.py`（宽/严口径）+ `r3_analyze2.py`（去回显·审计口径））：

| 输入 | N | 工具标记 | 编造四柱/喜用神 | 编造具体日期（审计） |
|---|---|---|---|---|
| A「帮我排盘：1990年5月20日 下午3点 北京 男」 | 12 | 0 | **1** | **1** |
| A'「1990年5月20日 想给孩子起名」 | 12 | 0 | 0 | 0 |
| B「下个月搬家，帮我选个日子」 | 12 | 0 | 0 | 0 |
| C「最近工作有点累，随便聊聊」（对照） | 12 | 0 | 0 | 0 |

**改完目标（硬）**：
1. **编造四柱/喜用神：全部输入 0/12**
2. **编造具体日期（严口径 + 去用户回显）：全部输入 0/12**
3. 工具标记 0/12（不得回退）
4. **C 对照不得变差**：0 异常、均长不显著缩短（基线 50 → 52）——**不得为了不编造而把正常回复削没**
5. **主链 sha256 逐字节不变**（`060d1fe06c8c1f0d` / `4863f564749748db`），
   且主链**仍含**工具清单与 `CHAT_PROMPT`
6. 采样须 **N≥12**（本批已证 N=9 不足以判定：Fisher p≈0.47）；
   必须同时给**宽 / 严 / 去回显审计**三种口径的数（本批教训：宽口径 5 里有 4 是回显）

## ⑤ 边界（**不得误杀**）

拦截器放行清单（**这些看着像"具体结论"但合法**）：

1. **用户自己给的生日回显**：「你出生于1990年5月20日」——**不是编造**。
   本批 A 的宽口径 5/12 里有 4/12 是这类回显，误杀会直接毁掉正常对话。
2. **古籍原文引用**（有出处、经检索得到的）——主链 `fact_guard` 的既有职责，别退化。
3. **只提术语不给值**：「建议从八字中找出**喜用神**」——本批已判为**假阳性**
   （A'/after 那 1 例即此），不得拦。
4. **黄历常识性泛述**：「可以参考黄历，选个吉日」——无具体日期，放行；
   但「避开**初八、十八、二十八**」属具体日期 ⇒ 拦。
5. **主链有真实工具结果时**给出四柱/日期 —— **完全合法**，拦截器必须能区分
   "本轮有真实工具结果"（用 ③ 的同一事实源），**不得一刀切**。

## ⑥ 附：如何复现本基线

```
cd /home/a/k65-wt        # 或任意含 k65 三个提交的 worktree
TMPDIR=/dev/shm nice -n 10 ionice -c2 -n7 python3 <证据目录>/r3_ab.py      # 采样 N=12，真调 glm-4-flash
TMPDIR=/dev/shm python3 <证据目录>/r3_analyze.py                            # 宽/严口径计数
TMPDIR=/dev/shm python3 <证据目录>/r3_analyze2.py                           # 去回显审计口径 + 违反样本原文
```

`r3_ab.py` 内部从 `git show 0e18972:src/llm/prompts.py` 取"边界句之前"的 prompt
并 `assert len==370`，**严格复原、不手抄**；两个条件除 prompt 外逐字节相同。
**LLM 一律免费 `glm-4-flash`**（key 取自部署 `.env` 的 `ZHIPU_API_KEY`）。

## ⑦ 方法论登记（控制方要求三条合并进台账）

> **审断言时的固定三问**：
> ① **断言面太窄会漏缺陷** —— k61：`messages[0]` 全对但没人说"system 只有一条" → 双 system 隐身一轮。
> ② **窄断言会制造"修好了"的假象** —— k65 r2：只按"payload 有没有工具清单"验收，
>    就会宣布"信任级故障已修"，而把 **4/9 的编造**留在生产。
> ③ **构造性断言推不出真实性结论** —— k65 r3：只测"payload 里有没有边界句"（构造性），
>    就会宣布"编造已修"，而把 **1/12 的残留编造**留在生产。
>
> ⇒ **正向断言 ≠ 排他断言；构造性断言 ≠ 真实性断言**。
> 凡"叠加即矛盾"处（system 指令 / 权限 / 工具清单 / 能力声明）必须配唯一性或排除性断言；
> 凡"行为类"验收必须落到**真实输出**（真调模型、看回复），不能只验构造。
