// 姓名学 — 五格姓名分析
const api = require('../../utils/api');
const { MESSAGES } = require('../../utils/messages');

Page({
  data: {
    skeletonLoading: true,
    surname: '',
    givenName: '',
    gender: 'male',
    submitted: false,
    loading: false,
    error: null,
    result: null,

    // Error state
    showError: false,
    errorType: '',
  },

  onLoad() {
    setTimeout(() => {
      this.setData({ skeletonLoading: false });
    }, 300);
  },

  // ---- 输入处理 ----

  onSurnameInput(e) {
    this.setData({ surname: e.detail.value });
  },

  onGivenNameInput(e) {
    this.setData({ givenName: e.detail.value });
  },

  onGenderChange(e) {
    this.setData({ gender: e.detail.value });
  },

  // ---- 提交分析 ----

  async onSubmit() {
    const { surname, givenName, gender } = this.data;

    if (!surname.trim()) {
      wx.showToast({ title: '请输入姓氏', icon: 'none' });
      return;
    }
    if (!givenName.trim()) {
      wx.showToast({ title: '请输入名字', icon: 'none' });
      return;
    }

    this.setData({
      submitted: true,
      loading: true,
      error: null,
      result: null,
    });

    try {
      const res = await api.xingming({
        surname: surname.trim(),
        givenName: givenName.trim(),
        gender,
      });

      this.setData({
        loading: false,
        result: res,
      });
    } catch (e) {
      console.warn('[xingming] API failed, using demo data:', e);

      this.setData({
        showError: true,
        errorType: e.name === 'NetworkError' ? 'network' : 'server',
        loading: false,
        submitted: false,
      });
    }
  },

  // ---- Demo Data Fallback ----

  getDemoResult() {
    const { surname, givenName, gender } = this.data;
    const totalStrokes = surname.length * 3 + givenName.length * 4;

    return {
      fiveCells: [
        { name: '天格', strokes: surname.length * 2 + 1, wuxing: '木', luck: '吉' },
        { name: '人格', strokes: surname.length * 2 + givenName.length * 2, wuxing: '火', luck: '吉' },
        { name: '地格', strokes: givenName.length * 2 + 2, wuxing: '土', luck: '吉' },
        { name: '外格', strokes: surname.length + givenName.length + 1, wuxing: '金', luck: '凶' },
        { name: '总格', strokes: totalStrokes + 5, wuxing: '水', luck: '吉' },
      ],
      sancai: `三才配置为${['木火土', '木火金', '木水金', '火土木', '土金水'][totalStrokes % 5]}，天人地三才相生相克，整体配置`,
      judgment: `姓名「${surname}${givenName}」(${gender === 'male' ? '男' : '女'})，五格数理分析显示姓名格局良好。天格主祖先福荫，人格主一生命运核心，地格主早年运势，外格主社交人际，总格主整体成就。`,
    };
  },
});
