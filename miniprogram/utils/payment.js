// 易理明灯 — 支付管理模块
// 支付策略（与后端 .env 联动）：
//   后端配置齐（WECHAT_PAY_ENABLED=true + MIDAS_OFFER_ID/MIDAS_APP_KEY）→ 微信虚拟支付（米大师）
//   未配置 → 保留原 mock 支付（后端订单直接置 paid，前端演示成功）
const api = require('./api');
const DEV_MODE = true; // 开发模式(演示支付), 上线改为 false

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

  // 会员
  first_month: {
    id: 'first_month',
    name: '首月会员',
    price: 38,
    priceLabel: '¥38/首月',
    priceOrigLabel: '¥68/月',
    description: '首月优惠，全功能解锁',
    icon: '👑',
  },
  monthly: {
    id: 'monthly',
    name: '月度会员',
    price: 68,
    priceLabel: '¥68/月',
    description: '连续订阅，自动续费',
    icon: '👑',
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
    icon: '❤️',
    title: '感情合盘无限次',
    desc: '不限次数分析你和TA的缘分',
  },
  {
    icon: '📜',
    title: '深度报告免费',
    desc: '免费获取所有深度命理报告',
  },
  {
    icon: '🌟',
    title: '专属标识 · 去广告',
    desc: '会员专属徽章，清爽使用体验',
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
    console.warn('[Payment] 虚拟支付建单失败:', e);
    return { success: false, fallback: true };
  }

  const { signData, paySig, signature, mode, outTradeNo } = orderRes || {};
  if (!signData || !paySig || !signature || !outTradeNo) {
    console.warn('[Payment] 后端未返回完整三要素，降级 mock:', orderRes);
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

  // 免费产品直接"购买"成功
  if (product.price === 0) {
    return { success: true, orderId: 'free_' + Date.now() };
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
    } else if (DEV_MODE) {
      console.log('[Payment] Using demo payment mode');
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
 * @param {string} planId - 'monthly' | 'first_month'
 * @returns {Promise<{success: boolean}>}
 */
async function subscribeMember(planId) {
  const plan = PRODUCTS[planId];
  if (!plan) {
    wx.showToast({ title: '套餐不存在', icon: 'none' });
    return { success: false };
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
    } else if (DEV_MODE) {
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
  getProduct,
  getAllProducts,
  getMemberBenefits,
  purchase,
  subscribeMember,
  canUseVirtualPayment,
};
