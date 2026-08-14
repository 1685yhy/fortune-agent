// 设置 — 墨韵纸笺（账号与登录 / 账号信息 / 消息订阅 / 关于与隐私 / 数据说明 / 危险区注销）
// 设置入口重构：登录·退出·更换账号·注销 自 me 页收进此页，功能代码复用 me.js 原实现（不重写）
const api = require('../../utils/api');
const security = require('../../utils/security');
const theme = require('../../utils/theme');
const nightMode = require('../../utils/nightMode'); // 深夜时段三档预设(方案·灯下漫谈,同后端 src/engines/night_mode.py)

/* 注销确认文案（原型 dir_o：输入框须与之一致才可确认） */
const DEREG_CONFIRM = '注销';

/* ═══ 消息订阅（spec 三·订阅管理 / 推送授权与时间自选 / 五·免费边界）
     红线：推送必须主动开启；任何开关关闭 → PUT enabled:false，绝不默认发送。
     时间改动仅在对应通道开启时落库；未开启时改动仅留在本地（开启时随开关一并带出）。 ═══ */
const WHISPER_KEY = 'ylm_jian_whisper'; // 个性化私语本地偏好（后端字段待接入，同 jian_onboard 约定）

// 晨笺 06:00-10:00 每 15 分钟；晚安 21:00-23:45 每 15 分钟（后端正则拒绝 24:00，同 jian_onboard）
function buildTimeOptions(startHour, startMin, endHour, endMin) {
  const out = [];
  let cur = startHour * 60 + startMin;
  const end = endHour * 60 + endMin;
  while (cur <= end) {
    const h = Math.floor(cur / 60);
    const m = cur % 60;
    out.push(('0' + h).slice(-2) + ':' + ('0' + m).slice(-2));
    cur += 15;
  }
  return out;
}
const MORNING_OPTIONS = buildTimeOptions(6, 0, 10, 0); // 17 档 06:00-10:00
const NIGHT_OPTIONS = buildTimeOptions(21, 0, 23, 45); // 12 档 21:00-23:45
const MORNING_DEFAULT = '07:30';
const NIGHT_DEFAULT = '23:00';

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
    /* 消息订阅（晨笺/晚安开关、时间自选、个性化私语、服务号绑定态） */
    jianLoading: true,          // prefs 拉取中（开关禁用防闪变）
    jianEnabled: false,         // 晨笺开关
    nightEnabled: false,        // 晚安开关
    morningOptions: MORNING_OPTIONS,
    morningIdx: MORNING_OPTIONS.indexOf(MORNING_DEFAULT),
    morningTime: MORNING_DEFAULT,
    nightOptions: NIGHT_OPTIONS,
    nightIdx: NIGHT_OPTIONS.indexOf(NIGHT_DEFAULT),
    nightTime: NIGHT_DEFAULT,
    whisper: true,              // 个性化私语（默认开；本地偏好）
    bound: false,               // 服务号绑定态（bound_status === 'bound'）
    invalid: false,            // 订阅失效态（bound_status === 'invalid'，连续失败≥3次）
    /* 择日提醒（大事择吉日：GET/PUT /api/zeri/prefs；绑定态同服务号通道） */
    zeriLoading: true,          // 择日 prefs 拉取中
    zeriReminder: false,        // 择日提醒开关（全局偏好）
    zeriBound: false,           // 择日推送绑定态（同 jian_prefs bound_status）
    zeriInvalid: false,         // 择日推送失效态
    /* 深夜陪伴（方案·灯下漫谈：时段档位/点灯动效/深夜挽留/灯语定时/私语联动晨笺）
       水合源 GET /api/night/prefs（Task 2）；私语与本地 ylm_jian_whisper 双向同步 */
    nightPresetLabels: Object.keys(nightMode.PRESET_LABEL).map((k) => nightMode.PRESET_LABEL[k]), // 三档显示文案(早睡党/标准/夜猫子)
    nightPresetIdx: 1,          // 时段档位下标（默认标准 21:00-01:00）
    nightEffectOn: true,        // 点灯动效开关
    nightKeepOn: true,          // 深夜挽留开关
    lampTimerOptions: [5, 10, 15, 30], // 灯语定时关闭档位（分钟）
    lampTimerIdx: 2,            // 默认 15 分钟（[5,10,15,30] 下标 2）
    nightLoading: true,         // 水合中（开关禁用防闪变）
    whisperOn: true,            // 私语开关（联动晨笺，与 ylm_jian_whisper 同源）
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
  },

  onShow() {
    this._deriveIdentity();
    this._loadJianPrefs();
    this._loadZeriPrefs(); // 择日提醒（与 jian prefs 并行水合）
    this._loadNightPrefs(); // 深夜陪伴（与 jian prefs 并行水合）
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

  /* ═══ 消息订阅：拉取 prefs 水合（开关/时间/绑定态/私语）。
       成功→填状态；失败→静默默认（关、07:30/23:00、未绑定），不打扰。
       onShow 刷新：从 jian_onboard（去绑定）返回后绑定态同步更新；并发请求去重。 ═══ */
  _loadJianPrefs() {
    if (this._prefsBusy) return;
    this._prefsBusy = true;
    let whisper = true;
    try {
      const v = wx.getStorageSync(WHISPER_KEY);
      // 'off' 兼容：深夜陪伴私语开关（onNightWhisperSwitch）写入 'on'/'off' 格式
      if (v === 0 || v === '0' || v === 'off') whisper = false;
    } catch (e) { /* ignore */ }
    api.getJianPrefs().then((res) => {
      this._prefsBusy = false;
      const p = (res && res.prefs) || {};
      const morningTime = p.jian_time || MORNING_DEFAULT;
      const nightTime = p.night_time || NIGHT_DEFAULT;
      const mIdx = MORNING_OPTIONS.indexOf(morningTime);
      const nIdx = NIGHT_OPTIONS.indexOf(nightTime);
      this.setData({
        jianLoading: false,
        jianEnabled: p.jian_enabled === 1 || p.jian_enabled === true,
        nightEnabled: p.night_enabled === 1 || p.night_enabled === true,
        bound: p.bound_status === 'bound',
        invalid: p.bound_status === 'invalid',
        morningTime,
        morningIdx: mIdx >= 0 ? mIdx : MORNING_OPTIONS.indexOf(MORNING_DEFAULT),
        nightTime,
        nightIdx: nIdx >= 0 ? nIdx : NIGHT_OPTIONS.indexOf(NIGHT_DEFAULT),
        whisper,
      });
    }).catch(() => {
      this._prefsBusy = false;
      this.setData({ jianLoading: false, whisper }); // 静默降级：保持默认关态
    });
  },

  /* 开关（红线：关闭 → PUT enabled:false，绝不默认发送；开启 → 带所选时间一并落库） */
  onJianSwitch(e) {
    if (this.data.jianLoading) return;
    const on = !!e.detail.value;
    const prev = this.data.jianEnabled;
    this.setData({ jianEnabled: on });
    const patch = { jian_enabled: on };
    if (on) patch.jian_time = this.data.morningTime;
    this._savePrefs(patch, 'jianEnabled', prev);
  },

  onNightSwitch(e) {
    if (this.data.jianLoading) return;
    const on = !!e.detail.value;
    const prev = this.data.nightEnabled;
    this.setData({ nightEnabled: on });
    const patch = { night_enabled: on };
    if (on) patch.night_time = this.data.nightTime;
    this._savePrefs(patch, 'nightEnabled', prev);
  },

  /* 时间自选（仅在对应通道开启时落库；关闭时改动留在本地，开启时随开关带出） */
  onMorningPick(e) {
    const idx = Number(e.detail.value);
    const time = MORNING_OPTIONS[idx];
    this.setData({ morningIdx: idx, morningTime: time });
    if (this.data.jianEnabled) this._savePrefs({ jian_time: time });
  },

  onNightPick(e) {
    const idx = Number(e.detail.value);
    const time = NIGHT_OPTIONS[idx];
    this.setData({ nightIdx: idx, nightTime: time });
    if (this.data.nightEnabled) this._savePrefs({ night_time: time });
  },

  /* 统一保存：成功静默 toast；失败回滚开关（revertField 指定）+ 提示。时间改动不回滚，下次成功落库 */
  _savePrefs(patch, revertField, prev) {
    api.putJianPrefs(patch).then(() => {
      wx.showToast({ title: '已保存', icon: 'none' });
    }).catch(() => {
      if (revertField) this.setData({ [revertField]: prev });
      wx.showToast({ title: '保存失败，请重试', icon: 'none' });
    });
  },

  /* 个性化私语（本地偏好，默认开；后端 private_enabled 字段待接入后迁移，同 jian_onboard）
     终审:与深夜陪伴区私语开关(whisperOn)同源互刷——改这边同步另一边的 UI 态 */
  onWhisperSwitch(e) {
    const on = !!e.detail.value;
    this.setData({ whisper: on, whisperOn: on });
    try { wx.setStorageSync(WHISPER_KEY, on ? 1 : 0); } catch (err) { /* ignore */ }
  },

  /* ═══ 深夜陪伴（方案·灯下漫谈：时段档位/点灯动效/深夜挽留/灯语定时/私语联动晨笺）
       数据源 GET /api/night/prefs（Task 2）；变更即 PUT；私语与本地 ylm_jian_whisper 双向同步。
       水合失败静默默认（standard/开/开/15 分钟/私语开），不打扰。 ═══ */
  async _loadNightPrefs() {
    try {
      const res = await api.getNightPrefs();
      const p = (res && res.prefs) || {};
      const nightMode = require('../../utils/nightMode');
      const labels = Object.keys(nightMode.PRESET_LABEL);
      const idx = Math.max(0, labels.indexOf(p.preset || 'standard'));
      const tIdx = Math.max(0, [5, 10, 15, 30].indexOf(p.lamp_timer_min || 15));
      this.setData({
        nightLoading: false,
        nightPresetIdx: idx,
        nightEffectOn: p.effect_enabled !== 0,
        nightKeepOn: p.keep_enabled !== 0,
        lampTimerIdx: tIdx,
        whisperOn: p.whisper_enabled !== 0,
      });
      try { wx.setStorageSync('ylm_night_prefs', p); } catch (e) {}
    } catch (e) { this.setData({ nightLoading: false }); }
  },

  onNightPresetChange(e) {
    const nightMode = require('../../utils/nightMode');
    const labels = Object.keys(nightMode.PRESET_LABEL);
    const preset = labels[Number(e.detail.value)] || 'standard';
    this.setData({ nightPresetIdx: Number(e.detail.value) });
    api.putNightPrefs({ preset }).catch(() => wx.showToast({ title: '保存失败', icon: 'none' }));
  },

  onNightEffectSwitch(e) {
    this.setData({ nightEffectOn: e.detail.value });
    api.putNightPrefs({ effect_enabled: e.detail.value }).catch(() => {});
  },

  onNightKeepSwitch(e) {
    this.setData({ nightKeepOn: e.detail.value });
    api.putNightPrefs({ keep_enabled: e.detail.value }).catch(() => {});
  },

  onLampTimerChange(e) {
    const min = [5, 10, 15, 30][Number(e.detail.value)] || 15;
    this.setData({ lampTimerIdx: Number(e.detail.value) });
    api.putNightPrefs({ lamp_timer_min: min }).catch(() => {});
  },

  onNightWhisperSwitch(e) {
    // 终审:与消息订阅区私语开关(whisper)同源互刷(本地键共用 ylm_jian_whisper)
    this.setData({ whisperOn: e.detail.value, whisper: !!e.detail.value });
    try { wx.setStorageSync('ylm_jian_whisper', e.detail.value ? 'on' : 'off'); } catch (err) {}
    api.putNightPrefs({ whisper_enabled: e.detail.value }).catch(() => {});
  },

  /* ═══ 择日提醒（大事择吉日：GET/PUT /api/zeri/prefs）
       红线同晨笺：关闭 → PUT reminder_enabled:false，绝不默认推送；
       关闭时提示"已排期的提醒将停止"；未绑定 → 复用 jian_onboard 绑定引导。 ═══ */
  _loadZeriPrefs() {
    api.getZeriPrefs().then((res) => {
      const p = (res && res.prefs) || {};
      this.setData({
        zeriLoading: false,
        zeriReminder: p.reminder_enabled === 1 || p.reminder_enabled === true,
        zeriBound: p.bound_status === 'bound',
        zeriInvalid: p.bound_status === 'invalid',
      });
    }).catch(() => {
      this.setData({ zeriLoading: false }); // 静默降级：保持默认关态
    });
  },

  onZeriReminderSwitch(e) {
    if (this.data.zeriLoading) return;
    const on = !!e.detail.value;
    const prev = this.data.zeriReminder;
    this.setData({ zeriReminder: on });
    if (!on) {
      wx.showToast({ title: '已排期的提醒将停止', icon: 'none', duration: 2200 });
    }
    api.putZeriPrefs({ reminder_enabled: on }).then(() => {
      if (on) wx.showToast({ title: '已开启择日提醒', icon: 'none' });
    }).catch(() => {
      this.setData({ zeriReminder: prev });
      wx.showToast({ title: '保存失败，请重试', icon: 'none' });
    });
  },

  /* 未绑定 → 去绑定（jian_onboard 引导页：服务号二维码/绑定引导；返回后 onShow 刷新状态） */
  goJianOnboard() {
    wx.navigateTo({ url: '/pages/jian_onboard/jian_onboard' });
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
