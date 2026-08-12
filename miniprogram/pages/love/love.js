// 感情合盘 — Love Compatibility Page
// Flow: Input (两人生日) -> Payment (¥9.9) -> Result (匹配分析 + 分享)
const api = require('../../utils/api');
const payment = require('../../utils/payment');
const shareCard = require('../../utils/shareCard');
const security = require('../../utils/security');

// ---- 生辰数据生成 ----
function generateYears() {
  const years = [];
  for (let y = 1940; y <= 2012; y++) {
    years.push({ value: y, label: `${y}年` });
  }
  return years;
}

function generateMonths() {
  const months = [];
  for (let m = 1; m <= 12; m++) {
    months.push({ value: m, label: `${m}月` });
  }
  return months;
}

function generateDays() {
  const days = [];
  for (let d = 1; d <= 31; d++) {
    days.push({ value: d, label: `${d}日` });
  }
  return days;
}

function generateHours() {
  return [
    { value: 0, label: '子时 (23:00-00:59)' },
    { value: 1, label: '丑时 (01:00-02:59)' },
    { value: 2, label: '寅时 (03:00-04:59)' },
    { value: 3, label: '卯时 (05:00-06:59)' },
    { value: 4, label: '辰时 (07:00-08:59)' },
    { value: 5, label: '巳时 (09:00-10:59)' },
    { value: 6, label: '午时 (11:00-12:59)' },
    { value: 7, label: '未时 (13:00-14:59)' },
    { value: 8, label: '申时 (15:00-16:59)' },
    { value: 9, label: '酉时 (17:00-18:59)' },
    { value: 10, label: '戌时 (19:00-20:59)' },
    { value: 11, label: '亥时 (21:00-22:59)' },
  ];
}


