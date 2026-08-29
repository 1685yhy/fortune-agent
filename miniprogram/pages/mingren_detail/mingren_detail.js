// 名人命例 — 详情页（L3-1）
// 数据流: GET /api/mingren/{name} → 简介 + 命理评注(穷通宝鉴) + 生平大事时间线
//         + 命盘卡(chart: 四柱/十神/藏干/纳音/meta) + 大运×大事对照(timeline)（B5-3）
// 未解锁(免费/基础访问前 35 之外) → 后端 403 {code: VIP_REQUIRED} → 锁定视图 + 开通引导
const api = require('../../utils/api');
const payment = require('../../utils/payment');
const theme = require('../../utils/theme');

/* 命盘卡视图（B5-3 纯函数：chart 服务端字段 → 渲染结构）
   字段名与服务端 serialize 同构（bazi/pillars/meta/qiyun/dayun），
   渲染只用命盘卡所需子集；无 chart → null。
   盘面按问真式「四柱为列、多行为格」转置为行数据（主星/天干/地支/藏干/
   副星/星运/空亡/纳音/神煞），窄屏可折叠（detailRows 折叠层）。 */
const PILLAR_NAMES = ['年柱', '月柱', '日柱', '时柱'];
const BASE_ROWS = ['shishen', 'gan', 'zhi', 'nayin'];

function buildChartView(chart) {
  if (!chart) return null;
  const meta = chart.meta || {};
  const lunar = meta.lunar || {};
  const qiyun = chart.qiyun || {};
  const ps = (chart.pillars || []).map((p) => ({
    name: p.name,
    ganzhi: p.ganzhi,
    gan: p.gan,
    zhi: p.zhi,
    shishen: p.shishen,
    isDay: !!p.is_day,
    canggan: (p.canggan || []).map((cg) => ({ gan: cg.gan, shishen: cg.shishen })),
    nayin: p.nayin || '',
    xingyun: p.xingyun || '',
    kong: p.kong || '—',
    shensha: p.shensha || [],
  }));
  const col = (fn) => ps.map(fn);
  const rows = [
    { key: 'shishen', label: '主星', category: 'shishen', values: col((p) => p.shishen) },
    { key: 'gan', label: '天干', values: col((p) => p.gan) },
    { key: 'zhi', label: '地支', values: col((p) => p.zhi) },
    { key: 'canggan', label: '藏干', values: col((p) => p.canggan.map((c) => c.gan).join('')) },
    { key: 'cangss', label: '副星', values: col((p) => p.canggan.map((c) => c.shishen).join('')) },
    { key: 'xingyun', label: '星运', category: 'zhangsheng', values: col((p) => p.xingyun) },
    { key: 'kong', label: '空亡', values: col((p) => p.kong || '—') },
    { key: 'nayin', label: '纳音', category: 'nayin', values: col((p) => p.nayin || '—') },
    { key: 'shensha', label: '神煞', category: 'shensha', values: col((p) => p.shensha) },
  ];
  return {
    pillars: ps,
    bazi: chart.bazi || [],
    dayMaster: chart.day_master || '',
    zodiac: meta.zodiac || '',
    shichen: meta.shichen || '',
    gankun: meta.gankun || '乾造',
    solarText: meta.solar_text || '',
    lunarText: `${lunar.year_ganzhi || ''}年${lunar.month_text || ''}${lunar.day_text || ''}`,
    hourNote: chart.hour_note || '',
    qiyun: {
      desc: qiyun.desc || '',
      startSui: qiyun.start_sui || 0,
      startYear: qiyun.start_year || 0,
    },
    // 问真式盘面行数据（含列位；dayCol=日柱下标，乾造/坤造标注位）
    rows,
    baseRows: rows.filter((r) => BASE_ROWS.includes(r.key)),
    detailRows: rows.filter((r) => !BASE_ROWS.includes(r.key)),
    dayCol: PILLAR_NAMES.length > 0 ? 2 : 0,
  };
}

