// 今日 — 笺谱封面（原型 TodayScreen：date/brand/poem/yi-chips/talk-btn/shot-hint/foot + TabBar）
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const shareCard = require('../../utils/shareCard');
const lunar = require('../../utils/lunar');

/* 原型文案兜底（dir_b.html 755-797 行） */
const DEFAULT_POEM = ['雾散灯明处', '恰是归程时。'];
const DEFAULT_CHIPS = ['宜 · 安顿心事', '宜 · 早眠'];

/* 阳历日期 → 中文数字（原型「八月六日」格式） */
const CN_MONTH = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'];
const CN_DAY = ['一', '二', '三', '四', '五', '六', '七', '八', '九'];
function cnDate(now) {
  const d = now.getDate();
  let day;
  if (d < 10) day = CN_DAY[d - 1];
  else if (d <= 10) day = '十';
  else if (d < 20) day = '十' + CN_DAY[d - 11];
  else if (d === 20) day = '二十';
  else if (d < 30) day = '二十' + CN_DAY[d - 21];
  else if (d === 30) day = '三十';
  else day = '三十一';
  return `${CN_MONTH[now.getMonth()]}月${day}日`;
}

/* 签文拆两行：按句读拆分，成对仗两行（无数据时用原型原句） */
function splitPoem(text) {
  const t = (text || '').trim();
  if (!t) return DEFAULT_POEM.slice();
  const parts = t.split(/[。！？\n]/).map((s) => s.trim()).filter(Boolean);
  if (parts.length >= 2) return [parts[0], parts[1]];
  if (parts.length === 1) {
    const s = parts[0];
    if (s.length > 10) {
      const mid = Math.floor(s.length / 2);
      return [s.slice(0, mid), s.slice(mid)];
    }
    return [s, ''];
  }
  return DEFAULT_POEM.slice();
}

Page({
  data: {
    navOff: 0,
    dateText: '八月六日',
    solarHint: '明日立秋 · 今夜宜早眠',
    poemLines: DEFAULT_POEM,
    yiChips: DEFAULT_CHIPS,
    curTab: 'today',
    dark: false,
  },

  onLoad() {
    this._initNavOff();
    this._initDate();
    this._loadFortune();
    theme.bindTheme(this);
  },

  /* 状态栏高度适配：原型画板固定状态栏 47px，--nav-off 为差值 */
  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 日期：左阳历中文数字（八月六日），右节气提示（明日立秋 · 今夜宜早眠） */
  _initDate() {
    const now = new Date();
    let hint = '明日立秋 · 今夜宜早眠';
    try {
      hint = lunar.solarTermHint(now) || hint;
    } catch (e) {
      console.warn('[Today] 节气计算失败:', e);
    }
    this.setData({ dateText: cnDate(now), solarHint: hint });
  },

  /* 后端数据绑定（api.js 契约）：suitable → 两宜标签；personal_advice → 诗签两行 */
  async _loadFortune() {
    try {
      const app = getApp();
      // 等登录流程定型（最多 3s），保证用登录后的统一身份而非兜底 ID
      if (app && app.loginPromise) {
        await Promise.race([
          app.loginPromise,
          new Promise((resolve) => setTimeout(resolve, 3000)),
        ]);
      }
      // 统一身份：globalData.userId（登录响应 user.id）；失败降级用稳定值 local_user
      const userId = (app && app.globalData && app.globalData.userId) || 'local_user';
      const res = await api.getTodayFortune(userId);
      if (!res) return;
      const suitable = Array.isArray(res.suitable) ? res.suitable.filter(Boolean) : [];
      const chips = [suitable[0], suitable[1]]
        .filter(Boolean)
        .map((s) => '宜 · ' + s);
      this.setData({
        yiChips: chips.length >= 2 ? chips : DEFAULT_CHIPS,
        poemLines: splitPoem(res.personal_advice || res.advice || ''),
      });
    } catch (e) {
      console.warn('[Today] API 不可用，保持原型文案');
    }
  },

  /* 随手截屏 · 存为今晚的笺页：绘制墨韵笺页卡 → 保存相册 */
  onShot() {
    const data = {
      dateText: this.data.dateText,
      solarHint: this.data.solarHint,
      poemLines: this.data.poemLines,
      yiChips: this.data.yiChips,
    };
    wx.createSelectorQuery()
      .select('#inkCard')
      .fields({ node: true, size: true })
      .exec((res) => {
        const info = res && res[0];
        if (!info || !info.node) {
          wx.showToast({ title: '笺页绘制暂不可用', icon: 'none' });
          return;
        }
        const canvas = info.node;
        const dpr = wx.getWindowInfo ? wx.getWindowInfo().pixelRatio : 2;
        canvas.width = 750 * dpr;
        canvas.height = 1200 * dpr;
        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        shareCard.drawInkCard(data, canvas, (tempFilePath) => {
          shareCard.saveCardToAlbum(tempFilePath, (ok) => {
            if (ok) wx.showToast({ title: '已存入相册 · 今晚的笺页', icon: 'none', duration: 2200 });
          });
        });
      });
  },

  /* 原型 onTalk：进入夜话 */
  goChat() {
    wx.reLaunch({ url: '/pages/chat/chat' });
  },

  /* 原型 TabBar onTab */
  onTab(e) {
    const t = e.currentTarget.dataset.tab;
    const url = { today: '/pages/today/today', chat: '/pages/chat/chat', book: '/pages/reports/reports', me: '/pages/me/me' }[t];
    if (url && !url.includes('/today/')) wx.reLaunch({ url });
  },
});
