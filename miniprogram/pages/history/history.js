// 对话历史 — 归档夜话（原型 dir_p · 贰：搜索过滤 / 按日归档 / 会话预览 / 删除动画）
// 数据源：新开对话归档 ylm_chat_archives + 当前会话 ylm_chat_messages（未归档的夜话也入列）
// 交互：行点按 → 夜话预览（1:1 原型）；「继续这段夜话」→ 会话写回 ylm_chat_messages + 宿主 → 回聊天页；
//       删除：朱砂确认弹层 → 移除动画 → 空态如纸
const theme = require('../../utils/theme');
const lunar = require('../../utils/lunar');
const streamHost = require('../../utils/streamHost');
const cardUtil = require('../../utils/card');   // B3-24：预览展示剥卡片标记

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

  /* v1.3 我的收藏入口（列表顶部卡片 → 收藏页） */
  goFavorites() {
    wx.navigateTo({ url: '/pages/favorites/favorites' });
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
      /* 第二个参数为未过滤源（含晨笺），仅「继续这段夜话」写回用（M2：写回必须原样，否则抹除收藏） */
      items.push(this._buildEntry(a.id, a.createdAt || now, a.label, msgs, a.messages, false));
    });

    /* 当前会话：有真实用户消息（非 SEED 开场）才入列，作为「今天」最新一段 */
    const hostMsgs = (streamHost.getState() && streamHost.getState().messages) || null;
    const srcCur = (hostMsgs && hostMsgs.length)
      ? hostMsgs
      : (() => { try { return wx.getStorageSync(STORAGE_KEY); } catch (e) { return []; } })();
    const rawCur = Array.isArray(srcCur) ? srcCur : [];
    /* M1 修复：晨笺条目（type==='jian'）不入历史列表/预览（storage 原样保留） */
    const curMsgs = rawCur.filter((m) => !isJianEntry(m));
    const hasRealUser = curMsgs.some(
      (m) => m && m.role === 'user' && String(m.id || '').indexOf('s') !== 0
    );
    if (hasRealUser) {
      items.push(this._buildEntry('current', now, '', curMsgs, rawCur, true));
    }

    /* UX批4 Critical-3：搜索过滤（按摘要+全部消息内容，大小写不敏感）。
       空态可达：q 非空且无命中时 groups 为空 → wxml 展示「没有找到相关的夜话」 */
    const q = String(this.data.q || '').trim().toLowerCase();
    const filtered = q
      ? items.filter((it) => String(it.searchText || '').toLowerCase().indexOf(q) >= 0)
      : items;

    /* 分组（今天/昨天/更早 各按时间倒序） */
    filtered.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
    const groups = ['今天', '昨天', '更早']
      .map((g) => ({ g, items: filtered.filter((it) => it.group === g) }))
      .filter((x) => x.items.length);

    this.setData({ groups, loaded: true });
  },

  /* 归档条目 → 列表行模型（摘要=首条用户问题，时间=末条消息，条数=消息数）。
     rawMsgs = 未过滤源（含晨笺条目，仅供 onContinue 写回 ylm_chat_messages 时原样保留，
     晨笺在列表/预览的展示过滤由 msgs 承担） */
  _buildEntry(id, createdAt, label, msgs, rawMsgs, isCurrent) {
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
      _rawMsgs: (Array.isArray(rawMsgs) && rawMsgs.length) ? rawMsgs : msgs,
    };
  },

  /* 搜索：按摘要 + 全部消息内容过滤（原型 搜索即滤；UX批4 Critical-3 修复：防抖 150ms 后重装配） */
  onSearchInput(e) {
    this.setData({ q: e.detail.value });
    clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => this._load(), 150);
  },
  clearSearch() {
    this.setData({ q: '' });
    clearTimeout(this._searchTimer);
    this._load();
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
      // B3-24：预览为展示出口，卡片标记剥掉（[card:…]/[/card] 不暴露给用户）。
      // 纯展示映射：storage 原消息不落回、续聊写回走 _rawMsgs 不受影响。
      content: cardUtil.stripCardMarkers(String(m.content || '')),
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

  /* 「继续这段夜话」：归档消息写回 ylm_chat_messages + 流式宿主 → 回聊天页继续。
     M2：写回必须用未过滤源 _rawMsgs（含晨笺条目）——若写回展示层过滤后的 _msgs，
     晨笺收藏会在 storage/宿主里被永久抹除 */
  onContinue() {
    const s = this.data.current;
    if (!s) return;
    let msgs = [];
    const all = [];
    this.data.groups.forEach((grp) => all.push(...grp.items));
    const entry = all.find((it) => it.id === s.id);
    if (entry) msgs = (entry._rawMsgs || entry._msgs || []).map((m) => {
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
    /* UX批4 Important-5：续聊成功后清理源归档——同一批消息（同 id）不再二次归档，
       否则新开对话再归档会产生重复会话，收藏页同 id kept 消息出现双卡片。 */
    if (s.id && s.id !== 'current') {
      try {
        const arch = wx.getStorageSync(ARCHIVE_KEY);
        if (Array.isArray(arch)) {
          wx.setStorageSync(ARCHIVE_KEY, arch.filter((a) => !(a && a.id === s.id)));
        }
      } catch (e) { /* ignore */ }
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
