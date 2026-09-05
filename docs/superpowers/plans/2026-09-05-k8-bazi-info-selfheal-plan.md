# 2026-09-05 k8-bazi-info-selfheal：画像层 bazi_info 一致性修复批

- 分支：k8-bazi-info-selfheal（基线 main=b0ffcf6）
- 根因报告：/tmp/fortune-bazi_info-rootcause-20260905.md（21:44 事故 = W1 G1 自愈「保留旧行 bazi 键 + 只对齐 birth 键」把 persons(当时错误 1995) 抄进 bazi_info，并把 08-16 污染的他人 2026-08-18 盘四柱原样带到 09-04）
- 依据：数据一致性铁律（同一数据一个事实源）；四柱只属于 chart_records；persons = 出生档案单一事实源

## 范围（④ 修复建议 1/2/3）

### A. W1 自愈改造（核心，src/storage/birth_profile.py:89-121）
- 读路径自愈判定条件不变（persons 命中 + bazi_info 缺失/8 个 birth 键不一致 → stale）。
- **合并语义改为全量重建**：stale 时 `new_info` = persons 8 个 birth 键全量
  （year/month/day/hour/minute/city/gender/calendar，calendar 缺省 solar），
  不再 `dict(bazi or {})` 起步 → **旧行 bazi 四柱键等一切非 birth 键被丢弃**，
  bazi 键不再写回 bazi_info（四柱只属于 chart_records）。
- 0/None/空 等价归一与 calendar 缺省 solar 的比较逻辑保留（R1-3/R2-5 口径，防误触发与收敛）。
- 不一致重建（旧行存在且与 persons 不同 / 旧行含 bazi 键）→ `logger.warning` 含 user_id
  与 birth 摘要，便于复盘；bazi_info 完全缺失的首建 → `logger.info`。
- 保留 persons 优先读取与调用方契约（out 结构 8 键不变；③ chart 兜底与 ② 路径不动）。

### B. dao 层一致性守卫（单一漏斗，src/storage/dao.py save_user_bazi）
- 写入口若**同时**含出生 year/month/day 与 bazi 四柱键 → 用 BaziEngine 按
  (year,month,day,hour,minute,city,gender) 复算四柱并与待写 bazi 比对：
  - calendar=lunar 时先经 `to_solar_date` 转公历再复算（与 R2-5/R2-6 写路径同口径——
    W2/W3 落库保留原始农历 y/m/d+标记，四柱按转公历算）；
  - 复算 = 纯规则零 LLM，实测单次 ~26ms（BaziEngine import 0.1s 惰性一次）；
  - 不一致 → 丢弃 bazi 键 + `logger.warning`（含 user_id、待写 birth、两套四柱）；
  - 复算异常/形状不可比（非 4 柱串）→ fail-open 保留 + warning，绝不阻断写入。
- 引擎调用方式与耗时已实测确认：`src.engines.bazi.BaziEngine().calculate` 纯本地规则，
  默认 solar_time=True（R2-4 产品口径），handler W2/W3 均不带额外 flag → 复算默认
  与写路径同参，不会误伤正常排盘落库（合法性由测试 3 守护：一致 → 原样通过）。
  不需要退化为干支年份轻量校验。
- ⚠️ tool_calls.py / bot 主链零改动；守卫只挂在 UserDAO.save_user_bazi 漏斗。

### C. 显示/维护层只读 persons（四柱只读 chart_records）
- 新增 `get_user_birth_profile_full(dao, user_id, chart_dao=None)`（birth_profile.py）：
  birth 8 键 = get_user_birth_profile（persons 优先 + A 自愈）；bazi 及盘面扩展键
  （day_master 等）只取自 **出生档案匹配**（y/m/d + calendar + hour(0≡None) 归一）
  的最近 chart_records 行；无匹配 → 无 bazi 键。bazi_info 的 bazi 键不再被消费。
- 改读点（均为低改造成本）：
  1. `src/api/user.py` GET /api/user/profile —— pdao 兜底分支 bazi_info 改经
     get_user_birth_profile（persons 优先链）；bazi_label/has_bazi 改读
     profile_full 的匹配 chart 四柱（原直读 users.bazi_info.bazi，事故显示源家族）。
  2. `src/main.py` 报告页 —— /api/reports 基础命书兜底、/api/reports/base 详情、
     幸运色派生（_derive_lucky_refs 输入）改经 profile_full；无匹配盘 →
     「尚未排盘」/幸运参考模式（不再消费旧 bazi_info.bazi）。
  3. `src/api/xuetang.py` lesson —— 个性化示例 bazi_data 改经 profile_full（四柱
     齐才个性化，否则通用课程）。

