// 报告 — 历史解读报告
const api = require('../../utils/api');

Page({
  data: {
    reports: [],
    loading: true,
    refreshing: false,
    page: 1,
    hasMore: true,
    showDetail: false,
    currentReport: null,
    detailLoading: false,
  },

  onLoad() {
    this.startLoadingTextRotation();
    this.loadReports();
  },

  onUnload() {
    this.clearLoadingTextRotation();
  },

  onShow() {
    // 从详情返回时刷新
    if (this.data.showDetail) return;
    if (this.data.reports.length === 0) {
      this.startLoadingTextRotation();
      this.loadReports();
    }
  },

  onPullDownRefresh() {
    this.setData({
      refreshing: true,
      page: 1,
      hasMore: true,
    });
    this.loadReports(true, () => {
      wx.stopPullDownRefresh();
      this.setData({ refreshing: false });
    });
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loading) {
      this.setData({ page: this.data.page + 1 });
      this.loadReports();
    }
  },

  // ---- 加载文字轮播 ----
  startLoadingTextRotation() {
    const texts = ['加载报告中...', '读取命盘中...', '解析数据中...', '即将就绪'];
    let i = 0;
    this.setData({ loadingText: texts[0] });
    this._loadingTimer = setInterval(() => {
      i = (i + 1) % texts.length;
      this.setData({ loadingText: texts[i] });
    }, 2000);
  },

  clearLoadingTextRotation() {
    if (this._loadingTimer) {
      clearInterval(this._loadingTimer);
      this._loadingTimer = null;
    }
  },

  loadReports(isRefresh = false, callback) {
    if (!isRefresh) {
      this.setData({ loading: this.data.reports.length === 0 });
    }

    api.getReports(this.data.page, 20)
      .then((res) => {
        const reports = res.reports || [];

        // 演示数据（当后端不可用时）
        if (reports.length === 0 && this.data.page === 1) {
          this.setDemoData();
          if (callback) callback();
          return;
        }

        this.setData({
          reports: isRefresh ? reports : [...this.data.reports, ...reports],
          hasMore: reports.length >= 20,
          loading: false,
        });
      })
      .catch(() => {
        // 后端不可用，使用演示数据
        if (this.data.page === 1) {
          this.setDemoData();
        }
        this.setData({ loading: false });
      })
      .finally(() => {
        this.clearLoadingTextRotation();
        if (callback) callback();
      });
  },

  setDemoData() {
    const demoReports = [
      {
        id: 'demo-1',
        date: '2026-07-21',
        scenario: 'career',
        scenarioLabel: '事业',
        summary: '流年正官透干，事业运稳步上升。春季有贵人提携，适合拓展人脉。',
        score: 82,
        tags: ['事业', '正官', '贵人'],
      },
      {
        id: 'demo-2',
        date: '2026-07-18',
        scenario: 'love',
        scenarioLabel: '感情',
        summary: '桃花运渐旺，七杀坐夫妻宫。建议多参与社交活动，缘分可能在西方。',
        score: 75,
        tags: ['感情', '桃花', '七杀'],
      },
      {
        id: 'demo-3',
        date: '2026-07-15',
        scenario: 'wealth',
        scenarioLabel: '财运',
        summary: '正财稳健，偏财有波动。不宜高风险投资，稳中求进为上策。',
        score: 70,
        tags: ['财运', '正财', '稳健'],
      },
      {
        id: 'demo-4',
        date: '2026-07-10',
        scenario: 'health',
        scenarioLabel: '健康',
        summary: '木气过旺注意肝胆，金水不足易感疲惫。建议规律作息，多饮水。',
        score: 65,
        tags: ['健康', '肝胆', '调理'],
      },
    ];

    this.setData({
      reports: demoReports,
      loading: false,
      hasMore: false,
    });
  },

  // ---- 查看详情 ----
  viewDetail(e) {
    const id = e.currentTarget.dataset.id;
    const report = this.data.reports.find(r => r.id === id);
    if (!report) return;

    this.setData({
      showDetail: true,
      currentReport: report,
      detailLoading: true,
    });

    // 尝试获取详情
    api.getReportDetail(id)
      .then((res) => {
        if (res && res.report) {
          this.setData({
            currentReport: { ...report, ...res.report },
            detailLoading: false,
          });
        } else {
          // 补齐演示详情
          this.setData({
            currentReport: {
              ...report,
              fullContent: report.summary + '\n\n【详细分析】\n命盘显示，当前大运走势与流年形成良好互动，建议把握机遇，审慎决策。天时地利人和，三者缺一不可。',
              luckyColor: '金色、白色',
              luckyDirection: '西方',
              luckyNumber: 7,
            },
            detailLoading: false,
          });
        }
      })
      .catch(() => {
        this.setData({
          currentReport: {
            ...report,
            fullContent: report.summary + '\n\n【详细分析】\n命盘显示，当前大运走势与流年形成良好互动，建议把握机遇，审慎决策。',
          },
          detailLoading: false,
        });
      });
  },

  closeDetail() {
    this.setData({
      showDetail: false,
      currentReport: null,
    });
  },

  // ---- 分享 ----
  shareReport(e) {
    const id = e.currentTarget.dataset.id;
    const report = this.data.reports.find(r => r.id === id);
    if (!report) return;

    wx.showShareMenu({
      withShareTicket: true,
    });

    this.setData({
      shareReportId: id,
    });
  },

  onShareAppMessage() {
    const report = this.data.reports.find(r => r.id === this.data.shareReportId) || this.data.currentReport;
    if (!report) {
      return { title: '易理明灯 - 我的命理解读报告', path: '/pages/reports/reports' };
    }
    return {
      title: `易理明灯 - ${report.scenarioLabel}解读：${report.summary.slice(0, 20)}...`,
      path: `/pages/reports/reports`,
    };
  },

  // ---- 页面跳转 ----
  goChat() {
    wx.switchTab({ url: '/pages/chat/chat' });
  },

  // ---- 场景图标映射 ----
  getScenarioIcon(scenario) {
    const icons = {
      career: '',
      love: '',
      wealth: '',
      health: '',
    };
    return '';
  },
});
