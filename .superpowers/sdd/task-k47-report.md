# task-k47 报告：开发者工具真实渲染报错收口（A/B/C/D 四类）

- 工作目录：`/home/a/k47-wt`（分支 `k47-wxclean`，base=main=`67630bf`）
- 来源：控制方 2026-09-14 automator 实测 34 页 152 条 console（A=28 / B=29 / C=7 / D≈76）
- 复核方式：`node /tmp/k47_console.js <out.json> <shot.png>`（automator 驱动开发者工具 → 遍历 app.json 全部页面 → 汇总 console/exception；含 chat 页嵌套 markdown 注入 + 截图）

---

## 一、A【必须修·用户可见】WXML 模板自递归 → 嵌套 markdown 渲染不全

### 改了什么
1. **`utils/md.js`：行内节点协议由「容器 + children」改为「一层扁平叶子」**
   （递归只在 JS 解析侧发生；每个叶子自带 `cls` 样式标记）：
   - `{t:'text', s, cls}` / `{t:'link', s, url, cls}` / `{t:'code', s, cls}` / `{t:'image'}` / `{t:'cite', idx}`
   - `cls ∈ '' | 'md-strong' | 'md-em' | 'md-strong md-em'`（多类共存 = 样式叠加）
   - `inlineL2` 删除；`parseInline` 改为递归累积样式上下文（`ctx={bold,italic,link}`）+ 拍平；
     新增 `***粗斜***` 支持；未闭合标记的宽容行为、`[n]/🔗/图片/行内码` 语义不变。
2. **`utils/md.js`：引用块（块级嵌套）同样拍平** —— 实测 `md-block` 在 quote 分支里
   再调 `md-block` **同样被引擎中止**（本次实测捕获 1 条 `md-block` 递归告警，
   注入用例含 `>` 引用 → 引用内文字**整体不渲染**，旧截图里只剩一条空红线）。
   现由 `quoteLines()` 产出 `blk.lines`（每子块一行的行内节点数组），模板只调 `md-inline`；
   `blk.children` 结构保留（`chatSelect` 段落模型/纯文本口径不变）。
3. **`pages/chat/chat.wxml`**：`md-inline` 模板去掉全部 `<template is="md-inline">` 自递归，
   改单层遍历（link 走 `md-link {{it.cls}}` + `data-code`/`copyCode`）；`md-block` 的 quote 分支
   改遍历 `blk.lines`。样式类、`selectable`、段落长按 `onParaLongPress`/`data-para-id`、
   `copyCode`、cite chip、图片长按全部保留。
4. **`utils/chatSelect.js`**：`inlineText()` 支持扁平叶子（`text/link/code` 直接取 `s`），
   并保留 `children` 兼容分支。

### 证据（改前 → 改后）
| 项 | 改前 | 改后 |
|---|---|---|
| `md-inline` 递归告警（34 页） | **28** | **0** |
| `md-block` 递归告警（34 页） | 1（注入引用用例触发） | **0** |
| chat 页截图 | `.superpowers/sdd/k47-chat-before.png`：整行 `**加粗里嵌[链接]…**` **完全消失**；`仅与仅与 行内码`（加粗/斜体文字丢失）；列表项/表头缺字；**引用块只有一条空红线、无任何文字** | `.superpowers/sdd/k47-chat-after.png`：`加粗里嵌链接与内斜同现`（加粗+红色下划线链接+斜体齐全）、`仅加粗与仅斜体与行内码。`、列表项加粗、**引用两行完整显示（朱砂左线）**、表头 `表头粗` 与单元格 `x` 全部上屏 |

`node --test tests/k47_md_flat.test.js`：**10/10 pass**（含模板静态守卫：chat.wxml 内任何模板
不得 self-recursion、`md-inline` 内不得再出现 template 调用）。
`chatSelect` 段落文本/偏移回归用例（既有 `tests/chattextselect.test.js`）全绿。

---

## 二、B【必须修】`wx.getSystemInfoSync` 过时 API（29 处）

