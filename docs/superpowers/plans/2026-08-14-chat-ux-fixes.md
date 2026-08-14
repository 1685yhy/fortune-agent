# 对话体验修复 · 排盘档案/等待时长/思考展示/气泡渲染/滚动 实施计划

> **决策记录(2026-08-14):** 用户反馈 4 个对话体验问题(排盘读不到档案+等待长+失败重试 / 思考步骤全展开 / 气泡不干净:emoji多、缺字、表格成代码块、长链接穿泡、图片不显示 / 上滑被拽回底部)。两路调查完成,根因全部定位(见各 Task)。**用户拍板:先修对话体验,择吉日(feature/zeri 分支)暂停,本计划在 feature/chat-ux 分支(基于 main)执行,择吉日完成后再合并。** 对话是北极星,本批修复影响每个用户每个会话。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** ①排盘时完整读取用户档案(生日/时间/性别/城市,persons 表与 users.bazi_info 打通,说一次就记住)②缩短等待+消除"失败一次再重试"感知(阶段计时日志/意图+秒回并行/看门狗与回退优化)③思考步骤改为"当前到哪步显示哪步,完成自动收起"④气泡内容干净(emoji 收敛/拼接丢头修复/围栏表格识别/图片直接显示/长链接断行)⑤上滑后不再被自动拽回底部。

**Architecture:** 后端修复集中在 `src/bot/handler.py`(档案读取/阶段计时/思考步节奏)、`src/llm/prompts.py`(emoji 指令)、`src/api/chat_stream.py`(流式对齐)、`src/memory/user_memory.py`(画像注入);前端修复集中在 `miniprogram/utils/streamHost.js`、`pages/chat/chat.{js,wxml,wxss}`、`utils/md.js`——**已实测三版 chat 页三文件 md5 完全一致,改一份三版同生效;新增样式变量需同步三版 app.wxss**。测试沿用 scripts/test_*.py(离线)。

**Tech Stack:** FastAPI + sqlite(person_dao/dao)+ deepseek Anthropic 端点(复用 deepseek_anthropic_completion)+ 小程序原生(三版同源)+ scripts 测试。

## Global Constraints

- 所有后端代码遵循现有模式:DAO 在 `src/storage/*_dao.py`,API 在 `src/api/*.py`,引擎在 `src/engines/*.py`,LLM 用 `deepseek_anthropic_completion`
- 测试离线可跑(LLM/网络 mock);`.venv/bin/python3 scripts/test_xxx.py` 或 pytest
- **分支纪律:本计划在 feature/chat-ux(基于 main,commit 5f1fad1);不得触碰 feature/zeri 上的文件改动意图——handler.py 被两个分支都改,冲突留到合并时解决,chat-ux 侧只改本计划列出的区域**
- 三版同步:miniprogram(墨韵)/simple/fusion 逻辑文件 byte-identical;wxss 若新增变量须三版 app.wxss 同步
- 隐私红线不变:不新增用户数据暴露;档案读取仅服务端内部使用
- 不改变产品行为红线:对话工具调用协议(`<tool_call>` 标签)不变;SSE 事件协议不变(仅前端展示层与节奏调整);不减少现有能力(秒回/润色/工具循环保留,只优化耗时与拼接)
- 排盘准确性不得因档案打通而降级:persons 档案字段与 bazi_info 字段映射必须明确;缺字段仍走询问路径

---

### Task 1: 排盘档案打通(后端)

**Files:**
- Modify: `src/bot/handler.py`(`_handle_bazi` ~L2506-2525、`_tool_bazi` ~L1075-1087、画像/关键事实注入区)
- Modify: `src/memory/user_memory.py`(`get_profile_summary` ~L837-840,补原始出生字段)
- Create: `scripts/test_chat_profile.py`

**Interfaces:**
- 新增 `_get_user_birth_profile(user_id) -> dict|None`:① `dao.get_user_bazi(user_id)` 有非空 bazi_info 且有 year 键 → 直接返回;② 否则查 persons 表取主档案(`person_dao`,选最近更新的有出生数据的档案),把 `birth_year/birth_month/birth_day/birth_hour/birth_minute/gender/city` 映射为 `{year,month,day,hour,minute,city,gender}`;③ 都没有 → None
- `_handle_bazi`:档案复用路径改用 helper;所有字段改 `.get()` 带默认值(修 KeyError 隐患,handler.py:2518-2520 现为下标直取)
- `_tool_bazi`:`_extract_bazi_info(params)` 失败(needs_info)前,先试 `_get_user_birth_profile` 填参数;仍缺关键字段(年/月/日)才 needs_info 询问
- 画像注入:user_memory `get_profile_summary` 在"八字已排盘"基础上,若有原始出生字段补一行「出生:1990年8月20日 辰时 北京 男(来自用户档案)」;handler 关键事实注入(~L1786-1788)同步补,让 LLM 工具路径能直接填参
- 写回:对话排盘成功后现有 `save_user_bazi` 已覆盖 users.bazi_info;persons 建档时不回写 bazi_info 的现状保持(单向打通,不多动)

