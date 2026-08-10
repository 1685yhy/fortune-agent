// 易理明灯 v5.0 — AI 命运伴侣
const api = require('./utils/api');
const security = require('./utils/security');

App({
  onLaunch() {
    // 真机排查：全局错误捕获（console + 写入 storage ylm_last_error，便于远程查看）
    this._setupGlobalErrorCapture();

    // 系统主题检测
    this.detectTheme();

    // 会话恢复（storage 有 ylm_token → 恢复，不重复登录）
    this.restoreSession();

    // API baseURL 多候选探测提前启动（模拟器 127.0.0.1 / 真机局域网 / 生产兜底；
    // 结果缓存 ylm_baseurl 每天重探；业务请求会等待探测完成）
    api.probe();

    // 微信登录流程；loginPromise 供页面等待登录定型（避免用兜底 user_id 发请求）
    this.loginPromise = this.wechatLogin();

    // 新用户引导：无档案且无会话记录时 → onboarding（可跳过；跳过/完成后不再自动弹）
    this._maybeOnboard();
  },

  /* 首启引导判定：未跳过/未完成 + 无档案（本地 persons/bazi）+ 无会话记录 → navigateTo onboarding
     延迟 1.2s 等登录定型；纯本地判定，后端不可用不阻塞 */
  _maybeOnboard() {
    try {
      if (wx.getStorageSync('ylm_onboard_done') || wx.getStorageSync('ylm_onboard_skipped')) return;
      const persons = wx.getStorageSync('ylm_persons');
      if (Array.isArray(persons) && persons.length) return;
      const arch = wx.getStorageSync('ylm_chat_archives');
      if (Array.isArray(arch) && arch.length) return;
      const msgs = wx.getStorageSync('ylm_chat_messages');
      if (Array.isArray(msgs) && msgs.length) return;
      if (this.globalData.hasBazi && this.globalData.baziInfo) return;

      setTimeout(() => {
        // 延迟后再查一次全局态（登录可能已带回 bazi）
        if (this.globalData.hasBazi && this.globalData.baziInfo) return;
        wx.navigateTo({
          url: '/pages/onboarding/onboarding',
          fail: () => { /* 页面栈异常时忽略，下次启动再试 */ },
        });
      }, 1200);
    } catch (e) { /* ignore */ }
  },

  /* 真机排查：wx.onError / onUnhandledRejection → console.error + storage(ylm_last_error)
     页面 JS 崩溃、API 异常均可留痕；真机复测后读 storage 即可定位崩溃点 */
  _setupGlobalErrorCapture() {
    try {
      if (wx.onError) {
        wx.onError((err) => {
          const msg = (err && (err.message || err.stack || err)) || 'unknown';
          console.error('[全局错误]', msg);
          this._recordError(String(msg));
        });
      }
    } catch (e) { /* ignore */ }
    try {
      if (wx.onUnhandledRejection) {
        wx.onUnhandledRejection((res) => {
          const reason = (res && (res.reason || res.errMsg)) || '';
          console.error('[未处理Promise]', reason);
          this._recordError('[UnhandledRejection] ' + String(reason));
        });
      }
    } catch (e) { /* ignore */ }
  },

  /* 错误留痕：ylm_last_error 数组（最近 20 条，含时间戳） */
  _recordError(msg) {
    try {
      const prev = wx.getStorageSync('ylm_last_error');
      const list = Array.isArray(prev) ? prev.slice(-19) : [];
      list.push({ t: Date.now(), msg: String(msg).slice(0, 2000) });
      wx.setStorageSync('ylm_last_error', list);
    } catch (e) { /* ignore */ }
  },

  // ---- 全局状态 ----
  globalData: {
    userInfo: null,
    token: null,
    // 统一身份：登录响应 user.id（后端 JWT sub）。所有页面/请求一律取它，不再每页硬编码
    userId: null,
    isLoggedIn: false,
    hasBazi: false,
    baziInfo: null,
    theme: 'light',
    apiBase: 'http://127.0.0.1:8767',  // 本地开发
  },

  // ---- 会话恢复（已登录 → 不再重复 wx.login；除非 401 由 api.js 静默重登） ----
  restoreSession() {
    let token = null;
    let userId = null;
    try {
      token = wx.getStorageSync('ylm_token') || null;
      userId = wx.getStorageSync('ylm_user_id') || null;
    } catch (e) { /* ignore */ }

    // 旧版本迁移：security 'auth' 存有 token/userId 时搬入 ylm_* 键
    if (!token) {
      const auth = security.getSecure('auth');
      if (auth && auth.token) {
        token = auth.token;
        userId = auth.userId || userId;
        try {
          wx.setStorageSync('ylm_token', token);
          if (userId) wx.setStorageSync('ylm_user_id', userId);
        } catch (e) { /* ignore */ }
      }
    }

    if (token) {
      this.globalData.token = token;
      this.globalData.userId = userId;
      this.globalData.isLoggedIn = true;
      api.setToken(token);
      console.log('[登录] 已恢复会话，跳过重复登录');
    }
  },

  // ---- 微信登录 ----
  async wechatLogin() {
    // 已有 token 且已有稳定 userId → 会话有效，不重复登录
    if (this.globalData.token && this.globalData.userId) {
      return;
    }
    const hadSession = this._hadSession();

    try {
      // Step 1: wx.login() → code
      const { code } = await new Promise((resolve, reject) => {
        wx.login({
          success: resolve,
          fail: reject,
        });
      });

      if (!code) {
        console.warn('[登录] wx.login 未返回 code，使用本地模式');
        this.initLocalMode(hadSession);
        return;
      }

      // Step 2: 发送 code 到后端换取 JWT
      const res = await api.login(code);

      if (res && res.token) {
        // 统一身份：user.id（后端 JWT sub）→ globalData.userId + storage(ylm_user_id)
        api.applyAuth(res);
        // 兼容旧版安全存储
        security.setSecure('auth', {
          token: res.token,
          userId: (res.user && res.user.id) || null,
          loginTime: Date.now(),
        });
        if (!hadSession) this._welcome();
        console.log('[登录] 成功 userId=' + this.globalData.userId);
      } else {
        console.warn('[登录] 响应缺少 token，使用本地模式');
        this.initLocalMode(hadSession);
      }
    } catch (e) {
      console.warn('[登录] 失败，使用本地模式', e && e.message);
      this.initLocalMode(hadSession);
    }
  },

  // ---- 本地模式（离线或后端不可用；user_id 用稳定值，全局一致） ----
  initLocalMode(hadSession) {
    // 尽力恢复旧会话（登录失败但本地有 token）
    if (!this.globalData.token) {
      const auth = security.getSecure('auth');
      if (auth && auth.token) {
        this.globalData.token = auth.token;
        this.globalData.isLoggedIn = true;
        if (auth.userId) this.globalData.userId = auth.userId;
        api.setToken(auth.token);
      }
    }

    // user_id 兜底：稳定值 local_user（不再每页一个 ID）
    if (!this.globalData.userId) {
      let stored = null;
      try {
        stored = wx.getStorageSync('ylm_user_id') || null;
      } catch (e) { /* ignore */ }
      this.globalData.userId = stored || 'local_user';
      if (!stored) {
        try {
          wx.setStorageSync('ylm_user_id', 'local_user');
        } catch (e) { /* ignore */ }
      }
    }

    // 读取本地八字信息
    const profile = security.getSecure('userProfile');
    if (profile) {
      this.globalData.hasBazi = true;
      this.globalData.baziInfo = profile.bazi;
    }

    // 首次进入且登录失败：明确降级提示（有旧会话时静默，不打扰）
    if (!hadSession) {
      try {
        wx.showToast({ title: '登录失败，已使用本地模式', icon: 'none', duration: 2000 });
      } catch (e) { /* ignore */ }
    }
  },

  // ---- 是否已有会话（首次进入欢迎用） ----
  _hadSession() {
    try {
      return !!wx.getStorageSync('ylm_token');
    } catch (e) {
      return false;
    }
  },

  // ---- 首次登录欢迎 ----
  _welcome() {
    try {
      wx.showToast({ title: '欢迎来到易理明灯', icon: 'none', duration: 2000 });
    } catch (e) { /* ignore */ }
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
