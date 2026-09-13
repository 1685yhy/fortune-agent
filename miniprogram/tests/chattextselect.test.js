// 易理明灯 — k10-C 文字选取：段落模型（chatSelect util）+ 甲/乙 降级链（页面接线）
// 运行：node --test miniprogram/tests/chattextselect.test.js
// 覆盖：
//   1. util：md 多段/粗体/引用/列表/代码 → 段落键与纯文本（'md:<i>'）
//   2. util：卡片消息 pre/card/tail 三 zone 拼接与偏移；user 消息单段 user:0
//   3. util：每个 para.start/end 在全文 text 内精确切出 para.text（含 \n\n 分隔）
//   4. util：复制本段文本与「全文切片」同源（stripCardMarkers 同款清洗）
//   5. 页面：段落长按 → 冒泡菜单携带 paraKey；跨气泡陈旧命中被丢弃
//   6. 页面：菜单「复制本段」→ 剪贴板写入被按段全文（mock clipboard）
//   7. 页面：菜单「选取文字」段落路径 → iOS 平台 → 自动降甲（高亮 + 无 toast）
//   8. 页面：乙可行（android + 测量可用）→ selOverlay 打开、选区=段落偏移
//   9. 页面：无段落长按（空白区）→ 旧语义 toast + 全泡 selectable
//   10. 页面：选取模式中段落长按静默（不覆盖原生选择簿记）
//   11. 页面：onListTap 退出选取 → selParaKey 同步清空
const test = require('node:test');
const assert = require('node:assert/strict');
const md = require('../utils/md');
const chatSelect = require('../utils/chatSelect');

/* ── chat.js 页面配置加载 ── */
const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/chat/chat');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg.onParaLongPress === 'function', 'chat.js 页面配置应可加载');

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

function makePage(overrides, wxStub) {
  const calls = { toast: [], vibrate: [], clipboard: [] };
  installWx(Object.assign({
    showToast: (o) => calls.toast.push(o),
    vibrateShort: () => calls.vibrate.push(1),
    setClipboardData: (o) => calls.clipboard.push(o.data),
    getDeviceInfo: () => ({ platform: 'ios' }),   // k47-B：源码改用 wx.getDeviceInfo
  }, wxStub || {}));
  const page = Object.assign({}, pageCfg);
  page.data = Object.assign({
    multiMode: false,
    selectMsgId: '',
    selParaKey: '',
    selOverlay: { show: false, msgId: '', role: '', text: '', start: 0, end: 0, top: 0, left: 0, width: 0, height: 0, focus: false },
    actionMenu: { show: false, msgId: '', role: '', paraKey: '' },
    inputMode: 'text',
    inputText: '',
    messages: [],
  }, overrides || {});
  page.setData = function (upd, cb) { Object.assign(this.data, upd); if (typeof cb === 'function') cb(); };
  page._findMessage = (id) => page.data.messages.find((m) => m.id === id) || null;
  return { page, calls };
}

function aiMsg(text, extra) {
  return Object.assign({ id: 'a1', role: 'ai', tag: '明灯 · 此笺', content: text, time: '12:00', citations: [] }, extra || {});
}
const MD_TEXT = '第一段文字。\n\n第二段有**加粗**和[1]引用。\n\n> 引用行一\n> 引用行二\n\n1. 甲选项\n2. 乙选项';
const MD_BLOCKS = md.parseMd(MD_TEXT);

test('util：md 段落模型（p/h/quote/ol）键与文本', () => {
  const model = chatSelect.paragraphModel(aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS }));
  assert.equal(model.text, '第一段文字。\n\n第二段有加粗和[1]引用。\n\n引用行一\n引用行二\n\n1. 甲选项\n2. 乙选项');
  assert.deepEqual(model.paras.map((p) => p.key), ['md:0', 'md:1', 'md:2', 'md:3']);
  assert.equal(model.byKey['md:1'].text, '第二段有加粗和[1]引用。');
  assert.equal(model.byKey['md:2'].text, '引用行一\n引用行二');
  assert.equal(model.byKey['md:3'].text, '1. 甲选项\n2. 乙选项');
});

test('util：偏移精确性——每个段落在全文内按 start/end 切出原文', () => {
  const model = chatSelect.paragraphModel(aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS }));
  model.paras.forEach((p) => {
    assert.equal(model.text.slice(p.start, p.end), p.text, `段落 ${p.key} 偏移应精确`);
  });
  assert.ok(model.byKey['md:3'].start > model.byKey['md:2'].end, '后段起始应大于前段结束');
});

