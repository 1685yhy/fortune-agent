# task-k47 报告：开发者工具真实渲染报错收口（A/B/C/D 四类 + 复核追加 E）

- 工作目录：`/home/a/k47-wt`（分支 `k47-wxclean`，base=main=`67630bf`）
- 来源：控制方 2026-09-14 automator 实测 34 页 152 条 console（A=28 / B=29 / C=7 / D≈76）
- 复核方式：`node /tmp/k47_console.js <out.json> <shot.png>`（automator 驱动开发者工具 → 遍历 app.json 全部页面 → 汇总 console/exception；含 chat 页嵌套 markdown 注入 + 截图）
- **复核追加（2026-09-14 控制方独立复核 + 独立审查 `task-k47-review.md`）**：控制方复跑得 152 → 22，
  指出两项未收口：① 仍有 7 条 `console|error|[{}]`；② `[Event] 21 listeners of event ThemeChange…`。
  本轮已查清并处理，见 **§九 复核响应**（结论：7 条为**开发者工具自身记录**（加载 WechatSI 插件时框架崩溃），
  非应用日志；我们的全局错误行已改为可定位 + 标注工具侧噪音 + 不进留痕统计；ThemeChange 改全局单例，告警消失）。

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

### 证据（改前 / 改后，均以「同一套 automator 脚本跑全 34 页」为准）
| 项 | 改前 | 改后 | 说明 |
|---|---|---|---|
| `console|error|[{}]` | **7** | 0（**应用侧**）；7 条**工具侧记录**仍在 | 见 §九.① 溯源：插桩证明应用侧 console.error 仅 1 次（我们那条），其余为开发者工具自身记录 |
| `…失败: {}` 形态（页面 warn） | 2（`[mingren] 列表加载失败: {}`） | 0 | 改为 `[WARN] mingren 列表加载失败 — 登录已过期，自动重登失败` |
| 全局错误行 | `[全局错误]` + 原始异常对象（不可定位） | `[ERR] 全局错误（工具侧噪音：…不计入留痕） — 消息 @ 帧1 / 帧2 / 帧3` | 消息 + 堆栈摘要，单行、无隐私；工具侧噪音不入 `ylm_last_error` |
| 34 页 console 总数 | 152 | **20**（其中 7 条为工具侧记录，应用侧 13 条） | 逐类明细见 §九.③ |

**溯源结论（2026-09-14 追加，已确证）**：7 条 `[{}]` **不是应用侧日志**。临时插桩（在部署副本的 app.js
顶部包裹 `console.error` 与 `wx.onError`，跑完全 34 页后读回计数）实测：
**错误级记录 8 条，应用侧 `console.error` 实际调用仅 1 次**（即我们的全局错误日志），其余 7 条为
开发者工具自身写出的记录（`App.logAdded` 里形态即 `[{}]`）。根因为**开发者工具加载 app.json 声明的
WechatSI 插件时框架侧崩溃**（`reportPluginCodeRequire` 读 `.version` 抛 TypeError，栈帧全在
`__dev__/WAServiceMainContext.js`、`__dev__/WASubContext.js`、`__onlineplugin__/wx069ba97219f66d99/0.3.5/…`），
属工具/插件加载侧噪音（应用侧仅优雅降级「WechatSI 未配置 → 键盘输入」，无功能影响）。

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
| `[API] 自动重登后请求仍失败`（34 页） | **76** | **1**（同因只报一次；旧实现逐请求各 1 条） |
| `[API] 会话过期自动重登失败` 新增详报 | — | 1（含原因「不可恢复错误，本会话停止重登尝试」+ 错误码 + 场景名） |
| 运行期 `wx.login` 实际调用（8 个请求密集页计数窗口，期间确有 401） | 旧实现 = 1 次/请求（基线 76 条告警 ≈ 76 次） | **0 次**（`.superpowers/sdd/k47-login-count-after.json`） |

`node --test tests/k47_relogin.test.js`：**4/4 pass** —— ① 41002 下 8 次 401 只 1 次 `wx.login`、
告警 1 条；② 可恢复失败：退避期内不重登 → 退避到期重试 → 达上限长冷却 → 长冷却后仍可自愈；
③ 会话过期→静默重登→重放成功（token 落库、重复到期仍可重登）；④ 登录成功解除退避/fatal 标记并
恢复完整重登链路。

