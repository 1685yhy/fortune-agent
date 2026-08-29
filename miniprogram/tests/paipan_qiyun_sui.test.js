// 易理明灯 G5 — 排盘页起运区显示问真实岁串「X岁X个月起运」（实岁串为主、分解串为副）
// 运行：cd miniprogram && node --test tests/paipan_qiyun_sui.test.js
// 覆盖（task-G5-brief 测试要求 6）：
//   1. paipan.js 起运区读取 qiyun_sui_desc（新字段）并作主显示
//   2. 实岁串主显示 + 分解串（qiyun_desc）为副的文案结构
//   3. 旧记录（无 qiyun_sui_desc）回落虚岁口径不崩
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const PAIPAN_JS = path.join(__dirname, '../pages/paipan/paipan.js');
const PAIPAN_WXML = path.join(__dirname, '../pages/paipan/paipan.wxml');

test('G5 起运区：读取服务端新字段 qiyun_sui_desc（实岁串数据源）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  const m = js.match(/const qiyunSuiDesc = c\.qiyun_sui_desc \|\| ''/);
  assert.ok(m, 'paipan.js 应读取 c.qiyun_sui_desc（serialize_bazi 新字段）');
});

test('G5 起运区：实岁串为主显示（「X岁X个月」起运文案，含替换尾缀「起运」）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /qiyunSuiDesc\.replace\(\/起运\$\/, ''\)/, '实岁串应去尾「起运」后加粗展示');
  assert.match(js, /<b>\$\{qiyunSuiDesc\.replace/, '起运 chip val 应以实岁串加粗为主显示');
});

test('G5 起运区：分解串（qiyun_desc）保留为副显示', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  const m = js.match(/const qiyunDesc = c\.qiyun_desc \|\| ''/);
  assert.ok(m, 'paipan.js 应读取 qiyun_desc（分解串）');
  assert.match(js, /qiyunVal \+= `<br\/>\$\{qiyunDesc\}`/, '分解串应换行附加为副显示');
});

test('G5 起运区：旧记录回落虚岁口径（无 qiyun_sui_desc 不崩）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /: `<b>\$\{qiyunSui\}<\/b> 岁起运`/, '无实岁串时回落「N 岁起运」虚岁口径');
  assert.match(js, /qiyunSuiDesc\s*\?/, '三态：有实岁串 → 实岁；无 → 虚岁回落');
});

test('G5 起运区：slChips 起运 chip 绑定 qiyunVal（wxml 经 rich-text 渲染 val）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /\{ seal: '起', name: '起运', val: qiyunVal, gold: false \}/,
               '起运 chip 应使用 qiyunVal（实岁串主显示 + 分解串副显示）');
  const wxml = fs.readFileSync(PAIPAN_WXML, 'utf8');
  assert.match(wxml, /wx:for="\{\{slChips\}\}"[\s\S]*?rich-text class="sl-val" nodes="\{\{c\.val\}\}"/,
               'wxml 起运区经 rich-text 渲染 chip.val（既有绑定，含新起运文案）');
});
