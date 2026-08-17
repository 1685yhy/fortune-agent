/* 出生年月日 · 一次选完（persons / hehun / bazi 共用）
 * - 公历：原生 mode="date" 日期选择器，选中后显示对应农历（明确阴阳历）
 * - 农历：三列联动选择器（年/月含闰/日），选中后显示对应公历
 * - 历法切换：日期自动换算（同一实际日子，不丢值）
 * 契约：
 *   properties: value='YYYY-MM-DD'（当前历法下的年月日数值）, calendar='solar'|'lunar'
 *   event change: { date, calendar } —— 日期选定或历法切换时触发
 * 数据字段兼容：父级保存仍是 year/month/day + calendar 标记，结构不变。 */
const lunar = require('../../utils/lunar');

const YEAR_MIN = lunar.LUNAR_YEAR_MIN;   // 1900
const YEAR_MAX = lunar.LUNAR_YEAR_MAX;   // 2100

function pad2(n) {
  return String(n).padStart(2, '0');
}

Component({
  properties: {
    value: { type: String, value: '' },        // 'YYYY-MM-DD'
    calendar: { type: String, value: 'solar' }, // solar | lunar
    placeholder: { type: String, value: '请选择出生年月日' },
  },

  data: {
    solarDate: '1990-01-01',   // 公历 picker 当前值
    dateText: '',              // 主显示：公历「1998年5月12日」/ 农历「四月十七日」
    subText: '',               // 对应历法显示：农历「四月十七日」/ 公历「1998-06-06」
    lunarYears: [],            // ['1900年'...]
    lunarMonths: [],           // ['正月'...'闰四月'...]
    lunarDays: [],             // ['初一'...'三十']
    lunarValues: [0, 0, 0],    // [年, 月, 日] 下标
  },

  observers: {
    'value, calendar': function (val, cal) {
      this._sync(val, cal);
    },
  },

  lifetimes: {
    attached() {
      this._buildYearList();
      this._sync(this.data.value, this.data.calendar);
    },
  },

  methods: {
    /* ── 内部同步：由 value/calendar 变化驱动 ── */
    _buildYearList() {
      const years = [];
      for (let y = YEAR_MIN; y <= YEAR_MAX; y++) years.push(y + '年');
      this._years = years;
      this.setData({ lunarYears: years });
    },

    _sync(val, cal) {
      const v = String(val || '');
      const isLunar = cal === 'lunar';
      let y = 0, m = 0, d = 0;
      if (v) {
        const parts = v.split('-');
        y = parseInt(parts[0], 10) || 0;
        m = parseInt(parts[1], 10) || 0;
        d = parseInt(parts[2], 10) || 0;
      }
      if (isLunar) {
        this._syncLunar(y, m, d);
      } else {
        this._syncSolar(y, m, d);
      }
    },

    /* 公历显示：主=公历，副=农历对应 */
    _syncSolar(y, m, d) {
      const valid = y >= YEAR_MIN && y <= YEAR_MAX && m >= 1 && m <= 12 && d >= 1 && d <= 31;
      let dateText = '';
      let subText = '';
      let solarDate = this.data.solarDate;
      if (valid) {
        const dateStr = y + '-' + pad2(m) + '-' + pad2(d);
        solarDate = dateStr;
        dateText = y + '年' + m + '月' + d + '日';
        const l = lunar.solar2lunar(y, m, d);
        if (l) subText = '农历 ' + lunar.lunarMonthName(l.year, l.month, l.isLeap) + lunar.lunarDayName(l.day);
      }
      this.setData({ solarDate, dateText, subText, lunarValues: [0, 0, 0] });
    },

    /* 农历显示：主=农历，副=公历对应；同时重建三列 */
    _syncLunar(y, m, d) {
      const yy = (y >= YEAR_MIN && y <= YEAR_MAX) ? y : 1990;
      const cols = this._buildLunarColumns(yy);
      let mi = 0;
      let di = 0;
      if (m >= 1) {
        const idx = cols.monthsMeta.findIndex((s) => s.m === m && !s.isLeap);
        if (idx !== -1) mi = idx;
      }
      if (d >= 1 && cols.days.length >= d) di = d - 1;
      // 指定月份才重算日列
      let days = cols.days;
      let daysMeta = cols.daysMeta;
      if (mi > 0) {
        const mm = cols.monthsMeta[mi];
        const dd = this._dayList(yy, mm.m, mm.isLeap);
        days = dd.labels;
        daysMeta = dd.meta;
        if (d >= 1 && dd.labels.length >= d) di = d - 1;
      }
      this.setData({
        lunarMonths: cols.months,
        lunarDays: days,
        lunarValues: [yy - YEAR_MIN, mi, di],
        lunarMonthsMeta: cols.monthsMeta,
        lunarDaysMeta: daysMeta,
        lunarYear: yy,
      });
      this._refreshLunarDisplay(yy, cols.monthsMeta[mi] || { m: 1, isLeap: false }, daysMeta[di] || { d: 1 });
    },

    _monthList(year) {
      const leap = lunar.leapMonth(year);
      const meta = [];
      const labels = [];
      for (let m = 1; m <= 12; m++) {
        meta.push({ m, isLeap: false });
        labels.push(lunar.lunarMonthName(year, m, false));
        if (leap > 0 && m === leap) {
          meta.push({ m, isLeap: true });
          labels.push(lunar.lunarMonthName(year, m, true));
        }
      }
      return { meta, labels };
    },

    _dayList(year, month, isLeap) {
      const count = isLeap ? lunar.leapDays(year) : lunar.monthDays(year, month);
      const labels = [];
      const meta = [];
      for (let d = 1; d <= count; d++) {
        meta.push({ d });
        labels.push(lunar.lunarDayName(d));
      }
      return { meta, labels };
    },

    _buildLunarColumns(year) {
      const months = this._monthList(year);
      const days = this._dayList(year, months.meta[0].m, months.meta[0].isLeap);
      return { months: months.labels, monthsMeta: months.meta, days: days.labels, daysMeta: days.meta };
    },

    /* 重算农历主/副显示 */
    _refreshLunarDisplay(year, monthMeta, dayMeta) {
      const dLabel = lunar.lunarDayName(dayMeta.d);
      const dateText = '农历 ' + lunar.lunarMonthName(year, monthMeta.m, monthMeta.isLeap) + dLabel;
      let subText = '';
      const s = lunar.lunar2solar(year, monthMeta.m, dayMeta.d, monthMeta.isLeap);
      if (s) subText = '公历 ' + s.year + '-' + pad2(s.month) + '-' + pad2(s.day);
      this.setData({ dateText, subText });
    },

    _emit(date, calendar) {
      this.triggerEvent('change', { date, calendar });
    },

    /* ── 事件 ── */
    onCalTap(e) {
      const cal = e.currentTarget.dataset.cal;
      if (cal === this.data.calendar) return;
      const v = String(this.data.value || '');
      let out = '';
      if (v) {
        const parts = v.split('-');
        const y = parseInt(parts[0], 10) || 0;
        const m = parseInt(parts[1], 10) || 0;
        const d = parseInt(parts[2], 10) || 0;
        if (cal === 'lunar') {
          const l = lunar.solar2lunar(y, m, d);
          if (l) out = l.year + '-' + pad2(l.month) + '-' + pad2(l.day);
        } else {
          const s = lunar.lunar2solar(y, m, d, false);
          if (s) out = s.year + '-' + pad2(s.month) + '-' + pad2(s.day);
        }
      }
      this._emit(out, cal);
    },

    onSolarChange(e) {
      const date = e.detail.value;
      this._emit(date, 'solar');
    },

    onLunarColumnChange(e) {
      const col = e.detail.column;
      const idx = e.detail.value;
      const year = YEAR_MIN + this.data.lunarValues[0];
      const months = this._monthList(year);
      const patch = {};
      if (col === 0) {
        // 年变 → 月/日列重建
        const days = this._dayList(year, months.meta[0].m, months.meta[0].isLeap);
        patch.lunarMonths = months.labels;
        patch.lunarMonthsMeta = months.meta;
        patch.lunarDays = days.labels;
        patch.lunarDaysMeta = days.meta;
        patch.lunarValues = [idx, 0, 0];
      } else if (col === 1) {
        const mm = months.meta[idx] || months.meta[0];
        const days = this._dayList(year, mm.m, mm.isLeap);
        patch.lunarMonths = months.labels;
        patch.lunarMonthsMeta = months.meta;
        patch.lunarDays = days.labels;
        patch.lunarDaysMeta = days.meta;
        patch.lunarValues = [this.data.lunarValues[0], idx, 0];
      } else {
        patch.lunarValues = [this.data.lunarValues[0], this.data.lunarValues[1], idx];
      }
      patch.lunarYear = year;
      this.setData(patch);
      // 联动：先让列更新后再按新值重算显示
      const nv = patch.lunarValues;
      const mm = (patch.lunarMonthsMeta || this.data.lunarMonthsMeta)[nv[1]] || { m: 1, isLeap: false };
      const dm = (patch.lunarDaysMeta || this.data.lunarDaysMeta)[nv[2]] || { d: 1 };
      this._refreshLunarDisplay(year, mm, dm);
    },

    onLunarChange(e) {
      const v = e.detail.value;
      const year = YEAR_MIN + parseInt(v[0], 10);
      const mm = (this.data.lunarMonthsMeta || [])[v[1]] || { m: 1, isLeap: false };
      const dm = (this.data.lunarDaysMeta || [])[v[2]] || { d: 1 };
      const date = year + '-' + pad2(mm.m) + '-' + pad2(dm.d);
      this._emit(date, 'lunar');
    },
  },
});