test('util：卡片消息 pre/card/tail 三 zone 拼接；user 消息单段', () => {
  const cardMsg = aiMsg('x', {
    card: { type: 'paipan', title: '我的命盘' },
    cardPrefixNodes: md.parseMd('前置引导。'),
    cardNodes: md.parseMd('卡片正文一。\n\n卡片正文二。'),
    cardTailNodes: md.parseMd('还想了解。'),
  });
  const cmod = chatSelect.paragraphModel(cardMsg);
  assert.equal(cmod.text, '前置引导。\n\n卡片正文一。\n\n卡片正文二。\n\n还想了解。');
  assert.deepEqual(cmod.paras.map((p) => p.key), ['pre:0', 'card:0', 'card:1', 'tail:0']);
  cmod.paras.forEach((p) => assert.equal(cmod.text.slice(p.start, p.end), p.text));
  // user 消息：stripCardMarkers 同款清洗（含行首行尾标记剥除）+ 单段 user:0
  const um = chatSelect.paragraphModel({ id: 'u1', role: 'user', content: ' [card:data]\n正文\n[/card] 我这段' });
  assert.equal(um.text, '正文 我这段');
  assert.equal(um.byKey['user:0'].text, um.text);
  assert.equal(um.byKey['user:0'].start, 0);
});

test('util：段落文本与 stripCardMarkers 同源清洗（卡片标记不暴露）', () => {
  // 卡片正文段不含 [card:…] 装饰（由渲染节点构建，天然无标记）
  const cardMsg = aiMsg('x', {
    card: { type: 'yunshi', title: 'x' },
    cardNodes: md.parseMd('内容不准确这条要选。'),
  });
  const model = chatSelect.paragraphModel(cardMsg);
  assert.ok(model.text.indexOf('[card:') === -1, '段落文本不得含卡片标记');
  assert.equal(model.byKey['card:0'].text, '内容不准确这条要选。');
});

/* ── 页面接线 ── */

test('段落长按 → 冒泡菜单携带 paraKey；跨气泡陈旧命中丢弃', () => {
  const m1 = aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS });
  const m2 = aiMsg('别的回复。', { id: 'a2', mdNodes: md.parseMd('别的回复。') });
  const { page } = makePage({ messages: [m1, m2] });
  // 长按 a1 的第 2 段 → 菜单 paraKey=md:1
  page.onParaLongPress({ currentTarget: { dataset: { msgid: 'a1', paraId: 'md:1' } } });
  page.onBubbleLongPress({ currentTarget: { dataset: { id: 'a1', role: 'ai' } } });
  assert.equal(page.data.actionMenu.show, true);
  assert.equal(page.data.actionMenu.paraKey, 'md:1');
  // 陈旧命中（先按 a1 段落，再长按 a2 空白区开菜单）→ 不串消息
  page.onParaLongPress({ currentTarget: { dataset: { msgid: 'a1', paraId: 'md:1' } } });
  page.onBubbleLongPress({ currentTarget: { dataset: { id: 'a2', role: 'ai' } } });
  assert.equal(page.data.actionMenu.paraKey, '', '段落键不得跨气泡串用');
});

test('菜单「复制本段」→ 剪贴板写入被按段全文', () => {
  const m1 = aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS });
  const { page, calls } = makePage({ messages: [m1] });
  page.data.actionMenu = { show: true, msgId: 'a1', role: 'ai', paraKey: 'md:1' };
  try {
    page.actItem({ currentTarget: { dataset: { k: 'copyPara' } } });
  } finally {
    restoreGlobals();
  }
  assert.equal(calls.clipboard.length, 1);
  assert.equal(calls.clipboard[0], '第二段有加粗和[1]引用。');
});

test('菜单「选取文字」段落路径 + iOS → 自动降甲（高亮 + 引导，无 toast）', () => {
  const m1 = aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS });
  const { page, calls } = makePage({ messages: [m1] });
  page.data.actionMenu = { show: true, msgId: 'a1', role: 'ai', paraKey: 'md:1' };
  try {
    page.actItem({ currentTarget: { dataset: { k: 'select' } } });
  } finally {
    restoreGlobals();
  }
  assert.equal(page.data.selectMsgId, 'a1', '甲：全泡可原生选择');
  assert.equal(page.data.selParaKey, 'md:1', '甲：段落高亮定位');
  assert.equal(page.data.selOverlay.show, false, 'iOS 平台：不尝试乙覆盖层（平台限制注释）');
  assert.equal(calls.toast.length, 0, '甲路径不再 toast 打断（引导小字常驻）');
});

