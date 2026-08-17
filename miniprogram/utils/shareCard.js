// 易理明灯 — 分享卡片 Canvas 绘制系统
// 视觉语言：墨韵（项目定稿）
//   色板：宣纸底 #F5EFE1 · 墨 #3A2C1E · 朱砂 #A93A2C · 金 #B08A4F
//         淡墨 #6C5B45 · 浅米 #9A8B71 · 纸白 #FBF7EC · 黛青 #3E5C4E（双人第二印色）
//   禁用：粉金紫（#E8C4B8/#D4A574/#C8B8D4 等）与灰色系
// 字体：中文宋体（Songti SC/SimSun）+ 楷体（Kaiti SC/STKaiti，标题与正文），数字用衬线
// 元素：朱砂印章（圆角方印）、墨线细框、宣纸底、留白呼吸感、落款小字
// 层级节奏（全卡统一）：
//   标题  楷体 600 34-44px 墨色   ~y=120（缘笺双印夹题）
//   副题  宋体 22-24px 淡墨/浅米  ~y=190
//   正文  楷体 30-36px 墨色       缘语/答句
//   辅助  宋体 20-26px 淡墨       解析、标签
//   数字  衬线 bold 96-100px 墨色  契合分/运势分
//   落款  品牌（墨 600 30-34px）+ 口号（浅米 22px）+ 底部小字（极淡 20px, H-40）
//   印章  朱砂/黛青底 + 纸白楷体字，圆角方印
// 画布：750 宽（朋友圈最优）；drawChatCard 高度按内容自适应
//
// 使用方式：
//   1. 在 wxml 中添加 <canvas type="2d" id="shareCanvas" style="width:750px;height:1200px;position:fixed;left:-9999px;"></canvas>
//   2. 调用 drawLoveCard / drawInkCard / drawYuanCard / drawChatCard

// ---- 墨韵设计常量 ----
const COLORS = {
  paper: '#F5EFE1',        // 宣纸底
  paperBright: '#FBF7EC',  // 纸白（笺块/标签底）
  ink: '#3A2C1E',          // 墨
  cinnabar: '#A93A2C',     // 朱砂
  gold: '#B08A4F',         // 金
  muted: '#6C5B45',        // 淡墨
  light: '#9A8B71',        // 浅米
  faint: '#BBAE92',        // 极淡墨
  daiqing: '#3E5C4E',      // 黛青（双人第二印色，与 drawYuanCard 一致）
  sealText: '#FBF6E8',     // 印章文字（纸白微暖）
};

// 字体族（小程序端真实字体；iOS 有 Songti/Kaiti SC，Android 落系统衬线/黑体）
const FONT_KAI = '"Kaiti SC", "STKaiti", "KaiTi", serif';
const FONT_SONG = '"Songti SC", "SimSun", serif';
const FONT_HAN = '"PingFang SC", "Microsoft YaHei", sans-serif';
const FONT_NUM = '"Songti SC", "Times New Roman", serif'; // 衬线数字

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
 * 绘制多行文本自动换行（返回末行基线 y）
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
 * 估算文本行数（与 wrapText 同口径；高度预算与绘制共用）
 */
function countLines(ctx, text, maxWidth) {
  const s = String(text || '');
  if (!s) return 0;
  const chars = s.split('');
  let line = '';
  let n = 0;
  for (const ch of chars) {
    if (ch === '\n') { line = ''; n++; continue; }
    const test = line + ch;
    if (ctx.measureText(test).width > maxWidth && line !== '') { line = ch; n++; }
    else line = test;
  }
  if (line) n++;
  return n;
}

// ---- 墨韵骨架（全卡共用，保证层级与边框节奏一致） ----

/**
 * 宣纸底 + 4px 墨线细框（28 内距）+ 2px 顶线（54）
 */
function inkPaper(ctx, W, H) {
  ctx.fillStyle = COLORS.paper;
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = 'rgba(58,44,30,.45)';
  ctx.lineWidth = 4;
  ctx.strokeRect(28, 28, W - 56, H - 56);
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(28, 54);
  ctx.lineTo(W - 28, 54);
  ctx.stroke();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
}

/**
 * 菱形分隔（旋转 45° 方块）
 */
