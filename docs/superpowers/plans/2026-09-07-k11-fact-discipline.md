# k11 内容事实纪律修复批（2026-09-07，分支 k11-fact-discipline，基线 main=10e0cce）

> 权威根因报告：/tmp/fortune-actioncard-gap-20260906.md（只读）。
> 用户实诉四案：建议卡喊男命"姐妹"、正文"今年33岁"（实为27）、正文与建议卡结论打架、
> 神煞观感编造（孤辰寡宿/童子煞等卡上看不到）、工具 JSON 明文泄漏进对话流。
> 本批=A-G 七项，全部到证据行；每项"方案/取舍"落在此文档，代码以注释锚定同一编号。

---

## A. 事实包注入两链

**根因（报告 §②/§五）**：两条 LLM prompt（主链 `llm/client.py:_format_chart`、
建议卡 `advisor_v2.py:_build_prompt`）都只喂"3岁戊辰→…33岁乙丑"的**大运段序列**，
没有"当前年龄/当前所处大运段"字段——LLM 各自把换运岁数当当前年龄/未来节点解读。
`BaziResult` 也没有该字段（虚岁只在 `liunian_full`/API 展开层存在）。

**方案**：
1. `BaziResult` 追加尾字段 `current_stage: Optional[dict] = None`（dataclass 尾部加默认字段，
   既有按位构造/测试零影响）。
2. `src/engines/bazi.py` 新增模块级纯函数 `current_stage_facts(...)`：**在引擎
   `calculate()` 内部、`result.dayun_rel = ...`（~941 行）之后注入点一次性计算并挂到
   `result.current_stage`**——同一函数体内有 `orig_birth(公历年/月/日/时/分)`、`city`、
   `lunar_disp`、`dayun`、`jiaoyun.years`、`liunian_rel`，口径与 `api/paipan.py:311`
   大运展开完全同源（`jiaoyun years` 优先、兜底 `出生年+sui-1`，paipan 同款）：
   - 周岁 = 当前公历年 − 出生公历年 −（今年生日未到 ? 1 : 0）；
   - 虚岁 = 当前公历年 − 出生公历年 + 1（与 `_calc_qiyun_start_age` 的 qiyun 虚岁口径同源）；
   - 当前大运段 = dayun 中最后满足 `起运虚岁 <= 当前虚岁` 的段（含 sui 起/止、交运起止年份、
     下段干支/岁数/年份）；当前流年取 `liunian_rel`。
   - **不重写**任何既有函数：引擎本来就在该循环里对 `liunian_full` 逐年算"该年所在大运"
     （bazi.py:921-937 注入点注释），本批只把同一事实的"当前年"版固化成 `current_stage`。
3. `src/engines/bazi_formatter.py` 新增 `format_fact_pack_block(result)`（纯文本，
   不 import bazi 避免循环——bazi.py 已 import bazi_formatter，故本函数只按鸭子类型读
   BaziResult 属性 + `result.current_stage`）：输出确定性中文事实包
   「当前日期/当前流年/出生档案(公历+农历)/周岁+虚岁/当前大运段(干支+虚岁区间+约年份)/
   下步大运/神煞全集(N 个)/用神」+ 硬纪律句「年龄/大运/换运年份/神煞只许引用本包，
   禁止自行推算或编造」。`current_stage` 缺失（老对象/手工 mock）→ getattr 兜底降级
   字段缺席，绝不抛错。

**注入两链**：
- 主链：`llm/client.py:_format_chart` 末尾追加事实包块（含神煞白名单条款）。
- advisor：`advisor_v2.py:_build_prompt` 命盘段追加同一事实包块（共用 `format_fact_pack_block`，
  单一事实源，禁止两链各自拼口径）。

**取舍记录**：档案"calendar=lunar"标记在 LLM prompt 层不展示（prompt 只看引擎归一后的
公历排盘口径；农历出生信息由 `result.lunar` 提供农历文本）；事实包一律由引擎算、prompt
只引用，不新开存储字段（DB 不迁移）。

## B. 性别与称谓

**根因（报告 §三）**：advisor `_build_prompt` 全程无 gender（对照主链 client.py:648 有
`性别：{r.gender}`）；persona 硬编码"毒舌闺蜜"（advisor_v2.py:84）→ 男命收到女性口吻。
G1 全套只覆盖主链/运势卡，三消费点（handler 5675/5771、7364、api/advisor.py:121）全没到。

**方案**：
1. `AdaptiveAdvisor.generate()` persona 按 `result.gender` 分支：
   `女/female → "毒舌闺蜜"`；`男/male 或 unknown/空 → "理性分析师"`（中性专业口吻），
   注释写明"产品可调"（女性可换温柔陪伴者；男性如后续要男性向口吻在此扩展）。
