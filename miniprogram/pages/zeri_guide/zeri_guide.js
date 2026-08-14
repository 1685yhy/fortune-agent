// 择吉日 · 引导页：6 场景图标 + 一句话示例输入框 → 跳对话页携带场景词
// 传参机制（沿用现有）：globalData.jianQuestion 预填为聊天首条消息（chat._consumePrefill）
const api = require('../../utils/api');
const theme = require('../../utils/theme');

/* 6 场景（与后端 SCENES 的 key 一致；seal 为印章单字） */
const SCENES = [
  { key: '嫁娶', seal: '嫁', sub: '婚礼领证 · 设宴' },
  { key: '搬家', seal: '迁', sub: '入宅安顿 · 乔迁' },
  { key: '开业', seal: '开', sub: '开张揭牌 · 纳客' },
  { key: '出行', seal: '行', sub: '远行出发 · 启程' },
  { key: '提车', seal: '车', sub: '新车到家 · 上路' },
  { key: '签约', seal: '签', sub: '合同落定 · 用印' },
];

/* 一句话示例（点击填充输入框） */
const EXAMPLES = [
  '下个月想搬家，帮我挑个好日子',
  '8月20日左右领证，哪个日子好',
  '下周三开业，帮我选个吉日',
];

Page({
  data: {
    dark: false,
    scenes: SCENES,
    examples: EXAMPLES,
    picked: '',         // 已选场景
    input: '',          // 一句话描述
    exampleIdx: 0,
  },

  onLoad() {
    theme.bindTheme(this);
  },

  /* 选场景（再点取消） */
  onPickScene(e) {
    const key = e.currentTarget.dataset.key;
    if (!key) return;
    this.setData({ picked: this.data.picked === key ? '' : key });
  },

  onInput(e) {
    this.setData({ input: e.detail.value });
  },

  /* 示例轮换填充（每点一次换一条，避免总是同一条） */
  onExampleTap() {
    const next = (this.data.exampleIdx + 1) % EXAMPLES.length;
    this.setData({ exampleIdx: next, input: EXAMPLES[next] });
  },

  /* 去对话：globalData.jianQuestion 预填 + reLaunch chat（同 today 今日小问机制） */
  goChat() {
    const picked = this.data.picked;
    const input = String(this.data.input || '').trim();
    if (!picked) {
      wx.showToast({ title: '请先选一个场景', icon: 'none' });
      return;
    }
    const q = input
      ? `我想${picked}，${input}`
      : `我想${picked}，帮我选个好日子`;
    try {
      const app = getApp();
      if (app && app.globalData) app.globalData.jianQuestion = q;
    } catch (err) { /* ignore */ }
    wx.reLaunch({ url: '/pages/chat/chat' });
  },

  /* 直接看吉日（跳过对话） */
  goZeri() {
    const picked = this.data.picked;
    if (!picked) {
      wx.showToast({ title: '请先选一个场景', icon: 'none' });
      return;
    }
    wx.navigateTo({ url: `/pages/zeri/zeri?scene=${encodeURIComponent(picked)}` });
  },

});
