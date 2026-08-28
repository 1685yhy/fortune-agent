// 易理明灯 batch2 Q4 — 新手引导 tour 卡 1（排盘引导卡）预填本地档案 node 单测
// 运行：cd miniprogram && node --test tests/onboarding_tourprefill.test.js
// 覆盖（Q4 拍板）：
//   1. 已有本地命主档案（persons 同源，is_default 优先，无默认取首个）
//      → 进入导览时卡 1 附档案摘要「你的档案：{persons.birthSummary 同款}」
//   2. 无档案 / 仅对话归档无 person 对象 → 卡 1 原样（cta「去排盘」不变，引导去建档）
//   3. 自动尾链（onDonePrimary）与显式重看（replayTour）两路进入均预填
//   4. 不变异模块级 TOUR_CARDS（防共享引用污染）
//   5. wxml 接线：tour 卡渲染区含 wx:if="{{item.arch}}" 的档案摘要行 + 「你的档案：」前缀
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const persons = require('../utils/persons');

// ---- wx 桩：内存 storage（person 档案读写走 utils/persons 的 LOCAL_KEY） ----
let store = {};
function setStore() { store = {}; }
function setPersons(list) { store[persons.LOCAL_KEY] = list; }

global.wx = {
  getStorageSync: (k) => store[k],
  setStorageSync: (k, v) => { store[k] = v; },
  navigateBack: () => {},
  reLaunch: () => {},
  switchTab: () => {},
  navigateTo: () => {},
  showToast: () => {},
  getWindowInfo: () => ({ statusBarHeight: 47 }),
};
global.getApp = () => ({ globalData: {} });

// ---- 加载页面配置（与 tour_seen.test.js 同套路） ----
const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/onboarding/onboarding');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg._enterTour === 'function', 'onboarding.js 页面配置应可加载');
assert.ok(typeof pageCfg._tourCardsWithArchive === 'function', 'onboarding.js 应暴露 _tourCardsWithArchive（预填判定）');

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

/* 命主样例（Q4 报告示例同款：1976年8月23日丑时 榆树，女） */
const PERSON_DEFAULT = {
  id: 'p1', name: '我', relation: '自己', gender: 'female',
  birth_year: 1976, birth_month: 8, birth_day: 23,
  birth_hour: 1, birth_minute: 0, calendar: 'solar', city: '榆树',
  is_default: true, created_at: 1,
};
const PERSON_OTHER = {
  id: 'p2', name: '母', relation: '母亲', gender: 'female',
  birth_year: 1950, birth_month: 3, birth_day: 5,
  birth_hour: 7, birth_minute: 0, calendar: 'lunar', city: '沈阳',
  is_default: false, created_at: 2,
};

/* ── 1. 有档案 → 卡 1 预填 ── */

test('有默认命主：进入导览 → 卡 1 附档案摘要（birthSummary 同款文案），其余卡不变', () => {
  setPersons([PERSON_DEFAULT]);
  const p = makePage();
  p._enterTour();
  assert.equal(p.data.phase, 'tour');
  assert.equal(p.data.tourIdx, 0);
  const c0 = p.data.tourCards[0];
  assert.ok(c0.arch, '卡 1 应有档案摘要');
  assert.equal(c0.arch, persons.birthSummary(PERSON_DEFAULT), '摘要应复用 persons.birthSummary 格式');
  assert.match(c0.arch, /1976年8月23日/, '摘要应含出生年月日');
  assert.match(c0.arch, /丑时/, '摘要应含时辰（birth_hour=1 → 丑时）');
  assert.match(c0.arch, /榆树/, '摘要应含出生地');
  assert.equal(c0.cta, '去排盘', '按钮文案保持「去排盘」（paipan 为手动表单，不自动带档，不承诺「用我的档案排盘」）');
  assert.equal(c0.url, '/pages/paipan/paipan', '点击去向不变：paipan 建档表单页');
  assert.equal(p.data.tourCards[1].arch, undefined, '卡 2（今日）不受影响');
  assert.equal(p.data.tourCards[2].arch, undefined, '卡 3（问明灯）不受影响');
});

