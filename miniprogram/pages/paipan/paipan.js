// 排盘结果页（L4）— 输入生辰 → POST /api/paipan → 墨韵盘面
// 布局按原型 index.html 落地：命主信息 → 四柱盘面 → 司令交运 → 五行能量 →
// 大运时间线 → 流年胶囊 → 干支关系 → 神煞标签 → 称骨卡；点文字查知识弹层。
// B3-4：档案默认命主自动预填（persons.findDefault，与合盘页同款体验）+
// 排盘历史回看（GET /api/paipan/history 只显示自己的 · 脱敏摘要；
// 点击回看走详情接口，重看 0 重跑，不再落新记录）。
// 隐私红线：生辰只随请求内存排盘（后端不落库），本页不写任何本地存储。
const api = require('../../utils/api');
const theme = require('../../utils/theme');
const persons = require('../../utils/persons');
const guide = require('../../utils/guide');
const lunar = require('../../utils/lunar');

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
// 地支 → 生肖（B3-4 兜底：聊天路径缩减记录无 meta.zodiac，从年柱地支推导）
const SHENGXIAO = { 子: '鼠', 丑: '牛', 寅: '虎', 卯: '兔', 辰: '龙', 巳: '蛇',
  午: '马', 未: '羊', 申: '猴', 酉: '鸡', 戌: '狗', 亥: '猪' };
// B3-4 农历兜底：lunar 名在 _buildView 内被 meta.lunar 遮蔽，模块级起别名
const lunarTextForDate = lunar.lunarDateText;
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

/** 农历生辰 → 公历 'YYYY-MM-DD'（排盘引擎只吃公历；转换失败回落原文，与 hehun 同款） */
function lunarDateToSolar(dateStr) {
  const parts = String(dateStr || '').split('-');
  const y = parseInt(parts[0], 10) || 0;
  const m = parseInt(parts[1], 10) || 0;
  const d = parseInt(parts[2], 10) || 0;
  const s = lunar.lunar2solar(y, m, d, false);
  if (!s) return dateStr;
  return `${s.year}-${String(s.month).padStart(2, '0')}-${String(s.day).padStart(2, '0')}`;
}

