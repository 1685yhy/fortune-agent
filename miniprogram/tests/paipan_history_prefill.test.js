// 易理明灯 batch3 B3-4 — 排盘页历史回看 + 档案默认命主自动预填（用户问题 #10）node 单测
// 运行：cd miniprogram && node --test 'tests/*.test.js'
// 覆盖（B3-4 验收口径）：
//   1. 排盘页加载自动预填档案默认命主（persons.findDefault → 表单回填，与合盘页同款体验）
//   2. 历史入口（输入区 + 结果区）→ 弹层列表：谁（命主名/性别年）+ 什么时候排的 +
//      当时问的（排盘摘要）+ 一句话结论（日主/格局）——一眼认出「这是我上次排的」
//   3. 点击回看：GET /api/paipan/history/:id 完整盘面（重看 0 重跑，不再落新记录）
//   4. 称骨卡对缩减字段记录（聊天路径）隐藏空卡
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const PAIPAN_JS = path.join(__dirname, '../pages/paipan/paipan.js');
const PAIPAN_WXML = path.join(__dirname, '../pages/paipan/paipan.wxml');
const API_JS = path.join(__dirname, '../utils/api.js');

/* ── 1. 档案默认命主自动预填 ── */

test('B3-4 预填：onLoad 调用 _prefillDefaultPerson（档案默认命主自动预填）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /_prefillDefaultPerson\(\);/, 'onLoad 应调用 _prefillDefaultPerson');
  assert.match(js, /persons\.loadPersons\(\)/, '预填应走 persons.loadPersons（接口优先本地兜底）');
  assert.match(js, /persons\.findDefault\(/, '默认命主判定应复用 persons.findDefault');
});

test('B3-4 预填：_fillFromPerson 回填表单（日期/时辰序号/性别/城市 + 预填提示）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /date = `\$\{p\.birth_year\}-\$\{pad\(p\.birth_month\)\}-\$\{pad\(p\.birth_day\)\}`/, '出生日期应回填为 YYYY-MM-DD');
  assert.match(js, /lunarDateToSolar/, '农历档案应换算公历（排盘引擎只吃公历）');
  // k19：带 birth_minute 回填（精确钟表行按时钟口径映射，语义超集）
  // k77-M5 重钉：入参由"原始 p.birth_hour"改为**严格解析后的 bh**（parseHourStrict）；
  //            语义对合法档案零变化，脏值改为"视为无时辰"而不是被 parseInt 读成 0=子时。
  assert.match(js, /persons\.hourToShichenIndex\(bh,\s*p\.birth_minute\) \+ 1/, '时辰应转时辰序号（picker 下标 1-12）');
  assert.match(js, /persons\.parseHourStrict\(p\.birth_hour\)/, '预填必须走严格解析（M5 宁少不假）');
  assert.match(js, /patch\.bHourSet = true/, '有时辰应置 bHourSet');
  assert.match(js, /bGender: p\.gender === 'female' \? 'female' : 'male'/, '性别应归一为 male/female');
  assert.match(js, /split\('·'\)\.pop\(\)/, '城市请求名应取「省·市」后段裸市名');
  assert.match(js, /已从档案预填/, '应显示「已从档案预填」提示');
});

test('B3-4 预填：生辰不完整（无年月日）静默跳过，不污染表单', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /!p\.birth_year \|\| !p\.birth_month \|\| !p\.birth_day\) return/, '生辰不完整应直接 return');
});

/* ── 2. 历史入口与列表（谁 + 什么时候 + 问的什么 + 一句话结论） ── */

test('B3-4 历史：data 默认态（showHistory=false / historyList=[] / viewingHistory=false）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /showHistory: false/, '弹层默认关闭');
  assert.match(js, /historyList: \[\],/, '列表默认空');
  assert.match(js, /viewingHistory: false/, '回看标记默认 false');
});

test('B3-4 历史：onShowHistory 拉取 api.paipanHistory（只显示自己的）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /api\.paipanHistory\(\)/, '历史列表应调 GET /api/paipan/history');
  assert.match(js, /timeText: this\._timeText\(r\.created_at\)/, '每项应格式化排盘时间');
  assert.match(js, /nameText: this\._historyName\(r\)/, '每项应解析命主名（person_id → 档案姓名）');
  assert.match(js, /baziText: \(r\.bazi \|\| \[\]\)\.join\(' '\)/, '每项应展示四柱');
});

test('B3-4 历史：无档案姓名的记录用「男/女命 · 年份」兜底（一眼认出）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /\$\{genderCN\}命 · \$\{b\.year\}年/, '兜底名应含性别 + 出生年份');
});