2. `_build_prompt` 加「性别：男/女/未知」行 + 称谓硬规则段：男/未知 → 中性称谓
   （你/朋友/这位朋友），**禁止任何女性称谓与闺蜜口吻（姐妹/闺蜜/亲爱的/姑娘/集美/
   小姐姐/宝子等）**；女 → 才可用当前模式闺蜜式称呼。
3. 输出后校验器（见 D 段落"校验器"）含称谓词检查：男/未知命回复出现女性称谓词
   → 去词 + warning 告警（纯规则零 LLM）。
4. `_gen_instant_reply`（6317+，报告 §五#3 低危同类）prompt 补性别行 + 禁女性称谓句；
   主链 bazi 回复整段出口再做一次称谓校验（见 C 校验器接线）。

**取舍**：`_handle_advisor`(7331)/`api/advisor.py:99` 的"bazi_info 直读非 persons-first"
是 G1 残留（报告 §一注释），本批不改读取链（改动需重排盘口径评测，列入后续项）——
gender 由 BaziResult.gender 携带进 prompt，三个消费点自动生效（引擎侧已有
bazi.py:646-651 归一：male/female/中文 → 男/女，其余 → unknown）。

## C. 神煞一致性

**根因（报告 §四/⑤）**：引擎算全量（1999-05-13 9:00 长春男=14 个），排盘卡
`bazi_formatter.py` 详细版 [:12]（191 行）/紧凑版 [:6]（253 行）截断，advisor prompt
喂全集（advisor_v2.py:133）→ 卡引用"看不到"的神煞观感编造；主链另有真幻觉
（行 52 "文昌贵人"不在引擎 14 内）。

**方案**：
1. **显示集合二选一 → 落点：显示端改全量（折行）**。详细卡 [:12]→全量、紧凑卡 [:6]→全量，
   两端与喂给 LLM 的集合恒等，无省略提示需要（14-18 项一行为微信可接受的折行长）。
   备选（喂前 N+注明"另有 X 项未展示"）放弃：用户在同一会话可见卡有两档（紧凑/详细），
   "可见集合"有二义，全量展示消除一切二义与"引用了看不到的神煞"类投诉。
2. **神煞全集白名单（单一事实源 = `src/engines/shensha.py:SHENSHA_LUCK` 键集，59 型全集）**：
   - 主链 `_format_chart` 与 advisor prompt 都加条款「只许引用上方事实包神煞名单中的
     神煞，禁止自造任何神煞名（含任意"XX贵人"类）」；
   - 新增纯规则校验器（src/utils/fact_guard.py）：回复中出现的**神煞词典词**
     （SHENSHA_LUCK 键，词长降序匹配）若不在本盘 allow（引擎算出列表）→ 去词 + warning。
3. 校验器接线（输出后，纯规则零 LLM）：
   - `AdaptiveAdvisor.generate()` parse 后对 actions 全部文本字段/daily_tip/style_notes/
     serendipity 做称谓+神煞 scrub（性别/allow 取自同一 result）；
   - `_do_bazi_analysis` 拼装回复前对 analysis.response/instant/followup scrub；
   - `process()` 引擎意图（bazi/career）出口（dict 脱括号之后、wrap_card 之前）对
     润色+工具循环重写后的整段再做 scrub 兜底；
   - 流式展示态：`chat_stream.py` 出口逐 chunk scrub（见 E 的同一出口，chunk 级），
     handler 每轮排盘后把事实上下文（gender + shensha allow）记 `_fact_ctx[user_id]`，
     无上下文（非命理轮）不 scrub，防误伤。

**取舍**：显示端"全量折行"会略增卡面行数（可接受，纯文本）；神煞去词可能造成半句，
属规范允许的"去词/告警"档（告警落 log），不整句改写（零 LLM）。

## D. 建议卡基线规则

**根因（报告 §二）**：卡与正文并行生成无基线 → 双 LLM 对"金融属金/属金水、印=水/
土带金"各执口径反转矛盾；advisor prompt 无"不得反转方向"条款（对照解梦 G4
dream.py:497-511 硬约束先例）。

**方案**：
1. advisor prompt 注入【引擎方向要点】（确定性派生，复用 career_dir 纯函数与表——
   行业五行映射 INDUSTRY_WUXING / 方位 ELEMENT_DIRECTION / 喜用 helpful_elements /
   忌神 forbidden_elements / 日主强弱 day_master_strength_of，src/tools/career_dir.py
   是工具卡同源表，防口径分裂）：五行分布/强弱档/喜用五行/忌神五行+依据一句/
   行业五行映射表（节选行）/方位映射。
