// 易理明灯 — G2 保存链路假成功修复（P0）：成功提示必须以服务端确认响应为准
// 运行：cd miniprogram && node --test tests/g2_save_integrity.test.js
// 覆盖：
//   D2 api 响应业务校验（error 恒查 + 关键字段按调用点声明；不误伤 status 非 ok 的合法响应）
//   D1 支付 DEV_MODE：仅 develop 演示 / trial+release 支付失败必须真实失败，绝不假成功
//   A1 persons 保存失败 → 明确提示 + 不写本地 + 停留表单；缺 person 不拼装假对象
//   A2 persons 删除失败 → 明确提示 + 保留本地条目
//   A3 onboarding 建档失败 → 明确提示 + 不写本地 + 停留表单
//   A4 chat 建档提示条移除保存语义（零写入不得声称已保存）
//   A5/A6/A7 收藏后端同步失败 → 明示「暂存本机，云端同步失败」；气泡尾星标补齐后端调用
//   A8 意见反馈上报失败 → 明确提示
//   B1 赞踩上报失败 → 回滚点亮态 + 明确提示
//   B2/B3 手记/归档删除 storage 写失败 → 「删除失败」不假成功
//   B4 收藏本地导入失败 → 明确提示（重开收藏页自动重试机制存在）
const test = require('node:test');
const assert = require('node:assert/strict');

// ---- 全局环境（小程序运行时内置） ----
global.getApp = () => ({ globalData: {}, loginPromise: null });

const api = require('../utils/api');
const payment = require('../utils/payment');
const streamHost = require('../utils/streamHost');

/* ── 页面配置捕获（与既有测试同款） ── */
const savedPage = global.Page;
function loadPage(rel, path) {
  let cfg = null;
  global.Page = (c) => { cfg = c; };
  try {
    require(rel);
  } finally {
    global.Page = savedPage;
  }
  assert.ok(cfg, `${path} 页面配置应可加载`);
  return cfg;
}
const chatCfg = loadPage('../pages/chat/chat', 'chat.js');
const personsCfg = loadPage('../pages/persons/persons', 'persons.js');
const onboardingCfg = loadPage('../pages/onboarding/onboarding', 'onboarding.js');
const dreamsCfg = loadPage('../pages/dreams/dreams', 'dreams.js');
const historyCfg = loadPage('../pages/history/history', 'history.js');

/* ── setData 桩：模拟 wx 点路径赋值（如 'fb.m1-down'） ── */
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

/* ══════════════ D2 · api 响应业务校验 ══════════════ */

test('D2 validateBizResponse：响应体 error 恒查 → 抛错', () => {
  assert.throws(() => api.validateBizResponse({ error: '业务处理失败' }), /业务处理失败/);
  assert.throws(() => api.validateBizResponse({ status: 'ok', error: 'x' }), /x/);
});

test('D2 validateBizResponse：require 关键字段缺失/为空 → 抛错；齐全 → 通过', () => {
  assert.throws(() => api.validateBizResponse({ status: 'ok' }, ['person']), /缺少关键字段: person/);
  assert.throws(() => api.validateBizResponse({ status: 'ok', person: null }, ['person']), /缺少关键字段/);
  assert.throws(() => api.validateBizResponse({ person: undefined }, ['person']), /缺少关键字段/);
  assert.doesNotThrow(() => api.validateBizResponse({ status: 'ok', person: { id: 'p1' } }, ['person']));
  assert.doesNotThrow(() => api.validateBizResponse({ status: 'ok' }));           // 无 require 不拦截
});

test('D2 validateBizResponse：非对象响应体放行；status 非 ok 不误伤（粒度：全局不拦 status）', () => {
  assert.equal(api.validateBizResponse('plain'), 'plain');
  assert.equal(api.validateBizResponse(42), 42);
  assert.equal(api.validateBizResponse(null), null);
  // 合法 2xx 语义性状态（支付轮询 paid/pending、hourly no_bazi、上传 status）不得被全局拦截
  assert.doesNotThrow(() => api.validateBizResponse({ status: 'pending' }));
  assert.doesNotThrow(() => api.validateBizResponse({ status: 'paid' }));
  assert.doesNotThrow(() => api.validateBizResponse({ status: 'no_bazi' }));
});

