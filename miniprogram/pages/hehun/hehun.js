// 双人合盘 · 缘分契合 — 免费钩子（契合分/等级/三维得分条/缘语+悬念半句/墨韵缘笺）+ ¥19.9 深度报告（四章）
// 流程：输入双人生辰（可档案直选）→ 免费结果 → [生成墨韵缘笺] → [解锁深度报告]
// 隐私红线：TA 生辰只存本机 localStorage('yuan_ta_birth')，绝不进 persons 云端接口；缘笺图片数据全为服务端脱敏。
const { logWarn } = require('../../utils/log');
const api = require('../../utils/api');
const payment = require('../../utils/payment');
const shareCard = require('../../utils/shareCard');
const personUtil = require('../../utils/persons');
const theme = require('../../utils/theme');

const TA_STORAGE_KEY = 'yuan_ta_birth';

const HOUR_OPTIONS = ['未填', '子时(23-01)', '丑时(01-03)', '寅时(03-05)', '卯时(05-07)',
  '辰时(07-09)', '巳时(09-11)', '午时(11-13)', '未时(13-15)',
  '申时(15-17)', '酉时(17-19)', '戌时(19-21)', '亥时(21-23)'];

const RELATIONS = ['恋人', '暧昧', '夫妻', '朋友', '暗恋'];

const lunar = require('../../utils/lunar');
// k9（R2-5 Minor③）：lunarDateToSolar 收敛进 utils/lunar.js（paipan/duipan/
// hehun 三页共用单点，函数体逐字节不变），本页只做模块级别名
const lunarDateToSolar = lunar.lunarDateToSolar;

