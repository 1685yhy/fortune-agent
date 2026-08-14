// 我的 — 一卷手札（原型 MeScreen：印章/名号/四柱竖排/同行第N晚/列表 + TabBar）
const api = require('../../utils/api');
const lunar = require('../../utils/lunar');
const payment = require('../../utils/payment');
const theme = require('../../utils/theme');

const HOUR_CN = ['子时', '丑时', '寅时', '卯时', '辰时', '巳时', '午时', '未时', '申时', '酉时', '戌时', '亥时'];

/* 原型 PILLARS / ME_ROWS（dir_b.html 977-985 行） */
const DEFAULT_PILLARS = [
  { l: '年', c: '戊寅' }, { l: '月', c: '丁巳' }, { l: '日', c: '庚申' }, { l: '时', c: '己卯' },
];
/* 基础行（val 由 _buildRows 按档案/会员状态动态填充） */
const BASE_ROWS = [
  { icon: '/assets/images/ic-edit.png', label: '档案', action: 'persons' },
  { icon: '/assets/images/ic-lantern.png', label: '重新看引导', action: 'onboarding' },
  { icon: '/assets/images/ic-seal.png', label: '会员', action: 'member' },
  { icon: '/assets/images/ic-book2.png', label: '我的命书', action: 'reports' },
  { icon: '/assets/images/ic-keep.png', label: '我的收藏', action: 'favorites' },
  { icon: '/assets/images/ic-chat.png', label: '对话历史', action: 'history' },
  { icon: '/assets/images/ic-moon.png', label: '解梦手记', action: 'dreams' },
  { icon: '/assets/images/ic-seal.png', label: '灯下印记 · 守夜人', action: 'nightmark' },
  { icon: '/assets/images/ic-bell.png', label: '开灯提醒', action: 'sub', sub: true },
  { icon: '/assets/images/ic-kebab.png', label: '设置', action: 'settings' },
];

const STORAGE_KEY = 'ylm_chat_messages';
const ARCHIVE_KEY = 'ylm_chat_archives';

