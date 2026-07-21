# Security Architecture — 易理明灯 Fortune Agent

> 安全架构文档
> 版本：1.0.0 | 更新：2026-07-21

---

## Architecture Overview / 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    Client Layer                              │
│  ┌──────────┐  ┌──────────┐  ┌─────────────────────────┐   │
│  │  Web UI  │  │ Mini-App │  │ External API Clients    │   │
│  └────┬─────┘  └────┬─────┘  └───────────┬─────────────┘   │
│       │              │                    │                 │
├───────┴──────────────┴────────────────────┴─────────────────┤
│                    Nginx (Reverse Proxy)                     │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ • TLS 1.2+      • Security Headers   • Rate Limiting │   │
│  │ • Request Validation • Size Limits   • HSTS          │   │
│  └────────────────────────┬─────────────────────────────┘   │
├───────────────────────────┴─────────────────────────────────┤
│                    FastAPI Application                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ Rate     │ │ Auth     │ │Input     │ │ Audit        │  │
│  │ Limiter  │ │ (JWT/Key)│ │Sanitizer │ │ Logger       │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘  │
│       │             │            │               │          │
│  ┌────┴─────────────┴────────────┴───────────────┴───────┐  │
│  │                  Application Logic                      │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐  │  │
│  │  │ Bazi     │ │ Ziwei    │ │ Liuyao   │ │ RAG     │  │  │
│  │  │ Engine   │ │ Engine   │ │ Engine   │ │ Engine  │  │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └─────────┘  │  │
│  └────────────────────┬──────────────────────────────────┘  │
├───────────────────────┴────────────────────────────────────┤
│                    Data Layer                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ SQLite   │ │ VectorDB │ │ File     │ │ Audit Logs   │  │
│  │ (加密)   │ │ (Chroma) │ │ Storage  │ │ (JSON Lines) │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Defense in Depth / 纵深防御

### Layer 1: Network / 网络层
- HTTPS only (TLS 1.2+, HSTS preload)
- Nginx as reverse proxy (not exposed FastAPI directly)
- DDoS protection via cloud provider
- IP whitelist for admin endpoints

### Layer 2: Application / 应用层
- **Rate Limiting**: IP-based 30r/m chat, 10r/m analysis; user-based 100r/h
- **Authentication**: JWT for mini-program, API keys for external, admin keys
- **Input Sanitization**: XSS/SQLi/command injection/prompt injection protection
- **Audit Logging**: All sensitive operations logged to `/opt/fortune-agent/logs/audit.log`

### Layer 3: Data / 数据层
- **Encryption at Rest**: AES-256-GCM for PII (birth dates, bazi charts)
- **Key Rotation**: Versioned encryption keys, current key for writes, old keys for reads
- **Data Retention**: Auto-delete inactive users after 180 days
- **User ID Hashing**: HMAC-SHA256 hashed IDs in audit logs

### Layer 4: Compliance / 合规层
- PIPL compliance (个人信息保护法)
- Right to be forgotten (data deletion endpoint)
- Data portability (data export endpoint)
- Entertainment disclaimer on all responses

---

## Key Components / 关键组件

### Security Package (`src/security/`)

| Module | File | Responsibility |
|--------|------|----------------|
| Rate Limiter | `ratelimit.py` | Multi-strategy rate limiting with burst allowance |
| Auth Handler | `auth.py` | JWT tokens, API key validation, admin auth |
| Input Sanitizer | `sanitizer.py` | XSS/SQLi/prompt injection detection and cleaning |
| Data Encryptor | `encryption.py` | AES-256-GCM encryption with key rotation |
| Privacy Manager | `privacy.py` | Data retention, export, deletion (PIPL) |
| Audit Logger | `audit.py` | Structured audit logging with suspicious pattern detection |

### Infrastructure (`security/`)

| File | Purpose |
|------|---------|
| `nginx-hardening.conf` | Nginx security headers, rate limiting, SSL config |
| `README.md` | This document |

### Documentation (`docs/`)

| File | Purpose |
|------|---------|
| `MINI_PROGRAM_SECURITY.md` | Mini-program security checklist and guide |

---

## Incident Response Checklist / 安全事件响应

### 1. Detect / 检测
- Monitor audit logs at `/opt/fortune-agent/logs/audit.log`
- Check application logs: `/home/a/fortune-agent/logs/`
- Configure alerts for suspicious patterns (rapid deletions, mass exports)
- Review nginx access log for anomaly patterns

