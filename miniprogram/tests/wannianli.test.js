// 易理明灯 — 万年历页 B5-2 L（swiper 三页翻月 + 今日默认选中 + 摘要行 + 复制导出）
// 运行：node --test tests/wannianli.test.js（miniprogram 目录下）
// 覆盖：onLoad 今日默认落地（当月三页预渲染 + selectedDate=今日 + 中心标题）；
//       请求序号防旧响应覆盖（快速翻月时旧批丢弃；摘要行迟到今日摘要不覆盖选中日）；
//       onSwiperChange 滑动切月移位补页（前进/后退/越界弹回）；
//       onTapDay 选中态 + 摘要行同步 + 详情拉取；
//       _buildCopyText 复制文本（日期/宜忌/吉时仅吉/彭祖百忌/胎神）；
//       按钮翻月边界（1900/2100 提示）。
const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../utils/api');

const savedPage = global.Page;
const savedWx = global.wx;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/wannianli/wannianli');
} finally {
  global.Page = savedPage;
  global.wx = savedWx;
}
assert.ok(pageCfg && typeof pageCfg.onLoad === 'function', 'wannianli.js 页面配置应可加载');

function makePage() {
  global.wx = {
    getWindowInfo: () => ({ statusBarHeight: 20 }),
    getSystemInfoSync: () => ({ statusBarHeight: 20 }),
    showToast: () => {},
    setClipboardData: (o) => o.success && o.success({}),
    navigateBack: () => {},
    reLaunch: () => {},
  };
  const page = Object.assign({}, pageCfg);
  page.data = JSON.parse(JSON.stringify(pageCfg.data));
  page.setData = function (upd) { Object.assign(this.data, upd); };
  return page;
}

const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

// 假月视图（后端 month_view 口径）：days_in_month 天，首日周日对齐
function fakeMonth(y, m, daysInMonth = 30) {
  const days = [];
  for (let d = 1; d <= daysInMonth; d++) {
    const date = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    days.push({
      date, day: d, cell_lunar: `初${d}`, lunar_day: `初${d}`,
      day_ganzhi: '甲子', jieqi: '', festival: '',
      yi_short: ['祈福', '求嗣'], ji_short: ['出行'],
      huanghedao: '黄道', tianshen: '明堂', jianchu: '建',
      quality: '吉', is_today: false,
    });
  }
  return { year: y, month: m, days_in_month: daysInMonth, first_weekday: 0, today: `${y}-${String(m).padStart(2, '0')}-01`, days };
}

// 假日详情（后端 day_detail 口径，含 B5-2 三字段）
function fakeDetail(date) {
  return {
    date, weekday: '三', jieqi: '', festivals: [],
    lunar: { year: '丙午年', month: '七月', day: '初七', leap: false, full: '丙午年七月 初七' },
    ganzhi: { year: '丙午', month: '丙申', day: '乙丑' },
    nayin: { year: '天河水', month: '', day: '海中金' },
    jianchu: { name: '执', quality: '吉', desc: '持守进退' },
    huanghedao: { type: '黄道', tianshen: '明堂', luck: '吉' },
    ershibaxiu: { name: '轸', jixiong: '吉' },
    yi: ['祈福', '求嗣', '订婚', '嫁娶', '出行', '求财'],
    ji: ['开市', '入宅'],
    jishen: ['明堂'], xiongsha: [],
    chong: { desc: '', zodiac: '羊', ganzhi: '己未', sha: '东' },
    xunkong: ['戌', '亥'],
    positions: { cai: '东北', xi: '西北', fu: '西南', yang_gui: '东北', yin_gui: '正南' },
    jishi: [
      { time: '早子时', range: '00:00-00:59', ganzhi: '丙子', luck: '凶', tianshen: '天刑', type: '黑道', yi: [], ji: [] },
      { time: '巳时', range: '09:00-10:59', ganzhi: '辛巳', luck: '吉', tianshen: '玉堂', type: '黄道', yi: ['祈福'], ji: [] },
      { time: '晚子时', range: '23:00-23:59', ganzhi: '戊子', luck: '吉', tianshen: '青龙', type: '黄道', yi: [], ji: [] },
    ],
    pengzu: { gan: '乙不栽植千株不长', zhi: '丑不冠带主不还乡' },
    taishen: { desc: '碓磨厕 外东南' },
  };
}

