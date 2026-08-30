# 易理明灯 Agent 评测集标注规范（E1 评估集）

- 日期：2026-08-31
- 批次：E1 评估集建设（Agent 评测体系第一批）
- 数据文件：`data/eval/agent_tasks.jsonl`（100 条，JSONL 每行一条，本文件是**唯一事实源**）
- 校验器：`scripts/eval_agent/validate_tasks.py`（纯标准库，退出码 0/1/2）
- 方案事实源：`docs/superpowers/specs/2026-08-30-agent-eval-system-design.md` 第四章（4.1-4.6）
- 分支：`e1-eval-set`（BASE=901d355），交付 commit 只含本批 3 个目标文件

---

## 1. 背景与范围

E 系列评测体系第一块地基：100 条评估集任务 + schema 校验器。后续 E2-E7（执行器/判卷器/报告器）全部读取 `agent_tasks.jsonl`，因此**本批只建数据与校验工具，不写任何消费代码**（数据一致性铁律：唯一事实源）。

本批无生产代码改动：`src/bot/tool_calls.py` 零改动、接口契约零破坏、key 零落盘、零新增依赖。

## 2. 任务 schema（4.2/4.3 落地）

每条任务包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 唯一，T001-T100 |
| `title` | string | 任务标题 |
| `category` | string | 10 域之一：paipan/fortune/zeri/hehun/xingming/qian/liuyao/ziwei/chat/edge |
| `severity` | string | P0/P1/P2；P0 为崩溃级或数据错误，pass_k 固定 3（三判一致才过），其余 1 |
| `pass_k` | int | P0=3，P1/P2=1（校验器强制） |
| `source` | string | scenario-migrate / real-log / manual / bug-<编号>，无来源不入库 |
| `setup` | object | 种子契约（见 §3），可为空对象 |
| `turns` | array | 对话轮次，每轮 `{role: user/assistant, text}`，非空 |
| `expected_tools` | array | 期望工具调用（顺序敏感），每项 `{name, params, match}` |
| `no_tool` | bool | true=期望零工具调用（反例桶）；true 时 expected_tools 必须为空数组（校验器强制） |
| `state_checks` | object | 业务状态断言：`<表>_created/_unchanged/_equals`（persons/chat/favorites/memories 等业务表），只断言业务状态不断言内部实现 |
| `reply_checks` | object | `contains`（必须含全部）/ `neg_checks`（禁止含任一）/ `regex`（任一命中）/ `min_len`；`neg_checks` 每条必须含 `{`/`undefined`/`NaN`/`null` 四占位符（假数据/空壳守卫，校验器强制） |
| `judge_hint` | string | L3 判卷提示（客观题判卷要点），不写入回复 |

### 2.1 `match` 三档语义（4.3）

- `exact`：实际工具参数与 `params` 全等
- `partial`：`params` 中声明的字段全部出现在实际参数中（实际可多出）
- `any`：只查工具名（参数可由消息/档案动态补全，不锁值）

### 2.2 L1 契约（本批执行的工具期望口径）

工具注册表以 `src/bot/capability_registry.py` 为事实源（12 工具：bazi_chart/quote_rag/web_search/dream/fengshui/zeri/record_lookup/hehun/naming/fortune_cycle/career_dir/num_omen）。方案 4.2 示例中的 `fortune_query` 等名称不存在于真实注册表，本批一律用真实名。

- **意图引擎域**（排盘/紫微/六爻/解梦/择日引擎/存量直读/运势分析）：对话链确定性派发，**无 `<tool_call>` 工单**，`expected_tools=[]` 且 `no_tool=false` 表示"零工具调用（引擎路径契约）"——L1 按零工具调用断言。
- **工具域**（合婚/起名/流年流月/择业/数字吉凶）：场景词门控（`message_analyzer.TOOL_SCENE_WORDS`）确定性命中工具，断言真实工具名。
- 例外记录：`zeri` 意图路径（含 4 位年份日期锚点）由 handler 直接进引擎，不产生工具工单（D5 修复事实），故择日任务 expected_tools 为空。

