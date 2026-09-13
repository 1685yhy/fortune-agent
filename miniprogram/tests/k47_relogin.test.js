// 易理明灯 — k47-D 重登纪律（退避 + 上限/长冷却 + 不可恢复短路 + 告警降噪）
// 运行：cd miniprogram && node --test tests/k47_relogin.test.js
//
// 背景（开发者工具实测 34 页 ~80 条同因告警）：
//   旧实现「每个 401 请求都重新 wx.login 一次」，登录链路故障时（开发环境无 appid →
//   login:fail 41002 appid missing）逐请求热重试，日志刷屏并把真实错误淹没。
// 本批加固：① 失败退避（2s→4s→8s…封顶 60s）② 连续失败达上限转长冷却（仍可自愈）
//   ③ 不可恢复错误不重试 ④ 同因告警只报一次 + 恢复后汇总 —— 且「会话过期→静默重登
//   →重放请求」正常链路不得倒退。
const test = require('node:test');
const assert = require('node:assert/strict');

/* ── 可控时钟（api.js 只用 Date.now 做退避判定） ── */
const realNow = Date.now;
let NOW = 1757000000000;
function advance(ms) { NOW += ms; }
test.after(() => { Date.now = realNow; });

/* ── 日志捕获（验证降噪） ── */
function captureLogs() {
  const logs = [];
  const ow = console.warn, oe = console.error, ol = console.log;
  const fmt = (a) => a.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join(' ');
  console.warn = (...a) => logs.push(['warn', fmt(a)]);
  console.error = (...a) => logs.push(['error', fmt(a)]);
  console.log = () => {};
  return {
    logs,
    warns: () => logs.filter((l) => l[0] === 'warn').map((l) => l[1]),
    restore: () => { console.warn = ow; console.error = oe; console.log = ol; },
  };
}

/* ── 环境：wx 桩 + 全新 api 模块（重登状态是模块级，逐用例重载以隔离） ── */
function makeEnv(opts = {}) {
  Date.now = () => NOW;                       // 可控时钟
  const store = { ylm_baseurl: { url: 'http://127.0.0.1:8767', t: NOW } };
  const state = {
    login: 0,            // wx.login 实际发起次数
    urls: [],            // 实际请求 URL
    bizCount: 0,
    loginOk: !!opts.loginOk,
    loginErr: opts.loginErr || (() => ({ errMsg: 'login:fail 系统错误，错误码：41002,appid missing', errCode: 41002 })),
    biz: opts.biz || (() => ({ statusCode: 401, data: {} })),
  };
  global.getApp = () => ({ globalData: {}, loginPromise: null });
  global.wx = {
    getStorageSync: (k) => (store[k] !== undefined ? store[k] : null),
    setStorageSync: (k, v) => { store[k] = v; },
    removeStorageSync: (k) => { delete store[k]; },
    getWindowInfo: () => ({ statusBarHeight: 20 }),
    showToast: () => {}, showLoading: () => {}, hideLoading: () => {},
    login: (o) => {
      state.login += 1;
      if (state.loginOk) o.success({ code: 'code-' + state.login });
      else o.fail(state.loginErr());
    },
    request: (o) => {
      state.urls.push(o.url);
      if (o.url.indexOf('/api/user/login') !== -1) {
        if (opts.loginApiOk === false) { o.success({ statusCode: 500, data: {} }); return; }
        o.success({ statusCode: 200, data: { token: 'tk-' + state.login, user: { id: 'u1' } } });
        return;
      }
      state.bizCount += 1;
      o.success(state.biz(o, state));
    },
  };
  delete require.cache[require.resolve('../utils/api')];
  const api = require('../utils/api');
  return { api, state, store };
}

const fail = (p) => p.then(() => 'ok', (e) => e.message);

test('k47-D：不可恢复错误（41002 appid missing）→ 只重登 1 次，不逐请求热重试，告警降噪', async () => {
  const env = makeEnv({ loginOk: false });
  const cap = captureLogs();
  const rs = [];
  for (let i = 0; i < 8; i++) rs.push(await fail(env.api.getDateFortune('2026-09-14')));
  cap.restore();
  assert.equal(env.state.login, 1, '不可恢复错误只发起 1 次 wx.login（旧实现 8 次）');
  assert.ok(rs.every((r) => r === '登录已过期，自动重登失败'), '每次请求仍如实失败（不得静默成功）');
  const reloginWarns = cap.warns().filter((w) => w.indexOf('自动重登失败') !== -1);
  const replayWarns = cap.warns().filter((w) => w.indexOf('自动重登后请求仍失败') !== -1);
  assert.equal(reloginWarns.length, 1, '同因告警只 1 条：' + JSON.stringify(cap.warns()));
  assert.equal(replayWarns.length, 1, '重放失败告警只 1 条');
  assert.ok(/41002/.test(reloginWarns[0]), '告警含错误码便于定位：' + reloginWarns[0]);
  assert.ok(replayWarns[0].indexOf('/api/calendar/today') !== -1, '重放告警含请求路径：' + replayWarns[0]);
  assert.ok(replayWarns[0].indexOf('user_id') === -1, '告警不含 query（隐私/身份不进日志）');
});

