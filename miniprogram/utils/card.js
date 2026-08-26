// 易理明灯 — 对话消息卡片化（Task E2-2）：解析服务端 [card:…]…[/card] 标记
//
// 服务端契约（src/bot/card_mark.py，E2-1）：
//   [card:类型 title="标题"]
//   <markdown 正文>
//   [/card]
//   - 类型枚举：paipan / yunshi / zeri / data / knowledge（服务端只产这五种）
//   - title 可缺省（data/knowledge 无默认 → 服务端省略属性，端上回退类型默认标题）
//   - 标题转义：\"  \\  \n（端上解析后还原）
//   - 正文后的卡外引导语/反馈语（"还想了解…"/"可回复「准」"）在 [/card] 之外
//   - 错误/失败文案与普通闲聊从不包装 → 无标记文本保持原样
//
// 流式补充事实（E2-2 实测服务端行为）：标记只作用于 process() 最终返回字符串，
// 流式正文先流出、最终返回串（含标记）在收尾时整体重发 → 流式场景下 content 的
// 形态可能是：
//   1) 标记在开头（引擎/模板路径，无 LLM 流式）：[card:…]\n正文…[/card]
//   2) 标记在尾部 + 标记前正文 = 卡片正文的重复（LLM 流式路径）：正文 + [card:…]…[/card]
//   3) 流式中断：只收到正文 或 正文 + 半截 [card:…
// parseCard 统一按「提取第一个 [card:…]…[/card] 块」实现，前缀/未闭合交给调用方
// （chat.js _cardView）按流式规则决策：重复前缀丢弃、未闭合进卡片壳/中断降级。
//
// 纯逻辑，无小程序 API 依赖 → 可直接 node 单测（miniprogram/tests/card.test.js）。

const CARD_TYPES = ['paipan', 'yunshi', 'zeri', 'data', 'knowledge'];

/* 类型小标签文案（卡片标题条左侧 tag） */
const CARD_TYPE_LABELS = {
  paipan: '命盘',
  yunshi: '运势',
  zeri: '择吉',
  data: '档案',
  knowledge: '知识',
};

/* 类型默认标题（title 属性缺省时的端上回退） */
const CARD_DEFAULT_TITLES = {
  paipan: '我的命盘',
  yunshi: '运势分析',
  zeri: '择吉结果',
  data: '档案查阅',
  knowledge: '命理知识',
};

