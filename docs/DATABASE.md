# 易理明灯 数据库文档（SQLite）

> 版本：2026-08-08（运维完备性补全后快照）
> 库文件：`/mnt/d/fortune-data/userdata/fortune.db`（`.env` / `config/settings.yaml` 可改）
> 引擎：SQLite（WAL 模式已启用，见 §性能）
> 表定义：`src/storage/models.py`（SCHEMA_SQL）、`src/api/pay_midas.py`（midas_orders）
> 访问层：`src/storage/dao.py`（users/consultations/push_log）、`session_dao.py`（sessions）、
> `member_dao.py`（memberships/payments）、`preference_dao.py`（user_preferences）

## 0. 总览

业务表共 **9 张**（另有 `sqlite_sequence`，AUTOINCREMENT 自增序列维护表，勿手动改动）：

| # | 表名 | 行数(2026-08-08) | 主要用途 |
|---|------|------------------|----------|
| 1 | users | 415 | 用户档案（微信 openid 派生 ID + 八字密文 + 推送设置 + session_key） |
| 2 | consultations | 825 | 咨询记录（问题/盘图/分析/反馈） |
| 3 | sessions | 613 | 聊天会话历史（每条消息一行） |
| 4 | memberships | 94 | 会员套餐与配额 |
| 5 | payments | 7 | 订单（mock 支付 / 微信支付占位） |
| 6 | midas_orders | 0 | 微信虚拟支付（米大师）订单 |
| 7 | push_log | 295 | 每日运势推送日志 |
| 8 | user_preferences | 0 | 用户偏好画像（EMA 学习结果） |
| 9 | user_tone_feedback | 1 | 语气反馈统计（遗留/预留表，当前代码无引用） |

## 1. 加密说明

- 加密算法：AES-256-GCM，密钥来自 `ENCRYPTION_KEY`（base64 32 字节）。
- 密文格式：`{version}:<base64(nonce(12)+ciphertext+tag(16))>`（dev 模式为
  `dev:<...>`）。
- 密钥轮换：`v1:key,v2:key` 逗号分隔，末键为当前加密钥，旧键保留供解密
  （单个带标签键 `v1:key` 亦合法）。
- 读取路径自动解密；旧明文数据读取时懒迁移为密文（不改变业务计数）。
- 解密失败降级：按原样返回（兼容旧数据/密钥轮换期），`DataEncryptor.decrypt`
  会记 ERROR 日志（见 `src/security/encryption.py`）。
- **加密字段（敏感，2026-09-10 k20 审计逐点核实，无明文旁路）**：
  - `users.bazi_info`、`users.session_key_enc`、`users.phone_enc`
  - `persons.birth_enc / bazi_enc`（默认命主档案）
  - `chart_records.birth_enc / bazi_enc`（排盘记录）
  - `consultations.question / chart_data / analysis`
  - `sessions.content`（聊天消息正文）
  - `jian_cards.card_enc`（晨笺卡片）
  - L3 用户记忆文件 `fact_entries_enc / topic_evolution_enc`（文件内整段加密）
- **明文字段**：其余全部为明文（含 `users.user_id`——由 openid 派生，需用于关联查询，不加密）。
- 备注：`users.ziwei_info` 为 schema 预留列、当前无任何写入代码；启用写入时
  必须走 `_encrypt_text`（与 bazi_info 同款加密），禁止明文落库。

## 2. 表结构明细

### 2.1 users — 用户档案

| 字段 | 类型 | 加密 | 说明 |
|------|------|------|------|
| user_id | TEXT (PK) | 否 | `wx_<openid>` 派生，登录时确定，永不变更 |
| bazi_info | TEXT | ✅ v1 | JSON：`{year,month,day,hour,minute,city,gender,bazi[],calendar}` |
| ziwei_info | TEXT | 否 | 紫微盘 JSON（预留，当前无写入代码） |
| push_enabled | INTEGER | 否 | 每日推送开关（1=开） |
| push_time | TEXT | 否 | 推送时间 HH:MM（默认 08:00） |
| created_at / updated_at | TEXT | 否 | ISO 时间 |
| consultation_count | INTEGER | 否 | 累计咨询次数（save_user_bazi 时 +1） |
| session_key_enc | TEXT | ✅ v1 | 微信 code2session 返回的 session_key 密文（虚拟支付用户态签名用），懒迁移列 |

