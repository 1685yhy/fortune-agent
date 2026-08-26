// 易理明灯 — 历史页预览展示层剥卡片标记（批次 2 B3-24）
// 运行：node --test miniprogram/tests/history.test.js
// 覆盖：onRowTap 预览映射 content 为展示出口 → [card:…]/[/card] 剥净（含缺 ] 半截
//       标签）；纯展示映射：storage/源消息原样保留（续聊写回走 _rawMsgs 不受影响）。
const test = require('node:test');
const assert = require('node:assert/strict');

const savedPage = global.Page;
let pageCfg = null;
global.Page = (c) => { pageCfg = c; };
try {
  require('../pages/history/history');
} finally {
  global.Page = savedPage;
}
assert.ok(pageCfg && typeof pageCfg.onRowTap === 'function', 'history.js 页面配置应可加载');

function makePage() {
  const page = Object.assign({}, pageCfg);
  page.data = { groups: [] };
  page.setData = function (upd) { Object.assign(this.data, upd); };
  return page;
}

test('历史页预览（onRowTap）：AI 回复含卡片标记 → 展示文本剥净', () => {
  const page = makePage();
  page.data.groups = [{
    items: [{
      id: 's1',
      _msgs: [
        { role: 'user', content: '看看我的运势', time: '10:00' },
        { role: 'ai', content: '[card:yunshi title="运势分析"]\n日主甲木，财运稳中有升。\n[/card]\n\n💬 还想了解：事业', time: '10:01' },
      ],
    }],
  }];
  page.onRowTap({ currentTarget: { dataset: { id: 's1' } } });
  const msgs = page.data.current.msgs;
  assert.equal(msgs.length, 2);
  assert.equal(msgs[0].content, '看看我的运势');               // 用户消息不受影响
  assert.ok(msgs[1].content.indexOf('[card:') === -1);          // 标记不暴露
  assert.ok(msgs[1].content.indexOf('[/card]') === -1);
  assert.ok(msgs[1].content.indexOf('日主甲木') !== -1);        // 正文保留
  assert.ok(msgs[1].content.indexOf('还想了解') !== -1);        // 卡外引导语保留
});

test('历史页预览：缺 ] 半截标签也剥净，源消息原样保留（纯展示映射）', () => {
  const page = makePage();
  const raw = '正文[card:paipan title="我的命盘"';   // 截断残缺标签
  page.data.groups = [{
    items: [{ id: 's2', _msgs: [{ role: 'ai', content: raw, time: '10:02' }] }],
  }];
  page.onRowTap({ currentTarget: { dataset: { id: 's2' } } });
  assert.equal(page.data.current.msgs[0].content, '正文');
  // 源消息未被动过（B3-24 仅展示映射，续聊写回/收藏导出不受影响）
  assert.equal(page.data.groups[0].items[0]._msgs[0].content, raw);
});