function clock() {
  const now = new Date();
  return {
    y: now.getFullYear(),
    m: now.getMonth() + 1,
    md: String(now.getMonth() + 1).padStart(2, '0'),
    dd: String(now.getDate()).padStart(2, '0'),
  };
}

function offMonth(y, m, off) {
  let yy = y, mm = m + off;
  while (mm < 1) { mm += 12; yy -= 1; }
  while (mm > 12) { mm -= 12; yy += 1; }
  return `${yy}-${mm}`;   // 与页面 months[].key 格式一致（`${y}-${m}` 不补零）
}

test('onLoad：今日默认落地 —— 三页预渲染 + selectedDate=今日 + 中心标题', async (t) => {
  t.mock.method(api, 'getWannianliMonth', (y, m) => Promise.resolve(fakeMonth(y, m)));
  t.mock.method(api, 'getWannianliDay', (date) => Promise.resolve(fakeDetail(date)));
  const page = makePage();
  page.onLoad();
  assert.equal(page.data.swiperCurrent, 1, 'swiper 定位于中心页');
  const c = clock();
  assert.equal(page.data.year, c.y, '进入默认当前年');
  assert.equal(page.data.month, c.m, '进入默认当前月');
  assert.equal(page.data.selectedDate, `${c.y}-${c.md}-${c.dd}`, '默认选中今日');
  assert.equal(api.getWannianliMonth.mock.calls.length, 3, '三页预渲染各拉一次');
  assert.equal(api.getWannianliDay.mock.calls.length, 1, '未选中日 → 拉今日摘要');
  assert.equal(api.getWannianliDay.mock.calls[0].arguments[0],
    `${c.y}-${c.md}-${c.dd}`, '摘要先取今日');

  await flush();
  const months = page.data.months;
  assert.deepEqual(months.map((m) => (m ? m.key : null)),
    [offMonth(c.y, c.m, -1), offMonth(c.y, c.m, 0), offMonth(c.y, c.m, 1)],
    '三页 = 上/当/下月');
  assert.equal(page.data.monthText, `${c.y}年${c.m}月`, '中心月标题');
  assert.equal(page.data.loading, false, '中心页就绪后撤初始骨架');
  const centerCells = months[1].cells.filter((x) => !x.blank);
  assert.equal(centerCells.length, 30);
  const todayCell = centerCells.find((x) => x.date === page.data.selectedDate);
  assert.ok(todayCell && todayCell.isToday && todayCell.isSelected, '今日格 isToday+isSelected');
  assert.ok(centerCells.find((x) => x.isSelected && x.date === page.data.selectedDate));
  assert.ok(!centerCells.find((x) => x.isSelected && x.date !== page.data.selectedDate),
    '仅今日格被选中');
  // 摘要行 = 今日详情 yi/ji 前 4 项
  assert.equal(page.data.summary.title, '今日宜忌速览');
  assert.deepEqual(page.data.summary.yi, ['祈福', '求嗣', '订婚', '嫁娶']);
  assert.deepEqual(page.data.summary.ji, ['开市', '入宅']);
});

test('请求序号：快速翻月后旧响应丢弃，不覆盖新月', async (t) => {
  const pending = [];
  t.mock.method(api, 'getWannianliMonth',
    () => new Promise((res) => pending.push(res)));
  t.mock.method(api, 'getWannianliDay', () => Promise.resolve(fakeDetail('2000-01-01')));
  const page = makePage();
  page.onLoad();                       // 批1（token=1）: 3 个挂起请求
  page.onNextMonth();                  // 批2（token=2）: 3 个新请求，批1 作废
  assert.equal(api.getWannianliMonth.mock.calls.length, 6);
  // 先到批1 旧响应（月标题写 2026 年 1 月）→ 应被丢弃
  pending.slice(0, 3).forEach((res) => res(fakeMonth(2026, 1, 28)));
  await flush();
  const c = clock();
  const cur = offMonth(c.y, c.m, 1);   // onNextMonth 后的当前月
  assert.equal(page.data.year, Number(cur.split('-')[0]));
  assert.equal(page.data.month, Number(cur.split('-')[1]), '翻到下月');
  const months = page.data.months;
  assert.deepEqual(months.map((m) => (m ? m.key : null)),
    [offMonth(c.y, c.m, 0), cur, offMonth(c.y, c.m, 2)],
    '新月三页已就位（批1 旧响应未污染）');
  assert.equal(months[1].loading, true, '新月仍在加载（旧响应未写入）');
  assert.ok(months.every((m) => m.cells.length === 0), '旧月宫格未串入新月');
  assert.equal(page.data.monthText, '', '新月标题未被旧响应覆盖');
  // 批2 响应到达 → 正常渲染
  pending.slice(3).forEach((res) => res(fakeMonth(2026, 2, 28)));
  await flush();
  assert.equal(page.data.months[1].loading, false, '新月渲染完成');
  assert.equal(page.data.months[1].cells.length, 28);
  assert.equal(page.data.monthText, '2026年2月');
});