- 索引：PK `user_id`（全表唯一关联键）。
- 前端关系：`/api/user/login`（创建/复用）、`/api/user/profile`、`/api/user/bazi`、
  `/api/user/subscription`、`/api/push-settings/{uid}`、`/api/reports`（读八字兜底基础命书）。

### 2.2 consultations — 咨询记录

| 字段 | 类型 | 加密 | 说明 |
|------|------|------|------|
| id | INTEGER (PK, AUTOINCREMENT) | 否 | 咨询 ID（报告/反馈的关联键） |
| user_id | TEXT NOT NULL | 否 | 归属用户（FK → users.user_id） |
| question | TEXT | ✅ v1 | 用户提问原文 |
| intent | TEXT | 否 | 业务意图：bazi/ziwei/liuyao/fengshui/mianxiang/zeri/hehun/qimen/xingming/dream 等 |
| chart_data | TEXT | ✅ v1 | 排盘结果 JSON |
| analysis | TEXT | ✅ v1 | LLM 分析正文（报告详情 fullContent 来源） |
| feedback | TEXT | 否 | positive/negative（用户 👍/👎） |
| created_at | TEXT | 否 | 记录时间（报告列表按此排序） |

- 索引：`idx_consultations_user (user_id, created_at)` — **复合索引**，覆盖
  「按用户 + 时间倒序」的报告列表/历史查询（`/api/reports`、`/api/user/{id}/history`）。
- 前端关系：`/api/reports`（列表/详情）、`/api/user/{id}/history`、
  `/api/feedback/{consultation_id}`、`/api/user/{id}/accuracy`（反馈统计）。

### 2.3 sessions — 聊天会话历史

| 字段 | 类型 | 加密 | 说明 |
|------|------|------|------|
| id | INTEGER (PK, AUTOINCREMENT) | 否 | 消息 ID |
| user_id | TEXT NOT NULL | 否 | 归属用户 |
| role | TEXT NOT NULL | 否 | user / assistant |
| content | TEXT NOT NULL | ✅ v1 | 消息正文 |
| intent | TEXT | 否 | 命理意图（自由对话为 NULL） |
| created_at | TEXT | 否 | 时间 |

- 索引：`idx_sessions_user (user_id, created_at)` — 覆盖聊天历史查询
  （`get_history` ORDER BY created_at DESC, id DESC LIMIT n）与自动清理
  （每用户最多保留 100 条，`SessionDAO._cleanup`）。
- 前端关系：`/api/chat`（写入）、AI 多轮上下文读取（`get_context_for_llm`）。

### 2.4 memberships — 会员套餐与配额

| 字段 | 类型 | 加密 | 说明 |
|------|------|------|------|
| user_id | TEXT (PK) | 否 | 会员归属 |
| plan | TEXT NOT NULL | 否 | free/basic/pro/annual |
| started_at / expires_at | TEXT | 否 | 起止时间（ISO） |
| queries_used | INTEGER | 否 | 已用次数 |
| queries_limit | INTEGER | 否 | 上限（NULL=不限） |
| auto_renew | INTEGER | 否 | 自动续费标记（当前恒 0） |
| created_at | TEXT | 否 | 创建时间 |

- 索引：PK `user_id`。表小（百行级），无需额外索引。
- 前端关系：`/api/membership/{uid}`、`/api/user/member`、`/api/membership/{uid}/upgrade`
  （mock 支付开通）、`/api/admin/stats`（运营统计）。

### 2.5 payments — 订单