### 改了什么
按字段语义替换为新 API，**旧 API 文本全仓清零**（含注释），不留兜底引用（基础库 `libVersion=3.17.0`，
新 API 自 2.20.1 起提供；旧库/异常时走 `|| {}` + 既有默认值兜底）：
- `theme` → `wx.getAppBaseInfo()`：`app.js detectTheme()`、`utils/theme.js getTheme()`
- `statusBarHeight` / `windowWidth` → `wx.getWindowInfo()`：22 处 `wx.getWindowInfo ? … : wx.getSystemInfoSync()` 改为 `(wx.getWindowInfo && wx.getWindowInfo()) || {}`（20 个文件）
- `pixelRatio` → `wx.getWindowInfo()`：`pages/ming/ming.js`、`pages/night_mark`、`pages/zeri_plan`
- `platform` → `wx.getDeviceInfo()`：`pages/chat/chat.js _textOverlayFeasible`
- 测试桩同步：`tests/chattextselect.test.js` 改 mock `getDeviceInfo`（语义等价，异常→甲 的用例保留）

### 证据
| 项 | 改前 | 改后 |
|---|---|---|
| `wx.getSystemInfoSync is deprecated…` | **29** | **0** |
| 全仓 grep（非 tests） | 29 处引用 | **0 处** |

---

## 三、C【必须修】`console.error({})` 空对象日志（7 处）

### 改了什么
1. 新增 **`utils/log.js`**：`errText(e)`（错误码/HTTP 状态/errMsg|message 摘要，压平截断；
   普通对象 `{}` → `empty-object`）、`logErr(scene, err, extra)`、`logWarn(...)`——
   统一「场景名 + 关键字段」，**不打裸对象、不含用户隐私原文**。
2. 全仓 **40 处**「裸错误对象」日志调用点改为 `logErr/logWarn`（含 `app.js` 全局错误/未处理 Promise、
   `utils/api.js` 4 处、`utils/security.js` 4 处、`utils/shareCard.js` 6 处、chat/today/love/share/
   ming/hehun/mingren/mingren_detail/qimen/xingming 各页），共 14 个文件加 require。
3. 判定为「实测可复现的 `{}` 形态来源」的 `pages/mingren/mingren.js` 列表失败日志：
   `[mingren] 列表加载失败: {}` → `[WARN] mingren 列表加载失败 — 登录已过期，自动重登失败`。

### 证据
| 项 | 改前 | 改后 |
|---|---|---|
| `console|error|[{}]` | **7** | **0** |
| `…失败: {}` 形态（页面 warn） | 2（`[mingren] 列表加载失败: {}`） | 0（改为可定位文案，见上） |
| 34 页 console 总数 | 152 | **8**（其余为既有页面降级提示 `[Today] API 不可用` 等 5 条 + `ThemeChange` 监听提示 1 条 + 2 条页面级重登失败提示） |

**溯源结论（重要，见「遗留」）**：应用侧 `console.error` **无裸对象实参**（全仓 18 处均带场景字符串），
且在开发者工具内包裹 `console.error` 采集调用栈后，**应用侧 error 级日志为 0**；
7 条 `[{}]` 与 B 类「getSystemInfoSync 弃用」运行时告警同源（同一次运行中它与 B 类多出的 1 条
告警同时出现），B 修复后两类一并归零。

---

## 四、D【必须修】重登逐请求热重试（改前 34 页 76 条同因告警）

### 改了什么（`utils/api.js`）
1. **退避**：连续失败 n 次 → 退避 `2^n` 秒（2s/4s/8s…，封顶 60s），退避期内 401 直接按
   「重登失败」返回，**不再发起 `wx.login`**（消除逐请求热重试）。
2. **上限**：连续失败达 `RELOGIN_MAX_ATTEMPTS=3` → 转 **10 分钟长冷却**（低频重试、可自愈，
   避免网络/服务端恢复后无法静默重登的行为倒退）。
3. **不可恢复短路**：`41002` / `appid missing`（errCode 或 errMsg 命中）→ **首次失败即停止本会话尝试**。
4. **降噪**：`warnOnce` 同因只报 1 条（含错误码/请求路径，路径去 query 防隐私），其余计数；
   恢复（登录成功）时 `warnOnceRecover` 汇总「此前抑制同因告警 N 条」。
5. **恢复点**：`applyAuth()`（登录成功唯一写入口）→ `clearReloginHold()` 清零退避/上限/fatal，
   「会话过期 → 静默重登 → 重放请求」链路保持原样（并发 401 共享同一次重登也保留）。

