# k17 后端杂项收紧（2026-09-10，分支 k17-misc-tighten，BASE main=4f35076）

> 本批=多个独立小项（G1/k11/k11b/k15/k16 各批审查挂账的后端杂项），每项先核后做、
> 最小改动+独立小提交。纪律同前：git add 只加本批；不 push 不重启不碰生产库不改
> .env 不动 tool_calls.py；progress.md（.superpowers/sdd，非 git）追加 k17 段。
> 报告报数纪律（k17-7）：一律以 pytest 实际 collect/通过数为准（collect-only 口径），
> 不沿用旧报告声称数字（k16 审查 Info：报告 72/46/5 与文件实计数 36/82/4 不符）。

---

## 1. advisor/api 直读 persons-first（G1 残留收口）

**现状核对（先核后做）**：
- k11 报告（/tmp/fortune-actioncard-gap-20260906.md §一/§六-2）点名两个残余直读点：
  `_handle_advisor`（handler.py:7343 当时的行号）与 `api/advisor.py:107`——均直接
  `dao.get_user_bazi()` 只读 users.bazi_info，绕开 G1 persons→bazi_info→chart_records
  读取链与 `_solarize_birth` 转公历、k11c solar_time 开关。
- k11c（2026-09-08）已把 `_handle_advisor` 改为 `_get_user_birth_profile` +
  `_solarize_birth` + solar_time 透传（handler.py:7760-7796 注释锚定）。
- **advisor_v2（src/engines/advisor_v2.py）本身零 bazi_info 直读**：接收 BaziResult
  对象（性别/神煞/事实包全由引擎/result 携带，import 清单核对无 dao 依赖）。
- 因此本批唯一改造点 = **api/advisor.py:99 `_dao.get_user_bazi()`**（persons 建档新
  用户无 bazi_info 行 → 误报「未设置八字信息」400，T089 同款问题的 advisor REST 面；
  且 lunar 档案当公历排、无 solar_time 透传——三处口径缺口同源一次收口）。
- 改造后读取链与 handler._handle_advisor 同源语义：get_user_birth_profile(persons 默认
  档案 → bazi_info → chart_records 兜底) + lunar 单点 to_solar_date（birth_profile.py
  单一实现，不另起炉灶）+ solar_time 0/1 透传引擎 + hour/minute/city/gender 缺省
  归一（k9 同款口径：minute 缺省 0、gender unknown）。
- 读取链本身（persons 优先/自愈回写）已由既有 birth_profile 测试覆盖，本批端点测试
  锚「消费契约」：persons-only 档案不再 400、lunar 转公历后入引擎、solar_time=0 关、
  hour/minute None → 0。
- 邻接审计（**哪些点仍直读 bazi_info**，对照报告 §六 修复建议-2 的同类面）：全部盘点
  见下「残余直读点清单」——不在本批范围（各属不同产品面，本批只收 advisor REST）：
  | 位置 | 读法 | 状态 |
  |---|---|---|
  | api/advisor.py:99 | get_user_bazi 直读 | **本批改** |
  | handler._handle_advisor 7760 | 已改 persons-first | k11c 完成 ✓ |
  | main.py 2018/2055（/api/calendar/daily、/week 旧实现） | get_user_bazi 直读 | 残余（birth 键可被 persons 自愈回写覆盖，但读取不走统一链）——记录待后续批 |
  | main.py 2350（/api/share-card）、api/dashboard.py 27/92 | get_user_bazi 直读 bazi[] 四柱/日主 | 残余（chart 数据源应为 chart_records 权威，k8 语义）——记录待后续批 |
  | api/hourly.py 102 | get_user_bazi 直读 bazi[2] 日主 | 同上，记录待后续批 |
  | api/calendar.py 158 / main.py 1397/1421 / api/user.py 617 | get_user_birth_profile | 已同源 ✓ |
- 报告 §五 同类面盘点（1-8 行）中 #1 三消费点（advisor_v2 + handler 5675/5767/7364 +
  api/advisor）性别/基线问题 k11 已解（BaziResult.gender + 事实包 + 防反转条款），本批
  收 #1 的读取链残余；#3 秒回、#5 解梦、#6-8 各族为各自生成器语义，不在本批。

## 2. confidant/秒回域搜索接入评估（k11b §七范围外 → 本批结论）

