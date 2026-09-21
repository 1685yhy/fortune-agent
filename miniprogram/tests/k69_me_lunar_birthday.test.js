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

/* ═══ 3b. k73-M1「宁少不假」：不可解析/越界 hour 不得凭空显示「子时」 ═══

   复审实测的原始对照（改前）：
     hour = "abc" | 99 | -1 | NaN
       旧实现（k69 前）→ "1995.05.12"       （不显示时辰）
       k69 实现      → "1995.05.12 子时"    （凭空给了个时辰）← 必须修掉
   根因：hasHour 只拦 undefined/null/''，其余交给 hourToShichenIndex —— 它对无法
   识别的输入返回 0（=子时）。上面第 6 条测试只覆盖了 ''，注释自己都写了「空值
   兜底 0=子时，须前置拦住」，却没覆盖**不可解析值**，这里补齐。
   判据：与旧实现一致的「宁少不假」——解析失败/越界一律不显示时辰。 */
test('k73-M1：hour 不可解析/越界 ⇒ 不显示时辰（绝不兜底成「子时」）', () => {
  const BAD = [
    ['abc', '"abc"'], [99, '99'], [-1, '-1'], [NaN, 'NaN'],
    [Infinity, 'Infinity'], [24, '24（越上界）'],
    [true, 'true'], [[], '[]（空数组）'],
    [10.5, '10.5（非整数钟点）'], ['10abc', '"10abc"（半截垃圾）'],
  ];
  BAD.forEach(([val, label]) => {
    const page = makeMePage();
    page._applyBaziToView({
      year: 1995, month: 5, day: 12, hour: val, calendar: 'solar', gender: '男',
    });
    assert.equal(page.data.birthdayText, '1995.05.12',
      `hour=${label} 不可解析/越界 ⇒ 必须不显示时辰（宁少不假）；`
      + '改前实测：显示 "1995.05.12 子时"（凭空一个假时辰）');
  });
});

test('k73-M1 反向：合法 0-23 整数（含 0）与数字串**必须**仍显示时辰（不误伤真值）', () => {
  /* 反向守卫：上一条若写成「一律不显示」也会全绿 —— 故必须证明真值没被拦掉。
     0 是合法钟点（子时），不能被 falsy 判断误伤（这正是必须用 Number.isInteger
     而非 `if (!hour)` 的原因）。 */
  const ZH = [[0, '子时'], [1, '丑时'], [9, '巳时'], [10, '巳时'],
    [12, '午时'], [23, '子时']];
  ZH.forEach(([h, cn]) => {
    const page = makeMePage();
    page._applyBaziToView({
      year: 1995, month: 5, day: 12, hour: h, calendar: 'solar', gender: '男',
    });
    assert.equal(page.data.birthdayText, `1995.05.12 ${cn}`,
      `hour=${h} 是合法钟点 ⇒ 必须显示 ${cn}`);
  });
  // 后端 JSON/字符串形态的数字同样必须显示（不得只认 number 类型）
  ['0', '10', '23'].forEach((h) => {
    const page = makeMePage();
    page._applyBaziToView({
      year: 1995, month: 5, day: 12, hour: h, calendar: 'solar', gender: '男',
    });
    assert.equal(page.data.birthdayText,
      `1995.05.12 ${persons.shichenCN(persons.hourToShichenIndex(h))}`,
      `hour="${h}"（数字串）必须与 number 形态同值`);
  });
  // 核心场景（农历 1999-03-28 10:55 = 公历 1999-05-13 巳时）不因本批收紧而改变
  const core = makeMePage();
  core._applyBaziToView(BAZI_INFO_LUNAR);
  assert.equal(core.data.birthdayText, '1999.05.13 巳时', '真实档案主场景零回归');
});

/* ═══ 3c. k75「闸门与渲染必须共用同一个解析器」（k73 残留洞）═════════════

   复审实测（tip=闸门版 vs base）：闸门用 Number 完整解析判「可解析」，但交给
   单点的仍是**原始值**，而 persons.hourToShichenIndex 内部是 parseInt(hour, 10)
   ⇒ 两个解析器对同一输入给出不同数值。凡 Number 认、parseInt 不认的形态
   （"0x10"/"1e1"/"0b101"/"0o17"）**闸门放行、渲染侧却按 parseInt 算** ——
   **解析结果被丢掉**，又退回该函数的兜底 0（=子时）。实测改前：
     "0x10"  → 子时（期望申时）   "1e1"   → 丑时（期望巳时）
     "0b101" → 子时（期望卯时）   "0o17"  → 子时（期望申时）
   汇总：崩=0 漏=0 **误=4**（base 同期 崩=0 漏=20 误=4）
   ⇒ 与「解析失败一律不显示、**绝不兜底 0**」「用 `Number` 完整解析而非
   `parseInt`」两句直接冲突。修法：把**校验后的数值**交给单点
   （`persons.hourToShichenIndex(hourNum, rawMinute)`）。 */
