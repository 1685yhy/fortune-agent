// scripts/test_streamhost.js
// 单元测试：StreamHost._flushAccum —— 流式正文尾块落在 50ms flush 窗口内时，
// 收尾路径（done/abort/error）必须冲刷 chunkAccum，保证最后 chunk 不丢。
// 背景：此前收尾时 _clearFlush 直接把 chunkAccum 连同消息尾部一起清空，
// 若 done 紧跟最后一个 chunk 到达（<50ms，flush 定时器未触发），尾块会被静默丢弃。
// 运行：node scripts/test_streamhost.js （PASS/FAIL，失败 exit 1）
'use strict';

const Module = require('module');
const path = require('path');
const origLoad = Module._load;

// ── mock wx 全局（StreamHost._save 落盘；node 环境无 wx）──
global.wx = { setStorageSync: () => {} };

// ── 拦截 streamHost.js 顶层 require('./api')：不加载真实 api.js（其依赖 wx.request 等）──
Module._load = function (request, parent, isMain) {
  if (request === './api' && parent && /streamHost\.js$/.test(parent.filename)) {
    return { chatStream: async () => ({ abort() {} }), chat: async () => ({}) };
  }
  return origLoad.apply(this, arguments);
};

const StreamHost = require(path.resolve(__dirname, '../miniprogram/utils/streamHost.js')).constructor;

let ok = 0;
let fail = 0;
function check(name, cond) {
  if (cond) { ok++; console.log('PASS:', name); }
  else { fail++; console.log('FAIL:', name); }
}

// 模拟已开始的流：AI 消息在场、msgId 指向它、streaming 中
function newHost() {
  const h = new StreamHost();
  h.messages = [{ id: 'a1', role: 'ai', content: '', thinking: [], streaming: true }];
  h.msgId = 'a1';
  h.streaming = true;
  h.typing = true;
  return h;
}

(async () => {
  // ── 场景 1：chunk 落在 flush 窗口内，done 同一同步块紧跟（50ms 定时器未触发）──
  const h1 = newHost();
  h1._onSseEvent({ type: 'chunk', content: '第一段' });
  h1._onSseEvent({ type: 'chunk', content: '，第二段' });
  h1._onSseEvent({ type: 'done', consultation_id: 7, citations: [{ book: 'X' }], suggestions: ['再问'] });
  check('场景1 done 后消息完整（最后 chunk 不丢）', h1._find('a1').content === '第一段，第二段');
  check('场景1 done 后 chunkAccum 已冲刷', h1.chunkAccum === '');
  check('场景1 消息收尾状态', h1._find('a1').streaming === false && h1._find('a1').consultationId === 7);
  // flush 定时器已被 _clearFlush 清除：等 80ms（>FLUSH_MS）不应重复追加
  await new Promise((r) => setTimeout(r, 80));
  check('场景1 无重复追加（flush 定时器已清除）', h1._find('a1').content === '第一段，第二段');

  // ── 场景 2：真实 SSE 字节流（SseParser），chunk 断行跨块 + done 收尾 ──
  const h2 = newHost();
  const utf8 = (s) => new Uint8Array(Buffer.from(s, 'utf8'));
  h2._onChunkRaw({ data: utf8('data: {"type":"chunk","content":"尾') });   // 断行 → 留行缓冲
  h2._onChunkRaw({ data: utf8('块"}\n\n') });                               // 行补齐 → chunk 事件
  h2._onChunkRaw({ data: utf8('data: {"type":"done","consultation_id":9}\n\n') });
  check('场景2 字节流尾块不丢', h2._find('a1').content === '尾块');
  check('场景2 chunkAccum 已冲刷', h2.chunkAccum === '');

  // ── 场景 3：abort 收尾同样冲刷（用户点停止不丢已流出的尾巴）──
  const h3 = newHost();
  h3._onSseEvent({ type: 'chunk', content: '已输出内容' });
  h3._onAbort();
  check('场景3 abort 后已输出内容保留', h3._find('a1').content === '已输出内容');
  check('场景3 chunkAccum 已冲刷', h3.chunkAccum === '');

  Module._load = origLoad;
  console.log(fail ? `\nFAIL (${fail} failed, ${ok} passed)` : `\nALL PASS (${ok})`);
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  console.error('TEST CRASH:', e);
  process.exit(1);
});
