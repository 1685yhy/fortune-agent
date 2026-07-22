#!/bin/bash
# Fortune Agent Production Deployment
# Usage: bash deploy/production.sh
# This script prepares the production server environment only.
# It does NOT purchase domains or change DNS records.

set -e

SERVER="124.221.233.214"
SSH_CMD="sshpass -p 'Asdfghjkl123!!' ssh -o StrictHostKeyChecking=no root@$SERVER"

echo "=== [1/5] Opening firewall port 8765 ==="
$SSH_CMD "iptables -I YJ-FIREWALL-INPUT 1 -p tcp --dport 8765 -j ACCEPT"
echo "  -> Port 8765 opened"

echo ""
echo "=== [2/5] Installing SSL cert (self-signed for now) ==="
$SSH_CMD "mkdir -p /etc/nginx/ssl && openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/fortune.key -out /etc/nginx/ssl/fortune.crt \
  -subj '/CN=api.yilimingdeng.com'"
echo "  -> Self-signed cert created at /etc/nginx/ssl/"

echo ""
echo "=== [3/5] Setting up Nginx reverse proxy ==="
$SSH_CMD "cat > /etc/nginx/sites-available/fortune << 'NGINX'
server {
    listen 443 ssl;
    server_name api.yilimingdeng.com;

    ssl_certificate /etc/nginx/ssl/fortune.crt;
    ssl_certificate_key /etc/nginx/ssl/fortune.key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Increase body size for chat & report content
    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    # Health check endpoint
    location /health {
        access_log off;
        proxy_pass http://127.0.0.1:8765/api/health;
        proxy_set_header Host \$host;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name api.yilimingdeng.com;
    return 301 https://\$server_name\$request_uri;
}
NGINX
ln -sf /etc/nginx/sites-available/fortune /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx"
echo "  -> Nginx reverse proxy configured"

echo ""
echo "=== [4/5] Creating systemd service ==="
$SSH_CMD "cat > /etc/systemd/system/fortune-agent.service << 'SERVICE'
[Unit]
Description=Fortune Agent API - 易理明灯
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/fortune-agent

# Environment variables
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
SERVICE
systemctl daemon-reload && systemctl enable fortune-agent && systemctl restart fortune-agent"
echo "  -> systemd service created and started"

echo ""
echo "=== [5/5] Verifying deployment ==="
sleep 3
$SSH_CMD "curl -s http://127.0.0.1:8765/api/health" && echo ""
echo "  -> Health check passed"

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Next steps (manual):"
echo "  1. Point DNS A record for api.yilimingdeng.com -> 124.221.233.214"
echo "  2. Replace self-signed cert with Let's Encrypt: certbot --nginx -d api.yilimingdeng.com"
echo "  3. Update miniprogram/utils/api.js baseURL to https://api.yilimingdeng.com"
echo "  4. Add https://api.yilimingdeng.com to WeChat mini program domain whitelist"
echo "     (WeChat Dev Tools -> Details -> Local Settings -> Domain whitelist)"
echo "  5. Submit mini program for review (see wechat-checklist.md)"
echo ""