**结论：不接（纯评估记录，零代码改动）**，核现状证据：
- confidant（倾诉通道）= `analysis.is_sharing`（message_analyzer：个人故事+情绪深度）
  且消息无出生信息时早退分支（handler.py:4266-4276），走 CONFIDANT_PROMPT 纯 LLM
  共情倾听，2-3 轮后提议转命理；接 decide_search 的语义冲突：① 倾诉=情感陪伴，
  检索事实无必要且破坏共情调性；② 倾诉消息含出生信息的形态被 has_birth_info 门控
  排除（不走 confidant）；③ 外部实体 QA（易宝支付靠不靠谱）LLM 判 is_sharing 概率低
  （问句非故事）——若真发生「倾诉句 + 实体强问」并存，实体是叙事背景不是事实主体。
- 秒回安抚 = _gen_instant_reply/pregen（handler 1310-1364/6732）：触发前提=消息含完整
  出生信息且 intent 为排盘 → 纯本地计算面：decide_search 硬锚层（出生信息+排盘意图）
  恒否决；同轮主链已有 _engine_domain_ground_search（k11b，_will_polish 块）承接真实
  实体/时效问——秒回再判=双判且可能在安抚流上重复检索，纯开销。
- 边界观察（若未来实证再评估）：confidant 2-3 轮后提议转命理问、record_query 直读库
  秒回（r13）面如出现「问我档案里的外部实体事实」类问题，需语义路由级方案
  （semantic-router 备选，k11b plan §七已记录在案），非本批关键词级接入。

## 3. calc 双扫装饰性开销（search_trigger 模块内单扫）

**定位**：decide_search 里 `calc = bool(LOCAL_FORTUNE_ANCHOR_RE.search(msg))` 后跟
`local = calc or has_local_fortune_anchor(msg)`，而 has_local_fortune_anchor 第一句
又对同一正则整扫一遍（search_trigger.py:322）。实证计数：硬锚命中时因短路只扫 1 次；
**软路径（口语决策族/主题族，如「跳槽什么时候合适」）decide_search 内该正则扫 2 遍**
（探针计数=2）——装饰性重复。
**修法（行为不变）**：抽私有 `_local_fortune_verdict(text) -> (calc, local)` 两档判定
单扫；has_local_fortune_anchor = verdict()[1]（公开语义/docstring 不动）；decide_search
改 `calc, local = _local_fortune_verdict(msg)`。k11b-r1 两档边界注释（实体层按 calc 硬
锚判 weak/timely/llm）语义原样保留。

## 4. 「这个行业怎么样」白名单语义记录（k11b-r1 P2-B 残留 → 注释/plan 记录）

**现状实证**：decide_search("这个行业怎么样") → should_search=True reason=whitelist
query=「这个行业」（整句精简）。路径：行业 于 k11b-r1 P2-B 移出 _DEICTIC_NAME_RE
（「最近有什么行业新闻」类合法时效问须放行）→ 本句无实体、无本地锚、无时效词 →
白名单兜底层（RESEARCH_WHITELIST_RE 含 行业/公司——旧 handler._WEB_SEARCH_RESEARCH_RE
词条原文迁移）触发。
**语义结论（记录在案，非回归）**：本句经白名单触发 = 与旧关键词硬门控语义等价
（k11b-r1 残留 P3 原文确认），query 为泛化词（检索价值有限，但产物=搜索引擎+来源
尾注，绝不甩锅——PM 实诉教训方向的守成面）。
**后续方向建议（宁搜勿漏为本位）**：收紧须先有实体/主题解析能力（能判「这个行业」
指代的可检索主体）或语义路由（semantic-router 备选）覆盖证据，否则宁放行；候选收紧
路径记录 = 白名单层加「有检索价值主体」启发（纯指代句 + 无时效词 + LLM 信号缺席才
考虑拦），留待实体解析增强批评估。落点 = search_trigger.py 白名单层注释锚 k17-4。

## 5. jiaoyun 部分段缺 time 防御（k16 审查 Minor-2）

**盲区实证（引擎恒全量，仅手工/旧构造/时刻串损坏可达）**：
- 中段缺 time：golden dayun 12 段/年表 9 条，删 sui=53 条目 → now=2051-06-01
  （虚岁 53 已满）仍停 idx4 甲子（过期展示）；现码只在 now ≥ 表末时刻后走虚岁兜底，
  表内缺口不查（探针复现）。
- 首段缺 time（_timed[0][1]!=0）：起运前守卫失效，now 早于首条已知时刻 → 静默落
  k11 虚岁兜底（复现旧错报形态，无告警）。