- [ ] **Step 1: 写失败测试** `scripts/test_chat_profile.py`(mock dao/person_dao):
  - bazi_info 空 + persons 有档案 → helper 返回映射后的字段
  - bazi_info 有数据 → 优先返回 bazi_info
  - 档案缺键(如无 minute)→ 不崩,.get 默认
  - `_tool_bazi`:params 缺生辰 + 档案有 → 直接排盘不询问;档案也无 → 仍 needs_info
  - `_handle_bazi`:档案复用路径不再 KeyError
- [ ] **Step 2: 实现**,全绿;回归 `scripts/test_zeri_tool.py`(handler 改动区不相交,确认不破)
- [ ] **Step 3: 真实冒烟**:服务重启后对话"排盘"验证读到档案(留档日志)

### Task 2: 等待时长优化(后端+前端看门狗)

**Files:**
- Modify: `src/bot/handler.py`(阶段计时日志;意图分析+秒回安抚并行化)
- Modify: `miniprogram/utils/streamHost.js`(看门狗 60s→90s;失败回退静默化)
- Create: `scripts/test_stream_pacing.py`

**Interfaces:**
- 阶段计时:`handler.py` 意图分析/秒回/主分析/润色/工具循环各阶段前后 `logger.info("[timing] stage=xxx duration=%.1fs")`,先量化再优化(08-12 日志已证一轮排盘最坏 170-230s:4-5 次串行 LLM 调用)
- 并行化:意图分析与秒回安抚**无依赖**,改为并行发出(asyncio.to_thread 或等效,实现者按 handler 实际同步/异步结构选择,约束:不改变消息顺序与内容语义);预计一轮省 20-30s
- 主分析/润色/工具循环保持串行(存在依赖),但各阶段 timeout 统一走现有配置;重试逻辑:空/过短回复重试保留(立即重试比退避好),不做其他改动
- 前端看门狗:`streamHost.js` `CHUNK_GAP_TIMEOUT_S` 60→90(给后端更长窗口);流式失败且无输出自动回退 `/api/chat` 时**静默切换**(不显示错误态/不打断),仅在第二次也失败时显示「网络开小差了」+ 重试钮
- 测试:计时日志存在性(日志回调捕获);并行化后意图+秒回仍都发出且顺序语义不变(mock LLM 断言调用参数与次数);看门狗常量断言;回退静默分支(模拟流失败→断言不发错误事件直接回退)

- [ ] **Step 1: 写失败测试** `scripts/test_stream_pacing.py`
- [ ] **Step 2: 实现**,全绿
- [ ] **Step 3: 实测**:真实对话一轮排盘,从日志提取各阶段 timing,记录总时长对比(修复前后证据留档)

### Task 3: 思考步骤渐进展示(前端+后端节奏)

**Files:**
- Modify: `miniprogram/utils/streamHost.js`(`_addThinkingStep` ~L375-384、`_onDone` ~L427)
- Modify: `miniprogram/pages/chat/chat.wxml`(思考区 L78-89 改单步渲染)、`chat.wxss`(当前步动画)、`chat.js`(收起状态)
- Modify: `src/bot/handler.py`(预置步骤 ~L2051-2057 改为按真实里程碑发出)
- Create: `scripts/test_stream_steps.py`(后端侧:预置步骤不再开工前一秒打光)

**Interfaces:**
- 前端:思考数组只渲染**当前 doing 步**(加完成✓动画),已完成的折叠为「已完成 N 步」计数行;`_onDone` 全部完成后自动收起(thinkCollapsed=true,默认显示一行「思考完成」可点开展开看全部)
- 后端:`INTENT_THINKING_STEPS` 预置文案不再循环打光,改为在 `_do_*` 各真实里程碑点(`_emit_stream_event` 现有调用处)按进度发出;保证至少 1 条起始步骤(用户有"开始处理"反馈),之后每完成一步推进一步
- 事件协议不变(thinking/tool 两类,payload text)

- [ ] **Step 1: 写失败测试**(后端节奏:模拟 handler 调用,断言 thinking 事件跨真实工作点分布而非同秒全发;前端逻辑测试无法离线跑则用代码评审+三版 diff 验证)
- [ ] **Step 2: 实现**,全绿
- [ ] **Step 3: 三版验证**:chat 三文件 md5 一致复核;微信 IDE 编译 + 截图 vision 复核(思考步单步展示/完成后收起)

### Task 4: 气泡内容(emoji/拼接丢头/表格/图片/链接)

