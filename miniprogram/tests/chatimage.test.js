// 易理明灯 — 输入区/图片链路（B4-1 + k6 波1 语义回归）
// 运行：node --test miniprogram/tests/chatimage.test.js
// 覆盖：
//   1. streamHost.sendImage → 用户图片笺（msg.image）+ message_type=image/image_url 请求
//   2. streamHost.sendImage 生成中 → 排队（pending + 队列带 img，_nextQueued 透传）
//   3. streamHost.retry 图片消息保持 image 字段
//   4. api.chatStream 透传 image_url（payload.message_type=image + data.image_url）
//   5. api.uploadChatImage 走 wx.uploadFile（字段名 file、/api/chat/upload、Bearer）
//   6. chat 页：chooseImage → uploadChatImage → sendImage 全链路
//   7. chat 页：switchInputMode 语音入口（micAvailable 置灰时仅 toast 不切换；
//      模式切换不再触碰任何输入条高度机件——k6-P1 语义回归）
//   8. chat 页：k6-P1 行高机件整体退役（_updateInputBarH/onInputLineChange/
//      _onInputGrow/inputBarH 不复存在；setData 不再含 inputBarH/补滚）
//   9. chat 页：micLongPress 长按直达录音（守卫/授权→开录），M-1 窗口口径
//      （_touchStartAt 回拨 ~350ms 对齐按住条起算）
//   10. chat 页：+ 面板开合、图片点击预览
//   11. chat 页：retryStream AI 气泡重试 → 回溯 user 消息透传 image（接线路径，B4-1-fix I1）
//   12. chat 页：图片消息后的文字追问重试 → 不误挂旧图（就近回溯）
//   13. chat 页：_refreshQuota 额度条展示（k6-P1：不再联动输入条高度）
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
assert.ok(pageCfg && typeof pageCfg.sendMessage === 'function', 'chat.js 页面配置应可加载');
/* k6-P1：输入条行高机件已整体退役——这些符号必须不存在（review I-1 跟进清理） */
assert.equal(typeof pageCfg._updateInputBarH, 'undefined', '行高机件 _updateInputBarH 应已退役');
assert.equal(typeof pageCfg.onInputLineChange, 'undefined', '行高机件 onInputLineChange 应已退役');
assert.equal(typeof pageCfg._onInputGrow, 'undefined', '行高机件 _onInputGrow 应已退役');
assert.equal(pageCfg.data.inputBarH, undefined, 'data.inputBarH 应已移除');

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
    morePanel: { show: false },
    inputMode: 'text',
    inputFocused: false,
    micAvailable: true,
    isRecording: false,
    converting: false,
    streaming: false,
    multiMode: false,
    nightMode: false,
    inputText: '',
    scrollInto: '',
    actionMenu: { show: false, msgId: '', role: '' },
  }, extra || {});
  const sets = [];
  page.setData = function (upd, cb) { sets.push(upd); Object.assign(this.data, upd); if (typeof cb === 'function') cb(); };
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

test('chat 页：switchInputMode 语音入口（置灰仅 toast 不切换；切换零高度机件）', () => {
  const toasts = [];
  // k34 A20-M3：切语音态会预热麦克风授权（getSetting）→ 桩按「已授权」快路径提供
  installWx({
    showToast: (o) => toasts.push(o),
    getSetting: (o) => o.success && o.success({ authSetting: { 'scope.record': true } }),
  });
  const page = makePage();
  try {
    // 正常切换：text → voice；k6-P1：不产生任何 inputBarH / 高度 setData
    page.switchInputMode({ currentTarget: { dataset: { mode: 'voice' } } });
    assert.equal(page.data.inputMode, 'voice');
    const sets = page._getSets();
    assert.ok(!sets.some((s) => 'inputBarH' in s), '模式切换不得触碰输入条高度机件（k6-P1）');
    // 语音 → 文字
    page.switchInputMode({ currentTarget: { dataset: { mode: 'text' } } });
    assert.equal(page.data.inputMode, 'text');
    // 话筒置灰：点按不切换 + toast 提示
    const page2 = makePage({ micAvailable: false });
    page2.switchInputMode({ currentTarget: { dataset: { mode: 'voice' } } });
    assert.equal(page2.data.inputMode, 'text');
    assert.ok(toasts.some((t) => (t.title || '').indexOf('语音输入未开启') !== -1));
  } finally {
    restoreGlobals();
  }
});

