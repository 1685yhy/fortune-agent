// 易理明灯 — 今日运势（仪式感首页）
const canvasHelper = require('../../utils/canvas-helper');
const api = require('../../utils/api');
const { MESSAGES } = require('../../utils/messages');

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
    skeletonLoading: true,
    hourSkeletonLoading: true,
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
    hourlyLoaded: false,
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

    // Error/empty state
    showError: false,
    errorType: '',
    errorSubtype: '',
  },

  onReady() {
    this._loadData();
    this._loadHourlyFortune();
  },

  // ---- 数据加载 ----
  async _loadData() {
    try {
      const data = await api.getTodayFortune();
      this.setData({ skeletonLoading: false });
      this._processData(data);
      this._startEntrance();
    } catch (e) {
      this.setData({
        skeletonLoading: false,
        showError: true,
        errorType: e.name === 'NetworkError' ? 'network' : 'server',
        errorSubtype: '',
      });
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
      skeletonLoading: false, hourSkeletonLoading: false,
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

  // ---- 时辰运势 API 增强（Sub-project B）----
  async _loadHourlyFortune() {
    try {
      const app = getApp();
      const userId = app.globalData?.userInfo?.id || '';
      const data = await api.getHourlyFortune(userId);
      if (data && data.hours && data.hours.length === 12) {
        // Merge API data with existing hours, preserving local mark info
        const now = new Date();
        const currentHour = now.getHours();
        const enhanced = this.data.hours.map((h, i) => {
          const apiHour = data.hours[i] || {};
          const hourStart = i * 2 - 1;
          const isActive = (currentHour >= (hourStart < 0 ? hourStart + 24 : hourStart) &&
                            currentHour < (hourStart + 2 < 0 ? hourStart + 26 : hourStart + 2));
          return {
            ...h,
            ganzhi: apiHour.ganzhi || h.ganzhi || '',
            score: apiHour.score || 0,
            advice: apiHour.advice || h.advice || '',
            level: apiHour.level || h.level || 'fair',
            mark: apiHour.mark || h.mark || '',
            active: isActive,
          };
        });

        // Determine best/worst hours
        const scored = enhanced.filter(h => h.score > 0);
        let bestHourIndex = -1, worstHourIndex = -1;
        if (scored.length > 0) {
          const best = scored.reduce((a, b) => (a.score || 0) >= (b.score || 0) ? a : b);
          const worst = scored.reduce((a, b) => (a.score || 0) <= (b.score || 0) ? a : b);
          bestHourIndex = enhanced.findIndex(h => h.name === best.name);
          worstHourIndex = enhanced.findIndex(h => h.name === worst.name);
        }

        enhanced.forEach((h, i) => {
          h.isBest = i === bestHourIndex;
          h.isWorst = i === worstHourIndex;
        });

        this.setData({ hours: enhanced, hourlyLoaded: true, hourSkeletonLoading: false });
      }
    } catch (e) {
      // Keep existing locally-calculated hours
      console.warn('[Today] Hourly fortune API unavailable, using local calculation');
      this.setData({ hourSkeletonLoading: false });
    }
  },

  // ---- 入场动画编排 ----
  async _startEntrance() {
    // Check if user prefers reduced motion (safely fall back if API unavailable)
    let reduceMotion = false;
    try {
      const info = wx.getAppBaseInfo ? wx.getAppBaseInfo() : wx.getSystemInfoSync();
      reduceMotion = !!info.reduceMotion;
    } catch (e) {
      reduceMotion = false;
    }
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
    // Generate advice text based on mark level
    const adviceMap = {
      'good': '此时辰与日主相生，气场和谐，宜开展重要事务，顺势而为可获天时之利。',
      'fair': '此时辰气场平和，无大吉亦无大凶，日常事务可正常进行，宜静养收敛。',
      'poor': '此时辰与日主相克，气场略有不顺，宜静不宜动，避免重要决策。',
    };
    hour.advice = adviceMap[hour.level] || '此时辰平平无奇，按计划行事即可。';
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

  // ---- Retry after error ----
  onErrorRetry() {
    this.setData({ showError: false, skeletonLoading: true });
    this._loadData();
  },
});

const DIZHI = ['子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥'];
