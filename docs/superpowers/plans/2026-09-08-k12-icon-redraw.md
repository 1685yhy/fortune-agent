# k12 · 对话页图标重绘（P4.7 执行批）实施计划

日期：2026-09-08　分支：k12-icon-redraw（BASE main=310ae16）　仓库：/mnt/e/fortune-agent-deploy
依据：用户 2026-09-04 真机否决现状图标（「看不懂、丑」）后拍板——元宝/豆包式**浅色细线条、圆头端点、常态中灰、点亮主题色**（k6 brief P4.7 原话 + k12 任务书扩展输入区范围）。只动 miniprogram 资产与引用，前端行为零改动。

---

## 0. 勘察结论（本计划的数据基础，全部实测）

### 0.1 消费点地图（全仓 grep wxml/wxss/js/json 实测）
- 全部图标消费走 `/assets/images/*.png`（image src 或 js 常量映射）；`miniprogram/icons/` 全仓零引用（wxml/wxss/js/json/md 全扩展名 grep 均为空）——纯冗余目录，本批删除（见 §6）。
- 全仓 `ic-*-dark.png` 亦零引用（遗留死资产，非本批范围，见 §7 concern）。
- 本批重绘目标文件的跨页消费点与语义核对（同名文件=单源，须语义兼容）：
  | 文件 | chat 内消费（语义） | 跨页消费（语义） | 兼容 |
  |---|---|---|---|
  | ic-up / ic-up-on | footer 赞、菜单 点赞（竖拇指） | 仅 chat | ✓ |
  | ic-down / ic-down-on | footer 踩、菜单 点踩 | 仅 chat | ✓ |
  | ic-keep / ic-keep-on | footer 收藏、菜单 收藏 | history（历史收藏态）、me（我的收藏入口 on） | 五角星通用 ✓ |
  | ic-reaction | 菜单 表情反应 | 仅 chat | ✓ |
  | ic-copy | footer 复制钮、菜单 复制/复制本段 | 仅 chat | ✓ |
  | ic-speak | footer 朗读、菜单 朗读 | 仅 chat | ✓ |
  | ic-share | 菜单 分享 | reports 导航分享钮 | 节点分享通用 ✓ |
  | ic-edit | 菜单 选取文字 | me 档案行 | 铅笔=选取/编辑双读 ✓ |
  | ic-check | 菜单 多选 | 仅 chat | ✓ |
  | ic-chat | 菜单 意见反馈 | **today/celiang/me 页 tabbar「对话」非选中钮**、history 导航钮、share 微信好友通道 | 气泡通用 ✓（tabbar 色差 concern 见 §7） |
  | ic-delete | 菜单 删除此条 | 仅 chat | ✓ |
  | ic-link | 菜单 查看引用、md 引用角标 chip | share 复制链接通道 | 链节通用 ✓ |
  | ic-mic | 输入框内嵌话筒、按住说话条 | 仅 chat | ✓ |
  | ic-keyboard | 语音态键盘钮 | 仅 chat | ✓ |
  | ic-camera | 输入框内嵌相机、+面板 从相册选择 | share 朋友圈通道 | 相机通用 ✓ |
  | ic-camera-soft | +面板 拍照 | 仅 chat | ✓ |
  | ic-plus | 输入区 + 钮（语音/文字两态） | 仅 chat | ✓ |
  | ic-send | 朱砂发送钮纸飞机 | dreams/history 墨色宽钮「继续对话」 | 纸飞机通用 ✓ |

- 新增图标 **ic-regen.png**（重新生成，见 §4.4）；删除钮/停止钮/多选框等为 CSS 图形或文字字形，无资产，不涉及。
- 输入区其它非资产项：stop-btn 的 .stop-square 为纯 CSS（白方角于墨底），不重绘；.in-ic-off（mic 禁用）由 CSS opacity .35 承担——符合「禁用态降不透明度」规范，无独立资产。
- sel-guide 引导为纯文字（无图标资产）；emoji 宫格为字符，均不涉及。

