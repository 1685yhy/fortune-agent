// 今日 — 笺谱封面（原型 TodayScreen：date/brand/poem/yi-chips/talk-btn/foot + TabBar；
// v2026-08-17 随手截屏入口已移除）
const api = require('../../utils/api');
const theme = require('../../utils/theme');
// v2026-08-17：shareCard 依赖已移除（随手截屏入口 onShot 删除，drawInkCard
// 仍被 ming 页使用——utils/shareCard.js 勿删）
const lunar = require('../../utils/lunar');
const streamHost = require('../../utils/streamHost'); // 收藏同步宿主用（见 _syncHostJian）
const persons = require('../../utils/persons'); // 命主档案本地判定（未设置提示条用）

/* 收藏复用现有收藏机制：ylm_chat_messages 中 role==='ai' && kept===true（favorites 页数据源）。
   type:'jian' 为笺匣分类标记（Task 10 收藏页「笺」分类）。 */
const MSG_KEY = 'ylm_chat_messages';

/* 原型文案兜底（dir_b.html 755-797 行） */
const DEFAULT_POEM = ['雾散灯明处', '恰是归程时。'];
const DEFAULT_CHIPS = ['宜 · 安顿心事', '宜 · 早眠'];

/* 详情页数据透传 key（今日详解页读取；缺省时详情页自拉 API） */
const DETAIL_KEY = 'ylm_today_detail';

/* 流日四运四维元数据（key ↔ 展示名，顺序固定） */
const F4_META = [
  { key: 'career', label: '事业' },
  { key: 'wealth', label: '财运' },
  { key: 'love', label: '感情' },
  { key: 'health', label: '健康' },
];

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

