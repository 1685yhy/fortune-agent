// 名人命例 — 2807 位历史人物命例 + 穷通宝鉴评注（L3-1）
// 数据流: GET /api/mingren?page=&size=&q= → 分页卡片列表(姓名+简介+评注片段)
//         → 点卡片 → /pages/mingren_detail/mingren_detail?name=
// 门控(服务端强制): 免费/基础会员仅前 35 例(列表底部引导卡) · 高级会员(pro/annual)全量
// 列表接口不 403: 免费用户永远只拿到前 35 条, is_full=false 驱动引导卡展示
const api = require('../../utils/api');
const payment = require('../../utils/payment');
const theme = require('../../utils/theme');

const PAGE_SIZE = 20;

/* 列表卡片: 简介/评注片段截断 */
function truncate(text, n) {
  const s = String(text || '').trim();
  return s.length > n ? s.slice(0, n) + '…' : s;
}

Page({
  data: {
    navOff: 0,
    dark: false,
    keyword: '',
    items: [],
    page: 1,
    total: 0,
    totalAll: 0,
    libraryTotal: 0,
    isFull: false,        // 是否全量解锁(高级会员)
    loading: false,
    loadingMore: false,
    finished: false,      // 没有更多
    memberDialogVisible: false,
    memberPlans: [],
    _buying: false,
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
    // 会员开通方案(同 me.js: 基础三档 + 高级一档; 高级会员=全量解锁)
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
    this._load();
  },

  /* 状态栏高度适配(同 celiang): 原型固定 47px, --nav-off 为差值 */
  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 拉取列表(门控由服务端强制: 免费用户 total ≤ 35)
     请求序号: 新关键词/清空/开通后重载可打断在途请求, 过期响应丢弃——
     避免旧关键词结果覆盖新查询(UX批2 Important) */
  _load(append = false) {
    if (append && (this.data.loading || this.data.finished)) return;
    const token = (this._reqToken = (this._reqToken || 0) + 1);
    this.setData({ loading: !append, loadingMore: append });
    const page = append ? this.data.page + 1 : 1;
    return api.listMingren({ page, size: PAGE_SIZE, q: this.data.keyword })
      .then((res) => {
        if (token !== this._reqToken) return; // 已有更新的查询, 丢弃过期响应
        const items = (res.items || []).map((it) => ({
          name: it.name,
          info: truncate(it.info, 60),
          info2: truncate(it.info2, 40),
          hasInfo2: !!it.has_info2,
          hasChart: !!it.has_chart,   // B5-3 有命盘徽标（服务端出生数据透出）
          sealChar: (it.name || '例').charAt(0),
        }));
        const merged = append ? this.data.items.concat(items) : items;
        const total = res.total || 0;
        this.setData({
          items: merged,
          page,
          total,
          totalAll: res.total_all || 0,
          libraryTotal: res.library_total || 0,
          isFull: !!res.is_full,
          finished: merged.length >= total,
        });
      })
      .catch((err) => {
        if (token !== this._reqToken) return;
        console.warn('[mingren] 列表加载失败:', err);
        if (!append) {
          this.setData({ items: [] });
          wx.showToast({ title: '命例库加载失败，请稍后再试', icon: 'none' });
        }
      })
      .finally(() => {
        if (token !== this._reqToken) return;
        this.setData({ loading: false, loadingMore: false });
      });
  },

  /* 搜索: 防抖 500ms, 重置到第 1 页 */
  onSearchInput(e) {
    const keyword = e.detail.value;
    this.setData({ keyword });
    if (this._searchTimer) clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => {
      this.setData({ items: [], finished: false });
      this._load(false);
    }, 500);
  },

  onClearSearch() {
    if (this._searchTimer) clearTimeout(this._searchTimer);
    this.setData({ keyword: '', items: [], finished: false });
    this._load(false);
  },

  /* 键盘"搜索"键直接触发查询（不等 500ms 防抖） */
  onSearchConfirm() {
    if (this._searchTimer) clearTimeout(this._searchTimer);
    this.setData({ items: [], finished: false });
    this._load(false);
  },

  /* 触底加载更多 */
  onReachBottom() {
    this._load(true);
  },

  /* scroll-view 触底加载：列表在固定高度滚动区内滚动,
     页面级 onReachBottom 永不触发, 由 scrolltolower 驱动(UX批2 Critical) */
  onScrollLower(e) {
    if (e && e.detail && e.detail.direction && e.detail.direction !== 'bottom') return;
    this._load(true);
  },

  /* 点卡片 → 详情页 */
  onItemTap(e) {
    const name = e.currentTarget.dataset.name;
    if (!name) return;
    wx.navigateTo({ url: `/pages/mingren_detail/mingren_detail?name=${encodeURIComponent(name)}` });
  },

  /* ── 免费用户引导卡: 开通高级会员(全量 2807 位) ── */
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
          // 刷新: 高级会员后重新拉全量
          this.setData({ items: [], finished: false });
          this._load(false);
        }
      })
      .catch(() => {
        wx.showToast({ title: '支付异常，请稍后再试', icon: 'none' });
      })
      .finally(() => { this.setData({ _buying: false }); });
  },
});