/* ── wx.request 桩（探活 + 业务路由） ── */
function stubWxRequest(router) {
  global.wx.request = (opt) => {
    const url = String(opt.url || '');
    const hit = router.find((r) => url.indexOf(r.match) !== -1);
    if (!hit) { opt.fail && opt.fail({ errMsg: 'mock no route: ' + url }); return; }
    if (hit.fail) { opt.fail && opt.fail(hit.fail); return; }
    opt.success && opt.success(hit.res);
  };
}

test('D2 request 集成：2xx 但响应体含 error → reject（不得 resolve 假成功）', async () => {
  const oldWx = global.wx;
  global.wx = {
    getStorageSync: () => undefined,
    setStorageSync: () => {},
    removeStorageSync: () => {},
    request: () => {},
    showLoading: () => {},
    hideLoading: () => {},
    showToast: () => {},
  };
  stubWxRequest([
    { match: '/api/health', res: { statusCode: 200, data: { ok: true } } },
    { match: '/api/pricing', res: { statusCode: 200, data: { error: '服务异常' } } },
  ]);
  try {
    await assert.rejects(() => api.getPricing(), /服务异常/);
  } finally {
    global.wx = oldWx;
  }
});

test('D2 createPerson 集成：2xx 缺 person → reject（A1 依赖的确认字段存在性校验）', async () => {
  const oldWx = global.wx;
  global.wx = {
    getStorageSync: () => undefined,
    setStorageSync: () => {},
    removeStorageSync: () => {},
    request: () => {},
    showLoading: () => {},
    hideLoading: () => {},
    showToast: () => {},
  };
  stubWxRequest([
    { match: '/api/health', res: { statusCode: 200, data: { ok: true } } },
    { match: '/api/persons', res: { statusCode: 200, data: { status: 'ok' } } },  // 无 person！
  ]);
  try {
    await assert.rejects(
      () => api.createPerson({ name: '张三', birth_year: 1990 }),
      /缺少关键字段: person/
    );
  } finally {
    global.wx = oldWx;
  }
});

/* ══════════════ D1 · 支付 DEV_MODE ══════════════ */

test('D1 isDevDemo：仅 develop 环境为演示；trial/release/异常/无 wx 恒 false', () => {
  const savedWx = global.wx;
  try {
    global.wx = { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }) };
    assert.equal(payment.isDevDemo(), true);
    global.wx = { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'trial' } }) };
    assert.equal(payment.isDevDemo(), false);
    global.wx = { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'release' } }) };
    assert.equal(payment.isDevDemo(), false);
    global.wx = { getAccountInfoSync: () => { throw new Error('no env'); } };
    assert.equal(payment.isDevDemo(), false);   // 环境异常 → 生产安全失败
    global.wx = undefined;
    assert.equal(payment.isDevDemo(), false);   // 无 wx 环境（单测加载）→ false
  } finally {
    global.wx = savedWx;
  }
});

/* 支付全链路：虚拟支付未启用 → 降级 mock → mock 支付失败（非取消）
   k52-1：桩需显式声明平台——getDeviceInfo().platform='android' 即「安卓端行为逐字不变」
   的那条路径（iOS/未判定会被 k52 闸门拦下，见 k52_ios_pay.test.js）。 */
function stubPayWx(envVersion, toasts) {
  global.wx = {
    canIUse: () => true,
    getDeviceInfo: () => ({ platform: 'android' }),
    getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
    getStorageSync: () => undefined,
    setStorageSync: () => {},
    removeStorageSync: () => {},
    request: () => {},
    showLoading: () => {},
    hideLoading: () => {},
    showToast: (o) => toasts.push(o.title),
  };
  stubWxRequest([
    { match: '/api/health', res: { statusCode: 200, data: { ok: true } } },
    { match: '/api/pay/virtual/create', res: { statusCode: 400, data: { detail: { code: 'virtual_pay_not_enabled' } } } },
    { match: '/api/pay/create', fail: { errMsg: 'request:fail' } },   // mock 支付链路失败
  ]);
}

