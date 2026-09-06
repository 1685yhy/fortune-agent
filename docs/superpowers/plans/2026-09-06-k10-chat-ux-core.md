# k10 对话页 UX 核心批（chat-ux-core）——实施设计文档

- 日期：2026-09-06　分支：k10-chat-ux（BASE = main 2ac1fc5）
- 范围：只动 `miniprogram/**` + 本文档 + 测试文件；后端契约只读核实，零改动
- 对应调研：/tmp/fortune-chatux-gap-20260906.md（4 项真机反馈差距）
- 参照规划：task-k6-chat-ux-brief.md（P4.1-4.7）、task-k6-wave1-report.md（波1 DONE_WITH_CONCERNS + review I-1/M-1~M-3）、task-B4-1-brief.md（输入区拍板布局）、task-B6-1-report.md（图标家族口径）

---

## A. 合并 k6 波 1（commit 面核实 + M-1 顺手修）

- **提交面核实**：`git log k6-chat-ux` = 274b46c（k5）+ 46b0c24（波1 单提交，review 已通过含 I-1/M-1~M-3）——**无未审查提交 → merge 而非 cherry-pick**。
- **冲突面**：274b46c..main 对 miniprogram/ 零 diff（k7..k8 全在服务端）→ merge 干净（5200324，amend 为仓库主题格式）。
- **M-1 顺手修**（8040108）：micLongPress 的 `_touchStartAt` 于 longpress 触发（≈触摸后 350ms）才起算，与 voice-hold「touchstart 起算 ≥800ms 即发送」语义差 350ms——长按路径 0.8–1.15s 波段松手会被误判「说话时间太短」。改为 `Date.now() - 350`（≈真实按下时刻），与按住条同一口径。
- **M-2 / M-3 未动**：M-2（streaming 守卫与语音态不对称，brief 合规）与 M-3（授权慢桥接）记录为后续项，不擅改。
- **I-1（chatimage.test.js 陈旧机件断言）**：属「陈旧测试清理」跟进 commit（review 建议合入门禁项）。已在本批执行：13 条旧用例中机件断言（_updateInputBarH 公式/onInputLineChange 联动/inputBarH 高度）改写为 k6-P1 语义回归（符号退役断言 + 模式切换零高度 setData + 强制贴底滚动路径保留），并新增 micLongPress 守卫/开录/M-1 窗口测试——chatimage.test.js 15 条全绿。

## B. 多行输入（换行键）

- **设计**：chat.wxml textarea 摘除 `confirm-type="send"`（f10866d）——textarea 键盘右下角回到微信惯例「换行」（输入 \n），发送统一走右侧朱砂发送钮（与 B4-1 拍板布局一致；对齐元宝/豆包「发送在条上」）。
- **兜底**：bindconfirm/onConfirm 保留为无害兜底（仅当输入法仍给「发送」键时触发，走既有 _send 链——js 零改动，不残留对键盘发送的依赖）。
- **取舍**：按调研 §4② 方案 A（推荐）。不做「发送/换行切换小钮」——占输入条宽度、违背 B4-1 拍板。空输入/语音守卫均不受影响。
- **测试**：chatmultiline.test.js（wxml 属性静态断言：无 confirm-type=send、bindconfirm 保留、auto-height/maxlength 在；js onConfirm/sendMessage 同链 _send）。

## C. 文字选取：方案乙（textarea 覆盖层）+ 甲兜底（用户已拍板乙主路径）

### 段落定位（甲乙共用事实源）

