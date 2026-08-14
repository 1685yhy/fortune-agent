// 择吉日 — 吉日卡页（大事择吉日）：3 卡纵排 + 换一批 + 选它→清单页
// 数据流：带 scene 进入 → POST /api/zeri/refresh 取 3 卡 → 选它 → POST /api/zeri/select 落库 → 跳 zeri_plan
// 免费档每日换一批 3 次（429 时 toast 后端文案）；会员/体验模式不限
const api = require('../../utils/api');
const theme = require('../../utils/theme');

/* 6 场景（与后端 src/engines/zeri.py SCENES 的 key 一致） */
const SCENES = [
  { key: '嫁娶', seal: '嫁', sub: '婚礼领证 · 设宴' },
  { key: '搬家', seal: '迁', sub: '入宅安顿 · 乔迁' },
  { key: '开业', seal: '开', sub: '开张揭牌 · 纳客' },
  { key: '出行', seal: '行', sub: '远行出发 · 启程' },
  { key: '提车', seal: '车', sub: '新车到家 · 上路' },
  { key: '签约', seal: '签', sub: '合同落定 · 用印' },
];

/* 办事清单模板（与后端 src/engines/zeri_checklist.py CHECKLIST_TEMPLATES 一致；
   core=true 为免费档精简项；会员全量。选日落库时随 select 一并提交） */
const CHECKLIST_TEMPLATES = {
  '嫁娶': [
    { stage: '提前3天', text: '发请柬并统计宾客名单', core: true },
    { stage: '提前3天', text: '与酒店核对桌数菜单', core: true },
    { stage: '提前3天', text: '婚纱礼服最终试穿', core: false },
    { stage: '提前1天', text: '婚车路线踩点装饰', core: false },
    { stage: '提前1天', text: '彩排走场对词', core: true },
    { stage: '提前1天', text: '确认化妆师到位时间', core: false },
    { stage: '提前1天', text: '新房布置红包喜字', core: false },
    { stage: '当天', text: '吉时9-11点 婚车出发接亲', core: true },
    { stage: '当天', text: '敬茶改口,午前完成', core: false },
    { stage: '当天', text: '宴席开席,敬酒答谢', core: true },
  ],
  '搬家': [
    { stage: '提前3天', text: '联系搬家公司确认车型费用', core: true },
    { stage: '提前3天', text: '确认新旧小区停车与电梯时段', core: false },
    { stage: '提前3天', text: '打包分类,贵重物品随身', core: false },
    { stage: '提前1天', text: '水电气暖预约过户更名', core: true },
    { stage: '提前1天', text: '宽带迁移预约', core: false },
    { stage: '提前1天', text: '保洁提前一天新宅除尘', core: false },
    { stage: '当天', text: '7点前厨房米面入宅,米缸进财', core: true },
    { stage: '当天', text: '吉时9-11点 主家具先行入宅', core: true },
    { stage: '当天', text: '长者殿后进屋,进门开灯', core: false },
    { stage: '当天', text: '物业登记,门锁换新,清点物品', core: true },
  ],
  '开业': [
    { stage: '提前3天', text: '营业执照确认并张贴', core: true },
    { stage: '提前3天', text: '备货理货,陈列调整', core: true },
    { stage: '提前3天', text: '促销物料设计印制', core: false },
    { stage: '提前1天', text: '银行对公账户确认开通', core: true },
    { stage: '提前1天', text: '设备调试收银系统测试', core: true },
    { stage: '提前1天', text: '店面卫生清洁招牌点亮', core: false },
    { stage: '当天', text: '吉时9-11点 揭牌开业', core: true },
    { stage: '当天', text: '开业优惠,首客迎宾', core: false },
    { stage: '当天', text: '财神位摆供,开市鸣炮', core: false },
  ],
  '出行': [
    { stage: '提前3天', text: '确认行程,预订机酒门票', core: true },
    { stage: '提前3天', text: '检查证件签证是否有效', core: true },
    { stage: '提前3天', text: '换汇并告知家人行程', core: false },
    { stage: '提前1天', text: '行李打包,充电宝雨具备齐', core: true },
    { stage: '提前1天', text: '值机选座,预约接送车辆', core: false },
    { stage: '提前1天', text: '查目的地天气调整衣物', core: false },
    { stage: '当天', text: '吉时7-9点 出门启程', core: true },
    { stage: '当天', text: '提前2小时到机场车站', core: true },
    { stage: '当天', text: '出发前关水电煤,锁好门窗', core: false },
  ],
  '提车': [
    { stage: '提前3天', text: '联系4S店确认交车时间', core: true },
    { stage: '提前3天', text: '确认保险方案与生效日期', core: true },
    { stage: '提前3天', text: '准备身份证驾驶证购车合同', core: false },
    { stage: '提前1天', text: '车管所预约上牌时间', core: true },
    { stage: '提前1天', text: '查临牌办理所需材料', core: false },
    { stage: '提前1天', text: '验车清单打印备用', core: false },
    { stage: '当天', text: '吉时9-11点 到店提车', core: true },
    { stage: '当天', text: '验车核对车架号里程', core: false },
    { stage: '当天', text: '上牌贴膜,检查随车证件', core: true },
  ],
  '签约': [
    { stage: '提前3天', text: '合同文本逐条审阅', core: true },
    { stage: '提前3天', text: '咨询律师或专业意见', core: false },
    { stage: '提前3天', text: '核对双方资质证照', core: true },
    { stage: '提前1天', text: '备齐身份证营业执照公章', core: true },
    { stage: '提前1天', text: '确认款项支付方式与账户', core: true },
    { stage: '提前1天', text: '打印合同一式多份', core: false },
    { stage: '当天', text: '吉时9-11点 签约用印', core: true },
    { stage: '当天', text: '核对盖章签字与日期', core: false },
    { stage: '当天', text: '留存合同原件与附件', core: false },
  ],
};

