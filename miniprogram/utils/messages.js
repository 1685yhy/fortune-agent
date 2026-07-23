// 易理明灯 — Premium error/empty state messages
// Every message is crafted, warm, and guides the user to a next action

const MESSAGES = {
  // Network errors
  network: {
    title: '🌐 网络波动',
    subtitle: '宇宙的信号暂时打了个盹儿',
    action: '点此重新连接',
    detail: '我们的服务器在腾讯云上运行良好，检查一下你的网络设置试试',
  },

  // Empty states
  empty: {
    reports: {
      title: '📋 还没有分析报告',
      subtitle: '你的每一次命理探索都会被珍藏在这里',
      action: '去对话页开始第一次分析',
    },
    bazi: {
      title: '✨ 尚未设置八字',
      subtitle: '八字是你的命运密码，设置后即可解锁全部功能',
      action: '设置我的八字',
      detail: '不知道出生时间？选默认的 12:00 也行，准确度约80%',
    },
    chat: {
      title: '💫 开始一段对话',
      subtitle: '问运势、看八字、解梦境，我在这里等你',
      hints: ['帮我看看今天的运势', '昨晚梦到飞起来了', '我和TA八字合不合'],
    },
  },

  // Rate limit
  quota: {
    title: '🎁 今日免费额度已用完',
    subtitle: '每天3次免费分析，明天零点自动刷新',
    action: '了解会员计划（每月19.9元无限畅聊）',
    detail: '成为会员后，你的每一次分析都会更深入、更个性化',
  },

  // Server errors (user-friendly)
  server: {
    title: '🏯 命理师正在静修',
    subtitle: '我们的AI正在和古籍对话，马上回来',
    action: '点此重试',
    detail: '这种情况通常30秒内恢复，若持续出现请联系我们',
  },

  // Feature not available
  unavailable: {
    title: '🔮 此功能即将开放',
    subtitle: '正在汲取古籍中的智慧，很快就能为你服务',
    action: '先试试其他功能',
  },
};

module.exports = { MESSAGES };