### 2.3 链路任务判定（4.4，校验器内置）

`turns>=2` 且会话内含四步：**建档**（setup.persons/chart_records 种子，或任一轮含年份/出生）→ **排盘**（排.*盘/我的盘/八字/命盘）→ **测算**（运势/流年/择日/灵签/合婚/摇卦/紫微等）→ **收藏**（收藏）。本批链路 6 条：T013/T014/T026/T027/T038/T057。

## 3. setup 种子契约

校验器只放行以下键；**新增键必须先同步本文档再入库**。

| 键 | 结构 | 说明 |
|---|---|---|
| `persons` | 数组 `{name, gender: male/female, birth: "YYYY-MM-DD HH:MM", city}` | 已建档用户（默认命主） |
| `favorites` | 数组 `{person_id, kind: chat/jian/qian, title}` | 收藏内容 |
| `qian_saves` | 数组 `{no}` | 已抽灵签（只存签号+时间；签诗/吉凶按 QIAN_BY_NO 反查——D4 修复事实） |
| `zeri_plans` | 数组 `{scene, date: "YYYY-MM-DD"}` | 择吉计划（存量直读数据源；"吉日"触发） |
| `chart_records` | 数组 `{bazi: 四柱数组, day_master, geju, dayun, liunian, shensha}` | 已排盘记录（重看盘直读数据源） |
| `membership` | 对象 `{queries_used, queries_limit}` | 会员档位/额度（档位直读 + 非聊天功能额度门数据源） |
| `chat_quota` | 对象 `{used, limit}` | 免费对话额度（15 条/日，超限降级续聊数据源） |

## 4. 标注规范（4.5 落地，违反即返工）

1. **`expected_tools` 以真实用户意图为准，不以当前实现为准**——实现错了=评测抓 bug，不是改评测迁就实现。
2. **`no_tool=true` 任务输入不含任何测算意图**（闲聊/问天气/夸赞/无关问题/攻击/乱码/空输入）。
3. **`reply_checks.neg_checks` 每条含 `{`/`undefined`/`NaN`/`null` 四件套**（校验器强制）。
4. **`state_checks` 只断言业务状态**（persons/chat/favorites/memories 等表），不断言内部实现。
5. **每条必须有 source**——无来源不入库（校验器强制前缀）。
6. **全量过校验器（exit 0）才能提交**。

执行补充（本批落地口径，来自实现事实调研）：

- **直读/快路径不写 chat 历史**：重看盘直读/收藏直读/晨笺直读/会员直读/择吉直读等 0-LLM 路径无 chat 落库——凡此类任务**不得**断言 `chat_created`。
- **主链才写 chat**：LLM 分析路径（运势/排盘/解梦/六爻/合婚/紫微/闲聊）写 user+assistant 两行。
- **chat 六爻只支持自动摇卦**（cast(method="random")），手动爻象是端上页面能力，对话侧无入口——六爻域以自动摇卦变体覆盖（T058-T063，judge_hint 注明）。
- **会员单字/支付词分支**：`会员` 精确词 → 升级卡（19.9 元/月）且**显式落 chat**；支付守卫词（充/领/多少钱等全集）→ 支付引导卡，不得被档位直读劫持——支付意图任务断言 `memberships_unchanged`（守卫：不产生记录）。
- **择日日期种子用未来日期**：原 qa 场景部分日期（如 2026-08-20 开业）已过期，迁移时改为未来日期（2026-09-20 等）避免过期语义（T029 注释）。
- **起运/晚子时锚点**（G5/K2）：王芳 1993-05-08 14:30 西安 女 → 9岁5个月起运；李明 1990-01-01 23:40 北京 男 → 己巳 丙子 丁卯 庚子、8岁6个月起运、晚子时次日口径（T015/T065/T066）。
- **合婚 K6 案例**：H3 癸巳×戊申 60→66 分 上等婚配（T042/T043 判卷提示用）。