Page({
  data: {
    dark: false,           // 暗黑模式（theme.bindTheme）
    // 我方 (p1)
    p1Date: '',            // 'YYYY-MM-DD'（出生年月日，一次选完）
    p1Cal: 'solar',        // solar | lunar（出生历法）
    p1HourIdx: 0,          // 0=未填；1-12 对应时辰序号 0-11
    p1HourSet: false,
    p1Gender: 'male',
    p1City: '',
    p1CitySet: false,
    // k39 S3：来源标识（本人由档案补全时显式标注；手动改任一项即清除 →
    // 「覆盖后以手填为准」）。服务端同一标注串「本人（来自档案）」，
    // 口径一致不两套（见 src/bot/handler.py HEHUN_SELF_FROM_ARCHIVE_LABEL）。
    p1FromArchive: false,

    // TA (p2)
    p2Date: '',
    p2Cal: 'solar',
    p2HourIdx: 0,
    p2HourSet: false,
    p2Gender: 'female',
    p2City: '',
    p2CitySet: false,
    p2FromCache: false,    // 本机「上次记录」回填标注
    p2FromArchive: false,  // k39 S3：TA 由档案补全的来源标识（同 p1FromArchive 口径）

    // k29：页级真太阳时开关（R2-4 产品口径：默认开=按出生地经度校准时辰；
    // 关=北京时间直排）。口径与 paipan 页一致：档案直选/默认命主预填时回显
    // 该档案 solar_time（p.solar_time !== 0）；手动填表 → 本页会话级默认开。
    // 开关作用于**双方**排盘（person1/person2 各自透传 solarTime）。
    solarOn: true,

    // Picker 数据
    hourOptions: HOUR_OPTIONS,

    // 关系标签（5 chips 单选，可取消）
    relations: RELATIONS,
    relation: '',

    // 档案直选
    persons: [],
    personsLoaded: false,
    // 档案选择弹层（自绘底部弹层，UX批3：showActionSheet 上限 6 项，档案多时后几位选不到）
    showPersonPicker: false,
    personPickerTarget: '',

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

    // 合盘历史记录（本用户可见，只含脱敏摘要）
    showHistory: false,
    historyList: [],
    historyLoading: false,
    viewingHistory: false, // 正在查看历史记录（结果视图，隐藏付费入口）

    // 分享
    shareTitle: '双人合盘 · 缘分契合 - 测测你们合不合',
  },

  onLoad() {
    theme.bindTheme(this);
    this.restoreTaCache();
    this.loadDefaultSelf();
  },

  // ---- 本机缓存：TA 生辰（隐私：仅本机，标记「他/她」，永不进 persons 云端接口） ----
  restoreTaCache() {
    let c = null;
    try { c = wx.getStorageSync(TA_STORAGE_KEY); } catch (e) { c = null; }
    // 旧缓存格式 {year,month,day} → 迁移为 'YYYY-MM-DD'
    let date = String((c && c.date) || '');
    if (!date && c && c.year && c.month && c.day) {
      const pad = (n) => String(n).padStart(2, '0');
      date = `${c.year}-${pad(c.month)}-${pad(c.day)}`;
    }
    if (!c || !date) return;
    const patch = {
      p2Date: date,
      p2Cal: c.cal === 'lunar' ? 'lunar' : 'solar',
      p2Gender: c.gender === 'female' ? 'female' : 'male',
      p2FromCache: true,
    };
    // 时辰/出生地为选填：有则回填；UX批3：缓存 hourIdx 缺失/非法（非 1-12）时置未填，
    // 不再默认填「子时」误导用户以为是上次真实值
    if (c.hourSet) {
      const hi = parseInt(c.hourIdx, 10);
      if (hi >= 1 && hi <= 12) {
        patch.p2HourIdx = hi;
        patch.p2HourSet = true;
      }
    }
    if (c.city) {
      patch.p2City = String(c.city);
      patch.p2CitySet = true;
    }
    if (c.relation && RELATIONS.indexOf(c.relation) !== -1) {
      patch.relation = c.relation;
    }
    this.setData(patch);
  },

  _saveTaCache() {
    const d = this.data;
    try {
      wx.setStorageSync(TA_STORAGE_KEY, {
        date: String(d.p2Date || ''),
        cal: d.p2Cal,
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
      logWarn('hehun getPersons 失败（跳过档案直选）', err);
      this.setData({ personsLoaded: true });
    });
  },

  // ---- 从档案选择（UX批3：自绘可滚动底部弹层——wx.showActionSheet 官方上限 6 项，
  // 档案 >6 人时后几位静默截断选不到；弹层样式仿本页历史记录弹层） ----
  onPickPerson(e) {
    const target = e.currentTarget.dataset.target;
    const persons = this.data.persons;
    if (!persons || !persons.length) {
      wx.showToast({ title: '暂无档案，可手动填写', icon: 'none' });
      return;
    }
    this.setData({ showPersonPicker: true, personPickerTarget: target });
  },

  onClosePersonPicker() {
    this.setData({ showPersonPicker: false, personPickerTarget: '' });
  },

  onPersonPick(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const p = this.data.persons[idx];
    const target = this.data.personPickerTarget;
    if (!p) return;
    this.setData({ showPersonPicker: false, personPickerTarget: '' });
    this._fillFromPerson(target, p);
  },

  _fillFromPerson(target, p) {
    if (!p || !p.birth_year || !p.birth_month || !p.birth_day) {
      wx.showToast({ title: '该档案生辰不完整', icon: 'none' });
      return;
    }
    const pad = (n) => String(n).padStart(2, '0');
    let date = `${p.birth_year}-${pad(p.birth_month)}-${pad(p.birth_day)}`;
    // 档案为农历 → 换算公历（合盘引擎只吃公历），历法列统一显示公历
    let cal = p.calendar === 'lunar' ? 'lunar' : 'solar';
    if (cal === 'lunar') {
      date = lunarDateToSolar(date);
      cal = 'solar';
    }
    const base = {
      [`${target}Date`]: date,
      [`${target}Cal`]: cal,
      [`${target}Gender`]: p.gender === 'female' ? 'female' : 'male',
      // k29：档案级真太阳时开关随档案回显（solar_time=0 关；缺失/旧档案 →
      // 默认开，与 paipan _fillFromPerson 逐字同口径）——原缺口：档案显式关
      // 了真太阳时的用户，合盘页仍拿修正后的盘，与档案口径不一致。
      solarOn: p.solar_time !== 0,
    };
    // 时辰（选填）：档案有时辰才回填
    // k77-M5 同类收口「宁少不假」：脏值（'abc'/99/'0x10'）不再被当作子时高亮
    //（hourToShichenIndex 对不认识的值返回 0）——必须先过 parseHourStrict。
    const bh = personUtil.parseHourStrict(p.birth_hour);
    if (p.birth_hour !== undefined && p.birth_hour !== null && p.birth_hour !== ''
        && bh >= 0 && bh <= 23) {
      const hi = personUtil.hourToShichenIndex(bh) + 1;
      base[`${target}HourIdx`] = hi;
      base[`${target}HourSet`] = true;
    } else {
      base[`${target}HourIdx`] = 0;
      base[`${target}HourSet`] = false;
    }
    // 出生地（选填，省/市选择器）
    if (p.city) {
      base[`${target}City`] = p.city;
      base[`${target}CitySet`] = true;
    } else {
      base[`${target}City`] = '';
      base[`${target}CitySet`] = false;
    }
    if (target === 'p2') base.p2FromCache = false;
    // k39 S3：档案补全 → 打来源标识（显式标注；手动改任一项即清除）
    base[`${target}FromArchive`] = true;
    this.setData(base);
  },

  // ---- Person 1（我方）Handlers ----
  // k39 S3：手动改任一项 → 清来源标识（覆盖后以手填为准）
  onP1DateChange(e) {
    this.setData({ p1Cal: e.detail.calendar, p1Date: e.detail.date, p1FromArchive: false });
  },
  onP1HourChange(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ p1HourIdx: idx, p1HourSet: idx > 0, p1FromArchive: false });
  },
  onP1CityChange(e) {
    this.setData({ p1City: e.detail.full, p1CitySet: !!e.detail.full, p1FromArchive: false });
  },
  /* 性别：男/女 大按钮（点击切换，与 paipan 同款） */
  onP1GenderTap(e) {
    this.setData({ p1Gender: e.currentTarget.dataset.gender, p1FromArchive: false });
  },

  // ---- Person 2（TA）Handlers ----
  onP2DateChange(e) {
    this.setData({ p2Cal: e.detail.calendar, p2Date: e.detail.date, p2FromCache: false, p2FromArchive: false });
  },
  onP2HourChange(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ p2HourIdx: idx, p2HourSet: idx > 0, p2FromCache: false, p2FromArchive: false });
  },
  onP2CityChange(e) {
    this.setData({ p2City: e.detail.full, p2CitySet: !!e.detail.full, p2FromCache: false, p2FromArchive: false });
  },
  onP2GenderTap(e) {
    this.setData({ p2Gender: e.currentTarget.dataset.gender, p2FromCache: false, p2FromArchive: false });
  },

  /* 真太阳时开关（k29，与 paipan 同款交互）：开=按出生地经度校准（solarTime:true）；
     关=北京时间直排（solarTime:false）。页级开关作用于双方排盘。 */
  onSolarTimeChange(e) {
    this.setData({ solarOn: !!e.detail.value });
  },

  // ---- 关系标签（5 chips 单选，再点取消） ----
  onRelationTap(e) {
    const r = e.currentTarget.dataset.rel;
    this.setData({ relation: this.data.relation === r ? '' : r });
  },

  // ---- 组装提交载荷（小程序契约：birthYear/birthMonth/birthDay/birthHour(0-11)/gender/city）
  // 日期一次选完：date + calendar；农历 → 先换算公历再提交（引擎只吃公历） ----
  _buildPerson(prefix) {
    const d = this.data;
    let date = String(d[`${prefix}Date`] || '');
    if (d[`${prefix}Cal`] === 'lunar') date = lunarDateToSolar(date);
    const parts = date.split('-');
    const p = {
      birthYear: parseInt(parts[0], 10),
      birthMonth: parseInt(parts[1], 10),
      birthDay: parseInt(parts[2], 10),
      gender: d[`${prefix}Gender`],
    };
    if (d[`${prefix}HourSet`]) p.birthHour = d[`${prefix}HourIdx`] - 1;  // 时辰序号 0-11
    if (d[`${prefix}CitySet`]) p.city = d[`${prefix}City`];
    // k29（R2-4）：真太阳时开关随载荷透传（页级开关 → 双方各带 solarTime）。
    // 后端 BaziInput.solarTime 默认开=产品口径，显式传值才可能关闭；两人生辰
    // 各自独立，故双方都带（非档案手动输入同样透传，不受档案路径影响）。
    p.solarTime = !!d.solarOn;
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
    if (!d.p1Date) {
      wx.showToast({ title: '请选择我方出生年月日', icon: 'none' });
      return;
    }
    if (!d.p2Date) {
      wx.showToast({ title: '请选择TA的出生年月日', icon: 'none' });
      return;
    }

    this.setData({ loading: true, errorMsg: '', submitted: false, result: null, viewingHistory: false });
    try {
      const result = await api.union(this._buildPayload(false));
      this._setResult(result);
      // 提交成功后 TA 生辰只存本机（隐私红线）
      this._saveTaCache();
      this.setData({ submitted: true, loading: false });
    } catch (err) {
      logWarn('hehun union 免费档失败', err);
      this.setData({
        loading: false,
        submitted: false,
        errorMsg: (err && err.detail) || '推演失败，请稍后重试',
      });
    }
  },

  // ---- 合盘历史记录（只显示自己的 · 脱敏摘要：得分/等级/关系/三维/缘语/缘笺，不含双方生辰） ----
  _timeText(iso) {
    if (!iso) return '';
    const t = new Date(String(iso).replace(' ', 'T'));
    if (Number.isNaN(t.getTime())) return String(iso);
    const pad = (n) => String(n).padStart(2, '0');
    return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`;
  },

  onShowHistory() {
    if (this.data.historyLoading) return;
    this.setData({ showHistory: true, historyLoading: true });
    api.unionHistory().then((res) => {
      const list = ((res && res.records) || []).map((r) => Object.assign({}, r, {
        timeText: this._timeText(r.created_at),
      }));
      this.setData({ historyList: list, historyLoading: false });
    }).catch(() => {
      this.setData({ historyLoading: false });
      wx.showToast({ title: '历史记录加载失败，请重试', icon: 'none' });
    });
  },

  onCloseHistory() {
    this.setData({ showHistory: false });
  },

  onHistoryTap(e) {
    const idx = e.currentTarget.dataset.idx;
    const rec = this.data.historyList[idx];
    if (!rec) return;
    const c = (rec.chart && rec.chart.type === 'yuan_union') ? rec.chart : {};
    const qp = c.quoteParts || {};
    const main = String(qp.main || '').replace(/。+$/, '');
    const dims = c.dimensions || {};
    const mkDim = (key, label) => {
      const it = dims[key] || {};
      return { key, label, score: it.score || 0, max: it.max || 0, color: this._barColor(it.score, it.max) };
    };
    const dimsArr = [mkDim('wuxing', '五行'), mkDim('shengxiao', '生肖'), mkDim('rizhu', '日柱')];
    // UX批3：旧版本归档无 quoteParts/dimensions 时，不再渲染孤立「。」缘语卡与 0/0 得分条
    const yuanParts = [main, qp.suffix].filter(Boolean);
    this.setData({
      showHistory: false,
      viewingHistory: true,
      submitted: true,
      result: {
        score: c.score || 0,
        levelLabel: c.level || '',
        levelSublabel: c.levelSublabel || '',
        relation: c.relation || '',
        dimensions: dims,
        yuan_card: c.yuan_card || null,
        transient: false,
      },
      dims: dimsArr.some((d) => d.max > 0) ? dimsArr : [],
      yuanLine: yuanParts.length ? yuanParts.join('，') + '。' : '',
      cliffhanger: qp.cliffhanger || '',
      hourNotSetNote: '',
      report: null,
    });
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
    // 与历史查看路径一致：quoteParts 缺失时缘语卡不渲染孤立「。」
    const yuanParts = [main, qp.suffix].filter(Boolean);
    this.setData({
      result,
      dims: [mkDim('wuxing', '五行'), mkDim('shengxiao', '生肖'), mkDim('rizhu', '日柱')],
      yuanLine: yuanParts.length ? yuanParts.join('，') + '。' : '',
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

  // ---- 悬念半句「展开」→ 付费墙（历史查看态无双方生辰，不可付费重跑） ----
  onExpandCliffhanger() {
    if (this.data.viewingHistory) {
      wx.showToast({ title: '历史记录不含双方生辰，无法解锁深度报告', icon: 'none', duration: 2200 });
      return;
    }
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
      logWarn('hehun 深度报告失败', e);
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
    if (!chapters.length) return;
    // G3 H-13：归档成败以服务端 reportId 为准——后端归档失败时 report_id=""，
    // 原实现只查 chapters 就 toast「已存入报告页」= 假成功（报告页实际查不到）。
    // 报告本体已内联展示，如实提示「暂未存入」，不假装已归档
    if (paidRes.reportId) {
      wx.showToast({ title: '已存入报告页', icon: 'none' });
    } else {
      wx.showToast({ title: '报告已生成 · 暂未存入报告页', icon: 'none' });
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
      viewingHistory: false,
      p2Date: '',
      p2Cal: 'solar',
      p2HourIdx: 0,
      p2HourSet: false,
      p2City: '',
      p2CitySet: false,
      p2FromCache: false,
      p2FromArchive: false,
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
      viewingHistory: false,
      p1Date: '',
      p1Cal: 'solar',
      p1HourIdx: 0,
      p1HourSet: false,
      p1City: '',
      p1CitySet: false,
      p1FromArchive: false,
      p2Date: '',
      p2Cal: 'solar',
      p2HourIdx: 0,
      p2HourSet: false,
      p2City: '',
      p2CitySet: false,
      p2FromCache: false,
      p2FromArchive: false,
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
