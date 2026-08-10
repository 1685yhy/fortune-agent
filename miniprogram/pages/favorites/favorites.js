// 我的收藏 — 收藏星笺（v1.1）+ 笺匣「笺」分类（Task 10）
// 数据源：本机会话 ylm_chat_messages + 新开对话归档 ylm_chat_archives 中
//         role==='ai' && kept===true 的回复（后端未持久化收藏，feedback 仅 positive/negative）
// 分类：全部 / 笺（type==='jian' 晨笺卡）/ 对话（普通回复）；笺条目以卡片渲染（非气泡）
// 交互：点按复制；长按取消收藏
const theme = require('../../utils/theme');

const STORAGE_KEY = 'ylm_chat_messages';
const ARCHIVE_KEY = 'ylm_chat_archives';

/* 晨笺条目判定（同 chat/history 渲染层过滤口径 + M2 脚本兼容）：type 标记 / 标签 / id 前缀 */
function isJianEntry(m) {
  return !!(m && (m.type === 'jian'
    || (m.tag && String(m.tag).indexOf('晨笺') >= 0)
    || String(m.id || '').indexOf('jian_') === 0));
}

/* 解析 today.js onJianFav 写入的 content 多行文本 → 结构化字段（渲染卡片）。
   content 形如：
     `晨笺 · {干支日期}`
     `宜:{..} 忌:{..}`
     `"{quote}"——《{book}》`（无书名为 `"{quote}"`；quote 为空则整行缺失）
     `{privateLine}`（为空则缺失）
     `今日小问:{question}`
   格式不匹配时返回 null → 走原文气泡兜底，不吞内容。 */
function parseJianContent(content) {
  const lines = String(content || '').split('\n');
  const out = { date: '', yiArr: [], jiArr: [], quote: '', book: '', privateLine: '', question: '' };
  let header = false;
  let quoteLine = '';
  let privateLine = '';
  lines.forEach((raw) => {
    const s = String(raw || '').trim();
    if (!s) return;
    if (s.indexOf('晨笺 · ') === 0) { header = true; out.date = s.slice(5).trim(); return; }
    const yj = s.match(/^宜:(.*?)忌:(.*)$/);
    if (yj) {
      out.yiArr = String(yj[1] || '').split(/\s+/).filter(Boolean);
      out.jiArr = String(yj[2] || '').split(/\s+/).filter(Boolean);
      return;
    }
    if (s.indexOf('今日小问:') === 0) { out.question = s.slice(5).trim(); return; }
    const q = s.match(/^"(.*)"(?:——《(.*)》)?$/);
    if (q) { quoteLine = s; out.quote = q[1]; out.book = q[2] || ''; return; }
    if (!privateLine) privateLine = s;
  });
  if (!header && !quoteLine) return null; // 非晨笺格式（无头/无金句 → 原文气泡兜底）
  out.privateLine = privateLine;
  return out;
}

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* 分类空态文案 */
const EMPTY_TEXT = {
  all: { title: '还没有收藏', sub: '点回复下的 ⭐ 收藏，好话存下来' },
  jian: { title: '笺匣还空着', sub: '今日页晨笺卡点收藏，笺入此匣' },
  chat: { title: '还没有收藏的对话', sub: '点回复下的 ⭐ 收藏，好话存下来' },
};

Page({
  data: {
    navOff: 0,
    items: [],        // 全量（含 jian 解析）
    shown: [],        // 当前分类过滤
    cat: 'all',       // all | jian | chat
    loaded: false,
    dark: false,
    emptyTitle: EMPTY_TEXT.all.title,
    emptySub: EMPTY_TEXT.all.sub,
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

  /* 扫描本机会话 + 归档 → kept 的 AI 回复（按收藏时间倒序），标注晨笺并解析卡片字段 */
  _load() {
    const items = [];
    const now = Date.now();
    const push = (m) => {
      if (!m || m.role !== 'ai' || !m.kept) return;
      const jian = isJianEntry(m);
      items.push({
        id: m.id,
        content: String(m.content || ''),
        tag: m.tag || '明灯 · 夜话',
        time: m.time || '',
        keptAt: m.keptAt || now,
        isJian: jian,
        jian: jian ? (parseJianContent(m.content) || { fallback: true }) : null,
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
    this._applyCat(this.data.cat);
  },

  /* 分类过滤（笺=isJian；对话=非笺；全部=两者） */
  _applyCat(cat) {
    const items = this.data.items;
    const shown = cat === 'jian' ? items.filter((it) => it.isJian)
      : cat === 'chat' ? items.filter((it) => !it.isJian)
      : items;
    const t = EMPTY_TEXT[cat] || EMPTY_TEXT.all;
    this.setData({ shown, cat, emptyTitle: t.title, emptySub: t.sub });
  },

  onCatTap(e) {
    this._applyCat(e.currentTarget.dataset.cat);
  },

  /* 点按 → 复制收藏内容（晨笺卡片同样复制全文） */
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