## 5. 覆盖矩阵（实际分布，100 条全绿）

| 域 | 目标 | 实际 | 内容 |
|---|---|---|---|
| paipan | 12 | 15 | 建档/对话排盘/纠正时辰/性别纠正/渐进累积/引导/落库/第三方/链路 |
| fortune | 12 | 12 | 周/月/年运势/缓存命中/收藏查询/晨笺/复合问句/婚姻/链路 |
| zeri | 10 | 11 | 嫁娶/搬家/开业/出行/提车/晋升/多日推荐/排除规则/直读/引导 |
| hehun | 6 | 6 | 双档/单档补全/场景词门控/缺信息引导/文案矛盾/五合吉判 |
| xingming | 6 | 6 | 带生辰/无生辰/改名/姓名分析/康熙笔画/词表误伤 |
| qian | 6 | 7 | 摇签/指定签/直读/名笺/灯语/签文释义/链路 |
| liuyao | 6 | 6 | 自动摇卦 6 变体（事业/换工作/财运/复合/健康/缺问题兜底） |
| ziwei | 6 | 6 | 盘查询/晚子时/星曜落宫/缺生辰引导/口语变体/婚姻宫 |
| chat | 10 | 11 | no_tool 5（闲聊/倾诉/天气/夸赞/无关）+ 引导边界 6（建档引导/会员/续费/额度/多轮） |
| edge | 16 | 20 | 空输入/乱码/SQL 注入/超长/未知意图/额度门/支付守卫/改城市重算/性别链路/明天追问/明确重排/emoji/工具边界/G4 三连 |
| no_tool=true | ≥10 | **12** | T049/T070-074/T081-085/T093 |
| 链路任务 | ≥5 | **6** | T013/T014/T026/T027/T038/T057 |
| **合计** | 100 | **100** | |

## 6. 100 条任务清单（台账）

