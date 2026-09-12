// 易理明灯 k37 接缝收口 — 小程序侧两条（S2 名人盘 wx:key / S3 hehun 三开关透传）node 单测
// 运行：cd miniprogram && node --test tests/k37_seams.test.js
// S2：mingren_detail.wxml 事件列表 wx:key="year" → 同一年多条事件（忽必烈 1259×2、
//   朱元璋 1370×2 等）触发重复键告警；后端 `_build_timeline`（k32 A15）已给每组
//   事件补组内唯一 idx → 前端改用 wx:key="idx"（既有口径：zeri_plan.wxml 同款）。
// S3：api.js `_toBackendBazi`（POST /api/hehun 载荷构造）此前未带三开关
//   （daylightSaving/lateChildHour/solarTime）→ 调用方传了也被静默丢弃。
//   沿用 k19/k29 既有口径：nullish 守卫透传（未传=不带参 → 服务端 BaziInput
//   默认值兜底），不发明新字段、不给既有调用方引入行为变化。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ---- 全局环境（小程序运行时内置） ----
global.getApp = () => ({ globalData: {}, loginPromise: null });
global.wx = { getStorageSync: () => undefined }; // 页面模块 require 兜底

const api = require('../utils/api');

const API_JS = path.join(__dirname, '../utils/api.js');
const MINGREN_WXML = path.join(__dirname, '../pages/mingren_detail/mingren_detail.wxml');

/* ═══════ S2. 名人命盘：事件列表 wx:key ═══════ */

test('k37 S2：mingren_detail 事件列表 wx:key="idx"（组内唯一键，修同年重复键告警）', () => {
  const wxml = fs.readFileSync(MINGREN_WXML, 'utf8');
  const loop = wxml.match(/<view class="mrd-dy-item"[^>]*>/);
  assert.ok(loop, '事件列表行（mrd-dy-item）必须存在');
  assert.match(loop[0], /wx:for="\{\{g\.events\}\}"/, '遍历的仍是当前大运组的事件');
  assert.match(loop[0], /wx:key="idx"/, 'wx:key 必须是后端给的组内唯一键 idx');
  assert.doesNotMatch(loop[0], /wx:key="year"/, 'year 可重复（同一年多条事件）→ 不得再用作 key');
  assert.match(loop[0], /wx:for-item="ev"/, '循环变量名不变（ev.year/ev.event 渲染口径零变化）');
  assert.equal((wxml.match(/wx:key="year"/g) || []).length, 0,
    '本页不得残留 wx:key="year"（旧重复键写法）');
  // 渲染字段不变：year/event 仍按原样展示
  assert.match(wxml, /\{\{ev\.year\}\}/);
  assert.match(wxml, /\{\{ev\.event\}\}/);
});

/* ═══════ S3. hehun 载荷三开关透传 ═══════ */

test('k37 S3：_toBackendBazi 三开关 nullish 守卫透传（沿用 k19/k29 口径）', () => {
  const src = fs.readFileSync(API_JS, 'utf8');
  assert.match(src, /if \(src\.solarTime != null\) out\.solarTime = !!src\.solarTime;/,
    '真太阳时：显式 false 能带出，未传字段保持不带参（服务端默认 True 兜底）');
  assert.match(src, /if \(src\.lateChildHour != null\) out\.lateChildHour = !!src\.lateChildHour;/,
    '晚子时专业档：同款 nullish 守卫');
  assert.match(src, /if \(src\.daylightSaving != null\) out\.daylightSaving = !!src\.daylightSaving;/,
    '夏令时：同款 nullish 守卫');
  assert.match(src, /hour: _shichenHour\(src\.birthHour\)/, '白名单其余字段不变（hour 映射口径零变化）');
});

// 预置 baseURL 缓存（ylm_baseurl 当天）→ 探活走缓存零定时器；请求只回 /api/hehun。
const BASEURL_KEY = 'ylm_baseurl';
const MIN_HEHUN = { total_score: 88 };

function captureHehun(bodies) {
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
      if (url.indexOf('/api/hehun') !== -1) {
        bodies.push(Object.assign({}, opt.data));
        opt.success && opt.success({ statusCode: 200, data: MIN_HEHUN });
        return;
      }
      opt.fail && opt.fail({ errMsg: 'unexpected route: ' + url });
    },
  };
}

const PERSON = {
  birthYear: 1990, birthMonth: 5, birthDay: 20, birthHour: 8,
  gender: 'male', city: '北京',
};

async function runHehun(personPatch) {
  const bodies = [];
  const oldWx = global.wx;
  captureHehun(bodies);
  try {
    await api.hehun({
      person1: Object.assign({}, PERSON, personPatch),
      person2: Object.assign({}, PERSON, personPatch),
    });
  } finally {
    global.wx = oldWx;
  }
  return bodies;
}

test('k37 S3：hehun() 显式关三开关 → 请求体三字段均为 false（此前被静默丢弃）', async () => {
  const bodies = await runHehun({ solarTime: false, lateChildHour: false, daylightSaving: false });
  assert.equal(bodies.length, 1, '应恰好发出 1 次 /api/hehun');
  assert.equal(bodies[0].person_a.solarTime, false, '真太阳时关必须透传（服务端默认 True，不带=恒修正盘）');
  assert.equal(bodies[0].person_b.solarTime, false, '双方同款透传');
  assert.equal(bodies[0].person_a.lateChildHour, false);
  assert.equal(bodies[0].person_a.daylightSaving, false);
});

test('k37 S3：hehun() 显式开 → 请求体 true；未传 → 不带参（服务端默认兜底）', async () => {
  const on = await runHehun({ solarTime: true, lateChildHour: true, daylightSaving: true });
  assert.equal(on[0].person_a.solarTime, true);
  assert.equal(on[0].person_a.lateChildHour, true);
  assert.equal(on[0].person_a.daylightSaving, true);

  const none = await runHehun({});
  assert.ok(!('solarTime' in none[0].person_a), '未传 solarTime 不得凭空带参（服务端默认 True）');
  assert.ok(!('lateChildHour' in none[0].person_a), '未传 lateChildHour 不得凭空带参（服务端默认 False）');
  assert.ok(!('daylightSaving' in none[0].person_a), '未传 daylightSaving 不得凭空带参（服务端默认 False）');
  assert.deepEqual(Object.keys(none[0].person_a).sort(),
    ['city', 'day', 'gender', 'hour', 'minute', 'month', 'year'],
    '白名单其余字段与修复前完全一致（零行为变化）');
});