**最小修法（current_stage_facts，k16 全量语义零变化）**：
- 首段缺失场景记录年表出现过但 time 无效的 sui 集合（jy_seen）；
- 精确段选后统一向后走兜底循环（替代原「仅表末后」条件）：自 idx+1 起，遇有效交运
  时刻段即停（其时刻 > now，否则已在 _past 内）；无有效时刻的段（年表缺口段或表外
  段）在 虚岁已满（sui ≤ 虚岁，k11 兜底口径）后过渡计入；缺口段（sui ∈ jy_seen 但无
  效 time）过渡记 logger.warning 一次，表外段维持 k16 静默近似语义；
- 首段缺 time 且 now 早于首条已知时刻 → 整体虚岁兜底（k11 原行为）+ warning 一次。
- 全量数据路径逐分支等价（含 k16 锁定的 2081/2095 表末表外、起运前、无 time 兜底），
  零告警零行为变化——回归由 test_k16_calendar_dst 全绿背书。

## 6. age_claim 岁字省略形态（k15 审查 Minor-2 记录）

**现状实证（探针，窗口 26-29 / 档案 27 周岁 28 虚岁）**：「虚岁33了」「本人虚岁33。」
「今年我虚岁33。」「我现在虚岁33啦」全部漏拦（pass）——`_AGE_CLAIM_RES` 各 pattern
均要求「岁」字或数字在单位前。换运端点健康句（虚岁23到32岁走丙寅/从虚岁33起走乙丑/
乙丑大运虚岁33交运/虚岁33时换入乙丑大运/虚岁33开始走乙丑运/33岁进入乙丑大运）基线
全 pass（不误伤）。
**补拦（两个 pattern，语义镜像 pattern2 排除面）**：
- 完成体收尾型：`(?<![\d岁到从走换进交止起至后])(?:周岁|虚岁)(\d{1,2})
  (?![\d到至起走换进交止后时～~\-–—－])(?:了|啦|哈|吧|嘛)`——无「岁」完成体断言
  只可能是当前年龄声明（「我虚岁33了」事故句形态）；
- 声明前缀型：`(今年|现在|如今|目前|本人|我已经|我今年|命主|用户|你今年|你现在)
  (?:我|你|他|她)?(?:周岁|虚岁)(\d{1,2})(?=[，。！？；、,.!?]|$)`——「今年我虚岁33。」
  类句首声明（前缀=当前年龄语域 + 句读/行尾收尾排除事件表述）。
- 误伤面评估（记录）：窗内数字（27/28 类）恒不误伤（claims 按窗口判定）；换运端点
  表述由镜像 pattern2 的连接词排除面拦下（探针 6 句全 pass）；残余 = 反问/引用否定句
  「你虚岁33了？不，我28岁。」类会误拦——**k15 review 已记录的既有误拦族（岁字版
  「你虚岁33岁？」base 同样误拦）的同形态延伸，非新类**，本批注释+测试注明；
  风险低（真实年龄声明几乎必带岁或了），可接受。
- 影响面：scripts/eval_agent/l2_eval.py `_AGE_CLAIM_RES`；既有事故句/双单位回归测试
  由 test_k11_fact_discipline + test_k15_eval_tails 全绿背书。

## 7. k16/k15 报告计数校准（Info，无代码）

- k16 审查 Info：k16 报告「k11 72 + 邻接 46 + bazi 5 + qz_full 200」与实计数不符
  （实为 test_k11_fact_discipline=36、五文件=82、test_bazi=4）——历史批报告报数偏小不实。
- **报数纪律（本批起）**：报告/progress 一律以 pytest 实际 collect 与通过数为准
  （collect-only 口径，按文件实跑统计），不复述/不推算他人数字；本批各段测试数字
  全部实跑后按 collect 填写。

## 8. DST_TABLE 注释「第 2 个星期日」措辞（k16 审查 Minor-3）

**核对**：bazi.py DST_TABLE 注释现文（k16 已改）：「1987 年起每年 4 月中旬第 1 个
星期日 02:00 起（官方逐年日期落在 04-10~04-16）」。与表数据字面核对：
1988-04-10 = 4 月第 2 个星期日（1988-04 周日=3/10/17/24）、4/10 属上旬（非中旬）——
「中旬第 1 个星期日」的通则措辞无法覆盖 1988 年表值；表数据本身正确（1988-04-10 为
官方逐年通告值，各源一致，k16 测试锁定 1986-1991 六行）。
**修法（表数据零改动，仅注释措辞）**：注释补记「逐年以官方通告为准：1988-04-10 为
4 月第 2 个星期日（上旬末）特例，不满足中旬首周日通则；整体落 04-10~04-16」——
照 k16 审查 Minor-3 建议原文落地。

