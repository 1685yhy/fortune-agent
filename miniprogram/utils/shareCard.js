// 易理明灯 — 分享卡片 Canvas 绘制系统
// 设计：温暖金+柔和粉+浅紫 年轻女性审美
// 画布：750x1200（朋友圈最优尺寸）
//
// 使用方式：
//   1. 在 wxml 中添加 <canvas type="2d" id="shareCanvas" style="width:750px;height:1200px;position:fixed;left:-9999px;"></canvas>
//   2. 调用 drawFortuneCard(data, callback) 等

// ---- 设计常量 ----
const COLORS = {
  bgStart: '#FDF8F3',
  bgEnd: '#F5F0EB',
  cardBg: '#FFFFFF',
  gold: '#D4A574',
  goldLight: '#E8D5B0',
  goldDark: '#B8895A',
  pink: '#E8C4B8',
  pinkLight: '#F5E0D8',
  rose: '#D4A0A0',
  lavender: '#C8B8D4',
  text: '#8B7E74',
  textDark: '#5C4F46',
  textLight: '#B5A89E',
  white: '#FFFFFF',
  cream: '#F5F0EB',
  shadow: 'rgba(180, 150, 130, 0.15)',
};

const FONTS = {
  regular: 'normal 28px "PingFang SC", "Helvetica Neue", sans-serif',
  medium: '500 30px "PingFang SC", "Helvetica Neue", sans-serif',
  bold: 'bold 36px "PingFang SC", "Helvetica Neue", sans-serif',
  title: 'bold 48px "PingFang SC", "Helvetica Neue", sans-serif',
  score: 'bold 80px "PingFang SC", "Helvetica Neue", sans-serif',
  small: 'normal 24px "PingFang SC", "Helvetica Neue", sans-serif',
};

const CANVAS = {
  width: 750,
  height: 1200,
};

// ---- 工具函数 ----

/**
 * 绘制圆角矩形路径
 */
function roundRect(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h - r);
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h);
  ctx.arcTo(x, y + h, x, y + h - r, r);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
}

/**
 * 绘制多行文本自动换行
 */
function wrapText(ctx, text, x, y, maxWidth, lineHeight) {
  const chars = text.split('');
  let line = '';
  let lineY = y;
  for (const char of chars) {
    if (char === '\n') {
      ctx.fillText(line, x, lineY);
      line = '';
      lineY += lineHeight;
      continue;
    }
    const testLine = line + char;
    const metrics = ctx.measureText(testLine);
    if (metrics.width > maxWidth && line !== '') {
      ctx.fillText(line, x, lineY);
      line = char;
      lineY += lineHeight;
    } else {
      line = testLine;
    }
  }
  if (line) {
    ctx.fillText(line, x, lineY);
  }
  return lineY;
}

/**
 * 绘制装饰性顶部花纹
 */
function drawTopDecoration(ctx) {
  // 顶部渐变条
  const grad = ctx.createLinearGradient(0, 0, CANVAS.width, 0);
  grad.addColorStop(0, COLORS.goldLight);
  grad.addColorStop(0.5, COLORS.pink);
  grad.addColorStop(1, COLORS.goldLight);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, CANVAS.width, 8);

  // 装饰性斜线
  ctx.strokeStyle = 'rgba(212, 165, 116, 0.08)';
  ctx.lineWidth = 1;
  for (let i = -100; i < CANVAS.width + 100; i += 40) {
    ctx.beginPath();
    ctx.moveTo(i, 60);
    ctx.lineTo(i + 80, 120);
    ctx.stroke();
  }

  // 小装饰点
  ctx.fillStyle = 'rgba(212, 165, 116, 0.15)';
  const dotPositions = [
    [60, 30], [CANVAS.width - 60, 30], [120, 50],
    [CANVAS.width - 120, 50], [CANVAS.width / 2, 25],
  ];
  dotPositions.forEach(([x, y]) => {
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
  });
}

/**
 * 绘制底部信息（App名称 + 小程序码占位）
 */