test('D1 支付全链路（develop）：mock 失败 → 演示成功（仅开发环境允许）', async () => {
  const toasts = [];
  const savedWx = global.wx;
  stubPayWx('develop', toasts);
  try {
    const r = await payment.purchase('deep_report');
    assert.equal(r.success, true);
    assert.ok(r.orderId && String(r.orderId).indexOf('demo_') === 0);
    assert.ok(toasts.includes('购买成功！（演示）'));
  } finally {
    global.wx = savedWx;
  }
});

test('D1 支付全链路（release）：mock 支付失败 → 真实失败，绝不出现「购买成功」', async () => {
  const toasts = [];
  const savedWx = global.wx;
  stubPayWx('release', toasts);
  try {
    const r = await payment.purchase('deep_report');
    assert.equal(r.success, false);
    assert.ok(toasts.includes('支付失败，请稍后再试'));
    assert.ok(!toasts.some((t) => t.indexOf('购买成功') !== -1), '生产支付失败不得出现购买成功提示: ' + toasts.join(','));
  } finally {
    global.wx = savedWx;
  }
});

test('D1 支付全链路（trial）：体验版同样真实失败，不假成功', async () => {
  const toasts = [];
  const savedWx = global.wx;
  stubPayWx('trial', toasts);
  try {
    const r = await payment.purchase('deep_report');
    assert.equal(r.success, false);
    assert.ok(toasts.includes('支付失败，请稍后再试'));
    assert.ok(!toasts.some((t) => t.indexOf('购买成功') !== -1));
  } finally {
    global.wx = savedWx;
  }
});

/* ══════════════ A1/A2 · persons 保存/删除 ══════════════ */

function makePersonsPage(toasts, writes) {
  global.wx = {
    getStorageSync: (k) => (k === 'ylm_persons' ? [] : undefined),
    setStorageSync: (k, v) => writes.push([k, v]),
    removeStorageSync: () => {},
    showToast: (o) => toasts.push(o.title),
    showModal: () => {},
    getWindowInfo: () => ({ statusBarHeight: 20 }),
  };
  const page = Object.assign({}, personsCfg);
  page.data = {
    navOff: 0, dark: false, mode: 'form', persons: [], loaded: false, removingId: '',
    editing: null, dName: '张三', dRel: '自己', dCal: 'solar', dDate: '1990-01-01',
    dHourIndex: 0, dGender: '女', dPlace: '北京', filled: true, hint: '', saving: false,
  };
  page.setData = dottedSetData;
  return page;
}

test('A1 persons 保存失败：明确失败提示 + 不写本地缓存 + 停留表单页可重试', async () => {
  const toasts = [];
  const writes = [];
  const page = makePersonsPage(toasts, writes);
  const orig = api.createPerson;
  api.createPerson = () => Promise.reject(new Error('network down'));
  try {
    await page.onSave();
    assert.ok(toasts.includes('保存失败，请重试'), '应有失败提示: ' + toasts.join(','));
    assert.ok(!toasts.some((t) => t.indexOf('已加入') !== -1 || t.indexOf('已保存') !== -1), '不得弹成功: ' + toasts.join(','));
    assert.equal(page.data.mode, 'form', '失败后停留表单页（可重试）');
    assert.equal(page.data.editing, null);
    assert.ok(!writes.some((w) => w[0] === 'ylm_persons'), '失败不得写本地档案缓存');
  } finally {
    api.createPerson = orig;
  }
});

test('A1 persons 保存：2xx 缺 res.person → 视为失败（不拼装假对象）', async () => {
  const toasts = [];
  const writes = [];
  const page = makePersonsPage(toasts, writes);
  const orig = api.createPerson;
  api.createPerson = () => Promise.resolve({ status: 'ok' });   // 服务端确认响应缺 person
  try {
    await page.onSave();
    assert.ok(toasts.includes('保存失败，请重试'));
    assert.ok(!writes.some((w) => w[0] === 'ylm_persons'));
    assert.equal(page.data.mode, 'form');
  } finally {
    api.createPerson = orig;
  }
});