| 字段 | 类型 | 加密 | 说明 |
|------|------|------|------|
| id | INTEGER (PK, AUTOINCREMENT) | 否 | 订单 ID（orderId 透传前端） |
| user_id | TEXT NOT NULL | 否 | 下单用户 |
| amount | REAL NOT NULL | 否 | 金额（元） |
| plan | TEXT NOT NULL | 否 | 商品/套餐 ID（PRODUCTS/SUBSCRIBE_PLANS 的 key） |
| status | TEXT | 否 | pending / paid / cancelled |
| payment_method | TEXT | 否 | mock / wechat / midas |
| created_at | TEXT | 否 | 下单时间 |

- 索引：`idx_payments_user (user_id, created_at)` — 覆盖「本人订单倒序」查询
  （`/api/user/orders`）。
- 前端关系：`/api/pay/create`、`/api/pay/subscribe`、`/api/user/orders`、
  `/api/user/purchase/{product_id}`。

### 2.6 midas_orders — 微信虚拟支付（米大师）订单

| 字段 | 类型 | 加密 | 说明 |
|------|------|------|------|
| out_trade_no | TEXT (PK) | 否 | 商户订单号（YL+毫秒时间戳+随机 hex，全局唯一） |
| user_id | TEXT NOT NULL | 否 | 下单用户 |
| payment_id | INTEGER NOT NULL | 否 | 关联 payments.id |
| product_id | TEXT NOT NULL | 否 | 道具/套餐 ID |
| kind | TEXT NOT NULL | 否 | product（道具直购）/ subscription（会员订阅） |
| plan | TEXT | 否 | 订阅内部套餐（kind=subscription 时非空） |
| amount_cents | INTEGER NOT NULL | 否 | 金额（分） |
| env | INTEGER | 否 | 0=现网，1=沙箱 |
| status | TEXT | 否 | pending / paid / cancelled |
| attach | TEXT | 否 | JSON `{kind,id,plan}`（发货依据） |
| created_at / updated_at | TEXT | 否 | 时间 |

- 索引：`idx_midas_orders_user (user_id)`（本人订单查询）、
  `idx_midas_orders_status (status)`（对账/状态检索）。
- 表由 `src/api/pay_midas.py` 自管理（`_ensure_midas_orders_table`），独立于
  models.py SCHEMA_SQL。
- 前端关系：`POST /api/pay/virtual/create`（建单）、`POST /api/pay/virtual/notify`
  （米大师发货回调，验签通过后置 paid + 发货）、`GET /api/pay/virtual/status`（轮询）。

### 2.7 push_log — 推送日志

| 字段 | 类型 | 加密 | 说明 |
|------|------|------|------|
| id | INTEGER (PK, AUTOINCREMENT) | 否 | 日志 ID |
| user_id | TEXT NOT NULL | 否 | 推送对象 |
| push_date | TEXT NOT NULL | 否 | 推送日期（YYYY-MM-DD） |
| message | TEXT | 否 | 推送消息内容 |
| success | INTEGER | 否 | 1=成功（或已写日志未下发），0=失败 |
| error | TEXT | 否 | 失败原因 |
| created_at | TEXT | 否 | 记录时间 |

- 索引：`idx_push_log_user_date (user_id, push_date)` — 覆盖「某人某日推送」查询。
- 前端关系：无直接读取；由 `scripts/daily_push.py` 写入，`/api/push-daily` 手动触发。

### 2.8 user_preferences — 用户偏好画像（EMA 学习）

| 字段 | 类型 | 加密 | 说明 |
|------|------|------|------|
| user_id | TEXT (PK) | 否 | 归属用户 |
| style_sassy / style_analyst / style_gentle | REAL | 否 | 三种人格风格权重（EMA，和≈1） |
| topic_wealth / topic_love / topic_career / topic_health / topic_growth | REAL | 否 | 五类话题偏好权重（EMA，和≈1） |
| prefer_short | INTEGER | 否 | 偏好简短回复（0/1） |
| feedback_count / positive_count | INTEGER | 否 | 反馈总数 / 好评数 |
| last_style / last_topic | TEXT | 否 | 最近一次使用的人格模式 / 话题 |
| created_at / updated_at | TEXT | 否 | 时间 |

