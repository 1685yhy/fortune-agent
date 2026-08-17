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

/** 存档 hour（整点/时辰序号）→ 时辰序号 */
function _hourToIndex(hour) {
  const h = parseInt(hour, 10);
  if (Number.isNaN(h)) return 0;
  const idx = HOUR_VALUES.indexOf(h);
  if (idx !== -1) return idx;
  if (h >= 0 && h <= 11) return h;      // 旧数据：直接存了时辰序号
  return 0;
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
    gender: 'male',
    city: '',
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
  onMHourChange(e) { this.setData({ mHourIndex: parseInt(e.currentTarget.dataset.idx, 10) || 0 }); },
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
    const brief = `${m.mCal === 'lunar' ? '农历' : '公历'} ${bd.year}年${bd.month}月${bd.day}日 ${persons.shichenCN(m.mHourIndex)} ${m.mGender} · ${m.mPlace || '未填出生地'}`;
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
          wx.showToast({ title: '已本地保存档案 · 云端稍后同步', icon: 'none', duration: 2200 });
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
      birth_hour: persons.shichenIndexToHour(m.mHourIndex),
      birth_minute: 0,
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
      gender: m.mGender === '女' ? 'female' : 'male',
      city: m.mPlace || '',
      hasBazi: false,
      saving: false,
    });
  },

  /* 命主（档案/刚保存）→ 表单回显 */
  _enterForm(p) {
    if (!p) { this._prefill(); return; }
    const hourIndex = persons.hourToShichenIndex(p.birth_hour);
    this.setData({
      mode: 'form',
      currentPerson: { id: p.id, name: p.name || '未命名', relation: p.relation || '' },
      formTitle: `为「${p.name || '命主'}」排盘`,
      birthDate: p.birth_year ? _fmtDate(p.birth_year, p.birth_month, p.birth_day) : '1990-01-01',
      calendar: p.calendar === 'lunar' ? 'lunar' : 'solar',
      hourIndex,
      gender: persons.genderCode(p.gender),
      city: p.city || '',
      hasBazi: true,
      saving: false,
    });
    try { wx.setStorageSync(CURRENT_KEY, p.id); } catch (e) { /* ignore */ }
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
        ? `在对话中你说到了 ${this.data.bannerText}\n是否将其保存到档案？`
        : '对话中已识别到出生信息\n是否将其保存到档案？',
      confirmText: '确认保存',
      cancelText: '取消',
      confirmColor: '#A93A2C',
      success: (res) => {
        this._clearBanner();
        if (res.confirm) {
          wx.showToast({ title: '已保存到档案 · 档案-添加命主可查看', icon: 'none', duration: 2400 });
        } else {
          wx.showToast({ title: '未保存 · 本次排盘用完即弃', icon: 'none', duration: 2200 });
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
    const hourIndex = _hourToIndex(b.hour !== undefined && b.hour !== null ? b.hour : b.birthHour);
    const y = b.year || b.birthYear;
    const mo = b.month || b.birthMonth;
    const da = b.day || b.birthDay;
    this.setData({
      birthDate: y ? _fmtDate(y, mo || 1, da || 1) : '1990-01-01',
      calendar: b.calendar === 'lunar' ? 'lunar' : 'solar',
      hourIndex,
      gender: b.gender === '女' ? 'female' : (b.gender === '男' ? 'male' : (b.gender || 'male')),
      city: b.city || '',
      hasBazi: true,
      formTitle: '更正档案',
    });
  },

  // ---- 选择器 ----
  onFormDateChange(e) {
    this.setData({ calendar: e.detail.calendar, birthDate: e.detail.date });
  },
  onHourChange(e) {
    this.setData({ hourIndex: parseInt(e.detail.value, 10) || 0 });
  },
  onGenderChange(e) {
    this.setData({ gender: e.detail.value });
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
    const baziData = {
      birth_year: bd.year,
      birth_month: bd.month,
      birth_day: bd.day,
      birth_hour: HOUR_VALUES[d.hourIndex],
      birth_minute: 0,
      gender: d.gender === 'female' ? '女' : '男',
      calendar: d.calendar,
      city: (d.city || '').trim(),
    };

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
        wx.showToast({ title: `「${cp.name}」档案已保存`, icon: 'success' });
      } else {
        // 临时命主 / 旧路径：POST /api/user/bazi（用户本人档案）
        await api.updateBazi(baziData);
        this._syncGlobal(baziData);
        wx.showToast({ title: '八字档案已保存', icon: 'success' });
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

  /* 同步全局态，让 me 页手札即时反映（me.js _deriveUser 读 birthYear 形态） */
  _syncGlobal(baziData) {
    const app = getApp();
    if (app && app.globalData) {
      app.globalData.hasBazi = true;
      app.globalData.baziInfo = {
        birthYear: baziData.birth_year,
        birthMonth: baziData.birth_month,
        birthDay: baziData.birth_day,
        birthHour: this.data.hourIndex,   // 时辰序号，me 页按 子丑寅… 换算
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
