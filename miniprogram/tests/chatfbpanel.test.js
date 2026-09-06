// 易理明灯 — k6 波2 P4.6 元宝式反馈面板（footer 踩 / 菜单点踩·意见反馈三入口同面板）
// 运行：node --test miniprogram/tests/chatfbpanel.test.js
// 覆盖：
//   1. footer 踩钮 → 打开面板（不再直发 negative）；赞 → 点亮 + 轻提示一次
//   2. 长按菜单「点踩」/「意见反馈」→ 同一面板
//   3. 原因 chips 多选切换 + 补充输入 → canSubmit 置灰/可点派生
//   4. 空选择提交 → 拦截（面板保留、不发后端）
//   5. 提交（原因+补充）→ api.feedback negative 合并串；成功 → 踩点亮 + 面板关 + toast
//   6. 无咨询 ID → 只点亮不发后端（面板提交口径，brief P4.6）
//   7. 上报失败 → 不点亮 + 失败 toast（G2 B1 语义保留）
//   8. 旧 fbMenu 网格清零（grep 级断言在文件头加载检查）
const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../utils/api');

const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/chat/chat');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg.openFeedbackPanel === 'function', 'chat.js 页面配置应可加载');
/* 旧网格清零：页面配置上不得残留旧方法与旧数据键 */
assert.equal(typeof pageCfg.submitFeedbackReason, 'undefined', '旧 submitFeedbackReason 应已移除');
assert.equal(pageCfg.data.fbMenu, undefined, '旧 fbMenu data 应已移除');

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

/* 支持 'fb.a1-down' 点路径的 setData 桩（仿真实 setData 语义） */
function setPath(obj, key, val) {
  const seg = key.split('.');
  let cur = obj;
  for (let i = 0; i < seg.length - 1; i++) {
    if (cur[seg[i]] == null) cur[seg[i]] = {};
    cur = cur[seg[i]];
  }
  cur[seg[seg.length - 1]] = val;
}

function makePage(overrides) {
  const calls = { toast: [], feedback: [] };
  installWx({ showToast: (o) => calls.toast.push(o), vibrateShort: () => {} });
  const page = Object.assign({}, pageCfg);
  page.data = Object.assign({
    multiMode: false,
    fb: {},
    fbSheet: { show: false, msgId: '', reasons: {}, note: '', canSubmit: false },
    actionMenu: { show: false, msgId: '', role: '', paraKey: '', canRegen: false, canCite: false },
    messages: [],
  }, overrides || {});
  page.setData = function (upd, cb) {
    Object.keys(upd || {}).forEach((k) => { if (k.indexOf('.') !== -1) setPath(this.data, k, upd[k]); else this.data[k] = upd[k]; });
    if (typeof cb === 'function') cb();
  };
  page._findMessage = (id) => page.data.messages.find((m) => m.id === id) || null;
  return { page, calls };
}

function aiMsg(id, extra) {
  return Object.assign({ id, role: 'ai', content: '回复正文', time: '12:00',
    consultationId: 'cid-' + id }, extra || {});
}

test('footer 踩钮 → 打开反馈面板（不再直发 negative）', () => {
  const origFb = api.feedback;
  api.feedback = () => { throw new Error('不应被调用'); };
  const { page, calls } = makePage({ messages: [aiMsg('a1')] });
  try {
    page.toggleFb({ currentTarget: { dataset: { id: 'a1', k: 'down' } } });
  } finally {
    api.feedback = origFb;
    restoreGlobals();
  }
  assert.equal(page.data.fbSheet.show, true);
  assert.equal(page.data.fbSheet.msgId, 'a1');
  assert.deepEqual(page.data.fbSheet.reasons, {});
  assert.equal(calls.toast.length, 0, '开面板不弹 toast');
});

test('footer 赞 → 点亮 + 轻提示一次（成功后 toast「谢谢认可」）', async () => {
  const origFb = api.feedback;
  api.feedback = () => Promise.resolve({ ok: true });
  const { page, calls } = makePage({ messages: [aiMsg('a1')] });
  try {
    page.toggleFb({ currentTarget: { dataset: { id: 'a1', k: 'up' } } });
    await Promise.resolve();   // 等 api.then
  } finally {
    api.feedback = origFb;
    restoreGlobals();
  }
  assert.equal(page.data.fb['a1-up'], true, '赞点亮');
  assert.ok(calls.toast.some((t) => (t.title || '').indexOf('谢谢认可') !== -1), '点亮时轻提示一次');
  // 再点取消点亮：不发后端不提示
  api.feedback = () => { throw new Error('不应再发'); };
  page.toggleFb({ currentTarget: { dataset: { id: 'a1', k: 'up' } } });
  assert.equal(page.data.fb['a1-up'], false);
});

test('长按菜单「点踩」/「意见反馈」→ 同一面板（入口语义统一）', () => {
  const { page } = makePage({ messages: [aiMsg('a1')] });
  // 菜单点踩
  page.data.actionMenu = { show: true, msgId: 'a1', role: 'ai', paraKey: '' };
  page.actFeedback({ currentTarget: { dataset: { k: 'down' } } });
  assert.equal(page.data.fbSheet.show, true, '菜单点踩应开面板');
  assert.equal(page.data.actionMenu.show, false, '菜单应已关闭');
  page.closeFbSheet();
  // 菜单意见反馈
  page.data.actionMenu = { show: true, msgId: 'a1', role: 'ai', paraKey: '' };
  page.actItem({ currentTarget: { dataset: { k: 'feedback' } } });
  assert.equal(page.data.fbSheet.show, true, '菜单意见反馈应开同一面板');
});