Page({
  data: {
    dark: false,

    /* ── 输入表单 ── */
    noArchive: false,     // Q3：本地无档案 → 显示「还没建档」建档口径引导（E1 同款文案）
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

    /* ── B3-4 档案预填 ── */
    prefillNote: '',      // 「已从档案预填：XX」提示（无档案/拉取失败为空）

    /* ── B3-4 历史回看 ── */
    showHistory: false,   // 历史弹层
    historyLoading: false,
    historyList: [],      // [{id, birth, bazi, day_master, summary, conclusion, created_at, timeText, nameText, baziText}]
    viewingHistory: false,// 正在回看历史详情（结果视图）

    /* ── 盘面视图 ── */
    master: null,         // 命主信息头
    pillars: [],          // 四柱盘面（含藏干/纳音/行运）
    slChips: [],          // 起运/交运/司令三枚
    wuxingRows: [],       // 五行能量行 [{char,cls,count,pct,wangs,wangsCls}]
    wuxingNote: '',       // 论断行
    yongshenChip: '',     // 用神 chip
    dayunView: [],        // 大运步 [{suiText,ganzhi,shishen,years,isCur,isQi}]
    dyScrollInto: '',     // 大运时间线初始定位今运(UX批2 Minor)
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
    this._refreshNoarchive();
    this._prefillDefaultPerson();
  },

  /* 每次回页面刷新未建档引导（Q3）：本地无档案 → 引导条显示；建档返回自动消失 */
  onShow() {
    this._refreshNoarchive();
  },

  /* Q3：未建档点「排盘」（E1 提示条/导览卡/测算页）落地本页 → 建档口径引导。
     纯判定 guide.shouldShowPaipanNoarchGuide（node 单测）；不复用 E1 关闭标记：
     引导条不新增 storage 键，跟随有无档案自然显隐 */
  _refreshNoarchive() {
    const show = guide.shouldShowPaipanNoarchGuide(persons.hasLocalArchive());
    if (this.data.noArchive !== show) this.setData({ noArchive: show });
  },

  /* ════════ 表单 ════════ */

  /* B3-4：加载自动预填档案默认命主（与合盘页 hehun.loadDefaultSelf 同款体验）。
     无档案/生辰不完整/拉取失败 → 静默跳过（表单留空由用户手填）。 */
  _prefillDefaultPerson() {
    persons.loadPersons().then((list) => {
      this._persons = Array.isArray(list) ? list : [];
      const def = persons.findDefault(list);
      if (def) this._fillFromPerson(def);
    }).catch(() => {
      this._persons = [];
    });
  },

  _fillFromPerson(p) {
    if (!p || !p.birth_year || !p.birth_month || !p.birth_day) return;
    const pad = (n) => String(n).padStart(2, '0');
    let date = `${p.birth_year}-${pad(p.birth_month)}-${pad(p.birth_day)}`;
    // 档案为农历 → 换算公历（排盘引擎只吃公历），历法列统一显示公历
    if (p.calendar === 'lunar') date = lunarDateToSolar(date);
    const parts = String(date || '').split('-');
    const patch = {
      bCal: 'solar',
      bDate: date,
      bDateText: parts.length === 3
        ? `${parts[0]}年${parseInt(parts[1], 10)}月${parseInt(parts[2], 10)}日`
        : '',
      bGender: p.gender === 'female' ? 'female' : 'male',
    };
    // 时辰（选填）：档案有时辰才回填
    if (p.birth_hour !== undefined && p.birth_hour !== null && p.birth_hour !== '') {
      patch.bHourIdx = persons.hourToShichenIndex(p.birth_hour) + 1;
      patch.bHourSet = true;
    } else {
      patch.bHourIdx = 0;
      patch.bHourSet = false;
    }
    // 出生地（选填）：展示用「省·市」，请求用裸市名（真太阳时修正可命中）
    if (p.city) {
      patch.bCity = String(p.city);
      patch.bCityName = String(p.city).split('·').pop().trim() || '北京';
      patch.bCitySet = true;
    } else {
      patch.bCity = '';
      patch.bCityName = '';
      patch.bCitySet = false;
    }
    patch.prefillNote = p.name ? `已从档案预填：${p.name}` : '已从档案预填';
    this.setData(patch);
  },

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
    this.setData({ chart: null, submitted: false, errorMsg: '', viewingHistory: false });
    wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  },

  /* ════════ 历史回看（B3-4：只显示自己的 · 脱敏摘要） ════════ */

  _timeText(iso) {
    if (!iso) return '';
    const t = new Date(String(iso).replace(' ', 'T'));
    if (Number.isNaN(t.getTime())) return String(iso);
    return `${t.getFullYear()}-${_pad(t.getMonth() + 1)}-${_pad(t.getDate())} ${_pad(t.getHours())}:${_pad(t.getMinutes())}`;
  },

  /* 命主名：person_id → 档案姓名；无档案（表单排盘）→ 性别 + 出生年份兜底 */
  _historyName(r) {
    const plist = this._persons || [];
    const p = plist.find((x) => String(x.id) === String(r.person_id));
    if (p && p.name) return p.name;
    const b = r.birth || {};
    const genderCN = b.gender === 'female' ? '女' : '男';
    return b.year ? `${genderCN}命 · ${b.year}年` : `${genderCN}命`;
  },

  onShowHistory() {
    if (this.data.historyLoading) return;
    this.setData({ showHistory: true, historyLoading: true });
    api.paipanHistory().then((res) => {
      const list = ((res && res.records) || []).map((r) => Object.assign({}, r, {
        timeText: this._timeText(r.created_at),
        nameText: this._historyName(r),
        baziText: (r.bazi || []).join(' '),
      }));
      this.setData({ historyList: list, historyLoading: false });
    }).catch(() => {
      this.setData({ historyLoading: false });
      wx.showToast({ title: '历史记录加载失败，请重试', icon: 'none' });
    });
  },

  onCloseHistory() {
    this.setData({ showHistory: false });
  },

  /* 点击回看：详情接口返回完整盘面（重看 0 重跑，不再落新记录） */
  onHistoryTap(e) {
    const idx = e.currentTarget.dataset.idx;
    const rec = this.data.historyList[idx];
    if (!rec) return;
    wx.showLoading({ title: '读取中...', mask: true });
    api.paipanHistoryDetail(rec.id).then((d) => {
      wx.hideLoading();
      if (!d || !d.chart) throw new Error('记录为空');
      const b = d.birth || {};
      const payload = {
        birthYear: b.year, birthMonth: b.month, birthDay: b.day,
        birthHour: b.hour, gender: b.gender || 'male',
        city: b.city || '北京', minute: b.minute,
      };
      this._buildView(d.chart, payload);
      this.setData({
        chart: d.chart,
        submitted: true,
        viewingHistory: true,
        showHistory: false,
        errorMsg: '',
        // 回看场景：命主头/页脚时间显示记录本身的排盘时间（非今天）
        master: Object.assign({}, this.data.master, {
          time: this._timeText(d.created_at),
        }),
        footDate: this._timeText(d.created_at),
      });
      wx.pageScrollTo({ scrollTop: 0, duration: 0 });
    }).catch((err) => {
      wx.hideLoading();
      wx.showToast({
        title: (err && (err.detail || err.message)) || '读取失败，请重试',
        icon: 'none',
      });
    });
  },

  /* ════════ 视图构建 ════════ */

  _buildView(c, p) {
    const now = new Date();
    const birthYear = parseInt(p.birthYear, 10);
    const genderCN = p.gender === 'female' ? '女' : '男';

    /* 四柱盘面：后端字段 is_day → 视图 isDay（camelCase）；expanded 为柱身展开态。
       B3-4 兜底：聊天/工具路径的缩减记录无 pillars → 用 bazi/shishen/nayin 组装盘面。 */
    const rawPillars = c.pillars || [];
    const pillars = rawPillars.length
      ? rawPillars.map((x) => ({
          name: x.name, ganzhi: x.ganzhi, gan: x.gan, zhi: x.zhi,
          shishen: x.shishen, isDay: !!x.is_day,
          canggan: x.canggan || [], nayin: x.nayin || '', xingyun: x.xingyun || '',
          expanded: false,
        }))
      : (c.bazi || []).map((gz, i) => ({
          name: ['年柱', '月柱', '日柱', '时柱'][i] || '',
          ganzhi: gz, gan: gz[0] || '', zhi: gz[1] || '',
          shishen: (c.shishen || [])[i] || '',
          isDay: i === 2,
          canggan: [], nayin: (c.nayin || [])[i] || '', xingyun: '',
          expanded: false,
        }));

    /* 大运时间线（先于起运/司令使用）：聊天路径 dayun 为 [sui, 干支] 二元组 → 归一为对象 */
    const curSui = now.getFullYear() - birthYear + 1; // 当前虚岁（近似）
    const dayunView = (c.dayun || []).map((d, i) => {
      const item = Array.isArray(d) ? { sui: d[0], ganzhi: d[1] } : d;
      const endSui = item.end_sui != null ? item.end_sui : item.sui;
      return {
        sui: item.sui,
        suiText: item.end_sui != null ? `${item.sui}–${item.end_sui}` : `${item.sui}岁起`,
        ganzhi: item.ganzhi,
        shishen: item.shishen || '',
        years: item.start_year != null ? `${item.start_year}–${item.end_year}` : '',
        isQi: i === 0,
        isCur: curSui >= Number(item.sui) && curSui <= Number(endSui),
      };
    });
    // 今运步横向可能屏外: scroll-into-view 初始定位到今运(UX批2 Minor)
    const dyCurIdx = dayunView.findIndex((d) => d.isCur);
    const dyScrollInto = dyCurIdx >= 0 ? `dy-step-${dyCurIdx}` : '';

    /* 命主信息头（B3-4 兜底：缩减记录无 meta → 用入参生辰拼信息头） */
    const monthZhi = (rawPillars[1] && rawPillars[1].zhi)
      || ((c.bazi && c.bazi[1]) ? String(c.bazi[1])[1] || '' : '');
    const monthWx = WUXING_DZ[monthZhi] || '';
    const we = c.wuxing_energy || {};
    const wang = (we.wangshuai || {})[monthWx];
    const meta = c.meta || {};
    const lunar = meta.lunar || {};
    let solarText = meta.solar_text || '';
    let shichenTxt = meta.shichen || '';
    if (!solarText && p.birthYear) {
      solarText = `${p.birthYear}年${p.birthMonth}月${p.birthDay}日`;
      // 存库 birth.hour 为时钟小时 0-23 → 时辰序号（子23-0/丑1-2/…/亥21-22 起时口径，
      // 与服务端 SHICHEN_NAME 同口径；persons.hourToShichenIndex 只管整点代表不适用）
      const idx = Math.floor((((parseInt(p.birthHour, 10) || 0) + 1) % 24) / 2);
      const label = HOUR_OPTIONS[idx + 1] || '';
      shichenTxt = label.split('(')[0];
      // 农历兜底：公历生辰 → 「四月十七」文案（与排盘引擎同源算法）
      try {
        const lt = lunarTextForDate(Number(p.birthYear), Number(p.birthMonth), Number(p.birthDay));
        if (lt) { lunar.month_text = lt; lunar.day_text = ''; }
      } catch (e) { /* 兜底失败不阻塞渲染 */ }
    }
    // 生肖兜底：缩减记录无 meta.zodiac → 从年柱地支推导（戊辰→龙）
    const zodiac = meta.zodiac
      || ((c.bazi && c.bazi[0]) ? (SHENGXIAO[String(c.bazi[0])[1]] || '') : '') || '';
    const master = {
      seal: genderCN,
      name: `${genderCN} · ${zodiac}命`,
      sub: `公历 ${solarText} · 农历${lunar.year_ganzhi || ''}年${lunar.month_text || ''}${lunar.day_text || ''}${shichenTxt}`,
      tag: `${monthZhi}月 · ${monthWx}${wang || ''}`,
      time: `排盘 ${now.getFullYear()}.${_pad(now.getMonth() + 1)}.${_pad(now.getDate())}`,
    };

    /* 起运 / 交运 / 司令 */
    const qiyunSui = (dayunView[0] && dayunView[0].sui) || 0;
    const jy = c.jiaoyun || {};
    const slChips = [
      { seal: '起', name: '起运', val: `<b>${qiyunSui}</b> 岁`, gold: false },
      { seal: '交', name: '交运', val: `逢<b>${jy.gan_pair || '—'}</b>年 · ${jy.jie || ''}后<b>${jy.days_after_jie || ''}</b>天`, gold: false },
      { seal: '令', name: '司令', val: `<b>${c.siling || ''}</b>${WUXING_TG[c.siling] || ''}当令`, gold: true },
    ];

    /* 五行能量 */
    const counts = we.counts || c.wuxing || {};
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

    /* 大运时间线（dayunView 已在盘面前归一构建；此处只补交运行文案） */
    const jiaoyunLine = `逢<b>${jy.gan_pair || '—'}</b>年 · <b>${jy.jie || ''}后 ${jy.days_after_jie || ''} 天</b>换运 · 司令${c.siling || ''}${WUXING_TG[c.siling] || ''}当令`;

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

    /* 神煞（B3-4 兜底：缩减记录无 shensha_detail → 用名称列表组中性标签） */
    const shenshaView = (c.shensha_detail || []).length
      ? (c.shensha_detail || []).map((s) => ({
          name: s.name,
          src: s.source || '',
          cls: LUCK_CLS[s.luck] || 'is-zhong',
        }))
      : (c.shensha || []).map((name) => ({ name, src: '', cls: 'is-zhong' }));

    /* 称骨（B3-4 兜底：缩减记录无 chenggu → 空卡由 wxml wx:if 隐藏） */
    const cg = c.chenggu || {};
    const chenggu = {
      weight_text: cg.weight_text || '',
      source: '唐 · 袁天罡 称骨歌',
      sub: `${genderCN}命 · ${(c.bazi && c.bazi[0]) || ''}年 · ${(c.bazi && c.bazi[3] && c.bazi[3][1]) || ''}时`,
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
      dayunView, dyScrollInto, jiaoyunLine, liunianView, relGroups, shenshaView,
      chenggu, footDate: master.time.replace('排盘 ', ''),
    });
  },

  /* ════════ 知识弹层 ════════ */

  _openKnowledge(cat, name, src) {
    if (!name) return;
    // 请求序号: 连点多个词时后到者为准, 先完成者不得提前关掉后者的 loading(UX批2 Minor)
    const token = (this._knowToken = (this._knowToken || 0) + 1);
    wx.showLoading({ title: '解析中...', mask: true });
    api.getKnowledge(cat, name)
      .then((item) => {
        if (token !== this._knowToken) return;
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
        if (token !== this._knowToken) return;
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
