// 易理明灯 — 名人命盘 B5-3（批次 5 M）前端逻辑测试
// 运行：node --test tests/mingren_chart.test.js（miniprogram 目录下）
// 覆盖：mingren_detail 命盘卡映射 buildChartView（四柱/十神/藏干/纳音/meta/起运/
//       时辰标注）、详情 _load 注入 chart+timeline+hasChart、无 chart 名人只有生平；
//       mingren 列表 has_chart 徽标透出。
const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../utils/api');

const savedPage = global.Page;
const savedWx = global.wx;
let detailCfg = null;
global.Page = (c) => { detailCfg = c; };
try {
  require('../pages/mingren_detail/mingren_detail');
} finally {
  global.Page = savedPage;
  global.wx = savedWx;
}
assert.ok(detailCfg && typeof detailCfg.buildChartView === 'function',
  'mingren_detail.js 页面配置应可加载且暴露 buildChartView');

const CHART = {
  bazi: ['戊辰', '壬戌', '丁丑', '丙午'],
  day_master: '丁火',
  pillars: [
    { name: '年柱', ganzhi: '戊辰', gan: '戊', zhi: '辰', shishen: '伤官', is_day: false,
      canggan: [{ gan: '戊', shishen: '伤官' }, { gan: '乙', shishen: '偏印' }, { gan: '癸', shishen: '七杀' }],
      nayin: '大林木', xingyun: '衰' },
    { name: '日柱', ganzhi: '丁丑', gan: '丁', zhi: '丑', shishen: '日主', is_day: true,
      canggan: [{ gan: '己', shishen: '食神' }], nayin: '涧下水', xingyun: '墓' },
  ],
  meta: {
    solar_text: '1328年10月21日',
    shichen: '午时',
    zodiac: '龙',
    lunar: { year_ganzhi: '戊辰', month_ganzhi: '壬戌', day_ganzhi: '丁丑',
             month_text: '九月', day_text: '十八' },
  },
  qiyun: { desc: '出生后2年11月24天5时起运', detail: [2, 11, 24, 5, 44], start_sui: 4, start_year: 1331 },
  dayun: [{ sui: 4, end_sui: 13, ganzhi: '癸亥', shishen: '七杀', start_year: 1331, end_year: 1340 }],
  hour_note: '时辰无考，以午时推演',
};

test('buildChartView：命盘卡字段映射（四柱/藏干/纳音/meta/起运/时辰标注）', () => {
  const v = detailCfg.buildChartView(CHART);
  assert.ok(v, '有 chart → 返回视图');
  assert.equal(v.dayMaster, '丁火');
  assert.equal(v.pillars.length, 2);
  const p0 = v.pillars[0];
  assert.equal(p0.name, '年柱');
  assert.equal(p0.gan, '戊');
  assert.equal(p0.isDay, false);
  assert.equal(v.pillars[1].isDay, true);
  assert.equal(p0.canggan.length, 3);
  assert.equal(p0.canggan[2].shishen, '七杀');
  assert.equal(p0.nayin, '大林木');
  assert.equal(v.zodiac, '龙');
  assert.equal(v.shichen, '午时');
  assert.equal(v.solarText, '1328年10月21日');
  assert.equal(v.lunarText, '戊辰年九月十八');
  assert.equal(v.hourNote, '时辰无考，以午时推演');
  assert.equal(v.qiyun.startSui, 4);
  assert.equal(v.qiyun.startYear, 1331);
  assert.ok(v.qiyun.desc.includes('起运'));
});

test('buildChartView：无 chart → null（无生辰名人详情不渲染命盘区）', () => {
  assert.equal(detailCfg.buildChartView(null), null);
  assert.equal(detailCfg.buildChartView(undefined), null);
});

test('详情 _load：chart/timeline/hasChart 注入 detail，无 chart 只有生平', async () => {
  const savedGet = api.getMingrenDetail;
  const savedSetTitle = global.wx && global.wx.setNavigationBarTitle;
  try {
    const calls = [];
    api.getMingrenDetail = (name) => {
      calls.push(name);
      if (name === '朱元璋') {
        return Promise.resolve({
          name: '朱元璋', info: '明朝开国皇帝', flist: [
            { name: '1328年', data: '出生。' }, { name: '1352年', data: '投义军。' }],
          has_chart: true, chart: CHART, birth_text: '1328年10月21日',
          birth_source: '《明史》',
          timeline: [
            { status: 'before', label: '未起运', dayun: null, events: [{ year: 1328, event: '出生。' }] },
            { status: 'dayun', dayun: { ganzhi: '乙丑', start_year: 1351, end_year: 1360, shishen: '偏印' },
              events: [{ year: 1352, event: '投义军。' }] },
          ],
        });
      }
      return Promise.resolve({ name: '孔子', info: '至圣先师', flist: [], has_chart: false });
    };
    global.wx = {
      setNavigationBarTitle: () => {},
      showToast: () => {}, navigateBack: () => {}, reLaunch: () => {},
    };
    const page = Object.assign({}, detailCfg);
    page.data = JSON.parse(JSON.stringify(detailCfg.data));
    page.setData = function (upd) { Object.assign(this.data, upd); };
    page.onLoad({ name: '朱元璋' });
    await new Promise((r) => setTimeout(r, 0));  // 微任务链 settle（_load 无页面定时器）
    assert.deepEqual(calls, ['朱元璋']);
    assert.equal(page.data.detail.hasChart, true);
    assert.equal(page.data.detail.chart.dayMaster, '丁火');
    assert.equal(page.data.detail.chart.hourNote, '时辰无考，以午时推演');
    assert.equal(page.data.detail.timeline.length, 2);
    assert.equal(page.data.detail.timeline[0].status, 'before');
    assert.equal(page.data.detail.birthSource, '《明史》');
    // 无 chart 名人：hasChart=false 且 chart=null（照旧只生平）
    page.onLoad({ name: '孔子' });
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(page.data.detail.hasChart, false);
    assert.equal(page.data.detail.chart, null);
  } finally {
    api.getMingrenDetail = savedGet;
    global.wx = savedWx;
  }
});

test('列表 has_chart 徽标透出：服务端布尔 → item.hasChart', async () => {
  let listCfg = null;
  global.Page = (c) => { listCfg = c; };
  try {
    require('../pages/mingren/mingren');
  } finally {
    global.Page = savedPage;
  }
  assert.ok(listCfg && typeof listCfg._load === 'function', 'mingren.js 页面配置应可加载');
  const savedList = api.listMingren;
  try {
    api.listMingren = () => Promise.resolve({
      items: [
        { name: '忽必烈', info: '元世祖', info2: '-', has_info2: false, has_chart: true },
        { name: '孔子', info: '至圣先师', info2: '徐乐吾曰…', has_info2: true, has_chart: false },
      ],
      total: 2, total_all: 2, library_total: 2807, is_full: true,
    });
    const page = Object.assign({}, listCfg);
    page.data = JSON.parse(JSON.stringify(listCfg.data));
    page.setData = function (upd) { Object.assign(this.data, upd); };
    await page._load(false);
    assert.equal(page.data.items[0].hasChart, true);
    assert.equal(page.data.items[1].hasChart, false);
    assert.equal(page.data.items[1].hasInfo2, true);
  } finally {
    api.listMingren = savedList;
  }
});
