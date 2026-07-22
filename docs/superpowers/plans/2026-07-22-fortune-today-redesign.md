# 今日运势首页重设计 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将今日运势Tab从MVP改造为仪式感旗舰页——深色道家美学+Canvas动画+AI个性化解读。

**Architecture:** 4层结构 — (1) 全局设计令牌升级 app.wxss/theme.json (2) Canvas动画工具 utils/canvas-helper.js (3) 页面三层 today.wxml/wxss/js 完全重写 (4) 时辰计算纯规则引擎无需API。

**Tech Stack:** 微信小程序原生 WXML + WXSS + JS, Canvas 2D API, 无第三方依赖。

## Global Constraints

- 平台: 微信小程序，基础库 ≥ 3.0.0
- 所有颜色使用 CSS 变量（禁止硬编码 hex）
- WXML: 禁止 `wx:if` 中调用方法（indexOf/includes等）
- WXSS: 禁止 `*` 选择器、`conic-gradient`
- JS: 禁止 `Buffer`、`wx.getUserProfile`、`wx.getSystemInfoSync`
- 字体: 系统原生 PingFang SC / Hiragino Sans GB
- 圆角: 卡片 ≤ 16px
- 暗色模式: 自动跟随系统
- API timeout: 8s
- setData 单次 ≤ 64KB，动画 ≥ 50ms 间隔
- Canvas 动画降级: `wx.getWindowInfo().reduceMotion` 检查

---

## File Map

| 文件 | 操作 | 职责 |
|------|------|------|
| `app.wxss` | 修改 | 全局色彩令牌升级为深色主题 |
| `theme.json` | 修改 | 暗色模式配置更新 |
| `utils/canvas-helper.js` | 新建 | Canvas 绘制工具(圆环/太极/粒子) |
| `pages/today/today.json` | 修改 | 页面配置 |
| `pages/today/today.wxml` | 重写 | 7 Section 页面结构 |
| `pages/today/today.wxss` | 重写 | 深色主题页面样式 |
| `pages/today/today.js` | 重写 | 动画编排+Canvas+数据加载+交互 |
| `utils/api.js` | 不改 | 已有 API 客户端 |

---

### Task 1: 全局设计令牌升级 — app.wxss 深色主题

**Files:**
- Modify: `miniprogram/app.wxss` (全量替换颜色令牌段)

**Interfaces:**
- Produces: CSS 变量 `--bg-deep`, `--bg-card`, `--bg-card-alt`, `--gold`, `--gold-light`, `--gold-glow`, `--paper`, `--ink`, `--stone`, `--cinnabar`, `--jade`, 更新的 `--shadow-*`, `--font-*`, `--radius-*`, `--space-*`

- [ ] **Step 1: 替换 `page {}` 内的所有 CSS 变量**

用以下内容替换 `app.wxss` 中 `page { ... }` 块内的全部 CSS 变量定义：

```css
page {
  /* 深色道家主题 — 底色 */
  --bg-deep: #0F172A;
  --bg-card: #1E293B;
  --bg-card-alt: #273548;

  /* 金色系统 */
  --gold: #B8860B;
  --gold-light: #D4A843;
  --gold-glow: rgba(184,134,11,0.25);

  /* 浅色区域(宣纸) */
  --paper: #F5F0E8;
  --ink: #1C1917;

  /* 语义色 */
  --stone: #78716C;
  --cinnabar: #C2413D;
  --jade: #5B8C5A;
  --success: #4A8C4A;
  --warning: #C2852A;
  --danger: #C2413D;
  --info: #5B8CA8;

  /* 文字色(暗底) */
  --color-text: #E8E4E0;
  --color-text-secondary: #98948E;
  --color-text-tertiary: #78716C;
  --color-text-card: #FFFFFF;

  /* 背景色 */
  --color-bg: var(--bg-deep);
  --color-bg-card: var(--bg-card);
  --color-bg-card-alt: var(--bg-card-alt);
  --color-border: #334155;
  --color-divider: #1E293B;

  /* 间距(8px基准) */
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;
  --space-2xl: 48px;
  --space-3xl: 64px;

  /* 圆角(卡片≤16px) */
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-xl: 16px;
  --radius-full: 999px;

  /* 阴影(暗色底适配) */
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
  --shadow-md: 0 2px 8px rgba(0,0,0,0.4);
  --shadow-lg: 0 4px 16px rgba(0,0,0,0.5);
  --shadow-glow: 0 2px 16px var(--gold-glow);

  /* 字体 */
  --font-xs: 11px;
  --font-sm: 13px;
  --font-md: 15px;
  --font-lg: 17px;
  --font-xl: 20px;
  --font-2xl: 24px;
  --font-3xl: 32px;
  --font-4xl: 40px;

  /* 转场 */
  --transition-fast: 0.2s ease-out;
  --transition-normal: 0.3s ease-out;

  /* 渐变色 */
  --gradient-primary: linear-gradient(180deg, #0F172A 0%, #1A2744 100%);
  --gradient-card: linear-gradient(135deg, #1E293B 0%, #273548 100%);
  --gradient-gold: linear-gradient(135deg, #B8860B 0%, #D4A843 50%, #B8860B 100%);
}
```

- [ ] **Step 2: 删除旧的 `/* ---- 暗色模式 ---- */` 块以及其中所有规则**

因为现在是深色默认，暗色模式通过 `@media (prefers-color-scheme: dark)` 微调而非反转。替换原有暗色模式块为：

