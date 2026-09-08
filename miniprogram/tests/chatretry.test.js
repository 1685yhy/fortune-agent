// 易理明灯 — k13 重试/重新生成同轮原地 regenerate（retry-dedup）
// 运行：node --test miniprogram/tests/chatretry.test.js
// 覆盖（对应 task-k13 任务书 D-⑥ 前端用例）：
//   1. streamHost.retry（失败气泡重试）：同轮原地 regenerate——提问笺不复制，
//      目标 AI 笺原位替换为新 streaming 笺；请求 options.regen === true
//   2. streamHost.retry 双击（目标气泡已消失）→ no-op（无第二次请求、消息不变）
//   3. streamHost.retry 图片轮：提问笺保留 image、不新增 user 笺；请求
//      messageType=image + 原 imageUrl + regen（未传 img 时自动从提问笺回取原图）
//   4. 异常态（轮次提问笺缺失）→ 回退旧语义补建用户笺重发（保完整）
//   5. chat 页 retryStream 接线：仍调 streamHost.retry(id/text/tag/img)（回归）
//   6. chat 页 k10 regen 接线：菜单「重新生成」→ streamHost.retry(原 id/text/tag)（回归）
//   7. api.chatStream/api.chat：options.regen → payload.data.regen === true；
//      不带 → 无 regen 键
//   8. 轮次收尾复位：done/error 后 curRegen=false（下一轮普通发送不携带 regen）
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
assert.ok(pageCfg && typeof pageCfg.retryStream === 'function', 'chat.js 页面配置应可加载');

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

/* 失败轮现场：提问 + 失败 AI 气泡（真实 _onError 落盘形态） */
function failedRound(text, content) {
  return [
    { id: 'u1', role: 'user', content: text, time: '10:00' },
    { id: 'a1', role: 'ai', content: content || '网络开小差了，再试一次？',
      error: true, retryText: text, streaming: false, time: '10:01' },
  ];
}

function mockChatStream(sent) {
  const orig = api.chatStream;
  api.chatStream = (text, handlers, options) => {
    sent.push({ text, options });
    return Promise.resolve({ abort() {} });
  };
  return () => { api.chatStream = orig; };
}

function mockChat(sent) {
  const orig = api.chat;
  api.chat = (message, scenario, history, options) => {
    sent.push({ message, options });
    return Promise.resolve({ reply: '好的', content: '好的', suggestions: [], citations: [] });
  };
  return () => { api.chat = orig; };
}

test('k13 retry：失败轮同轮原地 regenerate——提问笺不复制 + 请求带 regen', () => {
  const sent = [];
  const restore = mockChatStream(sent);
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  streamHost.messages = failedRound('帮我看看我的八字');
  try {
    streamHost.retry('a1', '帮我看看我的八字', '明灯 · 夜话', null);
    // 提问笺唯一：无第二个 user 气泡（旧实现 = 复制提问 = 用户实锤重复消息）
    const users = streamHost.messages.filter((m) => m.role === 'user');
    assert.equal(users.length, 1, '重试后用户提问笺不得复制');
    assert.equal(users[0].id, 'u1');
    assert.equal(users[0].content, '帮我看看我的八字');
    // 失败 AI 笺原位替换为新 streaming 笺
    const ais = streamHost.messages.filter((m) => m.role === 'ai');
    assert.equal(ais.length, 1);
    assert.notEqual(ais[0].id, 'a1', '失败气泡应被替换而非保留');
    assert.equal(ais[0].streaming, true);
    assert.equal(ais[0].retryText, '帮我看看我的八字');
    assert.equal(streamHost.messages.length, 2, '[u][a] 两笺，无多余气泡');
    // 请求带 regen 同轮标记
    assert.equal(sent.length, 1);
    assert.equal(sent[0].text, '帮我看看我的八字');
    assert.equal(sent[0].options.regen, true, '重试请求必须带 regen 标记');
  } finally {
    restore();
    streamHost.reset([]);
    restoreGlobals();
  }
});

test('k13 retry 双击：目标气泡已消失 → no-op（无第二次请求）', () => {
  const sent = [];
  const restore = mockChatStream(sent);
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  streamHost.messages = failedRound('帮我看看我的八字');
  try {
    streamHost.retry('a1', '帮我看看我的八字', 'tag', null);   // 首击：替换成功
    streamHost.retry('a1', '帮我看看我的八字', 'tag', null);   // 二击：a1 已不在
    assert.equal(sent.length, 1, '双击第二次必须 no-op（防幽灵重复提交）');
    assert.equal(streamHost.messages.length, 2);
  } finally {
    restore();
    streamHost.reset([]);
    restoreGlobals();
  }
});

