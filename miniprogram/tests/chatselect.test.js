// 易理明灯 — 长按「选取文字」流程修复（批次 3 B3-3-D）
// 运行：node --test miniprogram/tests/chatselect.test.js
// 覆盖：① 选取模式中再次长按气泡 → 静默（不弹菜单/不 toast/不震动，系统原生
//       选择不被干扰）；② 普通长按 → 弹操作菜单 + 震动（回归）；③ 菜单「选取
//       文字」→ 进入选取模式（selectMsgId 置位）；④ 菜单「复制」能力保留；
//       ⑤ onListTap 点气泡外仍可退出选取模式（逃生出口保留）。
const test = require('node:test');
const assert = require('node:assert/strict');

const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/chat/chat');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg.onBubbleLongPress === 'function', 'chat.js 页面配置应可加载');

function makePage(overrides) {
  const calls = { toast: [], vibrate: [], clipboard: [] };
  global.wx = {
    showToast: (o) => calls.toast.push(o),
    vibrateShort: () => calls.vibrate.push(1),
    setClipboardData: (o) => calls.clipboard.push(o.data),
  };
  const page = Object.assign({}, pageCfg);
  page.data = Object.assign({
    multiMode: false,
    selectMsgId: '',
    actionMenu: { show: false, msgId: '', role: '' },
    messages: [{ id: 'm1', role: 'ai', content: '[card:paipan title="x"]\n正文\n[/card]' }],
  }, overrides || {});
  page.setData = function (upd) { Object.assign(this.data, upd); };
  page._findMessage = (id) => page.data.messages.find((m) => m.id === id) || null;
  return { page, calls };
}

test('选取模式中再次长按气泡：静默返回——不弹菜单、不 toast、不震动（B3-3-D 主修复）', () => {
  const { page, calls } = makePage({ selectMsgId: 'm1' });
  page.onBubbleLongPress({ currentTarget: { dataset: { id: 'm1', role: 'ai' } } });
  assert.equal(page.data.actionMenu.show, false, '不应弹操作菜单');
  assert.equal(calls.toast.length, 0, '不应 toast 干扰系统原生选择');
  assert.equal(calls.vibrate.length, 0, '不应震动');
});

test('普通长按（非选取/非多选）：弹操作菜单 + 震动（回归）', () => {
  const { page, calls } = makePage();
  page.onBubbleLongPress({ currentTarget: { dataset: { id: 'm1', role: 'ai' } } });
  assert.equal(page.data.actionMenu.show, true);
  assert.equal(page.data.actionMenu.msgId, 'm1');
  assert.equal(calls.vibrate.length, 1);
});

test('多选模式长按：不弹菜单（v1.3 回归）', () => {
  const { page, calls } = makePage({ multiMode: true });
  page.onBubbleLongPress({ currentTarget: { dataset: { id: 'm1', role: 'ai' } } });
  assert.equal(page.data.actionMenu.show, false);
  assert.equal(calls.toast.length, 0);
});

test('菜单「选取文字」：进入选取模式（selectMsgId 置位）', () => {
  const { page } = makePage();
  page.data.actionMenu = { show: true, msgId: 'm1', role: 'ai' };
  page.actItem({ currentTarget: { dataset: { k: 'select' } } });
  assert.equal(page.data.selectMsgId, 'm1');
  assert.equal(page.data.actionMenu.show, false, '菜单已关闭');
});

test('菜单「复制」：能力保留（B3-3 红线：不得删除复制）', () => {
  const { page, calls } = makePage();
  page.data.actionMenu = { show: true, msgId: 'm1', role: 'ai' };
  page.actItem({ currentTarget: { dataset: { k: 'copy' } } });
  assert.equal(calls.clipboard.length, 1);
  assert.equal(calls.clipboard[0], '正文', '复制剥标记后纯文本（卡片标记不暴露）');
});

test('onListTap（点气泡外）：仍可退出选取模式（逃生出口保留）', () => {
  const { page } = makePage({ selectMsgId: 'm1' });
  page.onListTap();
  assert.equal(page.data.selectMsgId, '', '气泡外点击退出选取模式');
});

test('onListTap：多选模式不退出（回归）', () => {
  const { page } = makePage({ multiMode: true, selectMsgId: 'm1' });
  page.onListTap();
  assert.equal(page.data.selectMsgId, 'm1', '多选模式下不退出');
});
