// 易理明灯 v1.1 — 全局流式会话宿主
// 职责：流式生成（wx.request enableChunked）跨页面存活 —— 切 tab（reLaunch 销毁页面）后
//       请求继续、队列不丢、历史照常落盘；页面重建后重新挂载（attach）取回现场。
// 页面通过 subscribe(listener) 接收状态快照：{messages, streaming, typing, activeMsgId, notice, tick}
// 唯一真源：host.messages。页面 data.messages 只是镜像（由页面自行计算 segments）。
const api = require('./api');

const STORAGE_KEY = 'ylm_chat_messages';
const FLUSH_MS = 50;              // setData 合并节流：每 50ms 批量刷新一次（防卡）
const CHUNK_GAP_TIMEOUT_S = 90;   // 90s 无 chunk → 判超时（Task 2：给后端排盘管线更长窗口）

/* SSE 行解析：UTF-8 增量解码（小程序无 TextDecoder，用字节缓冲 + 逐行转码） */
function _u8toString(bytes) {
  if (!bytes || typeof bytes.length !== 'number') return '';
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  try {
    return decodeURIComponent(escape(s)); // 经典 UTF-8 转码
  } catch (e) {
    return s;
  }
}

class SseParser {
  constructor(onEvent) {
    this._buf = [];     // 未到行尾的字节
    this._lines = [];   // 待处理行（UTF-8 已解码）
    this._onEvent = onEvent;
  }
  /* 追加原始字节（ArrayBuffer / Uint8Array）
     真机保护：解析异常一律 try/catch，绝不让页面 JS 崩（解析失败仅丢本块，后续块继续） */
  feed(data) {
    try {
      const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : data;
      if (!bytes || !bytes.length) return;
      let split = -1;
      for (let i = bytes.length - 1; i >= 0; i--) {
        if (bytes[i] === 0x0a) { split = i; break; }
      }
      if (split === -1) {
        this._buf.push(bytes);
        return;
      }
      const head = bytes.slice(0, split + 1);
      const tail = bytes.slice(split + 1);
      const all = this._buf.concat([head]);
      this._buf = tail.length ? [tail] : [];
      let s = _u8toString(this._concat(all));
      s.split('\n').forEach((l) => this._lines.push(l));
      this._drain();
    } catch (e) {
      console.warn('[StreamHost] SSE 解析异常（已忽略本块）:', e && e.message);
    }
  }
  _concat(parts) {
    const total = parts.reduce((n, p) => n + p.length, 0);
    const out = new Uint8Array(total);
    let off = 0;
    parts.forEach((p) => { out.set(p, off); off += p.length; });
    return out;
  }
  _drain() {
    let ready = -1;
    for (let i = 0; i < this._lines.length; i++) {
      if (this._lines[i] === '') { ready = i; break; }
    }
    if (ready === -1) return;
    const block = this._lines.slice(0, ready);
    this._lines = this._lines.slice(ready + 1);
    block.forEach((l) => {
      const t = String(l || '').trim();
      if (!t.startsWith('data:')) return;
      const payload = t.slice(5).trim();
      if (!payload) return;
      let evt = null;
      try { evt = JSON.parse(payload); } catch (e) { return; }
      if (evt && evt.type) this._onEvent(evt);
    });
    if (this._lines.length) this._drain();
  }
}

function nowTime(minOffset) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const m = (now.getMinutes() + (minOffset || 0)) % 60;
  return `${pad(now.getHours())}:${pad(m)}`;
}

class StreamHost {
  constructor() {
    this.listeners = [];
    this._init();
  }

  _init() {
    this.messages = [];
    this.streaming = false;
    this.typing = false;
    this.queue = [];
    this.task = null;            // wx.request 句柄（停止按钮 → abort）
    this.sse = null;
    this.msgId = null;           // 当前生成中的 AI 消息 id
    this.curText = '';
    this.curTag = '';
    this.chunkAccum = '';
    this.flushTimer = null;
    this.watchdog = null;
    this.watchdogFired = false;
    this.gotData = false;
    this.fallbackStarted = false;
    this.notice = null;          // 一次性 toast 提示（页面消费后清除）
    this.tick = 0;               // 状态变更计数（页面据此判断是否需要滚动/刷新）
    this._stopRequested = false;
  }

