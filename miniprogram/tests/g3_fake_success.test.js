// 易理明灯 — G3 批次回归（H-3~H-13 残留假成功/假数据/本地覆盖丢数据修复）
// 运行：cd miniprogram && node --test tests/g3_fake_success.test.js
// 覆盖：
//   H-3 晨笺收藏丢数据家族：streamHost 重置/覆盖/50 条裁剪三路保留收藏条目；
//       today 收藏直连云端（成功打 favImported / 失败明示暂存本机）；dreams/history
//       续聊与删除当前会话走 persist；favorites/me 同 id 双源去重
//   H-4 第 N 晚真实推导（首夜=1，不再虚构 231）
//   H-5 today 占位标记（缺诗签/宜忌 →「· 示例文案」；接口失败 → 失败提示）
//   H-6 persons 服务端成功不再覆盖 local_ 离线条目（逐条 upsert，G1 性别中文契约）
//   H-8 me 收藏数与收藏页三源同口径（本地+后端收藏+名笺）
//   H-10 TTS 前端不再拼 127.0.0.1（相对路径 → 失败提示）
//   H-12 无咨询记录的反馈点亮 → 回滚 + 如实提示
//   H-13 合盘归档失败（reportId 空）→「暂未存入报告页」，不假成功
//   H-3① bazi 建档提示条零写入不声称已保存（镜像 G2 A4）；本地降级 toast 如实收窄
const test = require('node:test');
const assert = require('node:assert/strict');

global.getApp = () => ({ globalData: {}, loginPromise: null });

const api = require('../utils/api');
const streamHost = require('../utils/streamHost');
const persons = require('../utils/persons');

/* ── 页面配置捕获（与既有测试同款） ── */
const savedPage = global.Page;
function loadPage(rel) {
  let cfg = null;
  global.Page = (c) => { cfg = c; };
  try {
    require(rel);
  } finally {
    global.Page = savedPage;
  }
  assert.ok(cfg, `${rel} 页面配置应可加载`);
  return cfg;
}
const todayCfg = loadPage('../pages/today/today');
const meCfg = loadPage('../pages/me/me');
const baziCfg = loadPage('../pages/bazi/bazi');
const chatCfg = loadPage('../pages/chat/chat');
const hehunCfg = loadPage('../pages/hehun/hehun');
const favoritesCfg = loadPage('../pages/favorites/favorites');
const historyCfg = loadPage('../pages/history/history');
const dreamsCfg = loadPage('../pages/dreams/dreams');

/* ── setData 桩：模拟 wx 点路径赋值（如 'fb.m1-down'、'jian.saved'） ── */
function dottedSetData(upd) {
  Object.keys(upd).forEach((k) => {
    if (k.indexOf('.') === -1) { this.data[k] = upd[k]; return; }
    const parts = k.split('.');
    let cur = this.data;
    for (let i = 0; i < parts.length - 1; i++) {
      if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) cur[parts[i]] = {};
      cur = cur[parts[i]];
    }
    cur[parts[parts.length - 1]] = upd[k];
  });
}

/* ── 通用 wx 桩：store 读 + 写捕获 + toast 捕获 ── */
function makeWx(store, toasts) {
  return {
    getStorageSync: (k) => (store[k] !== undefined ? store[k] : null),
    setStorageSync: (k, v) => { store[k] = v; },
    removeStorageSync: (k) => { delete store[k]; },
    getWindowInfo: () => ({ statusBarHeight: 20 }),
    showToast: (o) => { toasts.push(o.title); },
    showModal: () => {},
    showLoading: () => {},
    hideLoading: () => {},
    navigateTo: () => {},
    navigateBack: () => {},
    reLaunch: () => {},
  };
}

function cloneData(obj) {
  return JSON.parse(JSON.stringify(obj || {}));
}

/* 清理宿主内存（setMessages 为纯覆盖，可完整清空，便于测试间隔离） */
function cleanHost() {
  streamHost.setMessages([]);
}