test('B3-4 历史：onHistoryTap 按 id 拉详情完整盘面（重看 0 重跑）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /api\.paipanHistoryDetail\(rec\.id\)/, '回看应调 GET /api/paipan/history/:id');
  assert.match(js, /_buildView\(d\.chart, payload\)/, '详情盘面应走 _buildView 渲染');
  assert.match(js, /viewingHistory: true/, '回看应标记 viewingHistory');
});

test('B3-4 历史：缩减记录信息头兜底的时辰映射按时钟小时边界（与服务端 SHICHEN_NAME 同口径）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /Math\.floor\(\(\(\(parseInt\(p\.birthHour, 10\) \|\| 0\) \+ 1\) % 24\) \/ 2\)/, '时钟小时→时辰序号应走边界公式（子23-0/丑1-2/…/亥21-22）');
  assert.match(js, /HOUR_OPTIONS\[idx \+ 1\]/, '应取 HOUR_OPTIONS 时辰标签');
});

test('B3-4 历史：缩减记录信息头兜底——生肖从年柱地支推导（戊辰→龙）、农历从公历生辰换算', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /SHENGXIAO\s*=\s*\{[^}]*辰:\s*'龙'/, '应存在地支→生肖映射（含 辰→龙）');
  assert.match(js, /meta\.zodiac[\s\S]{0,40}\|\|[\s\S]{0,80}SHENGXIAO\[String\(c\.bazi\[0\]\)\[1\]\]/, '无 meta 时应从年柱地支推导生肖');
  assert.match(js, /lunar\.lunarDateText/, '农历兜底应调用 lunar.lunarDateText（公历生辰→农历文案）');
});

test('B3-4 历史：onReset 清空回看标记（重新排盘回输入态）', () => {
  const js = fs.readFileSync(PAIPAN_JS, 'utf8');
  assert.match(js, /onReset\(\)[\s\S]{0,200}viewingHistory: false/, 'onReset 应清 viewingHistory');
});

test('B3-4 历史：输入区与结果区均有「历史记录」入口', () => {
  const wxml = fs.readFileSync(PAIPAN_WXML, 'utf8');
  const m = wxml.match(/bindtap="onShowHistory"/g);
  assert.ok(m && m.length >= 2, '历史入口至少两处（输入区 + 结果区）');
});

test('B3-4 历史：弹层列表项展示 谁/时间/问的什么/一句话结论 四要素', () => {
  const wxml = fs.readFileSync(PAIPAN_WXML, 'utf8');
  assert.match(wxml, /class="ph-sheet"/, '应存在历史弹层');
  assert.match(wxml, /wx:for="\{\{historyList\}\}"/, '弹层应遍历 historyList');
  assert.match(wxml, /\{\{item\.nameText\}\}/, '列表项应显示命主名（谁）');
  assert.match(wxml, /\{\{item\.timeText\}\}/, '列表项应显示排盘时间（什么时候排的）');
  assert.match(wxml, /\{\{item\.summary\}\}/, '列表项应显示排盘摘要（当时问的）');
  assert.match(wxml, /\{\{item\.conclusion\}\}/, '列表项应显示一句话结论（日主/格局）');
  assert.match(wxml, /还没有排盘记录/, '空态文案应存在');
});

test('B3-4 历史：称骨卡对缩减字段记录（聊天路径）隐藏空卡', () => {
  const wxml = fs.readFileSync(PAIPAN_WXML, 'utf8');
  assert.match(wxml, /class="cg-card" wx:if="\{\{chenggu\.weight_text\}\}"/, '无称骨数据的记录不渲染空称骨卡');
});

/* ── 3. api.js 接线 ── */

test('B3-4 api：paipanHistory / paipanHistoryDetail 已实现并导出', () => {
  const src = fs.readFileSync(API_JS, 'utf8');
  assert.match(src, /function paipanHistory\(\)/, '应实现 paipanHistory');
  assert.match(src, /function paipanHistoryDetail\(id\)/, '应实现 paipanHistoryDetail');
  assert.match(src, /'\/api\/paipan\/history'/, '列表路径应为 /api/paipan/history');
  assert.match(src, /`\/api\/paipan\/history\/\$\{id\}`/, '详情路径应为 /api/paipan/history/:id');
  assert.match(src, /paipanHistory,/, '应导出 paipanHistory');
  assert.match(src, /paipanHistoryDetail,/, '应导出 paipanHistoryDetail');
});