Page({
  data: {
    navOff: 0,
    displayName: '小晚',
    birthdayText: '1998.05.12 卯时',
    lunarBirthday: '戊寅年 · 四月十七',
    pillars: DEFAULT_PILLARS,
    nights: 231,
    meRows: BASE_ROWS,
    memberStatus: '基础版',
    memberDialogVisible: false,
    memberPlans: [],
    subOn: false,
    memberIsMember: false,
    memberBenefits: [],
    memberExpireText: '',
    favCount: 0,                // v1.1 收藏数（我的收藏行 val）
    personCount: 0,             // 档案行 val：本地缓存命主数
    curTab: 'me',
    dark: false,
    /* 登录态展示（v5.1）：头像/状态标签（登录/退出/注销已移入设置页） */
    avatarUrl: '',              // 微信头像（无则印章「明」兜底）
    loginTag: '',               // 微信登录 / 体验用户（未登录不显示）
    realLogin: false,           // 标签配色：真实微信登录（朱砂） vs 体验用户（低调墨色）
    /* 头像昵称采集（Task 2：chooseAvatar + nickname → 弹层保存，服务端落库） */
    nicknameSet: false,         // 已采集昵称（决定引导文案 / 编辑按钮）
    profileDialogVisible: false, // 头像昵称采集弹层开关
    draftNickname: '',          // 弹层昵称草稿（默认当前昵称）
    draftAvatar: '',            // 弹层头像临时路径预览（未保存前仅本地显示）
  },

  onLoad() {
    this._initNavOff();
    // 会员开通方案（首月/月度，价格与后端 SUBSCRIBE_PLANS 对齐）
    const plans = ['first_month', 'monthly']
      .map((id) => payment.getProduct(id))
      .filter((p) => !!p)
      .map((p) => ({
        id: p.id,
        name: p.name,
        priceLabel: p.priceLabel,
        description: p.description,
        icon: p.icon,
      }));
    this.setData({ memberPlans: plans });
    theme.bindTheme(this, () => this._buildRows());
  },

  onShow() {
    this._loadFavCount();
    this._loadPersonCount();
    this._deriveUser();
    this._deriveIdentity();
    this._deriveNights();
    this._loadMember();
    this._loadSubscription();
  },

  /* v1.1 收藏数：本机会话 + 归档里 kept 的 AI 回复数（与 favorites 页数据源一致） */
  _loadFavCount() {
    let count = 0;
    try {
      const scan = (msgs) => {
        (Array.isArray(msgs) ? msgs : []).forEach((m) => {
          if (m && m.role === 'ai' && m.kept) count++;
        });
      };
      scan(wx.getStorageSync(STORAGE_KEY));
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      (Array.isArray(arch) ? arch : []).forEach((a) => scan(a.messages));
    } catch (e) { /* ignore */ }
    if (count !== this.data.favCount) {
      this.setData({ favCount: count });
      this._buildRows();
    }
  },

  /* 会员状态（P1：真实接口，失败/未登录回退基础版） */
  _loadMember() {
    api.getMemberInfo()
      .then((info) => {
        if (info && info.isMember) {
          const expire = info.expireDate ? String(info.expireDate).slice(0, 10) : '';
          this.setData({
            memberIsMember: true,
            memberStatus: expire ? `会员 · ${expire}` : '已开通',
            memberExpireText: expire,
            memberBenefits: (info.benefits && info.benefits.length ? info.benefits : ['每日运势 · 完整解读', '专属命书 · 无限查阅', '深度问答 · 畅聊不设限']),
          });
        } else {
          this.setData({ memberIsMember: false, memberStatus: '基础版', memberBenefits: [], memberExpireText: '' });
        }
        this._buildRows();
      })
      .catch(() => {
        this.setData({ memberIsMember: false, memberStatus: '基础版', memberBenefits: [], memberExpireText: '' });
        this._buildRows();
      });
  },

  /* 开灯提醒状态：GET /api/user/subscription（后端就绪则真实，否则本地兜底） */
  _loadSubscription() {
    api.getSubscription()
      .then((res) => {
        if (res && typeof res.daily_push === 'boolean') {
          this.setData({ subOn: res.daily_push });
          try { wx.setStorageSync('ylm_daily_push', res.daily_push); } catch (e) { /* ignore */ }
        }
        this._buildRows();
      })
      .catch(() => {
        let on = false;
        try { on = !!wx.getStorageSync('ylm_daily_push'); } catch (e) { /* ignore */ }
        this.setData({ subOn: on });
        this._buildRows();
      });
  },

  /* 开灯提醒开关：乐观更新 → POST /api/user/subscription → 失败回滚 */
  onSubChange(e) {
    const on = !!e.detail.value;
    const prev = this.data.subOn;
    this.setData({ subOn: on });
    this._buildRows();
    try { wx.setStorageSync('ylm_daily_push', on); } catch (err) { /* ignore */ }
    api.updateSubscription(on)
      .then(() => wx.showToast({ title: on ? '已开启每晚提醒' : '已关闭提醒', icon: 'none' }))
      .catch(() => {
        this.setData({ subOn: prev });
        this._buildRows();
        wx.showToast({ title: '保存失败，请检查网络', icon: 'none' });
      });
  },

  /* 行 val 动态化（图标随暗黑模式换暗色变体；按 label 匹配，避免行序变更后索引错位） */
  _buildRows(hasBazi) {
    const hb = hasBazi !== undefined ? hasBazi : !!this.data.birthdayText;
    const rows = BASE_ROWS.map((r) => ({
      ...r,
      icon: r.icon,   // 夜间模式已移除：恒用白天图标
    }));
    rows.forEach((r) => {
      if (r.label === '档案') r.val = this.data.personCount > 0 ? `${this.data.personCount} 位命主` : (hb ? '八字已设' : '未设置');
      else if (r.label === '会员') r.val = this.data.memberStatus;
      else if (r.label === '我的收藏') r.val = this.data.favCount > 0 ? `${this.data.favCount} 笺` : '还未收藏';
      else if (r.label === '开灯提醒') r.val = this.data.subOn ? '每晚 21:30' : '已关闭';
    });
    this.setData({ meRows: rows });
  },

  /* 档案行 val：本地缓存命主数（接口不可用不阻塞；persons/bazi 页保存后同步） */
  _loadPersonCount() {
    let count = 0;
    try {
      const list = wx.getStorageSync('ylm_persons');
      count = Array.isArray(list) ? list.length : 0;
    } catch (e) { /* ignore */ }
    if (count !== this.data.personCount) {
      this.setData({ personCount: count });
      this._buildRows();
    }
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 用户数据绑定：globalData.baziInfo → 手札；缺失时拉取 profile（后端 bazi_info 契约 {year,month,day,hour,gender}） */
  _deriveUser() {
    const gd = (getApp() && getApp().globalData) || {};
    const b = gd.baziInfo || null;
    const patch = {};
    if (gd.userInfo && gd.userInfo.nickName) patch.displayName = gd.userInfo.nickName;

    if (b) {
      this._applyBaziToView(b);
      return;
    }
    // 无本地档案：拉取真实档案（已保存过八字的用户回显）
    api.getUserProfile()
      .then((profile) => {
        const info = (profile && profile.bazi_info) || null;
        if (!info || !info.year) return;
        const app = getApp();
        if (app && app.globalData) {
          app.globalData.hasBazi = true;
          app.globalData.baziInfo = info;
        }
        this._applyBaziToView(info);
      })
      .catch(() => { /* 未登录/后端未就绪：保持原型默认 */ });
  },

  _applyBaziToView(b) {
    const y = b.year || b.birthYear;
    const m = b.month || b.birthMonth;
    const d = b.day || b.birthDay;
    if (!y || !m || !d) return;

    const hourIdx = this._hourToIndex(b.hour !== undefined && b.hour !== null ? b.hour : b.birthHour);
    const hour = (hourIdx >= 0 && HOUR_CN[hourIdx]) || '';
    const patch = {
      birthdayText: `${y}.${String(m).padStart(2, '0')}.${String(d).padStart(2, '0')}${hour ? ' ' + hour : ''}`,
      lunarBirthday: this._lunarLabel(Number(y), Number(m), Number(d)),
    };
    const vals = [b.year_pillar, b.month_pillar, b.day_pillar, b.hour_pillar];
    if (vals.every((v) => !!v)) {
      patch.pillars = ['年', '月', '日', '时'].map((l, i) => ({ l, c: vals[i] }));
    }
    this.setData(patch);
    this._buildRows(true);
  },

  /* 时辰字段（整点或序号）→ 时辰序号（0-11） */
  _hourToIndex(h) {
    const v = parseInt(h, 10);
    if (Number.isNaN(v) || v < 0) return -1;
    if (v <= 11) return v;                 // 旧数据：直接存了序号
    if (v === 23) return 0;                // 子时代表整点
    if (v % 2 === 1 && v <= 21) return (v + 1) / 2;  // 丑1 寅3 卯5 … 亥21
    return -1;
  },

  /* 公历 → 农历文案（如「农历六月廿五」） */
  _lunarLabel(y, m, d) {
    try {
      const t = lunar.solar2lunar(y, m, d);
      if (!t) return '';
      return `农历${lunar.formatLunarDate(y, m, d)}`;
    } catch (e) {
      return '';
    }
  },

  /* 第 N 晚：本地首见天数，无记录时原型默认 231 */
  _deriveNights() {
    try {
      const first = wx.getStorageSync('ylm_first_seen');
      if (!first) {
        wx.setStorageSync('ylm_first_seen', Date.now());
        return;
      }
      const days = Math.floor((Date.now() - first) / 86400000);
      if (days > 0 && days + 1 !== this.data.nights) {
        this.setData({ nights: days + 1 });
      }
    } catch (e) {
      console.warn('[Me] 首见时间读取失败');
    }
  },

  /* ═══ v5.1 登录态展示（头像/昵称/状态标签；登录/退出/注销已移入设置页） ═══
     身份态判定（app.js 契约）：
       - 真实微信登录：globalData.token 存在（userInfo 可能带 nickName/avatarUrl）
       - 体验用户：无 token 且 userId === 'local_user'（后端不可用时的本地兜底）
       - 未登录：无 token 且非 local_user（退出后；残留旧 userId 也归此态） */
  _deriveIdentity() {
    const gd = (getApp() && getApp().globalData) || {};
    const u = gd.userInfo || {};
    const token = gd.token || null;
    const isLocal = !token && gd.userId === 'local_user';
    const loggedOut = !token && !isLocal;

    if (loggedOut) {
      this.setData({ displayName: '未登录', avatarUrl: '', loginTag: '', realLogin: false, nicknameSet: false });
      return;
    }
    if (token) {
      // 真实昵称/头像优先取已保存值（本地缓存；头像存相对路径，渲染时拼 baseURL）
      const cache = this._readProfileCache();
      this.setData({
        realLogin: true,
        loginTag: '微信登录',
        nicknameSet: !!cache.nickname,
        displayName: cache.nickname || (u && u.nickName) || '小晚',
        avatarUrl: cache.avatarUrl ? api.getBaseURL() + cache.avatarUrl : ((u && u.avatarUrl) || ''),
      });
      return;
    }
    // 体验模式（local_user）
    this.setData({ displayName: '小晚', avatarUrl: '', loginTag: '体验用户', realLogin: false, nicknameSet: false });
  },

  /* ═══ 头像昵称缓存（Task 2：保存成功后本地缓存；GET /api/user/profile 暂无昵称字段，缓存即会话持久） ═══ */
  _readProfileCache() {
    let nickname = '';
    let avatarUrl = '';
    try { nickname = wx.getStorageSync('ylm_nickname') || ''; } catch (e) { /* ignore */ }
    try { avatarUrl = wx.getStorageSync('ylm_avatar_url') || ''; } catch (e) { /* ignore */ }
    return { nickname, avatarUrl };
  },

  /* ═══ 头像昵称采集弹层（Task 2：chooseAvatar + nickname 输入 → 上传头像 + 存昵称） ═══ */
  openProfileDialog() {
    if (!this.data.realLogin) {
      wx.showToast({ title: '请先微信登录', icon: 'none' });
      return;
    }
    this.setData({
      profileDialogVisible: true,
      draftNickname: this.data.nicknameSet ? this.data.displayName : '',
      draftAvatar: '',
    });
  },

  closeProfileDialog() {
    this.setData({ profileDialogVisible: false });
  },

  /* chooseAvatar 回调：临时路径仅本地预览（保存时才上传） */
  onChooseAvatar(e) {
    const path = e.detail && e.detail.avatarUrl;
    if (!path) return;
    this.setData({ draftAvatar: path });
  },

  onNicknameInput(e) {
    this.setData({ draftNickname: e.detail.value });
  },

  /* 保存：昵称非空 → saveProfile；头像已选 → uploadAvatar；成功 setData 刷新显示，失败 toast */
  onProfileSave() {
    if (this._profileSaving) return;
    const nickname = String(this.data.draftNickname || '').trim();
    if (!nickname) {
      wx.showToast({ title: '请填写昵称', icon: 'none' });
      return;
    }
    if (nickname.length > 20) {
      wx.showToast({ title: '昵称最长 20 个字符', icon: 'none' });
      return;
    }
    this._profileSaving = true;
    wx.showLoading({ title: '保存中…', mask: true });
    const hasAvatar = !!this.data.draftAvatar;
    const tasks = [api.saveProfile({ nickname })];
    if (hasAvatar) tasks.push(api.uploadAvatar(this.data.draftAvatar));
    /* allSettled：半成功也写成功的半边缓存（如昵称已存服务端但头像上传失败），幂等 */
    Promise.allSettled(tasks)
      .then((results) => {
        wx.hideLoading();
        this._profileSaving = false;
        this.setData({ profileDialogVisible: false });
        const nicknameOk = results[0].status === 'fulfilled';
        if (hasAvatar && results[1].status === 'fulfilled') {
          const up = results[1].value || {};
          const gd = (getApp() && getApp().globalData) || {};
          const rel = up.avatar_url || (gd.userId ? `/api/user/avatar/${gd.userId}` : '');
          if (rel) {
            try { wx.setStorageSync('ylm_avatar_url', rel); } catch (e) { /* ignore */ }
          }
        }
        if (nicknameOk) {
          try { wx.setStorageSync('ylm_nickname', nickname); } catch (e) { /* ignore */ }
        }
        this._deriveIdentity();
        if (nicknameOk && (!hasAvatar || results[1].status === 'fulfilled')) {
          wx.showToast({ title: '已保存', icon: 'none' });
        } else if (nicknameOk) {
          wx.showToast({ title: '昵称已保存，头像上传失败', icon: 'none' });
        } else {
          wx.showToast({ title: '保存失败，请重试', icon: 'none' });
        }
      });
  },

  /* 列表行点击（data-action 路由） */
  onRowTap(e) {
    const action = e.currentTarget.dataset.action;
    if (action === 'persons') {
      // 档案管理：多人命主（自己/家人/朋友；排盘直接选用）
      wx.navigateTo({ url: '/pages/persons/persons' });
    } else if (action === 'onboarding') {
      // 重新看引导（首启三步建档 · 可跳过）
      wx.navigateTo({ url: '/pages/onboarding/onboarding' });
    } else if (action === 'reports') {
      wx.reLaunch({ url: '/pages/reports/reports' });
    } else if (action === 'member') {
      this._openMemberDialog();
    } else if (action === 'favorites') {
      // v1.1 我的收藏：⭐收藏的回复列表（本机会话 + 归档扫描）
      wx.navigateTo({ url: '/pages/favorites/favorites' });
    } else if (action === 'history') {
      // 对话历史：归档夜话（搜索/预览/继续/删除）
      wx.navigateTo({ url: '/pages/history/history' });
    } else if (action === 'dreams') {
      // 解梦手记：夜话中的解梦回复收进此册
      wx.navigateTo({ url: '/pages/dreams/dreams' });
    } else if (action === 'nightmark') {
      // 灯下印记：守夜人成就（7 夜印章 / 30 夜长明灯）
      wx.navigateTo({ url: '/pages/night_mark/night_mark' });
    } else if (action === 'settings') {
      // 设置：账号与登录/隐私政策/注销等（原登录/退出/注销均收进此页）
      wx.navigateTo({ url: '/pages/settings/settings' });
    }
    // 其余行（开灯提醒）保持原样，无跳转
  },

  /* ── 会员开通（虚拟支付优先，mock 降级由 payment.js 统一处理） ── */
  _openMemberDialog() {
    this.setData({ memberDialogVisible: true });
  },

  closeMemberDialog() {
    this.setData({ memberDialogVisible: false });
  },

  noop() { /* 阻止遮罩点击穿透 */ },

  /* 选择套餐 → 支付 → 刷新会员状态 */
  buyPlan(e) {
    const planId = e.currentTarget.dataset.plan;
    if (!planId || this._buying) return;
    this._buying = true;
    payment.subscribeMember(planId)
      .then((res) => {
        if (res && res.success) {
          this.setData({ memberDialogVisible: false });
          this._loadMember();
        }
      })
      .catch(() => {
        wx.showToast({ title: '支付异常，请稍后再试', icon: 'none' });
      })
      .finally(() => { this._buying = false; });
  },

  /* 原型 onBack：返回今日 */
  goToday() {
    wx.reLaunch({ url: '/pages/today/today' });
  },

  /* 原型 onTab：底部栏切换 */
  onTab(e) {
    const t = e.currentTarget.dataset.tab;
    const url = { today: '/pages/today/today', chat: '/pages/chat/chat', book: '/pages/reports/reports', me: '/pages/me/me' }[t];
    if (url && !url.includes('/me/')) wx.reLaunch({ url });
  },
});
