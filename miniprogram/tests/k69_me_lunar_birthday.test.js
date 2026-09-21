// 易理明灯 k69 — 「我的」页农历档案生日显示修复（三处同根源缺陷）
// 运行：cd miniprogram && node --test tests/k69_me_lunar_birthday.test.js
//
// 背景（控制方真实渲染验证发现，「我的」页生日全错）：
//   生产库真实档案 { calendar:'lunar', birth_year:1999, birth_month:3,
//   birth_day:28, birth_hour:10, birth_minute:55, gender:'男',
//   city:'吉林·长春', solar_time:1 } —— 即农历三月廿八 10:55，
//   用户已确认是本人真实生日。正解（paipan.js 已渲染核对）：公历 1999-05-13 巳时。
//
// 原缺陷（me.js 是全仓唯一不认 calendar 的页面，paipan/duipan/hehun/bazi/
// persons 五页都认）三处同根源：
//   ① 日期：`${y}.${m}.${d}` 把**农历**数字 3/28 当**公历**直显 1999.03.28
//   ② 时辰：_hourToIndex 把钟点 10 当 0-11 序号直取 → HOUR_CN[10]='戌时'
//      （正解巳时=序号 5；排盘页 k19 已修过同款，注释「不再把 10 误读成 戌时」）
//   ③ 农历行：_lunarLabel 对**已经是农历**的 (1999,3,28) 又转一次
//      → 「农历二月十一日」（正解「农历三月廿八日」）
// 修复前实测：birthdayText='1999.03.28 戌时' lunarBirthday='农历二月十一日'
// 修复后实测：birthdayText='1999.05.13 巳时' lunarBirthday='农历三月廿八日'
//
// k69-F1（同类排查另发现的**另一类**缺陷，本批只报不修，待控制方排期）：
//   utils/lunar.js:DAY_NAMES 前 10 项为「一..十」，而权威侧（后端 lunar_python
//   = 排盘引擎同库 + src/engines/zeri.py:978 + src/engines/wannianli.py:69）
//   一律为「初一..初十」。即农历日 1-10 的前端文案与引擎/后端分裂
//   （实测 lunar.formatLunarDate(1995,3,8)='二月八日'，权威 '二月初八'）。
//   非本批两条判据（不认 calendar / 钟点当时辰序号）中任一条，且改动面覆盖
//   全前端所有农历文案，故不在本批修；见 task-k69 报告「同类排查结论表」。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ---- 全局环境（小程序运行时内置） ----
global.getApp = () => ({ globalData: {}, loginPromise: null });
global.wx = { getStorageSync: () => undefined, setStorageSync: () => {} };

const persons = require('../utils/persons');
const lunar = require('../utils/lunar');

const ME_JS = path.join(__dirname, '../pages/me/me.js');
const PAIPAN_JS = path.join(__dirname, '../pages/paipan/paipan.js');

/* 生产库真实档案两个形态：users.bazi_info（me 页数据源）与 persons 行
   （paipan 页数据源）——字段名不同，同一份事实。 */
const BAZI_INFO_LUNAR = {
  year: 1999, month: 3, day: 28, hour: 10, minute: 55,
  gender: '男', city: '吉林·长春', calendar: 'lunar', solar_time: 1,
};
const PERSON_LUNAR = {
  id: 1, name: '闫海洋', relation: '自己', gender: '男',
  birth_year: 1999, birth_month: 3, birth_day: 28,
  birth_hour: 10, birth_minute: 55,
  calendar: 'lunar', city: '吉林·长春', solar_time: 1, is_default: 1,
};

const savedPage = global.Page;
function loadPageCfg(pagePath) {
  let cfg = null;
  global.Page = (c) => { cfg = c; };
  try {
    require(pagePath);
  } finally {
    global.Page = savedPage;
  }
  assert.ok(cfg, '页面配置应可加载：' + pagePath);
  return cfg;
}

function dottedSetData(upd) {
  Object.keys(upd).forEach((k) => { this.data[k] = upd[k]; });
}

const meCfg = loadPageCfg('../pages/me/me');
const paipanCfg = loadPageCfg('../pages/paipan/paipan');

function makeMePage() {
  const page = Object.assign({}, meCfg);
  page.data = JSON.parse(JSON.stringify(meCfg.data));
  page.setData = dottedSetData;
  return page;
}

