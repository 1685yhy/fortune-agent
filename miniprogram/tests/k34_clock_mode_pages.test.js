// 易理明灯 k34 A12 — bazi / onboarding 两页接 k19 钟表档（分钟不降级）node 单测
// 运行：cd miniprogram && node --test tests/k34_clock_mode_pages.test.js
// 缺陷：两页此前只有时辰档（代表整点 + 0 分），既有精确档案（10:55）经本页保存
//   即被写成 minute=0（数据降级）——bazi 页 :251 手动输入 / :461 表单保存两处
//   `birth_minute: 0` 硬编码 + 读回只认时辰序号。
// 本用例锁：精确档案读回进钟表档 → 保存回写真实 minute（10:55 不丢）；只知时辰
//   仍按代表整点 + 0 分；两页 UI 接线。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

/* ── 全局环境：getApp / wx 桩（页面 require 时不需要，调用时才用） ── */
global.getApp = () => ({ globalData: {} });

function stubWx() {
  const store = {};
  global.wx = {
    getStorageSync: (k) => store[k],
    setStorageSync: (k, v) => { store[k] = v; },
    removeStorageSync: (k) => { delete store[k]; },
    getWindowInfo: () => ({ statusBarHeight: 20 }),
    getSystemInfoSync: () => ({ statusBarHeight: 20 }),
    showToast: () => {},
    showModal: () => {},
    navigateBack: () => {},
    reLaunch: () => {},
    setClipboardData: () => {},
    pageScrollTo: () => {},
  };
  return store;
}

/* ── 页面配置捕获（与既有测试同款） ── */
const savedPage = global.Page;
function loadPage(rel, label) {
  let cfg = null;
  global.Page = (c) => { cfg = c; };
  try {
    require(rel);
  } finally {
    global.Page = savedPage;
  }
  assert.ok(cfg, `${label} 页面配置应可加载`);
  return cfg;
}
const api = require('../utils/api');
const baziCfg = loadPage('../pages/bazi/bazi', 'bazi.js');
const onboardingCfg = loadPage('../pages/onboarding/onboarding', 'onboarding.js');

function makePage(cfg) {
  const page = Object.assign({}, cfg);
  page.data = JSON.parse(JSON.stringify(cfg.data));
  page.setData = function (upd, cb) {
    Object.assign(this.data, upd);
    if (typeof cb === 'function') cb();
  };
  return page;
}

const CLOCK_PERSON = {
  id: 3, name: '李四', relation: '自己', gender: '女',
  birth_year: 1999, birth_month: 5, birth_day: 13,
  birth_hour: 10, birth_minute: 55, calendar: 'solar', city: '上海',
  is_default: true, solar_time: 1,
};

/* ═══════ 1. bazi 页：档案读回与保存（核心验收：10:55 保存后仍 55） ═══════ */

test('A12 bazi 页：精确档案（10:55）读回进钟表档（时辰 chips 按时钟窗口推导）', () => {
  stubWx();
  const page = makePage(baziCfg);
  page._enterForm(CLOCK_PERSON);
  assert.equal(page.data.clockSet, true, 'minute>0 的档案必须进钟表档');
  assert.equal(page.data.clockHIdx, 10, '钟表小时 = 档案 birth_hour');
  assert.equal(page.data.clockMIdx, 55, '钟表分钟 = 档案 birth_minute（未被清零）');
  assert.equal(page.data.hourIndex, 5, '时辰 = 巳时（k19 时钟窗口，非旧误读戌时）');
  assert.equal(page.data.currentPerson.id, 3, '仍为档案命主（保存走 PUT）');
});

test('A12 bazi 页：只知时辰档案（代表整点）保持时辰档，不误进钟表档', () => {
  stubWx();
  const page = makePage(baziCfg);
  page._enterForm({
    id: 4, name: '王五', relation: '朋友', gender: '男',
    birth_year: 1990, birth_month: 1, birth_day: 1,
    birth_hour: 9, birth_minute: 0, calendar: 'solar', city: '',
  });
  assert.equal(page.data.clockSet, false, '代表整点 + 0 分 = 只知时辰，保持时辰档');
  assert.equal(page.data.hourIndex, 5, '9 点代表整点 = 巳时');
});

test('A12 bazi 页：档案保存 payload 带真实分钟（10:55 不降级为 0）', async (t) => {
  stubWx();
  const page = makePage(baziCfg);
  page._enterForm(CLOCK_PERSON);
  let sent = null;
  t.mock.method(api, 'updatePerson', (id, data) => {
    sent = data;
    return Promise.resolve({ person: Object.assign({ id }, data) });
  });
  await page.onSave();
  assert.ok(sent, 'onSave 应调用 updatePerson（档案命主走 PUT）');
  assert.equal(sent.birth_hour, 10, '钟表小时原样落档');
  assert.equal(sent.birth_minute, 55, '★ 分钟不降级（A12 核心验收）');
  assert.equal(sent.birth_year, 1999);
  assert.equal(sent.birth_month, 5);
  assert.equal(sent.birth_day, 13);
});

