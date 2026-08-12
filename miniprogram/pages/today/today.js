// 今日 — 笺谱封面（原型 TodayScreen：date/brand/poem/yi-chips/talk-btn/shot-hint/foot + TabBar）
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const shareCard = require('../../utils/shareCard');
const lunar = require('../../utils/lunar');
const streamHost = require('../../utils/streamHost'); // 收藏同步宿主用（见 _syncHostJian）

/* 收藏复用现有收藏机制：ylm_chat_messages 中 role==='ai' && kept===true（favorites 页数据源）。
   type:'jian' 为笺匣分类标记（Task 10 收藏页「笺」分类）。 */
const MSG_KEY = 'ylm_chat_messages';

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
    /* 晨笺卡：show=已开启且有数据；notEnabled=未开启（显示「开启晨笺」入口） */
    jian: {
      show: false,
      notEnabled: false,
      expanded: false,
      saved: false,
      savedId: '',
      date: '',
      ganzhiDate: '',
      yi: [],
      ji: [],
      quote: '',
      book: '',
      privateLine: '',
      question: '',
    },
  },

  onLoad() {
    this._initNavOff();
    this._initDate();
    this._loadFortune();
    this._loadJian();
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

  /* ═══ 晨笺卡（spec 三·小程序内晨笺卡 / 五·免费边界） ═══
     开启态：拉 GET /api/jian/today 渲染卡片；未开启态：顶部「开启晨笺」入口；
     接口失败/未开启均静默处理（无 error 噪音），新用户默认不推送、卡内仍可见 */
  async _loadJian() {
    let enabled = false;
    try {
      const app = getApp();
      if (app && app.loginPromise) {
        await Promise.race([
          app.loginPromise,
          new Promise((resolve) => setTimeout(resolve, 3000)),
        ]);
      }
      const res = await api.getJianPrefs();
      const p = (res && res.prefs) || {};
      enabled = p.jian_enabled === 1 || p.jian_enabled === true;
    } catch (e) {
      return; // prefs 不可用：静默，什么都不显示
    }
    if (!enabled) {
      this.setData({ 'jian.notEnabled': true, 'jian.show': false });
      return;
    }
    try {
      const res = await api.getJianToday();
      if (!res || !res.day_ganzhi) return;
      const date = res.date || '';
      const now = date ? new Date(date.replace(/-/g, '/')) : new Date();
      let ganzhiDate = '';
      try {
        ganzhiDate = `${lunar.formatLunarDate(now.getFullYear(), now.getMonth() + 1, now.getDate())} · ${res.day_ganzhi}`;
      } catch (e) {
        ganzhiDate = `${cnDate(now)} · ${res.day_ganzhi}`;
      }
      const yi = Array.isArray(res.suitable) ? res.suitable.filter(Boolean).slice(0, 3) : [];
      const ji = Array.isArray(res.unsuitable) ? res.unsuitable.filter(Boolean).slice(0, 3) : [];
      const savedId = `jian_${date || Date.now()}`;
      this.setData({
        'jian.show': true,
        'jian.notEnabled': false,
        'jian.date': date,
        'jian.ganzhiDate': ganzhiDate,
        'jian.yi': yi,
        'jian.ji': ji,
        'jian.quote': res.quote || '',
        'jian.book': res.book || '',
        'jian.privateLine': res.private_line || '',
        'jian.question': res.question || '今天最想做成的一件事是什么?',
        'jian.savedId': savedId,
        'jian.saved': this._isJianSaved(savedId),
      });
    } catch (e) {
      console.warn('[Today] 晨笺 API 不可用，隐藏卡片');
    }
  },

  /* 今日笺是否已在收藏（同 id 去重） */
  _isJianSaved(id) {
    if (!id) return false;
    try {
      const list = wx.getStorageSync(MSG_KEY);
      return Array.isArray(list) && list.some((m) => m && m.id === id);
    } catch (e) {
      return false;
    }
  },

  /* 金句展开/收起（收起态一行 + 点开展开：书名 + 原文） */
  onQuoteTap() {
    this.setData({ 'jian.expanded': !this.data.jian.expanded });
  },

  /* 今日小问 → 对话页（问题经 globalData 预填为聊天首条消息） */
  onQuestionTap() {
    const q = this.data.jian.question;
    try {
      const app = getApp();
      if (app && app.globalData) app.globalData.jianQuestion = q;
    } catch (e) { /* ignore */ }
    wx.reLaunch({ url: '/pages/chat/chat' });
  },

  /* 收藏/取消收藏：写入 ylm_chat_messages（kept 消息，favorites 页同数据源） */
  onJianFav() {
    if (this.data.jian.saved) {
      this._unfavJian();
      return;
    }
    const j = this.data.jian;
    const quoteLine = j.quote
      ? `"${j.quote}"${j.book ? '——《' + j.book + '》' : ''}`
      : '';
    const content = [
      `晨笺 · ${j.ganzhiDate}`,
      `宜:${(j.yi || []).join(' ')} 忌:${(j.ji || []).join(' ')}`,
      quoteLine,
      j.privateLine,
      `今日小问:${j.question}`,
    ].filter(Boolean).join('\n');
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const entry = {
      id: j.savedId,
      role: 'ai',
      kept: true,
      keptAt: Date.now(),
      tag: '明灯 · 晨笺',
      type: 'jian', // Task 10 笺匣「笺」分类标记
      content,
      time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
    };
    try {
      const list = wx.getStorageSync(MSG_KEY);
      const next = (Array.isArray(list) ? list : [])
        .filter((m) => m && m.id !== entry.id)
        .concat([entry]);
      wx.setStorageSync(MSG_KEY, next);
      this._syncHostJian(entry.id, entry);
      this.setData({ 'jian.saved': true });
      wx.showToast({ title: '已收藏 · 入笺匣', icon: 'none' });
    } catch (e) {
      wx.showToast({ title: '收藏失败，请重试', icon: 'none' });
    }
  },

  /* 宿主同步（M2 根因修复）：收藏/取消收藏同时更新 streamHost.messages——
     streamHost._save() 会把 host.messages 原样写回 ylm_chat_messages，若收藏条目只写 storage
     不进 host，聊天页下一次发送/流式/删除/重置的保存都会把它覆盖抹除。
     渲染层（chat._mirror / history._load）已排除 type==='jian'，host 保留不影响任何展示 */
  _syncHostJian(id, entry) {
    try {
      const host = streamHost.getState().messages;
      if (!Array.isArray(host)) return;
      const next = host.filter((m) => m && m.id !== id);
      if (entry) next.push(entry);
      streamHost.setMessages(next);
    } catch (e) { /* 同步失败不阻断收藏 */ }
  },

  _unfavJian() {
    const id = this.data.jian.savedId;
    try {
      const list = wx.getStorageSync(MSG_KEY);
      wx.setStorageSync(MSG_KEY, (Array.isArray(list) ? list : []).filter((m) => m && m.id !== id));
      this._syncHostJian(id, null);
      this.setData({ 'jian.saved': false });
      wx.showToast({ title: '已取消收藏', icon: 'none' });
    } catch (e) {
      wx.showToast({ title: '操作失败，请重试', icon: 'none' });
    }
  },

  /* 未开启态入口 → 开启引导页 */
  onOpenJian() {
    wx.navigateTo({ url: '/pages/jian_onboard/jian_onboard' });
  },

  /* 双人合盘入口卡 → 合盘页（onLoad 自动回填我方默认命主） */
  onYuanEntry() {
    wx.navigateTo({ url: '/pages/hehun/hehun' });
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
