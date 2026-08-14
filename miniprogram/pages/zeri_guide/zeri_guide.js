// 择吉日 · 表单式三步入口（dir_b v7 原型）：选大事 · 定时段 · 可加八字 → 吉日卡页
// 传参：navigateTo /pages/zeri/zeri?scene=..&period=..&bazi=..（bazi 选填）
const api = require('../../utils/api');
const theme = require('../../utils/theme');

/* 6 场景（与后端 src/engines/zeri.py SCENES 的 key 一致；seal 为印章单字；
   sub 对齐 dir_b v7 原型：择吉 · 订盟 · 行礼 等） */
const SCENES = [
  { key: '嫁娶', seal: '嫁', sub: '择吉 · 订盟 · 行礼' },
  { key: '搬家', seal: '迁', sub: '入宅 · 移徙 · 安床' },
  { key: '开业', seal: '开', sub: '开市 · 纳财 · 剪彩' },
  { key: '出行', seal: '行', sub: '启程 · 会友 · 求财' },
  { key: '提车', seal: '车', sub: '祈福 · 出行 · 安机械' },
  { key: '签约', seal: '签', sub: '交易 · 订盟 · 纳财' },
];

/* 时间段（原型 Step 2；与后端 zeri.py PERIODS 一致；默认下个月） */
const PERIODS = ['本周', '本月', '下个月', '三个月内'];
const DEFAULT_PERIOD = '下个月';

Page({
  data: {
    dark: false,
    scenes: SCENES,
    periods: PERIODS,
    picked: '',          // 已选场景（第 1 步 · 事）
    period: DEFAULT_PERIOD,  // 时间段（第 2 步 · 时）
    bazi: '',            // 八字选填（第 3 步 · 命）
  },

  onLoad() {
    theme.bindTheme(this);
  },

  /* 第 1 步 · 选场景（再点取消） */
  onPickScene(e) {
    const key = e.currentTarget.dataset.key;
    if (!key) return;
    this.setData({ picked: this.data.picked === key ? '' : key });
  },

  /* 第 2 步 · 选时间段 */
  onPickPeriod(e) {
    const key = e.currentTarget.dataset.key;
    if (!key || PERIODS.indexOf(key) === -1) return;
    this.setData({ period: key });
  },

  /* 第 3 步 · 八字选填 */
  onBaziInput(e) {
    this.setData({ bazi: e.detail.value });
  },

  /* CTA：开始择吉 · {period} → 吉日卡页（携 scene/period/bazi） */
  goZeri() {
    const scene = this.data.picked;
    if (!scene) {
      wx.showToast({ title: '请先选一件大事', icon: 'none' });
      return;
    }
    const q = [
      'scene=' + encodeURIComponent(scene),
      'period=' + encodeURIComponent(this.data.period),
    ];
    const bazi = String(this.data.bazi || '').trim();
    if (bazi) q.push('bazi=' + encodeURIComponent(bazi));
    wx.navigateTo({ url: '/pages/zeri/zeri?' + q.join('&') });
  },

});