/* ══════════════ H-3 · streamHost 收藏保留 ══════════════ */

test('H-3 streamHost._save：晨笺收藏条目不被 50 条上限挤出', () => {
  cleanHost();
  const toasts = [];
  const store = {};
  global.wx = makeWx(store, toasts);
  const kept = Array.from({ length: 3 }, (_, i) => ({
    id: 'jian_kept_' + i, role: 'ai', type: 'jian', kept: true, content: '晨笺', time: '', keptAt: i,
  }));
  const normal = Array.from({ length: 60 }, (_, i) => ({ id: 'n' + i, role: 'ai', content: 'x', time: '', keptAt: i }));
  const unkeptJian = { id: 'jian_unkept', role: 'ai', type: 'jian', content: '取消收藏后的晨笺', time: '' }; // 无 kept → 不豁免
  const list = kept.concat([unkeptJian], normal); // 取消收藏条目置于前部 → 落在 50 条窗口外
  streamHost.setMessages(list);
  streamHost._save();
  const saved = store.ylm_chat_messages;
  assert.equal(saved.length, 53, '3 条收藏 + 普通消息末尾 50 条（61 条普通裁剪到 50）');
  assert.ok(saved.some((m) => m.id === 'jian_kept_0') && saved.some((m) => m.id === 'jian_kept_2'), '前端收藏条目保留');
  assert.ok(!saved.some((m) => m.id === 'n0') && !saved.some((m) => m.id === 'n9'), '超出 50 条的普通消息仍被裁剪');
  assert.ok(!saved.some((m) => m.id === 'jian_unkept'), '取消收藏后的 jian 条目不再豁免');
  // 顺序保持：3 条收藏 + 50 条普通尾部（无收藏插序）
  assert.equal(saved[0].id, 'jian_kept_0');
  assert.equal(saved[3].id, 'n10');
});

test('H-3 streamHost.reset：清空/新开对话保留晨笺收藏条目', () => {
  cleanHost();
  const toasts = [];
  const store = {};
  global.wx = makeWx(store, toasts);
  streamHost.setMessages([
    { id: 'a1', role: 'ai', content: 'x' },
    { id: 'j1', role: 'ai', type: 'jian', kept: true, content: '晨笺', time: '' },
    { id: 'a2', role: 'ai', content: 'y' },
  ]);
  streamHost.reset([]);
  assert.ok(streamHost.messages.some((m) => m.id === 'j1'), 'reset([]) 后宿主仍含收藏条目');
  assert.ok(!streamHost.messages.some((m) => m.id === 'a1' || m.id === 'a2'), '普通消息随对话清空');
  const saved = store.ylm_chat_messages;
  assert.deepEqual(saved.map((m) => m.id), ['j1'], 'storage 只保留收藏条目');
});

test('H-3 streamHost.persist：覆盖写保留收藏条目；写失败如实返回 false', () => {
  cleanHost();
  const toasts = [];
  const store = {};
  global.wx = makeWx(store, toasts);
  streamHost.setMessages([
    { id: 'a1', role: 'ai', content: 'x' },
    { id: 'j1', role: 'ai', type: 'jian', kept: true, content: '晨笺', time: '' },
  ]);
  const ok = streamHost.persist([{ id: 'n1', role: 'ai', content: '新会话' }]);
  assert.equal(ok, true);
  assert.deepEqual(store.ylm_chat_messages.map((m) => m.id), ['n1', 'j1']);
  assert.deepEqual(streamHost.messages.map((m) => m.id), ['n1', 'j1']);
  // 写失败 → 返回 false（G2 B3：删除/续聊 toast 以真实写成为准）
  const oldSet = wx.setStorageSync;
  wx.setStorageSync = () => { throw new Error('quota'); };
  try {
    assert.equal(streamHost.persist([]), false);
  } finally {
    wx.setStorageSync = oldSet;
  }
});

/* ══════════════ H-3 · today 收藏直连云端 ══════════════ */