- 新增 `miniprogram/utils/chatSelect.js`：`paragraphModel(msg)` 把消息镜像（mdNodes/card*/user content）转纯文本段落模型 `{text, paras[{key,zone,text,start,end}], byKey}`。
- 段落键 `<zone>:<index>`（zone = md/pre/card/tail/user）与 wxml `data-para-id` 同构（模板侧 `paraKey + ':' + index` 拼接）。
- 段落粒度 = md 顶层可视行块：p/h 各一段；quote 整体一段（内部递归压平）；ul/ol **整体一段**（各 li 行 \n 连接）；code 不参与（自带复制钮）；table 不产生段落但以无 key chunk 计入全文（偏移连续）。user 消息单段 `user:0`。
- **li 粒度取舍**：wxml 列表内层 wx:for 会遮蔽外层 index，逐 li 挂 data-para 需外层 index 透传重构——当前整列表一段覆盖「复制本段」多数诉求，li 粒度列后续项。
- 偏移严格累计（无 indexOf 猜测），node 单测逐段 `text.slice(start,end) === para.text` 校验。
- wxml：md-block 顶层 p/h/quote/ul·ol 视图挂 `bindlongpress="onParaLongPress"` + data-msgid/data-para-id；quote 递归以 `paraKey:''` 关闭内层捕获。onParaLongPress 只簿记 `_lastParaHit`（冒泡先于气泡级 onBubbleLongPress），气泡开菜单时校验 msgId 一致性后写入 actionMenu.paraKey，防跨气泡陈旧命中。选取模式/多选模式中不记录（B3-3 让位原生语义保留）。

### 乙（主路径，自动降甲）——chat.wxml .sel-overlay

- 结构：全屏模态容器 + 独立 `.sel-mask`（catchtap 退出，不拦 textarea 触摸——textarea 原生层不吃 mask）+ 绝对定位 textarea 原位覆盖 `.jz-body`（AI，id 化 class `sel-body-<msgId>`）或 `.user-note` 实测矩形。
- 行为：`_enterTextSelect` → 段落存在且 `_textOverlayFeasible` → `_openSelOverlay`（先落甲态高亮兜底 → createSelectorQuery 测量矩形 → setData 覆盖层 + focus + selection-start/end = 长按段落偏移 → 呈现可拖选区观感）。失焦/点外部/滚动/流式更新 → `_closeTextOverlay`（恢复原气泡，甲态保留可二次长按）。
- **键盘抑制（结构性障碍自检结论）**：微信 textarea/input **均无 readonly 属性**——程序 focus 必然弹键盘，无任何 API 可阻止（adjust-position 只控制页面顶推）；`wx.hideKeyboard` 只能在 bindfocus 后尽力调用（onSelOvFocus），iOS 程序聚焦下保留选区手柄不保证。→ **降级判定链**（清晰可测）：
  1. 常量 `TEXT_SEL_ENGINE='b'`（乙优先）→ 'a' 强制全甲；
  2. iOS 平台（用户真机）默认直接甲（`TEXT_SEL_IOS_OVERLAY=false` 实验开关可放开 Android 实验）；
  3. 文本 > TEXT_SEL_MAX_TEXT(900) / 段落偏移 > TEXT_SEL_MAX_PARA_START(500)：超长段落在 textarea 首屏外（textarea 无预滚 API）；
  4. 图片混合消息（几何不可靠）/ 无段落 / 无测量能力 / 异常捕获 → 甲。
- **结论建议**：乙为「最小可行实现」已落地但默认在 iOS 自动降甲；**建议主会话拍板：甲为默认**（真机验证乙在 Android 表现后再放开开关）。本批无真机渲染验证通道，属平台层不可达项——代码内注释写明平台限制与为何需二次长按。

### 甲（自动降级 + 用户已拍板的复制本段能力）

- 菜单组二顶部新增「复制本段」（data-k=copyPara，actionMenu.paraKey 非空可见）→ `wx.setClipboardData(paragraphModel(msg).byKey[key].text)`（stripCardMarkers 同款清洗，段落模型即由其构建）。
- 「选取文字」点击：段落路径且乙不可行 → `selectMsgId + selParaKey`（被按段落 md-hl 高亮 + 气泡顶部常驻引导小字「长按这段文字即可拖动选择」）；无段落（空白/装饰区）→ 旧语义 toast + 整泡 selectable。
- 引导小字替代旧 toast（B3-3「toast 打断 iOS 原生选择 UI」教训同源）。
- 清除点：exitMulti/onListTap/_resetChatUi/_onHostState 流式/onScroll 全覆盖接线。

