"""数据库在线备份（sqlite3 .backup API，安全备份运行中的库）。

用法:
    python scripts/backup_db.py                  # 立即备份一次
    python scripts/backup_db.py --check          # 距上次备份>24h 才备份（启动兜底）
    python scripts/backup_db.py --keep 14        # 保留最近 N 份（默认 14）

备份目录: /mnt/d/fortune-data/backups/fortune_YYYYMMDD_HHMMSS.db
"""
import argparse
import datetime
import glob
import logging
import os
import sqlite3
import sys
from typing import Optional

logger = logging.getLogger("backup_db")

DEFAULT_DB = "/mnt/d/fortune-data/userdata/fortune.db"
DEFAULT_BACKUP_DIR = "/mnt/d/fortune-data/backups"
DEFAULT_KEEP = 14
DEFAULT_MAX_AGE_HOURS = 24


def run_backup(db_path: str = DEFAULT_DB, backup_dir: str = DEFAULT_BACKUP_DIR,
               keep: int = DEFAULT_KEEP) -> str:
    """在线备份数据库（sqlite3 Connection.backup，安全支持运行中的库）。

    Returns:
        备份文件绝对路径
    """
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"fortune_{stamp}.db")

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        with dst:
            src.backup(dst)  # 在线安全备份，不影响读写
    finally:
        src.close()
        dst.close()

    # 清理：保留最近 keep 份
    files = sorted(glob.glob(os.path.join(backup_dir, "fortune_*.db")))
    for old in files[:-keep] if keep > 0 else []:
        try:
            os.remove(old)
        except OSError:
            pass

    logger.info("备份完成: %s（共保留 %d 份）", dest, min(len(files), keep) if keep > 0 else len(files))
    return dest


def latest_backup_time(backup_dir: str = DEFAULT_BACKUP_DIR) -> Optional[float]:
    """返回最近一次备份文件的 mtime（无备份返回 None）。"""
    files = sorted(glob.glob(os.path.join(backup_dir, "fortune_*.db")))
    if not files:
        return None
    try:
        return os.path.getmtime(files[-1])
    except OSError:
        return None


def ensure_recent_backup(db_path: str = DEFAULT_DB, backup_dir: str = DEFAULT_BACKUP_DIR,
                         max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                         keep: int = DEFAULT_KEEP) -> Optional[str]:
    """若上次备份时间超过 max_age_hours 则执行备份（后端启动兜底用）。"""
    last = latest_backup_time(backup_dir)
    if last is not None and (datetime.datetime.now().timestamp() - last) < max_age_hours * 3600:
        logger.info("备份检查：上次备份在 %s 内，跳过", datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S"))
        return None
    logger.warning("备份检查：需要备份（上次备份过旧或不存在）")
    return run_backup(db_path, backup_dir, keep=keep)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Fortune Agent 数据库在线备份")
    parser.add_argument("--check", action="store_true", help="距上次备份>24h 才备份（兜底模式）")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="保留最近 N 份（默认 14）")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--dir", default=DEFAULT_BACKUP_DIR, help="备份目录")
    args = parser.parse_args()

    if args.check:
        ensure_recent_backup(args.db, args.dir, keep=args.keep)
    else:
        run_backup(args.db, args.dir, keep=args.keep)


if __name__ == "__main__":
    main()
