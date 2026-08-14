// 易理明灯 v5.0 — API 客户端
// Backend: https://yilichat.com

// ---- 配置 ----
const CONFIG = {
  // 多环境 baseURL 由启动探测决定（见 BASE_CANDIDATES / probeBaseURL）；
  // 探测全部失败时静默回退到生产域名。
  baseURL: 'https://yilichat.com',  // 生产兜底
  timeout: 30000,     // 普通请求 30s（app.json networkTimeout.request 已同步为 60000，不再被 15s 截断）
  chatTimeout: 60000, // chat 冷启动最慢 59s；微信平台单请求超时上限 60000ms（无法更大）
};

// ---- 多环境 baseURL 探测 ----
// 启动时并发探测 GET /api/health（3s 超时，无鉴权），第一个成功者即 baseURL：
//   127.0.0.1        → 开发者工具模拟器（本机后端）
//   192.168.0.104    → 真机预览（同一 WiFi 直连开发机）
//   https://yilichat.com → 生产（兜底）
// 结果缓存 storage(ylm_baseurl)，每天重探一次；业务请求在探测完成前排队等待。
// 按运行环境分流候选地址：
// - develop（开发者工具/预览码）：本机 127 + 局域网 + 生产，探测选通的
// - trial（体验版）/release（正式版）：强制只走生产域名（合法域名校验下
//   127/局域网必然失败，且失败缓存会被带进生产）
const __env = (typeof __wxConfig !== 'undefined' && __wxConfig.envVersion) || 'develop';
const BASE_CANDIDATES = __env === 'develop'
  ? ['http://127.0.0.1:8767', 'http://192.168.0.104:8767', 'https://yilichat.com']
  : ['https://yilichat.com'];
const BASEURL_STORAGE_KEY = 'ylm_baseurl';
const PROBE_TIMEOUT = 3000;                 // 单候选探测超时（并发进行，总等待 ≈ 3s）
const PROBE_CACHE_TTL = 24 * 60 * 60 * 1000; // 缓存 1 天
const PROBE_RETRY_COOLDOWN = 30 * 1000;     // 网络失败触发后台重探的最小间隔

let baseURL = null;         // 探测结果（null = 未探测/探测中）
let probePromise = null;    // 探测 Promise：业务请求 await 它，保证探测期间不抢发
let lastProbeAt = 0;
let reprobeTimer = null;

/**
 * 启动探测（app.js onLaunch 调用；模块内首次请求也会惰性触发）。
 * 失败不算错：全部失败静默回退 CONFIG.baseURL（生产域名），不 reject。
 * @param {boolean} force - true 时忽略缓存强制重探（网络失败后的后台自愈用）
 * @returns {Promise<string>} 解析为选定的 baseURL
 */
function probeBaseURL(force = false) {
  if (probePromise && !force) return probePromise;

  // 缓存命中（当天已探过且未强制）→ 直接采用，零延迟
  if (!force) {
    try {
      const cached = wx.getStorageSync(BASEURL_STORAGE_KEY);
      if (cached && cached.url && Date.now() - (cached.t || 0) < PROBE_CACHE_TTL) {
        baseURL = cached.url;
        lastProbeAt = Date.now();
        probePromise = Promise.resolve(cached.url);
        return probePromise;
      }
    } catch (e) { /* ignore */ }
  }

  probePromise = new Promise((resolve) => {
    let settled = false;
    const done = (url) => {
      if (settled) return;
      settled = true;
      baseURL = url;
      lastProbeAt = Date.now();
      try {
        wx.setStorageSync(BASEURL_STORAGE_KEY, { url, t: Date.now() });
      } catch (e) { /* ignore */ }
      console.log('[API] baseURL =', url);
      resolve(url);
    };
    // 并发探测全部候选（health 无需鉴权；失败静默忽略）
    BASE_CANDIDATES.forEach((candidate) => {
      wx.request({
        url: candidate + '/api/health',
        method: 'GET',
        timeout: PROBE_TIMEOUT,
        success: (res) => {
          if (res.statusCode >= 200 && res.statusCode < 300) done(candidate);
        },
        fail: () => { /* 失败不算错，等其它候选或兜底 */ },
      });
    });
    // 全部失败/超时 → 生产兜底（并发下先成功者已抢先 resolve）
    setTimeout(() => done(CONFIG.baseURL), PROBE_TIMEOUT + 200);
  });
  return probePromise;
}

/** 业务请求等待探测完成（探测期间排队，不发请求） */
function ensureBaseURL() {
  return probePromise || probeBaseURL();
}

/** 探测完成后取当前 baseURL（探测中/未探测时返回生产兜底，供 chatStream 等直接拼接） */
function getBaseURL() {
  return baseURL || CONFIG.baseURL;
}

/**
 * 网络层失败 → 后台重探一次并刷新缓存（防"模拟器缓存了 127.0.0.1，当天切真机"类场景；
 * 30s 冷却避免后端宕机时高频刷）。不阻塞本次失败请求。
 */
