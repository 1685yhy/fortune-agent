"""Configuration for Fortune Agent."""
import os
from dataclasses import dataclass, field
from pathlib import Path
import yaml

from .book_categories import BOOKS_COLLECTION


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
    # k24：默认值必须指向真实有数据的古籍库（BOOKS_COLLECTION 单一事实源）。
    # 旧默认值 "fortune_books" 是 chroma 实测 0 条的空集合——线上 8 项能力
    # refs=0 的根因之一（yaml 的 embedding_collection 当时根本没被读取）。
    embedding_collection: str = BOOKS_COLLECTION
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
    # k33/A27：LLM provider 配置点（此前 main.py:815 硬编码 "deepseek"）。
    # 默认 "deepseek" == 硬编码原值 → 零行为变化；生产 GLM 灰度前需先把
    # 该切换点接出来（消费点见 src/main.py FortuneLLM 装配与启动日志）。
    llm_provider: str = "deepseek"
    # 体验模式：EXPERIENCE_MODE=true/1 时解锁全部付费内容并跳过配额限制（体验版测试用）
    experience_mode: bool = False


def is_experience_mode() -> bool:
    """体验模式开关：EXPERIENCE_MODE 取值为 "1"/"true"/"yes"/"on"（忽略大小写）时为 True。

    .env 已在模块导入时加载进 os.environ，因此本函数在 love.py / handler.py /
    main.py / chat_stream.py / pay.py 等任意支付墙、配额检查点可直接调用。
    """
    return os.getenv("EXPERIENCE_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def tts_upstream_base() -> str:
    """TTS 合成服务内部地址（转发目标，默认本机 8768；可经 TTS_UPSTREAM_BASE 覆盖）。

    注意：这是「后端 → TTS 服务」的内部转发地址，与服务对外域名无关，
    生产上两者通常在同一台机器/内网，无需也不应暴露公网地址。
    """
    return os.getenv("TTS_UPSTREAM_BASE", "http://127.0.0.1:8768").rstrip("/")


def public_base_url() -> str:
    """对外可访问的服务根地址（G3 H-10：TTS 音频等相对路径改写为完整 URL 用）。

    默认 https://yilichat.com（生产域名）——TTS 相对路径改写若落到 127.0.0.1，
    真机上指向手机自身、语音全部不可达（上线即坏功能，H-10 根因）。
    开发环境在本地 .env 设 PUBLIC_BASE_URL=http://127.0.0.1:8768 覆盖。
    """
    return os.getenv("PUBLIC_BASE_URL", "https://yilichat.com").rstrip("/")


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
            # k24：yaml 的 embedding_collection 此前被漏读（只支持 env 覆盖），
            # 配置写了也不生效、静默落到空集合默认值。补上读取，保持
            # 「yaml 配置 < 环境变量」的既有优先级。
            if data.get("embedding_collection"):
                settings.embedding_collection = str(data["embedding_collection"])
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
    # k33/A27：provider 切换点（env 覆盖；不设 == 默认 "deepseek"，零行为变化）
    if os.getenv("FORTUNE_LLM_PROVIDER"):
        settings.llm_provider = os.getenv("FORTUNE_LLM_PROVIDER").strip().lower()
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
