// 易理明灯 — k47-E 主题监听单例（修复开发者工具「listeners of event ThemeChange」累积告警）
// 运行：cd miniprogram && node --test tests/k47_theme_singleton.test.js
//
// 背景（两次独立抓取均出现）：
//   [Event] 21 listeners of event ThemeChange have been added, possibly causing memory leak.
//   旧 utils/theme.js bindTheme() 每页注册一个 wx.onThemeChange(apply) 且从不解绑，
//   app.js detectTheme() 另注册一个 → 会话访问 20+ 页即累积 20+ 监听器。
// 修后：全局唯一监听器（单例）+ 页面 onUnload 自动解绑；app.js 不再自行注册。
const test = require('node:test');
const assert = require('node:assert/strict');

function loadTheme(opts = {}) {
  const listeners = [];
  const app = { globalData: { theme: 'light' } };
  const state = { theme: opts.theme || 'light' };   // 平台侧主题状态（主题变化时 getAppBaseInfo 同步变化）
  global.getApp = () => app;
  global.wx = {
    getAppBaseInfo: opts.getAppBaseInfo || (() => ({ theme: state.theme })),
    onThemeChange: opts.noOnThemeChange ? undefined : (cb) => {
      listeners.push((e) => { state.theme = e.theme; cb(e); });   // 模拟平台：事件到达时同步状态
    },
  };
  delete require.cache[require.resolve('../utils/theme')];
  const theme = require('../utils/theme');
  return { theme, listeners, app, state };
}

function makePage(id) {
  return {
    id,
    data: { dark: false },
    sets: [],
    setData(u) { this.sets.push(JSON.parse(JSON.stringify(u))); Object.assign(this.data, u); },
    onUnload() { this.unloadedBy = 'page'; },
  };
}

test('k47-E：N 个页面绑定后 wx.onThemeChange 只注册 1 次（不再逐页累积）', () => {
  const { theme, listeners } = loadTheme();
  const pages = [];
  for (let i = 0; i < 25; i++) { const p = makePage(i); pages.push(p); theme.bindTheme(p); }
  assert.equal(listeners.length, 1, '旧实现 25 个监听器（触发 21 listeners 告警）');
  assert.equal(pages.every((p) => p.data.dark === false), true, '初始主题已应用');
});

test('k47-E：同页重复绑定幂等（不重复登记、不新增监听器）', () => {
  const { theme, listeners } = loadTheme({ theme: 'dark' });
  const p = makePage('a');
  theme.bindTheme(p);
  theme.bindTheme(p);
  theme.bindTheme(p);
  assert.equal(listeners.length, 1);
  p.sets.length = 0;
  listeners[0]({ theme: 'light' });
  assert.equal(p.sets.length, 1, '一次主题变化只 setData 一次');
  assert.equal(p.data.dark, false);
});

test('k47-E：主题变化分发给所有已绑定页面 + 同步 app.globalData.theme', () => {
  const { theme, listeners, app } = loadTheme();
  const a = makePage('a'); const b = makePage('b');
  theme.bindTheme(a); theme.bindTheme(b);
  listeners[0]({ theme: 'dark' });
  assert.equal(a.data.dark, true);
  assert.equal(b.data.dark, true);
  assert.equal(app.globalData.theme, 'dark');
  listeners[0]({ theme: 'light' });
  assert.equal(a.data.dark, false);
  assert.equal(app.globalData.theme, 'light');
});

test('k47-E：页面 onUnload 自动解绑（页面销毁后不再接收分发，监听器数不随访问累积）', () => {
  const { theme, listeners } = loadTheme();
  const keep = makePage('keep'); const gone = makePage('gone');
  theme.bindTheme(keep); theme.bindTheme(gone);
  assert.equal(listeners.length, 1);
  gone.onUnload();                    // 页面卸载（包装后的实例 onUnload）
  assert.equal(gone.unloadedBy, 'page', '原 onUnload 仍被调用（包装不丢行为）');
  gone.sets.length = 0;
  listeners[0]({ theme: 'dark' });
  assert.equal(gone.sets.length, 0, '已卸载页面不再 setData');
  assert.equal(keep.data.dark, true);
  assert.equal(listeners.length, 1, '监听器始终只有 1 个');
});

test('k47-E：页面未定义 onUnload 时自动补挂解绑（大多数页无 onUnload）', () => {
  const { theme, listeners } = loadTheme();
  const p = { data: { dark: false }, sets: [], setData(u) { this.sets.push(u); Object.assign(this.data, u); } };
  theme.bindTheme(p);
  assert.equal(typeof p.onUnload, 'function', '应自动补挂 onUnload');
  p.onUnload();
  p.sets.length = 0;
  listeners[0]({ theme: 'dark' });
  assert.equal(p.sets.length, 0, '卸载后不再接收分发');
});

test('k47-E：onChange 回调保留；低版本基础库（无 onThemeChange）不抛错', () => {
  const { theme, listeners } = loadTheme();
  const seen = [];
  const p = makePage('a');
  theme.bindTheme(p, (dark) => seen.push(dark));
  assert.deepEqual(seen, [false], '绑定时回调一次');
  listeners[0]({ theme: 'dark' });
  assert.deepEqual(seen, [false, true]);
  // 无 onThemeChange：不抛错、页面仍拿到初始主题
  const env2 = loadTheme({ noOnThemeChange: true, theme: 'dark' });
  const p2 = makePage('b');
  env2.theme.bindTheme(p2);
  assert.equal(p2.data.dark, true);
});

test('k47-E：getTheme 走新 API（getAppBaseInfo），异常/缺失回退 light', () => {
  assert.equal(loadTheme({ theme: 'dark' }).theme.getTheme(), 'dark');
  assert.equal(loadTheme({ theme: 'light' }).theme.getTheme(), 'light');
  assert.equal(loadTheme({ getAppBaseInfo: () => { throw new Error('boom'); } }).theme.getTheme(), 'light');
  assert.equal(loadTheme({ getAppBaseInfo: undefined }).theme.getTheme(), 'light');
});
