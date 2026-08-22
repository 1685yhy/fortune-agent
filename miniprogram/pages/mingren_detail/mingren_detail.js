// 名人命例 — 详情页（L3-1）
// 数据流: GET /api/mingren/{name} → 简介 + 命理评注(穷通宝鉴) + 生平大事时间线
// 未解锁(免费/基础访问前 35 之外) → 后端 403 {code: VIP_REQUIRED} → 锁定视图 + 开通引导
const api = require('../../utils/api');
const payment = require('../../utils/payment');
const theme = require('../../utils/theme');

Page({
  data: {
    dark: false,
    name: '',
    loading: true,
    failed: false,          // 加载失败(非 403/未找到) → 重试入口
    locked: false,          // 未解锁(403 VIP_REQUIRED)
    lockMessage: '',
    detail: null,
    memberDialogVisible: false,
    memberPlans: [],
    _buying: false,
  },

  onLoad(options) {
    theme.bindTheme(this);
    const name = decodeURIComponent(options.name || '');
    this.setData({ name });
    // 会员开通方案(同列表页: 基础三档 + 高级一档)
    const plans = ['monthly', 'quarterly', 'yearly', 'pro_monthly']
      .map((id) => payment.getProduct(id))
      .filter((p) => !!p)
      .map((p) => {
        const parts = String(p.priceLabel || '').split('/');
        return {
          id: p.id,
          name: p.name,
          priceNum: parts[0] || p.priceLabel,
          priceUnit: parts[1] ? '/' + parts[1] : '',
          description: p.description,
          icon: p.icon,
        };
      });
    this.setData({ memberPlans: plans });
    if (name) {
      this._load();
    } else {
      // 无 name 参数直入: 避免 loading 永真卡死, 提示后返回(UX批2 Minor)
      this.setData({ loading: false });
      wx.showToast({ title: '未指定命例', icon: 'none' });
      setTimeout(() => wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/mingren/mingren' }) }), 800);
    }
  },

  _load() {
    this.setData({ loading: true, locked: false, failed: false });
    api.getMingrenDetail(this.data.name)
      .then((d) => {
        wx.setNavigationBarTitle({ title: d.name });
        this.setData({
          detail: {
            name: d.name,
            info: d.info,
            info2: d.info2,
            source: d.source,
            hasInfo2: !!d.has_info2,
            flist: (d.flist || []).map((f) => ({
              year: f.name,
              text: f.data,
            })),
          },
          name: d.name,
        });
      })
      .catch((err) => {
        console.warn('[mingren] 详情失败:', err);
        // 未解锁 → 锁定视图 + 开通引导（服务端 403 VIP_REQUIRED 语义）
        if (err && err.detail && err.detail.code === 'VIP_REQUIRED') {
          this.setData({ locked: true, lockMessage: err.detail.message || '' });
          return;
        }
        if (err && err.detail && err.detail === '未找到该命例') {
          wx.showToast({ title: '未找到该命例', icon: 'none' });
          setTimeout(() => wx.navigateBack(), 800);
          return;
        }
        wx.showToast({ title: '命例加载失败，请稍后再试', icon: 'none' });
        this.setData({ failed: true }); // 失败态提供重试路径(UX批2 Minor)
      })
      .finally(() => this.setData({ loading: false }));
  },

  /* 失败重试 */
  retryLoad() {
    this._load();
  },

  /* ── 未解锁: 开通高级会员 ── */
  openMemberDialog() {
    this.setData({ memberDialogVisible: true });
  },
  closeMemberDialog() {
    this.setData({ memberDialogVisible: false });
  },
  noop() { /* 阻止遮罩点击穿透 */ },

  buyPlan(e) {
    const planId = e.currentTarget.dataset.plan;
    if (!planId || this.data._buying) return;
    this.setData({ _buying: true });
    payment.subscribeMember(planId)
      .then((res) => {
        if (res && res.success) {
          this.setData({ memberDialogVisible: false });
          wx.showToast({ title: '开通成功 · 全量解锁', icon: 'none' });
          this._load();  // 解锁后重拉详情
        }
      })
      .catch(() => {
        wx.showToast({ title: '支付异常，请稍后再试', icon: 'none' });
      })
      .finally(() => { this.setData({ _buying: false }); });
  },
});
