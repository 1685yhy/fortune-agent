// 用户协议
Page({
  data: {},

  onLoad() {
    // 页面加载时的初始化逻辑
  },

  onShareAppMessage() {
    return {
      title: '易理明灯 - 用户协议',
      path: '/pages/agreement/agreement',
    };
  },
});
