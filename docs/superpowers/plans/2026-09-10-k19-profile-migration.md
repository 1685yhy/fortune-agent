# 2026-09-10 k19-profile-migration：画像层清理迁移+守卫+精度（k8/k9 尾项收口）

- 分支：k19-profile-migration（基线 main=86e0661）
- 依据：数据一致性铁律 / k8 plan 后续项（存量 bazi 键清理、④-4 年份守卫、
  ④-6 读路径不写库评估）/ R2-4 真太阳时（分钟精度场景）
- 执行纪律：执行类动作留待用户确认（迁移脚本不真跑）；数据操作用
  dry-run+待确认；不 push 不重启不碰生产库不改 .env 不动 tool_calls.py

## 0. 本批逐项结论速览

| # | 项 | 结论 | 交付 |
|---|----|------|------|
| 1 | 存量 bazi_info bazi 键清理迁移 | 已做（脚本+判定单测+CLI smoke） | scripts/migrate_stale_bazi_keys.py + tests/test_k19_migrate_stale_bazi.py（9 passed）；**执行留待用户确认**（dry-run 跑过临时库，未碰任何真实库） |
| 2 | ④-4 年份合理性守卫（保守版） | 已做（默认仅告警+可选拒绝参数位） | person_dao.py 守卫 + api/user.py 表单哨兵 + handler.py 对话 ctx 透传 + tests/test_k19_year_guard.py（9 passed） |
| 3 | ④-6 读路径不写库评估 | 评估结论入本 plan（建议：短期保持+两处副作用收口；长期方案 B） | 见 §4 |
| 4 | 表单分钟级精度（10:55） | 已做（persons 档案表单 + paipan 排盘表单钟表模式；读回口径归一） | utils/persons.js + persons/paipan 页 js/wxml/wxss + api.js birthClock 透传 + birth_contract/hehun/zhuanxiang 后端 + tests（node 12 + py 6 passed） |
| 5 | paipan/duipan/hehun 会话开关档案化评估 | 评估结论入本 plan（paipan 已随 k11c；hehun 未随需小改；duipan 无档案联动不涉及） | 见 §5 |

## 1. 项 1：存量 bazi_info bazi 键清理迁移（k8 后续）

### 背景
k8 后 dao 写守卫（save_user_bazi → _guard_bazi_pillars）已防**新增**矛盾
bazi 键；但 21:44 事故族的**存量行**仍在：users.bazi_info 内携带 bazi 键但
与 persons/chart_records 分裂（08-16 污染的他人 2026-08-18 盘四柱曾被
G1 自愈沿用至 09-04 的形态）。k8 起该键无任何显示方消费，但构成数据污染
与未来误读风险 → 主动清理。

### 清理判定（宁少删不可误删）
- 无 bazi 键/空 → 无事（幂等）；
- birth y/m/d 齐全 → BaziEngine 按本人 birth 复算四柱比对（lunar 先
  to_solar_date 转公历，与 k8 守卫同口径）：
  - 相等 → **自证一致**（真实本人盘镜像）→ 保留；
  - 不等 → **stale_contradicts_birth**（21:44 畸形组装族）→ 清理；
  - 复算失败 → chart_records 盘证（birth 匹配口径=k8 _chart_birth_matches：
    y/m/d + calendar + hour 有无，hour 值不参与）→ 相等保留，无盘证清理；
- birth 键不齐（孤儿键，08-16 污染族形态）→ 无基准 → 清理。

### 脚本（scripts/migrate_stale_bazi_keys.py）
- 默认 dry-run：列出将清理行（user/birth 摘要/判定/persons 一致性备注），
  零写入；`--execute` 必须显式 + 必须 `--backup`（整库备份，目标已存在拒绝=
  幂等保护）+ 必须 `--audit`（逐行变更 jsonl）；
- 只认显式 `--db`（不认 FORTUNE_DB_PATH 等环境变量，防误碰生产库）；
- 清理动作=只删 bazi 键，birth 8 键及 solar_time 等镜像键原样保留；直接
  SQL 写密文（绕过 save_user_bazi——避免 consultation_count+1 副作用与
  写守卫复算；清理侧消费面 k8 已封口）；
- 执行时刻逐行双检（防扫描与执行间行变化误删）；幂等（执行后重跑 0 行）。

### 测试与验证
- tests/test_k19_migrate_stale_bazi.py 9 passed（判定纯函数 5 组 + chart
  匹配口径 1 组；零 DB 零 LLM）
