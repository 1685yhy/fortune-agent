// 档案管理 — 多人命主（原型 dir_funcs 柒：卡片列表 / 添加编辑删除 / 默认星标 / 空态引导）
// 契约：GET/POST /api/persons、PUT/DELETE /api/persons/{id}、POST /api/persons/{id}/default
//   G2 A1/A2：保存/删除成功必须以服务端确认响应为准；接口失败 → 明确失败提示，不写本地假数据
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const persons = require('../../utils/persons');

const EMPTY_DRAFT = () => ({
  name: '',
  rel: '自己',
  cal: 'solar',
  date: '',
  hourIndex: 0,
  gender: '女',
  place: '',
  solarOn: true,          // k11c：真太阳时修正开关（档案级，默认开=产品口径）
});

Page({
  data: {
    navOff: 0,
    dark: false,
    mode: 'list',              // list | form
    persons: [],               // 视图模型：{id,name,rel,seal,birth,is_default}
    loaded: false,
    removingId: '',            // 删除消散动画中

    // 表单（新增/编辑共用 draft）
    editing: null,             // null=新增, 否则为命主对象
    dName: '',
    dRel: '自己',
    dCal: 'solar',
    dDate: '',                 // 'YYYY-MM-DD'（按 dCal 历法的数值）
    dHourIndex: 0,
    dGender: '女',
    dPlace: '',
    hourLabels: persons.HOUR_LABELS,
    relations: persons.RELATIONS,
    filled: false,
    hint: '',
    saving: false,
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
  },

  onShow() {
    if (this.data.mode === 'list') this._load();
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 拉取列表：接口优先 → 本地缓存降级（永不 reject，空数组也可） */
  _load() {
    persons.loadPersons().then((list) => {
      this._rawList = list;                 // 原始契约对象（编辑/本地缓存同步用）
      this.setData({ persons: this._toView(list), loaded: true });
    });
  },

  _toView(list) {
    return (Array.isArray(list) ? list : []).map((p) => ({
      id: p.id,
      name: p.name || '未命名',
      rel: p.relation || '',
      seal: persons.sealChar(p),
      birth: persons.birthBrief(p),
      is_default: !!p.is_default,
    }));
  },

  /* 视图 → 契约 payload（字段结构与原先一致：year/month/day + calendar 标记）。
     k11c F1（审查）：solar_time 只在用户真实改动时携带——编辑未碰开关保存 =
     不带字段 → 服务端 update 合并保留既有开关（本地缓存陈旧/缺字段场景绝不把
     0 静默写回成 1）；显式翻转（0↔1）才随请求落档 */
  _payload() {
    const d = this.data;
    const parts = String(d.dDate || '').split('-');
    const payload = {
      name: d.dName.trim(),
      relation: d.dRel,
      gender: persons.genderCode(d.dGender),
      birth_year: parseInt(parts[0], 10) || 0,
      birth_month: parseInt(parts[1], 10) || 0,
      birth_day: parseInt(parts[2], 10) || 0,
      birth_hour: persons.shichenIndexToHour(d.dHourIndex),
      birth_minute: 0,
      calendar: d.dCal,
      city: (d.dPlace || '').trim(),
    };
    if (d.solarOn !== this._origSolar) payload.solar_time = d.solarOn ? 1 : 0;
    return payload;
  },

  // ---- 列表交互 ----

  /* 点卡片 → 编辑 */
  onOpenEdit(e) {
    const id = e.currentTarget.dataset.id;
    const raw = this._rawById(id);
    if (!raw) return;
    // k11c：档案真太阳时开关回显（solar_time=0 关；缺失/旧档案 → 默认开）
    this._origSolar = raw.solar_time !== 0;
    this.setData({
      mode: 'form',
      editing: raw,
      dName: raw.name || '',
      dRel: raw.relation || '自己',
      dCal: raw.calendar === 'lunar' ? 'lunar' : 'solar',
      dDate: raw.birth_year ? this._fmtDate(raw.birth_year, raw.birth_month, raw.birth_day) : '',
      dHourIndex: persons.hourToShichenIndex(raw.birth_hour),
      dGender: persons.genderCN(raw.gender),
      dPlace: raw.city || '',
      solarOn: raw.solar_time !== 0,
      saving: false,
    }, () => this._refreshForm());
  },

  _rawById(id) {
    return (this._rawList || []).find((p) => String(p.id) === String(id)) || null;
  },

  /* 新增 */
  onAdd() {
    this._origSolar = true;   // 新档案无「切换」语义（默认开）
    this.setData(Object.assign({ mode: 'form', editing: null }, EMPTY_DRAFT(), {
      saving: false,
    }), () => this._refreshForm());
  },

  /* 真太阳时修正开关（k11c 档案级）：开=按出生地经度换算真太阳时定时辰；
     关=按本地时间直接排。默认开=产品口径，切换随保存落档案、重排生效 */
  onSolarTimeChange(e) {
    this.setData({ solarOn: !!e.detail.value });
  },

  /* 返回列表 */
  onCancel() {
    this.setData({ mode: 'list', editing: null });
    this._load();
  },

  /* 星标：设为默认命主（已是默认 → 提示） */
  onStar(e) {
    const id = e.currentTarget.dataset.id;
    const list = this.data.persons;
    const target = list.find((p) => String(p.id) === String(id));
    if (!target) return;
    if (target.is_default) {
      wx.showToast({ title: '已是默认命主', icon: 'none' });
      return;
    }
    // 乐观更新 → 接口确认；失败回滚并提示
    const prev = list.map((p) => Object.assign({}, p));
    this._applyDefault(id);
    api.setDefaultPerson(id)
      .then(() => {
        this._persistLocal();
        wx.showToast({ title: `已设「${target.name}」为默认命主`, icon: 'none' });
      })
      .catch(() => {
        this.setData({ persons: prev });
        this._persistLocal();
        wx.showToast({ title: '设置失败，请检查网络', icon: 'none' });
      });
  },

  _applyDefault(id) {
    this.setData({
      persons: this.data.persons.map((p) => Object.assign({}, p, { is_default: String(p.id) === String(id) })),
    });
  },

  /* 本地缓存同步（读原始列表 → 覆盖 is_default → 写回） */
  _persistLocal() {
    const def = this.data.persons.find((v) => v.is_default) || {};
    const raw = (this._rawList || []).map((p) => Object.assign({}, p, {
      is_default: String(p.id) === String(def.id),
    }));
    persons.saveLocalPersons(raw);
  },

  // ---- 保存 / 删除 ----

  _fmtDate(y, m, d) {
    const pad = (n) => String(n).padStart(2, '0');
    return `${y}-${pad(m)}-${pad(d)}`;
  },

  _refreshForm() {
    const d = this.data;
    const filled = !!(d.dName.trim() && d.dDate);
    let hint = '';
    if (filled) {
      const parts = String(d.dDate).split('-');
      const p2 = (s) => parseInt(s, 10) || 0;
      hint = `「${d.dName}」 ${d.dCal === 'solar' ? '公历' : '农历'} ${p2(parts[0])}年${p2(parts[1])}月${p2(parts[2])}日 ${persons.shichenCN(d.dHourIndex)} · ${d.dGender} · ${d.dPlace || '未填出生地'}`;
    } else {
      hint = '填写姓名与出生年月日后可保存';
    }
    this.setData({ filled, hint });
  },

  onNameInput(e) { this.setData({ dName: e.detail.value }, () => this._refreshForm()); },
  onRelChange(e) { this.setData({ dRel: e.currentTarget.dataset.rel }, () => this._refreshForm()); },
  onBirthDateChange(e) { this.setData({ dCal: e.detail.calendar, dDate: e.detail.date }, () => this._refreshForm()); },
  onPlaceChange(e) { this.setData({ dPlace: e.detail.full }, () => this._refreshForm()); },
  onHourChange(e) { this.setData({ dHourIndex: parseInt(e.currentTarget.dataset.idx, 10) || 0 }, () => this._refreshForm()); },
  onGenderChange(e) { this.setData({ dGender: e.currentTarget.dataset.g }, () => this._refreshForm()); },

  /* 保存：新增 POST / 编辑 PUT；成功以服务端确认响应（res.person）为准（G2 A1）：
     失败 → 明确失败提示 + 不写本地缓存 + 停留表单页可重试；绝不「本地假保存」+ 无同步机制 */
  async onSave() {
    if (!this.data.filled || this.data.saving) return;
    this.setData({ saving: true });
    const solarChanged = this.data.solarOn !== this._origSolar;
    const payload = this._payload();
    const editing = this.data.editing;

    try {
      let saved = null;
      if (editing) {
        const res = await api.updatePerson(editing.id, payload);
        if (!res || !res.person) throw new Error('服务端未返回档案');
        saved = res.person;
        // k11c：切换了真太阳时开关 → 提示重排生效（开关随档案保存）
        if (solarChanged) {
          wx.showToast({ title: '已更新，重新排盘生效', icon: 'none', duration: 2200 });
        } else {
          wx.showToast({ title: `已保存 · ${saved.name || payload.name}`, icon: 'none' });
        }
      } else {
        const res = await api.createPerson(payload);
        if (!res || !res.person) throw new Error('服务端未返回档案');
        saved = res.person;
        wx.showToast({ title: `已加入档案 · ${saved.name || payload.name}`, icon: 'none' });
      }
      this._mergeLocal(saved, editing);
      this.setData({ mode: 'list', editing: null });
      this._load();
    } catch (e) {
      // 不写本地、不弹成功、不回列表（表单数据保留，可直接重试）
      console.warn('[Persons] 保存失败:', e && e.message);
      wx.showToast({ title: '保存失败，请重试', icon: 'none' });
    } finally {
      this.setData({ saving: false });
    }
  },

  _mergeLocal(saved, editing) {
    const list = persons.getLocalPersons();
    const idx = list.findIndex((p) => String(p.id) === String(saved.id));
    if (editing && idx >= 0) {
      list[idx] = Object.assign({}, list[idx], saved);
    } else if (!editing) {
      list.push(saved);
    } else {
      list.push(saved); // 本地无此条（接口有但缓存缺）→ 追加
    }
    persons.saveLocalPersons(list);
  },

  /* 删除：朱砂确认弹层 → 接口删除 → 本地同步（删空后首条自动为默认） */
  askDelete() {
    const editing = this.data.editing;
    if (!editing) return;
    wx.showModal({
      title: '删除档案？',
      content: `「${editing.name || ''} · ${editing.relation || ''}」的档案删除后不可恢复\n名下命书与手记不受影响`,
      confirmText: '确认删除',
      cancelText: '留着',
      confirmColor: '#A93A2C',
      success: (res) => {
        if (res.confirm) this._doDelete(editing);
      },
    });
  },

  _doDelete(editing) {
    const id = editing.id;
    this.setData({ removingId: id });
    const done = () => {
      let list = persons.getLocalPersons();
      list = list.filter((p) => String(p.id) !== String(id));
      // 删空或默认被删 → 首条为默认
      if (list.length && !list.some((p) => !!p.is_default)) {
        list[0].is_default = true;
      }
      persons.saveLocalPersons(list);
      this.setData({ mode: 'list', editing: null, removingId: '' });
      this._load();
      wx.showToast({ title: '已删除档案', icon: 'none' });
    };
    api.deletePerson(id)
      .then(done)
      .catch((e) => {
        // G2 A2：接口失败 → 明确失败提示 + 保留本地条目（绝不「已删除」假成功）
        console.warn('[Persons] 删除失败:', e && e.message);
        this.setData({ removingId: '' });
        wx.showToast({ title: '删除失败，请重试', icon: 'none' });
      });
  },

  // ---- 导航 ----
  goBack() {
    wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/me/me' }) });
  },
  navHelp() {
    wx.showToast({ title: '可以保存自己、家人、朋友的生辰信息', icon: 'none', duration: 2200 });
  },
  goOnboarding() {
    wx.navigateTo({ url: '/pages/onboarding/onboarding', fail: () => {} });
  },

  onShareAppMessage() {
    return { title: '易理明灯 · 档案', path: '/pages/persons/persons' };
  },
});
