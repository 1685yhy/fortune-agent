# k9 R2-5/R2-6 遗留 Minor 收敛批（分支 k9-r2-minors，base main=2ac1fc5）

日期：2026-09-06
批次：7 项逐项核实现状 → 只做真残留；TDD 锁定；照惯例不写生产库/不改 .env/不 push/不重启/不碰 tool_calls.py。

---

## 逐项核实结论（现状证据）

### 1. format_birth_line 直渲染矛盾 —— 真残留，做
- 证据：`src/bot/handler.py:3298`（_compress_history）与 `:3436`（_collect_key_facts）
  直接 `format_birth_line(saved)`，saved 来自 `_get_user_birth_profile`（persons-first，k8）
  → lunar 档案载**农历原始 y/m/d + calendar='lunar'**，渲染「出生:1999年3月28日…」无任何
  农历标注进 LLM 上下文；而同上下文画像层行（17e0961 后）写引擎实收公历 5/13 →
  同框矛盾（R2-6 遗留①所述，自 R2-5 起）。
- `format_birth_line` 定义在 `src/memory/user_memory.py:49`（无 calendar 语义）；三个消费点：
  handler 3298 / 3436（dao 源=可能 lunar 原始）、user_memory:891 get_profile_summary（记忆层
  bazi_info=公历值无 calendar 键）。既有显示口径先例：`_fmt_birth_text`（handler:2899）lunar →
  转公历文本，转失败回落原值+warning。
- 设计：在唯一渲染函数 format_birth_line 内做 calendar 语义（单点，全消费方同口径）：
  - calendar=='lunar' 且 to_solar_date 成功 → 日期段渲染公历（与画像公历行一致）；
  - lunar 且转换失败（非法农历日）→ 保留原始日期并加「农历」前缀（值仍是农历时按需标注，
    绝不冒充公历）；
  - solar / 无 calendar 键（记忆层所有行）→ 逐字节不变（零回退）。
  - 本地 import to_solar_date（user_memory 目前无 storage 顶层依赖，避免环）。
- 不做：不给成功转换追加「（农历…）」后缀（口径跟随 _fmt_birth_text / 画像公历行：
  纯公历，避免 LLM 混淆；用户原述在会话历史可见，档案直读 _q_档案 保留农历标注是用户侧）。

### 2. lunarDateToSolar 三处重复 —— 真残留（位置=miniprogram JS，非 src/utils py），做
- 证据：`miniprogram/pages/paipan/paipan.js:57`、`duipan/duipan.js:22`、`hehun/hehun.js:21`
  三个逐字节相同（仅注释差异）的本地 `lunarDateToSolar(dateStr)`；共享转换原语
  `lunar2solar` 已在 `miniprogram/utils/lunar.js:168` 并导出（:280），三页各自已 require。
- 设计：helper 收敛进 `miniprogram/utils/lunar.js`（紧邻 lunar2solar 并导出 lunarDateToSolar），
  三页删除本地定义、改为模块级 `const lunarDateToSolar = lunar.lunarDateToSolar;`
  调用点（paipan.js:170/250、duipan.js:114、hehun.js:207/278）与函数体逐字节不变。
- 测试：JS 测试锁定（node --test，mock Page/wx 纯 node 可跑）——
  既有 `miniprogram/tests/paipan_lunar_date.test.js` 一条源锚正则
  （paipan.js 内 `const parts = String(date || '').split('-');`）随迁移改指向 utils/lunar.js；
  新增 `miniprogram/tests/lunar_date_util.test.js`：utils 导出函数行为直测（1999-03-28 →
  1999-05-13 公历串；非法农历日回落原文；空输入回落；与三个页面 import 接线）。
- 不做：不合并后端 python 三处——核实现状 paipan/duipan/hehun 三个 API 已共享
  `hehun._resolve_person`/`_resolve` 校验（src/api/hehun.py:158、paipan.py 复用 BaziInput、
  duipan.py 自实现同口径 _resolve），无 python 侧转换重复（转换仅前端发生；R2-5 495722f 已单点化）。

### 3. 记忆层写侧 calendar 标记 —— 已闭环（17e0961+R2-5），记录 + 补锁定测试
- 证据：记忆层写侧仅两处：
  - handler `:2129-2150`（_tool_bazi 尾）：写入 year/month/day = **引擎实收公历**
    （上方 2061-2078 lunar 档案单点 to_solar_date 转换后才进引擎/写画像）；
  - handler `:5997/6005`（_save_bazi_records）：17e0961 已加写前单点 to_solar_date。
  两处都写**转换后公历值、不带 calendar 键** —— 这正是本仓记忆层既定约定：
  记忆 bazi_info/L3 是 LLM 上下文消费方、恒公历口径（缺省 solar），calendar 标记只属于
  存储层原始值（dao bazi_info/persons/chart 均保留原值+标记，测试已锁）。
