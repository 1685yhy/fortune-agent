# k13 重试重复消息根治（retry-dedup）——实施设计文档

- 日期：2026-09-09　分支：k13-retry-dedup（BASE = main b8c4db1）
- 范围：`src/api/chat_stream.py`、`src/bot/handler.py`、`src/main.py`、`src/storage/session_dao.py`、`miniprogram/utils/api.js`、`miniprogram/utils/streamHost.js`、`tests/test_k13_retry_dedup.py`（新增）、`miniprogram/tests/chatretry.test.js`（新增）、`docs/superpowers/progress.md`、本文档
- 红线：不动 tool_calls.py、不改 .env、不碰生产库、不 push、不重启；git add 仅本批文件（预存脏态 data/eval/results、data/memory/.json、ledger.json、comparison_runs.jsonl 不 add）

---

## A. 现状核查结论（实锤打点，2026-09-09 代码级核查 + node 复现探针）

### A1. 用户实诉与后端实证
- 用户实诉：点「重试」出现多条重复的已发送消息。
- 后端实证（会话 s_mtme8nc9afzo）：同一句用户消息存了 4 份 user 行（#29 01:55 / #31 01:57 / #33 02:02 / #35 02:06），每份带独立 assistant 行；#35 的 assistant 缺失（失败轮）。**节奏 2/5/4 分钟——远超任何 20s 级窗口**。

### A2. 前端 retry/regen 现状路径（chat.js / streamHost.js 逐行走查 + node 探针复现）

三条入口共用同一实现 `streamHost.retry(msgId, text, tag, img)`（streamHost.js:455）：

1. **失败气泡「重试」钮**（chat.wxml:217 `wx:if="{{item.error}}"` → chat.js:849 `retryStream`）——重试钮挂在 AI 失败气泡上（data-id=AI id，B4-1-fix I1），点击 → `streamHost.retry(id, retryText, tag, img)`；
2. **k10「重新生成」菜单**（chat.js:1266 `k==='regen'`，canRegen 仅末条 AI 且非 error）→ 同一 `streamHost.retry(msg.id, retryText, tag)`；
3. **断点续传中断恢复**（chat.js:688 `_recoverInterrupted`：进入页面发现残留 streaming 且无输出 → toast「正在重新生成」→ 自动 `streamHost.retry(stuck.id, text, tag)`）。

`streamHost.retry` 现状实现 = **重发整条用户消息，不是 regenerate**：
```js
retry(msgId, text, tag, img) {
  if (!t || this.streaming) return;
  this.messages = this.messages.filter((m) => m.id !== msgId);  // 只移除失败 AI 气泡
  this._startStream(t, tag || '', null, img || null);            // 原样重发 → 新 user 笺 + 新 AI 笺
}
```
**node 复现探针（真实 require streamHost，mock api.chatStream）**：`[u1 提问][a1 失败]` → retry → 消息列表 = `[u1][u2 同文][a2 streaming]`——**用户提问气泡当场复制一份**，且每 POST 一次后端 process() 就插一条新 user 行。

### A3. 后端落库路径核查
- 用户消息唯一常规落库点：`handler.process()` 内 4170 行 `session_dao.add_message(user_id, "user", msg, …)`（/api/chat 与 /api/chat/stream 共用 process；voice 转写也走 process；image 路径 `_handle_image_*` 不落 user 行）。
- sessions 表 append-only（「原始对话全量留存一条不丢」红线），无作废/删除接口（k10 P4.5 voidConsultation 后续项，本批不新造）。

### A4. A 现状结论（定稿依据）
- **失败气泡重试 = 重发整条用户消息**（连带 k10 regen 与自动恢复同路）：每次点击/每次进入页面自动恢复都会本地复制用户气泡 + 后端新增 user 行 → 双端重复同源同因。
- k10「重新生成」**没有独立后端 API**（k10 P4.5 已核实），与重试同为 streamHost.retry 本地行为。因此「复用 k10 重新生成的守卫与 API」= 复用同一守卫链 + 同一发送通道，但把**语义改为对既有轮次原地 regenerate**。
- 后端 20s 级窗口护栏**单独无法根治实证节奏（2-5 分钟/次）**——每次恢复重发都在窗口外 → 前端必须让后端识别「这是对既有轮次的重试」→ **请求带 regen 标记**为主机制，窗口护栏兜底（防双击/双端并发/旧客户端裸重发）。

---

## B. 语义定稿

### B1. 前端：retry/regen/自动恢复 → 同轮原地 regenerate（不重发新轮）
- 保留该轮的唯一用户气泡（前向最近 role=user），把目标 AI 气泡**原位替换**为新的 streaming AI 气泡重流——不再追加第二个用户气泡。
- 目标 AI 气泡已不存在（重复点击）→ 静默 no-op（防幽灵重发）。
- 轮次用户气泡已不存在（异常态）→ 回退旧语义补建用户气泡（保可用，内容仍唯一）。
- 图片轮次：自动从保留的用户气泡回取 image（顺带修复 _recoverInterrupted 图片轮自动恢复丢图问题）；请求仍 message_type=image + 原 image_url。
- 请求 payload 携带 `regen: true`（api.js chatStream 与 chat 普通回退请求都带；streamHost 按轮置 `curRegen`，回退/收尾后复位——绝不泄漏到下一轮普通发送）。
- k10 菜单 canRegen/retryStream/自动恢复可见性守卫不变（复用）。