const DEFAULT_WINDOW_DAYS = 30; // 默认窗口：今天起 30 天（与对话侧一致）
const MAX_EXCLUDE_ROUNDS = 5;  // 换一批跨轮去重上限：最多累积最近 5 轮已展示日期（终审 M2）

/* 日期工具 */
function pad(n) { return String(n).padStart(2, '0'); }
function iso(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
function windowDates(days) {
  const start = new Date();
  const end = new Date();
  end.setDate(end.getDate() + (days || DEFAULT_WINDOW_DAYS));
  return { start: iso(start), end: iso(end) };
}

/* 公历日期 → 展示（9月5日 · 2026） */
function cnDate(isoStr) {
  if (!isoStr) return '';
  const p = String(isoStr).split('-');
  return `${Number(p[1])}月${Number(p[2])}日`;
}

/* 选它落库的清单项（免费档 core 精简；会员全量） */
function buildItems(scene, isMember) {
  const tpl = CHECKLIST_TEMPLATES[scene] || [];
  return tpl
    .filter((it) => isMember || it.core)
    .map((it) => ({ stage: it.stage, text: it.text }));
}

Page({
  data: {
    dark: false,
    scene: '',            // 当前场景（空 = 未选，显示场景引导）
    scenes: SCENES,
    loading: false,       // 首次取卡中
    refreshing: false,    // 换一批中
    cards: [],            // 吉日卡（含展示派生字段）
    suggestReason: '',
    refreshRemaining: null, // 今日剩余换一批次数（免费档）
    isMember: false,
    memberReady: false,   // 会员判定加载完成（未完成前禁用「选它」，避免真实会员拿到免费档清单）
    expandedIdx: -1,      // 展开评分明细的卡下标（会员）
    selectingIdx: -1,     // 正在落库的卡下标
    excludeRounds: [],    // 换一批跨轮去重（终审 M2）：每轮已展示日期数组，上限最近 5 轮
  },

  onLoad(options) {
    options = options || {};
    theme.bindTheme(this);
    this._loadMember();
    const scene = decodeURIComponent((options.scene || '').trim());
    if (scene) {
      this.setData({ scene });
      this._loadCards(scene, [], true);
    }
    // 无 scene → 显示场景引导（第 6 项：从对话深链进入时无场景参数）
  },

  /* 会员判定（评分明细展开门槛；失败按非会员处理） */
  _loadMember() {
    api.getMemberInfo()
      .then((info) => {
        this.setData({ isMember: !!(info && info.isMember), memberReady: true });
      })
      .catch(() => { this.setData({ memberReady: true }); /* 静默按非会员 */ });
  },

  /* 取卡：initial=true 走 GET options（初始加载，不扣换一批额度）；
     否则走 POST refresh（换一批，扣额度）。exclude 已展示日期跨轮去重（终审 M2：
     每轮把已展示日期累积进 excludeRounds，隔轮日期不再重复；上限最近 5 轮）。 */
  _loadCards(scene, excludeDates, initial) {
    if (this.data.loading || this.data.refreshing) return;
    this.setData(scene === this.data.scene && this.data.cards.length ? { refreshing: true } : { loading: true });
    const w = windowDates(DEFAULT_WINDOW_DAYS);
    const params = { scene, start: w.start, end: w.end, exclude_dates: excludeDates || [] };
    const req = initial ? api.getZeriOptions(params) : api.refreshZeri(params);
    req.then((res) => {
      const cards = (res && res.cards) || [];
      this._recordRound(cards.map((c) => c.dateISO).filter(Boolean));
      this.setData({
        loading: false,
        refreshing: false,
        scene,
        cards: cards.map((c) => this._renderCard(c)),
        suggestReason: (res && res.reason) || '',
        refreshRemaining: typeof res.refresh_remaining === 'number' ? res.refresh_remaining : null,
        expandedIdx: -1,
      });
    }).catch((err) => {
      this.setData({ loading: false, refreshing: false });
      const detail = (err && err.detail) || (err && err.message) || '';
      if (detail.indexOf('429') !== -1 || detail.indexOf('次数') !== -1) {
        wx.showToast({ title: detail || '今天的换一批次数已用完', icon: 'none', duration: 2500 });
      } else {
        wx.showToast({ title: detail || '取卡失败，请重试', icon: 'none', duration: 2500 });
      }
    });
  },

  /* 换一批跨轮去重（终审 M2）：每轮成功展示的日期追加进 excludeRounds，
     只保留最近 MAX_EXCLUDE_ROUNDS 轮；refresh 请求时扁平化为累积 exclude 列表。 */
  _recordRound(dates) {
    const rounds = (this.data.excludeRounds || []).concat([dates || []]);
    this.setData({ excludeRounds: rounds.slice(-MAX_EXCLUDE_ROUNDS) });
  },

  /* 原始卡 → 展示字段 */
  _renderCard(c) {
    const yi = Array.isArray(c.yi) ? c.yi : [];
    const ji = Array.isArray(c.ji) ? c.ji : [];
    return {
      raw: c,
      dateText: cnDate(c.date),
      dateISO: c.date,
      lunarText: c.lunar_text || '',
      yi,
      ji,
      jishi: c.jishi || '吉时以当日黄历为准',
      xi: c.xi_fangwei || '—',
      cai: c.cai_fangwei || '—',
      reason: c.reason_source || '明灯拟',
      total: c.total || 0,
      scores: [
        { label: '场景匹配', score: c.scene_score || 0, max: 50 },
        { label: '个人适配', score: c.personal_score || 0, max: 30 },
        { label: '实用加分', score: c.practical_score || 0, max: 20 },
      ],
    };
  },

  /* ═══ 场景引导（无 scene 进入） ═══ */
  onPickScene(e) {
    const key = e.currentTarget.dataset.key;
    if (!key) return;
    this.setData({ scene: key, excludeRounds: [] });   // 切场景重置跨轮去重（M2）
    this._loadCards(key, [], true);
  },

  /* ═══ 换一批（免费档每日 3 次；429 → toast 后端文案） ═══ */
  onRefresh() {
    const { scene } = this.data;
    if (!scene) {
      wx.showToast({ title: '请先选择场景', icon: 'none' });
      return;
    }
    if (this.data.refreshing || this.data.loading) return;
    // 累积最近 N 轮已展示日期（含当前屏），隔轮不再重复（终审 M2）
    const exclude = [];
    (this.data.excludeRounds || []).forEach((round) => {
      (round || []).forEach((d) => { if (exclude.indexOf(d) === -1) exclude.push(d); });
    });
    this._loadCards(scene, exclude, false);
  },

  /* ═══ 理由一行 → 展开评分明细（会员）；非会员提示 ═══ */
  onReasonTap(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    if (!this.data.isMember) {
      wx.showToast({ title: '评分明细 · 会员专享', icon: 'none' });
      return;
    }
    this.setData({ expandedIdx: this.data.expandedIdx === idx ? -1 : idx });
  },

  /* ═══ 选它 → 落库 → 跳清单页 ═══ */
  onSelect(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const card = this.data.cards[idx];
    if (!card || this.data.selectingIdx >= 0) return;
    if (!this.data.memberReady) {
      wx.showToast({ title: '会员识别中，请稍候', icon: 'none' });
      return;
    }
    const scene = this.data.scene;
    const items = buildItems(scene, this.data.isMember);
    this.setData({ selectingIdx: idx });
    api.selectZeri({
      scene,
      lucky_date: card.dateISO,
      card: card.raw,
      items,
      plan_type: this.data.isMember ? 'member' : 'free',
    }).then((res) => {
      this.setData({ selectingIdx: -1 });
      const pid = res && res.plan_id;
      if (!pid) {
        wx.showToast({ title: '落库失败，请重试', icon: 'none' });
        return;
      }
      wx.navigateTo({ url: `/pages/zeri_plan/zeri_plan?plan_id=${pid}` });
    }).catch((err) => {
      this.setData({ selectingIdx: -1 });
      const detail = (err && err.detail) || '落库失败，请重试';
      wx.showToast({ title: detail, icon: 'none', duration: 2500 });
    });
  },

});
