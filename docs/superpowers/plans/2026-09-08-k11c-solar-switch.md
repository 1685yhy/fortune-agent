# k11c 真太阳时档案级开关（2026-09-08，分支 k11c-solar-switch，基线 main=ae36cef）

> 用户 2026-09-06 拍板：真太阳时**默认开 + 前端档案开关**，切换存档案、重排生效。
> k11 plan 报告 §③ 用例 6（hour_boundary）依赖本批裁决后挂评测行；R2-1 concern
> 「daylightSaving/solarTime 未随盘落库，重排丢开关信息」= 档案缺开关字段，重排/跨
> 消费面读不到用户设置，本批在 persons 档案落开关字段并打通全部档案消费链。

---

## 一、现状与断点（证据）

- 引擎层 R2-4 已定稿默认开：`BaziEngine.calculate(..., solar_time=True)`（src/engines/
  bazi.py 691 起；False = 跳过 `_true_solar_time` 经度+均时差修正，北京时间直排）。
  `/api/paipan` BaziInput.solarTime 默认 True 已透传（hehun.py BaziInput 43 行）。
- 档案层断点：persons.birth_enc 密文 JSON 无开关字段（BIRTH_KEYS 8 键）；users.bazi_info
  / chart_records 更无。handler 十余处 engine 直调面全部默认开口径 —— **用户档案一旦
  无开关语义，切「关」无处存、重排即丢**（R2-1 concern 原文）。
- 档案读取链（persons 单一事实源）：src/storage/person_dao.py（加密存取）+ birth_profile.py
  `get_user_birth_profile`（① persons → ② bazi_info → ③ chart_records，G1/k8 自愈回写），
  cache 指纹 = `profile_fingerprint(profile)`（H-7：calendar:today / 对话缓存键同源）。
- 前端档案编辑面：pages/bazi（选命主+八字档案表单，更正八字档案=档案编辑主入口，
  PUT /api/persons 或 POST /api/user/bazi）与 pages/persons（档案添加/编辑表单，PUT
  /api/persons）；paipan 页有会话级开关（R2-4，注释自承「持久化是未来项」），预填默认
  命主时未回显档案开关。

## 二、方案（改动点逐条，注释均锚 k11c）

**A. 数据层 —— persons 档案新增 solar_time 开关（密文内字段，零迁移）**
- `BIRTH_KEYS` 增 `"solar_time"`（密文 JSON 内而非新列：birth_enc 全行加密重写无迁移，
  新列需 ALTER + 迁移脚本——最小改动取舍）；新增读口径归一 `solar_time_on(raw)`：
  0/'0'/false → 0；缺失/None/空/非法 → 1（默认开兼容旧行）。
- `_birth_dict`：solar_time None/空/非法 → **省略不落库**（update 合并语义 = 未提供
  不覆盖既有值；create 缺省开由读路径兜底）；显式 0/1 → int 落库。**0 必须显式携带**
  （禁止 `int(v or 0)` 把 0 吞成 None）。
- `_row_to_person` 出参带 `solar_time`（0/1，恒有键）；update_person 合并循环经
  BIRTH_KEYS 自动「缺失保留既有、显式 0/1 生效」；删除/设默认不涉及。
- API：`PersonRequest` / `BaziRequest` 增 `solar_time: Optional[int]`；`_person_birth`
  透传（鸭子类型请求 getattr 防御）；`/api/user/bazi` 显式携带时同写 bazi_info +
  persons 默认命主，未携带 = 旧调用方零变化（不重置开关）。
- 兼容：users.bazi_info / chart_records 源行无键 → 读取链默认开；bazi_info 自愈回写
  （k8）不比对 solar_time（避免存量用户一次性全量回写噪音），但回写内容 new_info=
  dict(out) 自然携带 —— 触发自然回写时顺带镜像，无键旧行由读路径缺省兜底。

**B. 引擎/消费链 —— 档案开关 → 引擎（开=现行为不变；关=跳过经度修正直排）**
- `_do_bazi_analysis(..., solar_time=True)` 透传 engine.calculate（主链中央口）；
  `_feed_birth` 从 src 档案 dict 取 solar_time（0/1/无键默认 True）→ 建档/B1/B2/F2
  对话直排全部随档案。
- `_handle_bazi` parsed 直排路：本人（非第三方）且有档案 → 随档案开关（年份冲突已
  在既有拦截返回）；第三方/无档案 → 引擎默认开（消息级排盘无开关语义，零变化）。
- 秒回预生成 `_start_pregen_instant(msg, user_id)`：本人且消息年份=档案 → 同口径
  （防秒回安抚引错时柱与主链卡片矛盾）。
- 运势/日历面：`_handle_advisor`（残余 get_user_bazi 直读改 G3c 统一档案读取 +
  solar 透传，persons 建档用户不再误引导）、`_handle_calendar`/`_handle_hourly` 引擎
  补齐、`_map_user_bazi_for_zeri`（solar 透传，供择日个人分）；`/api/calendar/today`
  与对话缓存指纹随 profile（含 solar_time）变化 → 开关切换当日立即重算（H-7 同源，
  无需新代码——读取出参键即指纹键）。
