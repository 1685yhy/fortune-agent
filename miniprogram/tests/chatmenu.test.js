// 易理明灯 — k6 波2 P4.1 重新生成 / P4.2 查看引用 / P4.3 footer 复制钮（k10-D 接线）
// 运行：node --test miniprogram/tests/chatmenu.test.js
// 覆盖：
//   1. onBubbleLongPress：末条 AI + 非错误/非流式/宿主空闲 → canRegen；非末条 AI → false
//   2. canRegen 守卫：error / streaming / streamHost.active / 无 retryText → false
//   3. citations 非空 → canCite；空/无 → false；user 消息两者恒 false
//   4. 菜单「重新生成」→ streamHost.retry(原 id / retryText / tag)（直接执行无确认）
//   5. 菜单「查看引用」→ 引用抽屉打开（与角标 onCiteTap 同链 _openCiteDrawer）
//   6. footer 复制钮 → stripCardMarkers 后写剪贴板；多选模式不响应
//   7. wxml 静态断言：菜单增补行 / footer 复制钮存在
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const streamHost = require('../utils/streamHost');

const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/chat/chat');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg.footerCopyText === 'function', 'chat.js 页面配置应可加载');

const savedWx = global.wx;
const savedGetApp = global.getApp;
function installWx(stub) {
  global.wx = stub;
  global.getApp = () => ({});
}
function restoreGlobals() {
  global.wx = savedWx;
  global.getApp = savedGetApp;
}

function makePage(overrides) {
  const calls = { toast: [], clipboard: [], retry: [] };
  installWx({
    showToast: (o) => calls.toast.push(o),
    setClipboardData: (o) => calls.clipboard.push(o.data),
    vibrateShort: () => {},
  });
  const page = Object.assign({}, pageCfg);
  page.data = Object.assign({
    multiMode: false,
    selectMsgId: '',
    actionMenu: { show: false, msgId: '', role: '', paraKey: '', canRegen: false, canCite: false },
    citeDrawer: { show: false, full: false, msgId: '', items: [] },
    messages: [],
  }, overrides || {});
  page.setData = function (upd) { Object.assign(this.data, upd); };
  page._findMessage = (id) => page.data.messages.find((m) => m.id === id) || null;
  return { page, calls };
}

function ai(id, extra) {
  return Object.assign({ id, role: 'ai', tag: '明灯 · 此笺', content: '回复正文', retryText: '提问原文',
    time: '12:00', error: false, streaming: false, citations: [] }, extra || {});
}

test('P4.1 可见条件：末条 AI（非失败/非流式/空闲/有 retryText）→ canRegen', () => {
  const { page } = makePage({
    messages: [ai('a1', { content: '旧回复' }), ai('a2')],
  });
  // a2 = 末条 AI → 可重新生成
  page.onBubbleLongPress({ currentTarget: { dataset: { id: 'a2', role: 'ai' } } });
  assert.equal(page.data.actionMenu.canRegen, true);
  // a1 = 非末条 AI → 不可见（重生成只对末条语义成立）
  page.onBubbleLongPress({ currentTarget: { dataset: { id: 'a1', role: 'ai' } } });
  assert.equal(page.data.actionMenu.canRegen, false);
});

test('P4.1 可见条件守卫：error / streaming / 宿主 active / 无 retryText → false', () => {
  const cases = [
    { msg: ai('a1', { error: true }) },
    { msg: ai('a1', { streaming: true }) },
    { msg: ai('a1', { retryText: '' }) },
  ];
  cases.forEach(({ msg }) => {
    const { page } = makePage({ messages: [msg] });
    page.onBubbleLongPress({ currentTarget: { dataset: { id: 'a1', role: 'ai' } } });
    assert.equal(page.data.actionMenu.canRegen, false, `守卫应拦 ${JSON.stringify(msg)}`);
  });
  // 宿主生成中（active = getter → streaming）→ 不可见（防静默空点——host 内部对
  // retry 静默 return）
  const { page } = makePage({ messages: [ai('a1')] });
  const prevStreaming = streamHost.streaming;
  streamHost.streaming = true;
  try {
    page.onBubbleLongPress({ currentTarget: { dataset: { id: 'a1', role: 'ai' } } });
    assert.equal(page.data.actionMenu.canRegen, false, '宿主 active → 菜单不可见');
  } finally {
    streamHost.streaming = prevStreaming;
  }
});

test('P4.2 可见条件：citations 非空 → canCite；空/缺 → false；user 消息恒 false', () => {
  const withCite = makePage({ messages: [ai('a1', { citations: [{ title: '古籍', text: 'x' }] })] });
  withCite.page.onBubbleLongPress({ currentTarget: { dataset: { id: 'a1', role: 'ai' } } });
  assert.equal(withCite.page.data.actionMenu.canCite, true);
  const emptyCite = makePage({ messages: [ai('a1', { citations: [] })] });
  emptyCite.page.onBubbleLongPress({ currentTarget: { dataset: { id: 'a1', role: 'ai' } } });
  assert.equal(emptyCite.page.data.actionMenu.canCite, false, '空 citations → 不可见');
  const noCite = makePage({ messages: [ai('a1')] });
  noCite.page.onBubbleLongPress({ currentTarget: { dataset: { id: 'a1', role: 'ai' } } });
  assert.equal(noCite.page.data.actionMenu.canCite, false);
  // user 消息：AI 专属行恒不出现（role 判定）
  const userMsg = { id: 'u1', role: 'user', content: '我的问题' };
  const up = makePage({ messages: [userMsg] });
  up.page.onBubbleLongPress({ currentTarget: { dataset: { id: 'u1', role: 'user' } } });
  assert.equal(up.page.data.actionMenu.canRegen, false);
  assert.equal(up.page.data.actionMenu.canCite, false);
});

