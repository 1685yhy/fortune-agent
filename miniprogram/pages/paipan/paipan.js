// 排盘结果页（L4）— 输入生辰 → POST /api/paipan → 墨韵盘面
// 布局按原型 index.html 落地：命主信息 → 四柱盘面 → 司令交运 → 五行能量 →
// 大运时间线 → 流年胶囊 → 干支关系 → 神煞标签 → 称骨卡；点文字查知识弹层。
// 隐私红线：生辰只随请求内存排盘（后端不落库），本页不写任何本地存储。
const api = require('../../utils/api');
const theme = require('../../utils/theme');

/* ── 静态表 ── */

// 时辰（12 时辰序号 → 标签；与 hehun 页同款 picker 文案）
const HOUR_OPTIONS = ['未填', '子时(23-01)', '丑时(01-03)', '寅时(03-05)', '卯时(05-07)',
  '辰时(07-09)', '巳时(09-11)', '午时(11-13)', '未时(13-15)',
  '申时(15-17)', '酉时(17-19)', '戌时(19-21)', '亥时(21-23)'];

const WX_ORDER = ['金', '木', '水', '火', '土'];          // 五行行序（原型 wuxing-bars 同序）
const WX_CLS = { 金: 'jin', 木: 'mu', 水: 'shui', 火: 'huo', 土: 'tu' };
const WUXING_TG = { 甲: '木', 乙: '木', 丙: '火', 丁: '火', 戊: '土', 己: '土',
  庚: '金', 辛: '金', 壬: '水', 癸: '水' };
const WUXING_DZ = { 子: '水', 丑: '土', 寅: '木', 卯: '木', 辰: '土', 巳: '火',
  午: '火', 未: '土', 申: '金', 酉: '金', 戌: '土', 亥: '水' };
const WANGSHUAI_CLS = { 旺: 'wang', 相: 'xiang', 休: 'xiu', 囚: 'qiu', 死: 'si' };
const SHICHEN_CN = ['子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥'];
// 干支关系 → 徽标吉凶类（伏吟/反吟/盖头/截脚/天克/地冲 → 凶；合 → 吉；争合/妒合 → 中性）
const REL_CLS = {
  伏吟: 'is-xiong', 反吟: 'is-xiong', 天克地冲: 'is-xiong',
  天克: 'is-xiong', 地冲: 'is-xiong', 盖头: 'is-xiong', 截脚: 'is-xiong',
  合: 'is-he', 单合: 'is-he',
  争合: 'is-zhong', 妒合: 'is-zhong',
};
const REL_DEF = [
  { k: '伏吟', d: '与命局同干支' }, { k: '反吟', d: '天克地冲' },
  { k: '盖头', d: '干克支' }, { k: '截脚', d: '支克干' },
  { k: '合', d: '六合相会' }, { k: '争合', d: '两干争一' }, { k: '妒合', d: '合处见妒' },
];
const REL_NOTABLE = ['伏吟', '反吟', '天克地冲', '合', '单合', '争合', '妒合']; // 大运/流年只挑显著关系
const LUCK_CLS = { 吉: 'is-ji', 中性: 'is-zhong', 凶: 'is-xiong' };
// 知识分类（点文字查解析：十神/长生/纳音/神煞/天干/地支）
const KNOW_CATS = {
  shishen: '十神', zhangsheng: '长生', nayin: '纳音',
  shensha: '神煞', tiangan: '天干', dizhi: '地支',
};

function _pad(n) { return String(n).padStart(2, '0'); }

