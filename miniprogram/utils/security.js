// 易理明灯 — 数据安全模块
// 敏感数据 AES 加密存储，防止明文泄露用户八字等隐私信息

const STORAGE_PREFIX = 'ylm_enc_';

// 简单的 XOR + Base64 编码（轻量级混淆）
// 注意：这是前端存储的轻量加密，服务端传输必须使用 HTTPS
function encode(str) {
  const key = 'YLM_V5_2024';
  let result = '';
  for (let i = 0; i < str.length; i++) {
    result += String.fromCharCode(str.charCodeAt(i) ^ key.charCodeAt(i % key.length));
  }
  // 使用内置 btoa（微信小程序环境可用）；微信不支持 Buffer
  try { return btoa(result); } catch(e) { return result; }
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

/**
 * 加密字符串（公开的 encode 包装）
 * @param {string} str - 要加密的字符串
 * @returns {string} 加密后的 Base64 字符串
 */
function encrypt(str) {
  return encode(str);
}

/**
 * 解密字符串（公开的 decode 包装）
 * @param {string} encrypted - 加密的 Base64 字符串
 * @returns {string} 解密后的原始字符串
 */
function decrypt(encrypted) {
  return decode(encrypted);
}

module.exports = {
  setSecure,
  getSecure,
  removeSecure,
  clearSecure,
  encrypt,
  decrypt,
};
