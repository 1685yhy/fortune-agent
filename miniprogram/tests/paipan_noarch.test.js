// 易理明灯 batch2 Q3 — 未建档点「排盘」→ 建档表单页（paipan 未建档引导条）node 单测
// 运行：cd miniprogram && node --test tests/paipan_noarch.test.js
// 覆盖（Q3 拍板）：
//   1. 纯判定 guide.shouldShowPaipanNoarchGuide（无档案 → 显示引导条；有档案 → 不显示）
//   2. paipan.wxml 未建档引导条文案口径：与 E1 提示条同款「还没建档？排一次盘，
//      会生成专属分析」；沿用 P4「会生成分析」口径，不得承诺「保存到档案/更准」
//   3. 引导条展示条件：wx:if 绑定 noArchive；paipan.js data 默认 noArchive=false，
//      onLoad/onShow 走 _refreshNoarchive → persons.hasLocalArchive() 判定
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const guide = require('../utils/guide');

const PAIPAN_WXML = path.join(__dirname, '../pages/paipan/paipan.wxml');
const PAIPAN_JS = path.join(__dirname, '../pages/paipan/paipan.js');

/* ── 1. 纯判定逻辑（guide.js） ── */

test('Q3 引导条：本地无档案 → 显示（未建档点排盘落地建档表单页）', () => {
  assert.equal(guide.shouldShowPaipanNoarchGuide(false), true);
});

test('Q3 引导条：已有档案 → 不显示', () => {
  assert.equal(guide.shouldShowPaipanNoarchGuide(true), false);
});

/* ── 1b. k34 A32：复用 E1 关闭键（关过不再现） ── */

test('k34 A32 引导条：无档案但已关闭 → 不显示（× 关过不再打扰）', () => {
  assert.equal(guide.shouldShowPaipanNoarchGuide(false, true), false);
  assert.equal(guide.shouldShowPaipanNoarchGuide(true, true), false);
  // 未关闭（closed 缺省/显式 false）→ 保持既有语义：无档案即显示
  assert.equal(guide.shouldShowPaipanNoarchGuide(false, false), true);
  assert.equal(guide.shouldShowPaipanNoarchGuide(false, undefined), true);
});

test('k34 A32 关闭键与今日页 E1 同键（一枚标记两页共用）', () => {
  assert.equal(guide.NOARCH_CLOSED_KEY, 'ylm_noarch_tip_closed');
  const todayJs = fs.readFileSync(path.join(__dirname, '../pages/today/today.js'), 'utf8');
  assert.match(todayJs, /wx\.setStorageSync\(guide\.NOARCH_CLOSED_KEY, 1\)/,
    '今日页 E1 关闭键写入（基线，A32 复用它）');
});

/* ── 2. 引导条文案口径（P4 纪律：只承诺「会生成…分析」，不承诺保存/更准） ── */

test('paipan 未建档引导条标题：与 E1 提示条同款「还没建档？排一次盘，会生成专属分析」', () => {
  const src = fs.readFileSync(PAIPAN_WXML, 'utf8');
  const m = src.match(/<view class="pp-noarch-t">([^<]+)<\/view>/);
  assert.ok(m, 'paipan.wxml 应存在未建档引导条标题行（class="pp-noarch-t"）');
  assert.equal(m[1], '还没建档？排一次盘，会生成专属分析', '标题应与 E1 提示条同款口径（E1 文案基线）');
  assert.match(m[1], /会生成[^<]*分析/, '标题应含「会生成…分析」口径');
  assert.doesNotMatch(m[1], /保存|更准/, '标题不得承诺保存到档案/更准（P4 口径纪律）');
});

test('paipan 未建档引导条副标题：建档后解读更贴合自己 · 含「建档」引导字样', () => {
  const src = fs.readFileSync(PAIPAN_WXML, 'utf8');
  const m = src.match(/<view class="pp-noarch-s">([^<]+)<\/view>/);
  assert.ok(m, 'paipan.wxml 应存在未建档引导条副标题行（class="pp-noarch-s"）');
  assert.match(m[1], /建档/, '副标题应含「建档」字样（未建档用户需知本页即建档入口）');
  assert.doesNotMatch(m[1], /保存|更准/, '副标题不得承诺保存到档案/更准');
});