function makeTodayPage(store, toasts) {
  global.wx = makeWx(store, toasts);
  const page = Object.assign({}, todayCfg);
  page.data = cloneData(todayCfg.data);
  page.setData = dottedSetData;
  page.data.jian = {
    saved: false, savedId: 'j1', date: '2026-08-29', ganzhiDate: '甲子',
    yi: ['出行'], ji: ['动土'], quote: '金句', book: '诗经',
    privateLine: '私语', question: '今天做什么?',
  };
  return page;
}

test('H-3 today.onJianFav：本地收藏之外直连云端（type=jian）；成功打 favImported', async () => {
  const toasts = [];
  const store = { ylm_chat_messages: [] };
  const page = makeTodayPage(store, toasts);
  const savedFavAdd = api.favAdd;
  const favCalls = [];
  api.favAdd = (p) => { favCalls.push(p); return Promise.resolve({}); };
  const patched = [];
  const origPatch = streamHost.patchMessage;
  streamHost.patchMessage = (id, p) => { patched.push([id, p]); return origPatch.call(streamHost, id, p); };
  try {
    page.onJianFav();
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(favCalls.length, 1);
    assert.equal(favCalls[0].type, 'jian');
    assert.equal(favCalls[0].ref_id, 'j1');
    assert.ok(favCalls[0].summary.length <= 100, 'summary 截 100 字');
    assert.ok(patched.some(([id, p]) => id === 'j1' && p.favImported === true), '同步成功 → favImported 标记');
    assert.ok(toasts.includes('已收藏 · 入笺匣'));
  } finally {
    api.favAdd = savedFavAdd;
    streamHost.patchMessage = origPatch;
  }
});

test('H-3 today.onJianFav：云端同步失败 → 明示「收藏暂存本机，云端同步失败」', async () => {
  const toasts = [];
  const store = { ylm_chat_messages: [] };
  const page = makeTodayPage(store, toasts);
  const savedFavAdd = api.favAdd;
  api.favAdd = () => Promise.reject(new Error('net'));
  try {
    page.onJianFav();
    await new Promise((r) => setTimeout(r, 10));
    assert.ok(toasts.includes('收藏暂存本机，云端同步失败'), '失败不假装已上云，toast=' + JSON.stringify(toasts));
    assert.ok(store.ylm_chat_messages.some((m) => m.id === 'j1' && m.kept), '本地收藏保留（暂存本机）');
  } finally {
    api.favAdd = savedFavAdd;
  }
});

test('H-3 today._unfavJian：取消收藏同步 DELETE（幂等，失败静默不阻断）', async () => {
  const toasts = [];
  const store = { ylm_chat_messages: [{ id: 'j1', role: 'ai', type: 'jian', kept: true, content: '晨笺', time: '' }] };
  const page = makeTodayPage(store, toasts);
  page.data.jian.saved = true;
  const savedFavRemove = api.favRemove;
  const rmCalls = [];
  api.favRemove = (t, id) => { rmCalls.push([t, id]); return Promise.resolve({}); };
  try {
    page._unfavJian();
    await new Promise((r) => setTimeout(r, 10));
    assert.deepEqual(rmCalls, [['jian', 'j1']]);
    assert.ok(!store.ylm_chat_messages.some((m) => m.id === 'j1'), '本地收藏已移除');
    assert.ok(toasts.includes('已取消收藏'));
  } finally {
    api.favRemove = savedFavRemove;
  }
});

/* ══════════════ H-3 · favorites / history / dreams 覆盖路径 ══════════════ */