function drawBottomInfo(ctx, qrCodePath) {
  const y = CANVAS.height - 180;

  // 分隔线
  ctx.strokeStyle = COLORS.goldLight;
  ctx.lineWidth = 1;
  ctx.setLineDash([8, 8]);
  ctx.beginPath();
  ctx.moveTo(100, y);
  ctx.lineTo(CANVAS.width - 100, y);
  ctx.stroke();
  ctx.setLineDash([]);

  // App名称
  ctx.fillStyle = COLORS.goldDark;
  ctx.font = FONTS.medium;
  ctx.textAlign = 'center';
  ctx.fillText('易理明灯', CANVAS.width / 2, y + 50);

  ctx.fillStyle = COLORS.textLight;
  ctx.font = FONTS.small;
  ctx.fillText('AI 命运伴侣 · 懂你的命理助手', CANVAS.width / 2, y + 82);

  // 小程序码占位（绘制一个带金色边框的方形）
  if (qrCodePath) {
    // 如果有真实图片路径
    // 简化处理：使用 drawImage
    const qrSize = 100;
    const qrX = (CANVAS.width - qrSize) / 2;
    const qrY = y + 100;
    roundRect(ctx, qrX - 6, qrY - 6, qrSize + 12, qrSize + 12, 12);
    ctx.fillStyle = COLORS.white;
    ctx.fill();
    ctx.strokeStyle = COLORS.goldLight;
    ctx.lineWidth = 2;
    ctx.stroke();
    // 这里应该加载图片，但 WeChat canvas 2d 需要 image 对象
    // 我们绘制一个QR风格占位
    drawQRPlaceholder(ctx, qrX, qrY, qrSize);
  } else {
    // 绘制QR风格占位
    const qrSize = 100;
    const qrX = (CANVAS.width - qrSize) / 2;
    const qrY = y + 100;
    roundRect(ctx, qrX - 6, qrY - 6, qrSize + 12, qrSize + 12, 12);
    ctx.fillStyle = COLORS.white;
    ctx.fill();
    ctx.strokeStyle = COLORS.goldLight;
    ctx.lineWidth = 2;
    ctx.stroke();
    drawQRPlaceholder(ctx, qrX, qrY, qrSize);
  }

  // 底部小字
  ctx.fillStyle = COLORS.textLight;
  ctx.font = FONTS.small;
  ctx.textAlign = 'center';
  ctx.fillText('长按扫码查看完整内容', CANVAS.width / 2, CANVAS.height - 30);
}

/**
 * 绘制QR码占位图案
 */
function drawQRPlaceholder(ctx, x, y, size) {
  const cellSize = size / 9;
  ctx.fillStyle = COLORS.textLight;
  // 简化QR风格：三个角的大方块
  const corners = [
    [x + cellSize, y + cellSize],
    [x + size - cellSize * 4, y + cellSize],
    [x + cellSize, y + size - cellSize * 4],
  ];
  corners.forEach(([cx, cy]) => {
    roundRect(ctx, cx, cy, cellSize * 3, cellSize * 3, 3);
    ctx.fill();
  });

  // 中心圆
  ctx.fillStyle = COLORS.gold;
  ctx.beginPath();
  ctx.arc(x + size / 2, y + size / 2, 6, 0, Math.PI * 2);
  ctx.fill();
}

// ---- 卡片绘制函数 ----

/**
 * 绘制每日运势分享卡片
 * @param {Object} data - 运势数据
 * @param {Object} canvas - canvas 节点
 * @param {string} qrCodePath - 小程序码图片路径（可选）
 * @param {Function} callback - 完成回调 (tempFilePath)
 */