test('k75 闸门放行的可解析形态：必须按 Number 解析结果渲染（不得退回 parseInt/兜底 0）', () => {
  /* 期望值由**独立**口径推出：JS 数字字面量语义（Number 解析）→ 时钟小时 →
     utils/persons 时钟窗口。左=闸门放行的字符串形态，中=该形态的真实数值，
     右=权威时辰（16→申 10→巳 5→卯 15→申）。 */
  const MATRIX = [
    ['0x10', 16, '申时'],
    ['1e1', 10, '巳时'],
    ['0b101', 5, '卯时'],
    ['0o17', 15, '申时'],
  ];
  MATRIX.forEach(([raw, num, cn]) => {
    // ① 语义前提：该形态经 Number 解析确实等于 num（守卫的不是巧合）
    assert.equal(Number(raw), num, `前提失效：Number(${JSON.stringify(raw)}) ≠ ${num}`);
    // ② 权威单点对**数值**不可反驳：num 必落到 cn
    assert.equal(persons.shichenCN(persons.hourToShichenIndex(num)), cn,
      `权威口径失效：hourToShichenIndex(${num}) ≠ ${cn}`);
    // ③ 页面必须渲染 cn —— 改前渲染的是 parseInt(raw) 的结果（0/1/0/0 → 子/丑/子/子）
    const page = makeMePage();
    page._applyBaziToView({
      year: 1995, month: 5, day: 12, hour: raw, calendar: 'solar', gender: '男',
    });
    assert.equal(page.data.birthdayText, `1995.05.12 ${cn}`,
      `hour=${JSON.stringify(raw)}（闸门已放行）⇒ 必须按 Number 解析结果 ${num} 渲染 ${cn}；`
      + '改前实测退回 parseInt ⇒ '
      + persons.shichenCN(persons.hourToShichenIndex(parseInt(raw, 10))));
  });
});

test('k75 反向：同一批值的 number / 十进制数字串形态必须渲染同一时辰（两解析器不得分叉）', () => {
  /* 反向守卫：上一条若被写成「一律不显示」或「恒按某个错值渲染」都会红，但还要
     钉住另一侧 —— 同一批数值的 number 形态与十进制数字串形态（后端 JSON 常见
     形态）必须与上一条**逐字同值**，证明修的是「闸门与渲染共用解析结果」，
     不是「把可解析字符串也拦掉」。 */
  const PAIRS = [[16, '0x10', '申时'], [10, '1e1', '巳时'],
    [5, '0b101', '卯时'], [15, '0o17', '申时']];
  PAIRS.forEach(([num, raw, cn]) => {
    [[num, 'number'], [String(num), '十进制数字串']].forEach(([hour, label]) => {
      const page = makeMePage();
      page._applyBaziToView({
        year: 1995, month: 5, day: 12, hour, calendar: 'solar', gender: '男',
      });
      assert.equal(page.data.birthdayText, `1995.05.12 ${cn}`,
        `hour=${label} ${JSON.stringify(hour)} 必须与 ${JSON.stringify(raw)} 同值（${cn}）`);
    });
  });
});

