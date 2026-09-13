// 易理明灯 v1.2 — 轻量 Markdown 解析器（自写，无依赖，包体积小）
// 把 AI 回复解析为节点树 → chat.wxml 逐节点渲染（view/text 组合，墨韵样式）。
// 支持：**加粗**  *斜体*  - 列表  1. 列表  > 引用  `行内代码`  ```代码块```
//       表格（| a | b |）  # 标题  换行
// 引用角标兼容：正文中的 [n] / 🔗 解析为 cite 节点（点击开抽屉）；
//   代码块与行内代码内不解析引用（原样显示）。
// 流式兼容：解析器对未闭合标记宽容（半截 **、代码围栏、表格行照常渲染，
//   下个 chunk 补全后重解析即完整 —— 页面每 50ms 节流重解析，内容不长性能可接受）。
//
// 节点协议（block）：
//   {t:'p',    children:[inline]}          段落
//   {t:'h',    level:1-6, children}        标题
//   {t:'ul'|'ol', items:[{n, children}]}   列表（ol 的 n 为编号）
//   {t:'quote',children:[block]}           引用（可嵌套）
//   {t:'code', lang, s}                    代码块（原始文本）
//   {t:'table', headers:[{children}], rows:[[{children}]]}
// 节点协议（inline）—— k47-A：**扁平叶子节点**（模板层零递归）
//   {t:'text',  s, cls}            纯文本（cls 携带 strong/em 样式标记）
//   {t:'link',  s, url, cls}       链接文本（点击 copyCode；样式可与 strong/em 叠加）
//   {t:'code',  s, cls}            行内代码
//   {t:'image', alt, url}          图片（无文字）
//   {t:'cite',  idx}               引用角标（idx=0 为 🔗）
//   cls ∈ '' | 'md-strong' | 'md-em' | 'md-strong md-em'
// 设计说明：微信模板引擎不支持模板自递归（Template … is being called recursively,
//   will be stop.），旧协议把 strong/em/link 做成「容器 + children」→ chat.wxml 的
//   md-inline 必须自递归渲染内层，运行时被中止 → 加粗/斜体/链接内部文字不渲染。
//   现在递归只发生在 JS 解析侧，产出**一层扁平节点**（每个节点自带样式类标记），
//   WXML 只做单层遍历、不再有 template 自递归 → 嵌套行内样式完整上屏。

const CITE_LINK = '🔗';

/* 样式上下文 → 类名（扁平节点唯一渲染标记；样式叠加交给 CSS 多类共存） */
function clsOf(ctx) {
  const parts = [];
  if (ctx.bold) parts.push('md-strong');
  if (ctx.italic) parts.push('md-em');
  return parts.join(' ');
}

/* 文本落节点：处于链接上下文 → link 节点（可点击复制），否则 text */
function pushText(out, s, ctx) {
  if (!s) return;
  if (ctx.link) out.push({ t: 'link', s, url: ctx.link, cls: clsOf(ctx) });
  else out.push({ t: 'text', s, cls: clsOf(ctx) });
}

/* ── 行内解析（段落/列表项/单元格/表头；递归在 JS 侧完成，产出扁平叶子） ──
   ctx = { bold, italic, link }：嵌套样式在递归时累积到子节点，
   任意深度（strong/em/link 互相嵌套）都在此拍平——模板层无需感知层级。 */
