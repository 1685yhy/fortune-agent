// 我的 — 一卷手札（原型 MeScreen：印章/名号/四柱竖排/同行第N晚/列表 + TabBar）
const api = require('../../utils/api');
const lunar = require('../../utils/lunar');
const persons = require('../../utils/persons');
const payment = require('../../utils/payment');
const theme = require('../../utils/theme');

/* k69 单一事实源：本地 HOUR_CN 表与 _hourToIndex 已删除 —— 时辰中文/序号映射
   一律走 utils/persons（shichenCN + hourToShichenIndex）。原本地实现把钟点当
   序号查表（10 → HOUR_CN[10] = 戌时，正解巳时），且与仓内其它页（paipan/
   duipan/hehun/bazi/persons 均引 utils/persons）口径分裂，是「我的」页
   生日显示错误三处缺陷中的时辰根源。 */

/* UX批1 I-2：移除虚构命盘兜底（DEFAULT_PILLARS/「小晚」）——无档案一律展示
   「未设置命主信息」占位，绝不让用户误认虚构八字为自己已保存的盘 */
/* 基础行（val 由 _buildRows 按档案/会员状态动态填充） */
const BASE_ROWS = [
  { icon: '/assets/images/ic-edit.png', label: '档案', action: 'persons' },
  { icon: '/assets/images/ic-lantern.png', label: '重新看引导', action: 'onboarding' },
  { icon: '/assets/images/ic-seal.png', label: '会员', action: 'member' },
  { icon: '/assets/images/ic-book.png', label: '命书', action: 'reports' }, // fix-later: 命书行换旧 ic-book(与 tabbar 测算的 ic-book2 去双职)
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
    displayName: '',          // UX批1 I-2：无真实昵称时渲染兜底「明灯友人」，不虚构
    birthdayText: '',
    lunarBirthday: '',
    pillars: [],
    hasBazi: false,           // UX批1 I-2：命盘是否就绪（决定生日/四柱区 vs 未设置占位）
    nights: 1,                // G3 H-4：第 N 晚由 ylm_first_seen 真实推导，首夜即第 1 晚（原 231 为虚构数据）
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
    /* 登录态展示（v5.1）：头像/状态标签（登录/退出/注销已移入设置页）
       真机反馈：退出后点击未登录区无反应 → loggedOut 未登录态点击引导去设置页重登 */
    avatarUrl: '',              // 微信头像（无则印章「明」兜底）
    loginTag: '',               // 微信登录 / 体验用户（本页不再渲染，设置页当前登录态承担）
    realLogin: false,           // 真实微信登录（资料弹层门槛）
    loggedOut: false,           // 未登录：无 token 且非 local_user（退出后）
    /* 头像昵称采集（Task 2：chooseAvatar + nickname → 弹层保存，服务端落库） */
    nicknameSet: false,         // 已采集昵称（决定引导文案 / 编辑按钮）
    profileDialogVisible: false, // 头像昵称采集弹层开关
    draftNickname: '',          // 弹层昵称草稿（默认当前昵称）
    draftAvatar: '',            // 弹层头像临时路径预览（未保存前仅本地显示）
  },

  onLoad(options) {
    this._initNavOff();
    // 会员开通方案（L5-2：基础三档 + 高级一档，价格与后端 SUBSCRIBE_PLANS 对齐）
    const plans = ['monthly', 'quarterly', 'yearly', 'pro_monthly']
      .map((id) => payment.getProduct(id))
      .filter((p) => !!p)
      .map((p) => {
        /* Task3 价格对齐：priceLabel 拆成数字+单位（¥19.9 /月），wxml 分列渲染统一排版 */
        const parts = String(p.priceLabel || '').split('/');
        return {
          id: p.id,
          name: p.name,
          priceNum: parts[0] || p.priceLabel,
          priceUnit: parts[1] ? '/' + parts[1] : '',
          description: p.description,
          icon: p.icon,
        };
      });
    this.setData({ memberPlans: plans });
    /* UX批1 I-3：对话页额度条「开通会员」CTA → reLaunch me?openMember=1 →
       自动打开会员弹层（原只 reLaunch 到页，用户看不到开通入口预期结果） */
    if (options && String(options.openMember) === '1') {
      this.setData({ memberDialogVisible: true });
    }
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

  /* v1.1 收藏数：与 favorites 页三源同口径（H-8）——
     ① 本地 kept && !favImported（本机会话 + 归档，同 id 双源只计一次）；
     ② 后端对话收藏 GET /api/favorites（已导入条目的数据源，本地侧已排除不重复）；
     ③ 后端名笺 GET /api/ming/saved。
     后端失败静默降级（只显示已得源，与收藏页降级行为一致）。 */
  _loadFavCount() {
    let local = 0;
    try {
      const seen = new Set(); // G3 H-3：同 id 双源（storage + 归档）只计一次
      const scan = (msgs) => {
        (Array.isArray(msgs) ? msgs : []).forEach((m) => {
          if (!m || m.role !== 'ai' || !m.kept || m.favImported) return;
          if (seen.has(m.id)) return;
          seen.add(m.id);
          local++;
        });
      };
      scan(wx.getStorageSync(STORAGE_KEY));
      const arch = wx.getStorageSync(ARCHIVE_KEY);
      (Array.isArray(arch) ? arch : []).forEach((a) => scan(a.messages));
    } catch (e) { /* ignore */ }
    let remoteFavs = 0;
    let remoteMings = 0;
    const apply = () => {
      const count = local + remoteFavs + remoteMings;
      if (count !== this.data.favCount) {
        this.setData({ favCount: count });
        this._buildRows();
      }
    };
    apply();
    api.favList().then((data) => {
      remoteFavs = (data && data.items && data.items.length) || 0;
      apply();
    }).catch(() => { /* 失败静默：保持本地数（收藏页同款降级） */ });
    api.getMingSaved().then((data) => {
      remoteMings = (data && data.items && data.items.length) || 0;
      apply();
    }).catch(() => { /* 失败静默 */ });
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
    const hb = hasBazi !== undefined ? hasBazi : this.data.hasBazi;
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
    const info = (wx.getWindowInfo && wx.getWindowInfo()) || {};
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
        if (!info || !info.year) {
          this._resetBaziView();  // UX批1 I-2：确无档案 → 清残留展示，回「未设置」占位
          return;
        }
        const app = getApp();
        if (app && app.globalData) {
          app.globalData.hasBazi = true;
          app.globalData.baziInfo = info;
        }
        this._applyBaziToView(info);
      })
      .catch(() => { /* 未登录/后端未就绪：保持中性占位（UX批1 I-2：不再回原型默认假数据） */ });
  },

  /* UX批1 I-2：无档案时清掉可能残留的命盘展示 → 「未设置命主信息」占位 */
  _resetBaziView() {
    if (!this.data.hasBazi) return;
    this.setData({ hasBazi: false, pillars: [], birthdayText: '', lunarBirthday: '' });
    this._buildRows();
  },

  _applyBaziToView(b) {
    const y = b.year || b.birthYear;
    const m = b.month || b.birthMonth;
    const d = b.day || b.birthDay;
    if (!y || !m || !d) return;

    const pad = (n) => String(n).padStart(2, '0');
    /* k69 历法归一（三处同根源缺陷之一）：档案 calendar==='lunar' 时
       year/month/day 是**农历**数字，必须先换算公历再展示 —— 原实现直接
       `${y}.${m}.${d}` 把农历 3/28 当公历显示（1999.03.28，正解 1999-05-13）。
       换算单点 = utils/lunar.lunarDateToSolar（与 paipan.js:183 / duipan /
       hehun 同源，转换失败回落原文，不新增崩溃路径）。
       其后「公历行」与「农历行」两行同由这一份公历日期派生 —— 原实现的
       农历行对已是农历的 (y,m,d) 又转一次（农历二月十一日，正解三月廿八）。 */
    let sy = Number(y);
    let sm = Number(m);
    let sd = Number(d);
    if (b.calendar === 'lunar') {
      const conv = String(lunar.lunarDateToSolar(`${y}-${pad(m)}-${pad(d)}`)).split('-');
      if (conv.length === 3) {
        sy = parseInt(conv[0], 10);
        sm = parseInt(conv[1], 10);
        sd = parseInt(conv[2], 10);
      }
    }
    /* k69 时辰归一（三处同根源缺陷之二）：hour 是钟点（10:55 档）或时辰代表
       整点（奇数集），**不是** 0-11 序号 —— 原实现 _hourToIndex 先按序号直取，
       把钟点 10 变成 HOUR_CN[10]='戌时'（正解巳时，序号 5）。改引 utils/persons
       单点映射（paipan.js 的 k19 注释「不再把 10 误读成 戌时」即此口径）。
       无时辰（缺失/空串）→ 不显示时辰尾缀（沿用原 hasHour 空白语义）。

       k73-M1「宁少不假」：**hour 必须能完整解析为 0-23 的整数才显示时辰**。
       原实现只拦 undefined/null/''，其余一律转交 hourToShichenIndex —— 而该函数
       对无法识别的输入**返回 0（=子时）**，于是 "abc" / 99 / -1 / NaN 会凭空
       显示「子时」（实测改前：四者全显示 "1995.05.12 子时"；旧实现一律不显示
       时辰）。今天写路径（DB 只存整数或 NULL）不可达，但上游数据形态一变
       （历史数据、脏数据、迁移脚本）就会**安静地显示一个假时辰**，故在此拦死：
       解析失败或越界 ⇒ 不显示，**绝不兜底 0**。
       用 Number 完整解析而非 parseInt：parseInt('10abc')=10 会把半截垃圾当好值
       （实测改前 "10abc" 显示巳时）；非整数（10.5）同样不显示（不是合法钟点，
       宁少不假）。空串/空数组先归一为 NaN —— Number('') === 0 会把它们当子时。

       k75「闸门与渲染必须同一个解析器」：上面这道闸门用 Number 完整解析，
       但**交给单点的仍是原始值** —— 而 persons.hourToShichenIndex 内部是
       parseInt(hour, 10)，两个解析器对同一输入给出不同数值。凡 Number 认、
       parseInt 不认的形态（"0x10"→16 vs 0；"1e1"→10 vs 1；"0b101"→5 vs 0；
       "0o17"→15 vs 0）闸门放行，渲染侧却按 parseInt 的结果算，于是
       **解析结果被丢掉、又退回兜底 0（=子时）**：复审实测 "0x10"/"0b101"/"0o17"
       显示子时（期望申/卯/申），"1e1" 显示丑时（期望巳）——与「解析失败一律
       不显示、绝不兜底 0」直接冲突。故把**校验后的数值** hourNum 交给单点：
       闸门与渲染从此共用同一个 Number 解析结果（parseInt(number) 恒等于该值），
       两个解析器不可能再分叉。 */
    const rawHour = b.hour !== undefined && b.hour !== null ? b.hour : b.birthHour;
    const rawMinute = b.minute !== undefined && b.minute !== null ? b.minute : b.birthMinute;
    const hasHour = rawHour !== undefined && rawHour !== null && rawHour !== '';
    const hourText = String(rawHour).trim();
    const hourNum = hourText === '' ? NaN : Number(hourText);
    const hasUsableHour = hasHour
      && Number.isInteger(hourNum) && hourNum >= 0 && hourNum <= 23;
    const hour = hasUsableHour
      ? persons.shichenCN(persons.hourToShichenIndex(hourNum, rawMinute))
      : '';
    const patch = {
      birthdayText: `${sy}.${pad(sm)}.${pad(sd)}${hour ? ' ' + hour : ''}`,
      lunarBirthday: this._lunarLabel(sy, sm, sd),
    };
    const vals = [b.year_pillar, b.month_pillar, b.day_pillar, b.hour_pillar];
    if (vals.every((v) => !!v)) {
      patch.pillars = ['年', '月', '日', '时'].map((l, i) => ({ l, c: vals[i] }));
    }
    patch.hasBazi = true;   // UX批1 I-2：真实命盘就绪 → 渲染生日/四柱区
    this.setData(patch);
    this._buildRows(true);
  },

  /* 公历 → 农历文案（如「农历三月廿八日」）。
     k69：**入参必须是公历**（调用方已按 calendar 完成农历→公历归一）。
     原实现在 lunar 档案下直接传入未换算的农历数字 → 二次换算，
     「三月廿八」被显示成「二月十一日」。 */
  _lunarLabel(y, m, d) {
    try {
      const t = lunar.solar2lunar(y, m, d);
      if (!t) return '';
      return `农历${lunar.formatLunarDate(y, m, d)}`;
    } catch (e) {
      return '';
    }
  },

  /* 第 N 晚：由 ylm_first_seen 真实推导（G3 H-4）。
     首夜（无记录）→ 记首见时间并显示第 1 晚；之后每晚 +1。
     不再使用虚构的默认 231——宁少不假（原型默认值对老用户是假数据）。 */
  _deriveNights() {
    try {
      const first = wx.getStorageSync('ylm_first_seen');
      let nights = 1; // 首夜即第 1 晚
      if (first) {
        const days = Math.floor((Date.now() - first) / 86400000);
        nights = days + 1;
      } else {
        wx.setStorageSync('ylm_first_seen', Date.now());
      }
      if (nights !== this.data.nights) {
        this.setData({ nights });
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
      this.setData({ loggedOut: true, displayName: '未登录', avatarUrl: '', loginTag: '', realLogin: false, nicknameSet: false });
      return;
    }
    if (token) {
      // 真实昵称/头像优先取已保存值（本地缓存；头像存相对路径，渲染时拼 baseURL）
      const cache = this._readProfileCache();
      this.setData({
        loggedOut: false,
        realLogin: true,
        loginTag: '微信登录',
        nicknameSet: !!cache.nickname,
        displayName: cache.nickname || (u && u.nickName) || '明灯友人',  // I-2：中性兜底，不虚构
        avatarUrl: cache.avatarUrl ? api.getBaseURL() + cache.avatarUrl : ((u && u.avatarUrl) || ''),
      });
      this._loadBackendProfile(); // 后端 profile（含 nickname/avatar_url）为准，异步覆盖缓存
      return;
    }
    // 体验模式（local_user）：无后端身份，昵称仍走本地，不发请求
    this.setData({ loggedOut: false, displayName: '明灯友人', avatarUrl: '', loginTag: '体验用户', realLogin: false, nicknameSet: false });
  },

  /* ═══ 后端资料联动（Task4：GET /api/user/profile 返回 nickname/avatar_url 后优先后端值，
       覆盖本地缓存并刷新显示；失败/未登录静默保持缓存兜底） ═══ */
  _loadBackendProfile() {
    const gd = (getApp() && getApp().globalData) || {};
    if (!gd.token || this._profileBusy) return;
    this._profileBusy = true;
    api.getUserProfile()
      .then((profile) => {
        this._profileBusy = false;
        if (!profile) return;
        const patch = {};
        if (profile.nickname) {
          patch.nicknameSet = true;
          patch.displayName = profile.nickname;
          try { wx.setStorageSync('ylm_nickname', profile.nickname); } catch (e) { /* ignore */ }
        }
        if (profile.avatar_url) {
          patch.avatarUrl = profile.avatar_url.startsWith('http') ? profile.avatar_url : api.getBaseURL() + profile.avatar_url;
          try { wx.setStorageSync('ylm_avatar_url', profile.avatar_url); } catch (e) { /* ignore */ }
        }
        if (Object.keys(patch).length) this.setData(patch);
      })
      .catch(() => { this._profileBusy = false; /* 后端不可用：保持缓存兜底 */ });
  },

  /* ═══ 头像昵称缓存（Task 2：保存成功后本地缓存；Task4：后端 profile 为准，缓存作离线兜底） ═══ */
  _readProfileCache() {
    let nickname = '';
    let avatarUrl = '';
    try { nickname = wx.getStorageSync('ylm_nickname') || ''; } catch (e) { /* ignore */ }
    try { avatarUrl = wx.getStorageSync('ylm_avatar_url') || ''; } catch (e) { /* ignore */ }
    return { nickname, avatarUrl };
  },

  /* ═══ 头像昵称采集弹层（Task 2：chooseAvatar + nickname 输入 → 上传头像 + 存昵称） ═══
     真机反馈修复（退出后无法重新登录）：未登录（退出后）点击 → 跳设置页重新登录
     （设置页有完整微信登录入口 onLoginTap → wechatLogin）；已登录保持原行为打开弹层 */
  openProfileDialog() {
    if (this.data.loggedOut) {
      wx.navigateTo({ url: '/pages/settings/settings' });
      return;
    }
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

  /* 头像/昵称区点击（UX批1 I-5：已登录不再零反馈——与「编辑」行一致打开资料弹层；
     未登录→设置页重登；体验用户→toast 引导登录） */
  onProfileAreaTap() {
    this.openProfileDialog();
  },

  closeProfileDialog() {
    this.setData({ profileDialogVisible: false });
  },

  /* UX批1 I-1：右上 kebab（nav-right）接设置页（与列表「设置」行同目标） */
  onNavSettings() {
    wx.navigateTo({ url: '/pages/settings/settings' });
  },

  /* UX批1 I-2：未设置命主信息占位「去设置」→ 档案页（与今日页提示条同口径） */
  onGoArchive() {
    wx.navigateTo({ url: '/pages/persons/persons', fail: () => {} });
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

  /* 保存：昵称非空 → saveProfile；头像已选 → uploadAvatar；成功 setData 刷新显示，失败 toast。
     fix-later: ①第四场景（头像成功昵称失败）补 toast「头像已上传,昵称保存失败」；
     ②彻底失败（两样都挂）保留弹层不关闭 —— 昵称输入不丢，可直接改后重试。 */
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
        const nicknameOk = results[0].status === 'fulfilled';
        const avatarOk = !hasAvatar || results[1].status === 'fulfilled';
        if (hasAvatar && results[1].status === 'fulfilled') {
          const up = results[1].value || {};
          const gd = (getApp() && getApp().globalData) || {};
          const rel = up.avatar_url || (gd.userId ? `/api/user/avatar/${gd.userId}` : '');
          if (rel) {
            try { wx.setStorageSync('ylm_avatar_url', rel); } catch (e) { /* ignore */ }
          }
        }
        if (nicknameOk) {
          // Task4：以服务端返回的 nickname 为准（saveProfile 响应 {success, nickname}）
          const saved = (results[0].value && results[0].value.nickname) || nickname;
          try { wx.setStorageSync('ylm_nickname', saved); } catch (e) { /* ignore */ }
        }
        this._deriveIdentity();
        if (nicknameOk && avatarOk) {
          this.setData({ profileDialogVisible: false });
          wx.showToast({ title: '已保存', icon: 'none' });
        } else if (nicknameOk) {
          this.setData({ profileDialogVisible: false });
          wx.showToast({ title: '昵称已保存，头像上传失败', icon: 'none' });
        } else if (hasAvatar && avatarOk) {
          // 第四场景：头像成功、昵称失败 —— 头像已生效，保留弹层让昵称可改后重试
          wx.showToast({ title: '头像已上传，昵称保存失败', icon: 'none' });
        } else {
          // 彻底失败：保留弹层不关闭（昵称输入不丢），静默未动弹层
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

  /* 选择套餐 → 支付 → 刷新会员状态（UX批1 M-8：支付过程补 loading 反馈） */
  buyPlan(e) {
    const planId = e.currentTarget.dataset.plan;
    if (!planId || this._buying) return;
    this._buying = true;
    wx.showLoading({ title: '正在开通…', mask: true });
    payment.subscribeMember(planId)
      .then((res) => {
        wx.hideLoading();
        if (res && res.success) {
          this.setData({ memberDialogVisible: false });
          this._loadMember();
        } else {
          wx.showToast({ title: '支付未完成', icon: 'none' });
        }
      })
      .catch(() => {
        wx.hideLoading();
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
    const url = { chat: '/pages/chat/chat', today: '/pages/today/today', suance: '/pages/celiang/celiang', me: '/pages/me/me' }[t];
    if (url && !url.includes('/me/')) wx.reLaunch({ url });
  },
});