test('H-3 favorites._load：同 id 双源（storage+归档）只展示一份', async () => {
  const toasts = [];
  const store = {
    ylm_chat_messages: [{ id: 'm1', role: 'ai', kept: true, content: 'a', time: '', keptAt: 1000 }],
    ylm_chat_archives: [{ id: 's1', messages: [{ id: 'm1', role: 'ai', kept: true, content: 'a', time: '', keptAt: 1000 }] }],
  };
  const savedFavList = api.favList;
  const savedMing = api.getMingSaved;
  api.favList = () => Promise.resolve({ items: [] });
  api.getMingSaved = () => Promise.resolve({ items: [] });
  try {
    global.wx = makeWx(store, toasts);
    const page = Object.assign({}, favoritesCfg);
    page.data = { cat: 'all', items: [], loaded: false };
    page.setData = dottedSetData;
    page._load();
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(page.data.items.length, 1, 'storage 与归档同 id 收藏只展示一份');
  } finally {
    api.favList = savedFavList;
    api.getMingSaved = savedMing;
  }
});

test('H-3 history.confirmDel 删除当前会话：晨笺收藏条目保留（persist 兜底）', async () => {
  cleanHost();
  const toasts = [];
  const store = {};
  global.wx = makeWx(store, toasts);
  streamHost.setMessages([{ id: 'j1', role: 'ai', type: 'jian', kept: true, content: '晨笺', time: '' }]);
  const page = Object.assign({}, historyCfg);
  page.data = cloneData(historyCfg.data);
  page.data.dlgDel = { id: 'current', isCurrent: true };
  page.setData = dottedSetData;
  page._load = () => {};
  /* k77-I4：删除改为"先删服务端再删本机" —— 本用例关心的是**本机**行为，
     故把服务端删除 stub 成成功（服务端语义另有 tests/test_k77_* 与 g2 用例覆盖） */
  const savedDel = api.deleteChatSessions;
  api.deleteChatSessions = () => Promise.resolve({ status: 'ok', deleted: 1, legacy_remaining: 0 });
  try {
    page.confirmDel();
    await new Promise((r) => setTimeout(r, 400));
    const saved = store.ylm_chat_messages;
    assert.deepEqual(saved.map((m) => m.id), ['j1'], '删除会话不抹除晨笺收藏');
    assert.ok(toasts.includes('已删除 · 夜话不留痕'));
  } finally {
    api.deleteChatSessions = savedDel;
  }
});

test('H-3 dreams.onContinue：续聊写回不覆盖宿主里的收藏条目（persist）', async () => {
  cleanHost();
  const toasts = [];
  const store = {
    ylm_chat_archives: [{ id: 's1', createdAt: Date.now(), messages: [{ id: 'a1', role: 'ai', content: '解梦结论', time: '' }] }],
  };
  global.wx = makeWx(store, toasts);
  streamHost.setMessages([{ id: 'j1', role: 'ai', type: 'jian', kept: true, content: '晨笺', time: '' }]);
  const page = Object.assign({}, dreamsCfg);
  page.data = cloneData(dreamsCfg.data);
  page.data.current = { srcId: 's1' };
  page.setData = dottedSetData;
  page.onContinue();
  const saved = store.ylm_chat_messages;
  assert.ok(saved.some((m) => m.id === 'a1'), '归档消息写回');
  assert.ok(saved.some((m) => m.id === 'j1'), '宿主里的收藏条目不被覆盖写抹除');
});

/* ══════════════ H-6 · persons 服务端成功不覆盖 local_ 离线条目 ══════════════ */