/* 标题转义还原：\" → "  \\ → \  \n → 换行 */
function _unescapeTitle(s) {
  return String(s).replace(/\\(["\\n])/g, (m, ch) => (ch === 'n' ? '\n' : ch));
}

/* 解析开始标签行（流式中可为不完整行）：
   "[card:paipan title="我的命盘"]" → {type:'paipan', title:'我的命盘'}
   "[card:data]"                    → {type:'data', title:''}
   类型不在枚举 / 非 [card: 开头 → null（非卡片，不误解析） */
function _parseTagLine(line) {
  const t = String(line || '');
  const m = /^\[card:([a-z]+)/.exec(t);
  if (!m) return null;
  if (CARD_TYPES.indexOf(m[1]) === -1) return null;
  let title = '';
  const tm = /title="((?:[^"\\]|\\.)*)"/.exec(t);
  if (tm) title = _unescapeTitle(tm[1]);
  return { type: m[1], title };
}

/* 解析卡片标记 → {card, pending, prefix, tail, raw}
   - card:   {type, title, body} | null
     · 完整卡片（pending=false）→ body 为 [/card] 前正文
     · 未闭合（pending=true，流式中）→ body 为标记行后已收到的部分
       （brief 契约：未闭合返回 {card:null, pending:true, prefix}——此处 card 携带
       已解析的 type/title/body 供卡片壳渲染，语义一致，调用方以 pending 判断）
   - pending: true = 见 [card: 但未见 [/card]（流式中）
   - prefix:  第一个 [card: 之前的文本（流式重发场景 = 卡片正文的重复前缀；
     调用方按「正文以 prefix 开头 → 丢弃」去重；其余情况原样渲染在卡片上方）
   - tail:    [/card] 之后的卡外引导语
   - raw:     原始文本（兜底） */
function parseCard(text) {
  const src = String(text || '');
  const i = src.indexOf('[card:');
  if (i === -1) return { card: null, pending: false, prefix: '', tail: '', raw: src };
  const nl = src.indexOf('\n', i);
  const tagLine = nl === -1 ? src.slice(i) : src.slice(i, nl);
  const tag = _parseTagLine(tagLine);
  // 非法标签（未知类型/残缺到不可辨）→ 非卡片：保持原样（调用方按普通文本渲染）
  if (!tag) return { card: null, pending: false, prefix: src.slice(0, i), tail: '', raw: src };
  const prefix = src.slice(0, i);
  const bodyStart = nl === -1 ? src.length : nl + 1;
  const closeIdx = src.indexOf('[/card]', bodyStart);
  if (closeIdx === -1) {
    return {
      card: { type: tag.type, title: tag.title, body: src.slice(bodyStart) },
      pending: true,
      prefix,
      tail: '',
      raw: src,
    };
  }
  const body = src.slice(bodyStart, closeIdx).replace(/\n+$/, '');
  const tail = src.slice(closeIdx + 7).replace(/^\n+/, '');   // '[/card]'.length === 7
  return {
    card: { type: tag.type, title: tag.title, body },
    pending: false,
    prefix,
    tail,
    raw: src,
  };
}

/* E2-2 补漏（批次 2 B3-22）：未闭合标签缺 ] 剥不掉。流式截断/长标题截断会留下
   `[card:type title="…`（缺闭合 ]），上面两个 replace 都要求闭合的 ] → 半截标签
   原样残留进 TTS/复制/分享出口。追加行尾兜底：匹配 `[card:` 起、缺 ] 的残缺标签，
   [^\n]* 只吃到本行行尾（(?=\n|$) 锚定：串尾截断 或 标签行后还有正文行）——行内
   其它内容同标签行一并视为残缺产物剥掉；只吃一行，绝不吞后续正文行。 */
const _TRUNCATED_TAG_RE = /\n?\[card:[a-z]+(?:\s+title="(?:[^"\\]|\\.)*")?[^\n]*(?=\n|$)/g;

/* E2-2 补漏（批次 2 B3-23）：折叠连续空行需识别代码围栏（```）。原 \n{3,}→\n\n
   是无差别折叠——代码块内的空行是代码内容，被折叠会破坏代码块（md.js 只认 ```
   围栏，~~~ 不做围栏处理，口径一致）。实现：逐行扫描，围栏外连续空行折叠为
   1 行（语义与原 \n{3,}→\n\n 完全一致），围栏内空行原样保留。 */
function _foldBlankLines(text) {
  const lines = String(text || '').split('\n');
  const out = [];
  let inFence = false;
  let blankRun = 0;
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const s = raw.trim();
    if (s.indexOf('```') === 0) {           // 围栏开关（同 md.js 判定：行首 ```）
      inFence = !inFence;
      out.push(raw);
      blankRun = 0;
    } else if (s === '') {
      if (!inFence) {
        blankRun += 1;
        if (blankRun > 1) continue;          // 围栏外连续空行只留 1 行
      }
      out.push(raw);                         // 围栏内空行原样保留
    } else {
      blankRun = 0;
      out.push(raw);
    }
  }
  return out.join('\n');
}

/* 剥离卡片标记 → 纯文本（TTS 朗读 / 复制 / 分享标题等用户可见出口使用，
   标记本身不暴露给用户）。只删 [card:…] 标签行与 [/card] 行，正文原样保留。
   注意不做行首锚定：流式重发场景下 [card: 可能紧贴在前文正文之后。 */
