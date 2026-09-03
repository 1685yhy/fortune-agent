// 易理明灯 R2-4-fix — 排盘页真太阳时开关 wire-through（F1 Critical）node 单测
// 运行：cd miniprogram && node --test tests/paipan_solar_time.test.js
// 背景：R2-4 排盘页新增真太阳时开关（data 默认 true=按出生地经度校准，可关=false），
//   但 api.js paipan() 用白名单构造请求体时没有 solarTime → POST /api/paipan 恒不带
//   solarTime → 服务端 BaziInput 默认 True → 用户「关」无效果（仍出修正盘），
//   而页面文案已动态显示「已按北京时间直排」——开关断裂 + 误导。
// 修复：白名单加 nullish 守卫透传（p.solarTime != null 才带参）——未传该字段的
//   调用方保持不带参，服务端默认 True（默认开）语义由后端兜底，零行为变化。
// 调用方清单（2026-09-03 静态盘点全 miniprogram）：仅 pages/paipan/paipan.js
//   onPaipan 调用 api.paipan()（页内有开关 → 必须带当前值）；历史回看
//   onHistoryTap 走 paipanHistoryDetail（重看 0 重跑，不重排盘）；hehun/duipan
//   等其它四术接口各有独立后端入口，不调 api.paipan → 不带参分支=服务端默认开。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ---- 全局环境（小程序运行时内置） ----
global.getApp = () => ({ globalData: {}, loginPromise: null });
global.wx = { getStorageSync: () => undefined }; // 页面模块 require 兜底

const api = require('../utils/api');

const PAIPAN_JS = path.join(__dirname, '../pages/paipan/paipan.js');
const PAIPAN_WXML = path.join(__dirname, '../pages/paipan/paipan.wxml');
const API_JS = path.join(__dirname, '../utils/api.js');

/* ── 1. 接线源码断言（与既有测试同款正则口径） ── */

test('R2-4-fix api：paipan() 白名单带 nullish 守卫透传 solarTime（未传=不带参）', () => {
  const src = fs.readFileSync(API_JS, 'utf8');
  assert.match(src, /if \(p\.solarTime != null\) data\.solarTime = !!p\.solarTime;/,
    '透传必须 nullish 守卫：显式 false 能带出，未传字段的调用方保持不带参（服务端默认 True）');
  assert.match(src, /gender: p\.gender === 'female' \? 'female' : 'male',/, '白名单其余字段不变');
});

test('R2-4-fix 页面：开关默认开（data bSolarTime:true），bindchange 写入页面态', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /bSolarTime: true,/, '开关默认开（产品口径：默认真太阳时修正）');
  assert.match(js, /onSolarTimeChange\(e\)\s*\{\s*this\.setData\(\{ bSolarTime: !!e\.detail\.value \}\)/,
    'switch bindchange → onSolarTimeChange → setData bSolarTime');
  assert.match(js, /solarTime: this\.data\.bSolarTime,/, 'onPaipan payload 必须带页面当前开关值');
});

test('R2-4-fix wxml：出生地副文案按 bSolarTime 三元切换（「已按北京时间直排」仅关分支出现）', () => {
  const wxml = fs.readFileSync(PAIPAN_WXML, 'utf8');
  const ternary = wxml.match(/\{\{bSolarTime \? '真太阳时按出生地经度校准' : '已按北京时间直排[^']*'\}\}/);
  assert.ok(ternary, '出生地副文案必须是 bSolarTime 三元（开=经度校准 / 关=北京时间直排）');
  assert.equal(wxml.match(/已按北京时间直排/g) && wxml.match(/已按北京时间直排/g).length, 1,
    '「已按北京时间直排」只应出现在关分支文案（修后关=真直排，文案与行为一致）');
  const sw = wxml.match(/<switch checked="\{\{bSolarTime\}\}" bindchange="onSolarTimeChange"/);
  assert.ok(sw, '开关必须 checked 绑定 bSolarTime + bindchange 绑定 onSolarTimeChange');
});

/* ── 2. api 请求构造功能断言（wx.request 捕获） ── */
// 预置 baseURL 缓存（ylm_baseurl 当天）→ 探活走缓存零定时器；请求路由只回 /api/paipan。
const BASEURL_KEY = 'ylm_baseurl';
const MIN_CHART = { bazi: ['己卯', '己巳', '乙丑', '辛巳'] };

function capturePaipan(bodies) {
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
      if (url.indexOf('/api/paipan') !== -1) {
        bodies.push(Object.assign({}, opt.data));
        opt.success && opt.success({ statusCode: 200, data: MIN_CHART });
        return;
      }
      opt.fail && opt.fail({ errMsg: 'unexpected route: ' + url });
    },
  };
}