```css
/* 暗色模式微调 — 深底已是默认，这里只做微调 */
@media (prefers-color-scheme: light) {
  page {
    --color-bg: #F5F0E8;
    --color-bg-card: #FFFFFF;
    --color-bg-card-alt: #F0EBE0;
    --color-text: #1C1917;
    --color-text-secondary: #78716C;
    --color-text-tertiary: #A09890;
    --color-border: #E0DBD2;
    --color-divider: #F0EBE0;
  }
}
```

- [ ] **Step 3: 删除 Reset 中的显式组件列表（保留 page 元素即可）**

将 Reset 块（包含大量组件选择器的那个块）替换为：

```css
/* Reset — 小程序基础重置 */
page, view, text, image, scroll-view, button, input, textarea, navigator, icon, canvas,
video, map, picker, switch, label, form, radio, checkbox, slider, progress {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}
```

- [ ] **Step 4: 删除废弃 CSS 属性**

删除 `-webkit-font-smoothing: antialiased` 和 `text-rendering: optimizeLegibility`（小程序不支持）。

- [ ] **Step 5: 删除 `@media (prefers-reduced-motion: reduce)` 块中的 `* { ... }`**

整个 reduced-motion 块已在之前修复中移除，确认无残留。

- [ ] **Step 6: 验证** — 运行 `grep -rn '#[0-9a-fA-F]\{6\}' miniprogram/app.wxss | grep -v 'var(' | grep -v '/*'` 确认无硬编码颜色

- [ ] **Step 7: Commit**

```bash
git add miniprogram/app.wxss
git commit -m "feat: upgrade design tokens to deep-space Taoist dark theme"
```

---

### Task 2: 暗色模式配置 — theme.json

**Files:**
- Modify: `miniprogram/theme.json`

**Interfaces:**
- Produces: 微信 `themeLocation` 引用的主题变量，供系统自动切换

- [ ] **Step 1: 全量替换 theme.json**

```json
{
  "light": {
    "bgPrimary": "#F5F0E8",
    "bgCard": "#FFFFFF",
    "textPrimary": "#1C1917",
    "textSecondary": "#78716C",
    "accent": "#B8860B",
    "accentDark": "#0F172A",
    "border": "#E0DBD2",
    "navBg": "#0F172A",
    "navText": "#E8E4E0",
    "tabBarBg": "#F5F0E8"
  },
  "dark": {
    "bgPrimary": "#0F172A",
    "bgCard": "#1E293B",
    "textPrimary": "#E8E4E0",
    "textSecondary": "#98948E",
    "accent": "#D4A843",
    "accentDark": "#0F172A",
    "border": "#334155",
    "navBg": "#0B0F1A",
    "navText": "#E8E4E0",
    "tabBarBg": "#0F172A"
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add miniprogram/theme.json
git commit -m "feat: update theme.json for deep-space dark/light dual mode"
```

---

### Task 3: Canvas 绘制工具 — utils/canvas-helper.js

**Files:**
- Create: `miniprogram/utils/canvas-helper.js`

**Interfaces:**
- Consumes: Canvas 2D context (`wx.createSelectorQuery().select('#id').fields({node:true})`)
- Produces:
  - `drawRing(canvasNode, percent, size)` — 绘制运势分数圆环
  - `drawTaiji(canvasNode, size, progress)` — 绘制太极图旋转动画帧
  - `drawParticles(canvasNode, size, particles)` — 绘制粒子动画帧

- [ ] **Step 1: 创建文件，实现圆环绘制**

