// 分享笺 — 对话多选分享页（v1.4）
// 数据源：聊天页多选 → multiShare 写入 ylm_share_msgs（选中的 2-6 条消息）→ navigateTo 本页
// 流程：问答分组（用户问+明灯答成组）→ POST /api/share 拿落地页 id → 下载二维码 →
//       Canvas 2d 绘制墨韵分享卡（宣纸/墨/朱砂/印章「明灯」+ 品牌二维码）→ 预览
// 渠道区（元宝式）：微信好友（onShareAppMessage 卡片带分享图）/ 朋友圈（onShareTimeline）/
//                  生成分享图（wx.shareImageMessage，失败降级保存）/ 复制链接（落地页）
const { logErr, logWarn } = require('../../utils/log');
const shareCard = require('../../utils/shareCard');
const theme = require('../../utils/theme');

Page({
  data: {
    navOff: 0,
    dark: false,
    pairs: [],          // [{u, tag, content}]（渲染层同构预览）
    dateText: '',
    imgPath: '',        // 生成的分享卡临时路径
    generating: true,
    hasQr: false,       // 二维码是否就绪（决定说明文案，UX批4 Minor：无码不误导）
    // k84-必修2：空状态（分享参数缺失/已失效）。改前这条路径只发 Toast + navigateBack，
    // `generating` 恒为 true ⇒ 页面永远停在「研墨绘笺中…」；navigateBack 在本页是
    // **入口页**（冷启动/分享卡进入/开发者工具直接编译本页）时没有上一页可退、
    // **静默失败** ⇒ 用户彻底卡死，只能杀掉小程序。
    empty: false,
    emptyTitle: '',
    emptySub: '',
    emptyAction: '',
  },

  onLoad() {
    this._initNavOff();
    theme.bindTheme(this); // UX批4 Minor：与其他页一致接入主题绑定
    try {
      this._msgs = wx.getStorageSync('ylm_share_msgs');
    } catch (e) {
      this._msgs = [];
    }
    if (!Array.isArray(this._msgs) || this._msgs.length < 2) {
      // 两种触发面分开说：**从未勾选**（首次/直接打开本页）与**勾选过但缓存被清**
      // （清缓存、换设备、超过本地缓存期）。两条都给出**明确出口**（回到对话），
      // 不写"请重新勾选"这种只有第一种场景才成立的笼统指令。
      const neverPicked = !Array.isArray(this._msgs) || this._msgs.length === 0;
      this._showEmpty(
        neverPicked ? '分享内容缺失' : '分享内容已失效',
        neverPicked
          ? '分享参数缺失或已失效 —— 这份分享笺要从对话里选 2 条以上消息才会生成。回到对话，长按消息勾选后再发起分享。'
          : '分享参数已失效（本机缓存的分享内容已不完整或已过期）。回到对话重新勾选几条消息，再发起分享即可。'
      );
      return;
    }
    this._buildPairs();
    this.setData({ dateText: this._dateText() });
  },

  /* k84-必修2：进入**明确空状态**（而非无限「研墨绘笺中…」）。
     `generating: false` 是关键 —— 它把 wxml 从加载分支切走（`empty` 分支排在
     最前，双保险）；`onReady` 里 `pairs.length` 为 0 也不会去出图。 */
  _showEmpty(title, sub) {
    this.setData({
      generating: false,
      empty: true,
      emptyTitle: title,
      emptySub: sub,
      emptyAction: '回到对话',
    });
  },

  /* 空状态的出口：回到对话页。**不能**用 `navigateBack` —— 本页是入口页时它
     静默失败（见 onLoad 注释）。一律 `redirectTo`：它是"替换当前页"，
     无论有没有上一页都能到对话页，且不会在栈里留一个空页。 */
  onEmptyAction() {
    wx.redirectTo({
      url: '/pages/chat/chat',
      fail: () => wx.reLaunch({ url: '/pages/chat/chat' }),
    });
  },

  /* 页面渲染完成后再出图：POST /api/share 拿落地页 id → 下载二维码 → 绘制（任一失败均降级，不阻塞出图） */
  onReady() {
    if (this.data.pairs.length) this._prepareCard();
  },

  /* 出图流程编排：服务端不可用 → 本地兜底 id 且跳过二维码立即出图；二维码下载失败 → 跳过二维码 */
  _prepareCard() {
    this.setData({ generating: true });
    this._ensureShareId().then(({ id, fromServer }) => {
      if (!fromServer) return this._draw('');                 // 接口不可用：不尝试下载，立即出图
      return this._loadQr(id).then((qrPath) => this._draw(qrPath));
    }).catch(() => this._draw(''));
  },

  /* 落地页 id：POST /api/share 提交 {pairs, dateText} 拿 {id}；失败本地兜底（时间戳+随机） */
  _ensureShareId() {
    if (this._shareId) return Promise.resolve({ id: this._shareId, fromServer: this._shareFromServer !== false });
    const api = require('../../utils/api');
    return api.createShare(this.data.pairs, this.data.dateText)
      .then((res) => {
        if (res && res.id) {
          this._shareId = String(res.id);
          this._shareFromServer = true;
          return { id: this._shareId, fromServer: true };
        }
        throw new Error('响应缺少 id');
      })
      .catch((err) => {
        logWarn('share POST /api/share 失败，用本地兜底 id', err);
        this._shareId = this._localShareId();
        this._shareFromServer = false;
        return { id: this._shareId, fromServer: false };
      });
  },

  /* 下载落地页二维码（GET /api/share/qr?url=落地页）→ 临时路径；失败返回空串（跳过二维码不阻塞出图） */
  _loadQr(id) {
    const api = require('../../utils/api');
    const landingUrl = 'https://yilichat.com/share?id=' + encodeURIComponent(id);
    return api.downloadShareQr(landingUrl)
      .then((p) => p || '')
      .catch((err) => {
        logWarn('share 二维码下载失败，跳过二维码区域', err);
        return '';
      });
  },

  /* 本地兜底 id（时间戳+随机；落地页此时可能查不到内容，但链接结构一致） */
  _localShareId() {
    return 'local_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
  },

  /* 微信好友转发卡片：墨韵分享图（canvas 出图完成才渲染渠道区，此时必已就绪；兜底不带图） */
  onShareAppMessage() {
    const ret = {
      title: '易理明灯 · 夜话拾笺',
      path: '/pages/chat/chat',
    };
    if (this.data.imgPath) ret.imageUrl = this.data.imgPath;
    return ret;
  },

  /* 朋友圈卡片：同墨韵分享图（右上角「···」→ 分享到朋友圈触发） */
  onShareTimeline() {
    const ret = { title: '易理明灯 · 夜话拾笺', query: '' };
    if (this.data.imgPath) ret.imageUrl = this.data.imgPath;
    return ret;
  },

  _initNavOff() {
    const info = (wx.getWindowInfo && wx.getWindowInfo()) || {};
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
  },

  /* 朋友圈渠道：开启胶囊菜单「分享到朋友圈」+ 引导（平台无按钮直发，须经右上角「···」） */
  onChannelTimeline() {
    try {
      wx.showShareMenu({ menus: ['shareAppMessage', 'shareTimeline'] });
    } catch (e) { /* 低版本忽略：右上角菜单仍可用 */ }
    wx.showToast({ title: '请在右上角「···」\n选择分享到朋友圈', icon: 'none' });
  },

  /* 生成分享图：调起微信图片分享面板（失败自动降级保存到相册） */
  onChannelImage() {
    shareCard.shareCard(this.data.imgPath, '易理明灯 · 夜话拾笺');
  },

  /* 复制链接：落地页 https://yilichat.com/share?id=<shareId>（服务端 id 未就绪用本地兜底，链接结构一致）
     UX批4 Minor：兜底 id 为死链接 → 复制时如实提示，网络恢复后可重新进入本页再复制生效链接 */
  onChannelLink() {
    const id = this._getShareId();
    const url = 'https://yilichat.com/share?id=' + encodeURIComponent(id);
    wx.setClipboardData({
      data: url,
      success: () => wx.showToast({
        title: this._shareFromServer === false ? '已复制 · 网络恢复后链接才可访问' : '链接已复制',
        icon: 'none',
        duration: 2200,
      }),
      fail: () => wx.showToast({ title: '复制失败，请重试', icon: 'none' }),
    });
  },

  /* 落地页分享 id：有服务端 id 用之，否则本地兜底（时间戳+随机，链接结构一致） */
  _getShareId() {
    if (this._shareId) return this._shareId;
    this._shareId = this._localShareId();
    return this._shareId;
  },

  /* 今天日期文案（公历，如「八月十六 · 灯下」） */
  _dateText() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getMonth() + 1}月${pad(d.getDate())}日`;
  },

  /* 消息流 → 问答组：用户消息作为「问」，其后 AI 回复作为「答」成组 */
  _buildPairs() {
    const msgs = (this._msgs || []).slice(0, 6);
    const pairs = [];
    let curU = '';
    msgs.forEach((m) => {
      if (!m) return;
      if (m.role === 'user') {
        curU = String(m.content || '');
      } else if (m.role === 'ai') {
        pairs.push({
          u: curU,
          tag: m.tag || '明灯 · 夜话',
          content: String(m.content || ''),
        });
        curU = '';
      }
    });
    if (!pairs.length) {
      // 只选了用户消息（无 AI 回复）→ 兜底成单组（问=首条，答=末条用户消息占位提示）
      const anyMsg = msgs[0];
      pairs.push({
        u: String(anyMsg && anyMsg.content || ''),
        tag: '明灯 · 夜话',
        content: '这句心事，明灯收下了。想听听明灯的回应，回到对话里把这条问出来吧。',
      });
    }
    this.setData({ pairs });
  },

  /* Canvas 2d 绘制 → 预览（qrPath 为空则分享图不带二维码）。
     UX批4 Important-4：主链路包 try/catch + 失败弹「重试」入口（长内容/字体测量等异常可重试） */
  _draw(qrPath) {
    this.setData({ generating: true, hasQr: !!qrPath });
    this._qrPath = qrPath || '';
    const query = wx.createSelectorQuery();
    query.select('#shareCanvas')
      .fields({ node: true, size: true })
      .exec((res) => {
        if (!res || !res[0] || !res[0].node) {
          this._onDrawFail();
          return;
        }
        const canvas = res[0].node;
        try {
          shareCard.drawChatCard({
            pairs: this.data.pairs,
            dateText: this.data.dateText,
          }, canvas, (tempFilePath) => {
            this.setData({ generating: false });
            if (tempFilePath) {
              this.setData({ imgPath: tempFilePath });
            } else {
              this._onDrawFail();
            }
          }, qrPath);
        } catch (e) {
          logErr('share _draw 异常', e);
          this._onDrawFail();
        }
      });
  },

  /* 出图失败：弹层带「重试」入口（不再只能返回重选消息） */
  _onDrawFail() {
    this.setData({ generating: false });
    wx.showModal({
      title: '生成图片失败',
      content: '长对话内容可能超出分享卡上限，超限部分会自动节选。可重试一次',
      confirmText: '重试',
      cancelText: '取消',
      success: (res) => {
        if (res.confirm) this._draw(this._qrPath);
      },
    });
  },

  goBack() {
    // k84-必修2 同源加固：本页可能是**入口页**（冷启动/分享卡进入），此时
    // `navigateBack` 没有上一页可退、**静默失败**；补兜底落地（与仓内既有写法
    // 一致，见 pages/history/history.js、pages/dreams/dreams.js 等）。
    wx.navigateBack({ delta: 1, fail: () => wx.reLaunch({ url: '/pages/chat/chat' }) });
  },
});