test('k47-D：可恢复失败 → 指数退避（退避期内不重登）+ 达上限转长冷却（不永久锁死）', async () => {
  const env = makeEnv({ loginOk: false, loginErr: () => ({ errMsg: 'login:fail 系统繁忙，请稍后重试' }) });
  const cap = captureLogs();
  await fail(env.api.getDateFortune('d1'));
  assert.equal(env.state.login, 1, '首次 401 → 重登 1 次');
  // 退避窗口内（<2s）连续 3 次 401 → 不再发起 wx.login（旧实现每次都会发）
  await fail(env.api.getDateFortune('d2'));
  await fail(env.api.getDateFortune('d3'));
  await fail(env.api.getDateFortune('d4'));
  assert.equal(env.state.login, 1, '退避期内不重复 wx.login');
  // 退避结束 → 允许下一次尝试（2s → 第 2 次）
  advance(2001);
  await fail(env.api.getDateFortune('d5'));
  assert.equal(env.state.login, 2, '退避到期后允许再次尝试');
  // 4s 退避 → 第 3 次；此后转入长冷却（10min）
  advance(4001);
  await fail(env.api.getDateFortune('d6'));
  assert.equal(env.state.login, 3, '第 3 次尝试');
  advance(60 * 1000);
  await fail(env.api.getDateFortune('d7'));
  await fail(env.api.getDateFortune('d8'));
  assert.equal(env.state.login, 3, '连续失败达上限 → 长冷却内不再重登');
  advance(10 * 60 * 1000 + 1);
  await fail(env.api.getDateFortune('d9'));
  assert.equal(env.state.login, 4, '长冷却结束仍可低频重试（网络恢复可自愈，不锁死）');
  cap.restore();
  const reloginWarns = cap.warns().filter((w) => w.indexOf('自动重登失败') !== -1);
  assert.equal(reloginWarns.length, 1, '降噪：7 次失败只 1 条告警（旧实现 9 条）');
  assert.ok(/退避|长冷却/.test(reloginWarns[0]), '告警说明退避/冷却状态：' + reloginWarns[0]);
});

test('k47-D：不倒退 —— 会话过期 → 静默重登 → 原请求重放（既有 UX 链路）', async () => {
  const env = makeEnv({
    loginOk: true,
    biz: (o, st) => (st.bizCount === 1 ? { statusCode: 401, data: {} } : { statusCode: 200, data: { ok: 1 } }),
  });
  const r = await env.api.getDateFortune('2026-09-14');
  assert.deepEqual(r, { ok: 1 }, '重放请求成功返回（不得因加固而失败）');
  assert.equal(env.state.login, 1, '仅 1 次静默重登');
  assert.equal(env.store.ylm_token, 'tk-1', 'token 已更新落库');
  // 之后再次 401（token 又过期）→ 仍可静默重登（成功即清零，cap 不误伤）
  env.state.bizCount = 0;
  const r2 = await env.api.getDateFortune('2026-09-15');
  assert.deepEqual(r2, { ok: 1 });
  assert.equal(env.state.login, 2, '每次都成功 → 重登次数不受上限约束（连续失败才计数）');
});

test('k47-D：登录成功（含用户主动重登 applyAuth）解除退避/不可恢复标记 → 静默重登能力恢复', async () => {
  const env = makeEnv({ loginOk: false });
  await fail(env.api.getDateFortune('d1'));
  await fail(env.api.getDateFortune('d2'));
  await fail(env.api.getDateFortune('d2b'));
  assert.equal(env.state.login, 1, '41002 → 停止尝试（3 次 401 只 1 次 wx.login）');
  const cap = captureLogs();
  env.api.applyAuth({ token: 'manual-token', user: { id: 'u9' } });   // 模拟用户在设置页重新登录
  assert.equal(env.store.ylm_token, 'manual-token');
  const recovery = cap.warns().filter((w) => w.indexOf('已恢复') !== -1);
  assert.equal(recovery.length, 1, '恢复时汇总一条（含此前抑制的重复告警计数）：' + JSON.stringify(cap.warns()));
  assert.ok(/抑制同因告警 2 条/.test(recovery[0]), '汇总条数正确：' + recovery[0]);
  cap.restore();
  // 恢复后 401 仍能静默重登（不再被 fatal 标记锁死）→ 登录链路与本请求均恢复
  env.state.biz = (o, st) => (st.bizCount === 1 ? { statusCode: 401, data: {} } : { statusCode: 200, data: { ok: 2 } });
  env.state.bizCount = 0;   // 新一轮请求：首个 401 → 触发静默重登，重放成功
  env.state.loginOk = true; // 登录链路已恢复（模拟网络/服务端恢复）
  const r = await env.api.getDateFortune('d3').then((x) => x, (e) => e.message);
  assert.equal(env.state.login, 2, '恢复后再次发起 wx.login（不永久锁死）');
  assert.deepEqual(r, { ok: 2 }, '恢复后「重登 → 重放」链路完整可用');
});