test('k13 retry 图片轮：提问笺保留 image、不新增 user 笺、请求带 regen', () => {
  const sent = [];
  const restore = mockChatStream(sent);
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  streamHost.messages = [
    { id: 'u1', role: 'user', content: '（图片）', image: { url: 'http://x/uploads/r.jpg' }, time: '10:00' },
    { id: 'a1', role: 'ai', content: '生成失败', error: true, retryText: '（图片）', streaming: false, time: '10:01' },
  ];
  try {
    streamHost.retry('a1', '（图片）', 'tag', { url: 'http://x/uploads/r.jpg' });
    const users = streamHost.messages.filter((m) => m.role === 'user');
    assert.equal(users.length, 1, '图片轮重试不得复制提问笺');
    assert.equal(users[0].id, 'u1');
    assert.equal(users[0].image.url, 'http://x/uploads/r.jpg', '原图保留');
    assert.equal(sent[0].options.messageType, 'image');
    assert.equal(sent[0].options.imageUrl, 'http://x/uploads/r.jpg');
    assert.equal(sent[0].options.regen, true);
  } finally {
    restore();
    streamHost.reset([]);
    restoreGlobals();
  }
});

test('k13 retry 图片轮自动回取原图：未传 img（中断恢复路径）→ 从提问笺取', () => {
  const sent = [];
  const restore = mockChatStream(sent);
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  streamHost.messages = [
    { id: 'u1', role: 'user', content: '（图片）', image: { url: 'http://x/uploads/r.jpg' }, time: '10:00' },
    { id: 'a1', role: 'ai', content: '', error: true, retryText: '（图片）', streaming: false, time: '10:01' },
  ];
  try {
    streamHost.retry('a1', '（图片）', 'tag');   // _recoverInterrupted 调用形态（无 img）
    assert.equal(sent[0].options.messageType, 'image', '自动恢复图片轮必须保持 image 链路');
    assert.equal(sent[0].options.imageUrl, 'http://x/uploads/r.jpg');
    assert.equal(streamHost.messages.filter((m) => m.role === 'user').length, 1);
  } finally {
    restore();
    streamHost.reset([]);
    restoreGlobals();
  }
});

test('k13 retry 异常态：轮次提问笺缺失 → 回退补建用户笺重发（保完整）', () => {
  const sent = [];
  const restore = mockChatStream(sent);
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  streamHost.messages = [
    { id: 'a1', role: 'ai', content: '生成失败', error: true, retryText: '直接问', streaming: false },
  ];
  try {
    streamHost.retry('a1', '直接问', 'tag', null);
    assert.equal(streamHost.messages.filter((m) => m.role === 'user').length, 1, '异常态补建提问笺');
    assert.equal(sent.length, 1);
  } finally {
    restore();
    streamHost.reset([]);
    restoreGlobals();
  }
});

test('chat 页 retryStream 接线：调 streamHost.retry(目标AI id/retryText/tag/img)（回归）', () => {
  const calls = [];
  const origRetry = streamHost.retry;
  streamHost.retry = (msgId, text, tag, img) => { calls.push({ msgId, text, tag, img }); };
  installWx({ setStorageSync: () => {}, showToast: () => {} });
  streamHost.reset([]);
  streamHost.streaming = false;
  const page = Object.assign({}, pageCfg);
  page.data = {
    multiMode: false,
    messages: [
      { id: 'u1', role: 'user', content: '帮我看看', time: '10:00' },
      { id: 'a1', role: 'ai', content: '生成失败', error: true, retryText: '帮我看看', time: '10:01' },
    ],
  };
  page.setData = function (u) { Object.assign(this.data, u); };
  page._findMessage = (id) => page.data.messages.find((m) => m.id === id) || null;
  try {
    page.retryStream({ currentTarget: { dataset: { id: 'a1', text: '帮我看看' } } });
  } finally {
    streamHost.retry = origRetry;
    streamHost.reset([]);
    restoreGlobals();
  }
  assert.equal(calls.length, 1);
  assert.equal(calls[0].msgId, 'a1', '重试目标 = 失败 AI 气泡');
  assert.equal(calls[0].text, '帮我看看');
});