function scheduleReprobe() {
  if (Date.now() - lastProbeAt < PROBE_RETRY_COOLDOWN) return;
  if (reprobeTimer) return;
  reprobeTimer = setTimeout(() => {
    reprobeTimer = null;
    probeBaseURL(true);
  }, 1500);
}

let authToken = null;

// ---- Token 管理 ----
function setToken(token) {
  authToken = token;
}

function getToken() {
  // 内存为空时每次从 storage 恢复（app.js 重启恢复、401 重登后均可靠）
  if (!authToken) {
    try {
      authToken = wx.getStorageSync('ylm_token') || null;
    } catch (e) {
      authToken = null;
    }
  }
  return authToken;
}

// ---- 核心请求方法 ----

/**
 * 统一请求入口：先等 baseURL 探测完成（探测期间排队），再真正发请求。
 * @param {string} url - 业务路径（如 /api/calendar/today），baseURL 自动拼接
 */
function request(url, options = {}) {
  return ensureBaseURL().then(() => doRequest(url, options));
}

/** 实际发请求（baseURL 已确定） */
function doRequest(url, options = {}) {
  const { method = 'GET', data = {}, headers = {}, showLoading = false } = options;
  const timeout = options.timeout || CONFIG.timeout; // 单请求可覆盖（如 chat 长超时）

  // 构建请求头（token 每次从内存/storage 恢复，自动带 Authorization: Bearer）
  const header = {
    'Content-Type': 'application/json',
    ...headers,
  };

  const token = getToken();
  if (token) {
    header['Authorization'] = `Bearer ${token}`;
  }

  if (showLoading) {
    wx.showLoading({ title: '加载中...', mask: true });
  }

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${getBaseURL()}${url}`,
      method,
      data,
      header,
      timeout,
      success: (res) => {
        if (showLoading) wx.hideLoading();

        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else if (res.statusCode === 401) {
          // Token 过期：静默自动重登一次（不 toast），成功后重试原请求 1 次
          if (options._noAuthRetry || options._authRetried) {
            reject(new Error('Unauthorized'));
            return;
          }
          authToken = null;
          try {
            wx.removeStorageSync('ylm_token');
          } catch (e) { /* ignore */ }
          relogin()
            .then(() => request(url, Object.assign({}, options, { _authRetried: true })))
            .then(resolve)
            .catch((err) => {
              console.warn('[API] 自动重登后请求仍失败:', err && err.message);
              reject(new Error('登录已过期，自动重登失败'));
            });
        } else {
          reject(res.data || { error: '请求失败' });
        }
      },
      fail: (err) => {
        if (showLoading) wx.hideLoading();
        console.error('[API] request error:', err);
        // 网络层失败 → 后台重探候选并刷新缓存（模拟器↔真机切换自愈），不阻塞本次失败
        scheduleReprobe();
        reject(new Error('网络连接失败，请检查网络设置'));
      },
    });
  });
}

// ---- 登录状态 ----

/**
 * 应用登录响应：token → 内存 + storage(ylm_token)；user.id（后端 JWT sub）→
 * globalData.userId + storage(ylm_user_id)。全项目唯一身份写入点。
 * @param {{token: string, user?: {id: string, has_bazi?: boolean, bazi?: Object}}} data - 登录响应
 */
function applyAuth(data) {
  if (!data || !data.token) return;
  setToken(data.token);
  try {
    wx.setStorageSync('ylm_token', data.token);
  } catch (e) { /* ignore */ }

  const app = getApp && getApp();
  if (app && app.globalData) {
    app.globalData.token = data.token;
    app.globalData.isLoggedIn = true;
    const user = data.user || null;
    if (user) {
      app.globalData.userInfo = user;
      if (user.id) {
        app.globalData.userId = user.id;
        try {
          wx.setStorageSync('ylm_user_id', user.id);
        } catch (e) { /* ignore */ }
      }
      app.globalData.hasBazi = !!(user.has_bazi || user.bazi);
      app.globalData.baziInfo = user.bazi || null;
    }
  }
}

let reloginPromise = null;

/**
 * 静默自动重登（仅 401 触发）：wx.login → code → /api/user/login → 更新 token/userId。
 * 并发 401 共享同一次重登；不弹任何 toast。
 * @returns {Promise<Object>}
 */
function relogin() {
  if (reloginPromise) return reloginPromise;
  reloginPromise = new Promise((resolve, reject) => {
    wx.login({
      success: (res) => {
        if (!res || !res.code) {
          reject(new Error('wx.login 未返回 code'));
          return;
        }
        request('/api/user/login', {
          method: 'POST',
          data: { code: res.code },
          _noAuthRetry: true,
        }).then((data) => {
          if (data && data.token) {
            applyAuth(data);
            resolve(data);
          } else {
            reject(new Error('登录响应缺少 token'));
          }
        }).catch(reject);
      },
      fail: (err) => reject(new Error('wx.login 失败: ' + ((err && err.errMsg) || ''))),
    });
  });
  reloginPromise.catch(() => {}).then(() => { reloginPromise = null; });
  return reloginPromise;
}

/**
 * 等待 app.js 的登录流程定型（最多 3s），避免登录未完成时用兜底 user_id 发请求。
 * 登录失败/超时不影响请求继续（走统一兜底 local_user）。
 * @returns {Promise<void>}
 */
function waitForLogin() {
  const app = getApp && getApp();
  if (app && app.loginPromise) {
    return Promise.race([
      Promise.resolve(app.loginPromise).catch(() => {}),
      new Promise((resolve) => setTimeout(resolve, 3000)),
    ]);
  }
  return Promise.resolve();
}

/** 统一 user_id 来源：globalData.userId（登录响应 user.id）→ 兜底 local_user */
function resolveUserId() {
  const app = getApp && getApp();
  const gd = (app && app.globalData) || {};
  return gd.userId || gd.openid || 'local_user';
}

// ---- 用户认证 ----

/**
 * 微信登录
 * @param {string} code - wx.login 获取的临时 code
 * @returns {Promise<{token, user}>}
 */
function login(code) {
  return request('/api/user/login', {
    method: 'POST',
    data: { code },
    _noAuthRetry: true, // 登录本身不触发 401 重登，避免死循环
  });
}

// ---- 日历/运势 ----

/**
 * 获取今日运势
 * @returns {Promise<{date, ganzhi, score, yi, ji, advice, mood}>}
 */
function getTodayFortune(userId) {
  // user_id 统一：优先显式参数，否则 globalData.userId（登录响应 user.id），兜底 local_user
  const uid = userId || resolveUserId();
  return request(`/api/calendar/today?user_id=${encodeURIComponent(uid)}`, {
    method: 'GET',
    showLoading: false,
  });
}

/**
 * 获取指定日期的运势（前后滑动）
 * @param {string} date - 日期 YYYY-MM-DD
 * @returns {Promise}
 */
function getDateFortune(date) {
  return request(`/api/calendar/today?date=${date}`, {
    method: 'GET',
  });
}

// ---- 场景列表 ----

/**
 * 获取对话场景列表
 * @returns {Promise<{scenarios: Array}>}
 */
function getScenarios() {
  return request('/api/scenarios', {
    method: 'GET',
  });
}

// ---- 对话 ----

/**
 * 发送聊天消息
 * @param {string} message - 用户消息
 * @param {string} scenario - 场景标识
 * @param {Array} history - 历史消息（用于上下文）
 * @param {Object} options - { messageType: 'text'|'voice', voiceText: string }
 * @returns {Promise<{reply, parts, consultation_id, disclaimer, structured}>}
 */
function chat(message, scenario = '', history = [], options = {}) {
  const data = {
    message,
    user_id: resolveUserId(), // 统一身份：globalData.userId，不再硬编码 miniprogram_user
    message_type: options.messageType || 'text',
  };
  if (options.voiceText) data.voice_text = options.voiceText;
  if (options.deepNight) data.deep_night = true;  // Task 8 深夜倾诉：临时不记录 + 深夜语气层
  // chat 冷启动最慢 59s：单独长超时 60s（微信平台单请求上限），其余请求 30s
  return waitForLogin().then(() => {
    data.user_id = resolveUserId(); // 登录定型后取最新统一身份
    return request('/api/chat', {
      method: 'POST',
      data,
      timeout: CONFIG.chatTimeout,
    });
  });
}

/**
 * SSE 流式对话（v8 阶段 3·过程体验）：wx.request enableChunked 逐块接收。
 *
 * 事件协议（后端 /api/chat/stream，每行 data: {json}）：
 *   {type:'start'} {type:'thinking',text} {type:'tool',text}
 *   {type:'chunk',content} {type:'done',consultation_id} {type:'error',message} {type:'ping'}
 *
 * @param {string} message - 用户消息
 * @param {Object} handlers - { onStart, onThinking, onTool, onChunk, onDone, onError, onAbort }
 * @param {Object} options  - { messageType: 'text'|'voice', voiceText }
 * @returns {Promise<{abort: Function}>} abort() = 停止生成（已输出保留）
 */
function chatStream(message, handlers = {}, options = {}) {
  const data = {
    message,
    user_id: resolveUserId(),
    message_type: options.messageType || 'text',
  };
  if (options.voiceText) data.voice_text = options.voiceText;
  if (options.deepNight) data.deep_night = true;  // Task 8 深夜倾诉：临时不记录 + 深夜语气层

  return waitForLogin().then(ensureBaseURL).then(() => {
    data.user_id = resolveUserId(); // 登录定型后取最新统一身份
    const header = { 'Content-Type': 'application/json' };
    const token = getToken();
    if (token) header.Authorization = `Bearer ${token}`;

    let task;
    try {
      task = wx.request({
        url: `${getBaseURL()}/api/chat/stream`,
        method: 'POST',
        data,
        header,
        enableChunked: true,   // 小程序流式：逐块 onChunkReceived（开发者工具与真机均支持）
        timeout: 0,            // 流式不走总超时（长回复由 chunk 级看门狗/后端心跳负责）
        success: (res) => {
          // enableChunked 下流式正常结束走 success；非 200（401/404等）视为异常
          if (res.statusCode !== 200) {
            handlers.onError && handlers.onError(new Error('请求失败(' + res.statusCode + ')'));
          }
        },
        fail: (err) => {
          const msg = (err && (err.errMsg || err.message)) || '';
          if (/abort/.test(msg)) {
            handlers.onAbort && handlers.onAbort(err); // 用户主动停止：保留已输出
          } else {
            handlers.onError && handlers.onError(err);
          }
        },
      });
    } catch (e) {
      // 真机保护：个别基础库对 enableChunked/参数校验会同步抛错 → 走失败回调（页面回退普通请求）
      handlers.onError && handlers.onError(e);
      return Promise.resolve({ abort() {} });
    }
    if (task && task.onChunkReceived) {
      task.onChunkReceived((res) => handlers.onChunkRaw && handlers.onChunkRaw(res));
    }
    return {
      abort() {
        try { task.abort(); } catch (e) { /* ignore */ }
      },
    };
  });
}

/**
 * 文字转语音（语音播报）
 * @param {string} text - 要朗读的文本
 * @param {string} voice - 音色（可选，默认由后端决定）
 * @returns {Promise<{audio_url: string}>}
 */
function tts(text, voice = '') {
  const data = { text };
  if (voice) data.voice = voice;
  return request('/api/tts', {
    method: 'POST',
    data,
  });
}

/**
 * 提交回答反馈（反馈回路）
 * @param {number|string} consultationId - 后端返回的咨询 ID
 * @param {string} rating - 'positive' | 'negative'
 * @param {string} label - 反馈标签（附 comment 查询参数）
 * @returns {Promise}
 */
function feedback(consultationId, rating, label = '') {
  let url = `/api/feedback/${consultationId}?feedback=${encodeURIComponent(rating)}`;
  if (label) url += `&comment=${encodeURIComponent(label)}`;
  return request(url, {
    method: 'POST',
  });
}

// ---- 报告 ----

/**
 * 获取历史报告列表
 * @param {number} page - 页码
 * @param {number} limit - 每页数量
 * @returns {Promise<{reports: Array, total: number}>}
 */
function getReports(page = 1, limit = 20) {
  return request(`/api/reports?page=${page}&limit=${limit}`, {
    method: 'GET',
  });
}

/**
 * 获取单份报告详情
 * @param {string} reportId - 报告 ID
 * @returns {Promise<{report: Object}>}
 */
function getReportDetail(reportId) {
  return request(`/api/reports/${reportId}`, {
    method: 'GET',
  });
}

// ---- 用户设置 ----

/**
 * 更新用户八字信息
 * @param {Object} baziData - {birthYear, birthMonth, birthDay, birthHour, gender, calendar}
 * @returns {Promise}
 */
function updateBazi(baziData) {
  return request('/api/user/bazi', {
    method: 'POST',
    data: baziData,
  });
}

/**
 * 获取用户资料
 * @returns {Promise}
 */
function getUserProfile() {
  return request('/api/user/profile', {
    method: 'GET',
  });
}

/**
 * 更新订阅设置
 * @param {boolean} enabled - 是否开启每日推送
 * @returns {Promise}
 */
function updateSubscription(enabled) {
  return request('/api/user/subscription', {
    method: 'POST',
    data: { daily_push: enabled },
  });
}

/**
 * 查询订阅设置（后端 GET /api/user/subscription）
 * @returns {Promise<{daily_push: boolean, push_time: string}>}
 */
function getSubscription() {
  return request('/api/user/subscription', {
    method: 'GET',
  });
}

// ---- 明灯晨笺（早晚双笺订阅：开关 + 时间自选 + 服务号绑定） ----

/**
 * 获取晨笺订阅偏好（开关 + 时间自选 + 服务号绑定状态）
 * @returns {Promise<{prefs: {jian_enabled, jian_time, night_enabled, night_time, bound_status, mp_openid}}>}
 */
function getJianPrefs() {
  return request('/api/jian/prefs', {
    method: 'GET',
  });
}

/**
 * 更新晨笺订阅偏好（部分字段可空：开关 / 时间）
 * @param {Object} patch - {jian_enabled?, jian_time?, night_enabled?, night_time?}（时间格式 HH:MM，如 "07:30"）
 * @returns {Promise<{prefs: Object}>}
 */
function putJianPrefs(patch = {}) {
  return request('/api/jian/prefs', {
    method: 'PUT',
    data: patch,
  });
}

/**
 * 绑定服务号（mp_openid 为服务号 openid；真实绑定待 unionid 交换校验，后端已限流）
 * @param {string} mpOpenid - 服务号 openid
 * @returns {Promise<{bound: boolean}>}
 */
function bindMp(mpOpenid) {
  return request('/api/jian/bind', {
    method: 'PUT',
    data: { mp_openid: mpOpenid },
  });
}

/**
 * 今日晨笺（今日页晨笺卡数据）
 * @returns {Promise<{date, day_ganzhi, suitable: string[], unsuitable: string[],
 *                    quote, book, private_line, question}>}
 */
function getJianToday() {
  return request('/api/jian/today', {
    method: 'GET',
  });
}

/**
 * 注销账号（账号中心）：POST /api/user/cancel
 * @param {string} confirmText - 确认文案（用户需输入「注销」二字）
 * @returns {Promise}
 */
function cancelAccount(confirmText) {
  return request('/api/user/cancel', {
    method: 'POST',
    data: { confirm: confirmText },
  });
}

// ---- 多人命主档案（契约已定稿，后端并行实现中；失败由页面降级本地缓存） ----

/**
 * 命主档案列表
 * @returns {Promise<{persons: Array<{id,name,relation,gender,birth_year,birth_month,birth_day,birth_hour,birth_minute,calendar,city,is_default,created_at}>}>}
 */
function getPersons() {
  return request('/api/persons', { method: 'GET' });
}

/**
 * 新增命主
 * @param {Object} data {name,relation,gender,birth_year,birth_month,birth_day,birth_hour,birth_minute,calendar,city}
 * @returns {Promise<{person: Object}>}
 */
function createPerson(data) {
  return request('/api/persons', { method: 'POST', data });
}

/**
 * 更新命主
 * @param {string|number} id - 命主 id
 * @returns {Promise<{person: Object}>}
 */
function updatePerson(id, data) {
  return request(`/api/persons/${id}`, { method: 'PUT', data });
}

/**
 * 删除命主
 * @returns {Promise<{ok: boolean}>}
 */
function deletePerson(id) {
  return request(`/api/persons/${id}`, { method: 'DELETE' });
}

/**
 * 设为默认命主
 * @returns {Promise<{ok: boolean}>}
 */
function setDefaultPerson(id) {
  return request(`/api/persons/${id}/default`, { method: 'POST' });
}

// ---- 分享 ----

/**
 * 生成分享卡片
 * @param {string} reportId - 报告 ID
 * @returns {Promise<{imageUrl: string}>}
 */
function generateShareCard(reportId) {
  return request(`/api/share/${reportId}`, {
    method: 'GET',
  });
}

// ---- 定价 ----

/**
 * 获取定价信息
 * @returns {Promise<{pricing: Object}>}
 */
function getPricing() {
  return request('/api/pricing', {
    method: 'GET',
  });
}

// ---- 支付 ----

/**
 * 发起微信支付
 * @param {Object} orderInfo - { productId, totalFee, description }
 * @returns {Promise<{payment: Object, orderId: string}>}
 */
function createOrder(productId) {
  return request('/api/pay/create', {
    method: 'POST',
    data: { product_id: productId },
    showLoading: true,
  });
}

/**
 * 确认支付
 * @param {Object} paymentParams - 微信支付参数（从 createOrder 返回）
 * @returns {Promise}
 */
function confirmPayment(paymentParams) {
  return new Promise((resolve, reject) => {
    wx.requestPayment({
      ...paymentParams,
      success: resolve,
      fail: reject,
    });
  });
}

// ---- 虚拟支付（米大师） ----

/**
 * 创建虚拟支付订单（后端生成 signData/paySig/signature 三要素）
 * @param {string} productId - 商品/套餐 ID（love_compatibility / deep_report / full_analysis / monthly / first_month）
 * @returns {Promise<{signData, paySig, signature, mode, outTradeNo, productId, goodsPrice, offerId, env}>}
 *         未启用虚拟支付时 reject {detail:{code:'virtual_pay_not_enabled'}}
 */
function createVirtualOrder(productId) {
  return request('/api/pay/virtual/create', {
    method: 'POST',
    data: { product_id: productId },
    showLoading: true,
  });
}

/**
 * 查询虚拟支付订单状态（支付后轮询）
 * @param {string} outTradeNo - 业务订单号
 * @returns {Promise<{status, outTradeNo, productId}>}
 */
function checkVirtualStatus(outTradeNo) {
  return request(`/api/pay/virtual/status?outTradeNo=${encodeURIComponent(outTradeNo)}`, {
    method: 'GET',
  });
}

// ---- 四术（合婚 / 奇门 / 姓名 / 学堂） ----

/** 时辰序 → 代表整点（子时23:00起，其余每时辰2小时：丑1 寅3 卯5 辰7 巳9 午11 未13 申15 酉17 戌19 亥21）。
 *  与后端 BaziEngine（按公历整点排盘，hour>=23 走晚子时规则）语义一致。 */
function _shichenHour(index) {
  const i = parseInt(index, 10) || 0;
  return i === 0 ? 23 : i * 2 - 1;
}

/** 前端表单生辰 → 后端 BaziInput（hehun 契约：year/month/day/hour/minute/city/gender(男/女)） */
function _toBackendBazi(p) {
  const src = p || {};
  return {
    year: parseInt(src.birthYear, 10) || 0,
    month: parseInt(src.birthMonth, 10) || 0,
    day: parseInt(src.birthDay, 10) || 0,
    hour: _shichenHour(src.birthHour), // 时辰序号(0-11) → 代表整点
    minute: 0,
    city: src.city || '北京',
    gender: src.gender === 'female' ? '女' : '男',
  };
}

/**
 * 合婚配对
 * 入参：{ person1: {birthYear, birthMonth, birthDay, birthHour(0-11), gender('male'|'female'), city},
 *        person2: {…同} }
 * 响应：{score, wuxing:{score,detail}, shengxiao:{score,detail}, rizhu:{score,detail}, advice, summary, narrative}
 *   （后端 total_score → score；性别/时辰字段在此完成契约转换）
 */
function hehun(payload) {
  const p = payload || {};
  return request('/api/hehun', {
    method: 'POST',
    data: {
      person_a: _toBackendBazi(p.person1),
      person_b: _toBackendBazi(p.person2),
    },
  }).then((res) => ({
    score: res.total_score,
    wuxing: res.wuxing || {},
    shengxiao: res.shengxiao || {},
    rizhu: res.rizhu || {},
    advice: res.advice || [],
    summary: res.summary || '',
    narrative: res.narrative || '',
  }));
}

/**
 * 双人合盘（POST /api/union）
 * 入参：{ person1: {birthYear, birthMonth, birthDay, birthHour(0-11 时辰序号,可省), gender('male'|'female'), city(可省)},
 *        person2: {…同}, relation(恋人/暧昧/夫妻/朋友/暗恋,可省), paid(付费档,可省) }
 * 免费档响应：{score, levelLabel, levelSublabel, dimensions:{wuxing:{score,max},shengxiao:{score,max,relation},rizhu:{score,max,relation}},
 *             features, relation, quote, quoteParts:{main,suffix,cliffhanger,full}, yuan_card, paywall, transient}
 * 付费档(paid=true)响应追加：{report:{chapters:[{title,content}], citations}, reportId, purchased}
 */
function union(data) {
  return request('/api/union', {
    method: 'POST',
    header: { 'Content-Type': 'application/json' },
    data,
  });
}

// 洛书九宫：宫名 → 宫位序号（九宫盘用 position 判断中宫）
const LO_SHU_POSITION = { 坎: 1, 坤: 2, 震: 3, 巽: 4, 中: 5, 乾: 6, 兑: 7, 艮: 8, 离: 9 };
const LO_SHU_WUXING = { 坎: '水', 坤: '土', 震: '木', 巽: '木', 中: '土', 乾: '金', 兑: '金', 艮: '土', 离: '火' };

/**
 * 奇门遁甲
 * 入参：{ date: 'YYYY-MM-DD', time: 'HH:mm', city, question }
 * 响应：后端 QimenResponse，其中 palaces 补充 position/name/wuxing 供九宫盘渲染
 */
function qimen(payload) {
  const p = payload || {};
  let year = 0, month = 0, day = 0, hour = 12, minute = 0;
  const dateParts = String(p.date || '').split('-');
  if (dateParts.length === 3) {
    year = parseInt(dateParts[0], 10) || 0;
    month = parseInt(dateParts[1], 10) || 0;
    day = parseInt(dateParts[2], 10) || 0;
  }
  const timeParts = String(p.time || '').split(':');
  if (timeParts.length >= 2) {
    hour = parseInt(timeParts[0], 10) || 0;
    minute = parseInt(timeParts[1], 10) || 0;
  }
  return request('/api/qimen', {
    method: 'POST',
    data: {
      year, month, day, hour, minute,
      city: p.city || '北京',
      question: p.question || '',
    },
  }).then((res) => {
    const palaces = (res.palaces || []).map((pal) => {
      const name = String(pal.palace || '').replace('宫', '');
      return {
        ...pal,
        name,
        position: LO_SHU_POSITION[name] || 0,
        wuxing: LO_SHU_WUXING[name] || '',
      };
    });
    return { ...res, palaces };
  });
}

/**
 * 姓名学
 * 入参：{ surname, givenName, gender('male'|'female') }
 * 响应：{fiveCells:[{name,strokes,wuxing,luck}], sancai, judgment, 及后端原始字段}
 */
function xingming(payload) {
  const p = payload || {};
  return request('/api/xingming', {
    method: 'POST',
    data: {
      surname: p.surname || '',
      given_name: p.givenName || '',
      gender: p.gender === 'female' ? '女' : '男',
    },
  }).then((res) => {
    const order = ['天格', '人格', '地格', '外格', '总格'];
    const wuge = res.wuge || {};
    const analysis = res.analysis || {};
    const wuxing = res.wuxing || {};
    const fiveCells = order.map((name) => {
      const num = wuge[name] || 0;
      const luck = (analysis[name] && analysis[name].吉凶) || '';
      return {
        name,
        strokes: num,
        wuxing: wuxing[name] || '',
        luck: luck.includes('吉') ? '吉' : '凶',
      };
    });
    const sancai = res.sancai || '';
    return {
      fiveCells,
      sancai: sancai ? `三才配置为${sancai}，${res.sancai_ji || '配置一般'}` : '',
      judgment: res.overall || '',
      wuge,
      sancai_ji: res.sancai_ji || '',
      stroke_counts: res.stroke_counts || {},
      analysis,
      wuxing,
      overall: res.overall || '',
      bazi_match: res.bazi_match || null,
      narrative: res.narrative || '',
    };
  });
}

// 学堂课程层级 → 前端话题描述
const XUETANG_LEVEL_DESC = {
  入门: '八字基础入门，建立命理框架',
  进阶: '进阶理论，深入命理核心',
  专题: '专题应用，学以致用',
};

/**
 * 学堂课程目录（后端 GET /api/xuetang/topics）
 * 响应：{topics: [{id, icon, name, description, lessonCount}]}
 */
function getXuetangTopics() {
  return request('/api/xuetang/topics', {
    method: 'GET',
  }).then((res) => {
    const curriculum = (res && res.curriculum) || [];
    const topics = [];
    (curriculum || []).forEach((sec) => {
      (sec.topics || []).forEach((t) => {
        topics.push({
          id: t.name,            // 后端话题名为中文（如「什么是八字」），直接作 id 传给 lesson 接口
          icon: '',
          name: t.name,
          description: XUETANG_LEVEL_DESC[sec.level] || `「${sec.level}」课程`,
          lessonCount: 1,
          level: sec.level,
        });
      });
    });
    return { topics };
  });
}

/**
 * 学堂课程详情（后端 GET /api/xuetang/lesson?topic=xxx）
 * 响应：{lesson: {icon, title, topicLabel, content, related_topics}}
 */
function getXuetangLesson(topicId) {
  return request(`/api/xuetang/lesson?topic=${encodeURIComponent(topicId || '')}`, {
    method: 'GET',
  }).then((res) => ({
    lesson: {
      icon: '',
      title: res.topic || topicId,
      topicLabel: res.level || '',
      content: res.content || '',
      personalized: !!res.personalized,
      personalized_example: res.personalized_example || '',
      related_topics: res.related_topics || [],
    },
  }));
}

// ---- 感情合盘 ----

/**
 * 获取感情合盘分析
 * @param {Object} data - { birthYear1, birthMonth1, birthDay1, birthHour1, gender1,
 *                          birthYear2, birthMonth2, birthDay2, birthHour2, gender2 }
 * @param {boolean} paid - 是否已付费（true=完整报告）
 * @returns {Promise}
 */
function getLoveCompatibility(data, paid = false) {
  return request('/api/love/compatibility', {
    method: 'POST',
    data: { ...data, paid },
    showLoading: true,
  });
}

// ---- 会员 ----

/**
 * 获取会员信息
 * @returns {Promise<{isMember, expireDate, benefits}>}
 */
function getMemberInfo() {
  return request('/api/user/member', {
    method: 'GET',
  });
}

/**
 * 订阅会员
 * @param {string} planId - 套餐 ID: 'monthly' | 'first_month'
 * @returns {Promise}
 */
function subscribeMember(planId) {
  return request('/api/pay/subscribe', {
    method: 'POST',
    data: { plan_id: planId },
    showLoading: true,
  });
}

// ---- 购买记录 ----

/**
 * 获取购买记录
 * @returns {Promise<{orders: Array}>}
 */
function getOrders() {
  return request('/api/user/orders', {
    method: 'GET',
  });
}

/**
 * 检查是否已购买某产品
 * @param {string} productId
 * @returns {Promise<{purchased: boolean}>}
 */
function checkPurchase(productId) {
  return request(`/api/user/purchase/${productId}`, {
    method: 'GET',
  });
}

// ---- 深夜陪伴(灯下漫谈) ----

function getNightPrefs() { return request('/api/night/prefs', { method: 'GET' }); }
function putNightPrefs(patch) { return request('/api/night/prefs', { method: 'PUT', data: patch }); }
function getNightStatus() { return request('/api/night/status', { method: 'GET' }); }
function getLampToday() { return request('/api/night/lamp/today', { method: 'GET' }); }
function favLamp(date) { return request('/api/night/lamp/favorite', { method: 'POST', data: { date } }); }
function getLampHistory() { return request('/api/night/lamp/history', { method: 'GET' }); }
function rememberNight(message) { return request('/api/night/remember', { method: 'POST', data: { message } }); }

// ---- 择吉日(大事择吉日) ----

function getZeriPlans() { return request('/api/zeri/plans', { method: 'GET' }); }
function getZeriPlan(planId) { return request(`/api/zeri/plans/${planId}`, { method: 'GET' }); }
function selectZeri(data) { return request('/api/zeri/select', { method: 'POST', data, showLoading: true }); }
function updateZeriItem(planId, patch) { return request(`/api/zeri/plans/${planId}/item`, { method: 'PUT', data: patch }); }
function setZeriReminder(planId, enabled) { return request(`/api/zeri/plans/${planId}/reminder`, { method: 'PUT', data: { enabled } }); }
// data: {scene, start, end, exclude_dates} 或表单三步入口 {scene, period, bazi, exclude_dates}
function refreshZeri(data) { return request('/api/zeri/refresh', { method: 'POST', data }); }
function getZeriOptions(params = {}) {
  // 初始加载取卡：GET /api/zeri/options，不扣换一批额度；exclude_dates 逗号分隔可选传
  // period/bazi 可选（表单三步入口：period=时间段，bazi=选填八字）
  const q = [];
  if (params.scene) q.push('scene=' + encodeURIComponent(params.scene));
  if (params.start) q.push('start=' + encodeURIComponent(params.start));
  if (params.end) q.push('end=' + encodeURIComponent(params.end));
  if (params.period) q.push('period=' + encodeURIComponent(params.period));
  if (params.bazi) q.push('bazi=' + encodeURIComponent(params.bazi));
  if (params.exclude_dates && params.exclude_dates.length) {
    q.push('exclude_dates=' + params.exclude_dates.map(encodeURIComponent).join(','));
  }
  return request('/api/zeri/options' + (q.length ? '?' + q.join('&') : ''), { method: 'GET' });
}
function getZeriPrefs() { return request('/api/zeri/prefs', { method: 'GET' }); }
function putZeriPrefs(patch) { return request('/api/zeri/prefs', { method: 'PUT', data: patch }); }

// ---- 登录增强（Task 2：手机号绑定 + 头像昵称采集） ----

/** 绑定/换绑手机号：微信 getPhoneNumber 授权 code → 后端 AES 落库，响应 {phone_masked} */
function bindPhone(code) {
  return request('/api/user/phone-bind', { method: 'POST', data: { code } });
}

/** 查询手机号绑定状态：{bound, phone_masked}（只回脱敏号） */
function getPhone() {
  return request('/api/user/phone', { method: 'GET' });
}

/** 保存昵称：{nickname} 1-20 字，服务端 strip */
function saveProfile(patch) {
  return request('/api/user/profile', { method: 'POST', data: patch });
}

/** 上传头像（multipart，后端 form 字段名 file，≤2MB）→ {avatar_url:"/api/user/avatar/{user_id}"}。
    必须用 wx.uploadFile（request 不支持 multipart）；响应 data 是 JSON 字符串，需手动 parse。 */
function uploadAvatar(filePath) {
  return ensureBaseURL().then((baseURL) => new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${baseURL}/api/user/avatar`,
      filePath,
      name: 'file',
      header: { Authorization: `Bearer ${getToken()}` },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          let data = res.data;
          try { data = JSON.parse(res.data); } catch (e) { /* ignore */ }
          resolve(data);
        } else {
          reject(res.data || { error: '头像上传失败' });
        }
      },
      fail: (err) => {
        console.error('[API] uploadAvatar error:', err);
        scheduleReprobe();
        reject(new Error('网络连接失败，请检查网络设置'));
      },
    });
  }));
}