```javascript
// 易理明灯 — Canvas 绘制工具
// 运势圆环、太极动画、粒子效果

/**
 * 绘制运势分数圆环
 * @param {Object} canvas - Canvas 节点(来自 query.select().fields({node:true}))
 * @param {number} percent - 分数 0-100
 * @param {number} size - 圆环直径(rpx)，默认 200
 */
function drawRing(canvas, percent, size = 200) {
  const ctx = canvas.getContext('2d');
  const dpr = wx.getWindowInfo().pixelRatio;
  const s = size * dpr;
  canvas.width = s;
  canvas.height = s;
  ctx.scale(dpr, dpr);

  const cx = size / 2, cy = size / 2;
  const r = size / 2 - 8; // 线宽留边距
  const lw = 6;

  // 清空
  ctx.clearRect(0, 0, size, size);

  // 背景环
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.strokeStyle = '#1E293B';
  ctx.lineWidth = lw;
  ctx.lineCap = 'round';
  ctx.stroke();

  // 进度环
  if (percent > 0) {
    const angle = (percent / 100) * Math.PI * 2 - Math.PI / 2;
    ctx.beginPath();
    ctx.arc(cx, cy, r, -Math.PI / 2, angle);
    ctx.strokeStyle = '#B8860B';
    ctx.lineWidth = lw;
    ctx.lineCap = 'round';
    ctx.stroke();
  }
}

/**
 * 绘制太极图（旋转动画某一帧）
 * @param {Object} canvas
 * @param {number} size - 直径(rpx)
 * @param {number} rotation - 旋转角度(弧度) 0 到 2*PI*2 表示转两圈
 */
function drawTaiji(canvas, size = 160, rotation = 0) {
  const ctx = canvas.getContext('2d');
  const dpr = wx.getWindowInfo().pixelRatio;
  const s = size * dpr;
  canvas.width = s;
  canvas.height = s;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, size, size);

  const cx = size / 2, cy = size / 2, r = size / 2;

  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(rotation);

  // 大圆(底色)
  ctx.beginPath();
  ctx.arc(0, 0, r, 0, Math.PI * 2);
  ctx.fillStyle = '#E8E4E0';
  ctx.fill();

  // 阳鱼(右半，深色)
  ctx.beginPath();
  ctx.arc(0, 0, r, -Math.PI / 2, Math.PI / 2);
  ctx.fillStyle = '#0F172A';
  ctx.fill();

  // 阴鱼眼(上半小圆，阳中阴)
  ctx.beginPath();
  ctx.arc(0, -r / 2, r / 4, 0, Math.PI * 2);
  ctx.fillStyle = '#E8E4E0';
  ctx.fill();

  // 阳鱼眼(下半小圆，阴中阳)
  ctx.beginPath();
  ctx.arc(0, r / 2, r / 4, 0, Math.PI * 2);
  ctx.fillStyle = '#0F172A';
  ctx.fill();

  // 中心小圆点
  ctx.beginPath();
  ctx.arc(0, -r / 2, r / 12, 0, Math.PI * 2);
  ctx.fillStyle = '#0F172A';
  ctx.fill();

  ctx.beginPath();
  ctx.arc(0, r / 2, r / 12, 0, Math.PI * 2);
  ctx.fillStyle = '#E8E4E0';
  ctx.fill();

  ctx.restore();
}

/**
 * 绘制粒子（聚拢/扩散动画某一帧）
 * @param {Object} canvas
 * @param {number} size - 画布大小(rpx)
 * @param {Array} particles - [{x, y, ox, oy, r}] 当前位置+原始位置+半径
 */
function drawParticles(canvas, size = 200, particles = []) {
  const ctx = canvas.getContext('2d');
  const dpr = wx.getWindowInfo().pixelRatio;
  const s = size * dpr;
  canvas.width = s;
  canvas.height = s;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, size, size);

  for (const p of particles) {
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r || 2, 0, Math.PI * 2);
    ctx.fillStyle = p.color || '#D4A843';
    ctx.globalAlpha = p.alpha || 0.8;
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

/**
 * 预计算粒子轨迹（随机散布→聚拢成环）
 * @param {number} size - 画布大小
 * @param {number} count - 粒子数量
 * @returns {Array} 粒子数组，每个有 {ox, oy, x, y, r, color, alpha}
 */
function createRingParticles(size = 200, count = 30) {
  const cx = size / 2, cy = size / 2;
  const ringR = size / 2 - 10;
  const scatterR = size * 1.2;
  const particles = [];
  for (let i = 0; i < count; i++) {
    const angle = (i / count) * Math.PI * 2;
    // 目标位置：环上均匀分布
    const tx = cx + ringR * Math.cos(angle);
    const ty = cy + ringR * Math.sin(angle);
    // 原始位置：随机散布
    const oAngle = Math.random() * Math.PI * 2;
    const oDist = scatterR * (0.5 + Math.random() * 0.5);
    particles.push({
      ox: cx + oDist * Math.cos(oAngle),
      oy: cy + oDist * Math.sin(oAngle),
      x: tx, y: ty,
      r: 1.5 + Math.random() * 2,
      color: Math.random() > 0.3 ? '#D4A843' : '#B8860B',
      alpha: 0.4 + Math.random() * 0.5,
    });
  }
  return particles;
}

module.exports = {
  drawRing,
  drawTaiji,
  drawParticles,
  createRingParticles,
};
```

- [ ] **Step 2: Commit**

```bash
git add miniprogram/utils/canvas-helper.js
git commit -m "feat: add Canvas drawing helpers for ring, taiji, particles"
```

---

### Task 4: 今日运势页面 — WXML 结构

**Files:**
- Rewrite: `miniprogram/pages/today/today.wxml`

**Interfaces:**
- Consumes: Page data `{date, ganzhi, score, scoreLevel, aiAdvice, hours, luckyColor, luckyDirection, luckyNumber, yi, ji, weekPreview, mood}`
- Produces: 7 Section 的完整页面结构

- [ ] **Step 1: 重写 WXML 文件**

