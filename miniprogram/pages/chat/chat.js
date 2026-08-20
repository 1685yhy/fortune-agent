// 夜话 — 笺注对话（原型 ChatScreen：SEED 开场 / 明灯笺注卡 / 反馈 up·down·keep / 快捷笺 / 研墨中）
// 语音交互（元宝式）：输入条左侧麦克风切换「按住说话」模式（WechatSI 插件，未配置优雅降级）
//                 + AI 回复朗读（/api/tts）
// v1.1：流式生成放全局宿主（utils/streamHost）→ 切 tab 不中断；排队消息立即上屏（pending）；
//       语音/键盘模式切换；新开对话（归档 ylm_chat_archives）；气泡长按操作菜单（点赞/点踩/收藏/
//       复制/选取文字/朗读/分享/意见反馈/删除）。
// v1.2（气泡内容升级）：Markdown 完整渲染（utils/md 自写轻量解析器，代码块可复制）；
//       建议卡片（后端 done 事件 suggestions → 白卡朱砂描边，点击直接发送）；
//       表情反应（气泡尾部 ＋/长按菜单 → emoji 选择，本地 storage 持久化，可追加/移除）；
//       图片消息预留（msg.image: {url,width?,height?} → 圆角墨框渲染分支）。
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const streamHost = require('../../utils/streamHost');
const md = require('../../utils/md');

/* 原型 aiComplete 精选文案（dir_b.html 532-537 行 CURATED，后端不可用时兜底） */
const CURATED = {
  '解梦': { tag: '解梦 · 夜记', body: '梦是心在夜里悄悄记账。醒来时记下的第一个念头，往往比梦本身更真。若愿意，把那念头留着，白天回头看，自会有答案。' },
  '今日运势': { tag: '今日 · 日运', body: '今日宜静不宜争。午前把要紧事说完，午后留一杯茶、一段路给自己。傍晚若有人约，允了也无妨——贵人常藏在最不设防的闲谈里。' },
  '本周': { tag: '周运 · 观星', body: '这一周，风会往你想去的方向吹。周二与周五有两件小事值得放在心上：一是旧友的来信，二是一笔迟到的回音。' },
  default: { tag: '明灯 · 夜话', body: '这句话，我收下了。夜里想不明白的事，都可以在这里慢慢理。明灯只做一件事：陪你想清楚，不替你做决定。' },
};
function curatedFor(prompt) {
  const hit = Object.keys(CURATED).find((k) => prompt.includes(k));
  return CURATED[hit] || CURATED.default;
}

/* v2.0 元宝式空会话引导：SEED 开场三笺已移除——新开/首访不再自动出现演示对话，
   改为空消息 + 引导区（品牌 + 示例问题 + 快捷入口，见 chat.wxml .guide）。
   历史续读逻辑不受影响：有历史/现场 → 显示历史；无消息 → 空 + 引导区。 */

const STORAGE_KEY = 'ylm_chat_messages';
const ARCHIVE_KEY = 'ylm_chat_archives';
const REACT_KEY = 'ylm_reactions';   // v1.2 表情反应：{消息id: [emoji...]}

/* 晨笺收藏条目（today.js onJianFav 写入 type:'jian'）不是夜话，聊天/历史渲染一律排除；
   仅渲染层过滤，storage 原样保留（favorites 笺匣仍展示） */
function isJianEntry(m) {
  return !!(m && m.type === 'jian');
}

/* Task 8 对话内引导卡：识别 AI 回复行内的页面路径 → 气泡末尾渲染跳转按钮
   （当前入口为 /pages/hehun/hehun，按钮文案随路径区分；无路径保持纯文本渲染不破坏现有气泡） */
const NAV_PATH_RE = /\/pages\/[a-z_]+\/[a-z_]+/;
function navFor(content) {
  const c = String(content || '');
  const m = NAV_PATH_RE.exec(c);
  if (!m) return null;
  const path = m[0];
  return {
    path,
    label: path.indexOf('/hehun/') !== -1 ? '进入双人合盘 →' : '进入页面 →',
  };
}

/* v1.2 表情反应可选集（8 个常用） */
const EMOJIS = ['👍', '❤️', '😂', '😮', '😢', '🔥', '🙏', '✨'];

/* 语音输入参数（元宝式） */
const REC_MIN_MS = 800;   // 短按 < 800ms → 「说话时间太短」
const REC_MAX_S = 60;     // 最长录音 60s，到点自动发送
const SWIPE_CANCEL_PX = 80; // 上滑 80px 进入「松开 取消」
const WAVE_BAR_COUNT = 26;

/* v8 阶段 3·过程体验（流式打字机）：滚动节流（生成推进由宿主 tick 驱动） */
const SCROLL_MS = 100;      // 自动滚动节流

/* ═══ Task 5 滚动不拽回：距底阈值与可视区高度测量 ═══
   NEAR_BOTTOM_PX 为阈值兜底 50px；实际阈值 this._nearBottomPx 在 onLoad 按屏宽缩放：
   100rpx = 屏宽/750*100 px（设计稿 750rpx 宽，1rpx = 屏宽/750；375px 宽屏 = 50px 与原值
   一致，414px 宽屏 ≈ 55px）。语义：距底部还剩约 100rpx 内容未显示即视为「在底部」。 */
const NEAR_BOTTOM_PX = 50;        // 兜底阈值：50px（375px 宽屏的 100rpx），旧基础库无
                                  // getWindowInfo 时退回该值
const CLIENTH_MEASURE_MS = 1500;  // 可视区高度周期校准间隔：键盘弹起等布局变化会让 msg-list
                                  // 高度改变，滚动中每 ~1.5s 重测一次防阈值失真

