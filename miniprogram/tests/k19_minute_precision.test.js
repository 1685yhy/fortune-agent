// 易理明灯 k19 — 表单分钟级精度（10:55 场景）node 单测
// 运行：cd miniprogram && node --test tests/k19_minute_precision.test.js
// 背景：档案/排盘表单此前只能选 2 小时档时辰（代表整点 + 0 分），10:55 场景
//   （时辰边界 + 真太阳时修正可跨时辰）无法表达 → 新增「钟表时间」模式
//   （0-23 时 + 0-59 分）。readback 口径归一：birth_hour 只存在「代表整点
//   （奇数起点）」与「真实时钟小时」两形态，hourToShichenIndex 按时钟窗口
//   映射非代表整点值（修旧误读：10 → 戌时、12-22 → 子时）。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const persons = require('../utils/persons');

/* ═══════ 1. 工具映射（功能断言） ═══════ */

test('k19 hourToShichenIndex：代表整点查表不变（只知时辰旧行）', () => {
  assert.equal(persons.hourToShichenIndex(23), 0); // 子
  assert.equal(persons.hourToShichenIndex(1), 1);  // 丑
  assert.equal(persons.hourToShichenIndex(9), 5);  // 巳
  assert.equal(persons.hourToShichenIndex(11), 6); // 午
  assert.equal(persons.hourToShichenIndex(21), 11);// 亥
});

test('k19 hourToShichenIndex：真实时钟小时按时钟窗口（修旧误读）', () => {
  assert.equal(persons.hourToShichenIndex(10, 55), 5); // 10:55 → 巳（旧:戌）
  assert.equal(persons.hourToShichenIndex(12, 0), 6);  // 12:00 → 午（旧:子）
  assert.equal(persons.hourToShichenIndex(10, 0), 5);  // 10 点整 → 巳
  assert.equal(persons.hourToShichenIndex(22, 30), 11);// 亥
  assert.equal(persons.hourToShichenIndex(0, 30), 0);  // 0:30 → 子
  assert.equal(persons.hourToShichenIndex(5, 30), 3);  // 5:30 → 卯
  assert.equal(persons.hourToShichenIndex(13, 15), 7); // 未
});

test('k19 hourToShichenIndex：非法/空 → 子时兜底', () => {
  assert.equal(persons.hourToShichenIndex(''), 0);
  assert.equal(persons.hourToShichenIndex(null), 0);
  assert.equal(persons.hourToShichenIndex(-1), 0);
  assert.equal(persons.hourToShichenIndex(99), 0);
});

test('k19 shichenIndexFromClockHour 窗口映射', () => {
  assert.equal(persons.shichenIndexFromClockHour(23), 0);
  assert.equal(persons.shichenIndexFromClockHour(0), 0);
  assert.equal(persons.shichenIndexFromClockHour(2), 1);
  assert.equal(persons.shichenIndexFromClockHour(10), 5);
  assert.equal(persons.shichenIndexFromClockHour(12), 6);
  assert.equal(persons.shichenIndexFromClockHour(24), 0); // 非法
});

test('k19 timeText/birthBrief：分钟>0 附钟表时间；代表整点不变', () => {
  assert.equal(persons.timeText({ birth_hour: 9, birth_minute: 0 }), '巳时');
  assert.equal(persons.timeText({ birth_hour: 10, birth_minute: 55 }), '巳时 10:55');
  assert.equal(persons.timeText({ birth_hour: 23, birth_minute: 30 }), '子时 23:30');
  assert.equal(persons.timeText({}), '');
  assert.equal(persons.timeText({ birth_hour: 9, birth_minute: 55 }), '巳时 9:55');
  assert.ok(persons.birthBrief({
    birth_year: 1999, birth_month: 3, birth_day: 28,
    birth_hour: 10, birth_minute: 55, gender: '女', city: '上海',
  }).includes('巳时 10:55'));
});

test('k19 常量列就绪（0-23 时 / 0-59 分）', () => {
  assert.equal(persons.HOUR24.length, 24);
  assert.equal(persons.HOUR24[10], '10时');
  assert.equal(persons.MINUTE60.length, 60);
  assert.equal(persons.MINUTE60[55], '55分');
  assert.equal(persons.MINUTE60[0], '0分');
});

