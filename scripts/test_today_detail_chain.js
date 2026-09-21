// scripts/test_today_detail_chain.js
// Task 2 前端逻辑测试（离线）：今日页 _loadFortune 数据链 → goTodayDetail 透传 →
// 今日详解页 _render / 时辰高亮 / onHourTap。
// 运行：node scripts/test_today_detail_chain.js （PASS/FAIL，失败 exit 1）
'use strict';

const Module = require('module');
const path = require('path');
const origLoad = Module._load;

// ── mock wx 全局 ──
let storage = {};
global.wx = {
  getStorageSync: (k) => storage[k],
  setStorageSync: (k, v) => { storage[k] = v; },
  getSystemInfoSync: () => ({ statusBarHeight: 47, theme: 'light' }),
  request: () => {},
  navigateTo: () => {},
  showToast: () => {},
};
global.getApp = () => ({});
global.__wxConfig = { envVersion: 'develop' };

// ── 拦截 today/today_detail 的 api 依赖 + streamHost 的 api 依赖 ──
let fakeApi = null;
Module._load = function (request, parent, isMain) {
  if (parent && /pages\/today\/today\.js$|pages\/today_detail\/today_detail\.js$/.test(parent.filename)
      && request === '../../utils/api') {
    return fakeApi;
  }
  if (request === './api' && parent && /streamHost\.js$/.test(parent.filename)) {
    return { chatStream: async () => ({ abort() {} }), chat: async () => ({}) };
  }
  return origLoad.apply(this, arguments);
};

let pageOrder = [];
global.Page = (cfg) => { pageOrder.push(cfg); };

// 假 api 需在 require 页面文件前就位（页面顶层 require('../../utils/api') 被拦截）
fakeApi = { getTodayFortune: async () => fakeTodayRes() };
require(path.resolve(__dirname, '../miniprogram/pages/today/today.js'));
require(path.resolve(__dirname, '../miniprogram/pages/today_detail/today_detail.js'));
const todayCfg = pageOrder[0];
const detailCfg = pageOrder[1];

let ok = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { ok++; console.log('PASS:', name); }
  else { fail++; console.log('FAIL:', name + (detail ? ' — ' + detail : '')); }
}

// ── setData（含 dotted path）──
function makeInstance(cfg) {
  const inst = Object.create(cfg);
  inst.data = JSON.parse(JSON.stringify(cfg.data || {}));
  inst.setData = function (patch) {
    for (const k of Object.keys(patch)) {
      const v = patch[k];
      if (k.includes('.')) {
        const parts = k.split('.');
        let o = this.data;
        for (let i = 0; i < parts.length - 1; i++) {
          if (!o[parts[i]] || typeof o[parts[i]] !== 'object') o[parts[i]] = {};
          o = o[parts[i]];
        }
        o[parts[parts.length - 1]] = v;
      } else {
        this.data[k] = v;
      }
    }
  };
  return inst;
}

// ── 与产品端一致的当前时辰公式（子时=0..亥时=11）──
function shichenNowIdx() {
  const h = new Date().getHours();
  if (h === 23) return 0;
  return Math.floor((h + 1) / 2);
}

// ── 假后端响应（与 /api/calendar/today 真实契约同构）──
function fakeTodayRes() {
  const hourly = [];
  const times = ['子时 23-01', '丑时 01-03', '寅时 03-05', '卯时 05-07', '辰时 07-09',
    '巳时 09-11', '午时 11-13', '未时 13-15', '申时 15-17', '酉时 17-19', '戌时 19-21', '亥时 21-23'];
  times.forEach((t, i) => hourly.push({ time: t, tag: '宜' + (i + 1), desc: `第${i + 1}个时辰的吉凶解读。` }));
  return {
    date: '2026-08-15',
    day_ganzhi: '庚申日',
    day_wuxing: '金',
    score: 72,
    stars: 4,
    suitable: ['洽谈', '出行', '早起'],
    unsuitable: ['借贷', '熬夜'],
    yi_detail: [
      { action: '洽谈', time: '巳时9-11点', reason: '官星得地' },
      { action: '出行', time: '午时11-13点', reason: '气行通畅' },
      { action: '早起', time: '辰时7-9点', reason: '晨气清爽' },
    ],
    ji_detail: [
      { action: '借贷', time: '全天', reason: '财星坐库' },
      { action: '熬夜', time: '子时23点后', reason: '伤神损运' },
    ],
    fortune4: {
      career: { score: 7.2, desc: '官星得地，贵人星动。宜主动开口。' },
      wealth: { score: 6.8, desc: '财星坐库，稳稳当当。偏财勿贪。' },
      love: { score: 5.5, desc: '今日情绪偏低沉，话留到傍晚再说。' },
      health: { score: 7.0, desc: '精神头足，宜早活动筋骨。' },
    },
    hourly,
    personal_advice: '今日宜谈合作。',
    mood_reminder: '保持好心情。',
    overall_mood: '平稳向好',
    lucky_color: '金色', lucky_number: '4', lucky_direction: '西',
    lunar_date: '六月廿九',
  };
}