function inkDiamond(ctx, x, y, s, color) {
  const c = color || COLORS.cinnabar;
  const half = s / 2;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(Math.PI / 4);
  ctx.fillStyle = c;
  ctx.fillRect(-half, -half, s, s);
  ctx.restore();
}

/**
 * 朱砂/黛青圆角方印（单字默认字高 = size*0.64；两字竖排 = size*0.42）
 */
function inkSeal(ctx, cx, cy, size, chars, opts) {
  const o = opts || {};
  const bg = o.bg || COLORS.cinnabar;
  const r = (o.r === undefined) ? Math.max(6, Math.round(size * 0.1)) : o.r;
  roundRect(ctx, cx - size / 2, cy - size / 2, size, size, r);
  ctx.fillStyle = bg;
  ctx.fill();
  ctx.fillStyle = o.textColor || COLORS.sealText;
  ctx.textAlign = 'center';
  if (chars.length > 1 && !o.charSize) {
    const cs = Math.round(size * 0.42);
    ctx.font = cs + 'px ' + FONT_KAI;
    ctx.fillText(chars[0], cx, cy - cs * 0.18);
    ctx.fillText(chars[1], cx, cy + cs * 0.9);
  } else {
    const cs = o.charSize || Math.round(size * 0.64);
    ctx.font = cs + 'px ' + FONT_KAI;
    ctx.fillText(chars, cx, cy + cs * 0.35);
  }
}

/**
 * 品牌落款（左下）：易理明灯 + 口号
 */
function drawBrandLeft(ctx, y1, y2) {
  ctx.textAlign = 'left';
  ctx.fillStyle = COLORS.ink;
  ctx.font = '600 30px ' + FONT_HAN;
  ctx.fillText('易理明灯', 70, y1);
  ctx.fillStyle = COLORS.light;
  ctx.font = '22px ' + FONT_SONG;
  ctx.fillText('三秒内，为你掌灯', 70, y2);
}

/**
 * 底部小字（落款定式；opts.align='right' 时置于右下角）
 */
function inkBottomNote(ctx, W, H, opts) {
  ctx.fillStyle = COLORS.faint;
  ctx.font = '20px ' + FONT_SONG;
  if (opts && opts.align === 'right') {
    ctx.textAlign = 'right';
    ctx.fillText('签文只作心意，不作断言', W - 88, H - 40);
  } else {
    ctx.textAlign = 'center';
    ctx.fillText('签文只作心意，不作断言', W / 2, H - 40);
  }
}

/**
 * 绘制QR码占位图案（墨韵：淡墨角块 + 金心）
 */
function drawQRPlaceholder(ctx, x, y, size) {
  const cellSize = size / 9;
  ctx.fillStyle = COLORS.faint;
  const corners = [
    [x + cellSize, y + cellSize],
    [x + size - cellSize * 4, y + cellSize],
    [x + cellSize, y + size - cellSize * 4],
  ];
  corners.forEach(([cx, cy]) => {
    roundRect(ctx, cx, cy, cellSize * 3, cellSize * 3, 3);
    ctx.fill();
  });
  ctx.fillStyle = COLORS.gold;
  ctx.beginPath();
  ctx.arc(x + size / 2, y + size / 2, 6, 0, Math.PI * 2);
  ctx.fill();
}

// ---- 卡片绘制函数 ----

/**
 * 绘制双人合盘缘笺分享卡（墨韵：宣纸底 · 双印夹题 · 双色连印线 · 衬线契合分 · 合盘略解）
 * 双人辨识：朱砂「缘」印 + 黛青「合」印对称夹题（两人成对），朱砂/黛青双色连印线（红线牵合）
 * @param {Object} data - { score, quote, summary }（love 页传入，无时辰/姓名等敏感信息）
 * @param {Object} canvas - canvas 2d 节点
 * @param {string} qrCodePath - 保留参数（兼容调用点；当前统一绘制占位）
 * @param {Function} callback - (tempFilePath)
 */
