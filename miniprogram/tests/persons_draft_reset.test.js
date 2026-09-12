// 易理明灯 k34 A1 — 档案页「新增」表单复位（EMPTY_DRAFT 键名前缀缺陷回归锁）
// 运行：cd miniprogram && node --test tests/persons_draft_reset.test.js
// 缺陷（用户可见）：EMPTY_DRAFT() 返回的键名缺 `d` 前缀（name/rel/cal/date/hourIndex/
//   gender/place…），onAdd 的 setData 写入的全是 data 里不存在的键 —— dName/dDate 等
//   **沿用上一条编辑档案的值**：编辑某档案 → 返回列表 → 点「+」→ 表单回显上一条档案
//   的姓名/生辰（点保存还会把该档案内容重复建档）。
// 本用例锁：onOpenEdit（卡片点开=编辑）后 onAdd → 表单为空（dName==='' && dDate===''），且全 draft 字段复位；
//   另加结构断言：EMPTY_DRAFT 的每个键都必须真实存在于 data 中（防再次漂移）。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

/* ── 页面配置捕获（与既有测试同款） ── */
const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/persons/persons');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg.onOpenEdit === 'function', 'persons.js 页面配置应可加载');

function makePage() {
  const page = Object.assign({}, pageCfg);
  page.data = JSON.parse(JSON.stringify(pageCfg.data));
  page.setData = function (upd, cb) {
    Object.assign(this.data, upd);
    if (typeof cb === 'function') cb();
  };
  return page;
}

/* 一条「精确钟表档 + 关真太阳时」的既有档案：onEdit 会把表单填满（含钟表模式） */
const RICH_PERSON = {
  id: 7, name: '张三', relation: '父母', gender: '女',
  birth_year: 1999, birth_month: 5, birth_day: 13,
  birth_hour: 10, birth_minute: 55,
  calendar: 'lunar', city: '上海', is_default: true, solar_time: 0,
};

function openEditThenAdd() {
  const page = makePage();
  page._rawList = [RICH_PERSON];
  page.onOpenEdit({ currentTarget: { dataset: { id: 7 } } });
  const filled = {
    dName: page.data.dName, dDate: page.data.dDate, dRel: page.data.dRel,
    dCal: page.data.dCal, dGender: page.data.dGender, dPlace: page.data.dPlace,
    dClockMode: page.data.dClockMode, dClockH: page.data.dClockH,
    dClockM: page.data.dClockM, solarOn: page.data.solarOn,
  };
  page.onAdd();
  return { page, filled };
}

test('A1 onEdit 后 onAdd：姓名/生辰清空（不再回显上一条档案）', () => {
  const { page, filled } = openEditThenAdd();
  // 前置：onEdit 确实回显了上一条档案（否则本用例不成立）
  assert.equal(filled.dName, '张三', 'onOpenEdit 应先回显档案姓名');
  assert.equal(filled.dDate, '1999-05-13', 'onOpenEdit 应先回显档案生辰');
  assert.equal(filled.dClockMode, true, 'onOpenEdit 应先回显精确钟表档');
  // 缺陷点：点「+」新增 → 表单必须为空
  assert.equal(page.data.dName, '', 'onAdd 后姓名必须为空');
  assert.equal(page.data.dDate, '', 'onAdd 后生辰必须为空');
});

test('A1 onAdd：全 draft 字段复位（关系/历法/时辰/性别/出生地/钟表档/开关）', () => {
  const { page } = openEditThenAdd();
  assert.equal(page.data.dRel, '自己');
  assert.equal(page.data.dCal, 'solar');
  assert.equal(page.data.dDate, '');
  assert.equal(page.data.dHourIndex, 0);
  assert.equal(page.data.dGender, '女');
  assert.equal(page.data.dPlace, '');
  assert.equal(page.data.dClockMode, false, '钟表档必须退出');
  assert.equal(page.data.dClockH, -1, '钟表小时回到未选');
  assert.equal(page.data.dClockM, 0);
  assert.equal(page.data.solarOn, true, '真太阳时开关回到默认开');
  assert.equal(page._origSolar, true, '新增无「切换」语义（保存不携带开关）');
});

test('A1 onAdd：进入新增态（editing=null / mode=form / 空态提示）', () => {
  const { page } = openEditThenAdd();
  assert.equal(page.data.editing, null, '新增不是编辑（保存走 POST 而非 PUT）');
  assert.equal(page.data.mode, 'form');
  assert.equal(page.data.saving, false);
  assert.equal(page.data.filled, false, '空表单不可保存');
  assert.equal(page.data.hint, '填写姓名与出生年月日后可保存');
});

test('A1 结构锁：EMPTY_DRAFT 每个键都必须存在于 data（防前缀漂移复发）', () => {
  const src = fs.readFileSync(path.join(__dirname, '../pages/persons/persons.js'), 'utf8');
  const m = src.match(/const EMPTY_DRAFT = \(\) => \(\{([\s\S]*?)\n\}\);/);
  assert.ok(m, 'persons.js 应存在 EMPTY_DRAFT 定义');
  const keys = [...m[1].matchAll(/^\s*(\w+):/gm)].map((x) => x[1]);
  assert.ok(keys.length >= 10, `EMPTY_DRAFT 键应齐全（实测 ${keys.length} 个）`);
  for (const k of keys) {
    assert.ok(k in pageCfg.data, `EMPTY_DRAFT 键「${k}」不在 data 中（setData 写不生效=表单不复位）`);
  }
  // 反向：draft 类字段（d 前缀）必须都在 EMPTY_DRAFT 里被复位
  const draftKeys = Object.keys(pageCfg.data).filter((k) => /^d[A-Z]/.test(k));
  for (const k of draftKeys) {
    assert.ok(keys.includes(k), `data 字段「${k}」未在 EMPTY_DRAFT 中复位`);
  }
});