test('A1 persons 保存成功：以服务端 res.person 为准落本地 + 回列表', async () => {
  const toasts = [];
  const writes = [];
  const page = makePersonsPage(toasts, writes);
  const person = { id: 'p1', name: '张三', relation: '自己', gender: '女', birth_year: 1990, birth_month: 1, birth_day: 1, birth_hour: 23, birth_minute: 0, calendar: 'solar', city: '北京', is_default: true, created_at: Date.now() };
  const origCreate = api.createPerson;
  const origGet = api.getPersons;
  api.createPerson = () => Promise.resolve({ status: 'ok', person });
  api.getPersons = () => Promise.resolve({ status: 'ok', persons: [person] });
  try {
    await page.onSave();
    assert.ok(toasts.some((t) => t.indexOf('已加入档案') !== -1), '成功以服务端响应为准: ' + toasts.join(','));
    assert.equal(page.data.mode, 'list');
    const localWrite = writes.find((w) => w[0] === 'ylm_persons');
    assert.ok(localWrite, '成功后写本地缓存（与服务端一致）');
    assert.ok(localWrite[1].some((p) => p.id === 'p1'));
  } finally {
    api.createPerson = origCreate;
    api.getPersons = origGet;
  }
});

test('A2 persons 删除失败：明确提示 + 保留本地条目 + 不弹「已删除」', async () => {
  const toasts = [];
  const writes = [];
  const page = makePersonsPage(toasts, writes);
  const orig = api.deletePerson;
  api.deletePerson = () => Promise.reject(new Error('network'));
  try {
    await page._doDelete({ id: 'p1', name: '张三', relation: '自己' });
    await new Promise((r) => setTimeout(r, 0));   // _doDelete 非 async：等 promise 链微任务
    assert.ok(toasts.includes('删除失败，请重试'), '应有失败提示: ' + toasts.join(','));
    assert.ok(!toasts.includes('已删除档案'));
    assert.equal(page.data.removingId, '', '失败后清除消散动画态');
    assert.ok(!writes.some((w) => w[0] === 'ylm_persons'), '失败不得改写本地缓存（条目保留）');
  } finally {
    api.deletePerson = orig;
  }
});

test('A2 persons 删除成功：服务端确认 → 本地同步 + 成功提示', async () => {
  const toasts = [];
  const writes = [];
  const page = makePersonsPage(toasts, writes);
  const origDel = api.deletePerson;
  const origGet = api.getPersons;
  api.deletePerson = () => Promise.resolve({ status: 'ok' });
  api.getPersons = () => Promise.resolve({ status: 'ok', persons: [] });
  try {
    await page._doDelete({ id: 'p1', name: '张三', relation: '自己' });
    await new Promise((r) => setTimeout(r, 0));   // _doDelete 非 async：等 promise 链微任务
    assert.ok(toasts.includes('已删除档案'));
    assert.ok(writes.some((w) => w[0] === 'ylm_persons'));
  } finally {
    api.deletePerson = origDel;
    api.getPersons = origGet;
  }
});

/* ══════════════ A3 · onboarding 建档 ══════════════ */

function makeOnboardingPage(toasts, writes) {
  global.wx = {
    getStorageSync: (k) => (k === 'ylm_persons' ? [] : undefined),
    setStorageSync: (k, v) => writes.push([k, v]),
    removeStorageSync: () => {},
    showToast: (o) => toasts.push(o.title),
    showModal: () => {},
    getWindowInfo: () => ({ statusBarHeight: 20 }),
  };
  const page = Object.assign({}, onboardingCfg);
  page.data = {
    navOff: 0, dark: false, phase: 'form', stepCur: 2, tourSeen: false,
    cal: 'solar', year: '1990', month: '1', day: '1', hourIndex: 0, gender: '女', place: '北京',
    filled: true, summary: '', doneSummary: '', saving: false,
  };
  page.setData = dottedSetData;
  return page;
}