  /* ── 页面挂载/解挂 ── */
  subscribe(fn) {
    this.listeners.push(fn);
    return () => {
      this.listeners = this.listeners.filter((f) => f !== fn);
    };
  }

  get active() {
    return this.streaming;
  }

  /* 当前现场快照（页面 onLoad 采用） */
  getState() {
    return {
      messages: this.messages,
      streaming: this.streaming,
      typing: this.typing,
      activeMsgId: this.msgId,
      notice: this.notice,
      tick: this.tick,
    };
  }

  /* 页面导入消息（onLoad 恢复历史 / 新开对话 SEED） */
  setMessages(messages) {
    if (Array.isArray(messages)) this.messages = messages;
  }

  _emit(extra) {
    const state = Object.assign({
      messages: this.messages,
      streaming: this.streaming,
      typing: this.typing,
      activeMsgId: this.msgId,
      notice: this.notice,
      tick: this.tick,
    }, extra || {});
    this.listeners.forEach((fn) => {
      try { fn(state); } catch (e) {
        console.warn('[StreamHost] 页面监听回调异常:', e && e.message);
      }
    });
  }

  /* 一次性提示（页面 toast 后调用 clearNotice 清除） */
  notify(title) {
    this.notice = title;
    this._emit();
  }

  clearNotice() {
    this.notice = null;
  }

  /* ── 发送入口（v8 阶段 3）：生成中允许再发 → 排队（豆包式），且新消息立即上屏（pending 灰态） ── */
  /* Task 8 深夜倾诉开关：chat 页深夜模式时置 true → 流式请求随附 deep_night
     （后端：临时不落记忆 + 深夜语气层） */
  setDeepNight(v) {
    this.deepNight = !!v;
  }

  send(text, tag) {
    const t = (text || '').trim();
    if (!t) return 'empty';
    const now = Date.now();
    if (this.streaming) {
      // 排队：消息立即上屏 + pending 标记，轮到它时清除
      const userMsg = { id: 'u' + now, role: 'user', content: t, time: nowTime(0), pending: true };
      this.messages = this.messages.concat([userMsg]);
      this.queue.push({ text: t, tag: tag || '', userMsgId: userMsg.id });
      this.tick++;
      this._emit({ autoScroll: true });
      this._save();
      return 'queued';
    }
    this._startStream(t, tag || '', null);
    return 'sent';
  }

  /* 流式一条消息：上屏用户笺 + 空 AI 笺 → 逐 chunk 打字机 → done/error */
  _startStream(text, tag, queued) {
    const now = Date.now();
    let userMsg = null;
    if (queued && queued.userMsgId) {
      const idx = this._indexOf(queued.userMsgId);
      if (idx >= 0) {
        userMsg = Object.assign({}, this.messages[idx], { pending: false });
        this.messages = this.messages.slice();
        this.messages[idx] = userMsg;
      }
    }
    if (!userMsg) {
      // 直发（非排队）或排队消息在列表中已缺失：补建用户笺并上屏
      userMsg = { id: 'u' + now, role: 'user', content: text, time: nowTime(0) };
      this.messages = this.messages.concat([userMsg]);
    }
    const aiMsg = {
      id: 'a' + now,
      role: 'ai',
      tag: tag || '',
      content: '',
      time: nowTime(3),
      streaming: true,     // 生成中：闪烁光标 + 停止钮
      thinking: [],        // 思考路径步骤 [{text, state:'doing'|'done'}]
      error: false,
      retryText: text,     // 重试时原样重发
      consultationId: null,
      segments: [],        // 页面镜像时重算（引用分段）
      mdNodes: [],         // v1.2 页面镜像时重算（Markdown 节点树）
      citations: [],
      suggestions: [],     // v1.2 建议卡片：回复后的推荐追问（done 事件携带）
    };
    this.messages = this.messages.concat([aiMsg]);
    this.streaming = true;
    this.typing = true;    // 首个 chunk 前显示「研墨中」
    this.msgId = aiMsg.id;
    this.curText = text;
    this.curTag = tag || '';
    this.chunkAccum = '';
    this.gotData = false;
    this.fallbackStarted = false;
    this.watchdogFired = false;
    this._stopRequested = false;
    this.tick++;
    this._emit({ autoScroll: true });
    this._save();
    this._resetWatchdog();
    this._startRequest();
  }

