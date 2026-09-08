// 易理明灯 k11c — 档案级真太阳时开关（默认开 + 前端档案开关）node 单测
// 运行：cd miniprogram && node --test tests/k11c_solar_switch.test.js
// 覆盖（任务 D⑤）：
//   1. 档案编辑表单默认值：bazi 页 / persons 页 data solarOn 默认 true（产品口径默认开）
//   2. 回显：档案 solar_time=0（关）→ 表单开关显示关；缺失/旧档案 → 默认开
//   3. 提交字段：保存 payload 带 solar_time 1/0
//   4. 切换保存后提示「已更新，重新排盘生效」（未切换 → 常规保存文案）
//   5. 排盘页预填：默认命主档案 solar_time 回显到会话开关（档案=持久化语义）
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ---- 全局环境（小程序运行时内置） ----
global.getApp = () => ({ globalData: {} });
global.wx = { getStorageSync: () => undefined }; // 页面模块 require 兜底

const savedPage = global.Page;
const pageCfgs = {};
global.Page = (c) => { pageCfgs.cfg = c; };
try {
  require('../pages/bazi/bazi');
} finally {
  pageCfgs.bazi = pageCfgs.cfg || null;
  pageCfgs.cfg = null;
}
try {
  require('../pages/persons/persons');
} finally {
  pageCfgs.persons = pageCfgs.cfg || null;
  pageCfgs.cfg = null;
}
global.Page = savedPage;
assert.ok(pageCfgs.bazi, 'bazi 页面配置应可加载');
assert.ok(pageCfgs.persons, 'persons 页面配置应可加载');

const BAZI_JS = path.join(__dirname, '../pages/bazi/bazi.js');
const BAZI_WXML = path.join(__dirname, '../pages/bazi/bazi.wxml');
const PERSONS_JS = path.join(__dirname, '../pages/persons/persons.js');
const PERSONS_WXML = path.join(__dirname, '../pages/persons/persons.wxml');
const PAIPAN_JS = path.join(__dirname, '../pages/paipan/paipan.js');

/* ── 1. 接线源码断言 ── */

test('k11c bazi 页：开关默认开（data solarOn:true）+ bindchange 写入页面态', () => {
  const js = fs.readFileSync(BAZI_JS, 'utf8');
  assert.match(js, /solarOn: true,/, '开关默认开（产品口径：默认真太阳时修正）');
  assert.match(js, /onSolarTimeChange\(e\)\s*\{\s*this\.setData\(\{ solarOn: !!e\.detail\.value \}\)/,
    'switch bindchange → onSolarTimeChange → setData solarOn');
});

test('k11c bazi 页：保存 payload 带 solar_time（1/0）并回显档案开关', () => {
  const js = fs.readFileSync(BAZI_JS, 'utf8');
  assert.match(js, /solar_time: d\.solarOn \? 1 : 0,/, 'baziData 必须带 solar_time（随 /api/user/bazi 与 /api/persons PUT 落档案）');
  assert.match(js, /const solarOn = p\.solar_time !== 0;/, '档案回显：solar_time=0 关；缺失/旧档案 → 默认开');
});

test('k11c persons 页：开关默认开、payload 带 solar_time、编辑回显、切换提示文案', () => {
  const js = fs.readFileSync(PERSONS_JS, 'utf8');
  assert.match(js, /solarOn: true,/, '新增 draft 开关默认开');
  assert.match(js, /solarOn: raw\.solar_time !== 0,/, '编辑回显：raw.solar_time=0 → 关；旧档案缺字段 → 开');
  assert.match(js, /solar_time: d\.solarOn \? 1 : 0,/, '_payload 必须带 solar_time');
  assert.match(js, /已更新，重新排盘生效/, '切换后保存提示文案（C 要求逐字）');
});

test('k11c wxml：两个档案编辑面均有开关 + 说明行（既有 design tokens，说明逐字）', () => {
  for (const wxml of [BAZI_WXML, PERSONS_WXML]) {
    const src = fs.readFileSync(wxml, 'utf8');
    assert.match(src, /<switch checked="\{\{solarOn\}\}" bindchange="onSolarTimeChange" color="#A93A2C"/,
      '开关必须 checked 绑定 solarOn + bindchange onSolarTimeChange + 朱砂 #A93A2C');
    assert.match(src, /真太阳时修正/, '标题「真太阳时修正」');
    assert.match(src, /按出生地经度把当地时间换算真太阳时再定时辰；关闭则按本地时间直接排/,
      '说明行必须为 C 指定文案（关闭=按本地时间直接排）');
  }
});

test('k11c paipan 页：档案默认命主预填回显档案开关（solar_time=0 → 关）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /patch\.bSolarTime = p\.solar_time !== 0;/, '预填须带档案 solar_time（旧档案/缺失 → 默认开）');
});

