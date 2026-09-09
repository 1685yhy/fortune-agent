# 易理明灯小程序 — 全量功能审计报告（前端 vs 后端缺口清单）

- 审计日期：2026-08-07
- 前端：`/mnt/e/fortune-agent/miniprogram`（墨韵版）
- 后端：`/mnt/e/fortune-agent/src`（FastAPI，:8767）
- 方法：逐页读 wxml 事件绑定 + js 方法 + `utils/api.js` 封装 + 后端全部路由装饰器 + **对运行中的 8767 服务实测探测**（下文中所有 404/422/000 均为 2026-08-07 20:40-21:10 实测结果）
- 范围：只审计，未改任何代码

---

## 0. 重大运行事件（先看这里）

**服务已整体卡死（P0，实时发生）**：审计过程中（20:44 起）对 `/api/share/1`、`/api/xuetang/topics`、`/api/membership/audit_test`、`/api/user/export/audit_test` 的只读探测后，8767 全部接口（含 `/api/health`）超时不可达，持续 30 分钟以上未恢复。进程 `uvicorn src.main:app`（PID 52482）状态为 R（running，疑似忙循环/事件循环阻塞），线程数 86，CPU 累计 8 分 16 秒。**需要立即重启该服务**，并排查单 worker 下同步阻塞调用（LLM/embedder/文件 IO 无超时）导致的事件循环卡死；建议加请求级超时中间件与健康检查看门狗（详见 §5-P1）。

---

## 表格 1：前端页面 × 交互 × 后端状态