test('chat 页：k6-P1 行高机件退役语义（增行不再触发高度联动/补滚 setData）', () => {
  const page = makePage();
  // 模拟 auto-height 打字增行最接近页面的旧行为入口（bindlinechange）已不存在；
  // 断言旧机件符号在实例上也不可调用（防半退役残留）
  assert.equal(typeof page._updateInputBarH, 'undefined');
  assert.equal(typeof page.onInputLineChange, 'undefined');
  assert.equal(typeof page._onInputGrow, 'undefined');
  assert.equal(page.data.inputBarH, undefined);
  assert.equal(page._inputLines, undefined);
  // 既有滚动路径仍独立存在（与行高机件解耦）：直接强制贴底正常执行
  const sets0 = page._getSets().length;
  page._scrollBottom(true);
  const tail = page._getSets().slice(sets0);
  assert.ok(tail.some((s) => s.scrollInto === 'btm'), '强制贴底滚动路径应保留（流式/发送滚动零改动）');
});

test('chat 页：micLongPress 输入非空 → toast 提示且不切模式（k6-P2 守卫）', () => {
  const toasts = [];
  installWx({ showToast: (o) => toasts.push(o) });
  const page = makePage({ inputText: '  还有半句没发  ' });
  try {
    page.micLongPress({ touches: [{ clientY: 100 }] });
  } finally {
    restoreGlobals();
  }
  assert.equal(page.data.inputMode, 'text', '输入非空不得切换语音模式');
  assert.ok(toasts.some((t) => (t.title || '').indexOf('请先发送或清空输入') !== -1));
});

test('chat 页：micLongPress 录音中 / 转写中 / 话筒置灰 → 静默不响应（k6-P2 守卫）', () => {
  const toasts = [];
  installWx({ showToast: (o) => toasts.push(o) });
  try {
    // k34 A20-M2：streaming 已从守卫移除（生成中长按与按住条同语义，见下条用例）
    for (const over of [
      { isRecording: true },
      { converting: true },
      { micAvailable: false },
    ]) {
      const page = makePage(over);
      page.micLongPress({ touches: [{ clientY: 100 }] });
      assert.equal(page.data.inputMode, 'text', `守卫静默：${JSON.stringify(over)}`);
    }
  } finally {
    restoreGlobals();
  }
  assert.equal(toasts.length, 0, '录音/转写中/置灰守卫应完全静默');
});

/* ═══════ k34 A20：语音长按三项 Minor 收口 ═══════ */

test('chat 页 k34 A20-M2：生成中（streaming）长按 → 进语音态开录（与按住条同语义，不再静默）', () => {
  installWx({
    getSetting: (o) => o.success({ authSetting: { 'scope.record': true } }),
    showToast: () => {},
  });
  const started = [];
  const page = makePage({ streaming: true });
  page._speechPlugin = {};
  page._recMgr = { start: () => started.push(1), stop: () => {} };
  try {
    page.micLongPress({ touches: [{ clientY: 100 }] });
    assert.equal(page.data.inputMode, 'voice', '生成中长按同样进语音态（原为静默无反应）');
    assert.equal(page.data.isRecording, true, '生成中可直接开录（识别结果走既有排队上屏）');
    assert.equal(started.length, 1, '录音器已启动');
    assert.equal(page._voiceFromLongPress, true, '长按会话簿记就位（结束回文字态）');
  } finally {
    if (page._recordTimer) clearInterval(page._recordTimer);
    restoreGlobals();
  }
});

