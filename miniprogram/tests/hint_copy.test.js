// 易理明灯 batch2 C4 — 未建档提示条文案口径 node 单测
// 运行：node --test miniprogram/tests/hint_copy.test.js
// 覆盖（P4 拍板）：现状「排盘会保存到我的档案」但仅八字落库、6 类引擎不落库 →
//   提示条统一「会生成分析」口径，不得再承诺「保存/更准」。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const TODAY_WXML = path.join(__dirname, '../pages/today/today.wxml');

/* E1 未建档提示条标题行（today 页，本地无档案时显示；点击跳排盘） */
function noarchTipTitle() {
  const src = fs.readFileSync(TODAY_WXML, 'utf8');
  const m = src.match(/<view class="noarch-tip-t">([^<]+)<\/view>/);
  assert.ok(m, 'today.wxml 应存在 E1 未建档提示条标题行（class="noarch-tip-t"）');
  return m[1];
}

/* ── C4：提示条措辞（P4 拍板：统一「会生成分析」口径） ── */
test('未建档提示条标题：承诺「会生成…分析」，不得承诺「保存到档案/更准」', () => {
  const t = noarchTipTitle();
  assert.match(t, /会生成[^<]*分析/, '标题应含「会生成…分析」口径');
  assert.doesNotMatch(t, /保存|测算更准/, '标题不得再承诺保存/更准（6 类引擎不落库）');
});

test('未建档提示条标题：保留「排一次盘」行动引导', () => {
  const t = noarchTipTitle();
  assert.match(t, /排一次盘/, '标题应保留「排一次盘」行动引导');
});