function makePaipanPage() {
  const page = Object.assign({}, paipanCfg);
  page.data = JSON.parse(JSON.stringify(paipanCfg.data));
  page.setData = dottedSetData;
  return page;
}

/* ═══════ 1. 核心：真实农历档案 → 三处全对（修复前三条断言全红） ═══════ */

test('k69 我的页：农历档案 1999-03-28 10:55 → 公历 1999.05.13 巳时 + 农历三月廿八日', () => {
  const page = makeMePage();
  page._applyBaziToView(BAZI_INFO_LUNAR);
  assert.equal(page.data.birthdayText, '1999.05.13 巳时',
    '公历行必须换算为 1999.05.13 且时辰为巳时（修复前 "1999.03.28 戌时"）');
  assert.equal(page.data.lunarBirthday, '农历三月廿八日',
    '农历行必须为三月廿八日（修复前对已是农历的值二次换算 → "农历二月十一日"）');
  assert.equal(page.data.hasBazi, true, '真实命盘就绪 → 渲染生日区（不回退未设置占位）');
});

test('k69 我页判别力对照：三处旧错值逐一断言不相等（防回归到任一旧行为）', () => {
  const page = makeMePage();
  page._applyBaziToView(BAZI_INFO_LUNAR);
  assert.notEqual(page.data.birthdayText, '1999.03.28 戌时', '不得回到农历当公历直显 + 钟点当序号');
  assert.notEqual(page.data.birthdayText, '1999.03.28 巳时', '日期仍须换算（只修时辰不算修）');
  assert.notEqual(page.data.birthdayText, '1999.05.13 戌时', '时辰仍须换算（只修日期不算修）');
  assert.notEqual(page.data.lunarBirthday, '农历二月十一日', '不得对农历值二次换算');
});

/* ═══════ 2. 跨页一致（数据一致性铁律）：me 页 vs paipan 页同源同值 ═══════ */

test('k69 跨页一致：「我的」页换算结果 = 排盘页（已渲染核对的正确实现）', () => {
  const mePage = makeMePage();
  mePage._applyBaziToView(BAZI_INFO_LUNAR);

  const pp = makePaipanPage();
  pp._persons = [PERSON_LUNAR];
  pp._fillFromPerson(PERSON_LUNAR);

  // ① 公历日期：me 的 "1999.05.13" vs paipan 的 bDate "1999-05-13"
  const meSolar = mePage.data.birthdayText.split(' ')[0].split('.').join('-');
  assert.equal(meSolar, pp.data.bDate,
    'me 页公历行必须等于 paipan 页换算后的 bDate（两页不得各自换算出差）');
  assert.equal(meSolar, '1999-05-13', '且等于权威正解 1999-05-13');

  // ② 时辰：paipan 的 bHourIdx 是 1-based（hourToShichenIndex + 1）
  const paipanShichen = persons.shichenCN(pp.data.bHourIdx - 1);
  assert.equal(mePage.data.birthdayText.split(' ')[1], paipanShichen,
    'me 页时辰文案必须等于 paipan 页同一档案推导的时辰（不得一页巳时一页戌时）');
  assert.equal(paipanShichen, '巳时', 'paipan 侧口径为巳时（bHourIdx=6 → 序号 5）');

  // ③ 农历行与档案原始农历值自洽（三月廿八）
  assert.equal(mePage.data.lunarBirthday, `农历${lunar.formatLunarDate(1999, 5, 13)}`);
});

/* ═══════ 3. 零回归：公历档案 / 无时辰 / 本地兜底形态 ═══════ */

test('k69 零回归：公历档案原样直显（不掺换算）', () => {
  const page = makeMePage();
  page._applyBaziToView({
    year: 1995, month: 3, day: 8, hour: 9, minute: 0,
    gender: '女', city: '北京', calendar: 'solar',
  });
  assert.equal(page.data.birthdayText, '1995.03.08 巳时', '公历 3/8 原样（9 点代表整点=巳时）');
  // 农历行断言「委托单一事实源」而非固定文案：本批不承担 DAY_NAMES 用字口径
  // （见下方 k69-F1 注），只锁定 me.js 把公历日期交给 utils/lunar 单点换算。
  assert.equal(page.data.lunarBirthday, `农历${lunar.formatLunarDate(1995, 3, 8)}`,
    '公历 3/8 → 走 utils/lunar 单点转农历（实际文案 农历二月八日）');
});

