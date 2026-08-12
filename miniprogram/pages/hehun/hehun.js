// 双人合盘 · 缘分契合 — 免费钩子（契合分/等级/三维得分条/缘语+悬念半句/墨韵缘笺）+ ¥19.9 深度报告（四章）
// 流程：输入双人生辰（可档案直选）→ 免费结果 → [生成墨韵缘笺] → [解锁深度报告]
// 隐私红线：TA 生辰只存本机 localStorage('yuan_ta_birth')，绝不进 persons 云端接口；缘笺图片数据全为服务端脱敏。
const api = require('../../utils/api');
const payment = require('../../utils/payment');
const shareCard = require('../../utils/shareCard');
const personUtil = require('../../utils/persons');

const TA_STORAGE_KEY = 'yuan_ta_birth';

const HOUR_OPTIONS = ['未填', '子时(23-01)', '丑时(01-03)', '寅时(03-05)', '卯时(05-07)',
  '辰时(07-09)', '巳时(09-11)', '午时(11-13)', '未时(13-15)',
  '申时(15-17)', '酉时(17-19)', '戌时(19-21)', '亥时(21-23)'];

const CITIES = [
  '请选择', '北京', '上海', '广州', '深圳', '杭州', '成都', '武汉',
  '西安', '南京', '重庆', '天津', '苏州', '长沙', '郑州', '东莞',
  '青岛', '沈阳', '宁波', '昆明', '大连', '厦门', '合肥', '佛山',
  '福州', '哈尔滨', '济南', '温州', '长春', '石家庄', '常州',
  '泉州', '南宁', '贵阳', '南昌', '太原', '烟台', '嘉兴', '南通',
  '金华', '珠海', '惠州', '徐州', '海口', '乌鲁木齐', '绍兴',
  '中山', '台州', '兰州', '保定', '镇江', '扬州', '桂林', '洛阳',
];

const RELATIONS = ['恋人', '暧昧', '夫妻', '朋友', '暗恋'];