### 0.2 现状资产实测（PIL，RGBA，双目录核对）
- **assets/images/ 内两代风格混杂**：
  - B6-1 代 96px：reaction/copy/speak/share/edit/check/chat/delete/link/mic/keyboard/camera/camera-soft/plus/send（icon 主色浓墨/中墨/淡棕/纸白混杂）
  - B6-1 代 192px：up/up-on/down/down-on/keep/keep-on（浓墨 #382818≈#3A2C1E，覆盖 18-27%）
  - **legacy 淡棕 #988878/#9A8B71 残留**：ic-edit（选取文字）、ic-chat（意见反馈）、ic-camera-soft 三枚仍是旧淡棕细线（覆盖 12-20%），与本家族其它浓墨图标明暗粗细参差——用户「还是没统一」观感的资产根因。
  - ic-delete=朱砂深 #8C2E22、ic-send=纸白 #FAF5E7（置于朱砂钮）。
- 视觉复核（智谱 vision 逐枚描述实测）：**ic-up/down 现形状被读作「L 形」而非拇指、ic-keep 读作「S 形双箭头」而非五角星、ic-link 读作「书本/文件夹」而非链接、ic-check 为无框裸对勾**——「看不懂」的字面根因是图形不够通用，非仅颜色。本批按 P4.7 语义表全部改用最通用符号造型。
- 尺寸现状即目标尺寸（保持每文件名既有物理尺寸，避免无谓包体变化）：192px 六枚（上表 0.1 中 192 类），其余 96px；ic-regen 新增取 96px（与同菜单行兄弟档一致）。96/192 同源 = 同一 SVG 单一事实源两档渲染（§4.3 断言）。

### 0.3 界面显示档位（chat.wxss 实测 rpx→px@375）——「26px 可读性」仲裁基准
| 消费点 | 类 | 显示 | 涉及文件 |
|---|---|---|---|
| 长按菜单行 / +面板行 | .act-em-ic / .mp-ic 52rpx | 26px | up/down/keep/reaction/copy/speak/share/edit/check/chat/link/regen/camera(-soft) |
| 删除行 | .act-del-em-ic 48rpx | 24px | delete |
| footer 赞踩/收藏/复制 | .jz-fb-btn image 36-38rpx | 18-19px | up/down/keep/copy |
| footer 朗读 | .jz-speak image 31rpx | 15.5px | speak |
| 输入框内嵌 | .in-ic image 34rpx | 17px | camera/mic |
| 按住说话 | .vh-mic 36rpx | 18px | mic |
| 键盘钮 / + 钮 | .kb-btn / .plus-btn image 38rpx | 19px | keyboard/plus |
| 发送钮 | .send-btn image 33rpx | 16.5px | send |
| 引用角标 chip | .cite-chip-ic 24rpx | 12px | link |
| 表面底色 | act-sheet=--paper #F5EFE1；气泡/输入壳=--paper-card #FBF7EC；夜间夜色纸 #F4EBD6 | 全浅色系 | 中灰图标两态均适配 |

---

## 1. 风格规范（先行定稿，生成与断言均以本节为准）

### 1.1 造型
- 全部走**最通用符号直觉**（元宝/豆包公共图标同型），禁生僻造型；逐枚对应见 §4.1 造型表。
- 纯线性白描：单笔圆头端点（stroke-linecap/linejoin=round）、无渐变、无拟物、无填充剪影（唯一例外：表情眼睛/键盘键点/分享中心点等极小的内部点缀用实心圆点，白描允许）。
- 内细节笔画比主轮廓细一档（主 −1.2u），保证粗细层次。

