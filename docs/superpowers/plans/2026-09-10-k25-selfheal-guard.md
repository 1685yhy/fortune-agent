# 2026-09-10 k25-selfheal-guard：④-6 自愈收口（读路径去除写副作用 + 收敛前移写侧）

- 分支：k25-selfheal-guard（基线 main=1f38eab）
- 依据：`docs/superpowers/plans/2026-09-10-k19-profile-migration.md` §4「项 3：
  ④-6 读路径不写库评估」**短期项 (a)(b)**（唯一权威范围）
- 背景：users.bazi_info 是「默认档案」的 ② 兼容源；k8/k19 已收敛写形态，
  本批收口读路径写副作用，并把收敛时机从「读时」前移到「写时」
- 执行纪律：git add 只加本批；不 push 不重启不碰生产库不改 .env
  不动 tool_calls.py；pytest 只跑目标+邻接（勿全量）

## 0. 本批逐项结论速览

| # | 项（§4 短期项原文） | 结论 | 交付 |
|---|----|------|------|
| a | 读路径自愈回写改直接 SQL 镜像（绕开 `save_user_bazi` 的 consultation_count+1 与写守卫复算）；行缺失仍首建、count 落 DEFAULT 0 | 已做 | `person_dao.mirror_bazi_info_to_users` + `birth_profile.py` 回写点 |
| b | persons 写侧（`update_person` 默认行改动 / `create_person` 建档）补 bazi_info 8 键镜像漏斗，收敛前移到写时 | 已做 | `person_dao.create_person` / `update_person` |
| 配套 | payload 与读路径 out 同一实现（`bazi_info_of_person`）；`migrate_legacy_bazi` 必须 `mirror=False` | 已做 | `person_dao.py`（含反向路径豁免） |
| 长期 | **方案 B：persons 单表重构**（bazi_info 降级兼容视图/删列） | **本批不做**（需产品排期） | — |

## 1. 背景与现状（§4 摘录口径）

- 链是画像事实源统一出口：main.py / api advisor+calendar+hourly+dashboard+
  user+xuetang / bot handler / full 形态读取，消费点 10+ 文件 40+ 处，全部
  依赖「读=已收敛」语义。
- k8 前：读路径自愈（`get_user_birth_profile` W1）在 persons 命中且
  bazi_info 缺失/不一致时，经 `UserDAO.save_user_bazi` 全量重建 8 birth 键
  ——每次回写 `consultation_count+1`（dao.py:204/209）并触发
  `_guard_bazi_pillars` 复算。
- k11c F3 已示范直写镜像形态（persons 开关翻转 → 单键镜像 solar_time）。
- 历史事故族（21:44 脏写入 / 1995 年份污染）都出在这条链上 → 本批「收口读
  路径写副作用 + 收敛前移」即为 §4 短期项 (a)(b)。

## 2. 设计要点

### 2.1 单点镜像实现（配套要求的构造同构）

`person_dao.bazi_info_of_person(person)`（person_dao.py:124）——person 行 →
bazi_info payload，键集 = 8 birth 键 + k11c `solar_time`，缺省口径与
`_row_to_person`/`solar_time_on` 一致（hour/minute 沿用 0→None 折叠、city
空串、gender `unknown`、calendar 缺省 solar、solar_time 缺省开=1）。

三处共用同一实现 → 由**构造**保证同构，两库一致后读路径 stale 判定必为假：

| 消费处 | 位置 |
|---|---|
| 读返回 out | birth_profile.py:117（`out = bazi_info_of_person(pick)`） |
| 读路径自愈回写 payload | birth_profile.py:155-157（同一 `out`） |
| 写侧镜像 payload | person_dao.py:471 / 557-561 |

（原读路径内联构造的 dict 字面量删除，`_pick_solar_time` 保留给全量形态
`get_user_birth_profile_full`，未成死代码。）

### 2.2 (a) 读路径自愈回写改直写镜像

`person_dao.mirror_bazi_info_to_users(db_path, user_id, payload,
create_if_missing=False)`（person_dao.py:150）：

- 直接 SQL 读写密文行，**不经 `UserDAO.save_user_bazi`** → 绕开
  `consultation_count+1` 与 `_guard_bazi_pillars` 复算（payload 恒 birth
  形态无 bazi 四柱键，守卫对无 bazi 键写入本就不介入）；
- 行存在 → UPDATE 全量重建（k8 语义：旧行 bazi 四柱键等非 birth 键一律
  丢弃——四柱只属于 chart_records）；
- 行缺失 → `create_if_missing=True` 才 INSERT（读路径自愈首建契约保持），
  **不写 consultation_count** → 落库 DEFAULT 0（读不是咨询）；False 则
  no-op（写侧只镜像既有 ② 源，不代建档）；
- 失败仅 `logger.warning` 返回 False，绝不抛（调用方主流程不受阻）。

读路径对外语义不变：仍全量重建、仍首建缺失行、仍单向（不回写 persons）。

### 2.3 (b) 写侧镜像漏斗（触发条件）

- `create_person`（person_dao.py:465-472）：`mirror and created and
  is_default and created.get("birth_year")` → 建档即镜像（默认行 + 有出生年）。