### B2. 后端：regen 标记 + 窗口护栏（核心）
`ChatRequest` 新增可选字段 `regen: bool = False`（语义=对既有轮次的重新生成/重试补答）。`handler.process(..., regen=False)`：

在用户消息落库点（唯一 site，4170）改走新的原子方法 `SessionDAO.add_user_message_dedup(...)`（返回 `{"inserted": bool, "matched_id": int|None}`）：

1. **regen=True（前端重试/regenerate 标记）**：同会话存在规范化同文 user 行 → **不新插**（补答走正常生成链：assistant 照常落一条，不新插 user）；无匹配行（缓存命中轮等从未落库）→ **照插**（审计完整）。
2. **regen=False + 同会话最近规范化同文 user 行距其 created_at ≤ 窗口（默认 20s，模块常量可配）**：判定重复提交/双击/旧客户端裸重发 → **不新插**（assistant 由正常生成链补上）。
3. **其余（跨会话/异文/超窗同文=用户真重复提问）→ 正常插入**。
4. **判定+插入同一 `BEGIN IMMEDIATE` 事务**：并发（双击/双端/双进程）检查与写入原子（WAL 单写者 + busy_timeout=10s 排队）。锁超时/异常 → 向上抛，handler 退回原 add_message（对话一条不丢铁律，宁重勿丢）。
5. 窗口实现用 DB created_at（SQLite `datetime('now')` UTC，与落库同钟）对 `julianday` 差值计算——无内存态，多进程安全。规范化比较在 Python 侧解密后做（content 加密落库，无法 SQL 侧比较）。

**规范化规则**（纯函数，单测锁定）：`str.strip()`——按 Unicode isspace 剥首尾空白（含全角空格 　、换行 \r\n）；**内部空白不折叠**（不过度归并，防异文同判：`"帮我 看看" ≠ "帮我看看"`）。

### B3. 窗口值依据与边界（取舍记录）
- 窗口 20s 依据：真实「双击/连点/双端同发」间隔为秒级；用户阅读+再发 ≥ 数十秒——20s 误伤面极小。实测重复节奏（2-5 分钟）不在窗口内——**该节奏由 regen 标记覆盖**（前端重试入口全部带标记），窗口仅兜底无标记重复（旧客户端/竞态），故不放大窗口（放大会吞掉窗口外用户真重复提问的审计行——sessions 全量留存红线）。
- 边界一（有答案轮窗口内重发）：已答轮 ≤20s 内再来同文 → 不插 user 行 + 仍跑生成链（多一条 assistant=重试方可见的补答；旧 assistant 保留——append-only 红线，作废/替换属 k10 P4.5 后续项）。
- 边界二（缓存轮 regenerate）：缓存命中轮无任何落库行 → regen 找不到匹配 → 照插 user 行 + 生成（保证审计不缺口）。
- 边界三（历史残留行）：修复前已产生的重复行不清理（本批不写清理脚本，需用户拍板）；护栏只保证**不再新增**。
- 边界四（mid-list 失败气泡重试）：少见路径——原地重流（气泡位置不变）；后端行照尾追加，与现状一致。
- 边界五（中断恢复自动重发）：_recoverInterrupted 无输出残留轮 → 自动重发改为**自动 regenerate**（带 regen 标记），恢复节奏无论快慢都不再新增 user 行。
- 不拦的：跨会话同文（新开对话重问）、超窗同文（真重复提问）、异文。

### B4. 并发安全（C）
`BEGIN IMMEDIATE` 事务内「查最近同文行 → 判定 → INSERT」原子化；并发第二提交者等锁（busy_timeout 10s）后必然看见第一提交者刚插入的行 → 判定不插。测试用双线程真并发打点（同文件两连接）。

---

## C. 实现清单

### C1. `src/storage/session_dao.py`
- 模块常量 `RETRY_DEDUP_WINDOW_SECONDS = 20`、`_DEDUP_LOOKBACK = 10`。
- 静态方法 `normalize_dedup_text(text) -> str`（规范化规则见 B2-5）。
- `SessionDAO.add_user_message_dedup(user_id, content, *, intent=None, emotion=None, tool_calls=None, retrieval_hit=None, model=None, safety_flag=None, temp=False, session_id=None, regen=False, window_seconds=RETRY_DEDUP_WINDOW_SECONDS) -> dict`（BEGIN IMMEDIATE；查最近 10 条同作用域 user 行解密比对；返回 `{"inserted", "matched_id"}`）。