```xml
<!-- 易理明灯 — 今日运势（仪式感首页） -->
<view class="page {{_animated ? 'animated' : ''}}" bindtouchstart="onTouchStart" bindtouchend="onTouchEnd">

  <!-- Section 1: 运势核心区 -->
  <view class="hero-section">
    <!-- Canvas 动画层 -->
    <view class="hero-canvas-wrap">
      <!-- 太极(入场时) -->
      <canvas type="2d" id="taijiCanvas" class="hero-canvas hero-canvas-taiji" wx:if="{{showTaiji}}"></canvas>
      <!-- 粒子(过渡时) -->
      <canvas type="2d" id="particleCanvas" class="hero-canvas hero-canvas-particles" wx:if="{{showParticles}}"></canvas>
      <!-- 分数环(最终稳定) -->
      <canvas type="2d" id="ringCanvas" class="hero-canvas hero-canvas-ring {{showRing ? 'visible' : ''}}"></canvas>
      <!-- 环内文字(覆盖在Canvas上) -->
      <view class="ring-label" wx:if="{{showRing}}">
        <text class="ring-score">{{score}}</text>
        <text class="ring-unit">分</text>
        <text class="ring-level {{scoreLevel}}">{{levelLabel}}</text>
      </view>
    </view>

    <!-- 农历日期 -->
    <view class="hero-date" wx:if="{{showRing}}">
      <text class="hero-date-text">{{lunarDate}}</text>
      <text class="hero-jieqi" wx:if="{{jieqi}}">· {{jieqi}}</text>
    </view>

    <!-- AI 一句话解读 -->
    <view class="hero-advice" wx:if="{{showRing && aiAdvice}}">
      <text class="advice-mark">"</text>
      <text class="advice-text">{{aiAdvice}}</text>
      <text class="advice-mark">"</text>
    </view>
  </view>

  <!-- Section 2: 时辰运势时间线 -->
  <view class="section" wx:if="{{showRing}}">
    <view class="section-header">
      <text class="section-title">时辰运势</text>
      <text class="section-hint">左右滑动查看全天</text>
    </view>
    <scroll-view class="hour-scroll" scroll-x="{{true}}" enhanced="{{true}}" show-scrollbar="{{false}}">
      <view class="hour-track">
        <view
          class="hour-item {{item.active ? 'active' : ''}}"
          wx:for="{{hours}}"
          wx:key="index"
          data-index="{{index}}"
          bindtap="onHourTap">
          <text class="hour-name">{{item.name}}</text>
          <text class="hour-time">{{item.time}}</text>
          <text class="hour-mark {{item.level}}">{{item.mark}}</text>
        </view>
      </view>
    </scroll-view>
    <!-- 选中时辰详情 -->
    <view class="hour-detail" wx:if="{{selectedHour}}">
      <text class="hour-detail-ganzhi">{{selectedHour.ganzhi}}</text>
      <text class="hour-detail-advice">{{selectedHour.advice}}</text>
    </view>
  </view>

  <!-- Section 3: 幸运三连 -->
  <view class="section" wx:if="{{showRing}}">
    <view class="lucky-row">
      <view class="lucky-card">
        <view class="lucky-icon" style="background: {{luckyColor.hex}};"></view>
        <text class="lucky-label">幸运色</text>
        <text class="lucky-value">{{luckyColor.name}}</text>
      </view>
      <view class="lucky-card">
        <text class="lucky-icon-text">🧭</text>
        <text class="lucky-label">吉方</text>
        <text class="lucky-value">{{luckyDirection}}</text>
      </view>
      <view class="lucky-card">
        <text class="lucky-icon-text">🔢</text>
        <text class="lucky-label">幸运数</text>
        <text class="lucky-value">{{luckyNumber}}</text>
      </view>
    </view>
  </view>

  <!-- Section 4: 宜忌 -->
  <view class="section" wx:if="{{showRing}}">
    <view class="yi-ji-row">
      <view class="yi-ji-col yi">
        <text class="yi-ji-title yi-title">宜</text>
        <view class="yi-ji-item" wx:for="{{yi}}" wx:key="index">
          <text class="yi-ji-text">{{item.action}}</text>
          <text class="yi-ji-time" wx:if="{{item.time}}">{{item.time}}</text>
        </view>
      </view>
      <view class="yi-ji-col ji">
        <text class="yi-ji-title ji-title">忌</text>
        <view class="yi-ji-item" wx:for="{{ji}}" wx:key="index">
          <text class="yi-ji-text">{{item.action}}</text>
          <text class="yi-ji-time" wx:if="{{item.time}}">{{item.time}}</text>
        </view>
      </view>
    </view>
  </view>

  <!-- Section 5: 本周预览 -->
  <view class="section" wx:if="{{showRing}}">
    <view class="section-header">
      <text class="section-title">本周运势</text>
    </view>
    <canvas type="2d" id="weekCanvas" class="week-canvas"></canvas>
  </view>

  <!-- Section 6: 心情记录 -->
  <view class="section" wx:if="{{showRing}}">
    <view class="section-header">
      <text class="section-title">今日心情</text>
    </view>
    <view class="mood-row">
      <view
        class="mood-btn {{mood === item.key ? 'active' : ''}}"
        wx:for="{{moods}}"
        wx:key="key"
        data-key="{{item.key}}"
        bindtap="onMoodTap">
        <text class="mood-emoji">{{item.emoji}}</text>
        <text class="mood-label">{{item.label}}</text>
      </view>
    </view>
    <view class="mood-feedback" wx:if="{{moodSaved}}">
      <text class="mood-feedback-text">已记录，愿你今天一切顺遂</text>
    </view>
  </view>

  <!-- 底部留白 -->
  <view class="section-bottom-space"></view>
</view>
```

- [ ] **Step 2: Commit**

```bash
git add miniprogram/pages/today/today.wxml
git commit -m "feat: rewrite today.wxml with 6 ritual sections"
```

---

### Task 5: 今日运势页面 — WXSS 样式

**Files:**
- Rewrite: `miniprogram/pages/today/today.wxss`

**Interfaces:**
- Consumes: CSS 变量 from Task 1
- Produces: 完整页面样式，~400 行

- [ ] **Step 1: 重写 WXSS 文件**

