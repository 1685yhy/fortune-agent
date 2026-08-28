// 易理明灯 E2-2 — 对话卡片解析与流式规则的 node 单测
// 运行：node --test miniprogram/tests/card.test.js
// 覆盖（brief §测试与验证）：完整标记 / 无标记 / 未闭合流式 / 标题缺省 /
//   正文含表格 / 尾部引导语拆分 / [/card] 缺失中断，另附转义标题 / 流式重发重复前缀 /
//   类型枚举契约 / 未知类型 / 剥标记纯文本。
const test = require('node:test');
const assert = require('node:assert/strict');
const card = require('../utils/card');
const md = require('../utils/md');

const { parseCard, buildCardView, stripCardMarkers } = card;
const view = (text, opts) => buildCardView(text, opts || {}, md.parseMd);

/* ── parseCard：完整标记 ── */
test('完整 paipan 标记：type/title/body/tail 解析', () => {
  const text = '[card:paipan title="我的命盘"]\n## 八字\n日主甲木，身强。\n[/card]\n\n💬 还想了解：事业运势';
  const r = parseCard(text);
  assert.equal(r.pending, false);
  assert.deepEqual(r.card, { type: 'paipan', title: '我的命盘', body: '## 八字\n日主甲木，身强。' });
  assert.equal(r.prefix, '');
  assert.equal(r.tail, '💬 还想了解：事业运势');
});

test('正文含表格：body 原样保留（md 可解析为 table 节点）', () => {
  const body = '| 大运 | 起止 |\n| --- | --- |\n| 甲辰 | 2026-2035 |';
  const text = '[card:yunshi title="运势分析"]\n' + body + '\n[/card]';
  const r = parseCard(text);
  assert.equal(r.pending, false);
  assert.equal(r.card.body, body);
  const nodes = md.parseMd(r.card.body);
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].t, 'table');
  assert.equal(nodes[0].headers.length, 2);
  assert.equal(nodes[0].rows.length, 1);
});

test('闭合卡片的 buildCardView：定格 + 标题条 + 卡外 tail 独立渲染', () => {
  const text = '[card:zeri title="择吉结果"]\n宜：出行\n[/card]\n\n💬 还想了解：婚嫁吉日';
  const v = view(text, { streaming: false, error: false });
  assert.ok(v.card);
  assert.equal(v.card.type, 'zeri');
  assert.equal(v.cardFinal, true);
  assert.equal(v.cardTitle, '择吉结果');
  assert.equal(v.cardTypeLabel, '择吉');
  assert.equal(v.cardNodes.length, 1);          // 正文 1 段
  assert.ok(v.cardTailNodes && v.cardTailNodes.length === 1);  // 尾部独立渲染
  assert.equal(v.mdNodes, null);                // 卡片模式不走普通 mdNodes
});

/* ── parseCard：无标记 ── */
test('无标记普通文本：card=null 且保持原样（现状零回归）', () => {
  const r = parseCard('今天天气不错，财运也很好。');
  assert.equal(r.card, null);
  assert.equal(r.pending, false);
  assert.equal(r.prefix, '');
  assert.equal(r.tail, '');
  const v = view('今天天气不错，财运也很好。', { streaming: false, error: false });
  assert.equal(v.card, null);
  assert.equal(v.mdNodes, null);   // 调用方维持 md.parseMd 原渲染
});

test('文本含 [card: 字样但非合法标签（未知类型）→ 不当作卡片', () => {
  const r = parseCard('据说可以 [card:foo title="x"] 这样用');
  assert.equal(r.card, null);
  assert.equal(r.pending, false);
});

/* ── 未闭合流式（brief 关键）── */
test('未闭合 + 流式中 → 卡片壳模式：壳标题 + 增量正文（标记行后已收到部分）', () => {
  const text = '[card:paipan title="我的命盘"]\n## 八字\n日主甲';
  const r = parseCard(text);
  assert.equal(r.pending, true);
  assert.equal(r.card.type, 'paipan');
  assert.equal(r.card.body, '## 八字\n日主甲');   // 增量部分
  const v = view(text, { streaming: true, error: false });
  assert.ok(v.card);
  assert.equal(v.cardFinal, false);               // 壳骨架未定格（无收藏按钮）
  assert.equal(v.cardTitle, '我的命盘');
  assert.equal(v.cardTypeLabel, '命盘');
  assert.equal(v.cardNodes.length, 2);            // 标题段 + 段落段（md 增量解析）
  assert.equal(v.mdNodes, null);
});