test('k13 retry 流式生成中 → 守卫 return（不打断当前流）', () => {
  const sent = [];
  const restore = mockChatStream(sent);
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  streamHost.messages = failedRound('帮我看看我的八字');
  try {
    streamHost.streaming = true;
    streamHost.retry('a1', '帮我看看我的八字', 'tag', null);
    assert.equal(sent.length, 0, '流式生成中 retry 应静默 return');
    assert.equal(streamHost.messages.length, 2);
    streamHost.streaming = false;
  } finally {
    restore();
    streamHost.reset([]);
    restoreGlobals();
  }
});

test('k10 regen 接线：菜单「重新生成」→ streamHost.retry(原 id/retryText/tag)（回归）', () => {
  const retryCalls = [];
  const origRetry = streamHost.retry;
  streamHost.retry = (...a) => { retryCalls.push(a); };
  installWx({ showToast: () => {} });
  streamHost.active = false;
  const page = Object.assign({}, pageCfg);
  page.data = {
    multiMode: false,
    messages: [{ id: 'a1', role: 'ai', tag: '今日 · 日运', content: '回复正文', retryText: '提问原文', error: false, streaming: false, citations: [] }],
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
  assert.equal(retryCalls[0][0], 'a1');
  assert.equal(retryCalls[0][1], '提问原文');
  assert.equal(retryCalls[0][2], '今日 · 日运');
});

test('api.chatStream options.regen → payload.data.regen === true（stream）', async () => {
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
    await api.chatStream('hello', {}, { sessionId: 's_x1', regen: true });
    await api.chatStream('world', {}, {});
  } finally {
    restoreGlobals();
  }
  const posts = reqs.filter((r) => r.method === 'POST' && r.url.indexOf('/api/chat/stream') !== -1);
  assert.ok(posts.length >= 1);
  const withRegen = posts.find((p) => p.data.message === 'hello');
  const without = posts.find((p) => p.data.message === 'world');
  assert.equal(withRegen.data.regen, true, 'regen 标记应进 payload');
  assert.equal(without.data.regen, undefined, '普通发送不得携带 regen');
});

test('api.chat options.regen → payload.data.regen === true（普通回退）', async () => {
  const reqs = [];
  installWx({
    request: (opt) => {
      reqs.push(opt);
      if (opt.url.indexOf('/api/health') !== -1) {
        opt.success && opt.success({ statusCode: 200, data: {} });
      } else {
        opt.success && opt.success({ statusCode: 200, data: {} });
      }
    },
    getStorageSync: () => null,
    setStorageSync: () => {},
  });
  try {
    await api.chat('hello', '', [], { regen: true });
  } finally {
    restoreGlobals();
  }
  const post = reqs.find((r) => r.method === 'POST' && r.url.indexOf('/api/chat') !== -1
    && r.url.indexOf('/api/chat/stream') === -1);
  assert.ok(post, '应发出 /api/chat 请求');
  assert.equal(post.data.regen, true);
});

test('k13 轮次收尾复位：失败回退也失败（error 收尾）后 curRegen=false', async () => {
  const fallback = [];
  const origChat = api.chat;
  const origStream = api.chatStream;
  // 流式通道：接受请求（regen 轮）但不产出任何事件
  api.chatStream = (text, handlers, options) => Promise.resolve({ abort() {} });
  // 普通回退通道：记录调用并失败 → 进入 error 收尾
  api.chat = (message, scenario, history, options) => {
    fallback.push({ message, options });
    return Promise.reject(new Error('fallback also failed'));
  };
  installWx({ setStorageSync: () => {}, showToast: () => {} });
  streamHost.reset([]);
  streamHost.messages = failedRound('帮我看看我的八字');
  try {
    streamHost.retry('a1', '帮我看看我的八字', 'tag', null);   // regen 轮开始
    assert.equal(streamHost.curRegen, true);
    // 模拟流式失败：无正文 → 静默回退普通请求 → 回退也失败 → error 收尾
    streamHost._patch(streamHost.msgId, { content: '' });
    await streamHost._onError(new Error('stream failed'));
    assert.equal(streamHost.curRegen, false, 'error 收尾后 regen 标记必须复位');
    assert.equal(fallback.length, 1);
    assert.equal(fallback[0].options.regen, true, '失败回退请求同样带同轮标记');
    // 复位后普通发送不带 regen
    const sent2 = [];
    api.chatStream = (text, handlers, options) => { sent2.push(options); return Promise.resolve({ abort() {} }); };
    streamHost.send('新问题', 'tag');
    assert.equal(sent2[0].regen, false, '下一轮普通发送不得携带 regen');
  } finally {
    api.chat = origChat;
    api.chatStream = origStream;
    streamHost.reset([]);
    restoreGlobals();
  }
});
