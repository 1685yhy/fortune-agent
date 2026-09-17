// 易理明灯 — 支付管理模块
// 支付策略（与后端 .env 联动）：
//   后端配置齐（WECHAT_PAY_ENABLED=true + MIDAS_OFFER_ID/MIDAS_APP_KEY）→ 微信虚拟支付（米大师）
//   未配置 → 保留原 mock 支付（后端订单直接置 paid，前端演示成功）
const { logWarn } = require('./log');
const api = require('./api');

// ---- 演示支付判定（G2 D1：任何环境不得假成功） ----
// 旧实现 DEV_MODE=true 写死：生产真支付下，支付失败（非取消）也会弹「购买成功！（演示）」
// 并返回 success:true → 调用方继续请求付费内容，但后端订单 pending——收入/体验活漏洞。
// 修复：仅 develop（开发者工具/预览）环境允许「支付失败→演示成功」；trial/release
// 恒 false——生产支付失败一律真实失败提示、可重试。分支点运行时调用 isDevDemo()
// （可单测），不使用加载期常量。
function isDevDemo() {
  try {
    if (typeof wx === 'undefined' || !wx || typeof wx.getAccountInfoSync !== 'function') return false;
    const env = wx.getAccountInfoSync().miniProgram && wx.getAccountInfoSync().miniProgram.envVersion;
    return env === 'develop';
  } catch (e) {
    return false; // 环境信息异常 → 按生产处理，宁可真失败不可假成功
  }
}

// ---- k52-1：iOS 端虚拟支付屏蔽（合规硬性要求） ----
// 微信虚拟支付（米大师 wx.requestVirtualPayment）在 iOS 端不可用：苹果要求虚拟商品走
// 内购，微信亦禁止 iOS 端小程序内虚拟支付。iOS 端若露出可点击的付费入口 → 审核硬伤，
// 上线被扫到会封支付能力。故本模块**在 iOS 端不调起任何支付**，统一走引导路径。
// ⚠ 平台判定只用未废弃 API（k47-B 同口径，getDeviceInfo 属 getAppBaseInfo/
//   getWindowInfo 同一套新 API 家族）——不引入 wx.getSystemInfoSync。
// ⚠ 只拦「发起购买」的动作；已购/会员状态**读取**不受影响（iOS 用户仍能看已购内容）。
let _platformCache = null;

/**
 * 当前平台：'ios' | 'android' | 'devtools' | 'windows' | 'mac' | 'ohos' | ''（无法判定）
 * @returns {string}
 */
function getPlatform() {
  if (_platformCache !== null) return _platformCache;
  let platform = '';
  try {
    // 新 API（基础库 ≥2.20.1）：wx.getDeviceInfo().platform
    const info = (typeof wx !== 'undefined' && wx && wx.getDeviceInfo && wx.getDeviceInfo()) || {};
    platform = String(info.platform || '').toLowerCase();
  } catch (e) {
    platform = ''; // 设备信息异常 → 交由 isPurchaseBlocked 按保守策略处理
  }
  // 只缓存**已判定**的结果：'' （无法判定）不缓存，避免异常态被永久固化
  if (platform) _platformCache = platform;
  return platform;
}

/** 是否 iOS 端（Apple 平台统一上报 'ios'）——判定不可用时返回 false（由可购买性另判） */
function isIOS() {
  return getPlatform() === 'ios';
}

/**
 * 是否屏蔽「发起购买」：
 *   ① iOS → 恒屏蔽（合规红线）
 *   ② 平台无法判定（基础库过旧/设备信息异常）→ 保守屏蔽：宁可拦错，不可在 iOS 露出支付
 * @returns {boolean}
 */
function isPurchaseBlocked() {
  const p = getPlatform();
  return p === 'ios' || p === '';
}

/* iOS 端引导文案（不出现「支付/购买」字样；与墨韵调性一致）。
   渠道：关注公众号「易理明灯」/ 设置页「关于与隐私」中的联系方式（现有页面已列明）。 */
const IOS_GUIDE = {
  title: '此项暂未开放',
  lines: [
    '遵循平台规则，当前设备暂不支持在应用内开通',
    '如需获取完整内容，可关注公众号「易理明灯」',
    '或在「我的 — 设置 — 关于与隐私」查看联系方式',
  ],
  confirmText: '知道了',
};
IOS_GUIDE.content = IOS_GUIDE.lines.join('；');

