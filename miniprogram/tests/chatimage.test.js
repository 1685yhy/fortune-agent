// 易理明灯 — B4-1 输入区改版（批次 4）：图片消息链路 + 语音入口 + 自动长高
// 运行：node --test miniprogram/tests/chatimage.test.js
// 覆盖：
//   1. streamHost.sendImage → 用户图片笺（msg.image）+ message_type=image/image_url 请求
//   2. streamHost.sendImage 生成中 → 排队（pending + 队列带 img，_nextQueued 透传）
//   3. streamHost.retry 图片消息保持 image 字段
//   4. api.chatStream 透传 image_url（payload.message_type=image + data.image_url）
//   5. api.uploadChatImage 走 wx.uploadFile（字段名 file、/api/chat/upload、Bearer）
//   6. chat 页：chooseImage → uploadChatImage → sendImage 全链路
//   7. chat 页：switchInputMode 语音入口（micAvailable 置灰时仅 toast 不切换）
//   8. chat 页：_updateInputBarH 公式（基线/额度条/行数/封顶）
//   9. chat 页：onInputLineChange → inputBarH 联动 + 贴底补滚
//   10. chat 页：+ 面板开合、图片点击预览
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
assert.ok(pageCfg && typeof pageCfg._updateInputBarH === 'function', 'chat.js 页面配置应可加载');

/* ── wx/登录桩（不影响其它测试文件的全局） ── */
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

/* ── 页面实例（仿 chatkeep.test.js makePage） ── */
function makePage(extra) {
  const page = Object.assign({}, pageCfg);
  page.data = Object.assign({
    quotaBar: { show: false, text: '', downgraded: false },
    inputBarH: 130,
    morePanel: { show: false },
    inputMode: 'text',
    inputFocused: false,
    micAvailable: true,
    multiMode: false,
    nightMode: false,
    inputText: '',
    scrollInto: '',
  }, extra || {});
  const sets = [];
  page.setData = function (upd) { sets.push(upd); Object.assign(this.data, upd); };
  page._getSets = () => sets;
  return page;
}

test('streamHost.sendImage：图片笺上屏 + image 请求参数（messageType/imageUrl）', () => {
  const sent = [];
  const origChatStream = api.chatStream;
  api.chatStream = (text, handlers, options) => { sent.push({ text, options }); return Promise.resolve({ abort() {} }); };
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  const r = streamHost.sendImage({ url: 'http://127.0.0.1:8767/api/chat/uploads/abc.jpg' });
  assert.equal(r, 'sent');
  // 用户笺带 image 字段（渲染层 item.image 分支）
  const userMsg = streamHost.messages.find((m) => m.role === 'user');
  assert.ok(userMsg && userMsg.image, '用户消息应带 image');
  assert.equal(userMsg.image.url, 'http://127.0.0.1:8767/api/chat/uploads/abc.jpg');
  assert.equal(userMsg.content, '（图片）');
  // 请求参数：message_type=image + image_url 透传
  assert.equal(sent.length, 1);
  assert.equal(sent[0].options.messageType, 'image');
  assert.equal(sent[0].options.imageUrl, 'http://127.0.0.1:8767/api/chat/uploads/abc.jpg');
  // 收尾清理（清看门狗定时器）
  streamHost.reset([]);
  api.chatStream = origChatStream;
  restoreGlobals();
});

test('streamHost.sendImage：生成中 → 排队（pending + 队列携带 img）', () => {
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  streamHost.streaming = true;   // 模拟生成中
  const r = streamHost.sendImage({ url: 'http://x/uploads/q.jpg' });
  assert.equal(r, 'queued');
  const pend = streamHost.messages.find((m) => m.role === 'user' && m.pending);
  assert.ok(pend && pend.image && pend.image.url === 'http://x/uploads/q.jpg');
  assert.equal(streamHost.queue.length, 1);
  assert.equal(streamHost.queue[0].img.url, 'http://x/uploads/q.jpg');
  streamHost.reset([]);
  restoreGlobals();
});

