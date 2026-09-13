// 易理明灯 — k47-A 行内节点扁平化（WXML 模板自递归修复）
// 运行：cd miniprogram && node --test tests/k47_md_flat.test.js
//
// 背景（开发者工具实测 34 页 28 条）：
//   WXMLRT_$gwx: Template `./pages/chat/chat.wxml:md-inline` is being called recursively, will be stop.
//   微信模板引擎不支持模板自递归 → 旧协议（strong/em/link 为「容器 + children」，模板内再调
//   md-inline 渲染内层）运行时被中止 → **加粗/斜体/链接内部文字不渲染**。
// 修法：递归移到 JS 解析侧，产出**一层扁平叶子节点**（每节点带 cls 样式标记），
//   模板层单层遍历、零自递归；嵌套内容完整上屏，样式类/交互（copyCode / cite chip /
//   段落长按）不变。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const md = require('../utils/md');
const chatSelect = require('../utils/chatSelect');

/* 扁平协议不变量：所有行内节点必须是叶子（无 children）——模板层因此无需递归 */
function assertFlat(nodes, label) {
  (Array.isArray(nodes) ? nodes : []).forEach((n, i) => {
    assert.ok(!n.children, `${label}: 节点 #${i} (t=${n.t}) 不得再有 children（模板层零递归）`);
    if (n.t === 'text' || n.t === 'link' || n.t === 'code') {
      assert.equal(typeof n.s, 'string', `${label}: t=${n.t} 必须自带 s 文本`);
    }
  });
}

test('扁平：单层 text / strong / em / code / link / cite 全部为叶子且带 cls', () => {
  const nodes = md.parseInline('仅**加粗**与*斜体*与`code`与[链](https://a.cn)与[3]。');
  assertFlat(nodes, '单层');
  assert.deepEqual(nodes.map((n) => [n.t, n.s != null ? n.s : n.idx, n.cls || '']), [
    ['text', '仅', ''],
    ['text', '加粗', 'md-strong'],
    ['text', '与', ''],
    ['text', '斜体', 'md-em'],
    ['text', '与', ''],
    ['code', 'code', ''],
    ['text', '与', ''],
    ['link', '链', ''],
    ['text', '与', ''],
    ['cite', 3, ''],
    ['text', '。', ''],
  ]);
  const link = nodes.find((n) => n.t === 'link');
  assert.equal(link.url, 'https://a.cn');
});

test('扁平：加粗里嵌链接（brief 用例 **加粗里的[链接](url)**）→ 链接文字完整且带双标记', () => {
  const nodes = md.parseInline('**加粗里的[链接](https://e.cn)文字**');
  assertFlat(nodes, '粗内链');
  assert.deepEqual(nodes.map((n) => [n.t, n.s, n.cls, n.url || '']), [
    ['text', '加粗里的', 'md-strong', ''],
    ['link', '链接', 'md-strong', 'https://e.cn'],
    ['text', '文字', 'md-strong', ''],
  ]);
});

test('扁平：粗中带斜（brief 用例 **粗中*带斜*粗**）→ 三段样式各自正确且内容都在', () => {
  const nodes = md.parseInline('**粗中*带斜*粗**');
  assertFlat(nodes, '粗中带斜');
  assert.deepEqual(nodes.map((n) => [n.s, n.cls]), [
    ['粗中', 'md-strong'],
    ['带斜', 'md-strong md-em'],
    ['粗', 'md-strong'],
  ]);
  assert.equal(chatSelect.paragraphModel({ role: 'ai', content: '**粗中*带斜*粗**', mdNodes: md.parseMd('**粗中*带斜*粗**') }).text, '粗中带斜粗');
});

test('扁平：链接内嵌样式 / 多段叠加 / 三连星 ***粗斜***', () => {
  // 扫描器语义与旧实现一致（* 与 ** 同源，`*a **b** c*` 这类「斜里嵌粗」仍按单星切分），
  // 但**内容不再丢失**——旧实现下这些文字全被递归中止吞掉
  assert.deepEqual(md.parseInline('*斜里**粗**斜*').map((n) => [n.s, n.cls]), [
    ['斜里', 'md-em'], ['粗', 'md-em'], ['斜', 'md-em'],
  ]);
  assert.deepEqual(md.parseInline('**粗[链*斜*](https://x.cn)**').map((n) => [n.t, n.s, n.cls]), [
    ['text', '粗', 'md-strong'], ['link', '链', 'md-strong'], ['link', '斜', 'md-strong md-em'],
  ]);
  assert.deepEqual(md.parseInline('***粗斜***').map((n) => [n.s, n.cls]), [['粗斜', 'md-strong md-em']]);
  // brief 点名用例：链接里带粗（[链**粗**](url)）——链接文字完整且带双标记
  assert.deepEqual(md.parseInline('[链**粗**](https://u.cn)').map((n) => [n.t, n.s, n.cls]),
    [['link', '链', ''], ['link', '粗', 'md-strong']]);
});

test('扁平：行内码/引用角标在样式内 → 仍是独立叶子且样式不丢', () => {
  assert.deepEqual(md.parseInline('**`code`**').map((n) => [n.t, n.s, n.cls]), [['code', 'code', 'md-strong']]);
  assert.deepEqual(md.parseInline('**[2]**').map((n) => [n.t, n.idx]), [['cite', 2]]);
  const img = md.parseInline('![图](https://i.cn)**粗**');
  assert.deepEqual(img.map((n) => [n.t, n.s || n.url, n.cls]), [['image', 'https://i.cn', undefined], ['text', '粗', 'md-strong']]);
});

