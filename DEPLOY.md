# 微信公众号算命机器人 — 部署指南

## 第一步：注册微信公众号（你来做）

1. 打开 **https://mp.weixin.qq.com** 注册一个「订阅号」（个人即可，免费）
2. 注册完成后进入后台 →「设置与开发」→「基本配置」
3. 记录以下三个信息：
   - **AppID**（开发者ID）
   - **AppSecret**（开发者密码，点击生成）
   - **IP白名单**：把你服务器的公网IP加进去

## 第二步：让本地服务能被微信访问

**方案A：ngrok 内网穿透（测试用，免费）**

```bash
# 安装 ngrok
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok

# 注册 ngrok 账号 https://dashboard.ngrok.com/signup 获取 authtoken
ngrok config add-authtoken 你的token

# 启动穿透（将本地 80 端口暴露到公网）
ngrok http 80
# 记下输出的 https 地址，如: https://xxxx.ngrok-free.app
```

**方案B：云服务器（正式用）**
- 买一台最便宜的云服务器（阿里云/腾讯云轻量应用服务器，约50元/月）
- 把 Fortune Agent 和 CoW 部署上去
- 域名备案后用 HTTPS

## 第三步：配置并启动

### 3.1 填写你的公众号信息

编辑 `/home/a/cow-fortune/config-wechatmp.json`，把以下三个值替换掉：

```json
"wechatmp_token": "你随便设一个token，比如 fortune2024",
"wechatmp_app_id": "wx开头的AppID",
"wechatmp_app_secret": "AppSecret",
```

### 3.2 启动服务

```bash
# 终端1：启动 Fortune Agent（已运行）
nohup python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8765 &

# 终端2：启动 CoW 微信公众号
cd /home/a/cow-fortune
cp config-wechatmp.json config.json
python3 app.py
```

### 3.3 微信公众号后台配置

在「设置与开发」→「基本配置」→「服务器配置」：

| 字段 | 值 |
|------|-----|
| URL | `https://xxxx.ngrok-free.app` （ngrok给的地址） |
| Token | 你在 config 里填的 token |
| EncodingAESKey | 点击「随机生成」 |
| 消息加解密方式 | 明文模式 |

点击「提交」，微信会验证你的服务器。验证通过后点击「启用」。

## 第四步：开始使用

关注你的公众号，直接发消息测试：
- 「你好」
- 「帮我看八字 1990年5月20日 下午3点 北京 男」
- 「帮我算个卦 这次投资能成吗」
- 「找个搬家吉日 2026年8月」

---

## 故障排查

```bash
# 查看 Fortune Agent 日志
tail -f /tmp/fortune-server.log

# 测试 API 是否正常
curl http://127.0.0.1:8765/api/health

# 测试 Claude 分析是否正常
curl -X POST http://127.0.0.1:8765/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"fortune-agent","messages":[{"role":"user","content":"看八字 1990年5月20日 下午3点 北京 男"}]}'
```
