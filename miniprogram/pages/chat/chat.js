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
const cardUtil = require('../../utils/card');   // E2-2 对话卡片化：卡片标记解析
const chatSelect = require('../../utils/chatSelect'); // k10-C 文字选取：段落模型（乙覆盖层/甲高亮/复制本段共用事实源）

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

/* ═══ k6 波2 P4.6 元宝式反馈面板：踩（footer/菜单）→ 底部面板，分组原因多选
   （权威参考 = 用户 2026-09-04 实机截图 IMG_4510——本批无图可读，先按 brief 默认
   文案实现最小合理版，待图复核；用户可后改文案） ═══ */
const FB_REASON_GROUPS = [
  { label: '针对问题', opts: ['理解错了', '没回答到点上', '忽略了我的关键信息'] },
  { label: '针对回答', opts: ['内容不准确', '说得太绝对', '内容不完整', '太敷衍', '排盘或日期算错了', '内容让我不适'] },
  { label: '针对格式', opts: ['排版乱了', '内容重复了', '图片没显示'] },
];

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

/* ═══ k10-C 文字选取（用户 2026-09-04 实诉：点「选取文字」后没有默认选区） ═══
   平台限制（勿试图突破，官方无解）：
   ① `<text selectable>` 只能「允许用户自己长按」唤起系统选择——没有任何 API 可
      编程唤起选区/全选/预设选区（无 DOM Range/setSelection）；
   ② `<textarea>` 支持 selection-start/end，但仅在自身聚焦时生效；textarea 无
      readonly 属性（input 亦无）——程序 focus 必然弹键盘，只能 focus 后立即
      wx.hideKeyboard 尽力抑制，iOS 只读/程序聚焦下是否保留手柄不保证；
   ③ selectable 与自定义 bindlongpress 在同一元素互斥 → 现状「模式开关」让位原生，
      代价是菜单消费第一次长按、用户需第二次长按（甲兜底正为此引导）。
   双路径（2026-09-06 主会话拍板：乙默认关闭，全平台默认走甲；乙路径代码保留在
   调试开关 TEXT_SEL_ENGINE='b' 之后，供真机实验/后续评估复用）：
   乙：点「选取文字」→ 被按气泡正文以只读 textarea 覆盖层呈现纯文本，程序
       focus + selection-start/end 选中长按所在段落 → 可拖动两端焦点的观感；
       （结构见 chat.wxml .sel-overlay；键盘抑制/失焦/点外部退出/滚动联动见
       _openSelOverlay/_closeTextOverlay）
   甲：段落高亮定位 + 气泡顶部引导小字「长按这段文字即可拖动选择」
       + 菜单「复制本段」（一键复制被按段）。
   乙为何默认关闭（结构性障碍，勿试图突破）：
       ① textarea/input 均无 readonly 属性——程序 focus 必然弹起键盘，无 API 可
          抑制（wx.hideKeyboard 只能尽力而为，跨端行为不保证）；
       ② textarea selection-start/end 仅在聚焦时生效，iOS 程序聚焦下是否显示可拖
          手柄不保证（无任何 API 可编程唤起 <text> 的系统选择）；
       ③ 覆盖层几何依赖实测矩形，超长文本/目标段落落在 textarea 首屏外时预设
          选区不可见，且无真机验证通道。
   → 全平台默认甲（TEXT_SEL_ENGINE='a'）。真机实验乙：置 'b'（如需 iOS 一并放开
   TEXT_SEL_IOS_OVERLAY）；单测/调试亦可用 page._textSelEngine 实例覆盖。 */
const TEXT_SEL_ENGINE = 'a';          // 'a' = 全平台默认甲（乙关闭）| 'b' = 乙优先（实验开关，自动降甲）
const TEXT_SEL_IOS_OVERLAY = false;   // iOS 覆盖层实验开关：默认关（iOS 不保证只读选中行为）
const TEXT_SEL_MAX_TEXT = 900;        // 覆盖层文本超过该长度 → 甲（超长段落会落在首屏外）
const TEXT_SEL_MAX_PARA_START = 500;  // 目标段落起始偏移超过 → 甲（同上，textarea 无法预滚）

