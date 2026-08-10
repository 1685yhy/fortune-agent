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

/* 原型 SEED 开场三笺（dir_b.html 802-806 行） */
const SEED = [
  { id: 's1', role: 'ai', tag: '明灯 · 问候', content: '夜好。窗外有风，你这里也有灯。今晚想聊什么？梦、心事，或只是一天的尾巴。', time: '23:38' },
  { id: 's2', role: 'user', content: '我梦见自己站在桥上，河水很清，我却不敢走过去。', time: '23:39' },
  { id: 's3', role: 'ai', tag: '解梦 · 水与桥', content: '桥在梦里，是「渡」的记号——你心里已经有了过河的打算。水清，说明你并不糊涂；不敢过桥，只是还差一句推你上桥的话。这三日，把最想做的那件事，说给最信任的人听。桥，会自己搭好。', time: '23:41' },
];

const STORAGE_KEY = 'ylm_chat_messages';
const ARCHIVE_KEY = 'ylm_chat_archives';
const REACT_KEY = 'ylm_reactions';   // v1.2 表情反应：{消息id: [emoji...]}

/* v1.2 表情反应可选集（8 个常用） */
const EMOJIS = ['👍', '❤️', '😂', '😮', '😢', '🔥', '🙏', '✨'];

/* 语音输入参数（元宝式） */
const REC_MIN_MS = 800;   // 短按 < 800ms → 「说话时间太短」
const REC_MAX_S = 60;     // 最长录音 60s，到点自动发送
const SWIPE_CANCEL_PX = 80; // 上滑 80px 进入「松开 取消」
const WAVE_BAR_COUNT = 26;

/* v8 阶段 3·过程体验（流式打字机）：滚动节流（生成推进由宿主 tick 驱动） */
const SCROLL_MS = 100;      // 自动滚动节流