| id | category | severity | source | title |
|---|---|---|---|---|
| T001 | paipan | P0 | scenario-migrate | 对话排盘完整生辰 |
| T002 | paipan | P0 | real-log | 纯生日陈述快路径排盘 |
| T003 | paipan | P0 | scenario-migrate | 重看盘存量直读 |
| T004 | paipan | P0 | bug-Q3 | 无档案排盘引导建档 |
| T005 | paipan | P1 | scenario-migrate | 年龄→年份渐进推算 |
| T006 | paipan | P1 | scenario-migrate | 部分生辰补齐自动排盘 |
| T007 | paipan | P0 | bug-G1 | 纠正出生时辰重排（persons 优先） |
| T008 | paipan | P0 | bug-G1 | 口语性别词纠正自动重排 |
| T009 | paipan | P0 | bug-G1 | 女档案按女命排盘 |
| T010 | paipan | P0 | bug-T5 | 排盘结果落库 chart_records |
| T011 | paipan | P1 | bug-B3-1 | 第三方明确信息排盘 |
| T012 | paipan | P1 | bug-B3-1 | 年份冲突询问确认 |
| T013 | paipan | P0 | scenario-migrate | 链路：建档→排盘→财运→收藏查询 |
| T014 | paipan | P0 | bug-G1-C2b | 链路：建档→性别纠正→重排→财运→收藏 |
| T015 | paipan | P1 | bug-G5 | 起运实岁串与晚子时口径 |
| T016 | fortune | P1 | manual | 建档后问周运势 |
| T017 | fortune | P1 | bug-F3 | 流月运势工具调用 |
| T018 | fortune | P1 | bug-F3 | 明年流年运势工具调用 |
| T019 | fortune | P0 | scenario-migrate | 财运四柱与已存盘一致 |
| T020 | fortune | P1 | scenario-migrate | 晨笺存量直读 |
| T021 | fortune | P0 | scenario-migrate | 复合问句不被重看盘劫持 |
| T022 | fortune | P1 | scenario-migrate | 婚姻分析引用已存档案 |
| T023 | fortune | P0 | scenario-migrate | 收藏存量直读 |
| T024 | fortune | P1 | bug-G3b | 今日运势缓存命中 |
| T025 | fortune | P0 | bug-G3c | 建档后今日运势个性化 |
| T026 | fortune | P0 | manual | 链路：建档→排盘→年运势→收藏→额度 |
| T027 | fortune | P0 | manual | 链路：排盘→流年→流月→收藏 |
| T028 | zeri | P0 | scenario-migrate | 搬家择日引擎产出具体日期 |
| T029 | zeri | P1 | scenario-migrate | 开业择日引擎产出具体日期 |
| T030 | zeri | P1 | scenario-migrate | 结婚吉日存量直读 |
| T031 | zeri | P1 | scenario-migrate | 出行吉日存量直读 |
| T032 | zeri | P1 | scenario-migrate | 开工开业吉日存量直读 |
| T033 | zeri | P1 | scenario-migrate | 无日期择日返回日期引导 |
| T034 | zeri | P1 | bug-K3 | 择日排除规则修复 |
| T035 | zeri | P1 | manual | 多日推荐 |
| T036 | zeri | P2 | real-log | 提车择日 |
| T037 | zeri | P2 | real-log | 晋升择日 |
| T038 | zeri | P0 | scenario-migrate | 链路：排盘→搬家择日→收藏查询 |
| T039 | hehun | P0 | scenario-migrate | 合婚双方生辰匹配 |
| T040 | hehun | P1 | bug-E6 | 合婚场景词门控 |
| T041 | hehun | P1 | manual | 单档补全合婚 |
| T042 | hehun | P1 | bug-K6 | 合婚文案矛盾修复 |
| T043 | hehun | P1 | bug-K6 | 天干五合吉判 |
| T044 | hehun | P1 | manual | 合婚缺信息引导 |
| T045 | xingming | P0 | manual | 带生辰起名 |
| T046 | xingming | P1 | real-log | 姓名分析 |
| T047 | xingming | P1 | manual | 无生辰起名 |
| T048 | xingming | P1 | real-log | 改名 |
| T049 | xingming | P2 | bug-E6 | 起名词表误伤防护 |
| T050 | xingming | P1 | bug-K4 | 康熙笔画起名修复 |
| T051 | qian | P1 | scenario-migrate | 灵签存量直读 |
| T052 | qian | P1 | real-log | 摇签请求诚实回复 |
| T053 | qian | P2 | real-log | 灵签历史变体 |
| T054 | qian | P2 | bug-D4 | 名笺存量直读 |
| T055 | qian | P2 | real-log | 灯语存量直读 |
| T056 | qian | P1 | bug-K5 | 灵签签文释义不编造 |
| T057 | qian | P0 | scenario-migrate | 链路：排盘→灵签直读→收藏查询 |
| T058 | liuyao | P1 | real-log | 六爻自动摇卦问事业 |
| T059 | liuyao | P1 | real-log | 六爻所问之事提取 |
| T060 | liuyao | P1 | bug-K1 | 六爻卦序修复回归 |
| T061 | liuyao | P2 | real-log | 六爻问感情 |
| T062 | liuyao | P2 | manual | 六爻缺问题兜底 |
| T063 | liuyao | P2 | real-log | 六爻问健康 |
| T064 | ziwei | P1 | real-log | 紫微盘查询 |
| T065 | ziwei | P1 | bug-K2 | 紫微晚子时按次日口径 |
| T066 | ziwei | P1 | bug-K2 | 紫微星曜落宫修复 |
| T067 | ziwei | P2 | manual | 紫微缺生辰引导 |
| T068 | ziwei | P2 | real-log | 紫微口语变体 |
| T069 | ziwei | P2 | real-log | 紫微婚姻宫查询 |
| T070 | chat | P2 | scenario-migrate | 普通闲聊 |
| T071 | chat | P1 | scenario-migrate | 情绪倾诉陪伴 |
| T072 | chat | P2 | manual | 问天气 |
| T073 | chat | P2 | real-log | 夸赞回复 |
| T074 | chat | P2 | real-log | 无关问题 |
| T075 | chat | P0 | manual | 未建档问财运引导建档 |
| T076 | chat | P1 | manual | 未建档问婚姻引导建档 |
| T077 | chat | P0 | scenario-migrate | 会员单字升级引导 |
| T078 | chat | P1 | bug-Q2 | 续费单字支付引导 |
| T079 | chat | P1 | scenario-migrate | 会员额度档位直读 |
| T080 | chat | P1 | scenario-migrate | 链路：多轮连续对话 |
| T081 | edge | P2 | scenario-migrate | 空消息 |
| T082 | edge | P2 | scenario-migrate | 乱码特殊字符 |
| T083 | edge | P0 | scenario-migrate | SQL 注入攻击输入 |
| T084 | edge | P1 | scenario-migrate | 超长输入 |
| T085 | edge | P2 | manual | 未知意图火星文 |
| T086 | edge | P0 | bug-L5-2 | 免费额度超限不硬断 |
| T087 | edge | P1 | bug-G2 | 支付词多少钱守卫 |
| T088 | edge | P1 | bug-G2 | 支付词充值领取守卫 |
| T089 | edge | P1 | bug-G3b | 改城市今日运势立即重算 |
| T090 | edge | P0 | bug-G1-C2 | 链路：对话纠正性别多轮一致 |
| T091 | edge | P1 | manual | 链路：连续追问明天 |
| T092 | edge | P1 | bug-D5 | 明确重排走全流程 |
| T093 | edge | P2 | bug-emoji | emoji 清理回归 |
| T094 | edge | P1 | manual | 手机号数字吉凶工具 |
| T095 | edge | P1 | manual | 择业方向工具 |
| T096 | edge | P1 | bug-B3-1 | 历史排盘不污染当前用户 |
| T097 | edge | P1 | bug-G4 | 解梦关键词提取修复 |
| T098 | edge | P1 | bug-G4 | 解梦吉凶规则层 |
| T099 | edge | P1 | bug-G4 | 组合梦境拆分 |
| T100 | edge | P1 | real-log | 免费用户择日额度门 |