- CLI 端到端 smoke（临时 SQLite，非生产）：dry-run 正确分拣
  keep_self_corroborated / stale_orphan / stale_contradicts_birth（persons
  1999 vs bazi_info birth 1995 的不一致备注同现）；execute 后审计 jsonl
  完整、重跑 dry-run 0 行、备份已存在拒绝生效。
- ⏳ **执行留待用户确认**：确认后按
  `--db <真实库> --execute --backup prewipe-k19-<时间戳>.db --audit k19-*.jsonl`
  运行（服务停机窗口内）。

## 2. 项 2：④-4 年份合理性守卫（保守版）

### 语义
persons 默认命主行写入侧（PersonDAO.update_person 命中默认行 / create_person
顶替既有默认）——birth_year 与既有值差 > 2（1995 vs 1999 = 4 可拦截族）且
上下文**非明示纠正句式** → 默认只 `logger.warning`（21:44 前置根因 A =
persons 曾整 2.5 周存错年份，守卫提供审计痕迹）；
- 明示纠正句式（对话原文含 不是/其实/更正/填错…等 16 个标记）→ 不告警
  （豁免=防误伤真纠正）；
- 表单显式提交（api/user.py 传 FORM_EXPLICIT_CTX 哨兵）→ 视为用户亲手
  编辑，同权豁免；
- `YEAR_SHIFT_REJECT = False` 为**可选参数位**（未来拍板开拒绝 = 非纠正
  大差写入返回原行不落库；纠正/表单永远豁免，拒绝不误伤）；
- 非默认命主（家人/朋友行）不触发（其年份本就可能与命主不同）；
- 差 ≤ 2（年龄推算浮动）不触发。

### 挂点（单一漏斗）
- PersonDAO.update_person/create_person 内守卫（bot 对话建档
  _sync_person_profile 与 api 表单全经此漏斗）；
- handler.py：_sync_person_profile 增 birth_ctx 参数，3 个调用点
  （_tool_bazi=params / _do_bazi_analysis=question / _do_ziwei_analysis=
  question）传消息原文 → 纠正句式命中即豁免；
- api/user.py：user_update_bazi / /api/persons POST/PUT 传表单哨兵。

### 测试
tests/test_k19_year_guard.py 9 passed：判定纯函数 2 组（阈值/句式）；三态
集成（正常建档零告警 / 真纠正豁免零告警 / 异常大差告警但照写）；非默认行
不触发；差≤2 不触发；拒绝参数位（开启后拒绝+纠正豁免+小差放行）；顶替
既有默认场景同规。

## 3. 项 4：表单分钟级精度（10:55 场景）

### 现状核实结论
- 前端档案/排盘表单此前**只能选 2 小时档时辰**（12 时辰 chips/picker →
  时辰代表整点 23/1/3/…/21 + birth_minute 恒 0）——10:55 场景无法表达；
- 后端 birth_enc / users.bazi_info / chart_records 的 minute 全链路**已存**
  （persons BIRTH_KEYS、BaziRequest/P ersonRequest.birth_minute、
  BaziInput.minute、引擎真太阳时修正用真实时钟小时+分钟）；缺口只在表单
  表达与 REST 边界（normalize_hour 把 0-11 恒当时辰序号）；
- 10:55 为何必须分钟：时辰边界 + 真太阳时修正可跨时辰（如上海 10:55 →
  修正后 11:01 → 午时；表单只给巳时代表 9:00 → 修正后 8:4x → 辰时，
  双向错误）。

### 读回口径归一（utils/persons.js）
birth_hour 两形态：**时辰代表整点**（奇数起点，只知时辰行）与**真实时钟
小时 0-23**（钟表/对话解析行）。hourToShichenIndex 归一：
- HOUR_VALUES 代表整点 → 查表（与时钟窗口同义）；
- 其余 0-23 → 时钟窗口映射（修旧缺陷：10 曾显 戌时（实巳）、12-22 曾漏判
  落到 子时）；
- 遗留说明：P2 前「旧序号 0-11 直存」形态（约 2026-08 前写路径已绝迹，
  且部分值当日即误读）随口径按时钟读回——属既有近似残余，如需精确迁移
  需产品拍板（值无法区分两义）。

### REST 边界（后端）
- birth_contract.normalize_hour(hour, minute=None, clock_signal=False)：
  minute>0 或显式 birthClock=True = 钟表时间声明 → 0-23 时钟小时直通；
  旧调用方（不带两者）行为零变化；
- BaziInput 增 birthClock: bool = False；hehun._resolve_person（paipan/
  hehun 共用）与 zhuanxiang 解析传入 minute/声明；love/duipan 对比工具等
  不带分钟调用方不变。