function stripCardMarkers(text) {
  const src = String(text || '');
  return _foldBlankLines(src
    .replace(/\[card:[a-z]+(?:\s+title="(?:[^"\\]|\\.)*")?\]\s*\n?/g, '')
    .replace(/\n?\[\/card\]/g, '')
    .replace(_TRUNCATED_TAG_RE, ''))
    .trim();
}

/* ═══ 流式规则（brief §流式规则，关键）═══
   输入：content（含标记的当前文本）+ 流式状态 + md 解析器（依赖注入，纯函数）：
   - 闭合 → 定格卡片；流式重发的重复前缀（标记前文本 = 正文开头）丢弃，
     其余前置内容原样渲染在卡片上方（不丢内容）
   - 未闭合 + 流式中 → 卡片壳模式：标题条（已解析 title 或类型默认标题）
     + 壳内正文按现有流式增量渲染（正文 = 标记行后已收到的部分）
   - 未闭合 + 中断（error）→ 降级纯文本：标记前正文优先，标记本身不暴露；
     标记在开头时剥掉标记行显示已收到正文
   - 未闭合 + 正常结束（成功完成/用户停止）→ 定格为卡片壳（同上渲染）
   - 无标记/非法标记 → 返回 {card:null, mdNodes:null}（保持现有纯文本渲染）
   返回值：{card, mdNodes, cardNodes, cardTailNodes, cardPrefixNodes,
            cardTitle, cardTypeLabel, cardFinal}
     card 非空 → 卡片视图（mdNodes 恒空，wxml 走 card 分支）
     card 为空但 mdNodes 非空 → 降级纯文本视图
     两者皆空 → 非卡片，调用方维持原渲染 */
function buildCardView(content, opts, parseMd) {
  const pc = parseCard(content);
  if (!pc.card) return { card: null, mdNodes: null };
  const streaming = !!(opts && opts.streaming);
  const error = !!(opts && opts.error);
  const label = CARD_TYPE_LABELS[pc.card.type] || '';
  const title = pc.card.title
    || CARD_DEFAULT_TITLES[pc.card.type]
    || '易理明灯';
  if (pc.pending) {
    if (streaming) {
      // 流式卡片壳：壳骨架 + 标题条 + 壳内正文增量
      return {
        card: { type: pc.card.type, title: pc.card.title, body: pc.card.body, final: false },
        mdNodes: null,
        cardNodes: parseMd(pc.card.body),
        cardTailNodes: null,
        cardPrefixNodes: null,
        cardTitle: title,
        cardTypeLabel: label,
        cardFinal: false,
      };
    }
    if (pc.prefix) {
      // 中断/停止：标记前内容即已流出的正文（标记重发未完成）→ 降级纯文本
      return { card: null, mdNodes: parseMd(pc.prefix) };
    }
    if (error) {
      // 中断：标记在开头 → 剥掉标记行，显示已收到正文（标记不暴露）
      return { card: null, mdNodes: parseMd(pc.card.body) };
    }
    // 未闭合但正常结束 → 定格为卡片壳（正文 = 标记后已收到部分）
    return {
      card: { type: pc.card.type, title: pc.card.title, body: pc.card.body, final: true },
      mdNodes: null,
      cardNodes: parseMd(pc.card.body),
      cardTailNodes: null,
      cardPrefixNodes: null,
      cardTitle: title,
      cardTypeLabel: label,
      cardFinal: true,
    };
  }
  // 闭合 → 定格卡片；流式重发重复前缀（标记前文本是正文的开头）丢弃，其余保留。
  // 前缀可能带换行尾随（正文\n[card:…]），比对前剥掉尾部空白
  const dup = pc.prefix && pc.card.body.indexOf(pc.prefix.replace(/\s+$/, '')) === 0;
  return {
    card: pc.card,
    mdNodes: null,
    cardNodes: parseMd(pc.card.body),
    cardTailNodes: pc.tail ? parseMd(pc.tail) : null,
    cardPrefixNodes: (pc.prefix && !dup) ? parseMd(pc.prefix) : null,
    cardTitle: title,
    cardTypeLabel: label,
    cardFinal: true,
  };
}

module.exports = {
  parseCard, stripCardMarkers, buildCardView,
  CARD_TYPES, CARD_TYPE_LABELS, CARD_DEFAULT_TITLES,
};