2. 加硬约束条款（仿 DREAM_ELEMENTS「此骨架为…硬约束：不得反转吉凶方向」措辞）：
   「以下为引擎确定性方向基线：建议不得与之一致性相悖/不得反转（如喜水却荐补金土）；
   缺某五行、某行业属五行一律按表作答；基线外的生克联想（官杀=压力）可展开但不推翻
   基线结论」。
3. **取舍记录**：正文与卡仍并行（延迟理由保留在 handler.py:5665-5666 注释），故正文全文
   无法注入卡 prompt；方向要点由引擎确定性派生（同源同口径），事实包两链同注，卡侧
   单向禁止反转，正文侧按 B/C 校验器兜底——结构性串行化（延迟 +N 秒）不采纳，写入
   5665 注释本批已注入基线的说明。

## E. JSON 泄漏 chunk 级过滤

**根因（报告 §④）**：polish 带 search_hint 时被硬要求"先输出 <tool_calls> JSON 再继续"
（handler.py:1960-1965）+ 真流式（1969-1975，函数内无 strip）；`_run_tool_loop`
GLM 路径 1732 先流式、1747 后 strip；chat_stream.py:455 出口兜底追不回已发 chunk。
落库零命中 → 展示态泄漏。

**方案**：
1. 新建 `src/bot/stream_guard.py`：`ToolJsonChunkFilter`（纯规则、带跨 chunk 状态）：
   - 完整工具块（`<tool_calls>…</tool_calls>`/`<tool_call>…</tool_call>`）任意位置移除；
   - 流内出现未闭合起始标记 → 自标记处缓冲至闭合（JSON 型按 { [ 括号深度计数跨块闭合；
     TOOL: 型至换行），只放行标记前的正文；缓冲上限 4096 防悬挂；
   - 对既有工具 JSON 词条/残渣（`[{"tool"` 等）同样不落可见流。
   复用思路 = `_looks_like_tool_echo`/`strip_tool_calls` 同源目标，但**零改动
   tool_calls.py**（既有红线），独立新模块。
2. 接线三处：
   - `handler.py` polish 流式（~1963-1970 前包一层 stream_cb）；
   - `handler.py` `_run_tool_loop` GLM 二次流式（~1729-1733 前包一层）；
   - `chat_stream.py` events 出口 cb（422-427）逐 chunk 过滤 + `_to_event` 路径
     （对 polish/tool-loop 之外的自由对话流同样生效——单一总闸）+ 收尾"剩余模拟流"
     分句同样过过滤（reply 本身已 strip_tool_calls）。
3. 双层过滤幂等（第二层见不到第一层已滤内容），无顺序假设。

**取舍**：保留"先输出 JSON 工单再继续"的展示性引导文案不动（那是内部协议教学），
靠发送层过滤兜住展示态；前端 done 事件全量覆盖气泡正文 = 前端改造，列后续项。

## F. 评测补事实断言（L2 派生数值断言层）

**根因（报告 §③）**：L3 5 个 LLM 主观维无派生数值断言；唯一确定性层 = reply_checks
四键（contains/neg_checks/regex/min_len），无"从档案派生数值对比"类型。

**方案**（纯规则、零 LLM 判卷）：
1. `scripts/eval_agent/l2_eval.py` 扩展 reply_checks 可选键 `derived`（数组）：
   - `derive_facts(task)`：setup.persons[0] 出生 + 档案 city/gender → BaziEngine 本地
     排盘（确定性 0 LLM）→ 派生 {性别中文、神煞全集、周岁/虚岁、当前大运干支/岁数…}；
   - `eval_derived_checks(task, reply, facts)` 五类断言：
     a) `age_claim`：当前年龄声明式表述（今年/现在/我已经/周岁/虚岁 X岁）数字须落在
        [周岁−1, 虚岁+1] 窗内（报告口径 27±1），窗外当前年龄声明即 fail；require_mention
        参数可要求回复至少出现一个窗内年龄（T102 用）；
     b) `dayun_claim`："当前/现在走 X 运""刚进 X 运"类声明 X 必须是当前大运干支
        （引擎 facts），否则 fail；
     c) `gender_addr`：男命回复含女性称谓词（词表与生产 fact_guard 同源）→ fail；
     d) `shensha_refs`：回复中出现的引擎神煞词典词必须 ⊆ 本盘引擎神煞全集
        （文昌贵人 on 辛巳盘等白名单外引用 → fail）；
     e) `tool_json`：回复含 `<tool_calls`/`<tool_call`/`[{"tool"`/`{"tool"` → fail。
2. `scripts/eval_agent/validate_tasks.py`：reply_checks 可选 `derived` 键 schema
   （type 枚举 + params + 依赖 persons 前置检查）；任务总数门禁 100 → 103。