/* ── 3. 展示条件与接线（paipan 页） ── */

test('paipan 引导条由 wx:if="{{noArchive}}" 控制（仅未建档时展示）', () => {
  const src = fs.readFileSync(PAIPAN_WXML, 'utf8');
  const m = src.match(/<view class="pp-noarch" wx:if="\{\{noArchive\}\}">/);
  assert.ok(m, '引导条必须绑定 wx:if="{{noArchive}}"');
});

test('paipan.js：noArchive 默认 false，onLoad/onShow 经 _refreshNoarchive 用 hasLocalArchive 判定', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /noArchive: false/, 'data 默认 noArchive: false（有档案不显示）');
  assert.match(js, /noarchClosed: false/, 'k34 A32：data 默认 noarchClosed: false');
  assert.match(js, /onShow\(\)\s*\{\s*this\._refreshNoarchive\(\);/, 'onShow 应调用 _refreshNoarchive');
  assert.match(js, /setData\(\{ noarchClosed: closed \}, \(\) => this\._refreshNoarchive\(\)\)/,
    'onLoad 应在读关闭标记后刷新');
  assert.match(js, /shouldShowPaipanNoarchGuide\(\s*persons\.hasLocalArchive\(\), this\.data\.noarchClosed\)/,
    '判定应走 guide.shouldShowPaipanNoarchGuide(persons.hasLocalArchive(), this.data.noarchClosed)');
  assert.match(js, /require\('\.\.\/\.\.\/utils\/persons'\)/, 'paipan.js 应引入 persons（档案判定）');
  assert.match(js, /require\('\.\.\/\.\.\/utils\/guide'\)/, 'paipan.js 应引入 guide（纯判定）');
});

test('k34 A32：onLoad 读 E1 关闭标记 + × 关闭写标记', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /wx\.getStorageSync\(guide\.NOARCH_CLOSED_KEY\)/, 'onLoad 应读关闭标记');
  assert.match(js, /onCloseNoarchGuide[\s\S]{0,220}wx\.setStorageSync\(guide\.NOARCH_CLOSED_KEY, 1\)/,
    '× 关闭应写 E1 关闭键');
  assert.match(js, /setData\(\{ noarchClosed: true, noArchive: false \}\)/, '关闭即隐藏引导条');
});

test('k34 A32 paipan.wxml：× 关闭钮接线（catchtap 不冒泡）', () => {
  const wxml = fs.readFileSync(PAIPAN_WXML, 'utf8');
  assert.match(wxml, /class="pp-noarch-close"[^>]*catchtap="onCloseNoarchGuide"/,
    '引导条关闭钮必须 catchtap="onCloseNoarchGuide"（不冒泡到卡片）');
  assert.match(wxml, /pp-noarch-close-active/, '关闭钮应有按压高亮态（同今日页）');
});

/* ── 1c. k34 A32 运行时（非源码正则）：今日页 × 关闭 → 排盘页不显示 ── */

const persons = require('../utils/persons');
const savedPage = global.Page;
function loadPage(rel) {
  let cfg = null;
  global.Page = (c) => { cfg = c; };
  try { require(rel); } finally { global.Page = savedPage; }
  assert.ok(cfg, `${rel} 页面配置应可加载`);
  return cfg;
}
const todayCfg = loadPage('../pages/today/today');
const paipanCfg = loadPage('../pages/paipan/paipan');

function makePage(cfg) {
  const page = Object.assign({}, cfg);
  page.data = JSON.parse(JSON.stringify(cfg.data));
  page.setData = function (upd, cb) {
    Object.assign(this.data, upd);
    if (typeof cb === 'function') cb();
  };
  return page;
}