Page({
  data: {
    // 我方 (p1)
    p1BirthYear: '请选择',
    p1BirthMonth: '请选择',
    p1BirthDay: '请选择',
    p1HourIdx: 0,          // 0=未填；1-12 对应时辰序号 0-11
    p1HourSet: false,
    p1Gender: 'male',
    p1City: '请选择',
    p1CitySet: false,

    // TA (p2)
    p2BirthYear: '请选择',
    p2BirthMonth: '请选择',
    p2BirthDay: '请选择',
    p2HourIdx: 0,
    p2HourSet: false,
    p2Gender: 'female',
    p2City: '请选择',
    p2CitySet: false,
    p2FromCache: false,    // 本机「上次记录」回填标注

    // Picker 数据
    yearOptions: ['请选择'],
    monthOptions: ['请选择'],
    dayOptions: ['请选择'],
    hourOptions: HOUR_OPTIONS,
    cities: CITIES,
    yearOptionIdx: 0, monthOptionIdx: 0, dayOptionIdx: 0,
    cityOptionIdx: 0,

    // Picker 索引
    p1YearIdx: 0, p1MonthIdx: 0, p1DayIdx: 0, p1CityIdx: 0,
    p2YearIdx: 0, p2MonthIdx: 0, p2DayIdx: 0, p2CityIdx: 0,

    // 关系标签（5 chips 单选，可取消）
    relations: RELATIONS,
    relation: '',

    // 档案直选
    persons: [],
    personsLoaded: false,

    // 状态
    loading: false,
    submitted: false,
    errorMsg: '',

    // 免费结果
    result: null,
    dims: [],              // [{key,label,score,max,color}]
    yuanLine: '',          // 缘语主句+后缀
    cliffhanger: '',       // 悬念半句
    hourNotSetNote: '',    // 「时辰未填，仅供参考」

    // 付费
    purchasing: false,
    report: null,
    reportId: '',

    // 弹层
    showPaywall: false,    // 付费墙（悬念半句展开）
    showYuanPreview: false,
    yuanImagePath: '',
    generatingCard: false,
    showReport: false,     // 四章弹层

    // 分享
    shareTitle: '双人合盘 · 缘分契合 - 测测你们合不合',
  },

  onLoad() {
    this.initPickerOptions();
    this.restoreTaCache();
    this.loadDefaultSelf();
  },

  // ---- 选择器数据 ----
  initPickerOptions() {
    const yearOptions = ['请选择'];
    const monthOptions = ['请选择'];
    const dayOptions = ['请选择'];
    for (let y = 2024; y >= 1940; y--) yearOptions.push(String(y));
    for (let m = 1; m <= 12; m++) monthOptions.push(String(m));
    for (let d = 1; d <= 31; d++) dayOptions.push(String(d));
    this.setData({
      yearOptions,
      monthOptions,
      dayOptions,
      yearOptionIdx: yearOptions.length - 1,  // 默认 1995
      monthOptionIdx: 0,
      dayOptionIdx: 0,
      cityOptionIdx: 0,
    });
  },

  _indexOfOr0(arr, v) {
    const i = arr.indexOf(v);
    return i > 0 ? i : 0;
  },

  // ---- 本机缓存：TA 生辰（隐私：仅本机，标记「他/她」，永不进 persons 云端接口） ----
  restoreTaCache() {
    let c = null;
    try { c = wx.getStorageSync(TA_STORAGE_KEY); } catch (e) { c = null; }
    if (!c || !c.year || !c.month || !c.day) return;
    const year = String(c.year);
    const month = String(c.month);
    const day = String(c.day);
    this.setData({
      p2BirthYear: year,
      p2BirthMonth: month,
      p2BirthDay: day,
      p2YearIdx: this._indexOfOr0(this.data.yearOptions, year),
      p2MonthIdx: this._indexOfOr0(this.data.monthOptions, month),
      p2DayIdx: this._indexOfOr0(this.data.dayOptions, day),
      p2Gender: c.gender === 'female' ? 'female' : 'male',
      p2FromCache: true,
    });
    // 时辰/出生地为选填：有则回填
    if (c.hourSet) {
      const hi = Math.min(12, Math.max(1, parseInt(c.hourIdx, 10) || 1));
      this.setData({ p2HourIdx: hi, p2HourSet: true });
    }
    if (c.city) {
      const ci = this.data.cities.indexOf(c.city);
      this.setData({ p2City: c.city, p2CitySet: true, p2CityIdx: Math.max(0, ci) });
    }
    if (c.relation && RELATIONS.indexOf(c.relation) !== -1) {
      this.setData({ relation: c.relation });
    }
  },

  _saveTaCache() {
    const d = this.data;
    try {
      wx.setStorageSync(TA_STORAGE_KEY, {
        year: parseInt(d.p2BirthYear, 10) || 0,
        month: parseInt(d.p2BirthMonth, 10) || 0,
        day: parseInt(d.p2BirthDay, 10) || 0,
        hourIdx: d.p2HourIdx,
        hourSet: d.p2HourSet,
        city: d.p2CitySet ? d.p2City : '',
        gender: d.p2Gender,
        relation: d.relation,
        ts: Date.now(),
      });
    } catch (e) { /* storage 满等异常不阻断 */ }
  },

  // ---- onLoad 回填我方：档案默认命主（对话触发进入「带已填的我方生辰」由此实现，不在 URL 传生辰） ----
  loadDefaultSelf() {
    api.getPersons().then((res) => {
      const list = (res && res.persons) || [];
      const def = list.find((p) => p.is_default) || list[0];
      if (def) this._fillFromPerson('p1', def);
      this.setData({ persons: list, personsLoaded: true });
    }).catch((err) => {
      console.warn('[hehun] getPersons 失败（跳过档案直选）:', err && err.message);
      this.setData({ personsLoaded: true });
    });
  },

  // ---- 从档案选择（showActionSheet 列姓名 → 点选填充） ----
  onPickPerson(e) {
    const target = e.currentTarget.dataset.target;
    const persons = this.data.persons;
    if (!persons || !persons.length) {
      wx.showToast({ title: '暂无档案，可手动填写', icon: 'none' });
      return;
    }
    wx.showActionSheet({
      itemList: persons.map((p) => p.name),
      success: (res) => {
        const p = persons[res.tapIndex];
        if (p) this._fillFromPerson(target, p);
      },
    });
  },

  _fillFromPerson(target, p) {
    if (!p || !p.birth_year || !p.birth_month || !p.birth_day) {
      wx.showToast({ title: '该档案生辰不完整', icon: 'none' });
      return;
    }
    const year = String(p.birth_year);
    const month = String(p.birth_month);
    const day = String(p.birth_day);
    const base = {
      [`${target}BirthYear`]: year,
      [`${target}BirthMonth`]: month,
      [`${target}BirthDay`]: day,
      [`${target}YearIdx`]: this._indexOfOr0(this.data.yearOptions, year),
      [`${target}MonthIdx`]: this._indexOfOr0(this.data.monthOptions, month),
      [`${target}DayIdx`]: this._indexOfOr0(this.data.dayOptions, day),
      [`${target}Gender`]: p.gender === 'female' ? 'female' : 'male',
    };
    // 时辰（选填）：档案有时辰才回填
    if (p.birth_hour !== undefined && p.birth_hour !== null && p.birth_hour !== '') {
      const hi = personUtil.hourToShichenIndex(p.birth_hour) + 1;
      base[`${target}HourIdx`] = hi;
      base[`${target}HourSet`] = true;
    } else {
      base[`${target}HourIdx`] = 0;
      base[`${target}HourSet`] = false;
    }
    // 出生地（选填）
    if (p.city) {
      const ci = this.data.cities.indexOf(p.city);
      base[`${target}City`] = p.city;
      base[`${target}CitySet`] = true;
      base[`${target}CityIdx`] = Math.max(0, ci);
    } else {
      base[`${target}City`] = '请选择';
      base[`${target}CitySet`] = false;
      base[`${target}CityIdx`] = 0;
    }
    if (target === 'p2') base.p2FromCache = false;
    this.setData(base);
  },

  // ---- Person 1（我方）Handlers ----
  onP1YearChange(e) {
    const v = this.data.yearOptions[e.detail.value];
    this.setData({ p1BirthYear: v, p1YearIdx: e.detail.value });
  },
  onP1MonthChange(e) {
    const v = this.data.monthOptions[e.detail.value];
    this.setData({ p1BirthMonth: v, p1MonthIdx: e.detail.value });
  },
  onP1DayChange(e) {
    const v = this.data.dayOptions[e.detail.value];
    this.setData({ p1BirthDay: v, p1DayIdx: e.detail.value });
  },
  onP1HourChange(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ p1HourIdx: idx, p1HourSet: idx > 0 });
  },
  onP1CityChange(e) {
    const v = this.data.cities[e.detail.value];
    this.setData({ p1City: v, p1CitySet: v !== '请选择', p1CityIdx: e.detail.value });
  },
  onP1GenderChange(e) {
    this.setData({ p1Gender: e.detail.value });
  },

  // ---- Person 2（TA）Handlers ----
  onP2YearChange(e) {
    const v = this.data.yearOptions[e.detail.value];
    this.setData({ p2BirthYear: v, p2YearIdx: e.detail.value, p2FromCache: false });
  },
  onP2MonthChange(e) {
    const v = this.data.monthOptions[e.detail.value];
    this.setData({ p2BirthMonth: v, p2MonthIdx: e.detail.value, p2FromCache: false });
  },
  onP2DayChange(e) {
    const v = this.data.dayOptions[e.detail.value];
    this.setData({ p2BirthDay: v, p2DayIdx: e.detail.value, p2FromCache: false });
  },
  onP2HourChange(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ p2HourIdx: idx, p2HourSet: idx > 0, p2FromCache: false });
  },
  onP2CityChange(e) {
    const v = this.data.cities[e.detail.value];
    this.setData({ p2City: v, p2CitySet: v !== '请选择', p2CityIdx: e.detail.value, p2FromCache: false });
  },
  onP2GenderChange(e) {
    this.setData({ p2Gender: e.detail.value, p2FromCache: false });
  },

  // ---- 关系标签（5 chips 单选，再点取消） ----
  onRelationTap(e) {
    const r = e.currentTarget.dataset.rel;
    this.setData({ relation: this.data.relation === r ? '' : r });
  },

  // ---- 组装提交载荷（小程序契约：birthYear/birthMonth/birthDay/birthHour(0-11)/gender/city） ----
  _buildPerson(prefix) {
    const d = this.data;
    const p = {
      birthYear: parseInt(d[`${prefix}BirthYear`], 10),
      birthMonth: parseInt(d[`${prefix}BirthMonth`], 10),
      birthDay: parseInt(d[`${prefix}BirthDay`], 10),
      gender: d[`${prefix}Gender`],
    };
    if (d[`${prefix}HourSet`]) p.birthHour = d[`${prefix}HourIdx`] - 1;  // 时辰序号 0-11
    if (d[`${prefix}CitySet`]) p.city = d[`${prefix}City`];
    return p;
  },

  _buildPayload(paid) {
    return {
      person1: this._buildPerson('p1'),
      person2: this._buildPerson('p2'),
      relation: this.data.relation || '',
      paid: !!paid,
    };
  },

  // ---- 提交 ----
  async onSubmit() {
    const d = this.data;
    const need = (v) => v && v !== '请选择';
    if (!need(d.p1BirthYear) || !need(d.p1BirthMonth) || !need(d.p1BirthDay)) {
      wx.showToast({ title: '请选择我方出生年月日', icon: 'none' });
      return;
    }
    if (!need(d.p2BirthYear) || !need(d.p2BirthMonth) || !need(d.p2BirthDay)) {
      wx.showToast({ title: '请选择TA的出生年月日', icon: 'none' });
      return;
    }

    this.setData({ loading: true, errorMsg: '', submitted: false, result: null });
    try {
      const result = await api.union(this._buildPayload(false));
      this._setResult(result);
      // 提交成功后 TA 生辰只存本机（隐私红线）
      this._saveTaCache();
      this.setData({ submitted: true, loading: false });
    } catch (err) {
      console.warn('[hehun] union 免费档失败:', err);
      this.setData({
        loading: false,
        submitted: false,
        errorMsg: (err && err.detail) || '推演失败，请稍后重试',
      });
    }
  },

  // ---- 免费结果展示 ----
  _setResult(result) {
    const dims = (result && result.dimensions) || {};
    const mkDim = (key, label) => {
      const it = dims[key] || {};
      return {
        key,
        label,
        score: it.score || 0,
        max: it.max || 0,
        color: this._barColor(it.score, it.max),
      };
    };
    const qp = (result && result.quoteParts) || {};
    // 主句可能带句号结尾（LLM 润色），与后缀拼接前去重，避免「。，」
    const main = String(qp.main || '').replace(/。+$/, '');
    this.setData({
      result,
      dims: [mkDim('wuxing', '五行'), mkDim('shengxiao', '生肖'), mkDim('rizhu', '日柱')],
      yuanLine: [main, qp.suffix].filter(Boolean).join('，') + '。',
      cliffhanger: qp.cliffhanger || '',
      hourNotSetNote: (!this.data.p1HourSet || !this.data.p2HourSet)
        ? '时辰未填，仅供参考'
        : '',
    });
  },

  _barColor(score, max) {
    const pct = max > 0 ? score / max : 0;
    if (pct >= 0.75) return '#A93A2C';   // 朱砂
    if (pct >= 0.5) return '#B08A4F';    // 金
    return '#9A8B71';                    // 淡墨
  },

  // ---- 三维得分条明细锁定：点击提示进深度报告 ----
  onDimTap() {
    wx.showToast({ title: '明细见深度报告·契合详情章', icon: 'none' });
  },

  // ---- 悬念半句「展开」→ 付费墙 ----
  onExpandCliffhanger() {
    this.setData({ showPaywall: true });
  },
  onClosePaywall() {
    this.setData({ showPaywall: false });
  },

  // ---- 缘笺流程：确认弹窗（脱敏说明）→ drawYuanCard → 预览弹层 ----
  onGenerateCard() {
    if (this.data.generatingCard) return;
    if (!this.data.result || !this.data.result.yuan_card) {
      wx.showToast({ title: '暂无缘笺数据', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '生成墨韵缘笺',
      content: '图片将包含脱敏后的生日信息（年/月/日+生肖+日柱，不含时辰与出生地），确认生成？',
      confirmText: '生成',
      success: (res) => {
        if (res.confirm) this._drawYuanCard();
      },
    });
  },

  _drawYuanCard() {
    this.setData({ generatingCard: true });
    const query = wx.createSelectorQuery();
    query.select('#shareCanvas')
      .fields({ node: true, size: true })
      .exec((res) => {
        if (!res || !res[0] || !res[0].node) {
          wx.showToast({ title: '生成失败，请重试', icon: 'none' });
          this.setData({ generatingCard: false });
          return;
        }
        const canvas = res[0].node;
        const card = this.data.result.yuan_card || {};
        shareCard.drawYuanCard(card, canvas, (tempFilePath) => {
          this.setData({ generatingCard: false });
          if (tempFilePath) {
            this.setData({ yuanImagePath: tempFilePath, showYuanPreview: true });
          } else {
            wx.showToast({ title: '生成图片失败', icon: 'none' });
          }
        });
      });
  },

  onCloseYuanPreview() {
    this.setData({ showYuanPreview: false, yuanImagePath: '' });
  },
  onSaveCard() {
    shareCard.saveCardToAlbum(this.data.yuanImagePath);
  },
  onShareCard() {
    shareCard.shareCard(this.data.yuanImagePath, '双人合盘 · 缘分契合');
  },

  // ---- 付费流程：解锁深度报告（¥19.9，deep_report 通道）→ 四章弹层 ----
  onUnlock() {
    this._startPurchase();
  },

  async _startPurchase() {
    if (this.data.purchasing) return;
    this.setData({ purchasing: true, showPaywall: false });
    try {
      const payResult = await payment.purchase('deep_report');
      if (!payResult || !payResult.success) return;  // 取消/失败已 toast
      const paidRes = await api.union(this._buildPayload(true));
      if (!paidRes || !paidRes.report) {
        throw new Error('报告数据异常');
      }
      this._setPaidResult(paidRes);
    } catch (e) {
      console.warn('[hehun] 深度报告失败:', e);
      const detail = (e && e.detail) || '';
      // request() 对非 2xx 的 reject 是 {detail}（无 statusCode），403 分支为死代码
      if (typeof detail === 'string' && detail.indexOf('解锁') !== -1) {
        wx.showModal({
          title: '未解锁',
          content: detail,
          showCancel: false,
        });
      } else {
        wx.showToast({ title: '报告生成失败，请重试', icon: 'none' });
      }
    } finally {
      this.setData({ purchasing: false });
    }
  },

  _setPaidResult(paidRes) {
    const chapters = (paidRes.report && paidRes.report.chapters) || [];
    this.setData({
      report: paidRes.report,
      reportId: paidRes.reportId || '',
      showReport: true,
    });
    if (chapters.length) {
      wx.showToast({ title: '已存入报告页', icon: 'none' });
    }
  },

  onCloseReport() {
    this.setData({ showReport: false });
  },
  goReports() {
    wx.navigateTo({ url: '/pages/reports/reports' });
  },

  // ---- 换一个人再测：保留我方，清 TA 与结果 ----
  onSwitchPerson() {
    this.setData({
      submitted: false,
      result: null,
      dims: [],
      yuanLine: '',
      cliffhanger: '',
      hourNotSetNote: '',
      p2BirthYear: '请选择',
      p2BirthMonth: '请选择',
      p2BirthDay: '请选择',
      p2HourIdx: 0,
      p2HourSet: false,
      p2City: '请选择',
      p2CitySet: false,
      p2YearIdx: 0,
      p2MonthIdx: 0,
      p2DayIdx: 0,
      p2CityIdx: 0,
      p2FromCache: false,
      relation: '',
    });
  },

  // ---- 重新输入：清双方 ----
  onReInput() {
    this.setData({
      submitted: false,
      result: null,
      dims: [],
      yuanLine: '',
      cliffhanger: '',
      hourNotSetNote: '',
      p1BirthYear: '请选择',
      p1BirthMonth: '请选择',
      p1BirthDay: '请选择',
      p1HourIdx: 0,
      p1HourSet: false,
      p1City: '请选择',
      p1CitySet: false,
      p1YearIdx: 0,
      p1MonthIdx: 0,
      p1DayIdx: 0,
      p1CityIdx: 0,
      p2BirthYear: '请选择',
      p2BirthMonth: '请选择',
      p2BirthDay: '请选择',
      p2HourIdx: 0,
      p2HourSet: false,
      p2City: '请选择',
      p2CitySet: false,
      p2YearIdx: 0,
      p2MonthIdx: 0,
      p2DayIdx: 0,
      p2CityIdx: 0,
      p2FromCache: false,
      relation: '',
    });
  },

  // ---- 弹层滚动穿透拦截 ----
  noop() {},

  // ---- 分享 ----
  onShareAppMessage() {
    return {
      title: '双人合盘 · 缘分契合 - 测测你们合不合',
      path: '/pages/hehun/hehun',
    };
  },
});