function drawFortuneCard(data, canvas, qrCodePath, callback) {
  const ctx = canvas.getContext('2d');
  const W = CANVAS.width;
  const H = CANVAS.height;

  // ---- 1. 背景 ----
  const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
  bgGrad.addColorStop(0, COLORS.bgStart);
  bgGrad.addColorStop(0.3, COLORS.bgEnd);
  bgGrad.addColorStop(0.7, COLORS.cream);
  bgGrad.addColorStop(1, COLORS.bgStart);
  ctx.fillStyle = bgGrad;
  ctx.fillRect(0, 0, W, H);

  // ---- 2. 装饰 ----
  drawTopDecoration(ctx);

  // ---- 3. 标题区域 ----
  // 日期
  ctx.fillStyle = COLORS.textLight;
  ctx.font = FONTS.small;
  ctx.textAlign = 'center';
  ctx.fillText(data.date || '', W / 2, 100);

  // 干支
  if (data.ganzhi) {
    ctx.fillStyle = COLORS.gold;
    ctx.font = FONTS.small;
    ctx.textAlign = 'center';
    // 干支标签背景
    const ganzhiText = data.ganzhi;
    const ganzhiW = ctx.measureText(ganzhiText).width + 30;
    roundRect(ctx, (W - ganzhiW) / 2, 112, ganzhiW, 36, 18);
    ctx.fillStyle = 'rgba(212, 165, 116, 0.12)';
    ctx.fill();
    ctx.fillStyle = COLORS.goldDark;
    ctx.font = FONTS.small;
    ctx.fillText(ganzhiText, W / 2, 137);
  }

  // "今日运势" 标题
  ctx.fillStyle = COLORS.textDark;
  ctx.font = FONTS.title;
  ctx.textAlign = 'center';
  ctx.fillText('每日运势', W / 2, 200);

  // ---- 4. 得分环 ----
  const score = data.score || 85;
  const ringCenterX = W / 2;
  const ringCenterY = 330;
  const ringRadius = 80;
  const ringWidth = 12;

  // 外圈渐变
  const ringGrad = ctx.createConicGradient ? null : null;
  if (ctx.createConicGradient) {
    try {
      const conicGrad = ctx.createConicGradient(0, ringCenterX, ringCenterY);
      conicGrad.addColorStop(0, COLORS.gold);
      conicGrad.addColorStop(0.5, COLORS.pink);
      conicGrad.addColorStop(1, COLORS.gold);
      ctx.strokeStyle = conicGrad;
    } catch (e) {
      ctx.strokeStyle = COLORS.gold;
    }
  } else {
    ctx.strokeStyle = COLORS.gold;
  }

  // 背景环
  ctx.beginPath();
  ctx.arc(ringCenterX, ringCenterY, ringRadius, 0, Math.PI * 2);
  ctx.strokeStyle = COLORS.goldLight;
  ctx.lineWidth = ringWidth;
  ctx.stroke();

  // 分数环 - 从顶部开始顺时针
  const angle = (score / 100) * Math.PI * 2 - Math.PI / 2;
  ctx.beginPath();
  ctx.arc(ringCenterX, ringCenterY, ringRadius, -Math.PI / 2, angle);
  ctx.strokeStyle = COLORS.gold;
  ctx.lineWidth = ringWidth;
  ctx.lineCap = 'round';
  ctx.stroke();
  ctx.lineCap = 'butt';

  // 分数数字
  ctx.fillStyle = COLORS.textDark;
  ctx.font = FONTS.score;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(score), ringCenterX, ringCenterY - 10);

  // "分" 标签
  ctx.fillStyle = COLORS.textLight;
  ctx.font = FONTS.small;
  ctx.textBaseline = 'middle';
  ctx.fillText('分', ringCenterX + 55, ringCenterY + 5);

  // ---- 5. 运势等级 ----
  ctx.textBaseline = 'top';
  const levelText = getScoreLevelText(data.scoreLevel || 'good');
  ctx.fillStyle = COLORS.goldDark;
  ctx.font = FONTS.bold;
  ctx.textAlign = 'center';
  ctx.fillText(levelText, W / 2, ringCenterY + ringRadius + 30);

  // ---- 6. 三列幸运信息 ----
  const luckyY = ringCenterY + ringRadius + 85;
  const luckyItems = [
    { label: '幸运色', value: data.luckyColor || '金色', icon: '🎨' },
    { label: '幸运数字', value: String(data.luckyNumber || 7), icon: '🔢' },
    { label: '幸运方位', value: data.luckyDirection || '西方', icon: '🧭' },
  ];

  const colW = 180;
  const gap = 30;
  const totalW = colW * 3 + gap * 2;
  const startX = (W - totalW) / 2;

  luckyItems.forEach((item, i) => {
    const cx = startX + i * (colW + gap);
    const cardY = luckyY;

    // 卡片背景
    roundRect(ctx, cx, cardY, colW, 120, 16);
    ctx.fillStyle = COLORS.white;
    ctx.fill();
    // 轻微阴影
    ctx.shadowColor = COLORS.shadow;
    ctx.shadowBlur = 12;
    ctx.shadowOffsetY = 4;
    roundRect(ctx, cx, cardY, colW, 120, 16);
    ctx.fill();
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;

    // 重新绘制白色背景覆盖阴影叠加
    ctx.fillStyle = COLORS.white;
    roundRect(ctx, cx, cardY, colW, 120, 16);
    ctx.fill();

    // 文字内容
    ctx.textAlign = 'center';
    ctx.fillStyle = COLORS.gold;
    ctx.font = '30px "PingFang SC"';
    ctx.fillText(item.icon, cx + colW / 2, cardY + 15);

    ctx.fillStyle = COLORS.textDark;
    ctx.font = FONTS.medium;
    ctx.fillText(item.value, cx + colW / 2, cardY + 65);

    ctx.fillStyle = COLORS.textLight;
    ctx.font = FONTS.small;
    ctx.fillText(item.label, cx + colW / 2, cardY + 95);
  });

  // ---- 7. 今日箴言 ----
  const quoteY = luckyY + 160;
  const quote = data.advice || '心怀希望，向阳而生。';

  // 引号装饰
  ctx.fillStyle = COLORS.goldLight;
  ctx.font = '60px "PingFang SC"';
  ctx.textAlign = 'left';
  ctx.fillText('"', 100, quoteY);

  ctx.fillStyle = COLORS.text;
  ctx.font = FONTS.regular;
  ctx.textAlign = 'center';
  const wrapY = wrapText(ctx, quote, 100, quoteY + 45, W - 200, 42);
  const endQuoteY = wrapY + 10;

  ctx.fillStyle = COLORS.goldLight;
  ctx.font = '60px "PingFang SC"';
  ctx.textAlign = 'right';
  ctx.fillText('"', W - 100, endQuoteY);

  // ---- 8. 底部信息 ----
  drawBottomInfo(ctx, qrCodePath);

  // ---- 9. 导出图片 ----
  wx.canvasToTempFilePath({
    canvas,
    width: W,
    height: H,
    destWidth: W * 2,
    destHeight: H * 2,
    fileType: 'png',
    quality: 1,
    success: (res) => {
      if (callback) callback(res.tempFilePath);
    },
    fail: (err) => {
      console.error('[ShareCard] canvasToTempFilePath error:', err);
      if (callback) callback(null);
    },
  });
}