### 表单
- persons 档案表单：时辰 chips 下新增「记不清时辰？填钟表时间更准」折叠
  档 → 0-23 时 + 0-59 分双 picker；选时自动推导时辰 chips 联动；手选
  chips 即退出钟表模式；编辑回显：minute>0 或 hour 非代表整点的精确行 →
  钟表模式回显（10:55 不再误显戌时）；保存 birth_hour=时钟小时、
  birth_minute=分钟。
- paipan 排盘表单：同款折叠档；payload 钟表模式 → birthHour=时钟小时 +
  minute + birthClock 声明；档案预填精确行同步回显钟表模式。
- 展示：timeText/birthBrief/birthSummary 分钟>0 附「巳时 10:55」；三页
  档案预填 chips 经更新后小时口映射自动正确（hehun/bazi 无需逐点改）。

### 测试
- node tests/k19_minute_precision.test.js 12 passed（工具映射/显示/两页
  wxml-js 接线/api.js birthClock nullish 透传）；
- py tests/test_k19_minute_contract.py 6 passed（normalize_hour 旧语义
  零变化 / 分钟直通 / birthClock 声明 / _resolve_person 三场景）；
- 邻接：miniprogram 全量 node --test 281/281；python 邻接
  k8/k11c/calendar_persons_read/person_sync/chat_entry/bazi_solar_time/
  duipan/hehun(+k6)/profile/g1/person_sync 等 214 passed（86+128）；
  全量 collect 口径见 §6。

### 留档（后续项，本批不做）
- bazi 页（pages/bazi，另一档案编辑面）与 onboarding 页仍是时辰档：编辑
  钟表精确档案保存会把 hour/minute 降级为代表整点（显示已正确）；建议下
  一小批同款钟表档（UI QA 一并做）。
- hehun/duipan 页表单仍时辰档（见 §5）。

## 4. 项 3：④-6 读路径不写库评估（结论+建议）

### 现状
get_user_birth_profile 的 W1 自愈写仍在读路径（persons 命中且 bazi_info
缺失/不一致 → save_user_bazi 全量重建 8 birth 键）。k8 已收敛写形态
（全量重建、不携带 bazi 键），写仍是读路径副作用。

### 调优点/调用面盘点
链是画像事实源统一出口：main.py / api advisor+calendar+hourly+dashboard+
user+xuetang / bot handler（问候、排盘、快路径、解梦等）/ full 形态读取。
消费点 10+ 文件 40+ 处，全部依赖「读=已收敛」的既有语义——把自愈移出链
会让任一未显式 heal 的消费点在 persons/bazi_info 分裂期读到两库混合值，
回归风险集中在调用面而不是写点本身。

### 实测副作用（本次确认）
- save_user_bazi 每次调用 `consultation_count+1`（dao.py:203）——自愈回写
  会 bump 咨询数（k8 plan 已记录，本次代码核实）。与 #82 授权/统计语义
  冲突面。
- 自愈频率：stale 判定为全量比较（8 键+0/None 归一），收敛后每次读零写；
  自愈写只在分裂/首建/外部复写时发生——不是每读一写的高频路径。
- 并发/幂等：同线程读后写非原子（两源窗口极小）；写值幂等（同键重建）；
  SQLite 无 WAL 时短暂写锁只影响同库并发读（fastapi 多线程）。

### 结论建议（入 plan）
**短期（≤1 小批，建议直接做）：保持读路径自愈语义不动，收两处副作用**——
(a) 自愈回写改直接 SQL 镜像（person_dao k11c solar 镜像同款，绕开
consultation_count+1 与写守卫复算）；(b) persons 写侧（update_person 默认
行/建档）补 bazi_info 8 键镜像漏斗 → 收敛时机从「读时」前移到「写时」，
读路径自愈退化为低频兜底（仅历史分裂与外部直写场景）。
**长期：方案 B 数据层重构**——persons 单表事实源 + bazi_info 降级为兼容
视图/缓存（或删除列并迁移消费点）；需产品排期。
不做「读路径纯读化 + 显式 heal 接口」的中间态：调用面 40+ 点需逐点补 heal
调用，收益低且引入漏 heal 回归面（除非同时完成写侧镜像 (b) 使 heal 成为
空操作——若 (b) 完成，显式 heal 接口仅作运维工具存在即可）。

## 5. 项 5：paipan/duipan/hehun 会话开关档案化评估

