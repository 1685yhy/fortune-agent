// 择吉日 · 表单式三步入口（dir_b v7 原型）：选大事 · 定时段 · 可加八字 → 吉日卡页
// 传参：navigateTo /pages/zeri/zeri?scene=..&period=..&bazi=..（bazi 选填）
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const { SCENES } = require('../../utils/zeriMeta'); // 6 场景元数据唯一来源（fix-later 去重）

/* 时间段（原型 Step 2；与后端 zeri.py PERIODS 一致；默认下个月） */
const PERIODS = ['本周', '本月', '下个月', '三个月内'];
const DEFAULT_PERIOD = '下个月';

/* 八字预校验（UX批3）：后端 _parse_bazi_form（ISO 式 / 中文年+月+日式 / 农历语义式）
   所有可解析格式都含 4 位出生年份 → 前端只挡「不含年份」的串，避免误拒语义式；
   不含年份的串后端必然解析失败静默降级（用户以为按命择日实际未用），提前提示。 */
function baziRecognizable(str) {
  return /\d{4}/.test(str) || /[零一二三四五六七八九十\d]+年/.test(str);
}

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
    const bazi = String(this.data.bazi || '').trim();
    if (bazi && !baziRecognizable(bazi)) {
      wx.showToast({
        title: '八字未能识别 · 需含出生年份（如 1995-08-12 14:30），或留空',
        icon: 'none',
        duration: 2500,
      });
      return;
    }
    const q = [
      'scene=' + encodeURIComponent(scene),
      'period=' + encodeURIComponent(this.data.period),
    ];
    if (bazi) q.push('bazi=' + encodeURIComponent(bazi));
    wx.navigateTo({ url: '/pages/zeri/zeri?' + q.join('&') });
  },

});