### 1.2 笔划宽度（以「26px 显示可读」为仲裁，论证记录）
- 任务基线文字为「96px 资产下笔画 ~3px」，但按显示档位换算：96px 资产在 26px 菜单位显示 = 笔画 3×26/96≈0.8px CSS——正是 09-06 差距报告 R3 预警的「50rpx 档细笔画发虚/断裂」风险区间，会重蹈 legacy 覆辙。按 k12 任务范围第 5 条「以 26px 可读性为准」仲裁：
- **主轮廓 = 7.0u@96 空间**（26px 显示 ≈1.9px CSS / 3x 屏 ≈5.7 物理px；19px footer≈1.4px；17px 输入≈1.2px；15.5px 朗读≈1.1px——全部 ≥1px，圆头端点下不发虚）；内细节 = 5.6u；tiny 实心点直径 ≤5u。
- ic-link 额外服务 12px 角标 chip，笔划取 7.5u（chip 位 ≈0.94px 仍可辨，menu 位 26px≈2.0px 不突兀）；ic-send 置于深底（朱砂/墨钮）反白显示，笔划 6.8u 即可（白底衬深最显）。
- 断言语：**每枚 icon 96px 渲染下主笔划实测 ≥5.5px**（PIL 逐行扫描最大水平连续不透明段 ≥5.5px 且 <11px，杜绝发虚与过度加粗双失）。

### 1.3 颜色（token 化，均自 app.wxss 既有变量取值）
| 态 | 色值 | 依据/验证 |
|---|---|---|
| 常态 | **#756E63**（中灰，任务建议带 #6E675C~#7A7266 内） | 宣纸系对比实测 4.06~4.71:1（§7 附录），26px 细线可读 |
| 点亮/选中 | **#A93A2C**（= var(--acc)，与既有 on 态实测 #A83828 系一致，本批取精确 token 值） | ic-up-on/down-on/keep-on |
| 危险（删除） | **#8C2E22**（= var(--acc-deep)，与 .act-del-txt 同值） | ic-delete |
| 反白（发送钮） | **#FAF5E7**（纸白，现状同值保留） | ic-send |
| 禁用 | 无独立资产：CSS opacity .35/.4（.in-ic-off / .send-btn-off）既成规范 | 不新增 |
| 语义同形两态 | on 文件与常态文件**同一几何**，仅换色 | up/down/keep 三对 |

### 1.4 布局
- 图形内容区 = 画布中心 64u 见方（边 16u 留白），个别饱满图形（键盘/相机体）≤72u 宽，笔画 + 圆帽不得越出画布（96 边界外留 ≥3.5u）。

---

## 2. 重绘清单（22 文件 = 21 覆盖 + 1 新增）

| # | 文件 | 尺寸 | 语义符号 | 色 | 主消费 |
|---|---|---|---|---|---|
| 1 | ic-up | 192 | 竖拇指轮廓 | 中灰 | footer/菜单 赞 |
| 2 | ic-up-on | 192 | 同上几何 | #A93A2C | 点亮态 |
| 3 | ic-down | 192 | 倒拇指轮廓 | 中灰 | footer/菜单 踩 |
| 4 | ic-down-on | 192 | 同上几何 | #A93A2C | 点亮态 |
| 5 | ic-keep | 192 | 五角星轮廓 | 中灰 | footer/菜单 收藏 |
| 6 | ic-keep-on | 192 | 同上几何 | #A93A2C | 点亮态 |
| 7 | ic-reaction | 96 | 笑脸（圆+目+弧笑） | 中灰 | 菜单 表情反应 |
| 8 | ic-copy | 96 | 双叠圆角方块（前实后露两缘） | 中灰 | footer/菜单 复制 |
| 9 | ic-speak | 96 | 喇叭+声波弧 | 中灰 | footer/菜单 朗读 |
| 10 | ic-share | 96 | 分享节点（圆心点+两圆点连线） | 中灰 | 菜单 分享 |
| 11 | ic-edit | 96 | 铅笔轮廓（斜 45°，尖右下） | 中灰 | 菜单 选取文字 |
| 12 | ic-check | 96 | 圆角方块内对勾 | 中灰 | 菜单 多选 |
| 13 | ic-chat | 96 | 圆角气泡+三点（带尾） | 中灰 | 菜单 意见反馈 |
| 14 | ic-delete | 96 | 垃圾桶（盖+身+内竖线） | #8C2E22 | 菜单 删除此条 |
| 15 | ic-link | 96 | 水平链节（双 C 环+中横） | 中灰 | 菜单 查看引用/引用角标 |
| 16 | ic-mic | 96 | 话筒（胶囊+U 托） | 中灰 | 输入框内嵌/按住说话 |
| 17 | ic-keyboard | 96 | 圆角键盘体+九键点 | 中灰 | 语音态键盘钮 |
| 18 | ic-camera | 96 | 相机（体+顶凸+镜头+闪光点） | 中灰 | 输入框内嵌/相册选择 |
| 19 | ic-camera-soft | 96 | 相机（体+顶凸+镜头，无闪光点，与前枚轻微区分） | 中灰 | +面板 拍照 |
| 20 | **ic-regen（新增）** | 96 | 圆弧回环+箭头（重新生成） | 中灰 | 菜单 重新生成 |