  /* 发起 wx.request（enableChunked 流式；请求在宿主内，页面销毁不影响） */
  _startRequest() {
    const handlers = {
      onChunkRaw: (res) => this._onChunkRaw(res),
      onAbort: () => this._onAbort(),
      onError: (err) => this._onError(err),
    };
    api.chatStream(this.curText, handlers, { deepNight: !!this.deepNight })
      .then((handle) => {
        this.task = handle;
        if (this._stopRequested) {
          // 停止时请求尚未建立（登录/探测等待中）→ 建立后立即中止
          try { handle.abort(); } catch (e) { /* ignore */ }
        }
      })
      .catch((e) => this._onError(e));
  }

  /* 停止生成（发送钮变停止钮）：保留已输出 */
  stop() {
    if (!this.streaming) return;
    if (this.task) {
      try { this.task.abort(); } catch (e) { /* ignore */ }
    } else {
      this._stopRequested = true; // 请求未建立 → 建立后立即中止
    }
  }

  /* 重试：丢弃失败气泡，用原消息重发 */
  retry(msgId, text, tag) {
    const t = (text || '').trim();
    if (!t || this.streaming) return;
    this.messages = this.messages.filter((m) => m.id !== msgId);
    this._save();
    this._startStream(t, tag || '', null);
  }

  /* 删除一条消息（气泡菜单）：生成中的消息 → 中止；排队中的用户消息 → 出队 */
  removeMessage(id) {
    if (id === this.msgId && this.streaming) {
      if (this.task) {
        try { this.task.abort(); } catch (e) { /* ignore */ }
      }
      this._clearFlush();
      this._clearWatchdog();
      this.streaming = false;
      this.typing = false;
      this.task = null;
    }
    this.queue = this.queue.filter((q) => q.userMsgId !== id);
    this.messages = this.messages.filter((m) => m.id !== id);
    this.tick++;
    this._save();
    this._emit();
  }

  /* 通用消息字段修补（收藏 kept / 思考折叠等），落盘同步 */
  patchMessage(id, patch) {
    const idx = this._indexOf(id);
    if (idx < 0) return;
    this.messages = this.messages.slice();
    this.messages[idx] = Object.assign({}, this.messages[idx], patch);
    this.tick++;
    this._emit();
    this._save();
  }

  /* 中断恢复：storage 里残留的 pending 用户消息 → 重新入队（返回时自动重连） */
  requeuePending(tagFor) {
    const pend = [];
    this.messages.forEach((m) => {
      if (m.role === 'user' && m.pending) {
        pend.push({
          text: String(m.content || ''),
          userMsgId: m.id,
          tag: tagFor ? tagFor(m.content) : '',
        });
      }
    });
    if (!pend.length) return;
    this.queue = pend.concat(this.queue);
    if (!this.streaming) this._nextQueued();
  }

  /* 重置（清空/新开对话）：中止进行中的流，清空队列与消息 */
  reset(messages) {
    if (this.task) {
      try { this.task.abort(); } catch (e) { /* ignore */ }
    }
    this._clearFlush();
    this._clearWatchdog();
    this._init();
    this.messages = Array.isArray(messages) ? messages : [];
    this.tick++;
    this._save();
    this._emit();
  }

  /* ── 内部：SSE 事件流 ── */

  _onChunkRaw(res) {
    if (!this.sse) this.sse = new SseParser((evt) => this._onSseEvent(evt));
    this.sse.feed(res.data);
  }

  _onSseEvent(evt) {
    this.gotData = true;               // 任一事件到达 → 流是活的
    this._resetWatchdog();             // 续命（ping/thinking/chunk 均算）
    const type = evt.type;
    if (type === 'start' || type === 'ping') return;
    if (type === 'thinking' || type === 'tool') {
      this._addThinkingStep(evt.text || '');
      return;
    }
    if (type === 'chunk') {
      this._appendChunk(evt.content || '');
      return;
    }
    if (type === 'done') {
      this._onDone(evt.consultation_id, evt.citations, evt.suggestions);
      return;
    }
    if (type === 'error') {
      this._onError(new Error(evt.message || '回复生成失败'));
    }
  }

  _addThinkingStep(text) {
    const msg = this._find(this.msgId);
    if (!msg) return;
    const thinking = (msg.thinking || []).map((s) => ({ text: s.text, state: 'done' }));
    thinking.push({ text, state: 'doing' });
    // v2026-08-17（元宝深度思考）：首步记起点，done 时算全程秒数（用时 Xs）
    const patch = { thinking };
    if (!msg.thinkStart) patch.thinkStart = Date.now();
    this._patch(this.msgId, patch);
    this.tick++;
    this._emit({ autoScroll: true });
    this._save();                       // 页面可能已销毁：思考路径也落盘
  }

