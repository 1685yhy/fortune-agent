// 测算 — 问事入口台（dir_b 屏 3 · SUANCE_MAIN 分组克制排布：姻缘·命名·择日 / 问事 / 运势）
// 只接已开发功能：双人合盘 → /pages/hehun/hehun、大事择吉日 → /pages/zeri_guide/zeri_guide、
// 抽灵签 → /pages/qian/qian（问事组 = 解梦 + 抽灵签，塔罗已移除）；
// 未开发功能（AI取名/解梦/人生时轴/运势曲线/水逆提醒）灰态占位「即将上线」，不可点（宁缺毋滥，不造假入口）。
const theme = require('../../utils/theme');

Page({
  data: {
    navOff: 0,
    dark: false,
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
  },

  /* 状态栏高度适配：原型画板固定状态栏 47px，--nav-off 为差值 */
  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 实入口跳转（占位卡无 data-url，tap 不触发跳转） */
  onEntryTap(e) {
    const url = e.currentTarget.dataset.url;
    if (!url) return;
    wx.navigateTo({ url });
  },

  /* 原型 TabBar onTab */
  onTab(e) {
    const t = e.currentTarget.dataset.tab;
    const url = { chat: '/pages/chat/chat', today: '/pages/today/today', suance: '/pages/celiang/celiang', me: '/pages/me/me' }[t];
    if (url && !url.includes('/celiang/')) wx.reLaunch({ url });
  },
});
