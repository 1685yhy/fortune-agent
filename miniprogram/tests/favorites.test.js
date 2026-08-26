// 易理明灯 — 收藏页展示层剥卡片标记（批次 2 B3-24）
// 运行：node --test miniprogram/tests/favorites.test.js
// 覆盖：本地兜底条目 content 剥标记（晨笺解析仍走原始文本）；
//       远程条目 summary 剥标记（后端 summary 是原始内容切片，含 [card:…]）；
//       用户消息不收录（零回归）；导入侧不动（_tryImportLocal 仍传原始 content）。
const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../utils/api');

// 页面前置：两条后端异步链 stub 为空，聚焦本地兜底展示断言
const savedFavList = api.favList;
const savedMingSaved = api.getMingSaved;
api.favList = () => Promise.resolve({ items: [] });
api.getMingSaved = () => Promise.resolve({ items: [] });

const savedPage = global.Page;
const savedWx = global.wx;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/favorites/favorites');
} finally {
  global.Page = savedPage;
  global.wx = savedWx;
}
assert.ok(pageCfg && typeof pageCfg._load === 'function', 'favorites.js 页面配置应可加载');

function makePage(store) {
  global.wx = {
    getStorageSync: (k) => (store[k] || []),
    setStorageSync: () => {},
    getWindowInfo: () => ({ statusBarHeight: 20 }),
    showToast: () => {},
    showModal: () => {},
  };
  const page = Object.assign({}, pageCfg);
  page.data = { cat: 'all', items: [], loaded: false };
  page.setData = function (upd) { Object.assign(this.data, upd); };
  return page;
}

test('收藏页本地兜底条目：content 剥卡片标记，晨笺解析走原始文本', async () => {
  const store = {
    ylm_chat_messages: [
      { id: 'm1', role: 'ai', kept: true, content: '[card:paipan title="我的命盘"]\n日主甲木。\n[/card]\n尾', time: '', keptAt: 1000 },
      { id: 'm2', type: 'jian', role: 'ai', kept: true, content: '晨笺 · 甲子\n宜:出行 忌:动土\n"金句"——《诗经》', time: '', keptAt: 2000 },
      { id: 'm3', role: 'user', kept: true, content: '用户消息不该被收藏' },
    ],
    ylm_chat_archives: [],
  };
  const page = makePage(store);
  page._load();
  await new Promise((r) => setTimeout(r, 0));   // 等两条后端异步链 settle
  const items = page.data.items;
  const chat = items.find((it) => it.id === 'm1');
  assert.equal(chat.content, '日主甲木。\n尾');              // 标记剥净
  assert.ok(chat.content.indexOf('[card:') === -1);
  const jian = items.find((it) => it.id === 'm2');
  assert.ok(jian.isJian && jian.jian && jian.jian.date === '甲子');  // 晨笺解析自原始文本
  assert.equal(jian.content, '晨笺 · 甲子\n宜:出行 忌:动土\n"金句"——《诗经》'); // 无标记原样
  assert.ok(!items.find((it) => it.id === 'm3'));            // 用户消息不收录
});

test('收藏页远程条目（_remoteItem）：summary 含标记 → 展示剥净', () => {
  const it = pageCfg._remoteItem({
    type: 'chat', ref_id: 'm9',
    summary: '正文[card:paipan title="我的命盘"]\n内容\n[/card]尾',
    created_at: '2026-08-26 10:00:00',
  });
  assert.equal(it.content, '正文内容尾');
  assert.ok(it.content.indexOf('[card:') === -1);
  // 晨笺远程条目走摘要卡片分支（fallback 标记不可缺——wxml 依赖）
  const jian = pageCfg._remoteItem({ type: 'jian', ref_id: 'j1', summary: '晨笺 · 甲子', created_at: '' });
  assert.equal(jian.content, '晨笺 · 甲子');
  assert.deepEqual(jian.jian, { fallback: true });
});

test('收藏页导入侧不动：_tryImportLocal summary 仍传原始 content（含标记）', async () => {
  // 后端要存原文（远程条目展示层才剥），导入侧改样会破坏「远程=本地原文切片」
  // 的后端一致性 —— 固化为回归断言
  const store = {
    ylm_chat_messages: [
      { id: 'm1', role: 'ai', kept: true, content: '[card:paipan title="x"]\n正文\n[/card]', time: '', keptAt: 1 },
    ],
    ylm_chat_archives: [],
  };
  const page = makePage(store);
  const importCalls = [];
  api.favImport = (items) => { importCalls.push(items); return Promise.resolve({ imported: 1 }); };
  page._markImported = () => {};
  page._tryImportLocal();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(importCalls.length, 1);
  assert.equal(importCalls[0][0].ref_id, 'm1');
  assert.equal(importCalls[0][0].type, 'chat');
  assert.equal(importCalls[0][0].summary, '[card:paipan title="x"]\n正文\n[/card]');  // 原始含标记
});
