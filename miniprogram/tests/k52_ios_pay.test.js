// 易理明灯 — k52：全终端虚拟支付口径（r2 按新政策反向重做）
// 运行：cd miniprogram && node --test tests/k52_ios_pay.test.js
//
// 政策（2026-09-17 复核，多来源一致）：
//   ① 微信小程序 **iOS 端虚拟支付自 2025-11 起已支持**（走 Apple IAP 结算，苹果抽成）；
//   ② 自 **2026-04-01** 起虚拟支付**全终端强制接入**（iOS / Android / Windows / 鸿蒙），
//      逾期按违规处理（风险提醒 → 限制功能 → 暂停/终止）；
//   ③ 规范**明令禁止**把用户「引导至 App、公众号、H5、个人号、网站等外部渠道」完成支付。
//
// 因此本批（r2）的红线是 **三条**，本文件逐条锁住：
//   A. 前端**不得按平台屏蔽 iOS** —— iOS/Android/Windows/鸿蒙走同一套 wx.requestVirtualPayment；
//   B. 前端**不得把用户引导到任何外部渠道**完成支付（公众号/客服/H5/个人号/网站）；
//   C. 「真不可用」（客户端没有该 API）与「调起失败」→ 只给**中性**提示，可重试，
//      **不静默改走其它支付通道**、不做任何外部引导。
//
// ⚠ 历史教训（本文件即其回归锁）：k52 初版曾按「iOS 端不支持虚拟支付」的**过期政策**
//   在 iOS 端屏蔽支付并引导到公众号 —— 那同时踩中①（iOS 早已可付）与③（禁外部引导）。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const api = require('../utils/api');

/* ── wx 桩：记录一切支付相关调用 ── */
function makeWx(opts = {}) {
  const rec = { virtual: [], toasts: [], modals: [], orders: [], confirmed: 0, polls: [] };
  global.wx = {
    canIUse: () => (opts.canUseVirtual !== undefined ? opts.canUseVirtual : true),
    // 平台信息**故意**暴露给桩：支付逻辑若按平台分支，下面的一致性断言会立刻红
    getDeviceInfo: () => ({ platform: opts.platform || 'android' }),
    getAccountInfoSync: () => ({ miniProgram: { envVersion: opts.envVersion || 'release' } }),
    getStorageSync: () => undefined,
    setStorageSync: () => {},
    removeStorageSync: () => {},
    request: () => {},
    showLoading: () => {},
    hideLoading: () => {},
    showToast: (o) => rec.toasts.push(o.title),
    showModal: (o) => rec.modals.push(o),
    requestVirtualPayment: (o) => {
      rec.virtual.push(o);
      if (opts.virtualFail) o.fail(opts.virtualFail);
      else o.success({ errMsg: 'requestVirtualPayment:ok' });
    },
  };
  return rec;
}

/** 全新 payment 模块（逐用例重载，避免模块级状态串味） */
function loadPayment(opts) {
  const rec = makeWx(opts);
  delete require.cache[require.resolve('../utils/payment')];
  return { payment: require('../utils/payment'), rec };
}

/* ── api 桩 ── */
const realApi = {
  createVirtualOrder: api.createVirtualOrder,
  createOrder: api.createOrder,
  confirmPayment: api.confirmPayment,
  checkVirtualStatus: api.checkVirtualStatus,
};
const apiRec = { virtualOrders: [], orders: [], confirmed: 0 };
function stubApi(opts = {}) {
  apiRec.virtualOrders = [];
  apiRec.orders = [];
  apiRec.confirmed = 0;
  api.createVirtualOrder = (productId) => {
    apiRec.virtualOrders.push(productId);
    if (opts.virtualOrder) return Promise.resolve(opts.virtualOrder);
    return Promise.reject({ detail: { code: 'virtual_pay_not_enabled' } });
  };
  api.createOrder = (productId) => {
    apiRec.orders.push(productId);
    return opts.mockOrder ? Promise.resolve(opts.mockOrder) : Promise.reject({ errMsg: 'request:fail' });
  };
  api.confirmPayment = () => { apiRec.confirmed += 1; return Promise.resolve({}); };
  api.checkVirtualStatus = () => Promise.resolve({ status: 'paid' });
}
test.after(() => { Object.assign(api, realApi); });