- 时钟边界（引擎既有逻辑零改动，测试锁定）：23:xx/00:00 跨日与生日当天边界不受开关
  影响——晚子时归日按「实际排盘时刻」判定：开=修正后、关=北京时间原样，两口径跨日
  结果逐字一致（如 23:40 长春：开=修正 00:05 归次日 / 关=晚子时归次日）；「归日只随
  修正发生」天然满足（关时无修正即无修正归日）。

**C. 前端 —— 档案编辑面开关（默认开 + 说明行 + 保存提示）**
- pages/bazi 表单与 pages/persons 表单各加「真太阳时修正」switch（默认开，朱砂
  #A93A2C，墨韵既有 tokens，禁灰系；新 wxss 仅 1-2 条布局规则）+ C 指定说明行
  「按出生地经度把当地时间换算真太阳时再定时辰；关闭则按本地时间直接排」。
- 编辑回显档案 solar_time（0→关，缺失/旧档案→默认开）；保存 payload 带
  solar_time 1/0（PUT /api/persons / POST /api/user/bazi）；切换后保存 toast 逐字
  「已更新，重新排盘生效」（未切换 → 原保存文案，零误报）。
- paipan 页预填默认命主时回显档案开关（patch.bSolarTime = p.solar_time !== 0；
  R2-4 注释「持久化是未来项」由档案开关承接）；paipan 页本体仍排完即走不改档。
- 不做：bazi/persons 表单分钟级精度改造（时辰粒度代表整点存量行为）、onboarding
  建档页开关（新建默认开=产品口径，无需开关交互）。

**D. 测试**
- tests/test_k11c_solar_switch.py（13 用例）：①golden 引擎逐字 1999-05-13 10:55 长春
  男 开=11:20 壬午午时/关=10:55 辛巳巳时（年/月/日柱逐字一致）+ 省前缀全路径 city；
  ②时钟边界 23:40/00:30 开关两口径四柱逐字一致 + 边界样本年/月/日柱不受开关扰动；
  ③旧档案无字段默认开 + solar_time_on 归一矩阵；④DAO 存取往返（仅开关翻转/无关字段
  更新不吞开关）+ 读取链出参带 solar_time + bazi_info-only 默认开；⑤指纹开/关异键；
  ⑥calendar:today H-7 同款换键断言（切关 → 重算 1 次、新旧键并存）；⑦API 往返
  /api/persons POST/PUT（0→1→未传不覆盖）+ /api/user/bazi（0→未传保留→1）。
- miniprogram/tests/k11c_solar_switch.test.js（9 用例，node）：默认开/回显（0→关、
  缺字段→开）/payload 字段/切换 toast 逐字/未切换零误报/新增命主默认 1/paipan 预填回显。
- 回归邻接：bazi（r24/solar_time/geo_city）、calendar_today_cache/calendar_persons_read/
  calendar_llm_route、birth_profile 系（profile_consistency/person_sync）、partial_birth、
  g1_gender_contract/g1_person_api_gender、r2 相关（bazi_residual_paths/archive_priority/
  chart_write_points/chart_reuse/chart_dao/handler_analysis_flow/lunar_birth_solar_convert/
  r13_profile_routing/chat_entry_fixes）。

## 三、hour_boundary 评测行方案设计（本批只设计不挂载）

k11 plan 报告 §③ 用例 6 待本批裁决后挂行；挂载执行列入后续项（F），本段为方案定稿：
- 双行设计（同 golden 档案 1999-05-13 10:55 长春 男）：
  - `hour_boundary_solar_on`（档案默认开）：setup.persons 不带 solar_time（=默认开
    语义，兼测旧档案兼容），turns 用户问「帮我排个盘」类 → reply_checks contains
    午时柱「壬午」；neg 不含「辛巳」时柱（防 LLM 把巳时当盘的隐性矛盾）；
  - `hour_boundary_solar_off`（档案关）：setup.persons 带 solar_time=0 → contains
    「辛巳」；neg 不含「壬午」。
- 锚定答复中的确定性排盘卡片四柱（引擎输出非 LLM 生成，稳定）；若答复为 LLM 深度
  分析（T101 同款主链），卡片区仍是确定性文本 → 断言锚卡片四柱即可，不用 derived。
- 校验约束：reply_checks.contains 只锚时柱干支（2 字）会与年/月/日柱字面混叠风险低
  （壬午/辛巳 组合词唯一性已核对：全盘 8 字内无重复干支）；加 derived 前置 persons
  检查（solar_time 存在与否）沿用 validate_tasks 现有 schema。
- 挂载点（后续项执行时）：data/eval/agent_tasks.jsonl 增行 + validate_tasks 任务总数
  门禁 100→105 + tests/test_eval_l2.py/test_eval_e6.py 计数同步（k11 同款流程）；
  结果落 data/eval/results/ 独立目录。