Page({
  data: {
    navOff: 0,
    messages: SEED,
    typing: false,
    inputText: '',
    inputFocused: false,
    fb: {},
    scrollInto: '',
    curTab: 'chat',
    dark: false,
    /* v8 阶段 3·过程体验（流式） */
    streaming: false,       // 当前有回复正在生成（发送钮 → 停止钮）
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
  },

  onLoad() {
    this._loadReactions();
    this._initNavOff();
    this._attachHost();
    this._loadHistory();
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
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* ═══ v1.1 全局流式宿主接线（切 tab 对话不中断） ═══ */

  _attachHost() {
    this._lastTick = -1;
    this._segCache = null;
    this._unsubHost = streamHost.subscribe((state) => this._onHostState(state));
  },

  /* 宿主状态 → 页面镜像（segments 由页面重算，引用分段渲染在页面侧） */
  _onHostState(state) {
    if (state.notice) {
      wx.showToast({ title: state.notice, icon: 'none' });
      streamHost.clearNotice();
    }
    if (this._lastTick === state.tick) return; // tick 未变：仅提示类事件
    this._lastTick = state.tick;
    // 流式结束且本轮有新内容 → 检查回复是否含建档标记（最小实现：含 persons 相关 key 即提示）
    if (this._prevStreaming && !state.streaming) {
      this._checkArchiveKeys(state.messages || []);
    }
    this._prevStreaming = !!state.streaming;
    this.setData({
      messages: this._mirror(state.messages || []),
      streaming: !!state.streaming,
      typing: !!state.typing,
    });
    if (state.autoScroll) this._scrollBottom();
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
     同时把本地表情反应合并进镜像（reactions） */
  _mirror(messages) {
    const out = new Array(messages.length);
    const reactions = this.data.reactions || {};
    for (let i = 0; i < messages.length; i++) {
      const m = messages[i];
      const c = String(m.content || '');
      const cached = this._segCache;
      if (cached && cached.id === m.id && cached.content === c) {
        out[i] = Object.assign({}, m, {
          mdNodes: cached.mdNodes,
          reactions: reactions[m.id] || [],
        });
        continue;
      }
      const mdNodes = md.parseMd(c);
      this._segCache = { id: m.id, content: c, mdNodes };
      out[i] = Object.assign({}, m, {
        mdNodes,
        reactions: reactions[m.id] || [],
      });
    }
    return out;
  },

  /* 历史续读：宿主现场优先（可能后台生成中/刚完成）；无则 storage；再无则 SEED 开场 */
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
    if (!messages || !messages.length) messages = SEED.slice();
    streamHost.setMessages(messages);
    this.setData({
      messages: this._mirror(messages),
      streaming: !!hostState.streaming,
      typing: !!hostState.typing,
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

  /* 原型 onBack：返回今日 */
  goToday() {
    wx.reLaunch({ url: '/pages/today/today' });
  },

  /* 原型 onTab：底部栏切换 */
  onTab(e) {
    const t = e.currentTarget.dataset.tab;
    const url = { today: '/pages/today/today', chat: '/pages/chat/chat', book: '/pages/reports/reports', me: '/pages/me/me' }[t];
    if (url && !url.includes('/chat/')) wx.reLaunch({ url });
  },

  /* 原型 quickchip：点标签即问（生成中再点 = 排队，消息立即上屏） */
  quickAsk(e) {
    const text = (e.currentTarget.dataset.text || '').trim();
    if (!text) return;
    this._send(text);
  },

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
    if (r !== 'empty') this.setData({ inputText: '' });
  },

  /* 停止生成（发送钮变停止钮） */
  stopStream() {
    streamHost.stop();
  },

  /* 重试：丢弃失败气泡，用原消息重发 */
  retryStream(e) {
    const text = (e.currentTarget.dataset.text || '').trim();
    if (!text || streamHost.active) return;
    const id = e.currentTarget.dataset.id;
    streamHost.retry(id, text, curatedFor(text).tag);
  },

  /* 思考路径折叠/展开（宿主持久化） */
  toggleThink(e) {
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

  /* 长按气泡 → 操作菜单（选取模式中不弹菜单，提示长按文字选取） */
  onBubbleLongPress(e) {
    const { id, role } = e.currentTarget.dataset;
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

  /* 消息区点击：选取模式自动退出 */
  onListTap() {
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
    const text = (e.currentTarget.dataset.text || '').trim();
    if (!text) return;
    this._send(text);
  },

  /* ═══ v1.2 代码块一键复制（墨韵代码块头部「复制」钮） ═══ */

  copyCode(e) {
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

  /* 新开对话（页头「新开」按钮）：当前会话归档 ylm_chat_archives → 回到 SEED 开场。
     真机反馈修复：确认后必须清空回 SEED 并归档；任何异常不静默失败——
     归档失败不阻断重置，并给明确提示。 */
  startNewChat() {
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
    wx.showModal({
      title: '清空对话',
      content: '确定清空所有聊天记录吗？',
      success: (res) => {
        if (!res.confirm) return;
        this._resetChatUi();
      },
    });
  },

  /* 重置对话 UI（新开/清空共用）：宿主重置 + 页内状态归零 */
  _resetChatUi() {
    this._cleanupVoice();
    if (this._audioCtx) this._audioCtx.stop();
    this._drawerRestore = null;
    this.setData({
      fb: {},
      typing: false,
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
    streamHost.reset(SEED.slice());
    this._scrollBottom(true);
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
    this._audioCtx.onEnded(() => this.setData({ speakingId: '' }));
    this._audioCtx.onStop(() => this.setData({ speakingId: '' }));
    this._audioCtx.onError((err) => {
      console.warn('[Chat] 语音播放失败:', err);
      this.setData({ speakingId: '' });
      wx.showToast({ title: '语音播放失败', icon: 'none' });
    });
  },

  /* 点朗读：合成并播放该消息语音；再次点击停止 */
  speakMessage(e) {
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
