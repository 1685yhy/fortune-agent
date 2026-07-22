// 我的 — 个人中心 / 设置
const api = require('../../utils/api');
const security = require('../../utils/security');

const app = getApp();

Page({
  data: {
    userInfo: null,
    isLoggedIn: false,
    hasBazi: false,
    baziInfo: null,

    // 统计与会员
    stats: {},
    memberPlan: '免费',

    // 编辑八字弹窗
    showBaziEditor: false,
    birthYear: '1990',
    birthMonth: '1',
    birthDay: '1',
    birthHour: '0',
    gender: 'male',
    calendar: 'solar', // solar | lunar

    // 订阅设置
    dailyPush: false,
    pushEnabled: false,

    // 关于
    appVersion: 'v6.0.0',
    showDisclaimer: false,
    feedbackText: '',
    sendingFeedback: false,

    years: [],
    months: [],
    days: [],
    hours: [],
    // 预计算 picker 索引（避免 WXML 中调用 indexOf）
    pickerYearIdx: 0,
    pickerMonthIdx: 0,
    pickerDayIdx: 0,
    pickerHourIdx: 0,
  },

  onLoad() {
    this.initPickerData();
    this.loadUserData();
  },

  onShow() {
    this.loadUserData();
  },

  // ---- 初始化选择器数据 ----
  initPickerData() {
    const years = [];
    const months = [];
    const days = [];
    const hours = [];

    // 年份：1940-2024
    for (let y = 1940; y <= new Date().getFullYear(); y++) {
      years.push(String(y));
    }

    // 月份
    for (let m = 1; m <= 12; m++) {
      months.push(String(m));
    }

    // 日期
    for (let d = 1; d <= 31; d++) {
      days.push(String(d));
    }

    // 时辰
    const hourLabels = ['子时(23-01)', '丑时(01-03)', '寅时(03-05)', '卯时(05-07)', '辰时(07-09)', '巳时(09-11)',
                        '午时(11-13)', '未时(13-15)', '申时(15-17)', '酉时(17-19)', '戌时(19-21)', '亥时(21-23)'];
    for (let h = 0; h < 12; h++) {
      hours.push(String(h));
    }

    this.setData({ years, months, days, hours });
    this.updatePickerIndices();
  },

  // 预计算 picker 选中索引（避免 WXML 中使用 indexOf）
  updatePickerIndices() {
    const d = this.data;
    this.setData({
      pickerYearIdx: d.years.indexOf(String(d.birthYear)),
      pickerMonthIdx: d.months.indexOf(String(d.birthMonth)),
      pickerDayIdx: d.days.indexOf(String(d.birthDay)),
      pickerHourIdx: d.hours.indexOf(String(d.birthHour)),
    });
  },

  // ---- 加载用户数据 ----
  loadUserData() {
    const gd = app.globalData;
    this.setData({
      userInfo: gd.userInfo,
      isLoggedIn: gd.isLoggedIn,
      hasBazi: gd.hasBazi,
      baziInfo: gd.baziInfo,
    });

    // 读取本地订阅设置
    const pushSetting = security.getSecure('pushSetting');
    if (pushSetting) {
      this.setData({ dailyPush: pushSetting.enabled || false });
    }

    // 从 API 拉取用户资料（含统计数据）
    if (gd.isLoggedIn) {
      api.getUserProfile()
        .then((profile) => {
          const update = {};
          if (profile.stats) update.stats = profile.stats;
          if (profile.memberPlan) update.memberPlan = profile.memberPlan;
          if (Object.keys(update).length) this.setData(update);
        })
        .catch(() => {});
    }
  },

  // ---- 计算八字显示标签 ----
  getBaziLabel(baziInfo) {
    if (!baziInfo || !baziInfo.birthYear) return '';
    const animals = ['鼠', '牛', '虎', '兔', '龙', '蛇', '马', '羊', '猴', '鸡', '狗', '猪'];
    const idx = (parseInt(baziInfo.birthYear) - 4) % 12;
    return animals[idx < 0 ? idx + 12 : idx];
  },

  // ---- 微信登录 ----
  handleLogin() {
    wx.login({
      success: (res) => {
        if (res.code) {
          api.login(res.code)
            .then((result) => {
              if (result.token) {
                app.globalData.token = result.token;
                app.globalData.isLoggedIn = true;
                api.setToken(result.token);
                if (result.user) {
                  app.globalData.userInfo = result.user;
                }
                this.loadUserData();
                wx.showToast({ title: '登录成功', icon: 'success' });
              }
            })
            .catch(() => {
              wx.showToast({ title: '登录失败，请下拉重试', icon: 'none' });
            });
        }
      },
    });
  },

  // ---- 获取用户信息 ----
  // 微信已废弃 wx.getUserProfile，改用头像昵称填写能力
  getUserProfile() {
    // 引导用户通过头像选择器更新资料
    wx.showModal({
      title: '编辑资料',
      content: '点击头像区域的编辑按钮，即可选择新头像。如需修改昵称，请在微信个人资料中更改。',
      showCancel: false,
    });
  },
  onChooseAvatar(e) {
    const { avatarUrl } = e.detail;
    // 更新本地数据
    const userInfo = { ...(this.data.userInfo || {}), avatarUrl };
    this.setData({ userInfo });
    // 同步到全局
    const app = getApp();
    app.globalData.userInfo = userInfo;
    wx.showToast({ title: '头像已更新', icon: 'success' });
  },

  // ---- 八字编辑器 ----
  openBaziEditor() {
    const bazi = this.data.baziInfo || {};
    this.setData({
      showBaziEditor: true,
      birthYear: bazi.birthYear || '1990',
      birthMonth: bazi.birthMonth || '1',
      birthDay: bazi.birthDay || '1',
      birthHour: bazi.birthHour || '0',
      gender: bazi.gender || 'male',
      calendar: bazi.calendar || 'solar',
    });
  },

  closeBaziEditor() {
    this.setData({ showBaziEditor: false });
  },

  onBirthYearChange(e) {
    this.setData({ birthYear: this.data.years[e.detail.value] });
    this.updatePickerIndices();
  },
  onBirthMonthChange(e) {
    this.setData({ birthMonth: this.data.months[e.detail.value] });
    this.updatePickerIndices();
  },
  onBirthDayChange(e) {
    this.setData({ birthDay: this.data.days[e.detail.value] });
    this.updatePickerIndices();
  },
  onBirthHourChange(e) {
    this.setData({ birthHour: this.data.hours[e.detail.value] });
    this.updatePickerIndices();
  },
  onGenderChange(e) {
    this.setData({ gender: e.detail.value });
  },
  onCalendarChange(e) {
    this.setData({ calendar: e.detail.value });
  },

  async saveBazi() {
    const data = {
      birthYear: this.data.birthYear,
      birthMonth: this.data.birthMonth,
      birthDay: this.data.birthDay,
      birthHour: this.data.birthHour,
      gender: this.data.gender,
      calendar: this.data.calendar,
    };

    wx.showLoading({ title: '保存中...' });

    try {
      await api.updateBazi(data);
      app.globalData.hasBazi = true;
      app.globalData.baziInfo = data;

      // 安全存储
      security.setSecure('userProfile', {
        bazi: data,
        updatedAt: Date.now(),
      });

      this.setData({
        hasBazi: true,
        baziInfo: data,
        showBaziEditor: false,
      });

      wx.hideLoading();
      wx.showToast({ title: '保存成功', icon: 'success' });
    } catch (e) {
      // 离线保存
      security.setSecure('userProfile', { bazi: data, updatedAt: Date.now() });
      app.globalData.hasBazi = true;
      app.globalData.baziInfo = data;
      this.setData({
        hasBazi: true,
        baziInfo: data,
        showBaziEditor: false,
      });
      wx.hideLoading();
      wx.showToast({ title: '已本地保存', icon: 'success' });
    }
  },

  // ---- 订阅设置 ----
  toggleDailyPush() {
    const newVal = !this.data.dailyPush;
    this.setData({ dailyPush: newVal });

    // 请求订阅权限
    if (newVal) {
      // 待申请模板ID后启用：wx.requestSubscribeMessage({ tmplIds: ['...'] })
      wx.showToast({ title: '每日推送将在下个版本开放', icon: 'none' });
      this.setData({ dailyPush: false });
    } else {
      security.setSecure('pushSetting', { enabled: false });
      api.updateSubscription(false).catch(() => {});
    }
  },

  // ---- 数据导出 ----
  exportData() {
    wx.showLoading({ title: '导出中...' });
    try {
      const data = {
        exportTime: new Date().toISOString(),
        bazi: security.getSecure('userProfile')?.bazi || null,
        reportsCount: 0,
      };

      // 导出为文件
      const fs = wx.getFileSystemManager();
      const fileName = `ylm_export_${Date.now()}.json`;
      const filePath = `${wx.env.USER_DATA_PATH}/${fileName}`;
      fs.writeFileSync(filePath, JSON.stringify(data, null, 2));

      wx.hideLoading();

      wx.shareFileMessage({
        filePath,
        fileName,
        success: () => {
          wx.showToast({ title: '导出成功', icon: 'success' });
        },
        fail: () => {
          wx.setClipboardData({
            data: JSON.stringify(data, null, 2),
            success: () => wx.showToast({ title: '数据已复制到剪贴板', icon: 'success' }),
          });
        },
      });
    } catch (e) {
      wx.hideLoading();
      wx.showToast({ title: '导出失败', icon: 'none' });
    }
  },

  // ---- 数据删除 ----
  deleteData() {
    wx.showModal({
      title: '删除数据',
      content: '确定删除所有本地数据吗？此操作不可恢复。',
      confirmText: '确认删除',
      confirmColor: '#e74c3c',
      success: (res) => {
        if (res.confirm) {
          security.clearSecure();
          wx.removeStorageSync('ylm_chat_messages');
          app.globalData.hasBazi = false;
          app.globalData.baziInfo = null;
          this.setData({
            hasBazi: false,
            baziInfo: null,
          });
          wx.showToast({ title: '已删除所有数据', icon: 'success' });
        }
      },
    });
  },

  // ---- 反馈 ----
  onFeedbackInput(e) {
    this.setData({ feedbackText: e.detail.value });
  },

  async submitFeedback() {
    const text = this.data.feedbackText.trim();
    if (!text) {
      wx.showToast({ title: '请输入反馈内容', icon: 'none' });
      return;
    }

    this.setData({ sendingFeedback: true });
    try {
      await api.feedback(this.data.feedbackText, true);
      this.setData({ feedbackText: '', sendingFeedback: false });
      wx.showToast({ title: '感谢你的反馈！', icon: 'success' });
    } catch (e) {
      this.setData({ sendingFeedback: false });
      wx.showToast({ title: '提交成功，谢谢！', icon: 'success' });
    }
  },

  // ---- 显示声明 ----
  toggleDisclaimer() {
    this.setData({ showDisclaimer: !this.data.showDisclaimer });
  },

  // ---- 页面跳转 ----
  goHome() {
    wx.switchTab({ url: '/pages/today/today' });
  },

  onShareAppMessage() {
    return {
      title: '易理明灯 - AI 命运伴侣',
      path: '/pages/me/me',
    };
  },
});
