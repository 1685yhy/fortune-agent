// 我的收藏 — 收藏星笺（v1.1）+ 笺匣「笺」分类（Task 10）+ 名笺「名」分类
// 数据源①（本地）：本机会话 ylm_chat_messages + 新开对话归档 ylm_chat_archives 中
//         role==='ai' && kept===true 的回复（后端未持久化收藏，feedback 仅 positive/negative）
// 数据源②（后端）：GET /api/ming/saved 名笺收藏（ming_saves 表，isMing 标记，
//         取消收藏走 DELETE /api/ming/delete）
// 分类：全部 / 笺（type==='jian' 晨笺卡）/ 对话（普通回复）/ 名（AI 取名名笺）
// 交互：点按复制；长按取消收藏（名笺走后端删除，本地收藏同步移除 kept 标记）
const api = require('../../utils/api');
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
  ming: { title: '还没有收藏的名笺', sub: '在 AI 取名页收藏一张吧' },
};

/* 名笺复制文本：`名笺 · 张XX（男）` / `五维评分 92 分` / `风格：温润如玉` */
function mingCopy(m) {
  const lines = [`名笺 · ${m.full}（${m.gender || '男'}）`];
  if (typeof m.score === 'number') lines.push(`五维评分 ${m.score} 分`);
  if (m.style_note) lines.push(`风格：${m.style_note}`);
  return lines.join('\n');
}

/* 后端 GET /api/ming/saved 条目 → 收藏页条目（isMing 标记；id 含 saved_at 保证稳定且唯一） */
function mingItem(m, now) {
  if (!m || !m.full) return null;
  const savedAt = m.saved_at ? m.saved_at * 1000 : 0; // UX批4：缺 saved_at → 归「更早」，不再伪造当前时刻
  return {
    id: `ming_${m.surname}_${m.given}_${Math.round(savedAt)}`,
    content: mingCopy(m),
    tag: '名笺',
    time: '',
    keptAt: savedAt,
    isMing: true,
    ming: {
      full: m.full,
      surname: m.surname,
      given: m.given,
      gender: m.gender || '男',
      score: m.score || 0,
      style_note: m.style_note || '',
    },
  };
}

Page({
  data: {
    navOff: 0,
    items: [],        // 全量（含 jian 解析 / isMing 名笺）
    shown: [],        // 当前分类过滤
    cat: 'all',       // all | jian | chat | ming
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

  /* 扫描本机会话 + 归档 → kept 的 AI 回复（按收藏时间倒序），标注晨笺并解析卡片字段；
     名笺收藏走后端（GET /api/ming/saved），并行拉取后合并且按收藏时间倒序 */
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
        keptAt: m.keptAt,
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
      it.keptLabel = formatTime(it.keptAt) || '更早'; // UX批4：缺 keptAt 的旧收藏如实显示「更早」
    });
    this.setData({ items, loaded: true });
    this._applyCat(this.data.cat);

    // 名笺（后端）：失败静默，不影响本地收藏展示；合并时先剔除旧名笺防重复
    api.getMingSaved().then((data) => {
      const mingItems = [];
      ((data && data.items) || []).forEach((m) => {
        const it = mingItem(m, now);
        if (it) mingItems.push(it);
      });
      const merged = this.data.items.filter((it) => !it.isMing).concat(mingItems);
      merged.sort((a, b) => (b.keptAt || 0) - (a.keptAt || 0));
      merged.forEach((it) => {
        it.keptLabel = formatTime(it.keptAt) || '更早';
      });
      this.setData({ items: merged });
      this._applyCat(this.data.cat);
    }).catch(() => { /* ignore */ });
  },

  /* 分类过滤（笺=isJian；对话=非笺非名笺；名=isMing；全部=所有） */
  _applyCat(cat) {
    const items = this.data.items;
    const shown = cat === 'jian' ? items.filter((it) => it.isJian)
      : cat === 'chat' ? items.filter((it) => !it.isJian && !it.isMing)
      : cat === 'ming' ? items.filter((it) => it.isMing)
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

  /* 长按 → 取消收藏（名笺走后端 DELETE /api/ming/delete；本地收藏同步移除 kept 标记） */
  onItemLongPress(e) {
    const { id } = e.currentTarget.dataset;
    const item = (this.data.items || []).find((it) => it.id === id);
    if (!item) return;

    if (item.isMing) {
      wx.showModal({
        title: '取消收藏',
        content: '从收藏中移除这张名笺？',
        confirmText: '移除',
        confirmColor: '#A93A2C',
        success: (res) => {
          if (!res.confirm) return;
          api.deleteMing({
            surname: item.ming.surname,
            given: item.ming.given,
          }).then(() => {
            // 后端已删（deleted=false 视为本就不存在，同样本地移除）
            this.setData({ items: this.data.items.filter((it) => it.id !== id) });
            this._applyCat(this.data.cat);
            wx.showToast({ title: '已移除', icon: 'none' });
          }).catch(() => {
            wx.showToast({ title: '移除失败，请重试', icon: 'none' });
          });
        },
      });
      return;
    }

    wx.showModal({
      title: '取消收藏',
      content: '从收藏中移除这条回复？',
      confirmText: '移除',
      confirmColor: '#A93A2C',
      success: (res) => {
        if (!res.confirm) return;
        const ok = this._unkeep(id);
        this._load();
        wx.showToast({ title: ok ? '已移除' : '移除失败，请重试', icon: 'none' });
      },
    });
  },

  /* UX批4：storage 写失败返回 false（长按移除时如实提示，不再无条件「已移除」） */
  _unkeep(id) {
    let ok = true;
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
    } catch (e) { ok = false; }
    // 宿主内存同步（回到聊天页时现场一致）
    try {
      const streamHost = require('../../utils/streamHost');
      if (streamHost && typeof streamHost.patchMessage === 'function') {
        streamHost.patchMessage(id, { kept: false });
      }
    } catch (e) { /* ignore */ }
    return ok;
  },

  goBack() {
    wx.navigateBack({});
  },
});
