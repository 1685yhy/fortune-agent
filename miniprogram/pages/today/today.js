// 今日 — Daily Fortune Card
const api = require('../../utils/api');

Page({
  data: {
    loading: true,
    error: false,
    swipeIndex: 0, // 0=today, -1=yesterday, 1=tomorrow
    dates: [],
    fortune: null,
    score: 0,
    scoreLevel: '', // excellent, good, fair, poor
    ganzhi: '',
    yi: [],
    ji: [],
    advice: '',
    mood: '',
    moodIcon: '',
    hasBazi: false,
    refreshing: false,
  },

  onLoad() {
    this.loadToday();
  },

  onShow() {
    if (this.data.fortune === null) {
      this.loadToday();
    }
  },

  onPullDownRefresh() {
    this.setData({ refreshing: true });
    this.loadToday(() => {
      wx.stopPullDownRefresh();
      this.setData({ refreshing: false });
    });
  },

  loadToday(callback) {
    this.setData({ loading: true, error: false });

    api.getTodayFortune()
      .then((res) => {
        if (!res || res.error) {
          // 演示数据（当后端不可用时）
          this.setDemoData();
        } else {
          this.processFortuneData(res);
        }
      })
      .catch(() => {
        // 后端不可用，使用演示数据
        this.setDemoData();
      })
      .finally(() => {
        this.setData({ loading: false });
        if (callback) callback();
      });
  },

  setDemoData() {
    const today = new Date();
    const dateStr = `${today.getFullYear()}年${today.getMonth() + 1}月${today.getDate()}日`;

    this.setData({
      fortune: {
        date: dateStr,
        ganzhi: '甲子日',
        score: Math.floor(Math.random() * 30) + 65,
        yi: [
          { action: '求财', time: '09:00-11:00' },
          { action: '签约', time: '14:00-16:00' },
          { action: '出行', time: '吉时皆宜' },
        ],
        ji: [
          { action: '争执', time: '全天' },
          { action: '借贷', time: '不宜' },
        ],
        advice: '今日宜静心思考，顺势而为。财运渐起，把握良机。',
        mood: '内心平和，适宜规划未来',
      },
      hasBazi: getApp().globalData.hasBazi || true,
    });

    this.processFortuneData(this.data.fortune);
  },

  processFortuneData(data) {
    const score = data.score || 85;
    let scoreLevel = 'good';
    if (score >= 85) scoreLevel = 'excellent';
    else if (score >= 70) scoreLevel = 'good';
    else if (score >= 55) scoreLevel = 'fair';
    else scoreLevel = 'poor';

    // 根据运势给出心情表情
    const moodIcons = {
      excellent: '🌟',
      good: '☀️',
      fair: '⛅',
      poor: '🌧️',
    };

    this.setData({
      fortune: data,
      score,
      scoreLevel,
      ganzhi: data.ganzhi || '甲子日',
      yi: (data.yi || []).slice(0, 3),
      ji: (data.ji || []).slice(0, 3),
      advice: data.advice || '保持平和，顺势而为',
      mood: data.mood || '心境平和',
      moodIcon: moodIcons[scoreLevel],
      hasBazi: true,
      swipeIndex: 0,
    });
  },

  // 滑动切换日期
  onSwipeChange(e) {
    const current = e.detail.current;
    this.setData({ swipeIndex: current === 1 ? 0 : current === 0 ? -1 : 1 });
  },

  // 滑动查看前后日期
  swipePrev() {
    const idx = this.data.swipeIndex - 1;
    this.setData({ swipeIndex: idx });
    // TODO: 加载前一天的运势
  },

  swipeNext() {
    const idx = this.data.swipeIndex + 1;
    this.setData({ swipeIndex: idx });
    // TODO: 加载后一天的运势
  },

  goChat() {
    wx.switchTab({ url: '/pages/chat/chat' });
  },

  goEditProfile() {
    wx.switchTab({ url: '/pages/me/me' });
  },

  onShareAppMessage() {
    const data = this.data;
    return {
      title: ` ${data.score}分 — ${data.advice?.slice(0, 20) || '易理明灯'}`,
      path: '/pages/today/today',
    };
  },
});
