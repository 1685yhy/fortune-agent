// 新用户引导 — 三步建档（原型 dir_funcs 陆：欢迎 → 为何需要 → 填写生辰 → 完成 / 跳过）
// 契约：POST /api/persons（relation=自己, is_default 由后端定）
//   接口未就绪 → 降级：本地缓存 ylm_persons + 提示稍后，仍可进入完成页
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const persons = require('../../utils/persons');

const STEP_LABELS = ['为何需要', '填写生辰', '建档完成'];
const DONE_KEY = 'ylm_onboard_done';
const SKIP_KEY = 'ylm_onboard_skipped';

Page({
  data: {
    navOff: 0,
    dark: false,
    phase: 'welcome',          // welcome | why | form | done | skipped
    stepLabels: STEP_LABELS,
    stepCur: 0,                // 1=为何需要 2=填写生辰 3=建档完成

    // 表单（原型 BirthForm 字段）
    cal: 'solar',              // solar | lunar
    year: '',
    month: '',
    day: '',
    hourLabels: persons.HOUR_LABELS,
    hourIndex: 0,              // 时辰序号 0-11
    gender: '女',
    place: '',
    filled: false,             // 年月日齐全才可「建档」
    summary: '',               // 表单底部已填摘要
    doneSummary: '',           // 完成页生辰摘要
    saving: false,
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  // ---- 步骤切换 ----
  goWhy() {
    this.setData({ phase: 'why', stepCur: 1 });
  },
  backWelcome() {
    this.setData({ phase: 'welcome', stepCur: 0 });
  },
  goForm() {
    this.setData({ phase: 'form', stepCur: 2 });
  },
  backWhy() {
    this.setData({ phase: 'why', stepCur: 1 });
  },

  /* 跳过：标记 storage（下次启动不再自动弹出引导），展示「未建档案」屏 */
  doSkip() {
    try { wx.setStorageSync(SKIP_KEY, 1); } catch (e) { /* ignore */ }
    this.setData({ phase: 'skipped', stepCur: 0 });
  },

  /* 重新看引导（完成页/跳过页底部） */
  replay() {
    this.setData({
      phase: 'welcome', stepCur: 0,
      cal: 'solar', year: '', month: '', day: '', hourIndex: 0, gender: '女', place: '',
      filled: false, summary: '',
    });
    wx.showToast({ title: '已回到引导开头', icon: 'none' });
  },

  // ---- 表单 ----
  onCalChange(e) {
    const cal = e.currentTarget.dataset.cal;
    this.setData({ cal }, () => this._refreshSummary());
  },
  onYearInput(e) {
    this.setData({ year: this._digits(e.detail.value, 4) }, () => this._refreshFilled());
  },
  onMonthInput(e) {
    this.setData({ month: this._digits(e.detail.value, 2) }, () => this._refreshFilled());
  },
  onDayInput(e) {
    this.setData({ day: this._digits(e.detail.value, 2) }, () => this._refreshFilled());
  },
  onHourChange(e) {
    this.setData({ hourIndex: parseInt(e.currentTarget.dataset.idx, 10) || 0 }, () => this._refreshSummary());
  },
  onGenderChange(e) {
    this.setData({ gender: e.currentTarget.dataset.g }, () => this._refreshSummary());
  },
  onPlaceInput(e) {
    this.setData({ place: e.detail.value });
  },

  _digits(v, max) {
    return String(v || '').replace(/\D/g, '').slice(0, max);
  },
  _refreshFilled() {
    const d = this.data;
    const filled = !!(d.year && d.month && d.day);
    this.setData({ filled }, () => this._refreshSummary());
  },
  _refreshSummary() {
    const d = this.data;
    if (!d.filled) {
      this.setData({ summary: '' });
      return;
    }
    this.setData({
      summary: `已填写 ${d.cal === 'solar' ? '公历' : '农历'} ${d.year} 年 ${d.month} 月 ${d.day} 日 ${persons.shichenCN(d.hourIndex)} · ${d.gender}`,
    });
  },

  /* 建档：POST /api/persons → 成功进完成页；失败降级（本地存档 + 提示稍后，仍可进入） */
  async onSubmit() {
    if (!this.data.filled || this.data.saving) return;
    this.setData({ saving: true });
    const d = this.data;
    const gd = (getApp() && getApp().globalData) || {};
    const payload = {
      name: (gd.userInfo && gd.userInfo.nickName) || '我',
      relation: '自己',
      gender: persons.genderCode(d.gender),
      birth_year: parseInt(d.year, 10),
      birth_month: parseInt(d.month, 10),
      birth_day: parseInt(d.day, 10),
      birth_hour: persons.shichenIndexToHour(d.hourIndex),
      birth_minute: 0,
      calendar: d.cal,
      city: (d.place || '').trim(),
    };

    try {
      const res = await api.createPerson(payload);
      const created = (res && res.person) || null;
      if (created) {
        const list = persons.getLocalPersons();
        list.push(created);
        persons.saveLocalPersons(list);
      }
      this._finish();
    } catch (e) {
      console.warn('[Onboarding] 建档接口未就绪，走本地降级:', e && e.message);
      const local = Object.assign({
        id: 'local_' + Date.now(),
        is_default: true,
        created_at: Date.now(),
      }, payload);
      const list = persons.getLocalPersons();
      list.push(local);
      persons.saveLocalPersons(list);
      wx.showToast({ title: '云端稍后同步 · 已本地建档', icon: 'none', duration: 2200 });
      this._finish();
    } finally {
      this.setData({ saving: false });
    }
  },

  _finish() {
    try { wx.setStorageSync(DONE_KEY, 1); } catch (e) { /* ignore */ }
    const d = this.data;
    const shi = persons.shichenCN(d.hourIndex);
    this.setData({
      phase: 'done',
      stepCur: 3,
      doneSummary: `${d.cal === 'solar' ? '公历' : '农历'} ${d.year} 年 ${d.month} 月 ${d.day} 日 · ${shi} · ${d.gender}${d.place ? ' · ' + d.place : ''}`,
    });
  },

  /* 完成页：开始排盘 → bazi 页（选命主/直接排盘） */
  startPaipan() {
    wx.navigateTo({
      url: '/pages/bazi/bazi',
      fail: () => wx.reLaunch({ url: '/pages/bazi/bazi' }),
    });
  },

  /* 跳过页：演示排盘 → 提示需要出生信息 → 回到「为何需要」 */
  demoPaipan() {
    wx.showModal({
      title: '排盘需要出生信息',
      content: '八字排盘需要准确的出生年月日时\n现在去填写，只需约 1 分钟',
      confirmText: '现在去填写',
      cancelText: '暂不填写',
      confirmColor: '#A93A2C',
      success: (res) => {
        if (res.confirm) this.goWhy();
      },
    });
  },

  onShareAppMessage() {
    return { title: '易理明灯 · 点亮这盏灯', path: '/pages/onboarding/onboarding' };
  },
});
