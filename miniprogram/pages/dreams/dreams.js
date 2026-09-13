// 解梦手记 — 夜梦入册（原型 dir_s · 伍：日期+关键词+结论摘要 → 详情：梦境原文+结论+古籍角标）
// 数据源：本机会话 ylm_chat_messages + 归档 ylm_chat_archives 中 role==='ai' 且
//         tag 以「解梦」开头的回复（chat.js curatedFor / SEED 的既有 tag 约定：
//         '解梦 · 夜记'、'解梦 · 水与桥'、'解梦 · 旧宅' 等）
// 梦境原文 = 该解梦回复前紧邻的用户消息；结论 = AI 回复内容；角标 = 该回复引用（无则墨韵兜底）
// 交互：点入详情（梦境原文笺纸 + 解梦结论 + 引用角标）；「再问问这个梦」回夜话带上下文；
//       删除：朱砂确认 → 消散动画 → 空态
const theme = require('../../utils/theme');
const lunar = require('../../utils/lunar');
const streamHost = require('../../utils/streamHost');

const STORAGE_KEY = 'ylm_chat_messages';
const ARCHIVE_KEY = 'ylm_chat_archives';

/* 解梦消息判定：tag 以「解梦」开头（chat.js curatedFor / SEED 的既有约定） */
function isDreamMsg(m) {
  return !!(m && m.role === 'ai' && m.tag && String(m.tag).indexOf('解梦') === 0);
}

/* 日期标签：今夜 / 昨夜 / 周X（原型 dow 位） */
function dayLabel(ts) {
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = Number(ts) || 0;
  if (t >= startToday) return '今夜';
  if (t >= startToday - 86400000) return '昨夜';
  return '周' + '日一二三四五六'[new Date(t).getDay()];
}

/* 农历日期（原型 六月廿二 位），失败回退公历 */
function lunarDay(ts) {
  const d = new Date(ts || Date.now());
  try {
    return lunar.formatLunarDate(d.getFullYear(), d.getMonth() + 1, d.getDate());
  } catch (e) {
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }
}

/* 梦境关键词：首句截断（原型 梦见蛇/梦见考试/梦见水） */
function dreamKey(text) {
  const t = String(text || '').trim().replace(/\s+/g, ' ');
  const m = t.match(/^[^，。！？、；\n]{2,14}/);
  const k = (m && m[0]) || t.slice(0, 12);
  return k.length > 14 ? k.slice(0, 14) : k;
}

