// 万年历 — 月历宫格 + 日详情（问真"吉真万年历"同款，确定性 0 LLM）
// 数据流：onLoad/翻月 → GET /api/wannianli?year=&month=（月视图）
//         点某天 → GET /api/wannianli/day?date=（日详情，底部笺页）
// 确定性纯规则：lunar-python 历法 + 建除/黄黑道宜忌规则（后端引擎）
const api = require('../../utils/api');

const MIN_YEAR = 1900;
const MAX_YEAR = 2100;
const WEEK_HEAD = ['日', '一', '二', '三', '四', '五', '六'];

Page({
  data: {
    navOff: 0,              // 安全区避让（胶囊）
    year: 0,
    month: 0,
    monthText: '',
    weekHead: WEEK_HEAD,
    cells: [],              // 宫格（含前置空格 {blank:true} 与每日 {…}）
    todayDate: '',          // 设备时钟今日 YYYY-MM-DD（is_today/今日跳转以设备为准）
    todayMonth: '',         // 设备时钟今日年月（"今日"按钮显示/跳转用）
    loading: true,
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
    });
    this._loadMonth();
  },

  /* 安全区避让（同 me 页 _initNavOff） */
  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  _loadMonth() {
    const { year, month } = this.data;
    // 请求序号: 快速连点翻月时后到的旧响应丢弃, 不覆盖新月(UX批2 Important)
    const token = (this._monthToken = (this._monthToken || 0) + 1);
    this.setData({ loading: true });
    api.getWannianliMonth(year, month)
      .then((data) => {
        if (token !== this._monthToken) return; // 过期响应丢弃
        this._renderMonth(data);
      })
      .catch(() => {
        if (token !== this._monthToken) return;
        wx.showToast({ title: '万年历加载失败', icon: 'none' });
        // 失败时清空旧月宫格与标题, 避免"新月标题配旧月宫格"内容错配
        this.setData({ loading: false, cells: [], monthText: '' });
      });
  },

  _renderMonth(data) {
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
        jianchu: d.jianchu,
        quality: d.quality,            // 吉/平/凶（建除口径）
        qCls: { 吉: 'q-ji', 平: 'q-ping', 凶: 'q-xiong' }[d.quality] || 'q-ping',
        // P1-1 审查 C2: is_today 以设备时钟自算为准（后端月视图缓存 24h，
        // 服务端 is_today/today 可能过期——23:59 渲染的缓存次日会标错"今日"）
        isToday: d.date === this.data.todayDate,
      });
    }
    this.setData({
      cells,
      monthText: `${data.year}年${data.month}月`,
      loading: false,
    });
  },

  /* 翻月 */
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

  /* 点某天 → 日详情 */
  onTapDay(e) {
    const { date, blank } = e.currentTarget.dataset;
    if (blank || !date) return;
    this._openDetail(date);
  },

  _openDetail(date) {
    if (this.data.detailDate === date && this.data.detail) {
      // 同日重开: 仅重新展开, 不重复请求(UX批2 Minor)
      this.setData({ detailVisible: true });
      return;
    }
    this.setData({ detailDate: date, detailLoading: true, detail: null, detailVisible: true });
    api.getWannianliDay(date)
      .then((data) => {
        // WXML 不支持方法调用/复杂嵌套：旬空与建除颜色 class 预计算
        data.xunkongText = (data.xunkong || []).join('、');
        const jq = (data.jianchu || {}).quality;
        data.jcCls = jq === '吉' ? 'ws-zhi-huang' : (jq === '凶' ? 'ws-zhi-hei' : '');
        this.setData({ detail: data, detailLoading: false });
      })
      .catch(() => {
        wx.showToast({ title: '详情加载失败', icon: 'none' });
        this.setData({ detailLoading: false });
      });
  },

  onCloseDetail() {
    // 仅藏弹层, 保留 detail 缓存供同日重开直达
    this.setData({ detailVisible: false });
  },
  noop() {},

  goToday() {
    wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/today/today' }) });
  },
});
