// 易理明灯 P3 — 功能导览 seen-once（批次 2 P3）：onboarding 页主按钮不再每次尾链导览
// 运行：node --test miniprogram/tests/tour_seen.test.js
// 覆盖（拍板流程：首次进入弹 → 看完 → 再次进入不弹 → 入口重看可弹）：
//   首访未看过 → 主按钮进导览；看过（done/skipped 任一）→ 主按钮直接进入不再弹；
//   显式「再看一遍导览」replayTour 无视 seen 标记强制进入；完成/跳过仍写原二键
//   （不新增键）；_finish/doSkip 按当前标记如实置 tourSeen。
const test = require('node:test');
const assert = require('node:assert/strict');
const guide = require('../utils/guide');

// ---- wx 桩：内存 storage + 导航记录（fail 不触发，成功路径留痕） ----
let store = {};
const navCalls = [];
function setStore() { store = {}; navCalls.length = 0; }

global.wx = {
  getStorageSync: (k) => store[k],
  setStorageSync: (k, v) => { store[k] = v; },
  navigateBack: () => { navCalls.push('navigateBack'); },
  reLaunch: () => { navCalls.push('reLaunch'); },
  switchTab: () => { navCalls.push('switchTab'); },
  navigateTo: () => { navCalls.push('navigateTo'); },
  showToast: () => {},
  getWindowInfo: () => ({ statusBarHeight: 47 }),
};

// ---- 加载页面配置（与 history.test.js 同套路） ----
const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/onboarding/onboarding');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg.onDonePrimary === 'function', 'onboarding.js 页面配置应可加载');
assert.ok(typeof pageCfg.replayTour === 'function', 'onboarding.js 应暴露 replayTour（再次查看入口）');
assert.equal(typeof pageCfg.goTour, 'undefined', 'goTour 已并入 onDonePrimary（守卫版主按钮）');

function makePage() {
  const page = Object.assign({}, pageCfg);
  page.data = Object.assign({}, pageCfg.data);
  page.setData = function (patch, cb) {
    Object.assign(this.data, patch);
    if (cb) cb();
  };
  return page;
}

test.beforeEach(setStore);

/* ── 首次进入：未看过 → 主按钮尾链导览（现状保持） ── */
test('首访未看过：done 页主按钮 → 进入导览 phase=tour，不离开引导', () => {
  const p = makePage();
  p.setData({ tourSeen: false });
  p.onDonePrimary();
  assert.equal(p.data.phase, 'tour');
  assert.equal(navCalls.length, 0, '未看过 → 不应直接离开引导');
});

/* ── 看完一次 → 再次进入不弹 ── */
test('看过一次（ylm_tour_done=1）：主按钮 → 直接进入 App，不再弹导览', () => {
  store[guide.TOUR_DONE_KEY] = 1;
  const p = makePage();
  p.setData({ tourSeen: true });
  p.onDonePrimary();
  assert.notEqual(p.data.phase, 'tour', '看过 → 不得进入导览');
  assert.equal(navCalls[0], 'navigateBack', '看过 → 主按钮应离开引导（navigateBack）');
});

test('关闭过一次（ylm_tour_skipped=1）：主按钮 → 直接进入 App，不再弹导览', () => {
  store[guide.TOUR_SKIP_KEY] = 1;
  const p = makePage();
  p.setData({ tourSeen: true });
  p.onDonePrimary();
  assert.notEqual(p.data.phase, 'tour');
  assert.equal(navCalls[0], 'navigateBack');
});

/* ── 再次查看入口可弹 ── */
test('再次查看入口 replayTour：无视 seen 标记，强制进入导览', () => {
  store[guide.TOUR_DONE_KEY] = 1;
  const p = makePage();
  p.replayTour();
  assert.equal(p.data.phase, 'tour', '显式再次查看 → 仍可进入导览');
  assert.equal(p.data.tourIdx, 0, '重看从第 1 卡开始');
});

/* ── 完成/跳过仍写原二键，不新增键 ── */
test('导览完成/跳过：仍写原 ylm_tour_done / ylm_tour_skipped 二键（不新增键）', () => {
  const p = makePage();
  p.onTourDone();
  assert.equal(store[guide.TOUR_DONE_KEY], 1, '看完 3 卡「开始使用」→ ylm_tour_done=1');
  assert.equal(store[guide.TOUR_SKIP_KEY], undefined);
  const p2 = makePage();
  p2.onTourSkip();
  assert.equal(store[guide.TOUR_SKIP_KEY], 1, '「跳过」→ ylm_tour_skipped=1');
  const tourKeys = Object.keys(store).filter((k) => k.startsWith('ylm_tour')).sort();
  assert.deepEqual(tourKeys, ['ylm_tour_done', 'ylm_tour_skipped'], '键集合不扩增');
});

/* ── tourSeen 数据位：随当前标记如实置位（驱动 wxml 按钮文案/重看入口显隐） ── */
test('_finish 建档完成：无导览标记 → tourSeen=false（主按钮显示「完成」尾链导览）', () => {
  const p = makePage();
  p.data.cal = 'solar'; p.data.year = '1990'; p.data.month = '1'; p.data.day = '1';
  p.data.hourIndex = 0; p.data.gender = '女'; p.data.place = '';
  p._finish();
  assert.equal(p.data.phase, 'done');
  assert.equal(p.data.tourSeen, false);
  assert.equal(store['ylm_onboard_done'], 1, 'onboard 判定键不受影响');
});

test('_finish 建档完成：已有导览标记 → tourSeen=true（主按钮「直接进入」，重看入口可见）', () => {
  store[guide.TOUR_DONE_KEY] = 1;
  const p = makePage();
  p.data.cal = 'solar'; p.data.year = '1990'; p.data.month = '1'; p.data.day = '1';
  p.data.hourIndex = 0; p.data.gender = '女'; p.data.place = '';
  p._finish();
  assert.equal(p.data.tourSeen, true);
});

test('doSkip 跳过建档：无导览标记 → tourSeen=false；有标记 → true', () => {
  const p = makePage();
  p.doSkip();
  assert.equal(p.data.phase, 'skipped');
  assert.equal(p.data.tourSeen, false);
  assert.equal(store['ylm_onboard_skipped'], 1);

  setStore();
  store[guide.TOUR_SKIP_KEY] = 1;
  const p2 = makePage();
  p2.doSkip();
  assert.equal(p2.data.tourSeen, true);
});