/** 弹出 iOS 引导（付费入口在 iOS 端统一走此路径，不调起支付） */
function showIosGuide() {
  try {
    if (typeof wx === 'undefined' || !wx || typeof wx.showModal !== 'function') return;
    wx.showModal({
      title: IOS_GUIDE.title,
      content: IOS_GUIDE.content,
      showCancel: false,
      confirmText: IOS_GUIDE.confirmText,
    });
  } catch (e) { /* 引导显示失败不影响「不调起支付」这一硬约束 */ }
}

/** 屏蔽结果：调用方据 blocked 判定「已引导，不再提示支付失败」 */
function blockedResult() {
  showIosGuide();
  return { success: false, blocked: true, reason: 'platform_restricted' };
}

// ---- 产品定价 ----
const PRODUCTS = {
  // 单次购买
  love_compatibility: {
    id: 'love_compatibility',
    name: '感情合盘分析',
    price: 9.9,
    priceLabel: '¥9.9',
    description: '解锁完整合盘分析报告',
    icon: '❤️',
  },
  deep_report: {
    id: 'deep_report',
    name: '深度解读报告',
    price: 19.9,
    priceLabel: '¥19.9',
    description: '获取详尽命理分析',
    icon: '📜',
  },
  ming_report: {
    id: 'ming_report',
    name: 'AI取名名笺深度报告（宝宝版）',
    price: 19.9,
    priceLabel: '¥19.9',
    description: '八字契合度矩阵 · 备选 15 名 · 墨韵名笺',
    icon: '名',
  },
  ming_report_pro: {
    id: 'ming_report_pro',
    name: '成人改名深度报告',
    price: 29.9,
    priceLabel: '¥29.9',
    description: '现名诊断 · 改名对比 · 备选 15 名 · 墨韵名笺',
    icon: '名',
  },
  full_analysis: {
    id: 'full_analysis',
    name: '全盘分析',
    price: 29.9,
    priceLabel: '¥29.9',
    description: '完整八字命盘解读',
    icon: '🔮',
  },
  detailed_fortune: {
    id: 'detailed_fortune',
    name: '详细每日运势',
    price: 0,
    priceLabel: '免费',
    description: '每日运势详情',
    icon: '✨',
  },

  // 会员（L5-2 档位与后端 SUBSCRIBE_PLANS 对齐：基础三档 + 高级一档）
  monthly: {
    id: 'monthly',
    name: '基础会员·月',
    price: 19.9,
    priceLabel: '¥19.9/月',
    description: '完整分析 · 每日运势 · 畅聊不设限',
    icon: '阅',
  },
  quarterly: {
    id: 'quarterly',
    name: '基础会员·季',
    price: 49.9,
    priceLabel: '¥49.9/季',
    description: '基础会员全部权益 · 一次开通 90 天',
    icon: '阅',
  },
  yearly: {
    id: 'yearly',
    name: '基础会员·年',
    price: 168,
    priceLabel: '¥168/年',
    description: '超值年卡 · 折合 ¥14/月',
    icon: '阅',
  },
  pro_monthly: {
    id: 'pro_monthly',
    name: '高级会员·月',
    price: 39.9,
    priceLabel: '¥39.9/月',
    description: '含论财/论事业/论健康等专项解读',
    icon: '玺',
  },
};

// ---- 会员权益 ----
const MEMBER_BENEFITS = [
  {
    icon: '🔮',
    title: '每日详细运势',
    desc: '解锁完整运势解读，含十二生肖逐项分析',
  },
  {
    icon: '💬',
    title: '畅聊不设限',
    desc: '对话额度不设上限，无需降级精简',
  },
  {
    icon: '📜',
    title: '深度报告免费',
    desc: '免费获取所有深度命理报告',
  },
  {
    icon: '🌟',
    title: '高级会员专项',
    desc: '高级会员含论财/论事业/论健康等专项论断',
  },
];

/**
 * 获取产品信息
 * @param {string} productId
 * @returns {Object|null}
 */
function getProduct(productId) {
  return PRODUCTS[productId] || null;
}

/**
 * 获取所有产品
 * @returns {Object}
 */
function getAllProducts() {
  return PRODUCTS;
}

/**
 * 获取会员权益列表
 * @returns {Array}
 */
function getMemberBenefits() {
  return MEMBER_BENEFITS;
}

// ---- 虚拟支付（米大师）：wx.requestVirtualPayment（基础库 >= 2.19.2） ----

/** 基础库是否支持虚拟支付 */
function canUseVirtualPayment() {
  return !!(wx.canIUse && wx.canIUse('requestVirtualPayment'));
}