## D. P4.1-4.3 菜单增补 + P4.4/P4.5 核实（72858f3）

- **P4.1 重新生成**（AI 专属菜单行，图标 ↻ 字形 .act-em-glyph——零新增图片资产红线内，仓库先例 jz-retry/react-add）：可见 = role=ai && !error && !streaming && 宿主空闲 && 有 retryText && **是本列表最后一条 AI 回复**（_isLastAiMessage）；点击 → `streamHost.retry(msg.id, retryText, tag)` 直接执行无确认（元宝即时感）。
- **P4.2 查看引用**：可见 = msg.citations 非空数组；点击 → onCiteTap 抽取的公共 `_openCiteDrawer(msgId)`（同一抽屉，不新造）。
- **P4.3 footer 复制钮**：jz-fb 第 5 钮（ic-copy.png，图标 36rpx 与 keep 同档）；行为与菜单复制同链（stripCardMarkers 后写剪贴板）。**宽度核算**：5×62 + 4×4 gap = 326rpx + timewrap（时间≈113 + gap10 + speak52 ≈175）= 501rpx ≤ 气泡内容宽 511rpx（569−29×2）——留 ~10rpx 余量，无需压钮/压缩 timewrap。
- **P4.4 user 气泡**：对照元宝结论 = footer 不加钮（移动端元宝用户气泡也无悬浮钮）——只记录判断，无改动（chatmenu.test.js 静态断言 user 分支无 footerCopyText）。
- **P4.5 后端作废接口（voidConsultation）未接**：miniprogram api.js 与后端均不存在该端点（grep 核实：api.js 无 voidConsult；src 仅 POST /api/feedback/{consultation_id}）→ **按任务红线「缺的不新造」列入后续项**：重新生成/删除此条的前端作废接线（`msg.consultationId` 数据层已齐）待后端接口排期。

## E. P4.6 元宝式反馈面板（ee61891）

- **参考图**：IMG_4510（用户 09-04 实机）**本机不可得**（fortune-agent/screenshots 09-01 后无新文件、无 IMG_4510 命中文档）→ 按 brief 文字描述实现**最小合理版**，报告/文档标注**待图复核**。
- 三入口同面板：footer 踩钮（toggleFb k=down，不再直发 negative）/ 长按菜单「点踩」（actFeedback k=down）/「意见反馈」（actItem k=feedback）→ `openFeedbackPanel`。
- 面板结构（act-sheet 复用 + fbs-* 新样式）：顶部感谢行「谢谢你的反馈，我们会继续优化进步」→ 三组原因（针对问题/针对回答/针对格式，默认文案按 brief 本域改编，chips 多选）→「我要补充」输入框（可不填）→ 取消/提交行。
- 提交态派生 canSubmit（任何原因或补充非空才可点，置灰样式）；提交 → 原因「、」连接 + 补充「；」拼接 → `api.feedback(cid, 'negative', label)` → 成功关闭面板 + 踩图标点亮 + toast「已收到反馈」；**无咨询 ID → 只点亮不发后端**（本地 _logFeedback 留档为证据，brief P4.6 口径——取代旧网格同规则）；上报失败 → 不点亮 + 「反馈提交失败，请重试」（G2 B1 不静默语义保留）。
- 赞（footer/菜单）：点亮态 + 点亮成功轻提示一次「谢谢认可，我会继续精进」（_toggleFbCore 增 onToast 参数）；不弹窗；G3 H-12 无咨询记录回滚规则保留。
- **旧 fbMenu 网格整体退役**：wxml/js 全清（grep fbMenu/submitFeedbackReason 代码级清零，仅注释残留）+ 死样式 .act-grid/.act-item/.act-ic/.act-glyph*/.fb-grid 清除；g2 A8 两条用例改走新面板（成功/失败断言语义不变）。