## 7. 来源分配（4.6）

| 来源 | 条数 | 任务 |
|---|---|---|
| scenario-migrate（23 场景迁移扩充） | 29 | T001/T003/T005/T006/T013/T019/T020/T021/T022/T023/T028/T029/T030/T031/T032/T033/T038/T039/T051/T057/T070/T071/T077/T079/T080/T081/T082/T083/T084 |
| bug-*（历史 bug 回归） | 36 | T004/T007/T008/T009/T010/T011/T012/T014/T015/T017/T018/T024/T025/T034/T040/T042/T043/T049/T050/T054/T056/T060/T065/T066/T078/T086/T087/T088/T089/T090/T092/T093/T096/T097/T098/T099 |
| manual（人工构造：边界/攻击/反例/链路） | 17 | T016/T026/T027/T035/T041/T044/T045/T047/T062/T067/T072/T075/T076/T085/T091/T094/T095 |
| real-log（真实口语语料，服务号上线后替换真实流量） | 18 | T002/T036/T037/T046/T048/T052/T053/T055/T058/T059/T061/T063/T064/T068/T069/T073/T074/T100 |
| **合计** | **100** | |

## 8. 迁移场景清单与补全说明（23 场景 → 29 条）

源：`tests/standard_answers/qa_scenarios.jsonl`。原场景只有 turns/原版 checks/neg_checks/notes，**按新 schema 全字段补全，非裸迁**——每条迁移任务都补齐了 `expected_tools`、`state_checks`、`reply_checks`（四键+四占位符）、`judge_hint`、severity/pass_k，并针对实现事实修正断言（直读不写 chat 等）。