/** 调起米大师支付（Promise 封装） */
function requestVirtualPayment(params) {
  return new Promise((resolve, reject) => {
    wx.requestVirtualPayment({
      signData: params.signData,
      paySig: params.paySig,
      signature: params.signature,
      mode: params.mode,
      env: params.env !== undefined ? params.env : 0,
      success: resolve,
      fail: reject,
    });
  });
}

/**
 * 支付成功后轮询后端订单状态（等发货回调落地，最多 ~15s）
 * @param {string} outTradeNo
 * @returns {Promise<{status: string}>}
 */
function pollOrderStatus(outTradeNo, maxTries = 10, intervalMs = 1500) {
  return new Promise((resolve) => {
    let tries = 0;
    const timer = setInterval(() => {
      tries += 1;
      api.checkVirtualStatus(outTradeNo)
        .then((res) => {
          if (res && res.status === 'paid') {
            clearInterval(timer);
            resolve({ status: 'paid' });
          } else if (tries >= maxTries) {
            clearInterval(timer);
            resolve({ status: 'pending' });
          }
        })
        .catch(() => {
          if (tries >= maxTries) {
            clearInterval(timer);
            resolve({ status: 'pending' });
          }
        });
    }, intervalMs);
  });
}

/**
 * 后端错误 → 错误 code（后端 detail 为 {code, message} 时）
 */
function backendErrorCode(e) {
  const d = (e && e.detail) || e || {};
  return typeof d === 'object' ? (d.code || '') : '';
}

/**
 * 虚拟支付主流程：后端建单 → wx.requestVirtualPayment → 轮询订单 → 刷新会员
 * @param {string} productId
 * @returns {Promise<{success: boolean, orderId?: string, fallback?: boolean, needRelogin?: boolean}>}
 */
async function tryVirtualPay(productId) {
  // k52-1 兜底闸门：任何直接调用本函数者也不得在 iOS 端调起虚拟支付
  if (isPurchaseBlocked()) {
    console.log('[Payment] 平台受限（iOS/未判定），不调起虚拟支付');
    return { success: false, blocked: true, reason: 'platform_restricted' };
  }
  if (!canUseVirtualPayment()) {
    console.log('[Payment] 基础库不支持 requestVirtualPayment，降级 mock');
    return { success: false, fallback: true };
  }

  let orderRes;
  try {
    orderRes = await api.createVirtualOrder(productId);
  } catch (e) {
    if (backendErrorCode(e) === 'virtual_pay_not_enabled') {
      console.log('[Payment] 后端未启用虚拟支付，降级 mock');
      return { success: false, fallback: true };
    }
    if (backendErrorCode(e) === 'session_key_missing') {
      wx.showToast({ title: '请重新登录后重试', icon: 'none' });
      return { success: false, needRelogin: true };
    }
    logWarn('Payment 虚拟支付建单失败', { errCode: backendErrorCode(e), errMsg: ((e && (e.errMsg || e.message)) || '') });
    return { success: false, fallback: true };
  }

  const { signData, paySig, signature, mode, outTradeNo } = orderRes || {};
  if (!signData || !paySig || !signature || !outTradeNo) {
    // 隐私：订单响应体（含 outTradeNo 等要素）不进日志，只列缺失字段名
    logWarn('Payment 后端未返回完整三要素，降级 mock', { errMsg: 'missing: ' + [
      !signData && 'signData', !paySig && 'paySig', !signature && 'signature', !outTradeNo && 'outTradeNo',
    ].filter(Boolean).join(',') });
    return { success: false, fallback: true };
  }

  // 调起米大师支付
  try {
    await requestVirtualPayment({ signData, paySig, signature, mode, env: orderRes.env });
  } catch (e) {
    const errMsg = (e && e.errMsg) || '';
    if (errMsg.includes('cancel')) {
      wx.showToast({ title: '支付已取消', icon: 'none' });
      return { success: false };
    }
    if (errMsg.includes('-15007') || errMsg.includes('session')) {
      wx.showToast({ title: '登录态已过期，请重新登录后重试', icon: 'none' });
      return { success: false, needRelogin: true };
    }
    console.warn('[Payment] requestVirtualPayment 失败:', errMsg);
    wx.showToast({ title: '支付未完成，请稍后再试', icon: 'none' });
    return { success: false };
  }

  // 支付成功 → 轮询后端订单状态（发货回调）
  const status = await pollOrderStatus(outTradeNo);
  if (status.status === 'paid') {
    wx.showToast({ title: '购买成功！', icon: 'success' });
    return { success: true, orderId: outTradeNo };
  }
  wx.showToast({ title: '支付确认中，稍后可在订单中查看', icon: 'none' });
  return { success: false, orderId: outTradeNo };
}