---

## 五、node 全量测试（实跑）

```
cd miniprogram && OMP_NUM_THREADS=1 node --test tests/*.test.js
# 首轮：tests 351 / pass 351 / fail 0（基线 337；新增 k47_md_flat 10 + k47_relogin 4）
# 复核追加后：tests 364 / pass 364 / fail 0（新增 k47_theme_singleton 7 + k47_global_error 6，
#   并补齐「链接里带粗」等用例）
```

## 六、文件清单（首轮 35 改 + 3 新；复核追加 5 改 + 2 新）

- 新：`miniprogram/utils/log.js`、`miniprogram/tests/k47_md_flat.test.js`、`miniprogram/tests/k47_relogin.test.js`
- A：`miniprogram/utils/md.js`、`pages/chat/chat.wxml`、`pages/chat/chat.wxss`、`utils/chatSelect.js`
- B：`app.js`、`utils/theme.js`、`pages/{bazi,celiang,chat,dreams,favorites,history,jian_onboard,ming,mingren,night_mark,onboarding,persons,qian,reports,settings,share,today,wannianli,zeri_plan}/*.js`、`tests/chattextselect.test.js`
- C：`utils/api.js`、`utils/security.js`、`utils/shareCard.js`、`pages/{chat,love,share,ming,hehun,mingren,mingren_detail,qimen,today,xingming}/*.js`
- D：`utils/api.js`（同上）
- **复核追加（E）**：`utils/theme.js`（主题监听单例 + 卸载解绑）、`app.js`（全局错误详情日志 +
  噪音不入留痕）、`utils/log.js`（`errDetail/logErrDetail` + 工具侧噪音判定）、`pages/chat/chat.js`、
  `pages/bazi/bazi.js`、`utils/payment.js`（补齐审查 §3 Minor 的 4 处裸对象日志，含语音识别原文
  不回显的隐私加固；`pages/xingming` 无裸对象，审查未列）
- 新测试：`miniprogram/tests/k47_theme_singleton.test.js`、`miniprogram/tests/k47_global_error.test.js`
- 证据：`.superpowers/sdd/k47-console-before.json`（152 条基线）、`k47-console-after.json`（20 条，
  最终代码 · 控制方同款 stock 脚本）、`k47-evidence-toolnoise.json`（插桩实证：应用侧 console.error
  仅 1 次 vs 错误级记录 8 条）、`k47-chat-before.png`、`k47-chat-after.png`、`k47-login-count-after.json`

## 七、同步与红线

- 已把 35 个运行时文件**单文件 cp** 到 `/mnt/e/fortune-agent-deploy/miniprogram/`（未用 `--delete`，
  未同步 tests/，未碰其它目录）；未执行 `cli.bat upload`；未重启服务、未碰生产库/`.env`；
  未 push（由控制方决定）；`git add` 只加本批文件（`data/` 不入）。

## 八、遗留 / 需拍板

1. **C 的 7 条 `console|error|[{}]`：已确证为开发者工具自身记录（工具侧噪音，非应用日志）** ——
   插桩实测同批应用侧 console.error 仅 1 次（见 §九.① 与 `k47-evidence-toolnoise.json`）；根因是
   开发者工具加载 WechatSI 插件时框架崩溃（`reportPluginCodeRequire` 读 `.version`）。**应用代码无法消除
   这 7 条**；应用侧已做到：可定位日志 + 噪音标注 + 不进留痕统计。若控制方希望 IDE 控制台也清爽，
   只能在开发环境移除 app.json 的 plugin 声明（会影响真机语音输入，不建议）或等工具侧修复。
2. **D 的上限语义**：按「连续失败 3 次 → 10 分钟长冷却（可自愈）」实现，而非「本会话永久停止」——
   后者会让网络恢复/服务端恢复后无法再静默重登（行为倒退）。如需严格「每会话 N 次上限」，请拍板。
3. **引用块多层缩进**：嵌套引用（`> > x`）以 `│ ` 前缀压平为单层（内容不丢，缩进层级简化）；
   若要保留多层缩进视觉，需要给 `quoteLines` 增加 depth 样式（后续小批可做）。