test('chat 页 k34 A20-M1：长按计时回拨 350ms —— 真实按住 0.85s 即发送，短于 0.8s 仍判太短', () => {
  const toasts = [];
  const started = [];
  const realNow = Date.now;
  let fakeNow = 1700000000000;              // 假时钟：精确模拟 bindlongpress 的 ~350ms 延迟
  installWx({
    showToast: (o) => toasts.push(o),
    // 已授权快路径：不带 authorize 桩（若走授权分支会 TypeError → 用例失败）
    getSetting: (o) => o.success({ authSetting: { 'scope.record': true } }),
  });
  const page = makePage();
  page._speechPlugin = {};
  page._recMgr = { start: () => started.push(1), stop: () => {} };
  Date.now = () => fakeNow;
  let finish = null;
  // 桩替身必须保留计时器收口（_cleanupTimer）：同用例内两次开录，否则前一个 setInterval 泄漏 → 进程不退出
  page._finishRecording = (send) => { finish = send; page._cleanupTimer(); };
  try {
    // ① 手指按下 → 350ms 后 longpress 触发 → 直接开录
    fakeNow += 350;
    page.micLongPress({ touches: [{ clientY: 100 }] });
    assert.equal(page._touchStartAt, fakeNow - 350, '计时起点回拨 350ms ≈ 手指真正按下时刻');
    assert.equal(page.data.isRecording, true, '长按直达录音');
    assert.equal(started.length, 1);
    // ② 再按住 500ms（真实按住合计 850ms ≥ 800ms）→ 松手 = 发送
    fakeNow += 500;
    page.micTouchEnd();
    assert.equal(finish, true, '真实按住 850ms → 发送（计时未回拨的旧口径会误判「太短」）');
    assert.ok(!toasts.some((t) => (t.title || '').indexOf('太短') !== -1), '不得出现「说话时间太短」');
    // ③ 反向：真实按住 500ms（350 + 150 < 800ms）→ 仍判太短并取消
    page.setData({ isRecording: false });
    fakeNow += 1000;
    page.micLongPress({ touches: [{ clientY: 100 }] });
    fakeNow += 150;
    page.micTouchEnd();
    assert.equal(finish, false, '真实按住 500ms → 取消（太短）');
    assert.ok(toasts.some((t) => (t.title || '').indexOf('说话时间太短') !== -1), '太短提示保留');
  } finally {
    Date.now = realNow;
    if (page._recordTimer) clearInterval(page._recordTimer);
    restoreGlobals();
  }
});

test('chat 页 k34 A20-M3：进入语音态即预热授权（授权弹窗落在切态时刻，不吃长按触摸）', () => {
  let authorizeCalls = 0;
  installWx({
    getSetting: (o) => o.success({ authSetting: {} }),      // 未授权过（首次使用）
    authorize: (o) => { authorizeCalls += 1; o.success({}); },
    showToast: () => {},
  });
  const page = makePage();
  try {
    page.switchInputMode({ currentTarget: { dataset: { mode: 'voice' } } });
  } finally {
    restoreGlobals();
  }
  assert.equal(page.data.inputMode, 'voice');
  assert.equal(authorizeCalls, 1, '切到语音态即发起授权（预热；长按路径随后走已授权快路径）');
});

test('chat 页 k34 A20-M3：授权请求单飞 —— 并发调用共享同一次结果，弹窗只弹一次', () => {
  let settingCalls = 0;
  let authorizeCalls = 0;
  const results = [];
  const pendingAuthorize = [];
  installWx({
    getSetting: (o) => { settingCalls += 1; o.success({ authSetting: {} }); },
    authorize: (o) => { authorizeCalls += 1; pendingAuthorize.push(o); },  // 挂起=弹窗未关
    showToast: () => {},
  });
  const page = makePage();
  try {
    page._ensureRecordPermission((ok) => results.push(ok));
    page._ensureRecordPermission((ok) => results.push(ok));
    assert.equal(settingCalls, 1, '授权在途 → 后续调用不重复 getSetting');
    assert.equal(authorizeCalls, 1, '授权弹窗只弹一次');
    assert.equal(results.length, 0, '结果未出前不派发回调');
    pendingAuthorize[0].success({});
    assert.deepEqual(results, [true, true], '两个回调共享同一次授权结果');
    // 结果派发后复位：下一次调用重新走完整流程
    page._ensureRecordPermission((ok) => results.push(ok));
    assert.equal(settingCalls, 2, '单飞只作用于在途请求');
    pendingAuthorize[1].success({});
    assert.deepEqual(results, [true, true, true]);
  } finally {
    restoreGlobals();
  }
});