| 页面 | 文件 | 交互（wxml 事件 → js 方法） | 后端状态 | 结论 |
|---|---|---|---|---|
| today 今日 | pages/today/today.{js,wxml} | goChat（去聊天）、onTab（自定义 TabBar）、onLoad→_loadFortune→`api.getTodayFortune`（L81）→ GET /api/calendar/today | ✅ 路由存在（calendar.py L87），实测 200 | **已接通**。但 user_id 恒为 `default_user`（app.js 从不设置 globalData.openid，today.js L80），无八字时返回通用黄历；失败静默保持原型文案 |
| chat 夜话 | pages/chat/chat.{js,wxml} | sendMessage/quickAsk/onConfirm→`api.chat`（L125）→ POST /api/chat；toggleFb（L145）→ up/down 调 `api.feedback`（L153）→ POST /api/feedback/{id}；clearChat；无语音按钮 | ✅ /api/chat（main.py L697）、/api/feedback/{id}（L914）均存在，实测 200 | **已接通**。问题：① chat() 的 user_id 恒为 `miniprogram_user`（api.js L138，openid 未设置）→ 咨询记录挂错账；② 「keep」反馈只点亮本地不提交（chat.js L150 只处理 up/down）；③ tts 封装存在但页面从未调用（无语音播报/语音输入） |
| reports 命书 | pages/reports/reports.{js,wxml} | onLoad→`api.getReports(1,20)`（L42）→ GET /api/reports | ✅ 路由存在（main.py L598），实测 200 | **已接通（半）**。问题：① 四卷章目行**无 bindtap**，报告详情不可点开（api.js getReportDetail L197 封装了但无页面调用）；② 因登录态与 chat user_id 分裂（见 §审计7），咨询记录挂在不同 ID 下，列表大概率恒为空 → 永远显示默认四卷 |
| love 感情合盘 | pages/love/love.{js,wxml} | onSubmit→`api.getLoveCompatibility`（L503）→ POST **/api/love/compatibility** | ❌ **404**：后端只有 POST /api/compatibility（compatibility.py L365） | **未接通（假数据）**。双重断裂：① 路径不同；② 契约不同——前端发 `{birthYear1,birthMonth1,birthDay1,birthHour1,gender1,...}`（love.js L341-352），后端要 `{user1:{year,month,day,hour,minute,gender,city},user2:{...}}`（compatibility.py L37-50）。catch 后走 `generateDemoResults`（L371）**生成伪随机分数+内置文案**，用户看到的是假结果。支付链路：startPayment→payment.purchase（payment.js L117）→ POST /api/pay/create（**404**）→ DEV_MODE 演示成功（payment.js L3,L143-146）。另：**love.js 定义了两个同名 onSubmit（L303 与 L495），后者覆盖前者**，整套 picker 表单+支付+DEMO_ANALYSIS（L52-175，12KB）是死代码 |
| me 我的 | pages/me/me.{js,wxml} | goToday、onTab；_deriveUser（L43）读 globalData.baziInfo；meRows 四行**无 bindtap** | —（无任何网络调用） | **纯静态/占位**。① baziInfo 永不填充：login 响应没有 bazi 字段（app.js L51 读 `res.user.bazi` 恒 undefined），getUserProfile 无人调用；② 无会员入口、无更正档案、无订阅开关、无登出——四行文案（我的命书/解梦手记/开灯提醒/关于明灯）全是写死的原型文案（me.js L8-13） |
| privacy 隐私 | pages/privacy/privacy.wxml | 纯静态文本 | — | 静态页（已注册） |
| agreement 协议 | pages/agreement/* | 纯静态文本 | — | **未注册**：不在 app.json pages（见下） |
| hehun 合婚 | pages/hehun/* | onSubmit→`api.hehun({person1,person2})`（L225） | ❌ **api.js 未导出 hehun 方法** → `api.hehun is not a function`，永远走 catch→showError（hehun.js L228-237）；后端 POST /api/hehun 实测 422（路由存在） | **未接通**。且即使补方法，契约也要对齐：后端要 `person_a/person_b:{year,month,day,hour,minute,city,gender}`（hehun.py L22-28），前端发 `person1/person2:{birthYear,...}`；响应键 `total_score` vs 前端读 `score`（hehun.js L242） |
| qimen 奇门 | pages/qimen/* | onSubmit→`api.qimen({date,time,city,question})`（L101） | ❌ api.js 未导出 qimen → 永远走 catch→showError；后端 POST /api/qimen 实测 422（路由存在） | **未接通**。契约需对齐：后端要 `{year,month,day,hour,minute,city,question}`（qimen.py L19-25），前端发 date/time 字符串 |
| xingming 姓名 | pages/xingming/* | onSubmit→`api.xingming({surname,givenName,gender})`（L63） | ❌ api.js 未导出 xingming → 永远 catch→showError；后端 POST /api/xingming 实测 422（路由存在） | **未接通**。契约：后端 `surname/given_name/gender`（xingming.py L14-17，下划线命名），响应 `wuge/sancai/...` 与前端预期结果渲染字段不一致 |
| xuetang 学堂 | pages/xuetang/* | loadTopics→`api.getXuetangTopics`（L226）；selectTopic→`api.getXuetangLesson(topicId)`（L264） | ❌ api.js 未导出这两个方法 → 永远 catch→showError（连 demo 话题都不展示）；后端 GET /api/xuetang/topics、/lesson 路由存在（xuetang.py L34/L58） | **未接通**。即使补方法，契约也不匹配：后端 topics 返回 `{curriculum:[{level,topics:[{name,level}]}]}`，前端要 `res.topics:[{id,name,description,lessonCount}]`；lesson 返回 `{topic,content,...}`，前端要 `res.lesson:{title,content,...}` |
| — | app.json | 注册页仅 6 个：today/chat/reports/love/me/privacy | — | **hehun/qimen/xingming/xuetang/agreement 五页未注册**（代码在 pages/ 但无法导航进入）；全项目无任何 navigateTo/switchTab 指向 love 页（仅能靠分享卡片 path 进入） |

---

## 表格 2：后端路由 × 前端使用

| 后端路由 | 位置（文件:行） | 前端使用 | 备注 |
|---|---|---|---|
| POST /api/user/login | api/user.py:66 | ✅ app.js:42 | 见 §审计7：实为 dev 模式 |
| GET /api/calendar/today | api/calendar.py:87 | ✅ today.js:81 | 6 小时缓存；无鉴权（见 §审计8） |
| POST /api/chat | main.py:697 | ✅ chat.js:125 | 配额检查+sanitizer；user_id 可伪造 |
| POST /api/feedback/{cid} | main.py:914 | ✅ chat.js:153 | 任意咨询 ID 可提交（P1） |
| GET /api/reports | main.py:598 | ✅ reports.js:42 | JWT 可选，无 token 返回空 |
| GET /api/tts（POST） | main.py:662 | ⚠️ api.js:154 有封装，无页面调用 | 语音未接 |
| GET /api/reports/{id} | main.py:613 | ⚠️ api.js:197 有封装，无页面调用 | 报告详情无入口 |
| GET /api/scenarios | api/scenarios.py:64 | ⚠️ api.js:118 有封装，无页面调用 | |
| GET /api/pricing、/api/pricing/statement | api/pricing.py:8/19 | ⚠️ api.js:258 有封装，无页面调用 | |
| POST /api/user/bazi | api/user.py:152 | ⚠️ api.js:210 有封装，无页面调用 | 前端无设置表单页（B 类） |
| GET /api/user/profile | api/user.py:110 | ❌ api.js:221 封装但**不带 user_id** → 必 400；无页面调用 | |
| POST /api/user/subscription | api/user.py:177 | ⚠️ api.js:232 有封装，无页面调用 | 订阅开关无 UI |
| POST /api/user/feedback | api/user.py:191 | ❌ 未用（前端走 main.py /api/feedback/{id}） | 重复实现 |
| GET /api/user/preferences | api/user.py:220 | ❌ 未用 | |
| POST /api/compatibility | api/compatibility.py:365 | ❌ 未用！前端调 /api/love/compatibility（404） | 路径+契约双断裂 |
| GET /api/share/{reading_id} | api/share.py:40 | ⚠️ api.js:246 有封装，无页面调用 | 数据源为 data/reports/*.json（share.py:20-32），前端传入咨询 ID → 几乎必然 404；响应也无前端要的 imageUrl 字段 |
| POST /api/hehun | api/hehun.py:62 | ❌ api.js 无方法（页面试图调用） | 实测 422（路由活） |
| POST /api/qimen | api/qimen.py:74 | ❌ api.js 无方法 | 实测 422（路由活） |
| POST /api/xingming | api/xingming.py:55 | ❌ api.js 无方法 | 实测 422（路由活） |
| GET /api/xuetang/topics、/lesson | api/xuetang.py:34/58 | ❌ api.js 无方法 | 实测在服务卡死前响应 405（路由活） |
| GET /api/hourly-fortune | api/hourly.py:68 | ❌ 未用 | |
| POST /api/advisor | api/advisor.py:83 | ❌ 未用 | |
| /api/report、/report/{id}、/api/report/generate | api/visual_report.py:439/448/947 | ❌ 未用 | 旧 web 报告体系 |
| /api/security/*（disclaimer/info/token/refresh/revoke/export/data/anonymize/retention/audit/sanitizer/status） | security/router.py:107-365 | ❌ 全部未用 | 与 main.py 的导出/删除接口重复 |
| /v1/*（openai 兼容） | openai_compat.py:68-130 | ❌ 未用（chatgpt-on-wechat 用） | |
| /api/health、/api/stats、/api/stats/predictions | main.py:783/788/1189 | ❌ 未用 | |
| /api/calendar/daily、/api/calendar/week | main.py:933/966 | ❌ 未用 | 与 GET /api/calendar/today 并存 |
| /api/face-reading、/api/palm-reading | main.py:1030/1092 | ❌ 未用 | 前端无入口（ic-camera 是静态提示） |
| /api/dashboard/{uid}、/api/share-card/{uid} | main.py:1134/1146 | ❌ 未用 | |
| /api/user/{uid}/history、/accuracy | main.py:885/898 | ❌ 未用 | |
| /api/user/export/{uid}、DELETE /api/user/data/{uid} | main.py:799/814 | ❌ 未用 | **无鉴权，P0（见 §审计8）** |
| /api/push-daily、/api/push-weekly | main.py:833/857 | ❌ 未用（运维触发） | 后台 _daily_push_worker（main.py:168） |
| /api/push-settings/{uid} GET/POST | main.py:1197/1205 | ❌ 未用（前端走 /api/user/subscription） | 重复实现 |
| /api/membership/{uid}、/upgrade | main.py:1235/1243 | ❌ 未用（前端走 /api/user/member→404、/api/pay/subscribe→404） | 支付链路断裂 |
| /api/admin/stats、/api/admin/active-members | main.py:1279/1289 | ❌ 未用 | admin_key 可空=放行（main.py:1229-1232） |
| **前端调但后端无**：POST /api/love/compatibility、POST /api/pay/create、POST /api/pay/subscribe、GET /api/user/member、GET /api/user/orders、GET /api/user/purchase/{id} | — | api.js:304/272/329/318/343/354 | **全部 404（实测）** |

---

## 表格 3：缺口清单（A / B / C 类）

### A 类 — 前端调用但后端 404 / 字段契约不匹配

| # | 位置 | 现状 | 建议修复 | 优先级 |
|---|---|---|---|---|
| A1 | love.js:341-355 → api.js:303-309 → compatibility.py:365 | 前端 POST `/api/love/compatibility` 实测 **404**；正确路由是 `/api/compatibility`，且契约完全不同（前端 birthYear1/… vs 后端 user1:{year,month,…}）。失败后静默返回**伪随机假结果**（love.js:371-393） | ① 前端改为调 `/api/compatibility`；② 前端把 picker 数据组装成 `{user1,user2}` PersonInfo；③ 后端响应键对齐（后端 `match_score/summary/strengths/...` vs 前端读 `score/personality/fate/...`，love.js:356/395-408）；④ **取消假数据兜底**——失败必须显式报错（假结果有合规风险） | **P0** |
| A2 | api.js:271/328/317/342/353；payment.js:117-152 | `/api/pay/create`、`/api/pay/subscribe`、`/api/user/member`、`/api/user/orders`、`/api/user/purchase/{id}` **全部 404**（实测）。payment.js DEV_MODE=true（L3）→ 所有购买静默"演示成功"，无任何订单落库 | 后端新增：POST /api/pay/create（微信支付统一下单，会员套餐接 member_dao 的 PLANS：free/basic/pro/annual，member_dao.py:10）、POST /api/pay/subscribe、GET /api/user/member、GET /api/user/orders、GET /api/user/purchase/{id}；前端 DEV_MODE 上线前置 false。套餐 ID 也要对齐：前端 payment.js:42-58 用 monthly/first_month，后端是 basic/pro/annual | **P0** |
| A3 | api.js:221-225 → user.py:110-116 | getUserProfile 封装**不带 user_id**，后端必返 400；且无页面调用 | 封装改为从全局 token/userId 传入；me 页 onShow 调它填充手札 | **P1** |
| A4 | hehun.js:225 / qimen.js:101 / xingming.js:63 / xuetang.js:226,264 | 页面调用 `api.hehun/api.qimen/api.xingming/api.getXuetangTopics/api.getXuetangLesson`，**api.js 未导出这 5 个方法** → TypeError → 页面永远错误态。后端对应路由均存在且实测活（422/405） | 在 api.js 补 5 个封装（POST /api/hehun、POST /api/qimen、POST /api/xingming、GET /api/xuetang/topics、GET /api/xuetang/lesson），并做字段映射 | **P0** |
| A5 | api.js:246-250 → share.py:40 | generateShareCard 传咨询 ID，后端去 data/reports/{id}.json 找文件（share.py:24-32）→ 几乎必然 404；且响应没有前端注释声明的 `imageUrl` | 后端改为按 consultation_id 从 DB 读（或前端传 reading_id）；响应补 image_url；前端接生成图 | **P1** |
| A6 | xuetang.js:226-234 → xuetang.py:34-52、58-125 | 即使补上封装，契约仍不匹配：前端要 `res.topics:[{id,name,description,lessonCount}]`，后端返回 `{curriculum:[{level,topics:[{name,level}]}]}`；lesson 前端要 `res.lesson:{title,...}`，后端返回 `{topic,content,...}` | 前端适配器或后端改响应结构（推荐后端兼容前端契约） | **P1** |
| A7 | hehun.js:206-226 → hehun.py:22-42 | 前端发 `person1:{birthYear,...}`，后端要 `person_a:{year,...}`；前端读 `result.score`，后端响应键 `total_score` | 前端组装/读键对齐后端 | **P1** |

### B 类 — 前端占位/演示数据，后端有接口但没接

| # | 位置 | 现状 | 建议修复 | 优先级 |
|---|---|---|---|---|
| B1 | me.js（全页）+ app.js:51 | me 页全静态：无「更正档案/首次设置八字」入口（后端 POST /api/user/bazi 已就绪）、无会员入口（后端 /api/membership/{uid}/upgrade 模拟支付已就绪）、无订阅开关（后端 /api/user/subscription 已就绪） | me 页补三行可点击项：八字档案（表单页：year/month/day/hour/minute/city/gender/calendar，对应用户.py:44-52 契约）、会员中心、每日推送开关 | **P0**（八字设置是今日页个性化+合盘的前提） |
| B2 | love.js:371-393 | 假数据兜底 | 见 A1，删除 | **P0** |
| B3 | payment.js:3,143-146 | DEV_MODE 演示支付 | 后端补齐 pay 接口后置 false | **P0** |
| B4 | chat.js:150 | keep 反馈仅本地 | 后端 /api/feedback/{cid} 支持 keep 值（现只认 positive/negative，main.py:917），前端 keep 也上报 | **P2** |
| B5 | reports.js:41-52 | 只列前 4 条，行不可点，无详情页（api.js:197 已封装 getReportDetail） | reports 行加 bindtap → 详情视图（fullContent/luckyColor 后端已返回，main.py:613-638） | **P1** |
| B6 | xuetang.js:5-186 | DEMO_TOPICS/DEMO_LESSONS 内置内容，后端课程引擎未接 | 见 A4/A6 | **P1** |
| B7 | today.js:77-94 | 失败静默保持原型文案；无八字时是通用黄历 | 接 B1 的八字设置后自然个性化 | **P1** |
| B8 | api.js:118/258/197/154/342/353 | getScenarios/getPricing/getDateFortune/getReportDetail/tts/getOrders/checkPurchase 封装闲置 | 按产品规划接入（或从 api.js 删除以免误导） | **P2** |
| B9 | main.py:933/966 vs api/calendar.py:87 | POST /api/calendar/daily、/week 与 GET /api/calendar/today 并存未用 | 统一到一条链路 | **P2** |

### C 类 — 前后端都没有 / 流程整体缺失

| # | 现状 | 建议 | 优先级 |
|---|---|---|---|
| C1 | **八字设置/更正档案表单页**：前端无表单页（旧版有，墨韵版被删）；后端接口有但无调用方 | 新增页面：四柱输入 + 城市 + 性别 + 历法，POST /api/user/bazi，成功后刷新今日页/me 页 | **P0** |
| C2 | **会员购买 UI**：me 页无会员入口；后端只有模拟升级接口 | 新增会员页（价格/权益/支付），对齐套餐 ID（前端 monthly/first_month vs 后端 basic/pro/annual） | **P1** |
| C3 | **分享链路**：reports/love 仅微信内置分享，/api/share 生成图未接 | 接 A5 + shareCard.js（已有本地 canvas 绘制能力） | **P2** |
| C4 | **订阅推送设置 UI**：无开关；后端订阅接口+_daily_push_worker（main.py:168-194）+scripts/daily_push.py 已有 | me 页加开关，调 /api/user/subscription；确认 subscribeMessage 模板（微信订阅消息授权） | **P1** |
| C5 | **登出/换号**：无入口 | me 页加登出（清 token + security.js clearSecure） | **P2** |
| C6 | **数据导出/删除（PIPL）**：后端有（main.py:799/814），前端无入口 | privacy 页加"导出我的数据/注销并删除"按钮 | **P2** |
| C7 | **报告详情页**：无（见 B5） | 新建 | **P1** |
| C8 | **语音输入/播报**：tts 后端已通（main.py:662，实测 405 即路由活），前端无按钮 | chat 页加麦克风/朗读 | **P2** |
| C9 | **hehun/qimen/xingming/xuetang 入口**：5 页未注册（app.json 只有 6 页）且无导航 | 注册页面 + today/chat 加入口（如快捷笺） | **P1** |
| C10 | **face-reading/palm-reading**：后端已实现（main.py:1030/1092），前端无入口（今日页"随手截屏"为静态提示） | 后续迭代再接 | **P2** |

---

## 审计 6：数据安全机制（用户原话"数据非常重要，要考虑到防的机制"）

| # | 项 | 现状（证据） | 风险 | 建议 | 优先级 |
|---|---|---|---|---|---|
| S1 | **数据库备份** | ❌ **无任何自动备份**：scripts/ 下无备份脚本；crontab 无 fortune-agent 条目（仅 prophet_futures 任务）；/mnt/d/fortune-data/ 无 DB 备份副本（warehouse/backup 是爬虫仓库目录）。DB：/mnt/d/fortune-data/userdata/fortune.db（1.7MB，单文件） | 数据库损坏/误删=全部用户八字与记录丢失 | 新增每日备份脚本（sqlite `.backup` 到 /mnt/d/fortune-data/backups/ + 第二位置），crontab 每日 03:00 执行，保留 30 天 | **P0** |
| S2 | **敏感字段加密** | ❌ bazi_info 以**明文 JSON** 存 users 表（dao.py:40 `json.dumps(bazi_info)` 直接落库）；DataEncryptor 只用于数据导出/删除时的 user_id 混淆（privacy.py:161/299），**未用于任何业务字段**；`ENCRYPTION_KEY` 未配置（security/router.py:122 显示 encryption_at_rest=false） | 数据文件被拷走即泄露全部命盘 | 字段级加密：bazi_info 用 AES-GCM（encryption.py 已有能力）加密后落库，密钥走环境变量；或至少全库级加密。注意会影响导出/删除实现（privacy.py 已有解密路径可复用） | **P0** |
| S3 | **日志脱敏** | audit.py 记录 action/user_id/IP/input_preview（main.py:80 截断 80 字符）；uvicorn access log 无 body。未收集手机号/身份证字段 | 基本无高危敏感字段入日志；但 audit.log 明文含 user_id+IP+问题摘要 | 保持截断策略；audit.log 文件权限收紧（0600）；巡检日志目录 | **P2** |
| S4 | **接口鉴权（越权）** | 全部 api/*.py 无一处 `Depends(require_auth)`（grep 实证）；几乎所有接口以 query/path 的 user_id 直接取数 | **任意人可查/改任意用户数据**（明细见 §审计8） | 全局 JWT 鉴权中间件 + 从 token sub 取 user_id（见 §审计8 修复方案） | **P0（红线）** |
| S5 | **输入安全** | sanitizer 仅用于 /api/chat 的 message/voice_text（main.py:704-722）；hehun/qimen/advisor/xingming 的 city/question 等输入无 sanitize；DAO 全部参数化查询（`WHERE user_id = ?`，dao.py:26 等，无 SQL 注入面） | 注入风险主要在 LLM prompt（相对低危） | sanitize 覆盖所有含自由文本的接口（qimen.question、advisor、hehun 备注）；RAG/LLM 输入也过 sanitizer | **P1** |
| S6 | **限流覆盖** | RateLimitMiddleware 仅覆盖 /api/chat、/api/analysis、/api/face-reading、/api/palm-reading、/api/calendar、/api/compatibility、/api/user、/api/feedback（ratelimit.py:139-148）；/api/reports、/api/share、/api/xuetang、/api/tts、/api/pricing、/api/membership、/api/push-* 不限流 | 爬虫可批量拉取/打爆 | 补全路径列表；chat 类接口限流收紧 | **P1** |

---

## 审计 7：登录模块（用户原话"登录的话也要做好"）

| # | 项 | 现状（证据） | 影响 | 建议 | 优先级 |
|---|---|---|---|---|---|
| L1 | **微信 code2session** | ❌ user.py:267-281 `_wechat_code_to_openid` **永远返回 None**（真实微信接口调用被注释 TODO）；/api/security/token 同样是模拟（security/router.py:133-134 注释说明） | 所有用户都是 dev 模式：user_id = `user_` + 随机 uuid（user.py:75）——**同一用户每次登录 ID 都不同**，八字/记录/会员按 ID 分裂，无法跨会话识别 | 配置 AppID/AppSecret，实现 code2session；无 secret 时至少用 wx.login code 换取稳定 openid 的一次性方案 | **P0** |
| L2 | **JWT_SECRET 稳定性** | ❌ 未设置 JWT_SECRET_KEY 时启动自动生成随机 key（auth.py:34-41），**重启服务全部 token 失效**，用户全被登出 | 生产不可接受 | 固定 JWT_SECRET_KEY 到环境变量/配置；secret 强度≥32 字节 | **P0** |
| L3 | **登录态/数据分裂** | 登录拿到的 user_id（user_xxx）**从未被前端用于后续请求**：app.js 从不设 globalData.openid；today 用 `default_user`（today.js:80）、chat 用 `miniprogram_user`（api.js:138）、reports 用 JWT sub（user_xxx）——**三套身份并存** | 今日运势不个性化、报告列表恒空、咨询记录查不到 | 登录后全局存 userId，api.js 所有请求统一从 token 解析或显式带 userId | **P0** |
| L4 | **401 处理/刷新** | api.js:55-60 收到 401 只清 token+toast，**不自动重登**；无 refresh token 调用（后端 /api/security/token/refresh 存在但未用）；token 有效期 7 天（auth.py:52,71） | 过期后用户需重进小程序才恢复 | api.js 401 时自动重跑 wx.login→/api/user/login；或接 refresh 机制 | **P1** |
| L5 | **会话保持** | ✅ 已做：security.js setSecure('auth') 存 token（XOR 混淆，storage key `YLM_V5_*`），app.js initLocalMode（L74-88）重启恢复 | 保持 OK；混淆非加密（前端约束不了安全边界，可接受但认知要正确） | 保持；建议 token 放内存+refresh 持久化 | **P2** |
| L6 | **登出** | ❌ 无退出入口 | 换号/共用设备无法切换 | me 页加登出 | **P2** |
| L7 | **login 响应契约** | 后端返回 `{token, user:{id, has_bazi, is_new}}`（user.py:100-107），前端 app.js:51-52 读 `res.user.bazi`（**字段不存在**）→ hasBazi/baziInfo 恒空 | me 页/今日页永远拿不到八字 | 后端 user 对象补 bazi 字段，或前端改调 /api/user/profile | **P1** |
| L8 | **存储敏感数据** | security.js 用固定 XOR key `YLM_V5_2024`（security.js:9,18）"加密"存储 love_form（两人生日） | 生日/命盘在本地可被解出；XOR 不是加密 | 换用微信安全存储/减少存储敏感数据 | **P2** |

---

## 审计 8：接口暴露面逐条清单（红线——"绝对不允许接口/数据暴露"）

**当前总体结论：后端 60+ 个业务路由中没有一个强制鉴权；凡以 user_id 为 query/path 参数的接口均可任意读取/修改他人数据（IDOR），其中包含删除与导出接口。以下全部为 P0。**

| # | 接口（文件:行） | 当前鉴权 | 暴露内容 | 修复方案 |
|---|---|---|---|---|
| E1 | GET /api/calendar/today?user_id=X（api/calendar.py:87） | 无（可匿名带任意 user_id） | 他人八字个性化运势 | 改为从 JWT sub 取 user_id，拒绝 query 覆盖 |
| E2 | GET /api/user/profile?user_id=X（api/user.py:110） | 无 | 他人八字+推送设置+咨询统计 | 同上 |
| E3 | POST /api/user/bazi?user_id=X（api/user.py:152） | 无 | **可覆写他人八字** | 同上（写接口重点防护） |
| E4 | POST /api/user/subscription?user_id=X（api/user.py:177） | 无 | 可改他人推送设置 | 同上 |
| E5 | GET /api/user/export/{user_id}（main.py:799） | **无** | **导出他人全量数据（PIPL 接口裸奔）** | 必须登录+owner 校验+审计；与 /api/security/user/{uid}/export 去重 |
| E6 | DELETE /api/user/data/{user_id}（main.py:814） | **无** | **可删除他人全部数据** | 必须登录+owner 校验+确认二次验证 |
| E7 | GET /api/user/{uid}/history、/accuracy（main.py:885/898） | 无 | 他人全部咨询记录 | JWT 强制 |
| E8 | GET /api/membership/{uid}、POST /api/membership/{uid}/upgrade（main.py:1235/1243） | 无 | 查/改他人会员等级 | JWT 强制 + owner 校验 |
| E9 | GET/POST /api/push-settings/{uid}（main.py:1197/1205） | 无 | 改他人推送 | JWT 强制 |
| E10 | GET /api/dashboard/{uid}、/api/share-card/{uid}（main.py:1134/1146） | 无 | 他人聚合数据/分享文案 | JWT 强制 |
| E11 | POST /api/chat {user_id 自填}（main.py:697） | 无 | 冒用任意 user_id：消耗他人配额、在他人名下写入咨询记录 | user_id 一律从 JWT sub 取，body 字段忽略/校验一致 |
| E12 | POST /api/feedback/{cid}（main.py:914） | 无 | 对任意咨询 ID 刷反馈（可探测 ID） | 校验咨询归属当前用户 |
| E13 | GET /api/reports（main.py:598） | JWT 可选（无 token 返回空，已防） | 有 token 时安全；无强制 | 改为必填 token |
| E14 | GET /api/hehun、/api/qimen、/api/xingming、/api/advisor、/api/hourly-fortune、/api/xuetang/*、/api/tts | 无 | 无用户数据（计算类），但可被刷（成本） | 限流补齐即可，可不强制登录 |
| E15 | /api/security/user/{uid}/export、/data、/anonymize（security/router.py:195/231/267） | 需核实（与 E5/E6 重复实现） | 与 E5/E6 同源风险 | 统一收口到一套鉴权实现 |
| E16 | /api/admin/stats、/api/admin/active-members（main.py:1279/1289） | admin_key 可为空→**直接放行**（main.py:1229-1232） | 全量付费会员数据 | 无 admin_key 时必须拒绝，禁止空 key 放行 |

**统一修复方案（可一步落地）**：
1. 新增 FastAPI 依赖 `require_user`（security/auth.py:271 已有 `require_auth`，只是没人用）：从 Authorization Bearer 解 token，返回 sub 作为 user_id；
2. 除白名单（/api/user/login、/api/health、/api/pricing、/api/scenarios、/api/xuetang/topics、HTML 页、/share、/report）外**全部业务路由挂该依赖**；
3. 接口内一律用 `user_id = request.state.user_id`，**删除/忽略** query/path 中的 user_id 入参（或校验二者一致后报 403）；
4. 导出/删除接口：登录 + owner 校验 + audit_logger 记录（main.py 导出已记审计，补鉴权即可）；
5. admin 接口：admin_key 缺失时一律 403（改 main.py:1229-1232）。

---

## 性能 / 维护性建议

| # | 项 | 现状 | 建议 | 优先级 |
|---|---|---|---|---|
| P1 | **服务稳定性（卡死）** | 2026-08-07 20:44 起 8767 全端口超时（含 /api/health），进程 R 状态、86 线程，30min+ 未恢复 | 立即重启；排查事件循环阻塞（同步 LLM/embedder/文件调用无超时）；加请求超时中间件（如 asgi-timeout-middleware）+ 健康检查看门狗；uvicorn 改多 worker | **P0** |
| P2 | **前端超时配置矛盾** | api.js timeout=30s（api.js:11），但 app.json networkTimeout.request=**15s**（app.json）——微信侧 15s 先断；chat 冷启动已知 59s → 必然超时 | 统一提高：app.json request 设 60s+，api.js 60s；chat 首次请求前预加载（或后端 chat 改流式/任务轮询） | **P0** |
| P3 | 前端重复/死代码 | love.js 双 onSubmit（L303/L495）+ 12KB DEMO_ANALYSIS；hehun.js getDemoResult（L283-307）与 qimen FALLBACK_*、xingming getDemoResult 均未被 catch 调用；api.js 8 个未用封装 | 清理或接通，二选一 | P2 |
| P4 | 后端重复路由 | /api/user/export/{id} 与 /api/security/user/{id}/export；/api/user/data/{id} 与 /api/security/user/{id}/data；/api/user/subscription 与 /api/push-settings；/api/user/feedback 与 /api/feedback/{cid}；/api/calendar/today 与 POST /api/calendar/daily | 收敛为一套，main.py 1338 行拆到 api/ | P2 |
| P5 | 前端错误处理 | 大部分 catch 静默 console.warn + 兜底文案（love 假结果、today 原型文案、chat 精选文案）——**用户无感知失败**，且无法区分"真实结果"与"兜底" | 兜底内容明示"离线示例"；关键页面失败时给重试按钮 | **P1** |
| P6 | 缓存/防抖 | 前端无缓存无防抖（today 每次 onLoad 请求）；后端 GET /api/calendar/today 有 6h 缓存、/api/reports 无缓存 | reports 加 60s 缓存；输入框加防抖 | P2 |
| P7 | 分享契约 | share.py 响应无 imageUrl 且数据源是旧 report JSON（share.py:20-32） | 见 A5 | P1 |
| P8 | 未用路由 | advisor/hourly/visual_report/security router 前端零使用，RAG 依赖 vectordb 未重建（main.py:283-289 警告） | 规划接入或下线；vectordb 重建按 memory 中计划继续 | P2 |

---

## 推荐实施顺序

**第一批（P0，修复后产品才能"真"上线）**：
1. **重启 8767 服务**并加超时/看门狗（§P1）——当前服务已死；
2. **接口鉴权改造**（§审计8 统一方案）：全局 JWT 依赖 + user_id 从 token 取 + 导出/删除/会员/admin 接口收口（E1-E16）——红线；
3. **登录闭环**：实现 code2session（L1）+ 固定 JWT_SECRET（L2）+ 前端统一身份（L3）+ login 契约修复（L7）；
4. **支付/会员链路**：后端补 5 个 404 接口（A2），套餐 ID 对齐；前端 DEV_MODE 关（B3）；
5. **八字设置表单页**（B1/C1）——个性化、合盘、报告的前提；
6. **love 页接通** /api/compatibility 并删除假数据（A1/B2）——love 是付费转化核心；
7. **四术页接通**：api.js 补 5 个方法 + 契约映射（A4/A6/A7）+ 注册 5 页入口（C9）；
8. **数据库备份脚本 + 每日 cron**（S1）、bazi 字段加密（S2）；
9. **前端超时统一 60s**（P2）。

**第二批（P1）**：reports 详情页（B5/C7）、会员中心 UI（C2）、订阅开关（C4）、sanitizer/限流补全（S5/S6）、错误态用户可感知（P5）、getUserProfile 接通（A3）、分享卡片（A5/P7）。

**第三批（P2）**：keep 反馈上报（B4）、语音（C8）、登出（C5）、PIPL 导出/删除入口（C6）、死代码清理（P3/P4）、本地存储加密升级（L8）。

## 修复完成记录（2026-08-07 晚）
- P0 服务卡死：120s 超时中间件+LLM 入线程池+全 httpx 超时（已重启验证）
- P0 接口零鉴权：60 路由全挂 require_user，user_id 取 token sub，IDOR 16 点全封（test_auth.py 27/27 PASS）
- P0 登录闭环：真实 code2session（.env WECHAT_APP_SECRET 待填）+ dev 稳定 openid + JWT_SECRET_KEY 固定（.env）
- P0 前端身份统一：default_user/miniprogram_user 消灭，统一 wx_dev_user（端到端实测）
- P0 数据加密：bazi/consultations AES-256-GCM，旧明文自动迁移
- P0 备份：scripts/backup_db.sh 每日+保留14份+crontab+启动兜底（首份已生成）
- A 类 404：love/pay/create/pay/subscribe/user/member/user/orders/user/purchase 全部实现（test_gap_api.py 65/65 PASS，mock 支付，WECHAT_PAY_ENABLED 配置切换）
- 四术页：后端契约适配+前端 5 api 方法+注册 app.json（hehun 43分/qimen 九宫/xingming 五格/xuetang 12话题 实测）
- 八字表单页 pages/bazi/bazi 新建（公历仅支持，回显/保存实测 200）
- love 页去伪随机假数据（真实调用+明确报错）
- 遗留：sessions 明文（后续加密）、WECHAT_APP_SECRET 待填、真微信支付配置

---

## 2026-09-10 k20 加密层审计收口（后附，正文各行为 2026-08-07 审计时点记录）

- S2（敏感字段加密）：该时点 ❌ 已收口——users.bazi_info / sessions.content 等
  自后续批次起全部 AES-256-GCM 落库；旧明文读时懒迁移。本 k20 批逐点核实
  全部加密消费点（清单见 docs/DATABASE.md「加密字段」+ plans/2026-09-10-k20-
  aes-encryption.md），确认无明文旁路。
- L5/L8（前端 XOR 混淆 security.js）：认知保持正确（混淆≠加密），仍为客户端
  本地 storage 混淆层、不在服务端 at-rest 加密范围内；升级端侧真加密
  （crypto-js/微信安全存储）待 PM 拍板。security.js 头部已附 2026-09-10 审计标注。
- 加密层算法核实（2026-09-10 审计确认）：AES-256-GCM（32B 密钥 / 12B 随机
  nonce / 16B tag，篡改即解密失败）；密钥轮换 v1/v2 版本化、末键为当前键；
  密钥解析坏格式告警回退、单带标签键 "v1:key" 合法（k20 修复）。
- 遗留修正：正文「sessions 明文（后续加密）」已过期（session_dao.py 自加密
  起 content 密文落库），以本文 S2 收口说明为准。
