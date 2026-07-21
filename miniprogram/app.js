// 易理明灯 v5.0 — AI 命运伴侣
const api = require('./utils/api');
const security = require('./utils/security');

App({
  onLaunch() {
    // 系统主题检测
    this.detectTheme();

    // 微信登录流程
    this.wechatLogin();
  },

  // ---- 全局状态 ----
  globalData: {
    userInfo: null,
    token: null,
    isLoggedIn: false,
    hasBazi: false,
    baziInfo: null,
    theme: 'light',
    apiBase: 'https://124.221.233.214',
  },

  // ---- 微信登录 ----
  async wechatLogin() {
    try {
      // Step 1: wx.login() → code
      const { code } = await new Promise((resolve, reject) => {
        wx.login({
          success: resolve,
          fail: reject,
        });
      });

      if (!code) {
        console.warn('[登录] wx.login 未返回 code');
        return;
      }

      // Step 2: 发送 code 到后端换取 JWT
      const res = await api.login(code);

      if (res.token) {
        this.globalData.token = res.token;
        this.globalData.isLoggedIn = true;
        api.setToken(res.token);

        if (res.user) {
          this.globalData.userInfo = res.user;
          this.globalData.hasBazi = !!res.user.bazi;
          this.globalData.baziInfo = res.user.bazi || null;
        }

        // 安全存储登录态
        security.setSecure('auth', {
          token: res.token,
          userId: res.user?.id,
          loginTime: Date.now(),
        });

        console.log('[登录] 成功');
      } else {
        // 使用本地模式
        this.initLocalMode();
      }
    } catch (e) {
      console.warn('[登录] 失败，使用本地模式', e);
      this.initLocalMode();
    }
  },

  // ---- 本地模式（离线或后端不可用） ----
  initLocalMode() {
    const auth = security.getSecure('auth');
    if (auth?.token) {
      this.globalData.token = auth.token;
      this.globalData.isLoggedIn = true;
      api.setToken(auth.token);
    }

    // 读取本地八字信息
    const profile = security.getSecure('userProfile');
    if (profile) {
      this.globalData.hasBazi = true;
      this.globalData.baziInfo = profile.bazi;
    }
  },

  // ---- 系统主题检测 ----
  detectTheme() {
    try {
      const sysInfo = wx.getSystemInfoSync();
      const theme = sysInfo.theme || 'light';
      this.globalData.theme = theme;

      // 监听主题变化
      wx.onThemeChange((result) => {
        this.globalData.theme = result.theme;
      });
    } catch (e) {
      this.globalData.theme = 'light';
    }
  },
});