### 证据
| 项 | 改前 | 改后 |
|---|---|---|
| `[API] 自动重登后请求仍失败`（34 页） | **76** | **0** |
| `[API] 会话过期自动重登失败` 新增详报 | — | 同因最多 1 条（窗口内 0，首次已在捕获窗口外计数抑制） |
| 运行期 `wx.login` 实际调用（8 个请求密集页计数窗口，期间确有 401） | 旧实现 = 1 次/请求（基线 76 条告警 ≈ 76 次） | **0 次**（`.superpowers/sdd/k47-login-count-after.json`） |

`node --test tests/k47_relogin.test.js`：**4/4 pass** —— ① 41002 下 8 次 401 只 1 次 `wx.login`、
告警 1 条；② 可恢复失败：退避期内不重登 → 退避到期重试 → 达上限长冷却 → 长冷却后仍可自愈；
③ 会话过期→静默重登→重放成功（token 落库、重复到期仍可重登）；④ 登录成功解除退避/fatal 标记并
恢复完整重登链路。

---

## 五、node 全量测试（实跑）

```
cd miniprogram && OMP_NUM_THREADS=1 node --test tests/*.test.js
# tests 351 / pass 351 / fail 0   （基线 337 通过；本批新增 k47_md_flat 10 + k47_relogin 4）
```

## 六、文件清单（35 改 + 3 新）

- 新：`miniprogram/utils/log.js`、`miniprogram/tests/k47_md_flat.test.js`、`miniprogram/tests/k47_relogin.test.js`
- A：`miniprogram/utils/md.js`、`pages/chat/chat.wxml`、`pages/chat/chat.wxss`、`utils/chatSelect.js`
- B：`app.js`、`utils/theme.js`、`pages/{bazi,celiang,chat,dreams,favorites,history,jian_onboard,ming,mingren,night_mark,onboarding,persons,qian,reports,settings,share,today,wannianli,zeri_plan}/*.js`、`tests/chattextselect.test.js`
- C：`utils/api.js`、`utils/security.js`、`utils/shareCard.js`、`pages/{chat,love,share,ming,hehun,mingren,mingren_detail,qimen,today,xingming}/*.js`
- D：`utils/api.js`（同上）
- 证据：`.superpowers/sdd/k47-console-before.json`（152 条基线）、`k47-console-after.json`（8 条）、
  `k47-chat-before.png`、`k47-chat-after.png`、`k47-login-count-after.json`

## 七、同步与红线

- 已把 35 个运行时文件**单文件 cp** 到 `/mnt/e/fortune-agent-deploy/miniprogram/`（未用 `--delete`，
  未同步 tests/，未碰其它目录）；未执行 `cli.bat upload`；未重启服务、未碰生产库/`.env`；
  未 push（由控制方决定）；`git add` 只加本批文件（`data/` 不入）。

## 八、遗留 / 需拍板

1. **C 的 7 条 `console|error|[{}]` 未能定位到应用调用点**：应用侧 console.error 全部带场景字符串，
   应用内包裹后 error 级调用为 0；证据指向开发者工具「废弃 API 告警」运行时路径（与 B 同源，B 修后归零）。
   若控制方后续仍复现，建议在 IDE 侧打开「不合并相同日志」再抓一次原始 CDP 事件（本批脚本已存 `/tmp/k47_probe_c.js`）。
2. **D 的上限语义**：按「连续失败 3 次 → 10 分钟长冷却（可自愈）」实现，而非「本会话永久停止」——
   后者会让网络恢复/服务端恢复后无法再静默重登（行为倒退）。如需严格「每会话 N 次上限」，请拍板。
3. **引用块多层缩进**：嵌套引用（`> > x`）以 `│ ` 前缀压平为单层（内容不丢，缩进层级简化）；
   若要保留多层缩进视觉，需要给 `quoteLines` 增加 depth 样式（后续小批可做）。
4. 行内强调解析沿用旧扫描器语义（`*a **b** c*` 仍按单星切分），仅新增 `***粗斜***` 支持——
   未改变既有单层行为。
5. 既有 `[Event] 21 listeners of event ThemeChange…` 告警不在本批范围（未新增监听来源）。
6. **临时件共用提醒**：`/tmp/wx_console.js` 与 `/tmp/wx_console_all.json` 是控制方留档脚本的固定路径，
   本批复核期间该文件被另一并行进程重写过一次（时间戳 00:19–00:23，内容为改前状态 684 条）。
   本报告的证据文件已另存为 `.superpowers/sdd/k47-console-{before,after}.json`（before = 控制方
   基线 152 条，after = 本批改后 8 条），不再依赖 `/tmp` 路径。
