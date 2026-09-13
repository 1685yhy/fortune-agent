// 易理明灯 — k47-E 全局错误日志可定位 + 工具侧噪音不计入留痕
// 运行：cd miniprogram && node --test tests/k47_global_error.test.js
//
// 背景（控制方独立复核 + 本批插桩实测）：
//   开发者工具里出现 7 条 console|error|[{}]；插桩核实同批**应用侧 console.error 仅 1 次**
//   （我们的全局错误处理器），其余 7 条为工具自身记录。真实异常为开发者工具加载
//   app.json 声明的 WechatSI 插件时框架崩溃：
//     MiniProgramError / Cannot read properties of undefined (reading 'version')
//     at kU (…/__dev__/WAServiceMainContext.js…) / at Object.AU [as reportPluginCodeRequire]
//     at o.beforeFactory (…/__dev__/WASubContext.js…) / at weapp:///__onlineplugin__/wx069ba97219f66d99/0.3.5/…
//   要求：① 我们那条必须可定位（消息 + 堆栈摘要，不含隐私）；② 工具侧噪音标注且
//   不进 ylm_last_error 统计。
const test = require('node:test');
const assert = require('node:assert/strict');

const { errDetail, logErr, logWarn, logErrDetail, errText } = require('../utils/log');

const TOOL_ERR = 'MiniProgramError\n'
  + "Cannot read properties of undefined (reading 'version')\n"
  + "TypeError: Cannot read properties of undefined (reading 'version')\n"
  + '    at kU (http://127.0.0.1:26422/__dev__/WAServiceMainContext.js?t=wechat&v=3.17.0:1:2336659)\n'
  + '    at Object.AU [as reportPluginCodeRequire] (http://127.0.0.1:26422/__dev__/WAServiceMainContext.js?t=wechat&v=3.17.0:1:2336859)\n'
  + '    at o.beforeFactory (http://127.0.0.1:26422/__dev__/WASubContext.js?t=wechat&v=3.17.0:1:500571)\n'
  + '    at weapp:///__onlineplugin__/wx069ba97219f66d99/0.3.5/appservice.js:1:1';

/* 捕获 console.error/warn 的实参（验证「绝不打裸对象」） */
function captureLogs() {
  const calls = [];
  const oe = console.error, ow = console.warn;
  console.error = (...a) => calls.push(['error', a]);
  console.warn = (...a) => calls.push(['warn', a]);
  return { calls, restore: () => { console.error = oe; console.warn = ow; } };
}

test('k47-E：errDetail 从 MiniProgramError 文本提取「消息 + 堆栈摘要」并识别工具侧噪音', () => {
  const d = errDetail(new Error(TOOL_ERR));
  assert.equal(d.noise, true, 'devtools/插件加载崩溃应判为工具侧噪音');
  assert.ok(d.message.indexOf("Cannot read properties of undefined (reading 'version')") !== -1, d.message);
  assert.ok(d.frames.length >= 2, '应含堆栈摘要帧：' + JSON.stringify(d.frames));
  assert.ok(d.frames[0].indexOf('WAServiceMainContext') !== -1);
  assert.ok(d.text.indexOf('reportPluginCodeRequire') !== -1 || d.text.indexOf('WAServiceMainContext') !== -1);
  assert.ok(d.text.indexOf('\n') === -1, '日志必须单行');
});

test('k47-E：业务侧异常（无框架帧）不误判为噪音，且同样可定位', () => {
  const e = new TypeError('Cannot read property 生辰 of undefined');
  e.stack = 'TypeError: Cannot read property x of undefined\n'
    + '    at onGenerate (weapp:///pages/ming/ming.js:131:9)\n'
    + '    at Object.onTap (weapp:///pages/ming/ming.js:88:5)';
  const d = errDetail(e);
  assert.equal(d.noise, false, '业务异常不得被标为工具噪音');
  assert.ok(d.message.indexOf('Cannot read property') !== -1, '消息摘要：' + d.message);
  assert.ok(d.text.indexOf('pages/ming/ming.js:131:9') !== -1, '堆栈摘要应含业务帧：' + d.text);
  assert.ok(d.text.indexOf('onGenerate') !== -1, '堆栈摘要应含函数名：' + d.text);
});

