// 对话 — AI 命运伴侣聊天
const api = require('../../utils/api');
const security = require('../../utils/security');

Page({
  data: {
    messages: [],
    inputText: '',
    sending: false,
    scrollToView: '',
    selectedScenario: '',
    scenarios: [
      { id: '', label: '随便聊聊', icon: 'chat' },
      { id: 'career', label: '换工作', icon: 'career' },
      { id: 'love', label: '感情', icon: 'love' },
      { id: 'wealth', label: '财运', icon: 'wealth' },
      { id: 'health', label: '健康', icon: 'health' },
    ],
    showScenarios: false,
    personality: 'sassy',
    typingText: '',
    typingDots: '',
    isTyping: false,
    // 语音+图片
    isRecording: false,
    imagePreview: '',
    _recorder: null,
    // 结构化报告展开状态
    reportExpanded: false,
  },

  onLoad() {
    this.loadHistory();
    this._initRecorder();
  },

  // ---- 语音录制 ----
  _initRecorder() {
    const rm = wx.getRecorderManager();
    rm.onStart(() => { this.setData({ isRecording: true }); });
    rm.onStop((res) => {
      this.setData({ isRecording: false });
      if (res.tempFilePath) {
        this.sendVoice(res.tempFilePath);
      }
    });
    rm.onError((e) => {
      this.setData({ isRecording: false });
      wx.showToast({ title: '录音失败', icon: 'none' });
    });
    this._recorder = rm;
  },

  startRecord() {
    if (this.data.sending) return;
    this._recorder.start({ format: 'mp3', duration: 60000 });
  },

  stopRecord() {
    this._recorder.stop();
  },

  async sendVoice(filePath) {
    const userMsg = {
      id: 'msg-' + Date.now(),
      role: 'user',
      content: '[语音消息]',
      time: this.getTimeString(),
      type: 'voice',
      voicePath: filePath,
    };
    const msgs = [...this.data.messages, userMsg];
    this.setData({ messages: msgs, sending: true, isTyping: true });
    this.saveHistory();
    this.scrollToBottom();

    try {
      const reply = await api.chat('[语音消息]', this.data.selectedScenario, []);
      this._addReply(reply);
    } catch (e) {
      this._addReply({ reply: '语音识别暂不可用，请用文字描述你的问题 🙏' });
    }
  },

  // ---- 图片选择 ----
  chooseImage() {
    if (this.data.sending) return;
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const tempPath = res.tempFiles[0].tempFilePath;
        this.setData({ imagePreview: tempPath });
        this.sendImage(tempPath);
      },
    });
  },

  cancelImage() {
    this.setData({ imagePreview: '' });
  },

  async sendImage(filePath) {
    const userMsg = {
      id: 'msg-' + Date.now(),
      role: 'user',
      content: '[图片]',
      time: this.getTimeString(),
      type: 'image',
      imagePath: filePath,
    };
    const msgs = [...this.data.messages, userMsg];
    this.setData({ messages: msgs, imagePreview: '', sending: true, isTyping: true });
    this.saveHistory();
    this.scrollToBottom();

    try {
      const reply = await api.chat('[图片分析请求]', this.data.selectedScenario, []);
      this._addReply(reply);
    } catch (e) {
      this._addReply({ reply: '图片分析暂不可用，请描述你想了解的内容 🙏' });
    }
  },

  _addReply(data) {
    const reply = typeof data === 'string' ? data : (data.reply || '');
    const assistantMsg = {
      id: 'msg-' + Date.now(),
      role: 'assistant',
      content: reply,
      time: this.getTimeString(),
      type: 'text',
    };
    const msgs = [...this.data.messages, assistantMsg];
    this.setData({ messages: msgs, sending: false, isTyping: false });
    this.saveHistory();
    this.scrollToBottom();
  },

  onShow() {
    // 每次显示时滚动到底部
    this.scrollToBottom();
  },

  // ---- 历史消息 ----
  loadHistory() {
    try {
      const encrypted = wx.getStorageSync('ylm_chat_messages');
      if (encrypted) {
        const str = security.decrypt(encrypted);
        const saved = JSON.parse(str);
        if (saved && saved.length > 0) {
          this.setData({ messages: saved });
          this.scrollToBottom();
          return;
        }
      }
    } catch (e) {
      console.warn('[Chat] Failed to load history');
    }

    // 无历史，显示欢迎消息
    this.addWelcomeMessage();
  },

  addWelcomeMessage() {
    const welcome = {
      id: 'welcome',
      role: 'assistant',
      content: '你好！我是易理明灯 AI 命运伴侣\n\n我可以帮你：\n• 查看八字运势，解读命理格局\n• 分析事业、感情、财运等人生课题\n• 提供每日宜忌建议\n\n选一个话题开始吧 请说',
      time: this.getTimeString(),
      type: 'text',
    };
    this.setData({ messages: [welcome] });
    this.saveHistory();
  },

  // ---- 场景选择 ----
  toggleScenarios() {
    this.setData({ showScenarios: !this.data.showScenarios });
  },

  selectScenario(e) {
    const id = e.currentTarget.dataset.id || '';
    const label = e.currentTarget.dataset.label || '';

    this.setData({
      selectedScenario: id,
      showScenarios: false,
    });

    if (id) {
      // 追加系统提示消息
      const sysMsg = {
        id: 'scenario-' + Date.now(),
        role: 'system',
        content: `场景已切换至：${label}`,
        time: this.getTimeString(),
        type: 'scenario_hint',
      };
      const msgs = [...this.data.messages, sysMsg];
      this.setData({ messages: msgs });
      this.saveHistory();
      this.scrollToBottom();
    }
  },

  // ---- 人格模式切换 ----
  onPersonalityTap(e) {
    const mode = e.currentTarget.dataset.mode;
    this.setData({ personality: mode });
  },

  // ---- 输入 ----
  onInput(e) {
    this.setData({ inputText: e.detail.value });
  },

  // ---- 发送消息 ----
  async sendMessage() {
    const text = this.data.inputText.trim();
    if (!text || this.data.sending) return;

    // 添加用户消息
    const userMsg = {
      id: 'msg-' + Date.now(),
      role: 'user',
      content: text,
      time: this.getTimeString(),
      type: 'text',
    };

    const newMessages = [...this.data.messages, userMsg];
    this.setData({
      messages: newMessages,
      inputText: '',
      sending: true,
      isTyping: true,
    });
    this.saveHistory();
    this.scrollToBottom();

    // 开始打字动画
    this.startTypingAnimation();

    try {
      const history = this.data.messages
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .slice(-20)
        .map(m => ({ role: m.role, content: m.content }));

      const res = await api.chat(text, this.data.selectedScenario, history);

      this.clearTypingAnimation();

      const reply = res.reply || res.message || '...';
      const aiMsg = {
        id: 'msg-' + Date.now() + 1,
        role: 'assistant',
        content: reply,
        time: this.getTimeString(),
        type: res.structured ? 'structured' : 'text',
        structured: res.structured || null,
      };

      const finalMessages = [...this.data.messages, aiMsg];
      this.setData({
        messages: finalMessages,
        sending: false,
        isTyping: false,
        typingText: '',
      });
      this.saveHistory();
      this.scrollToBottom();
    } catch (e) {
      this.clearTypingAnimation();
      const errMsg = {
        id: 'msg-' + Date.now() + 2,
        role: 'assistant',
        content: '网络开小差了，请稍后再试 感谢',
        time: this.getTimeString(),
        type: 'text',
      };
      this.setData({
        messages: [...this.data.messages, errMsg],
        sending: false,
        isTyping: false,
      });
      this.saveHistory();
      this.scrollToBottom();
    }
  },

  // ---- 打字动画 ----
  startTypingAnimation() {
    const dots = ['.', '..', '...'];
    let i = 0;
    this._typingTimer = setInterval(() => {
      i = (i + 1) % dots.length;
      this.setData({ typingDots: dots[i] });
    }, 500);
  },

  clearTypingAnimation() {
    if (this._typingTimer) {
      clearInterval(this._typingTimer);
      this._typingTimer = null;
    }
    this.setData({ typingDots: '' });
  },

  // ---- 消息操作 ----
  toggleReport(e) {
    this.setData({ reportExpanded: !this.data.reportExpanded });
  },

  copyMessage(e) {
    const content = e.currentTarget.dataset.content || '';
    wx.setClipboardData({
      data: content,
      success: () => wx.showToast({ title: '已复制', icon: 'none' }),
    });
  },

  // ---- 工具函数 ----
  getTimeString() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    return `${h}:${m}`;
  },

  scrollToBottom() {
    setTimeout(() => {
      this.setData({ scrollToView: 'scroll-bottom' });
    }, 100);
  },

  saveHistory() {
    try {
      const recent = this.data.messages.slice(-50);
      wx.setStorageSync('ylm_chat_messages', security.encrypt(JSON.stringify(recent)));
    } catch (e) {
      console.warn('[Chat] Failed to save history');
    }
  },

  clearChat() {
    wx.showModal({
      title: '清空对话',
      content: '确定清空所有聊天记录吗？',
      success: (res) => {
        if (res.confirm) {
          wx.removeStorageSync('ylm_chat_messages');
          this.setData({ messages: [] });
          this.addWelcomeMessage();
        }
      },
    });
  },

  goXuetang() {
    wx.navigateTo({ url: '/pages/xuetang/xuetang' });
  },

  onShareAppMessage() {
    return {
      title: '易理明灯 - AI 命运伴侣',
      path: '/pages/chat/chat',
    };
  },
});