/* 当前时辰序号：子时=0 .. 亥时=11（子时跨 23-01，取 23 点后归子时） */
function shichenNowIdx() {
  const h = new Date().getHours();
  if (h === 23) return 0;
  return Math.floor((h + 1) / 2);
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
    topPad: 88,              // 顶部安全区：状态栏 + 微信胶囊避让（真机胶囊遮挡日期行修复；88≈通用机兜底，onLoad 用胶囊实测覆盖）
    dateText: '八月六日',
    solarHint: '明日立秋 · 今夜宜早眠',
    poemLines: DEFAULT_POEM,
    yiChips: DEFAULT_CHIPS,
    curTab: 'today',
    dark: false,
    /* 深夜入口横幅：mode=深夜时段内(21:00 后,白天不出现)；banner=入口开关 */
    night: { mode: false, banner: true },
    /* 未设置命主档案 → 通用运势提示条（true 时显示「去设置」跳档案页） */
    archiveHint: false,
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
    /* 流日四运卡：fortune4=[{key,label,scoreText,percent,desc}]（点开今日详解） */
    fortune4: [],
    /* 时辰择时行：当前时辰起 3 个时辰 chip [{time,tag,desc,cur}]（点开今日详解） */
    shichen: [],
    /* 今日详解页透传数据（宜忌/四运/时辰） */
    yiDetail: [],
    jiDetail: [],
    suitable: [],
    unsuitable: [],
    detailDate: '',
    detailGanzhi: '',
    detailScore: '',
    detailFortune4: null,
    hourlyRaw: [],
  },

  onLoad() {
    this._initNavOff();
    this._initDate();
    this._loadFortune();
    this._loadJian();
    this._loadNight();
    theme.bindTheme(this);
  },

  /* 每次回页面刷新深夜态（21:00 后出现横幅，白天不出现） */
  onShow() {
    this._loadNight();
  },

  /* 状态栏高度适配：原型画板固定状态栏 47px，--nav-off 为差值。
     顶部安全区 topPad（真机胶囊遮挡修复）：胶囊底部 + 12px 留白，整页内容下移避让
     微信胶囊按钮区域；getMenuButtonBoundingClientRect 不可用时兜底
     状态栏 + 44px（≈胶囊高 32 + 边距 12） */
  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const sb = info.statusBarHeight || 47;
    const off = sb - 47;
    let pad = sb + 44;
    try {
      const cap = wx.getMenuButtonBoundingClientRect();
      if (cap && cap.bottom) pad = cap.bottom + 12;
    } catch (e) { /* 兜底值已设 */ }
    const patch = { topPad: pad };
    if (off !== 0) patch.navOff = off;
    this.setData(patch);
  },

  /* 日期：左阳历中文数字（八月六日），右节气提示（明日立秋 · 今夜宜早眠）；
     整行可点 → 今日详解页（原型「流日行」入口） */
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

  /* 后端数据绑定（api.js 契约）：suitable → 两宜标签；personal_advice → 诗签两行；
     fortune4/hourly/yi_detail → 流日四运卡 + 时辰择时行 + 今日详解页透传数据 */
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
      /* 无命主档案 → 后端返回通用日历（personal_advice 以「请先设置八字信息」标识）。
         诗签不再展示这段说明文字（回退原型诗签），改为提示条 + 「去设置」按钮跳档案页；
         本地已建档（后端缓存未刷新）时不再提示 */
      const needsArchive = (res.personal_advice || '').includes('请先设置八字信息')
        && !persons.hasLocalArchive();
      this.setData({
        yiChips: chips.length >= 2 ? chips : DEFAULT_CHIPS,
        poemLines: needsArchive ? DEFAULT_POEM.slice() : splitPoem(res.personal_advice || res.advice || ''),
        archiveHint: needsArchive,
      });

      /* ── 流日四运（0-10 分，进度条 = score×10%） ── */
      const f4 = (res && res.fortune4) || null;
      const fortune4 = F4_META
        .filter((m) => f4 && f4[m.key])
        .map((m) => {
          const it = f4[m.key];
          const sc = Number(it.score) || 0;
          return {
            key: m.key,
            label: m.label,
            scoreText: sc.toFixed(1),
            percent: Math.max(0, Math.min(100, sc * 10)),
            desc: it.desc || '',
          };
        });

      /* ── 时辰择时：当前时辰起 3 个 chip（原型 巳时/午时/申时 式样） ── */
      const hourlyRaw = Array.isArray(res.hourly) ? res.hourly : [];
      const cur = shichenNowIdx();
      const shichen = [];
      for (let i = 0; i < 3 && hourlyRaw.length; i++) {
        const it = hourlyRaw[(cur + i) % hourlyRaw.length];
        if (it) shichen.push({ time: it.time || '', tag: it.tag || '', desc: it.desc || '', cur: i === 0 });
      }

      /* ── 宜忌详解（三宜二忌逐条 action/time/reason） ── */
      const yiDetail = Array.isArray(res.yi_detail) ? res.yi_detail.filter((d) => d && d.action) : [];
      const jiDetail = Array.isArray(res.ji_detail) ? res.ji_detail.filter((d) => d && d.action) : [];

      this.setData({
        fortune4,
        shichen,
        yiDetail,
        jiDetail,
        suitable,
        unsuitable: Array.isArray(res.unsuitable) ? res.unsuitable.filter(Boolean) : [],
        detailDate: res.date || '',
        detailGanzhi: res.day_ganzhi || '',
        detailScore: res.score || '',
        detailFortune4: f4,
        hourlyRaw,
      });
    } catch (e) {
      console.warn('[Today] API 不可用，保持原型文案');
    }
  },

  /* ═══ 深夜入口（方案·灯下漫谈）：21:00 后横幅出现，白天不出现 ═══ */

  async _loadNight() {
    let preset = 'standard';
    try {
      const cached = wx.getStorageSync('ylm_night_prefs');
      if (cached && cached.preset) preset = cached.preset;
      const res = await api.getNightPrefs();
      const p = (res && res.prefs) || {};
      if (p.preset) { preset = p.preset; wx.setStorageSync('ylm_night_prefs', p); }
    } catch (e) { /* 缓存兜底,静默 */ }
    const mode = require('../../utils/nightMode').isNightMode(preset);
    if (mode !== this.data.night.mode) this.setData({ 'night.mode': mode });
  },

  /* 深夜入口：进入深夜对话页(以入口为准,强制 deepNight) */
  onNightEntry() {
    try {
      const app = getApp();
      if (app && app.globalData) app.globalData.deepNight = true;
    } catch (e) { /* ignore */ }
    wx.reLaunch({ url: '/pages/chat/chat?entry=night' });
  },

  /* v2026-08-17：随手截屏入口（onShot）已移除（PM：真机与下方元素视觉重叠，
     做不好就去掉）。绘制能力保留：utils/shareCard.js drawInkCard 仍被
     ming 页（AI取名）使用，勿删；今日页 #inkCard 画布保留（无害）。 */

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

  /* ═══ 今日详解（原型 v7 反馈#2）：4 路入口（四运卡/时辰行/宜忌区/流日行） ═══
     已加载数据经 storage 透传；详情页缺省时自拉 /api/calendar/today */
  goTodayDetail() {
    try {
      wx.setStorageSync(DETAIL_KEY, {
        date: this.data.detailDate,
        dayGanzhi: this.data.detailGanzhi,
        score: this.data.detailScore,
        suitable: this.data.suitable,
        unsuitable: this.data.unsuitable,
        yiDetail: this.data.yiDetail,
        jiDetail: this.data.jiDetail,
        fortune4: this.data.detailFortune4,
        hourly: this.data.hourlyRaw,
        needsArchive: this.data.archiveHint, // 通用运势标记 → 详解页提示条
      });
    } catch (e) { /* 存储失败不阻断跳转（详情页自拉 API） */ }
    wx.navigateTo({ url: '/pages/today_detail/today_detail' });
  },

  /* 未设置命主信息提示条 → 档案页（添加命主） */
  onGoArchive() {
    wx.navigateTo({ url: '/pages/persons/persons', fail: () => {} });
  },

  /* 原型 onTalk：进入夜话 */
  goChat() {
    wx.reLaunch({ url: '/pages/chat/chat' });
  },

  /* 原型 TabBar onTab */
  onTab(e) {
    const t = e.currentTarget.dataset.tab;
    const url = { chat: '/pages/chat/chat', today: '/pages/today/today', suance: '/pages/celiang/celiang', me: '/pages/me/me' }[t];
    if (url && !url.includes('/today/')) wx.reLaunch({ url });
  },
});
