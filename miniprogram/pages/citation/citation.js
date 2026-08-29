// 参考资料详情页（阶段 5·引用交互）：从对话抽屉点某条进入
// 展示：分类角标 + 标题 + 摘要全文 + 网络来源 URL（可复制）
// 数据流：chat 页 navigateTo 前把条目写入 storage（ylm_cite_<msgId>_<idx>）
Page({
  data: {
    item: null,
    icon: '/assets/images/ic-book.png',
    typeLabel: '古籍',
  },

  onLoad(options) {
    const msgId = options.msgId || '';
    const idx = options.idx || '0';
    let item = null;
    try {
      item = wx.getStorageSync('ylm_cite_' + msgId + '_' + idx) || null;
    } catch (e) {
      item = null;
    }
    if (!item) {
      wx.showToast({ title: '资料已过期，请回到对话重新打开', icon: 'none' });
      return;
    }
    const map = {
      book: ['/assets/images/ic-book.png', '古籍'], engine: ['/assets/images/ic-engine.png', '引擎'],
      memory: ['/assets/images/ic-memory.png', '记忆'], web: ['/assets/images/ic-web.png', '网络'],
    };
    const [icon, typeLabel] = map[item.type] || map.book;
    this.setData({ item, icon, typeLabel });
  },

  /* 复制网页 URL（网络来源） */
  copyUrl() {
    const url = (this.data.item && this.data.item.url) || '';
    if (!url) return;
    wx.setClipboardData({
      data: url,
      success: () => wx.showToast({ title: '链接已复制', icon: 'none' }),
    });
  },
});
