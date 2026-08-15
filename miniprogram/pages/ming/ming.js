// AI 取名 · 名笺 — 输入(姓氏+性别+期望+风格chips+自定义) → 免费 5 名(五维评分+雷达+出处+补益标签)
// → 深度报告(契合度矩阵/改名对比/备选15) → 墨韵名笺(收藏/分享)
// 原型 S4/S15: ming-input → ming-result(5名卡+雷达, 第4/5名锁) → ming-report(名笺)
// 付费边界服务端强制; 体验模式全免费; 会员深度报告免费
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const payment = require('../../utils/payment');
const shareCard = require('../../utils/shareCard');

const STYLE_CHIPS = ['文雅', '大气', '古典', '现代', '诗意'];
const RADAR_LABELS = ['音形义', '五行', '数理', '笔画', '性别'];
const WX_COLORS = { 金: '#C8A15A', 木: '#5E7A63', 水: '#3F5A6B', 火: '#A93A2C', 土: '#9A8B71' };

function bjDay() {
  const d = new Date();
  const off = d.getTimezoneOffset() * 60000 + 8 * 3600000;
  const bj = new Date(d.getTime() + off);
  return `${bj.getUTCFullYear()}-${String(bj.getUTCMonth() + 1).padStart(2, '0')}-${String(bj.getUTCDate()).padStart(2, '0')}`;
}

/* 解析出生日期输入: 2026.05.20 / 2026-05-20 → {y,m,d}; 空 → null */
function parseBirth(str) {
  const s = (str || '').trim();
  if (!s) return null;
  const m = s.match(/^(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})日?$/);
  if (!m) return null;
  const y = parseInt(m[1], 10), mo = parseInt(m[2], 10), d = parseInt(m[3], 10);
  if (y < 1900 || y > 2100 || mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  return { y, m: mo, d };
}

