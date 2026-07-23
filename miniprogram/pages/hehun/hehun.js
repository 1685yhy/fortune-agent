// 合婚配对 — 八字婚姻契合度分析
const api = require('../../utils/api');
const { MESSAGES } = require('../../utils/messages');

const HOUR_LABELS = [
  '子时(23-01)', '丑时(01-03)', '寅时(03-05)', '卯时(05-07)',
  '辰时(07-09)', '巳时(09-11)', '午时(11-13)', '未时(13-15)',
  '申时(15-17)', '酉时(17-19)', '戌时(19-21)', '亥时(21-23)',
];

const CITIES = [
  '请选择', '北京', '上海', '广州', '深圳', '杭州', '成都', '武汉',
  '西安', '南京', '重庆', '天津', '苏州', '长沙', '郑州', '东莞',
  '青岛', '沈阳', '宁波', '昆明', '大连', '厦门', '合肥', '佛山',
  '福州', '哈尔滨', '济南', '温州', '长春', '石家庄', '常州',
  '泉州', '南宁', '贵阳', '南昌', '太原', '烟台', '嘉兴', '南通',
  '金华', '珠海', '惠州', '徐州', '海口', '乌鲁木齐', '绍兴',
  '中山', '台州', '兰州', '保定', '镇江', '扬州', '桂林', '洛阳',
];