### C2. `src/bot/handler.py`
- `process(...)` 末尾新增 kwarg `regen: bool = False`（docstring 说明）；4170 用户消息落库点改为调用新私有方法 `_persist_user_turn(...)`。
- 新增 `_persist_user_turn(self, user_id, msg, analysis, deep, session_id, regen=False)`：走 `add_user_message_dedup`；异常退回原 add_message；dedup 命中（inserted=False）记 info 日志。

### C3. `src/main.py`
- `ChatRequest` 加 `regen: bool = False`。
- `/api/chat` 的 process 调用透传 `regen=req.regen`。

### C4. `src/api/chat_stream.py`
- `ChatStreamer.events`：读 `regen = bool(getattr(req, "regen", False))`，`_run` 的 process 调用透传。

### C5. `miniprogram/utils/api.js`
- `chat()` 与 `chatStream()`：`if (options.regen) data.regen = true;`

### C6. `miniprogram/utils/streamHost.js`
- `retry(msgId, text, tag, img)` 语义改为同轮原地 regenerate（B1 全规则）；内部抽取 `_buildAiMessage`/流状态启动共用段（`_startStream` 与 regenerate 共用同一状态机）。
- `_init` 增 `this.curRegen = false`；请求 options 带 `regen: this.curRegen`；`_onDone/_onError/_onAbort` 收尾复位 `curRegen=false`；`_onError` 的普通回退请求同样带 regen。

### C7. 测试
- 后端新增 `tests/test_k13_retry_dedup.py`（见 D）。
- 前端新增 `miniprogram/tests/chatretry.test.js`（见 D），回归邻接 node 套件。

### C8. 文档
- 本文档；`docs/superpowers/progress.md` 追加 k13 段。

---

## D. 测试清单

### D1. 后端 `tests/test_k13_retry_dedup.py`（tmp db 直连，无 LLM；created_at 用 SQL UPDATE 控龄，不 sleep）
1. 同会话同内容 10s 内第二次提交 → `inserted=False` 且 user 行数不变；随后补一条 assistant（= 生成链补答完成）→ 恰一轮 `[u,a]`（补答触发语义）。
2. 超窗（created_at 回拨 30s）同文 → `inserted=True`（2 行）；异文 → `inserted=True`。
3. 跨会话同文 → 均插入（不拦）。
4. 规范化：尾随换行/全角空格/首尾空白 → 判同文不插；内部半角空格 → 异文照插。
5. regen=True：有匹配行（且超窗）→ 不插；无匹配行 → 照插；regen 列仍加密落库一致。
6. 并发：双线程同文同会话同时提交 → 恰 1 行（BEGIN IMMEDIATE 原子性打点）。
7. handler 级 `_persist_user_turn`：object.__new__ 装配（session_dao + llm 桩 + _safety_flag 桩）——两发同文 = 1 行；`add_user_message_dedup` 抛异常 → 退回 add_message 仍落库（宁重勿丢）。

### D2. 前端 `miniprogram/tests/chatretry.test.js`（node --test，mock api）
1. 失败轮 retry：`[u1][a1(err)]` → 消息 = `[u1][a2 streaming]`（**u 气泡不复制**）；请求 options.regen===true；retryText 原样。
2. 双击：首击后目标气泡已消失 → 二击 no-op（无第二次请求、消息不变）。
3. 图片轮 retry：u 气泡保留 image；请求 messageType=image + 原 imageUrl + regen。
4. k10 regen（chat.js actItem regen 接线）：调 streamHost.retry(原 id/text/tag) 不变（回归）。
5. 页面 retryStream：仍调 streamHost.retry(id, text, tag, img)（接线回归，chatimage 同款）。
6. api.js：chatStream/chat 带 options.regen → payload.data.regen===true；不带 → 无 regen 键。
7. 收尾复位：一轮 done/error 后下一次普通发送不携带 regen。

### D3. 回归邻接（跑目标 + 邻接，勿全量）
- 后端：`tests/test_k13_retry_dedup.py` + `test_chat_offline_resume.py` + `test_chat_stream_k7.py` + `test_single_stream_dedupe.py` + `test_chat_entry_fixes.py` + `test_handler_pillar_k7d.py` + `test_stream_disconnect_cleanup.py`
- 前端：`node --test miniprogram/tests/chatretry.test.js miniprogram/tests/chatimage.test.js miniprogram/tests/chatmenu.test.js`（chatmenu/chatimage 已覆盖 k10/retry 页面接线）

---

## E. 后续项（不在本批）
- 历史已产生的重复行清理脚本（需用户拍板口径：按 (user, session, 同文, ≤N 分钟) 归并？会动「全量留存」红线）。
- k10 P4.5：voidConsultation 作废接口（regenerate/删除此条的旧稿后端清理）独立批。
- 语音/图片轮 regen 标记的完整化（当前 voice→process 文本路径天然生效；image 轮无 user 行，天然无重复风险，regen 字段忽略）。