test('原因 chips 多选 + 补充输入 → canSubmit 派生（置灰/可点）', () => {
  const { page } = makePage({ messages: [aiMsg('a1')] });
  page.openFeedbackPanel('a1');
  assert.equal(page.data.fbSheet.canSubmit, false, '初始空选择 → 提交置灰');
  // 选一个原因
  page.onFbReasonTap({ currentTarget: { dataset: { opt: '内容不准确' } } });
  assert.equal(page.data.fbSheet.canSubmit, true);
  assert.equal(page.data.fbSheet.reasons['内容不准确'], true);
  // 再选第二个（多选）
  page.onFbReasonTap({ currentTarget: { dataset: { opt: '排版乱了' } } });
  assert.deepEqual(page.data.fbSheet.reasons, { '内容不准确': true, '排版乱了': true });
  // 取消一个 → 仍在可提交（剩一个）
  page.onFbReasonTap({ currentTarget: { dataset: { opt: '内容不准确' } } });
  assert.deepEqual(page.data.fbSheet.reasons, { '排版乱了': true });
  // 全部取消 + 补充为空 → 置灰
  page.onFbReasonTap({ currentTarget: { dataset: { opt: '排版乱了' } } });
  assert.equal(page.data.fbSheet.canSubmit, false);
  // 仅补充非空 → 可提交
  page.onFbNoteInput({ detail: { value: '  想多说两句  ' } });
  assert.equal(page.data.fbSheet.canSubmit, true);
  assert.equal(page.data.fbSheet.note, '  想多说两句  ');
});

test('提交：空选择拦截（面板保留、不发后端）', async () => {
  const origFb = api.feedback;
  api.feedback = () => { throw new Error('空选择不得发后端'); };
  const { page, calls } = makePage({ messages: [aiMsg('a1')] });
  try {
    page.openFeedbackPanel('a1');
    page.submitFbSheet();
    await Promise.resolve();
  } finally {
    api.feedback = origFb;
    restoreGlobals();
  }
  assert.equal(page.data.fbSheet.show, true, '空选择提交应保留面板');
  assert.equal(calls.toast.length, 0);
  assert.equal(page.data.fb['a1-down'], undefined);
});

test('提交：原因+补充合并串 → api.feedback negative；成功点亮 + toast', async () => {
  const origFb = api.feedback;
  const sent = [];
  api.feedback = (...a) => { sent.push(a); return Promise.resolve({ ok: true }); };
  const { page, calls } = makePage({ messages: [aiMsg('a1')] });
  try {
    page.openFeedbackPanel('a1');
    page.onFbReasonTap({ currentTarget: { dataset: { opt: '内容不准确' } } });
    page.onFbReasonTap({ currentTarget: { dataset: { opt: '理解错了' } } });
    page.onFbNoteInput({ detail: { value: '请多给一点建议' } });
    page.submitFbSheet();
    await Promise.resolve();
  } finally {
    api.feedback = origFb;
    restoreGlobals();
  }
  assert.equal(sent.length, 1);
  assert.equal(sent[0][0], 'cid-a1');
  assert.equal(sent[0][1], 'negative');
  assert.equal(sent[0][2], '理解错了、内容不准确；请多给一点建议', '多选「、」连接 + 补充「；」拼接');
  assert.equal(page.data.fbSheet.show, false, '提交成功 → 面板关闭');
  assert.equal(page.data.fb['a1-down'], true, '踩图标点亮 on');
  assert.ok(calls.toast.some((t) => t.title === '已收到反馈'));
});

test('提交：无咨询 ID → 只点亮不发后端（本地留档口径）', async () => {
  const origFb = api.feedback;
  api.feedback = () => { throw new Error('无 cid 不得发后端'); };
  const { page, calls } = makePage({ messages: [aiMsg('a2', { consultationId: null })] });
  try {
    page.openFeedbackPanel('a2');
    page.onFbReasonTap({ currentTarget: { dataset: { opt: '太敷衍' } } });
    page.submitFbSheet();
    await Promise.resolve();
  } finally {
    api.feedback = origFb;
    restoreGlobals();
  }
  assert.equal(page.data.fb['a2-down'], true, '无 cid → 仍点亮（面板提交口径）');
  assert.ok(calls.toast.some((t) => t.title === '已收到反馈'));
});

test('提交：上报失败 → 不点亮 + 明确失败提示（G2 B1）', async () => {
  const origFb = api.feedback;
  api.feedback = () => Promise.reject(new Error('net'));
  const { page, calls } = makePage({ messages: [aiMsg('a1')] });
  try {
    page.openFeedbackPanel('a1');
    page.onFbReasonTap({ currentTarget: { dataset: { opt: '内容让我不适' } } });
    page.submitFbSheet();
    await new Promise((r) => setTimeout(r, 0));   // 拒绝链在宏任务后结算
  } finally {
    api.feedback = origFb;
    restoreGlobals();
  }
  assert.equal(page.data.fb['a1-down'], undefined, '失败不得点亮（不静默装成功）');
  assert.ok(calls.toast.some((t) => (t.title || '').indexOf('提交失败') !== -1));
});