备注：react-add「＋」、jz-retry「↻」、emoji、多选对勾、think 折叠符等为文字字形位，**保持不动**（仓库先例，用户未点名；ic-regen 之所以落资产：重生成为 P4.1 已合入的新功能入口且位于用户点名菜单内，字形与线性家族视觉断裂，属「新增钮图标」例外，任务书允许）。

---

## 3. 引用改动（最小面）
1. chat.wxml L499-502 重新生成行：`<view class="act-em-glyph">↻</view>` → `<image class="act-em-ic" src="/assets/images/ic-regen.png"></image>`（与菜单其它行同构；act-row 布局不变）。
2. chat.wxss：删除 .act-em-glyph 样式块（wxml 零引用后为死样式）。
3. 其余全部图标同名原地覆盖——**引用零改动**（含 chat 页外消费页，单源自动同步）。

## 4. 生成管线（可复现脚本入库 scripts/）
- 管线选择理由：B6-1 两代图标均为二进制直提交、仓库无生成脚本；comfyui MCP 会话不可达且不宜做确定性细线资产。本机 python3 有 cairosvg → **SVG 为单一事实源**，cairosvg 渲染 4x 超采样（768px）→ PIL LANCZOS 降采样至目标档（192 或 96px）RGBA。禁 CSS 模拟、禁手绘截图（任务红线）。
- 脚本：`scripts/gen_k12_icons.py`——内嵌全部 20 枚几何（SVG 片段，参数化颜色/尺寸复用 on 态与 -soft 变体）；`--out` 定向 miniprogram/assets/images/；同时输出视觉 QA 拼贴（真实显示尺寸 + 宣纸底）供逐枚目检。
- 产物校验：96/192 同源 = 同 SVG 两档降采样（脚本内对 192 渲染再做 96 降采样与直接 96 渲染比对容差 <2 灰阶）。
- 脚本可重复：同版本 SVG → 同 PNG（确定性输入、确定性输出）。

## 5. 验证
1. `scripts/verify_k12_icons.py` 像素断言（§5.1）。
2. legacy 色清理 grep：#9A8B71/#988878 在 20 枚目标文件主色带归零（PIL 实测为主）；文本层全仓 grep #9A8B71 仅余 app.wxss 调色板变量声明（--ink-soft 等属设计 token，非图标引用）与注释。
3. `node --check` chat.js 语法 + 全量测试：`node miniprogram/tests/*.test.js`（chatmenu.test.js 断 ic-copy 引用在 wxml——本次零改引用，仍绿）。
4. 视觉 QA：20 枚以真实显示档（26px/19px/12px…）拼贴 + vision 逐枚语义读出（赞=拇指、收藏=星、链接=链节…），不符合即回改几何再生成。
5. 模拟器视觉核验——由主会话统一执行（本批只保证资产与引用正确）。

### 5.1 像素断言细则（verify 脚本）
- 每文件 RGBA、尺寸=目标档（192/96 按 §2）、非空（不透明像素 >1%）。
- 常态文件主色命中中灰带：|ΔRGB|≤28 @ #756E63（排除极小点缀/反白文件）；-on 命中 #A93A2C±24；delete 命中 #8C2E22±24；send 命中 #FAF5E7±24（纸白文件统计覆盖 ≥8%）。
- 笔画覆盖率：常态文件 8%≤cov≤22%（96 空间同口径，B6 浓墨 27%+ 与 legacy 细线弱化的折中带）。
- 主笔划实宽 ≥5.5px（96 空间）且 ≤11px（见 §1.2）。
- 96/192 同源比对（§4）。
- 目标文件与未列入批次的文件（ic-lantern 等 tab 系）颜色对照打印（仅记录，不改）。