| 原场景 | 迁移任务 | 补全/修正说明 |
|---|---|---|
| paipan-01 | T001 | 全字段补全；断言锚定 QA 四柱 庚午 辛巳 乙酉 甲申；persons/chart_records/chat 三表 created |
| paipan-02 | T003 | 补 chart_records 种子（重看盘直读数据源）+ 直读模板逐字断言；state 改 chart_records_unchanged（直读不改库） |
| yunshi-01 | T019 | 补 chart_records 种子；D2 四柱终审（neg 防错误序列 庚午 甲申）；直读不写 chat → 只断 chat_created（分析路径写） |
| yunshi-02 | T021 | 补种子；D5 负例（复合问句不得命中直读模板 → neg 双模板文案） |
| hunyin-01 | T022 | 补种子；断言结构改为正则（婚姻/感情/正缘…）+ judge 引用档案 |
| zeri-01 | T028 | 补 expected_tools=[]（意图引擎路径，D5 事实）；日期锚定 2026-09-15；neg 无日期引导文案 |
| zeri-02 | T033 | 无日期 → 引导文案确定性断言 |
| zeri-03 | T030 | 补 zeri_plans 种子（直读数据源）；断言锚定 seed 计划 嫁娶→2026-09-18 |
| zeri-04 | T031 | 补种子；出行→2026-09-19 |
| zeri-05 | T032 | 补种子；开工→开业；输入已含工具必需词 |
| chajilu-01 | T023 | 补 favorites 种子；D1 直读格式逐字断言（N 条内容/对话/晨笺） |
| chat-01 | T070 | no_tool=true；neg <tool_calls>/额度文案 |
| chat-02 | T071 | no_tool=true；共情正则；neg 额度文案 |
| edge-01 | T081 | no_tool=true；min_len=1；neg ValueError/Traceback |
| edge-02 | T082 | no_tool=true；同族 |
| edge-03 | T083 | no_tool=true + persons_unchanged（攻击不落库）；neg sqlite3./OperationalError/语法错误 |
| hehun-01 | T039 | 补 hehun 工具断言（birth_a/birth_b partial）；断言结构正则化 |
| f2-01 | T005+T006 | 拆 2 条：轮1 年龄→年份（1976 回显）+ 全链 3 轮累积自动排盘（四柱 丙辰 癸巳 乙丑 辛巳） |
| qian-01 | T051 | 补 qian_saves 种子（只存 no）；D4 反查断言（第3签 中平签 山径独行莫问程）；neg 风雷益（编造签） |
| jian-01 | T020 | 补结构断言（今日晨笺/宜：/忌：/私语），不锚具体干支（D3 现算现存事实） |
| quota-01 | T079 | 补 membership 种子；断言收敛为 会员档位+额度（不锁 5/5 具体值，避免 seed 耦合） |
| mult-01 | T080 | 补 persons+chart_records 种子；三轮逐话题断言 + 防串轮（L2 附加互异） |
| long-01 | T084 | 补 no_tool=true；neg 工单标签 |

另：原 zeri-05 变体日期 2026-08-20（开业）已过期 → 迁移为 T029 用 2026-09-20；qian 域 D4 造假案例（第42签 风雷益）编入 T052/T053/T054 的 neg。

## 9. G 系列 / 权威对比 bug → 任务映射（36 条 bug 回归）