/**
 * 绘制感情合盘分享卡片
 */
function drawLoveCard(data, canvas, qrCodePath, callback) {
  const ctx = canvas.getContext('2d');
  const W = CANVAS.width;
  const H = CANVAS.height;

  // ---- 1. 背景（渐变粉） ----
  const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
  bgGrad.addColorStop(0, '#FDF8F6');
  bgGrad.addColorStop(0.3, '#FEF0EA');
  bgGrad.addColorStop(0.7, '#F5E8E4');
  bgGrad.addColorStop(1, '#FDF8F6');
  ctx.fillStyle = bgGrad;
  ctx.fillRect(0, 0, W, H);

  // ---- 2. 装饰 ----
  // 顶部渐变条（粉色）
  const grad = ctx.createLinearGradient(0, 0, W, 0);
  grad.addColorStop(0, COLORS.pink);
  grad.addColorStop(0.5, COLORS.rose);
  grad.addColorStop(1, COLORS.pink);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, W, 8);

  // 心形装饰点
  ctx.fillStyle = 'rgba(212, 160, 160, 0.12)';
  const hearts = [
    [80, 40], [W - 80, 40], [W / 2, 30],
    [150, 55], [W - 150, 55],
  ];
  hearts.forEach(([x, y]) => {
    ctx.font = '24px sans-serif';
    ctx.fillText('♥', x - 12, y + 8);
  });

  // ---- 3. 标题 ----
  ctx.fillStyle = COLORS.textDark;
  ctx.font = FONTS.title;
  ctx.textAlign = 'center';
  ctx.fillText('感情合盘', W / 2, 120);

  // ---- 4. 匹配度大分数 ----
  const score = data.score || 85;
  const ringCenterX = W / 2;
  const ringCenterY = 280;
  const ringRadius = 90;

  // 外发光
  ctx.shadowColor = 'rgba(212, 160, 160, 0.3)';
  ctx.shadowBlur = 30;
  // 背景环
  ctx.beginPath();
  ctx.arc(ringCenterX, ringCenterY, ringRadius, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(212, 160, 160, 0.2)';
  ctx.lineWidth = 14;
  ctx.stroke();

  // 分数环（粉色渐变）
  const angle = (score / 100) * Math.PI * 2 - Math.PI / 2;
  ctx.shadowColor = 'transparent';
  ctx.shadowBlur = 0;
  ctx.beginPath();
  ctx.arc(ringCenterX, ringCenterY, ringRadius, -Math.PI / 2, angle);
  const ringGrad = ctx.createLinearGradient(
    ringCenterX - ringRadius, ringCenterY,
    ringCenterX + ringRadius, ringCenterY
  );
  ringGrad.addColorStop(0, COLORS.pink);
  ringGrad.addColorStop(0.5, COLORS.rose);
  ringGrad.addColorStop(1, COLORS.pink);
  ctx.strokeStyle = ringGrad;
  ctx.lineWidth = 14;
  ctx.lineCap = 'round';
  ctx.stroke();
  ctx.lineCap = 'butt';

  // 分数
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = COLORS.textDark;
  ctx.font = FONTS.score;
  ctx.fillText(String(score), ringCenterX, ringCenterY - 8);

  ctx.fillStyle = COLORS.rose;
  ctx.font = FONTS.small;
  ctx.fillText('分', ringCenterX + 55, ringCenterY + 8);

  // 匹配度标签
  ctx.textBaseline = 'top';
  ctx.fillStyle = COLORS.rose;
  ctx.font = FONTS.regular;
  ctx.fillText(getLoveLevelText(score), ringCenterX, ringCenterY + ringRadius + 25);

  // ---- 5. 浪漫金句 ----
  const quoteY = ringCenterY + ringRadius + 80;
  const quote = data.quote || '缘起缘灭，皆是天意。\n相遇相知，便是缘分。';

  ctx.fillStyle = COLORS.rose;
  ctx.font = 'italic 28px "PingFang SC"';
  ctx.textAlign = 'center';
  const qEndY = wrapText(ctx, quote, 80, quoteY, W - 160, 40);

  // ---- 6. 分析摘要 ----
  const summaryY = qEndY + 50;
  const summary = data.summary || '你们的命盘呈现出奇妙的互补与共鸣';
  ctx.fillStyle = COLORS.text;
  ctx.font = FONTS.regular;
  ctx.textAlign = 'center';
  wrapText(ctx, summary, 80, summaryY, W - 160, 38);

  // ---- 7. 底部信息 ----
  drawBottomInfo(ctx, qrCodePath);

  // ---- 导出 ----
  wx.canvasToTempFilePath({
    canvas,
    width: W,
    height: H,
    destWidth: W * 2,
    destHeight: H * 2,
    fileType: 'png',
    quality: 1,
    success: (res) => {
      if (callback) callback(res.tempFilePath);
    },
    fail: (err) => {
      console.error('[ShareCard] love card error:', err);
      if (callback) callback(null);
    },
  });
}

