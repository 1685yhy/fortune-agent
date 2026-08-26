// 易理明灯 — 长按收藏后端兜底同步（批次 2 B3-25）
// 运行：node --test miniprogram/tests/chatkeep.test.js
// 覆盖：actFeedback keep → 本地 kept + 后端 favAdd（与收藏页 _tryImportLocal 同
//       口径：type=chat、ref_id=id 截 128、summary=原始内容截 100）→ 成功后打
//       favImported（收藏页本地兜底不重复展示）；unkeep → 后端 favRemove；
//       用户消息不同步（收藏页/后端本就只收纳 AI 回复）；favAdd 失败静默不崩；
//       multiFav 批量收藏逐条同步（已收藏的不重复同步）。
const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../utils/api');
const streamHost = require('../utils/streamHost');

const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/chat/chat');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg._syncKeepBackend === 'function', 'chat.js 页面配置应可加载');

/* ── 记录桩：patchMessage / favAdd / favRemove 调用留痕 ── */
const patches = [];
const favAddCalls = [];
const favRemoveCalls = [];
const origPatchMessage = streamHost.patchMessage;
const origFavAdd = api.favAdd;
const origFavRemove = api.favRemove;
function installStubs() {
  patches.length = 0;
  favAddCalls.length = 0;
  favRemoveCalls.length = 0;
  streamHost.patchMessage = (id, patch) => { patches.push([id, patch]); };
  api.favAdd = (data) => { favAddCalls.push(data); return Promise.resolve({ success: true, already: false }); };
  api.favRemove = (type, refId) => { favRemoveCalls.push([type, refId]); return Promise.resolve({ success: true }); };
}
function restoreStubs() {
  streamHost.patchMessage = origPatchMessage;
  api.favAdd = origFavAdd;
  api.favRemove = origFavRemove;
}

function makePage(msg, msgs) {
  global.wx = { showToast: () => {} };
  const page = Object.assign({}, pageCfg);
  page.data = {
    actionMenu: { show: true, msgId: msg ? msg.id : '', role: msg ? msg.role : '' },
    messages: msgs || [],
    multiSel: {},
    multiCount: 0,
    multiAll: false,
  };
  page.setData = function (upd) { Object.assign(this.data, upd); };
  page._findMessage = (id) => (msgs || []).find((m) => m.id === id) || (msg && msg.id === id ? msg : null);
  return page;
}

test('actFeedback keep：本地 kept + 后端 favAdd 同 import 口径 + 成功后 favImported', async () => {
  installStubs();
  try {
    const msg = { id: 'm1', role: 'ai', kept: false, content: '[card:paipan title="x"]\n正文\n[/card]' };
    const page = makePage(msg, [msg]);
    page.actFeedback({ currentTarget: { dataset: { k: 'keep' } } });
    // 同步部分：本地 kept 标记先落
    assert.equal(patches[0][0], 'm1');
    assert.equal(patches[0][1].kept, true);
    assert.ok(patches[0][1].keptAt > 0);
    // 后端 favAdd：type/ref_id/summary 与收藏页 _tryImportLocal 同口径
    assert.equal(favAddCalls.length, 1);
    assert.deepEqual(favAddCalls[0], {
      type: 'chat',
      ref_id: 'm1',
      summary: '[card:paipan title="x"]\n正文\n[/card]',   // 原始内容切片（后端存原文）
    });
    await new Promise((r) => setTimeout(r, 0));
    // 成功后打 favImported（收藏页本地兜底不重复展示）
    assert.deepEqual(patches[patches.length - 1], ['m1', { favImported: true }]);
  } finally {
    restoreStubs();
  }
});

test('actFeedback unkeep：本地清标记 + 后端 favRemove', async () => {
  installStubs();
  try {
    const msg = { id: 'm1', role: 'ai', kept: true, content: 'x' };
    const page = makePage(msg, [msg]);
    page.actFeedback({ currentTarget: { dataset: { k: 'keep' } } });
    assert.deepEqual(patches[0], ['m1', { kept: false, keptAt: 0 }]);
    assert.deepEqual(favRemoveCalls, [['chat', 'm1']]);
    await new Promise((r) => setTimeout(r, 0));   // 不崩
  } finally {
    restoreStubs();
  }
});

test('actFeedback keep 用户消息：本地 kept 行为不变，不同步后端', async () => {
  installStubs();
  try {
    const msg = { id: 'u1', role: 'user', kept: false, content: '你好' };
    const page = makePage(msg, [msg]);
    page.actFeedback({ currentTarget: { dataset: { k: 'keep' } } });
    assert.equal(patches[0][1].kept, true);        // 本地行为照旧
    assert.equal(favAddCalls.length, 0);           // 后端不收纳用户消息
    await new Promise((r) => setTimeout(r, 0));
  } finally {
    restoreStubs();
  }
});

test('favAdd 失败静默：不崩、不打 favImported（收藏页首启导入仍可兜底）', async () => {
  installStubs();
  api.favAdd = (data) => { favAddCalls.push(data); return Promise.reject(new Error('network')); };
  try {
    const msg = { id: 'm2', role: 'ai', kept: false, content: 'x' };
    const page = makePage(msg, [msg]);
    page.actFeedback({ currentTarget: { dataset: { k: 'keep' } } });
    await new Promise((r) => setTimeout(r, 0));
    assert.ok(!patches.some((p) => p[0] === 'm2' && p[1].favImported));
  } finally {
    restoreStubs();
  }
});

test('multiFav：批量收藏逐条本地标记 + 后端同步（已收藏的不重复同步）', async () => {
  installStubs();
  try {
    const msgs = [
      { id: 'a1', role: 'ai', kept: false, content: '回复一' },
      { id: 'a2', role: 'ai', kept: true, content: '回复二' },   // 已收藏 → 不重复同步
    ];
    const page = makePage(null, msgs);
    page.data.multiSel = { a1: true, a2: true };
    page.multiFav();
    assert.ok(favAddCalls.some((c) => c.ref_id === 'a1'));
    assert.ok(!favAddCalls.some((c) => c.ref_id === 'a2'));
    assert.equal(patches[0][0], 'a1');
    assert.equal(patches[0][1].kept, true);
    await new Promise((r) => setTimeout(r, 0));   // 不崩
  } finally {
    restoreStubs();
  }
});
