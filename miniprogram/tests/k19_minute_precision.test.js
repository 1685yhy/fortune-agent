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

/* k77-M5「宁少不假」：`timeText` 是 5 个渲染出口的唯一公因子
   （persons.js 列表页 / bazi.js 三处 / onboarding.js 导览卡 都经 birthBrief·birthSummary），
   故在根上钉住：脏/越界 hour ⇒ **不显示时辰**（绝不回落 0=子时）。 */
test('k77-M5：脏/越界 birth_hour ⇒ timeText 不显示时辰（5 个渲染出口同时受保护）', () => {
  const BAD = ['abc', '0x10', '0x0', '1e1', '0b101', '0o17', '+10', [10], 99, -1, 24,
    NaN, Infinity, '10abc', {}, true];
  BAD.forEach((h) => {
    const p = { birth_year: 1999, birth_month: 3, birth_day: 28, birth_hour: h,
      birth_minute: 0, gender: '女', city: '上海', calendar: 'solar' };
    assert.equal(persons.timeText(p), '',
      `birth_hour=${JSON.stringify(h)} 解析不出来 ⇒ 不得显示时辰（改前会显示"子时"）`);
    assert.ok(persons.birthBrief(p).indexOf('子时') === -1,
      `birthBrief 不得因脏值出现"子时"：${persons.birthBrief(p)}`);
    assert.ok(persons.birthSummary(p).indexOf('子时') === -1,
      `birthSummary 不得因脏值出现"子时"：${persons.birthSummary(p)}`);
  });
  // 反向：规范值照旧（不误伤真档案）
  assert.equal(persons.timeText({ birth_hour: 9, birth_minute: 0 }), '巳时');
  assert.equal(persons.timeText({ birth_hour: '9', birth_minute: 0 }), '巳时');
  assert.equal(persons.timeText({ birth_hour: 0, birth_minute: 0 }), '子时');
  assert.equal(persons.timeText({ birth_hour: 10, birth_minute: 55 }), '巳时 10:55');
  // 精确钟表行的钟点取**解析后的数值**（不回显原始脏串）
  assert.equal(persons.timeText({ birth_hour: ' 10 ', birth_minute: 55 }), '巳时 10:55');
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
  /* k77 重钉：本条的原始正则钉在 `birth_hour: clockMode ? …`（对象字面量写法）上，
     M-5 把 birth_hour/birth_minute 改成**条件写入**（见下一条行为型用例的代码依据：
     时辰解析不出来时**不写回**，交由服务端合并保留既有值）。
     语义未变（钟表模式=时钟小时+分钟；时辰档=代表整点+0 分），故按新写法重钉；
     另加行为型断言真跑 `_payload()`（不再是"看源码里像不像"）。 */
  const js = fs.readFileSync(PERSONS_JS, 'utf8');
  assert.match(js, /const clockMode = d\.dClockMode && d\.dClockH >= 0;/,
    '钟表模式判定必须存在');
  assert.match(js, /payload\.birth_hour = clockMode \? d\.dClockH[\s\S]{0,80}persons\.shichenIndexToHour\(d\.dHourIndex\)/,
    '钟表模式 birth_hour=时钟小时，否则时辰代表整点');
  assert.match(js, /payload\.birth_minute = clockMode \? d\.dClockM : 0;/,
    '钟表模式 birth_minute=dClockM，否则 0');
  assert.match(js, /onHourChange[\s\S]{0,160}dClockMode: false/,
    '手选时辰 chips → 退出钟表模式');
  assert.match(js, /onClockHourChange\(e\)[\s\S]{0,200}shichenIndexFromClockHour\(h\)/,
    '选时 → 时辰 chips 联动');
  assert.match(js, /dClockMode: rawClockRow,/,
    '编辑回显：精确钟表行进入钟表模式');
});

/* k77-M5 行为型：真跑 persons 页的 `_payload()`，看**发出去的是什么** */
test('k77 行为型：_payload() 的 birth_hour/birth_minute 语义（含时辰解析不出来时不写回）', () => {
  const savedPage = global.Page;
  let cfg = null;
  global.Page = (c) => { cfg = c; };
  try { require('../pages/persons/persons'); } finally { global.Page = savedPage; }
  assert.ok(cfg && typeof cfg._payload === 'function', 'persons 页配置应可加载');
  const mk = (data) => {
    const page = Object.assign({}, cfg);
    page.data = Object.assign({}, cfg.data, data);
    return page;
  };
  // ① 钟表模式：birth_hour=时钟小时、birth_minute=分钟（10:55 场景）
  let p = mk({ dName: '甲', dDate: '1999-05-13', dClockMode: true, dClockH: 10,
    dClockM: 55, dHourIndex: 5, dGender: '男', dCal: 'solar', dPlace: '' });
  assert.equal(p._payload().birth_hour, 10);
  assert.equal(p._payload().birth_minute, 55);
  // ② 只知时辰档：代表整点 + 0 分（序号 5=巳 → 9）
  p = mk({ dName: '甲', dDate: '1999-05-13', dClockMode: false, dHourIndex: 5,
    dGender: '男', dCal: 'solar', dPlace: '' });
  assert.equal(p._payload().birth_hour, 9);
  assert.equal(p._payload().birth_minute, 0);
  // ③ 档案里的时辰**解析不出来**（dHourIndex=-1，见 onOpenEdit）→ 两个字段都不写回，
  //    服务端 update 合并逻辑保留既有值；**绝不能**走 shichenIndexToHour(-1)=23（子时）
  p = mk({ dName: '甲', dDate: '1999-05-13', dClockMode: false, dHourIndex: -1,
    dGender: '男', dCal: 'solar', dPlace: '' });
  const dirty = p._payload();
  assert.ok(!('birth_hour' in dirty),
    `时辰解析不出来时不得写回 birth_hour（实际 ${JSON.stringify(dirty.birth_hour)}）`
    + '——写回 23 就是把用户没填过的"子时"落进档案（凭空子时，写路径版）');
  assert.ok(!('birth_minute' in dirty), '时辰解析不出来时同样不得写回 birth_minute');
  // ④ 必要字段仍在（防止 ③ 把整个 payload 弄空）
  assert.equal(dirty.name, '甲');
  assert.equal(dirty.birth_year, 1999);
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
  /* k77 重钉（M-5 同类收口）：原正则钉在 `patch.bClockHIdx = parseInt(p.birth_hour, 10) || 0;`
     —— 那正是"脏值被 parseInt 读成 0（=子时）还当成合法钟点"的写法（'0x10'→0）。
     现在预填先过 `persons.parseHourStrict`：解析不出/越界 ⇒ 按"档案无时辰"处理
     （不预选任何时辰），合法值才回填钟表模式。语义对合法档案零变化。 */
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /const bh = persons\.parseHourStrict\(p\.birth_hour\);/,
    '预填必须走单一事实源的严格解析（parseHourStrict）');
  assert.match(js, /const isClockRow = parseInt\(p\.birth_minute, 10\) > 0[\s\S]{0,160}HOUR_VALUES\.indexOf/,
    'minute>0 或非代表整点 → 钟表行');
  assert.match(js, /patch\.bClockHIdx = bh;/,
    '钟表行的钟点必须用严格解析后的数值（不得 parseInt 原始脏值）');
  assert.match(js, /bh >= 0 && bh <= 23/,
    '越界/不可解析 ⇒ 视为档案无时辰（不预选时辰，绝不回落子时）');
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