test('无默认命主：取列表首个命主作摘要来源', () => {
  const noDefault = Object.assign({}, PERSON_DEFAULT, { is_default: false });
  setPersons([PERSON_OTHER, noDefault]); // 均无 is_default → 取首个
  const p = makePage();
  p._enterTour();
  assert.equal(p.data.tourCards[0].arch, persons.birthSummary(PERSON_OTHER), '无默认 → 首个命主摘要');
});

test('显式重看 replayTour：与自动尾链同预填（共用 _enterTour 入口）', () => {
  setPersons([PERSON_DEFAULT]);
  const p = makePage();
  p.replayTour();
  assert.equal(p.data.phase, 'tour');
  assert.equal(p.data.tourCards[0].arch, persons.birthSummary(PERSON_DEFAULT), '重看入口也应预填档案');
});

/* ── 2. 无档案 / 无可展示对象 → 现状不变 ── */

test('无档案：卡 1 无摘要、cta 保持「去排盘」（引导去建档，现状）', () => {
  const p = makePage();
  p._enterTour();
  const c0 = p.data.tourCards[0];
  assert.equal(c0.arch, undefined, '无档案 → 不显示档案摘要');
  assert.equal(c0.cta, '去排盘');
  assert.equal(c0.title, '排一次盘');
});

test('仅对话归档（hasLocalArchive 真但无 person 对象）：卡 1 保持原样（无摘要可展示）', () => {
  store['ylm_chat_archives'] = [{ name: '我' }];
  const p = makePage();
  p._enterTour();
  const c0 = p.data.tourCards[0];
  assert.equal(c0.arch, undefined, '无 person 对象 → 无摘要，卡 1 原样');
  assert.equal(c0.cta, '去排盘');
});

test('未进入导览（welcome 等 phase）：不预填，tourCards 保持初始常量', () => {
  setPersons([PERSON_DEFAULT]);
  const p = makePage();
  assert.equal(p.data.phase, 'welcome');
  assert.equal(p.data.tourCards[0].arch, undefined, '未进导览不预填');
});

/* ── 3. 防共享引用污染 ── */

test('预填不变异模块级 TOUR_CARDS（重复进入多次仍纯净）', () => {
  setPersons([PERSON_DEFAULT]);
  const p = makePage();
  p._enterTour();
  const withArch = p.data.tourCards;
  assert.ok(withArch !== pageCfg.data.tourCards, '应返回新数组而非复用常量引用');
  assert.equal(pageCfg.data.tourCards[0].arch, undefined, '模块级常量卡 1 不得带 arch');
  setPersons([]);
  p._enterTour();
  const without = p.data.tourCards;
  assert.equal(without[0].arch, undefined, '清档后重进 → 无预填');
  assert.equal(pageCfg.data.tourCards[0].arch, undefined, '模块级常量始终无 arch');
});

test('无档案路径同样返回副本：就地改返回值不污染常量与后续调用（M4 Minor-3②）', () => {
  const p = makePage();
  p._enterTour();
  const cards = p.data.tourCards;
  assert.ok(cards !== pageCfg.data.tourCards, '无档案路径应返回新数组（非共享常量引用）');
  cards[0].title = '被污染';
  p._enterTour();
  assert.equal(p.data.tourCards[0].title, '排一次盘', '就地改上次返回值不应影响后续调用');
  assert.equal(pageCfg.data.tourCards[0].title, '排一次盘', '模块级 TOUR_CARDS 不得被污染');
});

/* ── 4. wxml 接线（Q3 惯例：读文件断言展示条件） ── */

test('onboarding.wxml：tour 卡渲染区含档案摘要行（wx:if="{{item.arch}}" + 「你的档案：」前缀）', () => {
  const src = fs.readFileSync(path.join(__dirname, '../pages/onboarding/onboarding.wxml'), 'utf8');
  const m = src.match(/<view wx:if="\{\{item\.arch\}\}" class="tour-arch">([^<]+)<\/view>/);
  assert.ok(m, 'tour 卡应存在 wx:if="{{item.arch}}" 的档案摘要行（无档案不显示）');
  assert.equal(m[1], '你的档案：{{item.arch}}', '摘要行应带「你的档案：」前缀，内容为 birthSummary');
  const cardRegion = src.split('tour-item')[1];
  assert.match(cardRegion, /wx:if="\{\{item\.arch\}\}"/, '摘要行应位于 tour-item 卡渲染区内');
});