test('A12 bazi 页：时辰档档案保存仍为代表整点 + 0 分（不误写钟表值）', async (t) => {
  stubWx();
  const page = makePage(baziCfg);
  page._enterForm({
    id: 5, name: '赵六', relation: '父母', gender: '女',
    birth_year: 1970, birth_month: 2, birth_day: 2,
    birth_hour: 7, birth_minute: 0, calendar: 'solar', city: '北京',
  });
  let sent = null;
  t.mock.method(api, 'updatePerson', (id, data) => {
    sent = data;
    return Promise.resolve({ person: Object.assign({ id }, data) });
  });
  await page.onSave();
  assert.equal(sent.birth_hour, 7, '辰时代表整点 07');
  assert.equal(sent.birth_minute, 0);
});

test('A12 bazi 页：旧路径（服务端 bazi_info 10:55）读回钟表档 → 保存经 updateBazi 不丢分钟', async (t) => {
  stubWx();
  const page = makePage(baziCfg);
  page._applyBazi({
    year: 1988, month: 8, day: 8, hour: 10, minute: 55,
    gender: '男', calendar: 'solar', city: '杭州', solar_time: 1,
  });
  assert.equal(page.data.clockSet, true, '服务端 minute>0 → 钟表档回显');
  assert.equal(page.data.clockHIdx, 10);
  assert.equal(page.data.clockMIdx, 55);
  assert.equal(page.data.hourIndex, 5);
  let sent = null;
  t.mock.method(api, 'updateBazi', (data) => {
    sent = data;
    return Promise.resolve({ status: 'ok' });
  });
  await page.onSave();
  assert.ok(sent, '无档案命主 → updateBazi（② 源）');
  assert.equal(sent.birth_hour, 10);
  assert.equal(sent.birth_minute, 55, '★ ② 源用户档案分钟不降级');
});

test('A12 bazi 页：手动输入钟表档 → _manualPayload 带真实分钟；时辰档不变', () => {
  stubWx();
  const page = makePage(baziCfg);
  page.setData({ mDate: '1995-03-08', mCal: 'solar', mGender: '女', mPlace: '广州' });
  // 钟表档
  page.setData({ mClockSet: true, mClockHIdx: 10, mClockMIdx: 55, mHourIndex: 5 });
  let p = page._manualPayload('老张');
  assert.equal(p.birth_hour, 10);
  assert.equal(p.birth_minute, 55);
  // 时辰档（手选时辰 → onMHourChange 退出钟表档）
  page.onMHourChange({ currentTarget: { dataset: { idx: 5 } } });   // 巳时
  assert.equal(page.data.mClockSet, false, '手选时辰退出钟表档');
  p = page._manualPayload('老张');
  assert.equal(p.birth_hour, 9, '巳时代表整点 09');
  assert.equal(p.birth_minute, 0);
});

test('A12 bazi 页：钟表档联动（开档起点 12:00 / 选时联动时辰 / 手选时辰退出）', () => {
  stubWx();
  const page = makePage(baziCfg);
  page.onMClockToggle();
  assert.equal(page.data.mClockSet, true);
  assert.equal(page.data.mClockHIdx, 12, '未选时辰 → 12:00 起点（与 persons 同款）');
  page.onMClockHourChange({ detail: { value: 10 } });
  assert.equal(page.data.mHourIndex, 5, '10 时 → 巳时（时钟窗口）');
  page.onMClockMinuteChange({ detail: { value: 55 } });
  assert.equal(page.data.mClockMIdx, 55);
  page.setData({ clockSet: false });
  page.onClockToggle();
  assert.equal(page.data.clockSet, true);
  assert.equal(page.data.clockHIdx, 12);
  page.onClockHourChange({ detail: { value: 23 } });
  assert.equal(page.data.hourIndex, 0, '23 时 → 子时');
  page.onHourChange({ detail: { value: 5 } });
  assert.equal(page.data.clockSet, false, '手选时辰退出钟表档');
});

test('A12 bazi.wxml：钟表档 UI 接线（开关 + 时/分 picker）', () => {
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/bazi/bazi.wxml'), 'utf8');
  assert.match(wxml, /bindtap="onClockToggle"/);
  assert.match(wxml, /bindchange="onClockHourChange"/);
  assert.match(wxml, /bindchange="onClockMinuteChange"/);
  assert.match(wxml, /bindtap="onMClockToggle"/);
  assert.match(wxml, /bindchange="onMClockHourChange"/);
  assert.match(wxml, /bindchange="onMClockMinuteChange"/);
  assert.match(wxml, /clockHourLabels\[clockHIdx\]/);
  assert.match(wxml, /clockMinuteLabels\[clockMIdx\]/);
});