// ---- 支付流程 ----

/**
 * 发起购买流程（虚拟支付优先，后端未启用时降级 mock）
 * @param {string} productId - 产品ID
 * @returns {Promise<{success: boolean, orderId?: string}>}
 */
async function purchase(productId) {
  const product = PRODUCTS[productId];
  if (!product) {
    wx.showToast({ title: '产品不存在', icon: 'none' });
    return { success: false };
  }

  // 免费产品直接"购买"成功（免费领取不涉及虚拟支付，不在 k52-1 屏蔽范围）
  if (product.price === 0) {
    return { success: true, orderId: 'free_' + Date.now() };
  }

  // k52-1：iOS 端不调起支付 → 引导路径（返回 blocked，调用方据此走引导，不再提示支付失败）
  if (isPurchaseBlocked()) {
    return blockedResult();
  }

  // Step 1: 虚拟支付（米大师）——后端 .env 配置齐才启用
  const virtual = await tryVirtualPay(productId);
  if (virtual.success) return virtual;
  if (!virtual.fallback) return virtual; // 取消/失败/需重登：不再降级 mock

  // Step 2: 降级 mock 支付（后端 WECHAT_PAY_ENABLED=false）
  try {
    const orderRes = await api.createOrder(productId);
    const paymentParams = orderRes.payment || orderRes;

    // Step 3: 调起微信支付
    await api.confirmPayment(paymentParams);

    // 支付成功
    wx.showToast({ title: '购买成功！', icon: 'success' });
    return { success: true, orderId: orderRes.orderId || 'order_' + Date.now() };
  } catch (e) {
    if (e.errMsg && e.errMsg.includes('cancel')) {
      wx.showToast({ title: '支付已取消', icon: 'none' });
    } else if (isDevDemo()) {
      // 仅 develop 环境：演示支付（后端 mock 模式下模拟成功；绝不进入生产）
      console.log('[Payment] Using demo payment mode (develop only)');
      wx.showToast({ title: '购买成功！（演示）', icon: 'success' });
      return { success: true, orderId: 'demo_' + Date.now() };
    } else {
      wx.showToast({ title: '支付失败，请稍后再试', icon: 'none' });
    }
    return { success: false };
  }
}

/**
 * 购买会员（虚拟支付优先，后端未启用时降级 mock）
 * @param {string} planId - 'monthly' | 'quarterly' | 'yearly' | 'pro_monthly'
 * @returns {Promise<{success: boolean}>}
 */
async function subscribeMember(planId) {
  const plan = PRODUCTS[planId];
  if (!plan) {
    wx.showToast({ title: '套餐不存在', icon: 'none' });
    return { success: false };
  }

  // k52-1：iOS 端不调起支付 → 引导路径（同 purchase）
  if (isPurchaseBlocked()) {
    return blockedResult();
  }

  // Step 1: 虚拟支付（米大师）——会员开通（product_id = 套餐 id）
  const virtual = await tryVirtualPay(planId);
  if (virtual.success) return virtual;
  if (!virtual.fallback) return virtual;

  // Step 2: 降级 mock（后端 WECHAT_PAY_ENABLED=false）
  try {
    const res = await api.subscribeMember(planId);
    const paymentParams = res.payment || res;
    await api.confirmPayment(paymentParams);

    wx.showToast({ title: '订阅成功！', icon: 'success' });
    return { success: true };
  } catch (e) {
    if (e.errMsg && e.errMsg.includes('cancel')) {
      wx.showToast({ title: '订阅已取消', icon: 'none' });
    } else if (isDevDemo()) {
      // 仅 develop 环境：演示支付（同上，绝不进入生产）
      wx.showToast({ title: '订阅成功！（演示）', icon: 'success' });
      return { success: true };
    } else {
      wx.showToast({ title: '订阅失败，请稍后再试', icon: 'none' });
    }
    return { success: false };
  }
}

module.exports = {
  PRODUCTS,
  MEMBER_BENEFITS,
  IOS_GUIDE,
  getProduct,
  getAllProducts,
  getMemberBenefits,
  purchase,
  subscribeMember,
  canUseVirtualPayment,
  isDevDemo,
  // k52-1：虚拟支付内部主流程也对齐导出——兜底闸门（iOS 不调起）需可被测试覆盖
  tryVirtualPay,
  // k52-1：平台判定与 iOS 屏蔽（页面用它决定「显示付费入口」还是「显示引导」）
  getPlatform,
  isIOS,
  isPurchaseBlocked,
  showIosGuide,
};