/**
 * 绘制报告分享卡片
 */
function drawReportCard(data, canvas, qrCodePath, callback) {
  const ctx = canvas.getContext('2d');
  const W = CANVAS.width;
  const H = CANVAS.height;

  // ---- 1. 背景 ----
  const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
  bgGrad.addColorStop(0, COLORS.bgStart);
  bgGrad.addColorStop(0.3, COLORS.bgEnd);
  bgGrad.addColorStop(0.7, COLORS.cream);
  bgGrad.addColorStop(1, COLORS.bgStart);
  ctx.fillStyle = bgGrad;
  ctx.fillRect(0, 0, W, H);

  drawTopDecoration(ctx);

  // ---- 2. 标题 ----
  ctx.fillStyle = COLORS.textDark;
  ctx.font = FONTS.title;
  ctx.textAlign = 'center';
  ctx.fillText(data.title || '命理解读报告', W / 2, 120);

  // ---- 3. 场景标签 ----
  if (data.scenarioLabel) {
    ctx.fillStyle = COLORS.gold;
    ctx.font = FONTS.regular;
    ctx.textAlign = 'center';
    ctx.fillText(data.scenarioLabel, W / 2, 170);
  }

  // ---- 4. 分数 ----
  if (data.score) {
    const scoreY = 260;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = COLORS.gold;
    ctx.font = FONTS.score;
    ctx.fillText(String(data.score), W / 2, scoreY);
    ctx.fillStyle = COLORS.textLight;
    ctx.font = FONTS.regular;
    ctx.textBaseline = 'top';
    ctx.fillText('运势评分', W / 2, scoreY + 45);
  }

  // ---- 5. 内容摘要 ----
  const contentStartY = data.score ? 400 : 280;
  const summary = data.summary || data.fullContent || '';
  ctx.fillStyle = COLORS.text;
  ctx.font = FONTS.regular;
  ctx.textAlign = 'center';
  const endY = wrapText(ctx, summary, 80, contentStartY, W - 160, 38);

  // ---- 6. 日期 ----
  if (data.date) {
    ctx.fillStyle = COLORS.textLight;
    ctx.font = FONTS.small;
    ctx.textAlign = 'center';
    ctx.fillText(data.date, W / 2, endY + 45);
  }

  // ---- 7. 底部信息 ----
  drawBottomInfo(ctx, qrCodePath);

  // ---- 导出 ----
  wx.canvasToTempFilePath({
    canvas,
    width: W,
    height: H,
    destWidth: W * 2,
    destHeight: H * 2,
    fileType: 'png',
    quality: 1,
    success: (res) => {
      if (callback) callback(res.tempFilePath);
    },
    fail: (err) => {
      console.error('[ShareCard] report card error:', err);
      if (callback) callback(null);
    },
  });
}