test('chat 页：micLongPress 授权通过 → 切 voice 态并开录（k6-P2）+ M-1 窗口口径', () => {
  let recStarted = 0;
  installWx({
    showToast: () => {},
    vibrateShort: () => {},
    getSetting: (o) => o.success && o.success({ authSetting: { 'scope.record': true } }),
  });
  const page = makePage();
  page._speechPlugin = { getRecordRecognitionManager: () => page._recMgr };
  page._recMgr = { start: () => { recStarted++; }, stop: () => {} };
  try {
    const before = Date.now();
    page.micLongPress({ touches: [{ clientY: 88 }] });
    // 守卫通过 → 走既有 switchInputMode 内部路径进 voice 态
    assert.equal(page.data.inputMode, 'voice', '长按应切到按住说话态');
    assert.equal(page.data.isRecording, true, '授权通过且手指未松 → 直接开录');
    assert.equal(recStarted, 1);
    // M-1：_touchStartAt 回拨 ~350ms（longpress 触发延迟）→ 与按住条（touchstart 起算）
    // 同一口径：真实按住 ≥0.8s 松手即可发送，不被误判「太短」
    const back = before - page._touchStartAt;
    assert.ok(back >= 320 && back <= 390, `_touchStartAt 应回拨≈350ms（实测 ${back}ms）`);
    assert.equal(page._voiceFromLongPress, true, '应置长按来源标记（收尾复位用）');
  } finally {
    try { page._cleanupVoice(); } catch (e) { /* ignore */ }
    restoreGlobals();
  }
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

test('chat 页：retryStream AI 气泡重试 → 回溯 user 消息透传 image（接线路径，B4-1-fix I1）', () => {
  const calls = [];
  const origRetry = streamHost.retry;
  streamHost.retry = (msgId, text, tag, img) => { calls.push({ msgId, text, tag, img }); };
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);   // streaming=false → retryStream 可通过 active 闸
  const page = makePage({
    messages: [
      { id: 'u1', role: 'user', content: '（图片）', image: { url: 'http://x/uploads/r.jpg' } },
      { id: 'a1', role: 'ai', content: '生成失败', error: true, retryText: '（图片）' },
    ],
  });
  try {
    // 真实点击路径：data-id 是 AI 消息 id（AI 消息本身无 image）
    page.retryStream({ currentTarget: { dataset: { id: 'a1', text: '（图片）' } } });
  } finally {
    streamHost.retry = origRetry;
    restoreGlobals();
  }
  assert.equal(calls.length, 1);
  assert.equal(calls[0].msgId, 'a1');
  assert.ok(calls[0].img && calls[0].img.url === 'http://x/uploads/r.jpg',
    '重试应回溯最近 user 消息透传 image（保持 CV 链路）');
});

test('chat 页：图片消息后的文字追问重试 → 不误挂旧图（B4-1-fix I1 就近回溯）', () => {
  const calls = [];
  const origRetry = streamHost.retry;
  streamHost.retry = (...args) => { calls.push(args); };
  installWx({ setStorageSync: () => {} });
  streamHost.reset([]);
  const page = makePage({
    messages: [
      { id: 'u1', role: 'user', content: '（图片）', image: { url: 'http://x/uploads/r.jpg' } },
      { id: 'a1', role: 'ai', content: '面相分析……' },
      { id: 'u2', role: 'user', content: '再看仔细些' },
      { id: 'a2', role: 'ai', content: '生成失败', error: true },
    ],
  });
  try {
    page.retryStream({ currentTarget: { dataset: { id: 'a2', text: '再看仔细些' } } });
  } finally {
    streamHost.retry = origRetry;
    restoreGlobals();
  }
  assert.equal(calls.length, 1);
  assert.ok(!calls[0][3], '文字追问重试不得误挂更早图片消息的 image');
});

test('chat 页：_refreshQuota 额度条展示（k6-P1：不再联动输入条高度）', async () => {
  const origQuota = api.getChatQuota;
  api.getChatQuota = () => Promise.resolve({ is_member: false, limit: 15, used: 3, downgraded: false });
  installWx({ setStorageSync: () => {} });
  const page = makePage({ inputMode: 'voice' });
  try {
    await page._refreshQuota();
  } finally {
    api.getChatQuota = origQuota;
    restoreGlobals();
  }
  assert.equal(page.data.quotaBar.show, true);
  assert.equal(page.data.quotaBar.text, '今日 12/15 条');
  assert.equal(page.data.inputBarH, undefined, '额度条不再写输入条高度机件（k6-P1）');
  const sets = page._getSets();
  assert.ok(!sets.some((s) => 'inputBarH' in s), '额度刷新不得产生 inputBarH setData');
});
