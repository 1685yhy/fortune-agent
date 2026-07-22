# WeChat Mini Program Review Checklist

> Application: 易理明灯 (AppID: wxfc41c6b04fa892d1)
> Positioning: AI-powered traditional culture learning tool
> Production API: https://api.yilimingdeng.com

---

## 1. Pre-Submission Requirements

### 1.1 HTTPS API (Mandatory)

- [ ] API server is reachable via HTTPS (port 443)
- [ ] SSL certificate is valid (not self-signed for production)
- [ ] All API endpoints return proper HTTP status codes
- [ ] API response time < 3s (WeChat audit timeout)

### 1.2 ICP Filing (Mandatory for Chinese mainland)

- [ ] Domain (api.yilimingdeng.com) has ICP filing
- [ ] ICP filing number displayed in mini program settings (if required)
- [ ] Server (124.221.233.214, Tencent Cloud) ICP filing completed

### 1.3 Domain Whitelist Configuration

- [ ] `api.yilimingdeng.com` added to WeChat Dev Tools -> Details -> Domain whitelist
- [ ] Only HTTPS protocol (wss if WebSocket is used)
- [ ] No IP addresses in whitelist (WeChat requires domain names)
- [ ] Server-side firewall allows inbound HTTPS (port 443)

### 1.4 Content Compliance

- [ ] No claims like "guaranteed accuracy" or "100% effective"
- [ ] All fortune-related content framed as "cultural reference" / "entertainment"
- [ ] No superstition-related keywords in app description or screenshots
- [ ] AI-generated content disclaimer present

---

## 2. Required In-App Pages

### 2.1 Privacy Policy (`pages/privacy/privacy`)

- [ ] Explains what user data is collected (WeChat nickname, avatar, birth data)
- [ ] Explains how data is used (personalized fortune analysis)
- [ ] Explains data retention and deletion policy
- [ ] Includes contact information for data inquiries
- [ ] Link accessible from "My" tab

### 2.2 User Agreement (`pages/agreement/agreement`)

- [ ] Terms of service for using the mini program
- [ ] User responsibilities (no abuse, no commercial misuse)
- [ ] Disclaimer of liability for AI-generated content
- [ ] Intellectual property notice

### 2.3 Content Disclaimer

- [ ] Prominently displayed before any fortune analysis
- [ ] Wording: "本内容由AI生成，仅供文化参考，不可作为决策依据"
- [ ] (English: "This content is AI-generated, for cultural reference only, not a basis for decision-making")

---

## 3. Screenshot Requirements

Prepare 5-6 clear screenshots for the review submission:

### Tab Screenshots (4 total)
- [ ] **Today tab** — shows daily fortune/calendar view
- [ ] **Chat tab** — shows a conversation with the AI
- [ ] **Reports tab** — shows list of previous reports
- [ ] **My tab** — shows user profile and settings

### Additional Screenshots (1-2 total)
- [ ] **Privacy Policy page** — shows the privacy policy content
- [ ] **Normal usage flow** — entering birth info -> receiving analysis

### Screenshot Guidelines
- [ ] Screenshots show the app in a positive, educational light
- [ ] No exorbitant claims or fortune-telling terminology visible
- [ ] Clean UI — no development banners or debug info visible
- [ ] Screenshot resolution matches target device (iPhone 6/7/8 recommended)
- [ ] Chinese text in all screenshots

---

## 4. Review Category Selection

- [ ] Select **"工具 > 教育"** (Tools > Education) as primary category
- [ ] Secondary category: **"工具 > 文化"** (Tools > Culture)
- [ ] Do NOT select "占卜" (divination) or "算命" (fortune-telling)

---

## 5. Common Rejection Reasons & Mitigation

| Risk | Mitigation |
|------|-----------|
| **"涉及迷信内容"** (Superstitious content) | All content framed as "传统文化学习" (traditional culture learning). Add disclaimer. |
| **"ICP备案缺失"** (No ICP filing) | Complete ICP filing before submission. Allow 1-2 weeks. |
| **"功能与描述不符"** (App doesn't match description) | Ensure app description says "AI传统文化学习工具" not "算命" |
| **"无法加载"** (Can't load) | Ensure HTTPS is properly configured. Test on cellular network. |
| **"隐私政策缺失"** (No privacy policy) | Privacy policy page must be accessible without login. |

---

## 6. Submission Checklist (Final)

- [ ] ICP filing completed (allow 10-20 business days)
- [ ] Domain whitelist configured in WeChat console
- [ ] HTTPS working with valid SSL certificate
- [ ] Privacy Policy, User Agreement, and Disclaimer pages ready
- [ ] 5-6 screenshots prepared (all 4 tabs + 1-2 additional)
- [ ] App description uses "传统文化学习" language
- [ ] No debug logs, console.log, or development banners
- [ ] API baseURL changed to `https://api.yilimingdeng.com`
- [ ] `urlCheck: true` in project.config.json (production)
- [ ] Tested on cellular network (not just WiFi)
- [ ] Version number updated before submission

---

## 7. After Approval

- [ ] Monitor server logs for the first 48 hours
- [ ] Check user feedback for submission errors
- [ ] Verify HTTPS certificate auto-renewal (Let's Encrypt)
- [ ] Set up uptime monitoring for the API endpoint

---

> **Note:** WeChat review typically takes 1-7 business days.
> Re-submission is free if rejected — address the specific rejection reason and re-submit.
