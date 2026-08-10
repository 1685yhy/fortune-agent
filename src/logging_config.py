"""统一日志配置 — 应用日志按天轮转落盘 + 控制台输出。

用法（src/main.py lifespan 中调用）:
    from .logging_config import setup_app_logging
    app_log_path = setup_app_logging()

行为:
- 应用日志 → logs/app.log（TimedRotatingFileHandler，每天午夜轮转，保留 14 天）
- uvicorn 启动/访问/错误日志 → 同步落入 logs/app.log（同一轮转点），
  原 stdout/stderr 输出保持不变（兼容现有 nohup 重定向启动方式）
- 日志级别由环境变量 LOG_LEVEL 控制（DEBUG/INFO/WARNING/ERROR，默认 INFO）
- 日志目录由 LOG_DIR 环境变量控制（默认项目根 logs/）
- audit.log 由 src/security/audit.py 独立管理（独立 handler），本配置不触碰
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

DEFAULT_LEVEL = "INFO"
DEFAULT_BACKUP_DAYS = 14


def resolve_log_dir() -> Path:
    """日志目录：LOG_DIR 环境变量优先，否则项目根 logs/。"""
    env = os.getenv("LOG_DIR", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "logs"


def resolve_log_level() -> str:
    """日志级别：LOG_LEVEL 环境变量（默认 INFO），非法值回退 INFO。"""
    name = os.getenv("LOG_LEVEL", DEFAULT_LEVEL).strip().upper() or DEFAULT_LEVEL
    if name not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return DEFAULT_LEVEL
    return name


def _attach_daily_rotating_handler(logger: logging.Logger, log_file: Path,
                                   level: int, formatter: logging.Formatter):
    """为 logger 挂载按天轮转的 FileHandler（幂等：同一文件不重复添加）。

    Returns:
        TimedRotatingFileHandler
    """
    for h in logger.handlers:
        if isinstance(h, TimedRotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_file):
            return h
    handler = TimedRotatingFileHandler(
        str(log_file),
        when="midnight",   # 每天午夜轮转 → app.log.YYYY-MM-DD
        interval=1,
        backupCount=DEFAULT_BACKUP_DAYS,  # 保留 14 天
        encoding="utf-8",
        delay=False,
    )
    handler.setFormatter(formatter)
    handler.setLevel(level)
    logger.addHandler(handler)
    return handler


def setup_app_logging(log_dir: Path = None, level: str = None) -> Path:
    """配置应用日志：root + uvicorn 家族统一级别、统一落盘文件。

    Args:
        log_dir: 日志目录（默认 resolve_log_dir()）
        level:   日志级别名（默认 resolve_log_level()）

    Returns:
        日志文件绝对路径（logs/app.log）
    """
    log_dir = Path(log_dir) if log_dir is not None else resolve_log_dir()
    level_name = (level or resolve_log_level()).upper()
    numeric_level = getattr(logging, level_name, logging.INFO)
    log_dir.mkdir(parents=True, exist_ok=True)

    app_log = log_dir / "app.log"
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # 控制台输出（uvicorn 捕获 stdout 场景下保持原有可见性）
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        root.addHandler(console)

    # 落盘（按天轮转，保留 14 天）
    _attach_daily_rotating_handler(root, app_log, numeric_level, file_formatter)

    # uvicorn 家族（启动横幅/访问日志/错误日志）同步落盘
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.setLevel(numeric_level)
        _attach_daily_rotating_handler(lg, app_log, numeric_level, file_formatter)

    return app_log
