// 易理明灯 — G3c：命书详情消费 lucky_is_reference（无八字幸运值为参考标注）
// 运行：cd miniprogram && node --test tests/reports_lucky_ref.test.js
// 覆盖：
//   - lucky_is_reference=true → detail.luckyIsReference=true（渲染「参考」标注）
//   - lucky_is_reference=false → 不标注（真实推算）
//   - 字段缺失/旧版接口 → 按 false 处理（契约兼容）
//   - closeDetail 重置标注状态
//   - reports.wxml 已挂接标注（wx:if 绑定存在）
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

global.getApp = () => ({ globalData: {}, loginPromise: null });

const api = require('../utils/api');

/* ── 页面配置捕获（与既有测试同款） ── */
const savedPage = global.Page;
function loadPage(rel) {
  let cfg = null;
  global.Page = (c) => { cfg = c; };
  try {
    require(rel);
  } finally {
    global.Page = savedPage;
  }
  assert.ok(cfg, `${rel} 页面配置应可加载`);
  return cfg;
}
const reportsCfg = loadPage('../pages/reports/reports');

/* ── setData 桩：模拟 wx 点路径赋值 ── */
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

function cloneData(obj) {
  return JSON.parse(JSON.stringify(obj || {}));
}

/* ── wx 桩（_openDetail 需要的 showLoading/hideLoading） ── */
global.wx = {
  showLoading: () => {},
  hideLoading: () => {},
  showToast: () => {},
  getWindowInfo: () => ({ statusBarHeight: 20 }),
};

function makePage() {
  const page = { data: cloneData(reportsCfg.data), setData: dottedSetData };
  page._openDetail = reportsCfg._openDetail;
  page.closeDetail = reportsCfg.closeDetail;
  return page;
}

test('lucky_is_reference=true → detail 标注参考（前端消费）', async () => {
  api.getReportDetail = async () => ({
    report: {
      scenarioLabel: '爱情',
      luckyColor: '绿色',
      luckyDirection: '东',
      luckyNumber: '3',
      lucky_is_reference: true,
      lucky_source: '暂无八字，以当日干支五行参考',
      fullContent: '…',
    },
  });
  const page = makePage();
  await page._openDetail('7');
  assert.equal(page.data.detail.show, true);
  assert.equal(page.data.detail.luckyIsReference, true, '参考值应标注');
  assert.equal(page.data.detail.luckySource, '暂无八字，以当日干支五行参考');
  assert.equal(page.data.detail.luckyColor, '绿色');
});

test('lucky_is_reference=false → 不标注（真实推算）', async () => {
  api.getReportDetail = async () => ({
    report: {
      luckyColor: '绿色',
      lucky_is_reference: false,
      lucky_source: '日主甲五行属木',
      fullContent: '…',
    },
  });
  const page = makePage();
  await page._openDetail('7');
  assert.equal(page.data.detail.luckyIsReference, false, '真实推算不标注');
  assert.equal(page.data.detail.luckySource, '日主甲五行属木');
});

test('字段缺失（旧版接口）→ 按 false 处理，不崩', async () => {
  api.getReportDetail = async () => ({ report: { luckyColor: '红色', fullContent: 'x' } });
  const page = makePage();
  await page._openDetail('7');
  assert.equal(page.data.detail.luckyIsReference, false);
});

test('closeDetail 重置参考标注状态', async () => {
  api.getReportDetail = async () => ({ report: { lucky_is_reference: true } });
  const page = makePage();
  await page._openDetail('7');
  assert.equal(page.data.detail.luckyIsReference, true);
  page.closeDetail();
  assert.equal(page.data.detail.luckyIsReference, false);
  assert.equal(page.data.detail.luckySource, '');
  assert.equal(page.data.detail.show, false);
});

test('reports.wxml 已挂接「参考」标注（luckyIsReference 绑定存在）', () => {
  const wxml = fs.readFileSync(
    path.join(__dirname, '..', 'pages', 'reports', 'reports.wxml'), 'utf8');
  assert.ok(wxml.includes('detail.luckyIsReference'), 'wxml 应有 luckyIsReference 绑定');
  assert.ok(wxml.includes('参考'), 'wxml 应渲染「参考」文案');
  // 标注绑定在幸运色/方向/数字之后（同一 meta 行内）
  const luckyIdx = wxml.indexOf('利数 · {{detail.luckyNumber}}');
  const refIdx = wxml.indexOf('detail.luckyIsReference');
  assert.ok(luckyIdx !== -1 && refIdx > luckyIdx, '标注应位于幸运项之后');
});