- `update_person`（person_dao.py:557-561）：`updated.is_default and
  updated.get("birth_year") and (_birth_written or _default_set)` →
  默认行出生数据被改写 / 本行被提升为默认时即镜像。
  - `_birth_written`：本次真实写入 birth 键（含仅城市、仅开关）；
  - `_default_set`：本次显式携带 is_default（提升/降级）；生产
    「设为默认」端点走 `set_default()`（见 §6 遗留）。
- 二者均 `create_if_missing=False`：只镜像既有 ② 源行，不代建档。
- `add` 参数 `mirror: bool = True`（默认用户建档语义）。

### 2.4 非默认行隔离

漏斗条件含 `is_default` → 非默认命主（家人/朋友）改档**绝不**写 ② 源
（bazi_info 恒为默认档案镜像，不能被他人的出生数据覆盖）。既存测试
`test_k25_non_default_update_never_touches_bazi_info` 锁定。

### 2.5 k11c F3 块退位（语义不变）

`_solar_flipped and not _mirrored` 才走原 k11c 单键镜像块（person_dao.py:563）：
默认行已由漏斗全量覆盖（含 solar_time）→ 该块实际退为非默认行/无出生年行
的路径，行为语义与 k11c 一致。

### 2.6 迁移反向路径豁免

`migrate_legacy_bazi`（② 源 → person）调用 `create_person(..., mirror=False)`
（person_dao.py:667）——迁移若回写镜像会（a）丢旧行 bazi 四柱键、（b）把
solar_time 归一为生效值，破坏 k19 迁移脚本 dry-run「只分类不改数据」语义与
旧行判定证据。测试 `test_k25_migrate_legacy_does_not_rewrite_bazi_info` 锁定。

### 2.7 自审补口（本批收尾时新增，见 §5.3）

`update_person` 漏斗补 `updated.get("birth_year")` 条件：无出生年的默认行
（仅填真太阳时开关/仅改默认标记）镜像会写出全 None birth payload 覆盖 ② 源
既有档案——k11c 原块以 `_mdata.get("year")` 保护过同一场景，漏斗不得放宽；
此情形回落 k11c 块按旧语义处理（仅行存在且含出生年才单键镜像 solar_time）。
与 `create_person` 触发条件对齐（「默认行 + 有出生年」）。

## 3. §4 逐条核对（(a)(b) 对应表）

| §4 要求 | 实现证据 | 核对 |
|---|---|---|
| (a) 自愈回写改直接 SQL 镜像，绕开 save_user_bazi | birth_profile.py:155-157 改调 `mirror_bazi_info_to_users`；读路径对 `save_user_bazi` 零调用（grep 仅注释） | ✓ |
| (a) 绕开 `consultation_count+1` | 镜像不写 count 列（UPDATE 不含、INSERT 不带）→ 既有行不变、新行 DEFAULT 0 | ✓ |
| (a) 绕开 `_guard_bazi_pillars` 复算 | 不经 dao 漏斗；spy 断言 `{save:0, guard:0}` | ✓ |
| (a) 行缺失（persons-only）仍首建 | `create_if_missing=True`；用例 u3 首建 + count==0 | ✓ |
| (b) update_person 默认行改动即镜像 | person_dao.py:557-561（`_birth_written or _default_set`） | ✓ |
| (b) create_person 建档即镜像 | person_dao.py:469-471 | ✓ |
| (b) 收敛前移 → 读路径降为低频兜底 | 写时镜像后读路径 stale 判定为假 → 零写（用例 u6 spy mirror==0） | ✓ |
| 配套 payload 与读 out 同一实现 | 三处同调 `bazi_info_of_person`（§2.1 表） | ✓ |
| 配套 migrate_legacy_bazi mirror=False | person_dao.py:667 + 用例 u11（② 源含 bazi 键原样） | ✓ |
| 不越界：方案 B 单表重构 | 未做（单表/视图/删列零改动，只补 `mirror` 形参与镜像漏斗） | ✓ |

## 4. 测试清单与实跑数字

新测试 `tests/test_k25_selfheal_guard.py`（14 个用例函数 / 15 个 collected）：

| # | 用例 | 锁线 |
|---|---|---|
| 1 | read_heal_does_not_bump_consultation_count | 自愈仍发生但 count 零变化（旧=8≠7） |
| 2 | read_heal_skips_dao_save_and_write_guard | spy `{save:0, guard:0}` |
| 3 | read_heal_first_build_row_without_count | persons-only 首建 + count==0 |
| 4 | read_heal_payload_identical_to_person_mirror | 三处 payload 同构（读回==镜像==out 键集） |
| 5 | update_default_person_mirrors_at_write_time | 写时收敛（零读路径介入）+ 合并语义 + 不 bump |
| 6 | write_mirror_makes_read_heal_noop | 已收敛 → 读路径 mirror 调用数 0 |
| 7 | non_default_update_never_touches_bazi_info | 非默认行隔离 |
| 8 | default_person_without_birth_year_never_wipes_bazi_info | §2.7 补口回归锁（默认行无出生年 + 仅开关 → ② 源 year 保留、solar_time 单键镜像） |
| 9 | create_default_person_mirrors | 建档即镜像 |
| 10 | create_non_default_person_does_not_mirror | 非默认建档不写他人数据 |
| 11 | promotion_to_default_mirrors_promoted_person | 提升默认 → ② 源随默认换人 |
| 12 | migrate_legacy_does_not_rewrite_bazi_info | 反向路径豁免 |
| 13 | mirror_helper_row_missing_noop_and_no_create | 行缺失 no-op / 首建 count==0 |
| 14 | mirror_helper_failure_never_raises（×2） | db 不可用/不可序列化 → False 且不落半行 |