  /* 正文增量：合并缓冲 + 50ms 节流 flush（防 setData 卡顿） */
  _appendChunk(content) {
    this.chunkAccum = (this.chunkAccum || '') + content;
    if (this.typing) {
      this.typing = false;              // 首个 chunk 到达：研墨中 → 打字机
      this.tick++;
      this._emit({ autoScroll: true });
    }
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      if (!this.chunkAccum) return;
      const delta = this.chunkAccum;
      this.chunkAccum = '';
      const msg = this._find(this.msgId);
      if (!msg) return;
      this._patch(this.msgId, { content: msg.content + delta });
      this.tick++;
      this._emit({ autoScroll: true });
      this._save();                     // 页面可能已销毁：部分内容也落盘（中断可恢复）
    }, FLUSH_MS);
  }

  /* v1.2：done 事件携带 suggestions（回复后的推荐追问，失败/超时 → 无） */
  _onDone(consultationId, citations, suggestions) {
    this._flushAccum();   // 收尾前冲刷未 flush 的尾部增量（防最后 chunk 被 _clearFlush 丢弃）
    this._clearFlush();
    this._clearWatchdog();
    const msg = this._find(this.msgId);
    if (!msg) return;
    let content = msg.content || '';
    // 空回复兜底（v8 8.3）："我走神了，你再说一遍？"
    if (!content.trim()) content = '我走神了，你再说一遍？';
    const cits = Array.isArray(citations) ? citations : [];
    // v2026-08-17（元宝深度思考）：全程秒数 = 首步思考事件 → done
    const thinkSeconds = msg.thinkStart
      ? Math.max(1, Math.round((Date.now() - msg.thinkStart) / 1000))
      : 0;
    this._patch(this.msgId, {
      content,
      citations: cits,
      suggestions: Array.isArray(suggestions) ? suggestions.slice(0, 3) : [],
      consultationId: consultationId > 0 ? consultationId : null,
      streaming: false,
      error: false,
      thinking: (msg.thinking || []).map((s) => ({ text: s.text, state: 'done' })),
      thinkSeconds,
      // Task 3（思考步骤元宝式）：全部完成后自动收起为胶囊一行
      // （页面镜像派生 thinkLabel「深度思考完成 · 用时 Xs」，点开展开全部步骤）
      thinkCollapsed: true,
    });
    this.streaming = false;
    this.typing = false;
    this.task = null;
    this.tick++;
    this._emit({ autoScroll: true });
    this._save();
    this._nextQueued();
  }

  /* 用户点停止：保留已输出（watchdog 已接管时直接返回，避免双重收尾） */
  _onAbort() {
    if (this.watchdogFired) { this.watchdogFired = false; return; }  // watchdog 已收尾
    if (!this.task && !this.streaming) return;                       // watchdog/错误已收尾
    this._flushAccum();   // 收尾前冲刷未 flush 的尾部增量（停止时同样不丢已流出的尾巴）
    this._clearFlush();
    this._clearWatchdog();
    const msg = this._find(this.msgId);
    if (!msg) return;
    this._patch(this.msgId, {
      streaming: false,
      thinking: (msg.thinking || []).map((s) => ({ text: s.text, state: 'done' })),
    });
    this.streaming = false;
    this.typing = false;
    this.task = null;
    this.tick++;
    this._emit();
    this._save();
    this._nextQueued();
  }

  /* 失败：无任何输出 → 自动回退普通 /api/chat；有部分输出 → 保留 + 重试钮 */
  async _onError(err) {
    this._flushAccum();   // 收尾前冲刷未 flush 的尾部增量（失败时尽量保留已流出的内容）
    this._clearFlush();
    this._clearWatchdog();
    console.warn('[StreamHost] 流式失败:', err && (err.message || err.errMsg));
    const msg = this._find(this.msgId);
    if (!msg) return;
    const hasPartial = !!(msg.content && msg.content.trim());

    // 真机保护：流式未产出任何可见内容（非 200 / 异常 / 超时 / 无 chunk）→ 静默回退普通请求
    // Task 2 回退静默化：thinking/tool 事件不算可见输出——只要无正文就静默回退
    // （不显示错误态、不打断）；仅当回退也失败时才进入下方错误态 + 重试钮
    if (!hasPartial && !this.fallbackStarted) {
      this.fallbackStarted = true;
      try {
        const res = await api.chat(this.curText || '');
        const content = (res && (res.reply || res.content || '')) || '';
        const cur = this._find(this.msgId);
        if (!cur) return;
        this._patch(this.msgId, {
          content: content || '网络开小差了，再试一次？',
          citations: Array.isArray(res && res.citations) ? res.citations : [],
          suggestions: Array.isArray(res && res.suggestions) ? res.suggestions.slice(0, 3) : [],
          consultationId: (res && res.consultation_id) || null,
          streaming: false,
          error: false,
          thinking: (cur.thinking || []).map((s) => ({ text: s.text, state: 'done' })),
          // 回退成功 = 完成态：同样自动收起为一行（thinkLabel 派生「思考完成」）
          thinkCollapsed: true,
        });
        this.streaming = false;
        this.typing = false;
        this.task = null;
        this.tick++;
        this._emit({ autoScroll: true });
        this._save();
        this._nextQueued();
        return;
      } catch (e2) {
        console.warn('[StreamHost] 回退普通请求也失败:', e2 && e2.message);
      }
    }

    this._patch(this.msgId, {
      streaming: false,
      error: true,
      // 部分内容已输出时保留；否则给一句兜底提示
      content: hasPartial ? msg.content : (msg.content || '网络开小差了，再试一次？'),
      thinking: (msg.thinking || []).map((s) => ({ text: s.text, state: 'done' })),
    });
    this.streaming = false;
    this.typing = false;
    this.task = null;
    this.tick++;
    this._emit();
    this._save();
    if (!hasPartial) this.notify('生成失败，可点「重试」');
    this._nextQueued();
  }

  /* 队列处理：当前完成 → 处理下一条（豆包式连续对话；页面销毁时同样推进） */
  _nextQueued() {
    if (!this.queue.length) return;
    const next = this.queue.shift();
    setTimeout(() => this._startStream(next.text, next.tag, next), 60);
  }

  /* 流式看门狗：每次收到流事件即续命；超时 → 中止任务 + 走失败流程 */
  _resetWatchdog() {
    this._clearWatchdog();
    this.watchdog = setTimeout(() => {
      this.watchdog = null;
      if (this.streaming) {
        this.watchdogFired = true;   // 抑制随后的迟到 abort 回调（避免双重收尾）
        if (this.task) {
          try { this.task.abort(); } catch (e) { /* ignore */ }
          this.task = null;
        }
        this._onError(new Error('回复超时（90 秒无新内容）'));
      }
    }, CHUNK_GAP_TIMEOUT_S * 1000);
  }

  _clearWatchdog() {
    if (this.watchdog) {
      clearTimeout(this.watchdog);
      this.watchdog = null;
    }
  }

  _clearFlush() {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    this.chunkAccum = '';
  }

  /* 冲刷未 flush 的正文增量（收尾路径调用：done/abort/error 可能紧跟在 chunk 后到达，
     chunkAccum 若未到 50ms flush 窗口，_clearFlush 会把它连同消息尾部一起丢弃） */
  _flushAccum() {
    if (!this.chunkAccum) return;
    const delta = this.chunkAccum;
    this.chunkAccum = '';
    const msg = this._find(this.msgId);
    if (!msg) return;
    this._patch(this.msgId, { content: msg.content + delta });
  }

  _patch(id, patch) {
    const idx = this._indexOf(id);
    if (idx < 0) return;
    this.messages = this.messages.slice();
    this.messages[idx] = Object.assign({}, this.messages[idx], patch);
  }

  _find(id) {
    const idx = this._indexOf(id);
    return idx >= 0 ? this.messages[idx] : null;
  }

  _indexOf(id) {
    for (let i = 0; i < this.messages.length; i++) {
      if (this.messages[i].id === id) return i;
    }
    return -1;
  }

  /* 历史持久化（页面销毁后由宿主继续写盘） */
  _save() {
    try {
      wx.setStorageSync(STORAGE_KEY, this.messages.slice(-50));
    } catch (e) {
      console.warn('[StreamHost] 历史保存失败');
    }
  }
}

module.exports = new StreamHost();
