// 抽灵签 — 签筒摇签(原型 S12: 13-idle → 14 签卡 → 14b 保存 → 14c 再摇)
// 数据流: 点签筒/摇一支 → POST /api/qian/draw(纯随机,每摇可换) → 摇签动效 1.6s
//         → 单支弹出(签号竖写) + 签卡(第X签+吉凶徽标+四行诗+解曰+所求)
//         → 保存签卡 POST /api/qian/save(UNIQUE(user_id,no) 防重复,已存提示)
//         → 再摇一支;每日首摇本地日期标记提示「今日首签」
// 签文为传统民俗文化 · 内容仅供娱乐参考
const api = require('../../utils/api');
const theme = require('../../utils/theme');

const SHAKE_MS = 1600;                       // 摇签动效时长(1.5-2s)
const FIRST_DAY_KEY = 'qian_first_day';      // 每日首摇标记(本地)
const JX_LEVEL = { '上上签': 1, '上吉签': 2, '中吉签': 3, '中平签': 4, '下签': 5 };  // 吉凶徽标分级色
const NUM_CN = { 1: '一', 2: '二', 3: '三', 5: '五', 7: '七', 9: '九', 12: '十二', 15: '十五', 20: '二十' };

/* 签号 → 竖排签字(单支弹出签面,原型: 签号竖写) */
function noChars(no) {
  const num = NUM_CN[no] || String(no);
  return ['第', ...num.split(''), '签'];
}

function bjDay() {
  const d = new Date();
  const off = d.getTimezoneOffset() * 60000 + 8 * 3600000;  // 北京时区
  const bj = new Date(d.getTime() + off);
  return `${bj.getUTCFullYear()}-${String(bj.getUTCMonth() + 1).padStart(2, '0')}-${String(bj.getUTCDate()).padStart(2, '0')}`;
}

Page({
  data: {
    navOff: 0,
    dark: false,
    stage: 'idle',        // idle(待摇) | shaking(摇动) | done(签卡)
    card: null,           // 当前签卡(后端完整签卡 + 前端派生 level/noChars)
    raisedIdx: -1,        // 摇出的签枝下标(0-4,按签号映射)
    sticks: [0, 1, 2, 3, 4],
    saving: false,
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this);
  },

  /* UX批3：揭卡延时与页面卸载竞态——返回上一页时清掉挂起的 setTimeout，
     避免对已卸载页面 setData（微信仅告警，但属隐患） */
  onUnload() {
    if (this._drawTimer) {
      clearTimeout(this._drawTimer);
      this._drawTimer = null;
    }
  },

  /* 状态栏高度适配(同 celiang): 原型画板固定状态栏 47px,--nav-off 为差值 */
  _initNavOff() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* v2026-08-17（PM）：goBack 已移除——本页 qian.json 为默认导航样式，
     系统导航栏自带返回键（navigateTo 栈），自定义返回钮（navrow .back）已删，
     返回途径统一为系统导航栏返回键；onShake 保留（签筒 idle 态 + 再摇按钮用） */

  /* 点签筒 / 摇一支: 先进入摇动态,同时请求;响应后补足 1.6s 再揭签卡。
     v2026-08-17（PM）：抽完后签筒不可再点——wxml bindtap 仅 idle 态绑定
     onShake（done/shaking 态点击不触发），重摇只走下方「再摇一支」按钮；
     此处 shaking 守卫兜底（连点/按钮连击不重复触发） */
  onShake() {
    if (this.data.stage === 'shaking') return;
    this.setData({ stage: 'shaking', card: null, raisedIdx: -1 });
    api.drawQian()
      .then((res) => {
        const card = res.card || {};
        card.level = JX_LEVEL[card.jx] || 4;
        card.noChars = noChars(card.no);
        // UX批3：签号→5 签枝按 (no*7+3)%5 分散——原 (no%5) 使 5/15/20 号都落 0 枝
        const stickNo = (card.no || 1);
        const raisedIdx = (stickNo * 7 + 3) % 5;
        this._drawTimer = setTimeout(() => {
          this._drawTimer = null;
          this.setData({ stage: 'done', card, raisedIdx });
          this._firstDrawHint();
        }, SHAKE_MS);
      })
      .catch(() => {
        this.setData({ stage: 'idle' });
        wx.showToast({ title: '摇签失败，请重试', icon: 'none' });
      });
  },

  /* v2026-08-17（PM）：「再摇一支」不自动摇——复位到待抽状态
     （stage=idle + 清签卡 + 签枝回落），引语回到「点一下签筒，摇一支」，
     由用户自己点签筒再摇；不会触发 onShake */
  resetDraw() {
    this.setData({ stage: 'idle', card: null, raisedIdx: -1 });
  },

  /* 每日首摇提示(本地日期标记,非登录态校验) */
  _firstDrawHint() {
    try {
      const day = bjDay();
      if (wx.getStorageSync(FIRST_DAY_KEY) !== day) {
        wx.setStorageSync(FIRST_DAY_KEY, day);
        wx.showToast({ title: '今日首签 · 心诚则灵', icon: 'none' });
      }
    } catch (e) { /* ignore */ }
  },

  /* 保存签卡(已收藏由后端 already 提示) */
  onSave() {
    const card = this.data.card;
    if (!card || this.data.saving) return;
    this.setData({ saving: true });
    api.saveQian({ no: card.no })
      .then((res) => {
        wx.showToast({
          title: res && res.already ? '这张签已在您的收藏中' : '签卡已保存 · 可分享给亲友',
          icon: 'none',
        });
      })
      .catch(() => {
        wx.showToast({ title: '保存失败，请重试', icon: 'none' });
      })
      .then(() => this.setData({ saving: false }));
  },
});