test('streamHost.retry：图片消息重试保持 image 字段', () => {
  const sent = [];
  const origChatStream = api.chatStream;
  api.chatStream = (text, handlers, options) => { sent.push(options); return Promise.resolve({ abort() {} }); };
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  streamHost.messages = [
    { id: 'u1', role: 'user', content: '（图片）', image: { url: 'http://x/uploads/r.jpg' } },
    { id: 'a1', role: 'ai', content: '生成失败', error: true },
  ];
  streamHost.retry('a1', '（图片）', '明灯 · 夜话', { url: 'http://x/uploads/r.jpg' });
  const imgMsg = streamHost.messages.find((m) => m.role === 'user' && m.id === 'u1');
  assert.ok(imgMsg && imgMsg.image, '重试后用户图片笺应保留 image');
  assert.equal(sent[0].messageType, 'image');
  assert.equal(sent[0].imageUrl, 'http://x/uploads/r.jpg');
  streamHost.reset([]);
  api.chatStream = origChatStream;
  restoreGlobals();
});

test('api.chatStream：image_url 透传（payload.message_type=image + data.image_url）', async () => {
  const reqs = [];
  installWx({
    request: (opt) => {
      reqs.push(opt);
      if (opt.url.indexOf('/api/health') !== -1) {
        opt.success && opt.success({ statusCode: 200, data: {} });
      } else {
        opt.success && opt.success({ statusCode: 200 });
      }
    },
    getStorageSync: () => null,
    setStorageSync: () => {},
  });
  try {
    await api.chatStream('hello', {}, {
      messageType: 'image',
      imageUrl: 'http://127.0.0.1:8767/api/chat/uploads/abc.jpg',
    });
  } finally {
    restoreGlobals();
  }
  const stream = reqs.find((r) => r.method === 'POST' && r.url.indexOf('/api/chat/stream') !== -1);
  assert.ok(stream, '应发出 /api/chat/stream 请求');
  assert.equal(stream.data.message_type, 'image');
  assert.equal(stream.data.image_url, 'http://127.0.0.1:8767/api/chat/uploads/abc.jpg');
});

test('api.uploadChatImage：wx.uploadFile 字段名 file + /api/chat/upload + 解析返回', async () => {
  let captured = null;
  installWx({
    request: (opt) => { opt.success && opt.success({ statusCode: 200, data: {} }); },
    getStorageSync: () => null,
    setStorageSync: () => {},
    uploadFile: (opt) => {
      captured = opt;
      opt.success && opt.success({
        statusCode: 200,
        data: JSON.stringify({ status: 'ok', url: 'http://127.0.0.1:8767/api/chat/uploads/abc.jpg' }),
      });
    },
  });
  let res;
  try {
    res = await api.uploadChatImage('/tmp/photo.jpg');
  } finally {
    restoreGlobals();
  }
  assert.equal(res.url, 'http://127.0.0.1:8767/api/chat/uploads/abc.jpg');
  assert.ok(captured, '应调用 wx.uploadFile');
  assert.equal(captured.name, 'file');
  assert.equal(captured.filePath, '/tmp/photo.jpg');
  assert.ok(captured.url.indexOf('/api/chat/upload') !== -1);
  assert.ok((captured.header.Authorization || '').indexOf('Bearer ') === 0);
});

test('chat 页：chooseImage → 上传 → 发图片消息（全链路）', async () => {
  const calls = { chooseMedia: null, uploadPath: null, sentImg: null };
  installWx({
    showLoading: () => {},
    hideLoading: () => {},
    showToast: () => {},
    chooseMedia: (o) => {
      calls.chooseMedia = o;
      o.success && o.success({ tempFiles: [{ tempFilePath: '/tmp/cam.jpg' }] });
    },
  });
  const origUpload = api.uploadChatImage;
  const origSendImage = streamHost.sendImage;
  api.uploadChatImage = (p) => { calls.uploadPath = p; return Promise.resolve({ url: 'http://x/uploads/cam.jpg' }); };
  streamHost.sendImage = (img) => { calls.sentImg = img; return 'sent'; };
  const page = makePage();
  try {
    await page.chooseImage();
  } finally {
    api.uploadChatImage = origUpload;
    streamHost.sendImage = origSendImage;
    restoreGlobals();
  }
  assert.ok(calls.chooseMedia, '应调 wx.chooseMedia');
  assert.equal(calls.chooseMedia.count, 1);
  assert.equal(calls.uploadPath, '/tmp/cam.jpg');
  assert.ok(calls.sentImg && calls.sentImg.url === 'http://x/uploads/cam.jpg');
});

