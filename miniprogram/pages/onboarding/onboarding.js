// 新用户引导 — 三步建档（原型 dir_funcs 陆：欢迎 → 为何需要 → 填写生辰 → 完成 / 跳过）
// 契约：POST /api/persons（relation=自己, is_default 由后端定）
//   G2 A3：建档成功必须以服务端确认响应（person）为准；失败 → 停留表单页明确提示，不本地假建档
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const persons = require('../../utils/persons');
const guide = require('../../utils/guide');

const STEP_LABELS = ['为何需要', '填写生辰', '建档完成'];
const DONE_KEY = 'ylm_onboard_done';
const SKIP_KEY = 'ylm_onboard_skipped';

/* E1 功能导览 3 卡（首访尾链：排盘 → 今日 → 问明灯；tab=true 为底栏页 → switchTab
   失败回退 reLaunch——本项目无原生 tabBar 注册，见 onTourGo 注释） */
const TOUR_CARDS = [
  { seal: '排', title: '排一次盘', sub: '生辰八字，一生脉络', cta: '去排盘', url: '/pages/paipan/paipan', tab: false },
  { seal: '今', title: '看看今天运势', sub: '每日宜忌 · 流日四运 · 时辰择时', cta: '去今日', url: '/pages/today/today', tab: true },
  { seal: '问', title: '有问题问明灯', sub: '命理 · 运势 · 择吉 · 随时可问', cta: '去对话', url: '/pages/chat/chat', tab: true },
];

