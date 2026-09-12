// 易理明灯 k39 S3 — 合盘页「单档补全」来源标识与手填覆盖（node 单测）
// 运行：cd miniprogram && node --test tests/k39_hehun_archive_label.test.js
// 依据：k39 brief S3 —— 合盘一方缺信息时允许用默认命主档案补全，硬要求
//   ① 显式标注来源（「本人（来自档案）」；服务端同一串
//      src/bot/handler.py MessageHandler.HEHUN_SELF_FROM_ARCHIVE_LABEL）
//   ② 可手动覆盖（改任一项 → 标注消失，以手填为准）
//   ③ 服务端/前端口径一致（同一标注文案，不两套）
// 覆盖：
//   1. 页面态含来源标识字段（默认 false）
//   2. 档案补全（默认命主预填 / 从档案选择）→ 打来源标识
//   3. 手动改日期/时辰/出生地/性别 → 清来源标识（覆盖生效）
//   4. 「重新输入」清空双方时一并清标识
//   5. wxml 标注串与页面态绑定（文案 = 服务端同一串）
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

global.getApp = () => ({ globalData: {} });
global.wx = { getStorageSync: () => undefined, showToast: () => {} };

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
const LABEL = '本人（来自档案）';

/** 最小页面实例：只捕获 setData 载荷（方法与页面态解耦，不需要 wx 运行时）。*/
function fakePage() {
  return { data: {}, calls: [], setData(o) { this.calls.push(o); Object.assign(this.data, o); } };
}

const PERSON = {
  birth_year: 1990, birth_month: 5, birth_day: 20, birth_hour: 15,
  gender: 'male', calendar: 'solar', city: '北京', solar_time: 1,
};

test('k39 S3 页面态：p1/p2 来源标识字段存在且默认 false', () => {
  const js = fs.readFileSync(HEHUN_JS, 'utf8');
  assert.match(js, /p1FromArchive: false,/, 'p1 来源标识默认 false');
  assert.match(js, /p2FromArchive: false,/, 'p2 来源标识默认 false');
});

test('k39 S3 档案补全 → 本人打来源标识（默认命主预填与档案直选同路）', () => {
  const p = fakePage();
  hehunCfg._fillFromPerson.call(p, 'p1', PERSON);
  assert.equal(p.data.p1FromArchive, true, '档案补全后应打「本人（来自档案）」标识');
  assert.equal(p.data.p1Date, '1990-05-20');
  // 我方档案 = 默认命主预填（loadDefaultSelf）与「从档案选择」共用 _fillFromPerson
  const js = fs.readFileSync(HEHUN_JS, 'utf8');
  assert.match(js, /loadDefaultSelf\(\)[\s\S]{0,400}_fillFromPerson\('p1', def\)/,
    '默认命主预填走 _fillFromPerson（与档案直选同一实现）');
});

test('k39 S3 可手动覆盖：改日期/时辰/出生地/性别均清来源标识', () => {
  for (const [method, ev] of [
    ['onP1DateChange', { detail: { calendar: 'solar', date: '1991-01-01' } }],
    ['onP1HourChange', { detail: { value: '3' } }],
    ['onP1CityChange', { detail: { full: '上海' } }],
    ['onP1GenderTap', { currentTarget: { dataset: { gender: 'female' } } }],
  ]) {
    const p = fakePage();
    p.data.p1FromArchive = true;
    hehunCfg[method].call(p, ev);
    assert.equal(p.data.p1FromArchive, false,
      `${method} 后必须清来源标识（覆盖后以手填为准）`);
  }
  const js = fs.readFileSync(HEHUN_JS, 'utf8');
  for (const m of ['onP1DateChange', 'onP1HourChange', 'onP1CityChange',
    'onP1GenderTap', 'onP2DateChange', 'onP2HourChange', 'onP2CityChange',
    'onP2GenderTap']) {
    assert.ok(new RegExp(`${m}\\(e\\)[\\s\\S]{0,200}FromArchive: false`).test(js),
      `${m} 应清对应来源标识`);
  }
});

test('k39 S3 p2 档案补全同样打标识；重新输入清双方标识', () => {
  const p = fakePage();
  hehunCfg._fillFromPerson.call(p, 'p2', PERSON);
  assert.equal(p.data.p2FromArchive, true);
  const q = fakePage();
  hehunCfg.onReInput.call(q);
  assert.equal(q.data.p1FromArchive, false);
  assert.equal(q.data.p2FromArchive, false);
});

test('k39 S3 wxml：标注串与服务端同一文案，绑页面态', () => {
  const wxml = fs.readFileSync(HEHUN_WXML, 'utf8');
  assert.ok(wxml.includes(LABEL), `wxml 应含标注串「${LABEL}」`);
  assert.match(wxml, /wx:if="\{\{p1FromArchive\}\}"[\s\S]{0,80}本人（来自档案）/,
    'p1 标注应绑 p1FromArchive');
  assert.match(wxml, /wx:if="\{\{p2FromArchive\}\}"[\s\S]{0,80}TA（来自档案）/,
    'p2 标注应绑 p2FromArchive（同一来源口径）');
  // 复用既有来源标签样式（不新造样式族）
  assert.match(wxml, /class="hh-cache-tag">本人（来自档案）</,
    '沿用 hh-cache-tag（与「上次记录」同款标签样式）');
});

test('k39 S3 服务端同一文案（跨端口径一致）', () => {
  const server = fs.readFileSync(
    path.join(__dirname, '../../src/bot/handler.py'), 'utf8');
  assert.ok(server.includes(`HEHUN_SELF_FROM_ARCHIVE_LABEL = "${LABEL}"`),
    `服务端常量必须逐字等于「${LABEL}」（不两套）`);
});