## 6. 目录归一（icons/ 与 assets/images/）
- 结论：`miniprogram/icons/` 全仓零引用（含历史 tab-*.png/app-icon.png 等独有文件，均无消费），属 B6 时代双写残留 → **整体删除**（git rm -r miniprogram/icons），资产单一目录收敛到 assets/images/。删除前已复核 project.config.json / app.json / packOptions 无引用。同目录内零引用的 ic-*-dark 死资产**不随本批删**（防范围蔓延，列 concern 由主会话拍板后续清理）。

## 7. Concerns / 越界提示（呈主会话）
- C1：ic-chat 为多页 tabbar「对话」非选中钮共用文件（today/celiang/me）——重绘后这些页 tabbar 该钮变中灰，其余三钮仍 legacy 淡棕 #988878；chat 页 tabbar 用 ic-chat-on（不重绘）不受影响。tabbar/导航图标整族（lantern/seal/book2/chat-on/chev/back…）未入本批（用户点名=气泡操作区+输入区），建议下批统一。
- C2：ic-*-dark 全仓零引用死资产（96px 浅字版），未删未重绘；建议后续与 tab 族一并处理。
- C3：删除钮色取 --acc-deep（与删除行文字同值）而非 --acc——保留既有危险色层级，如主会话要纯 #A93A2C 可一行改参重跑。
- C4：ic-camera-soft 与 ic-camera 仅差闪光点；若产品语义上希望「拍照」与「相册」入口强区分，建议拍板换图标语义（如相册=矩形+山形画），本批不擅自扩。
- C5：send 白纸飞机几何同代保留（用户未点名发送钮 icon 造型）；尺寸 16.5px 位 6.8u 笔划。

## 8. 纪律
- git add 仅本批：20 枚 png、chat.wxml/wxss 引用改动、ic-regen.png、scripts/gen_k12_icons.py + verify_k12_icons.py、本 plan、progress.md k12 段；data/ 等脏态禁提交。
- commit 信息 fix:/feat: 前缀 + (k12)；不 push、不重启、不碰后端。

## 9. 执行记录（2026-09-08 实施后补记）
- 几何微调：赞/踩/朗读/分享/多选/话筒 8 枚最终采用 **Lucide(ISC) 标准基础符号路径**（24 格内嵌 scale(4)，脚本头注明出处；视觉 QA 多轮后选中——自绘拇指/喇叭/三点版可读性不达标）；收藏/表情/复制/选取/反馈/删除/引用/重生/键盘/相机/加号/发送 12 枚为本脚本自绘几何。逐枚语义经 vision 目检 19/19 符合。
- 引用改动（已实施）：chat.wxml 重新生成行 ↻ 字形 → ic-regen.png 资产（同名 .act-em-ic 布局不变）；chat.wxss 删除 .act-em-glyph 死样式（wxml 零引用，grep 清零）；ic-regen.png 新增 96px。
- 目录归一（已实施）：miniprogram/icons/ 整体 git rm（全仓零引用，含独有 tab-*/app-icon/ic-clear 等未消费文件），单一目录收敛 assets/images/。
- 生成：python3 scripts/gen_k12_icons.py 覆盖 22 枚（含新 ic-regen）；每文件保持既有物理档位（up/down/keep+on 192px，余 96px）。
- 验证结果：verify_k12_icons.py 全过（主色族/覆盖率带/最细段/legacy 占比<0.5%/落盘=生成器直接输出/96-192 同源）；文本层 #9A8B71 仅余 app.wxss 调色板变量与注释；`node --test 'miniprogram/tests/*.test.js'` 258/258 绿。
- 实际覆盖率区间 7.2%（plus）~23.3%（thumbs），中位 ~14%——低于 B6 浓墨 27-31%，高于 legacy 细线 12-20% 的形态但颜色/造型已换。
- 模拟器视觉核验（菜单 26px/删除 24px/footer 19px/输入 17px/引用 chip 12px 各档）由主会话统一执行。
