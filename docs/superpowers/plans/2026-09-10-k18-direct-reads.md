# k18 bazi_info 深度直读点 persons-first 化（2026-09-10，分支 k18-direct-reads，BASE main=a7f7236）

> 本批=全量盘点并收口「未经 get_user_birth_profile 直接读 users.bazi_info」的
> 消费点（k8/k9 后续、数据一致性铁律：persons=单一事实源，bazi_info 仅兜底，
> 四柱/盘面键只属于 chart_records）。k17 已收 advisor REST + k11c 已收
> chat advisor/calendar/hourly/zeri，本批收 k17 plan 记录的残余清单 +
> k11 报告已知清单（问候/解梦/学堂/_has_saved）+ 报告页/hourly/calendar legacy
> 核改。纪律同前：git add 只加本批；不 push 不重启不碰生产库不改 .env
> 不动 tool_calls.py；报告报数以 pytest 实跑为准（collect-only 口径）。

---

## 1. 全量盘点清单（权威，grep src/ 全仓 + k17 plan 清单并集）

读法约定：**E-族**=存在性/birth 键判断；**D-族**=盘面键（bazi/day_master/
wuxing/geju/yongshen/dayun）消费；✅=已 persons-first；🔧=本批改造；
📝=留档（理由在 §3）。

| # | 位置（当前 HEAD 行号） | 消费键 | 用途 | 处置 |
|---|---|---|---|---|
| 1 | api/advisor.py ~99 | birth 键 | advisor REST | ✅ k17 已改（链+to_solar_date+solar_time） |
| 2 | handler._handle_advisor 7760 | birth 键 | chat advisor | ✅ k11c 已改 |
| 3 | handler._handle_hourly 5284+ | birth 键 | chat 时辰 | ✅ k9/k11c 已改（链+缺四柱引擎补齐） |
| 4 | handler _tool_zeri（_map_user_bazi_for_zeri 3146） | birth 键 | 择日 | ✅ k9 已改；2568 docstring 仍写 dao.get_user_bazi → 本批注释修正 |
| 5 | main.py 报告页 1396/1421/1450 | 全量 | 基础命书/幸运派生 | ✅ k8 已改 get_user_birth_profile_full |
| 6 | api/user.py GET /api/user/profile 623-627 | 全量 | 维护页/画像 | ✅ k8 已改（persons 默认命主+full） |
| 7 | api/xuetang.py 118-125（REST） | 全量 | 学堂个性化 | ✅ k8 已改 full |
| 8 | api/calendar.py 158（today 现代面） | 链出参 | 今日运势 | ✅ G3c 链读取（非直读）——**观察项见 §3-5** |
| 9 | handler._greeting_reply 1279 | 存在性 | 问候「八字已保存」 | 🔧 E-族 → `_get_user_birth_profile` |
| 10 | handler._should_fastpath 5928 | 存在性 | RAG 快路径门控 | 🔧 E-族 → `_get_user_birth_profile(allow_chart_fallback=False)` |
| 11 | handler._free_chat 8314（saved_bazi 三处门） | 存在性 | 出生信息引导/更新路由/渐进 hint | 🔧 E-族 → `_get_user_birth_profile`（fail-open 包 try） |
| 12 | handler._do_dream_analysis 7612 | bazi[2] 日主 | 解梦个性化上下文 | 🔧 D-族 → `get_user_birth_profile_full` |
| 13 | handler._handle_xuetang 7726（chat 侧） | day_master/bazi/geju/yongshen | 学堂个性化 | 🔧 D-族 → `get_user_birth_profile_full`（与 REST k8 同源） |
| 14 | main.py /api/calendar/daily 2018（legacy） | D-族（喂 LuckyCalendar.daily） | AI 日历 | 🔧 D-族 → full |
| 15 | main.py /api/calendar/week 2055（legacy） | D-族（喂 LuckyCalendar.week） | AI 周历 | 🔧 D-族 → full |
| 16 | main.py /api/share-card 2350（legacy） | bazi[]/day_master | 分享卡 | 🔧 D-族 → full |
| 17 | api/dashboard.py 27 | bazi/day_master/gender/birth | 面板 profile | 🔧 D-族 → full（birth 键同源在出参） |
| 18 | api/dashboard.py 92 | D-族（喂 cal.daily） | 面板今日摘要 | 🔧 D-族 → full |
| 19 | api/hourly.py 102（REST /api/hourly-fortune） | bazi[2] 日主 | 时辰运势 REST | 🔧 D-族 → full |
| 20 | birth_profile.py 121/167 | ② 自愈比对/② 兜底 | 链本体（读序 persons→bazi_info→chart_records） | 📝 链核心，不动 |
| 21 | dao.py save_user_bazi 守卫 | 写侧 | k8 一致性守卫 | 📝 写路径（本批=读面） |
| 22 | api/user.py 302（login W5）/ POST /api/user/bazi | 写侧 | 登录空行/档案编辑 | 📝 写路径（W4/W5 写入口，非读点） |
| 23 | src/memory/user_memory.py 906（记忆层 bazi_info） | 记忆 json（非 users 表） | LLM 上下文画像摘要 | 📝 另一存储（排盘镜像，恒公历口径 k9 已定），非 users.bazi_info 读 |
| 24 | security/privacy.py 209/307 | users.bazi_info | 注销清除/后台导出 | 📝 合规面（删除/取证），非产品读面 |
| 25 | handlers 已知清单核对：问候 1243=#9、zeri 3163=#4 ✅、advisor 7331=#2 ✅、解梦 7187=#12、学堂 7295=#13、_has_saved 5554/7860=#10/#11 | | | 行号随迭代漂移，以本表为准 |