Page({
  data: {
    navOff: 0,
    showBack: false,          // 导航栈进入（历史/解梦 navigateTo）→ 显示返回箭头；tab 主屏隐藏
    messages: [],
    showGuide: true,          // v2.0 空会话引导（元宝式）：无消息时显示品牌+示例问题+快捷入口
    /* v1.3 多选收藏/分享：长按菜单「多选」进入勾选模式（勾选框 + 顶部操作条） */
    multiMode: false,
    multiSel: {},             // {消息id: true}
    multiCount: 0,
    multiAll: false,
    typing: false,
    inputText: '',
    inputFocused: false,
    inputFocus: false,         // 一次性聚焦开关（今日小问预填触发输入框聚焦）
    fb: {},
    scrollInto: '',
    curTab: 'chat',
    dark: false,
    /* ═══ Task 8 · 深夜模式（方案·灯下漫谈） ═══ */
    nightMode: false,          // 深夜模式(夜色主题+语气)
    nightHeadShow: true,       // 深夜提示条可见（2026-08-17 PM：可关闭/自动消失）
    lampLit: false,            // 灯笼动效本轮是否已播
    lamp: { show: false, date: '', text: '', audioUrl: '', favorited: false,
            timerMin: 15, playing: false },
    keepBar: { show: false, text: '不用急着回。我就在这,灯给你留着。' },
    sleepBar: false,
    rememberPrompt: { show: false, msgId: '', userText: '' },
    safetyCard: { show: false, text: '' },
    notKeep: false,            // 倾诉临时模式提示条(默认不记录)
    /* v8 阶段 3·过程体验（流式） */
    streaming: false,       // 当前有回复正在生成（发送钮 → 停止钮）
    slowHint: false,        // 2026-08-18 生成慢提示：15s 无可见内容 → 输入区上方浅色小字
    /* v1.1 语音/键盘模式切换（元宝式） */
    inputMode: 'text',      // 'text' | 'voice'
    /* 语音输入（元宝式长按） */
    isRecording: false,
    recSeconds: 0,
    recCanceling: false,
    converting: false,
    waveBars: [],
    /* 语音播报 */
    speakingId: '',
    /* 阶段 5·引用交互：底部抽屉（半屏↔全屏） */
    citeDrawer: { show: false, full: false, msgId: '', items: [] },
    /* v1.1 气泡长按操作菜单（墨韵弹层） */
    actionMenu: { show: false, msgId: '', role: '' },
    fbMenu: { show: false, msgId: '' },   // 意见反馈原因弹层
    selectMsgId: '',                      // 选取模式：该气泡 text 动态加 selectable
    /* v1.2 表情反应：{消息id: [emoji...]} 持久化 + 选择弹层 */
    reactions: {},
    EMOJIS,
    emojiSheet: { show: false, msgId: '', cur: [], curMap: {} },
    /* 对话建档提示条：AI 回复含建档 key（已保存到档案/建档…）→ 顶部提示 */
    saveBanner: false,
    /* L5-1/L5-2 对话额度条：免费用户「今日 X/15」；超限降级 → 精简提示 + 会员引导 */
    quotaBar: { show: false, text: '', downgraded: false },
  },

  onLoad(options) {
    /* ═══ Task 5 滚动不拽回·阈值按屏宽缩放：屏宽运行期不变，onLoad 算一次。
       换算 100rpx = 屏宽/750*100 px（375px 屏 = 50px 与原常量一致；414px 屏 ≈ 55px）。
       旧基础库无 wx.getWindowInfo → getSystemInfoSync 兜底 → 仍无则 50px 常量兜底。 */
    const win = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    this._nearBottomPx = win.windowWidth ? win.windowWidth / 750 * 100 : NEAR_BOTTOM_PX;
    /* ═══ Task 8 · 深夜模式进入（夜色主题/灯笼/挽留劝睡/灯语卡/要我记得吗/12356） ═══ */
    options = options || {};
    const app = getApp();
    const forceNight = options.entry === 'night'
      || (app && app.globalData && app.globalData.deepNight);
    if (app && app.globalData) app.globalData.deepNight = false; // 消费即清
    this._nightPreset = 'standard';
    try {
      const cached = wx.getStorageSync('ylm_night_prefs');
      if (cached && cached.preset) this._nightPreset = cached.preset;
    } catch (e) { /* ignore */ }
    const nightMode = require('../../utils/nightMode');
    this.data.nightMode = forceNight || nightMode.isNightMode(this._nightPreset);
    streamHost.setDeepNight(this.data.nightMode);
    /* ═══ 会话隔离：恢复/生成会话标识（ylm_session_id）→ 请求随附 →
       后端 AI 上下文只取本会话消息（新开对话 = 全新 session_id = 全新上下文） ═══ */
    this._loadSessionId();
    if (this.data.nightMode) this._enterNight();
    this._loadNightPrefs();           // 异步拉取档位/动效/挽留并缓存
    this._loadReactions();
    this._initNavOff();
    this._initNavDepth();     // 返回箭头仅导航栈进入（历史/解梦 navigateTo）时显示
    this._attachHost();
    this._loadHistory();
    /* L5-1/L5-2 对话额度展示：免费用户「今日 X/15」；超限降级提示 + 会员引导 */
    this._refreshQuota();
    /* 断点续传：本地历史恢复后 → 服务端 pending 补全（生成中退出/切走 →
       服务端继续生成完，下次进入自动补全看到结果；只查当前会话，静默追加） */
    this._loadPendingOffline();
    /* 今日小问预填（PM 2026-08-17：不自动发送——问题填进输入框由用户编辑/发送） */
    try {
      const prefill = (options.question || '').trim();
      if (prefill) this._prefillQuestion(prefill);
    } catch (e) { /* ignore */ }
    theme.bindTheme(this);
    // 真机保护：语音/音频初始化失败不阻塞页面（各自再兜一层 try/catch）
    try { this._initSpeech(); } catch (e) { console.warn('[Chat] 语音初始化失败:', e); }
    try { this._initAudio(); } catch (e) { console.warn('[Chat] 音频初始化失败:', e); }
  },

  /* 阶段 5：从引用详情页返回 → 抽屉状态保留（半屏/全屏 + 打开的列表）；
     v1.1：回到页面 → 滚到最新（不要停留在旧位置） */
  onShow() {
    if (this._drawerRestore && this.data.citeDrawer.show) {
      const { msgId, full } = this._drawerRestore;
      const msg = this._findMessage(msgId);
      if (msg) {
        this.setData({
          citeDrawer: {
            show: true,
            full: !!full,
            msgId,
            items: msg.citations || [],
          },
        });
      }
      this._drawerRestore = null;
    }
    this._scrollBottom(true);
  },

  /* v1.1：切走不中断流式生成（宿主在全局继续，页面销毁也不 abort）；仅取消进行中的录音 */
  onHide() {
    this._cleanupVoice();
  },

  /* v1.1：页面销毁 → 流式生成继续（切 tab 不中断）；仅解挂监听与本地资源 */
  onUnload() {
    if (this._unsubHost) {
      this._unsubHost();
      this._unsubHost = null;
    }
    this._cleanupVoice();
    if (this._audioCtx) {
      this._audioCtx.destroy();
      this._audioCtx = null;
    }
    /* Task 8 深夜：清定时器与灯语音频，退出深夜态 */
    if (this._keepTimer) { clearTimeout(this._keepTimer); this._keepTimer = null; }
    if (this._lampTimer) { clearTimeout(this._lampTimer); this._lampTimer = null; }
    this._clearNightHeadTimer();
    if (this._lampAudio) { try { this._lampAudio.destroy(); } catch (e) { /* ignore */ } this._lampAudio = null; }
    streamHost.setDeepNight(false);
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* ════════════════════════════════════════════════════════════
     Task 8 · 深夜模式（方案·灯下漫谈）
     夜色主题/灯笼动效/灯语卡/挽留劝睡/要我记得吗/12356 安全条
     ════════════════════════════════════════════════════════════ */

  /* 进入深夜模式：夜色主题 + 灯笼动效(每日一次,可关) + 临时倾诉提示 */
  _enterNight() {
    this.setData({ nightMode: true, nightHeadShow: true, notKeep: true });
    streamHost.setDeepNight(true);
    try { wx.setNavigationBarColor({ frontColor: '#000000', backgroundColor: '#F4EBD6' }); } catch (e) {}
    try { wx.setBackgroundColor({ backgroundColor: '#F4EBD6' }); } catch (e) {}
    /* 2026-08-17 PM：深夜提示条不常驻——3.5s 自动淡出消失（也可点 ✕ 关闭） */
    this._armNightHeadAutoHide();
    const nightMode = require('../../utils/nightMode');
    const h = nightMode.bjHour(Date.now());
    if (h >= 23 || h === 0) this._loadLamp();        // 23:00-01:00 灯语卡
    if (h >= 0 && h < 4) this.setData({ sleepBar: true });  // 0 点后劝睡
    if (!this.data.lampLit && this._effectEnabled() && wx.getStorageSync('ylm_lamp_lit_date') !== this._bjDate()) {
      this.setData({ lampLit: true });
      wx.setStorageSync('ylm_lamp_lit_date', this._bjDate());
    }
    this._armKeepTimer();                             // 挽留定时器
  },

  /* 深夜提示条自动消失（3.5s；点 ✕ 手动关闭走 dismissNightHead） */
  _armNightHeadAutoHide() {
    this._clearNightHeadTimer();
    this._nightHeadTimer = setTimeout(() => {
      this._nightHeadTimer = null;
      this.setData({ nightHeadShow: false });
    }, 3500);
  },

  _clearNightHeadTimer() {
    if (this._nightHeadTimer) {
      clearTimeout(this._nightHeadTimer);
      this._nightHeadTimer = null;
    }
  },

  /* 深夜提示条手动关闭（✕） */
  dismissNightHead() {
    this._clearNightHeadTimer();
    this.setData({ nightHeadShow: false });
  },

  _bjDate() { return new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10); },
  _effectEnabled() {
    try { const p = wx.getStorageSync('ylm_night_prefs'); return !p || p.effect_enabled !== 0; } catch (e) { return true; }
  },

  async _loadNightPrefs() {
    try {
      const res = await api.getNightPrefs();
      const p = (res && res.prefs) || {};
      wx.setStorageSync('ylm_night_prefs', p);
      if (!this.data.nightMode && p.preset) {
        this.data.nightMode = require('../../utils/nightMode').isNightMode(p.preset);
        if (this.data.nightMode) this._enterNight();
      }
    } catch (e) { /* 静默 */ }
  },

  /* 每发一条消息 → 守夜人成就登记（深夜模式内） */
  _nightTouch() {
    if (!this.data.nightMode) return;
    try { require('../../utils/nightWatch').touch(Date.now()); } catch (e) { /* ignore */ }
  },

  /* ═══ 枕边灯语卡（23:00-01:00） ═══ */

  async _loadLamp() {
    try {
      const res = await api.getLampToday();
      const l = (res && res.lamp) || {};
      if (!l || !l.text) return;
      const p = wx.getStorageSync('ylm_night_prefs') || {};
      this.setData({
        'lamp.show': true, 'lamp.date': l.date, 'lamp.text': l.text,
        'lamp.audioUrl': l.audio_url || '',
        'lamp.favorited': !!l.favorited,
        'lamp.timerMin': p.lamp_timer_min || 15,
      });
    } catch (e) { /* 静默 */ }
  },

  onLampPlay() {
    if (!this.data.lamp.audioUrl) { wx.showToast({ title: '语音版为会员权益', icon: 'none' }); return; }
    if (!this._lampAudio) {
      this._lampAudio = wx.createInnerAudioContext();
      this._lampAudio.onError(() => this.setData({ 'lamp.playing': false }));
      this._lampAudio.onEnded(() => this.setData({ 'lamp.playing': false }));
    }
    const a = this._lampAudio;
    // 终审:切换灯语日/首次点击才换源,保持当前进度;点「暂停」真正暂停不再重播
    if (!a.src || a.src !== this.data.lamp.audioUrl) a.src = this.data.lamp.audioUrl;
    if (this.data.lamp.playing) {
      a.pause();
      if (this._lampTimer) { clearTimeout(this._lampTimer); this._lampTimer = null; }
      this.setData({ 'lamp.playing': false });
      return;
    }
    a.play();
    this.setData({ 'lamp.playing': true });
    if (this._lampTimer) clearTimeout(this._lampTimer);
    this._lampTimer = setTimeout(() => { a.stop(); this.setData({ 'lamp.playing': false }); },
      this.data.lamp.timerMin * 60 * 1000);   // 定时关闭(默认 15 分钟)
  },

  onLampTimerChange(e) {
    this.setData({ 'lamp.timerMin': Number(e.detail.value) });
    if (this._lampAudio && this.data.lamp.playing) { /* 重新计时 */
      if (this._lampTimer) clearTimeout(this._lampTimer);
      this._lampTimer = setTimeout(() => { this._lampAudio.stop(); this.setData({ 'lamp.playing': false }); },
        this.data.lamp.timerMin * 60 * 1000);
    }
  },

  async onLampFav() {
    try {
      const res = await api.favLamp(this.data.lamp.date);
      this.setData({ 'lamp.favorited': !!res.favorited });
      wx.showToast({ title: res.favorited ? '已收藏 · 入笺匣' : '已取消收藏', icon: 'none' });
    } catch (e) { wx.showToast({ title: '操作失败', icon: 'none' }); }
  },

  /* ═══ 挽留条（静默 25-40 分钟，1 次/夜） ═══ */

  _armKeepTimer() {
    if (this._keepTimer) clearTimeout(this._keepTimer);
    if (this.data.sleepBar) return;
    if (wx.getStorageSync('ylm_keep_date') === this._bjDate()) return;
    const p = wx.getStorageSync('ylm_night_prefs') || {};
    if (p.keep_enabled === 0) return;
    const waitMs = (25 + Math.floor(Math.random() * 16)) * 60 * 1000;  // 25-40 分钟
    this._keepTimer = setTimeout(() => {
      this.setData({ 'keepBar.show': true });
      wx.setStorageSync('ylm_keep_date', this._bjDate());
    }, waitMs);
  },

  /* ═══ 「要我记得吗」（流式 done 后扫描回复；每夜一次） ═══ */

  _scanRemember(replyText) {
    if (this.data.nightMode && /要记住|要我记|帮我记住/.test(replyText || '')
        && wx.getStorageSync('ylm_remember_date') !== this._bjDate()) {
      const host = require('../../utils/streamHost');
      const msgs = (host.getState && host.getState().messages) || [];
      const lastUser = msgs.slice().reverse().find((m) => m && m.role === 'user');
      this.setData({ rememberPrompt: { show: true, msgId: '', userText: (lastUser && lastUser.content) || '' } });
    }
  },

  async onRememberYes() {
    const t = this.data.rememberPrompt.userText;
    this.setData({ rememberPrompt: { show: false, msgId: '', userText: '' } });
    wx.setStorageSync('ylm_remember_date', this._bjDate());
    if (!t) return;
    try {
      const res = await api.rememberNight(t);
      wx.showToast({ title: res.remembered ? '已记下 · 仅今晚有效' : '今晚已经记过啦', icon: 'none' });
    } catch (e) { wx.showToast({ title: '记录失败', icon: 'none' }); }
  },

  onRememberNo() {
    this.setData({ rememberPrompt: { show: false, msgId: '', userText: '' } });
    wx.setStorageSync('ylm_remember_date', this._bjDate());
  },

  /* ═══ 12356 安全条（输入与回复双向检测） ═══ */

  _checkSafety(text) {
    if (!text) return false;
    if (/自杀|自伤|轻生|不想活|活不下去|想死|结束生命/.test(text)) {
      this.setData({ safetyCard: {
        show: true,
        text: '我听到你了。请先拨打心理援助热线 12356(24 小时),白天我会陪你联系专业人士。你很重要。',
      } });
      return true;
    }
    return false;
  },

  /* ═══ v1.1 全局流式宿主接线（切 tab 对话不中断） ═══ */

  /* ═══ 晨笺「今日小问」预填（2026-08-17 PM：不自动发送——从今日页晨笺卡跳入
     时问题经 URL 参数（?question=）传入，只填充输入框 + 聚焦，由用户编辑/发送） ═══ */
  _prefillQuestion(q) {
    const text = String(q || '').trim();
    if (!text) return;
    this.setData({ inputText: text });
    // 触发输入框聚焦（focus 一次性置真，blur 时复位以便下次再触）
    this.setData({ inputFocus: true });
    setTimeout(() => { try { this.setData({ inputFocus: false }); } catch (e) { /* ignore */ } }, 600);
  },

  _attachHost() {
    this._lastTick = -1;
    this._segCache = null;
    this._unsubHost = streamHost.subscribe((state) => this._onHostState(state));
  },

  /* ── L5-1/L5-2 对话额度条：GET /api/user/chat-quota ──
     会员/体验模式：不展示；免费用户：展示「今日 X/15」；
     超限（downgraded）：展示「今日额度已用尽，已为你精简回复」+ 会员开通入口 */
  _refreshQuota() {
    api.getChatQuota()
      .then((q) => {
        if (!q || q.is_member || q.limit == null) {
          if (this.data.quotaBar.show) this.setData({ quotaBar: { show: false, text: '', downgraded: false } });
          return;
        }
        const used = Math.min(q.used || 0, q.limit);
        const left = Math.max(0, q.limit - used);
        this.setData({
          quotaBar: {
            show: true,
            downgraded: !!q.downgraded,
            text: q.downgraded
              ? '今日额度已用尽 · 已为你精简回复'
              : `今日 ${left}/${q.limit} 条`,
          },
        });
      })
      .catch(() => { /* 额度查询失败：静默隐藏（不打扰对话） */ });
  },

  /* 降级引导 → 我的页会员入口（开通会员解锁完整版） */
  goMember() {
    wx.reLaunch({ url: '/pages/me/me' });
  },

  /* 宿主状态 → 页面镜像（segments 由页面重算，引用分段渲染在页面侧） */
  _onHostState(state) {
    if (state.notice) {
      wx.showToast({ title: state.notice, icon: 'none' });
      streamHost.clearNotice();
    }
    /* 2026-08-18 生成慢提示：tick 未变（无新内容）也要消费 slowHint 状态 */
    if (this.data.slowHint !== !!state.slowHint) {
      this.setData({ slowHint: !!state.slowHint });
    }
    if (this._lastTick === state.tick) return; // tick 未变：仅提示类事件
    this._lastTick = state.tick;
    // 流式结束且本轮有新内容 → 检查回复是否含建档标记（最小实现：含 persons 相关 key 即提示）
    if (this._prevStreaming && !state.streaming) {
      /* L5-1/L5-2：一轮对话完成 → 后端已消费额度，刷新额度条（免费超限转降级提示） */
      this._refreshQuota();
      this._checkArchiveKeys(state.messages || []);
      // 深夜：回复扫描「要我记得吗」触发点 + 回复侧 12356 安全检测
      const msgs = state.messages || [];
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'ai' && !last.error) {
        const replyText = String(last.content || '');
        this._scanRemember(replyText);
        this._checkSafety(replyText);
      }
    }
    this._prevStreaming = !!state.streaming;
    const mirrored = this._mirror(state.messages || []);
    this.setData({
      messages: mirrored,
      showGuide: mirrored.length === 0,   // v2.0：消息出现即隐藏引导区
      streaming: !!state.streaming,
      typing: !!state.typing,
    });
    if (state.autoScroll) this._scrollBottomIfNear();
  },

  /* 对话建档提示：AI 回复含「已保存到档案/建档」类 key → 顶部提示条 + 本地标记
     （排盘选命主页读同一标记展示确认弹层；点提示条确认/取消后清除） */
  _checkArchiveKeys(messages) {
    const ARCHIVE_KEYS = ['已保存到档案', '已加入档案', '档案已建立', '已建立档案', '出生信息已保存', '已存到档案'];
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'ai' || last.error) return;
    const text = String(last.content || '');
    if (!text || this.data.saveBanner) return;
    if (ARCHIVE_KEYS.some((k) => text.indexOf(k) !== -1)) {
      this.setData({ saveBanner: true });
      try {
        wx.setStorageSync('ylm_dlg_person_saved', { t: Date.now(), text: '' });
      } catch (e) { /* ignore */ }
    }
  },

  /* 提示条点击：确认/取消建档弹层（清除标记与提示条） */
  onSaveBannerTap() {
    wx.showModal({
      title: '对话建档提示',
      content: '对话中已识别到出生信息\n是否将其保存到档案？',
      confirmText: '确认保存',
      cancelText: '取消',
      confirmColor: '#A93A2C',
      success: (res) => {
        this.setData({ saveBanner: false });
        try { wx.removeStorageSync('ylm_dlg_person_saved'); } catch (e) { /* ignore */ }
        if (res.confirm) {
          wx.showToast({ title: '已保存到档案', icon: 'none' });
        } else {
          wx.showToast({ title: '未保存', icon: 'none' });
        }
      },
    });
  },

  dismissSaveBanner() {
    this.setData({ saveBanner: false });
    try { wx.removeStorageSync('ylm_dlg_person_saved'); } catch (e) { /* ignore */ }
  },

  /* v1.2 表情反应持久化：{消息id: [emoji...]} → data.reactions */
  _loadReactions() {
    let map = {};
    try {
      const saved = wx.getStorageSync(REACT_KEY);
      if (saved && typeof saved === 'object') map = saved;
    } catch (e) { /* ignore */ }
    this.setData({ reactions: map });
  },

  /* 消息镜像：仅对内容变化的消息重算 Markdown 节点树（流式时缓存命中不重算）；
     同时把本地表情反应合并进镜像（reactions）。
     M2：晨笺收藏条目(type==='jian')在此渲染层排除（host/storage 原样保留——streamHost._save
     会把 host.messages 原样写回 ylm_chat_messages，若在存储层过滤，任何一次保存都会
     永久抹除收藏的晨笺；favorites 笺匣仍展示）
     v2026-08-17（元宝「深度思考」胶囊版）：派生 thinkDone/thinkDoing/thinkLabel/
     thinkSeconds/thinkCollapsed——流式中展开分步列表逐条累积（可见推进），完成后
     自动收起为「深度思考完成 · 用时 Xs」（streamHost._onDone 置 thinkCollapsed 与
     thinkSeconds；镜像必须显式派生 thinkCollapsed，否则 wxml 收起判断恒 false）。 */
  /* 思考区派生视图：数组语义来自 streamHost（旧步→done，新步→doing）；
     thinkLabel 状态文案：深度思考中 / 深度思考完成 / 思考中断 / 思考过程；
     thinkSeconds = 后端生成全程秒数（streamHost._onDone 计算落盘） */
  _thinkView(m) {
    const arr = Array.isArray(m.thinking) ? m.thinking : [];
    let done = 0;
    let doing = '';
    for (let i = 0; i < arr.length; i++) {
      const s = arr[i];
      if (s && s.state === 'done') done++;
      else if (s && s.state === 'doing' && !doing) doing = s.text || '';
    }
    let label = '思考中';
    if (m.streaming) {
      label = '深度思考中';
    } else if (m.error) {
      label = '思考中断';
    } else if (done > 0) {
      // 完成态（含异常/回退成功路径）：不残留「思考中」
      label = m.consultationId ? '深度思考完成' : '思考过程';
    }
    return {
      thinkDone: done,
      thinkDoing: doing,
      thinkLabel: label,
      thinkSeconds: Number(m.thinkSeconds) || 0,
      // 完成态收起标记（streamHost._onDone/_onAbort/回退成功路径置 thinkCollapsed）：
      // 宿主字段必须显式派生到渲染项，否则 wxml item.thinkCollapsed 恒为 false，
      // 思考过程完成后永远展开不收起（真机反馈 #1）
      thinkCollapsed: !!m.thinkCollapsed,
    };
  },

  _mirror(messages) {
    const vis = (Array.isArray(messages) ? messages : []).filter((m) => !isJianEntry(m));
    const out = new Array(vis.length);
    const reactions = this.data.reactions || {};
    for (let i = 0; i < vis.length; i++) {
      const m = vis[i];
      const tv = this._thinkView(m);
      const c = String(m.content || '');
      const cached = this._segCache;
      if (cached && cached.id === m.id && cached.content === c) {
        out[i] = Object.assign({}, m, {
          mdNodes: cached.mdNodes,
          reactions: reactions[m.id] || [],
          navPath: cached.navPath,
          navLabel: cached.navLabel,
          thinkDone: tv.thinkDone,
          thinkDoing: tv.thinkDoing,
          thinkLabel: tv.thinkLabel,
          thinkSeconds: tv.thinkSeconds,
          thinkCollapsed: tv.thinkCollapsed,
        });
        continue;
      }
      const mdNodes = md.parseMd(c);
      const nav = (m.role === 'ai' && !m.error) ? navFor(c) : null;
      this._segCache = { id: m.id, content: c, mdNodes, navPath: nav && nav.path, navLabel: nav && nav.label };
      out[i] = Object.assign({}, m, {
        mdNodes,
        reactions: reactions[m.id] || [],
        navPath: nav && nav.path,
        navLabel: nav && nav.label,
        thinkDone: tv.thinkDone,
        thinkDoing: tv.thinkDoing,
        thinkLabel: tv.thinkLabel,
        thinkSeconds: tv.thinkSeconds,
        thinkCollapsed: tv.thinkCollapsed,
      });
    }
    return out;
  },

  /* 历史续读：宿主现场优先（可能后台生成中/刚完成）；无则 storage；再无则空会话引导区
     （v2.0：不再回 SEED 开场——渲染空列表 → 引导区显示）。
     M2：晨笺条目(type==='jian')不在此过滤——host 必须保留它，否则 streamHost._save()
     会把过滤后的数组写回 storage，永久抹除收藏条目；渲染层(_mirror)才做排除。
     只剩晨笺条目时渲染为空 → 显示引导区（晨笺仍留在 host，favorites 笺匣不受影响） */
  _loadHistory() {
    const hostState = streamHost.getState();
    let messages = null;
    if (hostState.messages && hostState.messages.length) {
      messages = hostState.messages;
    } else {
      let saved = null;
      try {
        saved = wx.getStorageSync(STORAGE_KEY);
      } catch (e) {
        console.warn('[Chat] 历史读取失败');
      }
      if (Array.isArray(saved) && saved.length) messages = saved;
    }
    if (!messages || !messages.length) messages = [];
    streamHost.setMessages(messages);
    const mirrored = this._mirror(messages);
    this.setData({
      messages: mirrored,
      showGuide: mirrored.length === 0,   // v2.0：无消息 → 元宝式空会话引导区
      streaming: !!hostState.streaming,
      typing: !!hostState.typing,
      slowHint: !!hostState.slowHint,     // 2026-08-18 生成慢提示现场恢复
    });
    this._lastTick = hostState.tick;
    this._prevStreaming = !!hostState.streaming;
    if (hostState.notice) {
      wx.showToast({ title: hostState.notice, icon: 'none' });
      streamHost.clearNotice();
    }
    this._recoverInterrupted();
    this._scrollBottom(true);
  },

  /* ═══ 断点续传：退出/切走后生成不中断，下次进入自动补全 ═══
     时序：_loadHistory（本地历史）→ _loadPendingOffline（服务端 pending 补全）。
     只查当前 session_id（新开会话后端无数据，天然空）；静默追加不打断、
     不弹 toast；追加后按服务端最新时间调 consume 标记已消费（幂等）。 */
  async _loadPendingOffline() {
    const sid = streamHost.sessionId;
    if (!sid) return;
    if (streamHost.active) return;  // 宿主仍在生成中：等本次流式结束后自然落库/补全
    let res = null;
    try {
      res = await api.chatPending(sid);
    } catch (e) {
      return;  // 网络失败静默降级（不影响历史展示）
    }
    const items = (res && res.items) || [];
    if (!items.length) return;
    const added = streamHost.appendOfflineMessages(items);
    if (added > 0) {
      // 追加完成 → 滚动到底（自动滚动遵守「上滑不拽回」规则）
      this._scrollBottom(true);
    }
    // 消费标记：以补全最新一条的服务端时间为截止（所有已返回的补全一次消费）
    const last = items[0] && items[0].time;
    if (last) {
      api.consumePending(sid, last).catch(() => {});
    }
  },

  /* v1.1 中断恢复：返回时检测「发送中但无响应」状态 → 自动重连/重试 */
  _recoverInterrupted() {
    if (streamHost.active) return; // 宿主仍在生成，无需恢复
    const msgs = streamHost.messages || [];
    const stuck = msgs.find((m) => m.role === 'ai' && m.streaming);
    if (stuck) {
      const text = stuck.retryText || '';
      // 标记失败态（保留已输出）
      streamHost.patchMessage(stuck.id, {
        streaming: false,
        error: true,
        thinking: (stuck.thinking || []).map((s) => ({ text: s.text, state: 'done' })),
      });
      if (!String(stuck.content || '').trim() && text.trim()) {
        // 无任何输出 → 自动重连重试（重发原文）
        wx.showToast({ title: '上次回复被中断，正在重新生成', icon: 'none' });
        streamHost.retry(stuck.id, text, curatedFor(text).tag);
      } else {
        wx.showToast({ title: '上次回复被中断，可点「重试」', icon: 'none' });
      }
    }
    // 残留的排队消息（pending）→ 重新入队，依次处理
    streamHost.requeuePending((t) => curatedFor(t).tag);
  },

  /* ═══ Task 5 · 滚动不拽回（上滑查看历史时不被流式自动滚打扰） ═══
     scroll-view（chat.wxml）bindscroll 记录用户滚动位置（_scrollTop/_scrollHeight）；
     自动滚前先做距底判断：距底 > 阈值 → 视为上滑查看 → 跳过 _scrollBottom（不打扰）；
     回到距底 ≤ 阈值 → 恢复自动跟随；流结束（done）同规则：本就在底部则停在底部，
     自行上滑过则不再拽回（流结束不强制滚）。 */

  /* scroll-view 滚动事件：只记录位置与内容总高（WXML bindscroll 每帧触发，不做重活） */
  onScroll(e) {
    const d = e.detail || {};
    if (typeof d.scrollTop === 'number') this._scrollTop = d.scrollTop;
    if (typeof d.scrollHeight === 'number') this._scrollHeight = d.scrollHeight;
    this._ensureClientH();   // 滚动期间周期校准可视区高度（键盘等布局变化）
  },

  /* 距底判断（纯函数，便于自查；阈值走 this._nearBottomPx，onLoad 按屏宽缩放）：
     距离 = scrollHeight - scrollTop - clientHeight = 距底部还剩多少内容未显示。
     距离 ≤ this._nearBottomPx（≈100rpx，屏宽缩放后 50~55px 级）→ 在底部，允许自动跟随；
     距离 > 阈值 → 用户上滑查看中，跳过自动滚。
     高度未知（未测量/无滚动事件/首帧）→ 保守返回 true，维持原有跟随行为。 */
  _isNearBottom(scrollTop, scrollHeight, clientHeight) {
    if (typeof scrollTop !== 'number' || typeof scrollHeight !== 'number') return true;
    if (!clientHeight) return true;
    return scrollHeight - scrollTop - clientHeight <= (this._nearBottomPx || NEAR_BOTTOM_PX);
  },

  /* 自动滚前先问「是否在底部」：上滑查看历史期间，宿主每 50ms 的 autoScroll
     事件不再把用户拽回底部；回到底部后恢复自动跟随 */
  _scrollBottomIfNear() {
    this._ensureClientH();
    if (this._isNearBottom(this._scrollTop, this._scrollHeight, this._clientH)) {
      this._scrollBottom();
    }
  },

  /* msg-list 可视区高度（px）：首次测量后缓存，滚动中按 CLIENTH_MEASURE_MS
     周期校准；测量失败 → 保持未知（_isNearBottom 保守跟随），下次再试 */
  _ensureClientH() {
    const now = Date.now();
    if (this._clientH && this._clientHAt && now - this._clientHAt < CLIENTH_MEASURE_MS) return;
    try {
      this.createSelectorQuery()  // 页面作用域（文档推荐；wx.* 不带 .in(this) 在自定义组件里会挂）
        .select('.msg-list')
        .boundingClientRect((rect) => {
          const h = rect && rect.height;
          if (h) { this._clientH = h; this._clientHAt = now; }
        }).exec();
    } catch (e) { /* 静默：未知高度按保守跟随处理 */ }
  },

  _scrollBottom(force) {
    const now = Date.now();
    if (!force) {
      if (this._lastScrollAt && now - this._lastScrollAt < SCROLL_MS) return;
      this._lastScrollAt = now;
    }
    this._lastScrollAt = now;
    if (force) {
      // 强制：先复位再滚，保证 scroll-into-view 变化触发滚动
      this.setData({ scrollInto: '' }, () => {
        this.setData({ scrollInto: 'btm' });
      });
    } else {
      this.setData({ scrollInto: 'btm' });
    }
  },

  /* 返回箭头：仅导航栈进入（历史/解梦 navigateTo）时显示；tab 主屏隐藏（原型 4-tab 常驻无返回） */
  _initNavDepth() {
    const pages = getCurrentPages();
    this.setData({ showBack: !!(pages && pages.length > 1) });
  },
  goBack() {
    const pages = getCurrentPages();
    if (pages && pages.length > 1) wx.navigateBack();
  },

  /* v1.3：临时对话（⊕）入口已删除——该入口独占的 tempChat 状态与 toggleTempChat 一并移除。
     说明：⊕ 临时对话原本复用 deepNight（后端临时不落记忆）；深夜模式（灯下漫谈）由
     夜间时段自动/晚安推送 entry='night' 独立进入（_enterNight → streamHost.setDeepNight(true)），
     后端 deepNight 能力保留、仅收回前端入口，深夜功能不受影响。 */

  /* 原型 onTab：底部栏切换 */
  onTab(e) {
    const t = e.currentTarget.dataset.tab;
    const url = { chat: '/pages/chat/chat', today: '/pages/today/today', suance: '/pages/celiang/celiang', me: '/pages/me/me' }[t];
    if (url && !url.includes('/chat/')) wx.reLaunch({ url });
  },

  /* 原型 quickchip：点标签即问（生成中再点 = 排队，消息立即上屏） */
  quickAsk(e) {
    const text = (e.currentTarget.dataset.text || '').trim();
    if (!text) return;
    this._send(text);
  },

  /* v2026-08-17：引导区快捷入口（onGuideNav）已移除——引导区仅保留 4 条示例
     问题（PM 反馈），今日运势/深夜灯语/双人合盘/择吉日入口排整体删除；
     quickAsk 保留（引导区示例问题使用） */

  onInput(e) {
    this.setData({ inputText: e.detail.value });
  },
  onFocus() { this.setData({ inputFocused: true }); },
  onBlur() { this.setData({ inputFocused: false }); },
  onConfirm() { this._send(this.data.inputText); },

  /* 发送入口（v8 阶段 3 + v1.1）：生成中允许再发 → 排队且消息立即上屏（pending 灰态） */
  async sendMessage() {
    if (this.data.isRecording || this.data.converting) return;
    this._send(this.data.inputText);
  },

  _send(text) {
    const r = streamHost.send(text, curatedFor(text).tag);
    if (r === 'queued') {
      wx.showToast({ title: '已排队，等我说完就回你', icon: 'none', duration: 1200 });
    }
    if (r !== 'empty') {
      this.setData({ inputText: '' });
      this._nightTouch();          // 深夜：每发一条登记守夜人
      this._checkSafety(text);     // 深夜：自伤关键词 → 12356 安全条
    }
  },

  /* 停止生成（发送钮变停止钮） */
  stopStream() {
    streamHost.stop();
  },

  /* 重试：丢弃失败气泡，用原消息重发 */
  retryStream(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const text = (e.currentTarget.dataset.text || '').trim();
    if (!text || streamHost.active) return;
    const id = e.currentTarget.dataset.id;
    streamHost.retry(id, text, curatedFor(text).tag);
  },

  /* 思考路径折叠/展开（宿主持久化） */
  toggleThink(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const id = e.currentTarget.dataset.id;
    const msg = this._findMessage(id);
    if (!msg || !msg.thinking || !msg.thinking.length) return;
    streamHost.patchMessage(id, { thinkCollapsed: !msg.thinkCollapsed });
  },

  /* ═══ 阶段 5·引用交互（元宝式角标）：浅色链接符号 → 抽屉 → 详情页 ═══ */

  noop() {},  // 抽屉内 catchtouchmove 吞掉背景滚动

  citeTypeLabel(type) {
    const map = { book: '📖 古籍', engine: '⚙ 引擎', memory: '🧠 记忆', web: '🌐 网络' };
    return map[type] || '📖 古籍';
  },

  /* 点角标 [n]/🔗 → 打开底部抽屉（该条回复的来源列表） */
  onCiteTap(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const { msgid, idx } = e.currentTarget.dataset;
    const msg = this._findMessage(msgid);
    const items = (msg && Array.isArray(msg.citations)) ? msg.citations : [];
    if (!items.length) {
      wx.showToast({ title: '本条回复暂无参考资料', icon: 'none' });
      return;
    }
    this._drawerRestore = null;
    this.setData({ citeDrawer: { show: true, full: false, msgId: msgid, items } });
  },

  closeCiteDrawer() {
    this.setData({ citeDrawer: { show: false, full: false, msgId: '', items: [] } });
    this._drawerRestore = null;
  },

  /* 半屏 ↔ 全屏（点把手/全屏按钮切换） */
  toggleCiteFull() {
    const d = this.data.citeDrawer;
    this.setData({ citeDrawer: Object.assign({}, d, { full: !d.full }) });
  },

  /* 抽屉条目 → navigateTo 详情页（返回后抽屉状态保留） */
  onCiteItemTap(e) {
    const i = e.currentTarget.dataset.i;
    const d = this.data.citeDrawer;
    const item = d.items[i];
    if (!item) return;
    try {
      wx.setStorageSync('ylm_cite_' + d.msgId + '_' + i, item);
    } catch (err) {
      console.warn('[Chat] 引用详情存储失败:', err);
    }
    // 记录抽屉状态 → onShow 恢复（半屏/全屏 + 列表）
    this._drawerRestore = { msgId: d.msgId, full: d.full };
    wx.navigateTo({
      url: '/pages/citation/citation?msgId=' + d.msgId + '&idx=' + i,
    });
  },

  /* 抽屉把手拖动：上滑全屏 / 下滑半屏 */
  cdTouchStart(e) {
    const t = (e.touches && e.touches[0]) || {};
    this._cdTouchY = t.clientY || 0;
  },
  cdTouchMove(e) {
    const t = (e.touches && e.touches[0]) || {};
    const dy = (t.clientY || 0) - (this._cdTouchY || 0);
    const d = this.data.citeDrawer;
    if (Math.abs(dy) > 30 && d.full !== (dy < 0)) {
      this.setData({ citeDrawer: Object.assign({}, d, { full: dy < 0 }) });
    }
  },
  cdTouchEnd() {
    this._cdTouchY = null;
  },

  /* ═══ v1.1 气泡长按操作菜单（墨韵弹层） ═══ */

  /* v1.3 多选模式：气泡点按 = 勾选/取消勾选（其他气泡内交互一律不响应） */
  onMsgTap(e) {
    if (!this.data.multiMode) return;
    const id = e.currentTarget.dataset.id;
    if (!id) return;
    this._toggleMulti(id);
  },

  /* 长按气泡 → 操作菜单（选取模式中不弹菜单，提示长按文字选取；多选模式不弹菜单） */
  onBubbleLongPress(e) {
    const { id, role } = e.currentTarget.dataset;
    if (this.data.multiMode) return;   // v1.3 多选：长按不弹菜单，避免与勾选混淆
    if (this.data.selectMsgId) {
      wx.showToast({ title: '长按文字即可选取', icon: 'none' });
      return;
    }
    try { wx.vibrateShort({}); } catch (err) { /* 模拟器无振动能力，静默 */ }
    const msg = this._findMessage(id);
    // 分享目标在开菜单时锁定（分享按钮 open-type=share 会在菜单关闭后读取）
    this._shareTarget = msg ? String(msg.content || '') : '';
    this.setData({ actionMenu: { show: true, msgId: id, role, kept: !!(msg && msg.kept) } });
  },

  closeActionMenu() {
    this.setData({ actionMenu: { show: false, msgId: '', role: '' } });
  },

  /* 消息区点击：选取模式自动退出（多选模式不退出——勾选由气泡点按负责） */
  onListTap() {
    if (this.data.multiMode) return;
    if (this.data.selectMsgId) this.setData({ selectMsgId: '' });
  },

  /* 菜单普通项：复制 / 选取文字 / 朗读 / 意见反馈 / 删除 */
  actItem(e) {
    const k = e.currentTarget.dataset.k;
    const { msgId } = this.data.actionMenu;
    const msg = this._findMessage(msgId);
    this.closeActionMenu();
    if (!msg) return;
    if (k === 'select') {
      // 选取模式：该气泡 text 动态加 selectable，长按文字出系统选择手柄
      this.setData({ selectMsgId: msgId });
      wx.showToast({ title: '长按文字即可选取', icon: 'none', duration: 2000 });
    } else if (k === 'copy') {
      wx.setClipboardData({ data: msg.content || '' });
    } else if (k === 'emoji') {
      // v1.2 表情反应：打开 emoji 选择弹层
      this._openEmojiFor(msgId);
    } else if (k === 'multi') {
      // v1.3 多选：进入勾选模式（批量收藏 / 分享）
      this._enterMulti();
      return;   // 已由 _enterMulti 关闭菜单
    } else if (k === 'speak') {
      this._playWithTts(msgId, msg.content || '', true);
    } else if (k === 'feedback') {
      this.setData({ fbMenu: { show: true, msgId } });
    } else if (k === 'delete') {
      wx.showModal({
        title: '删除此条',
        content: '删除后不可恢复，确定删除这条' + (msg.role === 'ai' ? '回复' : '消息') + '吗？',
        confirmText: '删除',
        confirmColor: '#A93A2C',
        success: (r) => {
          if (!r.confirm) return;
          // 清理该消息的页内反馈态
          const fb = {};
          Object.keys(this.data.fb || {}).forEach((key) => {
            if (key.indexOf(msgId + '-') !== 0) fb[key] = this.data.fb[key];
          });
          this.setData({ fb });
          // v1.2：表情反应一并清理（storage 持久化）
          this._clearReactions(msgId);
          streamHost.removeMessage(msgId);
        },
      });
    }
  },

  /* 菜单反馈项：点赞/点踩（复用反馈回路）/收藏（持久化 kept） */
  actFeedback(e) {
    const k = e.currentTarget.dataset.k;
    const { msgId } = this.data.actionMenu;
    this.closeActionMenu();
    if (k === 'keep') {
      const msg = this._findMessage(msgId);
      if (!msg) return;
      const on = !msg.kept;
      streamHost.patchMessage(msgId, { kept: on, keptAt: on ? Date.now() : 0 });
      wx.showToast({ title: on ? '已收藏 · 我的页可查看' : '已取消收藏', icon: 'none' });
      return;
    }
    this._toggleFbCore(msgId, k);
  },

  /* ═══ v1.3 多选收藏 / 分享（长按菜单「多选」→ 勾选模式 → 批量收藏 / 分享页） ═══ */

  /* 进入勾选模式：顶部出现操作条（已选 N 条/全选/收藏/分享/取消），气泡左上角出勾选框 */
  _enterMulti() {
    this.setData({
      multiMode: true,
      multiSel: {},
      multiCount: 0,
      multiAll: false,
      selectMsgId: '',
      actionMenu: { show: false, msgId: '', role: '' },
    });
  },

  exitMulti() {
    if (!this.data.multiMode) return;
    this.setData({ multiMode: false, multiSel: {}, multiCount: 0, multiAll: false });
  },

  /* 勾选/取消一条消息 */
  _toggleMulti(id) {
    const sel = Object.assign({}, this.data.multiSel);
    if (sel[id]) delete sel[id]; else sel[id] = true;
    const count = Object.keys(sel).length;
    this.setData({
      multiSel: sel,
      multiCount: count,
      multiAll: count > 0 && count === (this.data.messages || []).length,
    });
  },

  multiSelectAll() {
    const sel = {};
    if (!this.data.multiAll) {
      (this.data.messages || []).forEach((m) => { sel[m.id] = true; });
    }
    const count = Object.keys(sel).length;
    this.setData({ multiSel: sel, multiCount: count, multiAll: count > 0 });
  },

  /* 批量收藏：选中的 AI 回复置 kept=true（复用单条收藏标记：patchMessage 落盘，
     favorites 页读 ylm_chat_messages/归档的 kept）；用户消息不可收藏 */
  multiFav() {
    const sel = this.data.multiSel;
    if (!Object.keys(sel).length) {
      wx.showToast({ title: '先勾选几条再收藏', icon: 'none' });
      return;
    }
    const aiIds = (this.data.messages || [])
      .filter((m) => m.role === 'ai' && sel[m.id])
      .map((m) => m.id);
    if (!aiIds.length) {
      wx.showToast({ title: '只能收藏明灯的回复', icon: 'none' });
      return;
    }
    aiIds.forEach((id) => {
      const msg = this._findMessage(id);
      if (msg && !msg.kept) {
        streamHost.patchMessage(id, { kept: true, keptAt: Date.now() });
      }
    });
    this.exitMulti();
    wx.showToast({ title: `已收藏 ${aiIds.length} 条 · 我的页可查看`, icon: 'none' });
  },

  /* 批量分享：选中 2-6 条 → 写入 ylm_share_msgs → 分享页（墨韵分享卡 + 出图） */
  multiShare() {
    const sel = this.data.multiSel;
    const ids = Object.keys(sel);
    if (ids.length < 2) {
      wx.showToast({ title: '至少勾选 2 条', icon: 'none' });
      return;
    }
    if (ids.length > 6) {
      wx.showToast({ title: '最多勾选 6 条', icon: 'none' });
      return;
    }
    const msgs = (this.data.messages || [])
      .filter((m) => sel[m.id])
      .map((m) => ({ id: m.id, role: m.role, tag: m.tag || '', content: String(m.content || ''), time: m.time || '' }));
    try {
      wx.setStorageSync('ylm_share_msgs', msgs);
    } catch (e) {
      wx.showToast({ title: '分享准备失败，请重试', icon: 'none' });
      return;
    }
    this.exitMulti();
    wx.navigateTo({ url: '/pages/share/share' });
  },

  /* 意见反馈原因 → 本地留档 + 后端上报（有咨询 ID 时 negative + 备注） */
  submitFeedbackReason(e) {
    const reason = e.currentTarget.dataset.reason;
    const { msgId } = this.data.fbMenu;
    this.setData({ fbMenu: { show: false, msgId: '' } });
    const msg = this._findMessage(msgId);
    if (!msg) return;
    this._logFeedback(msg, reason);
    if (msg.consultationId) {
      api.feedback(msg.consultationId, 'negative', reason).catch(() => {});
    }
    wx.showToast({ title: '已收到你的反馈，明灯会改进', icon: 'none' });
  },

  closeFbMenu() {
    this.setData({ fbMenu: { show: false, msgId: '' } });
  },

  /* ═══ v1.2 表情反应（气泡尾部 ＋ / 长按菜单 → emoji 选择 → 气泡角显示，可追加/移除） ═══ */

  /* 打开 emoji 选择弹层（气泡尾部 ＋ 或 长按菜单「表情反应」） */
  openEmojiSheet(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const msgId = e.currentTarget.dataset.id;
    if (msgId) this._openEmojiFor(msgId);
  },

  _openEmojiFor(msgId) {
    const msg = this._findMessage(msgId);
    if (!msg) return;
    const cur = ((msg.reactions || [])).slice();
    const curMap = {};
    cur.forEach((em) => { curMap[em] = true; });
    this.setData({ emojiSheet: { show: true, msgId, cur, curMap } });
  },

  closeEmojiSheet() {
    this.setData({ emojiSheet: { show: false, msgId: '', cur: [], curMap: {} } });
  },

  /* emoji 选择弹层内点选：已在列表中 → 移除；否则追加 */
  toggleReaction(e) {
    const em = e.currentTarget.dataset.em;
    const { msgId } = this.data.emojiSheet;
    if (!msgId || !em) return;
    this._toggleReaction(msgId, em);
    // 弹层停留：刷新当前选中态
    const msg = this._findMessage(msgId);
    const cur = (msg && msg.reactions || []).slice();
    const curMap = {};
    cur.forEach((x) => { curMap[x] = true; });
    this.setData({ emojiSheet: Object.assign({}, this.data.emojiSheet, { cur, curMap }) });
  },

  /* 点气泡角已显示的表情 → 移除 */
  removeReaction(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const { id, em } = e.currentTarget.dataset;
    if (!id || !em) return;
    this._toggleReaction(id, em);
  },

  /* 核心：追加/移除一个表情 → 更新镜像消息 + 本地 storage 持久化 */
  _toggleReaction(msgId, em) {
    const map = Object.assign({}, this.data.reactions || {});
    let arr = (map[msgId] || []).slice();
    const idx = arr.indexOf(em);
    if (idx >= 0) {
      arr.splice(idx, 1);
    } else {
      arr.push(em);
    }
    if (arr.length) map[msgId] = arr; else delete map[msgId];
    this.setData({ reactions: map });
    try { wx.setStorageSync(REACT_KEY, map); } catch (e) { /* ignore */ }
    const msgs = this.data.messages.map((m) => (
      m.id === msgId ? Object.assign({}, m, { reactions: arr }) : m
    ));
    this.setData({ messages: msgs });
  },

  /* 删除消息时清理其反应 */
  _clearReactions(msgId) {
    const map = Object.assign({}, this.data.reactions || {});
    if (!map[msgId]) return;
    delete map[msgId];
    this.setData({ reactions: map });
    try { wx.setStorageSync(REACT_KEY, map); } catch (e) { /* ignore */ }
  },

  /* ═══ v1.2 建议卡片：点击推荐追问 → 直接发送（生成中自动排队） ═══ */

  sendSuggestion(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const text = (e.currentTarget.dataset.text || '').trim();
    if (!text) return;
    this._send(text);
  },

  /* ═══ Task 8 对话内引导卡跳转：识别到的 /pages/ 路径 → 跳转（hehun 页 onLoad 自动回填我方） ═══ */

  onNavBtnTap(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const url = (e.currentTarget.dataset.url || '').trim();
    if (!url) {
      wx.showToast({ title: '页面暂不可用', icon: 'none' });
      return;
    }
    wx.navigateTo({ url });
  },

  /* ═══ v1.2 代码块一键复制（墨韵代码块头部「复制」钮） ═══ */

  copyCode(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const code = e.currentTarget.dataset.code;
    if (!code) return;
    wx.setClipboardData({ data: code }); // 系统自带「内容已复制」toast
  },

  _logFeedback(msg, reason) {
    try {
      const log = wx.getStorageSync('ylm_feedback_log');
      const list = Array.isArray(log) ? log : [];
      list.unshift({
        id: msg.id,
        content: String(msg.content || '').slice(0, 80),
        reason,
        consultationId: msg.consultationId || null,
        t: Date.now(),
      });
      wx.setStorageSync('ylm_feedback_log', list.slice(0, 50));
    } catch (e) { /* ignore */ }
  },

  /* 分享（菜单「分享」= open-type=share 按钮 → 本回调）
     内容取菜单打开时锁定的 _shareTarget（菜单项 tap 先关闭菜单，actionMenu.msgId 已清空） */
  onShareAppMessage() {
    const content = this._shareTarget || '与明灯夜话';
    return {
      title: String(content).replace(/\s+/g, ' ').slice(0, 42),
      path: '/pages/chat/chat',
    };
  },

  /* 原型 toggleFb：反馈点亮（up/down 有咨询 ID 时上报后端；keep → 持久化收藏 kept） */
  toggleFb(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应（点气泡=勾选）
    const { id, k } = e.currentTarget.dataset;
    if (k === 'keep') {
      const msg = this._findMessage(id);
      if (!msg) return;
      const on = !msg.kept;
      streamHost.patchMessage(id, { kept: on, keptAt: on ? Date.now() : 0 });
      wx.showToast({ title: on ? '已收藏 · 我的页可查看' : '已取消收藏', icon: 'none' });
      return;
    }
    this._toggleFbCore(id, k);
  },

  _toggleFbCore(id, k) {
    const key = id + '-' + k;
    const on = !this.data.fb[key];
    this.setData({ [`fb.${key}`]: on });
    if ((k === 'up' || k === 'down') && on) {
      const msg = this._findMessage(id);
      if (msg && msg.consultationId) {
        api.feedback(msg.consultationId, k === 'up' ? 'positive' : 'negative').catch(() => {});
      }
    }
  },

  /* ═══ v1.1 新开对话（归档 → 清空 → 重新 welcome） ═══ */

  /* 对话历史入口（页头「历史」按钮）：归档夜话列表（搜索/预览/继续/删除） */
  goHistory() {
    wx.navigateTo({ url: '/pages/history/history' });
  },

  /* 新开对话（页头「新开」符号钮，v1.3 固定入口）：
     当前会话归档 ylm_chat_archives → 回到空会话引导区（v2.0 元宝式空页）。
     PM 要求：点新开 = 当前对话保存到历史 → 从欢迎/空开始重新说；下次进入小程序是
     这段新对话而非旧内容（_resetChatUi → streamHost.reset 会把空数组写回
     ylm_chat_messages，onLoad 恢复读到的是空对话 → 显示引导区；旧内容只存在于归档=历史页）。
     确认弹窗保留（PM 接受：当前对话将保存到历史）。
     真机反馈修复：确认后必须清空并归档；任何异常不静默失败——
     归档失败不阻断重置，并给明确提示。 */
  startNewChat() {
    this.exitMulti();   // v1.3：多选模式中新开 → 先退出勾选态
    wx.showModal({
      title: '新开对话',
      content: '当前对话将保存到历史，重新开始一段新的夜话？',
      confirmText: '新开',
      confirmColor: '#A93A2C',
      success: (res) => {
        if (!res.confirm) return;
        try {
          this._archiveCurrent();
        } catch (e) {
          console.warn('[Chat] 归档失败（不阻断新开）:', e);
        }
        try {
          this._resetChatUi();
        } catch (e) {
          console.error('[Chat] 新开对话失败:', e);
          wx.showToast({ title: '新开失败，请重试', icon: 'none' });
          return;
        }
        wx.showToast({ title: '已新开一段夜话', icon: 'none' });
      },
      fail: () => {
        wx.showToast({ title: '新开失败，请重试', icon: 'none' });
      },
    });
  },

  _archiveCurrent() {
    const msgs = (streamHost.messages && streamHost.messages.length) ? streamHost.messages : this.data.messages;
    if (!msgs || !msgs.length) return;
    /* v1.3：只有演示/引导（无真实用户消息）→ 不产生空归档
       （与 history.js hasRealUser 同口径：真实用户消息 id 为 u+时间戳；v2.0 起
       引导区不发消息，空会话不会走到归档） */
    const hasReal = msgs.some((m) => m.role === 'user' && String(m.id || '').indexOf('s') !== 0);
    if (!hasReal) return;
    const firstUser = msgs.find((m) => m.role === 'user' && !m.pending);
    const label = (firstUser && firstUser.content) ? String(firstUser.content).slice(0, 18) : '一段夜话';
    const arch = {
      id: 'arch_' + Date.now(),
      createdAt: Date.now(),
      label,
      messages: msgs.map((m) => {
        const copy = Object.assign({}, m);
        delete copy.segments;
        delete copy.mdNodes;   // v1.2 渲染缓存不落盘（镜像时重算）
        return copy;
      }),
    };
    let list = [];
    try {
      list = wx.getStorageSync(ARCHIVE_KEY);
    } catch (e) { /* ignore */ }
    if (!Array.isArray(list)) list = [];
    list.unshift(arch);
    try {
      wx.setStorageSync(ARCHIVE_KEY, list.slice(0, 20));
    } catch (e) { /* ignore */ }
  },

  /* 清空对话（长按页头细行）：直接清空不归档（与新开对话区分） */
  clearChat() {
    this.exitMulti();   // v1.3 多选：清空前先退出勾选态
    wx.showModal({
      title: '清空对话',
      content: '确定清空所有聊天记录吗？',
      success: (res) => {
        if (!res.confirm) return;
        this._resetChatUi();
      },
    });
  },

  /* 重置对话 UI（新开/清空共用）：宿主重置 + 页内状态归零。
     修复「点新开报语音失败」：清理音频期间置 _audioSilent（onError 静默，不弹 toast）；
     同时 _speakSeq++ 使进行中的 TTS 请求失效（其成功回调不再继续播放/失败不再弹「语音合成失败」）。
     走查：startNewChat → _archiveCurrent → _resetChatUi 全链路无 toast 触发点（仅确认弹窗
     与成功 toast「已新开一段夜话」）；_cleanupVoice 置 _dropResult 使识别 onError 也静默。
     v2.0（元宝式空会话引导）：新开/清空后不再回 SEED 三笺——setData 直接写空消息镜像 +
     showGuide=true（与 _onHostState 订阅路径同一渲染口径），不依赖 streamHost.reset →
     _emit → _onHostState 的订阅链路兜底——该链路在某些真机场景未生效时，界面也能立即
     切到空会话引导区；streamHost.reset([]) 照常执行（宿主现场/存储恢复为空，
     下次 onLoad 读到空 → 引导区），订阅到达时是幂等重绘 */
  _resetChatUi() {
    this._cleanupVoice();
    if (this._audioCtx) {
      this._audioSilent = true;
      this._audioCtx.stop();
      setTimeout(() => { this._audioSilent = false; }, 300);
    }
    this._speakSeq = (this._speakSeq || 0) + 1;   // 使在途 TTS 请求失效（播新停旧语义）
    this._drawerRestore = null;
    const emptyMirror = this._mirror([]);  // 与订阅路径同口径（空列表，引导区接管）
    this.setData({
      messages: emptyMirror,
      showGuide: true,
      fb: {},
      typing: false,
      slowHint: false,      // 2026-08-18 生成慢提示复位（宿主 reset 亦清除）
      streaming: false,
      speakingId: '',
      citeDrawer: { show: false, full: false, msgId: '', items: [] },
      actionMenu: { show: false, msgId: '', role: '' },
      fbMenu: { show: false, msgId: '' },
      selectMsgId: '',
      inputMode: 'text',
      inputText: '',
      emojiSheet: { show: false, msgId: '', cur: [], curMap: {} },
    });
    streamHost.reset([]);
    /* 会话隔离：新开/清空 = 新会话 → 生成新 session_id（旧会话消息不再进入上下文） */
    const sid = this._genSessionId();
    try { wx.setStorageSync(this.SESSION_KEY, sid); } catch (e) { /* ignore */ }
    streamHost.setSessionId(sid);
  },

  /* ═══ 会话隔离（PM：新开对话后回复不得带上个对话内容） ═══
     sessionId 生命周期：onLoad 恢复（无则生成）→ 新开/清空（_resetChatUi）时生成新的；
     持久化 ylm_session_id；请求 payload 随附 session_id → 后端上下文只取本会话。
     本期不做「历史页继续 → 切回旧 session_id」（历史续读按 user 兜底上下文）。 */
  SESSION_KEY: 'ylm_session_id',

  _genSessionId() {
    /* s_ + 时间戳36进制(8位) + 随机4位 → 总长 11~14，全 [A-Za-z0-9_]，
       符合后端格式校验 ^[A-Za-z0-9_-]{8,64}$ */
    const rand = Math.random().toString(36).slice(2, 6);
    return 's_' + Date.now().toString(36) + (rand || '0000');
  },

  /* 恢复（首次则生成）会话标识并注入流式宿主 */
  _loadSessionId() {
    let sid = '';
    try { sid = wx.getStorageSync(this.SESSION_KEY) || ''; } catch (e) { /* ignore */ }
    if (typeof sid !== 'string' || !/^[A-Za-z0-9_-]{8,64}$/.test(sid)) {
      sid = this._genSessionId();
      try { wx.setStorageSync(this.SESSION_KEY, sid); } catch (e) { /* ignore */ }
    }
    streamHost.setSessionId(sid);
  },

  /* ═══ v1.1 语音/键盘模式切换（元宝式） ═══ */

  /* 输入条左侧语音图标 → 按住说话模式；语音模式右侧键盘图标 → 文字输入 */
  switchInputMode(e) {
    const mode = e.currentTarget.dataset.mode;
    if (mode === this.data.inputMode) return;
    if (mode === 'text' && (this.data.isRecording || this.data.converting)) {
      // 录音中切回键盘：取消本次录音
      this._finishRecording(false);
    }
    this.setData({ inputMode: mode, inputFocused: false });
  },

  /* ════════════════════════════════════════════════════════════
     语音输入 — 元宝式按住说话（plugin://WechatSI 微信同声传译）
     需在小程序后台「设置-第三方设置-插件管理」添加「微信同声传译」并在
     app.json 配置 plugins；未配置时 requirePlugin 会 throw → 优雅降级。
     ════════════════════════════════════════════════════════════ */
  _initSpeech() {
    let plugin = null;
    try {
      plugin = requirePlugin('WechatSI');
    } catch (e) {
      console.warn('[Chat] WechatSI 插件未配置，语音输入降级为键盘:', e && e.message);
    }
    if (!plugin || !plugin.getRecordRecognitionManager) {
      this._speechPlugin = null;
      this._recMgr = null;
      return;
    }
    this._speechPlugin = plugin;
    const manager = plugin.getRecordRecognitionManager();
    this._recMgr = manager;
    manager.onRecognize = (res) => {
      console.log('[Chat] 识别中:', res && res.result);
    };
    manager.onStop = (res) => this._handleRecognitionResult(res);
    manager.onError = (res) => this._handleRecognitionError(res);
  },

  /* 麦克风权限：先 getSetting，未授权请求授权，拒绝引导去设置 */
  _ensureRecordPermission(cb) {
    wx.getSetting({
      success: (res) => {
        const st = res.authSetting && res.authSetting['scope.record'];
        if (st === true) { cb(true); return; }
        if (st === false) {
          wx.showModal({
            title: '需要麦克风权限',
            content: '语音输入需要麦克风权限，请在设置中开启。',
            confirmText: '去设置',
            confirmColor: '#A93A2C',
            success: (r) => { if (r.confirm) wx.openSetting({}); },
          });
          cb(false);
          return;
        }
        wx.authorize({
          scope: 'scope.record',
          success: () => cb(true),
          fail: () => {
            wx.showToast({ title: '请授权麦克风权限后使用语音', icon: 'none' });
            cb(false);
          },
        });
      },
      fail: () => cb(true), // getSetting 异常不阻塞（系统会再次询问）
    });
  },

  /* 生成一次录音会话的声波柱参数（CSS 动画随机起伏） */
  _buildWaveBars() {
    const bars = [];
    for (let i = 0; i < WAVE_BAR_COUNT; i++) {
      bars.push({
        i,
        h: 14 + Math.round(Math.random() * 30),       // 14-44rpx
        d: +(0.7 + Math.random() * 0.6).toFixed(2),   // 0.7-1.3s
        l: +(Math.random() * 0.9).toFixed(2),         // 0-0.9s
      });
    }
    return bars;
  },

  /* 按住说话：按下即进入录音态（v1.1：生成中也允许录音 → 识别结果排队上屏） */
  micTouchStart(e) {
    if (this.data.isRecording || this.data.converting) return;
    if (!this._speechPlugin || !this._recMgr) {
      wx.showToast({ title: '语音输入未开启，请使用键盘输入', icon: 'none' });
      return;
    }
    const t = (e.touches && e.touches[0]) || {};
    this._touchActive = true;
    this._touchY = t.clientY || 0;
    this._touchStartAt = Date.now();
    this._ensureRecordPermission((ok) => {
      if (!ok) return;
      if (!this._touchActive) {
        // 授权弹窗消耗了本次长按：短触按「说话时间太短」处理，长触引导再来一次
        if (Date.now() - this._touchStartAt < REC_MIN_MS) {
          wx.showToast({ title: '说话时间太短', icon: 'none' });
        } else {
          wx.showToast({ title: '已授权，请再次长按说话', icon: 'none' });
        }
        return;
      }
      this._startRecording();
    });
  },

  _startRecording() {
    if (this.data.isRecording) return;
    this._dropResult = false;
    this._recordStartAt = Date.now();
    this.setData({
      isRecording: true,
      recSeconds: 0,
      recCanceling: false,
      converting: false,
      waveBars: this._buildWaveBars(),
    });
    this._recordTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - this._recordStartAt) / 1000);
      this.setData({ recSeconds: seconds });
      if (seconds >= REC_MAX_S) this._finishRecording(true); // 60s 到点自动发送
    }, 1000);
    try {
      this._recMgr.start({ duration: REC_MAX_S * 1000, lang: 'zh_CN' });
    } catch (e) {
      console.warn('[Chat] 录音启动失败:', e);
      this._cleanupVoice();
      wx.showToast({ title: '录音启动失败，请重试', icon: 'none' });
    }
  },

  /* 按住上滑 ≥80px → 进入「松开 取消」 */
  micTouchMove(e) {
    if (!this.data.isRecording) return;
    const t = (e.touches && e.touches[0]) || {};
    const dy = (t.clientY || this._touchY) - this._touchY;
    const cancel = dy < -SWIPE_CANCEL_PX;
    if (cancel !== this.data.recCanceling) {
      this.setData({ recCanceling: cancel });
      if (cancel) {
        try { wx.vibrateShort({}); } catch (err) { /* 模拟器无振动能力，静默 */ }
      }
    }
  },

  /* 松开：取消 → 丢弃；<800ms → 太短；否则停止 → 转文字 → 直接发送 */
  micTouchEnd() {
    this._touchActive = false;
    if (!this.data.isRecording) return;
    if (this.data.recCanceling) {
      this._finishRecording(false); // 上滑取消
      return;
    }
    if (Date.now() - this._touchStartAt < REC_MIN_MS) {
      wx.showToast({ title: '说话时间太短', icon: 'none' });
      this._finishRecording(false);
      return;
    }
    this._finishRecording(true); // 松开发送
  },

  /* 触摸被系统打断（来电/弹窗）→ 按取消处理 */
  micTouchCancel() {
    this._touchActive = false;
    if (!this.data.isRecording) return;
    this._finishRecording(false);
  },

  /* 录音条圆形停止按钮：点按 = 停止并发送 */
  recStopTap() {
    if (!this.data.isRecording) return;
    this._finishRecording(true);
  },

  _finishRecording(send) {
    if (!this.data.isRecording) return;
    this._cleanupTimer();
    if (!send) {
      // 取消/太短：停录但丢弃识别结果
      this._dropResult = true;
      this.setData({ isRecording: false, recCanceling: false, recSeconds: 0 });
      try { this._recMgr && this._recMgr.stop(); } catch (e) { /* ignore */ }
      return;
    }
    this.setData({ isRecording: false, recCanceling: false, recSeconds: 0, converting: true });
    try {
      this._recMgr && this._recMgr.stop(); // onStop → _handleRecognitionResult
    } catch (e) {
      console.warn('[Chat] 录音停止失败:', e);
      this.setData({ converting: false });
      wx.showToast({ title: '录音停止失败，请重试', icon: 'none' });
    }
  },

  /* 识别完成 → 元宝式：说完即发（生成中 → 排队，消息立即上屏） */
  _handleRecognitionResult(res) {
    this._cleanupTimer();
    this.setData({ isRecording: false, converting: false, recCanceling: false });
    if (this._dropResult) { this._dropResult = false; return; }
    const text = ((res && res.result) || '').trim();
    if (!text) {
      wx.showToast({ title: '没听清，请再试一次', icon: 'none' });
      return;
    }
    this._send(text);
  },

  _handleRecognitionError(res) {
    this._cleanupTimer();
    this.setData({ isRecording: false, converting: false, recCanceling: false });
    if (this._dropResult) { this._dropResult = false; return; }
    console.warn('[Chat] 语音识别失败:', res);
    wx.showToast({ title: '识别失败，请再试一次', icon: 'none' });
  },

  _cleanupTimer() {
    if (this._recordTimer) {
      clearInterval(this._recordTimer);
      this._recordTimer = null;
    }
  },

  _cleanupVoice() {
    this._cleanupTimer();
    this._dropResult = true;
    if (this._recMgr && this.data.isRecording) {
      try { this._recMgr.stop(); } catch (e) { /* ignore */ }
    }
    this.setData({ isRecording: false, converting: false, recCanceling: false, recSeconds: 0 });
  },

  /* ════════════════════════════════════════════════════════════
     语音播报 — 后端 /api/tts 合成 → InnerAudioContext 播放
     单实例播放：播新停旧；同消息缓存不重复请求；再次点击停止
     ════════════════════════════════════════════════════════════ */
  _initAudio() {
    this._audioCtx = wx.createInnerAudioContext();
    this._ttsCache = {};
    this._audioSilent = false;   // 清理/重置中：onError 不弹 toast（仅用户主动点播放时提示）
    this._audioCtx.onEnded(() => this.setData({ speakingId: '' }));
    this._audioCtx.onStop(() => this.setData({ speakingId: '' }));
    this._audioCtx.onError((err) => {
      console.warn('[Chat] 语音播放失败:', err);
      this.setData({ speakingId: '' });
      /* 修复：新开/清空/重置流程中 _audioCtx.stop() 可能触发 onError → 报「语音播放失败」。
         清理中（_audioSilent）静默，错误 toast 仅限用户主动点播放时 */
      if (this._audioSilent) return;
      wx.showToast({ title: '语音播放失败', icon: 'none' });
    });
  },

  /* 点朗读：合成并播放该消息语音；再次点击停止 */
  speakMessage(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const id = e.currentTarget.dataset.id;
    const msg = this._findMessage(id);
    if (!msg || !this._audioCtx) return;
    this._playWithTts(id, msg.content || '', true);
  },

  /* 播放 TTS 语音；_speakSeq 让新播放请求使进行中的请求失效（播新停旧） */
  async _playWithTts(id, content, isManual) {
    const text = (content || '').trim();
    if (!text || !this._audioCtx) return;

    // 正在播放该消息 → 停止
    if (this.data.speakingId === id) {
      this._audioCtx.stop();
      this.setData({ speakingId: '' });
      return;
    }

    this._speakSeq = (this._speakSeq || 0) + 1;
    const seq = this._speakSeq;

    // 正在播放其它消息 → 先停（播新停旧）
    if (this.data.speakingId) this._audioCtx.stop();

    // 已缓存的语音直接播放
    const cached = this._ttsCache && this._ttsCache[id];
    if (cached) {
      this.setData({ speakingId: id });
      this._audioCtx.src = cached;
      this._audioCtx.play();
      return;
    }

    this.setData({ speakingId: id });
    try {
      const res = await api.tts(text);
      // 期间已有更新的播放请求 → 丢弃本次结果
      if (seq !== this._speakSeq) return;
      let url = res && res.audio_url;
      if (!url) throw new Error('no audio_url');
      if (url.indexOf('http') !== 0) {
        // 后端返回相对路径时的兜底前缀
        // TODO: 生产环境 audio 服务前缀改为 https://yilichat.com（或后端统一配置，勿硬编码）
        url = 'http://127.0.0.1:8768' + url;
      }
      if (!this._ttsCache) this._ttsCache = {};
      this._ttsCache[id] = url;
      this._audioCtx.src = url;
      this._audioCtx.play();
    } catch (e) {
      // 过期请求的失败不打扰当前播放
      if (seq !== this._speakSeq) return;
      this.setData({ speakingId: '' });
      console.warn('[Chat] TTS 失败:', e);
      if (isManual) wx.showToast({ title: '语音合成失败', icon: 'none' });
    }
  },

  _findMessage(id) {
    const msgs = this.data.messages;
    for (let i = 0; i < msgs.length; i++) {
      if (msgs[i].id === id) return msgs[i];
    }
    return null;
  },
});
