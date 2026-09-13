// 易理明灯 — k47-C 错误日志规范（可定位 · 无隐私）
//
// 背景（开发者工具实测 34 页 7 条 console|error|[{}] + 页面侧 `…失败: {}`）：
//   直接打印错误对象时，Error/类空对象被序列化为 `{}`，日志看不出是哪条链路；
//   而若把整个对象展开又可能带出用户隐私（生辰、对话原文、token 等）。
// 规范：错误日志统一走 logErr(scene, err, extra) —— 只输出
//   场景名 + 错误码/HTTP 状态 + 消息摘要（截断、压平空白），**不打印用户隐私原文**。
//   页面自有的业务字段（如接口路径）经 extra 显式传入，仍须自查不含隐私内容。

/** 单行压缩 + 截断（日志里不出现换行/超长文本） */
function clip(s, n) {
  const t = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const max = n || 160;
  return t.length > max ? t.slice(0, max) + '…' : t;
}

/**
 * 错误对象 → 可定位的单行摘要（errCode/status/errMsg|message 优先；
 * Error 实例取 message；普通对象取 JSON（`{}` → 'empty-object'））。
 * @param {*} e - 任意错误值
 * @returns {string} 形如 'code=41002 login:fail 系统错误，错误码：41002,appid missing'
 */
function errText(e) {
  if (e == null) return 'unknown';
  if (typeof e === 'string') return clip(e) || 'empty-string';
  const out = [];
  const code = (e.errCode != null) ? e.errCode : ((e.errno != null) ? e.errno : e.code);
  if (code != null && code !== '') out.push('code=' + code);
  if (e.statusCode != null) out.push('status=' + e.statusCode);
  const msg = e.errMsg || e.message || e.msg;
  if (msg) out.push(clip(msg));
  else {
    const s = String(e);
    if (s && s !== '[object Object]') out.push(clip(s));
    else {
      let j = '';
      try { j = JSON.stringify(e); } catch (x) { j = ''; }
      out.push(j && j !== '{}' ? clip(j) : 'empty-object');
    }
  }
  return out.join(' ') || 'unknown';
}

/**
 * 统一错误日志（console.error）：场景 + 关键字段，绝不打裸对象/隐私原文。
 * @param {string} scene - 场景名（如 'API request' / 'Chat 新开对话'）
 * @param {*} err - 错误值
 * @param {string} [extra] - 附加关键字段（如接口路径；勿传隐私）
 */
function logErr(scene, err, extra) {
  console.error('[ERR] ' + clip(scene, 80) + (extra ? ' (' + clip(extra, 120) + ')' : '') + ' — ' + errText(err));
}

/**
 * 统一告警日志（console.warn）：同上（页面侧大量「接口失败…」日志同属 {} 形态缺陷）
 * @param {string} scene - 场景名
 * @param {*} err - 错误值
 * @param {string} [extra] - 附加关键字段（如接口路径；勿传隐私）
 */
function logWarn(scene, err, extra) {
  console.warn('[WARN] ' + clip(scene, 80) + (extra ? ' (' + clip(extra, 120) + ')' : '') + ' — ' + errText(err));
}

module.exports = { errText, logErr, logWarn, clip };
