// 设置 — 墨韵纸笺（账号与登录 / 账号信息 / 关于与隐私 / 数据说明 / 危险区注销）
// 设置入口重构：登录·退出·更换账号·注销 自 me 页收进此页，功能代码复用 me.js 原实现（不重写）
const api = require('../../utils/api');
const security = require('../../utils/security');
const theme = require('../../utils/theme');

/* 注销确认文案（原型 dir_o：输入框须与之一致才可确认） */
const DEREG_CONFIRM = '注销';

Page({
  data: {
    navOff: 0,
    dark: false,
    /* 登录态（同 me 页三态判定：微信登录 / 体验用户 / 未登录） */
    loggedIn: false,            // 已登录（微信登录或体验用户）→ 显示头像/昵称/退出/更换
    displayName: '未登录',
    avatarUrl: '',              // 微信头像（无则印章「明」兜底）
    loginTag: '',               // 微信登录 / 体验用户（未登录不显示）
    realLogin: false,           // 标签配色：真实微信登录（朱砂） vs 体验用户（低调墨色）
    identityText: '未登录',     // 账号信息：当前登录态（三态）
    /* 账号弹层（原型 dir_o · 壹）：更换账号 / 注销账号 */
    switchDialogVisible: false, // 更换账号说明弹层
    deregDialogVisible: false,  // 注销确认弹层（输入「注销」解禁）
    deregInput: '',             // 注销确认输入
    deregDone: false,           // 注销成功页（账号已注销）
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
  },

  onShow() {
    this._deriveIdentity();
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* ═══ 登录态三态判定（复用 me.js _deriveIdentity，app.js 契约） ═══
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
      this.setData({ loggedIn: false, displayName: '未登录', avatarUrl: '', loginTag: '', realLogin: false, identityText: '未登录' });
      return;
    }
    if (token) {
      this.setData({
        loggedIn: true,
        realLogin: true,
        loginTag: '微信登录',
        identityText: '微信登录',
        displayName: (u && u.nickName) || '小晚',
        avatarUrl: (u && u.avatarUrl) || '',
      });
      return;
    }
    // 体验模式（local_user）
    this.setData({ loggedIn: true, displayName: '小晚', avatarUrl: '', loginTag: '体验用户', realLogin: false, identityText: '体验用户' });
  },

  /* ═══ 微信登录（复用 me.js _loginAgain：wx.login → code → JWT；后端不可用走 local_user 兜底） ═══ */
  onLoginTap() {
    this._loginAgain();
  },

  _loginAgain() {
    wx.showLoading({ title: '登录中…', mask: true });
    Promise.resolve(getApp().wechatLogin())
      .catch(() => { /* wechatLogin 内部已降级到本地模式，不阻断 */ })
      .then(() => {
        wx.hideLoading();
        this._deriveIdentity();
      });
  },

  /* ═══ 退出登录（复用 me.js onLoginActionTap/_logout：确认后清身份，聊天/收藏保留） ═══ */
  onLogoutTap() {
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

  _logout() {
    this._clearIdentity();
    this._deriveIdentity();
    wx.showToast({ title: '已退出登录', icon: 'none' });
  },

  /* 清身份（退出登录 / 更换账号 / 注销 共用，复用 me.js _clearIdentity）：
     只清 4 键 + 内存 token + globalData，聊天记录/收藏/灯油一律保留 */
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

    // 界面切为未登录态（聊天记录/收藏保留）
    this.setData({ loggedIn: false, displayName: '未登录', avatarUrl: '', loginTag: '', realLogin: false, identityText: '未登录' });
  },

  /* ═══ 更换账号（复用 me.js onSwitchAccountTap 系） ═══ */
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

  /* ═══ 注销账号（复用 me.js onDeregTap 系：朱砂警示 → 输入「注销」确认 → POST /api/user/cancel → 成功页） ═══ */
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

  /* ── 关于与隐私 ── */
  goPrivacy() {
    wx.navigateTo({ url: '/pages/privacy/privacy' });
  },

  goAgreement() {
    wx.navigateTo({ url: '/pages/agreement/agreement' });
  },

  goBack() {
    wx.navigateBack();
  },

  noop() { /* 阻止遮罩点击穿透 */ },
});