/**
 * 绘制每日笺页分享卡（墨韵版：宣纸底 · 墨字 · 朱砂印章 · 灯笼）
 * @param {Object} data - { dateText, solarHint, poemLines: [], yiChips: [], }
 * @param {Object} canvas - canvas 2d 节点
 * @param {Function} callback - (tempFilePath)
 */
function drawInkCard(data, canvas, callback) {
  const ctx = canvas.getContext('2d');
  const W = 750;
  const H = 1200;

  // ── 1. 宣纸底 + 细框 ──
  ctx.fillStyle = '#F5EFE1';
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = 'rgba(58,44,30,.45)';
  ctx.lineWidth = 4;
  ctx.strokeRect(28, 28, W - 56, H - 56);

  // 顶线
  ctx.strokeStyle = 'rgba(58,44,30,.45)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(28, 54);
  ctx.lineTo(W - 28, 54);
  ctx.stroke();

  // ── 2. 日期 + 节气 ──
  ctx.fillStyle = '#6C5B45';
  ctx.font = '26px "PingFang SC", sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(data.dateText || '', W / 2, 130);

  // 菱形分隔
  ctx.save();
  ctx.translate(W / 2, 168);
  ctx.rotate(Math.PI / 4);
  ctx.fillStyle = '#A93A2C';
  ctx.fillRect(-7, -7, 14, 14);
  ctx.restore();

  ctx.fillStyle = '#9A8B71';
  ctx.font = '22px "PingFang SC", sans-serif';
  ctx.fillText(data.solarHint || '', W / 2, 205);

  // ── 3. 灯笼（JPG 宣纸底与原卡同色，直接贴图） ──
  // 异步加载灯笼图
  const lantern = canvas.createImage();
  const drawRest = () => {
    const lw = 300;
    const lh = Math.round(300 * 482 / 270);
    const lx = (W - lw) / 2;
    const ly = 260;
    ctx.drawImage(lantern, lx, ly, lw, lh);

    // ── 4. 诗签 ──
    const lines = (data.poemLines && data.poemLines.length >= 2) ? data.poemLines : ['雾散灯明处', '恰是归程时。'];
    ctx.fillStyle = '#3A2C1E';
    ctx.font = '44px "Kaiti SC", "STKaiti", serif';
    ctx.fillText(lines[0], W / 2, ly + lh + 130);
    ctx.fillText(lines[1], W / 2, ly + lh + 210);

    // 朱砂小字「·」
    ctx.fillStyle = '#A93A2C';
    ctx.font = '26px "Kaiti SC", serif';
    ctx.fillText('·', W / 2, ly + lh + 290);

    // ── 5. 两宜标签 ──
    const chips = (data.yiChips && data.yiChips.length >= 2) ? data.yiChips : ['宜 · 安顿心事', '宜 · 早眠'];
    chips.forEach((chip, i) => {
      ctx.font = '24px "PingFang SC", sans-serif';
      const tw = ctx.measureText(chip).width + 44;
      const cx = W / 2 + (i === 0 ? -tw / 2 - 14 : 14);
      const cy = ly + lh + 340;
      roundRect(ctx, cx, cy, tw, 52, 8);
      ctx.fillStyle = 'rgba(251,247,236,.9)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(58,44,30,.25)';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = '#6C5B45';
      ctx.textAlign = 'center';
      ctx.fillText(chip, cx + tw / 2, cy + 36);
    });

    // ── 6. 签文注 ──
    ctx.fillStyle = '#BBAE92';
    ctx.font = '20px "PingFang SC", sans-serif';
    ctx.fillText('签文只作心意，不作断言 · 明灯拟', W / 2, ly + lh + 460);

    // ── 7. 朱砂印章「明」 ──
    const sy = ly + lh + 530;
    roundRect(ctx, W / 2 - 50, sy, 100, 100, 10);
    ctx.fillStyle = '#A93A2C';
    ctx.fill();
    ctx.fillStyle = '#FBF6E8';
    ctx.font = '64px "Kaiti SC", serif';
    ctx.fillText('明', W / 2, sy + 74);

    // ── 8. 品牌 ──
    ctx.fillStyle = '#3A2C1E';
    ctx.font = '600 34px "PingFang SC", sans-serif';
    ctx.fillText('易理明灯', W / 2, sy + 190);
    ctx.fillStyle = '#9A8B71';
    ctx.font = '22px "PingFang SC", sans-serif';
    ctx.fillText('三秒内，为你掌灯', W / 2, sy + 235);

    // ── 9. 导出 ──
    wx.canvasToTempFilePath({
      canvas,
      width: W,
      height: H,
      destWidth: W * 2,
      destHeight: H * 2,
      fileType: 'png',
      quality: 1,
      success: (res) => {
        if (callback) callback(res.tempFilePath);
      },
      fail: (err) => {
        console.error('[ShareCard] ink card error:', err);
        if (callback) callback(null);
      },
    });
  };
  lantern.onload = drawRest;
  lantern.onerror = () => { /* 灯笼图加载失败也继续绘制其余内容 */ drawRest(); };
  lantern.src = '/assets/images/lantern.jpg';
}

