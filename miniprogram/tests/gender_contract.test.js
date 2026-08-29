// Task G1（P0-A，2026-08-29）：性别契约统一 —— genderCode/genderCN 全映射。
// 契约：'男' | '女' | 'unknown'（中文单一契约；不再产出 male/female）。
// 显示层 genderCN 兼容历史 'female'/'male' 存量值；未知不再显示成男。
const test = require('node:test');
const assert = require('node:assert/strict');
const persons = require('../utils/persons');

test('G1 genderCode：展示值 → 中文契约（禁止再产英文）', () => {
  assert.equal(persons.genderCode('女'), '女');
  assert.equal(persons.genderCode('男'), '男');
  // 其余一切（含历史英文、空、未定义）→ unknown（未知绝不默认成男）
  assert.equal(persons.genderCode(''), 'unknown');
  assert.equal(persons.genderCode(undefined), 'unknown');
  assert.equal(persons.genderCode('unknown'), 'unknown');
  assert.equal(persons.genderCode('female'), 'unknown');
  assert.equal(persons.genderCode('male'), 'unknown');
});

test('G1 genderCN：兼容历史英文 + 中文 + 未知展示', () => {
  assert.equal(persons.genderCN('女'), '女');
  assert.equal(persons.genderCN('female'), '女');
  assert.equal(persons.genderCN('男'), '男');
  assert.equal(persons.genderCN('male'), '男');
  // 未知 → '未知'（原实现 unknown 显示成男，体验错位根因之一）
  assert.equal(persons.genderCN('unknown'), '未知');
  assert.equal(persons.genderCN(''), '未知');
  assert.equal(persons.genderCN(undefined), '未知');
});

test('G1 birthSummary/birthBrief 未知性别显示「未知」不冒充男', () => {
  const p = {
    name: '我', relation: '自己', gender: 'unknown',
    birth_year: 1999, birth_month: 2, birth_day: 26,
    birth_hour: 7, calendar: 'solar', city: '北京',
  };
  assert.match(persons.birthSummary(p), /未知/);
  assert.match(persons.birthBrief(p), /未知/);
});
