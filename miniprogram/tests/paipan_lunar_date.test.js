// 易理明灯 R2-5 — 农历生日提交直排修复（lunar 未转公历即排盘，起运差 5 年 P0）
// 运行：cd miniprogram && node --test tests/paipan_lunar_date.test.js
// 覆盖（task-R2-5-brief 测试要求 2）：
//   1. paipan.js onPaipan：bCal==='lunar' → payload 农历转公历（1999-03-28 → 1999/5/13）
//   2. solar 提交 → 原样（零行为回退）
//   3. lunar 转换失败（非法农历日 1999-03-30）→ 回落原文不崩溃（lunarDateToSolar
//      注释契约：失败回落原文）
//   4. 页面表单态 bDate/bCal/bDateText 不被提交转换改写（仅 payload 用公历）
//   5. duipan.js onCompare 同款漏转一并修复（同类入口扫描结论：唯一第二处）
// 修复前：onPaipan/onCompare 直接 this.data.bDate.split('-') 无视 bCal →
// 阴历 1999-03-28 被当公历 3/28 直排（起运 7年4月，问真正确 2年4月）。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ---- 全局环境（小程序运行时内置） ----
global.getApp = () => ({ globalData: {}, loginPromise: null });
global.wx = { getStorageSync: () => undefined };

const api = require('../utils/api');

const PAIPAN_JS = path.join(__dirname, '../pages/paipan/paipan.js');
const DUIPAN_JS = path.join(__dirname, '../pages/duipan/duipan.js');
const LUNAR_JS = path.join(__dirname, '../utils/lunar.js');

/* ── 1. 接线源码断言（与既有测试同款正则口径） ── */

test('R2-5 paipan：onPaipan 提交前 bCal lunar → lunarDateToSolar 转公历（失败回落原文）', () => {
  const src = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(src, /let date = this\.data\.bDate;[\s\S]{0,200}?if \(this\.data\.bCal === 'lunar'\) date = lunarDateToSolar\(date\) \|\| date;/,
    'onPaipan 必须：bCal===' + "'lunar'" + ' 时 date=lunarDateToSolar(date)||date（转换失败回落，与 helper 注释契约一致）');
});