test('A3 onboarding 建档失败：明确提示 + 不写本地 + 停留表单页（不跳完成页）', async () => {
  const toasts = [];
  const writes = [];
  const page = makeOnboardingPage(toasts, writes);
  const orig = api.createPerson;
  api.createPerson = () => Promise.reject(new Error('network'));
  try {
    await page.onSubmit();
    assert.ok(toasts.includes('建档失败，请重试'), '应有失败提示: ' + toasts.join(','));
    assert.ok(!toasts.some((t) => t.indexOf('本地建档') !== -1), '不得出现假成功文案');
    assert.equal(page.data.phase, 'form', '失败停留在表单页');
    assert.ok(!writes.some((w) => w[0] === 'ylm_persons'), '失败不写本地档案');
    assert.ok(!writes.some((w) => w[0] === 'ylm_onboard_done'), '失败不得标记建档完成');
  } finally {
    api.createPerson = orig;
  }
});

test('A3 onboarding 建档成功：以服务端 res.person 为准 → 本地同步 + 进完成页', async () => {
  const toasts = [];
  const writes = [];
  const page = makeOnboardingPage(toasts, writes);
  const person = { id: 'p1', name: '我', relation: '自己', gender: '女', birth_year: 1990, birth_month: 1, birth_day: 1, birth_hour: 23, birth_minute: 0, calendar: 'solar', city: '北京', is_default: true };
  const orig = api.createPerson;
  api.createPerson = () => Promise.resolve({ status: 'ok', person });
  try {
    await page.onSubmit();
    assert.equal(page.data.phase, 'done');
    const localWrite = writes.find((w) => w[0] === 'ylm_persons');
    assert.ok(localWrite && localWrite[1].some((p) => p.id === 'p1'), '成功落本地与服务端一致');
  } finally {
    api.createPerson = orig;
  }
});

/* ══════════════ A4 · chat 建档提示条 ══════════════ */

test('A4 提示条确认：移除保存语义（零写入不声称已保存），仅档案指引', () => {
  const toasts = [];
  const modals = [];
  global.wx = {
    getStorageSync: () => undefined,
    setStorageSync: () => {},
    removeStorageSync: () => {},
    showToast: (o) => toasts.push(o.title),
    showModal: (o) => modals.push(o),
  };
  const page = Object.assign({}, chatCfg);
  page.data = { saveBanner: true };
  page.setData = dottedSetData;

  page.onSaveBannerTap();
  assert.equal(modals.length, 1);
  const modal = modals[0];
  assert.equal(modal.confirmText, '知道了', '不得再有「确认保存」');
  assert.ok(modal.content.indexOf('保存到档案') === -1 && modal.content.indexOf('是否将其保存') === -1,
    '弹层文案不得承诺前端保存: ' + modal.content);
  modal.success({ confirm: true });
  assert.ok(toasts.includes('已为你标记，可在档案页查看'));
  assert.ok(!toasts.includes('已保存到档案'), '不得出现「已保存」假成功: ' + toasts.join(','));
});

test('A4 提示条取消：仅清标记，提示「未标记」', () => {
  const toasts = [];
  const modals = [];
  global.wx = {
    getStorageSync: () => undefined,
    setStorageSync: () => {},
    removeStorageSync: () => {},
    showToast: (o) => toasts.push(o.title),
    showModal: (o) => modals.push(o),
  };
  const page = Object.assign({}, chatCfg);
  page.data = { saveBanner: true };
  page.setData = dottedSetData;
  page.onSaveBannerTap();
  modals[0].success({ confirm: false });
  assert.ok(toasts.includes('未标记'));
});

/* ══════════════ A5/A6/A7 · 收藏后端同步 ══════════════ */

function makeChatPage(msgs, wxStub) {
  if (wxStub) {
    global.wx = wxStub;   // 调用方已提供记录桩时不再覆盖
  } else {
    global.wx = {
      getStorageSync: () => undefined,
      setStorageSync: () => {},
      removeStorageSync: () => {},
      showToast: () => {},
      showModal: () => {},
    };
  }
  const page = Object.assign({}, chatCfg);
  page.data = {
    actionMenu: { show: false, msgId: '', role: '' },
    messages: msgs || [],
    multiMode: false, multiSel: {}, multiCount: 0, multiAll: false,
    fb: {},
    fbSheet: { show: false, msgId: '', reasons: {}, note: '', canSubmit: false },
    reactions: {}, saveBanner: false,
  };
  page.setData = dottedSetData;
  page._findMessage = (id) => (msgs || []).find((m) => m.id === id) || null;
  return page;
}

