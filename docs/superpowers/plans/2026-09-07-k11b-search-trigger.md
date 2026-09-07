# k11b 联网搜索语义触发改造（2026-09-07，分支 k11b-search-trigger，基线 main=3344e99）

> 背景根因：/tmp/fortune-actioncard-gap-20260906.md §①（只读）+ /tmp/research-github-solutions.md
> 「联网搜索触发策略专项」（业界=模型/语义决策为主+护栏兜底，OpenWebUI 关键词硬编码翻车实证，
> RouteQuery 结构化路由+adaptive-rag 检索失败升级可抄，semantic-router 3.9k★ MIT 为备选）。
> 用户实诉：问「易宝支付这家公司怎么样」AI 甩锅「你自己查证」——关键词硬门控太死板。
> k11 commit cf3a122 注明 entity_qa_search 派生断言待 k11b。
> 本批=命理意图域（bazi/career 等引擎主链回答路径）遇到外部实体/时效事实自动搜索并
> 用结果作答+标注来源，绝不甩锅「你自己查/我查不到」；同时不得因「今年运势如何」这类
> 本地计算问题误触发搜索（防 T074 乱搜回归）。每个改动点以注释锚定「k11b」。

---

## 一、现状断点定位（证据）

- 搜索能力（`src/rag/web_search.py` Bing 免费通道，search_web/web_search_available，缓存 5min、
  query 精简 `_simplify_query`）完好，本机实测可用。
- 搜索引导只挂两条链，全部在 chat 域：
  1. 意图 hint 注入 `handler._tool_loop_analysis_hint`（1362-1394，needs_search 且
     `_web_search_allowed` 命中才注入「先输出 web_search JSON 工单」）；
  2. `handler._run_tool_loop` 工单执行（原生链 1554 / JSON 工单 1643 双重 `_web_search_allowed`
     门控，拦截则回传占位「实时信息暂不可用，以下按命理知识分析」）。
- `_web_search_allowed`（1354-1360）= 三层关键词硬门控：研究类白名单正则
  `_WEB_SEARCH_RESEARCH_RE`（公司/行业/政策/新闻…）+ 金融行情显式排除
  `_WEB_SEARCH_FINANCE_EXCLUDE_RE`（股市/行情/股票/基金…，产品无行情源不搜不编造）。
- 断点：intent=career/bazi 等走引擎主链 `handler_map` → `_handle_career`/`_do_bazi_analysis`
  （4156-4204），全程无搜索工具通道；润色 `_polish_with_engine_draft`（1827）虽有
  search_hint 硬要求（第 0 条「先输出 web_search JSON 工单」），但 search_hint 只在
  needs_search（LLM 分析器单次判定，波动）+ 白名单双命中时非空，且执行依赖 LLM 自愿
  输出工单 → 实体 QA 常落到「礼貌甩锅」（行 54 原文）。
- 意图分类：`src/engines/message_analyzer.py` MessageAnalysis（needs_search 为 LLM 分析器
  同一次调用内输出，零额外成本）。

## 二、触发判定（两层 + LLM 信号，新模块 src/rag/search_trigger.py 纯函数）

判定顺序 = 金融硬排除 → 实体层 → 命理本地锚 → 时效/查证层 → 研究白名单兜底 → LLM 信号：
1. **金融行情硬排除**（T074 防回归，规则原文不动）：股市|行情|股票|基金|大盘|指数|股价|
   涨跌|炒股|收盘|开盘 → 永不搜（产品无行情数据源，诚实说明，不搜索不编造）。
2. **实体/主题命中层（确定性）**：词表+模式：
   - 机构后缀族（最长优先 alternation）：有限公司/公司/股份/支付/银行/证券/保险/基金/
     信托/期货/集团/科技/网络/平台/APP/大学/学院/研究院/医院/出版社/小学/中学/连锁/
     品牌/产品/楼盘/旗舰店 等（含「X 这家公司」形态的定中结构剥离：前置名称串先做
     指代词清理，再向后匹配后缀）；
   - 主流实体名表（无后缀大厂/品牌：阿里/腾讯/字节跳动/小米/华为/百度…，可扩展词表，
     注释注明「用户实诉新词在此追加」）。
   实体命中 + 强实体问词（靠谱/可靠/正规/评价/口碑/待遇/工资/是做什么/主营业务/上市/
   融资/市值/裁员/暴雷/倒闭/欠薪/招聘/官网/创始人/多少钱/值不值得/适合我吗/面试…）
   → 必搜，query=实体名。
   实体命中 + 弱问词（怎么样/如何/咋样）且**无命理本地锚** → 搜；弱问词+本地锚并存
   （「我在易宝支付上班，今年运势怎么样」——问词挂的是运势）→ 不搜（见层 3）。
   实体命中但纯陈述/本地问 → 不搜（实体只是背景信息）。
