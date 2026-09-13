// 明灯晨笺 · 开启引导页（主动同意 + 时间自选）
// 红线（spec 三·推送授权与时间自选）：推送必须用户主动开启——点「我同意，开启晨笺」
// 才写 prefs；未开启/关闭后/未完成引导一律不推送。文案明示「每天早/晚一条运势推送，可随时关闭」。
const api = require('../../utils/api');
const theme = require('../../utils/theme');

// 晨笺 06:00-10:00 每 15 分钟；晚安 21:00-24:00 每 15 分钟。
// 后端时间正则 ^([01]\d|2[0-3]):[0-5]\d$ 拒绝 24:00，故晚安档最晚取 23:45（仍属 21:00-24:00 区间）。
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
const WHISPER_KEY = 'ylm_jian_whisper'; // 轻私语本地偏好（后端字段待订阅管理接入时补）

Page({
  data: {
    navOff: 0,
    dark: false,
    loading: true,            // prefs 拉取中
    enabled: false,           // 已开启态（jian_enabled=1）
    bound: false,             // 服务号绑定态
    invalid: false,           // 订阅失效态（bound_status === 'invalid'，连续失败≥3次）
    /* 晨笺时间 */
    morningOptions: MORNING_OPTIONS,
    morningIdx: MORNING_OPTIONS.indexOf(MORNING_DEFAULT),
    morningTime: MORNING_DEFAULT,
    /* 晚安时间 */
    nightOptions: NIGHT_OPTIONS,
    nightIdx: NIGHT_OPTIONS.indexOf(NIGHT_DEFAULT),
    nightTime: NIGHT_DEFAULT,
    /* 轻私语（默认开；关闭 = 纯金句模式） */
    whisper: true,
    /* 关闭晨笺二次确认 */
    closeDialogVisible: false,
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
    this._loadPrefs();
  },

  _initNavOff() {
    const info = (wx.getWindowInfo && wx.getWindowInfo()) || {};
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 拉取后端偏好：成功→填状态；失败→默认未开启（07:30/23:00），不打扰 */
  _loadPrefs() {
    let whisper = true;
    try {
      const v = wx.getStorageSync(WHISPER_KEY);
      // 'off' 兼容：设置页深夜陪伴私语开关写入 'on'/'off' 格式
      if (v === 0 || v === '0' || v === 'off') whisper = false;
    } catch (e) { /* ignore */ }

    api.getJianPrefs().then((res) => {
      const p = (res && res.prefs) || {};
      const morningTime = p.jian_time || MORNING_DEFAULT;
      const nightTime = p.night_time || NIGHT_DEFAULT;
      const mIdx = MORNING_OPTIONS.indexOf(morningTime);
      const nIdx = NIGHT_OPTIONS.indexOf(nightTime);
      this.setData({
        loading: false,
        enabled: p.jian_enabled === 1 || p.jian_enabled === true,
        bound: p.bound_status === 'bound',
        invalid: p.bound_status === 'invalid',
        morningTime,
        morningIdx: mIdx >= 0 ? mIdx : MORNING_OPTIONS.indexOf(MORNING_DEFAULT),
        nightTime,
        nightIdx: nIdx >= 0 ? nIdx : NIGHT_OPTIONS.indexOf(NIGHT_DEFAULT),
        whisper,
      });
    }).catch(() => {
      this.setData({ loading: false, whisper });
    });
  },

  /* ═══ 时间自选（晨笺 / 晚安） ═══ */
  onMorningChange(e) {
    const idx = Number(e.detail.value);
    const time = MORNING_OPTIONS[idx];
    this.setData({ morningIdx: idx, morningTime: time });
    if (this.data.enabled) this._savePrefs({ jian_time: time });
  },

  onNightChange(e) {
    const idx = Number(e.detail.value);
    const time = NIGHT_OPTIONS[idx];
    this.setData({ nightIdx: idx, nightTime: time });
    if (this.data.enabled) this._savePrefs({ night_time: time });
  },

  /* ═══ 轻私语（本地偏好；后端字段待 Task 10 订阅管理接入） ═══ */
  onWhisperChange(e) {
    const on = !!e.detail.value;
    this.setData({ whisper: on });
    try { wx.setStorageSync(WHISPER_KEY, on ? 1 : 0); } catch (err) { /* ignore */ }
  },

  /* ═══ 主动同意：点「我同意」才写 prefs（未开启绝不推送） ═══ */
  onAgree() {
    if (this.data.loading) return;
    wx.showLoading({ title: '开启中...', mask: true });
    api.putJianPrefs({
      jian_enabled: true,
      jian_time: this.data.morningTime,
      night_enabled: true,
      night_time: this.data.nightTime,
    }).then(() => {
      wx.hideLoading();
      this.setData({ enabled: true, loading: false });
      wx.showToast({ title: '晨笺已开启', icon: 'success' });
      setTimeout(() => this._back(), 900);
    }).catch(() => {
      wx.hideLoading();
      wx.showToast({ title: '开启失败，请重试', icon: 'none' });
    });
  },

  /* 已开启态：改动自动保存（改时间即时生效） */
  _savePrefs(patch) {
    api.putJianPrefs(patch).catch(() => {
      wx.showToast({ title: '保存失败，请重试', icon: 'none' });
    });
  },

  /* ═══ 关闭晨笺（随时可关 · 关闭当天即停） ═══ */
  onCloseTap() { this.setData({ closeDialogVisible: true }); },
  closeCloseDialog() { this.setData({ closeDialogVisible: false }); },
  confirmClose() {
    this.setData({ closeDialogVisible: false });
    wx.showLoading({ title: '关闭中...', mask: true });
    api.putJianPrefs({ jian_enabled: false, night_enabled: false }).then(() => {
      wx.hideLoading();
      this.setData({ enabled: false });
      wx.showToast({ title: '已关闭', icon: 'success' });
    }).catch(() => {
      wx.hideLoading();
      wx.showToast({ title: '关闭失败，请重试', icon: 'none' });
    });
  },

  /* 返回：有上级页则返回；直开本页则回今日页 */
  _back() {
    const pages = getCurrentPages();
    if (pages.length > 1) {
      wx.navigateBack();
    } else {
      wx.reLaunch({ url: '/pages/today/today' });
    }
  },

  goBack() { this._back(); },

  noop() {},
});
