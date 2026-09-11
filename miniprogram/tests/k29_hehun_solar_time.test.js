// 易理明灯 k29 — 合盘页（hehun）真太阳时接线 node 单测
// 运行：cd miniprogram && node --test tests/k29_hehun_solar_time.test.js
// 依据：k19 计划 §5 遗留④ —— 档案级真太阳时开关（persons.solar_time）已接入
//       chat 主链与 paipan 页，合盘页漏网（无开关、无档案回显、载荷不带
//       solarTime → REST 走服务端默认「开」），档案显式关了的用户在合盘页
//       仍得到修正后的盘。
// 覆盖：
//   1. 接线源码断言：页级 switch / 档案回显口径 p.solar_time !== 0 / 载荷透传
//      solarTime（交互与 paipan 页 k11c 同款，不发明新交互）
//   2. 档案 solar_time=0 → 开关关，双方载荷 solarTime:false
//   3. 旧档案缺 solar_time → 默认开；手动输入路径不依赖档案
//   4. onSubmit 端到端：POST /api/union body 的 person1/person2 均带 solarTime
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ---- 全局环境（小程序运行时内置） ----
global.getApp = () => ({ globalData: {} });
global.wx = { getStorageSync: () => undefined };

const savedPage = global.Page;
let hehunCfg = null;
global.Page = (c) => { hehunCfg = c; };
try {
  require('../pages/hehun/hehun');
} finally {
  global.Page = savedPage;
}
assert.ok(hehunCfg, 'hehun 页面配置应可加载');

const HEHUN_JS = path.join(__dirname, '../pages/hehun/hehun.js');
const HEHUN_WXML = path.join(__dirname, '../pages/hehun/hehun.wxml');
const HEHUN_WXSS = path.join(__dirname, '../pages/hehun/hehun.wxss');
const PAIPAN_WXML = path.join(__dirname, '../pages/paipan/paipan.wxml');

/* ── 1. 接线源码断言 ── */

