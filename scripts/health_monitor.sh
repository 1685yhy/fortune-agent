#!/bin/bash
# Health monitor - check every 60s, alert on failure
URL="http://127.0.0.1:8765/api/health"
LOG="/opt/fortune-data/monitor.log"
for i in {1..3}; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 10 "$URL" 2>/dev/null)
  if [ "$STATUS" != "200" ]; then
    echo "[$(date)] ALERT: Health check FAILED (attempt $i/3)" >> "$LOG"
    sleep 10
  else
    break
  fi
done
systemctl restart fortune-agent 2>/dev/null || true
systemctl restart cow-fortune 2>/dev/null || true
