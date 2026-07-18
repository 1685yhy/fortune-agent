// 对话页 — AI 聊天
const api = require('../../utils/api');

Page({
  data: {
    messages: [],
    inputText: '',
    sending: false,
    scrollToView: '',
  },

  onLoad() {
    // 欢迎消息
    this.addMessage('assistant', '你好！我是易理明灯 AI 命理顾问 🌟\n\n直接告诉我你的出生日期，我帮你看八字。\n或者试试：\n• 发一张自拍 → 面相分析\n• 「今日运势」→ 每日宜忌\n• 直接聊聊你的心事');
  },

  onInput(e) { this.setData({ inputText: e.detail.value }); },

  async sendMessage() {
    const text = this.data.inputText.trim();
    if (!text || this.data.sending) return;

    this.addMessage('user', text);
    this.setData({ inputText: '', sending: true });

    try {
      const res = await api.chat(text);
      this.addMessage('assistant', res.reply || '...');
    } catch (e) {
      this.addMessage('assistant', '网络出问题了，请重试 🙏');
    }
    this.setData({ sending: false });
  },

  addMessage(role, content) {
    const msg = { role, content, id: Date.now() };
    const messages = [...this.data.messages, msg];
    this.setData({ messages, scrollToView: 'msg-' + msg.id });
    wx.setStorageSync('chat_history', messages.slice(-50));
  },

  // 选择图片 → 面相分析
  async chooseImage() {
    const that = this;
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['camera', 'album'],
      success: async (res) => {
        const path = res.tempFiles[0].tempFilePath;
        that.addMessage('user', '[图片]');
        that.setData({ sending: true });
        try {
          const result = await api.faceReading(path);
          that.addMessage('assistant', result.report || result.message || '分析完成');
        } catch (e) {
          that.addMessage('assistant', '图片分析失败，请重试');
        }
        that.setData({ sending: false });
      },
    });
  },
});
