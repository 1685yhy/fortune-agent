// 易理明灯 — 奇门遁甲
const api = require('../../utils/api');
const { MESSAGES } = require('../../utils/messages');

// ---- Demo / Fallback Data ----
const FALLBACK_PALACES = [
  { position: 1, name: '坎', wuxing: '水', bashen: '值符', jiuxing: '天蓬', bamen: '休门' },
  { position: 2, name: '坤', wuxing: '土', bashen: '螣蛇', jiuxing: '天芮', bamen: '死门' },
  { position: 3, name: '震', wuxing: '木', bashen: '太阴', jiuxing: '天冲', bamen: '伤门' },
  { position: 4, name: '巽', wuxing: '木', bashen: '六合', jiuxing: '天辅', bamen: '杜门' },
  { position: 5, name: '中', wuxing: '土', bashen: '勾陈', jiuxing: '天禽', bamen: '中门' },
  { position: 6, name: '乾', wuxing: '金', bashen: '朱雀', jiuxing: '天心', bamen: '开门' },
  { position: 7, name: '兑', wuxing: '金', bashen: '九地', jiuxing: '天柱', bamen: '惊门' },
  { position: 8, name: '艮', wuxing: '土', bashen: '九天', jiuxing: '天任', bamen: '生门' },
  { position: 9, name: '离', wuxing: '火', bashen: '白虎', jiuxing: '天英', bamen: '景门' },
];

const FALLBACK_YONGSHEN = [
  { name: '日干', value: '甲子（你）落坎宫，休门临之，宜静待时机' },
  { name: '时干', value: '戊辰落震宫，伤门主事，当前事务有突破之象' },
  { name: '值符', value: '天蓬星落坎宫，主事近水，需防波折' },
];

const FALLBACK_ANALYSIS = '此时局为「天遁」格，日干甲子落坎宫逢休门，主你当前宜休养生息，不宜贸然行动。时干落震宫伤门，表明所问之事正处于突破前的关键阶段，虽有阻力但前景可观。值符天蓬落坎宫，提示需注意与水相关的人或事。综合来看，此局阳气渐升，阴气未退，建议你保持耐心，半月内自见分晓。';

const FALLBACK_ADVICE = '1. 近期宜静不宜动，重大决策可延后至下月初\n2. 注意北方（坎宫）相关的人和事，可能有贵人出现\n3. 所问之事宜谨慎推进，切忌冒进\n4. 每日巳时（9-11点）为吉时，重要事务可安排在此时段';

Page({
  data: {
    skeletonLoading: true,
    date: '',
    time: '',
    city: '',
    question: '',
    submitted: false,
    loading: false,
    error: null,
    result: null,

    // Error state
    showError: false,
    errorType: '',
  },

  onLoad() {
    this._setDefaultDateTime();
    setTimeout(() => {
      this.setData({ skeletonLoading: false });
    }, 300);
  },

  // ---- 设置默认日期时间 ----
  _setDefaultDateTime() {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');

    this.setData({
      date: `${year}-${month}-${day}`,
      time: `${hours}:${minutes}`,
    });
  },

  // ---- Input Handlers ----
  onDateChange(e) {
    this.setData({ date: e.detail.value });
  },

  onTimeChange(e) {
    this.setData({ time: e.detail.value });
  },

  onCityInput(e) {
    this.setData({ city: e.detail.value });
  },

  onQuestionInput(e) {
    this.setData({ question: e.detail.value });
  },

  // ---- Submit ----
  async onSubmit() {
    const { date, time, city, question } = this.data;

    // Validation
    if (!date) {
      wx.showToast({ title: '请选择日期', icon: 'none' });
      return;
    }
    if (!time) {
      wx.showToast({ title: '请选择时间', icon: 'none' });
      return;
    }

    this.setData({ loading: true, error: null, submitted: true });

    try {
      const result = await api.qimen({ date, time, city, question });
      this._processResult(result);
    } catch (e) {
      console.warn('[Qimen] API failed, using fallback:', e);
      this.setData({
        showError: true,
        errorType: e.name === 'NetworkError' ? 'network' : 'server',
        loading: false,
        submitted: false,
      });
    }
  },

  _processResult(data) {
    if (!data || !data.palaces) {
      return this._useFallback();
    }

    // Add bamen CSS classes for styling
    const bamenClassMap = {
      '开门': 'open-door',
      '休门': 'rest-door',
      '生门': 'life-door',
      '伤门': 'scare-door',
      '惊门': 'terror-door',
      '死门': 'death-door',
      '杜门': 'surprise-door',
      '景门': 'leave-door',
    };

    const palaces = (data.palaces || []).map(p => ({
      ...p,
      bamenClass: bamenClassMap[p.bamen] || '',
    }));

    this.setData({
      result: {
        ...data,
        palaces,
        dateStr: data.dateStr || this.data.date,
        timeStr: data.timeStr || this.data.time,
      },
    });
  },

  _useFallback() {
    this.setData({
      result: {
        palaces: FALLBACK_PALACES,
        yongshen: FALLBACK_YONGSHEN,
        analysis: FALLBACK_ANALYSIS,
        advice: FALLBACK_ADVICE,
        dateStr: this.data.date,
        timeStr: this.data.time,
      },
      error: null,
    });
  },

  // ---- Retry / Reset ----
  onErrorRetry() {
    this.setData({ showError: false });
    this.onSubmit();
  },

  onRetry() {
    this.setData({ error: null, loading: false, submitted: false, result: null });
  },

  onReset() {
    this.setData({
      submitted: false,
      loading: false,
      error: null,
      result: null,
    });
    this._setDefaultDateTime();
    // Also clear city and question
    this.setData({ city: '', question: '' });
  },

  // ---- Share ----
  onShareAppMessage() {
    return {
      title: '奇门遁甲 · 天机占卜',
      path: '/pages/qimen/qimen',
    };
  },
});
