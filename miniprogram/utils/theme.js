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

/**
 * 给页面绑定主题状态：data.dark = true/false，主题切换时自动更新
 * @param {Page} page - 页面实例（需有 setData）
 * @param {Function} [onChange] - 主题变化回调（可选）
 */
function bindTheme(page, onChange) {
  const apply = () => {
    const dark = getTheme() === 'dark';
    if (page.data.dark !== dark) {
      page.setData({ dark });
    }
    if (onChange) onChange(dark);
  };
  apply();
  try {
    if (wx.onThemeChange) wx.onThemeChange(apply);
  } catch (e) {
    // 低版本基础库无 onThemeChange，保持初始主题
  }
}

module.exports = { getTheme, bindTheme };