Page({
  data: {
    dark: false,

    /* ── 输入表单 ── */
    bDate: '',            // 'YYYY-MM-DD'（一次选完）
    bCal: 'solar',
    bDateText: '',        // 展示：1999年5月13日
    bHourIdx: 0,          // 0=未填；1-12 对应时辰序号 0-11
    bHourSet: false,
    bGender: 'male',
    bCity: '',            // 展示用 '省·市'
    bCityName: '',        // 请求用裸市名（真太阳时修正可命中）
    bCitySet: false,
    hourOptions: HOUR_OPTIONS,

    /* ── 状态 ── */
    loading: false,
    errorMsg: '',
    chart: null,          // 后端原样响应（保留全字段）
    submitted: false,

    /* ── 盘面视图 ── */
    master: null,         // 命主信息头
    pillars: [],          // 四柱盘面（含藏干/纳音/行运）
    slChips: [],          // 起运/交运/司令三枚
    wuxingRows: [],       // 五行能量行 [{char,cls,count,pct,wangs,wangsCls}]
    wuxingNote: '',       // 论断行
    yongshenChip: '',     // 用神 chip
    dayunView: [],        // 大运步 [{suiText,ganzhi,shishen,years,isCur,isQi}]
    jiaoyunLine: '',      // 大运卡交运行
    liunianView: [],      // 流年胶囊 [{year,ganzhi,nayin,age,shensha,rel,dayun,isNow}]
    relGroups: [],        // 干支关系 [{name,badges:[{name,where,note,cls}]}]
    relDefs: REL_DEF,
    shenshaView: [],      // 神煞 [{name,src,cls}]
    chenggu: null,        // {weight_text,source,sub,parts,jieci}
    footDate: '',         // 排盘日期

    /* ── 知识弹层 ── */
    zsOpen: false,
    zsKind: '',
    zsName: '',
    zsSrc: '',
    zsTabs: [],           // [{t, body}]（纯文本，\n 换行）
    zsTabIdx: 0,

    /* ── 流年详解弹层（批1：逐流年 干支/神煞/与原局关系/大运流年关系） ── */
    lnOpen: false,
    lnSheet: null,        // {gz, src, year, age, nayin, shenshaTags, relBadges, dayun:{ganzhi,sui,rel}}
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
  onHourChange(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ bHourIdx: idx, bHourSet: idx > 0 });
  },
  onCityChange(e) {
    this.setData({ bCity: e.detail.full, bCityName: e.detail.city, bCitySet: !!e.detail.city });
  },
  /* 性别：男/女 大按钮（点击切换） */
  onGenderTap(e) {
    this.setData({ bGender: e.currentTarget.dataset.gender });
  },

  /* 四柱柱身点击：展开/收起 纳音+星运（问真式弱化，默认收起） */
  onPillarTap(e) {
    const idx = parseInt(e.currentTarget.dataset.idx, 10);
    const p = this.data.pillars[idx];
    if (!p) return;
    this.setData({ [`pillars[${idx}].expanded`]: !p.expanded });
  },

  /* ════════ 排盘 ════════ */

  onPaipan() {
    if (this.data.loading) return;
    if (!this.data.bDate) {
      this.setData({ errorMsg: '请选择出生年月日' });
      return;
    }
    if (!this.data.bHourSet) {
      this.setData({ errorMsg: '请选择出生时辰（时辰不同盘面不同）' });
      return;
    }
    const parts = this.data.bDate.split('-');
    const payload = {
      birthYear: parseInt(parts[0], 10),
      birthMonth: parseInt(parts[1], 10),
      birthDay: parseInt(parts[2], 10),
      birthHour: this.data.bHourIdx - 1,          // 时辰序号 0-11
      gender: this.data.bGender,
      city: this.data.bCityName || '北京',
    };
    this.setData({ loading: true, errorMsg: '' });
    api.paipan(payload)
      .then((chart) => {
        this._buildView(chart, payload);
        this.setData({ chart, submitted: true, loading: false });
        // 滚动到盘面（输入区之上留一屏）
        wx.pageScrollTo({ scrollTop: 0, duration: 0 });
      })
      .catch((err) => {
        this.setData({
          loading: false,
          errorMsg: (err && (err.detail || err.message)) || '排盘失败，请稍后重试',
        });
      });
  },

  onReset() {
    this.setData({ chart: null, submitted: false, errorMsg: '' });
    wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  },

  /* ════════ 视图构建 ════════ */

  _buildView(c, p) {
    const now = new Date();
    const birthYear = parseInt(p.birthYear, 10);

    /* 四柱盘面：后端字段 is_day → 视图 isDay（camelCase）；expanded 为柱身展开态 */
    const pillars = (c.pillars || []).map((x) => ({
      name: x.name, ganzhi: x.ganzhi, gan: x.gan, zhi: x.zhi,
      shishen: x.shishen, isDay: !!x.is_day,
      canggan: x.canggan || [], nayin: x.nayin || '', xingyun: x.xingyun || '',
      expanded: false,
    }));

    /* 命主信息头 */
    const genderCN = p.gender === 'female' ? '女' : '男';
    const monthZhi = c.pillars[1].zhi;
    const monthWx = WUXING_DZ[monthZhi] || '';
    const wang = (c.wuxing_energy.wangshuai || {})[monthWx];
    const meta = c.meta || {};
    const lunar = meta.lunar || {};
    const master = {
      seal: genderCN,
      name: `${genderCN} · ${meta.zodiac || ''}命`,
      sub: `公历 ${meta.solar_text || ''} · 农历${lunar.year_ganzhi || ''}年${lunar.month_text || ''}${lunar.day_text || ''}${meta.shichen || ''}`,
      tag: `${monthZhi}月 · ${monthWx}${wang}`,
      time: `排盘 ${now.getFullYear()}.${_pad(now.getMonth() + 1)}.${_pad(now.getDate())}`,
    };

    /* 起运 / 交运 / 司令 */
    const qiyunSui = (c.dayun[0] && c.dayun[0].sui) || 0;
    const jy = c.jiaoyun || {};
    const slChips = [
      { seal: '起', name: '起运', val: `<b>${qiyunSui}</b> 岁`, gold: false },
      { seal: '交', name: '交运', val: `逢<b>${jy.gan_pair || ''}</b>年 · ${jy.jie || ''}后<b>${jy.days_after_jie || ''}</b>天`, gold: false },
      { seal: '令', name: '司令', val: `<b>${c.siling}</b>${WUXING_TG[c.siling] || ''}当令`, gold: true },
    ];

    /* 五行能量 */
    const we = c.wuxing_energy || {};
    const counts = we.counts || {};
    const maxCnt = Math.max(1, ...WX_ORDER.map((wx) => counts[wx] || 0));
    const wuxingRows = WX_ORDER.map((wx) => ({
      char: wx,
      cls: WX_CLS[wx] || '',
      count: counts[wx] || 0,
      pct: Math.round(((counts[wx] || 0) / maxCnt) * 100),
      wangs: (we.wangshuai || {})[wx] || '',
      wangsCls: WANGSHUAI_CLS[(we.wangshuai || {})[wx]] || '',
    }));
    const dmText = c.day_master || '日主';
    const strength = we.strength || '';
    const yongMatch = String(we.yongshen || '').match(/^(.+?)为用神/);
    const yongshenChip = yongMatch ? `用神 · ${yongMatch[1]}` : (we.yongshen || '');
    const wuxingNote = `${monthZhi}月${monthWx}${wang}，日主${dmText}${strength}；${we.yongshen || ''}`;

    /* 大运时间线 */
    const curSui = now.getFullYear() - birthYear + 1; // 当前虚岁（近似）
    const dayunView = (c.dayun || []).map((d, i) => ({
      sui: d.sui,
      suiText: `${d.sui}–${d.end_sui}`,
      ganzhi: d.ganzhi,
      shishen: d.shishen,
      years: `${d.start_year}–${d.end_year}`,
      isQi: i === 0,
      isCur: curSui >= d.sui && curSui <= d.end_sui,
    }));
    const jiaoyunLine = `逢<b>${jy.gan_pair || ''}</b>年 · <b>${jy.jie || ''}后 ${jy.days_after_jie || ''} 天</b>换运 · 司令${c.siling}${WUXING_TG[c.siling] || ''}当令`;

    /* 流年胶囊（批1：全量带 流年神煞/与原局关系/所在大运，点开即弹层详析） */
    const nowYear = c.liunian_rel && c.liunian_rel.year;
    const liunianView = (c.liunian_full || []).map((y) => ({
      year: y.year,
      ganzhi: y.ganzhi,
      nayin: y.nayin || '',
      age: y.age,
      shensha: y.shensha || [],
      rel: y.rel || [],
      dayun: y.dayun || {},
      isNow: y.year === nowYear,
    }));

    /* 干支关系 */
    const relGroups = [
      {
        name: '原局',
        badges: (c.ganzhi_rel || []).map((r) => ({
          name: r.type,
          where: r.between,
          note: r.desc,
          cls: REL_CLS[r.type] || 'is-zhong',
          rel: r,
        })),
      },
      {
        name: '大运',
        badges: [],
      },
      {
        name: '流年',
        badges: (c.liunian_rel && c.liunian_rel.rel || []).map((r) => ({
          name: r.type,
          where: `${c.liunian_rel.year}年`,
          note: r.desc,
          cls: REL_CLS[r.type] || 'is-zhong',
          rel: r,
        })),
      },
    ];
    // 大运：各步显著关系（伏吟/反吟/天克地冲/合/争合/妒合）
    (c.dayun_rel || []).forEach((dr) => {
      (dr.rel || []).forEach((r) => {
        if (!REL_NOTABLE.includes(r.type)) return;
        const dy = dayunView.find((d) => d.suiText.startsWith(String(dr.sui)));
        relGroups[1].badges.push({
          name: r.type,
          where: `${dr.ganzhi} · ${dy ? dy.suiText : dr.sui}岁`,
          note: r.desc,
          cls: REL_CLS[r.type] || 'is-zhong',
          rel: r,
        });
      });
    });

    /* 神煞 */
    const shenshaView = (c.shensha_detail || []).map((s) => ({
      name: s.name,
      src: s.source || '',
      cls: LUCK_CLS[s.luck] || 'is-zhong',
    }));

    /* 称骨 */
    const cg = c.chenggu || {};
    const chenggu = {
      weight_text: cg.weight_text || '',
      source: '唐 · 袁天罡 称骨歌',
      sub: `${genderCN}命 · ${c.bazi[0]}年 · ${c.bazi[3][1]}时`,
      parts: (cg.parts || []).map((pt, i) => ({
        label: pt.label,
        weight: pt.weight,
        isTotal: i === 3,
      })),
      jieci: cg.jieci || '',
    };
    // 末项为合计（共 X两X钱）
    if (chenggu.parts.length >= 4) {
      chenggu.parts[3] = {
        label: '共', weight: cg.weight_text || chenggu.parts[3].weight, isTotal: true,
      };
    }

    this.setData({
      master, pillars, slChips, wuxingRows, wuxingNote, yongshenChip,
      dayunView, jiaoyunLine, liunianView, relGroups, shenshaView,
      chenggu, footDate: master.time.replace('排盘 ', ''),
    });
  },

  /* ════════ 知识弹层 ════════ */

  _openKnowledge(cat, name, src) {
    if (!name) return;
    wx.showLoading({ title: '解析中...', mask: true });
    api.getKnowledge(cat, name)
      .then((item) => {
        wx.hideLoading();
        const tabs = [];
        if (item.tip) tabs.push({ t: '解析', body: item.tip });
        if (item.gujue || item.book1) tabs.push({ t: '歌诀', body: item.gujue || item.book1 });
        if (item.chafa || item.book2) tabs.push({ t: '查法', body: item.chafa || item.book2 });
        if (!tabs.length) tabs.push({ t: '解析', body: `知识库暂无「${name}」条目` });
        this.setData({
          zsOpen: true,
          zsKind: KNOW_CATS[cat] || '详解',
          zsName: item.name || name,
          zsSrc: src || '',
          zsTabs: tabs,
          zsTabIdx: 0,
        });
      })
      .catch(() => {
        wx.hideLoading();
        wx.showToast({ title: '知识解析暂未收录', icon: 'none' });
      });
  },

  /* 十神 / 天干 / 地支 / 纳音 / 行运（长生）点查 */
  onKnowTap(e) {
    const ds = e.currentTarget.dataset;
    this._openKnowledge(ds.cat, ds.name, ds.src || '');
  },

  /* 神煞点查 */
  onShenshaTap(e) {
    const ds = e.currentTarget.dataset;
    this._openKnowledge('shensha', ds.name, ds.src || '');
  },

  /* 流年点查（批1 流年详解）：底部弹层 —— 流年干支/神煞标签/与原局关系徽标/
     所在大运及大运流年关系（30 年全量数据由后端 liunian_full 逐项带出） */
  onLiunianTap(e) {
    const ds = e.currentTarget.dataset;
    const item = ds.item || {};
    if (!item.ganzhi) return;
    // 神煞吉凶类：复用原局 shensha_detail 的 name→luck 映射，未收录按中性
    const luckMap = {};
    (this.data.chart && this.data.chart.shensha_detail || []).forEach((s) => {
      if (s.name && s.luck) luckMap[s.name] = s.luck;
    });
    const shenshaTags = (item.shensha || []).map((name) => ({
      name,
      cls: LUCK_CLS[luckMap[name]] || 'is-zhong',
    }));
    const relBadges = (item.rel || []).map((r) => ({
      name: r.type,
      where: r.between || '',
      note: r.desc || '',
      cls: REL_CLS[r.type] || 'is-zhong',
    }));
    const dayun = item.dayun || {};
    const dayunRel = (dayun.rel || []).map((r) => ({
      name: r.type,
      note: r.desc || '',
      cls: REL_CLS[r.type] || 'is-zhong',
    }));
    const gz = item.ganzhi || '';
    this.setData({
      lnOpen: true,
      lnSheet: {
        gz,
        gan: gz[0] || '',
        zhi: gz[1] || '',
        src: `${item.year}年 · 虚岁${item.age}`,
        year: item.year,
        age: item.age,
        nayin: item.nayin || '',
        shenshaTags,
        relBadges,
        dayun: {
          ganzhi: dayun.ganzhi || '',
          sui: dayun.sui || 0,
          rel: dayunRel,
        },
      },
    });
  },
  onLnClose() {
    this.setData({ lnOpen: false });
  },
  onLnMaskTap() {
    this.setData({ lnOpen: false });
  },

  /* 大运点查：该步大运 vs 原局干支关系 */
  onDayunTap(e) {
    const ds = e.currentTarget.dataset;
    const item = ds.item || {};
    const dr = (this.data.chart && this.data.chart.dayun_rel || [])
      .find((x) => String(x.sui) === String(item.sui));
    const lines = dr && dr.rel && dr.rel.length
      ? dr.rel.map((r) => `${r.type}：${r.between} ${r.desc}`).join('\n')
      : '该步大运与原局无显著干支关系。';
    this._openLocalSheet('大运', `${item.ganzhi}（${item.suiText}岁）`, `${item.years}年`, [
      { t: '解析', body: `${item.ganzhi}（${item.suiText}岁 · ${item.years}年）：十神为${item.shishen}，与原局各柱干支关系如下。\n\n${lines}` },
    ]);
  },

  /* 干支关系徽标点查：该条关系说明 */
  onRelTap(e) {
    const ds = e.currentTarget.dataset;
    const rel = ds.rel;
    this._openLocalSheet('干支关系', `${rel.type}`, rel.between, [
      { t: '解析', body: rel.desc },
      {
        t: '识读',
        body: '伏吟：与命局同干支\n反吟：天克地冲\n盖头：干克支\n截脚：支克干\n合：六合相会\n争合：两干争一\n妒合：合处见妒',
      },
    ]);
  },

  /* 本地弹层（无网络请求） */
  _openLocalSheet(kind, name, src, tabs) {
    this.setData({ zsOpen: true, zsKind: kind, zsName: name, zsSrc: src, zsTabs: tabs, zsTabIdx: 0 });
  },

  onZsTab(e) {
    this.setData({ zsTabIdx: parseInt(e.currentTarget.dataset.idx, 10) });
  },
  onZsClose() {
    this.setData({ zsOpen: false });
  },
  onZsMaskTap() {
    this.setData({ zsOpen: false });
  },
  noop() {},
});
