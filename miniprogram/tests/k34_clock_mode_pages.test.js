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

/* ═══════ 4. k34 审查修复 Important-1：钟表档「开 → 关」往返恒等 ═══════
   原缺陷：关档分支只置 clockSet:false，不回滚 hourIndex；而开档起点对
   「未选/子时(0)」是 12:00 中性值（午时 6）→「子时 → 开 → 关」保存写
   birth_hour=11（子时被写成午时，错误出生数据落档，仅 index 0 中招）。 */

test('审查修复 Important-1 · bazi 表单：钟表档「开 → 关」对 12 个时辰序号全量恒等（含 index 0 子时）', () => {
  stubWx();
  for (let i = 0; i < 12; i++) {
    const page = makePage(baziCfg);
    page.setData({ hourIndex: i });
    page.onClockToggle();                       // 开档（起点改写 hourIndex）
    assert.equal(page.data.clockSet, true, `index ${i}：应进钟表档`);
    page.onClockToggle();                       // 关档
    assert.equal(page.data.clockSet, false, `index ${i}：应回时辰档`);
    assert.equal(page.data.hourIndex, i,
      `index ${i}：往返后时辰序号必须恒等（原缺陷：index 0 子时被改写成 6 午时）`);
  }
});

test('审查修复 Important-1 · bazi 表单：子时 → 开 → 关 → 保存写 birth_hour=23（档案路径与旧路径都不落午时 11）', async (t) => {
  // ① 档案路径（PUT /api/persons/{id}）：「子时」档案（代表整点 23）
  stubWx();
  const page = makePage(baziCfg);
  page._enterForm({
    id: 7, name: '子时档案', relation: '自己', gender: '女',
    birth_year: 1995, birth_month: 3, birth_day: 8,
    birth_hour: 23, birth_minute: 0, calendar: 'solar', city: '',
  });
  assert.equal(page.data.hourIndex, 0, '子时档案（代表整点 23）读回序号 0');
  page.onClockToggle();
  page.onClockToggle();
  assert.equal(page.data.hourIndex, 0, '关档后仍为子时');
  let sent = null;
  t.mock.method(api, 'updatePerson', (id, data) => {
    sent = data;
    return Promise.resolve({ person: Object.assign({ id }, data) });
  });
  await page.onSave();
  assert.equal(sent.birth_hour, 23, '★ 子时不得被写成午时 11（错误出生数据落档）');
  assert.equal(sent.birth_minute, 0);

  // ② 旧路径（POST /api/user/bazi）：无档案表单默认 hourIndex=0（子时）
  stubWx();
  const page2 = makePage(baziCfg);
  page2.setData({ birthDate: '1995-03-08' });
  page2.onClockToggle();
  page2.onClockToggle();
  let sent2 = null;
  t.mock.method(api, 'updateBazi', (data) => {
    sent2 = data;
    return Promise.resolve({ status: 'ok' });
  });
  await page2.onSave();
  assert.equal(sent2.birth_hour, 23, '★ ② 源路径同样不得写成午时 11');
  assert.equal(sent2.birth_minute, 0);
});

test('审查修复 Important-1 · bazi 手动输入：「开 → 关」对 12 个时辰序号全量恒等 → _manualPayload 仍写 23/0', () => {
  stubWx();
  for (let i = 0; i < 12; i++) {
    const page = makePage(baziCfg);
    page.setData({ mDate: '1995-03-08', mHourIndex: i });
    page.onMClockToggle();                      // 开档
    assert.equal(page.data.mClockSet, true, `index ${i}：应进钟表档`);
    page.onMClockToggle();                      // 关档
    assert.equal(page.data.mClockSet, false, `index ${i}：应回时辰档`);
    assert.equal(page.data.mHourIndex, i,
      `index ${i}：手动输入往返后时辰序号必须恒等（原缺陷：index 0 → 6 午时）`);
  }
  const page = makePage(baziCfg);
  page.setData({ mDate: '1995-03-08', mHourIndex: 0, mGender: '女' });
  page.onMClockToggle();
  page.onMClockToggle();
  const p = page._manualPayload('老张');
  assert.equal(p.birth_hour, 23, '★ 手动输入子时不得被写成午时 11');
  assert.equal(p.birth_minute, 0);
});

