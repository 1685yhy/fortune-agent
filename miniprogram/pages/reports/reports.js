// 命书 — 线装书（原型 BookScreen：封面 + 卷四章目 + 竖排落款；已非 tab 页，入口在我的页「命书」行）
// v1.3：卷章目/封面可点击 → 拉取该卷报告全文（GET /api/reports/{id}）→ 墨韵弹层展示；
//       后端报告列表项映射为卷目（scenarioLabel 作卷名、日期/标签作小注）；
//       无报告时展示友好空态「暂无命书 · 去聊一段生成」。
const api = require('../../utils/api');
const theme = require('../../utils/theme');

const CN_NUM = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];

/* 原型 BOOKS（dir_b.html 908-913 行）：后端不可用时的四卷默认 */
const DEFAULT_BOOKS = [
  { noText: '卷一', title: '生辰命格', sub: '落笔于开卷之夜 · 四柱既定', page: '一', read: true },
  { noText: '卷二', title: '性情 · 紫微', sub: '命宫天同 · 性缓心宽', page: '二', read: false },
  { noText: '卷三', title: '流年运势', sub: '丙午年 · 六事当记', page: '三', read: false },
  { noText: '卷四', title: '解梦手札', sub: '已存 12 记 · 墨未干', page: '四', read: false },
];

/* 后端列表项 {id,date,scenario,scenarioLabel,summary,score,tags,note?} → 卷目模型 */
function withNoText(list) {
  return list.map((r, i) => Object.assign({}, r, {
    noText: '卷' + (CN_NUM[i] || String(i + 1)),
    page: CN_NUM[i] || String(i + 1),
    read: i === 0,
    title: r.scenarioLabel || '命书',
    sub: [
      r.date || '',
      (Array.isArray(r.tags) && r.tags[0] && r.tags[0] !== r.scenarioLabel) ? r.tags[0] : '',
      r.score ? r.score + ' 分' : '',
    ].filter(Boolean).join(' · '),
  }));
}

Page({
  data: {
    navOff: 0,
    books: [],                 // 卷目（空 = 加载中/空态；后端不可用回退 DEFAULT_BOOKS）
    loaded: false,
    curTab: 'book',
    dark: false,
    sharing: false,
    /* 卷章全文弹层（墨韵线装书：宣纸底 / 竖排标题 / 印章） */
    detail: { show: false, noText: '', title: '', date: '', score: 0, luckyColor: '', luckyDirection: '', luckyNumber: '', fullContent: '' },
    loadingDetail: false,
  },

  onLoad() {
    this._initNavOff();
    this._loadBooks();
    theme.bindTheme(this);
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 后端数据绑定（api.js 契约：getReports → 卷目列表；getReportDetail → 全文） */
  _loadBooks() {
    api.getReports(1, 20)
      .then((res) => {
        const reports = (res && res.reports) || [];
        const books = reports.length > 0 ? withNoText(reports).slice(0, 20) : [];
        this.setData({ books, loaded: true });
      })
      .catch(() => {
        console.warn('[Reports] API 不可用，保持原型四卷');
        this.setData({ books: DEFAULT_BOOKS, loaded: true });
      });
  },

  /* 点卷章目 → 拉取该卷全文（墨韵弹层展示） */
  onBookTap(e) {
    const id = e.currentTarget.dataset.id;
    if (!id) {
      wx.showToast({ title: '暂无命书内容，去聊一段生成吧', icon: 'none' });
      return;
    }
    this._openDetail(id);
  },

  /* 点封面 → 打开第一卷 */
  onCoverTap() {
    const first = this.data.books.find((b) => b && b.id) || this.data.books[0];
    if (!first || !first.id) {
      wx.showToast({ title: '暂无命书内容，去聊一段生成吧', icon: 'none' });
      return;
    }
    this._openDetail(first.id);
  },

  _openDetail(id) {
    if (this.data.loadingDetail) return;
    this.setData({ loadingDetail: true });
    wx.showLoading({ title: '开卷中…', mask: true });
    api.getReportDetail(id)
      .then((res) => {
        const r = (res && res.report) || {};
        const book = this.data.books.find((b) => String(b.id) === String(id)) || {};
        this.setData({
          detail: {
            show: true,
            noText: book.noText || '',
            title: r.scenarioLabel || book.title || '命书',
            date: r.date || '',
            score: r.score || 0,
            luckyColor: r.luckyColor || '',
            luckyDirection: r.luckyDirection || '',
            luckyNumber: r.luckyNumber || '',
            fullContent: String(r.fullContent || ''),
          },
        });
      })
      .catch(() => {
        wx.showToast({ title: '开卷失败，请稍后再试', icon: 'none' });
      })
      .then(() => {
        wx.hideLoading();
        this.setData({ loadingDetail: false });
      });
  },

  closeDetail() {
    this.setData({ detail: { show: false, noText: '', title: '', date: '', score: 0, luckyColor: '', luckyDirection: '', luckyNumber: '', fullContent: '' } });
  },

  /* 空态：去聊一段（回聊天页生成命书） */
  goChat() {
    wx.reLaunch({ url: '/pages/chat/chat' });
  },

  /* fix-later: 命书自「我的」进入 → 返回我的页（原「返回今日」与入口不符） */
  goToday() {
    wx.reLaunch({ url: '/pages/me/me' });
  },

  /* 分享当前命书（navrow 分享按钮）：api.generateShareCard(reportId)
     后端返回 {imageUrl, card}：有图 → 下载后调起分享面板；仅结构化数据 → 提示 */
  async onShare() {
    if (this.data.sharing) return;
    const books = this.data.books || [];
    const first = books.find((b) => b && b.id) || books[0];
    if (!first || !first.id) {
      wx.showToast({ title: '暂无命书可分享，先去聊一卦吧', icon: 'none' });
      return;
    }
    this.setData({ sharing: true });
    try {
      const res = await api.generateShareCard(first.id);
      const imageUrl = res && (res.imageUrl || (res.card && res.card.imageUrl));
      if (imageUrl) {
        wx.showLoading({ title: '生成分享卡...', mask: true });
        try {
          const dl = await new Promise((resolve, reject) => {
            wx.downloadFile({
              url: imageUrl,
              success: (r) => (r.statusCode === 200 ? resolve(r.tempFilePath) : reject(new Error('下载失败'))),
              fail: reject,
            });
          });
          wx.hideLoading();
          wx.showShareImageMenu({
            path: dl,
            fail: () => wx.showToast({ title: '分享已取消', icon: 'none' }),
          });
        } catch (err) {
          wx.hideLoading();
          wx.showToast({ title: '分享图生成中，请稍后再试', icon: 'none' });
        }
      } else {
        wx.showToast({ title: '分享卡生成中，请稍后再试', icon: 'none' });
      }
    } catch (err) {
      wx.showToast({ title: '分享功能暂不可用', icon: 'none' });
    } finally {
      this.setData({ sharing: false });
    }
  },

  noop() { /* 弹层内吞掉背景滚动/穿透 */ },
});