4. 行内强调解析沿用旧扫描器语义（`*a **b** c*` 仍按单星切分），仅新增 `***粗斜***` 支持——
   未改变既有单层行为。
5. `[Event] 21 listeners of event ThemeChange…` **已在本轮修复**（见 §九.②）：`utils/theme.js` 改全局单例
   + 页面 `onUnload` 自动解绑；`app.js` 不再自行注册。实测 15 页访问窗口内新增注册 0 次、34 页抓取告警 0 条。
6. **临时件共用提醒**：`/tmp/wx_console.js` 与 `/tmp/wx_console_all.json` 是控制方留档脚本的固定路径，
   本批复核期间该文件被另一并行进程重写过一次（时间戳 00:19–00:23，内容为改前状态 684 条）。
   本报告的证据文件已另存为 `.superpowers/sdd/k47-console-{before,after}.json`（before = 控制方
   基线 152 条，after = 本批改后 8 条），不再依赖 `/tmp` 路径。

---

## 九、复核响应（2026-09-14 控制方复核 ①② + 独立审查 Minor）

### ① 7 条 `console|error|[{}]` 溯源 + 全局错误可定位（已确证 + 已修）

**溯源方法**：在部署副本的 `app.js` **顶部**临时插桩（包裹 `console.error`/`wx.onError`/`wx.onUnhandledRejection`/`wx.login`，
只计数不改输出）→ 跑全 34 页 → 读回计数。证据：`.superpowers/sdd/k47-evidence-toolnoise.json`（**工作区本地文件**，因 `.superpowers/sdd/.gitignore`
由控制方设为 `*` 未入库；关键数字已全量写入本报告；插桩文件为临时件，
验证后已还原；`grep 临时插桩` 在部署副本 app.js 中为 0）。

**结论**（回答「是 app.js 全局错误处理器？utils/log.js？还是别处？」）：
- 是 **开发者工具自身**（既不是 `app.js` 处理器，也不是 `utils/log.js`）。同一批 8 条 error 级记录里，
  **应用侧 `console.error` 实际调用只有 1 次**——就是我们那条全局错误日志；其余 7 条 `[{}]` 由工具写出，
  从未经过应用的 `console`（插桩计数为 0）。
- 真实异常（应用侧拿到的那条）根因：**开发者工具加载 `app.json` 声明的 WechatSI 插件时框架崩溃**——
  `MiniProgramError: Cannot read properties of undefined (reading 'version')`，
  栈帧全在 `__dev__/WAServiceMainContext.js`（`reportPluginCodeRequire`）、`__dev__/WASubContext.js`、
  `weapp:///__onlineplugin__/wx069ba97219f66d99/0.3.5/…`（插件 `wx069ba97219f66d99` v0.3.5 = WechatSI）。
  应用侧对此只有既有的优雅降级（`requirePlugin` throw → 语音输入切键盘），无功能影响。

**修法（应用侧能做到的全部）**：
1. `utils/log.js` 新增 `errDetail()` / `logErrDetail()`：输出「场景 + 消息 + 堆栈摘要（前 3 帧，单行、截断）」，
   兼容 `MiniProgramError` 这种把堆栈塞进 `message` 的形态；**不含用户隐私原文**。
2. `app.js` 全局错误/未处理 Promise 改走 `logErrDetail`；命中工具/框架特征（`__dev__/`、`WAServiceMainContext`、
   `WASubContext`、`__onlineplugin__`、`reportPluginCodeRequire`）→ **标注「工具侧噪音」并跳过 `_recordError`**，
   不污染 `ylm_last_error` 错误留痕统计。
3. 修后现场日志（34 页抓取实测）：
   `[ERR] 全局错误（工具侧噪音：开发者工具/插件加载，不计入留痕） — MiniProgramError · Cannot read properties of undefined (reading 'version') @ at kU (…WAServiceMainContext.js…) / at Object.AU [as reportPluginCodeRequire] (…) / …`
   —— 单条字符串、可定位、无裸对象、无隐私。
