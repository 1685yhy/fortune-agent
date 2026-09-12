// 排盘 — 选命主 + 八字档案（原型 dir_funcs 捌 + 原 bazi 表单）
// 契约：GET/POST /api/persons、PUT/DELETE /api/persons/{id}、POST /api/persons/{id}/default
//   - 默认命主直接进入排盘（不每次选）；无默认 → 先选命主（卡片点选 + 手动输入·帮别人排）
//   - 手动临时命主不存档案（可勾「保存到档案」）；接口未就绪 → 本地缓存降级，不阻塞
//   - 对话建档提示条：chat 页检测到排盘回复含建档 key → 本地标记 → 本页展示确认弹层
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const persons = require('../../utils/persons');

/* 时辰（12 时辰）：序号 → 代表整点（子时23-01 取 23，其后每时辰取起始整点） */
const HOUR_LABELS = [
  '子时(23-01)', '丑时(01-03)', '寅时(03-05)', '卯时(05-07)',
  '辰时(07-09)', '巳时(09-11)', '午时(11-13)', '未时(13-15)',
  '申时(15-17)', '酉时(17-19)', '戌时(19-21)', '亥时(21-23)',
];
const HOUR_VALUES = [23, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21];

const CURRENT_KEY = 'ylm_current_person';     // 当前排盘命主 id（默认命主直接进入的依据）
const BANNER_KEY = 'ylm_dlg_person_saved';    // 对话建档提示标记（chat 页写入，本页消费）