- 索引：PK `user_id`。
- 数据为数值画像（非原始敏感文本），明文存储。
- 前端关系：`/api/user/preferences`、`/api/user/{id}/accuracy`（仪表盘）。

### 2.9 user_tone_feedback — 语气反馈统计（遗留/预留）

| 字段 | 类型 | 加密 | 说明 |
|------|------|------|------|
| user_id | TEXT NOT NULL | 否 | 归属用户 |
| tone | TEXT NOT NULL | 否 | 语气标识 |
| count | INTEGER | 否 | 该语气次数 |
| updated_at | TEXT | 否 | 更新时间 |

- 联合主键 `(user_id, tone)`，无独立索引。
- **当前代码库无任何引用**（grep 全库为 0），推测为早期实验/测试创建，
  保留 1 行数据。结论：可作为未来语气画像扩展的预留表，或清理。
- 前端关系：无。

## 3. 索引与查询性能

| 热点查询 | 使用索引 | 结论 |
|----------|----------|------|
| 报告列表 `WHERE user_id=? ORDER BY created_at DESC` | idx_consultations_user **(user_id, created_at) 复合** | ✅ 已覆盖，无需新增 |
| 聊天历史 `WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT n` | idx_sessions_user (user_id, created_at) | ✅ |
| 推送记录 `WHERE user_id=? AND push_date=?` | idx_push_log_user_date (user_id, push_date) | ✅ |
| 本人订单 `WHERE user_id=? ORDER BY id DESC` | idx_payments_user (user_id, created_at) | ✅ |
| 虚拟支付订单（out_trade_no 主键 / user_id / status） | PK + 2 个单列索引 | ✅ |
| 会员查询（user_id 主键）、活跃付费会员（plan 过滤） | PK；表仅百行级 | ✅ 全表扫描可接受 |

- N+1 排查结论：DAO 层所有「用户数据列表」均为单条 SQL + 复合索引，无 N+1 热点。
  仅 `preference_dao.get_accuracy_dashboard` 对每个话题执行一次 LIKE 聚合（5+4 次 SQL/
  用户），属可接受的仪表盘计算开销。
- `get_user_consultations(user_id, limit=1000)`（/api/reports 使用）走复合索引，
  825 行数据量下无性能问题。

## 4. 连接策略与 WAL

- **WAL 模式**：已启用（`PRAGMA journal_mode=WAL`，由 `src/storage/models.py`
  的 `connect()` 在每次连接时幂等设置；首次设置后为数据库文件持久属性）。
  单写多读并发安全，读写互不阻塞；`busy_timeout=10000ms` 缓解写冲突。
- **连接策略现状**：每请求/每次方法调用新建连接、用后即关（SQLite 连接非线程共享）。
  当前数据量（千行级）与 QPS 下开销可忽略；若未来增长到十万行级/高并发写，
  可引入 `sqlite3` 连接池（注意 `check_same_thread` 与事务边界），或在读路径
  使用同一长连接。**现阶段维持每请求新连接（改动风险低、语义清晰），不改。**
- 备份：`scripts/backup_db.py`（sqlite3 在线备份 API，保留 14 份），
  cron 每日 03:00 + 后端启动 >24h 兜底；结果落 `logs/backup_db.log`。

## 5. 常见运维操作

```bash
# 手动备份
bash scripts/backup_db.sh
# 查看 WAL 是否生效（应返回 wal）
python -c "import sqlite3;print(sqlite3.connect('/mnt/d/fortune-data/userdata/fortune.db').execute('PRAGMA journal_mode').fetchone())"
# 今日错误日志
grep -c "$(date +%F).*\[ERROR\]" logs/app.log
# 完整健康详情（管理员）
curl -H "Authorization: Bearer $ADMIN_KEY" http://127.0.0.1:8767/api/health/detail
```