test('H-6 loadPersons：local_ 条目逐条 upsert（G1 性别中文契约），失败保留重试', async () => {
  const toasts = [];
  const store = {
    ylm_persons: [
      { id: 'local_1', name: '张三', gender: 'female', birth_year: 1990, birth_month: 5, birth_day: 12, calendar: 'solar' },
      { id: 'local_2', name: '李四', gender: 'male', birth_year: 1988, birth_month: 1, birth_day: 1, calendar: 'solar' },
    ],
  };
  global.wx = makeWx(store, toasts);
  const savedGet = api.getPersons;
  const savedCreate = api.createPerson;
  const createCalls = [];
  api.getPersons = () => Promise.resolve({ persons: [{ id: 'srv1', name: '王五', gender: '男' }] });
  api.createPerson = (p) => {
    createCalls.push(p);
    return p.name === '张三' ? Promise.resolve({ person: { id: 'new1', name: '张三' } }) : Promise.reject(new Error('net'));
  };
  try {
    const list = await persons.loadPersons();
    assert.equal(createCalls.length, 2, '两条 local_ 条目都尝试 upsert');
    assert.equal(createCalls[0].gender, '女', 'G1 契约：female → 女');
    assert.equal(createCalls[1].gender, '男', 'G1 契约：male → 男');
    assert.ok(createCalls.every((p) => ['男', '女', 'unknown'].includes(p.gender)), '绝不产出 male/female');
    assert.ok(list.some((p) => p.id === 'srv1'), '服务端列表在结果中');
    assert.ok(list.some((p) => p.id === 'local_2'), '同步失败的 local_ 条目保留在结果（等待重试）');
    assert.ok(!list.some((p) => p.id === 'local_1'), '同步成功的 local_ 条目不再返回（服务端已接管）');
    const cache = store.ylm_persons;
    assert.ok(!cache.some((p) => p.id === 'local_1'), '缓存同步移除成功条目');
    assert.ok(cache.some((p) => p.id === 'local_2'), '缓存保留失败条目');
  } finally {
    api.getPersons = savedGet;
    api.createPerson = savedCreate;
  }
});

test('H-6 loadPersons：无 local_ 条目时正常覆盖（零回归）', async () => {
  const toasts = [];
  const store = { ylm_persons: [{ id: 'p1', name: '旧' }] };
  global.wx = makeWx(store, toasts);
  const savedGet = api.getPersons;
  const savedCreate = api.createPerson;
  api.getPersons = () => Promise.resolve({ persons: [{ id: 'srv1', name: '新' }] });
  api.createPerson = () => { throw new Error('不应调用'); };
  try {
    const list = await persons.loadPersons();
    assert.deepEqual(list.map((p) => p.id), ['srv1']);
    assert.deepEqual(store.ylm_persons.map((p) => p.id), ['srv1']);
  } finally {
    api.getPersons = savedGet;
    api.createPerson = savedCreate;
  }
});

/* ══════════════ H-4 · 第 N 晚真实推导 ══════════════ */

function makeMePage(store, toasts) {
  global.wx = makeWx(store, toasts);
  const page = Object.assign({}, meCfg);
  page.data = cloneData(meCfg.data);
  page.setData = dottedSetData;
  page._buildRows = () => {}; // 行装配与本章无关
  return page;
}

test('H-4 me._deriveNights：首夜记录首见并显示第 1 晚（不再虚构 231）', () => {
  const toasts = [];
  const store = {};
  const page = makeMePage(store, toasts);
  page._deriveNights();
  assert.ok(store.ylm_first_seen, '首夜写入首见时间');
  assert.equal(page.data.nights, 1);
});

test('H-4 me._deriveNights：有首见记录 → 真实天数', () => {
  const toasts = [];
  const store = { ylm_first_seen: Date.now() - 10 * 86400000 };
  const page = makeMePage(store, toasts);
  page.data.nights = 231; // 旧假数据残留 → 被真实值覆盖
  page._deriveNights();
  assert.equal(page.data.nights, 11, '第 10 天后的夜晚 = 第 11 晚');
});

/* ══════════════ H-8 · me 收藏数三源同口径 ══════════════ */

