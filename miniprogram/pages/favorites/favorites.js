// 我的收藏 — 收藏星笺（v1.1）
// 数据源：本机会话 ylm_chat_messages + 新开对话归档 ylm_chat_archives 中
//         role==='ai' && kept===true 的回复（后端未持久化收藏，feedback 仅 positive/negative）
// 交互：点按复制；长按取消收藏
const theme = require('../../utils/theme');

const STORAGE_KEY = 'ylm_chat_messages';
const ARCHIVE_KEY = 'ylm_chat_archives';

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

Page({
  data: {
    navOff: 0,
    items: [],
    loaded: false,
    dark: false,
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
  },

  onShow() {
    this._load();
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 扫描本机会话 + 归档 → kept 的 AI 回复（按收藏时间倒序） */
  _load() {
    const items = [];
    const now = Date.now();
    const push = (m) => {
      if (!m || m.role !== 'ai' || !m.kept) return;
      items.push({
        id: m.id,
        content: String(m.content || ''),
        tag: m.tag || '明灯 · 夜话',
        time: m.time || '',
        keptAt: m.keptAt || now,
      });
    };
    try {
      (wx.getStorageSync(STORAGE_KEY) || []).forEach(push);
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      (Array.isArray(arch) ? arch : []).forEach((a) => {
        (a && a.messages ? a.messages : []).forEach(push);
      });
    } catch (e) { /* ignore */ }
    items.sort((a, b) => (b.keptAt || 0) - (a.keptAt || 0));
    items.forEach((it) => {
      it.keptLabel = formatTime(it.keptAt);
    });
    this.setData({ items, loaded: true });
  },

  /* 点按 → 复制收藏内容 */
  onItemTap(e) {
    const { content } = e.currentTarget.dataset;
    if (!content) return;
    wx.setClipboardData({
      data: content,
      success: () => wx.showToast({ title: '已复制', icon: 'none' }),
    });
  },

  /* 长按 → 取消收藏（同步移除消息上的 kept 标记） */
  onItemLongPress(e) {
    const { id } = e.currentTarget.dataset;
    wx.showModal({
      title: '取消收藏',
      content: '从收藏中移除这条回复？',
      confirmText: '移除',
      confirmColor: '#A93A2C',
      success: (res) => {
        if (!res.confirm) return;
        this._unkeep(id);
        this._load();
        wx.showToast({ title: '已移除', icon: 'none' });
      },
    });
  },

  _unkeep(id) {
    const unkeepIn = (list) => {
      if (!Array.isArray(list)) return list;
      return list.map((m) => {
        if (!m || m.id !== id || m.role !== 'ai') return m;
        const copy = Object.assign({}, m);
        delete copy.kept;
        delete copy.keptAt;
        return copy;
      });
    };
    try {
      wx.setStorageSync(STORAGE_KEY, unkeepIn(wx.getStorageSync(STORAGE_KEY)));
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      if (Array.isArray(arch)) {
        wx.setStorageSync(ARCHIVE_KEY, arch.map((a) => {
          if (!a || !a.messages) return a;
          return Object.assign({}, a, { messages: unkeepIn(a.messages) });
        }));
      }
    } catch (e) { /* ignore */ }
    // 宿主内存同步（回到聊天页时现场一致）
    try {
      const streamHost = require('../../utils/streamHost');
      if (streamHost && typeof streamHost.patchMessage === 'function') {
        streamHost.patchMessage(id, { kept: false });
      }
    } catch (e) { /* ignore */ }
  },

  goBack() {
    wx.navigateBack({});
  },
});
