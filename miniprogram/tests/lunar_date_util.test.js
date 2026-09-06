// k9（R2-5 Minor③）：lunarDateToSolar 收敛测试——utils/lunar.js 单点导出的
// 行为与收敛前 paipan/duipan/hehun 三页本地实现逐字节一致（1999-03-28 →
// 1999-05-13 等 R2-5 口径），转换失败回落原文。
// 运行：cd miniprogram && node --test tests/lunar_date_util.test.js
const test = require('node:test');
const assert = require('node:assert/strict');

const lunar = require('../utils/lunar');

/* 收敛前三页的本地实现（paipan/duipan/hehun 逐字节相同），用于一致性对照。 */
function legacyLocal(dateStr) {
  const parts = String(dateStr || '').split('-');
  const y = parseInt(parts[0], 10) || 0;
  const m = parseInt(parts[1], 10) || 0;
  const d = parseInt(parts[2], 10) || 0;
  const s = lunar.lunar2solar(y, m, d, false);
  if (!s) return dateStr;
  return `${s.year}-${String(s.month).padStart(2, '0')}-${String(s.day).padStart(2, '0')}`;
}

const CASES = [
  '1999-03-28',   // R2-5 锚点：农历 3/28 → 公历 1999-05-13
  '1999-12-26',   // 腊月廿六 → 2000-02-01（跨公历年）
  '2024-02-30',   // 30 天月为合法日（2024 农历二月实有 30 天）
  '1900-01-01',   // 契约下界
  '2100-12-29',   // 契约上界附近
  '1999-3-5',     // 无前导零输入 → 输出补零
];

test('k9 收敛：utils.lunarDateToSolar 与三页旧本地实现逐字节一致', () => {
  assert.equal(typeof lunar.lunarDateToSolar, 'function', 'utils/lunar.js 必须导出 lunarDateToSolar');
  for (const input of CASES) {
    assert.equal(lunar.lunarDateToSolar(input), legacyLocal(input),
      `输入 ${input} 必须与收敛前实现输出一致`);
  }
  assert.equal(lunar.lunarDateToSolar('1999-03-28'), '1999-05-13', 'R2-5 锚点换算');
  assert.equal(lunar.lunarDateToSolar('1999-12-26'), '2000-02-01', '跨公历年换算');
});

test('k9 收敛：转换失败回落原文（不崩溃、不造伪公历）', () => {
  // 1999 农历三月仅 29 天 → 3/30 不存在 → 回落原文
  assert.equal(lunar.lunarDateToSolar('1999-03-30'), '1999-03-30');
  for (const bad of ['', 'abc', '1999', null, undefined]) {
    assert.equal(lunar.lunarDateToSolar(bad), bad === undefined ? undefined : bad,
      `非法输入 ${String(bad)} 回落原文`);
  }
});