test('A5 长按收藏后端同步失败：明示「收藏暂存本机，云端同步失败」', async () => {
  const toasts = [];
  const wxStub = { showToast: (o) => toasts.push(o.title), getStorageSync: () => undefined, setStorageSync: () => {}, removeStorageSync: () => {} };
  global.wx = wxStub;
  const page = makeChatPage([], wxStub);
  const origAdd = api.favAdd;
  api.favAdd = () => Promise.reject(new Error('network'));
  try {
    page._syncKeepBackend({ id: 'm1', role: 'ai', content: 'x' }, true);
    await new Promise((r) => setTimeout(r, 0));
    assert.ok(toasts.includes('收藏暂存本机，云端同步失败'), '失败必须明示，不得静默: ' + toasts.join(','));
  } finally {
    api.favAdd = origAdd;
  }
});

test('A6 气泡尾星标收藏：补齐后端调用（此前完全无后端）', async () => {
  const favAddCalls = [];
  const origAdd = api.favAdd;
  const origPatch = streamHost.patchMessage;
  api.favAdd = (data) => { favAddCalls.push(data); return Promise.resolve({ success: true }); };
  streamHost.patchMessage = () => {};
  try {
    const msg = { id: 'm1', role: 'ai', kept: false, content: '好话' };
    const page = makeChatPage([msg]);
    page.toggleFb({ currentTarget: { dataset: { id: 'm1', k: 'keep' } } });
    assert.equal(favAddCalls.length, 1, '气泡尾星标收藏必须直连后端');
    assert.deepEqual(favAddCalls[0], { type: 'chat', ref_id: 'm1', summary: '好话' });
  } finally {
    api.favAdd = origAdd;
    streamHost.patchMessage = origPatch;
  }
});

test('A6 用户消息星标：本地行为不变，不同步后端（契约保持）', async () => {
  const favAddCalls = [];
  const origAdd = api.favAdd;
  const origPatch = streamHost.patchMessage;
  api.favAdd = (data) => { favAddCalls.push(data); return Promise.resolve({ success: true }); };
  streamHost.patchMessage = () => {};
  try {
    const msg = { id: 'u1', role: 'user', kept: false, content: '你好' };
    const page = makeChatPage([msg]);
    page.toggleFb({ currentTarget: { dataset: { id: 'u1', k: 'keep' } } });
    assert.equal(favAddCalls.length, 0);
  } finally {
    api.favAdd = origAdd;
    streamHost.patchMessage = origPatch;
  }
});

test('A8 反馈面板上报失败：明确失败提示（不静默、不点亮）', async () => {
  const toasts = [];
  const origFb = api.feedback;
  const wxStub = { showToast: (o) => toasts.push(o.title), getStorageSync: () => undefined, setStorageSync: () => {}, removeStorageSync: () => {} };
  global.wx = wxStub;
  api.feedback = () => Promise.reject(new Error('network'));
  try {
    const msg = { id: 'm1', role: 'ai', consultationId: 'c1', content: 'x' };
    const page = makeChatPage([msg], wxStub);
    page.openFeedbackPanel('m1');
    page.onFbReasonTap({ currentTarget: { dataset: { opt: '内容不准确' } } });
    page.submitFbSheet();
    await new Promise((r) => setTimeout(r, 0));
    assert.ok(toasts.includes('反馈提交失败，请重试'), '失败必须明示: ' + toasts.join(','));
    assert.ok(!toasts.includes('已收到反馈'), '失败不得弹成功: ' + toasts.join(','));
    assert.equal(page.data.fb['m1-down'], undefined, '失败不得点亮（不静默装成功）');
  } finally {
    api.feedback = origFb;
  }
});