4. 7 条工具侧记录**应用代码无法消除**（不经应用 console）；已按要求标注为工具侧噪音并排除出统计。
   `tests/k47_global_error.test.js`（6 用例）锁定：噪音识别、业务异常不误判、单字符串输出、
   噪音不入 `ylm_last_error`、识别原文/订单要素不回显。

### ② ThemeChange 监听器累积（已修 + 双证据）

**改法**：`utils/theme.js` 改**全局单例**监听——`wx.onThemeChange` 全应用只注册 1 次，按注册表分发给各页面；
`bindTheme(page)` 幂等，并自动挂载/包装 `page.onUnload` 解绑（各页无需改代码，含未定义 `onUnload` 的页）；
`app.js detectTheme()` 不再自行注册监听器（该字段由单例同步，且原本全仓无消费方）。

| 证据 | 改前 | 改后 |
|---|---|---|
| 静态注册次数（15 页绑定，node 实测旧/新实现） | **15**（每页 1 次、永不解绑 → 超过 20 触发工具告警） | **1** |
| 运行期实测（部署副本，包裹 `wx.onThemeChange` 计数，访问 15 个页面） | 旧实现 ≈ 15 次 | **新增 0 次** |
| 34 页 console 抓取 `[Event] 21 listeners of event ThemeChange…` | 1（间歇，两次抓取均出现/控制方两次均现） | **0** |

`tests/k47_theme_singleton.test.js`（7 用例）锁定：25 页只注册 1 次、同页幂等、分发到全部已绑定页 +
同步 `app.globalData.theme`、卸载解绑（含原 `onUnload` 行为保留）、无 `onThemeChange` 的旧库不抛错。

### ③ 最终 34 页 console 对照（同款 automator 脚本，最终代码）

| 类别 | 改前 | 改后 | 备注 |
|---|---|---|---|
| A `md-inline` / `md-block` 递归 | 28 / (1) | **0 / 0** | |
| B `getSystemInfoSync is deprecated` | 29 | **0** | |
| C 应用侧错误日志形态 | `[全局错误]` + 裸对象；`…失败: {}` ×2 | **0 条 `{}`**；1 条可定位（已标注工具侧噪音） | |
| C 工具侧记录 `[{}]`（非应用日志） | 7 | 7 | 应用代码无法消除（见 ①） |
| D `[API] 自动重登后请求仍失败` | 76 | **1** | 同因只报一次 |
| E `ThemeChange` 监听器告警 | 1 | **0** | 见 ② |
| 其它（既有页面降级提示 / 启动日志） | 12 | 6 | 非本批引入（`[Today] API 不可用` 等） |
| **合计** | **152** | **20**（应用侧 13 + 工具侧 7） |

### ④ 独立审查（`task-k47-review.md`）Minor 登记与处置

审查结论：A/B/C/D 规格符合性全 ✅、代码质量 Approved、Critical 0 / Important 0 / Minor 7。处置：

| 审查 Minor | 处置 |
|---|---|
| §3 ① `bazi.js:660` 裸对象 warn | **本批已修**（`logWarn` + 归一化消息） |
| §3 ② `chat.js:2183` 语音识别结果可能含**用户语音原文** | **本批已修**（只打结果码，显式「内容不回显」） |
| §3 ③ `payment.js:242/248` 订单要素裸对象 | **本批已修**（只打错误码/缺失字段名） |
| §1 ④ 缺「链接里带粗」用例 | **本批已补**（`[链**粗**](url)` → 两个 link 节点，cls 正确） |
| §2 ⑤ 基础库 <2.20.1 无 statusBarHeight 来源 | 登记（影响≈0，项目 `libVersion=3.17.0`） |
| §4 ⑥ 10 分钟长冷却的用户观感 | 登记（报告 §八.2 已列待拍板，`applyAuth` 可即时解除） |
| §7 ⑦ `quoteLines` 与 `chatSelect.blocksToLines` 前缀不一致 / `briefErr` 可复用 `errText` | 登记（下批收敛，本轮不改语义） |

> 说明：审查指出 C 的 7 条 `[{}]` 归因「属推断」——本轮已用插桩实证（①），推断变为确证，
> 且结论与审查的怀疑方向不同：**不是** B/devtools 废弃 API 路径，而是**插件加载崩溃**。
