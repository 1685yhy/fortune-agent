// 今日详解 — 原型 S13（流日四运逐条解读 + 三宜二忌详解 + 时辰高亮）
// 数据来源：今日页透传 storage(ylm_today_detail)；缺省时自拉 /api/calendar/today
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const lunar = require('../../utils/lunar');

/* 与今日页同款透传 key（today.js goTodayDetail 写入） */
const DETAIL_KEY = 'ylm_today_detail';

/* 流日四运四维元数据（key ↔ 展示名） */
const F4_META = [
  { key: 'career', label: '事业' },
  { key: 'wealth', label: '财运' },
  { key: 'love', label: '感情' },
  { key: 'health', label: '健康' },
];

/* 当前时辰序号：子时=0 .. 亥时=11（子时跨 23-01，取 23 点后归子时） */
function shichenNowIdx() {
  const h = new Date().getHours();
  if (h === 23) return 0;
  return Math.floor((h + 1) / 2);
}

Page({
  data: {
    dark: false,
    /* 头部：农历 · 干支 · 分数 · 宜忌 chips */
    dateLine: '',
    scoreText: '',
    yiChips: [],
    jiChips: [],
    /* 四运逐条：[{key,label,scoreText,percent,desc}] */
    fortune4: [],
    /* 宜忌详解：[{action,time,reason}] */
    yiDetail: [],
    jiDetail: [],
    /* 时辰：12 条，当前时辰高亮；选中条展示 desc */
    hourly: [],
    curHourIdx: -1,
    selHourIdx: -1,
    selHourDesc: '',
  },

  onLoad() {
    theme.bindTheme(this);
    const cached = wx.getStorageSync(DETAIL_KEY);
    if (cached && cached.dayGanzhi) {
      this._render(cached);
    } else {
      this._fetch();
    }
  },

  /* 透传数据缺失（直进详情页/存储失效）→ 自拉 API */
  async _fetch() {
    try {
      const app = getApp();
      if (app && app.loginPromise) {
        await Promise.race([
          app.loginPromise,
          new Promise((resolve) => setTimeout(resolve, 3000)),
        ]);
      }
      const userId = (app && app.globalData && app.globalData.userId) || 'local_user';
      const res = await api.getTodayFortune(userId);
      if (!res) return;
      this._render({
        date: res.date || '',
        dayGanzhi: res.day_ganzhi || '',
        score: res.score || '',
        suitable: Array.isArray(res.suitable) ? res.suitable : [],
        unsuitable: Array.isArray(res.unsuitable) ? res.unsuitable : [],
        yiDetail: Array.isArray(res.yi_detail) ? res.yi_detail : [],
        jiDetail: Array.isArray(res.ji_detail) ? res.ji_detail : [],
        fortune4: res.fortune4 || null,
        hourly: Array.isArray(res.hourly) ? res.hourly : [],
      });
    } catch (e) {
      console.warn('[TodayDetail] API 不可用，保持空态');
    }
  },

  _render(p) {
    /* 头部：农历日期 · 日干支 */
    let lunarDate = '';
    if (p.date) {
      try {
        const d = new Date(p.date.replace(/-/g, '/'));
        lunarDate = lunar.formatLunarDate(d.getFullYear(), d.getMonth() + 1, d.getDate());
      } catch (e) { /* ignore */ }
    }
    const dateLine = [lunarDate, p.dayGanzhi].filter(Boolean).join(' · ') || '今日';

    /* 四运逐条 */
    const f4 = p.fortune4 || null;
    const fortune4 = F4_META
      .filter((m) => f4 && f4[m.key])
      .map((m) => {
        const it = f4[m.key];
        const sc = Number(it.score) || 0;
        return {
          key: m.key,
          label: m.label,
          scoreText: sc.toFixed(1),
          percent: Math.max(0, Math.min(100, sc * 10)),
          desc: it.desc || '',
        };
      });

    /* 宜忌 chips（头部摘要） */
    const suitable = Array.isArray(p.suitable) ? p.suitable.filter(Boolean) : [];
    const unsuitable = Array.isArray(p.unsuitable) ? p.unsuitable.filter(Boolean) : [];

    /* 时辰 12 条：当前时辰高亮；默认选中当前时辰看 desc */
    const hourly = Array.isArray(p.hourly) ? p.hourly.filter((h) => h && h.time) : [];
    const curIdx = hourly.length ? shichenNowIdx() % hourly.length : -1;
    const selIdx = curIdx >= 0 ? curIdx : (hourly.length ? 0 : -1);

    this.setData({
      dateLine,
      scoreText: p.score ? `综合运势 ${p.score} 分` : '',
      yiChips: suitable.slice(0, 3),
      jiChips: unsuitable.slice(0, 2),
      fortune4,
      yiDetail: (Array.isArray(p.yiDetail) ? p.yiDetail : []).filter((d) => d && d.action),
      jiDetail: (Array.isArray(p.jiDetail) ? p.jiDetail : []).filter((d) => d && d.action),
      hourly,
      curHourIdx: curIdx,
      selHourIdx: selIdx,
      selHourDesc: selIdx >= 0 ? (hourly[selIdx].desc || '') : '',
    });
  },

  /* 点时辰 chip → 看该时辰吉凶解读 */
  onHourTap(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const hourly = this.data.hourly;
    if (idx < 0 || idx >= hourly.length) return;
    this.setData({
      selHourIdx: idx,
      selHourDesc: hourly[idx].desc || '',
    });
  },
});