/* ═══ k6-P1 输入条行高机件整体退役 ═══
   B4-1 曾以 JS 常量估算输入条总高并 setData --inputbar-h（wxml L1 内联 CSS 变量，
   wxss L6/L867 兜底）→ 打字每增一行：bindlinechange → setData → msg-list/引导区/
   安全条三处 calc 全页重排 + scroll-into-view 动画补滚——键盘弹出期间被 JS 打断
   （用户实诉：输入打到快满一行被中断、键盘直接关闭）。
   结构性修复：输入条改普通文档流（.screen flex 列内，见 chat.wxss .screen 注释），
   额度条显隐与 textarea auto-height 增行由 flex 自然吸收——本批移除：
   INPUTBAR_* 常量、_inputLines 状态、_updateInputBarH/onInputLineChange/_onInputGrow、
   wxml bindlinechange 与 --inputbar-h/inputBarH/qb-on、wxss 变量兜底与三处 calc。
   流式/发送既有自动滚动路径（scrollInto 'btm'）一律未动。 */

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
    /* ═══ Task 8 · 深夜模式（方案·灯下漫谈） ═══
       B3-3：聊天页深夜浮层（night-head/lamp-card/keepBar/sleepBar/rememberPrompt）
       已按 PM 要求整体移除；深夜主题（.night 变量级联）、服务端 /api/night/lamp/*
       与灯语收藏体系保留。 */
    nightMode: false,          // 深夜模式(夜色主题+语气)
    safetyCard: { show: false, text: '' },
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
    /* UX批1 M-7：WechatSI 语音插件可用性（未配置时 mic 入口置灰 + 点击即提示） */
    micAvailable: true,
    /* 阶段 5·引用交互：底部抽屉（半屏↔全屏） */
    citeDrawer: { show: false, full: false, msgId: '', items: [] },
    /* v1.1 气泡长按操作菜单（墨韵弹层） */
    actionMenu: { show: false, msgId: '', role: '', paraKey: '', canRegen: false, canCite: false },
    // paraKey: k10-C 被按段落键；canRegen/canCite: k6 波2 P4.1/4.2 可见条件（开菜单时计算）
    /* k6 波2 P4.6：元宝式反馈面板（取代旧 fbMenu 原因网格——踩/意见反馈同面板）。
       canSubmit 派生：有选中原因或补充非空；提交按钮据此置灰/可点 */
    fbSheet: { show: false, msgId: '', reasons: {}, note: '', canSubmit: false },
    FB_REASON_GROUPS,
    selectMsgId: '',                      // 选取模式：该气泡 text 动态加 selectable
    /* k10-C 文字选取：甲（段落高亮 + 引导）与乙（textarea 覆盖层）共用状态 */
    selParaKey: '',                       // 甲模式：当前高亮段落键（'md:2'/'card:0'/'user:0'）
    selOverlay: {                         // 乙模式：只读 textarea 覆盖层（几何/选区）
      show: false, msgId: '', text: '', start: 0, end: 0,
      top: 0, left: 0, width: 0, height: 0, focus: false,
    },
    /* v1.2 表情反应：{消息id: [emoji...]} 持久化 + 选择弹层 */
    reactions: {},
    EMOJIS,
    emojiSheet: { show: false, msgId: '', cur: [], curMap: {} },
    /* 对话建档提示条：AI 回复含建档 key（已保存到档案/建档…）→ 顶部提示 */
    saveBanner: false,
    /* L5-1/L5-2 对话额度条：免费用户「今日 X/15」；超限降级 → 精简提示 + 会员引导 */
    quotaBar: { show: false, text: '', downgraded: false },
    /* B4-1 「+」更多面板：拍照 / 从相册选择 */
    morePanel: { show: false },
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
    /* Task 8 深夜：退出深夜态（B3-3 聊天页浮层已移除，无定时器/音频需清理） */
    streamHost.setDeepNight(false);
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* ════════════════════════════════════════════════════════════
     Task 8 · 深夜模式（方案·灯下漫谈）
     夜色主题/守夜人登记/12356 安全条（B3-3 起：浮层类已整体移除）
     ════════════════════════════════════════════════════════════ */

  /* 进入深夜模式：夜色主题 + 语气（B3-3 按 PM 要求移除聊天页深夜浮层——
     夜提示条/灯语卡/挽留条/劝睡/「要我记得吗」全部下线；深夜主题、服务端
     /api/night/lamp/* 与灯语收藏体系保留） */
  _enterNight() {
    this.setData({ nightMode: true });
    streamHost.setDeepNight(true);
    try { wx.setNavigationBarColor({ frontColor: '#000000', backgroundColor: '#F4EBD6' }); } catch (e) {}
    try { wx.setBackgroundColor({ backgroundColor: '#F4EBD6' }); } catch (e) {}
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

  /* ═══ B3-3：枕边灯语卡 / 挽留条 / 「要我记得吗」浮层已按 PM 要求移除。
     ═════ 服务端 /api/night/lamp/*、灯语收藏（favLamp/收藏笺匣/night_mark 页）
     ═════ 与设置页深夜时段档位全部保留，仅收回聊天页浮层入口。 ═══ */

  /* ═══ 12356 安全条（输入与回复双向检测） ═══ */

  _checkSafety(text) {
    if (!text) return false;
    if (/自杀|自伤|轻生|不想活|活不下去|想死|结束生命/.test(text)) {
      this.setData({ safetyCard: {
        show: true,
        text: '我听到你了。请先拨打心理援助热线 12356（24 小时），白天我会陪你联系专业人士。你很重要。',
      } });
      return true;
    }
    return false;
  },

  /* UX批1 M-5：12356 心理援助卡关闭入口（✕）——用户读完可手动收起 */
  dismissSafetyCard() {
    this.setData({ safetyCard: { show: false, text: '' } });
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
        // k6-P1：额度条在流内输入条内（.chat-mid flex:1 承压）——显隐由 flex 自然让位，
        // 不再需要 JS 高度联动（旧 B4-1 注释/INPUTBAR_QBON 计算已随机件一并移除）
        if (!q || q.is_member || q.limit == null) {
          if (this.data.quotaBar.show) {
            this.setData({ quotaBar: { show: false, text: '', downgraded: false } });
          }
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

  /* 降级引导 → 我的页会员入口（开通会员解锁完整版）
     UX批1 I-3：带 ?openMember=1 → me 页 onLoad 读参自动开会员弹层 */
  goMember() {
    wx.reLaunch({ url: '/pages/me/me?openMember=1' });
  },

  /* 宿主状态 → 页面镜像（segments 由页面重算，引用分段渲染在页面侧） */
  _onHostState(state) {
    // k10-C：流式增量/消息变更会推移覆盖层矩形 → 先退出乙覆盖层（甲态高亮保留，
    // 用户仍可二次长按）。state.notice 等提示类事件不触发（tick 未变时下方早退）
    if (this.data.selOverlay.show && typeof state.tick === 'number') this._closeTextOverlay();
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
      // B3-3：回复侧 12356 安全检测（「要我记得吗」浮层已移除，不再扫描）
      const msgs = state.messages || [];
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'ai' && !last.error) {
        this._checkSafety(String(last.content || ''));
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
     （排盘选命主页读同一标记展示确认弹层；点提示条确认/取消后清除）。
     G2 A4：提示条只做「档案指引」，不承诺前端执行保存——对话分析落库发生在服务端
     （_save_bazi_records 双写 bazi_info+persons），前端无法可靠从会话文本回提出生信息
     （重复解析 = 契约分裂风险），零写入却声称「已保存」即假成功，故移除保存语义 */
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

  /* 提示条点击：确认/取消建档弹层（清除标记与提示条）。
     G2 A4（取舍见 _checkArchiveKeys 注释）：移除「确认保存」保存语义——
     改为档案指引「知道了」，不再声称前端执行了保存 */
  onSaveBannerTap() {
    wx.showModal({
      title: '对话建档提示',
      content: '对话中已识别到出生信息\n可在档案页查看并管理',
      confirmText: '知道了',
      cancelText: '取消',
      confirmColor: '#A93A2C',
      success: (res) => {
        this.setData({ saveBanner: false });
        try { wx.removeStorageSync('ylm_dlg_person_saved'); } catch (e) { /* ignore */ }
        if (res.confirm) {
          wx.showToast({ title: '已为你标记，可在档案页查看', icon: 'none' });
        } else {
          wx.showToast({ title: '未标记', icon: 'none' });
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

  /* E2-2 对话卡片化：卡片渲染视图构建（解析 + 流式规则，纯逻辑）
     实现在 utils/card.js buildCardView（node 可单测）；本页只接线：
     正文/尾部/降级均复用 md.parseMd 渲染。 */
  _mirror(messages) {
    const vis = (Array.isArray(messages) ? messages : []).filter((m) => !isJianEntry(m));
    const out = new Array(vis.length);
    const reactions = this.data.reactions || {};
    for (let i = 0; i < vis.length; i++) {
      const m = vis[i];
      const tv = this._thinkView(m);
      const c = String(m.content || '');
      /* E2-2-FIX：缓存键含 streaming/error——buildCardView 的 pending 分支输出
         依赖流式标志（流式中=卡片壳 / 中断=降级纯文本 / 停止=定格壳），而
         _onAbort/_onError 在无增量时 content 不变、仅标志变更（streamHost.js
         _flushAccum 无增量场景）→ 键缺标志会命中过期视图（停留未定格卡片壳，
         而非中断→降级纯文本 / 停止→定格壳） */
      const streamingFlag = !!m.streaming;
      const errorFlag = !!m.error;
      const cached = this._segCache;
      if (cached && cached.id === m.id && cached.content === c
          && cached.streaming === streamingFlag && cached.error === errorFlag) {
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
          card: cached.card,
          cardNodes: cached.cardNodes,
          cardTailNodes: cached.cardTailNodes,
          cardPrefixNodes: cached.cardPrefixNodes,
          cardTitle: cached.cardTitle,
          cardTypeLabel: cached.cardTypeLabel,
          cardFinal: cached.cardFinal,
        });
        continue;
      }
      const cv = (m.role === 'ai')
        ? cardUtil.buildCardView(c, { streaming: !!m.streaming, error: !!m.error }, md.parseMd)
        : null;
      const isCard = !!(cv && cv.card);
      const mdNodes = isCard ? [] : (cv && cv.mdNodes) ? cv.mdNodes : md.parseMd(c);
      const nav = (m.role === 'ai' && !m.error) ? navFor(c) : null;
      this._segCache = {
        id: m.id, content: c, streaming: streamingFlag, error: errorFlag, mdNodes,
        navPath: nav && nav.path, navLabel: nav && nav.label,
        card: isCard ? cv.card : null,
        cardNodes: (cv && cv.cardNodes) || null,
        cardTailNodes: (cv && cv.cardTailNodes) || null,
        cardPrefixNodes: (cv && cv.cardPrefixNodes) || null,
        cardTitle: (cv && cv.cardTitle) || '',
        cardTypeLabel: (cv && cv.cardTypeLabel) || '',
        cardFinal: !!(cv && cv.cardFinal),
      };
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
        card: isCard ? cv.card : null,
        cardNodes: (cv && cv.cardNodes) || null,
        cardTailNodes: (cv && cv.cardTailNodes) || null,
        cardPrefixNodes: (cv && cv.cardPrefixNodes) || null,
        cardTitle: (cv && cv.cardTitle) || '',
        cardTypeLabel: (cv && cv.cardTypeLabel) || '',
        cardFinal: !!(cv && cv.cardFinal),
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

  /* scroll-view 滚动事件：只记录位置与内容总高（WXML bindscroll 每帧触发，不做重活）。
     k10-C：乙覆盖层打开期间消息列表若发生滚动（mask 已阻断触摸滚动，此处兜底
     程序性滚动/流式位移）→ 退出覆盖层（覆盖层矩形随之失效） */
  onScroll(e) {
    if (this.data.selOverlay.show) {
      this._closeTextOverlay();
    }
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
    /* UX批1 M-1：空输入点击发送 → 明确提示（发送钮同时置灰，双保险） */
    if (!String(text || '').trim()) {
      wx.showToast({ title: '先写一句再发', icon: 'none' });
      return;
    }
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

  /* 重试：丢弃失败气泡，用原消息重发（B4-1：图片消息重试保持图片链路） */
  retryStream(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const text = (e.currentTarget.dataset.text || '').trim();
    if (!text || streamHost.active) return;
    const id = e.currentTarget.dataset.id;
    const msg = this._findMessage(id);
    // B4-1-fix I1：重试钮挂在 AI 气泡（data-id=AI id），AI 消息无 image →
    // 回溯最近一条 user 消息（即触发本回复的提问）取 image 一并重发（保持 CV 链路）
    const img = msg && msg.image ? msg.image : this._findPreviousUserImage(id);
    streamHost.retry(id, text, curatedFor(text).tag, img);
  },

  /* 回溯 id 之前的最近一条 user 消息的 image（AI 气泡重试取图用；无则 null）。
     就近回溯保证：图片消息后的文字追问失败重试不会误挂旧图。 */
  _findPreviousUserImage(id) {
    const msgs = this.data.messages;
    for (let i = 0; i < msgs.length; i++) {
      if (msgs[i].id === id) {
        for (let j = i - 1; j >= 0; j--) {
          if (msgs[j].role === 'user') return msgs[j].image || null;
        }
        return null;
      }
    }
    return null;
  },

  /* ═══ B4-1 输入区改版：相机选图 / + 面板 / 图片消息 / 自动长高 ═══ */

  /* 输入框内嵌相机图标：点击选图/拍照（chooseMedia 双 sourceType）→ 上传发图片消息 */
  chooseImage() {
    this._chooseAndSend(['camera', 'album']);
  },

  /* + 面板「拍照」 */
  choosePhoto() {
    this.closeMorePanel();
    this._chooseAndSend(['camera']);
  },

  /* + 面板「从相册选择」 */
  chooseAlbum() {
    this.closeMorePanel();
    this._chooseAndSend(['album']);
  },

  _chooseAndSend(sourceType) {
    if (!wx.chooseMedia) {
      wx.showToast({ title: '当前微信版本不支持选图', icon: 'none' });
      return;
    }
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType,
      success: (res) => {
        const f = res && res.tempFiles && res.tempFiles[0];
        if (f && f.tempFilePath) this._uploadAndSend(f.tempFilePath);
      },
      fail: () => { /* 用户取消/相机不可用：静默 */ },
    });
  },

  /* 上传 → 发图片消息（上传失败/超限 → 明确 toast，服务端错误码透传） */
  _uploadAndSend(filePath) {
    wx.showLoading({ title: '上传中…', mask: true });
    api.uploadChatImage(filePath)
      .then((res) => {
        wx.hideLoading();
        if (!res || !res.url) {
          wx.showToast({ title: '图片上传失败', icon: 'none' });
          return;
        }
        this._sendImage({ url: res.url });
      })
      .catch((err) => {
        wx.hideLoading();
        wx.showToast({ title: (err && err.message) || '图片上传失败', icon: 'none' });
      });
  },

  /* 图片消息进宿主（streamHost 上屏用户图片笺 + message_type=image 流式请求） */
  _sendImage(img) {
    const r = streamHost.sendImage(img);
    if (r === 'queued') {
      wx.showToast({ title: '已排队，等我说完就回你', icon: 'none', duration: 1200 });
    }
    if (r !== 'empty') this._nightTouch();  // 深夜：每发一条登记守夜人
  },

  /* + 面板 */
  openMorePanel() { this.setData({ morePanel: { show: true } }); },
  closeMorePanel() { this.setData({ morePanel: { show: false } }); },

  /* 图片消息点击 → 放大预览（一期单图） */
  previewMsgImage(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const url = e.currentTarget.dataset.url;
    if (!url) return;
    wx.previewImage({ current: url, urls: [url] });
  },

  /* ═══ k6-P1：以下输入条行高机件已整体退役（结构修复见文件头注释与
     chat.wxss .screen/.chat-mid——输入条为流内 flex 子项，原生长高自然让位）：
     _updateInputBarH / onInputLineChange / _onInputGrow 已删除；wxml 不再绑定
     bindlinechange；--inputbar-h 变量、data.inputBarH、_inputLines、qb-on 全清 ═══ */

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
    const map = { book: '古籍', engine: '引擎', memory: '记忆', web: '网络' };
    return map[type] || '古籍';
  },

  /* 引用角标图标：未知类型回退到古籍书图标（与 citeTypeLabel 同一映射） */
  citeTypeIcon(type) {
    const map = { book: '/assets/images/ic-book.png', engine: '/assets/images/ic-engine.png', memory: '/assets/images/ic-memory.png', web: '/assets/images/ic-web.png' };
    return map[type] || map.book;
  },

  /* 点角标 [n]/🔗 → 打开底部抽屉（该条回复的来源列表）。
     k6 波2 P4.2：长按菜单「查看引用」复用同一打开逻辑（_openCiteDrawer） */
  onCiteTap(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    this._openCiteDrawer(e.currentTarget.dataset.msgid);
  },

  _openCiteDrawer(msgId) {
    if (!msgId) return;
    const msg = this._findMessage(msgId);
    const items = (msg && Array.isArray(msg.citations)) ? msg.citations : [];
    if (!items.length) {
      wx.showToast({ title: '本条回复暂无参考资料', icon: 'none' });
      return;
    }
    this._drawerRestore = null;
    this.setData({ citeDrawer: { show: true, full: false, msgId, items } });
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

  /* 长按气泡 → 操作菜单（多选模式不弹菜单）。
     B3-3 D 修复：选取模式中长按一律静默返回——不弹菜单、不 toast、不震动，
     让系统原生文字选择正常出现（此前 toast/震动会盖在 iOS 原生选择 UI 上
     打断选取流程，即用户反馈「选取文字用不了」的主因之一）。 */
  /* k10-C 段落长按记录（md 段落 / user 正文的 data-para-id，冒泡先于气泡级
      onBubbleLongPress）：只做簿记，不拦事件、不震动——菜单仍由气泡级长按打开，
      菜单打开时读此记录判定「复制本段/高亮/乙预设选区」的目标段落。
     选取模式/多选模式中不记录（选取模式静默让位系统原生选择，B3-3 语义） */
  onParaLongPress(e) {
    if (this.data.multiMode) return;
    if (this.data.selectMsgId) return;
    const ds = (e.currentTarget && e.currentTarget.dataset) || {};
    const key = ds.paraId || '';
    if (!key || !ds.msgid) return;
    this._lastParaHit = { msgId: ds.msgid, key };
  },

  onBubbleLongPress(e) {
    const { id, role } = e.currentTarget.dataset;
    if (this.data.multiMode) return;   // v1.3 多选：长按不弹菜单，避免与勾选混淆
    if (this.data.selectMsgId) return; // 选取模式：长按交由系统原生选择
    try { wx.vibrateShort({}); } catch (err) { /* 模拟器无振动能力，静默 */ }
    const msg = this._findMessage(id);
    // 分享目标在开菜单时锁定（分享按钮 open-type=share 会在菜单关闭后读取）
    // E2-2：分享标题走剥标记后的纯文本（卡片标记不暴露给用户）
    this._shareTarget = msg ? cardUtil.stripCardMarkers(msg.content) : '';
    // k10-C：本次长按是否落在段落上（段落键仅对同一条消息有效，防跨气泡陈旧命中）
    const hit = this._lastParaHit;
    const paraKey = (hit && hit.msgId === id) ? hit.key : '';
    this._lastParaHit = null;
    // k6 波2 P4.1/4.2 可见条件（开菜单时一次算定）：
    //   canRegen：AI + 非失败 + 非流式 + 宿主空闲 + 是本消息列表中最后一条 AI 回复
    //     （重生成会移除原消息并在底部重流——只有「末条 AI」语义才成立）
    //   canCite：msg.citations 为非空数组（引用抽屉数据层已齐，见 streamHost done 事件）
    const lastAiOk = msg && !msg.error && !msg.streaming && !streamHost.active
      && !!msg.retryText && this._isLastAiMessage(id);
    const citeOk = msg && Array.isArray(msg.citations) && msg.citations.length > 0;
    this.setData({
      actionMenu: {
        show: true, msgId: id, role, kept: !!(msg && msg.kept), paraKey,
        canRegen: !!(role === 'ai' && lastAiOk),
        canCite: !!(role === 'ai' && citeOk),
      },
    });
  },

  /* P4.1 可见条件辅助：msgId 是否为本消息列表中最后一条 AI 回复 */
  _isLastAiMessage(id) {
    const msgs = this.data.messages || [];
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'ai') return msgs[i].id === id;
    }
    return false;
  },

  closeActionMenu() {
    this.setData({ actionMenu: { show: false, msgId: '', role: '', paraKey: '', canRegen: false, canCite: false } });
  },

  /* 消息区点击：选取模式自动退出（多选模式不退出——勾选由气泡点按负责）。
     B3-3 D 修复：选中气泡自身已加 catchtap（wxml），气泡内点击被吞掉、
     不冒泡到此处——iOS 上选取手柄/原生菜单操作后点击气泡不会清掉选择，
     点气泡外空白/输入区仍可退出选取模式（保留逃生出口）。 */
  onListTap() {
    if (this.data.multiMode) return;
    if (this.data.selectMsgId) this.setData({ selectMsgId: '', selParaKey: '' });
  },

  /* ═══ k10-C 文字选取：乙覆盖层（可行时）→ 甲兜底（高亮+引导+复制本段） ═══ */

  /* 菜单「选取文字」点击：
     - 长按落在段落上且乙可行 → _openSelOverlay（textarea 覆盖层预设该段选区）
     - 段落存在但乙不可行/异常 → 甲：selectMsgId + 段落高亮（md-hl）+ 气泡顶部
       引导小字（wxml .sel-guide：「长按这段文字即可拖动选择」——说明为何需
       二次长按：系统原生选择只能由用户自己长按唤起，无 API 预设）
     - 长按落在空白/装饰区（无段落）→ 保持旧语义：整泡 selectable + toast 引导 */
  _enterTextSelect(msgId, paraKey) {
    const msg = this._findMessage(msgId);
    if (!msg) return;
    const model = chatSelect.paragraphModel(msg);
    const para = (paraKey && model.byKey[paraKey]) ? model.byKey[paraKey] : null;
    if (!para) {
      this.setData({ selectMsgId: msgId, selParaKey: '' });
      wx.showToast({ title: '长按文字即可选取', icon: 'none', duration: 2000 });
      return;
    }
    if (this._textOverlayFeasible(msg, model, para)) {
      this._openSelOverlay(msgId, model, para);
      return;
    }
    this.setData({ selectMsgId: msgId, selParaKey: para.key });
  },

  /* 乙可行性判定（清晰可测：常量开关 + 平台名单 + 文本/偏移阈值 + 能力/异常兜底） */
  _textOverlayFeasible(msg, model, para) {
    try {
      // 引擎开关：默认常量 'a'（全平台甲）；真机/单测实验乙可用
      // page._textSelEngine = 'b' 实例覆盖（不改源码即可调试，见文件头 k10-C 注释）
      const engine = (typeof this._textSelEngine === 'string') ? this._textSelEngine : TEXT_SEL_ENGINE;
      if (engine === 'a') return false;
      const sys = wx.getSystemInfoSync ? wx.getSystemInfoSync() : {};
      if ((sys.platform || '') === 'ios' && !TEXT_SEL_IOS_OVERLAY) return false;
      if (!para || !model || !model.text) return false;
      if (msg.image) return false;                               // 图片+文字混合：几何不可靠
      if (model.text.length > TEXT_SEL_MAX_TEXT) return false;   // 超长：段落落在首屏外
      if (para.start > TEXT_SEL_MAX_PARA_START) return false;    // 同上（textarea 无法预滚）
      if (typeof this.createSelectorQuery !== 'function') return false;
      return true;
    } catch (e) {
      return false;                                              // 异常 → 甲
    }
  },

  /* 乙：打开覆盖层。先落甲态（高亮+引导，任何失败都停留在甲可继续二次长按），
     测量 .jz-body/.user-note 实际矩形后以纯文本 textarea 原位垫上并 focus +
     selection-start/end 预设长按段落 → 用户可见两端可拖选区。
     滚动联动：全屏 mask 阻断页面滚动；流式更新/程序滚动触发 _closeTextOverlay。 */
  _openSelOverlay(msgId, model, para) {
    const msg = this._findMessage(msgId);
    if (!msg) return;
    this.setData({ selectMsgId: msgId, selParaKey: para.key });
    let done = false;
    const finish = (rect) => {
      if (done) return;
      done = true;
      if (!rect || !rect.width || !rect.height) return;   // 测量失败 → 停留甲态
      const L = model.text.length;
      this.setData({
        selOverlay: {
          show: true, msgId, role: msg.role || 'ai', text: model.text,
          start: Math.max(0, Math.min(para.start, L)),
          end: Math.max(0, Math.min(para.end, L)),
          top: Math.round(rect.top), left: Math.round(rect.left),
          width: Math.round(rect.width), height: Math.round(rect.height),
          focus: true,
        },
      });
    };
    try {
      this.createSelectorQuery()
        .select('.sel-body-' + msgId)
        .boundingClientRect(finish)
        .exec();
    } catch (e) { /* 落 catch 外兜底 */ finish(null); }
    setTimeout(() => finish(null), 600);   // 测量兜底超时：不弹覆盖层，甲态保留
  },

  /* 关闭乙覆盖层（点外部/失焦/滚动/流式更新）：恢复原气泡；甲态（selectMsgId +
     高亮 + 引导）保留，用户仍可二次长按走系统原生选择 */
  _closeTextOverlay() {
    if (!this.data.selOverlay.show) return;
    this.setData({
      selOverlay: {
        show: false, msgId: '', role: '', text: '', start: 0, end: 0,
        top: 0, left: 0, width: 0, height: 0, focus: false,
      },
    });
  },

  /* 覆盖层聚焦（textarea 无 readonly、聚焦必弹键盘）→ 立即 hideKeyboard 尽力抑制。
     iOS 程序聚焦下是否保留选区手柄不保证（平台限制注释见文件头 k10-C）——真机验证
     不可靠时由主会话将 TEXT_SEL_ENGINE 拨回 'a'（甲默认）。 */
  onSelOvFocus() {
    try {
      if (wx.hideKeyboard) wx.hideKeyboard({});
    } catch (e) { /* ignore */ }
  },

  onSelOvBlur() {
    // 延迟关闭：允许聚焦/失焦抖动自愈；期间 mask 点击同样走 closeTextOverlay（幂等）
    setTimeout(() => this._closeTextOverlay(), 150);
  },

  /* 菜单普通项：复制本段 / 复制 / 选取文字 / 朗读 / 意见反馈 / 删除 */
  actItem(e) {
    const k = e.currentTarget.dataset.k;
    const { msgId, paraKey } = this.data.actionMenu;
    const msg = this._findMessage(msgId);
    this.closeActionMenu();
    if (!msg) return;
    if (k === 'copyPara') {
      // k10-C 甲兜底：复制被按段落全文（与复制全文同一清洗管线：段落模型由
      // stripCardMarkers 后同款文本构建——卡片标记/装饰不暴露给用户）
      const para = chatSelect.paragraphModel(msg).byKey[paraKey];
      if (para) wx.setClipboardData({ data: para.text });
      return;
    } else if (k === 'copy') {
      // E2-2：复制走剥标记后的纯文本（卡片标记不暴露给用户）
      wx.setClipboardData({ data: cardUtil.stripCardMarkers(msg.content) });
    } else if (k === 'select') {
      // k10-C：乙 textarea 覆盖层（可行时）→ 预设长按段落选区；否则甲兜底
      // （selectMsgId + 段落高亮 + 气泡顶部引导小字，替代旧 toast 引导）
      this._enterTextSelect(msgId, paraKey || '');
    } else if (k === 'emoji') {
      // v1.2 表情反应：打开 emoji 选择弹层
      this._openEmojiFor(msgId);
    } else if (k === 'multi') {
      // v1.3 多选：进入勾选模式（批量收藏 / 分享）
      this._enterMulti();
      return;   // 已由 _enterMulti 关闭菜单
    } else if (k === 'speak') {
      // E2-2-FIX：长按菜单朗读与气泡直接朗读（speakMessage）同口径——
      // 走剥标记后的纯文本，[card:…] 标记不被 TTS 读出（标记不暴露三出口之一）
      this._playWithTts(msgId, cardUtil.stripCardMarkers(msg.content), true);
    } else if (k === 'regen') {
      // k6 波2 P4.1 重新生成：移除原消息 → 用原提问重流（streamHost.retry）。
      // 无确认弹窗（元宝/豆包即时感）；宿主 active 时内部静默 return，菜单侧已按
      // canRegen 不可见兜底防静默空点。P4.5 作废旧 consultation 行需后端接口
      // （voidConsultation 不存在，见 k10 报告后续项）——本批不接作废。
      if (streamHost.active) return;
      streamHost.retry(msg.id, String(msg.retryText || ''), msg.tag || '');
    } else if (k === 'cite') {
      // k6 波2 P4.2 查看引用：复用角标同款引用抽屉（空态由可见条件挡掉，双保险仍判）
      this._openCiteDrawer(msgId);
    } else if (k === 'feedback') {
      // k6 波2 P4.6：意见反馈 → 元宝式反馈面板（与 footer 踩同面板）
      this.openFeedbackPanel(msgId);
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

  /* T9 兜底（批次 2 B3-25）：长按收藏/取消收藏直连后端。
     原实现收藏只打本地 kept 标记——收藏页首启导入（_tryImportLocal）是兜底，
     覆盖不到「一直没打开收藏页」的用户 → 收藏永远只存本地。这里 best-effort
     同步：type=晨笺判定→jian 否则 chat、ref_id=消息 id、summary 截 100 字
     （与收藏页 _tryImportLocal 完全同口径，后端 UNIQUE 幂等，重复收藏
     already:true 不报错）。
     - keep 成功 → 打 favImported：后端条目接管展示，收藏页本地兜底不再重复展示
       （不打标记则收藏页「本地 kept 条目 + 后端条目」双份显示，造成重复）
     - 失败（G2 A5/A7）：本地 kept 保留（纯本地收藏为设计内行为），但必须明示
       「云端同步失败」——不得静默装成功；收藏页首启导入仍可兜底 */
  _syncKeepBackend(msg, on) {
    if (!msg || !msg.id) return;
    const type = isJianEntry(msg) ? 'jian' : 'chat';
    const refId = String(msg.id).slice(0, 128);
    if (on) {
      api.favAdd({
        type,
        ref_id: refId,
        summary: String(msg.content || '').slice(0, 100),
      }).then(() => {
        streamHost.patchMessage(msg.id, { favImported: true });
      }).catch(() => {
        wx.showToast({ title: '收藏暂存本机，云端同步失败', icon: 'none' });
      });
    } else {
      api.favRemove(type, refId).catch(() => { /* 静默：deleted=false 视为本就不存在 */ });
    }
  },

  /* 菜单反馈项：点赞（点亮 + 轻提示）/ 点踩（k6 波2 P4.6 → 元宝式反馈面板）/
     收藏（持久化 kept） */
  actFeedback(e) {
    const k = e.currentTarget.dataset.k;
    const { msgId } = this.data.actionMenu;
    this.closeActionMenu();
    if (k === 'keep') {
      const msg = this._findMessage(msgId);
      if (!msg) return;
      const on = !msg.kept;
      streamHost.patchMessage(msgId, { kept: on, keptAt: on ? Date.now() : 0 });
      // B3-25：本地标记之外直连后端（仅 AI 回复同步——收藏页/后端本就只收纳
      // AI 回复，用户消息的本地 kept 行为保持不变）
      if (msg.role === 'ai') this._syncKeepBackend(msg, on);
      wx.showToast({ title: on ? '已收藏 · 我的页可查看' : '已取消收藏', icon: 'none' });
      return;
    }
    if (k === 'down') {
      // P4.6：菜单点踩与 footer 踩同语义——打开反馈面板（不再直发 negative）
      this.openFeedbackPanel(msgId);
      return;
    }
    this._toggleFbCore(msgId, k, '谢谢认可，我会继续精进');
  },

  /* k6 波2 P4.3：AI footer 复制钮——与菜单「复制」同一行为（E2-2 口径：
     stripCardMarkers 后写剪贴板，卡片标记不暴露；系统自带「内容已复制」提示） */
  footerCopyText(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const msg = this._findMessage(e.currentTarget.dataset.id);
    if (!msg) return;
    wx.setClipboardData({ data: cardUtil.stripCardMarkers(msg.content) });
  },

  /* ═══ v1.3 多选收藏 / 分享（长按菜单「多选」→ 勾选模式 → 批量收藏 / 分享页） ═══ */

  /* 进入勾选模式：顶部出现操作条（已选 N 条/全选/收藏/分享/取消），气泡左上角出勾选框 */
  _enterMulti() {
    this._closeTextOverlay();
    this.setData({
      multiMode: true,
      multiSel: {},
      multiCount: 0,
      multiAll: false,
      selectMsgId: '',
      selParaKey: '',
      actionMenu: { show: false, msgId: '', role: '', paraKey: '', canRegen: false, canCite: false },
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
        this._syncKeepBackend(msg, true);   // B3-25：批量收藏同样直连后端
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

  /* ═══ k6 波2 P4.6 元宝式反馈面板（踩不再直发 negative——footer 踩钮/长按菜单
     点踩/意见反馈三入口同面板；旧 fbMenu 网格整体退役，grep 清零） ═══ */

  /* 打开面板（重置选区与补充文本）。三入口共用：
     footer 踩钮（toggleFb k=down）/ 长按菜单点踩（actFeedback k=down）
     / 长按菜单意见反馈（actItem k=feedback） */
  openFeedbackPanel(msgId) {
    const msg = this._findMessage(msgId);
    if (!msg || !msgId) return;
    this.setData({ fbSheet: { show: true, msgId, reasons: {}, note: '', canSubmit: false } });
  },

  closeFbSheet() {
    if (!this.data.fbSheet.show) return;
    this.setData({ fbSheet: { show: false, msgId: '', reasons: {}, note: '', canSubmit: false } });
  },

  /* 原因 chip 点击：多选切换（选中=朱砂实心） */
  onFbReasonTap(e) {
    const opt = e.currentTarget.dataset.opt;
    if (!opt) return;
    const sheet = Object.assign({}, this.data.fbSheet);
    const reasons = Object.assign({}, sheet.reasons);
    if (reasons[opt]) delete reasons[opt]; else reasons[opt] = true;
    sheet.reasons = reasons;
    sheet.canSubmit = Object.keys(reasons).length > 0 || String(sheet.note || '').trim().length > 0;
    this.setData({ fbSheet: sheet });
  },

  /* 「我要补充」输入（可不填） */
  onFbNoteInput(e) {
    const sheet = Object.assign({}, this.data.fbSheet);
    sheet.note = String(e.detail && e.detail.value || '');
    sheet.canSubmit = Object.keys(sheet.reasons || {}).length > 0 || sheet.note.trim().length > 0;
    this.setData({ fbSheet: sheet });
  },

  /* 提交：原因多选「、」连接 + 补充文本 → 本地留档 + 后端 negative（有咨询 ID）。
     面板点亮口径（brief P4.6）：成功后踩图标点亮 + toast「已收到反馈」；无咨询 ID →
     只点亮不发后端（本地留档为证据，旧网格 submitFeedbackReason 同口径）。
     上报失败 → 不点亮 + 明确失败提示（G2 B1 语义：不静默装成功）。 */
  submitFbSheet() {
    const sheet = this.data.fbSheet;
    const msg = this._findMessage(sheet.msgId);
    if (!sheet.show || !msg) return;
    const reasons = FB_REASON_GROUPS.reduce((acc, g) => acc.concat(g.opts), [])
      .filter((o) => sheet.reasons[o]);
    const note = String(sheet.note || '').trim();
    const parts = [reasons.join('、'), note].filter((s) => s);
    if (!parts.length) return;                       // 空选择：提交钮已置灰，双保险
    const label = parts.join('；');
    this.closeFbSheet();
    this._logFeedback(msg, label);
    if (!msg.consultationId) {
      this.setData({ [`fb.${sheet.msgId}-down`]: true });
      wx.showToast({ title: '已收到反馈', icon: 'none' });
      return;
    }
    api.feedback(msg.consultationId, 'negative', label)
      .then(() => {
        this.setData({ [`fb.${sheet.msgId}-down`]: true });
        wx.showToast({ title: '已收到反馈', icon: 'none' });
      })
      .catch(() => {
        wx.showToast({ title: '反馈提交失败，请重试', icon: 'none' });
      });
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

  /* 原型 toggleFb：反馈点亮（up/down 有咨询 ID 时上报后端；keep → 持久化收藏 kept）
     k6 波2 P4.6：down 不再直发——footer 踩钮打开元宝式反馈面板（点赞仍为
     图标点亮 + 轻提示「谢谢认可，我会继续精进」，不弹窗） */
  toggleFb(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应（点气泡=勾选）
    const { id, k } = e.currentTarget.dataset;
    if (k === 'keep') {
      const msg = this._findMessage(id);
      if (!msg) return;
      const on = !msg.kept;
      streamHost.patchMessage(id, { kept: on, keptAt: on ? Date.now() : 0 });
      // G2 A6：气泡尾星标收藏此前完全无后端调用 → 与长按菜单同链路同步（失败提示见 _syncKeepBackend）
      if (msg.role === 'ai') this._syncKeepBackend(msg, on);
      wx.showToast({ title: on ? '已收藏 · 我的页可查看' : '已取消收藏', icon: 'none' });
      return;
    }
    if (k === 'down') {
      // P4.6：踩 → 反馈面板（取代直发 negative）
      this.openFeedbackPanel(id);
      return;
    }
    this._toggleFbCore(id, k, '谢谢认可，我会继续精进');
  },

  /* 赞/踩点亮核心（P4.6 起产品入口仅赞使用；踩旧直发路径退役，核心保留供
     状态一致性与既有单测）。onToast：点亮成功后停留点亮态时的轻提示文案。 */
  _toggleFbCore(id, k, onToast) {
    const key = id + '-' + k;
    const on = !this.data.fb[key];
    this.setData({ [`fb.${key}`]: on });
    if ((k === 'up' || k === 'down') && on) {
      const msg = this._findMessage(id);
      if (msg && msg.consultationId) {
        api.feedback(msg.consultationId, k === 'up' ? 'positive' : 'negative')
          .then(() => {
            if (onToast && this.data.fb[key]) {
              wx.showToast({ title: onToast, icon: 'none', duration: 2000 });
            }
          })
          .catch(() => {
            // G2 B1：上报失败 → 回滚点亮态 + 明确提示（失败不点亮，不静默）
            this.setData({ [`fb.${key}`]: false });
            wx.showToast({ title: '反馈失败，请重试', icon: 'none' });
          });
      } else {
        // G3 H-12：无咨询记录（离线/回退/未完成生成的回复）→ 点亮即假成功——
        // 反馈根本没上报。回滚点亮态并如实提示，不静默
        this.setData({ [`fb.${key}`]: false });
        wx.showToast({ title: '反馈未提交：该回复缺少咨询记录', icon: 'none' });
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
    this._closeTextOverlay();
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
      actionMenu: { show: false, msgId: '', role: '', paraKey: '', canRegen: false, canCite: false },
      fbSheet: { show: false, msgId: '', reasons: {}, note: '', canSubmit: false },
      selectMsgId: '',
      selParaKey: '',
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
    if (mode === 'voice' && !this.data.micAvailable) {
      // UX批1 M-7：语音插件未配置 → 入口置灰，点按即提示（不用等长按）
      wx.showToast({ title: '语音输入未开启，请使用键盘输入', icon: 'none' });
      return;
    }
    if (mode === 'text' && (this.data.isRecording || this.data.converting)) {
      // 录音中切回键盘：取消本次录音
      this._finishRecording(false);
    }
    this.setData({ inputMode: mode, inputFocused: false });
    // k6-P1：输入条高由流内 flex 自然决定（voice 按住条 / 文字行数差异自动吸收），
    // 不再有 JS 高度机件（旧 B4-1 _updateInputBarH 调用已随结构修复移除）
  },

  /* ═══ k6-P2 文字态长按小话筒直达录音（不必先切语音模式） ═══
     微信 bindlongpress(≈350ms) 与 bindtap 天然互斥：短按仍走 switchInputMode 切
     语音模式，长按直达「按住说话」。流程：守卫 → 走既有 switchInputMode 内部路径
     切 voice 态（textarea 整块卸载 → 键盘自然收起，仓库既有收键盘机制）→
     _ensureRecordPermission → _startRecording（手指仍按住，rec-bar 接住 touchend，
     voice-hold→rec-bar 中途切换为生产已验证模式）。语音/流式状态机核心零改动，
     本方法只加入口与触摸会话簿记；会话结束复位见 _clearLongPressVoice（发送完成/
     取消/太短/识别失败/中断等一切收尾路径均会经过）。 */
  micLongPress(e) {
    if (this.data.isRecording || this.data.converting || this.data.streaming) return;
    if (!this.data.micAvailable) return;      // 置灰态（.in-ic-off）：不响应（短按同规则）
    if ((this.data.inputText || '').trim()) {
      wx.showToast({ title: '请先发送或清空输入，再长按说话', icon: 'none' });
      return;
    }
    const t = (e.touches && e.touches[0]) || {};
    this._voiceFromLongPress = true;          // 会话结束 → 回文字态（见 _clearLongPressVoice）
    this._touchActive = true;                 // 触摸会话簿记（与 micTouchStart 同口径）
    this._touchY = t.clientY || 0;
    // k6 wave1 review M-1：bindlongpress 在手指按下约 350ms 后才触发，此刻才起算
    // 会让「真实按住 ≥0.8s 即发送」的按住条语义变成 ≥~1.15s（0.8–1.15s 波段的
    // 长按松手被误判「说话时间太短」取消）。_touchStartAt 回拨 350ms ≈ 手指真正
    // 按下时刻，与 voice-hold（micTouchStart 于 touchstart 起算）完全同一口径。
    this._touchStartAt = Date.now() - 350;
    this.switchInputMode({ currentTarget: { dataset: { mode: 'voice' } } });
    this._ensureRecordPermission((ok) => {
      if (!ok) {
        // 授权被拒/弹窗：本次触摸已被消耗（长按不会再来 touchend）——
        // 留在 voice 态（可再点按住条），清标记防止后续会话误复位
        this._voiceFromLongPress = false;
        return;
      }
      if (!this._touchActive) {
        // 授权弹窗消耗了本次长按（触摸已结束）：不自动开录，按既有口径提示
        this._voiceFromLongPress = false;
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

  /* k6-P2 收尾复位：长按直达语音的一切会话结束路径（发送完成/取消/太短/识别失败/
     中断/清理）调用本方法——若来自文字态长按入口（_voiceFromLongPress）→
     复位 inputMode='text' 并清标记（inputFocus 恒 false，不自动弹键盘）；
     短按手动进语音模式（无标记）不受影响。 */
  _clearLongPressVoice() {
    if (!this._voiceFromLongPress) return;
    this._voiceFromLongPress = false;
    if (this.data.inputMode !== 'text') {
      this.setData({ inputMode: 'text', inputFocused: false });
    }
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
      this.setData({ micAvailable: false });  // UX批1 M-7：语音入口置灰
      return;
    }
    this.setData({ micAvailable: true });
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
      this._clearLongPressVoice(); // k6-P2：长按会话取消/太短 → 复位回文字态
      return;
    }
    this.setData({ isRecording: false, recCanceling: false, recSeconds: 0, converting: true });
    try {
      this._recMgr && this._recMgr.stop(); // onStop → _handleRecognitionResult
    } catch (e) {
      console.warn('[Chat] 录音停止失败:', e);
      this.setData({ converting: false });
      wx.showToast({ title: '录音停止失败，请重试', icon: 'none' });
      this._clearLongPressVoice(); // k6-P2：异常路径同样收尾复位
    }
  },

  /* 识别完成 → 元宝式：说完即发（生成中 → 排队，消息立即上屏） */
  _handleRecognitionResult(res) {
    this._cleanupTimer();
    this.setData({ isRecording: false, converting: false, recCanceling: false });
    if (this._dropResult) {
      this._dropResult = false;
      this._clearLongPressVoice(); // k6-P2：识别结果被丢弃（取消会话）→ 复位回文字态
      return;
    }
    const text = ((res && res.result) || '').trim();
    if (!text) {
      wx.showToast({ title: '没听清，请再试一次', icon: 'none' });
      this._clearLongPressVoice(); // k6-P2：识别为空 = 会话结束 → 复位回文字态
      return;
    }
    this._send(text);
    this._clearLongPressVoice(); // k6-P2：发送完成 → 复位回文字态（键盘不自动弹出）
  },

  _handleRecognitionError(res) {
    this._cleanupTimer();
    this.setData({ isRecording: false, converting: false, recCanceling: false });
    if (this._dropResult) {
      this._dropResult = false;
      this._clearLongPressVoice(); // k6-P2：错误结果被丢弃 → 复位回文字态
      return;
    }
    console.warn('[Chat] 语音识别失败:', res);
    wx.showToast({ title: '识别失败，请再试一次', icon: 'none' });
    this._clearLongPressVoice(); // k6-P2：识别失败 = 会话结束 → 复位回文字态
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
    this._clearLongPressVoice(); // k6-P2：录音会话被中断（切走/清空/新开）→ 清标记并复位
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

  /* 点朗读：合成并播放该消息语音；再次点击停止
     E2-2：朗读走剥标记后的纯文本（卡片标记不该被读出来） */
  speakMessage(e) {
    if (this.data.multiMode) return;   // v1.3 多选：气泡内交互不响应
    const id = e.currentTarget.dataset.id;
    const msg = this._findMessage(id);
    if (!msg || !this._audioCtx) return;
    this._playWithTts(id, cardUtil.stripCardMarkers(msg.content), true);
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
        // G3 H-10：后端已按配置（PUBLIC_BASE_URL）返回完整 URL；相对路径不再拼
        // 127.0.0.1（真机上指向手机自身，语音必然不可达）——非完整地址视为失败，
        // 走下方「语音合成失败」提示，不静默播放无效地址
        throw new Error('audio_url 非完整地址');
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
