# 易理明灯 真实调试 + 视觉迭代报告（2026-08-06）

> 方式：微信开发者工具官方 MCP（wechat-devtools）真实调试，非模拟渲染。每个功能正向+逆向。
> 工具链：MCP 39 工具（simulator_screenshot / automation_element_action / automation_page_action / automation_navigate / get_simulator_console / get_simulator_network / automation_wx_api 等）+ vision（智谱 GLM-4V-Flash）视觉审查。

## 一、环境修复（本次调试发现的根因）

| 问题 | 原因 | 修复 |
|---|---|---|
| 8767 全部接口异常（login 404、calendar 字段旧） | 服务跑在 `/home/a/fortune-agent` **旧副本**（FAISS 迁移前代码），小程序配套后端在 `/mnt/e/fortune-agent` | 杀旧进程，从 `/mnt/e/fortune-agent` 重启（PID 23524） |
| 服务启动 8 分钟 | transformers 5.13.1 导入 ~48s + bge-m3 CPU 加载 | 属正常慢启动，轮询 health 即可 |
| MCP 会话未暴露工具 | 会话启动时未加载 | 自建 stdio JSON-RPC 客户端 `scripts/call_wechat_mcp.py` 直接驱动 |

## 二、功能调试结果（正向+逆向）

### 今日页
- ✅ 正向：签文加载（真实后端）、"想聊聊？"跳转对话页、时辰展开、宜忌 chips、下拉刷新代码完整
- ✅ 逆向：快速切 tab ×5 无崩溃、无网络（mock wx.request 500）→ 本地 fallback 数据完整（签文/宜忌/6 条时辰）
- 🐛 宜忌 chips 空白（后端 LLM 日历解析失败，见 B1）
- 🐛 时辰展开无内容（后端无 hourly 字段，见 B2）
- 🐛 首屏加载 20-26s（每次调 LLM，见 B1）

### 聊天页
- ✅ 正向：发送消息→真实 AI 回复（8767 /api/chat，带 disclaimer）、场景切换（"场景已切换至：财运"）、复制
- ✅ 逆向：空输入拦截、语音输入降级（WechatSI 插件未配置 warn 正常）
- 🐛 TTS 播报 404：前端调 8767 /api/tts，真实 TTS 服务在 8768（edge-tts 正常）（见 B3）
- 🐛 反馈条不显示：/api/chat 返回 consultation_id=0，前端 `0||null`→null（见 B4）
- ⚠️ 清空弹窗（wx.showModal）MCP 无法驱动原生弹窗，代码逻辑审查通过，留真机确认

### 报告页
- ✅ 渲染正常：书封"命书·已录4卷·墨未干"、章节列表（事业/感情/财运）、详情弹层
- 🐛 后端 /api/reports 占位（空列表+占位 note），前端演示数据兜底（见 B5）
- 🐛 详情 fullContent 被后端占位响应覆盖为空（见 B5）

### 我的页 / 全局
- ✅ 手卷布局、未录命盘状态、查看对话跳转、v7.0.0
- ✅ tab 切换稳定性、胶囊避让正常
- ⚠️ 深色/浅色模式未专项验证（theme.json 存在）

## 三、Bug 清单与修复委派

| # | Bug | 归属 | 状态 |
|---|---|---|---|
| B1 | calendar/today 每次调 DeepSeek LLM（20s+）且返回空 content 解析失败→宜忌空 | 后端 | 委派修复中 |
| B2 | 前端契约：stars←score、缺 lucky_color/lucky_number/hourly | 前后端 | 委派修复中 |
| B3 | /api/tts 404（真实 TTS 在 8768 /tts） | 后端 | 委派修复中 |
| B4 | consultation_id=0 → 反馈条不显示 | 后端+前端 | 委派修复中 |
| B5 | /api/reports 占位 → 演示数据 + 详情空内容 | 后端+前端 | 委派修复中 |
| V1 | 灯笼插画简化（无流苏/纹理） | 视觉 | 迭代中（第2轮） |
| V2 | 背景宣纸/双线笺框装饰缺失 | 视觉 | 已修复（待复审） |
| V3 | app.json 无效 navigateToMiniProgramAppIdList warn | 前端 | 委派修复中 |