const PAY_ORDER = {
  signData: 'SIGN', paySig: 'PAYSIG', signature: 'SIG', mode: 'short_series_goods',
  outTradeNo: 'OT-1', env: 0,
};
const paymentSrc = fs.readFileSync(require.resolve('../utils/payment'), 'utf8');
/** 去掉注释后的代码（注释里说明政策不算违规） */
const paymentCode = paymentSrc
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
/** 提取 JS 字符串字面量（用于「对外话术」红线） */
function paymentStrings(src) {
  const out = [];
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
  const re = /'([^'\\]*(?:\\.[^'\\]*)*)'|"([^"\\]*(?:\\.[^"\\]*)*)"|`([^`\\]*(?:\\.[^`\\]*)*)`/g;
  let m;
  while ((m = re.exec(code))) out.push(m[1] || m[2] || m[3] || '');
  return out;
}

/* ══════════════ A. 不按平台屏蔽 iOS ══════════════ */

test('k52-A：源码里不再有任何平台屏蔽（撤掉 iOS 拦截）', () => {
  assert.ok(paymentCode.indexOf('isPurchaseBlocked') === -1, '不得残留 iOS 屏蔽闸门');
  assert.ok(paymentCode.indexOf('getDeviceInfo') === -1, '支付流程不得再按平台分支');
  assert.ok(paymentCode.indexOf('getSystemInfoSync') === -1, '不得引入废弃 API');
  assert.ok(paymentCode.indexOf("platform") === -1, '代码里不应再有 platform 判定');
});

test('k52-A：虚拟支付主链路不按平台分支（iOS/Android/Windows/鸿蒙同一套）', async () => {
  // 逐个平台跑同一段流程：即便客户端能报出 platform，支付行为也必须完全一致
  const seen = [];
  for (const platform of ['ios', 'android', 'ohos', 'windows', 'mac', 'devtools']) {
    const { payment, rec } = loadPayment({ platform });
    stubApi({ virtualOrder: PAY_ORDER });
    const r = await payment.purchase('ming_report');
    assert.equal(r.success, true, platform + ' 端应正常完成虚拟支付');
    assert.equal(rec.virtual.length, 1, platform + ' 端应调起 1 次虚拟支付');
    assert.deepEqual(apiRec.virtualOrders, ['ming_report']);
    seen.push(JSON.stringify(rec.virtual[0]));
  }
  assert.equal(new Set(seen).size, 1, '各平台调起参数完全一致：' + JSON.stringify(seen));
});

test('k52-A：iOS 端会员四档与安卓一样正常开通', async () => {
  for (const plan of ['monthly', 'quarterly', 'yearly', 'pro_monthly']) {
    const { payment, rec } = loadPayment({});
    stubApi({ virtualOrder: Object.assign({}, PAY_ORDER, { outTradeNo: 'OT-' + plan }) });
    const r = await payment.subscribeMember(plan);
    assert.equal(r.success, true, plan + ' 应正常开通（iOS 不再被拦）');
    assert.deepEqual(apiRec.virtualOrders, [plan]);
    assert.equal(rec.virtual.length, 1);
    assert.ok(rec.toasts.includes('购买成功！'));
    assert.equal(rec.modals.length, 0, '不得再有「平台受限」弹层');
  }
});

test('k52-A：定价与商品定义未被本批改动', () => {
  const { payment } = loadPayment({});
  assert.equal(payment.PRODUCTS.monthly.price, 19.9);
  assert.equal(payment.PRODUCTS.quarterly.price, 49.9);
  assert.equal(payment.PRODUCTS.yearly.price, 168);
  assert.equal(payment.PRODUCTS.pro_monthly.price, 39.9);
  assert.equal(payment.PRODUCTS.ming_report.price, 19.9);
  assert.equal(payment.PRODUCTS.ming_report_pro.price, 29.9);
  assert.equal(payment.PRODUCTS.love_compatibility.price, 9.9);
});

/* ══════════════ B. 禁止引导至外部渠道完成支付 ══════════════ */

test('k52-B：支付模块对外文案不含任何外部渠道引导（公众号/客服/H5/个人号/网站）', () => {
  const banned = ['公众号', '关注', '客服', 'H5', 'h5', '个人号', '网址', '外部', '联系我们', '联系客服'];
  const bad = paymentStrings(paymentSrc).filter((s) => banned.some((b) => s.indexOf(b) !== -1));
  assert.deepEqual(bad, [], '支付路径不得出现外部引导话术：' + JSON.stringify(bad));
});

test('k52-B：付费受阻时只给中性提示，绝不弹「去别处付」的引导', async () => {
  const { payment, rec } = loadPayment({ canUseVirtual: false });
  stubApi();
  const r = await payment.purchase('deep_report');
  assert.equal(r.success, false);
  assert.equal(rec.modals.length, 0, '不得弹任何引导弹层（含公众号/客服引导）');
  assert.equal(rec.toasts.length, 1, '只给一条中性提示');
  assert.equal(rec.toasts[0], '当前微信版本暂不支持，请升级微信后重试');
  ['公众号', '客服', '关注', '联系', '网站', 'H5'].forEach((w) => {
    assert.ok(rec.toasts[0].indexOf(w) === -1, `中性提示不得含「${w}」`);
  });
});

test('k52-B：全仓小程序侧不得存在「引导外部支付」话术残留', () => {
  // 扫描用户可见面（pages/utils 的 wxml/js）中与「付款引导」直接相关的外部渠道组合。
  // 注释不算用户可见（且政策注释本身会引用被禁词），故先剥注释。
  const MINI = __dirname + '/..';
  const bad = [];
  const strip = (text, isWxml) => {
    let t = text.replace(/\/\*[\s\S]*?\*\//g, '');
    t = t.split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
    if (isWxml) t = t.replace(/<!--[\s\S]*?-->/g, '');
    return t;
  };
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = dir + '/' + name.name;
      if (name.isDirectory()) { walk(p); continue; }
      if (!/\.(wxml|js)$/.test(name.name)) continue;
      const text = strip(fs.readFileSync(p, 'utf8'), /\.wxml$/.test(name.name));
      // 只揪「公众号/客服/个人号」与支付语境的组合（分享按钮、关于页联系方式不算付费引导）
      for (const line of text.split('\n')) {
        if (/公众号|关注公众号|联系客服|个人号/.test(line) &&
            /支付|付款|购买|开通|解锁|充值/.test(line)) {
          bad.push(p.replace(MINI + '/', 'miniprogram/') + ': ' + line.trim().slice(0, 90));
        }
      }
    }
  };
  walk(MINI + '/pages');
  walk(MINI + '/utils');
  assert.deepEqual(bad, [], '存在「付费受阻 → 引导外部渠道」话术：\n' + bad.join('\n'));
});

/* ══════════════ C. 真不可用 → 中性提示、可重试、不走别的通道 ══════════════ */

test('k52-C：客户端不支持该 API → 中性提示 + 不建单/不调起/不降级其它通道', async () => {
  const { payment, rec } = loadPayment({ canUseVirtual: false });
  stubApi({ mockOrder: { orderId: 'MOCK', payment: {} } });
  const r = await payment.purchase('deep_report');
  assert.equal(r.success, false);
  assert.equal(r.unsupported, true, '标记「真不可用」，调用方据此走中性提示');
  assert.equal(apiRec.virtualOrders.length, 0, '不建虚拟支付单');
  assert.equal(rec.virtual.length, 0);
  assert.equal(apiRec.orders.length, 0, '不静默改走 mock/其它支付通道');
  assert.equal(apiRec.confirmed, 0);
  assert.equal(rec.toasts[0], '当前微信版本暂不支持，请升级微信后重试');
});

test('k52-C：unsupported 也可从 tryVirtualPay 直接拿到（兜底一致）', async () => {
  const { payment, rec } = loadPayment({ canUseVirtual: false });
  stubApi();
  const r = await payment.tryVirtualPay('deep_report');
  assert.equal(r.unsupported, true);
  assert.equal(rec.virtual.length, 0);
});

test('k52-C：不支持 → 升级后（能力恢复）可直接重试成功，无粘滞状态', async () => {
  const { payment, rec } = loadPayment({ canUseVirtual: false });
  stubApi({ virtualOrder: PAY_ORDER });
  const first = await payment.purchase('deep_report');
  assert.equal(first.unsupported, true);
  global.wx.canIUse = () => true;                    // 用户升级微信后重进页面
  const second = await payment.purchase('deep_report');
  assert.equal(second.success, true, '能力恢复后必须能直接付费（不得被永久拦住）');
  assert.equal(rec.virtual.length, 1);
});

test('k52-C：调起失败 → 中性失败提示（不引导外部渠道），再点可成功', async () => {
  const { payment, rec } = loadPayment({ virtualFail: { errMsg: 'requestVirtualPayment:fail 系统错误' } });
  stubApi({ virtualOrder: PAY_ORDER });
  const r1 = await payment.purchase('deep_report');
  assert.equal(r1.success, false);
  assert.ok(rec.toasts.includes('支付未完成，请稍后再试'), '中性提示：' + JSON.stringify(rec.toasts));
  assert.equal(rec.modals.length, 0, '不弹外部引导');
  // 第二次：调起成功（用户重试）→ 照常完成
  global.wx.requestVirtualPayment = (o) => { rec.virtual.push(o); o.success({}); };
  const r2 = await payment.purchase('deep_report');
  assert.equal(r2.success, true, '失败可重试，链路不粘滞');
});

/* ══════════════ 安卓既有链路逐字不变（回归锁） ══════════════ */

test('k52：安卓端虚拟支付链路逐字不变（建单 → 三要素调起 → 轮询 → 购买成功）', async () => {
  const { payment, rec } = loadPayment({});
  stubApi({ virtualOrder: PAY_ORDER });
  const r = await payment.purchase('ming_report');
  assert.equal(r.success, true);
  assert.equal(r.orderId, 'OT-1');
  assert.deepEqual(apiRec.virtualOrders, ['ming_report']);
  const p = rec.virtual[0];
  assert.equal(p.signData, 'SIGN');
  assert.equal(p.paySig, 'PAYSIG');
  assert.equal(p.signature, 'SIG');
  assert.equal(p.mode, 'short_series_goods');
  assert.equal(p.env, 0);
  assert.ok(rec.toasts.includes('购买成功！'));
});

test('k52：后端未启用虚拟支付 → 降级 mock 链路照旧（开发/演示配置，非外部渠道）', async () => {
  const { payment, rec } = loadPayment({ envVersion: 'develop' });
  stubApi({ mockOrder: { orderId: 'MOCK-1', payment: { timeStamp: 't' } } });
  const r = await payment.purchase('deep_report');
  assert.equal(r.success, true);
  assert.deepEqual(apiRec.orders, ['deep_report']);
  assert.equal(apiRec.confirmed, 1);
  assert.ok(rec.toasts.includes('购买成功！'));
});

test('k52：免费产品不受影响；读取路径（商品/权益）不受影响', async () => {
  const { payment } = loadPayment({});
  stubApi();
  assert.ok(payment.getProduct('yearly'));
  assert.ok(payment.getMemberBenefits().length >= 4);
  const free = await payment.purchase('detailed_fortune');
  assert.equal(free.success, true);
  assert.equal(apiRec.virtualOrders.length, 0);
});
