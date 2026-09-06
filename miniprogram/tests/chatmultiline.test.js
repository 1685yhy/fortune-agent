// 易理明灯 — k10-B 多行输入语义（wxml 属性断言 + js 兜底链）
// 运行：node --test miniprogram/tests/chatmultiline.test.js
// 覆盖：
//   1. textarea 不得带 confirm-type="send"（键盘右下角=换行，发送走条上发送钮）
//   2. bindconfirm=onConfirm 保留为无害兜底（输入法若仍给「发送」键 → _send 链可用）
//   3. onConfirm → _send 仍存在（发送链不残留对键盘发送的依赖）
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const WXML = fs.readFileSync(path.join(__dirname, '../pages/chat/chat.wxml'), 'utf8');

/* ── textarea 属性区块（多行文本区：去掉 confirm-type=send 后回车=换行） ── */
const taMatch = WXML.match(/<textarea[^>]*>/);
assert.ok(taMatch, 'chat.wxml 应含 textarea');
const ta = taMatch[0];
assert.ok(!/confirm-type\s*=\s*"send"/.test(ta), 'textarea 不得带 confirm-type="send"（键盘右下角应为换行键）');
assert.ok(/bindconfirm\s*=\s*"onConfirm"/.test(ta), 'bindconfirm=onConfirm 应保留为无害兜底');
assert.ok(/bindinput\s*=\s*"onInput"/.test(ta), 'bindinput=onInput 应保留');
assert.ok(/auto-height/.test(ta) && /maxlength="500"/.test(ta), 'auto-height/maxlength 应保留（多行不封顶输入）');
assert.ok(/focus="\{\{inputFocus\}\}"/.test(ta), 'focus 绑定应保留');

/* ── js 兜底链：onConfirm → _send（保留，不依赖键盘发送） ── */
const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/chat/chat');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg.onConfirm === 'function', 'onConfirm 兜底应存在');
assert.ok(pageCfg.onConfirm.toString().indexOf('_send') !== -1, 'onConfirm 应走 _send（与发送钮同链）');
assert.ok(pageCfg && typeof pageCfg.sendMessage === 'function', '发送钮入口 sendMessage 应存在');
assert.ok(pageCfg.sendMessage.toString().indexOf('_send') !== -1, 'sendMessage 应走 _send（发送统一在条上）');