Page({
  data: {
    navOff: 0,
    dark: false,
    stage: 'input',          // input | result | report
    mode: 'baby',            // baby 宝宝取名 | adult 成人改名
    surname: '',
    gender: '女',
    birth: '',               // 出生日期(选填)
    currentName: '',         // 成人改名现名
    chips: [],               // 已选风格(接口提交用)
    chipsView: [             // 渲染用(预计算 on, wxml 不做动态键/方法调用)
      { name: '文雅', on: false }, { name: '大气', on: false }, { name: '古典', on: false },
      { name: '现代', on: false }, { name: '诗意', on: false },
    ],
    custom: '',              // 自定义期望
    loading: false,
    names: [],               // 5 名卡
    styleNote: '',
    issues: [],              // 成人免费现名诊断(current_name_issues, ≤5 条)
    genError: '',
    // 单名详情弹层
    detail: null,
    // 付费
    paywall: false,
    purchasing: false,
    // 名笺报告
    report: null,
    reportErr: '',
    saving: false,
    sharing: false,
    unlocked: false,         // 已解锁(本会话内报告已获取)
  },

  onLoad(opts) {
    this._initNavOff();
    theme.bindTheme(this);
    // 从姓名学页「觉得名字不满意？去 AI 取名」跳入: 预填姓氏/性别
    if (opts && opts.surname) {
      this.setData({
        surname: decodeURIComponent(opts.surname),
        gender: opts.gender === '男' ? '男' : '女',
        mode: opts.mode === 'adult' ? 'adult' : 'baby',
      });
    }
  },

  /* 状态栏高度适配(同 celiang/qian) */
  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  goBack() {
    if (this.data.stage !== 'input') {
      this.setData({ stage: 'input', report: null, detail: null, names: [], paywall: false });
      return;
    }
    wx.navigateBack({
      delta: 1,
      fail: () => wx.reLaunch({ url: '/pages/celiang/celiang' }),
    });
  },

  /* ── 输入区 ── */
  onModeChange(e) {
    this.setData({ mode: e.currentTarget.dataset.mode });
  },
  onSurnameInput(e) {
    this.setData({ surname: e.detail.value });
  },
  onGenderTap(e) {
    this.setData({ gender: e.currentTarget.dataset.g });
  },
  onBirthInput(e) {
    this.setData({ birth: e.detail.value });
  },
  onCurrentNameInput(e) {
    this.setData({ currentName: e.detail.value });
  },
  onChipTap(e) {
    const s = e.currentTarget.dataset.s;
    const chips = this.data.chips.slice();
    const chipsView = this.data.chipsView.map((c) => Object.assign({}, c));
    const i = chips.indexOf(s);
    if (i >= 0) {
      chips.splice(i, 1);
      chipsView.forEach((c) => { if (c.name === s) c.on = false; });
    } else {
      chips.push(s);
      chipsView.forEach((c) => { if (c.name === s) c.on = true; });
    }
    this.setData({ chips, chipsView });
  },
  onCustomInput(e) {
    this.setData({ custom: e.detail.value });
  },

  /* ── 生成 5 名(免费) ── */
  onGenerate() {
    if (this.data.loading) return;
    const surname = this.data.surname.trim();
    if (!surname || !/^[一-龥]{1,4}$/.test(surname)) {
      wx.showToast({ title: '请输入 1-4 个汉字姓氏', icon: 'none' });
      return;
    }
    const birth = parseBirth(this.data.birth);
    if (this.data.birth.trim() && !birth) {
      wx.showToast({ title: '出生日期格式：如 2026.05.20', icon: 'none' });
      return;
    }
    if (this.data.mode === 'adult' && !this.data.currentName.trim()) {
      wx.showToast({ title: '成人改名请填写现名', icon: 'none' });
      return;
    }
    this.setData({ loading: true, genError: '' });
    api.genMing({
      surname,
      gender: this.data.gender,
      style_chips: this.data.chips,
      custom_expectation: this.data.custom.trim(),
      mode: this.data.mode,
      current_name: this.data.mode === 'adult' ? this.data.currentName.trim() : '',
      birthYear: birth ? birth.y : null,
      birthMonth: birth ? birth.m : null,
      birthDay: birth ? birth.d : null,
    })
      .then((res) => {
        this.setData({
          loading: false,
          stage: 'result',
          names: res.names || [],
          styleNote: res.style_note || '随心',
          issues: res.current_name_issues || [],
        });
        this._drawRadars();
      })
      .catch((e) => {
        this.setData({ loading: false });
        const detail = (e && e.detail) || '';
        wx.showToast({
          title: (detail && detail.indexOf('次数') >= 0)
            ? '今日免费次数已用完，明日再来或解锁深度报告'
            : '取名失败，请稍后重试',
          icon: 'none',
          duration: 2500,
        });
      });
  },

  onRegen() {
    if (!this.data.loading) this.onGenerate();
  },

  /* ── 五维雷达(每名一图, canvas 2d) ── */
  _drawRadars() {
    const names = this.data.names || [];
    wx.nextTick(() => {
      names.forEach((n, i) => {
        this._createRadar(`radar-${i}`, n.dims);
      });
    });
  },
  _createRadar(id, dims) {
    wx.createSelectorQuery().in(this).select(`#${id}`).fields({ node: true, size: true })
      .exec((res) => {
        if (!res || !res[0] || !res[0].node) return;
        const canvas = res[0].node;
        const dpr = wx.getSystemInfoSync().pixelRatio || 2;
        const size = 172;
        canvas.width = size * dpr;
        canvas.height = size * dpr;
        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        this._paintRadar(ctx, size, dims);
      });
  },
  _paintRadar(ctx, size, dims) {
    const cx = size / 2, cy = size / 2, r = size / 2 - 14;
    const vals = RADAR_LABELS.map((k) => (dims && dims[k]) || 0);
    const pt = (i, rad) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / 5;
      return [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
    };
    const poly = (g) => [0, 1, 2, 3, 4].map((i) => pt(i, r * g).join(',')).join(' ');
    ctx.clearRect(0, 0, size, size);
    ctx.strokeStyle = 'rgba(58,44,30,.16)';
    ctx.lineWidth = 1;
    [0.33, 0.66, 1].forEach((g) => {
      ctx.beginPath();
      ctx.moveTo(pt(0, r * g)[0], pt(0, r * g)[1]);
      [1, 2, 3, 4, 0].forEach((i) => { ctx.lineTo(pt(i, r * g)[0], pt(i, r * g)[1]); });
      ctx.closePath();
      ctx.stroke();
    });
    ctx.strokeStyle = 'rgba(58,44,30,.12)';
    [0, 1, 2, 3, 4].forEach((i) => {
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(pt(i, r)[0], pt(i, r)[1]);
      ctx.stroke();
    });
    const dataPts = vals.map((v, i) => pt(i, r * Math.max(0.08, Math.min(1, v / 100))));
    ctx.beginPath();
    ctx.moveTo(dataPts[0][0], dataPts[0][1]);
    dataPts.slice(1).forEach((p) => ctx.lineTo(p[0], p[1]));
    ctx.closePath();
    ctx.fillStyle = 'rgba(169,58,44,.16)';
    ctx.fill();
    ctx.strokeStyle = '#A93A2C';
    ctx.lineWidth = 1.4;
    ctx.stroke();
    // 五维标签
    ctx.fillStyle = '#9A8B71';
    ctx.font = '9px "PingFang SC", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    RADAR_LABELS.forEach((k, i) => {
      const [x, y] = pt(i, r + 11);
      ctx.fillText(k, x, y);
    });
  },

  /* ── 单名详情弹层(大雷达 + 五维 + 出处/点评 + 评测此名) ── */
  onNameTap(e) {
    const i = e.currentTarget.dataset.i;
    const n = this.data.names[i];
    if (!n) return;
    if (n.locked) {
      wx.showToast({ title: '契合推理已隐藏，解锁深度报告可见', icon: 'none' });
      return;
    }
    const dimsArr = RADAR_LABELS.map((k) => ({ k, v: n.dims[k] || 0 }));
    this.setData({ detail: Object.assign({}, n, { dimsArr }) });
    wx.nextTick(() => {
      this._createRadar('detail-radar', n.dims);
    });
  },
  onDetailClose() {
    this.setData({ detail: null });
  },
  noop() {},

  /* 评测此名 → 姓名学页(互打入口) */
  onEvaluate() {
    const n = this.data.detail;
    if (!n) return;
    wx.navigateTo({
      url: `/pages/xingming/xingming?surname=${encodeURIComponent(n.full.charAt(0) || '')}&givenName=${encodeURIComponent(n.given)}&gender=${this.data.gender === '男' ? 'male' : 'female'}&auto=1`,
    });
  },

  /* ── 付费解锁(服务端强制; 体验模式/会员/已购全免费) ── */
  onUnlock() {
    this.setData({ paywall: true });
  },
  onPaywallClose() {
    this.setData({ paywall: false });
  },
  async onBuyNow() {
    if (this.data.purchasing) return;
    this.setData({ purchasing: true, paywall: false });
    try {
      // 两档定价: 宝宝版 ming_report ¥19.9 / 成人版 ming_report_pro ¥29.9
      const payResult = await payment.purchase(
        this.data.mode === 'adult' ? 'ming_report_pro' : 'ming_report');
      if (!payResult || !payResult.success) return;  // 取消/失败已 toast
      await this._fetchReport();
    } catch (e) {
      console.warn('[ming] 深度报告失败:', e);
      wx.showToast({ title: '报告生成失败，请重试', icon: 'none' });
    } finally {
      this.setData({ purchasing: false });
    }
  },
  onMemberFree() {
    this.setData({ paywall: false });
    this._fetchReport();
  },
  onShowReport() {
    this._fetchReport();
  },
  _fetchReport() {
    if (this.data.purchasing) return Promise.resolve();
    this.setData({ purchasing: true, reportErr: '' });
    const birth = parseBirth(this.data.birth);
    const surname = this.data.surname.trim();
    const name = this.data.detail || this.data.names[0] || {};
    const given = name.given || '';
    if (!surname || !given) {
      this.setData({ purchasing: false });
      wx.showToast({ title: '先生成名字再解锁', icon: 'none' });
      return Promise.resolve();
    }
    return api.reportMing({
      surname,
      gender: this.data.gender,
      given,
      style_chips: this.data.chips,
      custom_expectation: this.data.custom.trim(),
      mode: this.data.mode,
      current_name: this.data.mode === 'adult' ? this.data.currentName.trim() : '',
      birthYear: birth ? birth.y : null,
      birthMonth: birth ? birth.m : null,
      birthDay: birth ? birth.d : null,
    })
      .then((res) => {
        const rep = this._decorateReport((res && res.report) || {});
        this.setData({ purchasing: false, stage: 'report', report: rep, unlocked: true, paywall: false });
      })
      .catch((e) => {
        this.setData({ purchasing: false });
        const detail = (e && e.detail) || '';
        if (detail && detail.indexOf('解锁') >= 0) {
          this.setData({ paywall: true });
        } else {
          wx.showToast({ title: '报告生成失败，请重试', icon: 'none' });
        }
        throw e;
      });
  },

  /* ── 名笺: 收藏 / 分享 ── */

  /* 名笺渲染装饰: 五行分布条 + 五格格组(wxml 不支持方法/动态键, 一律预计算) */
  _decorateReport(rep) {
    const bazi = rep && rep.bazi;
    if (bazi && bazi.wuxing && bazi.wuxing.counts) {
      const counts = bazi.wuxing.counts;
      const total = Object.keys(counts).reduce((s, k) => s + counts[k], 0) || 1;
      rep.bazi.wuxing.strongText = (bazi.wuxing.strong || []).join('、');
      rep.bazi.wuxing.weakText = (bazi.wuxing.weak || []).join('、');
      rep._wxRows = ['金', '木', '水', '火', '土'].map((k) => {
        const jian = (bazi.wuxing.strong || []).indexOf(k) >= 0 ? '偏旺'
          : (bazi.wuxing.weak || []).indexOf(k) >= 0 ? '缺 · 需补' : '';
        return {
          k,
          pct: Math.max(6, Math.round((counts[k] / total) * 100)),
          n: counts[k],
          jian,
          jianClass: jian.indexOf('缺') >= 0 ? 'add' : 'xie',
          color: WX_COLORS[k],
        };
      });
    }
    const na = rep && rep.name_analysis;
    if (na && na.wuge) {
      na.wugeCells = Object.keys(na.wuge).map((k) => ({
        k,
        num: na.wuge[k],
        jx: (na.cells && na.cells[k]) || '',
      }));
    }
    return rep;
  },

  onSaveJian() {
    if (this.data.saving || !this.data.report) return;
    this.setData({ saving: true });
    const rep = this.data.report;
    api.saveMing({
      surname: rep.surname || '',
      given: rep.given || '',
      gender: rep.gender || this.data.gender,
      score: (rep.name_analysis && rep.name_analysis.total) || 0,
      style_note: this.data.styleNote || '',
    })
      .then((res) => {
        wx.showToast({
          title: res && res.already ? '这张名笺已在您的收藏中' : '名笺已收藏',
          icon: 'none',
        });
      })
      .catch(() => {
        wx.showToast({ title: '收藏失败，请重试', icon: 'none' });
      })
      .then(() => this.setData({ saving: false }));
  },
  onShareJian() {
    if (this.data.sharing || !this.data.report) return;
    this.setData({ sharing: true });
    const rep = this.data.report;
    const na = rep.name_analysis || {};
    const data = {
      dateText: `${rep.full || ''} · 名笺`,
      solarHint: `${rep.gender || ''} · ${(na.buyi_matrix && na.buyi_matrix.rows || []).map((r) => `${r.k}${r.v}`).join(' ') || '五维评分'}`,
      poemLines: [rep.seal && rep.seal.summary ? rep.seal.summary.slice(0, 14) : '如云舒展，一生从容。', (rep.seal && rep.seal.summary ? rep.seal.summary.slice(14, 28) : '') || ''],
      yiChips: [rep.given ? `推荐 · ${rep.given}` : '', na.total ? `${na.total} 分` : ''],
      sealChar: '名',
    };
    // 复用 shareCard 墨韵卡模式绘制名笺分享图
    wx.createSelectorQuery().in(this).select('#ming-share-canvas').fields({ node: true, size: true })
      .exec((res) => {
        if (!res || !res[0] || !res[0].node) {
          this.setData({ sharing: false });
          wx.showToast({ title: '分享图生成失败', icon: 'none' });
          return;
        }
        const canvas = res[0].node;
        shareCard.drawInkCard(data, canvas, (tmp) => {
          this.setData({ sharing: false });
          if (!tmp) {
            wx.showToast({ title: '分享图生成失败', icon: 'none' });
            return;
          }
          wx.showActionSheet({
            itemList: ['保存到相册', '分享给好友'],
            success: (r2) => {
              if (r2.tapIndex === 0) {
                shareCard.saveCardToAlbum(tmp, () => {
                  wx.showToast({ title: '名笺已保存到相册', icon: 'none' });
                });
              } else if (r2.tapIndex === 1) {
                shareCard.shareCard(tmp, 'AI取名 · 墨韵名笺');
              }
            },
          });
        });
      });
  },

  onShareAppMessage() {
    const rep = this.data.report;
    return {
      title: rep && rep.full ? `${rep.full} · 墨韵名笺，五维评分` : 'AI取名 · 免费 5 名 · 五维评分',
      path: '/pages/ming/ming',
    };
  },
});
