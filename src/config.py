"""Configuration for Fortune Agent."""
import os
from dataclasses import dataclass, field
from pathlib import Path
import yaml


def load_env_file(path: str = ".env"):
    """Load a .env file into os.environ (never overrides existing variables).

    Called at import time so that JWT_SECRET_KEY / ENCRYPTION_KEY / ADMIN_KEY /
    WECHAT_* / FORTUNE_API_KEY 等密钥在任何启动方式下都可用。
    """
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# 模块导入即加载 .env（支持从项目根目录或任何 cwd 启动）
load_env_file(".env")
if not os.environ.get("JWT_SECRET_KEY") and Path("src/config.py").exists():
    # 兼容从仓库根之外启动的场景：再尝试项目根 .env
    load_env_file(Path(__file__).resolve().parent.parent / ".env")


@dataclass
class Settings:
    data_dir: Path = Path("/mnt/d/fortune-data")
    books_dir: Path = field(default_factory=lambda: Path("/mnt/d/fortune-data/books"))
    vectordb_dir: Path = field(default_factory=lambda: Path("/mnt/d/fortune-data/vectordb_v2"))
    faiss_index_dir: Path = field(default_factory=lambda: Path("/mnt/d/fortune-data/faiss"))
    db_path: Path = field(default_factory=lambda: Path("/mnt/d/fortune-data/userdata/fortune.db"))
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-5"
    embedding_model: str = "BAAI/bge-m3"
    embedding_collection: str = "fortune_books"
    embedding_dimension: int = 1024
    # Push settings
    push_enabled: bool = True
    push_time: str = "08:00"
    push_timezone: str = "Asia/Shanghai"
    push_max_users_per_batch: int = 50
    # Admin
    admin_key: str = ""
    # 智谱开放平台（Web Search / GLM 视觉等）
    zhipu_api_key: str = ""
    # 体验模式：EXPERIENCE_MODE=true/1 时解锁全部付费内容并跳过配额限制（体验版测试用）
    experience_mode: bool = False


def is_experience_mode() -> bool:
    """体验模式开关：EXPERIENCE_MODE 取值为 "1"/"true"/"yes"/"on"（忽略大小写）时为 True。

    .env 已在模块导入时加载进 os.environ，因此本函数在 love.py / handler.py /
    main.py / chat_stream.py / pay.py 等任意支付墙、配额检查点可直接调用。
    """
    return os.getenv("EXPERIENCE_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def load_settings(config_path: str = "config/settings.yaml") -> Settings:
    settings = Settings()
    path = Path(config_path)
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data:
            if "data_dir" in data:
                settings.data_dir = Path(data["data_dir"])
            if "claude_api_key" in data:
                settings.claude_api_key = data["claude_api_key"]
            if "claude_model" in data:
                settings.claude_model = data["claude_model"]
            push_cfg = data.get("push", {})
            if "enabled" in push_cfg:
                settings.push_enabled = push_cfg["enabled"]
            if "time" in push_cfg:
                settings.push_time = push_cfg["time"]
            if "timezone" in push_cfg:
                settings.push_timezone = push_cfg["timezone"]
            if "max_users_per_batch" in push_cfg:
                settings.push_max_users_per_batch = push_cfg["max_users_per_batch"]
    # env override
    if os.getenv("ANTHROPIC_API_KEY"):
        settings.claude_api_key = os.getenv("ANTHROPIC_API_KEY")
    if os.getenv("DEEPSEEK_API_KEY"):
        settings.claude_api_key = os.getenv("DEEPSEEK_API_KEY")
    if os.getenv("ADMIN_KEY"):
        settings.admin_key = os.getenv("ADMIN_KEY")
    if os.getenv("ZHIPU_API_KEY"):
        settings.zhipu_api_key = os.getenv("ZHIPU_API_KEY")
    if os.getenv("EMBEDDING_MODEL"):
        settings.embedding_model = os.getenv("EMBEDDING_MODEL")
    if os.getenv("EMBEDDING_COLLECTION"):
        settings.embedding_collection = os.getenv("EMBEDDING_COLLECTION")
    if os.getenv("EMBEDDING_DIMENSION"):
        settings.embedding_dimension = int(os.getenv("EMBEDDING_DIMENSION"))
    if os.getenv("FAISS_INDEX_DIR"):
        settings.faiss_index_dir = Path(os.getenv("FAISS_INDEX_DIR"))
    # 数据路径环境变量覆盖：/mnt/d 为 9P 挂载盘（慢窗口问题），可将数据迁到
    # WSL ext4 盘（如 /home/a）后通过 FORTUNE_DB_PATH / VECTORDB_DIR 指定新位置；
    # 不设置环境变量时保持默认路径不变，行为与原来完全一致。
    if os.getenv("FORTUNE_DB_PATH"):
        settings.db_path = Path(os.getenv("FORTUNE_DB_PATH"))
    if os.getenv("VECTORDB_DIR"):
        settings.vectordb_dir = Path(os.getenv("VECTORDB_DIR"))
    if os.getenv("EXPERIENCE_MODE"):
        settings.experience_mode = is_experience_mode()
    return settings
