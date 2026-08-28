// 易理明灯 — 抽灵签页 B5-1 三签种选择器（批次 5 K）
// 运行：node --test tests/qian_kinds.test.js（miniprogram 目录下）
// 覆盖：签种选择器 onKindTap 守卫（同种/摇签中不切，切换复位待抽态）；
//       onShake 携带当前 kind 调 drawQian、签卡注入 kindName；
//       onSave 携带 {no, kind} 保存；签号 1-100 中文竖排签字映射
//       （mock 时钟推进 1.6s 摇签动效，全链路验证）。
const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../utils/api');

const savedPage = global.Page;
const savedWx = global.wx;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/qian/qian');
} finally {
  global.Page = savedPage;
  global.wx = savedWx;
}
assert.ok(pageCfg && typeof pageCfg.onKindTap === 'function', 'qian.js 页面配置应可加载');
assert.deepEqual(pageCfg.data.kinds.map((k) => k.id),
  ['original', 'guanyin', 'guandi', 'xuanwushan'], '签种选择器恰 4 项');
assert.equal(pageCfg.data.kind, 'original', '缺省签种 original（老客户端行为不变）');

function makePage() {
  global.wx = {
    getWindowInfo: () => ({ statusBarHeight: 20 }),
    getSystemInfoSync: () => ({ statusBarHeight: 20 }),
    onThemeChange: () => {},
    showToast: () => {},
    getStorageSync: () => '',
    setStorageSync: () => {},
  };
  const page = Object.assign({}, pageCfg);
  page.data = JSON.parse(JSON.stringify(pageCfg.data));
  page.setData = function (upd) { Object.assign(this.data, upd); };
  return page;
}

const CARD = { no: 3, jx: '中平签', cls: 'mid', poem: ['一', '二', '三', '四'], jie: '解', suo: '求' };

test('onKindTap：切换签种并复位待抽态；同种/摇签中不切', () => {
  const page = makePage();
  const ev = (id) => ({ currentTarget: { dataset: { kind: id } } });
  page.setData({ stage: 'done', card: { no: 7 }, raisedIdx: 2 });
  page.onKindTap(ev('guanyin'));
  assert.equal(page.data.kind, 'guanyin');
  assert.equal(page.data.stage, 'idle');      // 已摇出 → 复位待抽
  assert.equal(page.data.card, null);
  assert.equal(page.data.raisedIdx, -1);
  page.onKindTap(ev('guanyin'));              // 同种点击不重置
  assert.equal(page.data.kind, 'guanyin');
  page.setData({ stage: 'shaking' });         // 摇签动效中不可切
  page.onKindTap(ev('guandi'));
  assert.equal(page.data.kind, 'guanyin');
});

test('onShake：携带当前 kind 调 drawQian，签卡注入 kindName', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  t.mock.method(api, 'drawQian', () => Promise.resolve({ card: CARD }));
  const page = makePage();
  page.setData({ kind: 'xuanwushan' });
  page.onShake();
  assert.equal(page.data.stage, 'shaking');
  await Promise.resolve();                    // 微任务：drawQian resolve → 注册假定时器
  await Promise.resolve();
  assert.equal(api.drawQian.mock.calls.length, 1);
  assert.equal(api.drawQian.mock.calls[0].arguments[0], 'xuanwushan', 'drawQian 携带当前签种');
  t.mock.timers.tick(1600);                   // 摇签动效完成 → 揭签卡
  assert.equal(page.data.card.kindName, '玄武山签');
  assert.equal(page.data.card.level, 4);      // 中平签 → lv4 徽标
});

test('onShake：签号 1-100 竖排签字映射（mock 时钟推进 1.6s 动效）', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const cases = {
    1: '一', 10: '十', 11: '十一', 20: '二十', 51: '五十一', 99: '九十九', 100: '一百',
  };
  for (const [no, cn] of Object.entries(cases)) {
    t.mock.method(api, 'drawQian', () =>
      Promise.resolve({ card: { ...CARD, no: Number(no) } }));
    const page = makePage();
    page.onShake();
    await Promise.resolve();                    // 微任务：drawQian resolve → 注册假定时器
    await Promise.resolve();
    t.mock.timers.tick(1600);                   // 摇签动效完成 → 揭签卡
    assert.equal(page.data.stage, 'done', `no=${no} 应揭卡`);
    assert.deepEqual(page.data.card.noChars, ['第', ...cn.split(''), '签'], `no=${no} 竖排签字`);
    assert.equal(page.data.card.kindName, '灵签原版');
  }
});

test('onSave：携带 {no, kind} 保存；无签卡不触发', async (t) => {
  t.mock.method(api, 'saveQian', () => Promise.resolve({ saved: true, already: false }));
  const page = makePage();
  page.onSave();                                // card=null → 早退不发请求
  assert.equal(api.saveQian.mock.calls.length, 0);
  page.setData({ kind: 'guandi', card: { ...CARD, no: 42 } });
  page.onSave();
  assert.equal(page.data.saving, true);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(api.saveQian.mock.calls.length, 1);
  assert.deepEqual(api.saveQian.mock.calls[0].arguments[0], { no: 42, kind: 'guandi' });
  assert.equal(page.data.saving, false);
});