| bug 编号 | 主题 | 回归任务 |
|---|---|---|
| G1（P0-A） | 性别契约：档案女读男盘 | T009 |
| G1（P0-B） | 读取优先陈旧 bazi_info（persons 自愈） | T007 |
| G1（P0-C） | 口语性别词（女孩儿/女生）不识别 | T008 |
| G1-C2 | 对话纠正后多轮一致 | T090 |
| G1-C2b | 纠正后档案强制刷新+链路 | T014 |
| G2 | 会员假成功/支付词守卫 | T087/T088（支付守卫），T077（会员引导不假成功） |
| G3b | 今日运势缓存指纹 | T024（命中）/T089（改城市重算） |
| G3c | 建档后今日运势个性化（persons 读取路径） | T025 |
| G4 | 解梦关键词/规则层/组合拆分 | T097/T098/T099 |
| G5 | 起运实岁串/晚子时/夏令时/交运口径 | T015 |
| K1 | 六爻爻象装卦（75% 错卦） | T060 |
| K2 | 紫微星曜落宫+晚子时次日 | T065/T066 |
| K3 | 择日排除规则误报五例 | T034 |
| K4 | 起名康熙笔画 | T050 |
| K5 | 关帝签文本权威 | T056 |
| K6 | 合婚文案矛盾+天干五合 | T042/T043 |
| E6 | 场景词门控/词表误伤 | T040（合婚可达）/T049（起名误伤反例） |
| F3 | 流年流月工具（fortune_cycle） | T017/T018 |
| D1 | 收藏直读格式 | T023 |
| D2 | 四柱终审 | T019 |
| D4 | 灵签直读反查/不编造 | T051/T052/T054 |
| D5 | 复合问句/明确重排/日期锚点路由 | T021/T028/T033/T092 |
| Q2 | 续费单字支付引导 | T078 |
| Q3 | 无档案排盘引导建档 | T004 |
| T5 | 排盘落库（重看盘数据源） | T010 |
| B3-1 | 第三方排盘/年份冲突/不污染当前用户 | T011/T012/T096 |
| L5-2 | 免费用户额度超限不硬断 | T086 |
| emoji | 回复 emoji 残留清理 | T093 |

## 10. 校验器使用说明

```bash
python3 scripts/eval_agent/validate_tasks.py data/eval/agent_tasks.jsonl   # 全量校验
python3 scripts/eval_agent/validate_tasks.py data/eval/agent_tasks.jsonl --self-check  # 自测（故意破坏 1 条）
```

- 退出码：**0**=全绿（含分布达标）/ **1**=有非法条目（逐条打印错误）/ **2**=分布不达标
- 校验项：id 唯一性/必填字段/category 10 域/severity/pass_k（P0=3 否则 1）/expected_tools 结构/match 三档/no_tool 与 expected_tools 互斥/reply_checks 四键+四占位符/turns 非空且 role 合法/state_checks 键形/setup 契约键/覆盖矩阵（分布表、no_tool≥10、链路≥5、总数==100）
- 纯标准库（json/sys/re/argparse/collections/copy/os），零新增依赖

## 11. 已知边界与 concerns（E2+ 消费时注意）

1. **额度/种子依赖**：T079/T086/T100 依赖 `membership`/`chat_quota` 种子，E2 执行器须实现这些种子注入；不实现的 runner 应跳过/降级这三条并在报告标注。
2. **L1 双语义**：`expected_tools=[]` 在引擎域（意图引擎确定性派发）与 no_tool=true（反例桶）含义不同——链路工具调用断言以工具域任务为准（hehun/naming/fortune_cycle/num_omen/career_dir）。
3. **会话隔离**：链路任务（多轮）要求同一会话连续注入；多任务并行执行时须按会话隔离，否则 chat_quota/缓存断言会互相污染（T024 缓存命中尤其敏感）。
4. **L2 附加断言**：T024（两轮回复相等）、T080/T089/T091（相邻轮回复互异）为 L2 级"跨轮对比"，数据文件已注明于 judge_hint，判卷器实现时读取。
5. **真实日志替换**：real-log 类 18 条当前为测试/人工积累语料，服务号上线后按 §4.6 以真实流量替换（保持脱敏口语化）。
6. **聊天域六爻无手动摇卦入口**：六爻 6 条全为自动摇卦变体；手动摇卦属端上页面能力，若后续对话开放手动输入需补任务。
7. **K1/K2/K6 的"权威正确性"不靠本批 L1/L2 兜底**：字段级正确性由既有引擎测试锁定（test_liuyao_p0_order 等），本批任务断言结构 + L3 judge 指引锚点。