**盘点数：25 点；改造 11（#9-#19）；留档 8（#20-#24 各族 + #8 观察）；其余 6 点先批已收。**

## 2. 改造语义（三族，均保留 persons→bazi_info→chart_records 读序兜底）

- **E-族（存在性/birth 键，4 点：#9 问候 / #10 fastpath / #11 free_chat 门）**：
  原 `dao.get_user_bazi()` 只认 users.bazi_info 行 → persons-only 建档用户
  （09-04 wipe 后画像清零但 persons 保留的 37 人现实人群）被误判「无八字」
  → 错误引导建档/漏问候/漏快路径。改走 `_get_user_birth_profile`（G3c 同源链）。
  - #10 特殊：**allow_chart_fallback=False**（新参数，默认 True 不扰他方）——
    fastpath 的「档案」语义只认 persons/bazi_info；③ chart_records 兜底行
    可能只是他人/择时盘，且「已存同盘」已由上方 chart 同生辰比对条件覆盖
    （k9 契约锁定：chart 不匹配 + 无档案 → False，跑 RAG）。
  - 问候/free_chat 保留 ③（D8 语义：排盘落库=有档案，不再引导建档）。
- **D-族（盘面键，7 点：#12 解梦 / #13 学堂 chat / #14-#16 legacy calendar+
  share-card / #17-#18 dashboard / #19 hourly REST）**：原直读旧行 bazi[]/
  day_master 等键（可能为历史他人盘污染，21:44 事故源；k8 起自愈重建行已
  无 bazi 键 → 这些点实际已断源，依赖自愈收敛=台账原文）→ 改走
  `get_user_birth_profile_full`（k8 语义：birth 键链读取 + 四柱只取「出生
  档案匹配（_chart_birth_matches）的 chart_records 盘」；无匹配盘 → 不消费
  行内 bazi 键，显示/引擎按无盘面兜底）。LuckyCalendar.daily/week 只消费
  bazi/day_master/wuxing/dayun → 喂 full 出参即可（无需 lunar 转换，盘面
  本身是排盘产物）；hourly/解梦日主取 bazi[2] 的显示形态逐字节不变（仅换源）。