test('k47-E：logErrDetail 输出单条字符串（消息+堆栈摘要+噪音标注），无裸对象', () => {
  const cap = captureLogs();
  logErrDetail('全局错误', new Error(TOOL_ERR));
  logErrDetail('全局错误', new TypeError('业务异常'));
  logErr('API request', { errMsg: 'request:fail timeout', statusCode: 0 }, '/api/chat');
  logWarn('mingren 列表加载失败', new Error('登录已过期，自动重登失败'));
  cap.restore();
  assert.deepEqual(cap.calls.map((c) => c[0]), ['error', 'error', 'error', 'warn']);
  cap.calls.forEach((c, i) => {
    assert.equal(c[1].length, 1, `第 ${i} 条日志只应有 1 个实参（旧实现打裸对象 → automator 记成 [{}]）`);
    assert.equal(typeof c[1][0], 'string');
  });
  const noiseLine = cap.calls[0][1][0];
  assert.ok(noiseLine.indexOf('工具侧噪音') !== -1, '工具侧噪音应有标注：' + noiseLine);
  assert.ok(noiseLine.indexOf('WAServiceMainContext') !== -1, '应含堆栈摘要：' + noiseLine);
  const bizLine = cap.calls[1][1][0];
  assert.ok(bizLine.indexOf('工具侧噪音') === -1, '业务异常不得被标注为噪音');
  assert.ok(bizLine.indexOf('业务异常') !== -1, '消息摘要：' + bizLine);
  assert.ok(bizLine.indexOf('at ') !== -1, '含堆栈摘要帧：' + bizLine);
  assert.ok(bizLine.indexOf('\n') === -1, '单行');
  assert.ok(cap.calls[2][1][0].indexOf('request:fail timeout') !== -1);
  assert.ok(cap.calls[3][1][0].indexOf('登录已过期，自动重登失败') !== -1);
});

test('k47-E：errText 对空对象/异常形态都给出可读摘要（不再出现裸 {}）', () => {
  assert.equal(errText({}), 'empty-object');
  assert.equal(errText(new Error('boom')), 'boom');
  assert.equal(errText({ errCode: 41002, errMsg: 'appid missing' }), 'code=41002 appid missing');
  assert.equal(errText(null), 'unknown');
  assert.equal(errText(''), 'empty-string');
});

test('k47-E：隐私守卫 —— 识别/订单类响应体不进日志（只打结果码/字段名）', () => {
  // 语音识别失败：res 可能含用户语音转写原文 → 调用点显式归一化
  const res = { result: '用户说过的原话', retcode: -30001 };
  const safe = { errCode: res.retcode, errMsg: '语音识别失败（内容不回显）' };
  const line = errText(safe);
  assert.ok(line.indexOf('用户说过的原话') === -1, '日志不得含识别原文：' + line);
  assert.ok(line.indexOf('-30001') !== -1 && line.indexOf('语音识别失败') !== -1, line);
  // 支付订单响应：只列缺失字段名
  const miss = errText({ errMsg: 'missing: signData,outTradeNo' });
  assert.ok(miss.indexOf('outTradeNo') !== -1 && miss.indexOf('signData') !== -1);
});

/* ── app.js 接线：工具侧噪音不进 ylm_last_error 留痕统计 ── */
test('k47-E：app.js 全局错误处理器 —— 噪音仅打印不计入留痕，业务异常照常留痕', () => {
  const store = {};
  let onErrorCb = null;
  global.App = (cfg) => { global.__k47AppCfg = cfg; };
  global.wx = {
    getStorageSync: (k) => (store[k] !== undefined ? store[k] : null),
    setStorageSync: (k, v) => { store[k] = v; },
    removeStorageSync: (k) => { delete store[k]; },
    onError: (cb) => { onErrorCb = cb; },
    onUnhandledRejection: () => {},
    getAppBaseInfo: () => ({ theme: 'light' }),
    showToast: () => {},
  };
  global.getApp = () => global.__k47AppCfg;
  delete require.cache[require.resolve('../app')];
  require('../app');
  const cfg = global.__k47AppCfg;
  cfg.globalData = cfg.globalData || {};
  const cap = captureLogs();
  cfg._setupGlobalErrorCapture();
  assert.ok(onErrorCb, 'onError 应已注册');

  onErrorCb(new Error(TOOL_ERR));                 // 工具侧噪音
  assert.equal(store.ylm_last_error, undefined, '工具侧噪音不得进留痕统计');

  onErrorCb(new TypeError('业务崩溃 at pages/ming/ming.js:1:1'));   // 业务异常
  cap.restore();
  const rec = store.ylm_last_error;
  assert.ok(Array.isArray(rec) && rec.length === 1, '业务异常应留痕：' + JSON.stringify(rec));
  assert.ok(String(rec[0].msg).indexOf('业务崩溃') !== -1);
  assert.ok(rec[0].msg.indexOf('\n') === -1, '留痕为单行摘要');
  const errs = cap.calls.filter((c) => c[0] === 'error');
  assert.equal(errs.length, 2);
  errs.forEach((c) => assert.equal(c[1].length, 1));   // 全部单字符串，不再打裸对象
  assert.ok(errs[0][1][0].indexOf('工具侧噪音') !== -1);
});
