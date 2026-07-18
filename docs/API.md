# 易理明灯 API 文档

## 基础信息
- 生产环境: `https://yilichat.com`
- 测试环境: `http://124.221.233.214:8765`
- 交互文档: `https://yilichat.com/docs` (FastAPI Swagger)

---

## 1. AI 对话

### POST /api/chat
```json
// Request
{
  "message": "帮我看看八字 1990年5月20日 下午3点 北京 男",
  "user_id": "user_001"
}
// Response
{
  "reply": "你的八字庚午辛巳乙酉...",
  "parts": ["..."],
  "membership": {"plan": "free", "queries_remaining": 2}
}
```

### POST /v1/chat/completions (OpenAI 兼容)
```json
// Request
{
  "model": "deepseek-v4-flash",
  "messages": [{"role": "user", "content": "你好"}],
  "max_tokens": 500
}
// Response (standard OpenAI format)
{"id": "...", "choices": [{"message": {"content": "..."}}]}
```

---

## 2. CV 面相分析

### POST /api/face-reading
```bash
curl -X POST https://yilichat.com/api/face-reading \
  -F "image=@selfie.jpg" \
  -F "user_id=user_001" \
  -F "personality=sassy"
```
```json
// Response
{
  "status": "ok",
  "measurements": {
    "face_shape": {"type": "鹅蛋脸", "confidence": 90},
    "eye_type": {"type": "桃花眼", "confidence": 82},
    "nose_type": {"type": "悬胆鼻", "confidence": 90},
    "skin_tone": {"type": "红润", "confidence": 85},
    "moles": [{"region": "下巴", "size_px": 4.2}]
  },
  "report": "📷 **面相分析报告**\n\n..."
}
```

---

## 3. CV 手相分析

### POST /api/palm-reading
```bash
curl -X POST https://yilichat.com/api/palm-reading \
  -F "image=@hand.jpg" \
  -F "user_id=user_001"
```

---

## 4. 日历

### POST /api/calendar/daily
```json
// Request
{"user_id": "user_001"}
// Response
{
  "status": "ok",
  "calendar": {
    "date": "2026-07-18",
    "yi": [{"action": "学习充电", "time": "辰时7-9点", "reason": "补土制水"}],
    "ji": [{"action": "冲动消费", "time": "酉时17-19点", "reason": "金水泄财"}],
    "lucky_color": "黄色", "lucky_direction": "南", "lucky_number": "5"
  }
}
```

### POST /api/calendar/week
```json
{"user_id": "user_001"}
// Returns array of 7 daily calendars
```

---

## 5. 仪表盘

### GET /api/dashboard/{user_id}
```json
{
  "profile": {"has_bazi": true, "bazi": "庚午 辛巳 乙酉 甲申"},
  "recent_activity": [{"intent": "bazi", "question": "...", "date": "2026-07-18"}],
  "accuracy": {"total_feedback": 6, "accuracy_pct": 100.0},
  "preferences": {"preferred_style": "gentle", "preferred_topic": "wealth"},
  "calendar_today": {"mood": "水润木生", "yi_count": 3, "ji_count": 3}
}
```

---

## 6. 反馈

### GET /api/user/{user_id}/accuracy
```json
{"total_feedback": 6, "accuracy_pct": 100.0, "preferred_style": "gentle"}
```

### POST /api/feedback/{consultation_id}?feedback=positive

---

## 7. 分享卡片

### GET /api/share-card/{user_id}?style=dark
```json
{
  "status": "ok",
  "bazi": "庚午 辛巳 乙酉 甲申",
  "quote": "命里七杀带贵人，一生逢凶化吉 ✨",
  "share_text": "🔮 我的八字..."
}
```

---

## 认证方式

### 小程序
```javascript
// 1. 微信登录获取 code
wx.login({
  success: (res) => {
    // 2. 发送到后端换取 token
    wx.request({
      url: 'https://yilichat.com/api/auth/login',
      data: { code: res.code },
      success: (r) => {
        // 3. 用 token 访问 API
        wx.request({
          url: 'https://yilichat.com/api/chat',
          header: { 'Authorization': 'Bearer ' + r.data.token },
          data: { message: '你好', user_id: r.data.openid }
        })
      }
    })
  }
})
```

### 企业微信/公众号
CoW 自动处理认证，用户无需关心。

---

## 错误码

| 状态码 | 含义 |
|--------|------|
| 200 | 正常 |
| 400 | 参数错误 |
| 401 | 未认证 |
| 429 | 请求过多 |
| 503 | 服务暂不可用 |