## F. 图标引用核对（P4.7 独立子批素材，本批零改动）

chat 页/菜单 legacy 淡棕（#9A8B71，B6 家族外残留，P4.7 需统一）引用点：

| 文件 | 位置 | 图标 | 语义 |
|---|---|---|---|
| miniprogram/pages/chat/chat.wxml | L475 长按菜单组二 | ic-edit.png | 选取文字（淡棕细线 legacy） |
| miniprogram/pages/chat/chat.wxml | L494 长按菜单组三 | ic-chat.png | 意见反馈（淡棕细线 legacy） |
| miniprogram/pages/me/me.js | L13 | ic-edit.png | 档案入口 |
| miniprogram/pages/me/me.js | L18 | ic-chat.png | 对话历史入口 |

中墨（#6C5B45）非家族引用点（P4.7 范围外候选）：

| 文件 | 位置 | 图标 |
|---|---|---|
| chat.wxml | L340 输入条内嵌相机 / L584 +面板·从相册 | ic-camera.png |
| chat.wxml | L490 菜单分享 | ic-share.png |
| reports.wxml L10、share.wxml L57 | ic-share/ic-camera | |
| celiang.wxml L178、history.wxml L9、me.wxml L127、today.wxml L175、share.wxml L53、me.js L18 | ic-chat.png（tab/入口/渠道徽章，语义各异） | |

P4.7 重绘范围（k6 brief）：气泡区 11+3 = ic-up/down/keep/reaction/copy/speak/share/check/link/delete/edit/chat（含 on 态 up/down/keep 三枚）；输入区（camera/mic/keyboard/plus/send）不在范围。本批未动任何 png（chat 页引用全部经 B6-1 验证存在）。

## 测试清单（node --test miniprogram/tests/*.test.js）

- chatimage.test.js：15 条（k6-P1 机件退役语义 + P2 micLongPress + M-1 窗口 + 既有图片链路全保留）
- chatmultiline.test.js：1 条（换行语义静态断言）
- chattextselect.test.js：12 条（段落模型/偏移/zone/user + 甲/乙降级链 + copyPara + 静默语义）
- chatmenu.test.js：7 条（P4.1 可见条件全套/守卫、P4.2、P4.3、P4.4 静态断言）
- chatfbpanel.test.js：8 条（三入口/多选派生/空拦截/合并串/无 cid/失败不点亮）
- g2_save_integrity.test.js：A8 两条改写走新面板（语义不变）
- 全量：**244/244 pass（含既有 236 条基线）**

## 后续项（下批）

1. **P4.7 图标重绘**：按 F 清单同名替换 + 风格（浅细线中灰/点亮主题色）；icons/ 与 assets/images/ 双目录隐患；ic-edit/ic-chat 淡棕清理；输入区图标是否扩围待 PM。
2. **P4.5 后端 void consultation 接口 + 前端接线**：api.js 无 voidConsult、后端无端点——重新生成/删除此条目前只做本地（旧稿后端残留隐患，task-k6-brief 已述）。
3. **乙覆盖层真机验证**：iOS 默认降甲已编码；Android 若可靠 → TEXT_SEL_IOS_OVERLAY/平台名单放开；真机不可靠 → TEXT_SEL_ENGINE='a' 一行回甲。
4. M-2（streaming 中长按话筒对齐语音态排队）/ M-3（授权慢桥接）review Minor。
5. li 级段落粒度（外层 index 透传重构）。
6. 意见反馈面板 IMG_4510 图复核后微调（布局/文案）；fbs 面板 textarea 内部滚动在 act-sheet catchtouchmove 下的真机表现。
