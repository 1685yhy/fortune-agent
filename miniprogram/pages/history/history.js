// 对话历史 — 归档夜话（原型 dir_p · 贰：搜索过滤 / 按日归档 / 会话预览 / 删除动画）
// 数据源：新开对话归档 ylm_chat_archives + 当前会话 ylm_chat_messages（未归档的夜话也入列）
// 交互：行点按 → 夜话预览（1:1 原型）；「继续这段夜话」→ 会话写回 ylm_chat_messages + 宿主 → 回聊天页；
//       删除：朱砂确认弹层 → 移除动画 → 空态如纸
const theme = require('../../utils/theme');
const lunar = require('../../utils/lunar');
const streamHost = require('../../utils/streamHost');

const STORAGE_KEY = 'ylm_chat_messages';
const ARCHIVE_KEY = 'ylm_chat_archives';
const PREVIEW_MAX = 8;        // 预览最多渲染条数，超出显示「示 意 中 途」

/* 晨笺收藏条目（today.js onJianFav 写入 type:'jian'）不属于夜话，
   历史列表/预览/续聊一律排除（storage 原样保留，favorites 笺匣仍展示） */
function isJianEntry(m) {
  return !!(m && m.type === 'jian');
}

/* 印章字候选：从摘要里挑一个字作朱砂印（原型各会话的 桥/运/宅/婚/海/水），无命中取首字 */
const SEAL_HINTS = ['桥', '水', '海', '蛇', '宅', '婚', '考', '梦', '运', '命', '钱', '财', '人', '家', '路', '灯', '夜', '心', '病', '雨', '雪', '山', '井', '房', '车', '门', '窗', '树', '花', '猫', '狗', '哭', '飞', '血'];
function sealCharFor(text) {
  const t = String(text || '');
  for (let i = 0; i < SEAL_HINTS.length; i++) {
    if (t.indexOf(SEAL_HINTS[i]) >= 0) return SEAL_HINTS[i];
  }
  return t.trim().charAt(0) || '梦';
}

/* 归档时间 → 今天/昨天/更早（原型日期分组） */
function dayGroup(ts) {
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = Number(ts) || 0;
  if (!t) return '更早';
  if (t >= startToday) return '今天';
  if (t >= startToday - 86400000) return '昨天';
  return '更早';
}

