"""Configuration for Fortune Agent."""
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class Settings:
    data_dir: Path = Path("/mnt/d/fortune-data")
    books_dir: Path = field(default_factory=lambda: Path("/mnt/d/fortune-data/books"))
    vectordb_dir: Path = field(default_factory=lambda: Path("/mnt/d/fortune-data/vectordb"))
    db_path: Path = field(default_factory=lambda: Path("/mnt/d/fortune-data/userdata/fortune.db"))
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-5"
    embedding_model: str = "BAAI/bge-m3"
    embedding_collection: str = "fortune_books_v4_bge_m3"
    embedding_dimension: int = 1024
    # Push settings
    push_enabled: bool = True
    push_time: str = "08:00"
    push_timezone: str = "Asia/Shanghai"
    push_max_users_per_batch: int = 50
    # Admin
    admin_key: str = ""


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
    import os
    if os.getenv("ANTHROPIC_API_KEY"):
        settings.claude_api_key = os.getenv("ANTHROPIC_API_KEY")
    if os.getenv("ADMIN_KEY"):
        settings.admin_key = os.getenv("ADMIN_KEY")
    if os.getenv("EMBEDDING_MODEL"):
        settings.embedding_model = os.getenv("EMBEDDING_MODEL")
    if os.getenv("EMBEDDING_COLLECTION"):
        settings.embedding_collection = os.getenv("EMBEDDING_COLLECTION")
    if os.getenv("EMBEDDING_DIMENSION"):
        settings.embedding_dimension = int(os.getenv("EMBEDDING_DIMENSION"))
    return settings