// ---- 导出 ----

module.exports = {
  // 多环境 baseURL 探测（app.js onLaunch 提前启动）
  probe: () => probeBaseURL(),
  getBaseURL,

  // Auth
  login,
  setToken,
  getToken,
  applyAuth,

  // Calendar
  getTodayFortune,
  getDateFortune,

  // Scenarios
  getScenarios,

  // Chat
  chat,
  chatStream,
  tts,
  feedback,

  // Reports
  getReports,
  getReportDetail,

  // User
  updateBazi,
  getUserProfile,
  updateSubscription,
  getSubscription,
  cancelAccount,

  // Login enhancement (Task 2: 手机号绑定 + 头像昵称)
  bindPhone,
  getPhone,
  saveProfile,
  uploadAvatar,

  // Jian (明灯晨笺：早晚双笺订阅)
  getJianPrefs,
  putJianPrefs,
  bindMp,
  getJianToday,

  // Persons (多人命主档案)
  getPersons,
  createPerson,
  updatePerson,
  deletePerson,
  setDefaultPerson,

  // Share
  generateShareCard,

  // Pricing
  getPricing,

  // Payment
  createOrder,
  confirmPayment,

  // Virtual Payment (Midas)
  createVirtualOrder,
  checkVirtualStatus,

  // Four Arts
  hehun,
  union,
  qimen,
  xingming,
  getXuetangTopics,
  getXuetangLesson,

  // Love
  getLoveCompatibility,

  // Member
  getMemberInfo,
  subscribeMember,

  // Orders
  getOrders,
  checkPurchase,

  // Night companion (灯下漫谈)
  getNightPrefs,
  putNightPrefs,
  getNightStatus,
  getLampToday,
  favLamp,
  getLampHistory,
  rememberNight,

  // Zeri (大事择吉日)
  getZeriPlans,
  getZeriPlan,
  selectZeri,
  updateZeriItem,
  setZeriReminder,
  refreshZeri,
  getZeriOptions,
  getZeriPrefs,
  putZeriPrefs,
};