改动测试（随语义同步）：

- `tests/test_k11c_solar_switch.py`：F3 契约更新——默认行出生改写改走写侧
  漏斗（旧「非开关更新零镜像」作废）；建档即镜像（solar_time 落地）；仅名字
  更新仍零镜像；无 ② 源行跳过不崩。
- `tests/test_k19_migrate_e2e.py`：e2e 库按 k25 前写入顺序**显式构造**历史
  分裂行（persons 建好后直写脏 bazi_info）——k25 写侧漏斗会即时收敛，不能再
  依赖旧代码路径产生分裂态。

### 实跑（2026-09-11 收尾复跑，最终提交内容）

```
# 本批三件套
pytest tests/test_k25_selfheal_guard.py tests/test_k11c_solar_switch.py \
       tests/test_k19_migrate_e2e.py -q
→ 38 passed, 1 warning in 48.59s   （k25 15 + k11c 16 + k19 e2e 7）
   唯一 warning = 既有 starlette/httpx StarletteDeprecationWarning（非本批）

# 邻接族（23 文件；补口修复后复跑）
→ 377 passed, 1 warning in 225.32s (0:03:45)（同上唯一既有 warning）
（k8 / k18 / k19 迁移+守卫+分钟契约 / g1×2 / person_sync / profile_consistency /
 calendar_persons_read / chat_entry / bazi_solar_time / bazi_r24_default_on /
 k17 / r13 / bazi_archive_priority / partial_birth / lunar_convert /
 duipan / hehun×3 / chenggu_k6）

# 补口区分度验证（临时还原 guard 单跑该用例 → 必失败）
→ 1 failed, 14 deselected（② 源 year 被空 payload 清空，断言 1999 失败）
   随后逐字节还原，复跑全绿
```

未跑全量（控制方统一跑，约 33 分钟）；邻接族数量 23 文件为自行 `ls tests/ |
grep` 取全（person/画像/迁移/守卫/开关族）。

## 5. 文件清单

- 改：`src/storage/person_dao.py`
  - 新 `bazi_info_of_person`（124）/ `mirror_bazi_info_to_users`（150）
  - `create_person` 增 `mirror=True` 形参 + 建档镜像（415-472）
  - `update_person` 增 `_birth_written`/`_default_set` 与写侧漏斗（505-561）
  - k11c F3 块退位（563）、`migrate_legacy_bazi` `mirror=False`（667）
- 改：`src/storage/birth_profile.py`
  - 读路径 out 改单点实现（117）；自愈回写改镜像（155-157）；docstring
    契约同步（65-70、75 及 `get_user_birth_profile_full` 的 dao 接口行）
- 新测试：`tests/test_k25_selfheal_guard.py`
- 改测试：`tests/test_k11c_solar_switch.py`、`tests/test_k19_migrate_e2e.py`
- 文档：本 plan

## 6. 纪律与遗留

### 纪律

- git add 仅本批 4 改 + 1 新测试 + 本 plan；`data/`（`data/memory/.json`、
  `data/eval/*`）与 `docs/superpowers/plans/2026-08-26-batch2-*.md` 等历史未
  跟踪文件**未 add**；不 push、不重启服务（8767/8768 线上运行中）、不碰生产
  库、不改 .env、不动 `tool_calls.py`、不 rsync、不复制到运行副本
  `/home/a/fortune-run`。

### 遗留 / 不确定项（交控制方与产品）

1. **`set_default()` 未接入漏斗**（person_dao.py:614）：生产
   `/api/persons/{id}/default` 走 `set_default`（直写 SQL），默认换人后 ② 源
   需等下一次读路径自愈（低频、不再 bump count）。§4 权威只列
   `update_person`/`create_person`，未顺手扩面；建议下批与「提升默认」统一。
2. `update_person(is_default=True)` 生产无调用方（表单/对话只传 name/relation/
   birth）→ `_default_set` 分支目前仅测试路径覆盖。
3. `users.consultation_count` 全仓无读取方（grep：仅 models.py 建列、dao.py
   自增）→ (a) 去掉读路径 +1 无消费方回归面；但历史统计口径若有外部离线
   报表消费该列，需知悉计数将少一条来源。
4. 读路径自愈仍为兜底（历史分裂/外部直写场景），未做「纯读化 + 显式 heal」
   中间态（§4 明确不做：调用面 40+ 点漏 heal 回归面大）。
5. 方案 B（persons 单表事实源 + bazi_info 降级/删除）仍待产品排期。