const BASE_P = {
  birthYear: 1999, birthMonth: 5, birthDay: 13, birthHour: 9,
  gender: 'male', city: '北京',
};

test('R2-4-fix api：paipan() 带 solarTime:false → 请求体含 solarTime:false（关=真直排）', async () => {
  const bodies = [];
  const oldWx = global.wx;
  capturePaipan(bodies);
  try {
    await api.paipan(Object.assign({}, BASE_P, { solarTime: false }));
    assert.equal(bodies.length, 1, '应恰好发出 1 次 /api/paipan');
    assert.equal(bodies[0].solarTime, false, '白名单必须透传 solarTime:false（此前被静默丢弃）');
    assert.equal(bodies[0].birthHour, 9);
  } finally {
    global.wx = oldWx;
  }
});

test('R2-4-fix api：paipan() 带 solarTime:true → 请求体含 solarTime:true（默认开=修正盘）', async () => {
  const bodies = [];
  const oldWx = global.wx;
  capturePaipan(bodies);
  try {
    await api.paipan(Object.assign({}, BASE_P, { solarTime: true }));
    assert.equal(bodies.length, 1);
    assert.equal(bodies[0].solarTime, true, '白名单必须透传 solarTime:true');
  } finally {
    global.wx = oldWx;
  }
});

test('R2-4-fix api：不带 solarTime（模拟其它页/未来调用方）→ 请求体无该字段（服务端默认开兜底）', async () => {
  const bodies = [];
  const oldWx = global.wx;
  capturePaipan(bodies);
  try {
    await api.paipan(Object.assign({}, BASE_P));   // 唯一真实调用方 paipan 页之外不存在其它调用方；此分支防未来回归
    assert.equal(bodies.length, 1);
    assert.ok(!('solarTime' in bodies[0]), '未传 solarTime 不得凭空带参（服务端 BaziInput 默认 True 语义不变）');
    assert.deepEqual(Object.keys(bodies[0]).sort(),
      ['birthDay', 'birthHour', 'birthMonth', 'birthYear', 'city', 'gender'],
      '白名单其余字段与修复前完全一致（零行为变化）');
  } finally {
    global.wx = oldWx;
  }
});

/* ── 3. 页面全链通：开关 → payload → 请求体 ── */

/* 页面配置捕获（与既有测试同款） */
const savedPage = global.Page;
function loadPaipanCfg() {
  let cfg = null;
  global.Page = (c) => { cfg = c; };
  try {
    require('../pages/paipan/paipan');
  } finally {
    global.Page = savedPage;
  }
  assert.ok(cfg, 'paipan 页面配置应可加载');
  return cfg;
}
const paipanCfg = loadPaipanCfg();

/* setData 桩：模拟 wx 点路径赋值 */
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

function makePaipanPage(bodies) {
  capturePaipan(bodies);
  const page = Object.assign({}, paipanCfg);
  page.data = JSON.parse(JSON.stringify(paipanCfg.data));
  page.setData = dottedSetData;
  Object.assign(page.data, {
    bDate: '1999-05-13', bCal: 'solar', bHourIdx: 3, bHourSet: true,
    bGender: 'male', bCityName: '北京', bCitySet: true, loading: false,
  });
  return page;
}

test('R2-4-fix 全链通：用户关开关 → onPaipan 请求体 solarTime:false（关=真直排到线）', async () => {
  const bodies = [];
  const oldWx = global.wx;
  const page = makePaipanPage(bodies);
  try {
    assert.equal(page.data.bSolarTime, true, '页面默认开');
    page.onSolarTimeChange({ detail: { value: false } });   // switch 关
    assert.equal(page.data.bSolarTime, false);
    page.onPaipan();
    await new Promise((r) => setTimeout(r, 20));             // 等 promise 链 + _buildView
    assert.equal(bodies.length, 1);
    assert.equal(bodies[0].solarTime, false, '关开关必须真正到达 POST /api/paipan 请求体');
    assert.equal(bodies[0].birthHour, 2, '时辰序号 0-11 转换不变（bHourIdx3 → 2）');
  } finally {
    global.wx = oldWx;
  }
});

test('R2-4-fix 全链通：默认开（不动开关）→ 请求体 solarTime:true（默认开=修正盘到线）', async () => {
  const bodies = [];
  const oldWx = global.wx;
  const page = makePaipanPage(bodies);
  try {
    page.onPaipan();
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(bodies.length, 1);
    assert.equal(bodies[0].solarTime, true, '默认开必须显式带 solarTime:true（与服务端默认一致的显式声明）');
  } finally {
    global.wx = oldWx;
  }
});
