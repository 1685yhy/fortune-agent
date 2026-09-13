// 我的收藏 — 收藏星笺（v1.1）+ 笺匣「笺」分类（Task 10）+ 名笺「名」分类
// 数据源①（后端）：GET /api/favorites 对话收藏（Task 9 收藏后端化，favorites 表，
//         type ∈ chat/jian/qian/ming/lamp；取消收藏走 DELETE /api/favorites）
// 数据源②（后端）：GET /api/ming/saved 名笺收藏（ming_saves 表，isMing 标记，
//         取消收藏走 DELETE /api/ming/delete）
// 数据源③（本地兜底）：ylm_chat_messages / ylm_chat_archives 中
//         role==='ai' && kept===true && !favImported 的回复——首启导入后端前的
//         兼容展示；启动时未导入的 kept 条目调 POST /api/favorites/import 迁移
//         （成功后打 favImported 标记，本地清标记为前端行为，后端 UNIQUE 幂等）。
// 分类：全部 / 笺（type==='jian' 晨笺卡）/ 对话（普通回复）/ 名（AI 取名名笺）
// 交互：点按复制；长按取消收藏（后端条目走 DELETE /api/favorites，本地未导入
//       条目移除 kept 标记；名笺走 DELETE /api/ming/delete）
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const cardUtil = require('../../utils/card');   // B3-24：收藏页展示剥卡片标记

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
    this._tryImportLocal();  // Task 9：启动时把本地 kept 存量导入后端（幂等，失败静默下轮再试）
  },

  _initNavOff() {
    const info = (wx.getWindowInfo && wx.getWindowInfo()) || {};
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 数据装配（三源合并，均按收藏时间倒序）：
     ① 后端对话收藏 GET /api/favorites（isRemote 标记，取消走 DELETE）；
     ② 本地 kept && !favImported 回复（首启导入前的兼容兜底，导入后打标记不再重复展示）；
     ③ 后端名笺 GET /api/ming/saved（isMing，取消走 DELETE /api/ming/delete）。
     任一后端源失败静默降级，不影响其它源展示。 */
  _load() {
    const items = [];
    const now = Date.now();
    const seen = new Set(); // G3 H-3：同 id 双源（storage + 归档）只展示一份
    const push = (m) => {
      if (!m || m.role !== 'ai' || !m.kept || m.favImported) return;
      if (seen.has(m.id)) return;
      seen.add(m.id);
      const jian = isJianEntry(m);
      items.push({
        id: m.id,
        // B3-24：content 是列表/复制出口的展示文本，卡片标记剥掉；
        // 晨笺解析走原始 m.content（晨笺本就无标记，不受影响）
        content: cardUtil.stripCardMarkers(String(m.content || '')),
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

    // ① 对话收藏（后端）：失败静默，本地兜底照常展示；后端条目与本地同 id 去重（后端优先）
    api.favList().then((data) => {
      const remoteItems = [];
      ((data && data.items) || []).forEach((f) => {
        const it = this._remoteItem(f);
        if (it) remoteItems.push(it);
      });
      const remoteRefs = new Set(remoteItems.map((it) => `${it.favType}:${it.favRefId}`));
      // 旧 remote/名笺条目剔除（防重复合并），后端已收且本地同 id → 剔除本地兜底条目
      const merged = this.data.items.filter((it) => {
        if (it.isMing || it.isRemote) return false;
        const localM = this._localMessage(it.id);
        return !(localM && remoteRefs.has(`${this._localType(localM)}:${it.id}`));
      }).concat(remoteItems);
      this._finishMerge(merged);
    }).catch(() => { /* ignore */ });

    // ③ 名笺（后端）：失败静默；合并时先剔除旧名笺防重复
    api.getMingSaved().then((data) => {
      const mingItems = [];
      ((data && data.items) || []).forEach((m) => {
        const it = mingItem(m, now);
        if (it) mingItems.push(it);
      });
      const merged = this.data.items.filter((it) => !it.isMing).concat(mingItems);
      this._finishMerge(merged);
    }).catch(() => { /* ignore */ });
  },

  /* 合并后统一排序 + 收藏时间标签 + 分类过滤 */
  _finishMerge(merged) {
    merged.sort((a, b) => (b.keptAt || 0) - (a.keptAt || 0));
    merged.forEach((it) => {
      it.keptLabel = formatTime(it.keptAt) || '更早';
    });
    this.setData({ items: merged });
    this._applyCat(this.data.cat);
  },

  /* 后端 favorites 条目 → 收藏页条目（isRemote 标记；id 稳定唯一供 wx:key/删除定位） */
  _remoteItem(f) {
    if (!f || !f.type || !f.ref_id) return null;
    const TAG_CN = { chat: '明灯 · 夜话', jian: '晨笺', qian: '灵签', ming: '名笺', lamp: '灯语' };
    let keptAt = 0;
    if (f.created_at) {
      const t = new Date(String(f.created_at).replace(' ', 'T')); // 'YYYY-MM-DD HH:MM:SS' → Date
      if (!isNaN(t.getTime())) keptAt = t.getTime();
    }
    return {
      id: `fav_${f.type}_${f.ref_id}`,
      // B3-24：后端 summary 是导入时的原始内容切片（含 [card:…] 标记）——
      // 展示层剥掉，标记不暴露（存储原样，勿动导入侧）
      content: cardUtil.stripCardMarkers(String(f.summary || '')),
      tag: TAG_CN[f.type] || '明灯 · 夜话',
      time: '',
      keptAt,
      isJian: f.type === 'jian',
      // 远程条目无本地晨笺卡的分行解析结构 → fallback 标记（wxml 分支
      // `!item.jian.fallback` 为假 → 走摘要卡片分支渲染 content=summary）。
      // 注意不可置 null：null.fallback 为 undefined，!undefined=true 会误命中
      // 解析版晨笺卡分支 → 渲染出全空卡片（宜:无/忌:无/无日期）。
      jian: { fallback: true },
      isRemote: true,
      favType: f.type,
      favRefId: f.ref_id,
    };
  },

  /* 本地存量 kept 条目 → 后端导入（Task 9）：type=晨笺判定→jian 否则 chat，
     ref_id=消息 id（稳定且 UNIQUE 可幂等），summary 截 100 字。成功打 favImported
     标记并刷新（本地清标记为前端行为；失败静默，kept 保留下轮再试）。 */
  _tryImportLocal() {
    if (this._importRan) return;
    this._importRan = true;
    const items = [];
    const push = (m) => {
      if (!m || m.role !== 'ai' || !m.kept || m.favImported || !m.id) return;
      items.push({
        type: isJianEntry(m) ? 'jian' : 'chat',
        ref_id: String(m.id).slice(0, 128),
        summary: String(m.content || '').slice(0, 100),
      });
    };
    try {
      (wx.getStorageSync(STORAGE_KEY) || []).forEach(push);
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      (Array.isArray(arch) ? arch : []).forEach((a) => {
        (a && a.messages ? a.messages : []).forEach(push);
      });
    } catch (e) { /* ignore */ }
    if (!items.length) return;
    api.favImport(items).then((res) => {
      const n = res && typeof res.imported === 'number' ? res.imported : 0;
      if (n <= 0) return;   // 0 = 已全部导入过（幂等），非失败不提示
      // 导入成功 → 本地打 favImported 标记（含归档），刷新列表（后端条目接管展示）
      items.forEach((it) => this._markImported(it.ref_id));
      this._load();
    }).catch(() => {
      // G2 B4：导入失败不再静默——本地 kept 保留，重新打开收藏页会再次自动导入
      wx.showToast({ title: '云端同步失败，稍后自动重试', icon: 'none' });
    });
  },

  /* 给本地消息打 favImported 标记（storage 双处 + 宿主内存，仿 _unkeep 写法） */
  _markImported(id) {
    const mark = (list) => {
      if (!Array.isArray(list)) return list;
      return list.map((m) => {
        if (!m || m.id !== id || m.role !== 'ai') return m;
        const copy = Object.assign({}, m);
        copy.favImported = true;
        return copy;
      });
    };
    try {
      wx.setStorageSync(STORAGE_KEY, mark(wx.getStorageSync(STORAGE_KEY)));
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      if (Array.isArray(arch)) {
        wx.setStorageSync(ARCHIVE_KEY, arch.map((a) => {
          if (!a || !a.messages) return a;
          return Object.assign({}, a, { messages: mark(a.messages) });
        }));
      }
    } catch (e) { /* ignore */ }
    try {
      const streamHost = require('../../utils/streamHost');
      if (streamHost && typeof streamHost.patchMessage === 'function') {
        streamHost.patchMessage(id, { favImported: true });
      }
    } catch (e) { /* ignore */ }
  },

  /* 本地消息 id → 本地消息（去重/取消收藏同步本地标记用） */
  _localMessage(id) {
    try {
      const found = (wx.getStorageSync(STORAGE_KEY) || []).find((m) => m && m.id === id);
      if (found) return found;
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      if (Array.isArray(arch)) {
        for (const a of arch) {
          const m = (a && a.messages || []).find((x) => x && x.id === id);
          if (m) return m;
        }
      }
    } catch (e) { /* ignore */ }
    return null;
  },

  /* 本地消息 → 后端 type（晨笺→jian，其余对话→chat） */
  _localType(m) {
    return isJianEntry(m) ? 'jian' : 'chat';
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

  /* 长按 → 取消收藏（后端条目走 DELETE /api/favorites 或 /api/ming/delete；
     本地未导入条目移除 kept 标记，后端条目删除后本地同 id 同步清标记） */
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

    // 后端对话收藏：DELETE /api/favorites（幂等；成功后本地同 id 消息同步移除 kept 标记）
    if (item.isRemote) {
      wx.showModal({
        title: '取消收藏',
        content: '从收藏中移除这条回复？',
        confirmText: '移除',
        confirmColor: '#A93A2C',
        success: (res) => {
          if (!res.confirm) return;
          api.favRemove(item.favType, item.favRefId).then(() => {
            this._unkeep(item.favRefId); // 本地同 id 同步清标记（失败静默，不影响展示）
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

    // 本地未导入条目：移除 kept 标记（原逻辑）
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