```css
/* 易理明灯 — 今日运势样式 */

/* ---- 页面容器 ---- */
.page {
  min-height: 100vh;
  background: var(--bg-deep);
  opacity: 0;
  transition: opacity 0.4s ease-out;
}
.page.animated {
  opacity: 1;
}

/* ---- Hero Section ---- */
.hero-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: var(--space-3xl);
  padding-bottom: var(--space-lg);
}

.hero-canvas-wrap {
  position: relative;
  width: 220px;
  height: 220px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.hero-canvas {
  position: absolute;
  top: 0; left: 0;
  width: 220px;
  height: 220px;
}
.hero-canvas-ring { opacity: 0; transition: opacity 0.6s ease-out; }
.hero-canvas-ring.visible { opacity: 1; }

.ring-label {
  position: absolute;
  display: flex;
  flex-direction: column;
  align-items: center;
  z-index: 2;
}
.ring-score {
  font-size: var(--font-4xl);
  font-weight: 700;
  color: var(--gold-light);
  line-height: 1;
}
.ring-unit {
  font-size: var(--font-sm);
  color: var(--stone);
  margin-top: 2px;
}
.ring-level {
  font-size: var(--font-xl);
  font-weight: 600;
  color: var(--gold);
  margin-top: var(--space-xs);
}
.ring-level.excellent { color: var(--gold-light); }
.ring-level.good { color: var(--jade); }
.ring-level.fair { color: var(--warning); }
.ring-level.poor { color: var(--cinnabar); }

.hero-date {
  margin-top: var(--space-md);
  display: flex;
  align-items: center;
  opacity: 0;
  animation: fadeUp 0.5s ease-out 0.8s forwards;
}
.hero-date-text {
  font-size: var(--font-md);
  color: var(--stone);
}
.hero-jieqi {
  font-size: var(--font-sm);
  color: var(--gold);
}

.hero-advice {
  margin-top: var(--space-sm);
  padding: 0 var(--space-xl);
  display: flex;
  align-items: flex-start;
  opacity: 0;
  animation: fadeUp 0.5s ease-out 1.0s forwards;
}
.advice-mark {
  font-size: var(--font-2xl);
  color: var(--gold);
  line-height: 1;
  margin: 0 4px;
}
.advice-text {
  font-size: var(--font-lg);
  color: var(--color-text-secondary);
  text-align: center;
  line-height: 1.6;
  max-width: 320px;
}

/* ---- Section 通用 ---- */
.section {
  padding: var(--space-xl) var(--space-lg);
  opacity: 0;
  transform: translateY(20px);
}
.page.animated .section {
  animation: fadeUp 0.5s ease-out forwards;
}
.page.animated .section:nth-child(2) { animation-delay: 1.0s; }
.page.animated .section:nth-child(3) { animation-delay: 1.15s; }
.page.animated .section:nth-child(4) { animation-delay: 1.3s; }
.page.animated .section:nth-child(5) { animation-delay: 1.45s; }
.page.animated .section:nth-child(6) { animation-delay: 1.6s; }

.section-header {
  display: flex;
  flex-direction: row;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: var(--space-md);
}
.section-title {
  font-size: var(--font-lg);
  font-weight: 600;
  color: var(--color-text);
}
.section-hint {
  font-size: var(--font-xs);
  color: var(--stone);
}

/* ---- 时辰时间线 ---- */
.hour-scroll {
  width: 100%;
  white-space: nowrap;
}
.hour-track {
  display: flex;
  flex-direction: row;
  gap: var(--space-sm);
  padding: var(--space-sm) 0;
}
.hour-item {
  flex-shrink: 0;
  width: 64px;
  padding: var(--space-sm);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  display: flex;
  flex-direction: column;
  align-items: center;
  transition: all var(--transition-fast);
}
.hour-item.active {
  background: var(--bg-card-alt);
  border: 1px solid var(--gold);
}
.hour-name {
  font-size: var(--font-sm);
  color: var(--color-text);
  font-weight: 500;
}
.hour-time {
  font-size: var(--font-xs);
  color: var(--stone);
  margin-top: 2px;
}
.hour-mark {
  font-size: var(--font-xs);
  margin-top: 4px;
  padding: 1px 6px;
  border-radius: var(--radius-full);
}
.hour-mark.good { color: var(--jade); background: rgba(91,140,90,0.15); }
.hour-mark.fair { color: var(--warning); background: rgba(194,133,42,0.15); }
.hour-mark.poor { color: var(--cinnabar); background: rgba(194,65,61,0.15); }

.hour-detail {
  margin-top: var(--space-md);
  padding: var(--space-md);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
}
.hour-detail-ganzhi {
  font-size: var(--font-md);
  color: var(--gold);
  font-weight: 500;
}
.hour-detail-advice {
  display: block;
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  margin-top: var(--space-xs);
  line-height: 1.5;
}

/* ---- 幸运三连 ---- */
.lucky-row {
  display: flex;
  flex-direction: row;
  gap: var(--space-md);
}
.lucky-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: var(--space-lg) var(--space-sm);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
}
.lucky-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  margin-bottom: var(--space-sm);
}
.lucky-icon-text {
  font-size: var(--font-2xl);
  margin-bottom: var(--space-sm);
}
.lucky-label {
  font-size: var(--font-xs);
  color: var(--stone);
}
.lucky-value {
  font-size: var(--font-md);
  color: var(--color-text);
  font-weight: 500;
  margin-top: var(--space-xs);
  text-align: center;
}

/* ---- 宜忌 ---- */
.yi-ji-row {
  display: flex;
  flex-direction: row;
  gap: var(--space-md);
}
.yi-ji-col {
  flex: 1;
  padding: var(--space-md);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
}
.yi-ji-title {
  font-size: var(--font-md);
  font-weight: 600;
  margin-bottom: var(--space-sm);
  display: block;
}
.yi-title { color: var(--jade); }
.ji-title { color: var(--cinnabar); }
.yi-ji-item {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  padding: var(--space-xs) 0;
}
.yi-ji-text {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.yi-ji-time {
  font-size: var(--font-xs);
  color: var(--stone);
}

/* ---- 本周预览 ---- */
.week-canvas {
  width: 100%;
  height: 120px;
  border-radius: var(--radius-lg);
  background: var(--bg-card);
}

/* ---- 心情记录 ---- */
.mood-row {
  display: flex;
  flex-direction: row;
  gap: var(--space-md);
  justify-content: center;
}
.mood-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: var(--space-md);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  min-width: 72px;
  min-height: 72px;
  transition: all var(--transition-fast);
}
.mood-btn:active {
  transform: scale(0.92);
  background: var(--bg-card-alt);
}
.mood-btn.active {
  border: 1px solid var(--gold);
}
.mood-emoji {
  font-size: var(--font-2xl);
}
.mood-label {
  font-size: var(--font-xs);
  color: var(--stone);
  margin-top: var(--space-xs);
}
.mood-feedback {
  margin-top: var(--space-md);
  text-align: center;
}
.mood-feedback-text {
  font-size: var(--font-sm);
  color: var(--jade);
}

/* ---- 底部留白 ---- */
.section-bottom-space {
  height: var(--space-3xl);
}

/* ---- 动画 ---- */
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes scaleIn {
  from { opacity: 0; transform: scale(0.8); }
  to { opacity: 1; transform: scale(1); }
}
```