test('k9 收敛：lunarDateToSolar 单点实现已迁入 utils/lunar.js（paipan 页只留别名调用）', () => {
  const utilSrc = fs.readFileSync(LUNAR_JS, 'utf8');
  assert.match(utilSrc, /const parts = String\(dateStr \|\| ''\)\.split\('-'\);/,
    'utils/lunar.js 必须持有唯一实现：parts 从入参 dateStr 拆分（R2-5 修复语义，k9 自 paipan/duipan/hehun 三页收敛）');
  assert.match(utilSrc, /lunar2solar\(y, m, d, false\)/,
    '实现必须复用本模块 lunar2solar（单点，行为逐字节不变）');
  assert.match(utilSrc, /lunarDateToSolar,/, 'utils/lunar.js 必须导出 lunarDateToSolar');
  // 三页不得再各自持有本地 function 实现（收敛后只允许模块级别名）
  for (const page of [PAIPAN_JS, DUIPAN_JS, path.join(__dirname, '../pages/hehun/hehun.js')]) {
    const pageSrc = fs.readFileSync(page, 'utf8');
    assert.ok(!/function lunarDateToSolar\(/.test(pageSrc), page + ' 不得残留本地 function 实现');
    assert.match(pageSrc, /lunarDateToSolar = lunar\.lunarDateToSolar;/,
      page + ' 必须以模块级别名引用 utils/lunar.js 单点');
  }
});

test('R2-5 paipan：表单展示态不回改（bDate/bCal 保持用户输入，仅 payload 用公历）', () => {
  const src = fs.readFileSync(PAIPAN_JS, 'utf8');
  const onPaipan = src.slice(src.indexOf('onPaipan()'), src.indexOf('onPaipan()') + 2200);
  assert.ok(!/setData\(\{ bDate:/.test(onPaipan), 'onPaipan 内不得改写 bDate 表单值');
  assert.ok(!/setData\(\{ bCal:/.test(onPaipan), 'onPaipan 内不得改写 bCal 表单值');
  assert.match(src, /errorMsg: '请选择出生年月日'/, '既有非法输入 errorMsg 校验路径保留（不新增崩溃路径）');
});

test('R2-5 duipan：onCompare 同款修复（bCal lunar → 转公历提交，表单态不回改）', () => {
  const src = fs.readFileSync(DUIPAN_JS, 'utf8');
  assert.match(src, /lunarDateToSolar\(date\) \|\| date/, 'onCompare 必须同款转换（全前端同类入口扫描：唯一第二处漏转）');
  const onCompare = src.slice(src.indexOf('onCompare()'), src.indexOf('onCompare()') + 1800);
  assert.ok(!/setData\(\{ bDate:/.test(onCompare), 'onCompare 内不得改写 bDate 表单值');
});

/* ── 2. paipan 功能断言（wx.request 捕获 payload） ── */

const BASEURL_KEY = 'ylm_baseurl';
const MIN_CHART = { bazi: ['己卯', '己巳', '乙丑', '辛巳'] };

function captureRoutes(bodies, route) {
  global.wx = {
    getStorageSync: (k) => (k === BASEURL_KEY ? { url: 'http://mock-base', t: Date.now() } : undefined),
    setStorageSync: () => {},
    removeStorageSync: () => {},
    showLoading: () => {},
    hideLoading: () => {},
    showToast: () => {},
    pageScrollTo: () => {},
    request: (opt) => {
      const url = String(opt.url || '');
      if (url.indexOf(route) !== -1) {
        bodies.push(Object.assign({}, opt.data));
        opt.success && opt.success({ statusCode: 200, data: route.indexOf('/api/duipan') !== -1 ? {} : MIN_CHART });
        return;
      }
      opt.fail && opt.fail({ errMsg: 'unexpected route: ' + url });
    },
  };
}

const savedPage = global.Page;
function loadPageCfg(pagePath) {
  let cfg = null;
  global.Page = (c) => { cfg = c; };
  try {
    require(pagePath);
  } finally {
    global.Page = savedPage;
  }
  assert.ok(cfg, '页面配置应可加载');
  return cfg;
}
const paipanCfg = loadPageCfg('../pages/paipan/paipan');
const duipanCfg = loadPageCfg('../pages/duipan/duipan');

function dottedSetData(upd) {
  Object.keys(upd).forEach((k) => {
    if (k.indexOf('.') === -1) { this.data[k] = upd[k]; return; }
    const parts = k.split('.');
    let cur = this.data;
    for (let i = 0; i < parts.length - 1; i++) {
      if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) cur[parts[i]] = {};
      cur = cur[parts[i]];
    }
    cur[parts[parts.length - 1]] = upd[k];
  });
}

function makePaipanPage(bodies, date, cal) {
  captureRoutes(bodies, '/api/paipan');
  const page = Object.assign({}, paipanCfg);
  page.data = JSON.parse(JSON.stringify(paipanCfg.data));
  page.setData = dottedSetData;
  Object.assign(page.data, {
    bDate: date, bCal: cal, bDateText: '1999年3月28日',
    bHourIdx: 3, bHourSet: true, bGender: 'male', bCityName: '长春',
    bCitySet: true, loading: false,
  });
  return page;
}

function makeDuipanPage(bodies, date, cal) {
  captureRoutes(bodies, '/api/duipan');
  const page = Object.assign({}, duipanCfg);
  page.data = JSON.parse(JSON.stringify(duipanCfg.data));
  page.setData = dottedSetData;
  Object.assign(page.data, {
    bDate: date, bCal: cal, bDateText: '1999年3月28日',
    bHourAIdx: 5, bHourASet: true, bHourBIdx: 6, bHourBSet: true,
    bGender: 'male', bCityName: '长春', loading: false,
  });
  return page;
}

test('R2-5 全链通：阴历提交 → payload 公历 1999/5/13（修复前=3/28 直排）', async () => {
  const bodies = [];
  const oldWx = global.wx;
  const page = makePaipanPage(bodies, '1999-03-28', 'lunar');
  try {
    page.onPaipan();
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(bodies.length, 1, '应恰好发出 1 次 /api/paipan');
    assert.equal(bodies[0].birthYear, 1999, 'payload birthYear 必须是转换后公历');
    assert.equal(bodies[0].birthMonth, 5, '阴历 3/28 → 公历 5 月（修复前 3 → 断言失败）');
    assert.equal(bodies[0].birthDay, 13, '阴历 3/28 → 公历 13 日（修复前 28 → 断言失败）');
    // 表单展示态不回改：仍是用户选择的阴历
    assert.equal(page.data.bDate, '1999-03-28');
    assert.equal(page.data.bCal, 'lunar');
  } finally {
    global.wx = oldWx;
  }
});

test('R2-5 全链通：公历提交 → 原样 1999/3/28（零行为回退）', async () => {
  const bodies = [];
  const oldWx = global.wx;
  const page = makePaipanPage(bodies, '1999-03-28', 'solar');
  try {
    page.onPaipan();
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(bodies.length, 1);
    assert.equal(bodies[0].birthYear, 1999);
    assert.equal(bodies[0].birthMonth, 3);
    assert.equal(bodies[0].birthDay, 28);
  } finally {
    global.wx = oldWx;
  }
});

test('R2-5 全链通：非法农历日（1999-03-30 不存在）→ 回落原文 3/30 不崩溃', async () => {
  const bodies = [];
  const oldWx = global.wx;
  const page = makePaipanPage(bodies, '1999-03-30', 'lunar');
  try {
    page.onPaipan();
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(bodies.length, 1, '转换失败必须回落原值继续提交（不新增崩溃路径）');
    assert.equal(bodies[0].birthMonth, 3);
    assert.equal(bodies[0].birthDay, 30);
    assert.equal(page.data.bCal, 'lunar');
  } finally {
    global.wx = oldWx;
  }
});

test('R2-5 duipan 全链通：阴历提交 → /api/duipan payload 公历 1999/5/13', async () => {
  const bodies = [];
  const oldWx = global.wx;
  const page = makeDuipanPage(bodies, '1999-03-28', 'lunar');
  try {
    page.onCompare();
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(bodies.length, 1, '应恰好发出 1 次 /api/duipan');
    assert.equal(bodies[0].birthYear, 1999);
    assert.equal(bodies[0].birthMonth, 5, 'duipan 阴历 3/28 → 公历 5 月（修复前 3 → 断言失败）');
    assert.equal(bodies[0].birthDay, 13);
    assert.equal(page.data.bDate, '1999-03-28', 'duipan 表单态也不被改写');
  } finally {
    global.wx = oldWx;
  }
});