/* ═══════ 2. onboarding 页：建档带真实分钟 ═══════ */

test('A12 onboarding 页：钟表档建档 → birth_minute 真实值（10:55）', async (t) => {
  stubWx();
  const page = makePage(onboardingCfg);
  page.setData({
    cal: 'solar', year: '1999', month: '5', day: '13', gender: '女',
    hourIndex: 5, clockSet: true, clockHIdx: 10, clockMIdx: 55,
  });
  page._refreshFilled();   // 年月日齐全（filled 由输入回调置位，用例内直接驱动）
  let sent = null;
  t.mock.method(api, 'createPerson', (data) => {
    sent = data;
    return Promise.resolve({ person: Object.assign({ id: 9 }, data) });
  });
  await page.onSubmit();
  assert.ok(sent, 'onSubmit 应调用 createPerson');
  assert.equal(sent.birth_hour, 10);
  assert.equal(sent.birth_minute, 55, '★ 建档分钟不丢（A12）');
  assert.equal(page.data.phase, 'done');
  assert.match(page.data.doneSummary, /巳时 10:55/, '完成页摘要含钟表时间');
});

test('A12 onboarding 页：时辰档建档 → 代表整点 + 0 分（原行为不变）', async (t) => {
  stubWx();
  const page = makePage(onboardingCfg);
  page.setData({ cal: 'solar', year: '1999', month: '5', day: '13', gender: '女', hourIndex: 5 });
  page._refreshFilled();
  let sent = null;
  t.mock.method(api, 'createPerson', (data) => {
    sent = data;
    return Promise.resolve({ person: Object.assign({ id: 10 }, data) });
  });
  await page.onSubmit();
  assert.equal(sent.birth_hour, 9);
  assert.equal(sent.birth_minute, 0);
  assert.match(page.data.doneSummary, /巳时/, '完成页摘要为时辰');
  assert.doesNotMatch(page.data.doneSummary, /\d+:\d\d/, '时辰档不显示伪钟表时间');
});

test('A12 onboarding 页：钟表档联动与摘要/复位', () => {
  stubWx();
  const page = makePage(onboardingCfg);
  page.setData({ year: '1999', month: '5', day: '13' });
  page._refreshFilled();
  page.onClockToggle();
  assert.equal(page.data.clockSet, true);
  assert.equal(page.data.clockHIdx, 12, '未选时辰 → 12:00 起点');
  assert.match(page.data.summary, /午时/, '摘要随钟表档刷新（12:00 → 午时）');
  page.onClockHourChange({ detail: { value: 10 } });
  assert.equal(page.data.hourIndex, 5);
  assert.match(page.data.summary, /巳时/, '选时联动时辰与摘要');
  page.onClockMinuteChange({ detail: { value: 55 } });
  assert.match(page.data.summary, /巳时 10:55/, '摘要含真实分钟');
  page.onHourChange({ currentTarget: { dataset: { idx: 6 } } });   // 午时
  assert.equal(page.data.clockSet, false, '手选时辰退出钟表档');
  assert.match(page.data.summary, /午时/, '摘要回到时辰档');
  page.setData({ clockSet: true, clockHIdx: 23, clockMIdx: 30 });
  page.replay();
  assert.equal(page.data.clockSet, false, '重看引导复位钟表档');
  assert.equal(page.data.clockHIdx, 0);
  assert.equal(page.data.clockMIdx, 0);
});

test('A12 onboarding.wxml：钟表档 UI 接线（开关 + 时/分 picker）', () => {
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/onboarding/onboarding.wxml'), 'utf8');
  assert.match(wxml, /bindtap="onClockToggle"/);
  assert.match(wxml, /bindchange="onClockHourChange"/);
  assert.match(wxml, /bindchange="onClockMinuteChange"/);
  assert.match(wxml, /clockHourLabels/);
  assert.match(wxml, /clockMinuteLabels/);
});

/* ═══════ 3. 结构锁：两页不再把 birth_minute 硬编码为 0 ═══════ */

test('A12 结构锁：bazi/onboarding 的 birth_minute 一律走钟表档分支，无硬编码 0', () => {
  for (const rel of ['../pages/bazi/bazi.js', '../pages/onboarding/onboarding.js']) {
    const js = fs.readFileSync(path.join(__dirname, rel), 'utf8');
    assert.doesNotMatch(js, /birth_minute: 0,/,
      `${rel} 不得再有 birth_minute: 0 硬编码（会把精确档案降级）`);
    assert.match(js, /birth_minute: d\.clockSet \? d\.clockMIdx : 0,/,
      `${rel} 的 birth_minute 必须按钟表档取真实分钟`);
    assert.match(js, /birth_hour: d\.clockSet \? d\.clockHIdx :/,
      `${rel} 的 birth_hour 必须按钟表档取时钟小时`);
  }
});