Page({
  data: {
    mode: 'input',
    myBirth: '',
    taBirth: '',
    result: null,
    errorMsg: '',
    loading: false,

    years: generateYears(), months: generateMonths(), days: generateDays(), hours: generateHours(),
    defaultYearIndex: 35, defaultMonthIndex: 0, defaultDayIndex: 0, defaultHourIndex: 0,
    personA: { yearIndex: 35, monthIndex: 0, dayIndex: 0, hourIndex: 0, gender: 'female', },
    personB: {
      dayIndex: 0,
      hourIndex: 0,
      gender: 'male',
    },

    // 结果数据
    result: null,
    score: 0,
    scoreLevel: '',
    personalityAnalysis: '',
    fateAnalysis: '',
    adviceAnalysis: '',
    romanticQuote: '',

    // UI状态
    loading: false,
    showResult: false,
    generatingShare: false,

    // 动画
    scoreAnimating: false,
    displayScore: 0,
  },

  onLoad() {
    // 双人合盘已并入 hehun 页（旧路由保留，不失效）
    wx.reLaunch({ url: '/pages/hehun/hehun' });
    return;
    // 尝试恢复上次输入
    const saved = security.getSecure('love_form');
    if (saved) {
      this.setData({
        'personA.yearIndex': saved.personA?.yearIndex ?? this.data.defaultYearIndex,
        'personA.monthIndex': saved.personA?.monthIndex ?? 0,
        'personA.dayIndex': saved.personA?.dayIndex ?? 0,
        'personA.hourIndex': saved.personA?.hourIndex ?? 0,
        'personA.gender': saved.personA?.gender ?? 'female',
        'personB.yearIndex': saved.personB?.yearIndex ?? this.data.defaultYearIndex,
        'personB.monthIndex': saved.personB?.monthIndex ?? 0,
        'personB.dayIndex': saved.personB?.dayIndex ?? 0,
        'personB.hourIndex': saved.personB?.hourIndex ?? 0,
        'personB.gender': saved.personB?.gender ?? 'male',
      });
    }
  },

  // ---- 生日选择器事件 ----

  onPersonAYearChange(e) {
    this.setData({ 'personA.yearIndex': e.detail.value });
  },
  onPersonAMonthChange(e) {
    this.setData({ 'personA.monthIndex': e.detail.value });
  },
  onPersonADayChange(e) {
    this.setData({ 'personA.dayIndex': e.detail.value });
  },
  onPersonAHourChange(e) {
    this.setData({ 'personA.hourIndex': e.detail.value });
  },
  onPersonAGenderChange(e) {
    this.setData({ 'personA.gender': e.detail.value });
  },

  onPersonBYearChange(e) {
    this.setData({ 'personB.yearIndex': e.detail.value });
  },
  onPersonBMonthChange(e) {
    this.setData({ 'personB.monthIndex': e.detail.value });
  },
  onPersonBDayChange(e) {
    this.setData({ 'personB.dayIndex': e.detail.value });
  },
  onPersonBHourChange(e) {
    this.setData({ 'personB.hourIndex': e.detail.value });
  },
  onPersonBGenderChange(e) {
    this.setData({ 'personB.gender': e.detail.value });
  },

  // ---- 提交合盘 ----

  onSubmit() {
    // 保存表单
    const formData = {
      personA: { ...this.data.personA },
      personB: { ...this.data.personB },
    };
    security.setSecure('love_form', formData);

    // 开始支付流程
    this.startPayment();
  },

  async startPayment() {
    this.setData({ loading: true, errorMsg: '' });

    try {
      const payResult = await payment.purchase('love_compatibility');

      if (payResult.success) {
        // 支付成功（或演示模式），开始合盘分析
        await this.doAnalysis();
      } else {
        wx.showToast({ title: '支付已取消', icon: 'none' });
      }
    } catch (e) {
      console.error('[Love] payment error:', e);
      wx.showToast({ title: '支付异常，请重试', icon: 'none' });
    } finally {
      this.setData({ loading: false });
    }
  },

  async doAnalysis() {
    this.setData({ loading: true });

    const pA = this.data.personA;
    const pB = this.data.personB;

    const birthData = {
      birthYear1: this.data.years[pA.yearIndex].value,
      birthMonth1: pA.monthIndex + 1,
      birthDay1: pA.dayIndex + 1,
      birthHour1: pA.hourIndex,
      gender1: pA.gender === 'male' ? 1 : 0,
      birthYear2: this.data.years[pB.yearIndex].value,
      birthMonth2: pB.monthIndex + 1,
      birthDay2: pB.dayIndex + 1,
      birthHour2: pB.hourIndex,
      gender2: pB.gender === 'male' ? 1 : 0,
    };

    try {
      const res = await api.getLoveCompatibility(birthData, true);
      // 真实接口：返回异常/缺分时明确报错，绝不回落伪随机结果
      if (res && (res.score !== undefined || res.match_score !== undefined)) {
        this.showResults(res);
      } else {
        this._showError('接口返回数据异常，请稍后重试');
      }
    } catch (e) {
      console.warn('[Love] 合盘接口失败:', e && e.message);
      this._showError('合盘服务暂时不可用，请检查网络后重试');
    } finally {
      this.setData({ loading: false });
    }
  },

  /* 真实接口失败：明确错误提示（不造假数据） */
  _showError(msg) {
    this.setData({
      errorMsg: msg || '合盘服务暂时不可用，请稍后重试',
      loading: false,
      result: null,
    });
  },

  showResults(data) {
    const score = data.score !== undefined ? data.score : data.match_score;
    this.setData({
      mode: 'result',
      showResult: true,
      result: data,
      score,
      scoreLevel: data.levelLabel || '',
      personalityAnalysis: data.personality || data.personality_analysis || '',
      fateAnalysis: data.fate || data.fate_analysis || '',
      adviceAnalysis: data.advice || data.advice_analysis || '',
      romanticQuote: data.quote || data.love_quote || '',
      scoreAnimating: true,
      displayScore: 0,
    });

    // 分数数字动画：从0递增到实际分数
    this.animateScore(score);
  },

  animateScore(targetScore) {
    const duration = 1500; // 1.5s
    const stepTime = 30;
    const steps = duration / stepTime;
    let currentStep = 0;

    const timer = setInterval(() => {
      currentStep++;
      const progress = Math.min(currentStep / steps, 1);
      // easeOutCubic
      const eased = 1 - Math.pow(1 - progress, 3);
      const currentScore = Math.round(eased * targetScore);

      this.setData({ displayScore: currentScore });

      if (progress >= 1) {
        clearInterval(timer);
        this.setData({
          displayScore: targetScore,
          scoreAnimating: false,
        });
      }
    }, stepTime);
  },

  // ---- 分享卡片 ----

  generateShareCard() {
    if (this.data.generatingShare) return;
    this.setData({ generatingShare: true });

    const query = wx.createSelectorQuery();
    query.select('#shareCanvas')
      .fields({ node: true, size: true })
      .exec((res) => {
        if (!res || !res[0] || !res[0].node) {
          wx.showToast({ title: '生成失败，请重试', icon: 'none' });
          this.setData({ generatingShare: false });
          return;
        }

        const canvas = res[0].node;
        const data = {
          score: this.data.score,
          quote: this.data.romanticQuote,
          summary: `${this.data.scoreLevel} · 性格${this.data.personalityAnalysis.slice(0, 40)}…`,
        };

        shareCard.drawLoveCard(data, canvas, null, (tempFilePath) => {
          this.setData({ generatingShare: false });
          if (tempFilePath) {
            shareCard.saveCardToAlbum(tempFilePath);
          } else {
            wx.showToast({ title: '生成图片失败', icon: 'none' });
          }
        });
      });
  },

  // ---- 重新合盘 ----

  onReset() {
    this.setData({
      mode: 'input',
      showResult: false,
      result: null,
      errorMsg: '',
      score: 0,
      displayScore: 0,
      scoreAnimating: false,
      personalityAnalysis: '',
      fateAnalysis: '',
      adviceAnalysis: '',
      romanticQuote: '',
    });
  },

  /* ---- v6 新增: 简单输入模式 ---- */
  onMyInput(e) { this.setData({ myBirth: e.detail.value }); },
  onTaInput(e) { this.setData({ taBirth: e.detail.value }); },
  reset() { this.setData({ mode: 'input', result: null, errorMsg: '', myBirth: '', taBirth: '' }); },

  async onSubmit() {
    const { myBirth, taBirth } = this.data;
    if (!myBirth || !taBirth) {
      wx.showToast({ title: '请填写双方出生日期', icon: 'none' });
      return;
    }
    this.setData({ loading: true, errorMsg: '' });
    try {
      const res = await api.getLoveCompatibility({ myBirth, taBirth }, false);
      // 真实接口：只认接口返回，缺失分数视为异常（不回落伪结果）
      const score = res.match_score !== undefined ? res.match_score : res.score;
      if (score === undefined || score === null) {
        this._showError('接口返回数据异常，请稍后重试');
        return;
      }
      this.setData({
        mode: 'result',
        result: {
          score,
          summary: res.summary || res.analysis || '你们之间有一种特殊的缘分。'
        }
      });
    } catch (e) {
      console.warn('[Love] 合盘接口失败:', e && e.message);
      this._showError('合盘服务暂时不可用，请检查网络后重试');
    } finally {
      this.setData({ loading: false });
    }
  },

  // ---- 分享 ----

  onShareAppMessage() {
    if (this.data.mode === 'result') {
      const level = this.data.scoreLevel;
      return {
        title: `❤️ 感情合盘 ${this.data.score}分 — ${level || '缘分解析'} · 易理明灯`,
        path: '/pages/love/love',
      };
    }
    return {
      title: '❤️ 测测你和TA的缘分指数 · 易理明灯',
      path: '/pages/love/love',
    };
  },
});