---

## 实现与提交计划

| 项 | 文件 | 提交 |
|---|---|---|
| 1 | src/api/advisor.py + tests/test_k17_advisor_persons_first.py | fix: … (k17) |
| 3+4 | src/rag/search_trigger.py + tests/test_k17_search_trigger.py | fix: …(k17)（3）+ docs 注释（4，同一文件随 3 提交或独立） |
| 5+8 | src/engines/bazi.py + tests/test_k17_bazi_gap_time.py | fix: …（5）+ 注释措辞（8） |
| 6 | scripts/eval_agent/l2_eval.py + tests/test_k17_age_claim_bare.py | fix: … (k17) |
| 7 | 本 plan + progress.md | docs: …（plan 随首个 fix 或独立） |
| 2 | 无代码（评估记录在本 plan + progress） | — |

测试（各改动点邻接，勿全量）：每项新测试文件 + 回归邻接
（test_k16_calendar_dst / test_k11b_search_trigger / test_k11_fact_discipline /
test_k15_eval_tails / test_adaptive_advisor / test_k11c_solar_switch）。

## 红线
- git add 仅本批文件；不 push 不重启不碰生产库不改 .env 不动 tool_calls.py；
- data/ 预存脏态（data/memory/.json、ledger.json、comparison_runs.jsonl、
  data/eval/results/*）不 add；
- progress.md 非 git（.superpowers/sdd/.gitignore），仅本地追加。

---

## 实施记录（2026-09-10，k17 完成）

提交（4 个，均本地未 push）：
- 4127745 fix: k17 advisor REST persons-first read（项 1：api/advisor.py + 测试 6）
- f85f104 fix: k17 search_trigger calc 单扫去重 + 白名单语义注记（项 3+4，测试 7）
- 0b73881 fix: k17 jiaoyun 部分段缺 time 防御兜底 + DST_TABLE 措辞（项 5+8，测试 9）
- a66214d fix: k17 age_claim 岁字省略形态补拦（项 6，测试 18）

逐项实现说明与实施中新增发现：
- 项 1：advisor_v2 核实零直读（接收 BaziResult，import 核对）；_handle_advisor 已在
  k11c 改过——唯一改造点 = api/advisor.py。残余直读清单（main.py 2018/2055/2350、
  dashboard.py 27/92、hourly.py 102）记录待后续批，本批未动。
- 项 3：双扫只发生在**软路径**（硬锚命中短路只扫 1 次）——探针「跳槽什么时候合适」
  = 2 遍；抽 `_local_fortune_verdict` 后恒 1 遍（计数测试锁定）。
- 项 5：缺口分两形——年表**有条目但 time 缺失/损坏** = 缺口告警过渡；**整条不在
  年表**（与表外段不可区分）＝静默按虚岁兜底（k16 表末语义自然延伸）。首段缺 time
  且 now 早于首条已知时刻 → 整体虚岁兜底 + 告警（行为与改造前一致，仅补可见性，
  探针实证）。全量数据多 now 零告警零漂移（2021/2026/2081/2095 对照 k16 语义）。
- 项 6 实施中新增发现（记录在案，非本批修复）：「虚岁33岁时进入乙丑大运」被
  pattern2 误拦 = **base 既有**（k15 双单位收紧的复现样例未覆盖「时」连接词，前瞻
  排除缺 时）——git stash 对比实测（base 同样 FAIL）；本批未扩未改，建议后续批给
  pattern2 前瞻补 时/际 字符（与 k17-6 新增 pattern 的排除面对齐）。另「你虚岁33了？
  不，我今年28岁。」反问否定句 = k15 已记录既有误拦族（岁字版同误拦）的同形态延伸。
- 测试实跑数字（collect-only 口径，pytest 实跑）：
  - k17 四文件 40 passed（6+7+9+18）
  - 邻接：k16_calendar_dst+jiaoyun+qiyun 56 / k11_fact_discipline+k15_eval_tails 52
    / k11b_search_trigger+web_search 56 / adaptive_advisor+k11_fact 53（5 skipped）
    / k11c_solar_switch+calendar_persons_read+r13_profile_routing+bazi 等 95 passed
    （1 starlette import 警告既有）——合计 k17 首测即全绿，无回归。
- 纪律：git add 每批仅本批文件；data/ 预存脏态未 add；不 push 不重启不碰生产库
  不改 .env 不动 tool_calls.py；progress.md（非 git）追加 k17 段见主会话侧。