test('乙可行（android + 测量可用 + 实验开关）→ 覆盖层打开且选区=段落偏移', () => {
  const m1 = aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS });
  const rect = { top: 200, left: 40, width: 300, height: 220 };
  const { page } = makePage({ messages: [m1] }, {
    getDeviceInfo: () => ({ platform: 'android' }),   // k47-B：源码改用 wx.getDeviceInfo
  });
  // 乙默认关闭（TEXT_SEL_ENGINE='a'）——本用例经实例实验开关置 'b' 验证乙路径本体
  page._textSelEngine = 'b';
  page.createSelectorQuery = () => ({
    select() { return this; },
    boundingClientRect(cb) { this._cb = cb; return this; },
    exec() { this._cb && this._cb(rect); },
  });
  page.data.actionMenu = { show: true, msgId: 'a1', role: 'ai', paraKey: 'md:1' };
  try {
    page.actItem({ currentTarget: { dataset: { k: 'select' } } });
  } finally {
    restoreGlobals();
  }
  assert.equal(page.data.selOverlay.show, true, '乙覆盖层应打开');
  const o = page.data.selOverlay;
  assert.equal(o.msgId, 'a1');
  const para = chatSelect.paragraphModel(m1).byKey['md:1'];
  assert.equal(o.start, para.start);
  assert.equal(o.end, para.end);
  assert.equal(o.text.slice(o.start, o.end), '第二段有加粗和[1]引用。');
  assert.deepEqual([o.top, o.left, o.width, o.height], [200, 40, 300, 220]);
  assert.equal(o.focus, true);
  assert.equal(page.data.selParaKey, 'md:1');
});

test('乙默认关闭（拍板）：android 全条件满足亦走甲——实例开关未置 b 时恒不可行', () => {
  const model = chatSelect.paragraphModel(aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS }));
  const msg = aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS });
  const { page } = makePage({}, { getDeviceInfo: () => ({ platform: 'android' }) });
  try {
    page.createSelectorQuery = () => ({});
    // 默认引擎 'a'：即使 android + 测量可用 + 段落存在 → 乙不可行（全平台甲）
    assert.equal(page._textOverlayFeasible(msg, model, model.byKey['md:1']), false,
      '乙默认关闭：android 全条件满足亦不可行');
  } finally {
    restoreGlobals();
  }
});

test('乙可行性各降级条件（实验开关 b 下：异常/超长/无段落 → 甲）', () => {
  const model = chatSelect.paragraphModel(aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS }));
  const msg = aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS });
  const { page } = makePage({}, { getDeviceInfo: () => ({ platform: 'android' }) });
  page._textSelEngine = 'b';   // 实验开关：验证乙开启时降级链仍完整
  try {
    page.createSelectorQuery = () => ({});
    // 基准可行（实验开关 b）
    assert.equal(page._textOverlayFeasible(msg, model, model.byKey['md:1']), true);
    // 图片消息 → 不可行
    assert.equal(page._textOverlayFeasible(Object.assign({}, msg, { image: { url: 'x' } }), model, model.byKey['md:1']), false);
    // 无段落/空模型
    assert.equal(page._textOverlayFeasible(msg, { text: '' }, null), false);
    // 超长文本 → 甲
    const LONG = { text: 'x'.repeat(2000), byKey: {} };
    const longPara = { key: 'md:0', text: 'x'.repeat(100), start: 1000, end: 1100 };
    assert.equal(page._textOverlayFeasible(msg, LONG, longPara), false, '超长文本 → 甲');
    // 异常（getDeviceInfo throw）→ 甲
    installWx({ getDeviceInfo: () => { throw new Error('boom'); }, showToast: () => {} });
    assert.equal(page._textOverlayFeasible(msg, model, model.byKey['md:1']), false, '异常 → 甲');
  } finally {
    restoreGlobals();
  }
});

test('无段落长按（空白/装饰区）→ 旧语义：整泡 selectable + toast 引导', () => {
  const m1 = aiMsg(MD_TEXT, { mdNodes: MD_BLOCKS });
  const { page, calls } = makePage({ messages: [m1] });
  page.data.actionMenu = { show: true, msgId: 'a1', role: 'ai', paraKey: '' };
  try {
    page.actItem({ currentTarget: { dataset: { k: 'select' } } });
  } finally {
    restoreGlobals();
  }
  assert.equal(page.data.selectMsgId, 'a1');
  assert.equal(page.data.selParaKey, '');
  assert.ok(calls.toast.some((t) => (t.title || '').indexOf('长按文字即可选取') !== -1));
});

test('选取模式中段落长按：静默（不覆盖原生选择簿记，B3-3 语义保留）', () => {
  const { page } = makePage({ selectMsgId: 'a1', selParaKey: 'md:1' });
  page.onParaLongPress({ currentTarget: { dataset: { msgid: 'a1', paraId: 'md:2' } } });
  assert.equal(page._lastParaHit, undefined, '选取模式不记录新段落');
});

test('onListTap 退出选取 → selParaKey 同步清空（逃生出口保留）', () => {
  const { page } = makePage({ selectMsgId: 'a1', selParaKey: 'md:1' });
  page.onListTap();
  assert.equal(page.data.selectMsgId, '');
  assert.equal(page.data.selParaKey, '');
});
