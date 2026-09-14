// 易理明灯 — k48 P0.5 回归：一个气泡两张命盘（done 定稿全文整泡替换）
// 运行：cd miniprogram && node --test tests/k48_bubble_replace.test.js
//
// 背景（用户实机 2026-09-14，session s_mtme8nc9afzo）：
//   流式已把**润色版**推给前端；后端 D2 数据一致性终审把整条回复换成**引擎
//   原稿**（两条文本完全不同、无重叠）。服务端"找重叠、只补未发部分"无处可
//   补 → 若前端按"追加"理解，气泡里 = 润色版 + 引擎原稿（两张命盘）。
//
// 契约（后端 chat_stream done 载荷已携带定稿全文 `content`，与落库同文）：
//   1. done 带 content → **整泡替换**（气泡 = 定稿全文，不拼接、不重复）；
//   2. done 不带 content（早退分支无定稿）→ 保留已流出内容，绝不清空气泡；
//   3. 正常续写（流式正文 == 定稿）→ 替换后内容不变（幂等，不重复投递）；
//   4. 空回复兜底文案仍生效。
const test = require('node:test');
const assert = require('node:assert/strict');

/* ── wx 桩（streamHost._save 走 wx.setStorageSync） ── */
const store = {};
global.wx = {
  setStorageSync: (k, v) => { store[k] = v; },
  getStorageSync: (k) => (k in store ? store[k] : ''),
  removeStorageSync: (k) => { delete store[k]; },
};
global.getApp = () => ({ globalData: {}, loginPromise: null });

const streamHost = require('../utils/streamHost');

// 用户实机两条稿的形态：润色版 vs 引擎原稿（文本完全不同）
const POLISHED = '己卯 己巳 乙丑 壬午。你的命盘四柱齐整，日主乙木生于巳月，'
  + '食伤当令而思虑细腻，做事讲究条理，追求可见的成效。';
const ENGINE_DRAFT = '根据您的出生信息：1999年4月5日 北京。四柱为 己卯 丁卯 丁亥 乙巳。'
  + '日主丁火生于卯月，印星当令，性格温润而有主见，为人重情重义。';

/* 造一条"正在流式"的 AI 消息现场，然后喂 done 事件 */
function mountStreaming(streamedText) {
  streamHost.reset([]);
  streamHost.messages = [
    { id: 'u1', role: 'user', content: '我的命盘怎么样', time: 1 },
    { id: 'a1', role: 'assistant', content: streamedText, streaming: true, time: 2 },
  ];
  streamHost.msgId = 'a1';
  streamHost.streaming = true;
  streamHost.sse = null;
  return () => streamHost.messages.find((m) => m.id === 'a1').content;
}

test('k48 P0.5：done 携带定稿全文 → 整泡替换（气泡不再是 润色版+引擎原稿）', () => {
  const read = mountStreaming(POLISHED);
  streamHost._onSseEvent({ type: 'done', consultation_id: 7, citations: [],
                           suggestions: [], content: ENGINE_DRAFT });
  const content = read();
  assert.equal(content, ENGINE_DRAFT, '气泡内容应等于定稿全文');
  assert.ok(!content.includes(POLISHED.slice(0, 10)),
            '旧稿绝不残留在气泡里（两张盘根因）');
  assert.equal(content.includes(ENGINE_DRAFT) && content.includes(POLISHED.slice(0, 20)),
               false, '不得拼接成 润色版+引擎原稿');
});

test('k48 P0.5：正常续写（定稿 == 已流正文）→ 替换幂等，不重复投递', () => {
  const body = '今天运势整体不错，宜积极行动。忌冲动消费。';
  const read = mountStreaming(body);
  streamHost._onSseEvent({ type: 'done', content: body });
  assert.equal(read(), body, '内容一字不变、不重复');
});

test('k48 P0.5：done 不带 content（早退分支）→ 保留已流内容，绝不清空', () => {
  const read = mountStreaming(POLISHED);
  streamHost._onSseEvent({ type: 'done', consultation_id: null });
  assert.equal(read(), POLISHED, '无定稿时保留已流出内容');
});

test('k48 P0.5：空回复兜底文案仍生效', () => {
  const read = mountStreaming('');
  streamHost._onSseEvent({ type: 'done', content: '' });
  assert.equal(read(), '我走神了，你再说一遍？');
});

test('k48 P0.5：chunk 之后 done 替换 —— 整轮形态（已流润色版 → 定稿引擎稿）', () => {
  const read = mountStreaming('');
  streamHost._onSseEvent({ type: 'chunk', content: POLISHED });
  streamHost._onSseEvent({ type: 'done', content: ENGINE_DRAFT });
  assert.equal(read(), ENGINE_DRAFT);
  assert.ok(!read().includes(POLISHED.slice(0, 12)));
});