test('未闭合 + 标签行都不完整（流式刚开头）→ 卡片壳 + 类型默认标题', () => {
  const text = '[card:data title="档案';
  const r = parseCard(text);
  assert.equal(r.pending, true);
  assert.equal(r.card.type, 'data');
  assert.equal(r.card.title, '');                 // 标题属性尚未收全
  const v = view(text, { streaming: true, error: false });
  assert.equal(v.cardTitle, '档案查阅');          // 类型默认标题回退
  assert.equal(v.cardNodes.length, 0);            // 正文尚未到达
});

test('未闭合 + [/card] 缺失中断（error）→ 降级纯文本：标记不暴露', () => {
  // 形态 A：标记在开头被截断
  const a = '[card:yunshi title="运势分析"]\n今年财运稳中有升，注意';
  const va = view(a, { streaming: false, error: true });
  assert.equal(va.card, null);
  assert.ok(va.mdNodes && va.mdNodes.length === 1);
  // 降级文本 = 剥掉标记行后的正文（节点文本不含 [card:）
  const flat = va.mdNodes.map((n) => JSON.stringify(n)).join('');
  assert.ok(flat.indexOf('[card:') === -1);
  // 形态 B：标记在尾部被截断（LLM 流式路径：正文先流出，收尾重发被中断）
  const b = '今年财运稳中有升，注意节流。\n[card:yunshi title="运势分析"]\n今年财运';
  const vb = view(b, { streaming: false, error: true });
  assert.equal(vb.card, null);
  assert.ok(vb.mdNodes.length >= 1);              // 显示标记前已流出的正文
  const flatB = vb.mdNodes.map((n) => JSON.stringify(n)).join('');
  assert.ok(flatB.indexOf('[card:') === -1);
});

test('未闭合 + 用户停止（非 error）→ 标记前正文降级纯文本', () => {
  const text = '## 今日财运\n稳步上升。\n[card:yunshi title="运';
  const v = view(text, { streaming: false, error: false });
  assert.equal(v.card, null);                     // 卡片不完整 → 不渲染壳
  const flat = v.mdNodes.map((n) => JSON.stringify(n)).join('');
  assert.ok(flat.indexOf('[card:') === -1);
  assert.ok(flat.indexOf('今日财运') !== -1);
});

test('未闭合 + 正常结束（服务端输出完整但流被截断理论场景）→ 定格为卡片壳', () => {
  const text = '[card:knowledge title="命理知识"]\n天干地支，十神生克';
  const v = view(text, { streaming: false, error: false });
  assert.ok(v.card);
  assert.equal(v.cardFinal, true);
  assert.equal(v.cardTitle, '命理知识');
});

/* ── 标题缺省 ── */
test('标题缺省：data/knowledge 无 title 属性 → 类型默认标题', () => {
  const r = parseCard('[card:data]\n你的档案里有 3 份排盘记录\n[/card]');
  assert.deepEqual(r.card, { type: 'data', title: '', body: '你的档案里有 3 份排盘记录' });
  const v = view(r.raw, { streaming: false, error: false });
  assert.equal(v.cardTitle, '档案查阅');
  const vk = view('[card:knowledge]\n关于八字命理的基本常识\n[/card]', {});
  assert.equal(vk.cardTitle, '命理知识');
});

test('标题转义还原：\\" \\\\ \\n', () => {
  const r = parseCard('[card:paipan title="我的\\"命盘\\" \\\\ 甲\\\\乙"]\n正文\n[/card]');
  assert.equal(r.card.title, '我的"命盘" \\ 甲\\乙');
  const rn = parseCard('[card:yunshi title="运势\\n明日"]\n正文\n[/card]');
  assert.equal(rn.card.title, '运势\n明日');
});

/* ── 尾部引导语拆分 ── */
test('尾部引导语（还想了解/反馈语/页脚）拆到卡片外', () => {
  const t1 = '[card:yunshi title="运势分析"]\n正财旺。\n[/card]\n\n💬 还想了解：感情';
  const r1 = parseCard(t1);
  assert.equal(r1.card.body, '正财旺。');
  assert.equal(r1.tail, '💬 还想了解：感情');
  const t2 = '[card:paipan title="我的命盘"]\n日主甲木\n[/card]\n\n———\n这个分析对你有帮助吗？可回复「准」或「不准」';
  const r2 = parseCard(t2);
  assert.equal(r2.card.body, '日主甲木');
  assert.equal(r2.tail, '———\n这个分析对你有帮助吗？可回复「准」或「不准」');
});