test('A8 反馈面板上报成功：以服务端为准点亮 + 弹成功', async () => {
  const toasts = [];
  const origFb = api.feedback;
  const wxStub = { showToast: (o) => toasts.push(o.title), getStorageSync: () => undefined, setStorageSync: () => {}, removeStorageSync: () => {} };
  global.wx = wxStub;
  api.feedback = () => Promise.resolve({ ok: true });
  try {
    const msg = { id: 'm1', role: 'ai', consultationId: 'c1', content: 'x' };
    const page = makeChatPage([msg], wxStub);
    page.openFeedbackPanel('m1');
    page.onFbReasonTap({ currentTarget: { dataset: { opt: '内容不准确' } } });
    page.submitFbSheet();
    await new Promise((r) => setTimeout(r, 0));
    assert.ok(toasts.includes('已收到反馈'), '以服务端为准弹成功');
    assert.equal(page.data.fb['m1-down'], true, '成功后踩图标点亮');
  } finally {
    api.feedback = origFb;
  }
});

test('B1 赞踩上报失败：回滚点亮态 + 明确提示（失败不点亮）', async () => {
  const toasts = [];
  const origFb = api.feedback;
  const wxStub = { showToast: (o) => toasts.push(o.title), getStorageSync: () => undefined, setStorageSync: () => {}, removeStorageSync: () => {} };
  global.wx = wxStub;
  api.feedback = () => Promise.reject(new Error('network'));
  try {
    const msg = { id: 'm1', role: 'ai', consultationId: 'c1', content: 'x' };
    const page = makeChatPage([msg], wxStub);
    page._toggleFbCore('m1', 'down');
    assert.equal(page.data.fb['m1-down'], true, '先乐观点亮');
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(page.data.fb['m1-down'], false, '上报失败必须回滚，不点亮');
    assert.ok(toasts.includes('反馈失败，请重试'));
  } finally {
    api.feedback = origFb;
  }
});

/* ══════════════ B2 · 解梦手记删除 ══════════════ */

test('B2 手记删除：storage 写失败 → 「删除失败，请重试」不假成功', async () => {
  const toasts = [];
  const oldWx = global.wx;
  global.wx = {
    getStorageSync: (k) => {
      if (k === 'ylm_chat_messages') return [{ id: 'd1', role: 'ai', tag: '解梦 · 夜记', content: 'x' }, { id: 'u1', role: 'user', content: '梦到水' }];
      return [];
    },
    setStorageSync: () => { throw new Error('quota exceeded'); },   // 写失败
    removeStorageSync: () => {},
    showToast: (o) => toasts.push(o.title),
    showModal: () => {},
    getWindowInfo: () => ({ statusBarHeight: 20 }),
  };
  const page = Object.assign({}, dreamsCfg);
  page.data = { navOff: 0, dark: false, notes: [], loaded: false, view: 'list', current: null, dlgDel: { id: 'd1', key: '梦' }, removing: '' };
  page.setData = dottedSetData;
  try {
    page.confirmDel();
    await new Promise((r) => setTimeout(r, 400));   // 等删除动画 330ms
    assert.ok(toasts.includes('删除失败，请重试'), '写失败必须真实提示: ' + toasts.join(','));
    assert.ok(!toasts.includes('已删除此记'));
  } finally {
    global.wx = oldWx;
  }
});

test('B2 手记删除成功：storage 写成功 → 「已删除此记」', async () => {
  const toasts = [];
  const oldWx = global.wx;
  global.wx = {
    getStorageSync: (k) => {
      if (k === 'ylm_chat_messages') return [{ id: 'd1', role: 'ai', tag: '解梦 · 夜记', content: 'x' }, { id: 'u1', role: 'user', content: '梦到水' }];
      return [];
    },
    setStorageSync: () => {},
    removeStorageSync: () => {},
    showToast: (o) => toasts.push(o.title),
    showModal: () => {},
    getWindowInfo: () => ({ statusBarHeight: 20 }),
  };
  const page = Object.assign({}, dreamsCfg);
  page.data = { navOff: 0, dark: false, notes: [], loaded: false, view: 'list', current: null, dlgDel: { id: 'd1', key: '梦' }, removing: '' };
  page.setData = dottedSetData;
  try {
    page.confirmDel();
    await new Promise((r) => setTimeout(r, 400));
    assert.ok(toasts.includes('已删除此记'));
  } finally {
    global.wx = oldWx;
  }
});