test('菜单「重新生成」→ streamHost.retry(原 id / retryText / tag)，直接执行', () => {
  const origRetry = streamHost.retry;
  const retryCalls = [];
  streamHost.retry = (...a) => { retryCalls.push(a); };
  installWx({ showToast: () => {} });
  streamHost.active = false;
  const page = Object.assign({}, pageCfg);
  page.data = {
    multiMode: false,
    messages: [ai('a1', { tag: '今日 · 日运' })],
    actionMenu: { show: true, msgId: 'a1', role: 'ai', paraKey: '', canRegen: true, canCite: false },
  };
  page.setData = function (u) { Object.assign(this.data, u); };
  page._findMessage = (id) => page.data.messages.find((m) => m.id === id) || null;
  try {
    page.actItem({ currentTarget: { dataset: { k: 'regen' } } });
  } finally {
    streamHost.retry = origRetry;
    restoreGlobals();
  }
  assert.equal(retryCalls.length, 1);
  assert.equal(retryCalls[0][0], 'a1', '重新生成应移除原消息并重流（retry 首参=原消息 id）');
  assert.equal(retryCalls[0][1], '提问原文');
  assert.equal(retryCalls[0][2], '今日 · 日运');
  assert.equal(page.data.actionMenu.show, false, '点击后菜单应关闭');
});

test('菜单「查看引用」→ 打开引用抽屉（与角标同链）', () => {
  const cites = [{ title: '命理古籍', text: '原文……', type: 'book' }];
  const { page, calls } = makePage({ messages: [ai('a1', { citations: cites })] });
  page.data.actionMenu = { show: true, msgId: 'a1', role: 'ai', paraKey: '', canRegen: true, canCite: true };
  page.actItem({ currentTarget: { dataset: { k: 'cite' } } });
  assert.equal(page.data.citeDrawer.show, true);
  assert.equal(page.data.citeDrawer.msgId, 'a1');
  assert.equal(page.data.citeDrawer.items.length, 1);
  assert.equal(calls.toast.length, 0);
  // 空态双保险：直接 _openCiteDrawer 空列表 → toast
  const e2 = makePage({ messages: [ai('a2', { citations: [] })] });
  e2.page._openCiteDrawer('a2');
  assert.equal(e2.page.data.citeDrawer.show, false);
  assert.ok(e2.calls.toast.some((t) => (t.title || '').indexOf('暂无参考资料') !== -1));
});

test('footer 复制钮：stripCardMarkers 后写剪贴板；多选模式不响应', () => {
  const { page, calls } = makePage({
    messages: [{ id: 'a1', role: 'ai', content: '[card:paipan title="盘"]\n正文甲\n[/card] 想复制这段' }],
  });
  page.footerCopyText({ currentTarget: { dataset: { id: 'a1' } } });
  assert.equal(calls.clipboard.length, 1);
  assert.equal(calls.clipboard[0], '正文甲 想复制这段', '复制口径=stripCardMarkers（标记不暴露）');
  // 多选模式：不响应
  page.data.multiMode = true;
  page.footerCopyText({ currentTarget: { dataset: { id: 'a1' } } });
  assert.equal(calls.clipboard.length, 1);
});

test('wxml 静态断言：菜单增补行（重新生成/查看引用）与 footer 复制钮存在', () => {
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/chat/chat.wxml'), 'utf8');
  assert.ok(/data-k="regen"[\s\S]*?重新生成/.test(wxml), '菜单应有「重新生成」行');
  assert.ok(wxml.indexOf('actionMenu.canRegen') !== -1, '重新生成可见条件应接 actionMenu');
  assert.ok(/data-k="cite"[\s\S]*?查看引用/.test(wxml), '菜单应有「查看引用」行');
  assert.ok(wxml.indexOf('actionMenu.canCite') !== -1, '查看引用可见条件应接 actionMenu');
  assert.ok(wxml.indexOf('bindtap="footerCopyText"') !== -1, 'footer 应挂复制钮');
  assert.ok(wxml.indexOf('/assets/images/ic-copy.png') !== -1, '复制钮用 ic-copy 图标');
  // P4.4 判断留档：user 气泡 footer 无钮（与元宝一致）——user 分支无 jz-foot 复制钮
  const userBlock = wxml.slice(wxml.indexOf('role === "user"'));
  assert.ok(userBlock.indexOf('footerCopyText') === -1, 'user 气泡不得挂 footer 复制钮（P4.4）');
});