/* ── 流式重发重复前缀（LLM 流式路径的最终形态）── */
test('闭合 + 标记前文本是正文开头（流式重发重复）→ 丢弃重复前缀，干净定格', () => {
  const body = '## 今日财运\n稳步上升，适合加仓。';
  const text = body + '\n[card:yunshi title="运势分析"]\n' + body + '\n[/card]';
  const v = view(text, { streaming: false, error: false });
  assert.ok(v.card);
  assert.equal(v.cardFinal, true);
  assert.equal(v.cardPrefixNodes, null);   // 重复前缀被丢弃
  assert.equal(v.cardNodes.length, 2);     // 标题段 + 段落段（md 解析）
});

test('闭合 + 标记前文本非正文开头（草稿场景）→ 前置内容保留在卡片上方', () => {
  const text = '先给你个简版。\n[card:yunshi title="运势分析"]\n详细分析如下\n[/card]';
  const v = view(text, {});
  assert.ok(v.card);
  assert.ok(v.cardPrefixNodes && v.cardPrefixNodes.length >= 1);
});

/* ── 多卡/第一个块 ── */
test('提取第一个 [card: 块；尾随文本全部归 tail', () => {
  const r = parseCard('[card:paipan title="我的命盘"]\n甲\n[/card]\n\n[card:data]\n乙\n[/card]');
  assert.equal(r.pending, false);
  assert.equal(r.card.type, 'paipan');
  assert.equal(r.card.body, '甲');
  assert.ok(r.tail.indexOf('[card:data]') === 0);   // 第二个块作为卡外文本保留
});

/* ── 类型枚举契约 ── */
test('类型枚举：五种基础类型均可解析', () => {
  ['paipan', 'yunshi', 'zeri', 'data', 'knowledge'].forEach((t) => {
    const r = parseCard('[card:' + t + ']\n正文\n[/card]');
    assert.equal(r.card.type, t);
  });
});

/* ── 批次 2 P1：6 类引擎结果卡片 ── */
test('类型枚举：6 类引擎类型可解析 + 标签/默认标题回退', () => {
  assert.deepEqual(card.CARD_TYPES, [
    'paipan', 'yunshi', 'zeri', 'data', 'knowledge',
    'ziwei', 'liuyao', 'fengshui', 'mianxiang', 'qimen', 'dream',
  ]);
  const cases = [
    ['ziwei', '紫微', '紫微命盘'],
    ['liuyao', '六爻', '六爻卦象'],
    ['fengshui', '风水', '风水分析'],
    ['mianxiang', '面相', '面相分析'],
    ['qimen', '奇门', '奇门遁甲局'],
    ['dream', '解梦', '解梦结果'],
  ];
  cases.forEach(([t, label, title]) => {
    const r = parseCard('[card:' + t + ']\n正文\n[/card]');
    assert.equal(r.card.type, t);
    const v = view('[card:' + t + ']\n正文\n[/card]', {});
    assert.equal(v.cardTypeLabel, label);
    assert.equal(v.cardTitle, title);          // 缺省标题 → 端上默认
    assert.equal(v.cardNodes.length, 1);
  });
  // 显式 title 优先于默认标题
  const v2 = view('[card:qimen title="今日局"]\n正文\n[/card]', {});
  assert.equal(v2.cardTitle, '今日局');
  // 流式卡片壳同样支持新类型
  const v3 = view('[card:dream title="解梦"]\n正在解读…', { streaming: true });
  assert.equal(v3.card.type, 'dream');
  assert.equal(v3.cardFinal, false);
});

/* ── 剥标记纯文本（TTS/复制/分享出口）── */
test('stripCardMarkers：开头/尾部/紧贴正文的标记全部剥离', () => {
  assert.equal(stripCardMarkers('[card:paipan title="我的命盘"]\n日主甲木\n[/card]'), '日主甲木');
  assert.equal(
    stripCardMarkers('今年财运稳中有升\n[card:yunshi title="运势分析"]\n今年财运稳中有升\n[/card]'),
    '今年财运稳中有升\n今年财运稳中有升');   // 剥离不负责去重（去重在 buildCardView）
  assert.equal(stripCardMarkers('普通文本，无标记'), '普通文本，无标记');
  assert.equal(stripCardMarkers(''), '');
});

/* ── 空文本/空 body 防御 ── */
test('空文本 → 非卡片；空 body 卡片 → 壳可渲染', () => {
  assert.equal(parseCard('').card, null);
  const r = parseCard('[card:data title="档案"]\n[/card]');
  assert.equal(r.pending, false);
  assert.equal(r.card.body, '');
  const v = view(r.raw, {});
  assert.ok(v.card);
  assert.equal(v.cardNodes.length, 0);
});