test('k29 hehun 页：页级开关默认开 + bindchange 写入页面态 + 档案回显口径', () => {
  const js = fs.readFileSync(HEHUN_JS, 'utf8');
  assert.match(js, /solarOn: true,/, '页级开关默认开（产品口径 R2-4：默认真太阳时修正）');
  assert.match(js, /onSolarTimeChange\(e\)\s*\{\s*this\.setData\(\{ solarOn: !!e\.detail\.value \}\)/,
    'switch bindchange → onSolarTimeChange → setData solarOn（与 paipan 同款）');
  assert.match(js, /solarOn: p\.solar_time !== 0,/,
    '档案回显：solar_time=0 → 关；缺失/旧档案 → 默认开（与 paipan _fillFromPerson 同口径）');
  assert.match(js, /p\.solarTime = !!d\.solarOn;/,
    '提交载荷透传 solarTime（页级开关 → 双方）');
});

test('k29 hehun wxml/wxss：开关行沿用 paipan 交互与墨韵 tokens（不新造样式族）', () => {
  const wxml = fs.readFileSync(HEHUN_WXML, 'utf8');
  assert.match(wxml, /<switch checked="\{\{solarOn\}\}" bindchange="onSolarTimeChange" color="#A93A2C"/,
    '开关必须 checked 绑定 solarOn + bindchange onSolarTimeChange + 朱砂 #A93A2C');
  assert.match(wxml, /真太阳时/, '标题「真太阳时」');
  assert.match(wxml, /按出生地经度校准时辰/, '说明行沿用 paipan 文案族');
  const wxss = fs.readFileSync(HEHUN_WXSS, 'utf8');
  assert.match(wxss, /\.solar-row \{ display: flex; align-items: center; gap: 24rpx; \}/,
    '开关行样式与 paipan 逐字一致');
  assert.match(wxss, /\.solar-t \{ font-family: var\(--font-song\)/,
    '标题用既有墨韵 font-song token');
  // 参照实现仍在校：paipan 同款 switch（防两边交互漂移）
  const paipan = fs.readFileSync(PAIPAN_WXML, 'utf8');
  assert.match(paipan, /<switch checked="\{\{bSolarTime\}\}" bindchange="onSolarTimeChange" color="#A93A2C"/,
    'paipan 参照实现（同一交互模式）');
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

function _newPage() {
  const page = Object.assign({}, hehunCfg);
  page.data = JSON.parse(JSON.stringify(hehunCfg.data || {}));
  page.setData = dottedSetData;
  return page;
}

// 预置 baseURL 缓存（ylm_baseurl 当天）→ 探活走缓存零定时器；union 路由回最小结果
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
    navigateTo: () => {},
    pageScrollTo: () => {},
    request: (opt) => {
      const url = String(opt.url || '');
      const m = String(opt.method || 'GET').toUpperCase();
      if (url === 'http://mock-base/api/union' && m === 'POST') {
        bodies.push(Object.assign({}, opt.data));
        opt.success && opt.success({
          statusCode: 200,
          data: {
            score: 78, levelLabel: '情投意合', levelSublabel: '', relation: '恋人',
            dimensions: {
              wuxing: { score: 30, max: 40 },
              shengxiao: { score: 20, max: 30 },
              rizhu: { score: 28, max: 30 },
            },
            features: [], quote: '缘起相守', quoteParts: { main: '缘起', suffix: '相守' },
            yuan_card: {}, paywall: {}, transient: true,
          },
        });
        return;
      }
      opt.fail && opt.fail({ errMsg: 'unexpected route: ' + url });
    },
  };
  return { toasts, bodies };
}

const rawPersonOff = {
  id: 7, name: '小晚', relation: '自己', is_default: true,
  gender: '男', birth_year: 1999, birth_month: 5, birth_day: 13,
  birth_hour: 10, birth_minute: 55, calendar: 'solar', city: '长春',
  solar_time: 0,   // k11c：档案关
};

test('k29 hehun 页：档案 solar_time=0 回显关 → 双方载荷 solarTime:false；开回 true', () => {
  installWx();
  const page = _newPage();
  page._fillFromPerson('p1', Object.assign({}, rawPersonOff));
  assert.equal(page.data.solarOn, false, '档案 solar_time=0 必须回显关');
  assert.equal(page.data.p1Date, '1999-05-13', '档案回填不回归');
  assert.equal(page._buildPerson('p1').solarTime, false, '档案口径随载荷透传（关）');
  assert.equal(page._buildPerson('p2').solarTime, false, '页级开关作用于双方排盘');

  page.onSolarTimeChange({ detail: { value: true } });
  assert.equal(page.data.solarOn, true);
  const pl = page._buildPayload(false);
  assert.equal(pl.person1.solarTime, true);
  assert.equal(pl.person2.solarTime, true);
  assert.equal(pl.paid, false);
});

test('k29 hehun 页：旧档案缺 solar_time → 默认开；手动输入不依赖档案且选填字段形态不变', () => {
  installWx();
  const page = _newPage();
  const legacy = Object.assign({}, rawPersonOff);
  delete legacy.solar_time;
  page._fillFromPerson('p2', legacy);          // 档案直选 TA（旧档案）
  assert.equal(page.data.solarOn, true, '缺失 solar_time 的旧档案 → 默认开');
  assert.equal(page._buildPerson('p2').solarTime, true);

  // 非档案手动输入路径：默认态开；关掉后同样透传（页级口径与档案解耦）
  const manual = _newPage();
  assert.equal(manual.data.solarOn, true, '手动填表 → 会话级默认开');
  assert.equal(manual._buildPerson('p1').solarTime, true);
  manual.setData({ p1Date: '1999-05-13' });
  manual.onSolarTimeChange({ detail: { value: false } });
  const built = manual._buildPerson('p1');
  assert.equal(built.solarTime, false);
  assert.equal(built.birthYear, 1999);
  assert.ok(!('birthHour' in built) && !('city' in built),
    '选填字段未填时不携带（开关不改变载荷形态）');
});

test('k29 hehun 页：onSubmit 端到端 —— POST /api/union 双方均带 solarTime（档案关）', async () => {
  const { bodies } = installWx();
  const page = _newPage();
  page._fillFromPerson('p1', Object.assign({}, rawPersonOff));   // 我方档案关
  page.setData({ p2Date: '1998-03-02' });                        // TA 手填
  await page.onSubmit();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(bodies.length, 1, '应恰好 1 次 POST /api/union');
  assert.equal(bodies[0].person1.solarTime, false,
    '我方：档案关 → 载荷 solarTime:false（旧代码不带字段 → 服务端默认开，口径分裂）');
  assert.equal(bodies[0].person2.solarTime, false, 'TA：同一页级开关');
  assert.equal(bodies[0].person1.birthYear, 1999);
  assert.equal(bodies[0].person2.birthYear, 1998);
  assert.equal(page.data.submitted, true, '免费档结果正常渲染');
});
