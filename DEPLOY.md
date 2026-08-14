# 易理明灯 (Fortune Agent) — 生产部署指南

> **警告**: 本指南仅用于生产环境准备。域名购买、DNS 更改和实际的微信审核提交需手动完成。
>
> **服务器**: 124.221.233.214 (Tencent Cloud)
> **小程序 AppID**: wxfc41c6b04fa892d1

---

## 目录

1. [概述](#1-概述)
2. [服务器环境准备](#2-服务器环境准备)
3. [防火墙配置](#3-防火墙配置)
4. [Nginx + SSL 配置](#4-nginx--ssl-配置)
5. [Systemd 服务管理](#5-systemd-服务管理)
6. [小程序域名白名单](#6-小程序域名白名单)
7. [小程序审核提交](#7-小程序审核提交)
8. [监控与运维](#8-监控与运维)
9. [回滚流程](#9-回滚流程)
10. [故障排查](#10-故障排查)

---

## 1. 概述

### 架构

```
用户 (微信小程序)
    ↓ HTTPS (443)
Nginx (SSL termination + 反向代理)
    ↓ HTTP (127.0.0.1:8765)
Uvicorn (Fortune Agent API)
    ↓
各种引擎 (LLM, 八字, 紫微, 六爻等)
```

### 端口说明

| 端口 | 用途 | 外部可访问 |
|------|------|-----------|
| 443 | HTTPS (Nginx) | 是 |
| 80 | HTTP -> HTTPS 重定向 | 是 |
| 8765 | Fortune Agent API (内网) | 否 (仅限本地) |
| 9898 | cow-fortune (内网) | 否 (仅限本地) |

### 域名

- **API**: `api.yilimingdeng.com` -> 124.221.233.214 (需 DNS A 记录)
- **生产环境**: `https://api.yilimingdeng.com`

---

## 2. 服务器环境准备

### 2.1 初始服务器设置

```bash
# SSH 登录服务器
sshpass -p 'Love521922..' ssh -o StrictHostKeyChecking=no root@124.221.233.214

# 更新系统
apt update && apt upgrade -y

# 安装必要软件
apt install -y nginx python3 python3-pip python3-venv git iptables-persistent certbot python3-certbot-nginx

# 设置时区
timedatectl set-timezone Asia/Shanghai
```

### 2.2 项目部署

```bash
# 创建项目目录
mkdir -p /opt/fortune-agent
mkdir -p /opt/fortune-data/{books,vectordb,userdata,charts}

# 克隆代码 (首次部署)
cd /opt/fortune-agent
git clone <your-repo-url> .

# 或者手动上传代码
# 从本地复制到服务器:
# scp -r /path/to/fortune-agent/* root@124.221.233.214:/opt/fortune-agent/

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install --upgrade pip
pip install -e .
```

### 2.3 数据目录

```bash
# 确保数据目录存在且权限正确
chmod 755 /opt/fortune-data
chown -R root:root /opt/fortune-data
```

---

## 3. 防火墙配置

### 3.1 使用自动化脚本

```bash
# 在本地开发机执行:
bash deploy/production.sh
```

脚本会自动执行:
- 开放端口 8765 (iptables)
- 生成自签名 SSL 证书
- 配置 Nginx 反向代理
- 创建 systemd 服务并启动

### 3.2 手动配置 (如需)

```bash
# SSH 到服务器
sshpass -p 'Love521922..' ssh root@124.221.233.214

# 查看当前防火墙规则
iptables -L YJ-FIREWALL-INPUT -n --line-numbers

# 开放 HTTPS 端口 (443)
iptables -I YJ-FIREWALL-INPUT 1 -p tcp --dport 443 -j ACCEPT

# 开放 HTTP 端口 (80)
iptables -I YJ-FIREWALL-INPUT 1 -p tcp --dport 80 -j ACCEPT

# 开放 API 端口 (8765) - 内部使用
iptables -I YJ-FIREWALL-INPUT 1 -p tcp --dport 8765 -j ACCEPT

# 保存规则 (Debian/Ubuntu)
apt install -y iptables-persistent
netfilter-persistent save

# CentOS/RHEL
# service iptables save
```

---

## 4. Nginx + SSL 配置

### 4.1 Nginx 反向代理配置

配置文件: `/etc/nginx/sites-available/fortune`

```nginx
server {
    listen 443 ssl http2;
    server_name api.yilimingdeng.com;

    ssl_certificate /etc/nginx/ssl/fortune.crt;
    ssl_certificate_key /etc/nginx/ssl/fortune.key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    location /health {
        access_log off;
        proxy_pass http://127.0.0.1:8765/api/health;
        proxy_set_header Host $host;
    }
}

server {
    listen 80;
    server_name api.yilimingdeng.com;
    return 301 https://$server_name$request_uri;
}
```

### 4.2 启用并测试

```bash
ln -sf /etc/nginx/sites-available/fortune /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### 4.3 Let's Encrypt 证书 (生产必需)

> **注意**: 获取 Let's Encrypt 证书前必须先配置 DNS A 记录指向服务器 IP。

```bash
# 安装 certbot
apt install -y certbot python3-certbot-nginx

# 获取证书 (会自动修改 Nginx 配置)
certbot --nginx -d api.yilimingdeng.com --non-interactive --agree-tos -m your-email@example.com

# 验证自动续期
certbot renew --dry-run

# 查看证书状态
certbot certificates
```

Let's Encrypt 证书有效期 90 天，certbot 会自动续期（通过 systemd timer）。

### 4.4 自签名证书 (临时/测试用)

```bash
# 生成自签名证书 (有效期365天)
mkdir -p /etc/nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/fortune.key \
  -out /etc/nginx/ssl/fortune.crt \
  -subj '/CN=api.yilimingdeng.com'
```

> **注意**: 微信小程序要求有效的 SSL 证书。自签名证书仅用于测试，正式提交审核前必须替换为 Let's Encrypt 或其他 CA 签发的证书。

---

## 5. Systemd 服务管理

### 5.1 服务配置

配置文件: `/etc/systemd/system/fortune-agent.service`

```ini
[Unit]
Description=Fortune Agent API - 易理明灯
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/fortune-agent

Environment=ANTHROPIC_API_KEY=sk-REPLACED-REMOVED-KEY
Environment=PYTHONPATH=/opt/fortune-agent
Environment=LOG_LEVEL=info

ExecStart=/opt/fortune-agent/.venv/bin/python3 -m uvicorn src.main:app \
    --host 127.0.0.1 \
    --port 8765 \
    --workers 2 \
    --log-level info

Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 5.2 常用命令

```bash
# 启动/停止/重启
systemctl start fortune-agent
systemctl stop fortune-agent
systemctl restart fortune-agent

# 查看状态
systemctl status fortune-agent

# 查看日志
journalctl -u fortune-agent -f
journalctl -u fortune-agent --since "5 min ago"

# 开机自启
systemctl enable fortune-agent
systemctl disable fortune-agent
```

### 5.3 更新服务后的重载

```bash
# 修改 service 文件后
systemctl daemon-reload
systemctl restart fortune-agent
```

---

## 6. 小程序域名白名单

### 6.1 配置步骤

1. 打开 **微信开发者工具**
2. 顶部菜单: **详情** -> **本地设置** (或 **项目设置**)
3. 找到 **域名白名单** 或 **服务器域名** 配置
4. 添加: `https://api.yilimingdeng.com`
5. 确保 **不校验合法域名** 复选框 **未勾选** (生产环境)

### 6.2 小程序 `project.config.json`

确保 `setting` 中包含 `urlCheck: true`:

```json
"setting": {
    "urlCheck": true,
    ...
}
```

`urlCheck: true` 表示开启 URL 安全校验，确保所有请求都经过白名单域名。

### 6.3 API 地址更新

在 `miniprogram/utils/api.js` 中修改 baseURL:

```javascript
// 生产环境
baseURL: 'https://api.yilimingdeng.com',
```

---

## 7. 小程序审核提交

### 7.1 提交前准备

完整的审核清单请参考: `deploy/wechat-checklist.md`

核心要点:

| 要求 | 说明 |
|------|------|
| **HTTPS** | 必须使用有效 SSL 证书（非自签名） |
| **ICP 备案** | 域名必须有 ICP 备案（1-2 周） |
| **内容合规** | 定位为"AI 传统文化学习工具"，非"算命" |
| **隐私政策** | 必须包含数据收集和使用说明 |
| **免责声明** | AI 生成内容的明确免责声明 |
| **分类选择** | 选择"工具 > 教育"，不选"占卜/算命" |

### 7.2 截图要求

- 所有 4 个 Tab 页（今日、对话、报告、我的）
- 隐私政策页面
- 正常使用流程（输入信息 -> 获取分析）
- 不包含任何迷信或夸大宣传用语
- 中文界面

### 7.3 审核注意事项

- 审核通常需要 1-7 个工作日
- 被拒后可重新提交（免费）
- 根据具体拒绝原因修改后重新提交

---

## 8. 监控与运维

### 8.1 日志轮转 (Logrotate)

创建 `/etc/logrotate.d/fortune-agent`:

```
/var/log/fortune-agent/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

### 8.2 健康检查端点

```bash
# 健康检查
curl -s https://api.yilimingdeng.com/api/health
# 预期返回: {"status": "ok", ...}

# 可以配置外部监控服务 (如 uptimerobot.com)
# 每 5 分钟检查一次 https://api.yilimingdeng.com/api/health
```

### 8.3 资源监控

```bash
# CPU/内存使用
htop
top -bn1 | head -20

# 磁盘使用
df -h

# Nginx 访问日志
tail -f /var/log/nginx/access.log | grep -v health

# Nginx 错误日志
tail -f /var/log/nginx/error.log

# API 调用统计
grep -c "POST /api/chat" /var/log/nginx/access.log
```

### 8.4 定期维护

```bash
# 检查证书到期时间
openssl x509 -in /etc/nginx/ssl/fortune.crt -noout -dates

# 手动续期 Let's Encrypt
certbot renew

# 更新系统包
apt update && apt upgrade -y

# 重启服务
systemctl restart fortune-agent
```

### 8.5 告警建议

推荐使用以下免费服务设置告警:

- **UptimeRobot** (免费 50 个监控): 每 5 分钟检查 `/api/health`
- **腾讯云云监控**: 服务器 CPU/内存/磁盘告警
- **Cron + 邮件**: 每天检查 SSL 证书到期时间

---

## 9. 回滚流程

### 9.1 代码回滚

```bash
# 方案 A: Git 回滚
cd /opt/fortune-agent
git log --oneline -5          # 查看最近 5 个 commit
git reset --hard <previous-commit-hash>
systemctl restart fortune-agent

# 方案 B: 备份恢复
# 部署前先备份:
cp -r /opt/fortune-agent /opt/fortune-agent.backup.$(date +%Y%m%d_%H%M%S)
# 回滚时:
rm -rf /opt/fortune-agent
cp -r /opt/fortune-agent.backup.<timestamp> /opt/fortune-agent
systemctl restart fortune-agent
```

### 9.2 Nginx 配置回滚

```bash
# 备份配置
cp /etc/nginx/sites-available/fortune /etc/nginx/sites-available/fortune.bak

# 回滚
cp /etc/nginx/sites-available/fortune.bak /etc/nginx/sites-available/fortune
nginx -t && systemctl reload nginx
```

### 9.3 SSL 证书回滚

```bash
# Let's Encrypt 自动备份在:
ls -la /etc/letsencrypt/archive/api.yilimingdeng.com/

# 恢复 certbot 备份:
certbot certificates
# 如需要重新获取:
certbot --nginx -d api.yilimingdeng.com --force-renewal
```

### 9.4 完整回滚步骤

1. **停止服务**: `systemctl stop fortune-agent`
2. **回滚代码**: 使用 Git 重置或备份恢复
3. **回滚配置**: 恢复 Nginx 配置
4. **启动服务**: `systemctl start fortune-agent`
5. **验证**: 检查 `/api/health` 返回正常
6. **检查日志**: `journalctl -u fortune-agent -n 50`

---

## 10. 故障排查

### 10.1 服务无法启动

```bash
# 查看详细错误
journalctl -u fortune-agent -n 50 --no-pager

# 手动启动试试 (查看 Python 错误)
sudo -u root /opt/fortune-agent/.venv/bin/python3 -m uvicorn src.main:app --host 127.0.0.1 --port 8765

# 检查端口占用
ss -tlnp | grep 8765
```

### 10.2 Nginx 502 Bad Gateway

```bash
# 检查后端是否运行
curl http://127.0.0.1:8765/api/health

# 检查 Nginx 错误日志
tail -f /var/log/nginx/error.log

# 检查后端日志
journalctl -u fortune-agent -n 20

# 常见原因: uvicorn 未启动、端口配置不对、socket 文件权限
```

### 10.3 SSL 证书问题

```bash
# 检查证书
openssl x509 -in /etc/nginx/ssl/fortune.crt -text -noout | grep -E "Subject:|Not Before|Not After"

# 测试 SSL 连接
openssl s_client -connect api.yilimingdeng.com:443 -servername api.yilimingdeng.com

# 如果 certbot 有问题
certbot certificates  # 查看现有证书
certbot renew --force-renewal  # 强制续期
```

### 10.4 防火墙阻止连接

```bash
# 查看当前规则
iptables -L YJ-FIREWALL-INPUT -n --line-numbers

# 临时关闭防火墙测试 (慎用!)
iptables -P INPUT ACCEPT
iptables -F YJ-FIREWALL-INPUT

# 恢复规则后保存
netfilter-persistent save
```

### 10.5 API 响应慢

```bash
# 检查服务器负载
top -bn1 | head -5
free -h

# 检查 Nginx 访问日志中的响应时间
tail -100 /var/log/nginx/access.log | awk '{print $NF}'

# 优化方向
# - 增加 uvicorn workers (目前 2 个)
# - 检查 LLM API 响应时间
# - 检查向量数据库查询性能
```

---

## 附录

### A. 快捷命令速查

| 操作 | 命令 |
|------|------|
| 查看服务状态 | `systemctl status fortune-agent` |
| 查看日志 | `journalctl -u fortune-agent -f` |
| 重启服务 | `systemctl restart fortune-agent` |
| 重载 Nginx | `nginx -t && systemctl reload nginx` |
| 健康检查 | `curl https://api.yilimingdeng.com/api/health` |
| 查看证书 | `certbot certificates` |
| 续期证书 | `certbot renew` |
| 保存防火墙 | `netfilter-persistent save` |

### B. 相关文件

| 文件 | 用途 |
|------|------|
| `/etc/systemd/system/fortune-agent.service` | Systemd 服务配置 |
| `/etc/nginx/sites-available/fortune` | Nginx 反向代理配置 |
| `/etc/nginx/ssl/fortune.crt` | SSL 证书 |
| `/etc/nginx/ssl/fortune.key` | SSL 私钥 |
| `/opt/fortune-agent/` | 项目代码目录 |
| `/opt/fortune-data/` | 数据存储目录 |
| `/var/log/nginx/` | Nginx 日志 |

### C. 检查清单 (首次部署)

- [ ] 服务器 SSH 连接正常
- [ ] 防火墙端口已开放 (80, 443, 8765)
- [ ] Nginx 已安装并运行
- [ ] SSL 证书已配置 (自签名或 Let's Encrypt)
- [ ] Systemd 服务已创建并启动
- [ ] 健康检查通过 (`/api/health`)
- [ ] DNS A 记录已配置 (api.yilimingdeng.com -> 124.221.233.214)
- [ ] ICP 备案已完成
- [ ] 小程序域名白名单已配置
- [ ] API baseURL 已更新为生产地址
- [ ] 审核截图已准备
- [ ] 隐私政策和用户协议已添加
- [ ] 内容免责声明已添加