test('H-8 me._loadFavCount：本地去重 + 后端收藏 + 名笺三源相加', async () => {
  const toasts = [];
  const store = {
    ylm_chat_messages: [
      { id: 'm1', role: 'ai', kept: true, content: 'a', time: '' },                    // 本地
      { id: 'm2', role: 'ai', type: 'jian', kept: true, content: '晨笺', time: '' },   // 本地（晨笺）
      { id: 'm3', role: 'ai', kept: true, favImported: true, content: 'b', time: '' }, // 已导入 → 不算本地
      { id: 'm4', role: 'user', kept: true, content: 'u', time: '' },                  // 用户消息不算
    ],
    ylm_chat_archives: [
      { id: 's1', messages: [{ id: 'm1', role: 'ai', kept: true, content: 'a', time: '' }, { id: 'm5', role: 'ai', kept: true, content: 'c', time: '' }] },
    ],
  };
  const savedFavList = api.favList;
  const savedMing = api.getMingSaved;
  api.favList = () => Promise.resolve({ items: [{ id: 'f1' }, { id: 'f2' }, { id: 'f3' }] });
  api.getMingSaved = () => Promise.resolve({ items: [{ id: 'g1' }] });
  try {
    const page = makeMePage(store, toasts);
    page._loadFavCount();
    await new Promise((r) => setTimeout(r, 20));
    // 本地：m1(去重双源)、m2、m5 = 3；后端收藏 3；名笺 1 → 共 7
    assert.equal(page.data.favCount, 7, '实际=' + page.data.favCount);
  } finally {
    api.favList = savedFavList;
    api.getMingSaved = savedMing;
  }
});

test('H-8 me._loadFavCount：后端失败静默降级为本地数', async () => {
  const toasts = [];
  const store = {
    ylm_chat_messages: [{ id: 'm1', role: 'ai', kept: true, content: 'a', time: '' }],
    ylm_chat_archives: [],
  };
  const savedFavList = api.favList;
  const savedMing = api.getMingSaved;
  api.favList = () => Promise.reject(new Error('net'));
  api.getMingSaved = () => Promise.reject(new Error('net'));
  try {
    const page = makeMePage(store, toasts);
    page._loadFavCount();
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(page.data.favCount, 1);
  } finally {
    api.favList = savedFavList;
    api.getMingSaved = savedMing;
  }
});

/* ══════════════ H-5 · today 占位标记 ══════════════ */

function stubFortune(resolveWith, rejectWith) {
  api.getTodayFortune = resolveWith
    ? () => Promise.resolve(resolveWith)
    : () => Promise.reject(rejectWith || new Error('net'));
}

const FORTUNE_OK = {
  suitable: ['出行', '静心'],
  personal_advice: '今日宜出行，心平气和。',
  fortune4: { career: { score: 7, desc: 'd' } },
  hourly: [],
  yi_detail: [],
  ji_detail: [],
  unsuitable: [],
};

test('H-5 today：接口有诗签 → 无占位标记', async () => {
  const toasts = [];
  const store = {};
  global.wx = makeWx(store, toasts);
  const page = Object.assign({}, todayCfg);
  page.data = cloneData(todayCfg.data);
  page.setData = dottedSetData;
  stubFortune(FORTUNE_OK);
  try {
    await page._loadFortune();
    assert.equal(page.data.placeholderNote, '');
    assert.ok(page.data.poemLines[0].indexOf('出行') >= 0 || page.data.poemLines[0].length > 2);
  } finally {
    api.getTodayFortune = () => Promise.reject(new Error('unused'));
  }
});

test('H-5 today：接口缺诗签/宜忌 → 原型兜底带「· 示例文案」标记', async () => {
  const toasts = [];
  const store = {};
  global.wx = makeWx(store, toasts);
  const page = Object.assign({}, todayCfg);
  page.data = cloneData(todayCfg.data);
  page.setData = dottedSetData;
  stubFortune(Object.assign({}, FORTUNE_OK, { personal_advice: '', suitable: [] }));
  try {
    await page._loadFortune();
    assert.equal(page.data.placeholderNote, 'example', '占位文案必须可见标记');
    assert.deepEqual(page.data.poemLines, ['雾散灯明处', '恰是归程时。'], '原型兜底文案保留');
    assert.deepEqual(page.data.yiChips, ['宜 · 安顿心事', '宜 · 早眠']);
  } finally {
    api.getTodayFortune = () => Promise.reject(new Error('unused'));
  }
});

