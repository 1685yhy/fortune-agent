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