/* ── 2. 页面功能行为（cfg 装载 + setData 桩 + wx 桩） ── */

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

function cloneData(cfg) {
  return JSON.parse(JSON.stringify(cfg.data || {}));
}

// 预置 baseURL 缓存（ylm_baseurl 当天）→ 探活走缓存零定时器；请求路由只回各档案端点。
const BASEURL_KEY = 'ylm_baseurl';

function installWx() {
  const toasts = [];
  const bodies = [];
  global.wx = {
    getStorageSync: (k) => (k === BASEURL_KEY
      ? { url: 'http://mock-base', t: Date.now() }
      : undefined),
    setStorageSync: () => {},
    removeStorageSync: () => {},
    showToast: (o) => toasts.push(Object.assign({}, o)),
    hideToast: () => {},
    showLoading: () => {},
    hideLoading: () => {},
    navigateBack: () => {},
    pageScrollTo: () => {},
    request: (opt) => {
      const url = String(opt.url || '');
      const m = String(opt.method || 'GET').toUpperCase();
      if (url.indexOf('/api/persons/') !== -1 && m === 'PUT') {
        bodies.push(Object.assign({}, opt.data));
        const pid = url.split('/').pop();
        opt.success && opt.success({ statusCode: 200, data: { status: 'ok', person: Object.assign({ id: pid, name: '小晚', relation: '自己' }, opt.data || {}) } });
        return;
      }
      if (url === 'http://mock-base/api/persons' && m === 'GET') {
        opt.success && opt.success({ statusCode: 200, data: { status: 'ok', persons: [] } });
        return;
      }
      if (url === 'http://mock-base/api/persons' && m === 'POST') {
        bodies.push(Object.assign({}, opt.data));
        opt.success && opt.success({ statusCode: 200, data: { status: 'ok', person: Object.assign({ id: 1, name: '新命', relation: '其他' }, opt.data || {}) } });
        return;
      }
      if (url.indexOf('/api/user/bazi') !== -1) {
        bodies.push(Object.assign({}, opt.data));
        opt.success && opt.success({ statusCode: 200, data: { success: true } });
        return;
      }
      opt.fail && opt.fail({ errMsg: 'unexpected route: ' + url });
    },
  };
  return { toasts, bodies };
}

const rawPerson = {
  id: 7, name: '小晚', relation: '自己', is_default: true,
  gender: '男', birth_year: 1999, birth_month: 5, birth_day: 13,
  birth_hour: 10, birth_minute: 55, calendar: 'solar', city: '长春',
  solar_time: 0,   // k11c：档案关
};

test('k11c bazi 页：档案 solar_time=0 回显关；翻转开保存 → PUT solar_time:1 + 「已更新，重新排盘生效」', async () => {
  const { toasts, bodies } = installWx();
  const page = Object.assign({}, pageCfgs.bazi);
  page.data = cloneData(pageCfgs.bazi);
  page.setData = dottedSetData;
  try {
    page._enterForm(rawPerson);                       // 档案关 → 表单开关关
    assert.equal(page.data.solarOn, false, '档案 solar_time=0 必须回显关');
    assert.equal(page.data.currentPerson.id, 7);
    assert.equal(page.data.birthDate, '1999-05-13');
    page.onSolarTimeChange({ detail: { value: true } });  // 用户打开开关
    assert.equal(page.data.solarOn, true);
    page.onSave();
    await new Promise((r) => setTimeout(r, 780));
    assert.equal(bodies.length, 1, '应恰好 1 次 PUT /api/persons/{id}');
    assert.equal(bodies[0].solar_time, 1, '保存 payload 必须带 solar_time:1');
    const last = toasts[toasts.length - 1];
    assert.ok(last, '应弹提示');
    assert.equal(last.title, '已更新，重新排盘生效', '切换后保存提示逐字（C 要求）');
  } finally {
    global.wx = { getStorageSync: () => undefined };
  }
});

