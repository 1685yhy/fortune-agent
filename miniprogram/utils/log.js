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

/* ── k47-E：全局错误详情（消息 + 堆栈摘要）与「工具侧噪音」判定 ──
   实测（开发者工具 3.17.0，2026-09-14）：app.json 声明的 WechatSI 插件
   （provider wx069ba97219f66d99 v0.3.5）在本机开发环境下加载时，框架自身在
   `reportPluginCodeRequire` 读 `.version` 抛 TypeError：
     MiniProgramError / Cannot read properties of undefined (reading 'version')
     at kU (…/__dev__/WAServiceMainContext.js…) / at Object.AU [as reportPluginCodeRequire]
     at o.beforeFactory (…/__dev__/WASubContext.js…) / at weapp:///__onlineplugin__/wx069ba97219f66d99/0.3.5/…
   → 这是**开发者工具/插件加载侧崩溃**（不是业务代码缺陷）：经插桩核实，同批
   console|error 记录里应用侧 console.error 仅 1 次（我们这条），其余为工具自身记录。
   处理：打印时标注「工具侧噪音」并**不计入 ylm_last_error 留痕统计**，
   避免工具噪音污染真实错误线索。 */
const TOOL_NOISE_PAT = /(__dev__\/|WAServiceMainContext|WASubContext|__onlineplugin__|reportPluginCodeRequire|WeappXWebview)/;

/**
 * 错误详情：消息 + 堆栈摘要（前 N 帧，单行、截断），并判定是否工具侧噪音。
 * 兼容 MiniProgramError 这种「message 里整段含堆栈」的形态。
 * @param {*} e - 错误值
 * @param {number} [maxFrames=3]
 * @returns {{message:string, frames:string[], noise:boolean, text:string}}
 */
function errDetail(e, maxFrames) {
  let msgPart = '';
  let stackPart = '';
  if (e == null) msgPart = 'unknown';
  else if (typeof e === 'string') msgPart = e;
  else if (typeof e === 'object') {
    msgPart = (e.message != null) ? String(e.message) : ((e.errMsg != null) ? String(e.errMsg) : '');
    stackPart = e.stack ? String(e.stack) : '';
    if (!msgPart && !stackPart) {
      try { msgPart = JSON.stringify(e) || 'empty-object'; } catch (x) { msgPart = 'unknown'; }
    }
  } else msgPart = String(e);
  const clean = (s) => s.replace(/\r/g, '').split('\n').map((x) => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const msgLines = clean(msgPart);
  const stackLines = clean(stackPart);
  // 帧判定：'at fn (path:line:col)' / 'at path:line:col'（含 MiniProgramError 把堆栈塞进 message 的形态）
  const isFrame = (l) => /^at\s/.test(l) || /\(.*:\d+:\d+\)$/.test(l);
  const frames = [];
  const seen = Object.create(null);
  msgLines.concat(stackLines).forEach((l) => {
    if (!isFrame(l) || seen[l]) return;
    seen[l] = 1;
    if (frames.length < (maxFrames || 3)) frames.push(clip(l, 160));
  });
  const msgHead = msgLines.filter((l) => !isFrame(l));
  const head = (msgHead.length ? msgHead : stackLines.filter((l) => !isFrame(l))).slice(0, 2);
  const message = clip(head.join(' · '), 240) || 'unknown';
  const noise = TOOL_NOISE_PAT.test(msgPart + '\n' + stackPart);
  return {
    message,
    frames,
    noise,
    text: clip(message + (frames.length ? ' @ ' + frames.join(' / ') : ''), 420),
  };
}

/**
 * 全局错误日志（console.error 单条字符串）：场景 +（噪音标注）+ 消息 + 堆栈摘要。
 * 不打印裸对象/隐私原文；返回详情供调用方决定是否计入留痕统计。
 * @param {string} scene - 场景名（如 '全局错误'）
 * @param {*} err - 错误值
 * @param {string} [extra] - 附加关键字段（勿传隐私）
 * @returns {{message:string, frames:string[], noise:boolean, text:string}}
 */
function logErrDetail(scene, err, extra) {
  const d = errDetail(err);
  console.error('[ERR] ' + clip(scene, 60)
    + (d.noise ? '（工具侧噪音：开发者工具/插件加载，不计入留痕）' : '')
    + (extra ? ' (' + clip(extra, 100) + ')' : '')
    + ' — ' + d.text);
  return d;
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

module.exports = { errText, logErr, logWarn, logErrDetail, errDetail, clip };