**Files:**
- Modify: `src/llm/prompts.py`(L132/L214/L303 emoji 指令改为"默认不用,情绪必要时极少量")
- Modify: `src/api/chat_stream.py`(L296-307 尾部对齐策略)
- Modify: `miniprogram/utils/md.js`(围栏内表格识别 ~L196-205;新增 image 节点 ~L44-51;link 节点)
- Modify: `miniprogram/pages/chat/chat.wxml`(md-inline 渲染 image + link tap)、`chat.wxss`(.md-p/.md-link word-break,~L887/900)
- Create: `scripts/test_stream_align.py`(后端对齐)、`scripts/test_md_render.py`(md.js 表格围栏/image/link)

**Interfaces:**
- emoji:prompts 三处改为"默认不使用 emoji,仅在情绪表达极必要时使用不超过 1 个"
- 拼接丢头根因:同一轮多次 LLM 调用(草稿/润色/续写)灌同一条流,`covered` 用 `endswith(reply[:k])` 计算,尾部与 reply 头不匹配时 reply 开头被静默丢弃(用户看到"直接、")。修法:对齐改为——若 `streamed_text` 尾部与 `reply` 前缀不匹配,定位 reply 中**上一个完整句子边界**(按 。！？换行 切),从该边界起补发 `reply[边界:]`;若匹配则维持现状增量补发;covered=0 时允许整段重发(当前整段重发已存在,保留)。**约束:任何情况下不丢弃 reply 中未流出过的句子**
- 表格代码块:md.js 围栏解析时检测围栏内容是否全为表格行(`|` 开头且含分隔行)→ 按 table 渲染;同时 prompts 润色提示(~handler.py:1015 附近"保持该结构完整")不鼓励 ``` 包裹(措辞微调)
- 图片:md.js 支持 `![alt](url)` → image 节点;wxml md-inline 渲染 `<image>`(气泡内直接显示,图片样式圆角/最大宽度);link 节点加 tap 事件(长按复制 + 点击提示,不做跳转以免跳出不安全)
- 断行:`.md-p/.md-link/.md-icode` 补 `word-break: break-all`(参考 .md-code-body 现成写法);气泡容器 max-width 约束保持
- 测试:对齐(构造"流尾≠reply头"断言无丢头无重复;覆盖 covered=0 整段重发);md.js 表格围栏识别(纯表格围栏→table,混合→code);image 语法解析;三版 diff

- [ ] **Step 1: 写失败测试**
- [ ] **Step 2: 实现**,全绿
- [ ] **Step 3: 验证**:真实对话(含排盘/合盘回复)截图 vision 复核气泡干净度

### Task 5: 滚动(上滑不被拽回)

**Files:**
- Modify: `miniprogram/pages/chat/chat.wxml`(scroll-view 加 bindscroll)
- Modify: `miniprogram/pages/chat/chat.js`(scrollTop 记录;`_onHostState` ~L408 距底判断;`_scrollBottom` ~L560-575)
- Modify: `miniprogram/utils/streamHost.js`(autoScroll 传递逻辑不变,配合 chat.js 判断)

**Interfaces:**
- scroll-view `bindscroll` 记录 scrollTop;距底阈值 ~100rpx:用户距底 >100rpx → 视为上滑 → 跳过自动滚(流式期间不打扰)
- 用户回到距底 ≤100rpx → 恢复自动跟随;流结束后不强制滚(用户自行上滑查看)
- `scrollInto` 复位逻辑保证连续触发(scrollInto=''→'btm' 序列)

- [ ] **Step 1: 实现**(纯前端,代码评审+真机/IDE 验证)
- [ ] **Step 2: 验证**:IDE 编译 + 行为验证(长回复流式期间上滑→不被拽回;回底部→恢复跟随);三版 diff

### Task 6: 集成验证 + 上线准备

**Files:** 无新文件

- [ ] 全量回归:
```bash
cd /mnt/e/fortune-agent
.venv/bin/python3 scripts/test_chat_profile.py && \
.venv/bin/python3 scripts/test_stream_pacing.py && \
.venv/bin/python3 scripts/test_stream_steps.py && \
.venv/bin/python3 scripts/test_stream_align.py && \
.venv/bin/python3 scripts/test_md_render.py
```
全绿;另跑关键回归 `scripts/test_zeri_tool.py`(handler 改动不相交确认)、`scripts/test_stream.py`、`scripts/test_jian_api.py`、`scripts/test_gap_api.py`
- [ ] 真实运行:重启 8767;实测一轮排盘对话(档案读取/总耗时/思考步节奏/气泡干净度/滚动),截图 vision 复核,计时对比表(修复前后)留档
- [ ] 三版:chat 三文件 md5 一致;改动的 wxss 若含新变量 → 三版 app.wxss 已同步
- [ ] 隐私自检:档案读取仅服务端内部,无新接口暴露
- [ ] Commit 收尾(feat/fix(chat) 系列,feature/chat-ux)