/* ══════════════ B3 · 历史归档删除 ══════════════ */

function makeHistoryPage(toasts, throwOnWrite) {
  global.wx = {
    getStorageSync: (k) => {
      if (k === 'ylm_chat_archives') return [{ id: 's1', createdAt: Date.now(), messages: [{ id: 'm1', role: 'user', content: '问个事' }, { id: 'm2', role: 'ai', content: '答' }] }];
      if (k === 'ylm_chat_messages') return [];
      return undefined;
    },
    setStorageSync: () => { if (throwOnWrite) throw new Error('quota exceeded'); },
    removeStorageSync: () => {},
    showToast: (o) => toasts.push(o.title),
    showModal: () => {},
    getWindowInfo: () => ({ statusBarHeight: 20 }),
  };
  const page = Object.assign({}, historyCfg);
  page.data = { navOff: 0, dark: false, q: '', groups: [], loaded: false, view: 'list', current: null, truncated: false, dlgDel: { id: 's1', isCurrent: false, brief: '问个事' }, removing: '' };
  page.setData = dottedSetData;
  return page;
}

test('B3 归档删除：storage 写失败 → 「删除失败，请重试」不假成功', async () => {
  const toasts = [];
  const oldWx = global.wx;
  const page = makeHistoryPage(toasts, true);
  try {
    page.confirmDel();
    await new Promise((r) => setTimeout(r, 400));   // 等删除动画 340ms
    assert.ok(toasts.includes('删除失败，请重试'), '写失败必须真实提示: ' + toasts.join(','));
    assert.ok(!toasts.includes('已删除 · 夜话不留痕'));
  } finally {
    global.wx = oldWx;
  }
});

test('B3 归档删除成功：storage 写成功 → 「已删除 · 夜话不留痕」', async () => {
  const toasts = [];
  const oldWx = global.wx;
  const page = makeHistoryPage(toasts, false);
  try {
    page.confirmDel();
    await new Promise((r) => setTimeout(r, 400));
    assert.ok(toasts.includes('已删除 · 夜话不留痕'));
  } finally {
    global.wx = oldWx;
  }
});

/* ══════════════ B4 · 收藏本地导入 ══════════════ */

test('B4 收藏本地导入失败：明确提示「云端同步失败，稍后自动重试」（重开收藏页自动重试）', async () => {
  const toasts = [];
  const savedWx = global.wx;
  const savedPage2 = global.Page;
  let favCfg = null;
  global.Page = (c) => { favCfg = c; };
  try {
    require('../pages/favorites/favorites');
  } finally {
    global.Page = savedPage2;
  }
  const origImport = api.favImport;
  const origFavList = api.favList;
  const origMingSaved = api.getMingSaved;
  api.favImport = () => Promise.reject(new Error('network'));
  api.favList = () => Promise.resolve({ items: [] });
  api.getMingSaved = () => Promise.resolve({ items: [] });
  try {
    global.wx = {
      getStorageSync: (k) => {
        if (k === 'ylm_chat_messages') return [{ id: 'm1', role: 'ai', kept: true, content: 'x', time: '', keptAt: 1000 }];
        if (k === 'ylm_chat_archives') return [];
        return undefined;
      },
      setStorageSync: () => {},
      removeStorageSync: () => {},
      showToast: (o) => toasts.push(o.title),
      showModal: () => {},
      getWindowInfo: () => ({ statusBarHeight: 20 }),
    };
    const page = Object.assign({}, favCfg);
    page.data = { cat: 'all', items: [], loaded: false };
    page.setData = dottedSetData;
    page._tryImportLocal();
    await new Promise((r) => setTimeout(r, 0));
    assert.ok(toasts.includes('云端同步失败，稍后自动重试'), '导入失败必须明示: ' + toasts.join(','));
  } finally {
    api.favImport = origImport;
    api.favList = origFavList;
    api.getMingSaved = origMingSaved;
    global.wx = savedWx;
  }
});