test('审查修复 Important-1 · onboarding：「开 → 关」对 12 个时辰序号全量恒等 → 子时建档写 23', async (t) => {
  stubWx();
  for (let i = 0; i < 12; i++) {
    const page = makePage(onboardingCfg);
    page.setData({ hourIndex: i });
    page.onClockToggle();                       // 开档
    assert.equal(page.data.clockSet, true, `index ${i}：应进钟表档`);
    page.onClockToggle();                       // 关档
    assert.equal(page.data.clockSet, false, `index ${i}：应回时辰档`);
    assert.equal(page.data.hourIndex, i,
      `index ${i}：往返后时辰序号必须恒等（原缺陷：index 0 子时被改写成 6 午时）`);
  }
  const page = makePage(onboardingCfg);
  page.setData({ cal: 'solar', year: '1995', month: '3', day: '8', gender: '女', hourIndex: 0 });
  page._refreshFilled();
  page.onClockToggle();
  page.onClockToggle();
  assert.match(page.data.summary, /子时/, '关档后摘要回到子时（用户所见即所存）');
  let sent = null;
  t.mock.method(api, 'createPerson', (data) => {
    sent = data;
    return Promise.resolve({ person: Object.assign({ id: 11 }, data) });
  });
  await page.onSubmit();
  assert.equal(sent.birth_hour, 23, '★ 引导建档子时不得被写成午时 11');
  assert.equal(sent.birth_minute, 0);
  assert.match(page.data.doneSummary, /子时/, '完成页摘要为子时');
});

test('审查修复 Important-1 · 关档语义：动过钟表值 → 保留联动推导的时辰（用户填的钟点不被丢弃）', async (t) => {
  // 三页同款：未动钟表值 → 回滚（往返恒等，上组用例）；动过 → 保留联动时辰
  // （否则「子时 → 开 → 填 10:55 → 关 → 存」把子时写进档案，用输入被静默丢弃）
  stubWx();
  // ① bazi 表单
  const page = makePage(baziCfg);
  page.setData({ birthDate: '1995-03-08' });
  page.onClockToggle();
  page.onClockHourChange({ detail: { value: 10 } });
  page.onClockMinuteChange({ detail: { value: 55 } });
  page.onClockToggle();
  assert.equal(page.data.clockSet, false);
  assert.equal(page.data.hourIndex, 5, '关档后仍为钟表值所推巳时（不得丢回子时 0）');
  let sent = null;
  t.mock.method(api, 'updateBazi', (data) => { sent = data; return Promise.resolve({ status: 'ok' }); });
  await page.onSave();
  assert.equal(sent.birth_hour, 9, '保存按保留的巳时代表整点 09（不是子时 23）');
  assert.equal(sent.birth_minute, 0);

  // ② bazi 手动输入档
  const m = makePage(baziCfg);
  m.setData({ mDate: '1995-03-08', mGender: '女' });
  m.onMClockToggle();
  m.onMClockHourChange({ detail: { value: 10 } });
  m.onMClockMinuteChange({ detail: { value: 55 } });
  m.onMClockToggle();
  assert.equal(m.data.mHourIndex, 5, '手动输入关档后仍为巳时');
  const mp = m._manualPayload('老张');
  assert.equal(mp.birth_hour, 9);
  assert.equal(mp.birth_minute, 0);

  // ③ onboarding
  const ob = makePage(onboardingCfg);
  ob.setData({ cal: 'solar', year: '1995', month: '3', day: '8', gender: '女' });
  ob._refreshFilled();
  ob.onClockToggle();
  ob.onClockHourChange({ detail: { value: 10 } });
  ob.onClockMinuteChange({ detail: { value: 55 } });
  ob.onClockToggle();
  assert.equal(ob.data.hourIndex, 5, '引导页关档后仍为巳时');
  assert.match(ob.data.summary, /巳时/, '摘要随保留的时辰');
  let sent2 = null;
  t.mock.method(api, 'createPerson', (data) => { sent2 = data; return Promise.resolve({ person: Object.assign({ id: 12 }, data) }); });
  await ob.onSubmit();
  assert.equal(sent2.birth_hour, 9);
  assert.equal(sent2.birth_minute, 0);
});