function parseInlineCtx(s, ctx, out) {
  const buf = [];
  const flush = () => { if (buf.length) { pushText(out, buf.join(''), ctx); buf.length = 0; } };
  let i = 0;
  const L = s.length;
  while (i < L) {
    const ch = s[i];
    if (ch === '`') {
      const j = s.indexOf('`', i + 1);
      if (j === -1) { buf.push(s.slice(i)); break; }   // 未闭合：宽容显示
      flush();
      let code = s.slice(i + 1, j);
      if (code.length >= 2 && code[0] === ' ' && code[code.length - 1] === ' ') code = code.slice(1, -1);
      out.push({ t: 'code', s: code, cls: clsOf(ctx) });
      i = j + 1;
      continue;
    }
    if (ch === '!') {
      const im = /^!\[([^\[\]]*)\]\(([^\s)]+)\)/.exec(s.slice(i));
      if (im) {
        flush();
        out.push({ t: 'image', alt: im[1] || '', url: im[2] });
        i += im[0].length;
        continue;
      }
      buf.push('!');
      i++;
      continue;
    }
    if (ch === '[') {
      const lm = /^\[([^\[\]]+)\]\(([^\s)]+)\)/.exec(s.slice(i));
      if (lm) {
        flush();
        // 链接文本同样走完整行内解析（链接内加粗/斜体/行内码照常渲染），并继承外层样式
        parseInlineCtx(lm[1], { bold: ctx.bold, italic: ctx.italic, link: lm[2] }, out);
        i += lm[0].length;
        continue;
      }
      const cm = /^\[(\d+)\]/.exec(s.slice(i));
      if (cm) {
        flush();
        out.push({ t: 'cite', idx: parseInt(cm[1], 10) });
        i += cm[0].length;
        continue;
      }
      buf.push('[');
      i++;
      continue;
    }
    if (ch === CITE_LINK) {
      flush();
      out.push({ t: 'cite', idx: 0 });
      i++;
      continue;
    }
    if (ch === '*') {
      if (s[i + 1] === '*') {
        // ***粗斜***（三连星，LLM 常用）：粗 + 斜同时叠加
        if (s[i + 2] === '*') {
          const j3 = s.indexOf('***', i + 3);
          if (j3 !== -1) {
            flush();
            parseInlineCtx(s.slice(i + 3, j3), { bold: true, italic: true, link: ctx.link }, out);
            i = j3 + 3;
            continue;
          }
        }
        const j = s.indexOf('**', i + 2);
        if (j === -1) { buf.push(s.slice(i)); break; }   // 未闭合：宽容显示
        flush();
        parseInlineCtx(s.slice(i + 2, j), { bold: true, italic: ctx.italic, link: ctx.link }, out);
        i = j + 2;
        continue;
      }
      const j = s.indexOf('*', i + 1);
      if (j === -1) { buf.push(s.slice(i)); break; }
      flush();
      parseInlineCtx(s.slice(i + 1, j), { bold: ctx.bold, italic: true, link: ctx.link }, out);
      i = j + 1;
      continue;
    }
    buf.push(ch);
    i++;
  }
  flush();
}

/* ── 行内解析入口（扁平叶子数组；旧调用方语义不变） ── */
function parseInline(s) {
  const out = [];
  parseInlineCtx(String(s == null ? '' : s), { bold: false, italic: false, link: '' }, out);
  return out;
}

/* 表格 → 行文本（引用块内压平用；与 chatSelect 的表格文本同口径） */
function tableLine(blk) {
  const row = (cells) => (cells || []).map((c) => (c.children || []).map((n) => {
    if (n.t === 'cite') return n.idx > 0 ? '[' + n.idx + ']' : CITE_LINK;
    return n.s != null ? String(n.s) : '';
  }).join('')).join(' | ');
  const rows = [row(blk.headers)];
  (blk.rows || []).forEach((r) => rows.push(row(r)));
  return rows.join('\n');
}

/* ── 引用块「扁平行视图」（k47-A：模板层零递归的第二处） ──
   实测：md-block 模板在 quote 分支里再调 md-block（引用内块级嵌套）同样触发微信
   「Template … is being called recursively, will be stop.」→ 引用内文字整体不渲染。
   这里把引用的子块压成一行行**行内节点**（每行 = {children:[扁平 inline]}），
   WXML 端只用 md-inline 单层渲染；块级 children 结构保留（chatSelect 段落模型、
   纯文本提取继续走 children，口径不变）。 */
function quoteLines(blocks) {
  const lines = [];
  const push = (children, prefix) => {
    const pre = prefix ? [{ t: 'text', s: prefix, cls: '' }] : [];
    lines.push({ children: pre.concat(children || []) });
  };
  (Array.isArray(blocks) ? blocks : []).forEach((blk) => {
    if (!blk) return;
    if (blk.t === 'p' || blk.t === 'h') push(blk.children);
    else if (blk.t === 'quote') quoteLines(blk.children).forEach((ln) => push(ln.children, '│ '));
    else if (blk.t === 'ul' || blk.t === 'ol') {
      (blk.items || []).forEach((li) => push(li.children, (blk.t === 'ol' && li.n) ? li.n + '. ' : '· '));
    } else if (blk.t === 'code') push([{ t: 'code', s: String(blk.s != null ? blk.s : ''), cls: '' }]);
    else if (blk.t === 'table') push([{ t: 'text', s: tableLine(blk), cls: '' }]);
    else push(blk.children);
  });
  return lines;
}

/* 表格行 → 单元格节点数组（[{children:[inline]}]） */
function parseRow(line) {
  let s = (line || '').trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((c) => ({ children: parseInline(c.trim()) }));
}

/* 判断一行是否表格分隔行（|---|----| 或 |:---:|） */
function isSepLine(line) {
  const t = (line || '').trim();
  if (!/^\|?[\s:\-|]+\|?$/.test(t)) return false;
  return t.indexOf('-') !== -1;
}