// ---- 辅助函数 ----

function getScoreLevelText(level) {
  const map = {
    excellent: '大吉 · 万事如意',
    good: '上吉 · 顺风顺水',
    fair: '中平 · 稳步前行',
    poor: '待时 · 积蓄力量',
  };
  return map[level] || '上吉 · 顺风顺水';
}

function getLoveLevelText(score) {
  if (score >= 90) return '天作之合';
  if (score >= 80) return '情投意合';
  if (score >= 70) return '相得益彰';
  if (score >= 60) return '和而不同';
  return '细水长流';
}

/**
 * 保存图片到相册
 * @param {string} tempFilePath
 * @param {Function} callback
 */
function saveCardToAlbum(tempFilePath, callback) {
  if (!tempFilePath) {
    wx.showToast({ title: '生成图片失败', icon: 'none' });
    return;
  }

  // 先检查授权状态：已授权直接保存，避免每次无谓弹授权
  wx.getSetting({
    success: (res) => {
      if (res.authSetting && res.authSetting['scope.writePhotosAlbum'] === true) {
        // 已授权，直接保存
        wx.saveImageToPhotosAlbum({
          filePath: tempFilePath,
          success: () => {
            wx.showToast({ title: '已保存到相册', icon: 'success' });
            if (callback) callback(true);
          },
          fail: () => {
            wx.showToast({ title: '保存失败', icon: 'none' });
            if (callback) callback(false);
          },
        });
        return;
      }
      // 未授权或未询问过，走授权流程
      saveWithAuth(tempFilePath, callback);
    },
    fail: () => {
      // getSetting 失败时退回原逻辑
      saveWithAuth(tempFilePath, callback);
    },
  });
}