test('H-5 today：接口失败 → 失败提示标记（不静默冒充真实运势）', async () => {
  const toasts = [];
  const store = {};
  global.wx = makeWx(store, toasts);
  const page = Object.assign({}, todayCfg);
  page.data = cloneData(todayCfg.data);
  page.setData = dottedSetData;
  stubFortune(null, new Error('network down'));
  try {
    await page._loadFortune();
    assert.equal(page.data.placeholderNote, 'error');
  } finally {
    api.getTodayFortune = () => Promise.reject(new Error('unused'));
  }
});

/* ══════════════ H-3① · bazi 假成功清理 ══════════════ */

test('H-3① bazi.onBannerTap：零写入不声称已保存（镜像 G2 A4）', async () => {
  const toasts = [];
  const store = { ylm_dlg_person_saved: { t: Date.now(), text: '' } };
  global.wx = makeWx(store, toasts);
  wx.showModal = (o) => { o.success({ confirm: true }); };
  const page = Object.assign({}, baziCfg);
  page.data = cloneData(baziCfg.data);
  page.setData = dottedSetData;
  const savedCreate = api.createPerson;
  const savedUpdate = api.updatePerson;
  let createCalled = false;
  let updateCalled = false;
  api.createPerson = () => { createCalled = true; return Promise.resolve({ person: {} }); };
  api.updatePerson = () => { updateCalled = true; return Promise.resolve({ person: {} }); };
  try {
    page.onBannerTap();
    assert.ok(toasts.includes('已为你标记，可在档案页查看'), 'toast=' + JSON.stringify(toasts));
    assert.equal(createCalled, false, '提示条点击绝不调用建档接口（标记里无出生数据，前端零写入）');
    assert.equal(updateCalled, false);
    assert.ok(!store.ylm_dlg_person_saved, '确认后清除标记');
  } finally {
    api.createPerson = savedCreate;
    api.updatePerson = savedUpdate;
  }
});

test('H-3① bazi._confirmSaveToArc：接口失败本地降级 toast 如实收窄（联网后自动同步）', async () => {
  const toasts = [];
  const store = { ylm_persons: [] };
  global.wx = makeWx(store, toasts);
  wx.showModal = (o) => { o.success({ confirm: true }); };
  const page = Object.assign({}, baziCfg);
  page.data = cloneData(baziCfg.data);
  page.data.mDate = '1990-05-12';
  page.data.mCal = 'solar';
  page.data.mHourIndex = 3;
  page.data.mGender = 'female';
  page.data.mPlace = '北京';
  page.data.mName = '';
  page.data.saveToArc = true;
  page.setData = dottedSetData;
  const savedCreate = api.createPerson;
  api.createPerson = () => Promise.reject(new Error('net'));
  try {
    page._confirmSaveToArc();
    await new Promise((r) => setTimeout(r, 10));
    assert.ok(toasts.some((t) => t.indexOf('已保存到本机档案') === 0), 'toast=' + JSON.stringify(toasts));
    assert.ok(toasts.some((t) => t.indexOf('联网后自动同步') > 0), '同步承诺收窄为「联网后自动同步」（H-6 有真实实现）');
    assert.ok(store.ylm_persons.some((p) => String(p.id).indexOf('local_') === 0), '本地降级条目已落缓存（H-6 可被服务端接管）');
  } finally {
    api.createPerson = savedCreate;
  }
});

/* ══════════════ H-12 · 无咨询记录反馈不点亮 ══════════════ */

function makeChatPage(store, toasts) {
  global.wx = makeWx(store, toasts);
  const page = Object.assign({}, chatCfg);
  page.data = cloneData(chatCfg.data);
  page.data.fb = {};
  page.data.messages = [];
  page.setData = dottedSetData;
  page._audioCtx = null;
  return page;
}

test('H-12 chat._toggleFbCore：无咨询记录 → 回滚点亮 + 如实提示', async () => {
  const toasts = [];
  const store = {};
  const page = makeChatPage(store, toasts);
  page.data.messages = [{ id: 'm1', role: 'ai', content: 'x', consultationId: null }];
  page._toggleFbCore('m1', 'up');
  assert.equal(page.data.fb['m1-up'], false, '不点亮（点亮即假成功）');
  assert.ok(toasts.includes('反馈未提交：该回复缺少咨询记录'), 'toast=' + JSON.stringify(toasts));
});