### 2. Assess / 评估
```
Severity Levels:
┌──────────┬────────────────────────────────────┐
│ CRITICAL │ Data breach, payment system down   │
│ HIGH     │ Rate limit bypass, auth bypass     │
│ MEDIUM   │ Suspicious pattern detected        │
│ LOW      │ Single attack attempt (blocked)    │
└──────────┴────────────────────────────────────┘
```

### 3. Respond / 响应
1. **CRITICAL**: Immediately rotate all encryption keys, revoke all JWT tokens, notify users
2. **HIGH**: Block affected IPs, review audit logs, patch vulnerability
3. **MEDIUM**: Investigate pattern, adjust rate limits if needed
4. **LOW**: Log and monitor for escalation

### 4. Recover / 恢复
- Restore from backup if data corrupted
- Rotate compromised credentials
- Update security rules
- Document incident in post-mortem

### 5. Escalation Contacts
| Severity | Contact | Response Time |
|----------|---------|---------------|
| CRITICAL | Admin: +86-xxx-xxxxxxx | 15 minutes |
| HIGH     | Dev Team: dev@fortune-agent.com | 1 hour |
| MEDIUM   | Security Team | 4 hours |

---

## Key Rotation Procedure / 密钥轮换

### Encryption Key Rotation

```bash
# 1. Generate new 32-byte encryption key
NEW_KEY=$(openssl rand -base64 32)
echo "New key (base64): $NEW_KEY"

# 2. Update environment variable (add as new version)
# Current: ENCRYPTION_KEY="v1:base64oldkey"
# Updated: ENCRYPTION_KEY="v1:base64oldkey,v2:base64newkey"

# 3. Reload application
sudo systemctl reload fortune-agent

# 4. Verify new encryptions use v2
grep "Encryption" /opt/fortune-agent/logs/audit.log

# 5. After migration period (e.g., 30 days), remove old key
# ENCRYPTION_KEY="v2:base64newkey"
```

### JWT Secret Rotation

```bash
# 1. Generate new JWT secret
JWT_SECRET=$(openssl rand -hex 32)
echo "JWT_SECRET=$JWT_SECRET"

# 2. Update environment and reload
# Note: This invalidates ALL existing tokens → users must re-login

# 3. Plan rotation during low-traffic period
# 4. Communicate with users: "Scheduled maintenance, please re-login"
```

---

## Audit Log Configuration / 审计日志配置

### Log Location
```
/opt/fortune-agent/logs/audit.log
```

### Log Format (JSON Lines)
```json
{
  "timestamp": "2026-07-21T10:30:00+00:00",
  "user_id": "hashed_user_id",
  "action": "data_export",
  "action_label": "用户数据导出",
  "ip": "123.123.123.123",
  "user_agent": "Mozilla/5.0 ...",
  "result": "success",
  "details": {}
}
```

### Log Rotation
- Rotation: 100MB per file, 10 backups
- Retention: 90 days online, 1 year archived
- Compression: gzip after rotation

### Monitoring Queries
```bash
# Recent critical events
grep '"result":"failure"' /opt/fortune-agent/logs/audit.log | tail -20

# Deletion events today
grep '"action":"data_deletion"' /opt/fortune-agent/logs/audit.log | grep "$(date +%Y-%m-%d)"

# Security alerts
grep -i 'alert' /opt/fortune-agent/logs/audit.log

# Rate limit violations
grep 'rate_limit_exceeded' /home/a/fortune-agent/logs/*.log

# Count by action type
grep -o '"action":"[^"]*"' /opt/fortune-agent/logs/audit.log | sort | uniq -c | sort -rn
```

---

## Environment Variables / 环境变量

| Variable | Required | Description |
|----------|----------|-------------|
| `ENCRYPTION_KEY` | YES | AES-256-GCM encryption key (base64, 32 bytes) |
| `JWT_SECRET_KEY` | YES | JWT signing secret |
| `ADMIN_KEY` | YES | Admin API key |
| `FORTUNE_API_KEY` | Optional | Primary API key for service access |
| `API_KEYS` | Optional | Comma-separated additional API keys |
| `AUDIT_LOG_PATH` | No | Audit log file path (default: /opt/fortune-agent/logs/audit.log) |

---

## Security Contacts / 安全联系人

- **Security Team**: security@fortune-agent.com
- **DevOps**: devops@fortune-agent.com
- **Emergency**: +86-xxx-xxxxxxx (24/7)
