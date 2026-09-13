// 易理明灯 k10-C — 文字选取 · 段落模型（纯逻辑，无小程序 API 依赖 → node 可单测）
//
// 职责：把一条消息（镜像后的渲染视图，含 mdNodes/cardNodes/cardTailNodes/
// cardPrefixNodes）转成一个「段落模型」——覆盖层（方案乙 textarea）所需的纯文本
// 与段落偏移、「复制本段」（甲兜底）所需的段落文本，二者共用同一事实源，保证
// 「复制本段 / 乙预设选区 / 甲高亮」取到的永远是同一段文字。
//
// 段落（para）粒度 = md 顶层可视行块（对齐 chat.wxml md-block 模板顶层 wx:for 的
// 一个 index）：
//   p / h          → 一段（行内 children 拼纯文本）
//   quote          → 整体一段（children 递归拼纯文本，内部 \n 连接）
//   ul / ol        → 整体一段（各 li 文本 \n 连接，ol 项保留 "n. " 前缀）
//   code 块        → 一段（原始代码文本）
//   table          → 不产生段落（该处长按无可复制段；其文本仍计入覆盖层全文，
//                    保证偏移连续——以无 key 的 chunk 参与拼接）
//   user 消息      → 单段 'user:0'（文本即整条消息，stripCardMarkers 同款清洗）
//
// 段落键格式 '<zone>:<index>'：zone ∈ md / pre / card / tail / user——与 wxml
// data-para-id 同构（模板侧以 paraKey+':'+index 拼出）。
//
// 卡片消息（msg.card 非空）：按 wxml 渲染顺序 pre → card → tail 三个 zone 拼接；
// 非卡片消息（含降级纯文本）走 md zone。卡片标题条/收藏钮等 UI 装饰不参与。
// 段落粒度取舍（li 级 → 整列表一级）：wxml 内层 wx:for 会遮蔽外层 index，
// 逐 li 挂 data-para 需模板外层 index 透传重构（后续项），当前整列表一段已可覆盖
// 「复制本段」多数诉求。
const cardUtil = require('./card');

/* 行内节点 → 纯文本（k47-A 扁平协议：text/link/code 均自带 s；cite 还原 [n]/🔗；
   image 不产生文字。末尾 children 分支仅兼容历史容器型节点 strong/em/link） */
function inlineText(nodes) {
  const out = [];
  (Array.isArray(nodes) ? nodes : []).forEach((n) => {
    if (!n) return;
    if (n.t === 'text' || n.t === 'code' || n.t === 'link') { out.push(String(n.s != null ? n.s : '')); }
    else if (n.t === 'cite') { out.push(n.idx > 0 ? '[' + n.idx + ']' : '🔗'); }
    else if (n.t === 'image') { /* 图片无文字 */ }
    else if (n.children) { out.push(inlineText(n.children)); }
  });
  return out.join('');
}

/* 引用块内部递归 → 行文本数组（每块一行；嵌套结构各自压平） */
function blocksToLines(blocks) {
  const lines = [];
  (Array.isArray(blocks) ? blocks : []).forEach((blk) => {
    if (!blk) return;
    if (blk.t === 'p' || blk.t === 'h') lines.push(inlineText(blk.children));
    else if (blk.t === 'quote') lines.push(blocksToLines(blk.children).join('\n'));
    else if (blk.t === 'ul' || blk.t === 'ol') {
      (blk.items || []).forEach((li) => {
        lines.push(((blk.t === 'ol' && li.n) ? li.n + '. ' : '') + inlineText(li.children));
      });
    } else if (blk.t === 'code') lines.push(String(blk.s != null ? blk.s : ''));
    else if (blk.t === 'table') lines.push(tableText(blk));
    else lines.push(inlineText(blk.children));
  });
  return lines;
}

function tableText(blk) {
  const row = (cells) => (cells || []).map((c) => inlineText(c.children)).join(' | ');
  const rows = [];
  rows.push(row(blk.headers));
  (blk.rows || []).forEach((r) => rows.push(row(r)));
  return rows.join('\n');
}

/* 一个 zone（同一次 md-block 顶层 wx:for 渲染）→ chunk 列表。
   chunk = { key: '<zone>:<index>' | null(仅占文本位、无段落), text }
   空文本 chunk 丢弃（纯装饰节点不占位）。 */
function chunksForZone(zoneKey, blocks) {
  const out = [];
  (Array.isArray(blocks) ? blocks : []).forEach((blk, i) => {
    if (!blk) return;
    let text = '';
    if (blk.t === 'p' || blk.t === 'h') text = inlineText(blk.children);
    else if (blk.t === 'quote') text = blocksToLines(blk.children).join('\n');
    else if (blk.t === 'ul' || blk.t === 'ol') {
      text = (blk.items || []).map((li) =>
        ((blk.t === 'ol' && li.n) ? li.n + '. ' : '') + inlineText(li.children)).join('\n');
    } else if (blk.t === 'code') text = String(blk.s != null ? blk.s : '');
    else if (blk.t === 'table') {
      out.push({ key: null, text: tableText(blk) });   // 占文本位，不产生段落
      return;
    }
    text = String(text || '').trim();
    if (text) out.push({ key: zoneKey + ':' + i, text });
  });
  return out;
}

/* 消息镜像 → 段落模型
   { text, paras:[{key,zone,text,start,end}], byKey:{key:para} }
   text = 覆盖层全文（chunk 间 '\n\n' 连接，模拟渲染层块间距）；
   para.start/end 为在 text 内的字符偏移（分隔符计入，严格累计，无 indexOf 猜测）。 */
function paragraphModel(msg) {
  const m = msg || {};
  const zones = [];
  if (m.role === 'user') {
    const t = cardUtil.stripCardMarkers(String(m.content || ''));
    if (t) zones.push({ key: 'user', chunks: [{ key: 'user:0', text: t }] });
  } else if (m.card) {
    if (m.cardPrefixNodes && m.cardPrefixNodes.length) {
      zones.push({ key: 'pre', chunks: chunksForZone('pre', m.cardPrefixNodes) });
    }
    if (m.cardNodes && m.cardNodes.length) {
      zones.push({ key: 'card', chunks: chunksForZone('card', m.cardNodes) });
    }
    if (m.cardTailNodes && m.cardTailNodes.length) {
      zones.push({ key: 'tail', chunks: chunksForZone('tail', m.cardTailNodes) });
    }
  } else if (m.mdNodes && m.mdNodes.length) {
    zones.push({ key: 'md', chunks: chunksForZone('md', m.mdNodes) });
  } else {
    const t = cardUtil.stripCardMarkers(String(m.content || ''));
    if (t) zones.push({ key: 'user', chunks: [{ key: 'user:0', text: t }] });
  }
  // 序列化（跨 zone 保持渲染顺序）→ 单遍累计偏移
  const seq = [];
  zones.forEach((z) => z.chunks.forEach((c) => seq.push({ key: c.key, text: c.text })));
  const text = seq.map((s) => s.text).join('\n\n');
  const paras = [];
  const byKey = {};
  let cursor = 0;
  seq.forEach((s, i) => {
    if (!s.key) { cursor += s.text.length + (i < seq.length - 1 ? 2 : 0); return; }
    const p = {
      key: s.key,
      zone: String(s.key).split(':')[0],
      text: s.text,
      start: cursor,
      end: cursor + s.text.length,
    };
    cursor += s.text.length + (i < seq.length - 1 ? 2 : 0);
    paras.push(p);
    byKey[p.key] = p;
  });
  return { text, paras, byKey };
}

module.exports = { paragraphModel, chunksForZone, inlineText };
