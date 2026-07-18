// 易理明灯 API 客户端
const BASE = 'https://yilichat.com';
const API = `${BASE}/api`;

// 获取本地存储的 user_id，没有则生成
function getUserId() {
  let uid = wx.getStorageSync('user_id');
  if (!uid) {
    uid = 'wx_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    wx.setStorageSync('user_id', uid);
  }
  return uid;
}

function request(url, options = {}) {
  const { method = 'GET', data = {}, headers = {} } = options;
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API}${url}`,
      method,
      data,
      header: { 'Content-Type': 'application/json', ...headers },
      success: (res) => {
        if (res.statusCode === 200) resolve(res.data);
        else reject(res.data);
      },
      fail: reject,
    });
  });
}

// ---- 对话 ----
function chat(message) {
  return request('/chat', { method: 'POST', data: { message, user_id: getUserId() } });
}

// ---- 面相 ----
function faceReading(filePath, personality = 'sassy') {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${API}/face-reading`,
      filePath,
      name: 'image',
      formData: { user_id: getUserId(), personality },
      success: (res) => resolve(JSON.parse(res.data)),
      fail: reject,
    });
  });
}

// ---- 手相 ----
function palmReading(filePath) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${API}/palm-reading`,
      filePath,
      name: 'image',
      formData: { user_id: getUserId() },
      success: (res) => resolve(JSON.parse(res.data)),
      fail: reject,
    });
  });
}

// ---- 今日运势 ----
function dailyCalendar() {
  return request('/calendar/daily', { method: 'POST', data: { user_id: getUserId() } });
}

// ---- 7 天日历 ----
function weekCalendar() {
  return request('/calendar/week', { method: 'POST', data: { user_id: getUserId() } });
}

// ---- 仪表盘 ----
function dashboard() {
  return request(`/dashboard/${getUserId()}`);
}

// ---- 分享卡片 ----
function shareCard(style = 'dark') {
  return request(`/share-card/${getUserId()}?style=${style}`);
}

// ---- 反馈 ----
function feedback(consultationId, isPositive) {
  return request(`/feedback/${consultationId}?feedback=${isPositive ? 'positive' : 'negative'}`, { method: 'POST' });
}

module.exports = {
  getUserId, chat, faceReading, palmReading,
  dailyCalendar, weekCalendar, dashboard, shareCard, feedback,
};
