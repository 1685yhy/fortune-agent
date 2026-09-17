// 易理明灯 — k52-1 合规：iOS 端虚拟支付屏蔽
// 运行：cd miniprogram && node --test tests/k52_ios_pay.test.js
//
// 背景（微信注册/提审前实扫）：
//   微信虚拟支付（米大师 wx.requestVirtualPayment）**在 iOS 端不可用**（苹果要求虚拟商品
//   走内购，微信亦禁止 iOS 端小程序内虚拟支付）。iOS 端若出现可点击的付费入口 → 审核硬伤，
//   上线被扫到会封支付能力。本批：iOS 端所有付费入口不调起支付、改只读引导；
//   **安卓端行为逐字不变**；已购/会员状态读取不受影响。
//
// 本文件覆盖：iOS 四条路径（purchase / subscribeMember 四档 / tryVirtualPay 兜底闸门 /
//   平台不可判定）+ 安卓照旧两条（虚拟支付成功链路；不支持虚拟支付 → mock 降级链路）
//   + 引导文案红线（不出现「支付/购买」）+ 读取与免费路径不受影响 + 开发者工具不误伤。
const test = require('node:test');
const assert = require('node:assert/strict');

const api = require('../utils/api');

/* ── wx 桩：平台 + 调用记录 ── */
function makeWx(platform, opts = {}) {
  const rec = { virtual: [], toasts: [], modals: [], orders: [], confirmed: 0, polls: 0 };
  global.wx = {
    // k52-1 平台判定源（新 API；getSystemInfoSync 已废弃，本批不引入）
    getDeviceInfo: opts.noDeviceInfo ? undefined : () => ({ platform }),
    canIUse: () => (opts.canUseVirtual !== undefined ? opts.canUseVirtual : true),
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

/* ── 全新 payment 模块（平台结果有模块级缓存 → 逐用例重载） ── */
function loadPayment(platform, opts) {
  const rec = makeWx(platform, opts);
  delete require.cache[require.resolve('../utils/payment')];
  return { payment: require('../utils/payment'), rec };
}

/* ── api 桩：虚拟支付建单/轮询/mock 建单（记录调用，默认「后端未启用」） ── */
const realApi = {
  createVirtualOrder: api.createVirtualOrder,
  createOrder: api.createOrder,
  confirmPayment: api.confirmPayment,
  checkVirtualStatus: api.checkVirtualStatus,
};
const apiRec = { virtualOrders: [], orders: [], polls: [] };
function stubApi(opts = {}) {
  apiRec.virtualOrders = [];
  apiRec.orders = [];
  apiRec.polls = [];
  api.createVirtualOrder = (productId) => {
    apiRec.virtualOrders.push(productId);
    if (opts.virtualOrder) return Promise.resolve(opts.virtualOrder);
    return Promise.reject({ detail: { code: 'virtual_pay_not_enabled' } });
  };
  api.createOrder = (productId) => {
    apiRec.orders.push(productId);
    return opts.mockOrder ? Promise.resolve(opts.mockOrder) : Promise.reject({ errMsg: 'request:fail' });
  };
  api.confirmPayment = () => { apiRec.confirmed = (apiRec.confirmed || 0) + 1; return Promise.resolve({}); };
  api.checkVirtualStatus = (outTradeNo) => { apiRec.polls.push(outTradeNo); return Promise.resolve({ status: 'paid' }); };
}
test.after(() => { Object.assign(api, realApi); });

const PAY_ORDER = {
  signData: 'SIGN', paySig: 'PAYSIG', signature: 'SIG', mode: 'short_series_goods',
  outTradeNo: 'OT-1', env: 0,
};

/* ══════════════ iOS：不调起支付，改引导 ══════════════ */

test('k52-1：iOS 端 purchase 不调起支付（零建单/零调起），改只读引导', async () => {
  const { payment, rec } = loadPayment('ios');
  stubApi();
  const r = await payment.purchase('ming_report');
  assert.equal(r.success, false);
  assert.equal(r.blocked, true, '返回 blocked，调用方据此走引导而非「支付失败」');
  assert.equal(apiRec.virtualOrders.length, 0, 'iOS 端零建单（不触后端订单）');
  assert.equal(rec.virtual.length, 0, 'iOS 端零 wx.requestVirtualPayment');
  assert.equal(apiRec.orders.length, 0, 'iOS 端零降级 mock 建单');
  assert.equal(apiRec.confirmed || 0, 0, 'iOS 端零支付确认');
  assert.equal(rec.modals.length, 1, '显示引导弹层');
});

test('k52-1：iOS 端会员四档（monthly/quarterly/yearly/pro_monthly）逐档不调起支付', async () => {
  const { payment, rec } = loadPayment('ios');
  stubApi();
  for (const plan of ['monthly', 'quarterly', 'yearly', 'pro_monthly']) {
    const r = await payment.subscribeMember(plan);
    assert.equal(r.blocked, true, plan + ' 应被平台屏蔽');
  }
  assert.equal(rec.virtual.length, 0, '四档合计零调起');
  assert.equal(apiRec.virtualOrders.length, 0, '四档合计零建单');
  assert.equal(rec.modals.length, 4, '每档都给一次引导（用户可感知原因）');
  // 定价与商品定义不得因本批改动
  assert.equal(payment.PRODUCTS.monthly.price, 19.9);
  assert.equal(payment.PRODUCTS.quarterly.price, 49.9);
  assert.equal(payment.PRODUCTS.yearly.price, 168);
  assert.equal(payment.PRODUCTS.pro_monthly.price, 39.9);
  assert.equal(payment.PRODUCTS.ming_report.price, 19.9);
  assert.equal(payment.PRODUCTS.ming_report_pro.price, 29.9);
  assert.equal(payment.PRODUCTS.love_compatibility.price, 9.9);
});

test('k52-1：iOS 端 tryVirtualPay 兜底闸门（任何直接调用者也不得调起）', async () => {
  const { payment, rec } = loadPayment('ios');
  stubApi({ virtualOrder: PAY_ORDER });
  const r = await payment.tryVirtualPay('deep_report');
  assert.equal(r.blocked, true);
  assert.equal(rec.virtual.length, 0, '兜底闸门之下 requestVirtualPayment 仍为 0 次');
  assert.equal(apiRec.virtualOrders.length, 0);
});

test('k52-1：平台不可判定（无 getDeviceInfo / 取信息异常）→ 保守屏蔽', async () => {
  const { payment, rec } = loadPayment('', { noDeviceInfo: true });
  stubApi({ virtualOrder: PAY_ORDER });
  const r = await payment.purchase('deep_report');
  assert.equal(payment.getPlatform(), '', '桩确认平台不可判定');
  assert.equal(r.blocked, true, '无法证明非 iOS → 不予放行（宁可拦错，不冒封支付能力之险）');
  assert.equal(rec.virtual.length, 0);
});

test('k52-1：引导文案红线——不出现「支付/购买」字样，且给出去向', async () => {
  const { payment, rec } = loadPayment('ios');
  stubApi();
  await payment.purchase('deep_report');
  const m = rec.modals[0];
  assert.ok(m && m.content, '引导内容非空');
  const text = m.title + m.content;
  ['支付', '购买', '付费'].forEach((w) => {
    assert.ok(text.indexOf(w) === -1, `引导文案不得出现「${w}」：` + text);
  });
  assert.ok(text.indexOf('公众号') !== -1, '给出去向（公众号）');
  assert.ok(text.indexOf('设置') !== -1, '给出备选去向（设置页联系方式）');
  assert.equal(m.showCancel, false, '单一确认按钮，不诱导二选');
  assert.ok(payment.IOS_GUIDE.lines.length >= 2, '分条引导（页内只读视图与弹层共用同一事实源）');
});

/* ══════════════ 已购/免费/读取路径不受影响 ══════════════ */

test('k52-1：iOS 端读取路径不受影响（商品/权益可取，免费产品照常领取）', async () => {
  const { payment } = loadPayment('ios');
  stubApi();
  assert.ok(payment.getProduct('monthly'), '商品定义可读（iOS 用户仍能看到已购内容与商品信息）');
  assert.equal(payment.getAllProducts().yearly.priceLabel, '¥168/年');
  assert.ok(payment.getMemberBenefits().length >= 4, '权益列表可读');
  const free = await payment.purchase('detailed_fortune');   // price 0：免费领取不涉及虚拟支付
  assert.equal(free.success, true, '免费产品不受平台屏蔽影响');
  assert.equal(apiRec.virtualOrders.length, 0);
});

/* ══════════════ 安卓：行为逐字不变 ══════════════ */

test('k52-1：安卓端虚拟支付链路逐字不变（建单 → 调起三要素 → 轮询 → 购买成功）', async () => {
  const { payment, rec } = loadPayment('android');
  stubApi({ virtualOrder: PAY_ORDER });
  const r = await payment.purchase('ming_report');
  assert.equal(r.success, true, '安卓端照常成功');
  assert.equal(r.orderId, 'OT-1');
  assert.deepEqual(apiRec.virtualOrders, ['ming_report'], '建单参数逐字不变');
  assert.equal(rec.virtual.length, 1, '调起 1 次');
  const p = rec.virtual[0];
  assert.equal(p.signData, 'SIGN');
  assert.equal(p.paySig, 'PAYSIG');
  assert.equal(p.signature, 'SIG');
  assert.equal(p.mode, 'short_series_goods');
  assert.equal(p.env, 0, 'env 透传（后端未给 → 0）');
  assert.deepEqual(apiRec.polls, ['OT-1'], '轮询后端订单状态照旧');
  assert.ok(rec.toasts.includes('购买成功！'), '成功提示照旧：' + JSON.stringify(rec.toasts));
  assert.equal(rec.modals.length, 0, '安卓端不出现平台受限引导');
});

test('k52-1：安卓端会员开通（虚拟支付）照旧成功', async () => {
  const { payment, rec } = loadPayment('android');
  stubApi({ virtualOrder: Object.assign({}, PAY_ORDER, { outTradeNo: 'OT-M' }) });
  const r = await payment.subscribeMember('yearly');
  assert.equal(r.success, true);
  assert.deepEqual(apiRec.virtualOrders, ['yearly']);
  assert.equal(rec.virtual.length, 1);
  assert.ok(rec.toasts.includes('购买成功！'));
});

test('k52-1：安卓端「不支持虚拟支付 → 降级 mock」链路照旧（含演示态判定）', async () => {
  const { payment, rec } = loadPayment('android', { canUseVirtual: false, envVersion: 'develop' });
  stubApi({ mockOrder: { orderId: 'MOCK-1', payment: { timeStamp: 't' } } });
  const r = await payment.purchase('deep_report');
  assert.equal(r.success, true);
  assert.deepEqual(apiRec.orders, ['deep_report'], '降级 mock 建单照旧');
  assert.equal(apiRec.confirmed, 1, 'mock 支付确认照旧');
  assert.ok(rec.toasts.includes('购买成功！'));
});

test('k52-1：开发者工具（devtools）不误伤——平台非 iOS 即照常', async () => {
  const { payment, rec } = loadPayment('devtools');
  stubApi({ virtualOrder: PAY_ORDER });
  assert.equal(payment.isPurchaseBlocked(), false);
  const r = await payment.purchase('ming_report');
  assert.equal(r.success, true);
  assert.equal(rec.virtual.length, 1);
});

/* ══════════════ 平台判定本身 ══════════════ */

test('k52-1：平台判定口径——只用未废弃 API，且只认 ios 为受限平台', async () => {
  const cases = [['ios', true], ['android', false], ['devtools', false],
    ['ohos', false], ['windows', false], ['mac', false], ['', true]];
  for (const [platform, blocked] of cases) {
    const { payment } = loadPayment(platform, { noDeviceInfo: platform === '' });
    assert.equal(payment.getPlatform(), platform, '平台读取：' + platform);
    assert.equal(payment.isPurchaseBlocked(), blocked, `isPurchaseBlocked(${platform})`);
    assert.equal(payment.isIOS(), platform === 'ios');
  }
  // 源码红线：**代码**不得引入已废弃 API（k47-B 已把 getSystemInfoSync 换掉）；
  // 注释里说明「不引入」不算违规 → 先剥注释再查。
  const src = require('fs').readFileSync(require.resolve('../utils/payment'), 'utf8');
  const code = src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
  assert.ok(code.indexOf('getSystemInfoSync') === -1, '不得引入废弃 API wx.getSystemInfoSync');
  assert.ok(code.indexOf('getSystemInfo(') === -1, '不得引入废弃 API wx.getSystemInfo');
  assert.ok(code.indexOf('getDeviceInfo') !== -1, '平台判定用新 API wx.getDeviceInfo');
});