function drawLoveCard(data, canvas, qrCodePath, callback) {
  const ctx = canvas.getContext('2d');
  const W = CANVAS.width;
  const H = CANVAS.height;
  const INK = COLORS.ink, CINNABAR = COLORS.cinnabar;
  const MUTED = COLORS.muted, LIGHT = COLORS.light, DAIQING = COLORS.daiqing;

  // 1. 宣纸底 + 墨框 + 顶线
  inkPaper(ctx, W, H);

  // 2. 标题区：双印夹题 + 双色连印线
  const title = '双人合盘 · 缘定三生';
  const sealS = 48;
  const sealCX = W / 2 - 214, sealCY = 138;
  const sealCX2 = W / 2 + 214;
  inkSeal(ctx, sealCX, sealCY, sealS, '缘', { bg: CINNABAR });
  inkSeal(ctx, sealCX2, sealCY, sealS, '合', { bg: DAIQING });
  // 连印线：缘印右缘 → 合印左缘，左朱砂右黛青（红线牵合 · 双人成线）
  const lx = sealCX + sealS / 2 + 18, rx = sealCX2 - sealS / 2 - 18;
  ctx.lineWidth = 2;
  ctx.strokeStyle = CINNABAR;
  ctx.beginPath(); ctx.moveTo(lx, sealCY); ctx.lineTo((lx + rx) / 2, sealCY); ctx.stroke();
  ctx.strokeStyle = DAIQING;
  ctx.beginPath(); ctx.moveTo((lx + rx) / 2, sealCY); ctx.lineTo(rx, sealCY); ctx.stroke();

  ctx.fillStyle = INK;
  ctx.font = '600 36px ' + FONT_KAI;
  ctx.fillText(title, W / 2, 150);

  // 3. 副题
  ctx.fillStyle = MUTED;
  ctx.font = '24px ' + FONT_SONG;
  ctx.fillText('两心相照 · 星命同辉', W / 2, 202);

  // 4. 契合分（衬线大字）+ 等级（朱砂楷体）
  const score = Math.round(data.score || 0);
  ctx.fillStyle = INK;
  ctx.font = 'bold 100px ' + FONT_NUM;
  ctx.fillText(String(score), W / 2 - 6, 348);          // 微左移，与「分」整体光学居中
  ctx.fillStyle = CINNABAR;
  ctx.font = '26px ' + FONT_SONG;
  ctx.fillText('分', W / 2 + 60, 338);
  ctx.fillStyle = CINNABAR;
  ctx.font = '600 32px ' + FONT_KAI;
  ctx.fillText(getLoveLevelText(score), W / 2, 416);

  // 5. 菱形分隔
  inkDiamond(ctx, W / 2, 472, 14);

  // 6. 缘语（楷体墨色，可晒体；按 \n 分行，每行自动换行）
  const quote = (data.quote && String(data.quote)) || '缘起缘灭，皆是天意。\n相遇相知，便是缘分。';
  ctx.fillStyle = INK;
  ctx.font = '36px ' + FONT_KAI;
  let qy = 548;
  quote.split('\n').slice(0, 4).forEach((ln) => {
    if (ln) qy = wrapText(ctx, ln, 90, qy, W - 180, 50) + 14;
  });

  // 7. 合盘略解（纸白笺块：墨细框 + 朱砂小标签 + 楷体淡墨，与缘语同字系更协调）
  const summary = String(data.summary || '你们的命盘呈现出奇妙的互补与共鸣');
  // 剥离 love 页拼入的等级前缀（"上吉 · 性格…" → "性格…"），等级已由 4 步单独呈现
  let body = summary;
  const m = summary.match(/^\s*(.{2,10}?)\s*[·•]\s*(.+)$/);
  if (m && m[2]) body = m[2];
  ctx.font = '26px ' + FONT_KAI;
  const aLines = countLines(ctx, body, W - 260);
  const aTop = qy + 30;
  const aH = 66 + aLines * 40 + 30;
  roundRect(ctx, 90, aTop, W - 180, aH, 10);
  ctx.fillStyle = COLORS.paperBright;
  ctx.fill();
  ctx.strokeStyle = 'rgba(58,44,30,.18)';
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = CINNABAR;
  ctx.font = '20px ' + FONT_KAI;
  ctx.fillText('合盘略解', 114, aTop + 34);
  ctx.fillStyle = MUTED;
  ctx.font = '26px ' + FONT_KAI;
  wrapText(ctx, body, 114, aTop + 66, W - 260, 40);

  // 8. 品牌落款（左下，上移衔接内容）+ 二维码（右下）+ 签名（右下角）
  drawBrandLeft(ctx, 925, 970);
  drawQRPlaceholder(ctx, 560, 1020, 100);
  inkBottomNote(ctx, W, H, { align: 'right' });

  // 9. 导出 2x PNG
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
 * 绘制每日笺页分享卡（墨韵版：宣纸底 · 墨字 · 朱砂印章 · 灯笼）
 * @param {Object} data - { dateText, solarHint, poemLines: [], yiChips: [], }
 * @param {Object} canvas - canvas 2d 节点
 * @param {Function} callback - (tempFilePath)
 */
function drawInkCard(data, canvas, callback) {
  const ctx = canvas.getContext('2d');
  const W = 750;
  const H = 1200;

  // ── 1. 宣纸底 + 细框 + 顶线 ──
  inkPaper(ctx, W, H);

  // ── 2. 日期 + 节气 ──
  ctx.fillStyle = '#6C5B45';
  ctx.font = '26px "PingFang SC", sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(data.dateText || '', W / 2, 130);

  // 菱形分隔
  inkDiamond(ctx, W / 2, 168, 14);

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
    inkSeal(ctx, W / 2, sy + 50, 100, '明');

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

/**
 * 绘制双人缘笺分享卡（墨韵：宣纸底 · 墨字 · 双色印章 · 双人生辰）
 * @param {Object} data - { birthA, birthB, score, levelLabel, quoteParts, sealChar }（服务端已脱敏）
 * @param {Object} canvas - canvas 2d 节点
 * @param {Function} callback - (tempFilePath)
 * 隐私：data 全部为服务端脱敏数据（无时辰/出生地/姓名）
 */
function drawYuanCard(data, canvas, callback) {
  const ctx = canvas.getContext('2d');
  const W = 750, H = 1200;
  const INK = '#3A2C1E', CINNABAR = '#A93A2C', DAIQING = '#3E5C4E';
  const MUTED = '#6C5B45', LIGHT = '#9A8B71', FAINT = '#BBAE92';

  // 1. 宣纸底 + 细框 + 顶线（墨韵骨架，全卡共用）
  inkPaper(ctx, W, H);

  // 2. 标题
  ctx.fillStyle = INK;
  ctx.font = '600 34px "PingFang SC", sans-serif';
  ctx.fillText('双人合盘 · 缘分契合', W / 2, 120);

  // 3. 双人生辰（脱敏）竖排两行
  ctx.fillStyle = MUTED;
  ctx.font = '28px "PingFang SC", sans-serif';
  ctx.fillText(data.birthA || '', W / 2, 185);
  ctx.fillText(data.birthB || '', W / 2, 235);

  // 4. 契合分大字 + 等级
  ctx.fillStyle = INK;
  ctx.font = 'bold 96px "PingFang SC", sans-serif';
  ctx.fillText(String(data.score || 0), W / 2, 400);
  ctx.fillStyle = CINNABAR;
  ctx.font = '26px "PingFang SC", sans-serif';
  ctx.fillText('分', W / 2 + 58, 392);
  ctx.fillStyle = CINNABAR;
  ctx.font = '600 32px "PingFang SC", sans-serif';
  ctx.fillText(data.levelLabel || '', W / 2, 452);

  // 5. 菱形分隔
  inkDiamond(ctx, W / 2, 502, 14);

  // 6. 缘语两行（楷体，可晒体；主句一行 + 后缀/悬念一行，自动换行）
  const qp = data.quoteParts || {};
  const line1 = qp.main || '';
  const line2 = [qp.suffix, qp.cliffhanger].filter(Boolean).join('，');
  ctx.fillStyle = INK;
  ctx.font = '40px "Kaiti SC", "STKaiti", serif';
  let wrapY = wrapText(ctx, line1, 80, 580, W - 160, 52);
  if (line2) wrapY = wrapText(ctx, line2, 80, wrapY + 22, W - 160, 52);

  // 7. 双色印章（右下）：朱砂「缘」印 + 黛青生肖印（如「午马」，视觉隐喻双人）
  const sy = 880, ss = 96;
  inkSeal(ctx, 470 + ss / 2, sy + ss / 2, ss, '缘', { charSize: 56 });
  inkSeal(ctx, 590 + ss / 2, sy + ss / 2, ss, data.sealChar || '缘', { bg: DAIQING, charSize: 44 });

  // 8. 品牌落款（左下）+ 小程序码占位
  drawBrandLeft(ctx, 940, 985);
  drawQRPlaceholder(ctx, 560, 1020, 100);

  // 9. 底部小字
  inkBottomNote(ctx, W, H);

  // 10. 导出 2x PNG（同 drawInkCard）
  wx.canvasToTempFilePath({
    canvas, width: W, height: H, destWidth: W * 2, destHeight: H * 2,
    fileType: 'png', quality: 1,
    success: (res) => { if (callback) callback(res.tempFilePath); },
    fail: (err) => { console.error('[ShareCard] yuan card error:', err); if (callback) callback(null); },
  });
}

/**
 * 绘制对话分享卡「夜话拾笺」（墨韵版：笺印 · 书法标题 · 墨线分隔 · 问答成组 · 落款印章 · 品牌二维码）
 * 内容：选中的 2-6 条对话（用户问 + 明灯答成组排版）+ 品牌落款 +（二维码：扫码查看这段对话）
 * @param {Object} data - { pairs: [{u, tag, content}], dateText }
 * @param {Object} canvas - canvas 2d 节点（宽 750，高度由本函数按内容设定）
 * @param {Function} callback - (tempFilePath)
 * @param {string} qrPath - 二维码本地临时路径（可选；为空/加载失败则不画二维码，不阻塞出图）
 */
function drawChatCard(data, canvas, callback, qrPath) {
  const ctx = canvas.getContext('2d');
  const W = 750;
  const INK = COLORS.ink, CINNABAR = COLORS.cinnabar;
  const MUTED = COLORS.muted, LIGHT = COLORS.light, FAINT = COLORS.faint;
  const CARD = COLORS.paperBright;
  const pairs = ((data && data.pairs) || []).slice(0, 6);
  const dateText = (data && data.dateText) || '';

  /* ── 高度预算：笺头 + 问答组 + 落款区（与绘制共用同一换行口径） ── */
  ctx.font = '30px ' + FONT_KAI;
  const headerH = 278;  // 笺印+标题+日期+分隔线 → 正文顶
  // 落款区：菱形+印章+品牌+口号 → 底部小字前；带二维码时增加 二维码笺块+说明小字
  // （修复原 326 偏紧：长内容时口号会顶到底部小字，预算增至 420）
  const footerH = qrPath ? 720 : 420;
  let contentH = 0;
  pairs.forEach((p) => {
    let h = 30 + Math.max(countLines(ctx, p.u, W - 230) - 1, 0) * 42 + 26;   // 问行区
    const aL = countLines(ctx, p.content, W - 240);
    h += 88 + (aL - 1) * 46 + 34;                                            // 答块
    h += 84;                                                                 // 组间距
    contentH += h;
  });
  const H = Math.max(1200, headerH + contentH + footerH);
  canvas.width = W;
  canvas.height = H;

  /* ── 1. 宣纸底 + 细框 + 顶线 ── */
  inkPaper(ctx, W, H);

  /* ── 2. 笺头：朱砂「笺」印 + 书法标题 + 日期 ── */
  inkSeal(ctx, W / 2, 98, 48, '笺');
  ctx.fillStyle = INK;
  ctx.font = '600 44px ' + FONT_KAI;
  ctx.fillText('夜话拾笺', W / 2, 166);
  ctx.fillStyle = MUTED;
  ctx.font = '22px ' + FONT_SONG;
  ctx.fillText(dateText, W / 2, 206);

  /* ── 3. 分隔线：墨线 + 中央朱砂菱形 ── */
  const dcy = 240, dHalf = 13;
  ctx.strokeStyle = 'rgba(58,44,30,.28)';
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(88, dcy); ctx.lineTo(W / 2 - dHalf, dcy); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(W / 2 + dHalf, dcy); ctx.lineTo(W - 88, dcy); ctx.stroke();
  inkDiamond(ctx, W / 2, dcy, 8);

  /* ── 4. 问答组（用户问 + 明灯答成组排版） ── */
  let y = headerH;
  pairs.forEach((p) => {
    /* 问：朱砂「问」小印 + 楷体墨字（左对齐） */
    roundRect(ctx, 92, y, 30, 30, 4);
    ctx.fillStyle = CINNABAR;
    ctx.fill();
    ctx.fillStyle = '#FBF6E8';
    ctx.font = '20px ' + FONT_KAI;
    ctx.textAlign = 'center';
    ctx.fillText('问', 107, y + 22);
    ctx.textAlign = 'left';
    ctx.fillStyle = INK;
    ctx.font = '30px ' + FONT_KAI;
    if (p.u) {
      y = wrapText(ctx, String(p.u), 136, y + 24, W - 230, 42); // 返回末行基线
      y += 26;                                                   // 问行末 → 答块顶
    } else {
      y += 56;
    }
    /* 答：纸白块 + 朱砂左条 + 朱砂描边小标签 + 楷体正文 */
    const aL = countLines(ctx, p.content, W - 240);
    const aH = 88 + (aL - 1) * 46 + 34;
    roundRect(ctx, 90, y, W - 180, aH, 10);
    ctx.fillStyle = CARD;
    ctx.fill();
    ctx.strokeStyle = 'rgba(58,44,30,.16)';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = CINNABAR;
    ctx.fillRect(96, y + 28, 5, aH - 52);        // 朱砂左条
    const tag = String(p.tag || '明灯 · 夜话');
    ctx.font = '20px ' + FONT_KAI;
    const tagW = ctx.measureText(tag).width + 28;
    roundRect(ctx, 124, y + 32, tagW, 30, 4);    // 朱砂描边小标签（章式）
    ctx.strokeStyle = CINNABAR;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = CINNABAR;
    ctx.textAlign = 'center';
    ctx.fillText(tag, 124 + tagW / 2, y + 52);
    ctx.textAlign = 'left';
    ctx.fillStyle = INK;
    ctx.font = '30px ' + FONT_KAI;
    wrapText(ctx, String(p.content || ''), 124, y + 88, W - 240, 46);
    y += aH + 84;                                // 下一组顶
  });

  /* ── 5. 落款：菱形分隔 + 朱砂「明灯」印 + 品牌 +（二维码笺块 + 说明小字） ── */
  inkDiamond(ctx, W / 2, y + 44, 14);
  const sy = y + 100;
  inkSeal(ctx, W / 2, sy + 55, 110, '明灯');
  ctx.fillStyle = INK;
  ctx.font = '600 34px ' + FONT_HAN;
  ctx.fillText('易理明灯', W / 2, sy + 214);
  ctx.fillStyle = LIGHT;
  ctx.font = '22px ' + FONT_SONG;
  ctx.fillText('三秒内，为你掌灯', W / 2, sy + 258);
  // 二维码区（纸白笺块 + 淡墨框 + 二维码 + 墨韵小字），仅 qrPath 存在时绘制
  const qrY = y + 410;
  const drawQrBlock = () => {
    roundRect(ctx, W / 2 - 105, qrY, 210, 210, 12);
    ctx.fillStyle = CARD;
    ctx.fill();
    ctx.strokeStyle = 'rgba(58,44,30,.18)';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.textAlign = 'center';
    ctx.fillStyle = FAINT;
    ctx.font = '22px ' + FONT_SONG;
    ctx.fillText('扫码查看这段对话 · 与明灯继续聊', W / 2, qrY + 244);
  };
  inkBottomNote(ctx, W, H);

  /* ── 6. 导出 2x PNG（有二维码时先异步加载二维码图，加载失败跳过二维码不阻塞出图） ── */
  const exportCard = () => {
    wx.canvasToTempFilePath({
      canvas, width: W, height: H, destWidth: W * 2, destHeight: H * 2,
      fileType: 'png', quality: 1,
      success: (res) => { if (callback) callback(res.tempFilePath); },
      fail: (err) => { console.error('[ShareCard] chat card error:', err); if (callback) callback(null); },
    });
  };

  if (qrPath) {
    const qrImg = canvas.createImage();
    qrImg.onload = () => {
      try {
        drawQrBlock();
        ctx.drawImage(qrImg, W / 2 - 85, qrY + 20, 170, 170);
      } catch (e) {
        console.error('[ShareCard] chat card qr draw error:', e);
      }
      exportCard();
    };
    qrImg.onerror = () => {
      console.warn('[ShareCard] 二维码加载失败，跳过二维码区域');
      exportCard();
    };
    qrImg.src = qrPath;
  } else {
    exportCard();
  }
}

// ---- 辅助函数 ----

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
  CANVAS,
  drawLoveCard,
  drawInkCard,
  drawYuanCard,
  drawChatCard,
  saveCardToAlbum,
  shareCard,
};