/* 围栏表格兜底：围栏内容所有非空行均以 | 开头且存在分隔行 → 按表格解析。
   首行作表头、跳过分隔行、其余作数据行；不满足 → 返回 null（保持代码块）。
   LLM 常用 ``` 包裹表格，前端识别后按 markdown 表格渲染，避免整表变代码块。 */
function parseFenceTable(buf) {
  const rows = buf.map((l) => (l || '').trim()).filter((l) => l);
  if (!rows.length) return null;
  if (!rows.every((l) => l.startsWith('|'))) return null;
  if (!rows.some((l) => isSepLine(l))) return null;
  const headers = parseRow(rows[0]);
  const data = [];
  for (let j = 1; j < rows.length; j++) {
    if (isSepLine(rows[j])) continue;
    data.push(parseRow(rows[j]));
  }
  return { t: 'table', headers, rows: data };
}

/* 判断一行是否列表项，返回 {ordered, content} 或 null */
function matchList(line) {
  const t = (line || '').trim();
  const mu = t.match(/^[-*+]\s+(.*)$/);
  if (mu) return { ordered: false, content: mu[1] };
  const mo = t.match(/^(\d+)[.、]\s+(.*)$/);
  if (mo) return { ordered: true, no: parseInt(mo[1], 10) || 1, content: mo[2] };
  return null;
}

/* 块级起始判定（段落收集时提前停下） */
function isBlockStart(line, lines, i) {
  const t = (line || '').trim();
  if (!t) return true;
  if (/^```/.test(t)) return true;
  if (/^#{1,6}\s/.test(t)) return true;
  if (/^>\s?/.test(t)) return true;
  if (matchList(t)) return true;
  // 表格：本行以 | 开头且下一行是分隔行
  if (/^\|/.test(t) && lines[i + 1] && isSepLine(lines[i + 1])) return true;
  return false;
}

/* ── 块级解析：Markdown 文本 → block 节点数组 ── */
function parseMd(src) {
  const raw = String(src || '').replace(/\r\n/g, '\n');
  const lines = raw.split('\n');
  const nodes = [];
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i];
    const t = line.trim();
    if (!t) { i++; continue; }

    // 代码围栏 ```lang ... ```
    const fm = t.match(/^```(\S*)\s*$/);
    if (fm) {
      const lang = fm[1] || '';
      const buf = [];
      i++;
      while (i < n && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
      if (i < n) i++; // 跳过闭合围栏（未闭合 → 剩余全部算代码，流式兼容）
      // 围栏表格兜底：内容全为 | 行且含分隔行 → 按表格渲染（复用现有表格模板）
      const fenceTable = parseFenceTable(buf);
      nodes.push(fenceTable || { t: 'code', lang, s: buf.join('\n') });
      continue;
    }

    // 标题 # ~ ######
    const hm = t.match(/^(#{1,6})\s+(.*)$/);
    if (hm) {
      nodes.push({ t: 'h', level: hm[1].length, children: parseInline(hm[2]) });
      i++;
      continue;
    }

    // 引用 > …（连续引用行合并；内部递归块级解析，可含列表/代码等）
    if (/^>\s?/.test(t)) {
      const buf = [];
      while (i < n && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ''));
        i++;
      }
      const qChildren = parseMd(buf.join('\n'));
      nodes.push({ t: 'quote', children: qChildren, lines: quoteLines(qChildren) });
      continue;
    }

    // 表格：表头行 + 分隔行 + 连续 | 行
    if (/^\|/.test(t) && lines[i + 1] && isSepLine(lines[i + 1])) {
      const headers = parseRow(line);
      i += 2; // 跳过表头与分隔行
      const rows = [];
      while (i < n && /^\|/.test(lines[i].trim())) {
        rows.push(parseRow(lines[i]));
        i++;
      }
      nodes.push({ t: 'table', headers, rows });
      continue;
    }

    // 列表（- / * / + / 1. 2. 3.）
    const ml = matchList(t);
    if (ml) {
      const ordered = ml.ordered;
      const items = [];
      let idx = ml.ordered ? ml.no : 0;
      while (i < n) {
        const m = matchList(lines[i]);
        if (!m || m.ordered !== ordered) break;
        if (ordered) idx = m.no;
        items.push({ n: ordered ? idx : 0, children: parseInline(m.content) });
        if (ordered) idx++;
        i++;
      }
      nodes.push({ t: ordered ? 'ol' : 'ul', items });
      continue;
    }

    // 段落：收集到下一个块级起始
    const buf = [line];
    i++;
    while (i < n && !isBlockStart(lines[i], lines, i)) {
      buf.push(lines[i]);
      i++;
    }
    const joined = buf.join('\n');
    nodes.push({ t: 'p', children: parseInline(joined) });
  }
  return nodes;
}

module.exports = { parseMd, parseInline, CITE_LINK };