test('k69 零回归：calendar 缺失（旧行）按公历处理，不误判为农历', () => {
  const page = makeMePage();
  page._applyBaziToView({ year: 1995, month: 3, day: 8, hour: 9, minute: 0, gender: '女' });
  assert.equal(page.data.birthdayText, '1995.03.08 巳时', '无 calendar 键 → 不换算（与 paipan 缺省 solar 同口径）');
});

test('k69 零回归：无时辰 → 不显示时辰尾缀（沿用原空白天语义）', () => {
  const page = makeMePage();
  page._applyBaziToView({ year: 1999, month: 3, day: 28, calendar: 'lunar', gender: '男' });
  assert.equal(page.data.birthdayText, '1999.05.13', '无 hour → 仅公历日期，不得兜底成「子时」');
  const p2 = makeMePage();
  p2._applyBaziToView({ year: 1999, month: 3, day: 28, hour: '', calendar: 'lunar', gender: '男' });
  assert.equal(p2.data.birthdayText, '1999.05.13', '空串 hour 同样不显示时辰（hourToShichenIndex 空值兜底 0=子时，须前置拦住）');
});

test('k69 零回归：本地兜底形态 birthYear/birthMonth/birthDay + birthHour 同样生效', () => {
  const page = makeMePage();
  page._applyBaziToView({
    birthYear: 1999, birthMonth: 3, birthDay: 28, birthHour: 9,
    calendar: 'lunar', gender: '男',
  });
  assert.equal(page.data.birthdayText, '1999.05.13 巳时', '本地形态农历档案同款换算（9 = 巳时代表整点）');
});

test('k69 零回归：非法农历日（1999-03-30 不存在）→ 回落原文不崩溃', () => {
  const page = makeMePage();
  page._applyBaziToView({ year: 1999, month: 3, day: 30, hour: 9, calendar: 'lunar', gender: '男' });
  assert.equal(page.data.birthdayText, '1999.03.30 巳时', 'lunarDateToSolar 契约：转换失败回落原文（与 paipan/duipan 同款）');
});

/* ═══════ 4. 单一事实源接线（摘掉即红：本地时辰表/换算表不得回流） ═══════ */

const ME_SRC = fs.readFileSync(ME_JS, 'utf8');
const ME_CODE = ME_SRC.replace(/\/\*[\s\S]*?\*\//g, ''); // 去块注释，只看代码

test('k69 接线：me.js 不得再持有本地时辰中文表（单点 = utils/persons.HOUR_CN）', () => {
  assert.ok(!/const\s+HOUR_CN\s*=/.test(ME_CODE),
    'me.js 不得自建 HOUR_CN 表（第二份映射表=口径分裂根源，正是本 bug 成因）');
  assert.ok(!/HOUR_CN\s*\[/.test(ME_CODE),
    'me.js 不得直接索引 HOUR_CN（应走 persons.shichenCN）');
  assert.ok(!/_hourToIndex/.test(ME_CODE),
    'me.js 不得残留 _hourToIndex（钟点当序号查表的本地实现已删）');
});

test('k69 接线：me.js 复用 utils/persons 与 utils/lunar 既有单点', () => {
  assert.match(ME_SRC, /const persons = require\('\.\.\/\.\.\/utils\/persons'\);/,
    '必须 require utils/persons（时辰映射单一事实源）');
  assert.match(ME_CODE, /persons\.shichenCN\(persons\.hourToShichenIndex\(rawHour, rawMinute\)\)/,
    '时辰文案必须由 persons.hourToShichenIndex + shichenCN 推导');
  assert.match(ME_CODE, /lunar\.lunarDateToSolar\(/,
    '农历换算必须复用 utils/lunar.lunarDateToSolar（与 paipan/duipan/hehun 同源）');
  assert.ok(!/function\s+lunarDateToSolar\s*\(/.test(ME_CODE),
    '不得新写第二份农历换算实现');
});

test('k69 接线：me.js 认 calendar 标志（与仓内其余五页同款判据）', () => {
  assert.match(ME_CODE, /b\.calendar === 'lunar'/,
    'me.js 必须读 b.calendar === \'lunar\'（原缺陷：全仓唯一不认该标志的页面）');
});
