#!/bin/bash
# Fortune Agent 一键部署脚本 - 阿里云服务器
# 在服务器上运行: bash deploy.sh

set -e

echo "========================================"
echo "  易理明灯 - 算命AI 部署脚本"
echo "========================================"

# 1. 安装系统依赖
echo "[1/5] 安装系统依赖..."
sudo apt update -qq
sudo apt install -y -qq python3 python3-pip python3-venv nginx git 2>/dev/null || \
  yum install -y python3 python3-pip git 2>/dev/null || true

# 2. 创建项目目录
echo "[2/5] 创建项目目录..."
mkdir -p /opt/fortune-agent /opt/fortune-data/{books,vectordb,userdata,charts}

# 3. 安装 Python 依赖
echo "[3/5] 安装 Python 依赖..."
pip3 install --break-system-packages -q fastapi uvicorn chromadb sentence-transformers \
  anthropic pymupdf jieba pillow matplotlib pyyaml httpx lunar-python cnlunar 2>/dev/null || \
pip3 install -q fastapi uvicorn chromadb sentence-transformers \
  anthropic pymupdf jieba pillow matplotlib pyyaml httpx lunar-python cnlunar 2>/dev/null

# 4. 设置环境变量
echo "[4/5] 配置环境..."

# 创建 systemd 服务
cat > /etc/systemd/system/fortune-agent.service << 'SERVICE'
[Unit]
Description=Fortune Agent - 易理明灯 API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/fortune-agent
Environment=ANTHROPIC_API_KEY=sk-70725a9a672b4acd9ae8e86424014c63
Environment=PYTHONPATH=/opt/fortune-agent
ExecStart=python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

# 配置 Nginx 反向代理（80端口 → 8765端口）
cat > /etc/nginx/sites-enabled/fortune << 'NGINX'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
NGINX

systemctl daemon-reload
systemctl enable fortune-agent
systemctl restart nginx 2>/dev/null || nginx 2>/dev/null || true

echo "[5/5] 启动服务..."
systemctl restart fortune-agent
sleep 3

# 验证
echo ""
echo "========================================"
echo "  验证部署..."
echo "========================================"
curl -s http://127.0.0.1:8765/api/health && echo ""
echo ""
echo "========================================"
echo "  ✅ 部署完成！"
echo ""
echo "  服务地址: http://47.102.42.238"
echo "  健康检查: http://47.102.42.238/api/health"
echo ""
echo "  查看日志: journalctl -u fortune-agent -f"
echo "========================================"
