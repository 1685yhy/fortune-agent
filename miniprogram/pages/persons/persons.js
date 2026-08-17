// 档案管理 — 多人命主（原型 dir_funcs 柒：卡片列表 / 添加编辑删除 / 默认星标 / 空态引导）
// 契约：GET/POST /api/persons、PUT/DELETE /api/persons/{id}、POST /api/persons/{id}/default
//   接口未就绪 → 降级：本地缓存 ylm_persons（乐观写，失败提示「云端稍后同步」）
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

  /* 视图 → 契约 payload（字段结构与原先一致：year/month/day + calendar 标记） */
  _payload() {
    const d = this.data;
    const parts = String(d.dDate || '').split('-');
    return {
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
  },

  // ---- 列表交互 ----

  /* 点卡片 → 编辑 */
  onOpenEdit(e) {
    const id = e.currentTarget.dataset.id;
    const raw = this._rawById(id);
    if (!raw) return;
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
      saving: false,
    }, () => this._refreshForm());
  },

  _rawById(id) {
    return (this._rawList || []).find((p) => String(p.id) === String(id)) || null;
  },

  /* 新增 */
  onAdd() {
    this.setData(Object.assign({ mode: 'form', editing: null }, EMPTY_DRAFT(), {
      saving: false,
    }), () => this._refreshForm());
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

  /* 保存：新增 POST / 编辑 PUT；接口失败 → 本地降级 */
  async onSave() {
    if (!this.data.filled || this.data.saving) return;
    this.setData({ saving: true });
    const payload = this._payload();
    const editing = this.data.editing;

    try {
      let saved = null;
      if (editing) {
        const res = await api.updatePerson(editing.id, payload);
        saved = (res && res.person) || Object.assign({}, editing, payload);
        wx.showToast({ title: `已保存 · ${saved.name || payload.name}`, icon: 'none' });
      } else {
        const res = await api.createPerson(payload);
        saved = (res && res.person) || null;
        wx.showToast({ title: `已加入档案 · ${payload.name}`, icon: 'none' });
      }
      if (saved) this._mergeLocal(saved, editing);
    } catch (e) {
      console.warn('[Persons] 保存接口未就绪，走本地降级:', e && e.message);
      const local = Object.assign({
        id: editing ? editing.id : 'local_' + Date.now(),
        is_default: editing ? !!editing.is_default : this.data.persons.length === 0,
        created_at: editing ? (editing.created_at || Date.now()) : Date.now(),
      }, payload);
      this._mergeLocal(local, editing);
      wx.showToast({ title: '云端稍后同步 · 已本地保存', icon: 'none', duration: 2200 });
    } finally {
      this.setData({ saving: false });
      this.setData({ mode: 'list', editing: null });
      this._load();
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
        console.warn('[Persons] 删除接口未就绪，走本地降级:', e && e.message);
        done();
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
