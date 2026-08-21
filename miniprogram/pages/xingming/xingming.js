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

  onLoad(opts) {
    // 从 AI取名页「评测此名」跳入: ?surname=林&givenName=云舒&gender=female&auto=1 → 预填并自动分析
    if (opts && opts.surname) {
      const surname = decodeURIComponent(opts.surname || '');
      const givenName = decodeURIComponent(opts.givenName || '');
      const gender = opts.gender === 'female' ? 'female' : 'male';
      this.setData({ surname, givenName, gender, submitted: true, loading: true });
      setTimeout(() => {
        this.setData({ skeletonLoading: false });
        if (opts.auto === '1' && surname && givenName) {
          this._doAnalyze(surname, givenName, gender);
        }
      }, 350);
      return;
    }
    setTimeout(() => {
      this.setData({ skeletonLoading: false });
    }, 300);
  },

  /* 评测此名自动分析(与 onSubmit 同一管线) */
  _doAnalyze(surname, givenName, gender) {
    this.setData({ loading: true, error: null, result: null });
    api.xingming({ surname, givenName, gender })
      .then((res) => {
        this.setData({ loading: false, result: res });
      })
      .catch((e) => {
        console.warn('[xingming] 评测分析失败:', e);
        this.setData({
          showError: true,
          errorType: e.name === 'NetworkError' ? 'network' : 'server',
          loading: false,
          submitted: false,
        });
      });
  },

  /* 互打: 去 AI 取名(带姓氏/性别, 成人改名由取名页分段切换) */
  onGoMing(e) {
    const url = e.currentTarget.dataset.url;
    if (url) wx.navigateTo({ url });
  },

  // ---- 输入处理 ----

  onSurnameInput(e) {
    this.setData({ surname: e.detail.value });
  },

  onGivenNameInput(e) {
    this.setData({ givenName: e.detail.value });
  },

  /* 性别：男/女 大按钮（点击切换，与 paipan 同款，金色选中态） */
  onGenderTap(e) {
    this.setData({ gender: e.currentTarget.dataset.gender });
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

  // ---- Retry after error ----
  onErrorRetry() {
    this.setData({ showError: false });
    this.onSubmit();
  },
});