test('k34 A32 运行时：今日页 × 关闭 → 排盘页引导条不显示（字面复用 ylm_noarch_tip_closed）', () => {
  const store = {};
  global.getApp = () => ({ globalData: {} });
  global.wx = {
    getStorageSync: (k) => (store[k] !== undefined ? store[k] : null),
    setStorageSync: (k, v) => { store[k] = v; },
    removeStorageSync: (k) => { delete store[k]; },
    getSystemInfoSync: () => ({ statusBarHeight: 20 }),
    getWindowInfo: () => ({ statusBarHeight: 20 }),
    showToast: () => {},
    navigateTo: () => {},
    reLaunch: () => {},
  };
  const savedLoad = persons.loadPersons;
  persons.loadPersons = () => Promise.resolve([]);   // 无档案：hasLocalArchive() 走真实实现
  try {
    // ① 基线：无档案 + 未关闭 → 排盘页引导条可见
    const p1 = makePage(paipanCfg);
    p1.onLoad();
    assert.equal(p1.data.noArchive, true, '无档案且未关过 → 引导条可见（基线）');
    // ② 今日页点 × 关闭（真实 handler，非源码正则）→ 落同键
    const today = makePage(todayCfg);
    today.onCloseNoarchTip();
    assert.equal(store.ylm_noarch_tip_closed, 1, '今日页关闭落 ylm_noarch_tip_closed=1（A 方案字面复用）');
    assert.equal(today.data.noarchHint, false, '今日页自身隐藏');
    // ③ 同一 storage 进排盘页 → 引导条不显示（跨页联动运行时生效）
    const p2 = makePage(paipanCfg);
    p2.onLoad();
    assert.equal(p2.data.noarchClosed, true, '排盘页读回关闭标记');
    assert.equal(p2.data.noArchive, false, '★ 今日页关过 → 排盘页不再显示引导条');
  } finally {
    persons.loadPersons = savedLoad;
  }
});

/* ── 4. 全链路护栏：「未建档点排盘 → 建档表单页」 ── */

test('全链路：E1 提示条跳转目标 = /pages/paipan/paipan（建档表单页），与引导条同页闭环', () => {
  const todayJs = fs.readFileSync(path.join(__dirname, '../pages/today/today.js'), 'utf8');
  const m = todayJs.match(/wx\.navigateTo\(\{ url: '(\/pages\/paipan\/paipan)'/);
  assert.ok(m, 'today.js onGoNoarchTip 应 navigateTo /pages/paipan/paipan（既有行为，Q3 不改跳转）');
  // 同页闭环：paipan.wxml 该输入区同时承载未建档引导条（本页即建档表单）
  const paipanWxml = fs.readFileSync(PAIPAN_WXML, 'utf8');
  assert.match(paipanWxml, /<view class="pp-noarch" wx:if="\{\{noArchive\}\}">/, '落地页应含未建档引导条');
});

test('全链路：onboarding 导览卡「去排盘」与测算页八字排盘卡均指向 paipan 建档表单页', () => {
  const obJs = fs.readFileSync(path.join(__dirname, '../pages/onboarding/onboarding.js'), 'utf8');
  assert.match(obJs, /url: '\/pages\/paipan\/paipan'/, 'onboarding 导览卡 1 应指向 /pages/paipan/paipan');
  const celiangWxml = fs.readFileSync(path.join(__dirname, '../pages/celiang/celiang.wxml'), 'utf8');
  assert.match(celiangWxml, /data-url="\/pages\/paipan\/paipan"/, '测算页八字排盘卡应指向 /pages/paipan/paipan');
});

/* ── 5. 回归护栏：E1 提示条文案未被本任务改动 ── */

test('回归：E1 未建档提示条文案（today.wxml）保持 P4 拍板口径不变', () => {
  const src = fs.readFileSync(path.join(__dirname, '../pages/today/today.wxml'), 'utf8');
  const m = src.match(/<view class="noarch-tip-t">([^<]+)<\/view>/);
  assert.ok(m, 'today.wxml 应存在 E1 提示条标题行');
  assert.equal(m[1], '还没建档？排一次盘，会生成专属分析', 'E1 提示条文案不得被本任务改动（P4 拍板口径）');
});
