// k70-F1：农历日名前端口径必须与**引擎权威侧**一致。
//
// 缺陷（k69-F1 报告）：utils/lunar.js 的 DAY_NAMES 前 10 项原为「一..十」，
// 而引擎权威侧（src/engines/zeri.py:_lunar_day_cn、src/engines/wannianli.py:
// _lunar_day_cn，两处逐字相同）一律「初一..初十」→ 前端渲染「二月八日」、
// 引擎渲染「农历二月初八日」，农历日 1-10（30 天里的 10 天，约 1/3）前后端分裂。
//
// 本测试的**判据是权威口径**，不是"表自己等于自己"：
//   1) oracle = 引擎公式逐字复刻（下 engineDayCN），逐日 1..30 比对；
//   2) 锚点 = lunar_python（引擎同库）+ 引擎取日，已实测输出（见下 ANCHORS）；
//   3) 渲染串 = 断言公共 API 实际吐出的字（lunarDayName / formatLunarDate）。
// 摘掉修复（把前 10 项改回「一..十」）→ 本文件必红。
//
// 运行：cd miniprogram && node --test tests/k70_lunar_day_authority.test.js
const test = require('node:test');
const assert = require('node:assert/strict');

const lunar = require('../utils/lunar');

/* 引擎权威公式逐字复刻（zeri.py:_lunar_day_cn / wannianli.py:_lunar_day_cn）。
 * 注意：这里**故意**复制公式而非引用前端表 —— 公式才是 oracle。 */
const CN_NUM = { 1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '七', 8: '八', 9: '九' };
function engineDayCN(day) {
  if (day === 10) return '初十';
  if (day === 20) return '二十';
  if (day === 30) return '三十';
  if (day < 10) return '初' + CN_NUM[day];
  if (day < 20) return '十' + CN_NUM[day % 10];
  if (day < 30) return '廿' + CN_NUM[day % 10];
  return String(day);
}

/* lunar_python（引擎同库）实测锚点：Solar.fromYmd → Lunar.getMonth/getDay →
 * 引擎 _lunar_day_cn。原始输出逐条记录在此，作为**外部权威**（非本仓库自证）。 */
const ANCHORS = [
  // 2026 农历八月初一..初十（初十=2026-09-20），11 日起进「十一」段
  ['2026-09-16', '八月初六日', 8, 6],
  ['2026-09-17', '八月初七日', 8, 7],
  ['2026-09-18', '八月初八日', 8, 8],
  ['2026-09-19', '八月初九日', 8, 9],
  ['2026-09-20', '八月初十日', 8, 10],
  ['2026-09-21', '八月十一日', 8, 11],
];

test('k70-F1 农历日名 1-30 与引擎权威公式逐日一致（0 差异）', () => {
  const diff = [];
  for (let d = 1; d <= 30; d++) {
    const got = lunar.DAY_NAMES[d - 1];
    const want = engineDayCN(d);
    if (got !== want) diff.push(`第 ${d} 日: 前端「${got}」≠ 引擎「${want}」`);
  }
  assert.equal(diff.length, 0, `与引擎口径分裂 ${diff.length} 处：\n  ${diff.join('\n  ')}`);
  assert.equal(lunar.DAY_NAMES.length, 30, '日名表应为 30 项');
});

test('k70-F1 日 1-10 必须以「初」开头（k69 报告的正是这 10 天）', () => {
  for (let d = 1; d <= 10; d++) {
    const name = lunar.DAY_NAMES[d - 1];
    assert.equal(name.charAt(0), '初', `农历第 ${d} 日「${name}」须以「初」开头（引擎口径）`);
    assert.equal(name, engineDayCN(d), `农历第 ${d} 日`);
  }
});

test('k70-F1 渲染串：lunarDayName 实际吐出的字（1-10 带「初」）', () => {
  // 摘掉修复时这里会吐出「八日」「一日」「十日」→ 红
  assert.equal(lunar.lunarDayName(1), '初一日');
  assert.equal(lunar.lunarDayName(8), '初八日');
  assert.equal(lunar.lunarDayName(10), '初十日');
  // 11 日起口径不变（回归护栏）
  assert.equal(lunar.lunarDayName(11), '十一日');
  assert.equal(lunar.lunarDayName(20), '二十日');
  assert.equal(lunar.lunarDayName(21), '廿一日');
  assert.equal(lunar.lunarDayName(30), '三十日');
});

test('k70-F1 渲染串：formatLunarDate 与 lunar_python/引擎锚点逐字一致', () => {
  for (const [ymd, wantText, , ld] of ANCHORS) {
    const [y, m, d] = ymd.split('-').map(Number);
    assert.equal(lunar.formatLunarDate(y, m, d), wantText, `${ymd} 的农历文案`);
    // 锚点自身必须与引擎公式自洽（防锚点写错）
    assert.equal(wantText, '八月' + engineDayCN(ld) + '日', `${ymd} 锚点与引擎公式自洽`);
    // 换算出的农历日也要与锚点一致（防换算漂移掩盖日名问题）
    assert.equal(lunar.solar2lunar(y, m, d).day, ld, `${ymd} 的农历日数`);
  }
});

test('k70-F1 k69 报告的原串「二月八日」不再出现，应为「二月初八日」', () => {
  const text = lunar.lunarMonthName(2026, 2, false) + lunar.lunarDayName(8);
  assert.equal(text, '二月初八日');
  assert.notEqual(text, '二月八日', 'k69 报告的错串必须消失');
});

test('k70-F1 lunarDateText（paipan 农历兜底）同源口径', () => {
  // lunarDateText = lunarMonthName + lunarDayName，须与 formatLunarDate 同日名
  for (let d = 1; d <= 10; d++) {
    assert.equal(lunar.lunarDayName(d), engineDayCN(d) + '日', `第 ${d} 日`);
  }
  assert.ok(lunar.lunarDateText(2026, 9, 18).indexOf('初八') !== -1,
    `lunarDateText 应含「初八」，实际 ${lunar.lunarDateText(2026, 9, 18)}`);
});