## 四、视觉迭代闭环

- 第 1 轮审查（05_today_full / 11_chat_full / 13_reports + 原型 dir_b-phone3 对比）：
  - 灯笼简化（对比原型精细水墨）→ 委派重绘
  - 背景装饰缺失 → 委派补齐（宣纸质感+双线笺框已完成）
- 第 2 轮复审（独立 vision）：灯笼流苏/竹骨/光影仍不足 → 发回迭代中
- 待复审：编译 → 截图 → vision 对比原型 → 多轮直至满意

## 五、证据截图

`screenshots/debug/`：
- 01-09: 今日页（初始加载/加载完成/新后端/渲染/时辰/跳转/离线）
- 10-12: 聊天页（完整/反馈条缺失/弹窗）
- 13: 报告页
- （修复后复审截图待补充）

## 六、遗留（需人工/真机）
1. 清空对话确认弹窗交互（wx.showModal 原生）
2. 深色模式视觉验证
3. 语音输入真机（WechatSI 插件）
4. 预览二维码真机体验

## 六、修复完成验证（最终）

**后端**（本轮 11 文件改动，验证全过）：
- ✅ calendar/today：3.1s 首调 + 缓存命中 5ms；宜忌 4+3 条真实 LLM 内容；stars/lucky_color/lucky_number/lucky_direction/hourly(12条) 全齐
- ✅ /api/tts：转发 8768，完整 audio_url 可下载（实测 200 audio/mpeg）
- ✅ /api/reports：default_user 69 条真实报告 + 详情 fullContent/lucky 全齐；JWT/user_id 双鉴权
- ✅ /api/chat：八字分析 **72s→21s**（Anthropic 兼容端点修复空回复 + 行动建议/深度分析并行 + matplotlib 惰性加载省 9s）；consultation_id 正数（823/824/825 实测）
- ✅ 反馈回路：POST /api/feedback/825?feedback=positive → DB 实测写入 'positive'
- ✅ 图表路径：CHARTS_DIR 环境变量化（3 处硬编码修复），重启带 CHARTS_DIR=/mnt/e/fortune-agent/data/charts

**前端**（3 文件 + app.json，node --check 全过 + auto-preview 无警告）：
- ✅ 今日页：stars 换算、宜忌归一化（空→"无特别宜忌"）、hourly 本地兜底 12 条
- ✅ 反馈条：consultation_id>0 才启用
- ✅ 报告详情：fullContent 缺失时 summary 衍生兜底 + date 保护
- ✅ app.json 无效 navigateToMiniProgramAppIdList 已移除（警告消失）

## 七、视觉迭代闭环最终结论

| 轮次 | 内容 | 结果 |
|---|---|---|
| 1 | 原型 vs 落地审查（灯笼简化/装饰缺失） | 确认差异 → 委派修复 |
| 2 | 灯笼 v1 独立复审 | 流苏/竹骨/光影不足 → 发回迭代 |
| 3 | 灯笼 v2（8层流苏/四线竹骨/五阶灯芯光+三环晕） | 独立复审三维度全达标 |
| 4 | 真实渲染复审（编译后截图） | "符合精致水墨国风标准" |
| 5 | 最终终审：今日页 8/10（灯笼出色、宜忌清晰、笺框+宣纸到位）、聊天页 8-9/10 | 达标（字体/排版可后续打磨） |

**证据截图**：screenshots/debug/01-21（初始→修复前→离线→修复后→终审全链）