/* ═══════ 4. 单一事实源：口径用行为断言 + 仅剩的结构性接线回归锁 ═══════

   k73-M2（复审裁定）：原第 10/11 条是**源码文本匹配**，行为等价的改写会**假红**。
   本批实测（同一份 me.js 变体 × 改前/改后两份测试文件，共 6 种**行为等价**改写）：
     变体                             改前测试文件   改后测试文件
     ①局部变量重命名 rawHour→bh         RED(假红)      GREEN
     ②抽 const isLunar = ... 布尔        GREEN          GREEN
     ③Yoda 条件 'lunar' === b.calendar  RED(假红)      GREEN
     ④persons.shichenCN 取局部别名       RED(假红)      GREEN
     ⑤行注释里提到旧表名/禁用 token      RED(假红)      GREEN
     ⑥require 绑定名改写 persons→P       RED(假红)      GREEN
   ⇒ 6 种里 5 种**改前假红**，改后全部不变红。
   ⚠️ 一并更正复审给的那一例（报告里作「变体 B」）：`calendar !== 'solar'` **不是**
   行为等价改写 —— 旧行缺 calendar 时会误把公历当农历换算（本文件第 5 条测试即红），
   属**语义**变化，改红是正确的（改前/改后都红，且应当红）。
   按复审给的两条路，本批**选了 ①**：凡能用输入/输出刻画的，一律改**行为断言**
   （与权威单点 utils/persons + utils/lunar 逐值比对，不看 me.js 源码字符串）
   —— 行为等价改写按构造不可能变红。
   **只有「某物不得存在」这类结构性禁令**无法行为化（一份行为正确的副本在输出上
   不可区分），保留为 ②「接线回归锁」，并已收窄到最小必要模式（见 §4c）。 */

/* ── 4a. 行为断言：时辰口径 = persons 单点（覆盖全部 24 钟点 + 12 代表整点）── */

test('k73 行为断言：24 钟点 × 12 时辰代表整点，me 页时辰文案 == persons 单点推导', () => {
  const HOURS = [];
  for (let h = 0; h < 24; h++) HOURS.push(h);                 // 时钟小时 0-23
  HOURS.push(23, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21);      // 时辰代表整点（HOUR_VALUES）
  const seen = new Set();
  HOURS.forEach((h) => {
    [0, 55].forEach((mi) => {
      const page = makeMePage();
      page._applyBaziToView({
        year: 1995, month: 5, day: 12, hour: h, minute: mi,
        calendar: 'solar', gender: '男',
      });
      const expected = persons.shichenCN(persons.hourToShichenIndex(h, mi));
      assert.equal(page.data.birthdayText, `1995.05.12 ${expected}`,
        `hour=${h} minute=${mi} 的时辰文案必须等于 persons 单点推导`
        + '（me.js 自建第二份映射表 / 自算窗口 ⇒ 本条即红）');
      seen.add(expected);
    });
  });
  // 判别力自证：矩阵必须真的覆盖 12 个时辰（否则「全等于 expected」可能因
  // 输入退化（如全是同一时辰）而失去判别力）
  assert.equal(seen.size, 12, `12 个时辰都必须出现在矩阵里，实际覆盖 ${seen.size} 个`);
});

/* ── 4b. 行为断言：calendar 标志语义完全刻画（等价于原第 11 条，但不看源码）── */

test('k73 行为断言：calendar 语义完全刻画 —— 仅当值恰为 "lunar" 才做农历→公历换算', () => {
  const pad = (n) => String(n).padStart(2, '0');
  /* 期望值由**规格 + 权威单点**推出（不是抄 me.js 的代码）：
     仅 calendar === 'lunar' 时走 utils/lunar.lunarDateToSolar，否则原样直显。 */
  const expectedOf = (cal) => {
    let y = 1999; let m = 3; let d = 28;
    if (cal === 'lunar') {
      const conv = String(lunar.lunarDateToSolar('1999-03-28')).split('-');
      if (conv.length === 3) { y = +conv[0]; m = +conv[1]; d = +conv[2]; }
    }
    return `${y}.${pad(m)}.${pad(d)} 巳时`;   // hour=10, minute=55 ⇒ 巳时
  };
  const GRID = [['lunar', 'lunar'], ['solar', 'solar'], ['Solar', '"Solar"（大小写不等）'],
    ['LUNAR', '"LUNAR"（大小写不等）'], ['x', '"x"（未知值）'],
    ['', '""（空串）'], [null, 'null'], [0, '0'], [1, '1'],
    [undefined, '（键缺失：旧行）']];
  const outs = new Set();
  GRID.forEach(([cal, label]) => {
    const b = { year: 1999, month: 3, day: 28, hour: 10, minute: 55, gender: '男' };
    if (cal !== undefined) b.calendar = cal;   // undefined 分支 = 旧行缺键
    const page = makeMePage();
    page._applyBaziToView(b);
    const want = expectedOf(cal);
    assert.equal(page.data.birthdayText, want,
      `calendar=${label} ⇒ 期望 ${want}（判据：恰为 'lunar' 才换算；`
      + '「仅非 solar 即换算」这类看似等价的改写会在此判红——那是**语义**变化）');
    outs.add(page.data.birthdayText);
  });
  // 判别力自证：矩阵确实同时包含「换算」与「不换算」两种输出，
  // 否则「全等于期望」可能因所有分支输出相同而失去判别力
  assert.deepEqual([...outs].sort(), ['1999.03.28 巳时', '1999.05.13 巳时'],
    `矩阵必须同时覆盖两种结果，实际 ${JSON.stringify([...outs])}`);
  assert.equal(expectedOf('lunar'), '1999.05.13 巳时', '权威正解（与 paipan 页一致）');
});