3. **命理本地概念锚（防误触核心）**：运势|运程|财运|流年|流月|大运|八字|命盘|排盘|
   起名|取名|改名|择日|吉日|黄历|搬家|嫁娶|合婚|配对|风水|面相|紫微|六爻|解梦|
   抽签|桃花|姻缘|生肖 等（纯本地问题集）→ 无实体强问时一律不搜（「今年运势如何」
   「我明年财运」「这个月适合搬家吗」零搜索，T105 断言锁死）。
4. **时效/查证层（确定性）**：最近|最新|新闻|政策|法规|新规|赛事|多少钱|价格|市值|
   财报|评价|口碑|网上|官网|官方|报道|辟谣|查一下|搜一下 等外部事实词；需无本地锚
   且非金融 → 搜，query=_simplify_query(msg)（复用 web_search 既有精简，不新建）。
5. **研究白名单兜底（向后兼容）**：原 `_WEB_SEARCH_RESEARCH_RE` 词条原样移入本模块，
   供 chat 域与旧测试语义不变（无本地锚时正信号）。
6. **LLM 语义信号**：`MessageAnalysis.needs_search`（分析器同一次 LLM 调用已产出，
   复用主链既有通道——本批不新增 GLM 二判调用）→ 叠加为 OR 信号（层 1/3 硬排除仍否决）。

**取舍（LLM 二判用没用）**：没用独立二判 LLM。理由：主链引擎意图域没有可复用的
「工具决策」通道（capability_registry 工具说明书只注入 chat 域 tool-loop），另加一次
GLM 判定 = 每个引擎意图轮 +1 次 LLM 调用（~0.5-1.5s 延迟 + token），而确定性层已覆盖
PM 实诉长尾、误触风险由「本地锚/金融排除」双否决控制；模型语义信号已有
MessageAnalysis.needs_search（同一调用内免费产出），按 OR 语义接入即等效「一判语义 +
护栏」业界范式。若后续意图面膨胀（如秒回/advisor 卡域接入）再评估 semantic-router
（本地 embedding 路由，MIT，备选记录在案）。

## 三、接线（复用既有 executor，禁新建 HTTP 通道）

主链引擎意图出口（`handler.process` 的 `_will_polish` 块，4246-4254）：
1. `analysis_hint` 已预生成（3938）。`_will_polish` 为真（引擎 draft + 引用已注册 +
   非降级 + 非 xuetang/advisor 早退面）时调用新方法 `_engine_domain_ground_search(msg,
   user_id, analysis)`：
   - `decide_search(msg, llm_needs_search=analysis.needs_search)` → 不触发 → skip=True
     （保持旧 analysis_hint 通道原样，零行为漂移）；
   - 触发 → 护栏频控（3 次/60s/用户，新 `_search_rate_ok`，类常量可覆写）→ 超限按
     「查询太频繁」降级注（不静默不甩锅）；
   - 复用 search executor `_tool_web_search(query, user_id)`（真实检索 + 本轮 citations
     注册 type=web 单一编号源，`_tool_web_search` 顶层加同 query 复用：本轮已自动检索
     过同 query → 直接回传首查结果文本，防 LLM 工单二次真实检索/编号分裂）；
   - 域名白名单护栏：结果仅收 http(s) URL、去重、`_parse_result_domains` 取站点域名
     （www. 前缀剥离）供来源尾注；长度钳制：注入块 ≤2400 字符；
   - 返回 `{block, entity, domains, ok, query, skip}`。
2. 触发后润色注入（4249-4252 改造）：
   - `search_hint` 置空（结果已注入，不再要求 LLM「先输出 web_search JSON 工单」——
     旧硬要求若保留会诱导 LLM 重复发工单；同 query 由 executor 复用去重，异 query 由
     频控兜底）；
   - `extra_hint` = topic_hint +（未触发时 analysis_hint）+ ground block + 使用要求段：
     「外部实体事实以检索结果为准，禁凭记忆编造，引用标 [n]，回复末尾一行注明信息来源
     （信息来源：…公开网络搜索结果，仅供参考，以官方渠道为准）」；检索空/失败 →
     降级注要求 LLM 如实说「帮你查过公开信息有限/查无结果，建议以官方渠道为准」，
     绝不说「你自己查」（graceful degrade，非甩锅；检索到官网/百科链接时给出）。