test('onSwiperChange：滑到第 3 页 → 前进一月移位补页回中', async (t) => {
  t.mock.method(api, 'getWannianliMonth', (y, m) => Promise.resolve(fakeMonth(y, m)));
  t.mock.method(api, 'getWannianliDay', () => Promise.resolve(fakeDetail('2000-01-01')));
  const page = makePage();
  page.onLoad();
  await flush();
  const c = clock();
  const before = page.data.months.map((m) => m.key);
  page.onSwiperChange({ detail: { current: 2 } });
  assert.equal(page.data.year, c.y);
  assert.equal(page.data.month, (c.m % 12) + 1, '前进一月');
  assert.deepEqual(page.data.months.map((m) => (m ? m.key : null)),
    [before[1], before[2], offMonth(c.y, c.m, 2)], '移位: 旧当/下 → 新上/当，远端补新页');
  assert.equal(page.data.swiperCurrent, 1, '回中');
  assert.equal(api.getWannianliMonth.mock.calls.length, 4, '远端新页补拉一次');
  const farY = offMonth(c.y, c.m, 2).split('-').map(Number);
  assert.equal(api.getWannianliMonth.mock.calls[3].arguments[0], farY[0], '远端补拉年份');
  assert.equal(api.getWannianliMonth.mock.calls[3].arguments[1], farY[1], '远端补拉月份');
  await flush();
  assert.equal(page.data.months[2].loading, false, '远端页渲染完成');
});

test('onSwiperChange：滑到第 1 页 → 后退一月；越界弹回', async (t) => {
  t.mock.method(api, 'getWannianliMonth', (y, m) => Promise.resolve(fakeMonth(y, m)));
  t.mock.method(api, 'getWannianliDay', () => Promise.resolve(fakeDetail('2000-01-01')));
  const page = makePage();
  page.onLoad();
  await flush();
  const c = clock();
  const before = page.data.months.map((m) => m.key);
  page.onSwiperChange({ detail: { current: 0 } });
  const farBack = offMonth(c.y, c.m, -2);
  assert.deepEqual(page.data.months.map((m) => (m ? m.key : null)),
    [farBack, before[0], before[1]], '后退一月移位补远端');
  // 边界: 1900 年 1 月再退 → 越界弹回
  page.setData({ year: 1900, month: 1 });
  page.setData({ months: [null, null, null] });
  page.onSwiperChange({ detail: { current: 0 } });
  assert.equal(page.data.swiperCurrent, 1, '越界弹回中心');
});

test('onTapDay：选中态 + 摘要行同步 + 详情打开', async (t) => {
  t.mock.method(api, 'getWannianliMonth', (y, m) => Promise.resolve(fakeMonth(y, m)));
  t.mock.method(api, 'getWannianliDay', (date) => Promise.resolve(fakeDetail(date)));
  const page = makePage();
  page.onLoad();
  await flush();
  page.onTapDay({ currentTarget: { dataset: { date: '2000-05-05', blank: false } } });
  assert.equal(page.data.selectedDate, '2000-05-05');
  assert.equal(page.data.detailVisible, true);
  assert.equal(page.data.detailLoading, true);
  await flush();
  assert.equal(page.data.detailDate, '2000-05-05');
  assert.equal(page.data.detail.jishi.length, 3);
  assert.equal(page.data.detail.pengzu.gan, '乙不栽植千株不长');
  assert.equal(page.data.detail.taishen.desc, '碓磨厕 外东南');
  assert.equal(page.data.summary.title, '选中日宜忌速览');
  assert.deepEqual(page.data.summary.yi, ['祈福', '求嗣', '订婚', '嫁娶']);
  // 同一天重开不重复请求
  page.onCloseDetail();
  assert.equal(page.data.detailVisible, false);
  const before = api.getWannianliDay.mock.calls.length;
  page.onTapDay({ currentTarget: { dataset: { date: '2000-05-05', blank: false } } });
  assert.equal(page.data.detailVisible, true);
  assert.equal(api.getWannianliDay.mock.calls.length, before, '同日重开不重复拉取');
});