test('chat 页：switchInputMode 语音入口（话筒置灰时仅 toast 不切换）', () => {
  const toasts = [];
  installWx({ showToast: (o) => toasts.push(o) });
  const page = makePage();
  try {
    // 正常切换：text → voice，输入条回落基线
    page.switchInputMode({ currentTarget: { dataset: { mode: 'voice' } } });
    assert.equal(page.data.inputMode, 'voice');
    assert.equal(page.data.inputBarH, 130);
    // 语音 → 文字：恢复文本区行数高度（_inputLines=3 → 130+80=210）
    page._inputLines = 3;
    page.switchInputMode({ currentTarget: { dataset: { mode: 'text' } } });
    assert.equal(page.data.inputMode, 'text');
    assert.equal(page.data.inputBarH, 210);
    // 话筒置灰：点按不切换 + toast 提示
    const page2 = makePage({ micAvailable: false });
    page2.switchInputMode({ currentTarget: { dataset: { mode: 'voice' } } });
    assert.equal(page2.data.inputMode, 'text');
    assert.ok(toasts.some((t) => (t.title || '').indexOf('语音输入未开启') !== -1));
  } finally {
    restoreGlobals();
  }
});

test('chat 页：_updateInputBarH 公式（基线/额度条/行数/封顶）', () => {
  const page = makePage();
  // 无额度条：130 + (行数-1)×40
  page._updateInputBarH(1);
  assert.equal(page.data.inputBarH, 130);
  page._updateInputBarH(3);
  assert.equal(page.data.inputBarH, 210);
  page._updateInputBarH(6);
  assert.equal(page.data.inputBarH, 330);
  // 6 行封顶（5.5 行 → 行数 6）：再多行不增
  page._updateInputBarH(10);
  assert.equal(page.data.inputBarH, 330);
  // 额度条出现：+42
  page.data.quotaBar = { show: true, text: '', downgraded: false };
  page._updateInputBarH(1);
  assert.equal(page.data.inputBarH, 172);
  page._updateInputBarH(4);
  assert.equal(page.data.inputBarH, 292);
});

test('chat 页：onInputLineChange → inputBarH 联动 + 贴底补滚', () => {
  const page = makePage();
  // 在底部：_scrollTop/_scrollHeight/_clientH 未知 → 保守跟随 → 补滚
  page.onInputLineChange({ detail: { lineCount: 3 } });
  assert.equal(page._inputLines, 3);
  assert.equal(page.data.inputBarH, 210);
  const sets = page._getSets();
  assert.ok(sets.some((s) => s.scrollInto === 'btm'), '输入条变高后应补一次贴底滚动');
  // 行数未变：不触发补滚
  const n = sets.length;
  page.onInputLineChange({ detail: { lineCount: 3 } });
  assert.equal(sets.length, n);
});

test('chat 页：+ 面板开合 + 图片点击预览', () => {
  const previews = [];
  installWx({ previewImage: (o) => previews.push(o) });
  const page = makePage();
  try {
    page.openMorePanel();
    assert.equal(page.data.morePanel.show, true);
    page.closeMorePanel();
    assert.equal(page.data.morePanel.show, false);
    page.previewMsgImage({ currentTarget: { dataset: { url: 'http://x/uploads/p.jpg' } } });
    assert.equal(previews.length, 1);
    assert.equal(previews[0].current, 'http://x/uploads/p.jpg');
    // 多选模式：预览不响应
    page.data.multiMode = true;
    page.previewMsgImage({ currentTarget: { dataset: { url: 'http://x/uploads/p.jpg' } } });
    assert.equal(previews.length, 1);
  } finally {
    restoreGlobals();
  }
});
