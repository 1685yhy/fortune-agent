// 万年历 — 月历宫格 + 日详情（问真"吉真万年历"同款，确定性 0 LLM）
// 数据流：onLoad/翻月 → GET /api/wannianli?year=&month=（月视图）
//         点某天 → GET /api/wannianli/day?date=（日详情，底部笺页 + 宜忌摘要行）
// B5-2 L：swiper 三页滑动翻月（上/当/下月预渲染，滑动换月后移位补远端页）、
//         进入默认定位当前月并选中今日、月历下方选中日宜忌摘要行（未选中显示今日）、
//         详情弹层 吉时/彭祖百忌/胎神 展示 + 复制文本导出。
// 确定性纯规则：lunar-python 历法 + 建除/黄黑道宜忌规则（后端引擎）
const api = require('../../utils/api');

const MIN_YEAR = 1900;
const MAX_YEAR = 2100;
const WEEK_HEAD = ['日', '一', '二', '三', '四', '五', '六'];
// 6 行宫格为最大值（如 2026-08：首日周六 31 天 → 6 行），固定高度避免翻页跳动
const SWIPER_GRID_HEIGHT = 1040; // rpx

Page({
  data: {
    navOff: 0,              // 安全区避让（胶囊）
    year: 0,
    month: 0,
    monthText: '',
    weekHead: WEEK_HEAD,
    swiperHeight: SWIPER_GRID_HEIGHT,
    swiperCurrent: 1,       // swiper 中心页下标（0=上月 1=当月 2=下月，滑动后回中）
    // swiper 三页月视图: [{year, month, key, monthText, cells, loading}]（越界月为 null）
    months: [null, null, null],
    todayDate: '',          // 设备时钟今日 YYYY-MM-DD（is_today/今日跳转以设备为准）
    todayMonth: '',         // 设备时钟今日年月（"今日"按钮显示/跳转用）
    selectedDate: '',       // 选中日（进入默认今日；切走后不受影响）
    loading: true,
    // 宜忌摘要行（月历下方）：选中日 宜/忌 前几项；未选中日显示今日摘要
    summary: null,          // {date, title, yi[], ji[]}
    summaryLoading: false,
    // 日详情（底部笺页）
    detail: null,           // 后端 day_detail 全字段
    detailDate: '',         // 已选中日期（重复点击同一天不重复拉取）
    detailVisible: false,   // 弹层显隐（detail 缓存保留, 关弹层重开同日不重复请求）
    detailLoading: false,
  },

  onLoad() {
    this._initNavOff();
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    this.setData({
      year: y,
      month: now.getMonth() + 1,
      todayMonth: `${y}-${m}`,
      todayDate: `${y}-${m}-${d}`,
      selectedDate: `${y}-${m}-${d}`,   // 今日默认选中
    });
    this._loadMonth();
    this._loadSummary(this.data.selectedDate);   // 未选中日 → 今日摘要
  },

  /* 安全区避让（同 me 页 _initNavOff） */
  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 年月偏移: 上/下 n 月，越出 1900-2100 返回 null */
  _offsetMonth(year, month, off) {
    let y = year;
    let m = month + off;
    while (m < 1) { m += 12; y -= 1; }
    while (m > 12) { m -= 12; y += 1; }
    if (y < MIN_YEAR || y > MAX_YEAR) return null;
    return [y, m];
  },

  /* 载入当前年月 ± 1 的三页预渲染（请求序号防旧响应覆盖，UX批2 Important） */
  _loadMonth() {
    const { year, month } = this.data;
    const token = (this._monthToken = (this._monthToken || 0) + 1);
    const months = this.data.months.map((_, i) => {
      const ym = this._offsetMonth(year, month, i - 1);
      if (!ym) return null;
      return {
        key: `${ym[0]}-${ym[1]}`,
        year: ym[0],
        month: ym[1],
        monthText: '',
        cells: [],
        loading: true,
      };
    });
    this.setData({ months, loading: true });
    for (let i = 0; i < 3; i++) {
      const s = months[i];
      if (s) this._fetchMonth(s.year, s.month, i, token);
    }
  },

  _fetchMonth(y, m, slotIdx, token) {
    api.getWannianliMonth(y, m)
      .then((data) => {
        if (token !== this._monthToken) return; // 过期响应丢弃
        this._renderMonth(data, slotIdx);
      })
      .catch(() => {
        if (token !== this._monthToken) return;
        // 失败仅清空该页宫格, 避免"新月标题配旧月宫格"内容错配
        const months = this.data.months.map((mo, i) => {
          if (i !== slotIdx || !mo) return mo;
          return { ...mo, cells: [], loading: false };
        });
        const center = months[1];
        this.setData({
          months,
          loading: false,
          monthText: (center && center.monthText) || this.data.monthText,
        });
        // 仅中心页失败才弹窗（边页预取失败属噪声：滑动到达时仍会重新补拉）
        if (slotIdx === 1) wx.showToast({ title: '万年历加载失败', icon: 'none' });
      });
  },

  _renderMonth(data, slotIdx) {
    const cells = [];
    // 前置空格：first_weekday 为 0=周日（calendar.monthrange 口径），与星期表头对应
    for (let i = 0; i < data.first_weekday; i++) cells.push({ blank: true });
    for (const d of data.days) {
      cells.push({
        blank: false,
        date: d.date,
        day: d.day,
        ganzhi: d.day_ganzhi,
        cellLunar: d.cell_lunar,
        isJieqi: !!d.jieqi,            // 节气朱砂标注
        isFestival: !!d.festival,
        festival: d.festival,
        yiDot: (d.yi_short[0] || '').slice(0, 2),   // 宜忌点（简表首项）
        jiDot: (d.ji_short[0] || '').slice(0, 2),
        huanghedao: d.huanghedao,      // 黄道/黑道
        jianchu: d.jianchu,            // 值日名（建除十二神）
        // k27c（产品 2026-09-11 拍板）: 不再透传/预计算建除吉凶（原 quality +
        // qCls 颜色映射）—— 万年历面不展示吉/凶判定; 吉凶总评只在择吉/聊天给。
        // P1-1 审查 C2: is_today 以设备时钟自算为准（后端月视图缓存 24h，
        // 服务端 is_today/today 可能过期——23:59 渲染的缓存次日会标错"今日"）
        isToday: d.date === this.data.todayDate,
        // B5-2 L: 选中态（默认今日；点某天更新）
        isSelected: d.date === this.data.selectedDate,
      });
    }
    const monthText = `${data.year}年${data.month}月`;
    const months = this.data.months.map((mo, i) => {
      if (i !== slotIdx) return mo;
      return {
        key: `${data.year}-${data.month}`,
        year: data.year,
        month: data.month,
        monthText,
        cells,
        loading: false,
      };
    });
    const patch = { months };
    if (slotIdx === 1) {
      // 中心页就绪：标题 + 撤初始骨架（swiper 显示）
      patch.monthText = monthText;
      patch.loading = false;
    }
    this.setData(patch);
  },

  /* swiper 滑动翻月：滑到边页 → 以目标页为新月中心移位补页（回中无感） */
  onSwiperChange(e) {
    const cur = e.detail.current;
    if (cur === 1) return;                 // 轻滑回弹/仍在中心：不换月
    const dir = cur === 2 ? 1 : -1;        // 滑到第 3 页=前进一月；第 1 页=后退一月
    const { year, month } = this.data;
    const ym = this._offsetMonth(year, month, dir);
    if (!ym) {
      // 越界（1900/2100 边界）: 弹回中心，不换月
      this.setData({ swiperCurrent: 1 });
      return;
    }
    const [y, m] = ym;
    const months = this.data.months;
    const far = this._offsetMonth(y, m, dir);          // 远端新页（可能越界=null）
    const farSlot = far
      ? { key: `${far[0]}-${far[1]}`, year: far[0], month: far[1], monthText: '', cells: [], loading: true }
      : null;
    const newMonths = dir > 0
      ? [months[1], months[2], farSlot]
      : [farSlot, months[0], months[1]];
    // 目标页内容已成新中心；新中心标题取已渲染值
    this.setData({
      year: y,
      month: m,
      months: newMonths,
      monthText: (dir > 0 ? months[2] : months[0]).monthText || `${y}年${m}月`,
      swiperCurrent: 1,                  // 回中：内容与刚看的一致, 视觉无感
    });
    if (far) {
      const token = (this._monthToken = (this._monthToken || 0) + 1);
      this._fetchMonth(far[0], far[1], dir > 0 ? 2 : 0, token);
    }
  },

  /* 翻月（按钮辅助，保留原有语义） */
  onPrevMonth() {
    let { year, month } = this.data;
    month -= 1;
    if (month < 1) { month = 12; year -= 1; }
    if (year < MIN_YEAR) { wx.showToast({ title: '历法仅支持 1900 年起', icon: 'none' }); return; }
    this.setData({ year, month, detail: null, detailVisible: false, detailDate: '' });
    this._loadMonth();
  },
  onNextMonth() {
    let { year, month } = this.data;
    month += 1;
    if (month > 12) { month = 1; year += 1; }
    if (year > MAX_YEAR) { wx.showToast({ title: '历法仅支持至 2100 年', icon: 'none' }); return; }
    this.setData({ year, month, detail: null, detailVisible: false, detailDate: '' });
    this._loadMonth();
  },
  onJumpToday() {
    // 今日跳转以设备时钟为准（服务端 today 随月视图缓存 24h 可能过期）
    const t = this.data.todayDate || '';
    if (!t) return;
    const [y, m] = t.split('-').map(Number);
    this.setData({ year: y, month: m, detail: null, detailVisible: false, detailDate: '' });
    this._loadMonth();
  },

  /* 点某天 → 日详情（同步更新选中态与摘要行） */
  onTapDay(e) {
    const { date, blank } = e.currentTarget.dataset;
    if (blank || !date) return;
    this._markSelected(date);
    this._openDetail(date);
  },

  /* 更新选中态（中心/边页宫格高亮） */
  _markSelected(date) {
    const months = this.data.months.map((mo) => {
      if (!mo || !mo.cells) return mo;
      return {
        ...mo,
        cells: mo.cells.map((c) => (c.blank ? c : { ...c, isSelected: c.date === date })),
      };
    });
    this.setData({ months, selectedDate: date });
  },

  _openDetail(date) {
    if (this.data.detailDate === date && this.data.detail) {
      // 同日重开: 仅重新展开, 不重复请求(UX批2 Minor)
      this.setData({ detailVisible: true });
      return;
    }
    this._summaryToken = (this._summaryToken || 0) + 1; // 新用户意图: 作废在途摘要请求
    // k34 A13（B5-2 Minor②）：详情请求序号——快速连点 A→B 时，A 的迟到响应不得
    // 覆盖 B 的详情（含详情同步写的摘要行）。与 summaryLoading/_loadSummary 同款做法。
    const token = (this._detailToken = (this._detailToken || 0) + 1);
    this.setData({ detailDate: date, detailLoading: true, detail: null, detailVisible: true });
    api.getWannianliDay(date)
      .then((data) => {
        if (token !== this._detailToken) return; // 迟到响应丢弃（用户已点选他日）
        // WXML 不支持方法调用/复杂嵌套：旬空预计算
        data.xunkongText = (data.xunkong || []).join('、');
        // k27c: 原按建除吉凶预计算 jcCls（吉=朱砂/凶=淡墨）—— 已下线, 不再消费
        // 后端 quality 字段（该字段同时从后端删除, 见 src/engines/wannianli.py）
        this.setData({ detail: data, detailLoading: false });
        this._setSummary(data);          // 摘要行与详情同一次请求
      })
      .catch(() => {
        if (token !== this._detailToken) return; // 过期详情失败不弹窗（不打断新选中）
        wx.showToast({ title: '详情加载失败', icon: 'none' });
        this.setData({ detailLoading: false });
      });
  },

  /* 宜忌摘要行：宜/忌 各前 4 项（未选中日 → 今日摘要）
     请求序号防旧响应覆盖：onLoad 的今日摘要若在用户点选他日之后才返回，必须丢弃 */
  _loadSummary(date) {
    const token = (this._summaryToken = (this._summaryToken || 0) + 1);
    this.setData({ summaryLoading: true });
    api.getWannianliDay(date)
      .then((data) => {
        if (token !== this._summaryToken) return; // 迟到响应丢弃（用户已改选他日）
        this._setSummary(data);
      })
      .catch(() => {
        if (token !== this._summaryToken) return;
        wx.showToast({ title: '摘要加载失败', icon: 'none' });
        this.setData({ summaryLoading: false });
      });
  },

  _setSummary(data) {
    const date = data.date || '';
    this.setData({
      summary: {
        date,
        title: date === this.data.todayDate ? '今日宜忌速览' : '选中日宜忌速览',
        yi: (data.yi || []).slice(0, 4),
        ji: (data.ji || []).slice(0, 4),
      },
      summaryLoading: false,
    });
  },

  onCloseDetail() {
    // 仅藏弹层, 保留 detail 缓存供同日重开直达
    this.setData({ detailVisible: false });
  },
  noop() {},

  /* 复制文本导出：日期+宜忌+吉时+彭祖百忌+胎神 */
  _buildCopyText(det) {
    const rows = [];
    rows.push(`【万年历】${det.date} 星期${det.weekday}`);
    const lunar = det.lunar || {};
    if (lunar.full) rows.push(`${lunar.full}（${(det.ganzhi || {}).day || ''}日）`);
    if (det.yi && det.yi.length) rows.push(`宜：${det.yi.join('、')}`);
    if (det.ji && det.ji.length) rows.push(`忌：${det.ji.join('、')}`);
    const jiTimes = (det.jishi || [])
      .filter((t) => t.luck === '吉')
      .map((t) => `${t.time} ${t.range}`)
      .join('、');
    if (jiTimes) rows.push(`吉时：${jiTimes}`);
    const pz = det.pengzu || {};
    if (pz.gan || pz.zhi) rows.push(`彭祖百忌：${pz.gan}；${pz.zhi}`);
    if (det.taishen && det.taishen.desc) rows.push(`胎神：${det.taishen.desc}`);
    return rows.join('\n');
  },

  onCopyDetail() {
    if (!this.data.detail) return;
    wx.setClipboardData({
      data: this._buildCopyText(this.data.detail),
      success: () => wx.showToast({ title: '已复制', icon: 'success' }),
    });
  },

  goToday() {
    wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/today/today' }) });
  },
});