/* ── 4c. 接线回归锁（k73-M2 ②：**只**剩无法行为化的结构性禁令）────────────
   本节断言的是「**某物不存在 / 某依赖存在**」——一份行为正确的副本在输出上
   与单点不可区分，故行为断言**原则上无法**证明它。已收窄到最小必要模式：
   - require 锁只匹配**模块路径**（不锁绑定名）⇒ `const P = require(...)` 不假红；
   - 负向锁只匹配**定义式**（`const HOUR_CN =`）/ **自算函数签名**
     （`function lunarDateToSolar(`）/ 旧实现标志名（`_hourToIndex`）
     ⇒ 不锁调用形状、不锁局部变量名（那是 k73-M2 修掉的假红来源）。
   注释一律先剥除（块注释 + 行注释）：**注释不是实现**，注释里提到旧表名
   不该假红（剥注释只提升精度，不放宽被禁的实现形态）。
   ⚠️ 这是**接线回归锁**：改 me.js 实现时若命中，请先判断是「误伤注释/绑定名」
     还是「真的又添了第二份实现」——前者改本锁，后者改实现。 */
const ME_SRC = fs.readFileSync(ME_JS, 'utf8');
const ME_CODE = ME_SRC
  .replace(/\/\*[\s\S]*?\*\//g, '')      // 块注释
  .replace(/(^|\s)\/\/[^\n]*/gm, '$1');  // 行注释（要求 // 前有空白，避开 http://）

test('k73 接线回归锁：me.js 必须消费共享单点（require 路径 + 单点 API 被引用）', () => {
  assert.match(ME_CODE, /require\('\.\.\/\.\.\/utils\/persons'\)/,
    '必须 require utils/persons（时辰映射单一事实源；绑定名不限）');
  assert.match(ME_CODE, /require\('\.\.\/\.\.\/utils\/lunar'\)/,
    '必须 require utils/lunar（农历换算单一事实源；绑定名不限）');
  /* 单点 API 必须**被引用**（只匹配 API 名，不匹配接收者/绑定名/调用形状）：
     `persons.shichenCN(...)`、`const cn = persons.shichenCN; cn(...)`、
     `L.lunarDateToSolar(...)` 都通过；「不再走单点」（改自算/换表）即红。 */
  assert.match(ME_CODE, /shichenCN/,
    'me.js 必须引用 persons.shichenCN（时辰文案单点；原缺陷正是自建表绕过它）');
  assert.match(ME_CODE, /lunarDateToSolar\(/,
    'me.js 必须引用 lunarDateToSolar（农历换算单点；不锁接收者变量名）');
});

test('k73 接线回归锁：me.js 不得自建第二份映射表 / 第二份农历换算', () => {
  assert.ok(!/const\s+HOUR_CN\s*=/.test(ME_CODE),
    'me.js 不得自建 HOUR_CN 表（第二份映射表=口径分裂根源，正是本 bug 成因）');
  assert.ok(!/HOUR_CN\s*\[/.test(ME_CODE),
    'me.js 不得直接索引 HOUR_CN（应走 persons.shichenCN）');
  assert.ok(!/_hourToIndex/.test(ME_CODE),
    'me.js 不得残留 _hourToIndex（钟点当序号查表的本地实现已删）');
  assert.ok(!/function\s+lunarDateToSolar\s*\(/.test(ME_CODE),
    '不得新写第二份农历换算实现');
  assert.ok(!/[子丑寅卯辰巳午未申酉戌亥]时/.test(ME_CODE),
    'me.js 代码里不得出现任何时辰中文字面量（一律由 persons.shichenCN 供；'
    + '这一条把「本地表换个名字」也堵住 —— 任何时辰表都必须写出这些字。'
    + '顺带堵住「解析失败就硬编码兜底 子时」这类假时辰写法）');
});
