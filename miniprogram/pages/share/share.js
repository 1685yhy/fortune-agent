// 分享笺 — 对话多选分享页（v1.3）
// 数据源：聊天页多选 → multiShare 写入 ylm_share_msgs（选中的 2-6 条消息）→ navigateTo 本页
// 流程：问答分组（用户问+明灯答成组）→ Canvas 2d 绘制墨韵分享卡（宣纸/墨/朱砂/印章「明灯」）
//       → 预览 → 保存到相册 / 分享图片（wx.shareImageMessage，失败降级保存）
const shareCard = require('../../utils/shareCard');

Page({
  data: {
    navOff: 0,
    dark: false,
    pairs: [],          // [{u, tag, content}]（渲染层同构预览）
    dateText: '',
    imgPath: '',        // 生成的分享卡临时路径
    generating: true,
  },

  onLoad() {
    this._initNavOff();
    try {
      this._msgs = wx.getStorageSync('ylm_share_msgs');
    } catch (e) {
      this._msgs = [];
    }
    if (!Array.isArray(this._msgs) || this._msgs.length < 2) {
      wx.showToast({ title: '分享内容缺失，请重新勾选', icon: 'none' });
      setTimeout(() => wx.navigateBack({}), 700);
      return;
    }
    this._buildPairs();
    this.setData({ dateText: this._dateText() });
  },

  /* 页面渲染完成后再取 canvas 节点绘制 */
  onReady() {
    if (this.data.pairs.length) this._draw();
  },

  onShareAppMessage() {
    return {
      title: '易理明灯 · 夜话拾笺',
      path: '/pages/chat/chat',
    };
  },

  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 今天日期文案（公历，如「八月十六 · 灯下」） */
  _dateText() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getMonth() + 1}月${pad(d.getDate())}日`;
  },

  /* 消息流 → 问答组：用户消息作为「问」，其后 AI 回复作为「答」成组 */
  _buildPairs() {
    const msgs = (this._msgs || []).slice(0, 6);
    const pairs = [];
    let curU = '';
    msgs.forEach((m) => {
      if (!m) return;
      if (m.role === 'user') {
        curU = String(m.content || '');
      } else if (m.role === 'ai') {
        pairs.push({
          u: curU,
          tag: m.tag || '明灯 · 夜话',
          content: String(m.content || ''),
        });
        curU = '';
      }
    });
    if (!pairs.length) {
      // 只选了用户消息（无 AI 回复）→ 兜底成单组（问=首条，答=末条用户消息占位提示）
      const anyMsg = msgs[0];
      pairs.push({
        u: String(anyMsg && anyMsg.content || ''),
        tag: '明灯 · 夜话',
        content: '这句心事，明灯收下了。想听听明灯的回应，回到对话里把这条问出来吧。',
      });
    }
    this.setData({ pairs });
  },

  /* Canvas 2d 绘制 → 预览 */
  _draw() {
    this.setData({ generating: true });
    const query = wx.createSelectorQuery();
    query.select('#shareCanvas')
      .fields({ node: true, size: true })
      .exec((res) => {
        if (!res || !res[0] || !res[0].node) {
          wx.showToast({ title: '生成失败，请重试', icon: 'none' });
          this.setData({ generating: false });
          return;
        }
        const canvas = res[0].node;
        shareCard.drawChatCard({
          pairs: this.data.pairs,
          dateText: this.data.dateText,
        }, canvas, (tempFilePath) => {
          this.setData({ generating: false });
          if (tempFilePath) {
            this.setData({ imgPath: tempFilePath });
          } else {
            wx.showToast({ title: '生成图片失败', icon: 'none' });
          }
        });
      });
  },

  /* 保存到相册（复用缘笺的授权流程） */
  onSave() {
    shareCard.saveCardToAlbum(this.data.imgPath);
  },

  /* 分享图片给好友/朋友圈（失败自动降级保存到相册） */
  onShareImage() {
    shareCard.shareCard(this.data.imgPath, '易理明灯 · 夜话拾笺');
  },

  goBack() {
    wx.navigateBack({});
  },
});