// ═══ 1. 今日页数据链 ═══
fakeApi = { getTodayFortune: async () => fakeTodayRes() };
const today = makeInstance(todayCfg);
(async () => {
  await today._loadFortune();

  // 四运
  const f4 = today.data.fortune4;
  check('四运 4 行', f4.length === 4, JSON.stringify(f4));
  check('四运顺序与标签', f4.map((x) => x.label).join(',') === '事业,财运,感情,健康');
  check('四运分数保留 1 位小数', f4.map((x) => x.scoreText).join(',') === '7.2,6.8,5.5,7.0');
  check('四运进度条百分值', f4.map((x) => x.percent).join(',') === '72,68,55,70');
  check('四运 desc 透传', f4[0].desc === '官星得地，贵人星动。宜主动开口。');

  // 时辰择时：当前时辰起 3 个
  const cur = shichenNowIdx();
  const sc = today.data.shichen;
  check('时辰择时 3 个 chip', sc.length === 3, JSON.stringify(sc));
  check('时辰择时从当前时辰起', sc[0].time === fakeTodayRes().hourly[cur].time, sc[0].time);
  check('时辰 chip 带 tag/desc/cur', sc[0].tag && sc[0].desc && sc[0].cur === true);
  check('时辰 chip 环形取数', sc[2].time === fakeTodayRes().hourly[(cur + 2) % 12].time);

  // 宜忌详解透传
  check('yiDetail 3 条', today.data.yiDetail.length === 3 && today.data.yiDetail[0].action === '洽谈');
  check('jiDetail 2 条', today.data.jiDetail.length === 2 && today.data.jiDetail[0].reason === '财星坐库');

  // 4 路入口 handler 存在（wxml 绑定的方法）
  check('goTodayDetail 入口存在', typeof today.goTodayDetail === 'function');
  today.goTodayDetail.call(today);
  const payload = storage['ylm_today_detail'];
  check('透传 storage 写入', !!payload && payload.dayGanzhi === '庚申日' && payload.score === 72);
  check('透传含四运/时辰/宜忌', !!payload.fortune4 && payload.hourly.length === 12
    && payload.yiDetail.length === 3 && payload.jiDetail.length === 2);

  // ═══ 2. 今日详解页渲染 ═══
  fakeApi = { getTodayFortune: async () => fakeTodayRes() };
  const detail = makeInstance(detailCfg);
  detail.onLoad.call(detail);

  check('详解头部干支', detail.data.dateLine.includes('庚申日'), detail.data.dateLine);
  // 农历按日期实算（2026-08-15 = 农历七月初三日，与 lunar util 一致；
  // 日名口径同引擎 zeri/wannianli._lunar_day_cn —— k70-F1）
  const lunar = require(path.resolve(__dirname, '../miniprogram/utils/lunar.js'));
  const expLunar = lunar.formatLunarDate(2026, 8, 15);
  check('详解头部农历（实算一致）', detail.data.dateLine.includes(expLunar),
    `expect ${expLunar}, got ${detail.data.dateLine}`);
  check('详解综合分', detail.data.scoreText.includes('72'));
  check('详解宜忌 chips', detail.data.yiChips.join(',') === '洽谈,出行,早起'
    && detail.data.jiChips.join(',') === '借贷,熬夜');
  check('详解四运 4 条', detail.data.fortune4.length === 4
    && detail.data.fortune4[3].scoreText === '7.0');
  check('详解宜忌逐条含 reason', detail.data.yiDetail[0].reason === '官星得地');
  check('时辰 12 条', detail.data.hourly.length === 12);
  check('当前时辰高亮', detail.data.curHourIdx === cur, `curHourIdx=${detail.data.curHourIdx}`);
  check('默认选中当前时辰', detail.data.selHourIdx === cur && detail.data.selHourDesc
    === detail.data.hourly[cur].desc);

  // 点其它时辰 → 切换解读
  detail.onHourTap.call(detail, { currentTarget: { dataset: { idx: 9 } } });
  check('onHourTap 切换时辰解读', detail.data.selHourIdx === 9
    && detail.data.selHourDesc === detail.data.hourly[9].desc);

  // ═══ 3. 透传缺失 → 自拉 API 兜底 ═══
  storage = {};
  fakeApi = { getTodayFortune: async () => fakeTodayRes() };
  const detail2 = makeInstance(detailCfg);
  await detail2._fetch.call(detail2);
  check('无透传自拉 API 兜底', detail2.data.fortune4.length === 4 && detail2.data.curHourIdx === cur);

  console.log(`\n${ok} PASS / ${fail} FAIL`);
  process.exit(fail === 0 ? 0 : 1);
})();