test('k11c bazi 页：未切换开关保存 → 常规「档案已保存」提示（零误报）', async () => {
  const { toasts, bodies } = installWx();
  const page = Object.assign({}, pageCfgs.bazi);
  page.data = cloneData(pageCfgs.bazi);
  page.setData = dottedSetData;
  try {
    page._enterForm(Object.assign({}, rawPerson, { solar_time: 1 }));  // 档案开
    assert.equal(page.data.solarOn, true);
    page.onSave();                                    // 不动开关
    await new Promise((r) => setTimeout(r, 780));
    assert.equal(bodies.length, 1);
    assert.equal(bodies[0].solar_time, 1);
    const last = toasts[toasts.length - 1];
    assert.notEqual(last.title, '已更新，重新排盘生效');
  } finally {
    global.wx = { getStorageSync: () => undefined };
  }
});

test('k11c persons 页：编辑回显（关）+ 翻转保存 → PUT solar_time:1 + 切换提示；旧档案缺字段 → 默认开', async () => {
  const { toasts, bodies } = installWx();
  const page = Object.assign({}, pageCfgs.persons);
  page.data = cloneData(pageCfgs.persons);
  page.setData = dottedSetData;
  try {
    // 编辑回显：raw.solar_time=0 → 表单 solarOn=false
    page._rawList = [rawPerson];
    page.onOpenEdit({ currentTarget: { dataset: { id: 7 } } });
    page._refreshForm();   // setData 桩不回调 → 手动刷新 filled/hint
    assert.equal(page.data.mode, 'form');
    assert.equal(page.data.solarOn, false, 'persons 编辑回显关');
    assert.equal(page.data.dName, '小晚');
    assert.equal(page.data.filled, true, '表单可保存（filled 由 _refreshForm 计算）');
    // 翻转开 → 保存
    page.onSolarTimeChange({ detail: { value: true } });
    page.onSave();
    await new Promise((r) => setTimeout(r, 60));
    assert.equal(bodies.length, 1);
    assert.equal(bodies[0].solar_time, 1);
    assert.equal(toasts[toasts.length - 1].title, '已更新，重新排盘生效');
  } finally {
    global.wx = { getStorageSync: () => undefined };
  }

  // 旧档案缺 solar_time 字段（本地存量）→ 编辑默认开（兼容默认开）
  const { toasts: toasts2 } = installWx();
  const page2 = Object.assign({}, pageCfgs.persons);
  page2.data = cloneData(pageCfgs.persons);
  page2.setData = dottedSetData;
  try {
    const legacy = Object.assign({}, rawPerson);
    delete legacy.solar_time;
    page2._rawList = [legacy];
    page2.onOpenEdit({ currentTarget: { dataset: { id: 7 } } });
    page2._refreshForm();
    assert.equal(page2.data.solarOn, true, '缺失字段旧档案 → 默认开');
    assert.equal(page2._origSolar, true);
  } finally {
    global.wx = { getStorageSync: () => undefined };
  }
});

test('k11c 新增命主：开关默认开且 payload solar_time:1', async () => {
  const { bodies } = installWx();
  const page = Object.assign({}, pageCfgs.persons);
  page.data = cloneData(pageCfgs.persons);
  page.setData = dottedSetData;
  try {
    page.onAdd();
    assert.equal(page.data.solarOn, true, '新增 draft 默认开');
    page.setData({ dName: '新命', dDate: '1999-05-13', dCal: 'solar' });
    page._refreshForm();
    assert.equal(page.data.filled, true);
    page.onSave();
    await new Promise((r) => setTimeout(r, 60));
    assert.equal(bodies.length, 1);
    assert.equal(bodies[0].solar_time, 1, '新档案默认开必须随创建请求落库');
  } finally {
    global.wx = { getStorageSync: () => undefined };
  }
});
