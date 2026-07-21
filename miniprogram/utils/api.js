// 易理明灯 v5.0 — API 客户端
// Backend: https://124.221.233.214/api

const app = getApp();

// ---- 配置 ----
const CONFIG = {
  // TODO: Switch to HTTPS for production and add a proper domain name.
  // WeChat requires all production API requests to use HTTPS.
  // Current: HTTP on port 8765 for development only.
  baseURL: 'http://124.221.233.214:8765',
  timeout: 15000,
};

let authToken = null;

// ---- Token 管理 ----
function setToken(token) {
  authToken = token;
}

function getToken() {
  return authToken;
}

// ---- 核心请求方法 ----
function request(url, options = {}) {
  const { method = 'GET', data = {}, headers = {}, showLoading = false } = options;

  // 构建请求头
  const header = {
    'Content-Type': 'application/json',
    ...headers,
  };

  if (authToken) {
    header['Authorization'] = `Bearer ${authToken}`;
  }

  if (showLoading) {
    wx.showLoading({ title: '加载中...', mask: true });
  }

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${CONFIG.baseURL}${url}`,
      method,
      data,
      header,
      timeout: CONFIG.timeout,
      success: (res) => {
        if (showLoading) wx.hideLoading();

        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else if (res.statusCode === 401) {
          // Token 过期，清除登录态
          authToken = null;
          wx.showToast({ title: '登录已过期，请重新进入', icon: 'none' });
          reject(new Error('Unauthorized'));
        } else {
          reject(res.data || { error: '请求失败' });
        }
      },
      fail: (err) => {
        if (showLoading) wx.hideLoading();
        console.error('[API] request error:', err);
        reject(new Error('网络连接失败，请检查网络设置'));
      },
    });
  });
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
  });
}

// ---- 日历/运势 ----

/**
 * 获取今日运势
 * @returns {Promise<{date, ganzhi, score, yi, ji, advice, mood}>}
 */
function getTodayFortune() {
  return request('/api/calendar/today', {
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
 * @returns {Promise<{reply, structured, thinking}>}
 */
function chat(message, scenario = '', history = []) {
  return request('/api/chat', {
    method: 'POST',
    data: {
      message,
      scenario,
      history: history.slice(-20), // 只传最近 20 条
    },
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

// ---- 反馈 ----

/**
 * 提交用户反馈
 * @param {string} text - 反馈内容
 * @param {boolean} isAnonymous - 是否匿名
 * @returns {Promise}
 */
function feedback(text, isAnonymous = false) {
  return request('/api/user/feedback', {
    method: 'POST',
    data: { text, is_anonymous: isAnonymous },
  });
}

// ---- 导出 ----

module.exports = {
  // Auth
  login,
  setToken,
  getToken,

  // Calendar
  getTodayFortune,
  getDateFortune,

  // Scenarios
  getScenarios,

  // Chat
  chat,

  // Reports
  getReports,
  getReportDetail,

  // User
  updateBazi,
  getUserProfile,
  updateSubscription,
  feedback,

  // Share
  generateShareCard,

  // Pricing
  getPricing,
};