Page({
  data: {
    dark: false,
    name: '',
    loading: true,
    failed: false,          // 加载失败(非 403/未找到) → 重试入口
    locked: false,          // 未解锁(403 VIP_REQUIRED)
    lockMessage: '',
    detail: null,
    memberDialogVisible: false,
    memberPlans: [],
    _buying: false,
    // B5-3 盘面折叠 + 知识弹层
    chartOpen: true,
    explain: { visible: false, title: '', sections: [] },
  },

  buildChartView,           // 命盘卡视图映射（node 测试入口；渲染不直接调用）

  /* 盘面详盘折叠（问真对标：藏干/星运/空亡/神煞 高密度行可收起，窄屏不超屏） */
  toggleChartDetail() {
    this.setData({ chartOpen: !this.data.chartOpen });
  },

  /* 点文字弹解释（十神/星运/纳音/神煞 → knowledge 接口，问真对标交互） */
  showExplain(e) {
    const { category, name } = e.currentTarget.dataset;
    if (!category || !name) return;
    this._explain({ title: name, name, category });
  },

  /* wxml 盘面单元格统一点击口：无 category（天干/地支/藏干行）不弹 */
  onCellTap(e) {
    const { category, name } = e.currentTarget.dataset;
    if (!category || !name) return;
    this._explain({ title: name, name, category });
  },

  /* 大运干支解释：天干+地支两段合并弹层 */
  showGanzhiExplain(e) {
    const gz = e.currentTarget.dataset.name;
    if (!gz || gz.length !== 2) return;
    this._explain({ title: gz + ' 大运', name: gz, category: 'ganzhi' });
  },

  _explain(params) {
    const load = params.category === 'ganzhi'
      ? Promise.all([
          api.getKnowledge('tiangan', params.name.charAt(0)),
          api.getKnowledge('dizhi', params.name.charAt(1)),
        ])
      : api.getKnowledge(params.category, params.name).then((item) => [item]);
    load
      .then((items) => {
        const found = items.filter(Boolean);
        if (!found.length) {
          wx.showToast({ title: '暂无此条目', icon: 'none' });
          return;
        }
        this.setData({
          explain: {
            visible: true,
            title: params.title,
            sections: found.map((it) => ({
              name: it.name || '',
              text: it.tip || it.content || '',
              extra: it.gujue || '',
            })),
          },
        });
      })
      .catch(() => wx.showToast({ title: '加载失败', icon: 'none' }));
  },

  closeExplain() {
    this.setData({ 'explain.visible': false });
  },
  noopExplain() { /* 阻止遮罩点击穿透 */ },

  onLoad(options) {
    theme.bindTheme(this);
    const name = decodeURIComponent(options.name || '');
    this.setData({ name });
    // 会员开通方案(同列表页: 基础三档 + 高级一档)
    const plans = ['monthly', 'quarterly', 'yearly', 'pro_monthly']
      .map((id) => payment.getProduct(id))
      .filter((p) => !!p)
      .map((p) => {
        const parts = String(p.priceLabel || '').split('/');
        return {
          id: p.id,
          name: p.name,
          priceNum: parts[0] || p.priceLabel,
          priceUnit: parts[1] ? '/' + parts[1] : '',
          description: p.description,
          icon: p.icon,
        };
      });
    this.setData({ memberPlans: plans });
    if (name) {
      this._load();
    } else {
      // 无 name 参数直入: 避免 loading 永真卡死, 提示后返回(UX批2 Minor)
      this.setData({ loading: false });
      wx.showToast({ title: '未指定命例', icon: 'none' });
      setTimeout(() => wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/mingren/mingren' }) }), 800);
    }
  },

  _load() {
    this.setData({ loading: true, locked: false, failed: false });
    api.getMingrenDetail(this.data.name)
      .then((d) => {
        wx.setNavigationBarTitle({ title: d.name });
        this.setData({
          detail: {
            name: d.name,
            info: d.info,
            info2: d.info2,
            source: d.source,
            hasInfo2: !!d.has_info2,
            flist: (d.flist || []).map((f) => ({
              year: f.name,
              text: f.data,
            })),
            // B5-3 命盘卡 + 大运×大事对照（无出生数据名人只有生平）
            hasChart: !!d.chart,
            chart: buildChartView(d.chart),
            timeline: d.timeline || [],
            birthText: d.birth_text || '',
            birthSource: d.birth_source || '',
          },
          name: d.name,
        });
      })
      .catch((err) => {
        console.warn('[mingren] 详情失败:', err);
        // 未解锁 → 锁定视图 + 开通引导（服务端 403 VIP_REQUIRED 语义）
        if (err && err.detail && err.detail.code === 'VIP_REQUIRED') {
          this.setData({ locked: true, lockMessage: err.detail.message || '' });
          return;
        }
        if (err && err.detail && err.detail === '未找到该命例') {
          wx.showToast({ title: '未找到该命例', icon: 'none' });
          setTimeout(() => wx.navigateBack(), 800);
          return;
        }
        wx.showToast({ title: '命例加载失败，请稍后再试', icon: 'none' });
        this.setData({ failed: true }); // 失败态提供重试路径(UX批2 Minor)
      })
      .finally(() => this.setData({ loading: false }));
  },

  /* 失败重试 */
  retryLoad() {
    this._load();
  },

  /* ── 未解锁: 开通高级会员 ── */
  openMemberDialog() {
    this.setData({ memberDialogVisible: true });
  },
  closeMemberDialog() {
    this.setData({ memberDialogVisible: false });
  },
  noop() { /* 阻止遮罩点击穿透 */ },

  buyPlan(e) {
    const planId = e.currentTarget.dataset.plan;
    if (!planId || this.data._buying) return;
    this.setData({ _buying: true });
    payment.subscribeMember(planId)
      .then((res) => {
        if (res && res.success) {
          this.setData({ memberDialogVisible: false });
          wx.showToast({ title: '开通成功 · 全量解锁', icon: 'none' });
          this._load();  // 解锁后重拉详情
        }
      })
      .catch(() => {
        wx.showToast({ title: '支付异常，请稍后再试', icon: 'none' });
      })
      .finally(() => { this.setData({ _buying: false }); });
  },
});
