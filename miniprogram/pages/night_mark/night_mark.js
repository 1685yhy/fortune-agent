// 灯下印记 — 守夜人印章(连续 7 夜)/ 长明灯(30 夜)。数据源:本地 nightWatch 成就。
const nightWatch = require('../../utils/nightWatch');

Page({
  data: {
    navOff: 0,
    display: 0, target: 7, achieved: false, longLit: false,
    nights: [],      // 近 7 夜网格
  },
  onLoad() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
    this._load();
  },
  onShow() { this._load(); },   // 从聊天页返回即刷新
  goBack() {
    wx.navigateBack({});
  },
  _load() {
    const st = nightWatch.getState();
    this.setData({
      display: st.display, target: st.target,
      achieved: st.achieved, longLit: st.longLit,
      nights: nightWatch.recentNights(7),
    });
  },
  /* 分享截图:印章卡 → node canvas → 保存相册(复用 today.js onShot 模式) */
  onShareCard() {
    wx.createSelectorQuery()
      .select('#sealCard')
      .fields({ node: true, size: true })
      .exec((res) => {
        const info = res && res[0];
        if (!info || !info.node) {
          wx.showToast({ title: '绘制暂不可用', icon: 'none' });
          return;
        }
        const canvas = info.node;
        const dpr = wx.getWindowInfo ? wx.getWindowInfo().pixelRatio : 2;
        canvas.width = 600 * dpr;
        canvas.height = 800 * dpr;
        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        // 墨韵画法:宣纸底 + 朱砂印章方框 + 白字「守夜人」 + 进度文案
        ctx.fillStyle = '#F5EFE1';
        ctx.fillRect(0, 0, 600, 800);
        ctx.strokeStyle = '#A93A2C';
        ctx.lineWidth = 6;
        ctx.strokeRect(180, 150, 240, 240);
        ctx.fillStyle = '#A93A2C';
        ctx.font = 'bold 72px serif';
        ctx.textAlign = 'center';
        ctx.fillText('守 夜 人', 300, 300);
        ctx.font = '32px serif';
        ctx.fillText(`第 ${this.data.display}/7 夜`, 300, 480);
        ctx.fillText('七夜了。你睡,我守。', 300, 560);
        wx.canvasToTempFilePath({
          canvas, fileType: 'png',
          success: (r) => {
            wx.saveImageToPhotosAlbum({
              filePath: r.tempFilePath,
              success: () => wx.showToast({ title: '已存入相册', icon: 'none' }),
              fail: () => wx.showToast({ title: '请授权相册权限', icon: 'none' }),
            });
          },
        }, this);
      });
  },
});