Page({
  data: {
    navOff: 0,
    dark: false,
    notes: [],             // 手记列表（按时间倒序）
    loaded: false,
    /* 详情视图（页内切换，原型 list ↔ detail） */
    view: 'list',
    current: null,         // {id, key, dream, read, tag, time, dow, date, quote, quoteNote, srcId}
    /* 删除确认弹层 + 移除动画 */
    dlgDel: null,
    removing: '',
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
  },

  onShow() {
    this._load();
  },

  _initNavOff() {
    const info = (wx.getWindowInfo && wx.getWindowInfo()) || {};
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  goBack() {
    if (this.data.view === 'detail') {
      this.setData({ view: 'list', current: null });
      return;
    }
    wx.navigateBack({ delta: 1, fail: () => wx.reLaunch({ url: '/pages/me/me' }) });
  },

  /* ═══ 数据装配：本机会话 + 归档 → 解梦回复（tag 解梦*） → 手记 ═══ */

  _load() {
    const notes = [];
    const seen = {};
    const scan = (msgs, baseTs) => {
      (Array.isArray(msgs) ? msgs : []).forEach((m, i) => {
        if (!isDreamMsg(m)) return;
        if (seen[m.id]) return;          // 同一解梦回复只入册一次（会话/归档重名）
        seen[m.id] = true;
        const prev = msgs[i - 1];
        const dream = (prev && prev.role === 'user') ? String(prev.content || '') : '';
        const ts = baseTs || Date.now();
        const cit = (m.citations && m.citations.length) ? m.citations[0] : null;
        notes.push({
          id: m.id,
          key: dream ? dreamKey(dream) : dreamKey(String(m.content || '')),
          dream,
          read: String(m.content || ''),
          tag: m.tag,
          time: m.time || '',
          ts,
          dow: dayLabel(ts),
          date: lunarDay(ts),
          quote: (cit && (cit.title || cit.source)) ? (cit.title || cit.source) : '《梦书》夜录',
          quoteNote: cit ? '「' + String((cit.text || cit.source || '').slice(0, 12)) + (String(cit.text || '').length > 12 ? '…' : '') + '」' : '「梦是心在夜里悄悄记账」',
          srcId: null,                   // null = 本机会话（已是最新上下文）
        });
      });
    };
    try {
      const hostMsgs = (streamHost.getState() && streamHost.getState().messages) || null;
      const cur = (hostMsgs && hostMsgs.length) ? hostMsgs : wx.getStorageSync(STORAGE_KEY);
      scan(cur, Date.now());
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      (Array.isArray(arch) ? arch : []).forEach((a) => {
        const before = notes.length;
        scan(a && a.messages, (a && a.createdAt) || Date.now());
        for (let i = before; i < notes.length; i++) notes[i].srcId = a && a.id;
      });
    } catch (e) { /* ignore */ }
    notes.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    this.setData({ notes, loaded: true });
  },

  /* 点入详情 */
  onNoteTap(e) {
    const id = e.currentTarget.dataset.id;
    const n = this.data.notes.find((it) => it.id === id);
    if (!n) return;
    this.setData({ view: 'detail', current: n });
  },

  /* 「再问问这个梦」：带该梦上下文回夜话（归档会话 → 恢复；本机会话直接回） */
  onContinue() {
    const n = this.data.current;
    if (!n) return;
    if (n.srcId) {
      let arch = [];
      try {
        arch = wx.getStorageSync(ARCHIVE_KEY);
      } catch (e) { /* ignore */ }
      const a = (Array.isArray(arch) ? arch : []).find((x) => x && x.id === n.srcId);
      if (a && a.messages && a.messages.length) {
        const msgs = a.messages.map((m) => {
          const copy = Object.assign({}, m);
          delete copy.mdNodes;
          delete copy.segments;
          return copy;
        });
        if (streamHost.streaming) streamHost.stop();
        // G3 H-3：写回走 persist —— 晨笺收藏条目（type==='jian' && kept）不被覆盖写抹除
        streamHost.persist(msgs.slice(-50));
      }
    }
    wx.navigateTo({ url: '/pages/chat/chat' });
  },

  /* ═══ 删除此记：确认弹层 → 移除该解梦回复（会话与归档同步） → 动画 ═══ */

  askDel() {
    const n = this.data.current;
    if (!n) return;
    this.setData({ dlgDel: { id: n.id, key: n.key } });
  },

  closeDlgDel() {
    this.setData({ dlgDel: null });
  },

  confirmDel() {
    const d = this.data.dlgDel;
    if (!d) return;
    this.setData({ dlgDel: null, removing: d.id });
    setTimeout(() => {
      // G2 B2：删除以 storage 真实写成为准，写失败 → 「删除失败」而非假成功
      const ok = this._removeNote(d.id);
      this.setData({ removing: '', view: 'list', current: null });
      this._load();
      wx.showToast({ title: ok ? '已删除此记' : '删除失败，请重试', icon: 'none' });
    }, 330);
  },

  /* 从本机会话 + 归档里移除该消息（与 favorites 页 _unkeep 同源操作方式）；
     返回是否全部写入成功（G2 B2：storage 写失败不再被吞） */
  _removeNote(id) {
    let ok = true;
    const removeIn = (list) => (Array.isArray(list) ? list : []).filter((m) => m && m.id !== id);
    try {
      const cur = wx.getStorageSync(STORAGE_KEY);
      wx.setStorageSync(STORAGE_KEY, removeIn(cur));
    } catch (e) { ok = false; }
    try {
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      if (Array.isArray(arch)) {
        wx.setStorageSync(ARCHIVE_KEY, arch.map((a) => (
          (a && a.messages) ? Object.assign({}, a, { messages: removeIn(a.messages) }) : a
        )));
      }
    } catch (e) { ok = false; }
    /* 宿主内存同步（回到聊天页时现场一致） */
    if (streamHost && typeof streamHost.removeMessage === 'function') {
      try { streamHost.removeMessage(id); } catch (e) { ok = false; }
    }
    return ok;
  },

  noop() { /* 弹层内吞掉背景滚动/穿透 */ },
});
