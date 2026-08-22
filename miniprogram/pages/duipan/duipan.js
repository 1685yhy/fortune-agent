// 多盘对比（P1-2 前端）— 同一生辰不同时辰两盘差异对照
// 表单（复用 birth-date / place-picker + 双时辰选择器）→ POST /api/duipan →
// 墨韵对照盘面：摘要 → 四柱并排（变化柱朱砂高亮）→ 五行 / 用神·格局 / 大运 /
// 神煞（新增朱砂 · 消失墨灰）差异卡。样式复用排盘页墨韵组件（pp-* / form-*）。
// 隐私红线：生辰只随请求内存排盘（后端不落库），本页不写任何本地存储。
const api = require('../../utils/api');
const theme = require('../../utils/theme');

/* ── 静态表 ── */

// 时辰（12 时辰序号 → 标签；与排盘页同款 picker 文案，序号 1-12 ↔ 时辰序号 0-11）
const HOUR_OPTIONS = ['未填', '子时(23-01)', '丑时(01-03)', '寅时(03-05)', '卯时(05-07)',
  '辰时(07-09)', '巳时(09-11)', '午时(11-13)', '未时(13-15)',
  '申时(15-17)', '酉时(17-19)', '戌时(19-21)', '亥时(21-23)'];

const PILLAR_NAMES = ['年柱', '月柱', '日柱', '时柱'];
const WX_ORDER = ['金', '木', '水', '火', '土'];          // 五行行序（排盘页同序）
const WX_CLS = { 金: 'jin', 木: 'mu', 水: 'shui', 火: 'huo', 土: 'tu' };