/* 日期 'YYYY-MM-DD' → 年月日（容错） */
function _parseDate(dateStr) {
  const parts = String(dateStr || '').split('-');
  return {
    year: parseInt(parts[0], 10) || 0,
    month: parseInt(parts[1], 10) || 0,
    day: parseInt(parts[2], 10) || 0,
  };
}
function _fmtDate(y, m, d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${y}-${pad(m)}-${pad(d)}`;
}

/** 本地兜底形态 birthHour（**时辰序号 0-11**，见 _syncGlobal）→ 时辰序号。
    k34 审查修复（Important-2）：此处必须**按序号直取**——旧实现先查 HOUR_VALUES
    （「代表整点」表）再兜底序号，把序号 3/5/7/9/11 误当同值代表整点读
    （5=巳 → 卯时 3、11=亥 → 午时 6；偶数序号恰好正确 → 极难发现）。
    服务端/登录形态（时钟小时或代表整点）不走本函数，一律 persons.hourToShichenIndex。 */
function _birthHourToIndex(seq) {
  return Math.max(0, Math.min(11, parseInt(seq, 10) || 0));
}

/* k34 A12（照抄 pages/paipan k19 口径）：精确钟表行判定——birth_minute>0 或
   birth_hour 非「时辰代表整点」（HOUR_VALUES 奇数集）即为真实时钟小时语义
   （10:55 场景）。此形态读回必须进钟表档，保存必须回写真实 minute，
   绝不能再按「代表整点 + 0 分」降级（原缺陷：10:55 → 10:00）。 */
function _isClockRow(hour, minute) {
  return parseInt(minute, 10) > 0
    || (hour !== undefined && hour !== null && hour !== ''
      && HOUR_VALUES.indexOf(parseInt(hour, 10)) === -1);
}

/* 当前表单/手动输入的时刻文本（时辰 + 若钟表档附 HH:MM）——摘要与确认弹层同源 */
function _timeText(clockSet, clockH, clockM, hourIndex) {
  if (clockSet) {
    return persons.timeText({
      birth_hour: parseInt(clockH, 10) || 0,
      birth_minute: parseInt(clockM, 10) || 0,
    }) || persons.shichenCN(hourIndex);
  }
  return persons.shichenCN(hourIndex);
}

Page({
  data: {
    navOff: 0,
    mode: 'pick',                // pick=选命主 | form=排盘表单
    dark: false,

    /* ── 选命主（原型捌） ── */
    persons: [],                 // 视图：{id,name,rel,seal,birth,is_default}
    selId: '',                   // 选中命主 id
    mDate: '',                   // 手动临时生辰 'YYYY-MM-DD'（一次选完）
    mCal: 'solar',               // 手动历法 solar|lunar
    mHourIndex: 0,               // 手动时辰序号
    // k34 A12（接 k19 钟表档）：手动输入的精确钟表时间（10:55）
    mClockSet: false,
    mClockHIdx: 0,               // 0-23 时下标
    mClockMIdx: 0,               // 0-59 分下标
    mGender: '女',
    mPlace: '',
    mName: '',                   // 勾选保存到档案时的姓名
    saveToArc: false,
    canStart: false,
    ctaText: '请先选择或填写生辰',
    bannerVisible: false,        // 对话建档提示条
    bannerText: '',

    /* ── 排盘表单（原 bazi 页） ── */
    hourLabels: HOUR_LABELS,
    birthDate: '1990-01-01',     // 排盘表单出生年月日（一次选完）
    calendar: 'solar',           // solar|lunar（历法随选择器切换）
    hourIndex: 0,
    // k34 A12（接 k19 钟表档）：精确钟表时间（0-23 时 / 0-59 分）——精确档案（10:55）
    // 读回即进钟表档，保存回写真实分钟；只知时辰则保持时辰档（代表整点 + 0 分）
    clockHourLabels: persons.HOUR24,
    clockMinuteLabels: persons.MINUTE60,
    clockSet: false,
    clockHIdx: 0,
    clockMIdx: 0,
    gender: 'male',
    city: '',
    // k11c：真太阳时修正开关（档案级，默认开=产品口径；切后随档案保存、重排生效）
    solarOn: true,
    hasBazi: false,              // 已有档案（标题「更正档案」）
    formTitle: '设置档案',        // 表单导航标题
    currentPerson: null,         // {id,name,relation} 当前排盘命主（档案直选）
    saving: false,
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
    this._init();
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 进入：默认命主直接排盘；无默认 → 选命主；无档案 → 旧表单（profile 回显） */
  async _init() {
    const list = await persons.loadPersons();
    this._rawList = list;
    const views = (list || []).map((p) => ({
      id: p.id,
      name: p.name || '未命名',
      rel: p.relation || '',
      seal: persons.sealChar(p),
      birth: persons.birthBrief(p),
      is_default: !!p.is_default,
    }));

    let curId = null;
    try { curId = wx.getStorageSync(CURRENT_KEY) || null; } catch (e) { /* ignore */ }
    const cur = (list || []).find((p) => String(p.id) === String(curId)) || null;
    const def = persons.findDefault(list);

    if (cur) {
      this._enterForm(cur);
    } else if (def) {
      this._enterForm(def);          // 默认命主直接进入排盘（不每次选）
    } else if (list && list.length) {
      this.setData({ mode: 'pick', persons: views }, () => this._refreshCta());
    } else {
      this._enterForm(null);         // 无档案：保留原行为（profile 回显）
    }

    this._checkBanner();
  },

  // ════ 选命主（原型捌） ════

  onPick(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ selId: String(id) }, () => this._refreshCta());
  },

  /* 手动输入：清空选中，输入即触发校验 */
  _clearSel() {
    if (this.data.selId) {
      this.setData({ selId: '' }, () => this._refreshCta());
    } else {
      this._refreshCta();
    }
  },
  onMDateChange(e) { this.setData({ mCal: e.detail.calendar, mDate: e.detail.date }, () => this._clearSel()); },
  /* k34 A12：手选时辰 = 只知时辰档 → 退出钟表档（代表整点 + 0 分） */
  onMHourChange(e) {
    this.setData({
      mHourIndex: parseInt(e.currentTarget.dataset.idx, 10) || 0,
      mClockSet: false,
    });
  },
  /* k34 A12 钟表档（手动输入）：开 → 以当前时辰代表整点起始（未选/子时 → 12:00，
     与 persons.onClockModeToggle 同款）；选时/分 → 时辰 chips 联动推导 */
  onMClockToggle() {
    const d = this.data;
    if (d.mClockSet) {
      // k34 审查修复（Important-1）：关档不得把「开档起点」留下的时辰当成用户选择——
      // 未选/子时(0) 的开档起点是 12:00 中性值（午时 6），不回滚则「子时 → 开 → 关」
      // 存档写 birth_hour=11（子时被写成午时 = 错误出生数据落档）。
      // 关档语义 = 撤销本次钟表输入：未动过钟表值 → 回滚到开档前时辰（往返恒等）；
      // 动过 → 保留钟表联动推导的时辰（用户填的钟点不被丢弃，与 persons k19 同义）。
      const prev = this._mClockPrevHourIndex;
      const openH = prev === undefined ? null : (prev > 0 ? HOUR_VALUES[prev] : 12);
      const untouched = openH !== null && d.mClockHIdx === openH && d.mClockMIdx === 0;
      this.setData(untouched
        ? { mClockSet: false, mHourIndex: prev }
        : { mClockSet: false });
      return;
    }
    const startH = d.mHourIndex > 0 ? HOUR_VALUES[d.mHourIndex] : 12;
    this._mClockPrevHourIndex = d.mHourIndex;
    this.setData({
      mClockSet: true,
      mClockHIdx: startH,
      mClockMIdx: 0,
      mHourIndex: persons.shichenIndexFromClockHour(startH),
    });
  },
  onMClockHourChange(e) {
    const h = parseInt(e.detail.value, 10) || 0;
    this.setData({ mClockHIdx: h, mHourIndex: persons.shichenIndexFromClockHour(h) });
  },
  onMClockMinuteChange(e) {
    this.setData({ mClockMIdx: parseInt(e.detail.value, 10) || 0 });
  },
  onMGenderChange(e) { this.setData({ mGender: e.currentTarget.dataset.g }); },
  onMPlaceChange(e) { this.setData({ mPlace: e.detail.full }); },
  onMNameInput(e) { this.setData({ mName: e.detail.value }); },

  toggleSaveArc() {
    this.setData({ saveToArc: !this.data.saveToArc });
  },

  _refreshCta() {
    const d = this.data;
    const manualFilled = !!d.mDate;
    const sel = this._selPerson();
    const canStart = !!sel || manualFilled;
    let ctaText = '请先选择或填写生辰';
    if (sel) ctaText = `为「${sel.name}」开始排盘`;
    else if (manualFilled) ctaText = '按此生辰开始排盘';
    this.setData({ canStart, ctaText });
  },

  _selPerson() {
    if (!this.data.selId) return null;
    return (this.data.persons || []).find((p) => String(p.id) === String(this.data.selId)) || null;
  },

  /* 底部主按钮 */
  onStart() {
    const sel = this._selPerson();
    if (sel) {
      this._confirmStartArchive(sel);
      return;
    }
    const m = this.data;
    if (!m.mDate) return;
    if (m.saveToArc) {
      this._confirmSaveToArc();
    } else {
      wx.showToast({ title: '已按临时生辰排盘 · 排完即走', icon: 'none', duration: 2200 });
      this._enterTempForm();
    }
  },

  /* 档案直选 → 确认 → 进表单 */
  _confirmStartArchive(sel) {
    wx.showModal({
      title: '开启排盘',
      content: `已选定 ${sel.name}（${sel.rel}）\n${sel.birth}\n确认后以此生辰排盘`,
      confirmText: '开始排盘',
      cancelText: '再看一眼',
      confirmColor: '#A93A2C',
      success: (res) => {
        if (!res.confirm) return;
        const raw = (this._rawList || []).find((p) => String(p.id) === String(sel.id)) || null;
        if (raw) this._enterForm(raw);
      },
    });
  },

  /* 手动 + 勾选保存 → 二次确认 → POST /api/persons（失败本地降级） */
  _confirmSaveToArc() {
    const m = this.data;
    const name = (m.mName || '').trim() || '命主';
    const bd = _parseDate(m.mDate);
    const brief = `${m.mCal === 'lunar' ? '农历' : '公历'} ${bd.year}年${bd.month}月${bd.day}日 ${_timeText(m.mClockSet, m.mClockHIdx, m.mClockMIdx, m.mHourIndex)} ${m.mGender} · ${m.mPlace || '未填出生地'}`;
    wx.showModal({
      title: '保存到档案？',
      content: `此命主将加入「档案」\n${brief}\n保存后可在档案中再次选用`,
      confirmText: '保存并排盘',
      cancelText: '不保存',
      confirmColor: '#A93A2C',
      success: async (res) => {
        if (res.cancel) {
          wx.showToast({ title: '已按临时生辰排盘 · 未入档案', icon: 'none', duration: 2200 });
          this._enterTempForm();
          return;
        }
        const payload = this._manualPayload(name);
        try {
          const resp = await api.createPerson(payload);
          const saved = (resp && resp.person) || null;
          if (saved) {
            this._mergeLocal(saved, true);
            wx.showToast({ title: '已保存到档案并开始排盘', icon: 'none' });
            this._enterForm(saved);
            return;
          }
          throw new Error('no person');
        } catch (e) {
          console.warn('[Bazi] 保存命主接口未就绪，走本地降级:', e && e.message);
          const local = Object.assign({ id: 'local_' + Date.now(), is_default: false, created_at: Date.now() }, payload);
          this._mergeLocal(local, true);
          // G3 H-3①a：同步承诺有真实实现兜底——local_ 条目在下次 loadPersons（档案页/
          // 引导页）时逐条 upsert 到服务端（persons.js H-6），文案如实收窄为「联网后自动同步」
          wx.showToast({ title: '已保存到本机档案 · 联网后自动同步', icon: 'none', duration: 2200 });
          this._enterForm(local);
        }
      },
    });
  },

  _manualPayload(name) {
    const m = this.data;
    const bd = _parseDate(m.mDate);
    return {
      name,
      relation: '朋友',
      gender: persons.genderCode(m.mGender),
      birth_year: bd.year,
      birth_month: bd.month,
      birth_day: bd.day,
      // k34 A12：钟表档 → 真实时钟小时 + 分钟（10:55 不丢）；只知时辰 → 代表整点 + 0 分
      birth_hour: m.mClockSet ? m.mClockHIdx : persons.shichenIndexToHour(m.mHourIndex),
      birth_minute: m.mClockSet ? m.mClockMIdx : 0,
      calendar: m.mCal,
      city: (m.mPlace || '').trim(),
    };
  },

  _mergeLocal(saved, isNew) {
    const list = persons.getLocalPersons();
    const idx = list.findIndex((p) => String(p.id) === String(saved.id));
    if (idx >= 0) list[idx] = Object.assign({}, list[idx], saved);
    else list.push(saved);
    persons.saveLocalPersons(list);
    if (isNew) {
      this._rawList = (this._rawList || []).concat([saved]);
      this.setData({ persons: this.data.persons.concat([{
        id: saved.id, name: saved.name, rel: saved.relation,
        seal: persons.sealChar(saved), birth: persons.birthBrief(saved),
        is_default: !!saved.is_default,
      }]) });
    }
  },

  /* 临时命主进表单（不存档案） */
  _enterTempForm() {
    const m = this.data;
    const bd = _parseDate(m.mDate);
    this.setData({
      mode: 'form',
      currentPerson: null,
      formTitle: '临时排盘',
      birthDate: m.mDate || '1990-01-01',
      calendar: m.mCal,
      hourIndex: m.mHourIndex || 0,
      // k34 A12：手动输入的钟表档原样带入表单（保存（updateBazi/PUT）不丢分钟）
      clockSet: !!m.mClockSet,
      clockHIdx: m.mClockHIdx || 0,
      clockMIdx: m.mClockMIdx || 0,
      gender: m.mGender === '女' ? 'female' : 'male',
      city: m.mPlace || '',
      solarOn: true,          // 临时排盘不落档案：随表单一次排盘，默认开
      hasBazi: false,
      saving: false,
    });
    this._origSolar = true;
  },

  /* 命主（档案/刚保存）→ 表单回显 */
  _enterForm(p) {
    if (!p) { this._prefill(); return; }
    // k34 A12（照抄 paipan k19 口径）：精确钟表行（10:55）→ 回显钟表档；
    // 时辰 chips 按 hourToShichenIndex(小时,分钟) 时钟窗口推导（修旧误读）
    const clockRow = _isClockRow(p.birth_hour, p.birth_minute);
    const hourIndex = persons.hourToShichenIndex(p.birth_hour, p.birth_minute);
    // k11c：档案真太阳时开关回显（solar_time=0 关；缺失/旧档案 → 默认开）
    const solarOn = p.solar_time !== 0;
    this._origSolar = solarOn;
    this.setData({
      mode: 'form',
      currentPerson: { id: p.id, name: p.name || '未命名', relation: p.relation || '' },
      formTitle: `为「${p.name || '命主'}」排盘`,
      birthDate: p.birth_year ? _fmtDate(p.birth_year, p.birth_month, p.birth_day) : '1990-01-01',
      calendar: p.calendar === 'lunar' ? 'lunar' : 'solar',
      hourIndex,
      clockSet: clockRow,
      clockHIdx: clockRow ? (parseInt(p.birth_hour, 10) || 0) : 0,
      clockMIdx: clockRow ? (parseInt(p.birth_minute, 10) || 0) : 0,
      gender: persons.genderCode(p.gender),
      city: p.city || '',
      solarOn,
      hasBazi: true,
      saving: false,
    });
    try { wx.setStorageSync(CURRENT_KEY, p.id); } catch (e) { /* ignore */ }
  },

  /* 真太阳时修正开关（k11c 档案级）：开=按出生地经度换算真太阳时再定时辰；
     关=按本地时间直接排。默认开；切换随保存落档案 */
  onSolarTimeChange(e) {
    this.setData({ solarOn: !!e.detail.value });
  },

  /* 切换命主：回到选择页（保留当前表单不动） */
  switchPerson() {
    this.setData({ mode: 'pick', selId: '' });
    // 刷新档案列表（可能有新增）
    persons.loadPersons().then((list) => {
      this._rawList = list;
      this.setData({
        persons: (list || []).map((p) => ({
          id: p.id,
          name: p.name || '未命名',
          rel: p.relation || '',
          seal: persons.sealChar(p),
          birth: persons.birthBrief(p),
          is_default: !!p.is_default,
        })),
      }, () => this._refreshCta());
    });
    this._checkBanner();
  },

  /* 档案为空时的入口（选命主页） */
  goPersons() {
    wx.navigateTo({ url: '/pages/persons/persons', fail: () => {} });
  },

  // ════ 对话建档提示条（chat 页标记 → 本页展示确认弹层） ════

  _checkBanner() {
    let flag = null;
    try { flag = wx.getStorageSync(BANNER_KEY) || null; } catch (e) { /* ignore */ }
    if (!flag) return;
    this.setData({
      bannerVisible: true,
      bannerText: (flag && flag.text) || '',
    });
  },

  onBannerTap() {
    wx.showModal({
      title: '对话建档提示',
      content: this.data.bannerText
        ? `在对话中你说到了 ${this.data.bannerText}\n可在档案页查看并管理`
        : '对话中已识别到出生信息\n可在档案页查看并管理',
      confirmText: '知道了',
      cancelText: '取消',
      confirmColor: '#A93A2C',
      success: (res) => {
        this._clearBanner();
        if (res.confirm) {
          // G3 H-3①b：原「已保存到档案 · 档案-添加命主可查看」为假成功——标记里只有
          // {t, text:''} 无出生数据，前端零写入。镜像 chat.js G2 A4：只做档案指引，
          // 不声称已保存（对话分析落库在服务端，前端无法可靠回提）
          wx.showToast({ title: '已为你标记，可在档案页查看', icon: 'none' });
        } else {
          wx.showToast({ title: '未标记', icon: 'none' });
        }
      },
    });
  },

  dismissBanner() {
    this._clearBanner();
  },

  _clearBanner() {
    this.setData({ bannerVisible: false, bannerText: '' });
    try { wx.removeStorageSync(BANNER_KEY); } catch (e) { /* ignore */ }
  },

  // ════ 排盘表单（原 bazi 页逻辑保留） ════

  /* 已有八字回显：GET /api/user/profile → bazi_info；登录未定型时降级读 globalData.baziInfo */
  _prefill() {
    const gd = (getApp() && getApp().globalData) || {};
    const local = gd.baziInfo || null;

    api.getUserProfile()
      .then((profile) => {
        const b = (profile && profile.bazi_info) || local || null;
        if (b && (b.year || b.birthYear)) this._applyBazi(b);
      })
      .catch(() => {
        if (local && local.birthYear) this._applyBazi(local);
      });
  },

  _applyBazi(b) {
    // k34 A12：两种来源形态——
    //   ① 服务端 bazi_info / 登录 bazi（year/month/day/hour/minute，时钟小时口径；
    //      person_dao.bazi_info_of_person 契约）——app.js 登录、me 页 profile 同源；
    //   ② 本地 globalData 兜底（birthYear/…/birthHour = 时辰序号 0-11，见 _syncGlobal；
    //      k34 起附 birthClock/birthClockHour/birthClockMinute 精确字段）。
    // 以是否带 camelCase `birth*` 键区分（服务端形态恒不带）。
    const localShape = b.birthYear !== undefined || b.birthHour !== undefined
      || b.birthClock !== undefined;
    // 精确钟表行（10:55）→ 钟表档回显，保存按真实 minute 回写（绝不降级为 0 分）。
    // 服务端形态按 paipan k19 口径判定（minute>0 或 hour 非代表整点）与映射
    // （persons.hourToShichenIndex 时钟窗口）；本地形态只在 birthClock===true 时
    // 进钟表档，且 birthHour 恒为时辰序号 0-11（见 _syncGlobal）→ 按序号直取
    // （与 me.js 对本地形态的读法一致；旧实现按「代表整点」查表把 5/7/9/11 读错）。
    const clockRow = localShape ? b.birthClock === true : _isClockRow(b.hour, b.minute);
    const clockH = localShape ? b.birthClockHour : b.hour;
    const clockM = localShape ? b.birthClockMinute : b.minute;
    const hourIndex = localShape
      ? _birthHourToIndex(b.birthHour)                  // 本地形态：时辰序号直取
      : persons.hourToShichenIndex(clockH, clockM);     // 服务端形态：时钟窗口/代表整点
    const y = b.year || b.birthYear;
    const mo = b.month || b.birthMonth;
    const da = b.day || b.birthDay;
    // k11c F1（审查）：solar_time 优先取真值（bazi_info 带出时读之；旧契约无
    // 该键 → 默认开展示，但见 onSave：未真实改动不携带开关 → 不静默写回 1）
    const solarOn = b.solar_time !== undefined && b.solar_time !== null ? b.solar_time !== 0 : true;
    this._origSolar = solarOn;
    this.setData({
      birthDate: y ? _fmtDate(y, mo || 1, da || 1) : '1990-01-01',
      calendar: b.calendar === 'lunar' ? 'lunar' : 'solar',
      hourIndex,
      clockSet: clockRow,
      clockHIdx: clockRow ? (parseInt(clockH, 10) || 0) : 0,
      clockMIdx: clockRow ? (parseInt(clockM, 10) || 0) : 0,
      gender: b.gender === '女' ? 'female' : (b.gender === '男' ? 'male' : (b.gender || 'male')),
      city: b.city || '',
      solarOn,
      hasBazi: true,
      formTitle: '更正档案',
    });
  },

  // ---- 选择器 ----
  onFormDateChange(e) {
    this.setData({ calendar: e.detail.calendar, birthDate: e.detail.date });
  },
  /* k34 A12：手选时辰 = 只知时辰档 → 退出钟表档（代表整点 + 0 分） */
  onHourChange(e) {
    this.setData({
      hourIndex: parseInt(e.detail.value, 10) || 0,
      clockSet: false,
    });
  },
  /* k34 A12 钟表档（表单）：开 → 以当前时辰代表整点起始（未选/子时 → 12:00，
     与 persons.onClockModeToggle 同款）；选时/分 → 时辰选择器联动推导 */
  onClockToggle() {
    const d = this.data;
    if (d.clockSet) {
      // k34 审查修复（Important-1）：关档不得把「开档起点」留下的时辰当成用户选择——
      // 未选/子时(0) 的开档起点是 12:00 中性值（午时 6），不回滚则「子时 → 开 → 关」
      // 保存写 birth_hour=11（子时被写成午时 = 错误出生数据落档；子时正是
      // 「记不清时辰」人群的默认值）。关档语义 = 撤销本次钟表输入：
      //   未动过钟表值 → 回滚到开档前时辰（开→关往返恒等）；
      //   动过 → 保留钟表联动推导的时辰（用户填的钟点不被丢弃，与 persons k19 同义）。
      // 开档即真值（档案 10:55 回显后关档）无 _clockPrevHourIndex → 不猜不回滚。
      const prev = this._clockPrevHourIndex;
      const openH = prev === undefined ? null : (prev > 0 ? HOUR_VALUES[prev] : 12);
      const untouched = openH !== null && d.clockHIdx === openH && d.clockMIdx === 0;
      this.setData(untouched
        ? { clockSet: false, hourIndex: prev }
        : { clockSet: false });
      return;
    }
    const startH = d.hourIndex > 0 ? HOUR_VALUES[d.hourIndex] : 12;
    this._clockPrevHourIndex = d.hourIndex;
    this.setData({
      clockSet: true,
      clockHIdx: startH,
      clockMIdx: 0,
      hourIndex: persons.shichenIndexFromClockHour(startH),
    });
  },
  onClockHourChange(e) {
    const h = parseInt(e.detail.value, 10) || 0;
    this.setData({ clockHIdx: h, hourIndex: persons.shichenIndexFromClockHour(h) });
  },
  onClockMinuteChange(e) {
    this.setData({ clockMIdx: parseInt(e.detail.value, 10) || 0 });
  },
  /* 性别：男/女 大按钮（点击切换，与 paipan 同款） */
  onGenderTap(e) {
    this.setData({ gender: e.currentTarget.dataset.gender });
  },
  onCityChange(e) {
    this.setData({ city: e.detail.full });
  },

  /* 保存：档案命主 → PUT /api/persons/{id}；临时/旧档案 → POST /api/user/bazi（原有） */
  async onSave() {
    if (this.data.saving) return;
    const d = this.data;
    if (!d.birthDate) {
      wx.showToast({ title: '请完整填写出生年月日', icon: 'none' });
      return;
    }

    this.setData({ saving: true });
    const bd = _parseDate(d.birthDate);
    const solarChanged = this._origSolar !== d.solarOn;
    const baziData = {
      birth_year: bd.year,
      birth_month: bd.month,
      birth_day: bd.day,
      // k34 A12：钟表档 → 真实时钟小时 + 分钟（已存 10:55 保存后仍是 10:55）；
      // 只知时辰 → 时辰代表整点 + 0 分
      birth_hour: d.clockSet ? d.clockHIdx : HOUR_VALUES[d.hourIndex],
      birth_minute: d.clockSet ? d.clockMIdx : 0,
      gender: d.gender === 'female' ? '女' : '男',
      calendar: d.calendar,
      city: (d.city || '').trim(),
    };
    // k11c F1（审查）：开关只在用户真实改动时携带（1=开=经度校准；0=关=本地
    // 直排）。未改动保存 = 不带字段 → 服务端 update 合并保留既有开关——离线/
    // 列表空场景无档案真值可回显（默认开展示）时，绝不把 0 静默写回成 1
    if (solarChanged) baziData.solar_time = d.solarOn ? 1 : 0;

    try {
      const cp = d.currentPerson;
      if (cp && cp.id) {
        // 档案命主：PUT /api/persons/{id}（保留姓名/关系）
        const resp = await api.updatePerson(cp.id, Object.assign({
          name: cp.name,
          relation: cp.relation || '朋友',
        }, baziData));
        const saved = (resp && resp.person) || null;
        if (saved) {
          const list = persons.getLocalPersons();
          const idx = list.findIndex((p) => String(p.id) === String(saved.id));
          if (idx >= 0) list[idx] = Object.assign({}, list[idx], saved);
          else list.push(saved);
          persons.saveLocalPersons(list);
        }
        this._syncGlobal(baziData);
        if (solarChanged) {
          wx.showToast({ title: '已更新，重新排盘生效', icon: 'none', duration: 2200 });
        } else {
          wx.showToast({ title: `「${cp.name}」档案已保存`, icon: 'success' });
        }
      } else {
        // 临时命主 / 旧路径：POST /api/user/bazi（用户本人档案）
        await api.updateBazi(baziData);
        this._syncGlobal(baziData);
        if (solarChanged) {
          wx.showToast({ title: '已更新，重新排盘生效', icon: 'none', duration: 2200 });
        } else {
          wx.showToast({ title: '八字档案已保存', icon: 'success' });
        }
      }
      setTimeout(() => wx.navigateBack(), 700);
    } catch (err) {
      console.warn('[Bazi] 保存失败:', err);
      const msg = (err && (err.message || err.error || err.detail)) || '';
      wx.showToast({
        title: msg && msg.length <= 24 ? msg : '保存失败，请重试',
        icon: 'none',
        duration: 2500,
      });
    } finally {
      this.setData({ saving: false });
    }
  },

  /* 同步全局态，让 me 页手札即时反映（me.js _deriveUser 读 birthYear 形态）。
     k34 A12：附 birthClock / birthClockHour / birthClockMinute —— 本地兜底形态被
     _applyBazi 读回时（服务端不可达场景）钟表档与分钟不丢；me 页只读 birthYear/
     birthHour 时辰序号，新增键为纯增量不影响。 */
  _syncGlobal(baziData) {
    const app = getApp();
    if (app && app.globalData) {
      app.globalData.hasBazi = true;
      app.globalData.baziInfo = {
        birthYear: baziData.birth_year,
        birthMonth: baziData.birth_month,
        birthDay: baziData.birth_day,
        birthHour: this.data.hourIndex,   // 时辰序号，me 页按 子丑寅… 换算
        birthClock: !!this.data.clockSet,
        birthClockHour: baziData.birth_hour,
        birthClockMinute: baziData.birth_minute,
        gender: baziData.gender,
        calendar: baziData.calendar,
        city: baziData.city,
      };
    }
  },

  // ---- 返回 ----
  navHelp() {
    wx.showToast({ title: '为谁排盘，先选谁', icon: 'none' });
  },
  goBack() {
    wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/me/me' }) });
  },

  onShareAppMessage() {
    return { title: '易理明灯 · 排盘', path: '/pages/bazi/bazi' };
  },
});