## 四、口径注记（挂账，换运年切段/交运时刻专项后续评估用）

- **换运年切段**：引擎大运年份段由 `_calc_jiaoyun`（_qiyun_datetime 精确交运时刻 →
  各步起止年表）驱动；虚岁「X岁起运」为展示近似，切段年表为权威（问真 8658 例
  100% 对齐口径）。开关翻转只改变排盘时刻（分钟级），大部分档案的交运时刻落在同一
  日内 → 切段年表不变；仅在修正跨日/跨节气日（如 23:5x 长春 + 21 分钟跨日）或修正
  前后交运时刻距节气边界 < 修正量时切段年表才会变——hour_boundary 类的用例都锚
  时柱而非换运年（换运年断言对分钟级修正不敏感，敏感样例为跨节气日的 23:xx，后续
  专项单列）。
- **交运时刻 vs 岁首**：岁首（大运第一段的展示起始）= 交运时刻所在公历年（按交运实
  刻换算），非出生年 + 虚岁整年的粗切；两者差 1 年的场景集中在修正前恰在节前、
  修正后跨节的边界档案。本批不做 0 点/节气的精确切段（问真口径已在交运年表层对齐），
  记录供换运年切段专项设计参考。

## 五、F 不做（后续项记录）

- 夏令时表 1986-1991 口径评估（daylight_saving 引擎参数已有，档案级开关=未来项）；
- 00:00 换日真机复测（引擎/测试已锁定，真机 UI 复测待用户可用时段）；
- hour_boundary 评测行挂载执行（§三 方案已定稿，行数据与计数测试随评测批落）；
- 换运年切段专项、交运时刻 vs 岁首精确口径专项（§四 注记在案）；
- 分前端表单分钟级输入、onboarding 开关、paipan/duipan/hehun 会话开关持久化到档案
  （hehun/duipan 的 BaziInput 各自默认开口径与档案语义解耦，如产品要求随档案再评）。

## 六、提交与纪律

- 提交：feat: k11c solar switch — …（k11c），git add 仅本批文件；data/memory/.json、
  data/eval/results/ledger.json、data/engine/out/comparison_runs.jsonl 及未跟踪
  data/eval/results/ 目录等预存脏态禁 add；不 push 不重启不碰生产库不改 .env 不动
  tool_calls.py。
- progress.md 追加 k11c 段。

## k11c-r1 审查修复（2026-09-08，/tmp/k11c-review-20260908.md：有条件通过，条件 1=F1 修 + 条件 2=F2/F3 拍板记录）

- **F1（修·C 回显漏洞）**：`_applyBazi`（bazi 页 _prefill 回显路径）不再硬编码默认开——
  有真值读真值（`b.solar_time !== 0`），无键（default_person_bazi_info 保持旧 8 键
  契约不剥离变更，legacy shape 测试锁死）才默认开；**保存 payload 语义由「恒携带 1/0」
  改为「仅在用户真实改动时携带」**（bazi/persons 两表单同规则：未改动保存不带字段 →
  服务端 update 合并保留既有开关，离线/空列表等无真值可回显场景绝不把 0 静默写回成
  1；新建缺省开由后端兜底）。测试：node 增「回显无真值未改动不带字段」用例 + bazi 未
  切换保存断言改为「不带 solar_time」；后端增 API 无字段创建默认开 1 / PUT 无字段保持 0。
- **F2（对齐·拍板记录）**：_tool_bazi 文本直排路径与 _handle_bazi parsed 直排同口径——
  本人（档案年份与消息一致）→ 随档案 solar_time；第三方/异年消息/无档案 → 引擎默认开
  （消息级无开关语义）。拍板：同句自我生辰经 tool/直达两路由四柱一致，档案开关即用户
  全局设置。测试：tests/test_k11c_solar_switch.py 增 _tool_bazi 四场景（关→False/开→
  True/无档案→True/异年第三方→True）。
- **F3（镜像·一行不可修→实现最小镜像）**：persons update_person 开关翻转时对
  users.bazi_info 密文行直接 SQL 镜像（不经 UserDAO.save_user_bazi——避开其
  consultation_count+1 副作用）；无关字段更新不镜像、行无出生年跳过、失败仅告警。
  删除默认命主后 ② 源回弹默认开场景：有 bazi_info 行的用户已被镜像 → 回弹消除；
  纯 persons 用户（无 bazi_info 行）删除默认后 ② 无源 → 无档案默认开 = 产品正确
  行为（新用户默认开口径），记录在案。
- 独立复跑：k11c 后端 16 passed（r1 新增 3）+ 邻接 213 passed（person_sync/profile_
  consistency/g1×2/calendar_persons_read/calendar_today_cache/bazi_residual_paths/
  chart_write_points/lunar_birth_solar_convert/capability_registry/k9_r2_minors/
  partial_birth）+ node 70 passed（k11c 10 用例 r1 更新 + g2_save_integrity/
  paipan_history_prefill/paipan_solar_time/paipan_noarch）。
