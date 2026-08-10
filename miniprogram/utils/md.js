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
// 节点协议（inline）：
//   {t:'text', s}   {t:'strong', children}  {t:'em', children}
//   {t:'code', s}   {t:'link', url, children}   {t:'cite', idx}（idx=0 为 🔗）

const CITE_LINK = '🔗';

/* ── 行内二级解析（strong/em/link 的内容）：只处理 code / [n] / 🔗 / 链接，不再嵌套加粗斜体 ── */
function inlineL2(s) {
  const out = [];
  const buf = [];
  const flush = () => {
    if (buf.length) { out.push({ t: 'text', s: buf.join('') }); buf.length = 0; }
  };
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
      out.push({ t: 'code', s: code });
      i = j + 1;
      continue;
    }
    if (ch === '[') {
      const lm = /^\[([^\[\]]+)\]\(([^\s)]+)\)/.exec(s.slice(i));
      if (lm) {
        flush();
        out.push({ t: 'link', children: inlineL2(lm[1]), url: lm[2] });
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
    buf.push(ch);
    i++;
  }
  flush();
  return out;
}

/* ── 行内一级解析（段落/列表项/单元格）：strong / em / code / link / cite ── */
function parseInline(s) {
  const out = [];
  const buf = [];
  const flush = () => {
    if (buf.length) { out.push({ t: 'text', s: buf.join('') }); buf.length = 0; }
  };
  let i = 0;
  const L = s.length;
  while (i < L) {
    const ch = s[i];
    if (ch === '`') {
      const j = s.indexOf('`', i + 1);
      if (j === -1) { buf.push(s.slice(i)); break; }
      flush();
      let code = s.slice(i + 1, j);
      if (code.length >= 2 && code[0] === ' ' && code[code.length - 1] === ' ') code = code.slice(1, -1);
      out.push({ t: 'code', s: code });
      i = j + 1;
      continue;
    }
    if (ch === '[') {
      const lm = /^\[([^\[\]]+)\]\(([^\s)]+)\)/.exec(s.slice(i));
      if (lm) {
        flush();
        out.push({ t: 'link', children: inlineL2(lm[1]), url: lm[2] });
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
        const j = s.indexOf('**', i + 2);
        if (j === -1) { buf.push(s.slice(i)); break; }   // 未闭合：宽容显示
        flush();
        out.push({ t: 'strong', children: inlineL2(s.slice(i + 2, j)) });
        i = j + 2;
        continue;
      }
      const j = s.indexOf('*', i + 1);
      if (j === -1) { buf.push(s.slice(i)); break; }
      flush();
      out.push({ t: 'em', children: inlineL2(s.slice(i + 1, j)) });
      i = j + 1;
      continue;
    }
    buf.push(ch);
    i++;
  }
  flush();
  return out;
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
      nodes.push({ t: 'code', lang, s: buf.join('\n') });
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
      nodes.push({ t: 'quote', children: parseMd(buf.join('\n')) });
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