// 授权流程：saveImageToPhotosAlbum → auth fail 时 wx.authorize → 重试 → 拒绝则引导设置
function saveWithAuth(tempFilePath, callback) {
  wx.saveImageToPhotosAlbum({
    filePath: tempFilePath,
    success: () => {
      wx.showToast({ title: '已保存到相册', icon: 'success' });
      if (callback) callback(true);
    },
    fail: (err) => {
      if (err.errMsg && err.errMsg.includes('auth')) {
        // 未授权，请求相册权限
        wx.authorize({
          scope: 'scope.writePhotosAlbum',
          success: () => {
            // 重试
            wx.saveImageToPhotosAlbum({
              filePath: tempFilePath,
              success: () => {
                wx.showToast({ title: '已保存到相册', icon: 'success' });
                if (callback) callback(true);
              },
              fail: () => {
                wx.showToast({ title: '保存失败', icon: 'none' });
                if (callback) callback(false);
              },
            });
          },
          fail: () => {
            wx.showModal({
              title: '需要相册权限',
              content: '请在设置中开启相册权限，以便保存分享卡片',
              success: (res) => {
                if (res.confirm) {
                  wx.openSetting();
                }
              },
            });
            if (callback) callback(false);
          },
        });
      } else {
        wx.showToast({ title: '保存失败', icon: 'none' });
        if (callback) callback(false);
      }
    },
  });
}

/**
 * 分享卡片（调起微信分享面板）
 * @param {string} tempFilePath - 图片临时路径
 * @param {string} title - 分享标题
 */
function shareCard(tempFilePath, title) {
  if (!tempFilePath) {
    wx.showToast({ title: '生成图片失败', icon: 'none' });
    return;
  }

  // 分享到朋友圈或好友
  wx.shareImageMessage({
    imagePath: tempFilePath,
    success: () => {
      console.log('[ShareCard] shared successfully');
    },
    fail: (err) => {
      console.warn('[ShareCard] share failed:', err);
      // 降级处理：保存到相册
      saveCardToAlbum(tempFilePath);
    },
  });
}

module.exports = {
  COLORS,
  FONTS,
  CANVAS,
  drawFortuneCard,
  drawLoveCard,
  drawReportCard,
  drawInkCard,
  saveCardToAlbum,
  shareCard,
};
