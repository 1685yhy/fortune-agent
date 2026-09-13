// 易理明灯 — 主题工具（暗黑模式支持）
// 微信官方机制：app.json darkmode:true + theme.json（window 配置变量化）
// 业务样式在 app.wxss 以 @media (prefers-color-scheme: dark) 切换 CSS 变量；
// 本模块负责 JS 侧：图片资源（灯笼/图标）按主题切换 src。

/**
 * 当前主题：'light' | 'dark'（未开启 darkmode 时 theme 为 undefined）
 * k47-B：取新 API wx.getAppBaseInfo()（getSystemInfoSync 已废弃；基础库 ≥2.20.1 提供）
 */
function getTheme() {
  try {
    const info = (wx.getAppBaseInfo && wx.getAppBaseInfo()) || {};
    return info.theme === 'dark' ? 'dark' : 'light';
  } catch (e) {
    return 'light';
  }
}

/* ── k47-E 主题监听单例（修复「listeners 累积」告警） ──
   旧实现 `bindTheme()` 每页注册一个 wx.onThemeChange(apply) 且**从不解绑** →
   一季会话访问 20+ 页即累积 20+ 监听器，开发者工具报
   「[Event] 21 listeners of event ThemeChange have been added, possibly causing memory leak.」
   现在：全局只注册一次 wx.onThemeChange（单例），按注册表分发给各页面；
   页面 onUnload 自动解绑（包装实例 onUnload，无需各页改代码）。 */
const bound = [];          // [{ page, onChange }]
let listening = false;

function _dispatch() {
  const dark = getTheme() === 'dark';
  bound.slice().forEach((b) => {
    try {
      if (b.page && b.page.data && b.page.data.dark !== dark) b.page.setData({ dark });
      if (b.onChange) b.onChange(dark);
    } catch (e) { /* 页面已销毁等：忽略本次分发 */ }
  });
  // 兼容既有语义：app.globalData.theme 跟随（app.js 不再自行注册监听器）
  try {
    const app = getApp && getApp();
    if (app && app.globalData) app.globalData.theme = dark ? 'dark' : 'light';
  } catch (e) { /* ignore */ }
}

function _ensureListening() {
  if (listening) return;
  try {
    if (!wx.onThemeChange) return;   // 低版本基础库：保持初始主题
    wx.onThemeChange(_dispatch);     // 全局唯一监听器
    listening = true;
  } catch (e) { /* ignore */ }
}

function unbindTheme(page) {
  for (let i = bound.length - 1; i >= 0; i--) {
    if (bound[i].page === page) bound.splice(i, 1);
  }
}

/**
 * 给页面绑定主题状态：data.dark = true/false，主题切换时自动更新。
 * 同页重复绑定只保留一份；页面 onUnload 自动解绑（见上）。
 * @param {Page} page - 页面实例（需有 setData）
 * @param {Function} [onChange] - 主题变化回调（可选）
 */
function bindTheme(page, onChange) {
  const dark = getTheme() === 'dark';
  if (page.data.dark !== dark) {
    page.setData({ dark });
  }
  if (onChange) onChange(dark);
  if (bound.some((b) => b.page === page)) return;   // 幂等：同页不重复登记
  bound.push({ page, onChange });
  _ensureListening();
  // 卸载解绑：页面未定义 onUnload 时补一个（各页无需改代码）；已定义则包装保留原行为
  const orig = (typeof page.onUnload === 'function') ? page.onUnload : null;
  page.onUnload = function () {
    unbindTheme(page);
    if (orig) return orig.apply(this, arguments);
  };
}

module.exports = { getTheme, bindTheme, unbindTheme };