test('H-12 chat._toggleFbCore：有咨询记录上报；失败回滚（G2 B1 回归）', async () => {
  const toasts = [];
  const store = {};
  const page = makeChatPage(store, toasts);
  page.data.messages = [{ id: 'm1', role: 'ai', content: 'x', consultationId: 7 }];
  const savedFb = api.feedback;
  api.feedback = () => Promise.reject(new Error('net'));
  try {
    page._toggleFbCore('m1', 'down');
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(page.data.fb['m1-down'], false, '上报失败 → 回滚');
    assert.ok(toasts.includes('反馈失败，请重试'));
  } finally {
    api.feedback = savedFb;
  }
});

/* ══════════════ H-10 · TTS 前端不再拼 127.0.0.1 ══════════════ */

test('H-10 chat._playWithTts：相对路径不再拼 127.0.0.1 → 失败提示', async () => {
  const toasts = [];
  const store = {};
  const page = makeChatPage(store, toasts);
  let played = null;
  page._audioCtx = { src: '', play: () => { played = page._audioCtx.src; }, stop: () => {}, destroy: () => {}, onError: () => {}, onEnded: () => {}, onStop: () => {} };
  const savedTts = api.tts;
  api.tts = () => Promise.resolve({ audio_url: '/audio/x.mp3' }); // 相对路径（后端未配 PUBLIC_BASE_URL 的异常态）
  try {
    await page._playWithTts('m1', '内容', true);
    assert.equal(played, null, '相对路径绝不播放');
    assert.ok(toasts.includes('语音合成失败'), 'toast=' + JSON.stringify(toasts));
    assert.equal(page.data.speakingId, '', '播放态回退');
  } finally {
    api.tts = savedTts;
  }
});

test('H-10 chat._playWithTts：完整 URL 正常播放（不回归）', async () => {
  const toasts = [];
  const store = {};
  const page = makeChatPage(store, toasts);
  let played = null;
  page._audioCtx = { src: '', play: () => { played = page._audioCtx.src; }, stop: () => {}, destroy: () => {}, onError: () => {}, onEnded: () => {}, onStop: () => {} };
  const savedTts = api.tts;
  api.tts = () => Promise.resolve({ audio_url: 'https://yilichat.com/audio/x.mp3' });
  try {
    await page._playWithTts('m1', '内容', true);
    assert.equal(played, 'https://yilichat.com/audio/x.mp3');
    assert.ok(!toasts.includes('语音合成失败'));
  } finally {
    api.tts = savedTts;
  }
});

/* ══════════════ H-13 · 合盘归档失败不假成功 ══════════════ */

function makeHehunPage(store, toasts) {
  global.wx = makeWx(store, toasts);
  const page = Object.assign({}, hehunCfg);
  page.data = cloneData(hehunCfg.data);
  page.setData = dottedSetData;
  return page;
}

test('H-13 hehun._setPaidResult：reportId 空（后端归档失败）→ 如实提示未存入', () => {
  const toasts = [];
  const store = {};
  const page = makeHehunPage(store, toasts);
  page._setPaidResult({ report: { chapters: [{ t: '一' }, { t: '二' }, { t: '三' }] }, reportId: '' });
  assert.equal(page.data.showReport, true, '报告本体仍内联展示');
  assert.ok(toasts.includes('报告已生成 · 暂未存入报告页'), 'toast=' + JSON.stringify(toasts));
});

test('H-13 hehun._setPaidResult：reportId 存在 → 已存入报告页', () => {
  const toasts = [];
  const store = {};
  const page = makeHehunPage(store, toasts);
  page._setPaidResult({ report: { chapters: [{ t: '一' }] }, reportId: 'r1' });
  assert.ok(toasts.includes('已存入报告页'));
});