3. 来源痕迹确定性兜底（引擎意图出口 scrub 后、卡片包装前）：LLM 漏写来源时
   `append_source_trace(reply, entity, domains)` 纯函数补尾注
   「（信息来源：站点域名…，公开搜索结果，仅供参考，以官方渠道核实为准）」——
   仅当回复含实体名且无「来源/http」痕迹时补；回复以反馈提示（———…准/不准）收尾时
   插在反馈提示之前（不破坏「准/不准」交互位置）。禁裸 JSON 禁泄漏：注入内容全为
   确定性文本（无 <tool_calls>/JSON），并走 k11 stream-guard/scrub 既有出口。
4. 每轮 process 入口清 `_turn_grounded[user_id]`（3768 区域，与 _citations 同位）。

## 四、护栏降级（原关键词硬门控 → 护栏，不拦截触发判定）

- 判定层 `_web_search_allowed` 改委托 `decide_search(msg, llm_needs_search=False)`
  （chat 域 tool-loop/hint 语义自动继承：金融硬排除与本地锚仍否决，白名单正信号保留——
  T074 相关既有断言逐条不变，新增实体/时效正信号扩大覆盖「易宝支付靠谱吗」这类
  无白名单词的实体 QA）。
- 执行层护栏：① 频控 3 次/60s/用户（`_search_rate_ok`，判定不计数、发起真实检索前计数；
  ② 域名：仅 http(s)+去重（可扩展黑名单占位注释）；③ 结果长度钳制 2400 字符/块；
  web_search.py 既有 snippet 200/标题 120 截断不动。

## 五、防误触测试锁定（T105 负例）

- 「今年运势如何」「我明年财运怎么样」「这个月适合搬家吗」「我和她八字合不合」+
  「我在易宝支付上班 今年运势怎么样」（实体背景+本地问）→ 零搜索；
- 「今天股市行情怎么样」族 → 零搜索（T074 语义原样）；既有 T074 断言文件回归不红。

## 六、评测（T104 实体 QA 正例，断言即 tests/test_k11b_search_trigger.py）

- 用例句：易宝支付这家公司靠不靠谱/怎么样（行 54 同款）+ 变体（适合我吗/靠谱吗）。
- 断言：触发 web_search（monkeypatch search_web 计数 ≥1，query 含实体）；
  回复含来源痕迹（LLM 回复漏写来源时确定性尾注在最终回复里）；无「你自己查/自己查证/
  实时信息暂不可用，以下按命理知识分析」甩锅句；无裸 JSON/`{` 泄漏；引用注册含
  type=web 条目；注入润色的 system 含「网络检索结果」且不含「先输出…web_search」工单
  要求（防双搜）。负例「今年运势如何」同链路断言 search_web 零调用。
- agent_tasks.jsonl / l2_eval derived（entity_qa_search 挂行）不动：本批范围=确定性
  pytest 断言（k11-F 已留 entity_qa_search 待办；挂行+真实 Bing 冒烟留主会话评测批）。

## 七、范围外（记录后续项）

- 秒回安抚 / advisor 关键词早退面（4028 分支）/ xuetang / confidant 的搜索接入（PM
  实诉经 career/bazi 主链，此批覆盖主链即可；advisor 面=行动建议卡域，另行评估）；
- 意图面若膨胀 → semantic-router（本地 embedding 路由，MIT，3.9k★）触发条件记录在案；
- 无后缀非主流实体（「极兔」类）=实体词表扩展点（注释注明追加位置），暂由
  needs_search LLM 信号兜底。

## 八、纪律与验证

- plan 本文件 + 代码 k11b 注释锚点；提交 fix:/feat:… (k11b)；git add 只加本批文件；
  不 push 不重启不碰生产库不改 .env；tool_calls.py 零改动；progress.md(.superpowers/sdd)
  追加 k11b 段。
- 测试：`cd /mnt/e/fortune-agent-deploy && OMP_NUM_THREADS=4
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k11b_search_trigger.py -q
  -p no:cacheprovider`；回归邻接：web_search/r13_profile_routing（T074）/
  handler_analysis_flow（hint 门控）/eval_r1_2（引擎主链 e2e 脚手架）/k11_fact_discipline/
  toolguide_mainchain/tool_scene_routing。