/* 会话时间（归档无 time 字段时用归档时刻补） */
function timeText(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* 农历日期文案（预览页头「八月初六 · 夜」），失败回退公历 */
function lunarDayText(ts) {
  const d = new Date(ts || Date.now());
  try {
    return lunar.formatLunarDate(d.getFullYear(), d.getMonth() + 1, d.getDate());
  } catch (e) {
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }
}

Page({
  data: {
    navOff: 0,
    dark: false,
    q: '',                 // 搜索词
    groups: [],            // [{g:'今天', items:[{id,seal,summary,time,count}]}]
    loaded: false,
    /* 预览视图（页内切换，原型 list ↔ preview） */
    view: 'list',
    current: null,         // {id, seal, summary, count, lunar, msgs:[{role,tag,content,time}]}
    truncated: false,
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
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  goBack() {
    if (this.data.view === 'preview') {
      this.setData({ view: 'list', current: null });
      return;
    }
    wx.navigateBack({ delta: 1, fail: () => wx.reLaunch({ url: '/pages/me/me' }) });
  },

  /* ═══ 数据装配：归档（新开对话产生）+ 当前会话（含真实对话但未归档） ═══ */

  _load() {
    const items = [];
    const now = Date.now();

    /* 归档夜话 */
    let arch = [];
    try {
      arch = wx.getStorageSync(ARCHIVE_KEY);
    } catch (e) { /* ignore */ }
    (Array.isArray(arch) ? arch : []).forEach((a) => {
      if (!a || !a.messages || !a.messages.length) return;
      const msgs = (Array.isArray(a.messages) ? a.messages : []).filter((m) => !isJianEntry(m));
      if (!msgs.length) return;
      items.push(this._buildEntry(a.id, a.createdAt || now, a.label, msgs, false));
    });

    /* 当前会话：有真实用户消息（非 SEED 开场）才入列，作为「今天」最新一段 */
    const hostMsgs = (streamHost.getState() && streamHost.getState().messages) || null;
    let curMsgs = (hostMsgs && hostMsgs.length)
      ? hostMsgs
      : (() => { try { return wx.getStorageSync(STORAGE_KEY); } catch (e) { return []; } })();
    /* M1 修复：晨笺条目（type==='jian'）不入历史列表/预览（storage 原样保留） */
    curMsgs = (Array.isArray(curMsgs) ? curMsgs : []).filter((m) => !isJianEntry(m));
    const hasRealUser = (Array.isArray(curMsgs) ? curMsgs : []).some(
      (m) => m && m.role === 'user' && String(m.id || '').indexOf('s') !== 0
    );
    if (hasRealUser) {
      items.push(this._buildEntry('current', now, '', curMsgs, true));
    }

    /* 分组（今天/昨天/更早 各按时间倒序） */
    items.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
    const groups = ['今天', '昨天', '更早']
      .map((g) => ({ g, items: items.filter((it) => it.group === g) }))
      .filter((x) => x.items.length);

    this.setData({ groups, loaded: true });
  },

  /* 归档条目 → 列表行模型（摘要=首条用户问题，时间=末条消息，条数=消息数） */
  _buildEntry(id, createdAt, label, msgs, isCurrent) {
    const firstUser = (msgs || []).find((m) => m.role === 'user' && !m.pending);
    const summary = String((firstUser && firstUser.content) || label || '一段夜话').trim();
    const lastMsg = msgs[msgs.length - 1];
    const time = (lastMsg && lastMsg.time) ? lastMsg.time : timeText(createdAt);
    const searchText = summary + ' ' + (msgs || []).map((m) => String(m.content || '')).join(' ');
    return {
      id,
      createdAt,
      group: dayGroup(createdAt),
      summary,
      seal: sealCharFor(summary),
      time,
      count: msgs.length,
      isCurrent,
      searchText,
      _msgs: msgs,
    };
  },

  /* 搜索：按摘要 + 全部消息内容过滤（原型 搜索即滤） */
  onSearchInput(e) {
    this.setData({ q: e.detail.value });
  },
  clearSearch() {
    this.setData({ q: '' });
  },

  /* ═══ 会话预览（原型 preview 屏） ═══ */

  onRowTap(e) {
    const id = e.currentTarget.dataset.id;
    const all = [];
    this.data.groups.forEach((grp) => all.push(...grp.items));
    const s = all.find((it) => it.id === id);
    if (!s) return;
    const msgs = (s._msgs || []).map((m) => ({
      role: m.role,
      tag: m.tag || '',
      content: String(m.content || ''),
      time: m.time || '',
    }));
    const truncated = msgs.length > PREVIEW_MAX;
    this.setData({
      view: 'preview',
      current: {
        id: s.id,
        seal: s.seal,
        summary: s.summary,
        count: s.count,
        isCurrent: s.isCurrent,
        lunar: lunarDayText(s.createdAt),
        msgs: msgs.slice(0, PREVIEW_MAX),
        truncated,
      },
    });
  },

  /* 「继续这段夜话」：归档消息写回 ylm_chat_messages + 流式宿主 → 回聊天页继续 */
  onContinue() {
    const s = this.data.current;
    if (!s) return;
    let msgs = [];
    const all = [];
    this.data.groups.forEach((grp) => all.push(...grp.items));
    const entry = all.find((it) => it.id === s.id);
    if (entry) msgs = (entry._msgs || []).map((m) => {
      const copy = Object.assign({}, m);
      delete copy.mdNodes;
      delete copy.segments;
      return copy;
    });
    if (msgs.length) {
      if (streamHost.streaming) streamHost.stop();
      streamHost.setMessages(msgs.slice(-50));
      try { wx.setStorageSync(STORAGE_KEY, msgs.slice(-50)); } catch (e) { /* ignore */ }
    }
    wx.navigateTo({ url: '/pages/chat/chat' });
  },

  /* ═══ 删除：确认弹层 → 移除动画 → 重新装配 ═══ */

  askDel(e) {
    const { id } = e.currentTarget.dataset;
    const all = [];
    this.data.groups.forEach((grp) => all.push(...grp.items));
    const s = all.find((it) => it.id === id);
    if (!s) return;
    this.setData({
      dlgDel: {
        id: s.id,
        isCurrent: s.isCurrent,
        brief: s.summary.length > 12 ? s.summary.slice(0, 12) + '…' : s.summary,
      },
    });
  },

  closeDlgDel() {
    this.setData({ dlgDel: null });
  },

  confirmDel() {
    const d = this.data.dlgDel;
    if (!d) return;
    this.setData({ dlgDel: null, removing: d.id });
    setTimeout(() => {
      if (d.isCurrent) {
        /* 删除当前会话：清空消息 → 聊天页回 SEED 开场 */
        try { wx.setStorageSync(STORAGE_KEY, []); } catch (e) { /* ignore */ }
        if (streamHost.streaming) streamHost.stop();
        streamHost.setMessages([]);
      } else {
        try {
          const arch = wx.getStorageSync(ARCHIVE_KEY);
          if (Array.isArray(arch)) {
            wx.setStorageSync(ARCHIVE_KEY, arch.filter((a) => a && a.id !== d.id));
          }
        } catch (e) { /* ignore */ }
      }
      this.setData({ removing: '', view: 'list', current: null });
      this._load();
      wx.showToast({ title: '已删除 · 夜话不留痕', icon: 'none' });
    }, 340);
  },

  noop() { /* 弹层内吞掉背景滚动/穿透 */ },
});