/* ═══════ 2. persons 档案页接线（源码断言） ═══════ */

const PERSONS_JS = path.join(__dirname, '../pages/persons/persons.js');
const PERSONS_WXML = path.join(__dirname, '../pages/persons/persons.wxml');

test('k19 persons.js：钟表模式 payload（时钟小时+分钟）+ 联动清除', () => {
  const js = fs.readFileSync(PERSONS_JS, 'utf8');
  assert.match(js, /const clockMode = d\.dClockMode && d\.dClockH >= 0;/,
    '钟表模式判定必须存在');
  assert.match(js, /birth_hour: clockMode \? d\.dClockH[\s\S]{0,80}persons\.shichenIndexToHour\(d\.dHourIndex\)/,
    '钟表模式 birth_hour=时钟小时，否则时辰代表整点');
  assert.match(js, /birth_minute: clockMode \? d\.dClockM : 0,/,
    '钟表模式 birth_minute=dClockM，否则 0');
  assert.match(js, /onHourChange[\s\S]{0,160}dClockMode: false/,
    '手选时辰 chips → 退出钟表模式');
  assert.match(js, /onClockHourChange\(e\)[\s\S]{0,200}shichenIndexFromClockHour\(h\)/,
    '选时 → 时辰 chips 联动');
  assert.match(js, /dClockMode: rawClockRow,/,
    '编辑回显：精确钟表行进入钟表模式');
});

test('k19 persons.wxml：钟表模式 UI 接线', () => {
  const wxml = fs.readFileSync(PERSONS_WXML, 'utf8');
  assert.match(wxml, /bindtap="onClockModeToggle"/);
  assert.match(wxml, /bindchange="onClockHourChange"/);
  assert.match(wxml, /bindchange="onClockMinuteChange"/);
  assert.match(wxml, /clockHourLabels\[dClockH\]/);
  assert.match(wxml, /clockMinuteLabels\[dClockM\]/);
});

/* ═══════ 3. paipan 排盘页接线 ═══════ */

const PAIPAN_JS = path.join(__dirname, '../pages/paipan/paipan.js');
const PAIPAN_WXML = path.join(__dirname, '../pages/paipan/paipan.wxml');
const API_JS = path.join(__dirname, '../utils/api.js');

test('k19 paipan.js：payload 时钟模式带 birthClock/minute', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /birthHour: clockMode \? this\.data\.bClockHIdx : this\.data\.bHourIdx - 1,/,
    '钟表模式 birthHour=时钟小时（0-23 直通）');
  assert.match(js, /minute: clockMode \? this\.data\.bClockMIdx : 0,/);
  assert.match(js, /birthClock: clockMode,/);
  assert.match(js, /onClockToggle[\s\S]{0,400}shichenIndexFromClockHour/,
    'toggle/选时联动时辰');
  assert.match(js, /bClockSet: true,/);
});

test('k19 paipan.js：档案预填精确钟表行 → 钟表模式回显', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /const isClockRow = parseInt\(p\.birth_minute, 10\) > 0[\s\S]{0,160}HOUR_VALUES\.indexOf/,
    'minute>0 或非代表整点 → 钟表行');
  assert.match(js, /patch\.bClockHIdx = parseInt\(p\.birth_hour, 10\) \|\| 0;/);
});

test('k19 paipan.wxml：钟表 UI 接线', () => {
  const wxml = fs.readFileSync(PAIPAN_WXML, 'utf8');
  assert.match(wxml, /bindtap="onClockToggle"/);
  assert.match(wxml, /bindchange="onClockHourChange"/);
  assert.match(wxml, /bindchange="onClockMinuteChange"/);
});

test('k19 api.js：birthClock nullish 透传（旧调用方不带=语义不变）', () => {
  const src = fs.readFileSync(API_JS, 'utf8');
  assert.match(src, /if \(p\.birthClock != null\) data\.birthClock = !!p\.birthClock;/,
    'birthClock 必须 nullish 守卫透传');
});