- 遗留测试盲区：_tool_bazi 系测试（test_lunar_birth_solar_convert.py）装配
  `h.memory_system = None` → 记忆层写侧从未被直接测试。补锁定测试（真 UserMemory 装配）：
  lunar 档案走 _tool_bazi → mem bazi_info == 公历 5/13 且 calendar != lunar、L3 profile
  条目含「1999年5月13日」不含「1999年3月28日」；solar 对照零变化。
- 不改生产代码。

### 4. handler ~4679 残余农历直喂 —— 已闭环（R2-6 Gap B），记录，无需改
- 证据：现版 `_handle_bazi`（handler:4707-4909）全部路径已归一：
  三方 partial 路径 4757-4770、B1 档案+消息合并 4818-4822、F2 渐进 4829-4841、
  B2 档案兜底 4847-4856 —— 全部经 `_feed_birth`（:4943，_solarize_birth 单点转公历，
  arch_raw 原值+标记落库）；parsed 完整消息（B3）4905 直排——解析层 `_extract_bazi_info`
  （5058+）本就转公历（lunar-python，is_lunar 分支）。`_solarize_birth` :4917 语义已文档化。
  行为由 tests/test_bazi_residual_paths.py B1/B2/F2/B3 + 单测锁定。
- 新增缺口只剩 item 6 的跨年边角（B1 solar 档案 + 农历月日覆写跨年），并入 item 6。

### 5. 扩展点无行为测试 —— 部分真残留，补
- 现状：`_should_fastpath` 有 tests/test_fastpath.py 6 条（无 lunar-calendar 语义用例：
  chart 行 lunar 原始值 vs 公历生辰比对，R2-6 的 _solarize_birth 比对逻辑无测试）；
  `_handle_advisor`（:7328）、`_handle_hourly`（:7522）、`_map_user_bazi_for_zeri`（:3146）
  在 tests/ 下零直接行为测试。
- 设计：tests/test_k9_r2_minors.py 补直接行为测试（object.__new__ + 临时库真实 DAO/persons/
  chart/UserMemory + 记录式真实引擎包裹 Mock 引擎，零 LLM/零网络）：
  - _map_user_bazi_for_zeri：persons-only lunar 档案无 bazi → 引擎实收公历 (1999,5,13,9,0)
    + 映射键 shengxiao(兔)/day_gan(乙)/month_zhi(巳)/wuxing；跨年 lunar 2000-12-26 →
    引擎实收 (2001,1,20)（shengxiao 仍按立春前年支=龙）；solar 档案零转换；无档案 → None。
  - _handle_advisor：无档案固定引导文案；lunar 档案 → 引擎实收公历；monkeypatch
    AdaptiveAdvisor → 结构化建议回复拼装；引擎异常/建议异常 fail-open 文案。
  - _handle_hourly：无档案引导文案；persons-only lunar 档案缺四柱 → 引擎补齐实收公历、
    llm=None 确定性卡片回复不崩。
  - _should_fastpath：chart 行 birth lunar(1999-3-28,calendar lunar) vs 生辰 dict 公历
    (1999,5,13) → True（R2-6 solarize 比对语义）；chart lunar 与不同公历 → 回落到 dao 判定。

### 6. B1 solar 档案+农历月日覆写跨年边角 —— 未测，补测试（预期不暴露 bug，规则锁定）
- 现状语义（B1 合并分支 handler:4788-4790）：cur 覆写月日 → merged.calendar 随覆写语义
  （_md_lunar → lunar）；年份沿用档案（solar 档案的公元年按**农历年**语义进
  Lunar.fromYmd 换算——与 D7 解析层/to_solar_date 同约定）。
- 测试：solar 档案 1999-05-13 9:00 + 消息「我是农历腊月廿六出生的」
  （_extract_partial_birth → {month:12, day:26, _md_lunar:True}，已实测）→ 合并
  (1999,12,26,lunar) → to_solar_date = **2000-02-01**（跨年！）→ 引擎实收 (2000,2,1,9,0)，
  四柱年柱仍己卯（立春 2000-02-04 前）；落库三处保留 1999-12-26 + calendar lunar（arch_raw），
  chart 盘 = 公历盘（己丑日）。数值已实测：lunar(1999,12,26)→solar 2000-02-01，
  calc(2000,2,1,9,0,'长春','男').bazi=['己卯','丁丑','己丑','己巳']。
- 若测试暴露真 bug（如 merged.calendar 未随覆写置 lunar → 直喂 12/26 当公历）→ 修；预期已闭环。