### D. 文档
- 本 plan 文档（范围/设计/取舍/测试清单/后续项）。
- 运行纪律：DEPLOY.md 补「切 FORTUNE_DB_PATH 禁止裸切换」条款（persons 默认命主
  指纹核对）。progress.md 末尾追加 k8 段落。

## 设计取舍说明
1. bazi_info 行内 bazi 键 → 消费侧一律弃用 + 写侧守卫删除 + persons 读侧自愈重建。
   残留 bazi_info-only（无 persons、无 chart）旧行的 bazi 键：写侧守卫只在下次
   带 bazi 写入时清理；显示侧（C）已不再读取 → 不再构成污染显示面。
2. 「persons 存在但 bazi_info 缺 birth_year 之外的异常键」仍只重建 8 键
   （不写 day_master/geju 等 → 这些只属 chart_records）。
3. 引擎复算耗时 ~26ms + 惰性单例 import，无重依赖；不做年份轻量校验降级。
4. 匹配 chart 过滤 subject=other/择时盘：chart birth y/m/d/calendar/hour 与档案
   一致才取（08-16 污染盘的 chart 亦因 birth 不同被滤除）；gender 不影响四柱故不参与匹配。

## 测试清单（tests/test_k8_bazi_info_consistency.py，隔离 tmp 库）
1. 21:44 事故复现：persons=1995-03-28 9点 男 长春 + bazi_info 含他人盘四柱
   （丙午丙申甲子甲子=2026-08-18 0点 实盘）→ 读 → bazi_info 以 persons 重建、
   **无 bazi 键残留**、warning 含 user_id、persons 未动。
2. persons=1999 正确 + bazi_info 旧 1995 脏值 → 读后 bazi_info 对齐 1999 且收敛。
3. dao 守卫：birth 键与 bazi 四柱矛盾（不同年份盘）→ bazi 被丢弃 + warning；
   birth 与 bazi 一致（真实引擎盘）→ 原样通过不误伤。
4. lunar 写路径守卫：lunar birth 键 + 按转公历复算的四柱 → 不误伤（R2-5 口径）。
5. profile_full：persons + 匹配 chart → 四柱齐；persons + 不匹配 chart（他人盘）→
   无 bazi 键；bazi_info-only 旧行 → birth 兜底且不带 bazi 键消费。
6. 回归：test_partial_birth.py / test_g1_gender_contract.py / test_calendar_persons_read.py /
   test_profile_consistency.py / test_lunar_birth_solar_convert.py（self-heal 语义
   变更点：u1「保留 bazi 键」断言 → 改为「无 bazi 键」，见下）。

## 存量测试语义变更（随本批，需同步）
- tests/test_g1_gender_contract.py test_g1_profile_persons_first_when_bazi_info_stale：
  原断言「既有 bazi 键保留」→ 新契约「bazi 键被丢弃重建」（202-220）。
- tests/test_calendar_persons_read.py 同款 u1 用例（120-136）。
其余 u2/u3/u4/u-p6（lunar 标记回写、收敛、persons 无数据不覆盖）语义不变应保持绿。

## 后续项（本批不做，原因）
- 报告 ④-4（persons 写入侧年份合理性守卫，需产品口径拍板）。
- 报告 ④-6（读路径不写库：get_user_birth_profile 纯读化 + 显式自愈接口）——
  W1 自愈写库与 #82 授权矛盾面需单独批次评估；本批已把写形态收敛为全量重建。
- 直读 bazi_info 的深层 LLM/引擎消费点（chat 问候 presence 1243、_map_user_bazi_for_zeri
  3163、_handle_advisor 7331、解梦日主 7187、学堂 7295、_has_saved gate 5554/7860、
  memory 层 save_bazi_info、legacy /api/calendar/daily+week / share-card / dashboard /
  api/hourly-fortune / api/advisor）—— 多数需要「birth→引擎实参归一」（lunar 单点
  转公历 + hour/minute or 0）或 day-master 兜底语义重设计 + 评测验证，非显示/维护
  事故面；C 显示层改造后 bazi_info.bazi 已无消费，深层点语义缺口由 B 守卫 + A 自愈
  兜底收敛，单独批次处理（带评测）。
- bazi_info-only 存量旧行（无 persons）历史 bazi 键的主动清理脚本/迁移。
- save_user_bazi 副作用 `consultation_count+1`（自愈写库会 bump 咨询数）——与 ④-6
  同批评估。
