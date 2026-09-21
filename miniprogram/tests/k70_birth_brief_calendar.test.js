// k70-F2：档案「简版」生日（persons.birthBrief）必须带历法标记。
//
// 缺陷（k69-F2 报告）：persons/bazi 的命主列表卡片渲染 birthBrief，产出
// 「1999 年 3 月 28 日 · 巳时 10:55 · 男」——年/月/日数字本身不带历法信息，
// 农历档会被读成公历。该字段是**原样回显**契约（不做换算），故只补标记。
//
// 共享工具 persons.birthBrief 有 4 个调用点（persons.js:87、bazi.js:134/340/424），
// 全部收口到本函数 → 本测试即覆盖全部 4 处可见文案。
// 摘掉修复（去掉 calLabel 前缀）→ 本文件必红。
//
// 运行：cd miniprogram && node --test tests/k70_birth_brief_calendar.test.js
const test = require('node:test');
const assert = require('node:assert/strict');

const persons = require('../utils/persons');

/* k69 报告里实测的那条档案：农历 1999-03-28 巳时 10:55 男 */
const LUNAR = {
  name: '我', relation: '自己', gender: '男',
  birth_year: 1999, birth_month: 3, birth_day: 28,
  birth_hour: 10, birth_minute: 55, calendar: 'lunar', city: '上海',
};
const SOLAR = Object.assign({}, LUNAR, { calendar: 'solar' });

test('k70-F2 农历档：简版生日带「农历」标记（k69 报告的原串必须消失）', () => {
  const got = persons.birthBrief(LUNAR);
  assert.equal(got, '农历 1999 年 3 月 28 日 · 巳时 10:55 · 男 · 上海');
  assert.ok(got.indexOf('农历') === 0, `必须以「农历」开头，实际 ${JSON.stringify(got)}`);
  // k69 报告的原串（无历法标记）不得再产出
  assert.ok(!/^1999 年/.test(got), `不得再产出无历法标记的串：${JSON.stringify(got)}`);
  assert.notEqual(got, '1999 年 3 月 28 日 · 巳时 10:55 · 男 · 上海');
});

test('k70-F2 公历档：简版生日带「公历」标记（不缺省静默）', () => {
  const got = persons.birthBrief(SOLAR);
  assert.equal(got, '公历 1999 年 3 月 28 日 · 巳时 10:55 · 男 · 上海');
  assert.ok(got.indexOf('公历') === 0, `必须以「公历」开头，实际 ${JSON.stringify(got)}`);
});

test('k70-F2 标记缺省判定：calendar 缺失/空/未知 → 公历（与 birthSummary 同判定）', () => {
  for (const cal of [undefined, null, '', 'weird', 'SOLAR']) {
    const p = Object.assign({}, LUNAR, { calendar: cal });
    assert.ok(persons.birthBrief(p).indexOf('公历') === 0,
      `calendar=${JSON.stringify(cal)} 应判为公历，实际 ${JSON.stringify(persons.birthBrief(p))}`);
  }
});

test('k70-F2 简版与详版历法口径同源一致（禁止两处内联分叉）', () => {
  for (const cal of ['lunar', 'solar', undefined, '']) {
    const p = Object.assign({}, LUNAR, { calendar: cal });
    const brief = persons.birthBrief(p);
    const summary = persons.birthSummary(p);
    const briefCal = brief.indexOf('农历') === 0 ? '农历' : '公历';
    assert.ok(summary.indexOf(briefCal) === 0,
      `calendar=${JSON.stringify(cal)}：简版标「${briefCal}」但详版为 ${JSON.stringify(summary)}`);
  }
});

test('k70-F2 原样回显契约不变：数字不换算、其余字段不动', () => {
  const got = persons.birthBrief(LUNAR);
  // 农历 1999-03-28 的**公历**是 1999-05-13 —— 简版必须仍回显农历数字
  assert.ok(got.indexOf('1999 年 3 月 28 日') !== -1, `应原样回显 3 月 28 日：${got}`);
  assert.ok(got.indexOf('5 月 13 日') === -1, `不得顺手换成公历换算值：${got}`);
  // 其余字段（时辰/性别/出生地）与修复前逐字相同
  assert.ok(got.indexOf('巳时 10:55') !== -1, `时辰分钟档不变：${got}`);
  assert.ok(got.indexOf('男') !== -1, `性别不变：${got}`);
  assert.ok(got.indexOf('上海') !== -1, `出生地不变：${got}`);
});

test('k70-F2 空档案不炸（保持既有空串契约）', () => {
  assert.equal(persons.birthBrief(null), '');
  assert.equal(persons.birthBrief(undefined), '');
  assert.equal(persons.birthSummary(null), '');
});