/* ═══ E2-2-FIX：复审 2 项 Important 回归 ═══ */

/* I-1：长按菜单「朗读」（chat.js actItem k==='speak'）与气泡直接朗读同口径，
   先 stripCardMarkers 再交 TTS —— 朗读文本不得含 [card: 字样 */
test('E2-2-FIX I-1：朗读出口剥标记——TTS 文本不含 [card: 字样，正文/引导语保留', () => {
  const content = '[card:paipan title="我的命盘"]\n日主甲木，身强。\n[/card]\n\n💬 还想了解：事业运势';
  const ttsText = stripCardMarkers(content);   // actItem speak 路径同款
  assert.ok(ttsText.indexOf('[card:') === -1);
  assert.ok(ttsText.indexOf('[/card]') === -1);
  assert.ok(ttsText.indexOf('日主甲木') !== -1);    // 正文保留可朗读
  assert.ok(ttsText.indexOf('还想了解') !== -1);    // 卡外引导语照常朗读
});

/* I-2：_segCache 缓存键必须含 streaming/error——同一 content 下标志变更视图必切换
   （chat.js _mirror 单测，直接测页面内缓存逻辑：流式中断 → 降级纯文本而非过期卡片壳） */
test('E2-2-FIX I-2：_mirror 缓存含流式标志——中断（content 不变仅标志变更）→ 降级纯文本', () => {
  const savedPage = global.Page;
  let pageCfg = null;
  global.Page = (c) => { pageCfg = c; };
  try {
    require('../pages/chat/chat');
  } finally {
    global.Page = savedPage;
  }
  assert.ok(pageCfg && typeof pageCfg._mirror === 'function', 'chat.js 页面配置应可加载');
  const ctx = {
    data: { reactions: {} },
    _segCache: null,
    _thinkView: pageCfg._thinkView,
    _mirror: pageCfg._mirror,
  };
  const content = '[card:paipan title="我的命盘"]\n## 八字\n日主甲';   // 未闭合（引擎路径：标记开头）
  const mk = (streaming, error) => [{
    id: 'm1', role: 'ai', content, streaming, error,
    thinking: [], thinkSeconds: 0, thinkCollapsed: false,
  }];
  // 1) 流式中 → 卡片壳（未定格）
  const shell = ctx._mirror(mk(true, false))[0];
  assert.ok(shell.card);
  assert.equal(shell.cardFinal, false);
  // 2) 同 id+content 中断（_onError：streaming 停止 + error）→ 必须降级纯文本而非卡片壳
  //    （修复前缓存键仅 id+content → 命中过期卡片壳，本条即回归断言）
  const degraded = ctx._mirror(mk(false, true))[0];
  assert.equal(degraded.card, null);            // 不再是卡片壳
  assert.ok(Array.isArray(degraded.mdNodes) && degraded.mdNodes.length >= 1);
  const flat = JSON.stringify(degraded.mdNodes);
  assert.ok(flat.indexOf('[card:') === -1);     // 标记不暴露
  // 3) 同 id+content 用户停止（_onAbort：非 error）→ 定格卡片壳（final:true），
  //    同样不得停留在流式「未定格」壳
  const stopped = ctx._mirror(mk(false, false))[0];
  assert.ok(stopped.card);
  assert.equal(stopped.cardFinal, true);
});

/* ═══ E2-2 补漏（批次 2 B3-22）：未闭合标签缺 ] 剥不掉 ═══
   流式截断/长标题截断 → `[card:type title="…` 缺闭合 ] → 旧实现两个 replace 都
   要求闭合 ] → 半截标签原样残留进 TTS/复制/分享。追加行尾兜底。 */

test('B3-22：缺 ] 的半截标签在串尾 → 剥掉（修复前残留）', () => {
  assert.equal(stripCardMarkers('正文[card:paipan title="我的命盘"'), '正文');
  assert.equal(stripCardMarkers('[card:paipan title="我的命盘"'), '');
  assert.equal(stripCardMarkers('正文[card:paipan title="我的命'), '正文');
  assert.equal(stripCardMarkers('[card:paipan'), '');
});

test('B3-22：半截标签行 + 后随正文行 → 只剥标签行，正文行保留', () => {
  assert.equal(
    stripCardMarkers('[card:paipan title="我的命盘"\n你是庚金日主。'),
    '你是庚金日主。');
});