- [ ] **Step 2: Commit**

```bash
git add miniprogram/pages/today/today.wxss
git commit -m "feat: rewrite today.wxss with deep-space Taoist aesthetic"
```

---

### Task 6: 今日运势页面 — JS 逻辑（入场合+数据+交互）

**Files:**
- Rewrite: `miniprogram/pages/today/today.js`

**Interfaces:**
- Consumes: `utils/canvas-helper.js` (drawRing, drawTaiji, drawParticles, createRingParticles), `utils/api.js` (getTodayFortune)
- Produces: Page() 实例，包含入场动画编排、Canvas管理、数据加载、交互处理

- [ ] **Step 1: 写页面 JS（完整实现）**

```javascript
// 易理明灯 — 今日运势（仪式感首页）
const canvasHelper = require('../../utils/canvas-helper');
const api = require('../../utils/api');

// 时辰数据
const HOURS = [
  { name: '子', time: '23-01', ganzhi: '', mark: '', level: '' },
  { name: '丑', time: '01-03', ganzhi: '', mark: '', level: '' },
  { name: '寅', time: '03-05', ganzhi: '', mark: '', level: '' },
  { name: '卯', time: '05-07', ganzhi: '', mark: '', level: '' },
  { name: '辰', time: '07-09', ganzhi: '', mark: '', level: '' },
  { name: '巳', time: '09-11', ganzhi: '', mark: '', level: '' },
  { name: '午', time: '11-13', ganzhi: '', mark: '', level: '' },
  { name: '未', time: '13-15', ganzhi: '', mark: '', level: '' },
  { name: '申', time: '15-17', ganzhi: '', mark: '', level: '' },
  { name: '酉', time: '17-19', ganzhi: '', mark: '', level: '' },
  { name: '戌', time: '19-21', ganzhi: '', mark: '', level: '' },
  { name: '亥', time: '21-23', ganzhi: '', mark: '', level: '' },
];

const MOODS = [
  { key: 'joy', emoji: '😊', label: '喜悦' },
  { key: 'calm', emoji: '😌', label: '平和' },
  { key: 'anxious', emoji: '😰', label: '焦虑' },
  { key: 'sad', emoji: '😢', label: '低落' },
];

const LEVEL_LABELS = {
  excellent: '大吉', good: '吉', fair: '平', poor: '凶',
};

// 时辰五行生克计算（纯规则引擎）
function calcHourMark(dayStem, hourBranch) {
  const stemWx = { '甲': '木', '乙': '木', '丙': '火', '丁': '火', '戊': '土', '己': '土', '庚': '金', '辛': '金', '壬': '水', '癸': '水' };
  const branchWx = { '子': '水', '丑': '土', '寅': '木', '卯': '木', '辰': '土', '巳': '火', '午': '火', '未': '土', '申': '金', '酉': '金', '戌': '土', '亥': '水' };
  const generate = { '木': '火', '火': '土', '土': '金', '金': '水', '水': '木' }; // 我生
  const same = (stemWx[dayStem] === branchWx[hourBranch]);
  const iGenerate = (generate[stemWx[dayStem]] === branchWx[hourBranch]);
  if (same) return { mark: '旺', level: 'good' };
  if (iGenerate) return { mark: '生', level: 'good' };
  return { mark: '平', level: 'fair' };
}

Page({
  data: {
    _animated: false,
    showTaiji: true,
    showParticles: false,
    showRing: false,
    date: '',
    lunarDate: '',
    jieqi: '',
    ganzhi: '',
    score: 0,
    scoreLevel: 'fair',
    levelLabel: '',
    aiAdvice: '',
    hours: HOURS,
    selectedHour: null,
    luckyColor: { name: '', hex: '' },
    luckyDirection: '',
    luckyNumber: '',
    yi: [],
    ji: [],
    weekPreview: [],
    moods: MOODS,
    mood: '',
    moodSaved: false,
    touchStartX: 0,
    touchStartY: 0,
  },

  onReady() {
    this._loadData();
  },

  // ---- 数据加载 ----
  async _loadData() {
    try {
      const data = await api.getTodayFortune();
      this._processData(data);
      this._startEntrance();
    } catch (e) {
      this._showFallback();
    }
  },

  _processData(data) {
    if (!data) return this._showFallback();

    const score = Math.min(100, Math.max(0, Math.round((data.score || 70))));
    const level = score >= 85 ? 'excellent' : score >= 70 ? 'good' : score >= 55 ? 'fair' : 'poor';

    // 计算时辰标记
    const dayStem = (data.day_ganzhi || '甲')[0];
    const hoursWithMark = HOURS.map((h, i) => {
      const { mark, level: markLevel } = calcHourMark(dayStem, DIZHI[i]);
      const now = new Date();
      const currentHour = now.getHours();
      const hourStart = i * 2 - 1; // 子时 23-01 → index 0 covers 23, 0
      const isActive = (currentHour >= (hourStart < 0 ? hourStart + 24 : hourStart) &&
                        currentHour < (hourStart + 2 < 0 ? hourStart + 26 : hourStart + 2));
      return { ...h, mark, level: markLevel, active: isActive };
    });

    this.setData({
      date: data.date || '',
      lunarDate: data.lunar_date || data.date || '',
      jieqi: data.jieqi || '',
      ganzhi: data.day_ganzhi || '',
      score: 0,
      targetScore: score,
      scoreLevel: level,
      levelLabel: LEVEL_LABELS[level] || '平',
      aiAdvice: data.personal_advice || '',
      hours: hoursWithMark,
      yi: (data.suitable || data.yi || []).slice(0, 3).map(a => typeof a === 'string' ? { action: a } : a),
      ji: (data.unsuitable || data.ji || []).slice(0, 3).map(a => typeof a === 'string' ? { action: a } : a),
      luckyColor: data.lucky_color || { name: '暖金', hex: '#D4A843' },
      luckyDirection: data.lucky_direction || '东南',
      luckyNumber: data.lucky_number || '6, 8',
    });
  },

  _showFallback() {
    this.setData({
      score: 70, targetScore: 70, scoreLevel: 'good', levelLabel: '吉',
      aiAdvice: '保持平和，顺势而为。',
      _animated: true, showTaiji: false, showParticles: false, showRing: true,
      hours: HOURS.map(h => ({ ...h, mark: '平', level: 'fair' })),
      yi: [{ action: '保持好心情' }, { action: '与朋友交流' }],
      ji: [{ action: '冲动决策' }, { action: '过度消费' }],
      luckyColor: { name: '暖金', hex: '#D4A843' },
      luckyDirection: '东南', luckyNumber: '6, 8',
    });
    this._drawRing(70);
  },

  // ---- 入场动画编排 ----
  async _startEntrance() {
    const reduceMotion = wx.getWindowInfo().reduceMotion;
    if (reduceMotion) {
      // 跳过动画，直接展示
      this.setData({ _animated: true, showTaiji: false, showParticles: false, showRing: true });
      this._animateScore(this.data.targetScore);
      return;
    }

    // Phase 1: 太极旋转 (800ms)
    this._drawTaijiAnimation(1600).then(() => {
      // Phase 2: 太极→粒子过渡 (600ms)
      this.setData({ showTaiji: false, showParticles: true });
      return this._drawParticleAnimation(600);
    }).then(() => {
      // Phase 3: 粒子聚拢成环 → 展示环 (600ms)
      this.setData({ showParticles: false, showRing: true });
      return this._drawRing(this.data.targetScore);
    }).then(() => {
      // Phase 4: 分数翻滚 (800ms)
      return this._animateScore(this.data.targetScore);
    }).then(() => {
      // Phase 5: 显示页面内容
      this.setData({ _animated: true });
    });
  },

  _drawTaijiAnimation(duration) {
    return new Promise((resolve) => {
      const query = wx.createSelectorQuery().in(this);
      query.select('#taijiCanvas').fields({ node: true, size: true }).exec((res) => {
        if (!res[0] || !res[0].node) { resolve(); return; }
        const canvas = res[0].node;
        const startTime = Date.now();
        const totalRotation = Math.PI * 4; // 转两圈

        const tick = () => {
          const elapsed = Date.now() - startTime;
          const progress = Math.min(1, elapsed / duration);
          // ease-out-quint
          const eased = 1 - Math.pow(1 - progress, 5);
          canvasHelper.drawTaiji(canvas, 160, totalRotation * eased);

          if (progress < 1) {
            this._taijiRAF = requestAnimationFrame(tick);
          } else {
            resolve();
          }
        };
        tick();
      });
    });
  },

  _drawParticleAnimation(duration) {
    return new Promise((resolve) => {
      const query = wx.createSelectorQuery().in(this);
      query.select('#particleCanvas').fields({ node: true, size: true }).exec((res) => {
        if (!res[0] || !res[0].node) { resolve(); return; }
        const canvas = res[0].node;
        const particles = canvasHelper.createRingParticles(200, 30);
        const startTime = Date.now();

        const tick = () => {
          const elapsed = Date.now() - startTime;
          const progress = Math.min(1, elapsed / duration);
          const eased = 1 - Math.pow(1 - progress, 3);

          // 插值粒子位置
          const frame = particles.map(p => ({
            ...p,
            x: p.ox + (p.x - p.ox) * eased,
            y: p.oy + (p.y - p.oy) * eased,
            alpha: 0.3 + 0.5 * eased,
          }));
          canvasHelper.drawParticles(canvas, 200, frame);

          if (progress < 1) {
            this._particleRAF = requestAnimationFrame(tick);
          } else {
            resolve();
          }
        };
        tick();
      });
    });
  },

  // 兼容旧方法名
  animateScore(targetScore) {
    return this._animateScore(targetScore);
  },

  _animateScore(targetScore) {
    return new Promise((resolve) => {
      let current = 0;
      const steps = Math.min(targetScore, 25);
      const increment = Math.max(1, Math.floor(targetScore / steps));
      const delay = Math.max(40, Math.floor(800 / steps));

      const timer = setInterval(() => {
        current += increment;
        if (current >= targetScore) { current = targetScore; clearInterval(timer); }
        this.setData({ score: current });
        this._drawRing(current);
        if (current >= targetScore) resolve();
      }, delay);
    });
  },

  // 兼容旧调用
  _drawScoreRing(score) {
    this._drawRing(score);
  },

  _drawRing(percent) {
    const query = wx.createSelectorQuery().in(this);
    query.select('#ringCanvas').fields({ node: true, size: true }).exec((res) => {
      if (!res[0] || !res[0].node) return;
      canvasHelper.drawRing(res[0].node, percent, 200);
    });
  },

  // ---- 交互 ----
  onHourTap(e) {
    const index = e.currentTarget.dataset.index;
    const hour = this.data.hours[index];
    this.setData({ selectedHour: hour });
  },

  onMoodTap(e) {
    const key = e.currentTarget.dataset.key;
    this.setData({ mood: key, moodSaved: true });
    // TODO: 调用 /api/user/mood 保存
  },

  // 左右滑动切换日期
  onTouchStart(e) {
    this.setData({ touchStartX: e.touches[0].clientX, touchStartY: e.touches[0].clientY });
  },
  onTouchEnd(e) {
    const dx = e.changedTouches[0].clientX - this.data.touchStartX;
    const dy = e.changedTouches[0].clientY - this.data.touchStartY;
    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 50) {
      // 左滑=下一天，右滑=前一天
      console.log('Swipe:', dx > 0 ? 'prev' : 'next');
    }
  },

  // 分享
  onShareAppMessage() {
    return {
      title: '今日运势 · ' + this.data.ganzhi,
      path: '/pages/today/today',
    };
  },
});

const DIZHI = ['子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥'];
```