Page({
  data: {
    dark: false,

    /* ── 表单 ── */
    bDate: '',            // 'YYYY-MM-DD'
    bCal: 'solar',
    bDateText: '',
    bHourAIdx: 0,         // 0=未填；1-12 对应时辰序号 0-11
    bHourBIdx: 0,
    bHourASet: false,
    bHourBSet: false,
    bGender: 'male',
    bCity: '',            // 展示用 '省·市'
    bCityName: '',        // 请求用裸市名
    hourOptions: HOUR_OPTIONS,

    /* ── 状态 ── */
    loading: false,
    errorMsg: '',
    chart: null,          // 后端原样响应 {pan_a, pan_b, diff, summary}
    submitted: false,

    /* ── 对照视图 ── */
    master: null,         // 头：时辰 A/B + 生辰
    summaryLines: [],     // 摘要要点 [{t, key}]（'；' 拆分，关键差异朱砂）
    panAView: [],         // 四柱 A [{name, ganzhi, shishen, nayin, changed}]
    panBView: [],
    wuxingRows: [],       // 五行差异 [{char, cls, a, b, delta, changed}]
    ysView: null,         // 用神 {a, b, coreA, coreB, same}
    gejuView: null,       // 格局 {a, b, same}
    dayunView: null,      // 大运 {aStart, bStart, gap, seqSame, aQiyun, bQiyun, aSteps, bSteps}
    shenshaView: null,    // 神煞 {added, removed, common}
    footDate: '',
  },

  onLoad() {
    theme.bindTheme(this);
  },

  /* ════════ 表单 ════════ */

  onDateChange(e) {
    const d = e.detail;
    const parts = String(d.date || '').split('-');
    this.setData({
      bCal: d.calendar,
      bDate: d.date,
      bDateText: parts.length === 3 ? `${parts[0]}年${parseInt(parts[1], 10)}月${parseInt(parts[2], 10)}日` : '',
    });
  },
  onHourAChange(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ bHourAIdx: idx, bHourASet: idx > 0 });
  },
  onHourBChange(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ bHourBIdx: idx, bHourBSet: idx > 0 });
  },
  onCityChange(e) {
    this.setData({ bCity: e.detail.full, bCityName: e.detail.city });
  },
  /* 性别：男/女 大按钮（点击切换，与 paipan 同款） */
  onGenderTap(e) {
    this.setData({ bGender: e.currentTarget.dataset.gender });
  },

  /* ════════ 对比 ════════ */

  onCompare() {
    if (this.data.loading) return;
    if (!this.data.bDate) {
      this.setData({ errorMsg: '请选择出生年月日' });
      return;
    }
    if (!this.data.bHourASet || !this.data.bHourBSet) {
      this.setData({ errorMsg: '请分别选择时辰 A 与时辰 B（时辰不同，盘面不同）' });
      return;
    }
    const parts = this.data.bDate.split('-');
    const payload = {
      birthYear: parseInt(parts[0], 10),
      birthMonth: parseInt(parts[1], 10),
      birthDay: parseInt(parts[2], 10),
      hourA: this.data.bHourAIdx - 1,          // 时辰序号 0-11
      hourB: this.data.bHourBIdx - 1,
      gender: this.data.bGender,
      city: this.data.bCityName || '北京',
    };
    this.setData({ loading: true, errorMsg: '' });
    api.duipan(payload)
      .then((chart) => {
        this._buildView(chart, payload);
        this.setData({ chart, submitted: true, loading: false });
        wx.pageScrollTo({ scrollTop: 0, duration: 0 });
      })
      .catch((err) => {
        this.setData({
          loading: false,
          errorMsg: (err && (err.detail || err.message)) || '对比失败，请稍后重试',
        });
      });
  },

  onReset() {
    this.setData({ chart: null, submitted: false, errorMsg: '' });
    wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  },

  /* ════════ 视图构建 ════════ */

  _buildView(c, p) {
    const diff = c.diff || {};
    const panA = c.pan_a || {};
    const panB = c.pan_b || {};
    const now = new Date();
    const _pad = (n) => String(n).padStart(2, '0');

    /* 头：时辰 A/B + 生辰 */
    const hours = diff.hours || {};
    const genderCN = p.gender === 'female' ? '女' : '男';
    const master = {
      seal: '比',
      title: '多盘对比',
      sub: `${p.birthYear}年${p.birthMonth}月${p.birthDay}日 · ${genderCN} · ${p.city || '北京'}`,
      tag: `${hours.a || '时辰A'} vs ${hours.b || '时辰B'}`,
      shA: hours.a || '时辰A',
      shB: hours.b || '时辰B',
      time: `排盘 ${now.getFullYear()}.${_pad(now.getMonth() + 1)}.${_pad(now.getDate())}`,
    };

    /* 摘要：'；' 拆分 → 要点行；首条要点朱砂高亮。
       后端 compare_summary 的「关键差异：」前缀仅在有日主/用神/格局变化时出现,
       否则首段为"四柱中仅…发生变化/两盘日主不同…"——原 `indexOf('关键差异')===0`
       匹配在这些场景永假(死逻辑)。改为: 多条要点时对首行直接高亮
       ("两盘完全相同"单条不标红), UX批2 Minor */
    const lines = String(c.summary || '').split('；').map((t) => t.trim()).filter(Boolean);
    const summaryLines = lines.map((t, i) => ({
      t,
      key: i === 0 && lines.length > 1,
    }));

    /* 四柱变动说明（pp-foot）：如「仅时柱 庚辰→壬午（十神由正官变为正印）」 */
    const changes = (diff.four_pillars || {}).changes || [];
    const pillarNote = changes.length
      ? changes.map((ch) => {
        const extra = ch.a_shishen && ch.a_shishen !== ch.b_shishen
          ? `（十神由${ch.a_shishen}变为${ch.b_shishen}）` : '';
        return `${ch.pillar} ${ch.a}→${ch.b}${extra}`;
      }).join('；')
      : '两盘四柱完全相同';

    /* 四柱并排：两行 A/B；变化柱（diff.four_pillars.changed）朱砂高亮 */
    const changedSet = new Set((diff.four_pillars || {}).changed || []);
    const _pillarRow = (pan) => (pan.bazi || []).map((gz, i) => ({
      name: PILLAR_NAMES[i],
      ganzhi: gz,
      shishen: (pan.shishen || [])[i] || '',
      nayin: (pan.nayin || [])[i] || '',
      changed: changedSet.has(PILLAR_NAMES[i]),
    }));
    const panAView = _pillarRow(panA);
    const panBView = _pillarRow(panB);

    /* 五行差异：A/B counts + delta（变化行朱砂） */
    const wx = diff.wuxing || {};
    const wuxingRows = WX_ORDER.map((w) => {
      const delta = (wx.diff || {})[w] || 0;
      return {
        char: w,
        cls: WX_CLS[w] || '',
        a: (wx.a || {})[w] || 0,
        b: (wx.b || {})[w] || 0,
        delta,
        deltaText: delta > 0 ? `+${delta}` : String(delta),
        changed: delta !== 0,
      };
    });
    const strengthNote = wx.a_strength && wx.b_strength
      ? (wx.a_strength === wx.b_strength
        ? `日主同为${wx.a_strength}`
        : `日主强弱由${wx.a_strength}转为${wx.b_strength}`)
      : '';

    /* 用神 / 格局 */
    const ys = diff.yongshen || {};
    const ysView = {
      a: ys.a || '', b: ys.b || '',
      coreA: ys.core_a || '', coreB: ys.core_b || '',
      same: !!ys.same, coreSame: !!ys.core_same,
    };
    const gj = diff.geju || {};
    const gejuView = { a: gj.a || '', b: gj.b || '', same: !!gj.same };

    /* 大运：起运岁数 / 序列 / 前四步对照 */
    const dy = diff.dayun || {};
    const dayunView = {
      aStart: dy.a_start, bStart: dy.b_start,
      gap: dy.start_gap, seqSame: !!dy.sequence_same,
      gapText: (dy.start_gap || 0) === 0
        ? '起运同岁'
        : `相差${Math.abs(dy.start_gap || 0)}岁`,
      aQiyun: dy.a_qiyun || '', bQiyun: dy.b_qiyun || '',
      aSteps: (dy.a || []).slice(0, 4),
      bSteps: (dy.b || []).slice(0, 4),
    };

    /* 神煞：新增朱砂 / 消失墨灰 / 共有计数 */
    const ss = diff.shensha || {};
    const shenshaView = {
      added: ss.added || [], removed: ss.removed || [],
      common: ss.common || [], same: !!ss.same,
    };

    this.setData({
      master, summaryLines, panAView, panBView, wuxingRows,
      ysView, gejuView, dayunView, shenshaView,
      pillarNote, strengthNote,
      footDate: master.time.replace('排盘 ', ''),
    });
  },
});
