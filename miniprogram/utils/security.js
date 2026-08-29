// 易理明灯 — 数据安全模块
// 说明（G3 H-11）：本模块为「静态密钥 XOR + Base64」混淆，并非 AES 加密——
// 密钥与算法同在客户端源码（密钥硬编码于代码，不落盘），仅防止 storage 被
// 直接读出明文（防随手可读/肩窥），不具备真正的密码学强度。真正的端侧加密
// （AES-GCM 等）需引入加密库（如 crypto-js），属需 PM 拍板的依赖决策
// （红线：本批次零新依赖）；服务端传输/存储由后端 HTTPS + 服务端加密承担。

const STORAGE_PREFIX = 'ylm_enc_';

// 静态密钥 XOR + Base64（轻量混淆，非加密——见文件头说明；密钥不落盘存储）
function encode(str) {
  const key = 'YLM_V5_2024';
  let result = '';
  for (let i = 0; i < str.length; i++) {
    result += String.fromCharCode(str.charCodeAt(i) ^ key.charCodeAt(i % key.length));
  }
  return wx.arrayBufferToBase64 ? btoa(result) : Buffer.from(result, 'binary').toString('base64');
}

function decode(encoded) {
  const key = 'YLM_V5_2024';
  const str = atob(encoded);
  let result = '';
  for (let i = 0; i < str.length; i++) {
    result += String.fromCharCode(str.charCodeAt(i) ^ key.charCodeAt(i % key.length));
  }
  return result;
}

// 浏览器环境的 Base64 垫片
function btoa(str) {
  if (typeof wx !== 'undefined' && wx.arrayBufferToBase64) {
    const chars = str.split('').map(c => c.charCodeAt(0));
    const buf = new Uint8Array(chars).buffer;
    return wx.arrayBufferToBase64(buf);
  }
  // 后备方案
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
  let output = '';
  for (let i = 0; i < str.length; i += 3) {
    const a = str.charCodeAt(i) || 0;
    const b = str.charCodeAt(i + 1) || 0;
    const c = str.charCodeAt(i + 2) || 0;
    output += chars.charAt(a >> 2);
    output += chars.charAt(((a & 3) << 4) | (b >> 4));
    output += chars.charAt(((b & 15) << 2) | (c >> 6));
    output += chars.charAt(c & 63);
  }
  return output;
}

function atob(str) {
  if (typeof wx !== 'undefined' && wx.base64ToArrayBuffer) {
    const buf = wx.base64ToArrayBuffer(str);
    const chars = new Uint8Array(buf);
    return String.fromCharCode(...chars);
  }
  // 后备方案
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
  let output = '';
  str = str.replace(/[^A-Za-z0-9+/=]/g, '');
  for (let i = 0; i < str.length; i += 4) {
    const a = chars.indexOf(str[i] || '=');
    const b = chars.indexOf(str[i + 1] || '=');
    const c = chars.indexOf(str[i + 2] || '=');
    const d = chars.indexOf(str[i + 3] || '=');
    output += String.fromCharCode(((a << 2) | (b >> 4)) & 255);
    if (c !== 64) output += String.fromCharCode(((b << 4) | (c >> 2)) & 255);
    if (d !== 64) output += String.fromCharCode(((c << 6) | d) & 255);
  }
  return output;
}

// ---- 公开 API ----

/**
 * 安全存储敏感数据
 * @param {string} key - 存储键名
 * @param {*} value - 要存储的值（会自动 JSON 序列化）
 */
function setSecure(key, value) {
  try {
    const str = JSON.stringify(value);
    const encrypted = encode(str);
    wx.setStorageSync(STORAGE_PREFIX + key, encrypted);
    return true;
  } catch (e) {
    console.error('[Security] setSecure error:', e);
    return false;
  }
}

/**
 * 读取安全存储的数据
 * @param {string} key - 存储键名
 * @returns {*|null} 解密后的数据
 */
function getSecure(key) {
  try {
    const encrypted = wx.getStorageSync(STORAGE_PREFIX + key);
    if (!encrypted) return null;
    const str = decode(encrypted);
    return JSON.parse(str);
  } catch (e) {
    console.error('[Security] getSecure error:', e);
    return null;
  }
}

/**
 * 删除安全存储的数据
 * @param {string} key - 存储键名
 */
function removeSecure(key) {
  try {
    wx.removeStorageSync(STORAGE_PREFIX + key);
  } catch (e) {
    console.error('[Security] removeSecure error:', e);
  }
}

/**
 * 清空所有安全存储数据
 */
function clearSecure() {
  try {
    const info = wx.getStorageInfoSync();
    const keys = info.keys.filter(k => k.startsWith(STORAGE_PREFIX));
    keys.forEach(k => wx.removeStorageSync(k));
  } catch (e) {
    console.error('[Security] clearSecure error:', e);
  }
}

module.exports = {
  setSecure,
  getSecure,
  removeSecure,
  clearSecure,
};
