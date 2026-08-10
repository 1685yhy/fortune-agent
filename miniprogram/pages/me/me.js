// 我的 — 一卷手札（原型 MeScreen：印章/名号/四柱竖排/同行第N晚/列表 + TabBar）
const api = require('../../utils/api');
const lunar = require('../../utils/lunar');
const payment = require('../../utils/payment');
const security = require('../../utils/security');
const theme = require('../../utils/theme');

const HOUR_CN = ['子时', '丑时', '寅时', '卯时', '辰时', '巳时', '午时', '未时', '申时', '酉时', '戌时', '亥时'];

/* 原型 PILLARS / ME_ROWS（dir_b.html 977-985 行） */
const DEFAULT_PILLARS = [
  { l: '年', c: '戊寅' }, { l: '月', c: '丁巳' }, { l: '日', c: '庚申' }, { l: '时', c: '己卯' },
];
/* 基础行（val 由 _buildRows 按档案/会员状态动态填充） */
const BASE_ROWS = [
  { icon: '/assets/images/ic-edit.png', label: '档案', action: 'persons' },
  { icon: '/assets/images/ic-lantern.png', label: '重新看引导', action: 'onboarding' },
  { icon: '/assets/images/ic-seal.png', label: '会员', action: 'member' },
  { icon: '/assets/images/ic-book2.png', label: '我的命书', action: 'reports' },
  { icon: '/assets/images/ic-keep.png', label: '我的收藏', action: 'favorites' },
  { icon: '/assets/images/ic-chat.png', label: '对话历史', action: 'history' },
  { icon: '/assets/images/ic-moon.png', label: '解梦手记', action: 'dreams' },
  { icon: '/assets/images/ic-bell.png', label: '开灯提醒', action: 'sub', sub: true },
  { icon: '/assets/images/ic-kebab.png', label: '关于明灯', action: '' },
];

const STORAGE_KEY = 'ylm_chat_messages';
const ARCHIVE_KEY = 'ylm_chat_archives';

/* 注销确认文案（原型 dir_o：输入框须与之一致才可确认） */
const DEREG_CONFIRM = '注销';

