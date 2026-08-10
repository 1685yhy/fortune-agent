// 命书 — 线装书（原型 BookScreen：封面 + 卷四章目 + 竖排落款 + TabBar）
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

function withNoText(list) {
  return list.map((r, i) => Object.assign({}, r, {
    noText: '卷' + (CN_NUM[i] || String(i + 1)),
    page: CN_NUM[i] || String(i + 1),
    read: i === 0,
  }));
}

Page({
  data: {
    navOff: 0,
    books: DEFAULT_BOOKS,
    curTab: 'book',
    dark: false,
    sharing: false,
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

  /* 后端数据绑定（api.js 契约：getReports → 四卷章目） */
  _loadBooks() {
    api.getReports(1, 20)
      .then((res) => {
        const reports = (res && res.reports) || [];
        if (reports.length > 0) {
          this.setData({ books: withNoText(reports).slice(0, 4) });
        }
      })
      .catch(() => {
        console.warn('[Reports] API 不可用，保持原型四卷');
      });
  },

  /* 原型 onBack：返回今日 */
  goToday() {
    wx.reLaunch({ url: '/pages/today/today' });
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

  /* 原型 onTab：底部栏切换 */
  onTab(e) {
    const t = e.currentTarget.dataset.tab;
    const url = { today: '/pages/today/today', chat: '/pages/chat/chat', book: '/pages/reports/reports', me: '/pages/me/me' }[t];
    if (url && !url.includes('/reports/')) wx.reLaunch({ url });
  },
});