/* ═══════ 5. k34 审查修复 Important-2：本地兜底形态 birthHour（时辰序号）按序号直取 ═══════
   原缺陷：_hourToIndex 先查 HOUR_VALUES（「代表整点」表）再兜底序号 → 序号
   3/5/7/9/11 被当成同值代表整点误映射（5=巳 → 卯时 3、11=亥 → 午时 6）；偶数
   序号恰好正确，极难被发现。本地形态由 _syncGlobal 写入，恒为序号 0-11。 */

test('审查修复 Important-2 · 本地形态时辰序号 3/5/7/9/11 不再被当成「代表整点」误映射', () => {
  stubWx();
  for (const [seq, idx] of [[0, 0], [1, 1], [3, 3], [5, 5], [7, 7], [9, 9], [11, 11]]) {
    const page = makePage(baziCfg);
    page._applyBazi({
      birthYear: 1995, birthMonth: 3, birthDay: 8, birthHour: seq,
      birthClock: false, gender: '女', calendar: 'solar', city: '',
    });
    assert.equal(page.data.hourIndex, idx, `birthHour=${seq}（时辰序号）→ 应得序号 ${idx}`);
    assert.equal(page.data.clockSet, false, '只知时辰不进钟表档');
  }
  // 判别力对照（旧实现的具体错值：5 → 卯时 3、11 → 午时 6）
  const p1 = makePage(baziCfg);
  p1._applyBazi({ birthYear: 1995, birthMonth: 3, birthDay: 8, birthHour: 5, gender: '女', calendar: 'solar', city: '' });
  assert.notEqual(p1.data.hourIndex, 3, '巳时序号 5 不得被当成代表整点 5（卯时）');
  const p2 = makePage(baziCfg);
  p2._applyBazi({ birthYear: 1995, birthMonth: 3, birthDay: 8, birthHour: 11, gender: '女', calendar: 'solar', city: '' });
  assert.notEqual(p2.data.hourIndex, 6, '亥时序号 11 不得被当成代表整点 11（午时）');
  // 字符串序号（JSON 往返）同样按序号直取
  const p3 = makePage(baziCfg);
  p3._applyBazi({ birthYear: 1995, birthMonth: 3, birthDay: 8, birthHour: '5', gender: '女', calendar: 'solar', city: '' });
  assert.equal(p3.data.hourIndex, 5, '字符串序号 "5" → 巳时 5');
});

test('审查修复 Important-2 · 本地形态钟表档（birthClock=true）仍精确回显，时辰按序号直取', () => {
  stubWx();
  const page = makePage(baziCfg);
  page._applyBazi({
    birthYear: 1995, birthMonth: 3, birthDay: 8, birthHour: 5,
    birthClock: true, birthClockHour: 10, birthClockMinute: 55,
    gender: '女', calendar: 'solar', city: '',
  });
  assert.equal(page.data.clockSet, true, 'birthClock=true → 进钟表档');
  assert.equal(page.data.clockHIdx, 10);
  assert.equal(page.data.clockMIdx, 55, '本地形态分钟不丢');
  assert.equal(page.data.hourIndex, 5, 'birthHour=5 序号即巳时（不得按代表整点读成卯时）');
});

test('审查修复 Important-2 · 服务端形态读回口径不变（代表整点 9/23、精确 10:55）', () => {
  stubWx();
  const p1 = makePage(baziCfg);
  p1._applyBazi({ year: 1990, month: 1, day: 1, hour: 9, minute: 0, gender: '男', calendar: 'solar', city: '' });
  assert.equal(p1.data.clockSet, false, '代表整点 + 0 分 = 时辰档');
  assert.equal(p1.data.hourIndex, 5, '9 点代表整点 = 巳时');
  const p2 = makePage(baziCfg);
  p2._applyBazi({ year: 1990, month: 1, day: 1, hour: 23, minute: 0, gender: '男', calendar: 'solar', city: '' });
  assert.equal(p2.data.hourIndex, 0, '23 点代表整点 = 子时（晚子时）');
  const p3 = makePage(baziCfg);
  p3._applyBazi({ year: 1990, month: 1, day: 1, hour: 10, minute: 55, gender: '男', calendar: 'solar', city: '' });
  assert.equal(p3.data.clockSet, true, '服务端 minute>0 → 钟表档');
  assert.equal(p3.data.hourIndex, 5, '10:55 → 巳时（时钟窗口）');
});
