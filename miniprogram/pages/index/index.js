// 首页 — 今日运势卡片 + 快捷入口
const api = require('../../utils/api');

Page({
  data: {
    loading: true,
    date: '',
    mood: '',
    yi: [],
    ji: [],
    lucky: '',
    hasBazi: false,
  },

  onLoad() { this.loadToday(); },
  onShow() { this.loadToday(); },

  async loadToday() {
    this.setData({ loading: true });
    try {
      const res = await api.dailyCalendar();
      if (res.status === 'ok') {
        const c = res.calendar;
        this.setData({
          loading: false,
          date: c.date,
          mood: c.overall_mood || '保持平和，顺势而为',
          yi: (c.yi || []).slice(0, 4),
          ji: (c.ji || []).slice(0, 4),
          lucky: [c.lucky_color, c.lucky_direction, c.lucky_number].filter(Boolean).join(' · '),
          hasBazi: true,
        });
      } else {
        this.setData({ loading: false, hasBazi: false });
      }
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  goChat() { wx.switchTab({ url: '/pages/chat/chat' }); },
  goFace() { wx.switchTab({ url: '/pages/face/face' }); },
  goCalendar() { wx.switchTab({ url: '/pages/calendar/calendar' }); },

  onShareAppMessage() {
    return {
      title: `🔮 今日运势：${this.data.mood}`,
      path: '/pages/index/index',
    };
  },
});