3. `data/eval/agent_tasks.jsonl` 追加 3 行（P1，fortune）：
   - T101 行 48 同款 golden（1999-05-13 09:00 长春男 / 国企央企 vs 金融）→
     age_claim(require=false) + dayun_claim(丙寅) + gender_addr(male) +
     shensha_refs + tool_json；
   - T102 同命主年龄/大运问答 → age_claim(require=true) + dayun_claim + tool_json；
   - T103 advisor 关键词兜底面（"给我点建议"）→ gender_addr(male) + shensha_refs +
     tool_json（覆盖消费点 2）。
4. 报告 §③ 用例 5（entity_qa_search：公司实体问答必触发 web_search + 禁甩锅）依赖
   命理意图域搜索通道 = k11b（本批未接）→ **标注 skip 待 k11b**（框架层派生钩子与
   expected_tools 拦截已具备，k11b 落搜索通道后按 T104 补行）；
   用例 6（hour_boundary：真太阳时口径）依赖产品裁决 = k11c → 待 k11c。
5. 配套更新存量断言计数测试（test_eval_l2.py/test_eval_e6.py 100→103）。

**取舍**：L2 用引擎本地复算作"档案派生数值"事实源（与主链同引擎同口径，无 LLM）；
不新增 LLM 判卷维度。

## G. 本 plan 文档 + progress.md k11 段

- 本文件即 G 落点；.superpowers/sdd/progress.md 末尾追加 k11 段（照旧格式）。

---

## 改动文件清单（提交范围）

新增：`src/utils/fact_guard.py`、`src/bot/stream_guard.py`、
`tests/test_k11_fact_discipline.py`、本 plan 文档
修改：`src/engines/bazi.py`、`src/engines/bazi_formatter.py`、`src/engines/advisor_v2.py`、
`src/llm/client.py`、`src/bot/handler.py`、`src/api/chat_stream.py`、
`scripts/eval_agent/l2_eval.py`、`scripts/eval_agent/validate_tasks.py`、
`data/eval/agent_tasks.jsonl`、`tests/test_eval_l2.py`、`tests/test_eval_e6.py`、
`.superpowers/sdd/progress.md`
不动：tool_calls.py、.env、生产库；data/memory/.json、data/eval/results/ledger.json 等
预存脏态不 add；不 push 不重启。

## 测试清单

新增 tests/test_k11_fact_discipline.py：
- 引擎 current_stage（固定 now 参数注入版断言周岁 27/虚岁 28/丙寅段/下段乙丑+年份）
- fact pack 文本含年龄 27/大运丙寅/性别男/神煞全集 N 项与白名单条款
- advisor _build_prompt：男 → 无"毒舌闺蜜/姐妹"词、有性别行、有事实包与防反转段；
  女 → 毒舌闺蜜保留
- advisor generate scrub：构造含"姐妹 文昌贵人"的 mock LLM 输出 → 输出字段已去词
- bazi_formatter：紧凑/详细卡神煞 = 引擎全量（无截断）
- fact_guard 纯函数：男命称谓去词命中、女命不 scrub、神煞白名单内保留/外去词
- ToolJsonChunkFilter：构造"前缀 JSON + 正文"流、逐字符切块 → 汇合后无任何 JSON 残留
- l2_eval derived 五断言纯函数（含 33 岁声明 fail / 丙寅通过 / 乙丑当前声明 fail /
  男命姐妹 fail / 文昌贵人白名单外 fail / JSON 泄漏 fail）
- validate_tasks derived schema 合法/非法样例 + 任务总数 103
回归 cluster：test_adaptive_advisor.py、test_handler_analysis_flow.py、
test_g1_gender_contract.py、test_k9_r2_minors.py、test_bazi_dash_format_regression.py、
test_engine_shensha.py、test_calendar_persons_read.py、test_eval_l2.py、test_eval_e6.py

## 后续项（挂账，不在本批）
- k11b：命理意图域实体 QA 必触发联网搜索（chat 域 tool-loop → career/bazi 域通道 +
  T104 评测行 + 禁甩锅句断言）
- k11c：真太阳时默认开/关口径产品拍板（10:55 巳午跨界敏感窗口提示 + hour_boundary
  评测行 + 双口径并存杜绝）
- 重试去重后端护栏（服务端幂等去重）、P4.7 图标视觉重绘
- `_handle_advisor`/api/advisor 直读点 persons-first 改造（G1 残留）+ api/advisor.py
  narrative_text NameError 疑似（未在范围，待独立批）
- advisor unknown 性别中性档的"产品可调"入口注释放产品拍板
- 秒回安抚预生成路径 prompt 同步（6317+ 已含性别行，预生成 1274-1353 线程复用
  _gen_instant_reply 的同步版 → 已覆盖）
