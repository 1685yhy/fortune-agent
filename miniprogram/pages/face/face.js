// 面相分析页 — 拍照/选照片 → CV 分析
const api = require('../../utils/api');

Page({
  data: {
    imagePath: '',
    analyzing: false,
    result: null,
    mode: 'face', // face | palm
  },

  // 拍照
  takePhoto() {
    const that = this;
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['camera'],
      success: (res) => that.analyze(res.tempFiles[0].tempFilePath),
    });
  },

  // 从相册选
  chooseFromAlbum() {
    const that = this;
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album'],
      success: (res) => that.analyze(res.tempFiles[0].tempFilePath),
    });
  },

  async analyze(filePath) {
    this.setData({ imagePath: filePath, analyzing: true, result: null });

    try {
      const isPalm = this.data.mode === 'palm';
      const res = isPalm ? await api.palmReading(filePath) : await api.faceReading(filePath);

      if (res.status === 'ok') {
        this.setData({ result: res, analyzing: false });
      } else {
        wx.showToast({ title: res.message || '未检测到人脸', icon: 'none' });
        this.setData({ analyzing: false });
      }
    } catch (e) {
      wx.showToast({ title: '分析失败，请重试', icon: 'none' });
      this.setData({ analyzing: false });
    }
  },

  switchMode() {
    const modes = ['face', 'palm'];
    const current = modes.indexOf(this.data.mode);
    this.setData({ mode: modes[(current + 1) % 2], result: null, imagePath: '' });
  },

  onShareAppMessage() {
    if (!this.data.result) return {};
    const m = this.data.result.measurements || {};
    return {
      title: `📷 我的面相分析：${m.face_shape?.type || ''} · ${m.eye_type?.type || ''}`,
      path: '/pages/face/face',
    };
  },
});