### 核实结论
- **会话（chat）主链：已随 k11c**——handler 排盘工具按档案 solar_time
  取值（2112-2141 区域 `_solar_tool` 口径，档案 0 关/1 开默认开）。
- **paipan 页：已随**——k11c 起 _fillFromPerson 回显默认命主档案
  solar_time（`patch.bSolarTime = p.solar_time !== 0`）+ 页内 switch +
  payload solarTime 透传（R2-4-fix 单测锁线）。
- **hehun 页：未随（缺口确认）**——无真太阳时开关、无档案 solar_time
  回显；_buildPerson 载荷不带 solarTime → REST 走 BaziInput 服务端默认开。
  即档案显式「关」（solar_time=0）的用户在合盘页仍出修正盘，与档案口径
  不一致（k11c 后主链已统一、此处漏网）。修复方向=小改：合盘页补页级
  switch（同 paipan 交互）+ 默认命主档案回显 + payload solarTime 透传。
- **duipan 页：不涉及档案联动**——纯手动双时辰对比表单（hourA/hourB，
  无档案读取），REST 服务端默认开 = R2-4 产品默认口径，无档案违背问题；
  可选页级开关属未来增强非缺陷。

### 建议
下一小批做 hehun 页小改（复制 paipan k11c 接线模式 + node 接线测试 + UI
截图 QA）；duipan 不动；本批不改（UI 改动无真机/模拟器 QA 窗口）。

## 6. 实施中新增发现（留档）
- persons.js EMPTY_DRAFT 键（name/rel/cal/date/hourIndex…）与 data 实际
  字段（dName/dRel/dCal/…）命名不一致（main=86e0661 即有）→「编辑→返回→
  再添加命主」时旧草稿字段可能残留。本批未改（避免波及 g2 测试语义），
  已用 d 前缀同步命名新增字段（dClockMode/dClockH/dClockM 随 onAdd 正确
  复位）；建议下批核对修复并补 node 测试。
- bazi 页仍持一份独立旧 hour 映射（_hourToIndex 兼容旧序号）；本批显示
  预填已改走 utils persons 时钟口径，保存侧降级风险见 §3 留档。
- 读回口径把「旧序号 0-11 直存」残余按时钟读（见 §3）：值层面无法区分
  两义，如需对历史行精确迁移需产品拍板（列迁移/标志位二选一）。

## 7. 文件清单
- 新：scripts/migrate_stale_bazi_keys.py
- 新测试：tests/test_k19_migrate_stale_bazi.py、tests/test_k19_year_guard.py、
  tests/test_k19_minute_contract.py、miniprogram/tests/k19_minute_precision.test.js
- 改动：src/storage/person_dao.py（④-4 守卫+ctx）、src/api/user.py（哨兵）、
  src/bot/handler.py（birth_ctx 透传 3 点+_sync_person_profile）、
  src/api/birth_contract.py（normalize_hour 时钟声明）、src/api/hehun.py
  （BaziInput.birthClock + resolve 透传）、src/api/zhuanxiang.py（分钟透传）、
  miniprogram/utils/persons.js（时钟口径+分显示+HOUR24/MINUTE60）、
  miniprogram/utils/api.js（birthClock 透传）、pages/persons 与 pages/paipan
  的 js/wxml/wxss、miniprogram/tests/paipan_history_prefill.test.js（单测
  断言语义超集更新）
- 文档：本 plan；.superpowers/sdd/progress.md 追加本段

## 8. 测试数字（实跑）
- py 新测试 24 passed（9 迁移判定 + 9 年份守卫 + 6 分钟契约）
- python 邻接 214 passed（profile/g1/person_api/person_sync/k8/k11c/
  calendar_persons_read/chat_entry/bazi_solar_time/duipan/hehun+k6）
- node 新测试 12 passed；miniprogram 全量 281/281 passed
- 迁移脚本 CLI smoke（临时库）：dry-run 分拣/execute 备份+审计+幂等全通过

## 9. 红线与待确认
- git add 仅本批文件；data/ 预存脏态未 add；不 push 不重启不碰生产库
  不改 .env 不动 tool_calls.py；progress.md 已追加
- ⏳ **待用户确认**：(a) 迁移脚本对真实库执行（命令见 §1）；(b) ④-4
  拒绝位未来是否开启（YEAR_SHIFT_REJECT）；(c) §4 短期副作用收口批
  （写侧镜像 + 自愈免 bump）是否排期；(d) hehun 页 k11c 接线小改排期；
  (e) persons.js EMPTY_DRAFT 命名核对修复排期；(f) bazi/onboarding 页
  钟表档排期