Page({
  data: {
    // Person 1
    p1BirthYear: '1990',
    p1BirthMonth: '1',
    p1BirthDay: '1',
    p1BirthHour: '0',
    p1Gender: 'male',
    p1City: '北京',
    p1HourLabel: HOUR_LABELS[0],

    // Person 2
    p2BirthYear: '1990',
    p2BirthMonth: '1',
    p2BirthDay: '1',
    p2BirthHour: '0',
    p2Gender: 'female',
    p2City: '北京',
    p2HourLabel: HOUR_LABELS[0],

    // Picker data
    years: [],
    months: [],
    days: [],
    hours: [],
    hourLabels: HOUR_LABELS,
    cities: CITIES,

    // Picker indices
    p1PickerYearIdx: 0,
    p1PickerMonthIdx: 0,
    p1PickerDayIdx: 0,
    p1PickerHourIdx: 0,
    p1PickerCityIdx: 1,
    p2PickerYearIdx: 0,
    p2PickerMonthIdx: 0,
    p2PickerDayIdx: 0,
    p2PickerHourIdx: 0,
    p2PickerCityIdx: 1,

    // States
    skeletonLoading: true,
    submitted: false,
    loading: false,
    error: null,
    result: null,

    // Error state
    showError: false,
    errorType: '',

    // Computed display values
    scorePercent: 0,
    scoreRingColor: '#D4A843',
    scoreDescription: '',
    wuxingScoreColor: '#D4A843',
    shengxiaoScoreColor: '#D4A843',
    rizhuScoreColor: '#D4A843',
  },

  onLoad() {
    this.initPickerData();
    // Disable skeleton after initial render
    setTimeout(() => {
      this.setData({ skeletonLoading: false });
    }, 300);
  },

  // ---- 初始化选择器数据 ----
  initPickerData() {
    const years = [];
    const months = [];
    const days = [];
    const hours = [];

    for (let y = 1940; y <= 2024; y++) {
      years.push(String(y));
    }
    for (let m = 1; m <= 12; m++) {
      months.push(String(m));
    }
    for (let d = 1; d <= 31; d++) {
      days.push(String(d));
    }
    for (let h = 0; h < 12; h++) {
      hours.push(String(h));
    }

    this.setData({ years, months, days, hours });
    this.updatePickerIndices();
  },

  // 更新所有 picker 索引
  updatePickerIndices() {
    const d = this.data;
    this.setData({
      p1PickerYearIdx: d.years.indexOf(d.p1BirthYear),
      p1PickerMonthIdx: d.months.indexOf(d.p1BirthMonth),
      p1PickerDayIdx: d.days.indexOf(d.p1BirthDay),
      p1PickerHourIdx: d.hours.indexOf(d.p1BirthHour),
      p1PickerCityIdx: Math.max(0, d.cities.indexOf(d.p1City)),
      p2PickerYearIdx: d.years.indexOf(d.p2BirthYear),
      p2PickerMonthIdx: d.months.indexOf(d.p2BirthMonth),
      p2PickerDayIdx: d.days.indexOf(d.p2BirthDay),
      p2PickerHourIdx: d.hours.indexOf(d.p2BirthHour),
      p2PickerCityIdx: Math.max(0, d.cities.indexOf(d.p2City)),
    });
  },

  // ---- Person 1 Handlers ----
  onP1BirthYearChange(e) {
    const p1BirthYear = this.data.years[e.detail.value];
    this.setData({ p1BirthYear });
    this.updatePickerIndices();
  },
  onP1BirthMonthChange(e) {
    const p1BirthMonth = this.data.months[e.detail.value];
    this.setData({ p1BirthMonth });
    this.updatePickerIndices();
  },
  onP1BirthDayChange(e) {
    const p1BirthDay = this.data.days[e.detail.value];
    this.setData({ p1BirthDay });
    this.updatePickerIndices();
  },
  onP1BirthHourChange(e) {
    const idx = parseInt(e.detail.value, 10);
    const p1BirthHour = this.data.hours[idx];
    const p1HourLabel = HOUR_LABELS[idx];
    this.setData({ p1BirthHour, p1HourLabel });
    this.updatePickerIndices();
  },
  onP1GenderChange(e) {
    this.setData({ p1Gender: e.detail.value });
  },
  onP1CityChange(e) {
    const p1City = this.data.cities[e.detail.value];
    this.setData({ p1City });
    this.updatePickerIndices();
  },

  // ---- Person 2 Handlers ----
  onP2BirthYearChange(e) {
    const p2BirthYear = this.data.years[e.detail.value];
    this.setData({ p2BirthYear });
    this.updatePickerIndices();
  },
  onP2BirthMonthChange(e) {
    const p2BirthMonth = this.data.months[e.detail.value];
    this.setData({ p2BirthMonth });
    this.updatePickerIndices();
  },
  onP2BirthDayChange(e) {
    const p2BirthDay = this.data.days[e.detail.value];
    this.setData({ p2BirthDay });
    this.updatePickerIndices();
  },
  onP2BirthHourChange(e) {
    const idx = parseInt(e.detail.value, 10);
    const p2BirthHour = this.data.hours[idx];
    const p2HourLabel = HOUR_LABELS[idx];
    this.setData({ p2BirthHour, p2HourLabel });
    this.updatePickerIndices();
  },
  onP2GenderChange(e) {
    this.setData({ p2Gender: e.detail.value });
  },
  onP2CityChange(e) {
    const p2City = this.data.cities[e.detail.value];
    this.setData({ p2City });
    this.updatePickerIndices();
  },

  // ---- Submit ----
  async onSubmit() {
    const d = this.data;

    // Basic validation
    if (d.p1City === '请选择' || d.p2City === '请选择') {
      wx.showToast({ title: '请选择出生城市', icon: 'none' });
      return;
    }

    this.setData({ loading: true, error: null, submitted: false });

    const person1 = {
      birthYear: d.p1BirthYear,
      birthMonth: d.p1BirthMonth,
      birthDay: d.p1BirthDay,
      birthHour: d.p1BirthHour,
      gender: d.p1Gender,
      city: d.p1City,
    };

    const person2 = {
      birthYear: d.p2BirthYear,
      birthMonth: d.p2BirthMonth,
      birthDay: d.p2BirthDay,
      birthHour: d.p2BirthHour,
      gender: d.p2Gender,
      city: d.p2City,
    };

    try {
      const result = await api.hehun({ person1, person2 });
      this._setResult(result);
      this.setData({ submitted: true, loading: false });
    } catch (err) {
      console.warn('[hehun] API call failed, using demo data:', err);
      // Show error state — user can retry
      this.setData({
        showError: true,
        errorType: err.name === 'NetworkError' ? 'network' : 'server',
        loading: false,
        submitted: false,
      });
    }
  },

  // ---- Compute and set result display values ----
  _setResult(result) {
    const score = result ? result.score : 0;
    const pct = Math.min(100, Math.max(0, score));

    // Score color
    let sColor;
    if (score >= 80) sColor = '#D4A843';
    else if (score >= 60) sColor = '#5B8C5A';
    else sColor = '#C2413D';

    // Score description
    let desc;
    if (score >= 90) desc = '天作之合，命定姻缘';
    else if (score >= 80) desc = '上等婚配，琴瑟和鸣';
    else if (score >= 70) desc = '中等偏上，相得益彰';
    else if (score >= 60) desc = '中等婚配，需用心经营';
    else if (score >= 50) desc = '基础尚可，多加磨合';
    else desc = '缘分较浅，慎重考虑';

    // Compute per-dimension colors
    const wuxingColor = this._scoreColor(result.wuxing ? result.wuxing.score : 0);
    const shengxiaoColor = this._scoreColor(result.shengxiao ? result.shengxiao.score : 0);
    const rizhuColor = this._scoreColor(result.rizhu ? result.rizhu.score : 0);

    this.setData({
      result,
      scorePercent: pct,
      scoreRingColor: sColor,
      scoreDescription: desc,
      wuxingScoreColor: wuxingColor,
      shengxiaoScoreColor: shengxiaoColor,
      rizhuScoreColor: rizhuColor,
    });
  },

  _scoreColor(score) {
    if (score >= 80) return '#D4A843';
    if (score >= 60) return '#5B8C5A';
    return '#C2413D';
  },

  // ---- Fallback Demo Data ----
  getDemoResult(person1, person2) {
    const year1 = parseInt(person1.birthYear, 10);
    const year2 = parseInt(person2.birthYear, 10);
    const yearDiff = Math.abs(year1 - year2);

    // Compute a pseudo-random but deterministic score based on inputs
    const seed = (year1 * 31 + parseInt(person1.birthMonth, 10) * 7 + parseInt(person1.birthDay, 10) * 13
                + year2 * 37 + parseInt(person2.birthMonth, 10) * 11 + parseInt(person2.birthDay, 10) * 17) % 101;
    const baseScore = 60 + (seed % 31);
    const wuxingScore = Math.min(100, baseScore + (yearDiff % 15) - 5);
    const shengxiaoScore = Math.min(100, baseScore + (yearDiff % 10) + 3);
    const rizhuScore = Math.min(100, baseScore - (yearDiff % 12) + 8);

    const totalScore = Math.round((wuxingScore + shengxiaoScore + rizhuScore) / 3);

    const advice = this.getAdvice(totalScore, person1.gender, person2.gender);

    return {
      score: totalScore,
      wuxing: { score: wuxingScore, detail: wuxingScore >= 80 ? '相生' : wuxingScore >= 60 ? '中等' : '相克' },
      shengxiao: { score: shengxiaoScore, detail: shengxiaoScore >= 80 ? '三合' : shengxiaoScore >= 60 ? '中等' : '相冲' },
      rizhu: { score: rizhuScore, detail: rizhuScore >= 80 ? '相合' : rizhuScore >= 60 ? '平和' : '相刑' },
      advice,
    };
  },

  getAdvice(score, gender1, gender2) {
    const base = [];
    if (score >= 80) {
      base.push('双方八字高度契合，天作之合，宜早结连理');
      base.push('五行互补，相互成就，婚后生活和谐美满');
      base.push('建议选择吉日举办婚礼，有助运势进一步提升');
    } else if (score >= 60) {
      base.push('双方有较好的缘分基础，需要用心经营');
      base.push('注意沟通方式，相互包容理解是长久之道');
      base.push('可通过家居风水布局改善五行平衡');
      base.push('在重大决策上建议多听取对方意见');
    } else {
      base.push('双方八字存在一定冲突，需谨慎对待婚姻');
      base.push('建议选择专业人士化解五行冲克');
      base.push('保持适当距离和独立空间有助于关系和谐');
      base.push('婚前多相处磨合，充分了解彼此差异');
    }
    base.push('婚姻幸福与否，更多在于彼此的用心经营与相互珍惜');
    return base;
  },

  // ---- Retry after error ----
  onErrorRetry() {
    this.setData({ showError: false });
    this.onSubmit();
  },

  // ---- Reset ----
  onReset() {
    this.setData({
      submitted: false,
      loading: false,
      error: null,
      result: null,
    });
  },

  // ---- Share ----
  onShareAppMessage() {
    return {
      title: '合婚配对 - 看你们的八字缘分',
      path: '/pages/hehun/hehun',
    };
  },
});