Page({
  data: {
    navOff: 0,
    dark: false,
    phase: 'welcome',          // welcome | why | form | done | skipped | tour
    stepLabels: STEP_LABELS,
    stepCur: 0,                // 1=为何需要 2=填写生辰 3=建档完成
    tourCards: TOUR_CARDS,
    tourIdx: 0,                // 功能导览当前卡（0-2）
    tourSeen: false,           // P3：是否看过导览（ylm_tour_done/skipped 任一）→ 不再自动弹

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
    this.setData({ phase: 'skipped', stepCur: 0, tourSeen: this._tourSeen() });
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
      if (!res || !res.person) throw new Error('服务端未返回档案');
      const list = persons.getLocalPersons();
      list.push(res.person);
      persons.saveLocalPersons(list);
      this._finish();
    } catch (e) {
      // G2 A3：失败 → 明确失败提示 + 不写本地 + 停留表单页（可重试），绝不「已本地建档」假成功
      console.warn('[Onboarding] 建档失败:', e && e.message);
      wx.showToast({ title: '建档失败，请重试', icon: 'none' });
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
      tourSeen: this._tourSeen(),
      doneSummary: `${d.cal === 'solar' ? '公历' : '农历'} ${d.year} 年 ${d.month} 月 ${d.day} 日 · ${shi} · ${d.gender}${d.place ? ' · ' + d.place : ''}`,
    });
  },

  /* E1+P3：done/skipped 页主按钮——首访未看过 → 尾链功能导览（现状）；
     看过一次（ylm_tour_done/ylm_tour_skipped 任一）→ 不再自动弹，直接进入 App。
     导览只在建档流程尾链出现——me 页「重新看引导」入口仍走本页 welcome 起，
     经流程走到此处：未看过 → 尾链导览；看过 → 直接进入（与拍板一致） */
  onDonePrimary() {
    if (this.data.tourSeen) {
      this._leaveOnboarding();
    } else {
      this._enterTour();
    }
  },

  /* 再次查看入口（复用 done/skipped 页按钮区，未新建页面/导航项）：
     显式重看功能导览，无视 seen 标记；重看后完成/跳过仍写原有二键 */
  replayTour() {
    this._enterTour();
  },

  /* Q4（批次2 收尾）：进入导览时按档案预填 tour 卡 1——本地已有命主档案
     （persons 同源，与 today.js hasLocalArchive 同源）→ 排盘引导卡追加档案摘要
     「你的档案：…」；无档案/无命主对象 → 原卡片原样（引导去建档，现状保持）。
     自动尾链（onDonePrimary）与显式重看（replayTour）共用本入口，两路同预填 */
  _enterTour() {
    this.setData({ phase: 'tour', stepCur: 0, tourIdx: 0, tourCards: this._tourCardsWithArchive() });
  },

  /* 预填判定：默认命主优先（is_default），无默认取列表首个；有命主对象 →
     卡 1 附档案摘要（persons.birthSummary 全应用同款格式，如
     「公历 1976年8月23日 丑时 女 · 榆树」）。按钮文案保持「去排盘」：
     paipan 落地页为手动输入表单（隐私红线不落库、不自动带档），改
     「用我的档案排盘」会过度承诺；落地后无建档引导条（Q3 已按档案区分），
     用档案信息重排天然可行。仅对话归档/登录带回 bazi（hasLocalArchive 为真
     但无 person 对象）→ 无摘要可展示，保持原卡片。不变异模块级 TOUR_CARDS */
  _tourCardsWithArchive() {
    const list = persons.getLocalPersons();
    const p = persons.findDefault(list) || (Array.isArray(list) && list.length ? list[0] : null);
    // 两路径都返回副本（M4 硬化：无档案路径原返回共享常量引用，就地改返回值会污染常量）
    if (!p) return TOUR_CARDS.map((c) => Object.assign({}, c));
    const cards = TOUR_CARDS.map((c) => Object.assign({}, c));
    cards[0].arch = persons.birthSummary(p);
    return cards;
  },

  /* 是否看过导览：ylm_tour_done / ylm_tour_skipped 二选一（任一存在即看过） */
  _tourSeen() {
    try {
      return !!(wx.getStorageSync(guide.TOUR_DONE_KEY) || wx.getStorageSync(guide.TOUR_SKIP_KEY));
    } catch (e) {
      return false;
    }
  },

  /* 导览「下一步」：卡 1/卡 2 → 下一张 */
  onTourNext() {
    if (guide.tourHasNext(this.data.tourIdx, this.data.tourCards.length)) {
      this.setData({ tourIdx: this.data.tourIdx + 1 });
    }
  },

  /* 导览「跳过」→ ylm_tour_skipped（保持跳过语义）→ 离开引导 */
  onTourSkip() {
    try { wx.setStorageSync(guide.TOUR_SKIP_KEY, 1); } catch (e) { /* ignore */ }
    this._leaveOnboarding();
  },

  /* 3 卡看完「开始使用」→ ylm_tour_done → 离开引导 */
  onTourDone() {
    try { wx.setStorageSync(guide.TOUR_DONE_KEY, 1); } catch (e) { /* ignore */ }
    this._leaveOnboarding();
  },

  /* 导览卡去向：卡1 排盘 navigateTo（失败静默，契约）；卡2/3 为底栏页 →
     契约用 wx.switchTab——本项目无原生 tabBar 注册（app.json 无 tabBar 配置，
     底栏为各页自绘），switchTab 必失败 → 回退 wx.reLaunch（与今日页 goChat/
     onTab 同套路）。去向按钮不落 tour 标记：中途离开未做「完成/跳过」决定，
     保持 ylm_tour_done/ylm_tour_skipped 二选一不变量 */
  onTourGo(e) {
    const card = this.data.tourCards[Number(e.currentTarget.dataset.idx)] || null;
    if (!card) return;
    if (card.tab) {
      wx.switchTab({
        url: card.url,
        fail: () => wx.reLaunch({ url: card.url, fail: () => {} }),
      });
    } else {
      wx.navigateTo({ url: card.url, fail: () => { /* 契约：失败静默 */ } });
    }
  },

  /* 离开引导：正常情况 onboarding 由 navigateTo 进入 → navigateBack 回上一页
     （首启即 tab 首页）；页面栈异常（分享直达等）→ reLaunch 今日兜底 */
  _leaveOnboarding() {
    wx.navigateBack({
      fail: () => wx.reLaunch({ url: '/pages/today/today', fail: () => {} }),
    });
  },

  onShareAppMessage() {
    return { title: '易理明灯 · 点亮这盏灯', path: '/pages/onboarding/onboarding' };
  },
});