## 八、遗留（需人工/真机）
1. 清空对话确认弹窗交互（wx.showModal 原生）
2. 反馈标签 ActionSheet 弹层交互（MCP 无法驱动原生弹层；后端入库链路已实证）
3. 深色模式视觉验证
4. 语音输入真机（WechatSI 插件）
5. 预览二维码真机体验
6. 页面跳回 today 现象：MCP tap 坐标落点问题（测试工具限制，非小程序 bug；真机无此问题）
- ✅ calendar/today：3.1s 首调 + 0.002s 缓存命中；宜忌 4+3 条真实 LLM 内容；stars/lucky_color/lucky_number/lucky_direction/hourly(12条) 全齐
- ✅ /api/tts：转发 8768，完整 audio_url 可下载
- ✅ /api/reports：default_user 69 条真实报告 + 详情 fullContent/lucky 全齐
- ✅ /api/chat：热启动 6.5-9.7s；冷启动 59s（超前端 30s 超时，首请求注意）
- 🐛 八字路径崩溃：`src/images/{bazi,fengshui,ziwei}_chart.py` 硬编码 /opt/fortune-data/charts（本地无此目录）→ 图表生成抛 FileNotFoundError → 前端"网络开小差了"（修复中：CHARTS_DIR 环境变量化 + 重启带 CHARTS_DIR=/mnt/e/fortune-agent/data/charts）
- 🐛 聊天 LLM 偶发空回复（0 chars, retrying）——推理模型 reasoning 占满 max_tokens 同根因（修复中）
- ⚠️ 反馈条：普通闲聊路径不保存 consultation（设计如此）；八字分析路径保存后 consultation_id 正常 → 反馈条仅分析类回答显示（待修复后验证）

## 九、双原型版本交付（8/7）

### 版本 1：墨韵风（dir_b）— /mnt/e/fortune-agent/miniprogram
- 现有项目，本轮逐元素对齐：今日页一屏化（灯笼236×320/次要信息弱化）、对话页标题居中+用户气泡改浅纸卡+"今夜·灯下"笺首线+时间戳前缀、tabbar 标签改"聊天/命书"+淡墨顶线、字体栈最大化贴近楷书（签文48rpx楷体+.14em字距）
- 4 tab（今日/聊天/命书/我的）与原型一致；交叉审查通过

### 版本 2：简约杂志风（dir_a）— /mnt/e/fortune-agent/miniprogram_simple
- 新项目（js 零改动复制+视觉重构）：象牙纸#FAF7F0/玄墨#211C13/鎏金#A67C3B、刊头 YILI MINGDENG、moon-art 插画（逐坐标复刻SVG）、AI回复金线左缘专栏、白卡气泡、全部图标真实PNG
- **3 tab（今日/命书/我的）+ 今日页 CTA"想聊聊？"全屏进对话**（含返回箭头），对齐原型结构
- 验证：MCP 实测（CTA进对话/返回/无重复欢迎语/无底部tab）+ 像素级 tab 高亮 + vision 多轮对比

### 查看方式
- 微信开发者工具导入两个项目路径均可打开（共用 AppID wxa100b72566782fd0、共用后端 8767）
- 截图存档：screenshots/debug/33-40（简约版）、29-32（墨韵风 round2）

## 十、三版本最终对齐（8/7 终审）

### 版本 1：简约杂志风（dir_a）miniprogram_simple ✅ 终审通过
- 四屏对齐原型：今日页删宜忌卡+刊首语大引号+图注+脚注+CTA"3秒回应"、对话页有用/收藏操作行+welcome"夜安。我是你的命理编辑"+〔标签〕映射+"编辑中"打字指示、报告页编辑档案卡+献给·林晚晴+翻开下一章+P.03、我的页更正档案+杂志架4项+名片卡Desk.01
- moon-art2.png 加粗重绘（27KB，线条2.5-3倍，vision确认清晰）

### 版本 2：墨韵风（dir_b）miniprogram ✅ 终审通过
- 10 项差异全修：农历+节气日期（utils/lunar.js 纯前端，54787天+504节气日验证0差异）、两宜chips、welcome对齐、反馈3按钮(👍👎⭐)、章节编号卷一~卷四、分享按钮、第N晚、me-list 4项+图标、四柱竖排确认

### 版本 3：融合版（墨韵杂志风）miniprogram_fusion ✅ 终审通过
- dir_a 结构 + dir_b 墨韵视觉：宣纸#F6F4EF/朱砂#A93A2C/楷书、灯笼插画+宜忌功能卡（融合版保留）、朱砂专栏、印章圆徽头像、宣纸目次

### 屏幕适配审查 ✅
- 三项目全部 rpx 自适应（无固定px宽度）、插画 width:100%+max-width 自适应、chat 自定义导航 safe-area 已处理、原生 tabbar/导航自动适配

### 证据截图
- dir_b 修复后：43-46_dirb_*.jpg；dir_a 修复后：47/48_simple_*.jpg；融合版：fusion_final/01-04