test('B3-22：完整卡 + 尾部半截卡 → 完整卡剥净、半截剥掉、正文保留', () => {
  const s = stripCardMarkers('[card:data]\n资料一\n[/card]\n\n尾部[card:paipan title="我的命');
  assert.equal(s, '资料一\n\n尾部');
  assert.ok(s.indexOf('[card:') === -1 && s.indexOf('[/card]') === -1);
});

test('B3-22：完整标记剥离零回归（旧行为不变）', () => {
  assert.equal(
    stripCardMarkers('[card:paipan title="我的命盘"]\n日主甲木，身强。\n[/card]\n\n💬 还想了解：事业运势'),
    '日主甲木，身强。\n\n💬 还想了解：事业运势');
  assert.equal(stripCardMarkers('普通文本，无标记'), '普通文本，无标记');
  assert.equal(stripCardMarkers(''), '');
});

/* ═══ B3-2-B：流式与卡片重叠去重加强（用户问题：文字+卡片重复两份）═══
   根因：服务端流式文本（引导句/润色前）与最终卡片正文不完全一致时，
   旧去重只认「正文以 prefix 开头」→ 失配 → 前缀+卡片同时渲染 = 两份。
   加强：prefix 整段是正文子串 → 全丢；prefix 尾部最长后缀与正文开头重叠
   （≥ 4 字符）→ 只渲染未重叠部分；完全不重叠 → 现状保留（不丢内容）。 */

test('B3-2-B：引导句 + 流式正文部分重叠 → 只渲染引导句，重复正文不渲染', () => {
  const body = '## 今日财运\n稳步上升，适合加仓。';
  // 流式输出 = 引导句 + 正文（最终回复把正文包进卡片并追加补充说明）
  const text = '好的，这是你的命盘。\n' + body
    + '\n[card:yunshi title="运势分析"]\n' + body + '\n\n补充说明\n[/card]';
  const v = view(text, { streaming: false, error: false });
  assert.ok(v.card);
  const flat = JSON.stringify(v.cardPrefixNodes || []);
  assert.ok(flat.indexOf('好的，这是你的命盘') !== -1);   // 引导句保留
  assert.ok(flat.indexOf('稳步上升') === -1);             // 重复正文丢弃
});

test('B3-2-B：流式正文是卡片正文子串（非开头）→ 整段丢弃，卡片上方无残留', () => {
  const body = '## 今日财运\n稳步上升，适合加仓。';
  const text = body + '\n[card:yunshi title="运势分析"]\n详细分析如下：\n'
    + body + '\n[/card]';
  const v = view(text, {});
  assert.ok(v.card);
  assert.equal(v.cardPrefixNodes, null);   // 前缀整段已在正文中出现 → 全丢
});

test('B3-2-B：短于阈值的巧合重叠不去重（防误伤，前置保留）', () => {
  const text = '今天天气很好。\n[card:yunshi title="运势分析"]\n很好，今天适合出行\n[/card]';
  const v = view(text, {});
  assert.ok(v.card);
  assert.ok(v.cardPrefixNodes && v.cardPrefixNodes.length >= 1);
});

/* ═══ E2-2 补漏（批次 2 B3-23）：折叠代码块空行 ═══
   旧 \n{3,}→\n\n 无差别折叠会破坏代码围栏内空行（md.js 只认 ``` 围栏）。 */

test('B3-23：围栏外连续空行折叠为 1 行（语义与旧 \n{3,}→\n\n 一致）', () => {
  assert.equal(stripCardMarkers('a\n\n\n\nb'), 'a\n\nb');
  assert.equal(stripCardMarkers('a\n\nb'), 'a\n\nb');   // 单空行不动
  assert.equal(stripCardMarkers('a\n\n\n\n\nb'), 'a\n\nb');
});

test('B3-23：代码围栏内空行原样保留（修复前被折叠破坏）', () => {
  const s = stripCardMarkers('```js\na\n\n\n\nb\n```');
  assert.equal(s, '```js\na\n\n\n\nb\n```');
});

test('B3-23：围栏内空行保留 + 围栏外空行折叠 + 卡片标记剥离 三者并存', () => {
  const s = stripCardMarkers('[card:data]\n```\nx\n\n\n\n```\n\n\n\n尾部\n[/card]');
  assert.equal(s, '```\nx\n\n\n\n```\n\n尾部');
});

test('B3-23：未闭合围栏（单 ```）→ 其后的空行按围栏内保留（同 md.js 语义）', () => {
  const s = stripCardMarkers('```js\na\n\n\nb');
  assert.equal(s, '```js\na\n\n\nb');
});