test('扁平：未闭合标记保持宽容（流式半截文本原样显示，不丢字）', () => {
  assert.deepEqual(md.parseInline('**未闭合').map((n) => [n.t, n.s, n.cls]), [['text', '**未闭合', '']]);
  assert.deepEqual(md.parseInline('*未闭合').map((n) => [n.t, n.s]), [['text', '*未闭合']]);
  assert.deepEqual(md.parseInline('`未闭合').map((n) => [n.t, n.s]), [['text', '`未闭合']]);
  assert.deepEqual(md.parseInline('***未闭合').map((n) => [n.t, n.s]), [['text', '***未闭合']]);
  assert.ok(md.parseInline('**a***b').every((n) => n.s), '容错路径不产出空文本节点');
});

test('扁平：parseMd 全文档（段落/标题/列表/引用/表格/代码）内行内节点均为叶子', () => {
  const src = [
    '# 标题**粗**',
    '',
    '段落**粗[链](https://a.cn)*斜***。',
    '',
    '- 项 *斜* 与 `code`',
    '1. 项 **粗**',
    '',
    '> 引用 **粗**',
    '',
    '| 表头**粗** | b |',
    '| --- | --- |',
    '| `c` | *斜* |',
    '',
    '```',
    'raw **not bold**',
    '```',
  ].join('\n');
  const blocks = md.parseMd(src);
  assert.ok(blocks.length >= 6, '文档块数');
  blocks.forEach((b, i) => {
    if (b.t === 'p' || b.t === 'h') assertFlat(b.children, `块#${i}(${b.t})`);
    else if (b.t === 'ul' || b.t === 'ol') b.items.forEach((li, j) => assertFlat(li.children, `块#${i} li#${j}`));
    else if (b.t === 'quote') b.children.forEach((q, j) => assertFlat(q.children, `块#${i} quote#${j}`));
    else if (b.t === 'table') {
      b.headers.forEach((c, j) => assertFlat(c.children, `块#${i} th#${j}`));
      b.rows.forEach((r, j) => r.forEach((c, k) => assertFlat(c.children, `块#${i} r${j}c${k}`)));
    } else if (b.t === 'code') {
      assert.equal(b.children, undefined, '代码块为原始文本节点（无行内 children）');
    }
  });
  // 代码块内的 ** 不被解析（原样文本）
  assert.ok(blocks.some((b) => b.t === 'code' && b.s.includes('**not bold**')));
});

test('扁平：段落纯文本提取与旧协议一致（「复制本段」/选区偏移不倒退）', () => {
  const MD_TEXT = '第一段文字。\n\n第二段有**加粗**和[1]引用。\n\n> 引用行一\n> 引用行二\n\n1. 甲选项\n2. 乙选项';
  const model = chatSelect.paragraphModel({ id: 'a1', role: 'ai', content: MD_TEXT, mdNodes: md.parseMd(MD_TEXT) });
  assert.equal(model.text, '第一段文字。\n\n第二段有加粗和[1]引用。\n\n引用行一\n引用行二\n\n1. 甲选项\n2. 乙选项');
  assert.equal(model.byKey['md:1'].text, '第二段有加粗和[1]引用。');
  model.paras.forEach((p) => assert.equal(model.text.slice(p.start, p.end), p.text, `段落 ${p.key} 偏移应精确`));
});

test('扁平：引用内块级内容 → blk.lines 扁平行视图（md-block 不再自递归）', () => {
  const q = md.parseMd('> 引用 **粗**\n> 第二行\n>\n> - 列表项 *斜*')[0];
  assert.equal(q.t, 'quote');
  assert.ok(Array.isArray(q.lines) && q.lines.length === 2, '引用应有扁平行视图（每子块一行）');
  q.lines.forEach((ln, i) => assertFlat(ln.children, `quote line#${i}`));
  const lineText = (ln) => ln.children.map((n) => (n.t === 'cite' ? '[cite]' : (n.s || ''))).join('');
  assert.deepEqual(q.lines.map(lineText), ['引用 粗\n第二行', '· 列表项 斜']);
  // 行内样式在扁平行里保留
  assert.equal(q.lines[0].children.find((n) => n.s === '粗').cls, 'md-strong');
  assert.equal(q.lines[1].children.find((n) => n.s === '斜').cls, 'md-em');
  // 嵌套引用 → 行前缀（不再递归渲染），内容不丢
  const q2 = md.parseMd('> 外层\n> > 内层 **粗**')[0];
  const t2 = q2.lines.map(lineText).join('|');
  assert.ok(t2.indexOf('外层') !== -1 && t2.indexOf('内层') !== -1, '嵌套引用内容不丢: ' + t2);
  // children 结构保留（chatSelect 段落模型口径不变）
  assert.ok(Array.isArray(q.children) && q.children[0].t === 'p');
});

test('模板层：chat.wxml 内不存在模板自递归（微信引擎会中止渲染）', () => {
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/chat/chat.wxml'), 'utf8');
  const defs = {};
  const re = /<template\s+name="([\w-]+)"\s*>([\s\S]*?)<\/template>/g;
  let m;
  while ((m = re.exec(wxml))) defs[m[1]] = m[2];
  assert.ok(defs['md-inline'], 'md-inline 模板应存在');
  assert.ok(defs['md-block'], 'md-block 模板应存在');
  Object.keys(defs).forEach((name) => {
    const calls = (defs[name].match(/<template\s+is="([\w-]+)"/g) || [])
      .map((s) => s.replace(/.*is="([\w-]+)"/, '$1'));
    assert.ok(calls.indexOf(name) === -1, `模板 ${name} 不得自递归调用自己（实测会被引擎 stop）`);
  });
  // md-inline 内不得再出现任何 template 调用（单层遍历）
  assert.equal((defs['md-inline'].match(/<template\s+is=/g) || []).length, 0, 'md-inline 应为单层遍历模板');
});