test('摘要请求序号：迟到的今日摘要不覆盖选中日摘要（含迟到失败不弹窗）', async (t) => {
  const dayPending = [];
  let toasts = 0;
  t.mock.method(api, 'getWannianliMonth', (y, m) => Promise.resolve(fakeMonth(y, m)));
  t.mock.method(api, 'getWannianliDay',
    () => new Promise((res, rej) => dayPending.push({ res, rej })));
  const page = makePage();
  global.wx.showToast = () => { toasts += 1; };
  page.onLoad();                       // 今日摘要请求挂起（未返回）
  assert.equal(api.getWannianliDay.mock.calls.length, 1);
  assert.equal(page.data.summaryLoading, true, '摘要行加载中');
  // 挂起期间用户点选另一天 → 详情请求（新用户意图作废旧摘要请求）
  page.onTapDay({ currentTarget: { dataset: { date: '2000-05-05', blank: false } } });
  assert.equal(api.getWannianliDay.mock.calls.length, 2);
  dayPending[1].res(fakeDetail('2000-05-05'));   // 详情先返回 → 选中日摘要
  await flush();
  assert.equal(page.data.summary.title, '选中日宜忌速览', '详情同步写选中日摘要');
  assert.equal(page.data.summary.date, '2000-05-05');
  assert.equal(page.data.summaryLoading, false);
  // 迟到的今日摘要成功返回 → 必须丢弃（不覆盖选中日）
  dayPending[0].res(fakeDetail(page.data.todayDate));
  await flush();
  assert.equal(page.data.summary.title, '选中日宜忌速览', '迟到今日摘要未覆盖选中日');
  assert.equal(page.data.summary.date, '2000-05-05', '摘要日期仍为选中日');
  // 迟到摘要失败 → 不弹 toast、不动已有摘要
  const page2 = makePage();
  page2.onLoad();
  assert.equal(api.getWannianliDay.mock.calls.length, 3);
  page2.onTapDay({ currentTarget: { dataset: { date: '2000-06-06', blank: false } } });
  assert.equal(api.getWannianliDay.mock.calls.length, 4);
  const before = toasts;
  dayPending[2].rej(new Error('net'));           // 过期摘要请求失败
  await flush();
  assert.equal(toasts, before, '过期摘要失败不弹 toast');
  assert.equal(page2.data.summary, null, '过期失败不写摘要');
  assert.equal(page2.data.summaryLoading, true, '加载态仍由在途详情请求决定');
});

test('_buildCopyText：日期+宜忌+吉时(仅吉)+彭祖百忌+胎神', () => {
  const page = makePage();
  const text = page._buildCopyText(fakeDetail('2026-08-19'));
  assert.ok(text.includes('【万年历】2026-08-19 星期三'));
  assert.ok(text.includes('宜：祈福、求嗣、订婚、嫁娶、出行、求财'));
  assert.ok(text.includes('忌：开市、入宅'));
  assert.ok(text.includes('吉时：巳时 09:00-10:59、晚子时 23:00-23:59'),
    '吉时仅列 luck=吉 时辰');
  assert.ok(!text.includes('早子时'), '凶时辰不进吉时行');
  assert.ok(text.includes('彭祖百忌：乙不栽植千株不长；丑不冠带主不还乡'));
  assert.ok(text.includes('胎神：碓磨厕 外东南'));
});

test('按钮翻月边界：1900/2100 提示不换月', (t) => {
  t.mock.method(api, 'getWannianliMonth', () => Promise.resolve(fakeMonth(2000, 1, 31)));
  const page = makePage();
  page.setData({ year: 1900, month: 1 });
  page.onPrevMonth();
  assert.equal(page.data.year, 1900, '1900-01 不后退');
  page.setData({ year: 2100, month: 12 });
  page.onNextMonth();
  assert.equal(page.data.year, 2100, '2100-12 不前进');
});