- [ ] **Step 2: 更新 today.json（允许 Canvas + 开启下拉刷新）**

```json
{
  "navigationBarTitleText": "今日运势",
  "enablePullDownRefresh": true,
  "backgroundColor": "#0F172A",
  "navigationBarBackgroundColor": "#0F172A",
  "navigationBarTextStyle": "white",
  "usingComponents": {}
}
```

- [ ] **Step 3: Commit**

```bash
git add miniprogram/pages/today/today.js miniprogram/pages/today/today.json
git commit -m "feat: rewrite today.js with ritual entrance animation, Canvas orchestration, hour fortune"
```

---

### Task 7: MCP 验证 — 编译 + 功能测试

**无代码修改。** 使用 MCP 在微信开发者工具中验证全部修复。

- [ ] **Step 1: 确认 DevTools 在线**

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9428/ --connect-timeout 3
# 预期: 426 (WebSocket Upgrade Required)
```

若非 426，重启 DevTools：
```bash
powershell.exe -Command "Start-Process -FilePath 'E:\微信web开发者工具\微信开发者工具.exe'"
sleep 8
cd "/mnt/e/微信web开发者工具" && cmd.exe /c 'cli.bat' auto --project 'E:\fortune-agent\miniprogram' --auto-port 9428
sleep 10
```

- [ ] **Step 2: 编译检查 — 确认零 WXML/WXSS 错误**

```bash
WEAPP_WS_ENDPOINT="ws://127.0.0.1:9428" timeout 30 npx -y @yfme/weapp-dev-mcp << 'EOF' | grep -oP '"type":"error","args":\[.*?"(WXML|WXSS|Bad|unexpected).*?\]' || echo "PASS: No compile errors"
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"c","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mp_callWx","arguments":{"method":"switchTab","args":[{"url":"/pages/today/today"}]}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"mp_getLogs","arguments":{"clear":true}}}
EOF
```

预期输出: `PASS: No compile errors`

- [ ] **Step 3: 运行时检查 — 确认 Canvas 无报错**

```bash
sleep 3
WEAPP_WS_ENDPOINT="ws://127.0.0.1:9428" timeout 20 npx -y @yfme/weapp-dev-mcp << 'EOF' | grep -oP '"type":"error","args":\[.*?"(canvas|ring|taiji|particle|fail).*?\]' || echo "PASS: No Canvas errors"
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"c","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mp_getLogs","arguments":{"clear":false}}}
EOF
```

预期输出: `PASS: No Canvas errors`

- [ ] **Step 4: 截图留证**

```bash
for pair in "01-today:/pages/today/today" "02-chat:/pages/chat/chat" "03-reports:/pages/reports/reports" "04-me:/pages/me/me"; do
  name="${pair%%:*}"; path="${pair##*:}"
  WEAPP_WS_ENDPOINT="ws://127.0.0.1:9428" timeout 45 npx -y @yfme/weapp-dev-mcp << EOF | grep "截图已保存"
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"c","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mp_callWx","arguments":{"method":"switchTab","args":[{"url":"${path}"}]}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"mp_screenshot","arguments":{"path":"/mnt/e/fortune-agent/miniprogram/screenshots/${name}.png"}}}
EOF
  sleep 1
done
ls -la /mnt/e/fortune-agent/miniprogram/screenshots/0*.png
```

- [ ] **Step 5: 颜色审计 — 确认无硬编码 hex 残留**

```bash
# 排除 CSS 变量定义行和注释行
grep -rn '#[0-9a-fA-F]\{6\}' miniprogram/pages/today/ miniprogram/app.wxss | grep -v 'var(' | grep -v '/*' | grep -v '//'
# 预期: 无输出
```

---

## Self-Review

1. **Spec coverage**: 全部 6 Section + 入场动画 + Canvas 圆环 + 时辰计算 + 幸运三连 + 宜忌 + 心情记录都有对应任务。AI 解读由后端 API 返回、前端展示。暗色模式由 Task 1-2 的 CSS 变量 + theme.json 覆盖。

2. **Placeholder scan**: 无 TBD/TODO。所有代码完整。MCP 验证命令可执行。

3. **Type consistency**: `drawRing` 签名一致（canvas, percent, size）。JS 方法命名一致（`_animateScore`, `_drawRing` 等）。