- solar_time 透传：本批 11 点无引擎 calculate 消费者（盘面直接来自 chart
  records / 存在性判断），无 solar_time 语义缺口；full/链出参已带
  solar_time（k11c），留给后续引擎面消费方。

## 3. 留档点与理由（改不动/语义特殊/范围外）

1. **链本体（birth_profile.py ② 自愈+兜底）**：就是 persons-first 读取序本身；
   ③ 级兜底对「档案存在性类判断」新增可选开关关闭（#10）。
2. **写路径（dao 守卫 / api/user.py 302 + POST bazi）**：写侧（W1 自愈、
   W4 档案编辑、W5 登录空行、dao 守卫），k8 已收写面语义，读面批不动写。
3. **记忆层 bazi_info（user_memory.py:906，get_profile_summary）**：读的是
   memory json 的排盘镜像（写侧=排盘时转换后公历值，LLM 上下文恒公历口径，
   k9 已定），非 users.bazi_info 表——不属本批「users.bazi_info 直读」面。
4. **privacy.py（注销清除/后台导出）**：合规删除/取证面，非产品显示读面。
5. **观察项：api/calendar.py 现代 today 面喂盘面键的供给**：today 已走 G3c
   链（非直读），但 LuckyCalendar.daily 消费盘面键——链 ①② 出参在 k8 自愈
   后不再含 bazi（③ 含但仅限无 bazi_info 行用户）。指纹/缓存键契约
   （test_calendar_persons_read 锁定指纹=链出参）使 today 不宜直接换 full；
   建议后续批评估「盘面键消费的引擎面统一喂 full 出参」时的指纹分键方案
   （today 用户若有匹配 chart，full 与链指纹都会变 → 一次冷缓存，行为趋正）。
   本批不越界改（属 modern 面设计议题，非直读点）。
6. **#10 fastpath 的 ③ 关闭**：语义特殊点（RAG 快路径的「档案」=真实档案
   而非任意排盘记录），通过链新可选参数表达，理由见 §2。

## 4. 文件与测试

- 代码：src/storage/birth_profile.py（新参数 allow_chart_fallback）、
  src/bot/handler.py（5 直读点+docstring 修正）、src/main.py（3 legacy 点）、
  src/api/dashboard.py（2 点）、src/api/hourly.py（1 点）
- 新测试：tests/test_k18_direct_reads.py（38 条，真实 tmp 库 DAO/PersonDAO/
  ChartDAO 同库装配；问候 5 / fastpath 5 / free_chat 3 / 解梦 5 / 学堂 4 /
  hourly REST 4 / dashboard 4 / main legacy 8：每点覆盖 persons 有→读
  persons、persons 无→兜底 bazi_info、分裂场景→persons 胜且旧行 bazi 键
  不被消费）
- 回归邻接（pytest 实跑，collect-only 口径）：616 collected 全绿
  （38 k18 + 578 邻接 24 文件：k8 13/k9 27/fastpath 17/today_cache 6/
  llm_route 8/k11c 16/calendar_persons_read 9/partial_birth 42/bot 65/
  intent_routing 24/record_query 21/single_stream 17/member_pay 53/
  tool_scene_routing 21/capability_registry 46/card_six_engines 11/
  toolguide_mainchain 9/eval_r1_1 11/dream_g4 39/k11_fact 36/
  profile_consistency 9/r13_profile_routing 26/g1_gender_contract 26/
  bazi_archive_priority 26）

## 红线
- git add 仅本批 6 文件（4 代码 + 新测试 + plan）；data/ 预存脏态
  （data/memory/.json、ledger.json、comparison_runs.jsonl、data/eval/results/*）
  不 add；不 push 不重启不碰生产库不改 .env 不动 tool_calls.py；
- progress.md（非 git，.superpowers/sdd/）追加 k18 段。