### 7. 测试注释锚定 —— 真残留（纯注释+校准断言），做
- 现状：test_bazi_residual_paths.py NORTH_STAR 常量（:41，hour=11 →「2年4月22天0时起运」）
  与 B2/F2（hour=9）用例相邻但无两者关系注释；「9 点 → 12天0时 vs 11 点 → 22天0时」
  无任何文件出现（实测引擎：hour=9 →「出生后2年4月12天0时起运」、hour=11 →「…22天0时起运」，
  同日同月同「2年4月」前缀、天数恰差 10 天——真太阳时修正后两时辰的正常差异，非舍入）。
- 设计：在 test_bazi_residual_paths.py NORTH_STAR 常量处 + B1 用例区补注释（两数均为问真逐字
  各自验证，勿互相「修齐」）；k9 测试文件头部做两条校准断言把关系锁进测试。

---

## 改动文件清单

| 文件 | 动作 |
|---|---|
| src/memory/user_memory.py | format_birth_line 加 calendar 语义（改） |
| miniprogram/utils/lunar.js | +lunarDateToSolar 导出（改） |
| miniprogram/pages/paipan/paipan.js | 删本地 def → 引 utils（改） |
| miniprogram/pages/duipan/duipan.js | 同上（改） |
| miniprogram/pages/hehun/hehun.js | 同上（改） |
| miniprogram/tests/paipan_lunar_date.test.js | 源锚正则随迁移调整（改） |
| miniprogram/tests/lunar_date_util.test.js | 新增（node --test） |
| tests/test_k9_r2_minors.py | 新增（items 1/3锁/5/6/7） |
| tests/test_bazi_residual_paths.py | 注释锚定（改，仅注释） |
| docs/superpowers/plans/2026-09-06-k9-r2-minors-plan.md | 本计划（新增） |
| .superpowers/sdd/progress.md | 末尾追加 k9 段（改，完成时） |

不做项：item 3/4 生产代码零改动（已闭环，证据如上）；不合并 python 侧（无残留）；
不补「成功转换+农历后缀」标注（口径跟随既有显示函数）；不碰 tool_calls/支付/鉴权。

## 测试清单与运行

- 新增：`tests/test_k9_r2_minors.py`（约 18-22 条，全部离线确定性：真实 BaziEngine 毫秒级 +
  记录包裹，或 Mock 引擎；临时库；真 UserMemory tmp 目录）
- 回归 cluster（单进程一次性跑，共用一次 import）：
  tests/test_k9_r2_minors.py tests/test_bazi_residual_paths.py tests/test_fastpath.py
  tests/test_lunar_birth_solar_convert.py tests/test_partial_birth.py
  tests/test_bazi_archive_priority.py tests/test_calendar_persons_read.py
  tests/test_bazi_geo_city_match.py tests/test_bazi_jiaoyun.py
  命令：`cd /mnt/e/fortune-agent-deploy && OMP_NUM_THREADS=4 /home/a/fortune-agent/.venv/bin/python -m pytest <上述文件> -q -p no:cacheprovider`（import 冷启动 1-2 分钟正常；不跑全量）
- JS：`cd miniprogram && node --test tests/paipan_lunar_date.test.js tests/lunar_date_util.test.js`
  及就近相关（paipan_history_prefill.test.js 引用 lunarDateToSolar 正则——确认不受影响）

## 执行结果补记（TDD 实测暴露项）
- item 5 行为测试实测暴露 _handle_advisor 一个真缺陷：引擎实参不归一化——dao
  bazi_info 行 minute 缺省（时辰建档，None）直喂 BaziEngine → TypeError → 用户
  拿到「命盘重新计算失败」而非建议（同类消费方 _feed_birth/_map_user_bazi_for_zeri
  均归一化 0/缺省值）。已在 handler.py _handle_advisor 引擎调用处补归一化
  （int(hour or 0)/int(minute or 0)/city ""/gender "unknown" 显式化），零行为回退
  （正常行值幂等），4 条 advisor 行为测试锁定（含 None minute 流）。
- item 1/3/4/6 实现后无额外异常；item 6 未暴露 bug（规则行为与 D7/to_solar_date
  同约定，测试锁定）。

## 提交
- 单提交 `fix: k9 R2-5/R2-6 遗留 minors 收敛 — format_birth_line 公历口径 + JS lunarDateToSolar
  单点 + 扩展点行为测试 + B1 跨年边角 + 注释锚定 (k9)`
- 只 git add 本批文件（data/eval/results/ledger.json、data/memory/.json、
  src/engine/out/comparison_runs.jsonl 及 untracked data/eval/results/* 预存在脏态禁止提交）
