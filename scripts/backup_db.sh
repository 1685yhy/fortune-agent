#!/bin/bash
# 易理明灯 数据库每日备份脚本（可独立运行，也可被 crontab 调用）
#
# 用法:
#   bash /mnt/e/fortune-agent/scripts/backup_db.sh            # 立即备份
#   bash /mnt/e/fortune-agent/scripts/backup_db.sh --check    # 距上次备份>24h才备份
#
# crontab 示例（每日 03:00，已由部署时写入）:
#   0 3 * * * /mnt/e/fortune-agent/scripts/backup_db.sh >> /mnt/e/fortune-agent/logs/backup_db.log 2>&1
#
# 说明:
#   - 使用 sqlite3 的在线备份 API（Connection.backup），服务运行中也可安全备份
#   - 备份保留最近 14 份，自动清理更早的备份
#   - WSL 环境若 cron 未运行，可手动执行本脚本；后端启动时也会自动兜底检查（>24h 未备份则备份）

set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN=""
for cand in "$PROJECT_DIR/.venv/bin/python3" "$PROJECT_DIR/venv/bin/python3" python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    PYTHON_BIN="$cand"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "ERROR: python3 not found" >&2
  exit 1
fi

mkdir -p "$PROJECT_DIR/logs"

cd "$PROJECT_DIR" || exit 1
exec "$PYTHON_BIN" "$PROJECT_DIR/scripts/backup_db.py" "$@"
