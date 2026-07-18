App({
  onLaunch() {
    // 微信登录
    wx.login({
      success: (res) => {
        if (res.code) {
          // 可以在这里把 code 发送到后端换取 openid
          console.log('Login success');
        }
      },
    });
  },

  globalData: {
    userInfo: null,
    apiBase: 'https://yilichat.com/api',
  },
});
