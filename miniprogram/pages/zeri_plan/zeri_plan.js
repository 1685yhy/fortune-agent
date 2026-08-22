// 择吉日 — 清单页：选定吉日 + 分阶段勾选/备注 + 订阅提醒 + 分享给家人
// 数据流：plan_id → GET /api/zeri/plans/{id} → 勾选 PUT item / 备注 PUT item / 订阅 PUT reminder
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const shareCard = require('../../utils/shareCard');

/* 阶段顺序（与后端 zeri_checklist STAGES 一致） */
const STAGES = ['提前3天', '提前1天', '当天'];

Page({
  data: {
    dark: false,
    planId: '',
    scene: '',
    luckyDate: '',      // 公历 YYYY-MM-DD
    dateText: '',       // 公历展示 9月5日
    lunarText: '',      // 农历+干支+星期
    jishi: '',          // 吉时段
    groups: [],         // [{ stage, items: [{idx, text, done, note}] }]
    reminderOn: false,
    reminderLoading: true,
    itemBusy: -1,       // 正在提交的条目 idx
    noteIdx: -1,        // 正在编辑备注的条目 idx（-1 无）
    noteDraft: '',
    sharing: false,
    error: false,
  },

  onLoad(options) {
    options = options || {};
    theme.bindTheme(this);
    const planId = String(options.plan_id || '').trim();
    if (!planId) {
      this.setData({ error: true });
      return;
    }
    this.setData({ planId });
    this._loadPlan(planId);
  },

  /* ═══ 加载失败重试（UX批3） ═══ */
  onRetryLoad() {
    this.setData({ error: false });
    this._loadPlan(this.data.planId);
  },

  /* 拉取计划详情 → 分组渲染 */
  _loadPlan(planId) {
    api.getZeriPlan(planId).then((res) => {
      const plan = res && res.plan;
      if (!plan) throw new Error('计划不存在');
      const card = plan.card || {};
      const items = Array.isArray(plan.items) ? plan.items : [];
      const groups = STAGES.map((stage) => ({
        stage,
        items: items
          .map((it, idx) => ({ idx, text: it.text || '', done: !!it.done, note: it.note || '', stage: it.stage || '' }))
          .filter((it) => it.stage === stage),
      }));
      this.setData({
        scene: plan.scene || '',
        luckyDate: plan.lucky_date || '',
        dateText: cnDate(plan.lucky_date),
        lunarText: card.lunar_text || '',
        jishi: card.jishi || '',
        groups,
        reminderOn: !!plan.reminder_enabled,
        reminderLoading: false,
        error: false,
      });
    }).catch(() => {
      this.setData({ error: true, reminderLoading: false });
      wx.showToast({ title: '清单加载失败', icon: 'none' });
    });
  },

  /* ═══ 勾选 ═══ */
  onToggleItem(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    if (this.data.itemBusy >= 0) {
      // UX批3：全局锁在途时给视觉反馈，不再静默吞点
      wx.showToast({ title: '正在保存，请稍候', icon: 'none' });
      return;
    }
    const g = this.data.groups;
    const target = findItem(g, idx);
    if (!target) return;
    const prev = target.done;        // 乐观更新前先记旧值（失败回滚基准）
    const next = !prev;
    // 本地先行（体验优先），失败回滚到 prev
    applyItem(g, idx, { done: next });
    // UX批3：乐观更新后必须 setData 渲染，慢网下勾选即时反馈；itemBusy 顺带视觉置灰
    this.setData({ itemBusy: idx, groups: g });
    api.updateZeriItem(this.data.planId, { idx, done: next })
      .then((res) => {
        this.setData({ itemBusy: -1 });
        // 以服务端返回为准（items 数组全量）
        if (res && Array.isArray(res.items)) {
          this._rehydrateItems(res.items);
        }
      })
      .catch(() => {
        applyItem(this.data.groups, idx, { done: prev });
        this.setData({ itemBusy: -1, groups: this.data.groups });
        wx.showToast({ title: '保存失败，请重试', icon: 'none' });
      });
  },

  /* ═══ 备注（长按或编辑钮 → 输入弹层） ═══ */
  onNoteTap(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const target = findItem(this.data.groups, idx);
    if (!target) return;
    this.setData({ noteIdx: idx, noteDraft: target.note || '' });
  },

  onNoteInput(e) {
    this.setData({ noteDraft: e.detail.value });
  },

  onNoteCancel() {
    this.setData({ noteIdx: -1, noteDraft: '' });
  },

  noop() { /* 阻止遮罩点击/滚动穿透 */ },

  onNoteSave() {
    const idx = this.data.noteIdx;
    if (idx < 0) return;
    const note = String(this.data.noteDraft || '').trim();
    const target = findItem(this.data.groups, idx);
    const prevNote = target ? target.note : '';   // 保存前记旧备注（失败回滚基准）
    applyItem(this.data.groups, idx, { note });
    this.setData({ noteIdx: -1, noteDraft: '', itemBusy: idx, groups: this.data.groups });
    api.updateZeriItem(this.data.planId, { idx, note })
      .then((res) => {
        this.setData({ itemBusy: -1 });
        if (res && Array.isArray(res.items)) this._rehydrateItems(res.items);
        wx.showToast({ title: '备注已存', icon: 'none' });
      })
      .catch(() => {
        applyItem(this.data.groups, idx, { note: prevNote });
        this.setData({ itemBusy: -1, groups: this.data.groups });
        wx.showToast({ title: '保存失败，请重试', icon: 'none' });
      });
  },

  /* 服务端返回 items 全量 → 重新分组（保持 noteIdx/勾选一致） */
  _rehydrateItems(items) {
    const groups = STAGES.map((stage) => ({
      stage,
      items: items
        .map((it, idx) => ({ idx, text: it.text || '', done: !!it.done, note: it.note || '', stage: it.stage || '' }))
        .filter((it) => it.stage === stage),
    }));
    this.setData({ groups });
  },

  /* ═══ 订阅提醒（主动同意制） ═══ */
  onReminderSwitch(e) {
    const on = !!e.detail.value;
    if (on) {
      wx.showModal({
        title: '订阅提醒',
        content: '同意后将在吉日前1天晚和当天早各提醒一次，可随时关闭',
        confirmText: '同意',
        confirmColor: '#A93A2C',
        success: (res) => {
          if (res.confirm) this._setReminder(true);
          else this.setData({ reminderOn: false }); // 拒绝 → 回弹
        },
      });
    } else {
      this._setReminder(false);
    }
  },

  _setReminder(on) {
    if (this.data.reminderLoading) return;
    this.setData({ reminderLoading: true });
    api.setZeriReminder(this.data.planId, on)
      .then(() => {
        this.setData({ reminderOn: on, reminderLoading: false });
        wx.showToast({ title: on ? '已订阅提醒' : '已关闭提醒', icon: 'none' });
      })
      .catch(() => {
        this.setData({ reminderLoading: false, reminderOn: !on });
        wx.showToast({ title: '操作失败，请重试', icon: 'none' });
      });
  },

  /* ═══ 加入日历（微信无系统日历 API → 复制日期信息到剪贴板，如实说明） ═══ */
  onAddCalendar() {
    const d = this.data;
    let lunar = String(d.lunarText || '').split(/\s+/)[0]; // 农历七月十七日
    if (lunar) lunar = lunar.replace(/日$/, '');            // → 农历七月十七
    let copyText = d.dateText || d.luckyDate || '';
    if (lunar) copyText += `（${lunar}）`;
    copyText += `${d.scene || '大事'}吉日`;
    wx.setClipboardData({
      data: copyText,
      success: () => {
        wx.showToast({ title: '已复制，可粘贴到系统日历', icon: 'none', duration: 2200 });
      },
    });
  },

  /* ═══ 分享给家人（shareCard 风格：canvas 绘制吉日笺 → 分享/保存） ═══ */
  onShare() {
    if (this.data.sharing) return;
    this.setData({ sharing: true });
    wx.createSelectorQuery()
      .select('#zeriShareCanvas')
      .fields({ node: true, size: true })
      .exec((res) => {
        const info = res && res[0];
        if (!info || !info.node) {
          this.setData({ sharing: false });
          wx.showToast({ title: '绘制暂不可用', icon: 'none' });
          return;
        }
        const canvas = info.node;
        const dpr = wx.getWindowInfo ? wx.getWindowInfo().pixelRatio : 2;
        canvas.width = 750 * dpr;
        canvas.height = 1200 * dpr;
        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        this._drawPlanCard(ctx, canvas, (tempFilePath) => {
          this.setData({ sharing: false });
          if (!tempFilePath) return;
          // 模拟器无 shareImageMessage（真机才有）→ 降级保存相册
          if (typeof wx.shareImageMessage === 'function') {
            shareCard.shareCard(tempFilePath, `${this.data.scene} · 吉日清单`);
          } else {
            shareCard.saveCardToAlbum(tempFilePath, () => {
              wx.showToast({ title: '已存入相册 · 可发给家人', icon: 'none', duration: 2200 });
            });
          }
        });
      });
  },

  /* 宣纸底 + 墨框 + 朱砂印章 + 清单概要（仿 shareCard.drawInkCard 底稿） */
  _drawPlanCard(ctx, canvas, done) {
    const W = 750, H = 1200;
    const d = this.data;
    // 1. 宣纸底 + 细框 + 顶线
    ctx.fillStyle = '#F5EFE1';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = 'rgba(58,44,30,.45)';
    ctx.lineWidth = 4;
    ctx.strokeRect(28, 28, W - 56, H - 56);
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(28, 54); ctx.lineTo(W - 28, 54); ctx.stroke();
    ctx.textAlign = 'center';

    // 2. 标题
    ctx.fillStyle = '#3A2C1E';
    ctx.font = '600 36px "PingFang SC", sans-serif';
    ctx.fillText(`${d.scene || '大事'} · 吉日清单`, W / 2, 130);

    // 3. 朱砂菱形分隔
    ctx.save();
    ctx.translate(W / 2, 175);
    ctx.rotate(Math.PI / 4);
    ctx.fillStyle = '#A93A2C';
    ctx.fillRect(-7, -7, 14, 14);
    ctx.restore();

    // 4. 吉日
    ctx.fillStyle = '#A93A2C';
    ctx.font = '600 96px "PingFang SC", sans-serif';
    ctx.fillText(d.dateText || d.luckyDate, W / 2, 320);
    ctx.fillStyle = '#6C5B45';
    ctx.font = '28px "PingFang SC", sans-serif';
    ctx.fillText(d.lunarText || '', W / 2, 380);

    // 5. 吉时
    ctx.fillStyle = '#3A2C1E';
    ctx.font = '500 32px "PingFang SC", sans-serif';
    ctx.fillText(`吉时 ${d.jishi || '以当日黄历为准'}`, W / 2, 460);

    // 6. 清单分组（每阶段画到行，超出省略）
    ctx.textAlign = 'left';
    let y = 540;
    ctx.fillStyle = '#9A8B71';
    ctx.font = '24px "PingFang SC", sans-serif';
    ctx.fillText('—— 办事清单 ——', W / 2 - 60, y);
    y += 46;
    (d.groups || []).forEach((g) => {
      if (y > 950) return;
      ctx.fillStyle = '#A93A2C';
      ctx.font = '500 28px "PingFang SC", sans-serif';
      ctx.fillText(`【${g.stage}】`, 80, y);
      y += 42;
      (g.items || []).slice(0, 8).forEach((it) => {
        if (y > 980) return;
        const mark = it.done ? '✓ ' : '□ ';
        const note = it.note ? `（${it.note}）` : '';
        const line = `${mark}${it.text}${note}`;
        ctx.fillStyle = it.done ? '#9A8B71' : '#3A2C1E';
        ctx.font = '26px "PingFang SC", sans-serif';
        ctx.fillText(line.slice(0, 24), 80, y);
        y += 38;
      });
      y += 12;
    });

    // 7. 脚注
    ctx.textAlign = 'center';
    ctx.fillStyle = '#9A8B71';
    ctx.font = '24px "PingFang SC", sans-serif';
    ctx.fillText('易理明灯 · 大事择吉日', W / 2, 1090);

    // 8. 导出
    wx.canvasToTempFilePath({
      canvas,
      success: (r) => done(r.tempFilePath),
      fail: () => done(''),
    });
  },

});

/* ── 纯工具（渲染层分组共用） ── */

function cnDate(isoStr) {
  if (!isoStr) return '';
  const p = String(isoStr).split('-');
  return `${Number(p[1])}月${Number(p[2])}日`;
}

function findItem(groups, idx) {
  for (let i = 0; i < groups.length; i++) {
    const it = groups[i].items.find((x) => x.idx === idx);
    if (it) return it;
  }
  return null;
}

function applyItem(groups, idx, patch) {
  for (let i = 0; i < groups.length; i++) {
    const it = groups[i].items.find((x) => x.idx === idx);
    if (it) {
      if (patch.done !== undefined) it.done = patch.done;
      if (patch.note !== undefined) it.note = patch.note;
      return;
    }
  }
}