Page({
  data: {
    navOff: 0,
    displayName: '小晚',
    birthdayText: '1998.05.12 卯时',
    lunarBirthday: '戊寅年 · 四月十七',
    pillars: DEFAULT_PILLARS,
    nights: 231,
    meRows: BASE_ROWS,
    memberStatus: '基础版',
    memberDialogVisible: false,
    memberPlans: [],
    subOn: false,
    memberIsMember: false,
    memberBenefits: [],
    memberExpireText: '',
    favCount: 0,                // v1.1 收藏数（我的收藏行 val）
    personCount: 0,             // 档案行 val：本地缓存命主数
    curTab: 'me',
    dark: false,
    /* 登录显式展示（v5.1）：头像/状态标签/登录动作 */
    avatarUrl: '',              // 微信头像（无则印章「明」兜底）
    loginTag: '',               // 微信登录 / 体验用户（未登录不显示）
    loginAction: '退出登录',     // 已登录=退出登录 / 未登录=点击登录
    realLogin: false,           // 标签配色：真实微信登录（朱砂） vs 体验用户（低调墨色）
    /* 账号中心（原型 dir_o · 壹）：更换账号 / 注销账号 */
    switchDialogVisible: false, // 更换账号说明弹层
    deregDialogVisible: false,  // 注销确认弹层（输入「注销」解禁）
    deregInput: '',             // 注销确认输入
    deregDone: false,           // 注销成功页（账号已注销）
  },

  onLoad() {
    this._initNavOff();
    // 会员开通方案（首月/月度，价格与后端 SUBSCRIBE_PLANS 对齐）
    const plans = ['first_month', 'monthly']
      .map((id) => payment.getProduct(id))
      .filter((p) => !!p)
      .map((p) => ({
        id: p.id,
        name: p.name,
        priceLabel: p.priceLabel,
        description: p.description,
        icon: p.icon,
      }));
    this.setData({ memberPlans: plans });
    theme.bindTheme(this, () => this._buildRows());
  },

  onShow() {
    this._loadFavCount();
    this._loadPersonCount();
    this._deriveUser();
    this._deriveIdentity();
    this._deriveNights();
    this._loadMember();
    this._loadSubscription();
  },

  /* v1.1 收藏数：本机会话 + 归档里 kept 的 AI 回复数（与 favorites 页数据源一致） */
  _loadFavCount() {
    let count = 0;
    try {
      const scan = (msgs) => {
        (Array.isArray(msgs) ? msgs : []).forEach((m) => {
          if (m && m.role === 'ai' && m.kept) count++;
        });
      };
      scan(wx.getStorageSync(STORAGE_KEY));
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      (Array.isArray(arch) ? arch : []).forEach((a) => scan(a.messages));
    } catch (e) { /* ignore */ }
    if (count !== this.data.favCount) {
      this.setData({ favCount: count });
      this._buildRows();
    }
  },

  /* 会员状态（P1：真实接口，失败/未登录回退基础版） */
  _loadMember() {
    api.getMemberInfo()
      .then((info) => {
        if (info && info.isMember) {
          const expire = info.expireDate ? String(info.expireDate).slice(0, 10) : '';
          this.setData({
            memberIsMember: true,
            memberStatus: expire ? `会员 · ${expire}` : '已开通',
            memberExpireText: expire,
            memberBenefits: (info.benefits && info.benefits.length ? info.benefits : ['每日运势 · 完整解读', '专属命书 · 无限查阅', '深度问答 · 畅聊不设限']),
          });
        } else {
          this.setData({ memberIsMember: false, memberStatus: '基础版', memberBenefits: [], memberExpireText: '' });
        }
        this._buildRows();
      })
      .catch(() => {
        this.setData({ memberIsMember: false, memberStatus: '基础版', memberBenefits: [], memberExpireText: '' });
        this._buildRows();
      });
  },

  /* 开灯提醒状态：GET /api/user/subscription（后端就绪则真实，否则本地兜底） */
  _loadSubscription() {
    api.getSubscription()
      .then((res) => {
        if (res && typeof res.daily_push === 'boolean') {
          this.setData({ subOn: res.daily_push });
          try { wx.setStorageSync('ylm_daily_push', res.daily_push); } catch (e) { /* ignore */ }
        }
        this._buildRows();
      })
      .catch(() => {
        let on = false;
        try { on = !!wx.getStorageSync('ylm_daily_push'); } catch (e) { /* ignore */ }
        this.setData({ subOn: on });
        this._buildRows();
      });
  },

  /* 开灯提醒开关：乐观更新 → POST /api/user/subscription → 失败回滚 */
  onSubChange(e) {
    const on = !!e.detail.value;
    const prev = this.data.subOn;
    this.setData({ subOn: on });
    this._buildRows();
    try { wx.setStorageSync('ylm_daily_push', on); } catch (err) { /* ignore */ }
    api.updateSubscription(on)
      .then(() => wx.showToast({ title: on ? '已开启每晚提醒' : '已关闭提醒', icon: 'none' }))
      .catch(() => {
        this.setData({ subOn: prev });
        this._buildRows();
        wx.showToast({ title: '保存失败，请检查网络', icon: 'none' });
      });
  },

  /* 行 val 动态化（图标随暗黑模式换暗色变体；按 label 匹配，避免行序变更后索引错位） */
  _buildRows(hasBazi) {
    const hb = hasBazi !== undefined ? hasBazi : !!this.data.birthdayText;
    const suffix = this.data.dark ? '-dark.png' : '.png';
    const rows = BASE_ROWS.map((r) => ({
      ...r,
      icon: r.icon.replace(/\.png$/, suffix),
    }));
    rows.forEach((r) => {
      if (r.label === '档案') r.val = this.data.personCount > 0 ? `${this.data.personCount} 位命主` : (hb ? '八字已设' : '未设置');
      else if (r.label === '会员') r.val = this.data.memberStatus;
      else if (r.label === '我的收藏') r.val = this.data.favCount > 0 ? `${this.data.favCount} 笺` : '还未收藏';
      else if (r.label === '开灯提醒') r.val = this.data.subOn ? '每晚 21:30' : '已关闭';
    });
    this.setData({ meRows: rows });
  },

  /* 档案行 val：本地缓存命主数（接口不可用不阻塞；persons/bazi 页保存后同步） */
  _loadPersonCount() {
    let count = 0;
    try {
      const list = wx.getStorageSync('ylm_persons');
      count = Array.isArray(list) ? list.length : 0;
    } catch (e) { /* ignore */ }
    if (count !== this.data.personCount) {
      this.setData({ personCount: count });
      this._buildRows();
    }
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 用户数据绑定：globalData.baziInfo → 手札；缺失时拉取 profile（后端 bazi_info 契约 {year,month,day,hour,gender}） */
  _deriveUser() {
    const gd = (getApp() && getApp().globalData) || {};
    const b = gd.baziInfo || null;
    const patch = {};
    if (gd.userInfo && gd.userInfo.nickName) patch.displayName = gd.userInfo.nickName;

    if (b) {
      this._applyBaziToView(b);
      return;
    }
    // 无本地档案：拉取真实档案（已保存过八字的用户回显）
    api.getUserProfile()
      .then((profile) => {
        const info = (profile && profile.bazi_info) || null;
        if (!info || !info.year) return;
        const app = getApp();
        if (app && app.globalData) {
          app.globalData.hasBazi = true;
          app.globalData.baziInfo = info;
        }
        this._applyBaziToView(info);
      })
      .catch(() => { /* 未登录/后端未就绪：保持原型默认 */ });
  },

  _applyBaziToView(b) {
    const y = b.year || b.birthYear;
    const m = b.month || b.birthMonth;
    const d = b.day || b.birthDay;
    if (!y || !m || !d) return;

    const hourIdx = this._hourToIndex(b.hour !== undefined && b.hour !== null ? b.hour : b.birthHour);
    const hour = (hourIdx >= 0 && HOUR_CN[hourIdx]) || '';
    const patch = {
      birthdayText: `${y}.${String(m).padStart(2, '0')}.${String(d).padStart(2, '0')}${hour ? ' ' + hour : ''}`,
      lunarBirthday: this._lunarLabel(Number(y), Number(m), Number(d)),
    };
    const vals = [b.year_pillar, b.month_pillar, b.day_pillar, b.hour_pillar];
    if (vals.every((v) => !!v)) {
      patch.pillars = ['年', '月', '日', '时'].map((l, i) => ({ l, c: vals[i] }));
    }
    this.setData(patch);
    this._buildRows(true);
  },

  /* 时辰字段（整点或序号）→ 时辰序号（0-11） */
  _hourToIndex(h) {
    const v = parseInt(h, 10);
    if (Number.isNaN(v) || v < 0) return -1;
    if (v <= 11) return v;                 // 旧数据：直接存了序号
    if (v === 23) return 0;                // 子时代表整点
    if (v % 2 === 1 && v <= 21) return (v + 1) / 2;  // 丑1 寅3 卯5 … 亥21
    return -1;
  },

  /* 公历 → 农历文案（如「农历六月廿五」） */
  _lunarLabel(y, m, d) {
    try {
      const t = lunar.solar2lunar(y, m, d);
      if (!t) return '';
      return `农历${lunar.formatLunarDate(y, m, d)}`;
    } catch (e) {
      return '';
    }
  },

  /* 第 N 晚：本地首见天数，无记录时原型默认 231 */
  _deriveNights() {
    try {
      const first = wx.getStorageSync('ylm_first_seen');
      if (!first) {
        wx.setStorageSync('ylm_first_seen', Date.now());
        return;
      }
      const days = Math.floor((Date.now() - first) / 86400000);
      if (days > 0 && days + 1 !== this.data.nights) {
        this.setData({ nights: days + 1 });
      }
    } catch (e) {
      console.warn('[Me] 首见时间读取失败');
    }
  },

  /* ═══ v5.1 登录显式展示（头像/昵称/状态标签/登录·退出） ═══
     身份态判定（app.js 契约）：
       - 真实微信登录：globalData.token 存在（userInfo 可能带 nickName/avatarUrl）
       - 体验用户：无 token 且 userId === 'local_user'（后端不可用时的本地兜底）
       - 未登录：无 token 且非 local_user（退出后；残留旧 userId 也归此态） */
  _deriveIdentity() {
    const gd = (getApp() && getApp().globalData) || {};
    const u = gd.userInfo || {};
    const token = gd.token || null;
    const isLocal = !token && gd.userId === 'local_user';
    const loggedOut = !token && !isLocal;

    if (loggedOut) {
      this.setData({ displayName: '未登录', avatarUrl: '', loginTag: '', loginAction: '点击登录', realLogin: false });
      return;
    }
    if (token) {
      this.setData({
        realLogin: true,
        loginTag: '微信登录',
        loginAction: '退出登录',
        displayName: (u && u.nickName) || '小晚',
        avatarUrl: (u && u.avatarUrl) || '',
      });
      return;
    }
    // 体验模式（local_user）
    this.setData({ displayName: '小晚', avatarUrl: '', loginTag: '体验用户', loginAction: '退出登录', realLogin: false });
  },

  /* 登录/退出入口（名片区小字）：未登录 → 静默微信登录；已登录 → 确认后退出 */
  onLoginActionTap() {
    const gd = (getApp() && getApp().globalData) || {};
    const token = gd.token || null;
    const isLocal = !token && gd.userId === 'local_user';
    if (!token && !isLocal) {
      this._loginAgain();
      return;
    }
    wx.showModal({
      title: '退出登录',
      content: '确定退出登录吗？',
      confirmText: '退出',
      confirmColor: '#A93A2C',
      success: (res) => {
        if (res.confirm) this._logout();
      },
    });
  },

  /* 点击登录：重新走 app.js 静默微信登录（wx.login → code → JWT；后端不可用走 local_user 兜底，无需授权弹窗） */
  _loginAgain() {
    wx.showLoading({ title: '登录中…', mask: true });
    Promise.resolve(getApp().wechatLogin())
      .catch(() => { /* wechatLogin 内部已降级到本地模式，不阻断 */ })
      .then(() => {
        wx.hideLoading();
        this._deriveIdentity();
        this._loadMember();
        this._buildRows();
      });
  },

  /* 退出登录：只清身份（token/账号/本地档案），聊天记录与收藏一律保留 */
  _logout() {
    this._clearIdentity();
    this._deriveIdentity();
    this._buildRows();
    wx.showToast({ title: '已退出登录', icon: 'none' });
  },

  /* 清身份（退出登录 / 更换账号 / 注销 共用）：token/账号/本地档案，聊天记录与收藏一律保留 */
  _clearIdentity() {
    const remove = (k) => { try { wx.removeStorageSync(k); } catch (e) { /* ignore */ } };
    remove('ylm_token');              // JWT（api.js 401 重登也以它为准）
    remove('ylm_user_id');            // 用户 id（含 local_user 兜底值）
    security.removeSecure('auth');    // ylm_enc_auth（旧版身份：token/userId/loginTime）
    security.removeSecure('userProfile'); // ylm_enc_userProfile（账号八字档案，随账号走）
    api.setToken(null);               // 清内存 token（api.js getToken 的 storage 兜底已清空）

    const app = getApp();
    if (app && app.globalData) {
      const gd = app.globalData;
      gd.token = null;
      gd.userId = null;
      gd.userInfo = null;
      gd.isLoggedIn = false;
      gd.hasBazi = false;
      gd.baziInfo = null;
    }

    // 界面切为未登录态（手札回原型默认；聊天记录/收藏保留）
    this.setData({
      birthdayText: '1998.05.12 卯时',
      lunarBirthday: '戊寅年 · 四月十七',
      pillars: DEFAULT_PILLARS,
      memberIsMember: false,
      memberStatus: '基础版',
      memberBenefits: [],
      memberExpireText: '',
    });
    this._deriveIdentity();
    this._buildRows();
  },

  /* 列表行点击（data-action 路由） */
  onRowTap(e) {
    const action = e.currentTarget.dataset.action;
    if (action === 'persons') {
      // 档案管理：多人命主（自己/家人/朋友；排盘直接选用）
      wx.navigateTo({ url: '/pages/persons/persons' });
    } else if (action === 'onboarding') {
      // 重新看引导（首启三步建档 · 可跳过）
      wx.navigateTo({ url: '/pages/onboarding/onboarding' });
    } else if (action === 'reports') {
      wx.reLaunch({ url: '/pages/reports/reports' });
    } else if (action === 'member') {
      this._openMemberDialog();
    } else if (action === 'favorites') {
      // v1.1 我的收藏：⭐收藏的回复列表（本机会话 + 归档扫描）
      wx.navigateTo({ url: '/pages/favorites/favorites' });
    } else if (action === 'history') {
      // 对话历史：归档夜话（搜索/预览/继续/删除）
      wx.navigateTo({ url: '/pages/history/history' });
    } else if (action === 'dreams') {
      // 解梦手记：夜话中的解梦回复收进此册
      wx.navigateTo({ url: '/pages/dreams/dreams' });
    }
    // 其余行（开灯提醒/关于明灯）保持原样，无跳转
  },

  /* ═══ 账号中心（原型 dir_o · 壹）：更换账号 / 注销账号 ═══ */

  /* 更换账号入口（名片区小字）：说明弹层 → 确认 → 清身份 → 重新走微信登录 */
  onSwitchAccountTap() {
    const gd = (getApp() && getApp().globalData) || {};
    const token = gd.token || null;
    const isLocal = !token && gd.userId === 'local_user';
    if (!token && !isLocal) {
      this._loginAgain();               // 未登录 → 直接登录
      return;
    }
    this.setData({ switchDialogVisible: true });
  },

  closeSwitchDialog() {
    this.setData({ switchDialogVisible: false });
  },

  confirmSwitchAccount() {
    this.setData({ switchDialogVisible: false });
    // 清身份（灯油/命书/收藏保留）→ 重新走 app.js wechatLogin（wx.login → 新 token）
    this._clearIdentity();
    this._loginAgain();
  },

  /* 注销账号入口（危险区）：朱砂警示 → 输入「注销」确认 → POST /api/user/cancel → 成功页 */
  onDeregTap() {
    const gd = (getApp() && getApp().globalData) || {};
    const token = gd.token || null;
    const isLocal = !token && gd.userId === 'local_user';
    if (!token && !isLocal) {
      wx.showToast({ title: '当前未登录，无需注销', icon: 'none' });
      return;
    }
    this.setData({ deregDialogVisible: true, deregInput: '' });
  },

  closeDeregDialog() {
    this.setData({ deregDialogVisible: false, deregInput: '' });
  },

  onDeregInput(e) {
    this.setData({ deregInput: e.detail.value });
  },

  confirmDereg() {
    if (String(this.data.deregInput || '').trim() !== DEREG_CONFIRM) return;
    if (this._deregSubmitting) return;
    this._deregSubmitting = true;
    wx.showLoading({ title: '注销中…', mask: true });
    api.cancelAccount(DEREG_CONFIRM)
      .then(() => {
        wx.hideLoading();
        this._deregSubmitting = false;
        this.setData({ deregDialogVisible: false, deregInput: '', deregDone: true });
        this._clearIdentity();
        wx.showToast({ title: '账号已注销', icon: 'none' });
      })
      .catch(() => {
        wx.hideLoading();
        this._deregSubmitting = false;
        wx.showToast({ title: '注销失败，请检查网络', icon: 'none' });
      });
  },

  /* 注销成功页 → 回到今日首页 */
  onDeregGoneBack() {
    this.setData({ deregDone: false });
    wx.reLaunch({ url: '/pages/today/today' });
  },

  /* ── 会员开通（虚拟支付优先，mock 降级由 payment.js 统一处理） ── */
  _openMemberDialog() {
    this.setData({ memberDialogVisible: true });
  },

  closeMemberDialog() {
    this.setData({ memberDialogVisible: false });
  },

  noop() { /* 阻止遮罩点击穿透 */ },

  /* 选择套餐 → 支付 → 刷新会员状态 */
  buyPlan(e) {
    const planId = e.currentTarget.dataset.plan;
    if (!planId || this._buying) return;
    this._buying = true;
    payment.subscribeMember(planId)
      .then((res) => {
        if (res && res.success) {
          this.setData({ memberDialogVisible: false });
          this._loadMember();
        }
      })
      .catch(() => {
        wx.showToast({ title: '支付异常，请稍后再试', icon: 'none' });
      })
      .finally(() => { this._buying = false; });
  },

  /* 原型 onBack：返回今日 */
  goToday() {
    wx.reLaunch({ url: '/pages/today/today' });
  },

  /* 原型 onTab：底部栏切换 */
  onTab(e) {
    const t = e.currentTarget.dataset.tab;
    const url = { today: '/pages/today/today', chat: '/pages/chat/chat', book: '/pages/reports/reports', me: '/pages/me/me' }[t];
    if (url && !url.includes('/me/')) wx.reLaunch({ url });
  },
});
